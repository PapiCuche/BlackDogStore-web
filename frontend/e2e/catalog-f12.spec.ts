import { mkdirSync } from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import {
  test, expect, type APIRequestContext, type Browser, type BrowserContext, type Locator, type Page, type PlaywrightWorkerArgs,
} from "@playwright/test";
import { apiBase, imei, shell, TAG } from "./inventory-first-support";

/**
 * CATALOG FASE 1.2 · E2E — fotos por acabado de punta a punta, en navegador real y SIN MOCKS DEL BACKEND.
 *
 * Un modelo publicado con paleta Capacidad × Color × SIM × Región y cuatro variantes (128 GB Titanio arena, 128 GB
 * Azul glaciar, 256 GB Titanio arena y 128 GB Verde salvia; todas eSIM · Internacional) entra a la empresa del backend
 * local (`E2E_BACKEND_DIR`). Las fotos son DIBUJOS SINTÉTICOS generados aquí mismo (un rectángulo del color del
 * acabado sobre fondo transparente): ninguna foto real, ningún binario versionado. Se suben por la API del panel con
 * procedencia «foto propia», «autorizada por proveedor» o «recurso demostrativo».
 *
 *   1. Panel: subir con vista, reemplazar en su sitio (posición y principal se quedan, la verificación de la licencia
 *      se borra), muestra propia del modelo marcada «aproximada». Una foto sin la licencia verificada dice «No visible
 *      en la tienda hasta verificar la licencia» (propietario, 2026-11-23) hasta que se marca la casilla.
 *   2. Configurador público: cada color enseña SOLO sus fotos (principal y miniaturas); ?variante= restaura la
 *      elección; un color sin fotos enseña «Imagen pendiente», nunca otra foto; el chip usa la muestra del modelo.
 *   3. «Usar la muestra de la empresa» devuelve el chip a la muestra de la empresa.
 *   4. Carrito y checkout conservan la foto del acabado elegido y su configuración (sin pagar).
 *   5. Caja: Modelo → Capacidad → Color → SIM → Región → «Llegó el equipo» (IMEI sintético con Luhn) → venta; un
 *      equipo nuevo no pide batería ni nada de seminuevos.
 *   6. Aislamiento: otra empresa no ve ni toca las fotos por acabado de ésta (API).
 *   7. Revisión visual a 360, 390, 768 y 1440 px: sin desborde, foco visible y tokens V4 sólo dentro de
 *      [data-design="v4"]. Con `E2E_SCREENSHOTS=<carpeta>` deja las capturas (fuera del repositorio).
 *   8. Cobertura (#387): «Fotos por acabado y licencias» lista el acabado sin fotos con su enlace, y deja de listarlo
 *      cuando recibe una foto.
 */

const SHOTS = process.env.E2E_SCREENSHOTS ?? "";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const ADMIN = { username: "dev_admin", password: "Demo123!" };
const OTHER = { username: `f12_otra_${TAG.toLowerCase()}`, password: "F12-Otra-2026!" };
const VIEWPORTS = [
  { width: 360, height: 800 }, { width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 1440, height: 900 },
] as const;

// ── Synthetic pictures ───────────────────────────────────────────────────────────────────────────────────────

/**
 * A drawn phone, never a photograph: a rounded body in the finish's colour on a transparent background. `back` adds a
 * dark camera block, `mark` a light band, so every file is different (a replacement is another file).
 */
function drawing(rgb: [number, number, number], options: { back?: boolean; mark?: boolean } = {}): Buffer {
  const width = 480;
  const height = 480;
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 4);
    for (let x = 0; x < width; x++) {
      const body = x >= 140 && x < 340 && y >= 40 && y < 440;
      const corner = (x < 160 || x >= 320) && (y < 60 || y >= 420)
        && Math.hypot(x < 160 ? 160 - x : x - 319, y < 60 ? 60 - y : y - 419) > 20;
      let pixel: number[] = [0, 0, 0, 0];
      if (body && !corner) pixel = [...rgb, 255];
      if (options.back && x >= 160 && x < 230 && y >= 60 && y < 130) pixel = [40, 40, 44, 255];
      if (options.mark && body && y >= 300 && y < 316) pixel = [245, 245, 245, 255];
      row.set(pixel, 1 + x * 4);
    }
    rows.push(row);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 6, 0, 0, 0], 8);
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(zlib.crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header), chunk("IDAT", zlib.deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0)),
  ]);
}

const ARENA: [number, number, number] = [200, 183, 158];
const GLACIAR: [number, number, number] = [169, 198, 221];
const png = (name: string, buffer: Buffer) => ({ name, mimeType: "image/png", buffer });

// ── The shop of the run ──────────────────────────────────────────────────────────────────────────────────────

type Variant = { id: number; slug: string; name: string };
type Seed = {
  id: number; slug: string; name: string; branch: number; company: number;
  steps: { capacity: string; color: string; sim: string; region: string };
  colours: { arena: number; glaciar: number; salvia: number };
  companySwatch: { salvia: string };
  a: Variant; b: Variant; c: Variant; d: Variant;
};
let seed: Seed;
/** Public ids of the pictures, filled as the run uploads them. */
const ids = { arenaMain: "", arenaBack: "", glaciarOld: "", glaciarNew: "" };
let glaciarImage = 0;
let adminApi: APIRequestContext;
let adminState: Awaited<ReturnType<APIRequestContext["storageState"]>>;
/** The shopper of the cart test: the visual review reuses its cart (one web hold per run, never a second). */
let shopperState: Awaited<ReturnType<BrowserContext["storageState"]>> | undefined;

function seedShop(): Seed {
  return shell<Seed>(`
from django.contrib.auth import get_user_model
from store import checkout_services as checkout, device_catalog, model_palette
from store import stock_unit_services as units
from store.models import Company, Product, VariantAttribute, VariantOption
actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug=data['slug'])
branch = checkout.resolve_fulfillment_branch(company)

def attr(role, name, kind='text'):
    found = VariantAttribute.objects.filter(company=company, role=role).first()
    return found or device_catalog.create_attribute(company=company, name=name, role=role, kind=kind)

def opt(attribute, label, swatch=''):
    found = VariantOption.objects.filter(company=company, attribute=attribute, label__iexact=label).first()
    return found or device_catalog.create_option(company=company, attribute_id=attribute.pk, label=label, swatch_hex=swatch)

cap, col, sim, reg = attr('capacity', 'Capacidad'), attr('color', 'Color', 'color'), attr('sim', 'SIM'), attr('region', 'Región')
g128, g256 = opt(cap, '128 GB'), opt(cap, '256 GB')
arena, glaciar = opt(col, 'Titanio arena', '#C8B79E'), opt(col, 'Azul glaciar', '#A9C6DD')
salvia = opt(col, 'Verde salvia', '#8FA98B')
esim, chip = opt(sim, 'eSIM'), opt(sim, 'Chip')
intl, usa = opt(reg, 'Internacional'), opt(reg, 'EE. UU.')
name = 'Equipo F12 ' + data['tag']
model = device_catalog.create_model(company=company, name=name, is_published_online=True,
                                    description='Equipo de prueba de las fotos por acabado.')
model_palette.set_palette(model, option_ids=[o.pk for o in (g128, g256, arena, glaciar, salvia, esim, chip, intl, usa)],
                          actor=actor)

def variant(suffix, price, options, devices):
    product = Product.objects.create(company=company, name=name + ' ' + suffix,
                                     slug=(model.slug + '-' + suffix.replace(' ', '-')).lower(),
                                     price=Decimal(price), inventory=0, is_active=True, is_serialized=True,
                                     requires_imei=True, is_published_online=True)
    product = device_catalog.set_variant(product, model_id=model.pk, condition_line='new',
                                         option_ids=[o.pk for o in options] + [esim.pk, intl.pk])
    units.receive_units(branch=branch, product=product, actor=actor, reason='Prueba de navegador',
                        units=[{'imei': value} for value in devices])
    return {'id': product.pk, 'slug': product.slug, 'name': product.name}

a = variant('128 GB Titanio arena', '4199.00', [g128, arena], data['imeis'][:2])
# Two devices: a web cart holds one for 15 minutes («Apartado web»), and two carts of the run add this colour.
b = variant('128 GB Azul glaciar', '4299.00', [g128, glaciar], data['imeis'][2:4])
c = variant('256 GB Titanio arena', '4699.00', [g256, arena], data['imeis'][4:5])
d = variant('128 GB Verde salvia', '4199.00', [g128, salvia], data['imeis'][5:6])
salvia.refresh_from_db()
out({'id': model.pk, 'slug': model.slug, 'name': name, 'branch': branch.pk, 'company': company.pk,
     'steps': {'capacity': cap.name, 'color': col.name, 'sim': sim.name, 'region': reg.name},
     'colours': {'arena': arena.pk, 'glaciar': glaciar.pk, 'salvia': salvia.pk},
     'companySwatch': {'salvia': device_catalog.shown_swatch(salvia)},
     'a': a, 'b': b, 'c': c, 'd': d})
`, { imeis: [imei(101), imei(102), imei(103), imei(104), imei(105), imei(106)] });
}

// ── API ──────────────────────────────────────────────────────────────────────────────────────────────────────

type Reply<T = Record<string, unknown>> = { status: number; body: T };

async function apiLogin(playwright: PlaywrightWorkerArgs["playwright"], who: { username: string; password: string }) {
  const context = await playwright.request.newContext();
  for (let attempt = 0; ; attempt++) {
    const res = await context.post(`${apiBase()}/auth/login/`, { data: who, headers: { Origin: new URL(BASE).origin } });
    if (res.status() === 200) break;
    if (res.status() !== 429 || attempt >= 2) throw new Error(`login ${who.username}: ${res.status()}`);
    await new Promise((resolve) => setTimeout(resolve, 62_000));
  }
  await context.get(`${apiBase()}/auth/csrf/`);
  return context;
}

async function api<T = Record<string, unknown>>(
  context: APIRequestContext, method: "GET" | "POST" | "PATCH" | "DELETE", route: string,
  body: { json?: unknown; multipart?: Record<string, string | { name: string; mimeType: string; buffer: Buffer }> } = {},
): Promise<Reply<T>> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    const csrf = (await context.storageState()).cookies.find((cookie) => cookie.name === "csrftoken")?.value;
    if (csrf) headers["X-CSRFToken"] = csrf;
    headers.Origin = new URL(BASE).origin;
  }
  const res = await context.fetch(`${apiBase()}${route}`, {
    method, headers,
    ...(body.json !== undefined ? { data: body.json } : {}),
    ...(body.multipart ? { multipart: body.multipart } : {}),
  });
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return { status: res.status(), body: parsed as T };
}

const publicId = (address: string) => address.replace(/\/+$/, "").split("/").pop() ?? "";
const finishes = (model: number) => `/admin/catalog/models/${model}/finish-images/`;

type FinishImage = {
  id: number; url: string; view: string; is_primary: boolean; sort_order: number; public: boolean;
  source: { kind: string; license: string; license_verified: boolean };
};
type Listing = { finishes: { option: Record<string, unknown> & { id: number }; images: FinishImage[]; image_pending: boolean }[] };

async function listing(): Promise<Listing> {
  const reply = await api<Listing>(adminApi, "GET", finishes(seed.id));
  expect(reply.status).toBe(200);
  return reply.body;
}

const finishOf = (list: Listing, colour: number) => list.finishes.find((row) => row.option.id === colour)!;

// ── Page helpers ─────────────────────────────────────────────────────────────────────────────────────────────

const stage = (page: Page) => page.getByTestId("v4-stage");
const gallery = (page: Page) => page.getByTestId("v4-gallery");
const radio = (page: Page, group: string, name: string) =>
  page.getByRole("radiogroup", { name: new RegExp(`^${group}`) }).getByRole("radio", { name: new RegExp(`^${name}`) });

/** The public ids of the pictures in the model page's gallery (stage and thumbnails), among the run's own. */
async function galleryIds(page: Page): Promise<string[]> {
  const sources = await gallery(page).locator("img").evaluateAll((images) =>
    images.map((img) => decodeURIComponent(`${img.getAttribute("src") ?? ""} ${img.getAttribute("srcset") ?? ""}`)));
  const known = Object.entries(ids).filter(([, value]) => value);
  return [...new Set(known.filter(([, value]) => sources.some((source) => source.includes(value))).map(([key]) => key))].sort();
}

async function declineConsent(page: Page) {
  const reject = page.getByRole("button", { name: /Rechazar/ });
  if (await reject.count()) await reject.first().click();
}

async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

async function shot(target: Page | Locator, name: string, fullPage = true) {
  if (!SHOTS) return;
  if ("screenshot" in target && "goto" in target) {
    // A full-page capture draws fixed elements where the page is scrolled: always from the top.
    await target.evaluate(() => window.scrollTo(0, 0));
    await target.waitForTimeout(300);
    await target.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage });
  } else {
    await (target as Locator).screenshot({ path: path.join(SHOTS, `${name}.png`) });
  }
}

/** V4 tokens live ONLY under [data-design="v4"]: never on the root, the body or a page without the block. */
async function v4Scope(page: Page) {
  return page.evaluate(() => {
    const token = (element: Element | null) => (element ? getComputedStyle(element).getPropertyValue("--v4-ink").trim() : null);
    const block = document.querySelector('[data-design="v4"]');
    const outside = document.querySelector("header") ?? document.body;
    return { root: token(document.documentElement), body: token(document.body), outside: token(outside), block: token(block),
             blocks: document.querySelectorAll('[data-design="v4"]').length };
  });
}

/** After a key press, the focused element draws a ring (outline or box shadow) a person can see. */
async function focusRing(page: Page) {
  return page.evaluate(() => {
    const element = document.activeElement as HTMLElement | null;
    if (!element || element === document.body) return { tag: "", visible: false };
    const style = getComputedStyle(element);
    const outline = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 1;
    const shadow = style.boxShadow !== "none" && style.boxShadow !== "";
    return { tag: `${element.tagName} ${element.getAttribute("aria-label") ?? element.textContent?.trim().slice(0, 40) ?? ""}`,
             visible: outline || shadow, matches: element.matches(":focus-visible") };
  });
}

/** How many lines a piece of text takes, measured on its text rectangles (AGENTS §7), never on `scrollWidth`. */
async function textLines(locator: Locator): Promise<number> {
  return locator.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const tops = [...range.getClientRects()].filter((rect) => rect.width > 0).map((rect) => Math.round(rect.top));
    return new Set(tops).size;
  });
}

async function adminPage(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ storageState: adminState });
  return { context, page: await context.newPage() };
}

async function openModelPanel(page: Page) {
  await page.goto(`/admin/products/models/${seed.id}`, { waitUntil: "networkidle" });
  const region = page.getByRole("region", { name: "Imágenes por acabado" });
  await expect(region.getByRole("list", { name: "Acabados" })).toBeVisible({ timeout: 30_000 });
  return region;
}

const finishItem = (region: Locator, label: string) =>
  region.getByRole("list", { name: "Acabados" }).getByRole("listitem", { name: label, exact: true });

// ── The run ──────────────────────────────────────────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ playwright }) => {
  test.setTimeout(180_000);
  if (SHOTS) mkdirSync(SHOTS, { recursive: true });
  seed = seedShop();
  adminApi = await apiLogin(playwright, ADMIN);
  adminState = await adminApi.storageState();

  // Through the panel's own API: Titanio arena's main picture (the shop's own photo, view «main») and Azul glaciar's
  // front picture, authorised by a supplier, whose licence somebody verified.
  const main = await api<FinishImage>(adminApi, "POST", finishes(seed.id), { multipart: {
    file: png("arena-principal.png", drawing(ARENA)), color_option_id: String(seed.colours.arena), view: "main",
    source_kind: "own_photo", alt_text: `${seed.name} Titanio arena`,
  } });
  expect(main.status, JSON.stringify(main.body)).toBe(201);
  expect(main.body).toMatchObject({ view: "main", is_primary: true, public: false });
  ids.arenaMain = publicId(main.body.url);
  // Owner, 2026-11-23: a finish picture reaches the shop only once somebody verified it, an own photo too.
  const mainVerified = await api<FinishImage>(adminApi, "PATCH", `${finishes(seed.id)}${main.body.id}/`, { json: { license_verified: true } });
  expect(mainVerified.status, JSON.stringify(mainVerified.body)).toBe(200);
  expect(mainVerified.body.public).toBe(true);

  const front = await api<FinishImage>(adminApi, "POST", finishes(seed.id), { multipart: {
    file: png("glaciar-frontal.png", drawing(GLACIAR)), color_option_id: String(seed.colours.glaciar), view: "front",
    source_kind: "supplier_authorized", license: "Autorización escrita del distribuidor (prueba E2E)",
    alt_text: `${seed.name} Azul glaciar`,
  } });
  expect(front.status, JSON.stringify(front.body)).toBe(201);
  ids.glaciarOld = publicId(front.body.url);
  glaciarImage = front.body.id;
  const verified = await api<FinishImage>(adminApi, "PATCH", `${finishes(seed.id)}${glaciarImage}/`, { json: { license_verified: true } });
  expect(verified.status, JSON.stringify(verified.body)).toBe(200);
  expect(verified.body.source.license_verified).toBe(true);

  // A view outside the vocabulary is refused on its field.
  const bad = await api(adminApi, "POST", finishes(seed.id), { multipart: {
    file: png("mala-vista.png", drawing(ARENA, { mark: true })), color_option_id: String(seed.colours.arena),
    view: "trasera", source_kind: "own_photo",
  } });
  expect(bad.status).toBe(400);
  expect(bad.body).toMatchObject({ field: "view" });
});

/** The second company cannot be deleted (sequences, settings and roles protect it): it stays INACTIVE. */
function deactivateOther() {
  shell(`
from store.models import Company
Company.objects.filter(slug=data['other_slug_f12']).update(is_active=False)
out({'ok': True})
`, { other_slug_f12: `f12-otra-${TAG.toLowerCase()}` });
}

test.afterAll(async () => {
  await adminApi?.dispose();
  deactivateOther();
  // The run's web cart holds go back to the shelf: a browser may hold only a few devices at a time
  // (`max_holds_per_origin`), and a later run from this machine would be refused «Puedes apartar hasta…».
  if (seed) shell(`
from django.contrib.auth import get_user_model
from store import stock_unit_services as units
from store.models import StockUnit
actor = get_user_model().objects.get(username='dev_admin')
held = StockUnit.objects.filter(product__product_model_id=data['model'], status='reserved', hold_order__isnull=True)
held = held.exclude(hold_key='')
out({'released': [units.release(unit, actor=actor).pk for unit in held]})
`, { model: seed.id });
});

test("panel: subir con vista, reemplazar en su sitio y muestra propia del modelo marcada «aproximada»", async ({ browser }) => {
  test.setTimeout(180_000);
  const { context, page } = await adminPage(browser);
  await page.setViewportSize({ width: 1280, height: 900 });
  const region = await openModelPanel(page);

  // 1. Upload with a view: Titanio arena's back, a demo resource with its licence.
  const arena = finishItem(region, "Titanio arena");
  await arena.getByRole("button", { name: "Subir imagen de Titanio arena" }).click();
  const form = page.getByRole("form", { name: "Subir imagen de Titanio arena" });
  await form.getByLabel("Elegir imagen").setInputFiles(png("arena-posterior.png", drawing(ARENA, { back: true })));
  await form.getByLabel("Vista (opcional)").selectOption("back");
  await form.getByLabel("Origen de la imagen (obligatorio)").selectOption("demo");
  await form.getByLabel(/^Licencia o autorización/).fill("Recurso demostrativo de la prueba E2E");
  await form.getByRole("button", { name: "Subir imagen" }).click();
  await expect(region.getByText("Imagen subida a Titanio arena.")).toBeVisible({ timeout: 30_000 });
  const second = arena.getByRole("listitem", { name: "Imagen 2 de Titanio arena" });
  const term = (card: Locator, text: string) => card.getByRole("definition").filter({ hasText: new RegExp(`^${text}$`) });
  await expect(term(second, "Posterior")).toBeVisible();
  await expect(term(second, "Recurso demostrativo")).toBeVisible();
  let stored = finishOf(await listing(), seed.colours.arena);
  expect(stored.images.map((image) => [image.view, image.is_primary])).toEqual([["main", true], ["back", false]]);
  ids.arenaBack = publicId(stored.images[1].url);
  // Not on the shop until its licence is verified (owner, 2026-11-23); ticking the box publishes it.
  const notPublic = (item: Locator) => item.getByText("No visible en la tienda hasta verificar la licencia");
  await expect(notPublic(second)).toBeVisible();
  await expect(notPublic(arena.getByRole("listitem", { name: "Imagen 1 de Titanio arena" }))).toHaveCount(0);
  await second.getByRole("checkbox", { name: "Licencia verificada: imagen 2 de Titanio arena" }).click();
  await expect(notPublic(second)).toHaveCount(0, { timeout: 30_000 });
  expect(finishOf(await listing(), seed.colours.arena).images.map((image) => image.public)).toEqual([true, true]);

  // 2. Replace in place: Azul glaciar's only picture (primary, verified licence) takes a new file.
  const glaciar = finishItem(region, "Azul glaciar");
  const card = glaciar.getByRole("listitem", { name: "Imagen 1 de Azul glaciar" });
  await expect(card.locator("span", { hasText: /^Principal$/ })).toBeVisible();
  await expect(term(card, "Sin verificar")).toHaveCount(0);
  await card.getByRole("button", { name: "Reemplazar foto: imagen 1 de Azul glaciar" }).click();
  const replace = page.getByRole("form", { name: "Reemplazar la imagen 1 de Azul glaciar" });
  // Focus lands on the origin, the first thing to say (review of #388).
  await expect(replace.getByLabel("Origen de la imagen (obligatorio)")).toBeFocused();
  await replace.getByLabel("Elegir la foto nueva").setInputFiles(png("glaciar-nueva.png", drawing(GLACIAR, { mark: true })));
  await replace.getByLabel("Origen de la imagen (obligatorio)").selectOption("supplier_authorized");
  await replace.getByLabel(/^Licencia o autorización/).fill("Nueva autorización del distribuidor (prueba E2E)");
  await replace.getByRole("button", { name: "Reemplazar foto" }).click();
  await expect(region.getByText("Imagen 1 de Azul glaciar reemplazada.")).toBeVisible({ timeout: 30_000 });
  await expect(card.locator("span", { hasText: /^Principal$/ })).toBeVisible();
  await expect(term(card, "Frontal")).toBeVisible();
  await expect(term(card, "Sin verificar")).toBeVisible();
  stored = finishOf(await listing(), seed.colours.glaciar);
  expect(stored.images).toHaveLength(1);
  const replaced = stored.images[0];
  expect(replaced).toMatchObject({ id: glaciarImage, view: "front", is_primary: true, sort_order: 0 });
  expect(replaced.source).toMatchObject({ kind: "supplier_authorized", license_verified: false });
  expect(replaced.public).toBe(false);
  ids.glaciarNew = publicId(replaced.url);
  // The new file is not on the shop until somebody verifies it again.
  await expect(notPublic(card)).toBeVisible();
  await card.getByRole("checkbox", { name: "Licencia verificada: imagen 1 de Azul glaciar" }).click();
  await expect(notPublic(card)).toHaveCount(0, { timeout: 30_000 });
  await expect(term(card, "Sin verificar")).toHaveCount(0);
  expect(ids.glaciarNew).not.toBe(ids.glaciarOld);
  // The old file is gone from the public address (nothing else showed it).
  const gone = await adminApi.get(`${apiBase()}/storefront/images/${ids.glaciarOld}`);
  expect(gone.status()).toBe(404);

  // 3. The model's own swatch for Verde salvia, flagged approximate; the company's stays as it was.
  const salvia = finishItem(region, "Verde salvia");
  await expect(salvia.getByText("Imagen pendiente")).toBeVisible();
  await salvia.getByRole("button", { name: "Ajustar muestra de Verde salvia" }).click();
  const own = salvia.getByRole("group", { name: `Muestra solo para ${seed.name}` });
  await own.getByLabel(`Muestra de Verde salvia solo en ${seed.name} (#RRGGBB)`).fill("#3E6B48");
  await own.getByLabel("Muestra aproximada (pendiente de validación comercial)").check();
  await own.getByRole("button", { name: "Guardar muestra de este modelo" }).click();
  await expect(region.getByText(`Muestra de Verde salvia guardada para ${seed.name}.`)).toBeVisible({ timeout: 30_000 });
  await expect(salvia.getByText("Aproximada", { exact: true })).toBeVisible();
  await expect(salvia.getByText("Muestra propia de este modelo")).toBeVisible();
  await expect(salvia.getByTestId("finish-swatch")).toHaveCSS("background-color", "rgb(62, 107, 72)");
  const option = finishOf(await listing(), seed.colours.salvia).option;
  expect(option).toMatchObject({ model_swatch_hex: "#3E6B48", model_swatch_is_approximate: true,
                                 shown_swatch_hex: "#3E6B48", shown_swatch_is_approximate: true,
                                 swatch_hex: seed.companySwatch.salvia });
  await context.close();
});

test("configurador: cada color sólo sus fotos, ?variante= restaura, color sin fotos «Imagen pendiente», chip con la muestra del modelo", async ({ browser }) => {
  test.setTimeout(180_000);
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const C = seed.steps;

  // The public model says each picture's view and nothing of its provenance or of the approximation.
  const raw = await (await page.request.get(`${apiBase()}/catalog/models/${seed.slug}/`)).text();
  for (const leak of ["license", "source_kind", "approximate", "Autorización", "license_verified"]) {
    expect(raw, `la API pública dice «${leak}»`).not.toContain(leak);
  }
  const model = JSON.parse(raw) as {
    finish_images: Record<string, { url: string; view: string; is_primary: boolean; is_demo: boolean }[]>;
    variants: { options: { id: number; swatch_hex: string }[] }[];
  };
  expect(model.finish_images[String(seed.colours.arena)].map((image) => [publicId(image.url), image.view, image.is_demo]))
    .toEqual([[ids.arenaMain, "main", false], [ids.arenaBack, "back", true]]);
  expect(model.finish_images[String(seed.colours.glaciar)].map((image) => [publicId(image.url), image.view]))
    .toEqual([[ids.glaciarNew, "front"]]);
  expect(model.finish_images[String(seed.colours.salvia)] ?? []).toEqual([]);
  expect(raw).toContain('"swatch_hex":"#3E6B48"');

  // 1. Model → capacity → colour. Titanio arena: its main picture on the stage and its two thumbnails, nothing else.
  await page.goto(`/modelo/${seed.slug}?variante=${seed.a.slug}`, { waitUntil: "networkidle" });
  await declineConsent(page);
  await expect(page.getByRole("heading", { level: 1, name: seed.name })).toBeVisible({ timeout: 60_000 });
  await expect(radio(page, C.capacity, "128 GB")).toHaveAttribute("aria-checked", "true");
  await expect(radio(page, C.color, "Titanio arena")).toHaveAttribute("aria-checked", "true");
  await expect(stage(page).getByRole("img", { name: `${seed.name} Titanio arena` })).toBeVisible();
  await expect.poll(() => galleryIds(page)).toEqual(["arenaBack", "arenaMain"]);
  const thumbs = page.getByRole("group", { name: "Elegir foto" }).getByRole("button");
  await expect(thumbs).toHaveCount(2);
  await thumbs.nth(1).click();
  await expect(thumbs.nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => decodeURIComponent((await stage(page).locator("img").last().getAttribute("src")) ?? ""))
    .toContain(ids.arenaBack);

  // 2. Azul glaciar: only its (replaced) picture; never the old file, never Titanio arena's.
  await radio(page, C.color, "Azul glaciar").click();
  await expect(page).toHaveURL(new RegExp(`variante=${seed.b.slug}$`));
  await expect(stage(page).getByRole("img", { name: `${seed.name} Azul glaciar` })).toBeVisible();
  await expect.poll(() => galleryIds(page)).toEqual(["glaciarNew"]);
  await expect(page.getByRole("group", { name: "Elegir foto" })).toHaveCount(0);

  // 3. Verde salvia has no picture: the neutral «Imagen pendiente», never another colour's photo.
  await radio(page, C.color, "Verde salvia").click();
  await expect(page).toHaveURL(new RegExp(`variante=${seed.d.slug}$`));
  await expect(stage(page).getByTestId("image-pending")).toBeVisible();
  await expect(stage(page).getByTestId("image-pending")).toHaveAccessibleName(/Imagen pendiente/);
  await expect.poll(() => galleryIds(page)).toEqual([]);

  // 4. The chips: a picture for the colours that have one; Verde salvia, the model's own swatch (not the company's).
  for (const colour of ["Titanio arena", "Azul glaciar"]) {
    await expect(radio(page, C.color, colour).getByTestId("v4-chip-picture")).toHaveCount(1);
  }
  const salviaChip = radio(page, C.color, "Verde salvia");
  await expect(salviaChip.getByTestId("v4-chip-picture")).toHaveCount(0);
  await expect(salviaChip.locator(".v4-swatch")).toHaveCSS("background-color", "rgb(62, 107, 72)");
  // The shop never says «aproximada».
  await expect(page.getByText(/aproximad/i)).toHaveCount(0);

  // 5. A deep link restores the choice, in the first HTML: Azul glaciar, and the pending colour.
  await page.goto(`/modelo/${seed.slug}?variante=${seed.b.slug}`, { waitUntil: "networkidle" });
  await expect(radio(page, C.color, "Azul glaciar")).toHaveAttribute("aria-checked", "true");
  await expect.poll(() => galleryIds(page)).toEqual(["glaciarNew"]);
  await page.goto(`/modelo/${seed.slug}?variante=${seed.d.slug}`, { waitUntil: "networkidle" });
  await expect(radio(page, C.color, "Verde salvia")).toHaveAttribute("aria-checked", "true");
  await expect(stage(page).getByTestId("image-pending")).toBeVisible();
  await expect.poll(() => galleryIds(page)).toEqual([]);
  // 256 GB exists only in Titanio arena: choosing it moves the colour there, with its own picture.
  await radio(page, C.capacity, "256 GB").click();
  await expect(radio(page, C.color, "Titanio arena")).toHaveAttribute("aria-checked", "true");
  await expect.poll(() => galleryIds(page)).toEqual(["arenaBack", "arenaMain"]);
  await context.close();
});

test("«Usar la muestra de la empresa» devuelve el chip a la muestra de la empresa", async ({ browser }) => {
  test.setTimeout(120_000);
  const { context, page } = await adminPage(browser);
  await page.setViewportSize({ width: 1280, height: 900 });
  const region = await openModelPanel(page);
  const salvia = finishItem(region, "Verde salvia");
  await salvia.getByRole("button", { name: "Ajustar muestra de Verde salvia" }).click();
  await salvia.getByRole("button", { name: "Usar la muestra de Verde salvia de la empresa" }).click();
  await expect(region.getByText(`${seed.name} vuelve a usar la muestra de Verde salvia de la empresa.`)).toBeVisible({ timeout: 30_000 });
  // Focus goes to the model's (now empty) field, never to the top of the page.
  await expect(salvia.getByLabel(`Muestra de Verde salvia solo en ${seed.name} (#RRGGBB)`)).toBeFocused();
  await expect(salvia.getByLabel(`Muestra de Verde salvia solo en ${seed.name} (#RRGGBB)`)).toHaveValue("");
  await expect(salvia.getByText("Muestra propia de este modelo")).toHaveCount(0);
  const option = finishOf(await listing(), seed.colours.salvia).option;
  expect(option).toMatchObject({ model_swatch_hex: "", shown_swatch_hex: seed.companySwatch.salvia });

  const shop = await browser.newPage();
  await shop.goto(`/modelo/${seed.slug}?variante=${seed.d.slug}`, { waitUntil: "networkidle" });
  const expected = await shop.evaluate((hex) => {
    const probe = document.createElement("span");
    probe.style.backgroundColor = hex;
    document.body.append(probe);
    const value = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return value;
  }, seed.companySwatch.salvia);
  await expect(radio(shop, seed.steps.color, "Verde salvia").locator(".v4-swatch")).toHaveCSS("background-color", expected);
  await shop.close();

  // Put the model's own swatch back (not approximate) for the visual review.
  await salvia.getByLabel(`Muestra de Verde salvia solo en ${seed.name} (#RRGGBB)`).fill("#3E6B48");
  await salvia.getByRole("button", { name: "Guardar muestra de este modelo" }).click();
  await expect(region.getByText(`Muestra de Verde salvia guardada para ${seed.name}.`)).toBeVisible({ timeout: 30_000 });
  await context.close();
});

test("carrito y checkout conservan la foto del acabado y la configuración (sin pagar)", async ({ browser }) => {
  test.setTimeout(180_000);
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  await page.goto(`/modelo/${seed.slug}?variante=${seed.b.slug}`, { waitUntil: "networkidle" });
  await declineConsent(page);
  await expect(radio(page, seed.steps.color, "Azul glaciar")).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Agregar al carrito" }).click();
  await expect(page.getByText("Agregado al carrito.")).toBeVisible({ timeout: 20_000 });
  shopperState = await context.storageState();

  await page.goto("/cart", { waitUntil: "networkidle" });
  await expect(page.getByText(seed.b.name).first()).toBeVisible({ timeout: 30_000 });
  const thumb = page.getByTestId("cart-thumb").first().locator("img");
  // The replaced file, never the old one nor another colour's.
  await expect(thumb).toHaveAttribute("src", new RegExp(ids.glaciarNew));
  for (const other of [ids.glaciarOld, ids.arenaMain, ids.arenaBack]) {
    await expect(thumb).not.toHaveAttribute("src", new RegExp(other));
  }
  const device = page.getByRole("list", { name: `Equipos de ${seed.b.name}` }).getByRole("listitem").first();
  await expect(device).toContainText("IMEI termina en");
  await expect(device).not.toContainText("Batería");

  await page.getByRole("link", { name: "Continuar al checkout" }).click();
  await page.waitForURL(/\/checkout$/);
  await expect(page.getByText(seed.b.name).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("cart-thumb").first().locator("img")).toHaveAttribute("src", new RegExp(ids.glaciarNew));
  await expect(page.getByText("4299.00").first()).toBeVisible();
  await page.locator("#checkout-customer-name").fill("Compradora De Prueba");
  await page.locator("#checkout-customer-email").fill("compradora@example.invalid");
  // Stop here: no payment is opened (no request to create a checkout session ever leaves the page).
  let paid = false;
  page.on("request", (request) => { if (request.url().includes("create-checkout-session")) paid = true; });
  await page.waitForTimeout(500);
  expect(paid).toBe(false);
  await context.close();
});

test("caja: Modelo → Capacidad → Color → SIM → Región → «Llegó el equipo» → venta; un equipo nuevo no pide batería", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await adminPage(browser);
  await page.setViewportSize({ width: 1280, height: 900 });
  const S = seed.steps;
  const choose = (step: string, option: string) =>
    page.getByRole("radiogroup", { name: step }).locator("label").filter({ hasText: option }).first().click();
  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Escanear código")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Vender equipo por modelo" }).click();
  await page.getByLabel("Modelo", { exact: true }).fill(seed.name);
  await page.getByRole("button", { name: new RegExp(seed.name) }).click();
  await expect(page.getByRole("radiogroup", { name: S.capacity })).toBeVisible({ timeout: 20_000 });

  // The colour chips: finish picture for the colours that have one, the model's own swatch for Verde salvia.
  const colours = page.getByRole("radiogroup", { name: S.color });
  await expect(colours.locator("label").filter({ hasText: "Titanio arena" }).getByTestId("pos-chip-picture")).toHaveCount(1);
  await expect(colours.locator("label").filter({ hasText: "Azul glaciar" }).getByTestId("pos-chip-picture")).toHaveCount(1);
  const salvia = colours.locator("label").filter({ hasText: "Verde salvia" });
  await expect(salvia.getByTestId("pos-chip-picture")).toHaveCount(0);
  await expect(salvia.locator("span[style]").first()).toHaveCSS("background-color", "rgb(62, 107, 72)");

  // An existing variant says its own stock here.
  await choose(S.capacity, "128 GB");
  await choose(S.color, "Titanio arena");
  await choose(S.sim, "eSIM");
  await choose(S.region, "Internacional");
  await expect(page.getByRole("region", { name: "Equipo elegido" }).getByText("2 en esta sucursal")).toBeVisible();

  // A combination that is not a variant yet: 256 GB Azul glaciar.
  await choose(S.capacity, "256 GB");
  await choose(S.color, "Azul glaciar");
  await choose(S.sim, "eSIM");
  await choose(S.region, "Internacional");
  await page.getByRole("button", { name: "Llegó el equipo" }).click();
  const form = page.getByRole("form", { name: "Llegó el equipo" });
  await expect(form.getByLabel("IMEI 1")).toBeFocused();
  // A NEW device: nothing of a used one (battery, grade, cosmetic state).
  await expect(form.getByText(/bater[ií]a|grado|est[eé]tic|seminuevo/i)).toHaveCount(0);
  await expect(form.getByLabel(/bater[ií]a|grado/i)).toHaveCount(0);
  const device = imei(111);
  await form.getByLabel("IMEI 1").fill(device);
  await form.getByLabel("Precio de venta").fill("4799.00");
  await form.getByRole("button", { name: "Registrar y agregar a la venta" }).click();
  await expect(page.getByText(/equipo registrado y agregado a la venta/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(new RegExp(`IMEI ${device}`)).first()).toBeVisible();

  await page.getByRole("checkbox").first().check();
  await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
  await page.getByLabel("Efectivo recibido").fill("99999");
  const charge = page.getByRole("button", { name: /^cobrar$/i });
  await expect(charge).toBeEnabled({ timeout: 15_000 });
  await charge.click();
  await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });

  const sold = shell<{ variants: number; status: string; condition: string; battery: unknown; options: string[] }>(`
from store import device_catalog
from store.models import Product, StockUnit
unit = StockUnit.objects.select_related('product').get(imei=data['imei'])
out({'variants': Product.objects.filter(product_model_id=data['model']).count(), 'status': unit.status,
     'condition': unit.condition, 'battery': getattr(unit, 'battery_health', None),
     'options': sorted(o.label for o in device_catalog.variant_options_of(unit.product))})
`, { imei: device, model: seed.id });
  expect(sold).toMatchObject({ variants: 5, status: "sold", condition: "new", battery: null });
  expect(sold.options).toEqual(["256 GB", "Azul glaciar", "Internacional", "eSIM"].sort());
  await context.close();
});

test("aislamiento: otra empresa no ve ni toca las fotos por acabado de ésta", async ({ playwright }) => {
  test.setTimeout(180_000);
  const other = shell<{ company: number; model: number }>(`
from django.contrib.auth import get_user_model
from store import device_catalog
from store.company_provisioning import provision_company_access_defaults
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment
User = get_user_model()
company = Company.objects.create(name='F12 otra ' + data['tag'], slug=data['other_slug_f12'])
provision_company_access_defaults(company)
user = User.objects.create_user(username=data['user'], password=data['password'])
membership = Membership.objects.create(user=user, company=company, role='admin', is_active=True)
MembershipRoleAssignment.objects.create(membership=membership, role=CompanyRole.objects.get(company=company, slug='administrador'),
                                        is_active=True)
model = device_catalog.create_model(company=company, name='Modelo propio ' + data['tag'])
out({'company': company.pk, 'model': model.pk})
`, { other_slug_f12: `f12-otra-${TAG.toLowerCase()}`, user: OTHER.username, password: OTHER.password });
  const before = await listing();
  const intruder = await apiLogin(playwright, OTHER);
  try {
    // Its own model's gallery answers (it is an administrator of its company)…
    expect((await api(intruder, "GET", finishes(other.model))).status).toBe(200);
    // …and this company's model is as if it did not exist, for every verb.
    const file = () => png("intrusa.png", drawing([255, 0, 0]));
    const attempts: [string, Reply][] = [
      ["GET listado", await api(intruder, "GET", finishes(seed.id))],
      ["POST subida", await api(intruder, "POST", finishes(seed.id), { multipart: {
        file: file(), color_option_id: String(seed.colours.arena), source_kind: "own_photo" } })],
      ["POST reemplazo", await api(intruder, "POST", finishes(seed.id), { multipart: {
        file: file(), replace: String(glaciarImage), source_kind: "own_photo" } })],
      ["PATCH foto", await api(intruder, "PATCH", `${finishes(seed.id)}${glaciarImage}/`, { json: { view: "back", is_primary: true } })],
      ["DELETE foto", await api(intruder, "DELETE", `${finishes(seed.id)}${glaciarImage}/`)],
      ["POST orden", await api(intruder, "POST", `${finishes(seed.id)}order/`, { json: { color_option_id: seed.colours.arena, order: [] } })],
      ["PATCH muestra del modelo", await api(intruder, "PATCH", `/admin/catalog/models/${seed.id}/palette/`, { json: {
        option_id: seed.colours.salvia, swatch_hex: "#FF0000" } })],
      // Its own model, naming this company's picture: «not in this gallery», before anything is stored.
      ["POST reemplazo desde su modelo", await api(intruder, "POST", finishes(other.model), { multipart: {
        file: file(), replace: String(glaciarImage), source_kind: "own_photo" } })],
      ["PATCH foto desde su modelo", await api(intruder, "PATCH", `${finishes(other.model)}${glaciarImage}/`, { json: { view: "back" } })],
    ];
    for (const [what, reply] of attempts) expect(reply.status, `${what}: ${JSON.stringify(reply.body)}`).toBe(404);
    // Naming this company as a SELECTION does not authorise it either.
    const selected = await api(intruder, "GET", `${finishes(seed.id)}?company=${seed.company}`);
    expect([403, 404], JSON.stringify(selected.body)).toContain(selected.status);
    expect(JSON.stringify(selected.body)).not.toContain(ids.glaciarNew);
  } finally {
    await intruder.dispose();
    // In development the public shop resolves its company only while ONE is active (`resolve_storefront_company`):
    // the second company goes inactive at once, or every later page of the shop answers 404.
    deactivateOther();
  }
  // Nothing moved here, and nothing was stored for the intruder's company.
  expect(await listing()).toEqual(before);
  const stored = shell<{ images: number; audits: number }>(`
from store.models import AdminAuditLog, StorefrontImage
out({'images': StorefrontImage.objects.filter(company_id=data['company']).count(),
     'audits': AdminAuditLog.objects.filter(company_id=data['company'], action__startswith='finish_image').count()})
`, { company: other.company });
  expect(stored).toEqual({ images: 0, audits: 0 });
});

test("revisión visual a 360, 390, 768 y 1440 px: sin desborde, foco visible y V4 sólo en su bloque", async ({ browser }) => {
  test.setTimeout(420_000);
  test.skip(!shopperState, "la prueba del carrito no dejó un carrito");
  const shopper = await browser.newContext({ storageState: shopperState });
  const shop = await shopper.newPage();
  const { context, page: panel } = await adminPage(browser);
  const problems: string[] = [];
  const check = (condition: boolean, text: string) => { if (!condition) problems.push(text); };

  for (const viewport of VIEWPORTS) {
    const size = `${viewport.width}x${viewport.height}`;
    await shop.setViewportSize(viewport);
    await panel.setViewportSize(viewport);

    // Model page: Titanio arena, then the colour switch to Azul glaciar.
    await shop.goto(`/modelo/${seed.slug}?variante=${seed.a.slug}`, { waitUntil: "networkidle" });
    await declineConsent(shop);
    await expect(stage(shop).getByRole("img", { name: `${seed.name} Titanio arena` })).toBeVisible({ timeout: 60_000 });
    check(await overflow(shop) <= 1, `modelo ${size}: desborde horizontal`);
    const scope = await v4Scope(shop);
    check(scope.blocks === 1 && scope.block !== "" && scope.root === "" && scope.body === "" && scope.outside === "",
          `modelo ${size}: tokens V4 fuera de su bloque ${JSON.stringify(scope)}`);
    await shot(shop, `modelo-arena-${size}`);
    await radio(shop, seed.steps.color, "Azul glaciar").focus();
    await shop.keyboard.press("ArrowRight");
    const ring = await focusRing(shop);
    check(ring.visible, `modelo ${size}: foco sin anillo visible en ${ring.tag}`);
    await radio(shop, seed.steps.color, "Azul glaciar").click();
    await expect.poll(() => galleryIds(shop)).toEqual(["glaciarNew"]);
    check(await overflow(shop) <= 1, `modelo ${size}: desborde tras cambiar de color`);
    await shot(shop, `modelo-glaciar-${size}`);
    await radio(shop, seed.steps.color, "Verde salvia").click();
    await expect(stage(shop).getByTestId("image-pending")).toBeVisible();
    await shot(stage(shop), `modelo-pendiente-${size}`);

    // Cart and checkout: the cart test's line (Azul glaciar 128 GB).
    await shop.goto("/cart", { waitUntil: "networkidle" });
    await expect(shop.getByTestId("cart-thumb").first().locator("img")).toHaveAttribute("src", new RegExp(ids.glaciarNew), { timeout: 30_000 });
    check(await overflow(shop) <= 1, `carrito ${size}: desborde horizontal`);
    const cartScope = await v4Scope(shop);
    check(cartScope.blocks === 0 && cartScope.root === "", `carrito ${size}: tokens V4 ${JSON.stringify(cartScope)}`);
    await shot(shop, `carrito-${size}`);
    await shop.goto("/checkout", { waitUntil: "networkidle" });
    await expect(shop.getByTestId("cart-thumb").first().locator("img")).toHaveAttribute("src", new RegExp(ids.glaciarNew), { timeout: 30_000 });
    check(await overflow(shop) <= 1, `checkout ${size}: desborde horizontal`);
    const checkoutScope = await v4Scope(shop);
    check(checkoutScope.blocks === 0 && checkoutScope.root === "", `checkout ${size}: tokens V4 ${JSON.stringify(checkoutScope)}`);
    await shop.locator("#checkout-customer-name").focus();
    await shop.keyboard.press("Tab");
    const checkoutRing = await focusRing(shop);
    check(checkoutRing.visible, `checkout ${size}: foco sin anillo visible en ${checkoutRing.tag}`);
    await shot(shop, `checkout-${size}`);

    // Panel: the finish-image manager and the swatch editor.
    const region = await openModelPanel(panel);
    check(await overflow(panel) <= 1, `panel ${size}: desborde horizontal`);
    const panelScope = await v4Scope(panel);
    check(panelScope.blocks === 0 && panelScope.root === "", `panel ${size}: tokens V4 ${JSON.stringify(panelScope)}`);
    await region.scrollIntoViewIfNeeded();
    await shot(region, `panel-acabados-${size}`);
    const salvia = finishItem(region, "Verde salvia");
    // A finish's header (swatch, name, «Imagen pendiente», «Muestra propia…») wraps as whole pieces: a status pill
    // never breaks inside itself on a phone.
    for (const pill of [salvia.getByText("Imagen pendiente", { exact: true }), finishItem(region, "Titanio arena").getByText("2 imágenes", { exact: true })]) {
      check(await textLines(pill) === 1, `panel ${size}: «${await pill.textContent()}» se parte en ${await textLines(pill)} líneas`);
    }
    const adjust = salvia.getByRole("button", { name: "Ajustar muestra de Verde salvia" });
    await adjust.click();
    await expect(adjust).toHaveAttribute("aria-expanded", "true");
    await adjust.focus();
    await panel.keyboard.press("Tab");
    const panelRing = await focusRing(panel);
    check(panelRing.visible, `panel ${size}: foco sin anillo visible en ${panelRing.tag}`);
    check(await overflow(panel) <= 1, `editor de muestra ${size}: desborde horizontal`);
    await shot(salvia, `panel-muestra-${size}`);
    const glaciar = finishItem(region, "Azul glaciar");
    await glaciar.getByRole("button", { name: "Reemplazar foto: imagen 1 de Azul glaciar" }).click();
    check(await overflow(panel) <= 1, `reemplazar foto ${size}: desborde horizontal`);
    await shot(glaciar, `panel-reemplazar-${size}`);

    // Till: the model picker with every step chosen and «Llegó el equipo» open (nothing is registered).
    await panel.goto("/admin/sales/pos", { waitUntil: "networkidle" });
    await expect(panel.getByLabel("Escanear código")).toBeVisible({ timeout: 30_000 });
    await panel.getByRole("button", { name: "Vender equipo por modelo" }).click();
    await panel.getByLabel("Modelo", { exact: true }).fill(seed.name);
    await panel.getByRole("button", { name: new RegExp(seed.name) }).click();
    await expect(panel.getByRole("radiogroup", { name: seed.steps.capacity })).toBeVisible({ timeout: 20_000 });
    for (const [step, option] of [[seed.steps.capacity, "128 GB"], [seed.steps.color, "Verde salvia"], [seed.steps.sim, "eSIM"],
                                  [seed.steps.region, "Internacional"]] as const) {
      await panel.getByRole("radiogroup", { name: step }).locator("label").filter({ hasText: option }).first().click();
    }
    check(await overflow(panel) <= 1, `caja ${size}: desborde horizontal`);
    const tillScope = await v4Scope(panel);
    check(tillScope.blocks === 0 && tillScope.root === "", `caja ${size}: tokens V4 ${JSON.stringify(tillScope)}`);
    await shot(panel, `caja-selector-${size}`);
    await panel.getByRole("button", { name: "Llegó el equipo" }).click();
    await expect(panel.getByLabel("IMEI 1")).toBeFocused();
    check(await overflow(panel) <= 1, `caja ${size}: desborde con «Llegó el equipo»`);
    await shot(panel, `caja-llego-${size}`);
  }
  await shopper.close();
  await context.close();
  expect(problems, problems.join("\n")).toEqual([]);
});

test("cobertura: «Fotos por acabado y licencias» lista el acabado sin fotos y lo quita al recibir una", async ({ browser }) => {
  test.setTimeout(180_000);
  type Licensing = {
    finishes_without_images: number;
    pending_finishes: { model_id: number; option_id: number; model_name: string; option_label: string }[];
    uploads: { total: number; unknown_source: number; unverified_license: number; demo: number };
  };
  const report = async () => {
    const reply = await api<{ licensing: Licensing }>(adminApi, "GET", "/admin/catalog/image-sources/");
    expect(reply.status).toBe(200);
    return reply.body.licensing;
  };
  // The panel step verified this run's pictures (owner, 2026-11-23: verified before they are published); two more
  // wait for their check: one authorised by a supplier and one of the shop's own photos (a finish shows it, so it
  // is verified before it is published, and counts as unverified until then).
  for (const [name, view, origin] of [
    ["arena-lateral.png", "side", { source_kind: "supplier_authorized", license: "Autorización del distribuidor por revisar (prueba E2E)" }],
    ["arena-frontal.png", "front", { source_kind: "own_photo" }],
  ] as const) {
    const pending = await api<FinishImage>(adminApi, "POST", finishes(seed.id), { multipart: {
      file: png(name, drawing(ARENA, { mark: true, back: view === "side" })), color_option_id: String(seed.colours.arena),
      view, ...origin,
    } });
    expect(pending.status, JSON.stringify(pending.body)).toBe(201);
    expect(pending.body.public).toBe(false);
  }
  const before = await report();
  // This model: only Verde salvia lacks pictures (its other colours have them).
  expect(before.pending_finishes.filter((row) => row.model_id === seed.id).map((row) => row.option_id)).toEqual([seed.colours.salvia]);
  // Titanio arena's back is a demo resource; its side and front pictures wait for their licence check.
  expect(before.uploads.demo).toBeGreaterThanOrEqual(1);
  expect(before.uploads.unverified_license).toBeGreaterThanOrEqual(2);

  const { context, page } = await adminPage(browser);
  for (const viewport of [VIEWPORTS[0], VIEWPORTS[3]]) {
    await page.setViewportSize(viewport);
    await page.goto("/admin/products/images", { waitUntil: "networkidle" });
    const panel = page.getByRole("region", { name: "Fotos por acabado y licencias" });
    await expect(panel).toBeVisible({ timeout: 30_000 });
    const counter = panel.getByRole("list", { name: "Contadores de fotos y licencias" }).getByRole("listitem")
      .filter({ hasText: "Acabados sin fotos" });
    await expect(counter).toContainText(String(before.finishes_without_images));
    await expect(panel.getByRole("link", { name: `Fotos de «${seed.name}» en «Verde salvia»` })).toBeVisible();
    expect(await overflow(page), `cobertura ${viewport.width}: desborde horizontal`).toBeLessThanOrEqual(1);
    // «Subir imágenes en lote» sits below its explanation, never over its last line.
    const bulk = page.getByRole("link", { name: "Subir imágenes en lote" });
    const text = page.getByText(/cada archivo va a su producto por el código\./);
    const [linkBox, textBox] = [await bulk.boundingBox(), await text.boundingBox()];
    expect(linkBox!.y, `cobertura ${viewport.width}: el botón tapa el texto`).toBeGreaterThanOrEqual(textBox!.y + textBox!.height);
    await shot(page, `cobertura-${viewport.width}x${viewport.height}`);
  }
  // The link opens the model's finish manager.
  await page.getByRole("region", { name: "Fotos por acabado y licencias" })
    .getByRole("link", { name: `Fotos de «${seed.name}» en «Verde salvia»` }).click();
  await page.waitForURL(new RegExp(`/admin/products/models/${seed.id}#acabados$`));
  await expect(finishItem(page.getByRole("region", { name: "Imágenes por acabado" }), "Verde salvia").getByText("Imagen pendiente"))
    .toBeVisible({ timeout: 30_000 });

  // Verde salvia receives its picture: the report stops listing it, one finish less.
  const placed = await api<FinishImage>(adminApi, "POST", finishes(seed.id), { multipart: {
    file: png("salvia-principal.png", drawing([62, 107, 72])), color_option_id: String(seed.colours.salvia), view: "main",
    source_kind: "own_photo", alt_text: `${seed.name} Verde salvia`,
  } });
  expect(placed.status, JSON.stringify(placed.body)).toBe(201);
  const after = await report();
  expect(after.finishes_without_images).toBe(before.finishes_without_images - 1);
  expect(after.pending_finishes.filter((row) => row.model_id === seed.id)).toEqual([]);
  expect(after.uploads.total).toBe(before.uploads.total + 1);
  await page.goto("/admin/products/images", { waitUntil: "networkidle" });
  await expect(page.getByRole("region", { name: "Fotos por acabado y licencias" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: `Fotos de «${seed.name}» en «Verde salvia»` })).toHaveCount(0);
  await context.close();
});
