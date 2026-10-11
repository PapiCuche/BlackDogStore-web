import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

/**
 * PRODUCT-DESTINATION-01 — «solo stock interno», en navegador real.
 *
 * SIN MOCKS DEL BACKEND. Se sube un archivo de verdad por la carga masiva y se
 * mira la tienda como la ve cualquiera: lo que se importó como «Solo stock
 * interno» no está en el catálogo, ni en la búsqueda, ni tiene página, ni entra
 * en un carrito, ni se puede cotizar. Lo demás de la misma carga, sí.
 *
 * Y un archivo de antes de la columna «Destino» se importa como siempre.
 *
 * Los libros se escriben con openpyxl desde el backend local (`E2E_BACKEND_DIR`),
 * que es con lo que el servidor escribe su propia plantilla.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "destino-e2e-"));

const INTERNO = `Flex interno ${RUN}`;
const PUBLICO = `Funda web ${RUN}`;
const VIEJO = `Cable de antes ${RUN}`;

type Fila = { codigo: string; nombre: string; precio: number; destino?: string };

/** Un libro de productos. Con `destino` lleva la columna; sin él, es un archivo de antes. */
function libro(nombre: string, filas: Fila[], conDestino: boolean): string {
  const destino = path.join(WORK, nombre);
  const codigo = [
    "import json, os, openpyxl",
    "rows = json.loads(os.environ['E2E_ROWS'])",
    "with_destination = os.environ['E2E_WITH_DESTINATION'] == '1'",
    "wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Productos'",
    "headers = ['Código de barras', 'Código', 'Nombre', 'Descripción', 'Precio de venta', 'Categoría', 'Imagen principal', 'Imágenes']",
    "ws.append(headers + (['Destino (pendiente web)'] if with_destination else []))",
    "for row in rows:",
    "    line = [None, row['codigo'], row['nombre'], None, row['precio'], None, None, None]",
    "    ws.append(line + ([row.get('destino')] if with_destination else []))",
    "wb.save(os.environ['E2E_OUT'])",
  ].join("\n");
  execFileSync("python3", ["-c", codigo], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, E2E_ROWS: JSON.stringify(filas), E2E_WITH_DESTINATION: conDestino ? "1" : "0", E2E_OUT: destino },
  });
  return destino;
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

/** La carga masiva, de principio a fin, como la hace una persona. Devuelve el texto de la vista previa. */
async function importar(page: Page, archivo: string): Promise<string> {
  await page.goto("/admin/products/import", { waitUntil: "networkidle" });
  await page.locator('input[type="file"][accept=".xlsx"]').setInputFiles(archivo);
  await page.getByRole("button", { name: "Continuar" }).click();              // hoja
  await page.getByRole("button", { name: "Previsualizar" }).click();          // columnas
  await expect(page.getByRole("heading", { name: /Previsualización/ })).toBeVisible({ timeout: 30_000 });
  const vista = await page.locator("main").innerText();
  await page.getByRole("button", { name: "Continuar" }).click();              // confirmación
  await page.getByRole("button", { name: /Aplicar a \d+ producto/ }).click();
  await expect(page.getByText(/Importación aplicada/)).toBeVisible({ timeout: 30_000 });
  return vista;
}

/** Lo que la tienda pública responde, preguntado desde el navegador de un visitante. */
async function tienda(page: Page, ruta: string) {
  return page.evaluate(async (url) => {
    const res = await fetch(url, { credentials: "omit" });
    let body: unknown = null;
    try { body = await res.json(); } catch { /* sin cuerpo */ }
    return { status: res.status, body };
  }, ruta);
}

/** Las tarjetas de producto de la página: enlaces a una ficha. No el texto que la propia búsqueda repite. */
const tarjeta = (page: Page, nombre: string) => page.locator('a[href^="/product/"]').filter({ hasText: nombre });

const nombres = (body: unknown): string[] =>
  ((Array.isArray(body) ? body : (body as { results?: unknown[] })?.results ?? []) as { name: string }[]).map((p) => p.name);

test.describe("solo stock interno", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test("A · lo importado como «Solo stock interno» no existe para la tienda; lo demás sí", async ({ page, browser, baseURL }) => {
    await signIn(page);
    const vista = await importar(page, libro("con-destino.xlsx", [
      { codigo: `INT-${RUN}`, nombre: INTERNO, precio: 35, destino: "Solo stock interno" },
      { codigo: `WEB-${RUN}`, nombre: PUBLICO, precio: 59, destino: "Publicar en e-commerce" },
    ], true));

    // La vista previa lo dijo antes de aplicar nada.
    expect(vista).toContain("Destino");
    expect(vista).toContain("Solo stock interno");
    expect(vista).toContain("Publicar en e-commerce");

    // El panel lo lista, con su etiqueta: dentro de la empresa sigue siendo un producto.
    await page.goto("/admin/products", { waitUntil: "networkidle" });
    const fila = page.locator("tr").filter({ hasText: INTERNO });
    await expect(fila).toBeVisible();
    await expect(fila.getByText("Solo stock interno")).toBeVisible();
    await expect(fila.getByText("Activo", { exact: true })).toBeVisible();
    await expect(page.locator("tr").filter({ hasText: PUBLICO }).getByText("Solo stock interno")).toHaveCount(0);

    // Un visitante, sin sesión.
    const visitante = await (await browser.newContext({ baseURL })).newPage();

    // 1 · Catálogo.
    await visitante.goto("/product", { waitUntil: "networkidle" });
    await expect(tarjeta(visitante, PUBLICO).first()).toBeVisible({ timeout: 20_000 });
    await expect(tarjeta(visitante, INTERNO)).toHaveCount(0);

    // 2 · Búsqueda, por su nombre exacto.
    await visitante.goto(`/product?search=${encodeURIComponent(PUBLICO)}`, { waitUntil: "networkidle" });
    await expect(tarjeta(visitante, PUBLICO).first()).toBeVisible({ timeout: 20_000 });
    await visitante.goto(`/product?search=${encodeURIComponent(INTERNO)}`, { waitUntil: "networkidle" });
    await expect(visitante.locator('a[href^="/product/"]')).toHaveCount(0);        // ninguna tarjeta, de nada

    const catalogo = await tienda(visitante, "/api/products/");
    expect(nombres(catalogo.body)).toContain(PUBLICO);
    expect(nombres(catalogo.body)).not.toContain(INTERNO);
    const busqueda = await tienda(visitante, `/api/products/?search=${encodeURIComponent("interno " + RUN)}`);
    expect(nombres(busqueda.body)).toEqual([]);

    // 3 · Su página: como la de un producto que no existe.
    const interno = await page.evaluate(async (nombre) => {
      const res = await fetch(`/api/admin/products/?search=${encodeURIComponent(nombre)}`, { credentials: "include" });
      const fila = (await res.json()).results[0];
      return { id: fila.id as number, slug: fila.slug as string, publicado: fila.is_published_online as boolean };
    }, INTERNO);
    expect(interno.publicado).toBe(false);
    expect((await tienda(visitante, `/api/products/${interno.id}/`)).status).toBe(404);
    expect(nombres((await tienda(visitante, `/api/products/?slug=${interno.slug}`)).body)).toEqual([]);
    await visitante.goto(`/product/${interno.slug}`, { waitUntil: "networkidle" });
    await expect(visitante.getByRole("heading", { name: INTERNO })).toHaveCount(0);
    await expect(visitante.getByRole("button", { name: /Agregar al carrito|Añadir al carrito/i })).toHaveCount(0);

    // 4 · Carrito: no entra.
    const alCarrito = await visitante.evaluate(async (id) => {
      const res = await fetch("/api/cart/add/", {
        method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: `e2e-destino-${id}`, product: id, quantity: 1 }),
      });
      return res.status;
    }, interno.id);
    expect(alCarrito).toBe(404);
    const carrito = await tienda(visitante, `/api/cart/?session_key=e2e-destino-${interno.id}`);
    expect(carrito.body).toEqual([]);

    // 5 · Checkout: un carrito sin él no se puede cotizar ni pagar con él dentro.
    const cotizacion = await visitante.evaluate(async (id) => {
      const res = await fetch("/api/checkout/quote/", {
        method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: `e2e-destino-${id}`, delivery_method: "pickup_store" }),
      });
      return res.status;
    }, interno.id);
    expect(cotizacion).toBe(400);                                              // carrito vacío: nunca entró

    // El de la web, en cambio, entra en el carrito.
    const publico = nombres(catalogo.body).indexOf(PUBLICO);
    const idPublico = ((Array.isArray(catalogo.body) ? catalogo.body : (catalogo.body as { results: { id: number }[] }).results) as { id: number }[])[publico].id;
    const entra = await visitante.evaluate(async (id) => {
      const res = await fetch("/api/cart/add/", {
        method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_key: `e2e-destino-ok-${id}`, product: id, quantity: 1 }),
      });
      return res.status;
    }, idPublico);
    expect([200, 201, 400]).toContain(entra);                                  // 400 = sin stock; nunca 404
    expect(entra).not.toBe(404);
    await visitante.context().close();
  });

  test("B · un archivo de antes, sin la columna «Destino», se importa como siempre: publicado", async ({ page, browser, baseURL }) => {
    await signIn(page);
    const vista = await importar(page, libro("sin-destino.xlsx", [
      { codigo: `OLD-${RUN}`, nombre: VIEJO, precio: 19 },
    ], false));
    expect(vista).toContain(VIEJO);
    expect(vista).toContain("Publicar en e-commerce");                         // lo que va a pasar, a la vista
    expect(vista).not.toContain("Solo stock interno");

    await page.goto("/admin/products", { waitUntil: "networkidle" });
    await expect(page.locator("tr").filter({ hasText: VIEJO }).getByText("Solo stock interno")).toHaveCount(0);

    const visitante = await (await browser.newContext({ baseURL })).newPage();
    await visitante.goto(`/product?search=${encodeURIComponent(VIEJO)}`, { waitUntil: "networkidle" });
    await expect(tarjeta(visitante, VIEJO).first()).toBeVisible({ timeout: 20_000 });
    expect(nombres((await tienda(visitante, `/api/products/?search=${encodeURIComponent(VIEJO)}`)).body)).toEqual([VIEJO]);
    await visitante.context().close();
  });

  test("C · volver a subir el archivo viejo no devuelve a la web lo que se dejó interno", async ({ page, browser, baseURL }) => {
    await signIn(page);
    // El mismo producto interno de A, en un archivo sin columna «Destino» y con otro precio.
    await importar(page, libro("interno-sin-destino.xlsx", [
      { codigo: `INT-${RUN}`, nombre: INTERNO, precio: 40 },
    ], false));

    const estado = await page.evaluate(async (nombre) => {
      const res = await fetch(`/api/admin/products/?search=${encodeURIComponent(nombre)}`, { credentials: "include" });
      const fila = (await res.json()).results[0];
      return { precio: fila.price as string, publicado: fila.is_published_online as boolean };
    }, INTERNO);
    expect(estado).toEqual({ precio: "40.00", publicado: false });

    const visitante = await (await browser.newContext({ baseURL })).newPage();
    await visitante.goto(`/product?search=${encodeURIComponent(INTERNO)}`, { waitUntil: "networkidle" });
    await expect(visitante.locator('a[href^="/product/"]')).toHaveCount(0);
    expect(nombres((await tienda(visitante, `/api/products/?search=${encodeURIComponent(INTERNO)}`)).body)).toEqual([]);
    await visitante.context().close();
  });
});
