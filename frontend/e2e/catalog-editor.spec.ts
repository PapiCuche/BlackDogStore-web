import { execFileSync } from "node:child_process";
import path from "node:path";

import { test, expect, type Page } from "@playwright/test";

/**
 * CATALOG-EDITOR-FIX-01 — editar modelos y variantes, de punta a punta, con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, API y base de datos. Nada se simula: cada guardado se comprueba
 * recargando la página Y leyendo la base (mismo producto, sin duplicados), porque un 200 no prueba que
 * algo se haya guardado.
 *
 * Los datos son propios de esta ejecución (`RUN`) y se crean en la base que use el backend local
 * (`DATABASE_URL`, `E2E_BACKEND_DIR`). Nunca contra producción.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const MODEL = `Equipo editor ${RUN}`;
const VARIANT = `Equipo editor ${RUN} Azul`;
const ATTRIBUTE = `Capacidad ${RUN}`;

function shell(code: string, env: Record<string, string> = {}): string {
  return execFileSync("python3", ["manage.py", "shell", "-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env },
  }).toString();
}

/** What the database holds for this run's model and variant (not what the screen says). */
function stored(): { models: number; products: number; productId: number; price: string; published: boolean;
  options: string[]; description: string; sortOrder: number } {
  const out = shell(`
import json, os
from store.models import Company, Product, ProductModel
from store import device_catalog
company = Company.objects.get(slug='black-dog-store')
model = ProductModel.objects.get(company=company, name=os.environ['E2E_MODEL'])
product = Product.objects.get(company=company, name=os.environ['E2E_VARIANT'])
print('E2E_JSON' + json.dumps({
    'models': ProductModel.objects.filter(company=company, name=os.environ['E2E_MODEL']).count(),
    'products': Product.objects.filter(company=company, name=os.environ['E2E_VARIANT']).count(),
    'productId': product.pk, 'price': str(product.price), 'published': product.is_published_online,
    'options': [o.label for o in device_catalog.variant_options_of(product)],
    'description': model.description, 'sortOrder': model.sort_order,
}))
`, { E2E_MODEL: MODEL, E2E_VARIANT: VARIANT });
  return JSON.parse(out.split("E2E_JSON")[1]);
}

let SESSION_COOKIES: Awaited<ReturnType<import("@playwright/test").BrowserContext["cookies"]>> = [];
let PRODUCT_ID = 0;

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
  // A model like the owner's: one used variant, no options, the company may have no attributes yet.
  const seeded = shell(`
import os
from store.models import Company, Product
from store import device_catalog
company = Company.objects.get(slug='black-dog-store')
model = device_catalog.create_model(company=company, name=os.environ['E2E_MODEL'], description='Antes')
product = Product.objects.create(company=company, name=os.environ['E2E_VARIANT'],
                                 slug='equipo-editor-' + os.environ['E2E_RUN'], price='4090.00',
                                 is_published_online=True)
device_catalog.set_variant(product, model_id=model.pk, condition_line='used', option_ids=[])
print('E2E_PRODUCT', product.pk)
`, { E2E_MODEL: MODEL, E2E_VARIANT: VARIANT, E2E_RUN: RUN });
  PRODUCT_ID = Number(seeded.split("E2E_PRODUCT")[1]);
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  SESSION_COOKIES = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ context }) => {
  if (SESSION_COOKIES.length) await context.addCookies(SESSION_COOKIES);
});

const row = (page: Page) => page.getByRole("row").filter({ has: page.getByRole("link", { name: MODEL, exact: true }) });

test("1 · el listado tiene «Editar modelo» en la fila y abre ESE modelo, también con teclado", async ({ page }) => {
  await page.goto("/admin/products/models", { waitUntil: "networkidle" });
  await expect(page.getByRole("columnheader", { name: "Acciones" })).toBeVisible();
  const edit = row(page).getByRole("link", { name: `Editar modelo ${MODEL}` });
  await expect(edit).toBeVisible();
  await edit.focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/admin\/products\/models\/\d+\?editar=modelo/);
  const form = page.getByRole("form", { name: "Editar modelo" });
  await expect(form.getByLabel("Nombre")).toHaveValue(MODEL);
});

test("2 · editar el modelo persiste tras recargar, sin crear otro", async ({ page }) => {
  await page.goto("/admin/products/models", { waitUntil: "networkidle" });
  await row(page).getByRole("link", { name: `Editar modelo ${MODEL}` }).click();
  const form = page.getByRole("form", { name: "Editar modelo" });
  await form.getByLabel("Descripción").fill(`Después ${RUN}`);
  await form.getByLabel("Orden").fill("7");
  await form.getByRole("button", { name: "Guardar cambios" }).click();
  await expect(page.getByRole("status")).toHaveText("Modelo guardado.");
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.getByText(`Después ${RUN}`)).toBeVisible();
  const db = stored();
  expect([db.models, db.description, db.sortOrder]).toEqual([1, `Después ${RUN}`, 7]);
});

test("3 · con un atributo creado en su panel, la variante se edita en un solo guardado que persiste", async ({ page }) => {
  await page.goto("/admin/products/models", { waitUntil: "networkidle" });
  await row(page).getByRole("link", { name: `Gestionar variantes de ${MODEL}` }).click();
  await page.waitForURL(/#variantes$/);
  const variants = page.getByRole("region", { name: "Variantes" });
  await expect(variants.getByText(VARIANT)).toBeVisible();

  // The attribute and its option, created where the hint points.
  await page.goto("/admin/products/models#atributos", { waitUntil: "networkidle" });
  const attributes = page.getByRole("region", { name: "Atributos y opciones" });
  await attributes.getByRole("textbox", { name: "Nuevo atributo" }).fill(ATTRIBUTE);
  await attributes.getByRole("button", { name: "Crear atributo" }).click();
  const block = attributes.getByRole("listitem", { name: ATTRIBUTE });
  await expect(block).toBeVisible();
  await block.getByLabel("Opción").fill("256 GB");
  await block.getByRole("button", { name: "Agregar opción" }).click();
  await expect(block.getByText("256 GB")).toBeVisible();

  await page.goto("/admin/products/models", { waitUntil: "networkidle" });
  await row(page).getByRole("link", { name: `Gestionar variantes de ${MODEL}` }).click();
  await page.getByRole("button", { name: `Editar variante ${VARIANT}` }).click();
  const form = page.getByRole("form", { name: `Editar variante ${VARIANT}` });
  await form.getByLabel(ATTRIBUTE).selectOption({ label: "256 GB" });
  await form.getByLabel("Precio (S/)").fill("3999");
  await form.getByLabel("Publicado en la tienda").uncheck();
  const writes: string[] = [];
  // Answered writes (a redirect hop of the dev proxy is not a second save).
  page.on("response", (r) => {
    const method = r.request().method();
    if (method !== "GET" && r.status() < 300 && r.url().includes("/api/admin/")) writes.push(`${method} ${new URL(r.url()).pathname.replace(/\/?$/, "/")}`);
  });
  await form.getByRole("button", { name: "Guardar cambios" }).click();
  await expect(page.getByRole("status")).toHaveText("Variante actualizada.");
  expect(writes).toEqual([`PUT /api/admin/products/${PRODUCT_ID}/variant/`]);       // one save, not two

  await page.reload({ waitUntil: "networkidle" });
  const line = page.getByRole("region", { name: "Variantes" }).getByRole("row").filter({ hasText: VARIANT });
  await expect(line).toContainText("256 GB");
  await expect(line).toContainText("3,999.00");
  await expect(line).toContainText("Oculta");
  const db = stored();
  expect(db).toMatchObject({ products: 1, productId: PRODUCT_ID, price: "3999.00", published: false, options: ["256 GB"] });
});

test("4 · en el teléfono las acciones se ven y la página no se desborda", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/admin/products/models", { waitUntil: "networkidle" });
  const edit = row(page).getByRole("link", { name: `Editar modelo ${MODEL}` });
  await edit.scrollIntoViewIfNeeded();
  await expect(edit).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await edit.click();
  await expect(page.getByRole("form", { name: "Editar modelo" })).toBeVisible();
});
