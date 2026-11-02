import { mkdirSync } from "node:fs";
import { test, expect, chromium, devices, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { RUN, sembrar, shell } from "./inventory-first-support";
import { sessionFor, limpiarRecepcion } from "./inventory-receive-support";
import { RL_USERS, RL_PASSWORD, MODELS, sembrarLista, articulos, nextImei } from "./inventory-receive-list-support";

/**
 * INV-RECEIVE-PRICE-03 — el precio de venta de un seminuevo, de la recepción a «Equipos seminuevos», SIN MOCKS
 * (navegador real + API real de Django sobre una base desechable; la guarda de `inventory-first-guard` se niega a
 * correr contra otra cosa). Mismo entorno que `inventory-commercial.spec.ts`:
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3042 E2E_API_BASE=http://127.0.0.1:8602/api \
 *     npx playwright test e2e/inventory-receive-price.spec.ts
 *
 * 1 · quien sólo recibe (`inventory.adjust`, sin `products.manage`): «Datos del equipo» e «Información comercial»
 *     separados, ningún campo de precio, el aviso de precio pendiente; el paso 4 dice lo que falta y a quién pedirlo;
 *     en «Equipos seminuevos» completa la batería pero el precio es de sólo lectura y se explica por qué.
 * 2 · quien gestiona productos (sin `costs.view`): recibe un equipo con precio y otro sin él; desde el paso 4 llega a
 *     los equipos sin precio, fija el que falta con confirmación, y nada se publica por ello; un equipo vendido
 *     conserva su precio.
 * En escritorio (1280) y a 390 px. Precios y series inventados.
 */

const SHOTS = process.env.PRICE_SHOTS ?? "test-results/price-shots";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3042";
const PENDING = "Pendiente de precio de venta: este equipo no podrá ofrecerse en línea ni venderse en caja hasta configurarlo.";

type Env = { name: string; touch: boolean; options: Parameters<Browser["newContext"]>[0] };
const iphone = (() => { const { defaultBrowserType: _ignored, ...rest } = devices["iPhone 13"]; void _ignored; return rest; })();
const ENVS: Env[] = [
  { name: "chromium-1280x800", touch: false, options: { viewport: { width: 1280, height: 800 } } },
  { name: "chromium-390x844", touch: true, options: iphone },
];

const states = new Map<string, Awaited<ReturnType<BrowserContext["storageState"]>>>();
async function stateFor(browser: Browser, user: string) {
  const found = states.get(user);
  if (found) return found;
  for (let attempt = 0; ; attempt++) {
    try {
      const { context } = await sessionFor(browser, user, RL_PASSWORD);
      const state = await context.storageState();
      await context.close();
      states.set(user, state);
      return state;
    } catch (error) {
      if (attempt >= 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000)); // 5 inicios de sesión por minuto
    }
  }
}

const press = async (target: Locator, touch: boolean) => {
  await target.scrollIntoViewIfNeeded({ timeout: 30_000 });
  if (touch) await target.tap({ timeout: 30_000 }); else await target.click({ timeout: 30_000 });
};
const button = (page: Page, name: string | RegExp) => page.getByRole("button", { name });

async function noOverflow(page: Page, where: string) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `desborde horizontal en ${where}`).toBeLessThanOrEqual(1);
}

async function openAs(engine: Browser, user: string, env: Env) {
  const state = await stateFor(engine, user);
  const context = await engine.newContext({ ...env.options, storageState: state, baseURL: BASE });
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept());
  return { context, page };
}

/** Pega `count` seminuevos de la variante que ya existe y deja la lista lista para agregar. */
async function paste(page: Page, env: Env, serials: string[]) {
  await page.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 90_000 });
  await press(button(page, "Siguiente: artículos"), env.touch);
  await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
  await press(button(page, /^Pegar una lista/), env.touch);
  const text = serials.map((serial) => [
    MODELS.existing, "128 GB", "Color: negro", "Batería 90%", `NS: ${serial}`, `IMEI 1: ${nextImei()}`,
  ].join("\n")).join("\n\n");
  await page.getByLabel("Línea declarada").selectOption({ label: "Seminuevos" });
  await page.getByLabel("Texto para reconocer").fill(text);
  await press(button(page, "Analizar y preparar"), env.touch);
  await expect(page.getByText("Buscando en el catálogo…").first()).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByRole("list", { name: "Avance de la lista" }).locator("li").first().locator("strong"))
    .toHaveText(String(serials.length), { timeout: 30_000 });
}

const row = (page: Page, n: number) =>
  page.locator("details").filter({ has: page.locator("summary").filter({ hasText: new RegExp(`^\\s*Equipo ${n}(?!\\d)`) }) }).first();

async function openRow(page: Page, n: number) {
  const target = row(page, n);
  if ((await target.getAttribute("open")) === null) await target.locator("summary").first().click();
  await expect(target.getByRole("button", { name: /^Quitar/ })).toBeVisible();
  return target;
}

async function confirmReception(page: Page, env: Env, count: number) {
  await press(button(page, new RegExp(`^Agregar ${count} equipos? listos?`)), env.touch);
  await press(button(page, /Siguiente: revisar/), env.touch);
  await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel(/Revisé los datos/).check();
  await press(button(page, "Confirmar recepción"), env.touch);
  await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 60_000 });
}

const unitsOf = (serials: string[]) =>
  articulos(MODELS.existing)[0].units.filter((u) => serials.includes(u.serial));

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  sembrar();
  sembrarLista();
  await stateFor(browser, RL_USERS.plain);
  await stateFor(browser, RL_USERS.manager);
});

test.afterAll(async () => {
  if (process.env.E2E_KEEP === "1") return;
  const result = limpiarRecepcion();
  expect(result.left, `INV-RECEIVE-PRICE-03: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

for (const env of ENVS) {
  test(`1 · ${env.name}: quien sólo recibe no ve campos de precio, y el precio queda pendiente y explicado`, async ({ browser }) => {
    test.setTimeout(420_000);
    const engine = env.name === ENVS[0].name ? browser : await chromium.launch();
    const { context, page } = await openAs(engine, RL_USERS.plain, env);
    const serial = `PRP${RUN.slice(-5)}${env.touch ? "M" : "D"}1`;
    try {
      await paste(page, env, [serial]);
      const first = await openRow(page, 1);
      const data = first.getByRole("group", { name: "Datos del equipo" });
      const commercial = first.getByRole("group", { name: "Información comercial" });
      await expect(data.getByLabel(/Número de serie/)).toBeVisible();
      await expect(data.getByLabel(/Batería medida/)).toBeVisible();
      await expect(commercial.getByLabel(/Precio de venta/)).toHaveCount(0);
      await expect(commercial.getByLabel(/Costo del equipo/)).toHaveCount(0);
      await expect(commercial.getByRole("note")).toHaveText(PENDING);
      await expect(commercial).toContainText("Tu usuario no puede fijar precios de venta");
      await noOverflow(page, `${env.name} grupos de la fila`);
      mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: `${SHOTS}/${env.name}-1-row.png`, fullPage: false });

      await confirmReception(page, env, 1);
      const follow = page.getByRole("article", { name: /^Seguimiento de / });
      await expect(follow).toContainText("Recibido: 1 equipo identificado");
      await expect(follow).toContainText("0 equipos con precio y 1 pendiente");
      await expect(follow).toContainText("La tienda en línea no lo ofrece todavía");
      await expect(follow).toContainText("pide a quien gestiona productos");
      await expect(follow.getByRole("link", { name: /Fijar el precio/ })).toHaveCount(0);
      await noOverflow(page, `${env.name} seguimiento`);
      await page.screenshot({ path: `${SHOTS}/${env.name}-1-done.png`, fullPage: false });
      const [received] = unitsOf([serial]);
      expect(received.price).toBeNull();

      // «Equipos seminuevos»: completa la batería; el precio es de sólo lectura y se dice por qué.
      await press(follow.getByRole("link", { name: "Completar datos y fotos" }), env.touch);
      await expect(page.getByRole("heading", { name: "Seminuevos", exact: true })).toBeVisible({ timeout: 60_000 });
      const line = page.getByRole("row").filter({ hasText: serial });
      await expect(line).toContainText("Sin precio (pendiente)");
      await expect(page.getByText(/puede ver los precios, no cambiarlos/)).toBeVisible();
      await press(button(page, `Editar ${serial}`), env.touch);
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByLabel(/Precio de venta \(S\/\)/)).toHaveCount(0);
      await expect(dialog).toContainText("Sólo quien gestiona productos puede fijar o cambiar el precio de venta.");
      await expect(dialog.getByRole("note")).toHaveText(PENDING);
      await dialog.getByLabel("Batería (%)").fill("87");
      await noOverflow(page, `${env.name} ficha del equipo`);
      await page.screenshot({ path: `${SHOTS}/${env.name}-1-ficha.png`, fullPage: false });
      await press(dialog.getByRole("button", { name: "Guardar" }), env.touch);
      await expect(page.getByRole("status").filter({ hasText: "Equipo guardado." })).toBeVisible({ timeout: 30_000 });
      const [after] = unitsOf([serial]);
      expect([after.battery, after.price]).toEqual([87, null]);
    } finally {
      await context.close().catch(() => undefined);
      if (engine !== browser) await engine.close().catch(() => undefined);
    }
  });

  test(`2 · ${env.name}: quien gestiona productos fija el precio que falta con confirmación, y nada se publica`, async ({ browser }) => {
    test.setTimeout(420_000);
    const engine = env.name === ENVS[0].name ? browser : await chromium.launch();
    const { context, page } = await openAs(engine, RL_USERS.manager, env);
    const serials = [`PRG${RUN.slice(-5)}${env.touch ? "M" : "D"}1`, `PRG${RUN.slice(-5)}${env.touch ? "M" : "D"}2`];
    try {
      const before = articulos(MODELS.existing)[0];
      await paste(page, env, serials);
      const first = await openRow(page, 1);
      const commercial = first.getByRole("group", { name: "Información comercial" });
      await expect(commercial.getByLabel(/Costo del equipo/)).toHaveCount(0); // sin costs.view
      await commercial.getByLabel(/Precio de venta del equipo/).fill("1899.00");
      await expect(commercial.getByRole("note")).toHaveCount(0);
      const second = await openRow(page, 2);
      await expect(second.getByRole("group", { name: "Información comercial" }).getByRole("note")).toHaveText(PENDING);
      await confirmReception(page, env, 2);

      const follow = page.getByRole("article", { name: /^Seguimiento de / });
      await expect(follow).toContainText("1 equipo con precio y 1 pendiente");
      await expect(follow).toContainText("Poner un precio no publica nada");
      await noOverflow(page, `${env.name} seguimiento`);
      await page.screenshot({ path: `${SHOTS}/${env.name}-2-done.png`, fullPage: false });
      expect(unitsOf(serials).map((u) => u.price)).toEqual(["1899.00", null]);

      await press(follow.getByRole("link", { name: "Fijar el precio de 1 equipo" }), env.touch);
      await expect(page.getByRole("heading", { name: "Seminuevos", exact: true })).toBeVisible({ timeout: 60_000 });
      expect(new URL(page.url()).searchParams.get("precio")).toBe("sin");
      await expect(page.getByLabel("Precio de venta")).toHaveValue("no");
      const line = page.getByRole("row").filter({ hasText: serials[1] });
      await expect(line).toContainText("Sin precio (pendiente)");
      await expect(page.getByRole("row").filter({ hasText: serials[0] })).toHaveCount(0); // ya tiene precio
      await press(button(page, `Editar ${serials[1]}`), env.touch);
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel(/Precio de venta \(S\/\)/).fill("0");
      await press(dialog.getByRole("button", { name: "Guardar" }), env.touch);
      await expect(dialog.getByRole("alert")).toContainText("mayor que cero");
      await dialog.getByLabel(/Precio de venta \(S\/\)/).fill("1750.50");
      await press(dialog.getByRole("button", { name: "Guardar" }), env.touch);
      const confirm = dialog.getByRole("alertdialog", { name: "Confirmar el precio de venta" });
      await expect(confirm).toContainText("Vas a fijar el precio de venta en S/ 1,750.50");
      await noOverflow(page, `${env.name} confirmación del precio`);
      await page.screenshot({ path: `${SHOTS}/${env.name}-2-confirm.png`, fullPage: false });
      await press(confirm.getByRole("button", { name: "Sí, guardar este precio" }), env.touch);
      await expect(page.getByRole("status").filter({ hasText: "Equipo guardado." })).toBeVisible({ timeout: 30_000 });
      expect(unitsOf(serials).map((u) => u.price)).toEqual(["1899.00", "1750.50"]);
      const after = articulos(MODELS.existing)[0];
      expect([after.published, after.active, after.id]).toEqual([before.published, before.active, before.id]);

      // Un equipo vendido conserva su precio: la ficha no ofrece cambiarlo, y el servidor tampoco lo aceptaría.
      shell(`
from store.models import StockUnit
StockUnit.objects.filter(company__slug=data['slug'], serial_number=data['serial']).update(status='sold')
out(True)
`, { serial: serials[0] });
      await page.goto("/admin/inventory/used", { waitUntil: "networkidle" });
      await press(button(page, `Editar ${serials[0]}`), env.touch);
      const sold = page.getByRole("dialog");
      await expect(sold.getByLabel(/Precio de venta \(S\/\)/)).toHaveCount(0);
      await expect(sold).toContainText("ya se vendió: su precio es el de esa venta y no se cambia");
    } finally {
      await context.close().catch(() => undefined);
      if (engine !== browser) await engine.close().catch(() => undefined);
    }
  });
}
