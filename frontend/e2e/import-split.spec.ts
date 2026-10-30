import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

/**
 * IMPORT-SPLIT-01 — dos cargas, cada una con su plantilla de UNA hoja, en navegador real.
 *
 * SIN MOCKS DEL BACKEND. La plantilla se descarga con el enlace de la pantalla,
 * se mira por dentro, se rellena ESE MISMO archivo y se sube:
 *
 *   A · productos y accesorios: una hoja «Productos»; lo cargado entra al
 *       catálogo con su destino y no mueve stock. Una columna «Stock» se dice
 *       que no se importa.
 *   B · equipos: una hoja «Equipos»; serie sola, serie + IMEI y serie + dos
 *       IMEI entran como tres equipos. Una columna «Cantidad» rechaza el archivo.
 *
 * Los libros se leen y se rellenan con openpyxl desde el backend local
 * (`E2E_BACKEND_DIR`), que es con lo que el servidor los escribe.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "cargas-e2e-"));
const TELEFONO = `Teléfono carga ${RUN}`;
const PORTATIL = `Portátil carga ${RUN}`;

function python(code: string, env: Record<string, string>): string {
  return execFileSync("python3", ["-c", code], { cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ...env } }).toString();
}

type Shape = {
  sheets: string[]; headers: string[]; max_row: number; freeze: string | null;
  lists: Record<string, string>; notes: number; text_columns: string[];
};

/** Lo que hay DENTRO de un libro descargado. */
function mirar(archivo: string): Shape {
  return JSON.parse(python(`
import json, os, openpyxl
book = openpyxl.load_workbook(os.environ['E2E_FILE']); sheet = book.worksheets[0]
print(json.dumps({
    'sheets': book.sheetnames,
    'headers': [c.value for c in sheet[1]],
    'max_row': sheet.max_row,
    'freeze': sheet.freeze_panes,
    'lists': {str(rule.sqref): rule.formula1 for rule in sheet.data_validations.dataValidation},
    'notes': sum(1 for c in sheet[1] if c.comment),
    'text_columns': sorted(k for k, v in sheet.column_dimensions.items() if v.number_format == '@'),
}))`, { E2E_FILE: archivo }));
}

/** El MISMO libro descargado, con filas debajo de sus cabeceras (y columnas de más, si se piden). */
function rellenar(archivo: string, filas: unknown[][], columnasDeMas: string[] = []): string {
  const destino = path.join(WORK, `lleno-${path.basename(archivo, ".xlsx")}-${filas.length}-${columnasDeMas.length}.xlsx`);
  python(`
import json, os, openpyxl
book = openpyxl.load_workbook(os.environ['E2E_FILE']); sheet = book.worksheets[0]
extra = json.loads(os.environ['E2E_EXTRA'])
for offset, title in enumerate(extra, start=sheet.max_column + 1):
    sheet.cell(row=1, column=offset, value=title)
for row in json.loads(os.environ['E2E_ROWS']):
    sheet.append(row)
book.save(os.environ['E2E_OUT'])`, { E2E_FILE: archivo, E2E_ROWS: JSON.stringify(filas), E2E_EXTRA: JSON.stringify(columnasDeMas), E2E_OUT: destino });
  return destino;
}

const opciones = (lista: string | undefined) => (lista ?? "").replace(/^"|"$/g, "").split(",").filter(Boolean);

/** Un IMEI válido: 14 dígitos y su dígito de control (Luhn). */
function imei(seed: number): string {
  const body = `35${String(Date.now()).slice(-8)}${String(seed).padStart(4, "0")}`;
  let total = 0;
  for (let index = 0; index < body.length; index += 1) {
    let digit = Number(body[body.length - 1 - index]);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  }
  return body + String((10 - (total % 10)) % 10);
}

/** Dos productos con serie —uno que lleva IMEI y otro que no— para la carga de equipos. */
function sembrarProductos(): void {
  execFileSync("python3", ["manage.py", "shell", "-c", `
import json, os
from decimal import Decimal
from store.models import Company, Product, ProductBarcode
data = json.loads(os.environ['E2E_SEED'])
company = Company.objects.get(slug='black-dog-store')
for row in data:
    product = Product.objects.create(
        company=company, name=row['name'], slug=row['slug'], price=Decimal('999.00'), inventory=0,
        is_active=True, is_serialized=True, requires_imei=row['imei'], is_published_online=False,
    )
    ProductBarcode.objects.create(company=company, product=product, code=row['code'])
print('E2E_SEEDED')
`], {
    cwd: BACKEND_DIR, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_SEED: JSON.stringify([
      { name: TELEFONO, slug: `telefono-carga-${RUN}`, code: `TEL-${RUN}`.toUpperCase(), imei: true },
      { name: PORTATIL, slug: `portatil-carga-${RUN}`, code: `POR-${RUN}`.toUpperCase(), imei: false },
    ]) },
  });
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
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión");
      await page.waitForTimeout(62_000);
    }
  }
}

/** Pulsa «Descargar plantilla» y guarda lo que el navegador recibe. */
async function descargar(page: Page): Promise<{ archivo: string; nombre: string }> {
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Descargar plantilla" }).first().click(),
  ]);
  const nombre = download.suggestedFilename();
  const archivo = path.join(WORK, `${Date.now()}-${nombre}`);
  await download.saveAs(archivo);
  return { archivo, nombre };
}

/** Una lectura de la API con la sesión de quien mira, en el mismo nombre de máquina que la página. */
async function api(page: Page, ruta: string) {
  return page.evaluate(async (raw) => {
    const url = new URL(raw, window.location.origin);
    url.hostname = window.location.hostname;
    const res = await fetch(url.toString(), { credentials: "include" });
    return { status: res.status, body: await res.json().catch(() => null) };
  }, `${process.env.E2E_API_BASE ?? "/api"}${ruta}`);
}

test.describe("dos cargas, cada una con su plantilla de una hoja", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test("A · productos y accesorios: una hoja, catálogo con destino, sin stock", async ({ page }) => {
    await signIn(page);
    await page.goto("/admin/products/import", { waitUntil: "networkidle" });

    const aviso = page.getByRole("note", { name: "Qué hace esta carga" });
    await expect(aviso).toContainText("No mueve stock");
    await expect(aviso.getByRole("link", { name: "Carga de equipos" })).toHaveAttribute("href", "/admin/inventory/units/import");

    const { archivo, nombre } = await descargar(page);
    expect(nombre).toBe("carga-masiva-productos.xlsx");
    const forma = mirar(archivo);
    expect(forma.sheets).toEqual(["Productos"]);
    // DEVICE-COMMERCE-01 grew the template: «Costo de referencia» (DC1) and model columns (DC2).
    expect(forma.headers).toEqual(["Código de barras", "Código", "Nombre", "Descripción", "Precio de venta", "Categoría", "Imagen principal", "Imágenes", "Destino", "Costo de referencia", "Modelo", "Capacidad", "Color", "Línea"]);
    expect(forma.max_row).toBe(1);
    expect(forma.freeze).toBe("A2");
    expect(forma.lists["I2:I501"]).toBe('"Publicar en e-commerce,Solo stock interno"');
    expect(forma.notes).toBe(14); // one note per header, the DEVICE-COMMERCE-01 columns included
    expect(forma.text_columns).toEqual(["A", "B"]);
    // Las categorías de la lista son las de la empresa, leídas al descargar.
    const categorias = opciones(forma.lists["F2:F501"]);
    const delSistema = (await api(page, "/admin/categories/")).body;
    const activas = ((Array.isArray(delSistema) ? delSistema : delSistema?.results ?? []) as { name: string; is_active?: boolean }[])
      .filter((c) => c.is_active !== false).map((c) => c.name).sort((a, b) => a.localeCompare(b, "es"));
    // Una lista de celda es un texto de 255 caracteres separado por comas, y elegir es
    // escribir: si los nombres caben se ofrecen TODOS; si no, la plantilla sale sin lista.
    const caben = activas.length > 0
      && activas.every((nombre) => !/[,"\u0000-\u001f]/.test(nombre) && !/^[=+\-@]/.test(nombre.trimStart()))
      && activas.join(",").length <= 255;
    expect([...categorias].sort((a, b) => a.localeCompare(b, "es"))).toEqual(caben ? activas : []);
    const categoria = categorias[0] ?? activas[0] ?? null;

    const interno = `Funda interna ${RUN}`;
    const publico = `Cable web ${RUN}`;
    const lleno = rellenar(archivo, [
      [null, `INT-${RUN}`, interno, null, 35, categoria, null, null, "Solo stock interno", 12],
      [null, `WEB-${RUN}`, publico, null, 59, categoria, null, null, "Publicar en e-commerce", 7],
    ], ["Stock"]);

    await page.locator('input[type="file"][accept=".xlsx"]').setInputFiles(lleno);
    await page.getByRole("button", { name: "Continuar" }).click();              // hoja: sólo hay una
    await page.getByRole("button", { name: "Previsualizar" }).click();          // columnas
    await expect(page.getByRole("heading", { name: /Previsualización/ })).toBeVisible({ timeout: 30_000 });
    // La columna «Stock» se reconoce y se dice que no entra por aquí.
    const aparte = page.getByRole("alert").filter({ hasText: "Esta carga no mueve stock" });
    await expect(aparte).toBeVisible();
    await expect(aparte).toContainText("Stock");
    await page.getByRole("button", { name: "Continuar" }).click();              // confirmación
    await page.getByRole("button", { name: /Aplicar a 2 producto/ }).click();
    await expect(page.getByText(/Importación aplicada/)).toBeVisible({ timeout: 30_000 });

    const lista = (await api(page, `/admin/products/?search=${encodeURIComponent(RUN)}`)).body;
    const creados = ((Array.isArray(lista) ? lista : lista?.results ?? []) as { name: string; inventory: number; is_published_online: boolean; is_active: boolean }[])
      .filter((p) => [interno, publico].includes(p.name));
    expect(creados.map((p) => p.name).sort()).toEqual([publico, interno].sort());
    for (const producto of creados) {
      expect(producto.inventory, "la carga de productos no mueve stock").toBe(0);
      expect(producto.is_active).toBe(true);                                    // destino no es «activo»
      // Publicar por la carga pide categoría (decisión D2): sin ninguna en la empresa, queda interno.
      expect(producto.is_published_online).toBe(producto.name === publico && categoria !== null);
    }
  });

  test("B · equipos: una hoja, una fila por equipo, con lo que el producto lleva", async ({ page }) => {
    sembrarProductos();
    await signIn(page);
    await page.goto("/admin/inventory/units", { waitUntil: "networkidle" });
    // La carga de equipos tiene su entrada en el menú, con su nombre, dentro de «Más herramientas» (NAV-UX-01).
    const menu = page.getByRole("navigation", { name: "Módulos del control interno" });
    await menu.getByRole("button", { name: "Opciones de Más herramientas" }).click();
    await menu.getByRole("link", { name: "Carga de equipos" }).click();
    await expect(page).toHaveURL(/\/admin\/inventory\/units\/import/);

    const aviso = page.getByRole("note", { name: "Qué hace esta carga" });
    await expect(aviso).toContainText("Una fila = un equipo físico.");
    await expect(aviso).toContainText("Serie sola, serie + IMEI o serie + dos IMEI");

    const { archivo, nombre } = await descargar(page);
    expect(nombre).toBe("carga-masiva-equipos.xlsx");
    const forma = mirar(archivo);
    expect(forma.sheets).toEqual(["Equipos"]);
    // DEVICE-COMMERCE-01 (DC2) grew the template with the model and used-device columns.
    expect(forma.headers).toEqual(["Código", "Producto", "Sucursal", "Número de serie", "IMEI", "IMEI 2", "Condición", "Costo", "Motivo / referencia", "Modelo", "Capacidad", "Color", "Línea", "Estado estético", "Batería %", "Precio de venta"]);
    expect(forma.max_row).toBe(1);
    expect(forma.freeze).toBe("A2");
    expect(forma.lists["G2:G501"]).toBe('"Nuevo,Caja abierta,Reacondicionado,Usado"');
    expect(forma.notes).toBe(16); // one note per header
    expect(forma.text_columns).toEqual(["A", "D", "E", "F"]);
    const sucursal = opciones(forma.lists["C2:C501"])[0];
    expect(sucursal, "la plantilla lista las sucursales de quien carga").toBeTruthy();

    // La plantilla tal como se descarga no registra nada. Se espera a que la pantalla diga
    // qué encontró —cero equipos— y sólo entonces se mira que no ofrezca registrar.
    await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(archivo);
    await page.getByRole("button", { name: "Previsualizar" }).click();
    await expect(page.getByText(/0 equipos por registrar · 0 con error/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: /Registrar \d+ equipo/ })).toHaveCount(0);

    // Una columna que cuenta rechaza el archivo entero.
    const motivo = `Compra de prueba ${RUN}`;
    const soloSerie = [null, PORTATIL, sucursal, `POR${RUN}A`.toUpperCase(), null, null, "Nuevo", null, motivo];
    await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(rellenar(archivo, [[...soloSerie, 2]], ["Cantidad"]));
    await page.getByRole("button", { name: "Previsualizar" }).click();
    await expect(page.getByRole("alert").filter({ hasText: /una fila es un equipo/ })).toBeVisible({ timeout: 20_000 });

    // Las tres formas, en el mismo archivo descargado.
    const series = [`POR${RUN}A`, `TEL${RUN}A`, `TEL${RUN}B`].map((s) => s.toUpperCase());
    const imeis = [imei(21), imei(22), imei(23)];
    const lleno = rellenar(archivo, [
      soloSerie,                                                                             // serie sola
      [`TEL-${RUN}`.toUpperCase(), null, sucursal, series[1], imeis[0], null, "Usado", 800, motivo],     // serie + IMEI
      [`TEL-${RUN}`.toUpperCase(), null, sucursal, series[2], imeis[1], imeis[2], "Nuevo", null, motivo], // serie + dos IMEI
    ]);
    await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(lleno);
    await page.getByRole("button", { name: "Previsualizar" }).click();
    const tabla = page.getByRole("table", { name: "Equipos del archivo" });
    for (const serie of series) {
      await expect(tabla.getByRole("row").filter({ hasText: serie })).toContainText("Se registrará", { timeout: 20_000 });
    }
    // El portátil no lleva IMEI y no trae ninguno: no hay nada que avisar.
    await expect(page.getByRole("note", { name: "IMEI en productos sin marca" })).toHaveCount(0);
    await page.getByRole("button", { name: "Registrar 3 equipos" }).click();
    await expect(page.getByRole("status")).toContainText("Se registraron 3 equipos");

    const equipos = (await api(page, `/admin/inventory/units/?branch=all&search=${encodeURIComponent(RUN.toUpperCase())}`)).body;
    const filas = ((equipos?.results ?? equipos ?? []) as { serial_number: string; imei: string | null; imei2: string | null; status: string }[])
      .filter((u) => series.includes(u.serial_number))
      .map((u) => [u.serial_number, u.imei || null, u.imei2 || null, u.status]);
    expect(filas.sort()).toEqual([
      [series[0], null, null, "available"],
      [series[1], imeis[0], null, "available"],
      [series[2], imeis[1], imeis[2], "available"],
    ].sort());

    // Un IMEI bajo un producto no marcado «lleva IMEI»: se avisa arriba y en la fila, y se guarda.
    await page.goto("/admin/inventory/units/import", { waitUntil: "networkidle" });
    const conImei = `POR${RUN}B`.toUpperCase();
    const suImei = imei(24);
    await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(
      rellenar(archivo, [[null, PORTATIL, sucursal, conImei, suImei, null, "Nuevo", null, motivo]]));
    await page.getByRole("button", { name: "Previsualizar" }).click();
    const avisado = page.getByRole("note", { name: "IMEI en productos sin marca" });
    await expect(avisado).toContainText("1 equipo trae IMEI", { timeout: 20_000 });
    await expect(page.getByRole("table", { name: "Equipos del archivo" }).getByRole("row").filter({ hasText: conImei }))
      .toContainText("no está marcado «lleva IMEI»");
    await page.getByRole("button", { name: "Registrar 1 equipo" }).click();
    await expect(page.getByRole("status")).toContainText("Se registraron 1 equipo");
    const guardado = (await api(page, `/admin/inventory/units/?branch=all&search=${encodeURIComponent(conImei)}`)).body;
    expect(((guardado?.results ?? guardado ?? []) as { serial_number: string; imei: string | null }[])
      .filter((u) => u.serial_number === conImei).map((u) => u.imei)).toEqual([suImei]);
  });
});
