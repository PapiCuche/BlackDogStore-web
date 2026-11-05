import { mkdirSync } from "node:fs";
import { test, expect, webkit, devices, type Browser, type BrowserContext, type Locator, type Page, type Request } from "@playwright/test";
import { TAG } from "./inventory-first-support";
import { sessionFor, limpiarRecepcion, recibos, serial } from "./inventory-receive-support";
import { nextImei } from "./inventory-receive-list-support";
import { FLOW_USERS, FLOW_PASSWORD, FLOW_MODEL, FLOW_VARIANTS, sembrarFlujo, equipos, entradas, type FlowSeed } from "./inventory-receive-flow-04-support";

/**
 * INV-RECEIVE-FLOW-04 — UN solo camino para registrar equipos propios de una lista, SIN MOCKS (navegador real + API
 * real de Django sobre una base desechable; la guarda de `inventory-first-guard` se niega a correr contra otra cosa):
 *
 *   «Carga masiva» (lista de WhatsApp, origen propio) -> «Continuar en Recibir mercadería» -> sucursal -> la lista se
 *   analiza sola -> precio de venta de cada seminuevo en su fila CERRADA (y «mismo precio a los elegidos») -> revisar
 *   -> «Confirmar recepción» (el único que escribe) -> «Seminuevos» muestra cada precio.
 *
 * A · quien gestiona productos y ve costos (1280, 390 en Chromium y 390 en WebKit si está instalado): ocho seminuevos,
 *     ocho precios distintos sin abrir una fila, uno de «mismo precio» sobre dos, una confirmación; en la base cada serie
 *     tiene exactamente su precio y su costo aparte; «Seminuevos» los muestra.
 * B · quien sólo recibe (sin `products.manage`): ningún campo de precio, «Pendiente de precio»; se guarda sin precio.
 * C · doble clic en «Confirmar recepción»: una sola recepción; el paso siguiente lleva a los pendientes de precio.
 * En todos: el almacenamiento del navegador y la dirección nunca llevan una serie ni un IMEI.
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3051 E2E_API_BASE=http://127.0.0.1:8611/api \
 *     npx playwright test e2e/inventory-receive-flow-04.spec.ts
 */

const SHOTS = process.env.FLOW04_SHOTS ?? "test-results/flow04-shots";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";

type Env = { name: string; code: string; touch: boolean; kind: "chromium" | "webkit"; options: Parameters<Browser["newContext"]>[0] };
const profile = (name: string) => { const { defaultBrowserType: _ignored, ...rest } = devices[name]; void _ignored; return rest; };
const DESKTOP: Env = { name: "chromium-1280x800", code: "D", touch: false, kind: "chromium", options: { viewport: { width: 1280, height: 800 } } };
const ENVS: Env[] = [
  DESKTOP,
  { name: "chromium-390x844", code: "M", touch: true, kind: "chromium", options: profile("iPhone 13") },
  { name: "webkit-390x844", code: "W", touch: true, kind: "webkit", options: profile("iPhone 13") },
];

let seed: FlowSeed;
const states = new Map<string, Awaited<ReturnType<BrowserContext["storageState"]>>>();

/** Una sesión por usuario para toda la corrida: el inicio de sesión admite 5 por minuto. */
async function stateFor(browser: Browser, user: string) {
  const found = states.get(user);
  if (found) return found;
  for (let attempt = 0; ; attempt++) {
    try {
      const { context } = await sessionFor(browser, user, FLOW_PASSWORD);
      const state = await context.storageState();
      await context.close();
      states.set(user, state);
      return state;
    } catch (error) {
      if (attempt >= 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000));
    }
  }
}

async function openAs(engine: Browser, user: string, env: Env) {
  const state = states.get(user);
  if (!state) throw new Error(`INV-RECEIVE-FLOW-04: no hay sesión para ${user}`);
  const context = await engine.newContext({ ...env.options, storageState: state, baseURL: BASE });
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept());
  return { context, page };
}

/** El motor del entorno: el de la prueba, o WebKit propio (si no abre, se omite a propósito y lo dice). */
async function engineFor(browser: Browser, env: Env): Promise<Browser> {
  if (env.kind === "chromium") return browser;
  try { return await webkit.launch(); }
  catch (error) { test.skip(true, `WebKit no abre: ${String(error).slice(0, 120)}`); throw error; }
}

const press = async (target: Locator, touch: boolean) => {
  await target.scrollIntoViewIfNeeded({ timeout: 30_000 });
  if (touch) await target.tap({ timeout: 30_000 }); else await target.click({ timeout: 30_000 });
};
const button = (page: Page, name: string | RegExp) => page.getByRole("button", { name });
const pageText = (page: Page, matcher: string | RegExp) => page.getByText(matcher).and(page.locator(":not(title)"));

async function noOverflow(page: Page, where: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal en ${where}`).toBeLessThanOrEqual(1);
}

async function shot(page: Page, env: Env, name: string) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${env.name}-${name}.png`, fullPage: true });
}

/** El almacenamiento del navegador y la dirección nunca llevan una serie ni un IMEI (viajan en la memoria de la página). */
async function expectNoTraces(page: Page, secrets: string[], where: string) {
  const dump = await page.evaluate(async () => {
    const all = (store: Storage) => Object.keys(store).map((key) => `${key}=${store.getItem(key)}`).join("\n");
    let idb = "";
    try { idb = ((await indexedDB.databases?.()) ?? []).map((db) => db.name).join(","); } catch { /* sin acceso */ }
    return [location.href, all(localStorage), all(sessionStorage), idb, JSON.stringify(history.state ?? {})].join("\n");
  });
  for (const secret of secrets) expect(dump.includes(secret), `${where}: el navegador guardó una serie o un IMEI`).toBe(false);
}

type Device = { n: number; serial: string; imei: string; variant: keyof typeof FLOW_VARIANTS; battery: number; cost?: string };
const block = (d: Device) => {
  const v = FLOW_VARIANTS[d.variant];
  return [FLOW_MODEL, `${v.gb} GB`, `Color: ${v.colour}`, `Batería ${d.battery}%`, ...(d.cost ? [`Costo: ${d.cost}`] : []),
    `NS: ${d.serial}`, `IMEI 1: ${d.imei}`].join("\n");
};

/** «Carga masiva» con origen propio -> «Continuar en Recibir mercadería» -> sucursal -> paso 2 con la lista analizada. */
async function handOff(page: Page, env: Env, list: Device[], secrets: string[]) {
  await page.goto("/admin/inventory/bulk", { waitUntil: "networkidle" });
  await expect(page.getByLabel("Texto para reconocer"), "el centro de carga no cargó").toBeVisible({ timeout: 90_000 });
  await page.getByLabel("Línea declarada").selectOption("used");
  await page.getByLabel("Origen declarado").selectOption("own");
  await page.getByLabel("Texto para reconocer").fill(list.map(block).join("\n\n"));
  // Con origen propio «Carga masiva» no analiza ni registra: sólo entrega la lista.
  await expect(button(page, "Analizar localmente")).toHaveCount(0);
  await expect(button(page, /Generar borrador|Revisar y registrar/)).toHaveCount(0);
  await noOverflow(page, `${env.name} carga masiva`);
  await expectNoTraces(page, secrets, `${env.name} carga masiva`);
  return async (shoot: boolean) => {
    if (shoot) await shot(page, env, "1-carga-masiva");
    const go = page.getByRole("link", { name: "Continuar en Recibir mercadería" });
    await expect(go).toHaveAttribute("href", "/admin/inventory/receive");
    await press(go, env.touch);
    await expect(page).toHaveURL(/\/admin\/inventory\/receive$/, { timeout: 60_000 });
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 90_000 });
    await expect(pageText(page, /Trajimos tu lista pegada en «Carga masiva»/).first(), "la lista no llegó al asistente").toBeVisible();
    await page.getByLabel(/Sucursal que recibe/).selectOption({ label: seed.branchName });
    await page.getByLabel(/Referencia del documento/).fill(`[E2E] ${TAG} flujo 04 ${env.code}`);
    await noOverflow(page, `${env.name} paso 1`);
    if (shoot) await shot(page, env, "2-paso-1");
    await press(button(page, "Siguiente: artículos"), env.touch);
    await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible({ timeout: 30_000 });
    await expect(pageText(page, new RegExp(`Reconocimos ${list.length} equipos y 0 accesorios`)).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Buscando en el catálogo…").first()).toHaveCount(0, { timeout: 60_000 });
    await expect(page.getByRole("list", { name: "Avance de la lista" }).locator("li").first().locator("strong"))
      .toHaveText(String(list.length), { timeout: 30_000 });
    await expectNoTraces(page, secrets, `${env.name} lista analizada`);
  };
}

/** La fila «Equipo n» (el n-ésimo pegado): su tarjeta <li> y su <details>. */
const rowItem = (page: Page, n: number) =>
  page.getByRole("listitem").filter({ has: page.locator("details > summary").filter({ hasText: new RegExp(`^\\s*Equipo ${n}(?!\\d)`) }) }).last();
const rowDetails = (page: Page, n: number) => rowItem(page, n).locator("details").first();

function watchConfirm(page: Page) {
  const seen: { key: string; body: { lines: { units?: Record<string, unknown>[] }[] } }[] = [];
  const onRequest = (req: Request) => {
    if (req.method() === "POST" && /receipts\/confirm/.test(req.url()) && req.redirectedFrom() === null) {
      seen.push({ key: req.headers()["idempotency-key"] ?? "", body: req.postDataJSON() });
    }
  };
  page.on("request", onRequest);
  return { seen, stop: () => page.off("request", onRequest) };
}

async function toReview(page: Page, env: Env, count: number) {
  await press(button(page, `Agregar ${count} equipos listos a la recepción`), env.touch);
  await press(button(page, "Siguiente: revisar"), env.touch);
  await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible({ timeout: 60_000 });
  const box = page.getByLabel(/Revisé los datos y quiero registrar/);
  await box.scrollIntoViewIfNeeded();
  await box.check();
}

const soles = (value: string) => `S/ ${Number(value).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** «Seminuevos» filtrado por una serie (o su principio). */
async function usedRows(page: Page, env: Env, search: string) {
  await expect(page.getByRole("heading", { name: "Seminuevos", exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Serie o IMEI").fill(search);
  await press(button(page, "Filtrar"), env.touch);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  seed = sembrarFlujo();
  await stateFor(browser, FLOW_USERS.manager);
  await stateFor(browser, FLOW_USERS.plain);
});

test.afterAll(async () => {
  if (process.env.E2E_KEEP === "1") { console.warn(`INV-RECEIVE-FLOW-04: datos conservados con la marca ${TAG}`); return; }
  const result = limpiarRecepcion();
  expect(result.left, `INV-RECEIVE-FLOW-04: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

for (const env of ENVS) {
  test(`A · ${env.name}: ocho seminuevos de Carga masiva a Seminuevos, ocho precios sin abrir una fila, una confirmación`, async ({ browser }) => {
    test.setTimeout(480_000);
    const engine = await engineFor(browser, env);
    const { context, page } = await openAs(engine, FLOW_USERS.manager, env);
    // Alternan las dos variantes: el número de la fila es el orden pegado, no el de su tarjeta.
    const list: Device[] = Array.from({ length: 8 }, (_, i) => ({
      n: i + 1, serial: serial(`FA${env.code}`, i + 1), imei: nextImei(), variant: i % 2 === 0 ? "black" : "blue",
      battery: 82 + i, cost: String(1400 + (i + 1) * 10),
    }));
    // Ocho precios distintos; los dos últimos se reemplazan luego, a propósito, con «mismo precio a los elegidos».
    const typed = list.map((d) => `${1700 + d.n * 13}.${d.n}0`);
    const final = typed.map((p, i) => (i >= 6 ? "1200.00" : p));
    const secrets = list.flatMap((d) => [d.serial, d.imei]);
    try {
      const unitsBefore = equipos(list.map((d) => d.serial));
      expect(unitsBefore).toHaveLength(0);
      const receiptsBefore = recibos();
      const continueTo = await handOff(page, env, list, secrets);
      await continueTo(true);
      await expect.poll(async () => (await page.getByRole("list", { name: "Avance de la lista" }).locator("li").allInnerTexts()).map((t) => Number(t.split("\n")[0])),
        { timeout: 30_000 }).toEqual([8, 0, 0, 0]);
      const summary = page.getByLabel("Resumen de precios");
      await expect(summary).toContainText("0 de 8 seminuevos con precio de venta · 8 pendientes");

      // En cada fila cerrada: su costo (ve costos), el campo de precio y el estado del precio.
      for (const d of list) {
        const item = rowItem(page, d.n);
        await expect(rowDetails(page, d.n)).not.toHaveAttribute("open", /.*/);
        await expect(item).toContainText(`Costo S/ ${d.cost}`);
        await expect(item).toContainText("Pendiente de precio");
        const input = item.getByLabel(`Precio de venta del equipo ${d.n} (S/)`);
        await input.scrollIntoViewIfNeeded();
        await input.fill(typed[d.n - 1]);
        await expect(item).toContainText(`Precio S/ ${typed[d.n - 1]}`);
      }
      await expect(summary).toContainText("8 de 8 seminuevos con precio de venta · 0 pendientes");
      for (const d of list) await expect(rowDetails(page, d.n), `la fila ${d.n} se abrió`).not.toHaveAttribute("open", /.*/);

      // «Mismo precio a los elegidos», una vez, sobre dos filas que ya tenían el suyo: sólo esas cambian.
      const many = page.getByRole("group", { name: "Precio para varios equipos" });
      await expect(button(page, /^Aplicar/)).toBeDisabled();
      for (const n of [7, 8]) await rowItem(page, n).getByLabel(`Elegir el equipo ${n} para un mismo precio`).check();
      await many.getByLabel("Precio para los elegidos (S/)").fill("1200.00");
      await press(many.getByRole("button", { name: "Aplicar S/ 1200.00 a los 2 elegidos" }), env.touch);
      await expect(pageText(page, /Precio de venta S\/ 1200\.00 puesto en 2 equipos/).first()).toBeVisible();
      for (const d of list) {
        await expect(rowItem(page, d.n).getByLabel(`Precio de venta del equipo ${d.n} (S/)`)).toHaveValue(final[d.n - 1]);
        await expect(rowItem(page, d.n)).toContainText(`Precio S/ ${final[d.n - 1]}`);
      }
      await noOverflow(page, `${env.name} lista con precios`);
      await expectNoTraces(page, secrets, `${env.name} lista con precios`);
      await shot(page, env, "3-lista-con-precios");

      const watch = watchConfirm(page);
      await toReview(page, env, 8);
      await expect(page.getByRole("status").filter({ hasText: "Se prepararán" })).toContainText("8 equipos identificados");
      const review = await page.locator("main").innerText();
      for (const secret of secrets) expect(review.includes(secret), "la revisión mostró una serie o un IMEI completo").toBe(false);
      await noOverflow(page, `${env.name} revisión`);
      await shot(page, env, "4-revisar");
      const answered = page.waitForResponse((res) => /receipts\/confirm/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);
      await press(button(page, "Confirmar recepción"), env.touch);
      expect((await answered).status()).toBe(201);
      await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 60_000 });
      watch.stop();
      expect(watch.seen, "una sola confirmación").toHaveLength(1);
      // Precio y costo viajan en su campo, cada uno el suyo.
      const sent = watch.seen[0].body.lines.flatMap((line) => line.units ?? []);
      expect(sent).toHaveLength(8);
      for (const d of list) {
        const unit = sent.find((u) => u.serial === d.serial || u.serial_number === d.serial);
        expect(unit, `el equipo ${d.n} no viajó`).toBeDefined();
        expect(unit!.sale_price).toBe(final[d.n - 1]);
        expect(Number(unit!.unit_cost ?? unit!.cost), `costo enviado del equipo ${d.n}`).toBe(Number(d.cost));
      }
      const next = page.getByRole("navigation", { name: "Siguientes pasos" });
      await expect(next.getByRole("link", { name: "Ver equipos registrados" })).toBeVisible();
      await expect(next.getByRole("link", { name: /Completar \d+ precios? pendientes?/ })).toHaveCount(0);
      await expect(next.getByRole("link", { name: "Consultar stock" })).toBeVisible();
      await expect(next.getByRole("link", { name: "Ir a recuentos" })).toBeVisible();
      await noOverflow(page, `${env.name} recepción registrada`);
      await expectNoTraces(page, secrets, `${env.name} recepción registrada`);
      await shot(page, env, "5-registrada");

      // La base: cada serie tiene exactamente su precio y su costo, en campos distintos, en la sucursal elegida.
      const stored = equipos(list.map((d) => d.serial));
      expect(stored).toHaveLength(8);
      for (const d of list) {
        const unit = stored.find((u) => u.serial === d.serial)!;
        expect(unit, `falta el equipo ${d.n}`).toBeDefined();
        expect(unit.imei).toBe(d.imei);
        expect(unit.product).toBe(d.variant === "black" ? seed.black : seed.blue);
        expect(unit.price, `precio del equipo ${d.n}`).toBe(final[d.n - 1]);
        expect(Number(unit.cost), `costo del equipo ${d.n}`).toBe(Number(d.cost));
        expect(unit.price).not.toBe(unit.cost);
        expect([unit.status, unit.branch]).toEqual(["available", seed.branchId]);
      }
      expect(recibos()).toBe(receiptsBefore + 1);

      // «Seminuevos» muestra el precio de cada uno; ninguno aparece entre los que no tienen precio.
      await press(next.getByRole("link", { name: "Ver equipos registrados" }), env.touch);
      await expect(page).toHaveURL(/\/admin\/inventory\/used/, { timeout: 60_000 });
      await usedRows(page, env, serial(`FA${env.code}`, 1).slice(0, -2));
      for (const d of list) {
        const line = page.getByRole("row").filter({ hasText: d.serial });
        await expect(line, `«Seminuevos» no muestra el equipo ${d.n}`).toHaveCount(1, { timeout: 30_000 });
        await expect(line).toContainText(soles(final[d.n - 1]));
      }
      await noOverflow(page, `${env.name} seminuevos`);
      await shot(page, env, "6-seminuevos");
      await page.getByLabel("Precio de venta").selectOption("no");
      await expect(page.getByRole("row").filter({ hasText: serial(`FA${env.code}`, 1).slice(0, -2) })).toHaveCount(0, { timeout: 30_000 });
      await expectNoTraces(page, secrets, `${env.name} seminuevos`);
    } finally {
      await context.close().catch(() => undefined);
      if (engine !== browser) await engine.close().catch(() => undefined);
    }
  });
}

test("B · quien sólo recibe no ve campos de precio: los equipos entran pendientes y sin precio", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = DESKTOP;
  const { context, page } = await openAs(browser, FLOW_USERS.plain, env);
  const list: Device[] = Array.from({ length: 3 }, (_, i) => ({
    n: i + 1, serial: serial("FBP", i + 1), imei: nextImei(), variant: i === 1 ? "blue" : "black", battery: 88 + i,
  }));
  const secrets = list.flatMap((d) => [d.serial, d.imei]);
  try {
    const continueTo = await handOff(page, env, list, secrets);
    await continueTo(false);
    const summary = page.getByLabel("Resumen de precios");
    await expect(summary).toContainText("0 de 3 seminuevos con precio de venta · 3 pendientes");
    await expect(summary).toContainText("Tu usuario no fija precios de venta");
    await expect(page.getByRole("group", { name: "Precio para varios equipos" })).toHaveCount(0);
    await expect(button(page, "Siguiente sin precio")).toHaveCount(0);
    for (const d of list) {
      const item = rowItem(page, d.n);
      await expect(item.getByLabel(`Precio de venta del equipo ${d.n} (S/)`)).toHaveCount(0);
      await expect(item.getByLabel(`Elegir el equipo ${d.n} para un mismo precio`)).toHaveCount(0);
      await expect(item).toContainText("Pendiente de precio");
      await expect(item).not.toContainText("Costo S/");
    }
    await expect(page.getByLabel(/Precio de venta del equipo/)).toHaveCount(0);
    await shot(page, env, "B-lista-sin-permiso");

    const watch = watchConfirm(page);
    await toReview(page, env, 3);
    await press(button(page, "Confirmar recepción"), env.touch);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 60_000 });
    watch.stop();
    expect(watch.seen).toHaveLength(1);
    for (const unit of watch.seen[0].body.lines.flatMap((line) => line.units ?? [])) expect(unit.sale_price ?? null).toBeNull();
    // Sin `products.manage` el paso siguiente no ofrece completar precios (no podría).
    const next = page.getByRole("navigation", { name: "Siguientes pasos" });
    await expect(next.getByRole("link", { name: "Ver equipos registrados" })).toBeVisible();
    await expect(next.getByRole("link", { name: /Completar \d+ precios? pendientes?/ })).toHaveCount(0);
    await expectNoTraces(page, secrets, "B recepción registrada");
    const stored = equipos(list.map((d) => d.serial));
    expect(stored).toHaveLength(3);
    expect(stored.map((u) => u.price)).toEqual([null, null, null]);
    expect(stored.map((u) => u.cost)).toEqual([null, null, null]);
  } finally {
    await context.close().catch(() => undefined);
  }
});

test("C · doble clic en «Confirmar recepción» registra una vez; el pendiente de precio se completa desde el paso siguiente", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = DESKTOP;
  const { context, page } = await openAs(browser, FLOW_USERS.manager, env);
  const list: Device[] = [1, 2].map((n) => ({ n, serial: serial("FCD", n), imei: nextImei(), variant: "black", battery: 90, cost: "1450" }));
  const secrets = list.flatMap((d) => [d.serial, d.imei]);
  try {
    const before = entradas(seed.black);
    const receiptsBefore = recibos();
    const continueTo = await handOff(page, env, list, secrets);
    await continueTo(false);
    // Sólo el primero lleva precio; el segundo entra pendiente.
    await rowItem(page, 1).getByLabel("Precio de venta del equipo 1 (S/)").fill("1899.00");
    await expect(page.getByLabel("Resumen de precios")).toContainText("1 de 2 seminuevos con precio de venta · 1 pendiente");
    const watch = watchConfirm(page);
    await toReview(page, env, 2);
    await button(page, "Confirmar recepción").dblclick();
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 60_000 });
    watch.stop();
    expect(new Set(watch.seen.map((s) => s.key)).size, "todos los envíos comparten la clave").toBe(1);
    const after = entradas(seed.black);
    expect(after.units - before.units, "dos equipos, no cuatro").toBe(2);
    expect(after.entries - before.entries, "una entrada de compra").toBe(1);
    expect(after.batches - before.batches, "un lote de etiquetas").toBe(1);
    expect(recibos()).toBe(receiptsBefore + 1);
    expect(equipos(list.map((d) => d.serial)).map((u) => u.price)).toEqual(["1899.00", null]);

    // «Completar 1 precio pendiente» lleva a «Seminuevos» filtrado por los que no tienen precio.
    const pending = page.getByRole("navigation", { name: "Siguientes pasos" }).getByRole("link", { name: "Completar 1 precio pendiente" });
    await expect(pending).toBeVisible();
    await pending.click();
    await expect(page.getByRole("heading", { name: "Seminuevos", exact: true })).toBeVisible({ timeout: 60_000 });
    expect(new URL(page.url()).searchParams.get("precio")).toBe("sin");
    await expect(page.getByLabel("Precio de venta")).toHaveValue("no");
    await expect(page.getByRole("row").filter({ hasText: list[1].serial })).toContainText("Sin precio (pendiente)", { timeout: 30_000 });
    await expect(page.getByRole("row").filter({ hasText: list[0].serial })).toHaveCount(0);
    await expectNoTraces(page, secrets, "C seminuevos");
  } finally {
    await context.close().catch(() => undefined);
  }
});
