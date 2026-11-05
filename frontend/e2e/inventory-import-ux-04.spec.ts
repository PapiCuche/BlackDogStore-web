import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { BACKEND_DIR, TAG, conciliar, contar, limpiar, shell, signIn } from "./inventory-first-support";
import { CUTOUT } from "./media-helpers";

/**
 * INV-IMPORT-UX-04 · una carga dice QUÉ ES (vista previa ≠ importada) y QUÉ HACER, en navegador real y SIN MOCKS.
 *
 *   DEBUG=True DATABASE_URL=postgres://…/blackdog_e2e_<algo> E2E_BACKEND_DIR=<backend> \
 *     E2E_BASE_URL=http://localhost:3053 E2E_API_BASE=http://127.0.0.1:8613/api \
 *     npx playwright test e2e/inventory-import-ux-04.spec.ts
 *
 *   a · productos: 10 filas, 3 con error (2 sin nombre, 1 precio que no es número). El estado lo dice con las cuentas
 *       exactas, el enlace de errores devuelve un CSV, el historial (también tras recargar) la lista como
 *       «Vista previa con errores · no se importó nada» sin «Aplicar», y en la base no nace ningún producto.
 *   b · «Subir el archivo corregido» vuelve al paso 1 con su aviso y conserva modo, categorías e imagen; el corregido
 *       se previsualiza, se aplica y el historial dice «Aplicada · <usuario>» y «Se crearon 10…». En la base: 10.
 *   c · stock: una fila con error no mueve nada; el corregido genera 2 movimientos y el historial lo dice.
 *   d · 390 px: ni la vista previa ni el historial desbordan la página (la tabla tiene su propio desplazamiento).
 *
 * Todo lo escrito lleva la marca `TAG` y `limpiar()` lo borra (la base debe ser desechable: `shell()` se niega si no).
 * Los trabajos de importación quedan en el historial a propósito (son el registro de lo que se intentó).
 */

const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "imp-ux-04-"));
const SHORT = TAG.slice(-6).toUpperCase();
const IMAGE = `foto-${TAG.toLowerCase()}.png`;
const PRODUCT_HEADERS = ["Código", "Nombre", "Precio de venta", "Categoría", "Imagen principal", "Imágenes", "Destino"];

/** Un libro de una hoja, escrito con openpyxl (lo que el backend ya usa); no se añade nada al frontend. */
function libro(nombre: string, hoja: string, cabeceras: string[], filas: unknown[][]): string {
  const destino = path.join(WORK, nombre);
  execFileSync("python3", ["-c", `
import json, os, openpyxl
book = openpyxl.Workbook(); sheet = book.active; sheet.title = os.environ['E2E_SHEET']
sheet.append(json.loads(os.environ['E2E_HEAD']))
for row in json.loads(os.environ['E2E_ROWS']):
    sheet.append(row)
book.save(os.environ['E2E_OUT'])`], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_SHEET: hoja, E2E_HEAD: JSON.stringify(cabeceras), E2E_ROWS: JSON.stringify(filas), E2E_OUT: destino },
  });
  return destino;
}

const code = (index: number) => `U${SHORT}${String(index).padStart(2, "0")}`;
const productName = (index: number) => `Funda UX04 ${TAG} ${String(index).padStart(2, "0")}`;

/** 10 filas; con `errores`, la 3 y la 7 no traen nombre y la 9 trae un precio que no es un número. */
function filasDeProductos(errores: boolean): unknown[][] {
  return Array.from({ length: 10 }, (_, i) => {
    const index = i + 1;
    const broken = errores && (index === 3 || index === 7);
    const price = errores && index === 9 ? "doce soles" : 20 + index;
    return [code(index), broken ? null : productName(index), price, null, index === 1 ? IMAGE : null, null, "Solo stock interno"];
  });
}

/** La sección «Historial de importaciones» de las pantallas de carga. */
function historial(page: Page): Locator {
  return page.locator("section").filter({ has: page.getByRole("heading", { name: "Historial de importaciones", exact: true }) });
}

/** Desborde de la PÁGINA (no de un contenedor con su propio desplazamiento), como `expectNoOverflow` de NAV-UX. */
async function sinDesborde(page: Page, where: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal de la página en ${where}`).toBeLessThanOrEqual(1);
}

/** Un trabajo del historial, leído de la API con la sesión de la página (mismo origen: el proxy de Next). */
async function trabajo(page: Page, filename: string): Promise<{ id: number; status: string; applied_by: string | null; counts: Record<string, number> }> {
  const body = await page.evaluate(async () => {
    const res = await fetch("/api/admin/imports/", { credentials: "include" });
    return res.json();
  });
  const found = ((body?.results ?? []) as { original_filename: string }[]).find((job) => job.original_filename === filename);
  expect(found, `el historial de la API no trae «${filename}»`).toBeTruthy();
  return found as never;
}

async function subirProductos(page: Page, archivo: string) {
  await page.locator('input[type="file"][accept=".xlsx"]').setInputFiles(archivo);
  await expect(page.getByRole("heading", { name: "2 · Elige la hoja" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: /3 · Asigna las columnas/ })).toBeVisible();
}

let context: BrowserContext;
let page: Page;
let stock: { branchName: string; ids: number[]; names: string[]; codes: string[] };

test.describe.configure({ mode: "serial" });
test.setTimeout(240_000);

test.beforeAll(async ({ browser }) => {
  // Una sola sesión para todo el archivo: el inicio de sesión admite 5 por minuto.
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  await signIn(page);
});

test.afterAll(async () => {
  await context?.close();
  const result = limpiar();
  fs.rmSync(WORK, { recursive: true, force: true });
  expect(result.left, "lo que la limpieza no pudo borrar").toEqual([]);
});

test("a · 10 filas con 3 errores: vista previa con errores, CSV, historial sin «Aplicar» y nada creado", async () => {
  expect(contar().products, "la corrida empieza sin productos marcados").toBe(0);
  const conErrores = libro(`productos-${TAG}-con-errores.xlsx`, "Productos", PRODUCT_HEADERS, filasDeProductos(true));

  await page.goto("/admin/products/import", { waitUntil: "networkidle" });
  await subirProductos(page, conErrores);
  // Opciones que «Subir el archivo corregido» debe conservar (escenario b).
  await page.getByLabel("Sólo crear productos nuevos (no tocar los que ya existen)").check();
  await page.getByLabel(/Crear las categorías que falten/).check();
  await page.getByLabel("Elegir imágenes").setInputFiles([{ name: IMAGE, mimeType: "image/png", buffer: CUTOUT }]);
  await expect(page.getByText(/^1 imagen ·/)).toBeVisible();
  await page.getByRole("button", { name: "Previsualizar" }).click();

  const estado = page.getByRole("region", { name: "Estado de la importación" });
  await expect(estado).toBeVisible({ timeout: 30_000 });
  await expect(estado).toContainText("Vista previa con errores · no se importó nada");
  await expect(estado).toContainText("10 filas analizadas. 7 propuestas para crear. 3 con errores que debes revisar.");
  await expect(estado).toContainText("Corrige las 3 filas en tu Excel y vuelve a subirlo.");
  await expect(page.getByText(/Con errores no se importa ninguna fila/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Continuar" })).toBeDisabled();

  // El enlace de errores: un CSV de verdad, con las tres razones.
  const enlace = estado.getByRole("link", { name: "Descargar los 3 errores" });
  const href = await enlace.getAttribute("href");
  expect(href).toBeTruthy();
  const csv = await page.request.get(new URL(href!, page.url()).toString());
  expect(csv.status()).toBe(200);
  expect(csv.headers()["content-type"]).toContain("text/csv");
  const texto = await csv.text();
  expect(texto).toContain("Falta el nombre del producto.");
  expect(texto).toContain("El precio no es un número.");

  // El historial se refresca tras la vista previa, sin recargar…
  const filename = path.basename(conErrores);
  const fila = historial(page).getByRole("row").filter({ hasText: filename });
  await expect(fila).toContainText("Vista previa con errores · no se importó nada");
  // …y sigue diciéndolo al recargar, sin ofrecer «Aplicar» en ningún lado.
  await page.reload({ waitUntil: "networkidle" });
  const recargada = historial(page).getByRole("row").filter({ hasText: filename });
  await expect(recargada).toContainText("Vista previa con errores · no se importó nada");
  await expect(recargada).toContainText("10 filas analizadas. 7 propuestas para crear. 3 con errores que debes revisar.");
  await expect(recargada).toContainText("Corrige las 3 filas en tu Excel y vuelve a subirlo.");
  await expect(recargada.getByRole("link", { name: "Descargar los 3 errores" })).toBeVisible();
  await expect(historial(page).getByRole("button", { name: /Aplicar/ })).toHaveCount(0);
  expect((await trabajo(page, filename)).status).toBe("previewed");

  expect(contar().products, "una vista previa con errores no crea ningún producto").toBe(0);
});

test("b · «Subir el archivo corregido» conserva opciones; el corregido se aplica y el historial dice quién y qué", async () => {
  const conErrores = path.join(WORK, `productos-${TAG}-con-errores.xlsx`);
  await page.goto("/admin/products/import", { waitUntil: "networkidle" });
  await subirProductos(page, conErrores);
  await page.getByLabel("Sólo crear productos nuevos (no tocar los que ya existen)").check();
  await page.getByLabel(/Crear las categorías que falten/).check();
  await page.getByLabel("Elegir imágenes").setInputFiles([{ name: IMAGE, mimeType: "image/png", buffer: CUTOUT }]);
  await page.getByRole("button", { name: "Previsualizar" }).click();
  const estado = page.getByRole("region", { name: "Estado de la importación" });
  await expect(estado).toContainText("Vista previa con errores · no se importó nada", { timeout: 30_000 });

  await estado.getByRole("button", { name: "Subir el archivo corregido" }).click();
  await expect(page.getByRole("heading", { name: "1 · Elige el archivo" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Se conservan el modo, las categorías y las imágenes adjuntas" })).toBeVisible();
  await expect(estado).toHaveCount(0);

  const corregido = libro(`productos-${TAG}-corregido.xlsx`, "Productos", PRODUCT_HEADERS, filasDeProductos(false));
  await subirProductos(page, corregido);
  await expect(page.getByLabel("Sólo crear productos nuevos (no tocar los que ya existen)")).toBeChecked();
  await expect(page.getByLabel(/Crear las categorías que falten/)).toBeChecked();
  await expect(page.getByText(/^1 imagen ·/)).toBeVisible();
  await page.getByRole("button", { name: "Previsualizar" }).click();

  await expect(estado).toContainText("Vista previa · aún no se ha importado nada", { timeout: 30_000 });
  await expect(estado).toContainText("10 filas analizadas. 10 propuestas para crear.");
  await expect(estado).toContainText("Continúa a la confirmación para aplicarla.");
  await expect(estado.getByRole("link", { name: /Descargar/ })).toHaveCount(0);
  await expect(estado.getByRole("button", { name: "Subir el archivo corregido" })).toHaveCount(0);

  // Antes de aplicar, en el historial es una vista previa vieja: nunca se reactiva desde ahí.
  const filename = path.basename(corregido);
  const previa = historial(page).getByRole("row").filter({ hasText: filename });
  await expect(previa).toContainText("Vista previa · aún no se ha importado nada");
  await expect(previa).toContainText("No se aplicó. Para importarla, vuelve a subir el archivo.");
  await expect(historial(page).getByRole("button", { name: /Aplicar/ })).toHaveCount(0);
  expect(contar().products, "la vista previa corregida tampoco crea nada").toBe(0);

  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Aplicar a 10 producto(s)" }).click();
  await expect(page.getByText(/Importación aplicada/)).toBeVisible({ timeout: 30_000 });

  const job = await trabajo(page, filename);
  expect(job.status).toBe("applied");
  expect(job.applied_by, "el trabajo aplicado dice quién").toBeTruthy();
  const aplicada = historial(page).getByRole("row").filter({ hasText: filename });
  await expect(aplicada).toContainText(`Aplicada · ${job.applied_by}`);
  await expect(aplicada).toContainText("10 filas analizadas. Se crearon 10 y se actualizaron 0.");
  await expect(aplicada).not.toContainText("No se aplicó");
  await expect(historial(page).getByRole("button", { name: /Aplicar/ })).toHaveCount(0);

  const counts = contar();
  expect(counts.products, "exactamente los 10 del archivo corregido").toBe(10);
  expect(counts.movements, "la carga de productos no mueve stock").toBe(0);
  // Lo conservado llegó al servidor: el modo «sólo crear» y la imagen adjunta antes de corregir.
  const applied = shell<{ mode: string; images: number }>(`
from store.models import BulkImportJob, ProductImage
job = BulkImportJob.objects.get(pk=data['job'])
out({'mode': (job.options_snapshot or {}).get('mode', ''),
     'images': ProductImage.objects.filter(product__company__slug=data['slug'], product__name=data['name']).count()})
`, { job: job.id, name: productName(1) });
  expect(applied).toEqual({ mode: "create_only", images: 1 });

  // El centro de cargas lista los dos trabajos con su estado y sin «Aplicar».
  await page.goto("/admin/inventory/bulk", { waitUntil: "networkidle" });
  const centro = page.getByRole("region", { name: "Historial" });
  await expect(centro.getByRole("row").filter({ hasText: path.basename(conErrores) }).first())
    .toContainText("Vista previa con errores · no se importó nada");
  await expect(centro.getByRole("row").filter({ hasText: filename })).toContainText(`Aplicada · ${job.applied_by}`);
  await expect(centro.getByRole("button", { name: /Aplicar/ })).toHaveCount(0);
});

test("c · stock: una fila con error no mueve nada; el corregido genera 2 movimientos", async () => {
  stock = shell(`
from django.db import transaction
from store import product_codes
from store.models import Company, Product
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
ids, names = [], []
for index, code in enumerate(data['codes'], start=1):
    name = 'Cable UX04 ' + data['tag'] + ' ' + str(index)
    product = Product.objects.create(
        company=company, name=name, slug=('cable-ux04-' + data['tag'] + '-' + str(index)).lower(), price=Decimal('15.00'),
        inventory=0, is_active=True, is_published_online=False,
    )
    with transaction.atomic():
        product_codes.ensure_code(product, supplied=[code])
    ids.append(product.pk); names.append(name)
out({'branchName': branch.name, 'ids': ids, 'names': names, 'codes': data['codes']})
`, { codes: [`S${SHORT}01`, `S${SHORT}02`] });
  const before = contar().movements;
  const header = `ALMACEN 1 - ${TAG}`;
  const headers = ["ID", "CODIGO", "CODIGO EAN", "NOMBRE", header];
  const rows = (second: number) => [
    [null, stock.codes[0], null, stock.names[0], 5],
    [null, stock.codes[1], null, stock.names[1], second],
  ];
  const conError = libro(`stock-${TAG}-con-error.xlsx`, "Inventario", headers, rows(-3));
  const corregido = libro(`stock-${TAG}-corregido.xlsx`, "Inventario", headers, rows(3));

  async function previsualizar(archivo: string) {
    await page.locator('input[type="file"][accept=".xlsx"]').first().setInputFiles(archivo);
    await expect(page.getByRole("heading", { name: "2 · Qué sucursal es cada almacén" })).toBeVisible({ timeout: 30_000 });
    await page.locator("label").filter({ hasText: header }).locator("select").selectOption({ label: stock.branchName });
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.getByRole("button", { name: "Previsualizar" }).click();
  }

  await page.goto("/admin/inventory/import", { waitUntil: "networkidle" });
  await previsualizar(conError);
  const estado = page.getByRole("region", { name: "Estado de la importación" });
  await expect(estado).toContainText("Vista previa con errores · no se importó nada", { timeout: 30_000 });
  await expect(estado).toContainText("2 filas analizadas. 1 fila cambia el stock. 1 con error que debes revisar.");
  await expect(estado).toContainText("Corrige la fila en tu Excel y vuelve a subirlo.");
  await expect(page.getByText("Con errores no se mueve ningún stock: el archivo entra completo o no entra.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Continuar" })).toBeDisabled();
  const href = await estado.getByRole("link", { name: "Descargar el error" }).getAttribute("href");
  const csv = await page.request.get(new URL(href!, page.url()).toString());
  expect(csv.status()).toBe(200);
  expect(await csv.text()).toContain("es negativo");
  const conErrorFila = historial(page).getByRole("row").filter({ hasText: path.basename(conError) });
  await expect(conErrorFila).toContainText("Vista previa con errores · no se importó nada");
  await expect(historial(page).getByRole("button", { name: /Aplicar/ })).toHaveCount(0);
  expect(contar().movements - before, "con errores no se mueve ningún stock").toBe(0);

  await estado.getByRole("button", { name: "Subir el archivo corregido" }).click();
  await expect(page.getByRole("heading", { name: "1 · Elige el archivo" })).toBeVisible();
  await previsualizar(corregido);
  await expect(estado).toContainText("Vista previa · aún no se ha importado nada", { timeout: 30_000 });
  await expect(estado).toContainText("2 filas analizadas. 2 filas cambian el stock.");
  expect(contar().movements - before, "la vista previa corregida tampoco mueve stock").toBe(0);
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Generar movimientos para 2 fila(s)" }).click();
  await expect(page.getByText(/Inventario aplicado/)).toBeVisible({ timeout: 30_000 });

  const filename = path.basename(corregido);
  const job = await trabajo(page, filename);
  const aplicada = historial(page).getByRole("row").filter({ hasText: filename });
  await expect(aplicada).toContainText(`Aplicada · ${job.applied_by}`);
  await expect(aplicada).toContainText("2 filas analizadas. Se generaron 2 movimientos de stock.");
  await expect(historial(page).getByRole("button", { name: /Aplicar/ })).toHaveCount(0);

  expect(contar().movements - before, "dos movimientos, uno por fila").toBe(2);
  const kardex = conciliar();
  expect(kardex.mismatches).toEqual([]);
  expect(kardex.inventory_field).toEqual([]);
});

test("d · 390 px: la vista previa con errores y el historial no desbordan la página", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/admin/products/import", { waitUntil: "networkidle" });
  await subirProductos(page, path.join(WORK, `productos-${TAG}-con-errores.xlsx`));
  await page.getByLabel("Elegir imágenes").setInputFiles([{ name: IMAGE, mimeType: "image/png", buffer: CUTOUT }]);
  await page.getByRole("button", { name: "Previsualizar" }).click();
  const estado = page.getByRole("region", { name: "Estado de la importación" });
  await expect(estado).toContainText("Vista previa con errores · no se importó nada", { timeout: 30_000 });
  // Los 10 ya existen (escenario b): las 7 filas buenas ahora son propuestas para ACTUALIZAR.
  await expect(estado).toContainText("10 filas analizadas. 7 propuestas para actualizar. 3 con errores que debes revisar.");
  await expect(estado.getByRole("link", { name: "Descargar los 3 errores" })).toBeVisible();
  await expect(estado.getByRole("button", { name: "Subir el archivo corregido" })).toBeVisible();
  await sinDesborde(page, "productos · vista previa con errores");
  await historial(page).scrollIntoViewIfNeeded();
  await expect(historial(page).getByRole("row").filter({ hasText: "Aplicada" }).first()).toBeVisible();
  await sinDesborde(page, "productos · historial");

  await page.goto("/admin/inventory/import", { waitUntil: "networkidle" });
  await expect(historial(page).getByRole("row").filter({ hasText: `stock-${TAG}-corregido.xlsx` })).toBeVisible();
  await sinDesborde(page, "stock · historial");

  await page.goto("/admin/inventory/bulk", { waitUntil: "networkidle" });
  await expect(page.getByRole("region", { name: "Historial" }).getByRole("row").filter({ hasText: TAG }).first()).toBeVisible();
  await sinDesborde(page, "centro de cargas · historial");
});
