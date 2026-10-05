import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { CUTOUT, DOT, api, signIn } from "./media-helpers";

/**
 * BULK-MEDIA — carga masiva con imágenes, con un navegador de verdad.
 *
 * Un Excel con un producto que cita dos archivos. Primero se previsualiza SIN
 * uno de ellos: la fila lo dice y no se puede aplicar. Se vuelve atrás —el
 * Excel y la imagen ya adjunta siguen ahí—, se añade el que faltaba, y entonces
 * sí: se aplica y la tienda muestra el producto con su galería.
 *
 * El producto lleva la marca «[E2E]» y se desactiva al terminar.
 */

const RUN = Date.now().toString(36);
const NAME = `[E2E] Carga con imágenes ${RUN}`;
const CODE = `E2E-${RUN}`.toUpperCase();
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");

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
  await signIn(page);

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
