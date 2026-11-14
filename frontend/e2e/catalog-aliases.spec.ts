import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { test, expect, type BrowserContext, type Page } from "@playwright/test";

/**
 * CATALOG-COMMERCIAL-01 · A2-2 — «Vocabulario de proveedores», «Probar una descripción» y los alias en la paleta, de
 * punta a punta con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, API y base de datos. Cada escritura se comprueba en la pantalla Y en la base. Datos
 * propios de esta ejecución (`RUN`) en la base que use el backend local (`DATABASE_URL`, `E2E_BACKEND_DIR`). Nunca
 * contra producción.
 *
 * Recorrido: un alias sembrado se ve con su destino → crear un alias que choca con una etiqueta (rechazo junto al campo)
 * → crearlo bien → duplicado → borrarlo con confirmación → otra empresa responde 404 → sin `products.manage` sólo lee
 * (y el servidor responde 403) → el resolutor lee un alias, marca lo desconocido como duda y no escribe nada → la paleta
 * muestra los alias de cada opción → 390 px y 1280 px sin desborde horizontal.
 *
 * Persiste a propósito: los atributos con rol de la empresa y sus opciones (compartidos entre corridas) y la segunda
 * empresa, desactivada. Nada aquí es de Apple ni de la tienda piloto.
 */

const RUN = Date.now().toString(36);
/** Letters only: an alias is never only digits nor a capacity, and the run must not look like one. */
const TAG = RUN.replace(/\d/g, (d) => "abcdefghij"[Number(d)]);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const SHOTS = process.env.A2UI_SHOTS ?? "test-results/catalog-aliases";
const MODEL = `Modelo vocab ${TAG}`;
const SEEDED_ALIAS = `Noir ${TAG}`;
const NEW_ALIAS = `Celeste ${TAG}`;
const UNKNOWN_COLOUR = `Grafito ${TAG}`;
fs.mkdirSync(SHOTS, { recursive: true });

function shell(code: string): string {
  return execFileSync("python3", ["manage.py", "shell", "-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_MODEL: MODEL, E2E_RUN: RUN, E2E_TAG: TAG },
  }).toString();
}

const PREAMBLE = `
import json, os
from store.models import CatalogAlias, Company, ModelOption, Product, ProductModel, VariantAttribute, VariantOption
from store import catalog_aliases, device_catalog
company = Company.objects.get(slug='black-dog-store')
`;

/** This run's aliases as the database holds them (not what the screen says). */
function storedAliases(): { alias: string; scope: string; option: string | null }[] {
  const out = shell(`${PREAMBLE}
rows = [{'alias': a.alias, 'scope': a.scope, 'option': a.option.label if a.option_id else None}
        for a in CatalogAlias.objects.filter(company=company, alias_key__contains=os.environ['E2E_TAG']).select_related('option')]
print('E2E_JSON' + json.dumps(rows))
`);
  return JSON.parse(out.split("E2E_JSON")[1]);
}

let ADMIN: Awaited<ReturnType<BrowserContext["cookies"]>> = [];
let SALES: Awaited<ReturnType<BrowserContext["cookies"]>> = [];
let MODEL_ID = 0;
let IDS = { blue: 0, black: 0, foreignOption: 0 };

async function signIn(page: Page, username: string) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  const use = card.locator("li").filter({ hasText: username }).getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) test.skip(true, `${username} no existe: siembra con seed_demo_users`);
  await use.first().click();
  for (let intento = 0; intento < 3; intento++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 15_000 });
      return;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión");
      await page.waitForTimeout(62_000);
    }
  }
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser, baseURL }) => {
  test.setTimeout(300_000);
  // The company's ROLE attributes (whatever they are called), a model with a palette of 1 capacity × 2 colours ×
  // 1 SIM × 1 region and ONE variant (128 · Negro · eSIM · Internacional), a colour alias of this run for «Negro», and
  // a second company with an option of its own.
  const seeded = shell(`${PREAMBLE}
def attribute(role, name, kind):
    found = VariantAttribute.objects.filter(company=company, role=role).first()
    if found is None:
        found = VariantAttribute.objects.filter(company=company, name__iexact=name).first()
        if found is None:
            found = device_catalog.create_attribute(company=company, name=name, kind=kind, role=role)
        elif found.role != role:
            device_catalog.update_attribute(found, role=role)
    return found

def option(attr, label, swatch=''):
    found = attr.options.filter(label__iexact=label).first()
    return found or device_catalog.create_option(company=company, attribute_id=attr.pk, label=label, swatch_hex=swatch)

capacity = attribute('capacity', 'Capacidad', 'text')
colour = attribute('color', 'Color', 'color')
sim = attribute('sim', 'SIM', 'text')
region = attribute('region', 'Región', 'text')
gb128 = option(capacity, '128 GB')
black, blue = option(colour, 'Negro', '#111111'), option(colour, 'Azul', '#1D4ED8')
esim, intl = option(sim, 'eSIM'), option(region, 'Internacional')
model = device_catalog.create_model(company=company, name=os.environ['E2E_MODEL'])
from store import model_palette
model_palette.set_palette(model, option_ids=[gb128.pk, black.pk, blue.pk, esim.pk, intl.pk])
variant = Product.objects.create(company=company, name=os.environ['E2E_MODEL'] + ' 128 Negro', price='1000.00',
                                 slug='vocab-' + os.environ['E2E_RUN'], is_published_online=False)
device_catalog.set_variant(variant, model_id=model.pk, option_ids=[gb128.pk, black.pk, esim.pk, intl.pk])
catalog_aliases.create_alias(company=company, scope='option', alias='Noir ' + os.environ['E2E_TAG'], option_id=black.pk)

other, _ = Company.objects.get_or_create(slug='vocab-otra', defaults={'name': 'Otra empresa vocabulario'})
other.is_active = True
other.save(update_fields=['is_active'])
foreign_attr = VariantAttribute.objects.filter(company=other).first() or device_catalog.create_attribute(
    company=other, name='Color', kind='color', role='color')
foreign = foreign_attr.options.first() or device_catalog.create_option(
    company=other, attribute_id=foreign_attr.pk, label='Ajeno', swatch_hex='#222222')
print('E2E_IDS' + json.dumps({'model': model.pk, 'blue': blue.pk, 'black': black.pk, 'foreign_option': foreign.pk}))
`);
  const ids = JSON.parse(seeded.split("E2E_IDS")[1]);
  MODEL_ID = ids.model;
  IDS = { blue: ids.blue, black: ids.black, foreignOption: ids.foreign_option };
  for (const [username, keep] of [["dev_admin", (c: typeof ADMIN) => { ADMIN = c; }], ["dev_sales", (c: typeof ADMIN) => { SALES = c; }]] as const) {
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    await signIn(page, username);
    keep(await context.cookies());
    await context.close();
  }
});

test.afterAll(() => {
  // Only this run's rows: its aliases, the model's palette, its variant and the model. The other company is
  // deactivated. The attributes and their options stay (shared catalogue data).
  shell(`${PREAMBLE}
from store.models import ProductVariantOption
CatalogAlias.objects.filter(company=company, alias_key__contains=os.environ['E2E_TAG']).delete()
for model in ProductModel.objects.filter(company=company, name=os.environ['E2E_MODEL']):
    ModelOption.objects.filter(model=model).delete()
    for product in Product.objects.filter(company=company, name__startswith=os.environ['E2E_MODEL']):
        ProductVariantOption.objects.filter(product=product).delete()
        product.delete()
    model.delete()
other = Company.objects.filter(slug='vocab-otra').first()
if other:
    other.is_active = False
    other.save(update_fields=['is_active'])
print('E2E_CLEAN')
`);
});

test.beforeEach(async ({ context }) => {
  if (ADMIN.length) await context.addCookies(ADMIN);
});

const vocabulary = (page: Page) => page.getByRole("region", { name: "Vocabulario de proveedores" });
const resolver = (page: Page) => page.getByRole("region", { name: "Probar una descripción" });
const palette = (page: Page) => page.getByRole("region", { name: "Opciones de este modelo" });
const aliasRow = (page: Page, text: string) => vocabulary(page).getByRole("listitem", { name: `Alias ${text}` });

async function openVocabulary(page: Page) {
  await page.goto("/admin/products/models#vocabulario-opciones", { waitUntil: "networkidle" });
  await expect(vocabulary(page).getByRole("tab", { name: /Opciones/ })).toHaveAttribute("aria-selected", "true");
  await expect(aliasRow(page, SEEDED_ALIAS)).toBeVisible();
}

/** No horizontal scroll of the whole page (wide content scrolls in its own container). */
async function expectNoOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

/** A call of the API with the page's own session (cookie + CSRF), as the screen would make it. */
async function api(page: Page, method: string, url: string, body?: unknown) {
  return page.evaluate(async ({ method, url, body }) => {
    const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
    const res = await fetch(url, {
      method, credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return { status: res.status, body: await res.json().catch(() => null) };
  }, { method, url, body });
}

/** Writes the page answered with (a redirect hop of the dev proxy is not a second write). */
function trackWrites(page: Page): string[] {
  const writes: string[] = [];
  page.on("response", (r) => {
    const method = r.request().method();
    if (method !== "GET" && r.status() < 300 && r.url().includes("/api/admin/")) {
      writes.push(`${method} ${new URL(r.url()).pathname.replace(/\/?$/, "/")}`);
    }
  });
  return writes;
}

test("1 · el vocabulario lista cada alias con su destino real, por pestañas, sin desborde (1280 px)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openVocabulary(page);
  await expect(aliasRow(page, SEEDED_ALIAS)).toContainText(`«${SEEDED_ALIAS}» es la opción Negro`);
  // Keyboard: the tabs move with the arrows.
  await vocabulary(page).getByRole("tab", { name: /Opciones/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(vocabulary(page).getByRole("tab", { name: /Palabras de modelo/ })).toBeFocused();
  await expect(vocabulary(page).getByRole("tab", { name: /Palabras de modelo/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(vocabulary(page).getByRole("tab", { name: /Opciones/ })).toBeFocused();
  await expectNoOverflow(page);
  await vocabulary(page).scrollIntoViewIfNeeded();
  await vocabulary(page).screenshot({ path: path.join(SHOTS, "vocabulario-1280.png") });
});

test("2 · crear: un choque con una etiqueta se dice junto al campo; luego se crea, el duplicado se rechaza y se borra con confirmación", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openVocabulary(page);
  const writes = trackWrites(page);
  await vocabulary(page).getByRole("button", { name: "+ Agregar alias" }).click();
  const form = vocabulary(page).getByRole("form", { name: /Nuevo alias/ });
  const text = form.getByLabel("Texto del proveedor");
  // «Azul» IS the option's label: the server refuses it (conflict_with_label), the client never pre-judges it.
  await text.fill("Azul");
  await form.getByLabel("Opción").selectOption(String(IDS.blue));
  await form.getByRole("button", { name: "Agregar alias" }).click();
  await expect(text).toHaveAttribute("aria-invalid", "true");
  await expect(form.getByText(/^Choca con un nombre real: /)).toBeVisible();
  await expect(text).toBeFocused();

  await text.fill(NEW_ALIAS);
  await form.getByRole("button", { name: "Agregar alias" }).click();
  await expect(vocabulary(page).getByRole("status")).toContainText(`Alias «${NEW_ALIAS}» agregado.`);
  await expect(aliasRow(page, NEW_ALIAS)).toContainText(`«${NEW_ALIAS}» es la opción Azul`);
  expect(storedAliases()).toContainEqual({ alias: NEW_ALIAS, scope: "option", option: "Azul" });

  // The same text again: «Ya existe», at the field.
  await text.fill(NEW_ALIAS.toUpperCase());
  await form.getByLabel("Opción").selectOption(String(IDS.blue));
  await form.getByRole("button", { name: "Agregar alias" }).click();
  await expect(form.getByText(/^Ya existe: /)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "vocabulario-conflicto-1280.png"), fullPage: false });

  // Delete: an explicit confirmation first; nothing is sent until «Sí, borrar».
  await form.getByRole("button", { name: "Cancelar" }).click();
  const remove = aliasRow(page, NEW_ALIAS).getByRole("button", { name: `Borrar alias ${NEW_ALIAS}` });
  await remove.click();
  const confirm = vocabulary(page).getByRole("group", { name: `Borrar alias «${NEW_ALIAS}»` });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "Cancelar" }).click();
  await expect(remove).toBeFocused();
  expect(storedAliases().some((a) => a.alias === NEW_ALIAS)).toBe(true);
  await remove.click();
  await vocabulary(page).getByRole("group", { name: `Borrar alias «${NEW_ALIAS}»` }).getByRole("button", { name: "Sí, borrar" }).click();
  await expect(vocabulary(page).getByRole("status")).toContainText(`Se borró el alias «${NEW_ALIAS}».`);
  await expect(aliasRow(page, NEW_ALIAS)).toHaveCount(0);
  expect(storedAliases().some((a) => a.alias === NEW_ALIAS)).toBe(false);
  expect(writes.filter((w) => w.startsWith("POST")).length).toBe(1);
  expect(writes.filter((w) => w.startsWith("DELETE")).length).toBe(1);

  // An option of another company answers like one that does not exist.
  const foreign = await api(page, "POST", "/api/admin/catalog/aliases/", { scope: "option", alias: `Ajeno ${TAG}`, option_id: IDS.foreignOption });
  expect(foreign.status).toBe(404);
  expect(storedAliases().some((a) => a.alias === `Ajeno ${TAG}`)).toBe(false);
});

test("3 · sin `products.manage` se lee, sin formularios, y el servidor niega la escritura (403)", async ({ page, context }) => {
  await context.clearCookies();
  await context.addCookies(SALES);
  await page.setViewportSize({ width: 1280, height: 900 });
  await openVocabulary(page);
  await expect(vocabulary(page).getByRole("button", { name: /Agregar alias/ })).toHaveCount(0);
  await expect(vocabulary(page).getByRole("button", { name: /Editar alias|Borrar alias/ })).toHaveCount(0);
  const denied = await api(page, "POST", "/api/admin/catalog/aliases/", { scope: "option", alias: `Denegado ${TAG}`, option_id: IDS.black });
  expect(denied.status).toBe(403);
  // The resolver is a read: it works for whoever sees the catalogue.
  await resolver(page).getByLabel("Modelo").fill(MODEL);
  await resolver(page).getByRole("button", { name: "Probar" }).click();
  await expect(resolver(page).getByRole("region", { name: "Resultado" })).toBeVisible();
  expect(storedAliases().some((a) => a.alias === `Denegado ${TAG}`)).toBe(false);
});

test("4 · «Probar una descripción» lee el alias, deja lo desconocido como duda con sus acciones (sin ejecutarlas) y no escribe", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/admin/products/models#probar-descripcion", { waitUntil: "networkidle" });
  const panel = resolver(page);
  const before = storedAliases();
  const writes = trackWrites(page);
  await panel.getByLabel("Modelo").fill(MODEL);
  await panel.getByLabel("Capacidad").fill("128gb");
  await panel.getByLabel("Color (uno o varios)").fill(`${SEEDED_ALIAS}, ${UNKNOWN_COLOUR}`);
  await panel.getByLabel("SIM").fill("eSIM");
  await panel.getByLabel("Región").fill("Internacional");
  await panel.getByRole("button", { name: "Probar" }).click();
  const result = panel.getByRole("region", { name: "Resultado" });
  await expect(result.getByRole("heading", { name: "Resultado" })).toBeFocused();
  await expect(result).toContainText(`Por su nombre → ${MODEL}`);
  const cards = result.getByRole("article");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toHaveAccessibleName("Configuración 1 de 2: Con variante");
  await expect(cards.nth(0)).toContainText(`Color: «${SEEDED_ALIAS}» → Negro (por un alias de la empresa)`);
  await expect(cards.nth(0)).toContainText("Capacidad: «128gb» → 128 GB");
  await expect(cards.nth(0).getByRole("link", { name: `${MODEL} 128 Negro` })).toBeVisible();
  await expect(cards.nth(1)).toHaveAccessibleName("Configuración 2 de 2: Opción desconocida");
  await expect(cards.nth(1)).toContainText(`Color: «${UNKNOWN_COLOUR}»`);
  await expect(cards.nth(1)).toContainText("Desconocido");
  await expect(cards.nth(1)).toContainText("Qué puede hacer una persona (no se hace desde aquí)");
  await expect(cards.nth(1)).toContainText("Agregarla a la paleta del modelo");
  await expect(result.getByRole("button")).toHaveCount(0);
  await panel.getByRole("button", { name: "¿Qué significa cada estado?" }).click();
  await expect(panel.getByRole("button", { name: "¿Qué significa cada estado?" })).toHaveAttribute("aria-expanded", "true");
  await expectNoOverflow(page);
  await panel.screenshot({ path: path.join(SHOTS, "probar-descripcion-dudas-1280.png") });
  // A read (a POST that writes nothing): its only call is the resolver's, and no alias was added.
  expect(writes).toEqual(["POST /api/admin/catalog/resolve/"]);
  expect(storedAliases()).toEqual(before);
});

test("5 · la paleta muestra los alias de cada opción, desplegables, con enlace al vocabulario (1280 px)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  // Reads that reached the API (a 308 hop of the dev proxy is not a read); per-chip reads would be one per option.
  const vocabularyReads: string[] = [];
  const aliasListReads: string[] = [];
  page.on("response", (r) => {
    if (r.status() === 308) return;
    if (r.url().includes("/api/admin/catalog/vocabulary")) vocabularyReads.push(r.url());
    if (r.url().includes("/api/admin/catalog/aliases")) aliasListReads.push(r.url());
  });
  await page.goto(`/admin/products/models/${MODEL_ID}`, { waitUntil: "networkidle" });
  const toggle = palette(page).getByRole("button", { name: /^\d+ alias de Negro$/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const list = palette(page).getByRole("list", { name: "Alias de Negro" });
  await expect(list).toContainText(SEEDED_ALIAS);
  await expect(palette(page).getByRole("link", { name: "Gestionar en Vocabulario de proveedores" }))
    .toHaveAttribute("href", "/admin/products/models#vocabulario-opciones");
  // One read for the page: `next dev` runs effects twice (StrictMode), so at most two, never one per option.
  expect(vocabularyReads.length).toBeGreaterThanOrEqual(1);
  expect(vocabularyReads.length).toBeLessThanOrEqual(2);
  expect(aliasListReads).toEqual([]);
  await expectNoOverflow(page);
  await palette(page).screenshot({ path: path.join(SHOTS, "paleta-alias-1280.png") });
  // The link takes to the options tab of the vocabulary.
  await palette(page).getByRole("link", { name: "Gestionar en Vocabulario de proveedores" }).click();
  await expect(vocabulary(page).getByRole("tab", { name: /Opciones/ })).toHaveAttribute("aria-selected", "true");
});

test("6 · teléfono (390 px): vocabulario, resolutor y paleta sin desborde horizontal; botones de 44 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openVocabulary(page);
  await expectNoOverflow(page);
  const heights = await vocabulary(page).getByRole("button").evaluateAll((nodes) => nodes.map((n) => n.getBoundingClientRect().height));
  expect(Math.min(...heights)).toBeGreaterThanOrEqual(44);
  await vocabulary(page).scrollIntoViewIfNeeded();
  await vocabulary(page).screenshot({ path: path.join(SHOTS, "vocabulario-390.png") });

  const panel = resolver(page);
  await panel.getByLabel("Modelo").fill(MODEL);
  await panel.getByLabel("Capacidad").fill("128gb");
  await panel.getByLabel("Color (uno o varios)").fill(`${SEEDED_ALIAS}, ${UNKNOWN_COLOUR}`);
  await panel.getByRole("button", { name: "Probar" }).click();
  await expect(panel.getByRole("region", { name: "Resultado" }).getByRole("article")).toHaveCount(2);
  await expectNoOverflow(page);
  await panel.screenshot({ path: path.join(SHOTS, "probar-descripcion-dudas-390.png") });

  await page.goto(`/admin/products/models/${MODEL_ID}`, { waitUntil: "networkidle" });
  await palette(page).getByRole("button", { name: /^\d+ alias de Negro$/ }).click();
  await expect(palette(page).getByRole("list", { name: "Alias de Negro" })).toBeVisible();
  await expectNoOverflow(page);
  await palette(page).screenshot({ path: path.join(SHOTS, "paleta-alias-390.png") });

  for (const width of [320, 360, 375, 414, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/admin/products/models#vocabulario-opciones", { waitUntil: "networkidle" });
    await expect(aliasRow(page, SEEDED_ALIAS)).toBeVisible();
    await expectNoOverflow(page);
  }
});
