import { mkdirSync } from "node:fs";
import { test, expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { TAG, apiBase, call } from "./inventory-first-support";
import { expectNoOverflow } from "./nav-ux-support";
import {
  CX_USERS, CX_PASSWORD, sembrarRecuento, salida, recuento, existencias, recuentosDe, limpiarRecuento, type CxSeed,
} from "./inventory-count-ux-04-support";

/**
 * INV-COUNT-UX-04 — «Nuevo recuento» abre directo a la pantalla de conteo, con la sucursal nombrada y sus pendientes.
 * De punta a punta y SIN MOCKS: navegador real + Django real sobre una base desechable (`shell()` se niega a correr
 * contra otra cosa: DEBUG, servidor local y nombre de base con «e2e», «qae» o «test»).
 *
 * Datos sintéticos con la marca `TAG` (ver `inventory-count-ux-04-support.ts`); limpieza por marca al final
 * (`E2E_KEEP=1` la omite). Cuatro usuarios propios, cada uno inicia sesión UNA vez por la API (límite 5/min).
 *
 *   DATABASE_URL=postgres://…/blackdog_e2e_cux04 DEBUG=True DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store \
 *     E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3052 E2E_API_BASE=http://127.0.0.1:8612/api \
 *     npx playwright test e2e/inventory-count-ux-04.spec.ts
 *
 * Escenarios (seriales, en este orden porque cada uno parte del estado del anterior):
 *   1a  counter abre el recuento de A con la lista → aterriza en /counts/<id>; «Pendientes» = los accesorios de A;
 *       dos lecturas en «Sumar 1» y un total escrito.
 *   3   escanear el código del equipo avisa que se verifica por unidad; no escribe cantidad ni línea.
 *   1b  una salida manual en la línea en blanco, una venta en una contada; envío a revisión; sin «Aprobar».
 *   2   approver aprueba: correcciones = físico − teórico al iniciar, sobre el stock vigente; la línea en blanco
 *       intacta; la venta se conserva; una segunda aprobación por la API no cambia nada.
 *   4   viewer: la nota de «Mover inventario», sin «Abrir recuento»; POST por la API → 403.
 *   5   usuario de una sola sucursal: la sucursal como texto; el recuento se abre en ella; POST con B → 404.
 *   7   historial: el filtro «Borrador» muestra el borrador; «Continuar el recuento #N» lo abre.
 *   6   anular ese borrador desde su pantalla.
 *   8   390 px: crear y la pantalla de conteo sin desborde horizontal.
 */

const SHOTS = process.env.CUX04_SHOTS ?? "test-results/cux04-shots";
const DESKTOP = { width: 1280, height: 800 };
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";
const COUNTS = "/admin/inventory/counts/";

type Who = keyof typeof CX_USERS;
let seed: CxSeed;
const sessions = new Map<Who, { context: BrowserContext; page: Page }>();
const pageErrors: string[] = [];
/** Los recuentos que abre la prueba, por escenario. */
const ids: { main?: number; single?: number; mobile?: number } = {};

async function shot(page: Page, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

/** Inicia sesión por la API (la misma ruta que la pantalla). Un 429 (5 inicios por minuto) espera y reintenta. */
async function login(browser: Browser, username: string, options: Parameters<Browser["newContext"]>[0] = { viewport: DESKTOP }) {
  for (let attempt = 0; ; attempt++) {
    const context = await browser.newContext({ ...options, baseURL: BASE });
    const res = await context.request.post(`${apiBase()}/auth/login/`, { data: { username, password: CX_PASSWORD } });
    if (res.status() === 200) {
      await context.request.get(`${apiBase()}/auth/csrf/`);
      return context;
    }
    await context.close();
    if (res.status() !== 429 || attempt >= 2) throw new Error(`no pude iniciar sesión como ${username}: ${res.status()}`);
    await new Promise((resolve) => setTimeout(resolve, 62_000));
  }
}

async function as(browser: Browser, who: Who) {
  const found = sessions.get(who);
  if (found) return found;
  const context = await login(browser, CX_USERS[who]);
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept()); // window.confirm de enviar, aprobar y anular
  page.on("pageerror", (error) => pageErrors.push(`${who}: ${error.message}`));
  const made = { context, page };
  sessions.set(who, made);
  return made;
}

const newCount = (page: Page) => page.getByRole("region", { name: "Nuevo recuento" });
const pendingSection = (page: Page) => page.getByRole("region", { name: /^(Pendientes|No contados) \(/ });
const scanField = (page: Page) => page.getByLabel("Código de barras o búsqueda");
const countedCell = (page: Page, name: string) => page.locator("tr").filter({ hasText: name }).getByTestId("counted-cell");

async function openCounts(page: Page) {
  await page.goto("/admin/inventory/counts", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Recuentos físicos" })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Cargando recuentos…")).toHaveCount(0, { timeout: 30_000 });
}

/** Pulsa «Abrir recuento» y espera la pantalla del recuento nuevo; devuelve su número. */
async function openAndLand(page: Page): Promise<number> {
  await newCount(page).getByRole("button", { name: "Abrir recuento" }).click();
  await page.waitForURL(/\/admin\/inventory\/counts\/\d+$/, { timeout: 60_000 });
  const id = Number(new URL(page.url()).pathname.split("/").pop());
  expect(id).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: `Recuento #${id}` })).toBeVisible({ timeout: 60_000 });
  return id;
}

async function scan(page: Page, code: string) {
  await scanField(page).fill(code);
  await scanField(page).press("Enter");
}

test.describe.configure({ mode: "serial" });

test.describe("INV-COUNT-UX-04 · recuento que abre directo a contar", () => {
  test.setTimeout(240_000);

  test.beforeAll(() => {
    seed = sembrarRecuento();
  });

  test.afterAll(async () => {
    for (const { context } of sessions.values()) await context.close().catch(() => {});
    if (process.env.E2E_KEEP === "1") {
      console.warn(`INV-COUNT-UX-04: E2E_KEEP=1, quedan los datos de ${TAG}`);
      return;
    }
    const result = limpiarRecuento();
    console.log(`INV-COUNT-UX-04 limpieza: ${JSON.stringify(result.removed)}`);
    if (result.left.length) throw new Error(`limpieza incompleta: ${result.left.join(" | ")}`);
  });

  test("1a · counter abre el recuento de A con la lista y cuenta con el lector y a mano", async ({ browser }) => {
    const { page } = await as(browser, "counter");
    await openCounts(page);
    const panel = newCount(page);
    await expect(panel).toBeVisible();
    // Varias sucursales: hay que elegir; la propuesta no es A.
    const select = panel.getByLabel("Sucursal que se cuenta");
    await expect(select).toBeVisible();
    await select.selectOption({ label: seed.branchA.name });
    await panel.getByLabel("Motivo").fill(`Recuento E2E ${TAG}`);
    await expect(panel.getByRole("checkbox", { name: /Listar los productos con stock en esta sucursal/ })).toBeChecked();
    await shot(page, "1a-nuevo-recuento");

    const id = await openAndLand(page);
    ids.main = id;
    const created = recuento(id);
    expect(created).toMatchObject({ status: "draft", branch: seed.branchA.id, created_by: CX_USERS.counter });
    // Listado en blanco: los tres accesorios de A, sin cantidad ni foto; ni el de B ni el equipo.
    expect(created.lines.map((l) => l.product).sort()).toEqual([seed.cable.id, seed.funda.id, seed.cargador.id].sort());
    expect(created.lines.every((l) => l.physical === null && l.start === null)).toBe(true);
    expect(created.movements).toEqual([]);

    const pending = pendingSection(page);
    await expect(pending.getByRole("heading", { name: "Pendientes (3)" })).toBeVisible();
    for (const item of [seed.cable, seed.funda, seed.cargador]) {
      await expect(pending.getByRole("button", { name: `Contar ${item.name}` })).toBeVisible();
    }
    await expect(pending.getByText(seed.protector.name)).toHaveCount(0);
    await expect(pending.getByText(seed.device.name)).toHaveCount(0);
    await expect(scanField(page)).toBeFocused();
    await shot(page, "1a-pantalla-de-conteo");

    // «Sumar 1 por escaneo»: dos lecturas del cable.
    await page.getByRole("radio", { name: /Sumar 1 por escaneo/ }).check();
    await scan(page, seed.cable.code);
    await expect(countedCell(page, seed.cable.name)).toHaveText("1", { timeout: 30_000 });
    await scan(page, seed.cable.code);
    await expect(countedCell(page, seed.cable.name)).toHaveText("2", { timeout: 30_000 });

    // Una venta de la funda ANTES de contarla: la foto la toma la primera cantidad, no la lista.
    salida(seed.branchA.id, seed.funda.id, 1, "sale_exit");
    await page.getByRole("radio", { name: /Cantidad total/ }).check();
    await pending.getByRole("button", { name: `Contar ${seed.funda.name}` }).click();
    const entry = page.getByRole("form", { name: `Cantidad de ${seed.funda.name}` });
    await expect(entry).toBeVisible();
    await entry.getByLabel("Cantidad contada").fill("7");
    await entry.getByRole("button", { name: "Guardar total" }).click();
    await expect(countedCell(page, seed.funda.name)).toHaveText("7", { timeout: 30_000 });
    await expect(pending.getByRole("heading", { name: "Pendientes (1)" })).toBeVisible();
    await expect(pending.getByRole("button", { name: `Contar ${seed.cargador.name}` })).toBeVisible();
    await expect(countedCell(page, seed.cargador.name)).toHaveText("Sin contar");

    const state = recuento(id);
    expect(state.status).toBe("counting");
    const line = (product: number) => state.lines.find((l) => l.product === product)!;
    expect(line(seed.cable.id)).toMatchObject({ physical: 2, start: 10 });
    expect(line(seed.funda.id)).toMatchObject({ physical: 7, start: 5 });
    expect(line(seed.cargador.id)).toMatchObject({ physical: null, start: null });
    expect(state.movements).toEqual([]); // contar no mueve stock
    await shot(page, "1a-contado");
  });

  test("3 · el código de un equipo dice que se verifica por unidad y no escribe cantidad", async ({ browser }) => {
    const { page } = await as(browser, "counter");
    const alerts = page.getByTestId("count-alerts").getByRole("alert");
    for (const mode of [/Sumar 1 por escaneo/, /Cantidad total/]) {
      await page.getByRole("radio", { name: mode }).check();
      await scan(page, seed.device.code);
      await expect(alerts).toContainText(`${seed.device.name} es un equipo con serie: no se cuenta por cantidad`, { timeout: 30_000 });
      await expect(alerts.getByRole("link", { name: "Inventario › Equipos" })).toHaveAttribute("href", "/admin/inventory/units");
      await expect(page.getByRole("form", { name: `Cantidad de ${seed.device.name}` })).toHaveCount(0);
    }
    await shot(page, "3-equipo-por-unidad");
    const state = recuento(ids.main!);
    expect(state.lines.map((l) => l.product)).not.toContain(seed.device.id);
    expect(state.lines.find((l) => l.product === seed.cable.id)?.physical).toBe(2); // nada se sumó
  });

  test("1b · salidas a mitad del recuento, envío a revisión y counter sin «Aprobar»", async ({ browser }) => {
    const { page, context } = await as(browser, "counter");
    // La línea en blanco recibe una salida manual; la funda ya contada, una venta.
    salida(seed.branchA.id, seed.cargador.id, 1, "manual_exit");
    salida(seed.branchA.id, seed.funda.id, 1, "sale_exit");

    await page.getByRole("button", { name: "Enviar a revisión" }).click(); // confirm aceptado
    await expect(page.locator("strong").filter({ hasText: "Pendiente de aprobación" })).toBeVisible({ timeout: 30_000 });
    await expect.poll(() => recuento(ids.main!).status).toBe("review");
    await expect(page.getByRole("button", { name: "Aprobar y ajustar" })).toHaveCount(0);
    await shot(page, "1b-en-revision-counter");

    const refused = await call(context.request, "POST", `${COUNTS}${ids.main}/approve/`, {});
    expect(refused.status, JSON.stringify(refused.body)).toBe(403);
    expect(recuento(ids.main!).status).toBe("review");
  });

  test("2 · approver aprueba: sólo lo contado se ajusta, como delta sobre el stock vigente", async ({ browser }) => {
    const before = existencias([seed.cargador.id, seed.protector.id, seed.device.id]);
    const { page, context } = await as(browser, "approver");
    await page.goto(`${COUNTS}${ids.main}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: `Recuento #${ids.main}` })).toBeVisible({ timeout: 90_000 });
    const approve = page.getByRole("button", { name: "Aprobar y ajustar" });
    await expect(approve).toBeEnabled();
    await approve.click(); // confirm aceptado
    await expect.poll(() => recuento(ids.main!).status, { timeout: 30_000 }).toBe("approved");
    await expect(pendingSection(page).getByRole("heading", { name: "No contados (1): no se tocaron" })).toBeVisible({ timeout: 30_000 });
    await shot(page, "2-aprobado");

    const state = recuento(ids.main!);
    const line = (product: number) => state.lines.find((l) => l.product === product)!;
    // Cable: 2 − 10 = −8 sobre 10 → 2. Funda: 7 − 5 = +2 sobre 4 (tras la venta de después) → 6, la venta se conserva.
    expect(line(seed.cable.id)).toMatchObject({ physical: 2, start: 10, approval: 10, difference: -8 });
    expect(line(seed.funda.id)).toMatchObject({ physical: 7, start: 5, approval: 4, difference: 2 });
    expect(line(seed.cargador.id)).toMatchObject({ physical: null, start: null, approval: null, difference: null });
    expect(state.movements).toEqual([
      { product: seed.cable.id, type: "correction_negative", quantity: 8, before: 10, after: 2 },
      { product: seed.funda.id, type: "correction_positive", quantity: 2, before: 4, after: 6 },
    ]);
    const after = existencias([seed.cable.id, seed.funda.id, seed.cargador.id, seed.protector.id, seed.device.id]);
    const A = String(seed.branchA.id), B = String(seed.branchB.id);
    expect(after[seed.cable.id].stock).toEqual({ [A]: 2, [B]: 3 }); // B no se toca
    expect(after[seed.funda.id].stock).toEqual({ [A]: 6 });
    expect(after[seed.cargador.id]).toEqual(before[seed.cargador.id]); // en blanco: 4 − 1 (salida manual) = 3, intacto
    expect(after[seed.cargador.id].stock).toEqual({ [A]: 3 });
    expect(after[seed.protector.id]).toEqual(before[seed.protector.id]);
    expect(after[seed.device.id]).toEqual(before[seed.device.id]);

    // Una segunda aprobación por la API — con la versión que muestra la pantalla, como exige el servidor (#240) —
    // responde 200 con los MISMOS movimientos y no escribe nada nuevo.
    const shown = await call<{ version: string }>(context.request, "GET", `${COUNTS}${ids.main}/`);
    expect(shown.status).toBe(200);
    const again = await call<{ movements: { id: number; quantity: number }[] }>(
      context.request, "POST", `${COUNTS}${ids.main}/approve/`, { version: shown.body.version },
    );
    expect(again.status, JSON.stringify(again.body)).toBe(200);
    expect(again.body.movements.map((m) => m.quantity)).toEqual(state.movements.map((m) => m.quantity));
    expect(recuento(ids.main!)).toEqual(state);
    expect(existencias([seed.cable.id, seed.funda.id, seed.cargador.id])).toEqual(
      { [seed.cable.id]: after[seed.cable.id], [seed.funda.id]: after[seed.funda.id], [seed.cargador.id]: after[seed.cargador.id] },
    );
  });

  test("4 · viewer ve la nota de «Mover inventario» y no puede abrir uno", async ({ browser }) => {
    const { page, context } = await as(browser, "viewer");
    await openCounts(page);
    await expect(page.getByRole("note").filter({ hasText: "Mover inventario" })).toBeVisible();
    await expect(newCount(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Abrir recuento" })).toHaveCount(0);
    await shot(page, "4-viewer");

    const counts = recuentosDe([seed.branchA.id, seed.branchB.id]).length;
    const res = await call(context.request, "POST", COUNTS, { branch: seed.branchA.id, kind: "regular", include_stocked: true });
    expect(res.status, JSON.stringify(res.body)).toBe(403);
    expect(recuentosDe([seed.branchA.id, seed.branchB.id])).toHaveLength(counts);
  });

  test("5 · usuario de una sola sucursal: la sucursal como texto, y B no existe para él", async ({ browser }) => {
    const { page, context } = await as(browser, "single");
    await openCounts(page);
    const panel = newCount(page);
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("combobox")).toHaveCount(0);
    await expect(panel.getByText("Sucursal que se cuenta", { exact: true })).toBeVisible();
    await expect(panel.getByText(seed.branchA.name, { exact: true })).toBeVisible();
    await shot(page, "5-una-sucursal");

    const id = await openAndLand(page);
    ids.single = id;
    const created = recuento(id);
    expect(created).toMatchObject({ status: "draft", branch: seed.branchA.id, created_by: CX_USERS.single });
    expect(created.lines.map((l) => l.product).sort()).toEqual([seed.cable.id, seed.funda.id, seed.cargador.id].sort());
    await expect(pendingSection(page).getByRole("heading", { name: "Pendientes (3)" })).toBeVisible();

    const before = recuentosDe([seed.branchB.id]).length;
    const res = await call(context.request, "POST", COUNTS, { branch: seed.branchB.id, kind: "regular", include_stocked: true });
    expect(res.status, JSON.stringify(res.body)).toBe(404);
    expect(recuentosDe([seed.branchB.id])).toHaveLength(before);
  });

  test("7 · historial: «Borrador» muestra el borrador y «Continuar» lo abre", async ({ browser }) => {
    const { page } = await as(browser, "counter");
    await openCounts(page);
    await page.getByRole("button", { name: "Borrador", exact: true }).click();
    await expect(page.getByText("Cargando recuentos…")).toHaveCount(0, { timeout: 30_000 });
    const go = page.getByRole("link", { name: `Continuar el recuento #${ids.single}` });
    await expect(go).toBeVisible();
    // El aprobado no está entre los borradores.
    await expect(page.getByRole("link", { name: new RegExp(`el recuento #${ids.main}$`) })).toHaveCount(0);
    await shot(page, "7-filtro-borrador");
    // Sin filtro, el aprobado tiene «Ver».
    await page.getByRole("button", { name: "Todos", exact: true }).click();
    await expect(page.getByRole("link", { name: `Ver el recuento #${ids.main}` })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Borrador", exact: true }).click();
    await go.click();
    await page.waitForURL(new RegExp(`/admin/inventory/counts/${ids.single}$`));
    await expect(page.getByRole("heading", { name: `Recuento #${ids.single}` })).toBeVisible({ timeout: 60_000 });
  });

  test("6 · anular un borrador desde su pantalla", async ({ browser }) => {
    const { page } = await as(browser, "counter");
    await expect(page).toHaveURL(new RegExp(`/admin/inventory/counts/${ids.single}$`));
    expect(recuento(ids.single!).status).toBe("draft");
    await page.getByRole("button", { name: "Anular recuento" }).click(); // confirm aceptado
    await expect.poll(() => recuento(ids.single!).status, { timeout: 30_000 }).toBe("cancelled");
    await expect(page.getByRole("button", { name: "Anular recuento" })).toHaveCount(0, { timeout: 30_000 });
    expect(recuento(ids.single!).movements).toEqual([]);
    await shot(page, "6-anulado");
  });

  test("8 · 390 px: crear y la pantalla de conteo sin desborde horizontal", async ({ browser }) => {
    const { context: desktop } = await as(browser, "counter");
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
      storageState: await desktop.storageState(), baseURL: BASE,
    });
    try {
      const page = await context.newPage();
      page.on("pageerror", (error) => pageErrors.push(`390: ${error.message}`));
      await openCounts(page);
      const panel = newCount(page);
      await expect(panel).toBeVisible();
      await expectNoOverflow(page, "390 · recuentos");
      await shot(page, "8-390-recuentos");
      await panel.getByLabel("Sucursal que se cuenta").selectOption({ label: seed.branchB.name });
      const id = await openAndLand(page);
      ids.mobile = id;
      expect(recuento(id)).toMatchObject({ status: "draft", branch: seed.branchB.id });
      const pending = pendingSection(page);
      await expect(pending.getByRole("heading", { name: "Pendientes (2)" })).toBeVisible();
      await expect(pending.getByRole("button", { name: `Contar ${seed.protector.name}` })).toBeVisible();
      await expectNoOverflow(page, "390 · pantalla de conteo");
      await shot(page, "8-390-conteo");
      // Una lectura en «Cantidad total» abre la cantidad: también sin desborde.
      await scan(page, seed.protector.code);
      await expect(page.getByRole("form", { name: `Cantidad de ${seed.protector.name}` })).toBeVisible({ timeout: 30_000 });
      await expectNoOverflow(page, "390 · cantidad abierta");
      await shot(page, "8-390-cantidad");
    } finally {
      await context.close();
    }
  });

  test("sin errores de página", () => {
    expect(pageErrors).toEqual([]);
  });
});
