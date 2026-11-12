import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { test, expect, type Page } from "@playwright/test";

/**
 * CATALOG-COMMERCIAL-01 · A1 — «Opciones de este modelo» (la paleta) de punta a punta, con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, API y base de datos. Cada guardado se comprueba recargando la página Y leyendo la
 * base, porque un 200 no prueba que algo se haya guardado. Los datos son propios de esta ejecución (`RUN`) y viven en
 * la base que use el backend local (`DATABASE_URL`, `E2E_BACKEND_DIR`). Nunca contra producción.
 *
 * Lo que persiste a propósito (y la limpieza no borra): los atributos con rol «capacidad» y «color» de la empresa y
 * sus opciones «128 GB», «256 GB», «Negro», «Azul» y «Rojo»; son datos de catálogo compartidos entre corridas. Ningún
 * modelo, producto ni fila de paleta queda atado a ellos tras la limpieza. Nada aquí es de Apple ni de la tienda piloto.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const SHOTS = process.env.A1_SHOTS ?? "test-results/catalog-model-palette";
const MODEL = `Modelo paleta ${RUN}`;
const EXISTING = `${MODEL} 128 Negro`;
const LOOSE = `${MODEL} 256 Azul`;
fs.mkdirSync(SHOTS, { recursive: true });

function shell(code: string, env: Record<string, string> = {}): string {
  return execFileSync("python3", ["manage.py", "shell", "-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env, E2E_MODEL: MODEL, E2E_RUN: RUN },
  }).toString();
}

type Stored = {
  palette: string[]; variants: Record<string, string[]>; products: number; models: number;
  variantKeys: string[]; stockRows: number; movements: number;
};

/** What the database holds for this run's model (not what the screen says). */
function stored(): Stored {
  const out = shell(`
import json, os
from store.models import BranchStock, Company, ModelOption, Product, ProductModel, StockMovement
from store import device_catalog
company = Company.objects.get(slug='black-dog-store')
model = ProductModel.objects.get(company=company, name=os.environ['E2E_MODEL'])
variants = {p.name: [o.label for o in device_catalog.variant_options_of(p)] for p in model.variants.order_by('pk')}
print('E2E_JSON' + json.dumps({
    'palette': sorted(ModelOption.objects.filter(model=model).values_list('option__label', flat=True)),
    'variants': variants,
    'products': Product.objects.filter(company=company, name__startswith=os.environ['E2E_MODEL']).count(),
    'models': ProductModel.objects.filter(company=company, name=os.environ['E2E_MODEL']).count(),
    'variantKeys': sorted(model.variants.values_list('variant_key', flat=True)),
    'stockRows': BranchStock.objects.filter(product__name__startswith=os.environ['E2E_MODEL'], quantity__gt=0).count(),
    'movements': StockMovement.objects.filter(product__name__startswith=os.environ['E2E_MODEL']).count(),
}))
`);
  return JSON.parse(out.split("E2E_JSON")[1]);
}

let SESSION_COOKIES: Awaited<ReturnType<import("@playwright/test").BrowserContext["cookies"]>> = [];
let MODEL_ID = 0;

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  const use = card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  await use.click();
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
  test.setTimeout(240_000);
  // The company's capacity and colour attributes (by ROLE, whatever they are called), five options, a model with
  // one variant (128 GB Negro) and one loose product that will become 256 GB Azul from the screen.
  const seeded = shell(`
import os
from store.models import Company, Product, VariantAttribute
from store import device_catalog
company = Company.objects.get(slug='black-dog-store')

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
gb128, gb256 = option(capacity, '128 GB'), option(capacity, '256 GB')
black = option(colour, 'Negro', '#111111'); option(colour, 'Azul', '#1D4ED8'); option(colour, 'Rojo', '#B91C1C')
model = device_catalog.create_model(company=company, name=os.environ['E2E_MODEL'])
existing = Product.objects.create(company=company, name=os.environ['E2E_MODEL'] + ' 128 Negro',
                                  slug='paleta-' + os.environ['E2E_RUN'] + '-128-negro', price='1000.00')
device_catalog.set_variant(existing, model_id=model.pk, option_ids=[gb128.pk, black.pk])
Product.objects.create(company=company, name=os.environ['E2E_MODEL'] + ' 256 Azul',
                       slug='paleta-' + os.environ['E2E_RUN'] + '-256-azul', price='1200.00')
print('E2E_MODEL_ID', model.pk)
`);
  MODEL_ID = Number(seeded.split("E2E_MODEL_ID")[1]);
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  SESSION_COOKIES = await context.cookies();
  await context.close();
});

test.afterAll(() => {
  // Only this run's rows: the palette, the model and its products (no sales, no devices). The attributes stay.
  shell(`
import os
from store.models import Company, ModelOption, Product, ProductModel, ProductVariantOption
company = Company.objects.get(slug='black-dog-store')
for model in ProductModel.objects.filter(company=company, name=os.environ['E2E_MODEL']):
    ModelOption.objects.filter(model=model).delete()
    for product in Product.objects.filter(company=company, name__startswith=os.environ['E2E_MODEL']):
        ProductVariantOption.objects.filter(product=product).delete()
        product.delete()
    model.delete()
print('E2E_CLEAN')
`);
});

test.beforeEach(async ({ context }) => {
  if (SESSION_COOKIES.length) await context.addCookies(SESSION_COOKIES);
});

const panel = (page: Page) => page.getByRole("region", { name: "Opciones de este modelo" });
const variants = (page: Page) => page.getByRole("region", { name: "Variantes" });

async function open(page: Page) {
  await page.goto(`/admin/products/models/${MODEL_ID}`, { waitUntil: "networkidle" });
  await expect(variants(page).getByRole("link", { name: EXISTING })).toBeVisible();
  await expect(panel(page)).toBeVisible();
}

test("1 · la paleta se marca por atributo, se guarda y la tabla de configuraciones dice cuáles tienen variante", async ({ page }) => {
  await open(page);
  await expect(panel(page).getByText(/Este modelo todavía no dice qué opciones admite/)).toBeVisible();
  const capacity = panel(page).getByRole("group", { name: "Capacidad", exact: true });
  const colour = panel(page).getByRole("group", { name: "Color", exact: true });
  await expect(capacity.getByText("Capacidad", { exact: true }).first()).toBeVisible();
  for (const label of ["128 GB", "256 GB"]) await capacity.getByRole("checkbox", { name: label, exact: true }).check();
  for (const label of ["Negro", "Azul"]) await colour.getByRole("checkbox", { name: label, exact: true }).check();
  await expect(colour.getByRole("checkbox", { name: "Rojo", exact: true })).not.toBeChecked();

  const writes: string[] = [];
  page.on("response", (r) => {
    const method = r.request().method();
    // Answered writes (a redirect hop of the dev proxy is not a second save; its path may lose the final slash).
    if (method !== "GET" && r.status() < 300 && r.url().includes("/api/admin/")) writes.push(`${method} ${new URL(r.url()).pathname.replace(/\/?$/, "/")}`);
  });
  await panel(page).getByRole("button", { name: "Guardar paleta" }).click();
  await expect(panel(page).getByRole("status")).toHaveText("Paleta guardada.");
  expect(writes).toEqual([`PUT /api/admin/catalog/models/${MODEL_ID}/palette/`]);

  const table = panel(page).getByRole("table", { name: "Configuraciones de la paleta" });
  await expect(table.getByRole("row")).toHaveCount(5);                      // header + 2 × 2
  const existing = table.getByRole("row").filter({ hasText: "128 GB" }).filter({ hasText: "Negro" });
  await expect(existing).toContainText("Con variante");
  await expect(existing).toContainText(EXISTING);
  await expect(table.getByRole("row").filter({ hasText: "256 GB" }).filter({ hasText: "Azul" })).toContainText("Sin variante");
  await expect(panel(page).getByText(/4 configuraciones · 1 con variante · 3 sin variante/)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "01-paleta-escritorio.png"), fullPage: true });

  await page.reload({ waitUntil: "networkidle" });
  await expect(panel(page).getByRole("group", { name: "Capacidad", exact: true }).getByRole("checkbox", { name: "256 GB", exact: true })).toBeChecked();
  const db = stored();
  expect(db.palette).toEqual(["128 GB", "256 GB", "Azul", "Negro"]);
  expect(db.models).toBe(1);
  expect([db.stockRows, db.movements]).toEqual([0, 0]);                      // a catalogue change creates no stock
});

test("2 · «Agregar variante» sólo ofrece la paleta y la combinación guardada aparece como «Con variante»", async ({ page }) => {
  await open(page);
  await variants(page).getByRole("button", { name: "+ Agregar variante" }).click();
  const search = variants(page).getByRole("search", { name: "Buscar producto" });
  await search.getByRole("textbox").fill(LOOSE);
  await search.getByRole("button", { name: "Buscar" }).click();
  await variants(page).getByRole("radio", { name: LOOSE }).check();
  const form = variants(page).getByRole("form", { name: "Agregar variante" });
  await expect(form.getByLabel("Color").locator("option")).toHaveText(["Elige…", "Azul", "Negro"]);
  await expect(form.getByLabel("Capacidad").locator("option")).toHaveText(["Elige…", "128 GB", "256 GB"]);
  await form.getByLabel("Capacidad").selectOption({ label: "256 GB" });
  await form.getByLabel("Color").selectOption({ label: "Azul" });
  await form.getByRole("button", { name: "Guardar variante" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Variante guardada." })).toBeVisible();
  const table = panel(page).getByRole("table", { name: "Configuraciones de la paleta" });
  await expect(table.getByRole("row").filter({ hasText: "256 GB" }).filter({ hasText: "Azul" })).toContainText("Con variante");

  await page.reload({ waitUntil: "networkidle" });
  const db = stored();
  expect(db.variants).toEqual({ [EXISTING]: ["128 GB", "Negro"], [LOOSE]: ["256 GB", "Azul"] });
  expect(new Set(db.variantKeys).size).toBe(2);                              // two distinct combinations, no duplicate
  expect(db.products).toBe(2);                                               // the same product became the variant
});

test("3 · quitar una opción de la paleta no toca la variante que la usa: se señala «Fuera de la paleta»", async ({ page }) => {
  await open(page);
  await panel(page).getByRole("group", { name: "Color", exact: true }).getByRole("checkbox", { name: "Negro", exact: true }).uncheck();
  await panel(page).getByRole("button", { name: "Guardar paleta" }).click();
  await expect(panel(page).getByRole("status")).toHaveText("Paleta guardada.");
  const row = variants(page).getByRole("row").filter({ has: page.getByRole("link", { name: EXISTING }) });
  await expect(row).toContainText("Fuera de la paleta");
  const outside = panel(page).getByRole("list", { name: "Variantes fuera de la paleta o incompletas" });
  await expect(outside.getByRole("link", { name: EXISTING })).toBeVisible();
  await expect(panel(page).getByText(/1 fuera de la paleta/)).toBeVisible();
  const db = stored();
  expect(db.palette).toEqual(["128 GB", "256 GB", "Azul"]);
  expect(db.variants[EXISTING]).toEqual(["128 GB", "Negro"]);                // kept, not rewritten
  expect(db.products).toBe(2);
});

test("4 · en el teléfono la paleta se usa con el pulgar y la página no se desborda", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const chip = panel(page).getByRole("group", { name: "Capacidad", exact: true }).getByRole("checkbox", { name: "256 GB", exact: true });
  await chip.scrollIntoViewIfNeeded();
  const target = await chip.locator("xpath=..").boundingBox();
  expect(target?.height ?? 0).toBeGreaterThanOrEqual(44);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(panel(page).getByRole("table", { name: "Configuraciones de la paleta" })).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "02-paleta-390.png"), fullPage: true });
  await panel(page).getByRole("button", { name: "Guardar paleta" }).scrollIntoViewIfNeeded();
  await expect(panel(page).getByRole("button", { name: "Guardar paleta" })).toBeDisabled();  // nothing changed
});
