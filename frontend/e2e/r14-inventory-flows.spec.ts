import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { test, expect, devices, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { apiBase, BACKEND_DIR, call, RUN, shell } from "./inventory-first-support";

/**
 * R14-E2E — los recorridos de la R14 (#432–#440) de punta a punta, en ESCRITORIO y en TELÉFONO, con el backend real.
 *
 * NO LO CORRE EL CI (no hay un trabajo de Playwright): se corre a mano contra un backend local y una base desechable,
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:<web> E2E_API_BASE=http://127.0.0.1:<api>/api \
 *     npx playwright test e2e/r14-inventory-flows.spec.ts
 *
 * QUÉ ES REAL: navegador, frontend, API de Django y base (desechable: la guarda de `inventory-first-support` se niega a
 * sembrar en otra). Nada se simula con `page.route`. El lector de códigos es lo que es: un teclado que escribe el código
 * de golpe en el campo de escaneo y pulsa Enter.
 *
 * DATOS: una empresa PROPIA de la corrida (`r14-<corrida>`) con Capacidad y Color, y por cada entorno un modelo con su
 * paleta (128/256 GB · Negro/Azul/Verde), una variante nueva 128 GB Negro con su código de caja y una variante de
 * seminuevos. OTRA empresa (`r14-otra-<corrida>`) tiene modelos con EL MISMO NOMBRE, su propio código de caja y un
 * equipo con un IMEI que en la empresa de la prueba nadie tiene. Tres personas de la empresa de la prueba:
 *
 *   · administra (rol «Administrador»): caja, «Registrar artículo», listas de proveedor, aprobar precios y garantías;
 *   · reponedor (rol «Inventario»: `inventory.adjust` SIN `products.manage`): escanea y carga equipos;
 *   · consulta (sólo `inventory.view`): no registra nada.
 *
 * Por entorno (escritorio 1280×800 y Pixel 7 con pantalla táctil), cada uno con SU modelo y SUS identificadores:
 *
 *   1 · permisos: sólo `inventory.view` no llega a escanear, a cargar equipos ni a «Registrar artículo» (la API dice 403);
 *   2 · escaneo de un nuevo por el reponedor: el código de caja de la otra empresa no identifica nada, el propio elige la
 *       variante; una combinación nueva le pide «Administrar productos»; el IMEI de un equipo de la otra empresa no
 *       choca; confirmar registra el equipo en la variante existente (Kardex «Inventario: registro por escaneo»);
 *   3 · escaneo de un seminuevo por el reponedor: sin precio ni garantía, pendiente de inspección y sin publicar;
 *   4 · caja (DEPENDE DE 2: vende el equipo que 2 escaneó, es el recorrido): el configurador de la caja lo ve, y la caja
 *       lo vende leyendo su IMEI;
 *   5 · «Registrar artículo» → equipo seminuevo, uno a uno: entra a la variante existente, pendiente y sin publicar;
 *   6 · lista de proveedor por WhatsApp con varios colores: la variante existente se reconoce, sólo las que faltan se
 *       crean (borradores ocultos, sin precio) y no se mueve stock;
 *   7 · carga de seminuevos con precio y garantía (#434): aprobada por quien administra, la registra el reponedor con
 *       ellos; sin aprobación, el reponedor no puede registrarlos con ellos (403 al aprobar) y los registra sin ellos;
 *   8 · (sólo teléfono) el fallo conocido del enlace de la carga, medido.
 *
 * ORDEN: las pruebas corren en serie (`mode: "serial"`). Sólo 4 depende de otra (de 2), y lo comprueba al empezar.
 * Las demás comparan con lo que había al empezar cada una (antes/después), no con cuentas absolutas.
 *
 * IMEI sintéticos 3500000… con dígito de control válido; códigos de caja inventados con su dígito de control.
 * Capturas en `R14_SHOTS` (por defecto `test-results/r14-inventory-flows`).
 *
 * LIMPIEZA: al terminar, las dos empresas y los tres usuarios de la corrida quedan DESACTIVADOS (no se borran: lo
 * protegen secuencias, roles y el Kardex). Sus productos, equipos, ventas y cargas quedan en la base desechable, bajo
 * esas empresas inactivas; nada de la empresa `black-dog-store` se toca.
 */

const SHOTS = process.env.R14_SHOTS ?? "test-results/r14-inventory-flows";
let WORK = "";

const SLUG = `r14-${RUN.toLowerCase()}`;
const OTHER = `r14-otra-${RUN.toLowerCase()}`;
const PASSWORD = `R14-${crypto.randomBytes(9).toString("base64url")}!`;
const USERS = {
  manager: `qaer14_${RUN.toLowerCase()}_adm`,
  stocker: `qaer14_${RUN.toLowerCase()}_inv`,
  viewer: `qaer14_${RUN.toLowerCase()}_ver`,
} as const;
type Who = keyof typeof USERS;

/** What the server answers a caller without the capability (inventory views; product-authority views). */
const NO_INVENTORY = "No tienes permisos sobre el inventario.";
const NO_OPERATION = "No tienes permisos para esta operación.";

type Env = { name: string; code: "D" | "M"; touch: boolean; options: Parameters<Browser["newContext"]>[0] };
const pixel = (() => { const { defaultBrowserType: _ignored, ...rest } = devices["Pixel 7"]; void _ignored; return rest; })();
const ENVS: Env[] = [
  { name: "escritorio 1280", code: "D", touch: false, options: { viewport: { width: 1280, height: 800 } } },
  { name: "teléfono Pixel 7", code: "M", touch: true, options: pixel },
];
const MODEL: Record<Env["code"], string> = { D: "Equipo Alfa", M: "Equipo Beta" };

/** A Luhn-valid IMEI that is obviously synthetic: 3500000 + 7 digits + check digit. */
const STAMP = Number(String(Date.now()).slice(-6));
function syntheticImei(n: number): string {
  const body = `3500000${String((STAMP * 100 + n) % 10_000_000).padStart(7, "0")}`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}
/** An invented EAN-13 with its check digit (2 = in-store prefix: never a real product). */
function gtin(n: number): string {
  const body = `20${String((STAMP * 100 + n) % 10_000_000_000).padStart(10, "0")}`;
  const total = [...body].reverse().reduce((sum, char, index) => sum + Number(char) * (index % 2 === 0 ? 3 : 1), 0);
  return body + String((10 - (total % 10)) % 10);
}

/** Per environment: every identifier it reads, none shared with the other environment (seeds 1–6 and 21–26). */
const IDS = Object.fromEntries(ENVS.map((env, index) => {
  const base = index * 20;
  const serial = (suffix: string) => `R14${RUN.slice(-6)}${env.code}${suffix}`.toUpperCase();
  return [env.code, {
    box: gtin(base + 1), otherBox: gtin(base + 2), otherImei: syntheticImei(base + 1),
    scanImei2: syntheticImei(base + 2), scanSerial: serial("S1"),
    usedScan: syntheticImei(base + 3),
    wizardImei: syntheticImei(base + 4), wizardSerial: serial("W1"),
    approved: { imei: syntheticImei(base + 5), serial: serial("A1") },
    dropped: { imei: syntheticImei(base + 6), serial: serial("B1") },
  }];
})) as Record<Env["code"], {
  box: string; otherBox: string; otherImei: string; scanImei2: string; scanSerial: string; usedScan: string;
  wizardImei: string; wizardSerial: string; approved: { imei: string; serial: string }; dropped: { imei: string; serial: string };
}>;
/** The device of the known-bug test (8): a seed of its own, away from the environments' 1–6 and 21–26. */
const KNOWN_BUG_SEED = 90;

type Seed = { branch: number; branchName: string; models: Record<string, number>; otherModels: Record<string, number> };
let SEED: Seed;
const STATES: Partial<Record<Who, Awaited<ReturnType<BrowserContext["storageState"]>>>> = {};

function sembrar(): Seed {
  return shell<Seed>(`
from django.contrib.auth import get_user_model
from store import device_catalog, model_palette, stock_unit_services, supply_services
from store.company_provisioning import provision_company_access_defaults
from store.models import (Company, CompanyRole, Membership, MembershipRoleAssignment, Product, ProductBarcode)
User = get_user_model()

def tenant(slug, name):
    company = Company.objects.create(name=name, slug=slug)
    provision_company_access_defaults(company)
    company.refresh_from_db()
    cap = device_catalog.create_attribute(company=company, name='Capacidad', role='capacity', sort_order=0)
    col = device_catalog.create_attribute(company=company, name='Color', kind='color', role='color', sort_order=1)
    o = {}
    for attr, label, swatch in ((cap, '128 GB', ''), (cap, '256 GB', ''), (col, 'Negro', '#111111'),
                                (col, 'Azul', '#1d4ed8'), (col, 'Verde', '#15803d')):
        o[label] = device_catalog.create_option(company=company, attribute_id=attr.pk, label=label, swatch_hex=swatch)
    return company, o

def variant(company, model, o, name, slug, line, price):
    product = Product.objects.create(company=company, name=name, slug=slug, price=Decimal(price), inventory=0,
                                     is_active=True, is_serialized=True, requires_imei=True, is_published_online=False)
    device_catalog.set_variant(product, model_id=model.pk, condition_line=line, option_ids=[o['128 GB'].pk, o['Negro'].pk])
    return product

def member(user, company, legacy, role):
    membership = Membership.objects.create(user=user, company=company, role=legacy, is_active=True,
                                           branch_access_mode=Membership.ACCESS_MODE_ALL)
    MembershipRoleAssignment.objects.create(membership=membership, role=role, is_active=True)

company, o = tenant(data['slug_r14'], 'Empresa R14 ' + data['run'])
other, oo = tenant(data['other'], 'OTRA EMPRESA R14 ' + data['run'])
users = {key: User.objects.create_user(username=name, password=data['password']) for key, name in data['users'].items()}
member(users['manager'], company, 'admin', CompanyRole.objects.get(company=company, slug='administrador'))
member(users['stocker'], company, 'customer', CompanyRole.objects.get(company=company, slug='inventario'))
viewer_role = CompanyRole.objects.create(company=company, name='Sólo consulta ' + data['run'], slug='consulta-' + data['run'].lower(),
                                         capabilities=['company.view', 'inventory.view'])
member(users['viewer'], company, 'customer', viewer_role)
supply_services.create_supplier(company=company, name='Proveedor R14 ' + data['run'], actor=users['manager'])

models, other_models = {}, {}
for code, name in data['models'].items():
    ids = data['ids'][code]
    model = device_catalog.create_model(company=company, name=name)
    model_palette.set_palette(model, option_ids=[x.pk for x in o.values()], actor=users['manager'])
    new = variant(company, model, o, name + ' 128 GB Negro', (data['slug_r14'] + '-' + code + '-n').lower(), 'new', '3000.00')
    ProductBarcode.objects.create(company=company, product=new, code=ids['box'])
    variant(company, model, o, name + ' 128 GB Negro seminuevo', (data['slug_r14'] + '-' + code + '-u').lower(), 'used', '2000.00')
    models[code] = model.pk
    # The other company: a model of THE SAME NAME, its own box code and a device whose IMEI this company never had.
    omodel = device_catalog.create_model(company=other, name=name)
    model_palette.set_palette(omodel, option_ids=[x.pk for x in oo.values()])
    onew = variant(other, omodel, oo, name + ' 128 GB Negro OTRA', (data['other'] + '-' + code).lower(), 'new', '1.00')
    ProductBarcode.objects.create(company=other, product=onew, code=ids['otherBox'])
    stock_unit_services.receive_units(branch=other.default_inventory_branch, product=onew,
                                      units=[{'imei': ids['otherImei'], 'serial_number': 'OTRA' + ids['otherImei'][-8:]}],
                                      reason='[E2E] equipo de otra empresa')
    other_models[code] = omodel.pk
branch = company.default_inventory_branch
out({'branch': branch.pk, 'branchName': branch.name, 'models': models, 'otherModels': other_models})
`, { slug_r14: SLUG, other: OTHER, run: RUN, password: PASSWORD, users: USERS, models: MODEL, ids: IDS });
}

type Unit = { imei: string; imei2: string; serial: string; status: string; condition: string; inspection: string;
              published: boolean; price: string | null; warranty: number | null; product: string; reason: string };
type State = {
  products: Record<string, { price: string; origin: string; published: boolean; inventory: number; movements: number }>;
  units: Record<string, Unit>; companyUnits: number; companyMovements: number; otherUnits: string[];
  sold: Record<string, string[]>;
};

/** What the database holds for the model of `env` (read only). */
function estado(env: Env): State {
  return shell<State>(`
from store.models import OrderUnit, Product, StockMovement, StockUnit
products = Product.objects.filter(company__slug=data['s'], product_model__name=data['model'])
units = StockUnit.objects.filter(product__in=products).select_related('product')
def last_reason(unit):
    move = StockMovement.objects.filter(product=unit.product).order_by('-pk').first()
    return move.reason if move else ''
out({
    'products': {p.name: {'price': str(p.price), 'origin': p.draft_origin, 'published': p.is_published_online,
                          'inventory': p.inventory, 'movements': StockMovement.objects.filter(product=p).count()}
                 for p in products},
    'units': {u.imei: {'imei': u.imei, 'imei2': u.imei2 or '', 'serial': u.serial_number or '', 'status': u.status,
                       'condition': u.condition, 'inspection': u.inspection_status, 'published': u.published_online,
                       'price': None if u.sale_price is None else str(u.sale_price), 'warranty': u.warranty_days,
                       'product': u.product.name, 'reason': last_reason(u)} for u in units},
    'companyUnits': StockUnit.objects.filter(company__slug=data['s']).count(),
    'companyMovements': StockMovement.objects.filter(product__company__slug=data['s']).count(),
    'otherUnits': sorted(StockUnit.objects.filter(company__slug=data['o']).values_list('imei', flat=True)),
    'sold': {u.imei: [str(x) for x in OrderUnit.objects.filter(unit=u).values_list('order_id', flat=True)] for u in units},
})
`, { s: SLUG, o: OTHER, model: MODEL[env.code] });
}

/** Cleanup scope: the run's two companies and three users are deactivated, never deleted (see the header). */
function desactivar() {
  return shell(`
from django.contrib.auth import get_user_model
from store.models import Company
Company.objects.filter(slug__in=data['slugs']).update(is_active=False)
get_user_model().objects.filter(username__in=data['users']).update(is_active=False)
out({'ok': True})
`, { slugs: [SLUG, OTHER], users: Object.values(USERS) });
}

/** The login admits 5 a minute: a 429 waits a minute and retries, at most twice (the hook's budget counts it). */
const LOGIN_RETRY_MS = 62_000;
const LOGIN_ATTEMPTS = 3;
async function login(browser: Browser, who: Who) {
  const context = await browser.newContext();
  for (let attempt = 1; ; attempt++) {
    const answer = await context.request.post(`${apiBase()}/auth/login/`, { data: { username: USERS[who], password: PASSWORD } });
    if (answer.status() === 200) break;
    if (answer.status() !== 429 || attempt === LOGIN_ATTEMPTS) throw new Error(`R14-E2E: ${who} no pudo iniciar sesión: ${answer.status()}`);
    await new Promise((resolve) => setTimeout(resolve, LOGIN_RETRY_MS));
  }
  await context.request.get(`${apiBase()}/auth/csrf/`);
  STATES[who] = await context.storageState();
  await context.close();
}

async function session(browser: Browser, who: Who, env: Env) {
  const context = await browser.newContext({ ...env.options, storageState: STATES[who]! });
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept());
  return { context, page };
}

/** A click with a mouse, a tap on a touch screen. */
async function press(target: Locator, env: Env) {
  await target.scrollIntoViewIfNeeded({ timeout: 30_000 });
  if (env.touch) await target.tap({ timeout: 30_000 }); else await target.click({ timeout: 30_000 });
}

/**
 * How far the page scrolls sideways. With `wrapKnownLink`, the load's link of «Precios y garantías del archivo»
 * (the known bug, StockUnitImport.tsx:180) is measured as if it wrapped — only that element; everything else counts.
 */
async function overflow(page: Page, wrapKnownLink = false) {
  return page.evaluate((wrap) => {
    const prefix = `${window.location.origin}/admin/inventory/units/import?job=`;
    const links = wrap
      ? [...document.querySelectorAll<HTMLElement>("span.font-mono")].filter((span) => (span.textContent ?? "").startsWith(prefix))
      : [];
    const saved = links.map((span) => span.style.wordBreak);
    links.forEach((span) => { span.style.wordBreak = "break-all"; });
    const over = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    links.forEach((span, index) => { span.style.wordBreak = saved[index]; });
    return { over, wrapped: links.length };
  }, wrapKnownLink);
}

async function noOverflow(page: Page, where: string, { exceptKnownLink = false } = {}) {
  const { over } = await overflow(page, exceptKnownLink);
  expect(over, `desborde horizontal en ${where}`).toBeLessThanOrEqual(1);
}

async function shot(page: Page, env: Env, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${env.code === "D" ? "desktop" : "mobile"}-${name}.png`), fullPage: true });
}

/** The scanner: the whole code at once, then Enter, into the scan field; then the screen's answer to THAT read. */
async function leer(page: Page, code: string, answered: () => Promise<void>) {
  const input = page.getByTestId("scan-intake-input");
  await input.focus();
  await page.keyboard.type(code, { delay: 0 });
  await page.keyboard.press("Enter");
  await answered();
}

/** One step of «Registrar artículo»: the innermost section headed by `heading`. */
const wizardStep = (page: Page, heading: string) =>
  page.locator("section").filter({ has: page.getByRole("heading", { name: heading, exact: true }) }).last();

/**
 * The amount 1900 as an amount — «1900», «1,900», «1900.00», «S/1900» or a JSON value — never digits inside a longer
 * number or code such as an IMEI or a serial (the synthetic IMEIs are 3500000… and may hold «1900» anywhere).
 */
const AMOUNT_1900 = /(?<![\w.]|\d,)1,?900(?:\.\d{1,2})?(?!\d|,\d)/;

/** A chip of the configurator (the radio inside is visually hidden): a person presses its label. */
const chip = (scope: Locator, step: string, option: string) =>
  scope.getByRole("radiogroup", { name: step }).locator("label").filter({ hasText: option }).first();

function python(code: string, env: Record<string, string>): string {
  return execFileSync("python3", ["-c", code], { cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env } }).toString();
}

/** THE downloaded template, with `rows` ({header: value}) from row 3 — row 2 is the template's own example. */
function fill(file: string, rows: Record<string, string | number | null>[]): string {
  const target = path.join(WORK, `lleno-${Date.now()}-${path.basename(file)}`);
  python(`
import json, os, openpyxl
book = openpyxl.load_workbook(os.environ['E2E_FILE']); sheet = book.worksheets[0]
headers = [cell.value for cell in sheet[1]]
for offset, row in enumerate(json.loads(os.environ['E2E_ROWS'])):
    for header, value in row.items():
        if header not in headers:
            raise SystemExit('la plantilla no tiene la columna ' + header)
        sheet.cell(row=3 + offset, column=headers.index(header) + 1, value=value)
book.save(os.environ['E2E_OUT'])`, { E2E_FILE: file, E2E_ROWS: JSON.stringify(rows), E2E_OUT: target });
  return target;
}

async function usedTemplate(page: Page, env: Env): Promise<string> {
  const [file] = await Promise.all([page.waitForEvent("download"), press(page.getByRole("link", { name: "Stock de seminuevos" }), env)]);
  const target = path.join(WORK, `${env.code}-${Date.now()}-${file.suggestedFilename()}`);
  await file.saveAs(target);
  return target;
}

/** Previews a load of used devices bringing their own price and warranty; answers the address that reopens it. */
async function previewUsedLoad(page: Page, env: Env, unit: { imei: string; serial: string }, price: number, warranty: number) {
  await page.goto("/admin/inventory/units/import");
  await expect(page.getByLabel("Archivo de equipos (.xlsx)"), "la carga de equipos no cargó").toBeVisible({ timeout: 90_000 });
  const file = fill(await usedTemplate(page, env), [{
    Sucursal: SEED.branchName, Modelo: MODEL[env.code], Capacidad: "128 GB", Color: "Negro", "IMEI 1": unit.imei,
    Serie: unit.serial, "Salud de batería (%)": 87, "Precio de venta": price, "Garantía (días)": warranty,
  }]);
  await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(file);
  await press(page.getByRole("radio", { name: "Seminuevos", exact: true }), env);
  await press(page.getByRole("radio", { name: /^Compra a proveedor/ }), env);
  await page.getByLabel("Motivo para las filas sin motivo").fill(`Compra E2E R14 ${RUN}`);
  await press(page.getByRole("button", { name: "Previsualizar" }), env);
  const terms = page.getByRole("group", { name: "Precios y garantías del archivo" });
  await expect(terms, "la vista previa no dice que trae precios y garantías").toBeVisible({ timeout: 60_000 });
  const link = /job=(\d+)/.exec(await terms.innerText())?.[1];
  expect(link, "la vista previa no da el enlace de la carga").toBeTruthy();
  return { terms, link: `/admin/inventory/units/import?job=${link}`, job: Number(link) };
}

/** A staged load as `api`'s person reads it, as raw text (what that person's browser receives). */
async function loadAsText(api: BrowserContext["request"], job: number) {
  const answer = await call(api, "GET", `/admin/imports/${job}/`);
  expect(answer.status).toBe(200);
  return JSON.stringify(answer.body);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  // Seeding plus three logins, each of which may wait out the login rate limit twice.
  test.setTimeout(120_000 + Object.keys(USERS).length * (LOGIN_ATTEMPTS - 1) * LOGIN_RETRY_MS);
  fs.mkdirSync(SHOTS, { recursive: true });
  WORK = fs.mkdtempSync(path.join(os.tmpdir(), "r14-e2e-"));
  SEED = sembrar();
  for (const who of Object.keys(USERS) as Who[]) await login(browser, who);
});

test.afterAll(() => {
  desactivar();
  if (WORK) fs.rmSync(WORK, { recursive: true, force: true });
});

for (const env of ENVS) {
  const ids = IDS[env.code];
  const model = MODEL[env.code];
  const existing = `${model} 128 GB Negro`;
  const usedVariant = `${model} 128 GB Negro seminuevo`;

  test(`${env.name} · 1 · sólo inventory.view: no escanea, no carga equipos, no registra artículos`, async ({ browser }) => {
    test.setTimeout(180_000);
    const before = estado(env);
    const { context, page } = await session(browser, "viewer", env);
    try {
      await page.goto("/admin/inventory/scan");
      await expect(page.getByText(/Registrar un equipo mueve el stock y pide el permiso de ajustar inventario/))
        .toBeVisible({ timeout: 60_000 });
      await expect(page.getByTestId("scan-intake-input")).toHaveCount(0);
      await noOverflow(page, `${env.name} escaneo denegado`);

      await page.goto("/admin/inventory/units/import");
      await expect(page.getByText("Para registrar equipos hace falta el permiso de ajustar inventario.")).toBeVisible({ timeout: 60_000 });
      await expect(page.getByLabel("Archivo de equipos (.xlsx)")).toHaveCount(0);

      await page.goto("/admin/products/register");
      await expect(page.getByText(/no tiene permiso para esta sección/)).toBeVisible({ timeout: 60_000 });
      await expect(page.getByRole("radiogroup", { name: "Tipo de artículo" })).toHaveCount(0);

      // The server says the same, whatever the screen shows: refused for lack of the inventory's authority.
      const check = await call(context.request, "POST", "/admin/inventory/scan/check/", { code: ids.otherImei });
      expect(check).toEqual({ status: 403, body: { detail: NO_INVENTORY } });
      const receive = await call(context.request, "POST", `/admin/inventory/scan/receive/?branch=${SEED.branch}`,
        { condition: "new", imei: ids.otherImei }, { "Idempotency-Key": `r14-viewer-${env.code}-${RUN}` });
      expect(receive).toEqual({ status: 403, body: { detail: NO_INVENTORY } });
      const after = estado(env);
      expect(after.companyUnits).toBe(before.companyUnits);
      expect(after.companyMovements).toBe(before.companyMovements);
    } finally {
      await context.close();
    }
  });

  test(`${env.name} · 2 · reponedor sin products.manage: escanea caja + IMEI 1 + IMEI 2 + serie y confirma`, async ({ browser }) => {
    test.setTimeout(240_000);
    const before = estado(env);
    const { context, page } = await session(browser, "stocker", env);
    const log = page.getByTestId("scan-intake-log");
    const newest = log.getByRole("listitem").first();
    try {
      await page.goto("/admin/inventory/scan");
      await expect(page.getByTestId("scan-intake-input"), "la pantalla de escaneo no cargó").toBeVisible({ timeout: 60_000 });
      await noOverflow(page, `${env.name} escaneo vacío`);

      // Isolation: the other company has a model of the same name; only this company's is found.
      await page.getByTestId("scan-intake-model").fill(model);
      const hits = page.getByRole("list", { name: "Modelos encontrados" }).getByRole("button");
      await expect(hits).toHaveCount(1, { timeout: 30_000 });
      await expect(hits).toHaveText(model);
      await page.getByTestId("scan-intake-model").fill("");
      const foreign = await call(context.request, "GET",
        `/admin/inventory/scan/models/${SEED.otherModels[env.code]}/configurations/?branch=${SEED.branch}`);
      expect(foreign).toEqual({ status: 404, body: { detail: "No encontrado." } });

      // The other company's box code identifies nothing here, and names nothing of theirs.
      await leer(page, ids.otherBox, () => expect(newest).toContainText("ningún producto de la empresa lo tiene"));
      await expect(page.getByTestId("scan-intake-variant")).toHaveCount(0);

      // This company's box code chooses the variant on the configurator.
      await leer(page, ids.box, () => expect(page.getByTestId("scan-intake-variant")).toContainText(existing, { timeout: 30_000 }));
      await expect(newest).toContainText(`Código comercial de «${existing}»`);
      const config = page.getByRole("group", { name: "Configuración del equipo" });
      await expect(config.getByRole("radiogroup", { name: "Capacidad" }).getByRole("radio", { name: /128 GB/ })).toBeChecked();
      await expect(config.getByRole("radiogroup", { name: "Color" }).getByRole("radio", { name: /Negro/ })).toBeChecked();
      await expect(page.getByTestId("scan-intake-expected")).toContainText("IMEI 1");

      // The IMEI of the other company's device is free here: its device is never read.
      await leer(page, ids.otherImei, () => expect(page.getByTestId("scan-intake-value-imei")).toHaveText(ids.otherImei));
      await expect(newest).toContainText(`IMEI 1 leído: ${ids.otherImei}`);
      await leer(page, ids.scanImei2, () => expect(page.getByTestId("scan-intake-value-imei2")).toHaveText(ids.scanImei2));
      await leer(page, ids.scanSerial, () => expect(page.getByTestId("scan-intake-value-serial")).toHaveText(ids.scanSerial));
      await expect(newest).toContainText(`Serie leída: ${ids.scanSerial}`);
      // Every read answered (above): the log holds them, and none of them said the IMEI was taken or named the other's.
      await expect(log.getByRole("listitem")).toHaveCount(5);
      await expect(log).not.toContainText("ya está registrado");
      await expect(page.locator("body")).not.toContainText("OTRA");

      // A combination that is not a variant yet: creating it needs «Administrar productos», and nothing is confirmed.
      await press(chip(config, "Capacidad", "256 GB"), env);
      await press(chip(config, "Color", "Azul"), env);
      await expect(page.getByTestId("scan-intake-variant")).toContainText("crearla requiere «Administrar productos»");
      await expect(page.getByTestId("scan-intake-price")).toHaveCount(0);
      await expect(page.getByTestId("scan-intake-confirm")).toBeDisabled();
      await shot(page, env, "r14-scan-sin-products-manage");

      await press(chip(config, "Capacidad", "128 GB"), env);
      await press(chip(config, "Color", "Negro"), env);
      await expect(page.getByTestId("scan-intake-variant")).toContainText(existing);
      await expect(page.getByTestId("scan-intake-value-imei")).toHaveText(ids.otherImei);
      await expect(page.getByTestId("scan-intake-confirm")).toBeEnabled();
      expect(estado(env).units[ids.otherImei], "nada entra antes de confirmar").toBeUndefined();
      await noOverflow(page, `${env.name} revisión del escaneo`);

      await press(page.getByTestId("scan-intake-confirm"), env);
      await expect(page.getByTestId("scan-intake-done")).toHaveText("Equipo registrado", { timeout: 30_000 });
      const after = estado(env);
      expect(after.units[ids.otherImei]).toMatchObject({
        status: "available", condition: "new", imei2: ids.scanImei2, serial: ids.scanSerial, product: existing,
        reason: "Inventario: registro por escaneo",
      });
      // Into the EXISTING variant: no product created, one device more; the other company's device is untouched.
      expect(Object.keys(after.products).sort()).toEqual(Object.keys(before.products).sort());
      expect(after.companyUnits - before.companyUnits).toBe(1);
      expect(after.otherUnits).toEqual(before.otherUnits);
      await noOverflow(page, `${env.name} equipo registrado`);
      await shot(page, env, "r14-scan-registrado");
    } finally {
      await context.close();
    }
  });

  test(`${env.name} · 3 · reponedor: un seminuevo por escaneo, sin precio, pendiente de inspección y sin publicar`, async ({ browser }) => {
    test.setTimeout(180_000);
    const before = estado(env);
    const { context, page } = await session(browser, "stocker", env);
    try {
      await page.goto("/admin/inventory/scan");
      await expect(page.getByTestId("scan-intake-input")).toBeVisible({ timeout: 60_000 });
      await page.getByTestId("scan-intake-model").fill(model);
      await press(page.getByRole("list", { name: "Modelos encontrados" }).getByRole("button", { name: model, exact: true }), env);
      await expect(page.getByRole("group", { name: "Configuración del equipo" })).toBeVisible({ timeout: 30_000 });
      await press(page.getByRole("button", { name: "Seminuevo" }), env);
      await expect(page.getByTestId("scan-intake-used-variant")).toContainText(usedVariant, { timeout: 30_000 });
      await leer(page, ids.usedScan, () => expect(page.getByTestId("scan-intake-value-imei")).toHaveText(ids.usedScan));
      await page.getByTestId("scan-intake-battery").fill("86");
      // Without products.manage the used device's price and warranty are not even offered.
      await expect(page.getByText("El precio y la garantía del seminuevo los fija quien administra productos.")).toBeVisible();
      await expect(page.getByLabel("Precio de este equipo (opcional)")).toHaveCount(0);
      await noOverflow(page, `${env.name} seminuevo por escaneo`);
      await press(page.getByTestId("scan-intake-confirm"), env);
      await expect(page.getByTestId("scan-intake-done")).toHaveText("Equipo registrado", { timeout: 30_000 });
      await expect(page.getByText(/pendiente de inspección/)).toBeVisible();
      const after = estado(env);
      expect(after.units[ids.usedScan]).toMatchObject({
        status: "available", condition: "used", inspection: "pending", published: false, price: null, product: usedVariant,
      });
      expect(after.companyUnits - before.companyUnits).toBe(1);
    } finally {
      await context.close();
    }
  });

  test(`${env.name} · 4 · caja (usa el equipo escaneado en 2): el configurador lo ve y la caja lo vende leyendo su IMEI`, async ({ browser }) => {
    test.setTimeout(240_000);
    const before = estado(env);
    expect(before.units[ids.otherImei]?.status, "precondición: la prueba 2 registró este equipo y sigue disponible").toBe("available");
    const onShelf = Object.values(before.units).filter((unit) => unit.product === existing && unit.status === "available").length;
    const { context, page } = await session(browser, "manager", env);
    try {
      await page.goto("/admin/sales/pos");
      const scan = page.getByLabel("Escanear código");
      await expect(scan, "el punto de venta no cargó").toBeVisible({ timeout: 60_000 });

      // The till's device configurator (its own panel): the same model, the chips of its palette, the shelf count.
      const toggle = page.getByRole("button", { name: "Vender equipo por modelo" });
      await press(toggle, env);
      const picker = page.locator(`[id="${await toggle.getAttribute("aria-controls")}"]`);
      await expect(picker).toBeVisible();
      await picker.getByLabel("Modelo", { exact: true }).fill(model);
      const hit = picker.getByRole("listitem").getByRole("button").filter({ hasText: model });
      await expect(hit, "sólo el modelo de esta empresa").toHaveCount(1, { timeout: 30_000 });
      await press(hit, env);
      await expect(picker.getByRole("radiogroup", { name: "Capacidad" })).toBeVisible({ timeout: 30_000 });
      await press(chip(picker, "Capacidad", "128 GB"), env);
      await press(chip(picker, "Color", "Negro"), env);
      await expect(picker.getByRole("region", { name: "Equipo elegido" }).getByText(`${onShelf} en esta sucursal`)).toBeVisible({ timeout: 30_000 });
      await expect(picker).not.toContainText("OTRA");
      await noOverflow(page, `${env.name} configurador de la caja`);
      await shot(page, env, "r14-caja-configurador");

      // The scanner at the till: the IMEI the stockroom registered lands in the cart, identified.
      await page.goto("/admin/sales/pos");
      await expect(scan).toBeVisible({ timeout: 60_000 });
      await scan.focus();
      await page.keyboard.type(ids.otherImei, { delay: 0 });
      await page.keyboard.press("Enter");
      const cart = page.locator("#pos-step-products");
      await expect(cart.getByText(new RegExp(`IMEI ${ids.otherImei}`))).toBeVisible({ timeout: 30_000 });
      await noOverflow(page, `${env.name} caja con el equipo`);
      // The sale's steps (customer, payment, receipt, review, «Cobrar») live in the cart's column.
      await cart.getByRole("checkbox", { name: /Confirmo que informé al cliente las condiciones de venta/ }).check();
      await cart.getByLabel("Tipo de comprobante").selectOption("sales_note");
      await cart.getByLabel("Efectivo recibido").fill("99999");
      const charge = cart.getByRole("button", { name: /^cobrar$/i });
      await expect(charge).toBeEnabled({ timeout: 20_000 });
      await press(charge, env);
      await expect(page.getByText(/Venta registrada/i), "la venta no se completó").toBeVisible({ timeout: 60_000 });
      await shot(page, env, "r14-caja-vendido");
      const after = estado(env);
      expect(after.units[ids.otherImei].status).toBe("sold");
      expect(after.sold[ids.otherImei]).toHaveLength(1);
      // The other company's device with the same IMEI is still theirs.
      expect(after.otherUnits).toEqual(before.otherUnits);
    } finally {
      await context.close();
    }
  });

  test(`${env.name} · 5 · «Registrar artículo»: un seminuevo, uno a uno, pendiente de inspección y sin publicar`, async ({ browser }) => {
    test.setTimeout(240_000);
    const before = estado(env);
    const { context, page } = await session(browser, "manager", env);
    try {
      await page.goto("/admin/products/register");
      const kindStep = wizardStep(page, "¿Qué vas a registrar?");
      await expect(kindStep.getByRole("radiogroup", { name: "Tipo de artículo" })).toBeVisible({ timeout: 60_000 });
      await press(kindStep.getByRole("radio", { name: /Equipo seminuevo/ }), env);
      await press(kindStep.getByRole("button", { name: "Continuar" }), env);
      const dataStep = wizardStep(page, "Datos del artículo");
      const palette = page.waitForResponse((res) => /\/palette\/?$/.test(new URL(res.url()).pathname) && res.status() !== 308);
      await dataStep.getByLabel("Modelo").selectOption({ label: model });
      expect((await palette).status()).toBe(200);
      const config = dataStep.getByRole("group", { name: "Configuración del equipo" });
      await expect(config).toBeVisible();
      await press(chip(config, "Capacidad", "128 GB"), env);
      await press(chip(config, "Color", "Negro"), env);
      await expect(config).toContainText(`Ya existe: ${usedVariant}`);
      await noOverflow(page, `${env.name} registrar artículo · configurador`);
      await dataStep.getByLabel(/^Nombre/).fill(model);
      await dataStep.getByLabel(/^Precio de venta/).fill("2000");
      await press(dataStep.getByRole("button", { name: "Continuar" }), env);

      const stockStep = wizardStep(page, "Stock");
      await stockStep.getByLabel("Número de serie").fill(ids.wizardSerial);
      // The IMEI field takes a scanner too: the whole code and its Enter, which never submits the step.
      const imeiField = stockStep.getByLabel("IMEI", { exact: true });
      await imeiField.focus();
      await page.keyboard.type(ids.wizardImei, { delay: 0 });
      await page.keyboard.press("Enter");
      await expect(imeiField).toHaveValue(ids.wizardImei);
      await expect(stockStep).toBeVisible();
      await stockStep.getByLabel("Batería (%)").fill("91");
      await stockStep.getByLabel("Precio de este equipo (S/)").fill("1850");
      await noOverflow(page, `${env.name} registrar artículo · equipo`);
      await press(stockStep.getByRole("button", { name: "Revisar" }), env);
      await press(wizardStep(page, "Revisar y registrar").getByRole("button", { name: "Registrar artículo" }), env);
      await expect(page.getByRole("heading", { name: "Artículo registrado" })).toBeVisible({ timeout: 60_000 });
      await shot(page, env, "r14-registrar-articulo-seminuevo");
      const after = estado(env);
      expect(after.units[ids.wizardImei]).toMatchObject({
        status: "available", condition: "used", inspection: "pending", published: false, price: "1850.00",
        serial: ids.wizardSerial, product: usedVariant,
      });
      expect(Object.keys(after.products).sort()).toEqual(Object.keys(before.products).sort());
      expect(after.companyUnits - before.companyUnits).toBe(1);
    } finally {
      await context.close();
    }
  });

  test(`${env.name} · 6 · lista de WhatsApp con varios colores: reconoce la existente y crea sólo las que faltan, sin stock`, async ({ browser }) => {
    test.setTimeout(240_000);
    const before = estado(env);
    const { context, page } = await session(browser, "manager", env);
    try {
      await page.goto("/admin/products/suppliers/offers/update-list");
      await expect(page.getByRole("heading", { name: "Actualizar lista de proveedor" })).toBeVisible({ timeout: 60_000 });
      await page.getByLabel("Texto de la lista del proveedor").fill(`${model.toLowerCase()} 128gb negro, azul, verde 2500`);
      const resolved = page.waitForResponse((res) => /\/admin\/catalog\/resolve\/?$/.test(new URL(res.url()).pathname)
        && res.request().method() === "POST" && res.status() !== 308);
      await press(page.getByRole("button", { name: "Leer la lista" }), env);
      expect((await resolved).status()).toBe(200);

      const cards = page.getByRole("article", { name: "Línea 1", exact: true }).getByRole("article");
      await expect(cards).toHaveCount(3);
      await expect(cards.nth(0)).toContainText("Con variante");
      await expect(cards.nth(0)).toContainText(existing);
      await expect(cards.nth(1)).toContainText("Sin variante");
      await expect(cards.nth(2)).toContainText("Sin variante");
      await expect(page.locator("body")).not.toContainText("OTRA");
      await noOverflow(page, `${env.name} lista leída`);

      const batch = page.getByRole("region", { name: "Variantes que faltan" });
      await expect(batch).toContainText("2 configuraciones válidas no tienen variante");
      await press(batch.getByRole("button", { name: /Revisar y crear 2 variantes/ }), env);
      await expect(batch).toContainText(`${model} · 128 GB · Azul`);
      await expect(batch).toContainText(`${model} · 128 GB · Verde`);
      await expect(batch).not.toContainText(`${model} · 128 GB · Negro`);
      await shot(page, env, "r14-lista-variantes-que-faltan");
      expect(Object.keys(estado(env).products).sort(), "nada se crea antes de confirmar").toEqual(Object.keys(before.products).sort());
      await press(batch.getByRole("button", { name: "Confirmar: crear 2 borradores" }), env);
      await expect(page.getByText(/Se crearon 2 variantes como borrador/)).toBeVisible({ timeout: 30_000 });
      await noOverflow(page, `${env.name} borradores creados`);

      const after = estado(env);
      const created = Object.keys(after.products).filter((name) => !(name in before.products));
      expect(created).toHaveLength(2);
      for (const name of created) {
        expect(after.products[name]).toEqual({ price: "0.00", origin: "palette", published: false, inventory: 0, movements: 0 });
      }
      // No physical stock moved: the same devices and the same Kardex, and the existing variant kept its price.
      expect(after.companyUnits).toBe(before.companyUnits);
      expect(after.companyMovements).toBe(before.companyMovements);
      expect(after.products[existing].price).toBe(before.products[existing].price);
    } finally {
      await context.close();
    }
  });

  test(`${env.name} · 7 · carga de seminuevos con precio y garantía: aprobación explícita o registro sin ellos (#434)`, async ({ browser }) => {
    test.setTimeout(360_000);
    const manager = await session(browser, "manager", env);
    const stocker = await session(browser, "stocker", env);
    // At phone width the load's link overflows (known bug, test 8): only that element is excused, the rest is measured.
    const exceptKnownLink = env.touch;
    try {
      // A · whoever administers products approves; the stockroom registers WITH the file's price and warranty.
      const first = await previewUsedLoad(manager.page, env, ids.approved, 1900, 90);
      await expect(first.terms).toContainText("Tú administras productos");
      await noOverflow(manager.page, `${env.name} vista previa con precios`, { exceptKnownLink });
      await press(first.terms.getByRole("button", { name: "Aprobar precios y garantías" }), env);
      await expect(first.terms).toContainText("ya están aprobados", { timeout: 30_000 });
      await expect(first.terms).toContainText(`Aprobados por ${USERS.manager}`);
      await shot(manager.page, env, "r14-carga-aprobada");

      await stocker.page.goto(first.link);
      const approved = stocker.page.getByRole("group", { name: "Precios y garantías del archivo" });
      await expect(approved).toContainText("ya están aprobados", { timeout: 60_000 });
      // Who does not administer products sees that the rows carry terms, never their amounts: not in the terms block,
      // not in the rows of the load (which ARE on screen), not in what the server sends — while the manager's has them.
      const rows = stocker.page.getByRole("table", { name: "Equipos del archivo" });
      await expect(rows).toContainText(ids.approved.serial);
      await expect(approved).not.toContainText(AMOUNT_1900);
      await expect(rows).not.toContainText(AMOUNT_1900);
      expect(await loadAsText(manager.context.request, first.job)).toMatch(AMOUNT_1900);
      expect(await loadAsText(stocker.context.request, first.job)).not.toMatch(AMOUNT_1900);
      await noOverflow(stocker.page, `${env.name} carga aprobada reabierta`, { exceptKnownLink });
      await press(stocker.page.getByRole("button", { name: "Registrar 1 equipo" }), env);
      await expect(stocker.page.getByRole("status").filter({ hasText: /Se registr(ó|aron) 1 equipo/ })).toBeVisible({ timeout: 60_000 });
      expect(estado(env).units[ids.approved.imei]).toMatchObject({
        status: "available", condition: "used", inspection: "pending", published: false, price: "1900.00", warranty: 90,
      });

      // B · without the approval: no «Registrar», the server refuses an approval from the stockroom, and the devices
      //     enter without the file's price and warranty.
      const second = await previewUsedLoad(manager.page, env, ids.dropped, 1950, 60);
      await stocker.page.goto(second.link);
      const pending = stocker.page.getByRole("group", { name: "Precios y garantías del archivo" });
      await expect(pending).toContainText("Necesita la aprobación de quien administra productos", { timeout: 60_000 });
      await expect(pending.getByRole("button", { name: "Aprobar precios y garantías" })).toHaveCount(0);
      await expect(stocker.page.getByRole("button", { name: "Registrar 1 equipo" })).toHaveCount(0);
      const refused = await call(stocker.context.request, "POST", `/admin/inventory/units/import/${second.job}/approve-terms/`, {});
      expect(refused).toEqual({ status: 403, body: { detail: NO_OPERATION } });
      await noOverflow(stocker.page, `${env.name} carga sin aprobar`, { exceptKnownLink });
      await shot(stocker.page, env, "r14-carga-sin-aprobar");
      await press(pending.getByRole("button", { name: "Registrar sin precios ni garantías" }), env);
      await expect(stocker.page.getByRole("note", { name: "Sin precios ni garantías" })).toBeVisible({ timeout: 60_000 });
      expect(estado(env).units[ids.dropped.imei]).toMatchObject({
        status: "available", condition: "used", inspection: "pending", published: false, price: null, warranty: null,
      });
    } finally {
      await manager.context.close();
      await stocker.context.close();
    }
  });

  // KNOWN BUG (R14, StockUnitImport.tsx:180): «Enlace de esta carga» is one unbreakable monospace line, wider than a
  // phone, so the whole page scrolls sideways. This test proves the bug is still there AND that the link is its only
  // cause. IT IS MEANT TO GO RED WHEN THE BUG IS FIXED: its last assertion then fails on purpose. That red is the signal
  // to delete this test and the `exceptKnownLink` of test 7 in the same PR as the fix.
  if (env.touch) {
    test(`${env.name} · 8 · fallo conocido: el enlace de la carga desborda la página en el teléfono`, async ({ browser }) => {
      test.info().annotations.push({ type: "known-bug", description: "StockUnitImport.tsx:180 — el enlace de la carga no se parte" });
      test.setTimeout(180_000);
      const { context, page } = await session(browser, "manager", env);
      try {
        const unit = { imei: syntheticImei(KNOWN_BUG_SEED), serial: `R14${RUN.slice(-6)}${env.code}K1`.toUpperCase() };
        const load = await previewUsedLoad(page, env, unit, 1700, 30);
        await expect(load.terms).toContainText("Enlace de esta carga");
        const wrapped = await overflow(page, true);
        expect(wrapped.wrapped, "el enlace de la carga está en la página").toBe(1);
        expect(wrapped.over, "con el enlace partido, nada más desborda").toBeLessThanOrEqual(1);
        const raw = await overflow(page);
        expect(raw.over, "FALLO CONOCIDO: si esto falla, el enlace ya no desborda — borra esta prueba").toBeGreaterThan(1);
      } finally {
        await context.close();
      }
    });
  }
}
