import { mkdirSync, unlinkSync } from "node:fs";
import { test, expect, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { TAG, imei, sembrar, ficha, shell, type Seed } from "./inventory-first-support";
import {
  RX_USERS, serial, sembrarRecepcion, limpiarRecepcion, estado, sessionFor, jsonCall, type RxSeed,
} from "./inventory-receive-support";

/**
 * COST-WRITE-AUTHORITY (decisión C del propietario) — de punta a punta y SIN MOCKS.
 *
 *   «Rechazar en servidor valores de costo enviados por usuarios sin autorización para gestionarlos. [...]
 *    Conservar recepción sin acceso a costos cuando esté permitida.»
 *
 * Con los usuarios exactos del recorrido de recepción (`inventory-receive-support`): «aju» mueve inventario SIN
 * «Ver costos y márgenes»; «cos» tiene las dos. Se comprueba:
 *   1. «aju» recibe mercadería sin costo, como siempre;
 *   2. una petición manipulada de «aju» que registra un equipo CON costo es un 403 `costs_forbidden` y no escribe nada;
 *   3. «Registrar un equipo rápidamente» no le muestra a «aju» ningún campo de costo (y sí a «cos»);
 *   4. un Excel de equipos con la columna «Costo» llena, subido por «aju», queda rechazado en la pantalla.
 *
 * Mismo molde que `inventory-receive.spec.ts`: guarda de base local y desechable, marca `TAG`, limpieza al final.
 */

const SHOTS = process.env.COST_SHOTS ?? "test-results/cost-shots";
const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };
const IN_FILE = "Este archivo trae costos y tu usuario no puede registrarlos";

let base: Seed;
let rx: RxSeed;
const sessions = new Map<string, { context: BrowserContext; page: Page }>();

async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  const prefix = (page.viewportSize()?.width ?? DESKTOP.width) <= 500 ? "m390" : "d1280";
  await page.screenshot({ path: `${SHOTS}/${prefix}-${name}.png`, fullPage: true });
}

async function both(page: Page, name: string, ready: () => Promise<void>) {
  for (const size of [DESKTOP, MOBILE]) {
    await page.setViewportSize(size);
    await ready();
    const overflow = await page.evaluate(() => document.scrollingElement!.scrollWidth - window.innerWidth);
    expect(overflow, `${name} a ${size.width}px: la página se ensancha ${overflow}px`).toBeLessThanOrEqual(0);
    await shot(page, name);
  }
  await page.setViewportSize(DESKTOP);
}

async function as(browser: Browser, key: "adjust" | "costs") {
  const found = sessions.get(key);
  if (found) return found;
  let made: { context: BrowserContext; page: Page } | null = null;
  for (let attempt = 0; attempt < 3 && !made; attempt++) {
    try { made = await sessionFor(browser, RX_USERS[key]); }
    catch (error) {
      if (attempt === 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000)); // límite de 5 inicios por minuto
    }
  }
  made!.page.on("dialog", (dialog) => void dialog.accept());
  await made!.page.setViewportSize(DESKTOP);
  sessions.set(key, made!);
  return made!;
}

const button = (page: Page, name: string | RegExp) => page.getByRole("button", { name });

async function addProduct(page: Page, name: string): Promise<Locator> {
  await page.locator("#receive-product").fill(name);
  await page.getByRole("option").filter({ hasText: name }).first().click({ timeout: 30_000 });
  const card = page.getByRole("article", { name, exact: true });
  await expect(card).toBeVisible();
  return card;
}

/** Un .xlsx de equipos (la plantilla de la carga) escrito por openpyxl en el servidor de prueba. */
function unitWorkbook(rows: (string | number)[][]): string {
  return shell<string>(`
import openpyxl, os, tempfile
book = openpyxl.Workbook()
sheet = book.active
sheet.title = 'Equipos'
sheet.append(['Código', 'Producto', 'Sucursal', 'Número de serie', 'IMEI', 'IMEI 2', 'Condición', 'Costo', 'Motivo / referencia'])
for row in data['rows']:
    sheet.append(row)
handle, path = tempfile.mkstemp(prefix='cost-authority-', suffix='.xlsx')
os.close(handle)
book.save(path)
out(path)
`, { rows });
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  test.setTimeout(300_000);
  base = sembrar();
  rx = sembrarRecepcion();
});

test.afterAll(async () => {
  for (const { context } of sessions.values()) await context.close().catch(() => undefined);
  if (process.env.E2E_KEEP === "1") { console.warn(`COST-WRITE-AUTHORITY: datos conservados con la marca ${TAG}`); return; }
  const result = limpiarRecepcion();
  expect(result.left, `COST-WRITE-AUTHORITY: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

test.describe("costos · sólo los escribe quien puede gestionarlos", () => {
  test("1 · sin «Ver costos y márgenes» se recibe mercadería sin costo, como siempre", async ({ browser }) => {
    test.setTimeout(240_000);
    const before = ficha(base.cable);
    const { page } = await as(browser, "adjust");

    await page.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 60_000 });
    await page.locator("#receive-branch").selectOption({ label: base.branchName });
    await page.locator("#receive-doc").fill(`GR-${TAG}-C01`);
    await button(page, "Siguiente: artículos").click();
    await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    const card = await addProduct(page, base.cableName);
    await card.getByLabel("Cantidad", { exact: true }).fill("5");
    await expect(page.getByLabel(/Costo por unidad/)).toHaveCount(0);
    await both(page, "c1-recepcion-sin-costo", async () => { await expect(card).toBeVisible(); });

    await button(page, "Siguiente: revisar").click();
    await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible();
    await page.getByLabel(/Revisé los datos y quiero registrar/).check();
    const answered = page.waitForResponse((res) =>
      /receipts\/confirm/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);
    await button(page, /Confirmar recepción|Registrando/).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    expect(ficha(base.cable).stock).toBe(before.stock + 5);
  });

  test("2 · una petición manipulada con costo es un 403 `costs_forbidden` y no escribe nada", async ({ browser }) => {
    const { page } = await as(browser, "adjust");
    const before = estado(rx.newA.id);
    const refused = await jsonCall<{ code: string; field: string; detail: string }>(page.context().request, "/admin/inventory/units/", {
      product_id: rx.newA.id, branch: base.branch, reason: `[E2E] ${TAG}`,
      units: [{ serial_number: serial("CMAN"), imei: imei(9101), condition: "new", cost: "3700.00" }],
    });
    expect(refused.status).toBe(403);
    expect(refused.body.code).toBe("costs_forbidden");
    expect(refused.body.field).toBe("cost");
    expect(refused.body.detail).toContain("Ver costos y márgenes");
    expect(estado(rx.newA.id)).toEqual(before);

    // El mismo equipo sin costo sí entra: recibir sin ver costos sigue siendo suyo.
    const ok = await jsonCall<{ results: Record<string, unknown>[] }>(page.context().request, "/admin/inventory/units/", {
      product_id: rx.newA.id, branch: base.branch, reason: `[E2E] ${TAG}`,
      units: [{ serial_number: serial("CMAN"), imei: imei(9101), condition: "new" }],
    });
    expect(ok.status).toBe(201);
    expect(ok.body.results[0]).not.toHaveProperty("cost");
    const after = estado(rx.newA.id);
    expect(after.units).toHaveLength(before.units.length + 1);
    expect(after.units.at(-1)!.cost).toBeNull();
  });

  test("3 · «Registrar un equipo rápidamente» no le pide un costo a quien no puede registrarlo", async ({ browser }) => {
    test.setTimeout(240_000);
    const { page } = await as(browser, "adjust");
    await page.goto(`/admin/inventory/units?register=1&product=${rx.newB.id}`, { waitUntil: "networkidle" });
    const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Guardar equipo" }) });
    await expect(form.getByRole("button", { name: "Cambiar producto" })).toBeVisible({ timeout: 60_000 });
    await expect(form.getByLabel(/^Costo/)).toHaveCount(0);
    await form.getByLabel("Sucursal", { exact: true }).selectOption({ label: base.branchName });
    await form.getByLabel(/^Número de serie/).fill(serial("CQRK"));
    await form.getByLabel(/^IMEI \*/).fill(imei(9202));
    await form.getByLabel(/^Motivo o documento de ingreso/).fill(`Factura ${TAG}`);
    await both(page, "c3-registro-rapido-sin-costo", async () => { await expect(form).toBeVisible(); });
    await form.getByRole("button", { name: "Guardar equipo" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Equipo registrado" })).toBeVisible({ timeout: 30_000 });
    expect(estado(rx.newB.id).units.map((u) => u.cost)).toEqual([null]);

    // Quien tiene «Ver costos y márgenes» sí ve el campo.
    const costs = await as(browser, "costs");
    await costs.page.goto(`/admin/inventory/units?register=1&product=${rx.newB.id}`, { waitUntil: "networkidle" });
    const costForm = costs.page.locator("form").filter({ has: costs.page.getByRole("button", { name: "Guardar equipo" }) });
    await expect(costForm.getByLabel(/^Costo/)).toBeVisible({ timeout: 60_000 });
  });

  test("4 · un Excel de equipos con costo, subido sin el permiso, queda rechazado en la pantalla", async ({ browser }) => {
    test.setTimeout(240_000);
    const { page } = await as(browser, "adjust");
    const before = estado(rx.ux1.id);
    const file = unitWorkbook([
      ["", rx.ux1.name, base.branchName, serial("CXLS"), imei(9303), "", "Nuevo", "3700", `Compra ${TAG}`],
    ]);

    await page.goto("/admin/inventory/units/import", { waitUntil: "networkidle" });
    const note = page.getByRole("note", { name: "Costos en el archivo" });
    await expect(note).toBeVisible({ timeout: 60_000 });
    await expect(note).toContainText("«Costo»");
    await page.getByLabel("Archivo de equipos (.xlsx)").setInputFiles(file);
    const previewed = page.waitForResponse((res) =>
      /units\/import\/preview/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);
    await button(page, "Previsualizar").click();
    expect((await previewed).status()).toBe(201);
    unlinkSync(file); // only now: the browser reads the chosen file when it sends it
    await expect(page.getByText(IN_FILE)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Registrar \d/ })).toHaveCount(0);
    await expect(page.getByText(/Mientras haya una, no se\s+registra ningún equipo/)).toBeVisible();
    await both(page, "c4-excel-con-costo-rechazado", async () => { await expect(page.getByText(IN_FILE)).toBeVisible(); });
    expect(estado(rx.ux1.id)).toEqual(before);
  });
});
