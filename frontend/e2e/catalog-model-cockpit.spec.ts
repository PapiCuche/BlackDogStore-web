import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { test, expect, type BrowserContext, type Page } from "@playwright/test";

/**
 * CATALOG-COMMERCIAL-01 · A1b-2b — el «cockpit» del modelo de punta a punta, con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, API y base de datos. Cada paso se comprueba en la pantalla Y en la base
 * (un 200 no prueba lo que quedó guardado). Datos propios de esta ejecución (`RUN`) en la base que use el backend
 * local (`DATABASE_URL`, `E2E_BACKEND_DIR`). Nunca contra producción.
 *
 * Recorrido: paleta con capacidad, color, SIM y región → «Crear variante» con el nombre propuesto editado →
 * duplicado rechazado → borrador sin precio: «Habilitar» rechazado con sus problemas → con precio: habilitada sin
 * publicar («Habilitada», no pública) → otra variante habilitada y publicada («Publicada») → sin
 * `products.manage` no se ofrece → otra empresa responde 404 → la variante histórica no cambia → ningún stock.
 *
 * Persiste a propósito (y la limpieza no borra): los atributos con rol de la empresa y sus opciones (datos de
 * catálogo compartidos entre corridas) y la segunda empresa, desactivada. Nada aquí es de Apple ni de la tienda
 * piloto.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const SHOTS = process.env.A1B2B_SHOTS ?? "test-results/catalog-model-cockpit";
const MODEL = `Modelo cockpit ${RUN}`;
const HISTORIC = `${MODEL} histórico 128 Negro`;
const EDITED = `${MODEL} 128 Azul (caja)`;
fs.mkdirSync(SHOTS, { recursive: true });

function shell(code: string): string {
  return execFileSync("python3", ["manage.py", "shell", "-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, E2E_MODEL: MODEL, E2E_RUN: RUN },
  }).toString();
}

const PREAMBLE = `
import json, os
from decimal import Decimal
from store.models import (BranchStock, Company, ModelOption, Product, ProductModel, StockMovement, StockUnit,
                          VariantAttribute)
from store import device_catalog
from store.tenancy import company_storefront_products
company = Company.objects.get(slug='black-dog-store')
`;

type Row = {
  id: number; name: string; price: string; published: boolean; origin: string; enabled: boolean;
  pending: boolean; public: boolean; options: string[];
};
type Stored = { rows: Row[]; stockRows: number; movements: number; units: number };

/** What the database holds for this run's model and its products (not what the screen says). */
function stored(): Stored {
  const out = shell(`${PREAMBLE}
model = ProductModel.objects.get(company=company, name=os.environ['E2E_MODEL'])
public = set(company_storefront_products(company).values_list('pk', flat=True))
rows = [{'id': p.pk, 'name': p.name, 'price': str(p.price), 'published': p.is_published_online,
         'origin': p.draft_origin, 'enabled': p.sale_enabled_at is not None, 'pending': p.is_pending_draft,
         'public': p.pk in public, 'options': sorted(o.label for o in device_catalog.variant_options_of(p))}
        for p in Product.objects.filter(company=company, name__startswith=os.environ['E2E_MODEL']).order_by('pk')]
mine = Product.objects.filter(company=company, name__startswith=os.environ['E2E_MODEL'])
print('E2E_JSON' + json.dumps({
    'rows': rows,
    'stockRows': BranchStock.objects.filter(product__in=mine, quantity__gt=0).count(),
    'movements': StockMovement.objects.filter(product__in=mine).count(),
    'units': StockUnit.objects.filter(product__in=mine).count(),
}))
`);
  return JSON.parse(out.split("E2E_JSON")[1]);
}

const row = (db: Stored, name: string) => {
  const found = db.rows.find((r) => r.name === name);
  if (!found) throw new Error(`no hay producto «${name}» en la base: ${db.rows.map((r) => r.name).join(", ")}`);
  return found;
};

let ADMIN: Awaited<ReturnType<BrowserContext["cookies"]>> = [];
let SALES: Awaited<ReturnType<BrowserContext["cookies"]>> = [];
let MODEL_ID = 0;
let FOREIGN = { model: 0, product: 0 };

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
  // The company's four ROLE attributes (whatever they are called), a model with a category and a palette of
  // 2 capacities × 2 colours × 1 SIM × 1 region, one HISTORICAL variant (no provenance, published, priced), and
  // a second company with a model and a product of its own.
  const seeded = shell(`${PREAMBLE}
from store.models import Category

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
gb128, gb256 = option(capacity, '128 GB'), option(capacity, '256 GB')
black, blue = option(colour, 'Negro', '#111111'), option(colour, 'Azul', '#1D4ED8')
esim, intl = option(sim, 'eSIM'), option(region, 'Internacional')
category = Category.objects.create(company=company, name='Cockpit ' + os.environ['E2E_RUN'], slug='cockpit-' + os.environ['E2E_RUN'])
model = device_catalog.create_model(company=company, name=os.environ['E2E_MODEL'], category_id=category.pk)
from store import model_palette
model_palette.set_palette(model, option_ids=[gb128.pk, gb256.pk, black.pk, blue.pk, esim.pk, intl.pk])
historic = Product.objects.create(company=company, name=os.environ['E2E_MODEL'] + ' histórico 128 Negro',
                                  slug='cockpit-' + os.environ['E2E_RUN'] + '-hist', price='1000.00',
                                  category=category, is_published_online=True)
device_catalog.set_variant(historic, model_id=model.pk, option_ids=[gb128.pk, black.pk, esim.pk, intl.pk])

other, _ = Company.objects.get_or_create(slug='cockpit-otra', defaults={'name': 'Otra empresa cockpit'})
other.is_active = True
other.save(update_fields=['is_active'])
foreign_model = device_catalog.create_model(company=other, name='Ajeno ' + os.environ['E2E_RUN'])
foreign = Product.objects.create(company=other, name='Ajeno ' + os.environ['E2E_RUN'], slug='ajeno-' + os.environ['E2E_RUN'],
                                 price='0.00', draft_origin='palette', is_published_online=False)
print('E2E_IDS' + json.dumps({'model': model.pk, 'foreign_model': foreign_model.pk, 'foreign_product': foreign.pk}))
`);
  const ids = JSON.parse(seeded.split("E2E_IDS")[1]);
  MODEL_ID = ids.model;
  FOREIGN = { model: ids.foreign_model, product: ids.foreign_product };
  for (const [username, keep] of [["dev_admin", (c: typeof ADMIN) => { ADMIN = c; }], ["dev_sales", (c: typeof ADMIN) => { SALES = c; }]] as const) {
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    await signIn(page, username);
    keep(await context.cookies());
    await context.close();
  }
});

test.afterAll(() => {
  // Only this run's rows: the palette, the model, its products and the category; the other company is
  // deactivated with its own rows gone. The attributes and their options stay (shared catalogue data).
  shell(`${PREAMBLE}
from store.models import Category, ProductVariantOption
for model in ProductModel.objects.filter(company=company, name=os.environ['E2E_MODEL']):
    ModelOption.objects.filter(model=model).delete()
    for product in Product.objects.filter(company=company, name__startswith=os.environ['E2E_MODEL']):
        ProductVariantOption.objects.filter(product=product).delete()
        product.delete()
    model.delete()
Category.objects.filter(company=company, slug='cockpit-' + os.environ['E2E_RUN']).delete()
other = Company.objects.filter(slug='cockpit-otra').first()
if other:
    Product.objects.filter(company=other, name='Ajeno ' + os.environ['E2E_RUN']).delete()
    ProductModel.objects.filter(company=other, name='Ajeno ' + os.environ['E2E_RUN']).delete()
    other.is_active = False
    other.save(update_fields=['is_active'])
print('E2E_CLEAN')
`);
});

test.beforeEach(async ({ context }) => {
  if (ADMIN.length) await context.addCookies(ADMIN);
});

const summary = (page: Page) => page.getByRole("region", { name: "Resumen del modelo" });
const palette = (page: Page) => page.getByRole("region", { name: "Opciones de este modelo" });
const variants = (page: Page) => page.getByRole("region", { name: "Variantes" });
const configs = (page: Page) => palette(page).getByRole("table", { name: "Configuraciones de la paleta" });
const combo = (page: Page, ...labels: string[]) => {
  let rows = configs(page).getByRole("row");
  for (const label of labels) rows = rows.filter({ hasText: label });
  return rows.first();
};
const variantRow = (page: Page, name: string) =>
  variants(page).getByRole("row").filter({ has: page.getByRole("link", { name, exact: true }) });

async function open(page: Page) {
  await page.goto(`/admin/products/models/${MODEL_ID}`, { waitUntil: "networkidle" });
  await expect(variants(page).getByRole("link", { name: HISTORIC })).toBeVisible();
  await expect(configs(page)).toBeVisible();
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

test("1 · resumen, paleta por rol y «Crear variante» sólo en combinaciones completas sin variante", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await open(page);
  await expect(summary(page)).toContainText("Completas");
  await expect(summary(page).getByText("Sin variante", { exact: true }).locator("xpath=following-sibling::dd[1]")).toHaveText("3");
  const groups = palette(page).getByRole("group");
  const names = await groups.evaluateAll((nodes) => nodes.map((n) => n.getAttribute("aria-label")));
  // Capacity → colour → SIM → region: the company's attributes in ROLE order (other attributes may follow).
  expect(names.slice(0, 4)).toEqual(["Capacidad", "Color", "SIM", "Región"]);
  await expect(combo(page, "128 GB", "Negro")).toContainText("Con variante");
  await expect(combo(page, "128 GB", "Negro").getByRole("button", { name: /Crear variante/ })).toHaveCount(0);
  await expect(configs(page).getByRole("button", { name: /Crear variante/ })).toHaveCount(3);
  const historic = variantRow(page, HISTORIC);
  await expect(historic).toContainText("Publicada");
  await expect(historic).not.toContainText(/Borrador|Creada desde la paleta|Habilitada/);
  await expect(historic.getByRole("button", { name: /para la venta/ })).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "01-cockpit-escritorio.png"), fullPage: true });
});

test("2 · crea una variante desde una combinación con el nombre propuesto editado: nace borrador, sin stock", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await open(page);
  const writes = trackWrites(page);
  await combo(page, "128 GB", "Azul").getByRole("button", { name: "Crear variante 128 GB · Azul · eSIM · Internacional" }).click();
  const form = palette(page).getByRole("form", { name: "Crear variante 128 GB · Azul · eSIM · Internacional" });
  const name = form.getByLabel("Nombre de la variante");
  // The proposal is the server's own generated name: model + options in role order.
  await expect(name).toHaveValue(`${MODEL} 128 GB Azul eSIM Internacional`);
  await expect(name).toBeFocused();
  await form.getByLabel("Línea").selectOption("used");
  await expect(name).toHaveValue(`${MODEL} 128 GB Azul eSIM Internacional Seminuevo`);
  await form.getByLabel("Línea").selectOption("new");
  await name.fill(EDITED);
  await page.screenshot({ path: path.join(SHOTS, "02-crear-variante-escritorio.png"), fullPage: true });
  const submit = form.getByRole("button", { name: "Crear variante" });
  await submit.dblclick();
  await expect(palette(page).getByRole("status")).toContainText(`Se creó el borrador «${EDITED}»`);
  await expect(combo(page, "128 GB", "Azul")).toContainText("Con variante");
  expect(writes).toEqual([`POST /api/admin/catalog/models/${MODEL_ID}/variants/`]);   // one POST for a double click
  const draft = variantRow(page, EDITED);
  await expect(draft).toContainText("Borrador");
  await expect(draft).toContainText("Creada desde la paleta");

  const db = stored();
  expect(row(db, EDITED)).toMatchObject({
    price: "0.00", published: false, origin: "palette", enabled: false, pending: true, public: false,
    options: ["128 GB", "Azul", "Internacional", "eSIM"],
  });
  expect([db.stockRows, db.movements, db.units]).toEqual([0, 0, 0]);
});

test("3 · un duplicado (otra persona creó la misma combinación) se rechaza y la tabla se pone al día", async ({ page }) => {
  await open(page);
  await combo(page, "256 GB", "Negro").getByRole("button", { name: /Crear variante/ }).click();
  const form = palette(page).getByRole("form", { name: /Crear variante 256 GB · Negro/ });
  // Meanwhile, somebody else creates that very combination (through the same service).
  shell(`${PREAMBLE}
model = ProductModel.objects.get(company=company, name=os.environ['E2E_MODEL'])
ids = [o.pk for o in __import__('store.models', fromlist=['VariantOption']).VariantOption.objects.filter(
    company=company, label__in=['256 GB', 'Negro', 'eSIM', 'Internacional'])]
device_catalog.create_variant_product(company, model, condition_line='new', option_ids=ids, name=None, actor=None, request=None)
`);
  await form.getByRole("button", { name: "Crear variante" }).click();
  await expect(form.getByRole("alert")).toContainText("ya es esa variante");
  await expect(combo(page, "256 GB", "Negro")).toContainText("Con variante");
  const db = stored();
  expect(db.rows.filter((r) => r.options.join("|") === "256 GB|Internacional|Negro|eSIM")).toHaveLength(1);
});

test("4 · borrador sin precio: «Habilitar» se rechaza y dice por qué; nada cambia", async ({ page }) => {
  await open(page);
  const draft = variantRow(page, EDITED);
  await draft.getByRole("button", { name: `Habilitar ${EDITED} para la venta` }).click();
  const dialog = variants(page).getByRole("group", { name: `Habilitar ${EDITED}` });
  await expect(dialog.getByRole("checkbox", { name: "Publicar también en la tienda" })).not.toBeChecked();
  await dialog.getByRole("button", { name: "Habilitar para la venta" }).click();
  const alert = dialog.getByRole("alert");
  await expect(alert).toContainText("La variante no está lista para la venta.");
  await expect(alert.getByRole("listitem").filter({ hasText: "No tiene precio de venta." })).toHaveCount(1);
  await page.screenshot({ path: path.join(SHOTS, "03-habilitar-rechazado-escritorio.png"), fullPage: true });
  expect(row(stored(), EDITED)).toMatchObject({ enabled: false, pending: true, published: false, public: false });
});

test("5 · con precio, se habilita sin publicar: «Habilitada» y no pública", async ({ page }) => {
  await open(page);
  await variantRow(page, EDITED).getByRole("button", { name: `Editar variante ${EDITED}` }).click();
  const form = variants(page).getByRole("form", { name: `Editar variante ${EDITED}` });
  await expect(form.getByRole("checkbox", { name: "Publicado en la tienda" })).toBeDisabled();
  await form.getByLabel("Precio (S/)").fill("1500");
  await form.getByRole("button", { name: "Guardar cambios" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Variante actualizada." })).toBeVisible();

  const writes = trackWrites(page);
  await variantRow(page, EDITED).getByRole("button", { name: `Habilitar ${EDITED} para la venta` }).click();
  const dialog = variants(page).getByRole("group", { name: `Habilitar ${EDITED}` });
  await dialog.getByRole("button", { name: "Habilitar para la venta" }).dblclick();
  const notice = page.getByRole("status").filter({ hasText: "quedó habilitada para la venta" });
  await expect(notice).toContainText("Todavía no está publicada");
  await expect(notice).toBeFocused();
  expect(writes.filter((w) => w.includes("enable-sale"))).toHaveLength(1);
  const enabled = variantRow(page, EDITED);
  await expect(enabled).toContainText("Habilitada");
  await expect(enabled.getByRole("button", { name: /para la venta/ })).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "04-habilitada-escritorio.png"), fullPage: true });
  expect(row(stored(), EDITED)).toMatchObject({ price: "1500.00", enabled: true, pending: false, published: false, public: false, origin: "palette" });
});

test("6 · otra variante se habilita CON «publicar»: «Publicada» y pública", async ({ page }) => {
  const other = `${MODEL} 256 GB Negro eSIM Internacional`;
  shell(`${PREAMBLE}
Product.objects.filter(company=company, name='${other}').update(price=Decimal('1800.00'))
`);
  await open(page);
  await variantRow(page, other).getByRole("button", { name: `Habilitar ${other} para la venta` }).click();
  const dialog = variants(page).getByRole("group", { name: `Habilitar ${other}` });
  await dialog.getByRole("checkbox", { name: "Publicar también en la tienda" }).check();
  await dialog.getByRole("button", { name: "Habilitar para la venta" }).click();
  await expect(page.getByRole("status").filter({ hasText: "habilitada para la venta y publicada en la tienda" })).toBeVisible();
  await expect(variantRow(page, other)).toContainText("Publicada");
  expect(row(stored(), other)).toMatchObject({ enabled: true, pending: false, published: true, public: true });
});

test("7 · en un teléfono de 390 px: sin desborde, controles de 44 px y el formulario a la vista", async ({ page }) => {
  // A fresh draft so that «Habilitar para la venta» is on screen.
  shell(`${PREAMBLE}
model = ProductModel.objects.get(company=company, name=os.environ['E2E_MODEL'])
from store.models import VariantOption
ids = [o.pk for o in VariantOption.objects.filter(company=company, label__in=['256 GB', 'Azul', 'eSIM', 'Internacional'])]
device_catalog.create_variant_product(company, model, condition_line='new', option_ids=ids, name=None, actor=None, request=None)
`);
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(await overflow()).toBeLessThanOrEqual(1);
  const sized = [
    palette(page).getByRole("button", { name: "Guardar paleta" }),
    palette(page).getByRole("button", { name: "Cancelar" }),
    palette(page).getByText("Sólo las que aún no tienen variante"),
    variants(page).getByRole("button", { name: "+ Agregar variante" }),
    variants(page).getByRole("button", { name: /^Imágenes de / }).first(),
    variants(page).getByRole("button", { name: /^Habilitar .* para la venta$/ }).first(),
    variants(page).getByRole("button", { name: /^Por revisar/ }),
    page.getByRole("button", { name: "Editar modelo" }),
  ];
  for (const target of sized) {
    await target.scrollIntoViewIfNeeded();
    const box = await target.boundingBox();
    expect(box?.height ?? 0, await target.textContent() ?? "").toBeGreaterThanOrEqual(44);
  }
  await page.screenshot({ path: path.join(SHOTS, "05-cockpit-390.png"), fullPage: true });

  // The palette has no free configuration left; reopen one by taking the draft out is not needed: the enable
  // confirmation is the inline form to check on the phone.
  const pending = variants(page).getByRole("button", { name: /^Habilitar .* para la venta$/ }).first();
  await pending.click();
  const dialog = variants(page).getByRole("group", { name: /^Habilitar / });
  const confirm = dialog.getByRole("button", { name: "Habilitar para la venta" });
  await confirm.scrollIntoViewIfNeeded();
  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect((await confirm.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  expect(await overflow()).toBeLessThanOrEqual(1);
  await page.screenshot({ path: path.join(SHOTS, "06-habilitar-390.png"), fullPage: false });
  await dialog.getByRole("button", { name: "Cancelar" }).click();
});

test("8 · sin `products.manage` se ve el estado pero no se crea ni se habilita (y el servidor lo niega)", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL });
  await context.addCookies(SALES);
  const page = await context.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });
  await open(page);
  await expect(configs(page).getByRole("button", { name: /Crear variante/ })).toHaveCount(0);
  await expect(variants(page).getByRole("button", { name: /para la venta/ })).toHaveCount(0);
  const draft = variants(page).getByRole("row").filter({ hasText: "Borrador" }).first();
  await expect(draft).toContainText("Sólo quien administra el catálogo puede habilitarla.");
  await page.screenshot({ path: path.join(SHOTS, "07-sin-permiso-escritorio.png"), fullPage: true });
  const pendingId = stored().rows.find((r) => r.pending)!.id;
  const refused = await api(page, "POST", `/api/admin/products/${pendingId}/enable-sale/`, { publish: true });
  expect(refused.status).toBe(403);
  const create = await api(page, "POST", `/api/admin/catalog/models/${MODEL_ID}/variants/`, { condition_line: "new", option_ids: [] });
  expect(create.status).toBe(403);
  expect(row(stored(), stored().rows.find((r) => r.id === pendingId)!.name)).toMatchObject({ enabled: false, published: false });
  await context.close();
});

test("9 · lo de otra empresa responde 404 y no se toca", async ({ page }) => {
  await open(page);
  const enable = await api(page, "POST", `/api/admin/products/${FOREIGN.product}/enable-sale/`, { publish: true });
  expect(enable.status).toBe(404);
  const create = await api(page, "POST", `/api/admin/catalog/models/${FOREIGN.model}/variants/`, { condition_line: "new", option_ids: [1] });
  expect(create.status).toBe(404);
  const read = await api(page, "GET", `/api/admin/catalog/models/${FOREIGN.model}/palette/`);
  expect(read.status).toBe(404);
  await page.goto(`/admin/products/models/${FOREIGN.model}`, { waitUntil: "networkidle" });
  await expect(page.getByText(/No se pudo cargar el modelo|No encontrado/).first()).toBeVisible();
  const foreign = shell(`${PREAMBLE}
p = Product.objects.get(pk=${FOREIGN.product})
print('E2E_JSON' + json.dumps({'enabled': p.sale_enabled_at is not None, 'published': p.is_published_online}))
`);
  expect(JSON.parse(foreign.split("E2E_JSON")[1])).toEqual({ enabled: false, published: false });
});

test("10 · la variante histórica queda igual y el catálogo no creó stock", async ({ page }) => {
  await open(page);
  const historic = variantRow(page, HISTORIC);
  await expect(historic).toContainText("Publicada");
  await expect(historic).not.toContainText(/Borrador|Creada desde la paleta|Habilitada el/);
  const db = stored();
  expect(row(db, HISTORIC)).toMatchObject({ price: "1000.00", published: true, origin: "", enabled: false, pending: false, public: true });
  expect([db.stockRows, db.movements, db.units]).toEqual([0, 0, 0]);
  // «Por revisar» lists it only for what it lacks (no picture), never as a draft.
  await variants(page).getByRole("button", { name: /^Por revisar/ }).click();
  await expect(variantRow(page, HISTORIC)).toContainText("Sin imagen");
  await expect(variantRow(page, HISTORIC)).not.toContainText("Borrador");

  await page.screenshot({ path: path.join(SHOTS, "08-por-revisar-escritorio.png"), fullPage: true });
});
