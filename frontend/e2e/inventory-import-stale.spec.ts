import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { BACKEND_DIR, TAG, conciliar, contar, limpiar, shell, signIn } from "./inventory-first-support";

/**
 * IMPORT-STALE-01 · una vista previa vieja o desfasada no se aplica, en navegador real y SIN MOCKS.
 *
 *   DEBUG=True DATABASE_URL=postgres://…/blackdog_e2e_<algo> E2E_BACKEND_DIR=<backend> \
 *     E2E_BASE_URL=http://localhost:3061 E2E_API_BASE=http://127.0.0.1:8621/api \
 *     npx playwright test e2e/inventory-import-stale.spec.ts
 *
 *   a · una vista previa recién hecha se aplica: 2 movimientos, Kardex conciliado.
 *   b · la misma pantalla con la vista previa envejecida (`created_at` movido 2 h atrás por `shell()`): el servidor
 *       responde 409 `preview_expired`, la pantalla lo dice, «Generar movimientos» queda deshabilitado, no se
 *       escribe nada, y «Subir el archivo otra vez» vuelve al paso del archivo.
 *   c · una venta registrada después de la vista previa (un movimiento creado por `shell()`): 409 `preview_stale`
 *       con el nombre del producto; sólo existe el movimiento de la venta, el estante queda donde lo dejó la venta.
 *
 * Capturas en `STALE_SHOTS` (por defecto `test-results/stale-shots`). Todo lo escrito lleva `TAG` y `limpiar()` lo
 * borra; los trabajos de importación quedan en el historial a propósito.
 */

const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "imp-stale-"));
const SHOTS = process.env.STALE_SHOTS ?? "test-results/stale-shots";
const SHORT = TAG.slice(-6).toUpperCase();
const HEADER = `ALMACEN 1 - ${TAG}`;
const HEADERS = ["ID", "CODIGO", "CODIGO EAN", "NOMBRE", HEADER];

/** Un libro de una hoja, escrito con openpyxl (lo que el backend ya usa). */
function libro(nombre: string, filas: unknown[][]): string {
  const destino = path.join(WORK, nombre);
  execFileSync("python3", ["-c", `
import json, os, openpyxl
book = openpyxl.Workbook(); sheet = book.active; sheet.title = 'Inventario'
sheet.append(json.loads(os.environ['E2E_HEAD']))
for row in json.loads(os.environ['E2E_ROWS']):
    sheet.append(row)
book.save(os.environ['E2E_OUT'])`], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_HEAD: JSON.stringify(HEADERS), E2E_ROWS: JSON.stringify(filas), E2E_OUT: destino },
  });
  return destino;
}

let context: BrowserContext;
let page: Page;
let stock: { branchName: string; branchId: number; ids: number[]; names: string[]; codes: string[] };

const filas = (first: number, second: number) => [
  [null, stock.codes[0], null, stock.names[0], first],
  [null, stock.codes[1], null, stock.names[1], second],
];

/** Stock de los dos productos de la corrida en la sucursal de la prueba, leído de la base. */
function estantes(): number[] {
  return shell<number[]>(`
from store.models import BranchStock
out([BranchStock.objects.filter(branch_id=data['branch'], product_id=pk).values_list('quantity', flat=True).first() or 0
     for pk in data['ids']])
`, { branch: stock.branchId, ids: stock.ids });
}

function estadoDelTrabajo(filename: string): { id: number; status: string } {
  return shell(`
from store.models import BulkImportJob
job = BulkImportJob.objects.filter(company__slug=data['slug'], original_filename=data['name']).order_by('-pk').first()
out({'id': job.pk, 'status': job.status})
`, { name: filename });
}

async function previsualizar(archivo: string) {
  await page.goto("/admin/inventory/import", { waitUntil: "networkidle" });
  await page.locator('input[type="file"][accept=".xlsx"]').first().setInputFiles(archivo);
  await expect(page.getByRole("heading", { name: "2 · Qué sucursal es cada almacén" })).toBeVisible({ timeout: 30_000 });
  await page.locator("label").filter({ hasText: HEADER }).locator("select").selectOption({ label: stock.branchName });
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Previsualizar" }).click();
  await expect(page.getByRole("region", { name: "Estado de la importación" }))
    .toContainText("Vista previa · aún no se ha importado nada", { timeout: 30_000 });
  await page.getByRole("button", { name: "Continuar" }).click();
}

const aplicar = () => page.getByRole("button", { name: /^Generar movimientos para/ });

/** La página entera desde arriba (la barra superior es fija) y, si se da, el aviso solo. */
async function captura(nombre: string, aviso?: ReturnType<Page["getByRole"]>) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(SHOTS, `${nombre}.png`), fullPage: true });
  if (aviso) await aviso.screenshot({ path: path.join(SHOTS, `${nombre}-aviso.png`) });
}

test.describe.configure({ mode: "serial" });
test.setTimeout(240_000);

test.beforeAll(async ({ browser }) => {
  fs.mkdirSync(SHOTS, { recursive: true });
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  await signIn(page);
  stock = shell(`
from django.db import transaction
from store import product_codes
from store.models import Company, Product
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
ids, names = [], []
for index, code in enumerate(data['codes'], start=1):
    name = 'Cable STALE ' + data['tag'] + ' ' + str(index)
    product = Product.objects.create(
        company=company, name=name, slug=('cable-stale-' + data['tag'] + '-' + str(index)).lower(), price=Decimal('15.00'),
        inventory=0, is_active=True, is_published_online=False,
    )
    with transaction.atomic():
        product_codes.ensure_code(product, supplied=[code])
    ids.append(product.pk); names.append(name)
out({'branchName': branch.name, 'branchId': branch.pk, 'ids': ids, 'names': names, 'codes': data['codes']})
`, { codes: [`T${SHORT}01`, `T${SHORT}02`] });
});

test.afterAll(async () => {
  await context?.close();
  const result = limpiar();
  fs.rmSync(WORK, { recursive: true, force: true });
  expect(result.left, "lo que la limpieza no pudo borrar").toEqual([]);
});

test("a · una vista previa recién hecha se aplica", async () => {
  expect(contar().movements, "la corrida empieza sin movimientos marcados").toBe(0);
  await previsualizar(libro(`stale-${TAG}-a.xlsx`, filas(5, 3)));
  await aplicar().click();
  await expect(page.getByText(/Inventario aplicado/)).toBeVisible({ timeout: 30_000 });
  await captura("a-aplicada");
  expect(contar().movements).toBe(2);
  expect(estantes()).toEqual([5, 3]);
  expect(conciliar().mismatches).toEqual([]);
});

test("b · una vista previa caducada se rechaza, lo dice y no escribe nada", async () => {
  const archivo = libro(`stale-${TAG}-b.xlsx`, filas(8, 4));
  await previsualizar(archivo);
  const job = estadoDelTrabajo(path.basename(archivo));
  // Dos horas atrás: más que IMPORT_PREVIEW_TTL_SECONDS (por defecto 3600 s).
  shell(`
from datetime import timedelta
from django.utils import timezone
from store.models import BulkImportJob
out(BulkImportJob.objects.filter(pk=data['job']).update(created_at=timezone.now() - timedelta(hours=2)))
`, { job: job.id });
  const before = contar().movements;

  await aplicar().click();
  const alerta = page.getByRole("alert").filter({ hasText: "Subir el archivo otra vez" });
  await expect(alerta).toContainText("La vista previa caducó · no se aplicó nada", { timeout: 30_000 });
  await expect(alerta).toContainText("Esta vista previa caducó: tiene más de 60 minutos");
  await expect(aplicar()).toBeDisabled();
  await captura("b-caducada", alerta);

  expect(contar().movements - before, "una vista previa caducada no mueve stock").toBe(0);
  expect(estantes()).toEqual([5, 3]);
  expect(estadoDelTrabajo(path.basename(archivo)).status).toBe("previewed");

  await alerta.getByRole("button", { name: "Subir el archivo otra vez" }).click();
  await expect(page.getByRole("heading", { name: "1 · Elige el archivo" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Subir el archivo otra vez" })).toHaveCount(0);
});

test("c · una venta después de la vista previa la deja desfasada: se rechaza entera", async () => {
  const archivo = libro(`stale-${TAG}-c.xlsx`, filas(9, 6));
  await previsualizar(archivo);
  // La caja vende uno del primer producto mientras la pantalla espera.
  shell(`
from store import inventory_services
from store.models import Branch, StockMovement
inventory_services.create_stock_movement(
    branch=Branch.objects.get(pk=data['branch']), product_id=data['product'],
    movement_type=StockMovement.SALE_EXIT, quantity=1, reason='venta E2E ' + data['tag'],
)
out(True)
`, { branch: stock.branchId, product: stock.ids[0] });
  const before = contar().movements;

  await aplicar().click();
  const alerta = page.getByRole("alert").filter({ hasText: "Subir el archivo otra vez" });
  await expect(alerta).toContainText("El inventario cambió desde la vista previa · no se aplicó nada", { timeout: 30_000 });
  await expect(alerta).toContainText(`«${stock.names[0]}»`);
  await expect(aplicar()).toBeDisabled();
  await captura("c-desfasada", alerta);

  expect(contar().movements - before, "nada de la carga se escribió").toBe(0);
  expect(estantes(), "el estante queda donde lo dejó la venta").toEqual([4, 3]);
  expect(estadoDelTrabajo(path.basename(archivo)).status).toBe("previewed");
  expect(conciliar().mismatches).toEqual([]);
});
