import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Cookie, type Page } from "@playwright/test";
import { CUTOUT, DOT, SLUG, api, signIn } from "./media-helpers";

/**
 * PRODUCT-MEDIA · BULK-MEDIA · SERVICE-EVIDENCE-CONTEXT — con un navegador de verdad.
 *
 * Tres recorridos, de punta a punta y sin mocks:
 *
 *   1. dos imágenes subidas a un producto desde el panel llegan al catálogo y
 *      a la ficha, con la principal que se eligió;
 *   2. un Excel con imágenes: falta una, se corrige sin empezar de nuevo, se
 *      aplica y el producto aparece en la tienda con su galería;
 *   3. el técnico sube varias fotos con nota a una orden y la orden las conserva.
 *
 * UNA SOLA ENTRADA para los tres. El inicio de sesión tiene un límite de cinco
 * por minuto y por IP que comparte toda la suite: entrar una vez y reutilizar
 * la sesión es lo que evita gastarle el cupo a la prueba siguiente.
 *
 * Cada recorrido deja la base como la encontró: quita las imágenes que subió,
 * desactiva el producto que creó (lleva la marca «[E2E]») y ANULA sus
 * evidencias, que es lo que haría una persona: una evidencia no se borra.
 */

const RUN = Date.now().toString(36);
const NAME = `[E2E] Carga con imágenes ${RUN}`;
const CODE = `E2E-${RUN}`.toUpperCase();
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const INTERNAL = `/api/v1/internal/${SLUG}`;
const NOTE = `[E2E] Corrosión junto al conector ${RUN}`;
const MANAGED = /^\/api\/storefront\/images\/[0-9a-f]{32}$/;

let SESSION: Cookie[] = [];

test.beforeAll(async ({ browser, baseURL }) => {
  // El límite de entradas puede obligar a esperar más de un minuto.
  test.setTimeout(240_000);
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  SESSION = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ context }) => {
  if (SESSION.length) await context.addCookies(SESSION);
});

async function removeAll(page: Page) {
  for (;;) {
    const remove = page.getByRole("button", { name: /^Quitar imagen \d+$/ }).first();
    if ((await remove.count()) === 0) break;
    const before = await page.getByRole("button", { name: /^Quitar imagen \d+$/ }).count();
    await remove.click();
    await page.getByRole("button", { name: "Sí, quitar" }).click();
    await expect(page.getByRole("button", { name: /^Quitar imagen \d+$/ })).toHaveCount(before - 1);
  }
}

test("las imágenes subidas en el panel llegan al catálogo y a la ficha", async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));

  // El primer producto del catálogo público, por su nombre.
  const listed = await page.request.get("/api/products/?ordering=name");
  const [product] = (await listed.json()) as Array<{ id: number; slug: string; name: string; image_url: string }>;
  expect(product, "la base de pruebas no tiene productos").toBeTruthy();

  await page.goto(`/admin/products/${product.id}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Imágenes" })).toBeVisible({ timeout: 20_000 });
  await removeAll(page);

  try {
    // --- subir dos, de una vez ---------------------------------------------
    await page.getByLabel("Elegir imágenes").setInputFiles([
      { name: "frontal.png", mimeType: "image/png", buffer: CUTOUT },
      { name: "detalle.png", mimeType: "image/png", buffer: DOT },
    ]);
    const items = page.getByRole("listitem").filter({ has: page.getByLabel(/^Texto alternativo de la imagen/) });
    await expect(items).toHaveCount(2, { timeout: 30_000 });
    await expect(items.nth(0).getByText("Principal")).toBeVisible();
    const first = (await items.nth(0).getByRole("img").getAttribute("src"))!;
    const second = (await items.nth(1).getByRole("img").getAttribute("src"))!;
    expect(first).toMatch(MANAGED);
    expect(second).toMatch(MANAGED);

    // --- texto alternativo y principal ---------------------------------------
    const alt = page.getByLabel("Texto alternativo de la imagen 2");
    await alt.fill("Detalle del producto");
    await alt.blur();
    await page.getByRole("button", { name: "Marcar como principal: imagen 2" }).click();
    await expect(items.nth(1).getByText("Principal")).toBeVisible();

    // --- persiste al recargar -------------------------------------------------
    await page.reload({ waitUntil: "networkidle" });
    await expect(items).toHaveCount(2);
    await expect(items.nth(1).getByText("Principal")).toBeVisible();
    await expect(page.getByLabel("Texto alternativo de la imagen 2")).toHaveValue("Detalle del producto");
    // Con galería, la dirección no se escribe a mano.
    await expect(page.getByLabel(/Dirección de imagen externa/)).toHaveCount(0);

    // --- la tienda -------------------------------------------------------------
    const api = await page.request.get(`/api/products/?slug=${product.slug}`);
    const [published] = await api.json();
    expect(published.image_url).toBe(second);
    expect(published.images.map((image: { url: string }) => image.url)).toEqual([first, second]);

    await page.goto("/product", { waitUntil: "networkidle" });
    const card = page.locator(`a[href="/product/${product.slug}"]`).first();
    await expect(card.locator("img")).toHaveAttribute("src", second);

    await page.goto(`/product/${product.slug}`, { waitUntil: "networkidle" });
    const main = page.getByTestId("product-main-image").locator("img");
    await expect(main).toHaveAttribute("src", second);
    await expect(main).toHaveAttribute("alt", "Detalle del producto");
    expect(await main.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    const thumbs = page.getByRole("group", { name: "Imágenes del producto" }).getByRole("button");
    await expect(thumbs).toHaveCount(2);
    await thumbs.nth(0).click();
    await expect(main).toHaveAttribute("src", first);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  } finally {
    await page.goto(`/admin/products/${product.id}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "Imágenes" })).toBeVisible({ timeout: 20_000 });
    await removeAll(page);
  }

  // Sin galería, el producto vuelve a no tener imagen subida.
  const after = await page.request.get(`/api/products/?slug=${product.slug}`);
  const [restored] = await after.json();
  expect(restored.images).toEqual([]);
  expect(restored.image_url).toBe("");
  expect(errors).toEqual([]);
});

function workbook(): string {
  const target = path.join(os.tmpdir(), `carga-${RUN}.xlsx`);
  // openpyxl ya es una dependencia del backend: no se añade nada al frontend.
  execFileSync("python3", ["-c", `
import sys, openpyxl
book = openpyxl.Workbook(); sheet = book.active; sheet.title = "Productos"
sheet.append(["Código", "Nombre", "Precio de venta", "Imagen principal", "Imágenes"])
sheet.append([sys.argv[2], sys.argv[3], 19.9, "detalle.png", "frontal.png|detalle.png"])
book.save(sys.argv[1])
`, target, CODE, NAME], { cwd: BACKEND_DIR, stdio: "pipe" });
  return target;
}

test("un Excel con imágenes: falta una, se corrige, se aplica y llega a la tienda", async ({ page }) => {
  test.setTimeout(180_000);
  const file = workbook();

  try {
    await page.goto("/admin/products/import", { waitUntil: "networkidle" });
    await page.locator('input[type="file"][accept=".xlsx"]').setInputFiles(file);

    // La plantilla de la plataforma se reconoce: no hay que asignar columnas.
    await expect(page.getByText("Formato reconocido")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.getByRole("heading", { name: /Asigna las columnas y adjunta las imágenes/ })).toBeVisible();

    // --- primera previsualización: falta «detalle.png» ---------------------
    await page.getByLabel("Elegir imágenes").setInputFiles([
      { name: "frontal.png", mimeType: "image/png", buffer: CUTOUT },
      { name: "sobrante.png", mimeType: "image/png", buffer: DOT },
    ]);
    await expect(page.getByText(/2 imágenes/)).toBeVisible();
    await page.getByRole("button", { name: "Previsualizar" }).click();

    const media = page.getByRole("region", { name: "Imágenes de la importación" });
    await expect(media).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/«detalle\.png»: no está entre los archivos adjuntos/)).toBeVisible();
    await expect(media).toContainText("sobrante.png");
    await expect(page.getByRole("button", { name: "Continuar" })).toBeDisabled();

    // --- se corrige sin empezar de nuevo -------------------------------------
    await page.getByRole("button", { name: "Volver a columnas e imágenes" }).click();
    await expect(page.getByText(/2 imágenes/)).toBeVisible();
    await page.getByRole("button", { name: "Quitar sobrante.png" }).click();
    await page.getByLabel("Elegir imágenes").setInputFiles([
      { name: "detalle.png", mimeType: "image/png", buffer: DOT },
    ]);
    await page.getByRole("button", { name: "Previsualizar" }).click();

    await expect(media).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/no está entre los archivos adjuntos/)).toHaveCount(0);
    await expect(page.getByRole("cell", { name: "★ detalle.png + 1" })).toBeVisible();

    // --- aplicar ---------------------------------------------------------------
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.getByRole("button", { name: /Aplicar a 1 producto/ }).click();
    await expect(page.getByText(/Importación aplicada/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/con 2 imagen\(es\) en sus galerías/)).toBeVisible();

    // --- el producto y su galería, en la tienda ---------------------------------
    const found = await api(page, "GET", `/api/products/?search=${encodeURIComponent(NAME)}`);
    expect(found.data).toHaveLength(1);
    const product = found.data[0];
    expect(product.images).toHaveLength(2);
    const primary = product.images.find((image: { is_primary: boolean }) => image.is_primary);
    // «Imagen principal» manda aunque en «Imágenes» venga después: es la
    // primera de la galería y la que muestra el catálogo.
    expect(product.image_url).toBe(primary.url);
    expect(product.images[0].url).toBe(primary.url);

    await page.goto(`/product/${product.slug}`, { waitUntil: "networkidle" });
    const main = page.getByTestId("product-main-image").locator("img");
    await expect(main).toHaveAttribute("src", primary.url);
    expect(await main.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await expect(page.getByRole("group", { name: "Imágenes del producto" }).getByRole("button")).toHaveCount(2);
  } finally {
    fs.rmSync(file, { force: true });
    // La base de pruebas queda sin el producto a la vista.
    const listed = await api(page, "GET", `/api/admin/products/?search=${encodeURIComponent(NAME)}`);
    for (const row of listed.data?.results ?? []) {
      await api(page, "PATCH", `/api/admin/products/${row.id}/`, { is_active: false });
    }
  }
});

test("el técnico sube varias fotos con nota y la orden las conserva", async ({ page, playwright, baseURL }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));

  const orders = await api(page, "GET", `${INTERNAL}/service/orders/`);
  const order = (orders.data?.results ?? orders.data ?? [])[0];
  test.skip(!order, "la base de pruebas no tiene órdenes de servicio sembradas");

  const base = `${INTERNAL}/service/orders/${order.id}/evidence`;
  const before = await api(page, "GET", `${base}/`);
  const baseline = before.data.stage_counts?.diagnosis ?? 0;
  const known = new Set<number>(before.data.results.map((row: { id: number }) => row.id));

  await page.goto(`/admin/service/orders/${order.id}`, { waitUntil: "networkidle" });
  await expect(page.getByLabel("Etapa de las fotos")).toBeVisible({ timeout: 30_000 });

  try {
    // La cámara está a un toque y no impide elegir fotos ya tomadas.
    await expect(page.getByLabel("Tomar foto")).toHaveAttribute("capture", "environment");
    expect(await page.getByLabel("Elegir fotos").evaluate((input: HTMLInputElement) => input.multiple)).toBe(true);

    await page.getByLabel("Etapa de las fotos").selectOption("diagnosis");
    await page.getByLabel("Elegir fotos").setInputFiles([
      { name: "placa.png", mimeType: "image/png", buffer: CUTOUT },
      { name: "conector.png", mimeType: "image/png", buffer: CUTOUT },
    ]);
    const queue = page.getByRole("list", { name: "Fotos por subir" });
    await expect(queue.getByRole("listitem")).toHaveCount(2);
    await page.getByLabel("Nota de placa.png").fill(NOTE);

    await page.getByRole("button", { name: "Subir 2 fotos" }).click();
    await expect(queue).toHaveCount(0, { timeout: 60_000 });

    // --- la galería ------------------------------------------------------------
    const summary = page.getByRole("list", { name: "Evidencias por etapa" });
    await expect(summary.getByText(new RegExp(`Diagnóstico · ${baseline + 2} fotos?`))).toBeVisible();
    await expect(page.getByText(NOTE)).toBeVisible();

    // --- persiste al recargar ----------------------------------------------------
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.getByText(NOTE)).toBeVisible({ timeout: 30_000 });
    const after = await api(page, "GET", `${base}/`);
    const mine = after.data.results.filter((row: { id: number }) => !known.has(row.id));
    expect(mine).toHaveLength(2);
    for (const row of mine) {
      expect(row.stage).toBe("diagnosis");
      expect(row.visibility).toBe("internal");
      expect(row.uploaded_by).toBe("dev_admin");
      expect(JSON.stringify(row)).not.toContain("storage_key");
    }
    expect(mine.map((row: { caption: string }) => row.caption).sort()).toEqual(["", NOTE].sort());

    // --- en grande ------------------------------------------------------------------
    await page.getByRole("button", { name: `Ver en grande: ${NOTE}` }).click();
    const viewer = page.getByRole("dialog", { name: "Evidencia" });
    await expect(viewer).toContainText(NOTE);
    const big = viewer.getByRole("img");
    await expect(big).toHaveAttribute("src", /\/evidence\/\d+\/content\/$/);
    await expect.poll(() => big.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Escape");
    await expect(viewer).toHaveCount(0);

    // Una evidencia interna no se sirve a quien no tiene sesión.
    // (Un contexto nuevo: `page.request` comparte las cookies de la página.)
    const stranger = await playwright.request.newContext({ baseURL });
    const anonymous = await stranger.get(`${base}/${mine[0].id}/content/`, { failOnStatusCode: false });
    expect([401, 403, 404]).toContain(anonymous.status());
    const listing = await stranger.get(`${base}/`, { failOnStatusCode: false });
    expect([401, 403, 404]).toContain(listing.status());
    await stranger.dispose();

    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  } finally {
    const now = await api(page, "GET", `${base}/`);
    for (const row of now.data?.results ?? []) {
      if (!known.has(row.id) && !row.voided_at) {
        await api(page, "POST", `${base}/${row.id}/void/`, { reason: "[E2E] limpieza de la prueba" });
      }
    }
  }
  expect(errors).toEqual([]);
});
