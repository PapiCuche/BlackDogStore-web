import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { test, expect, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { apiBase, RUN, shell } from "./inventory-first-support";

/**
 * CATALOG-COMMERCIAL-01 · B1-1 — «Actualizar lista de proveedor», de punta a punta con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, el vocabulario y la resolución del servidor (A2), el ensayo del importador
 * (`/text-preview/offer-draft/`), el libro .xlsx que arma el servidor, el importador oficial con B0 y la base. Nada se
 * simula con `page.route`.
 *
 * DATOS: una empresa PROPIA de esta corrida (`b1-<corrida>`), sembrada desde `docs/catalog-commercial/training-lines.json`
 * (paletas, variantes y «Blanco» por el shell de Django; los alias por la API, como lo haría una persona), con su
 * proveedor y tres usuarios: con `costs.view`, sin `costs.view` y sin `products.manage`. La base debe ser local y
 * desechable (guarda de `inventory-first-support`). Al terminar, la empresa y sus usuarios quedan desactivados.
 *
 * Recorrido: las 14 líneas (19 configuraciones; R-4 P1/P2 sin tocar) → guardado parcial de dos configuraciones limpias
 * por el importador oficial → la misma lista otra vez: `already_applied_content` (B0) → «Crear variante» nace borrador
 * → sin `costs.view` → sin `products.manage` → 390 y 1280 px sin desborde horizontal, con capturas.
 */

const SHOTS = process.env.B1_SHOTS ?? "test-results/supplier-whatsapp-assistant";
fs.mkdirSync(SHOTS, { recursive: true });

// A throwaway password per run, never a literal in a public repository (as bulk-images / catalog-variant-images).
const PASSWORD = `B1l-${crypto.randomBytes(9).toString("base64url")}!`;
const SLUG = `b1-${RUN.toLowerCase()}`;
const USERS = {
  manager: `qaeb1_${RUN.toLowerCase()}_m`,
  nocost: `qaeb1_${RUN.toLowerCase()}_n`,
  viewer: `qaeb1_${RUN.toLowerCase()}_v`,
};
/** A second company whose palettes have NO compound colour (no «Glaciar…» label): review H1. */
const PLAIN_SLUG = `b1p-${RUN.toLowerCase()}`;
const PLAIN_USERS = { manager: `qaeb1_${RUN.toLowerCase()}_p` };
const FIXTURE_PATH = path.resolve(process.cwd(), "..", "docs", "catalog-commercial", "training-lines.json");
type FixtureLine = { n: number; text: string; amount: number; candidates: { color_text: string | null; expected: string }[] };
const FIXTURE = JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as { lines: FixtureLine[] };
const LIST = FIXTURE.lines.map((line) => line.text).join("\n");
const SUMMARY_19 = "14 líneas · 19 configuraciones · 2 fuera de la paleta · 8 incompleta · 4 con variante · 5 sin variante";

/** A day as the server counts it (America/Lima), `offset` days from today. */
function limaDay(offset = 0): string {
  const now = new Date(Date.now() + offset * 86_400_000);
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Lima", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}
const MESSAGE_DAY = limaDay(-1);

type Seed = { company: number; supplier: number; options: Record<string, Record<string, number>> };
let SEED: Seed;
let PLAIN: Seed;
let PLAIN_STATE: Awaited<ReturnType<BrowserContext["storageState"]>> | null = null;
const STATES: Record<keyof typeof USERS, Awaited<ReturnType<BrowserContext["storageState"]>> | null> = { manager: null, nocost: null, viewer: null };

function sembrar(slug = SLUG, users: Record<string, string> = USERS, compounds = true): Seed {
  return shell<Seed>(`
from django.contrib.auth import get_user_model
from store import device_catalog, model_palette
from store.company_provisioning import provision_company_access_defaults
from store.models import Company, CompanyRole, Membership, MembershipRoleAssignment, Product, Supplier
User = get_user_model()
tenant = json.load(open(data['fixture'], encoding='utf-8'))['tenant_a']
company = Company.objects.create(name='Empresa B1 ' + data['tag'], slug=data['b1'])
provision_company_access_defaults(company)
ROLES = ('capacity', 'color', 'sim', 'region')
attrs = {
    'capacity': device_catalog.create_attribute(company=company, name='Capacidad', role='capacity', sort_order=0),
    'color': device_catalog.create_attribute(company=company, name='Color', kind='color', role='color', sort_order=1),
    'sim': device_catalog.create_attribute(company=company, name='Tipo de SIM', role='sim', sort_order=2),
    'region': device_catalog.create_attribute(company=company, name='Región', role='region', sort_order=3),
}
options = {role: {} for role in ROLES}
def option(role, label):
    if label not in options[role]:
        options[role][label] = device_catalog.create_option(company=company, attribute_id=attrs[role].pk, label=label,
                                                             swatch_hex='#777777' if role == 'color' else '')
    return options[role][label]
def keep(role, label):
    return data['compounds'] or role != 'color' or ' ' not in label
for n, (name, palette) in enumerate(tenant['palette'].items()):
    model = device_catalog.create_model(company=company, name=name)
    model_palette.set_palette(model, option_ids=[option(role, label).pk for role in ROLES for label in palette[role] if keep(role, label)])
    for v, variant in enumerate(palette['variants']):
        if not all(keep(r, variant[r]) for r in variant):
            continue
        product = Product.objects.create(
            company=company, name=name + ' ' + ' '.join(variant[r] for r in ROLES if r in variant),
            slug=data['b1'] + '-' + str(n) + '-' + str(v), price=Decimal('1000.00'), inventory=0, is_published_online=False)
        device_catalog.set_variant(product, model_id=model.pk, condition_line='new',
                                   option_ids=[option(r, variant[r]).pk for r in variant])
option('color', 'Blanco')   # a colour of the COMPANY outside every palette (scenario A of the backend test)
supplier = Supplier.objects.create(company=company, name='Proveedor B1 ' + data['tag'])
def make_user(username, caps):
    user = User.objects.create_user(username=username, password=data['password'])
    role = CompanyRole.objects.create(company=company, name='Rol ' + username, slug=username.replace('_', '-'), capabilities=caps)
    membership = Membership.objects.create(user=user, company=company, role='customer', is_active=True,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)
base = ['company.view', 'products.view']
for who, caps in (('manager', ['products.manage', 'costs.view']), ('nocost', ['products.manage']), ('viewer', [])):
    if who in data['users']:
        make_user(data['users'][who], base + caps)
out({'company': company.pk, 'supplier': supplier.pk,
     'options': {role: {label: o.pk for label, o in found.items()} for role, found in options.items()}})
`, { fixture: FIXTURE_PATH, b1: slug, users, password: PASSWORD, compounds });
}

type Offer = { product: string; commercial: string | null; cost: string | null; quantity: number | null; active: boolean };
function ofertas(): Offer[] {
  return shell<Offer[]>(`
from store.models import SupplierOffer
rows = SupplierOffer.objects.filter(supplier__company__slug=data['b1']).select_related('product').order_by('product__name')
out([{'product': o.product.name, 'commercial': None if o.commercial_price is None else str(o.commercial_price),
      'cost': None if o.agreed_cost is None else str(o.agreed_cost), 'quantity': o.confirmed_quantity, 'active': o.is_active}
     for o in rows])
`, { b1: SLUG });
}

function catalogo() {
  return shell<{ products: number; drafts: { name: string; origin: string; price: string; published: boolean; enabled: boolean; inventory: number; movements: number }[]; aliases: number; models: number; options: number }>(`
from store.models import CatalogAlias, Product, ProductModel, StockMovement, VariantOption
products = Product.objects.filter(company__slug=data['b1'])
out({'products': products.count(), 'aliases': CatalogAlias.objects.filter(company__slug=data['b1']).count(),
     'models': ProductModel.objects.filter(company__slug=data['b1']).count(),
     'options': VariantOption.objects.filter(company__slug=data['b1']).count(),
     'drafts': [{'name': p.name, 'origin': p.draft_origin, 'price': str(p.price), 'published': p.is_published_online,
                 'enabled': p.sale_enabled_at is not None, 'inventory': p.inventory,
                 'movements': StockMovement.objects.filter(product=p).count()} for p in products.exclude(draft_origin='')]})
`, { b1: SLUG });
}

function desactivar() {
  return shell(`
from django.contrib.auth import get_user_model
from store.models import Company
Company.objects.filter(slug__in=data['slugs']).update(is_active=False)
get_user_model().objects.filter(username__in=data['users']).update(is_active=False)
out({'ok': True})
`, { slugs: [SLUG, PLAIN_SLUG], users: [...Object.values(USERS), ...Object.values(PLAIN_USERS)] });
}

/** A session by the API (the same route the login screen uses), kept as a storage state. */
async function login(browser: Browser, username: string) {
  const context = await browser.newContext();
  for (let attempt = 0; ; attempt++) {
    const answer = await context.request.post(`${apiBase()}/auth/login/`, { data: { username, password: PASSWORD } });
    if (answer.status() === 200) break;
    // The login limit (5 per minute, in the server's memory) is shared with the previous run: wait it out, never skip.
    if (answer.status() !== 429 || attempt === 2) throw new Error(`B1-1: no pude iniciar sesión como ${username}: ${answer.status()}`);
    await new Promise((resolve) => setTimeout(resolve, 62_000));
  }
  await context.request.get(`${apiBase()}/auth/csrf/`);
  const state = await context.storageState();
  return { context, state };
}

async function apiPost(context: BrowserContext, url: string, body: unknown) {
  const csrf = (await context.storageState()).cookies.find((cookie) => cookie.name === "csrftoken")?.value;
  const res = await context.request.post(`${apiBase()}${url}`, {
    data: body,
    headers: { ...(csrf ? { "X-CSRFToken": csrf } : {}), Origin: new URL(process.env.E2E_BASE_URL ?? "http://localhost:3002").origin },
  });
  return { status: res.status(), body: await res.json().catch(() => null) };
}

async function session(browser: Browser, who: keyof typeof USERS, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ storageState: STATES[who]!, viewport });
  const page = await context.newPage();
  return { context, page };
}

const assistantUrl = "/admin/products/suppliers/offers/update-list";
const lineOf = (page: Page, n: number) => page.getByRole("article", { name: `Línea ${n}`, exact: true });
const configOf = (page: Page, n: number, c: number) => page.getByRole("article", { name: `Configuración ${c} de la línea ${n}`, exact: true });
const amountOf = (page: Page, n: number, k = 1) => page.getByRole("group", { name: `Importe ${k} de la línea ${n}`, exact: true });
const summary = (page: Page) => page.getByRole("status", { name: "Resumen de la lectura" });
const preview = (page: Page) => page.getByRole("region", { name: "Vista previa de lo que se guardará" });

async function openAndRead(page: Page, text = LIST) {
  await page.goto(assistantUrl, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Actualizar lista de proveedor" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Texto de la lista del proveedor").fill(text);
  const resolved = page.waitForResponse((res) => /\/admin\/catalog\/resolve\/?$/.test(new URL(res.url()).pathname) && res.request().method() === "POST"
    && res.status() !== 308); // a redirect hop of the dev proxy is not the answer
  await page.getByRole("button", { name: "Leer la lista" }).click();
  expect((await resolved).status()).toBe(200);
  await expect(summary(page)).toBeVisible();
}

async function setList(page: Page, { supplier = true } = {}) {
  if (supplier) await page.getByLabel("Proveedor de la lista").selectOption(String(SEED.supplier));
  await page.getByLabel("Plazo de entrega (días)").fill("3");
  await page.getByLabel("Fecha del mensaje del proveedor").fill(MESSAGE_DAY);
  await page.getByLabel("Moneda de la lista").selectOption("PEN");
}

const classify = (page: Page, n: number, kind: string) => amountOf(page, n).getByRole("radio", { name: kind }).check();
const price = (page: Page, n: number, c: number) => page.getByLabel(`Precio comercial · línea ${n}, configuración ${c}`, { exact: true });
const pick = (page: Page, n: number, c: number) => page.getByRole("checkbox", { name: `Incluir en el guardado · línea ${n}, configuración ${c}`, exact: true });

/** No horizontal scroll of the whole page (wide content scrolls in its own box). */
async function expectNoOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function shot(target: Page | Locator, name: string) {
  if ("screenshot" in target && "goto" in target) await target.screenshot({ path: path.join(SHOTS, name), fullPage: true });
  else await (target as Locator).screenshot({ path: path.join(SHOTS, name) });
}

/** Line 1 as a cost, line 5 as a reference; both typed prices; both selected. Returns nothing: the screen says it. */
async function prepareTwo(page: Page) {
  await setList(page);
  await classify(page, 1, "Costo del proveedor");
  await classify(page, 5, "Referencia");
  await price(page, 1, 1).fill("2599");
  await price(page, 5, 1).fill("4999");
  await pick(page, 1, 1).check();
  await pick(page, 5, 1).check();
  await expect(preview(page).getByRole("row")).toHaveCount(3);
}

async function rehearseAndOpenImporter(page: Page) {
  const drafted = page.waitForRequest((req) => req.url().includes("/text-preview/offer-draft/") && !req.url().includes("/file/"));
  await page.getByRole("button", { name: "Ensayar con el importador oficial" }).click();
  const body = (await drafted).postDataJSON() as { complete: boolean; rows: Record<string, unknown>[] };
  await expect(page.getByRole("region", { name: "Resultado del ensayo" })).toBeVisible();
  return body;
}

test.describe.configure({ mode: "serial" });

/** The company's vocabulary, written as a person would: by the A2 endpoint (`products.manage`, audited). */
async function writeAliases(context: BrowserContext, seed: Seed) {
  const aliases = [
    { scope: "option", alias: "lla", option_id: seed.options.region["LL/A"] },
    { scope: "option", alias: "japan", option_id: seed.options.region["J/A"] },
    { scope: "option", alias: "japon", option_id: seed.options.region["J/A"] },
    { scope: "model_word", alias: "iph", replacement: "iphone" },
  ];
  for (const alias of aliases) expect((await apiPost(context, "/admin/catalog/aliases/", alias)).status).toBe(201);
}

test.beforeAll(async ({ browser }) => {
  test.setTimeout(480_000);
  SEED = sembrar();
  PLAIN = sembrar(PLAIN_SLUG, PLAIN_USERS, false);
  for (const who of Object.keys(USERS) as (keyof typeof USERS)[]) {
    const { context, state } = await login(browser, USERS[who]);
    STATES[who] = state;
    if (who === "manager") await writeAliases(context, SEED);
    await context.close();
  }
  const plain = await login(browser, PLAIN_USERS.manager);
  PLAIN_STATE = plain.state;
  await writeAliases(plain.context, PLAIN);
  await plain.context.close();
});

test.afterAll(() => { desactivar(); });

test("las 14 líneas: 19 configuraciones con su estado, sin escribir nada (escritorio 1280)", async ({ browser }) => {
  test.setTimeout(180_000);
  const before = catalogo();
  const { context, page } = await session(browser, "manager");
  const resolveBodies: string[] = [];
  page.on("request", (req) => { if (req.url().includes("/admin/catalog/resolve")) resolveBodies.push(req.postData() ?? ""); });
  await openAndRead(page);
  await expect(summary(page)).toHaveText(SUMMARY_19);
  for (const line of FIXTURE.lines) {
    await expect(lineOf(page, line.n).getByRole("article")).toHaveCount(line.candidates.length);
    for (const [index, candidate] of line.candidates.entries()) {
      const label = { variante_existente: "Con variante", combinacion_valida_sin_variante: "Sin variante",
        fuera_de_paleta: "Fuera de la paleta", incompleta: "Incompleta" }[candidate.expected]!;
      await expect(configOf(page, line.n, index + 1)).toContainText(label);
    }
  }
  // Nothing classified, filled or confirmed by itself; no amount travelled to /resolve.
  for (const radio of await page.getByRole("radio").all()) await expect(radio).not.toBeChecked();
  for (const box of await page.getByRole("checkbox", { name: /^Incluir en el guardado/ }).all()) await expect(box).not.toBeChecked();
  for (const body of resolveBodies) for (const line of FIXTURE.lines) expect(body).not.toContain(String(line.amount));
  await expectNoOverflow(page);
  await shot(page, "desktop-1280-lista-estados.png");
  expect(catalogo()).toEqual(before);
  expect(ofertas()).toEqual([]);
  await context.close();
});

test("guardado parcial por el importador oficial y la misma lista otra vez (B0)", async ({ browser }) => {
  test.setTimeout(240_000);
  const { context, page } = await session(browser, "manager");
  await openAndRead(page);
  await prepareTwo(page);
  await expect(configOf(page, 5, 1)).toContainText("Referencia del proveedor: 4650 PEN");
  await shot(lineOf(page, 1), "desktop-1280-clasificacion-importes.png");
  await shot(preview(page), "desktop-1280-vista-previa-guardado.png");
  const body = await rehearseAndOpenImporter(page);
  expect(body.complete).toBe(false);
  expect(body.rows).toEqual([
    expect.objectContaining({ key: "L1-C1", commercial_price: "2599", confirmed_quantity: null, agreed_cost: "2295" }),
    expect.objectContaining({ key: "L5-C1", commercial_price: "4999", confirmed_quantity: null }),
  ]);
  expect(body.rows[1]).not.toHaveProperty("agreed_cost");
  const previewed = page.waitForResponse((res) => /\/supplier-offers\/import\/preview\/?$/.test(new URL(res.url()).pathname) && res.request().method() === "POST" && res.status() !== 308);
  await page.getByRole("button", { name: "Continuar al importador oficial" }).click();
  expect((await previewed).status()).toBeLessThan(300);
  const importer = page.getByRole("region", { name: "Importador oficial de ofertas" });
  await expect(importer.getByTestId("supplier-import-preview")).toBeVisible();
  await expect(importer.getByText("Es la lista completa de un proveedor")).toHaveCount(0);
  await importer.getByRole("button", { name: "Aplicar cambios" }).click();
  await expect(page.getByText(/El importador oficial aplicó las ofertas/)).toBeVisible();
  const saved = ofertas();
  expect(saved).toEqual([
    { product: "iPhone 15 128 GB Negro", commercial: "2599.00", cost: "2295.00", quantity: null, active: true },
    { product: "iPhone 18 Pro 256 GB Borgoña eSIM LL/A", commercial: "4999.00", cost: null, quantity: null, active: true },
  ]);
  await context.close();

  // The same list, the same date, the same rows: the official dry run says it was already applied (B0).
  const again = await session(browser, "manager");
  await openAndRead(again.page);
  await prepareTwo(again.page);
  await rehearseAndOpenImporter(again.page);
  await expect(again.page.getByRole("region", { name: "Resultado del ensayo" })).toContainText(/ya se carg|idéntica/i);
  const previewedAgain = again.page.waitForResponse((res) => /\/supplier-offers\/import\/preview\/?$/.test(new URL(res.url()).pathname) && res.request().method() === "POST" && res.status() !== 308);
  await again.page.getByRole("button", { name: "Continuar al importador oficial" }).click();
  const answer = (await (await previewedAgain).json()) as { already_applied_content?: unknown };
  expect(answer.already_applied_content).toBeTruthy();
  await expect(again.page.getByTestId("already-applied-content")).toBeVisible();
  expect(ofertas()).toEqual(saved);
  await again.context.close();
});

test("«Crear variante» nace borrador oculto, sin precio ni stock, y la oferta no vende", async ({ browser }) => {
  test.setTimeout(180_000);
  const before = catalogo();
  const { context, page } = await session(browser, "manager");
  await openAndRead(page);
  await expect(configOf(page, 3, 3)).toContainText("Sin variante");
  await configOf(page, 3, 3).getByRole("button", { name: "Crear variante · línea 3, configuración 3" }).click();
  expect(catalogo().products).toBe(before.products);
  await configOf(page, 3, 3).getByRole("button", { name: "Confirmar: crear borrador" }).click();
  await expect(page.getByText(/Variante creada como borrador pendiente/)).toBeVisible();
  await expect(configOf(page, 3, 3)).toContainText("Con variante");
  await expect(configOf(page, 3, 3)).toContainText("no vende hasta habilitar");
  await expect(summary(page)).toContainText("5 con variante · 4 sin variante");
  const after = catalogo();
  expect(after.products).toBe(before.products + 1);
  expect([after.models, after.options, after.aliases]).toEqual([before.models, before.options, before.aliases]);
  expect(after.drafts).toEqual([expect.objectContaining({ origin: "palette", price: "0.00", published: false, enabled: false, inventory: 0, movements: 0 })]);
  await context.close();
});

test("sin costs.view: los importes no se ven ni pueden ser costo, y ningún costo viaja", async ({ browser }) => {
  test.setTimeout(180_000);
  const { context, page } = await session(browser, "nocost");
  await openAndRead(page);
  await expect(summary(page)).toContainText("19 configuraciones");
  for (const line of FIXTURE.lines) await expect(lineOf(page, line.n)).not.toContainText(String(line.amount));
  await expect(amountOf(page, 8).getByRole("radio", { name: "Costo del proveedor" })).toHaveCount(0);
  await expect(amountOf(page, 8)).toContainText("Importe oculto");
  await setList(page);
  await classify(page, 8, "Ignorar");
  await price(page, 8, 1).fill("5799");
  await pick(page, 8, 1).check();
  await expect(preview(page).getByRole("columnheader", { name: "Costo" })).toHaveCount(0);
  const body = await rehearseAndOpenImporter(page);
  expect(JSON.stringify(body)).not.toContain("agreed_cost");
  await context.close();
});

test("sin products.manage: sólo lectura, sin guardar ni acciones", async ({ browser }) => {
  test.setTimeout(180_000);
  const { context, page } = await session(browser, "viewer");
  await openAndRead(page);
  await expect(summary(page)).toContainText("19 configuraciones");
  await expect(page.getByRole("checkbox", { name: /^Incluir en el guardado/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Crear variante/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /es un alias de/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ensayar con el importador oficial" })).toHaveCount(0);
  await expect(page.getByText(/Guardar ofertas requiere permiso para gestionar productos/)).toBeVisible();
  // The server agrees: the dry run is refused to this user.
  const answer = await page.evaluate(async () => {
    const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
    const res = await fetch("/api/admin/inventory/text-preview/offer-draft/", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}) },
      body: JSON.stringify({ supplier_id: 1, message_date: "2026-11-01", rows: [{ key: "a", product_id: 1, commercial_price: "1" }] }),
    });
    return res.status;
  });
  expect(answer).toBe(403);
  await context.close();
});

test("móvil 390: leer, clasificar y la vista previa, sin desborde horizontal", async ({ browser }) => {
  test.setTimeout(180_000);
  const { context, page } = await session(browser, "manager", { width: 390, height: 844 });
  await openAndRead(page);
  // After «Crear variante» above, line 3 «naranja» is a (draft) variant: 19 configurations, one more with a variant.
  await expect(summary(page)).toHaveText(SUMMARY_19.replace("4 con variante · 5 sin variante", "5 con variante · 4 sin variante"));
  await expectNoOverflow(page);
  await shot(page, "mobile-390-lista-estados.png");
  await setList(page);
  await classify(page, 1, "Costo del proveedor");
  await price(page, 1, 1).fill("2599");
  await expectNoOverflow(page);
  await shot(lineOf(page, 1), "mobile-390-clasificacion-importes.png");
  await pick(page, 1, 1).check();
  await expect(preview(page).getByRole("row")).toHaveCount(2);
  await expectNoOverflow(page);
  await shot(preview(page), "mobile-390-vista-previa-guardado.png");
  await context.close();
});

test("H1 · una paleta sin colores compuestos: «glaciar X» es una duda que bloquea, nunca «Blanco» ni «Negro»", async ({ browser }) => {
  test.setTimeout(180_000);
  const context = await browser.newContext({ storageState: PLAIN_STATE!, viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const bodies: string[] = [];
  page.on("request", (req) => { if (req.url().includes("/admin/catalog/resolve")) bodies.push(req.postData() ?? ""); });
  await openAndRead(page);
  const rows = (JSON.parse(bodies[0]) as { rows: { key: string; colour_text?: string }[] }).rows;
  for (const n of [6, 9, 11, 13]) {
    const row = rows.find((r) => r.key === `L${n}`)!;
    expect(row).not.toHaveProperty("colour_text");
    await expect(lineOf(page, n)).toContainText(/Color con palabras sin reconocer: «glaciar (blanco|negro)»/i);
    await expect(lineOf(page, n).getByRole("checkbox", { name: /Ignorar/ })).toHaveCount(0);
    await expect(lineOf(page, n).getByRole("button", { name: /Crear variante/ })).toHaveCount(0);
    for (const box of await lineOf(page, n).getByRole("checkbox", { name: /^Incluir en el guardado/ }).all()) await expect(box).toBeDisabled();
  }
  for (const body of bodies) expect(body.toLowerCase()).not.toContain("glaciar");
  await expectNoOverflow(page);
  await shot(lineOf(page, 6), "desktop-1280-color-compuesto-duda.png");
  await context.close();
});

test("N3 · relleno junto al color: una persona acota el color y sólo entonces se envía", async ({ browser }) => {
  test.setTimeout(180_000);
  const { context, page } = await session(browser, "manager");
  const bodies: string[] = [];
  page.on("request", (req) => { if (req.url().includes("/admin/catalog/resolve")) bodies.push(req.postData() ?? ""); });
  await openAndRead(page, "IPH 15 128gb 2295 negro sellado");
  const doubt = page.getByRole("group", { name: "Duda de color de la línea 1", exact: true });
  await expect(doubt).toContainText("Color con palabras sin reconocer: «negro sellado»");
  await expect(doubt.getByRole("button").first()).toHaveText("Corregir el texto y volver a leer");
  await doubt.getByRole("button", { name: "Elegir qué palabras son el color" }).click();
  await doubt.getByRole("checkbox", { name: "No es parte del color: «sellado»" }).check();
  await doubt.getByRole("button", { name: "Revisar el color" }).click();
  await expect(doubt).toContainText("Se enviará «negro» como color de la línea 1; «sellado» queda fuera y no se envía.");
  expect(bodies).toHaveLength(1);
  const answered = page.waitForResponse((res) => /\/admin\/catalog\/resolve\/?$/.test(new URL(res.url()).pathname) && res.status() !== 308);
  await doubt.getByRole("button", { name: "Confirmar el color" }).click();
  expect((await answered).status()).toBe(200);
  await expect(configOf(page, 1, 1)).toContainText("Con variante");
  expect(JSON.parse(bodies[bodies.length - 1]).rows).toEqual([{ key: "L1", model_text: "IPH 15", capacity_text: "128gb", colour_text: "negro" }]);
  for (const body of bodies) expect(body).not.toContain("sellado");
  await context.close();
});
