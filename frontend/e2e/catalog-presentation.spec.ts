import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { test, expect, type Page } from "@playwright/test";

import { CUTOUT } from "./media-helpers";

/**
 * CATALOG-REFERENCE-01 · PR E — personalizar el catálogo desde el panel, de punta a punta con el backend de verdad.
 *
 * QUÉ ES REAL: navegador, frontend, API y base de datos LOCAL (una copia sembrada; nunca producción). Lo guardado se
 * comprueba leyendo la base, porque un 200 no prueba nada.
 *
 *   1. «Catálogo de referencia»: buscar un modelo que la empresa no tiene, previsualizarlo («Nuevo») e importarlo.
 *      Queda oculto, unido a su referencia y SIN variantes (ningún `Product`).
 *   2. El modelo importado: «Configuración comercial» y «Imágenes por acabado» con «Imagen pendiente»; subir una foto
 *      propia a un color la deja con su procedencia registrada.
 *
 * Exige el catálogo de referencia cargado en esa base (`load_reference_catalog --file …`); sin él, se omite. La
 * limpieza borra el modelo de esta corrida, su paleta, sus exclusiones y sus fotos; las opciones creadas quedan
 * (son datos de catálogo de la empresa). Ningún nombre de fabricante está escrito aquí: salen del dataset.
 */

const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const SHOTS = process.env.PRE_SHOTS ?? "test-results/catalog-presentation";
fs.mkdirSync(SHOTS, { recursive: true });

let KEY = "";
let NAME = "";
let MODEL_ID = 0;

function shell(code: string, env: Record<string, string> = {}): string {
  return execFileSync("python3", ["manage.py", "shell", "-c", code], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env },
  }).toString();
}

function json<T>(out: string): T {
  return JSON.parse(out.split("E2E_JSON")[1]);
}

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

test.beforeAll(() => {
  // A complete reference model the company would get as NEW (nothing of it exists yet).
  const found = json<{ key: string; name: string } | null>(shell(`
import json
from store.models import Company, ReferenceModel
from store import catalog_reference_import as importer
company = Company.objects.get(slug='black-dog-store')
pick = None
for ref in ReferenceModel.objects.filter(in_dataset=True, completeness='complete').order_by('-sort_order', 'key'):
    item = importer.preview(company, [ref.key])['items'][0]
    if item['state'] == 'new':
        pick = {'key': ref.key, 'name': ref.name}
        break
print('E2E_JSON' + json.dumps(pick))
`));
  if (!found) test.skip(true, "el catálogo de referencia no está cargado o ya se importó todo");
  KEY = found!.key;
  NAME = found!.name;
});

test.afterAll(() => {
  if (!KEY) return;
  shell(`
import os
from store.models import Company, ModelFinishImage, ModelOption, ModelOptionExclusion, ProductModel
company = Company.objects.get(slug='black-dog-store')
for model in ProductModel.objects.filter(company=company, reference_key=os.environ['E2E_KEY']):
    ModelFinishImage.objects.filter(model=model).delete()
    ModelOptionExclusion.objects.filter(model=model).delete()
    ModelOption.objects.filter(model=model).delete()
    model.delete()
print('E2E_CLEAN')
`, { E2E_KEY: KEY });
});

test("importar un modelo de referencia lo deja oculto y sin variantes", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/admin/products/models/reference");
  const list = page.getByRole("list", { name: "Modelos de referencia" });
  await expect(list).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Buscar").fill(NAME);
  await page.getByRole("checkbox", { name: NAME, exact: true }).check();
  await page.getByRole("button", { name: "Previsualizar (1)" }).click();
  const preview = page.getByRole("list", { name: "Modelos a importar" });
  await expect(preview.getByText("Nuevo", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "reference-1280.png"), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: path.join(SHOTS, "reference-375.png"), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.getByRole("button", { name: /^Importar 1 modelo/ }).click();
  await expect(page.getByText("Importación terminada.")).toBeVisible({ timeout: 30_000 });

  const stored = json<{ id: number; published: boolean; variants: number }>(shell(`
import json, os
from store.models import Company, Product, ProductModel
company = Company.objects.get(slug='black-dog-store')
model = ProductModel.objects.get(company=company, reference_key=os.environ['E2E_KEY'])
print('E2E_JSON' + json.dumps({'id': model.pk, 'published': model.is_published_online,
                               'variants': Product.objects.filter(product_model=model).count()}))
`, { E2E_KEY: KEY }));
  expect(stored.published).toBe(false);
  expect(stored.variants).toBe(0);
  MODEL_ID = stored.id;
});

test("el modelo importado muestra su configuración comercial y sube una foto por acabado con procedencia", async ({ page }) => {
  test.setTimeout(240_000);
  test.skip(!MODEL_ID, "la importación anterior no terminó");
  await signIn(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/admin/products/models/${MODEL_ID}`);
  const overview = page.getByRole("region", { name: "Configuración comercial" });
  await expect(overview.getByText("Visibilidad en la tienda")).toBeVisible({ timeout: 30_000 });
  const finishes = page.getByRole("region", { name: "Imágenes por acabado" });
  const first = finishes.getByRole("list", { name: "Acabados" }).getByRole("listitem").first();
  await expect(first.getByText("Imagen pendiente")).toBeVisible();
  const colour = (await first.getByRole("heading").textContent())?.trim() ?? "";
  await page.screenshot({ path: path.join(SHOTS, "model-1280.png"), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: path.join(SHOTS, "model-375.png"), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.setViewportSize({ width: 1280, height: 900 });

  await first.getByRole("button", { name: `Subir imagen de ${colour}` }).click();
  const form = page.getByRole("form", { name: `Subir imagen de ${colour}` });
  await form.getByLabel("Elegir imagen").setInputFiles({ name: "acabado.png", mimeType: "image/png", buffer: CUTOUT });
  await form.getByRole("button", { name: "Subir imagen" }).click();
  // Without an origin nothing is sent.
  await expect(form.getByText("Indica de dónde sale la imagen.")).toBeVisible();
  await form.getByLabel("Origen de la imagen (obligatorio)").selectOption("own_photo");
  await form.getByRole("button", { name: "Subir imagen" }).click();
  await expect(finishes.getByText(`Imagen subida a ${colour}.`)).toBeVisible({ timeout: 30_000 });
  await expect(first.getByText("1 imagen")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "finish-uploaded-1280.png"), fullPage: false });

  const source = json<{ count: number; kind: string }>(shell(`
import json, os
from store.models import ModelFinishImage, StorefrontImage
from store.storefront_media import managed_public_id
rows = list(ModelFinishImage.objects.filter(model_id=int(os.environ['E2E_MODEL_ID'])))
stored = StorefrontImage.objects.get(public_id=managed_public_id(rows[0].image_url)) if rows else None
print('E2E_JSON' + json.dumps({'count': len(rows), 'kind': stored.source_kind if stored else ''}))
`, { E2E_MODEL_ID: String(MODEL_ID) }));
  expect(source).toEqual({ count: 1, kind: "own_photo" });

  // PR G (#374 follow-up): removing the colour's only picture with the keyboard leaves focus on the colour's
  // heading, with a ring the person can see (it was `outline: none`).
  await first.getByRole("button", { name: `Quitar la imagen 1 de ${colour}` }).focus();
  await page.keyboard.press("Enter");
  await expect(finishes.getByRole("button", { name: "Sí, quitar" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(finishes.getByText(`Imagen quitada de ${colour}.`)).toBeVisible({ timeout: 30_000 });
  const heading = first.getByRole("heading", { name: colour });
  await expect(heading).toBeFocused();
  const ring = await heading.evaluate((element) => {
    const style = getComputedStyle(element);
    return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) };
  });
  expect(ring.style).not.toBe("none");
  expect(ring.width).toBeGreaterThanOrEqual(2);
  await page.screenshot({ path: path.join(SHOTS, "finish-removed-focus-1280.png"), fullPage: false });
});
