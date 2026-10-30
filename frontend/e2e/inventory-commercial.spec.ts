import { mkdirSync } from "node:fs";
import { test, expect, chromium, webkit, devices, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { sembrar } from "./inventory-first-support";
import { sessionFor, limpiarRecepcion } from "./inventory-receive-support";
import { RL_USERS, RL_PASSWORD, MODELS, sembrarLista, articulos, nextImei } from "./inventory-receive-list-support";

/**
 * INV-COM-01A/B — el expediente comercial de un seminuevo viaja con su recepción, de punta a punta y SIN MOCKS
 * (navegador real + API real de Django sobre una base desechable; la guarda de `inventory-first-guard` se niega a
 * correr contra otra cosa). Mismo entorno que `inventory-receive-list.spec.ts`:
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3031 E2E_API_BASE=http://127.0.0.1:8591/api \
 *     npx playwright test e2e/inventory-commercial.spec.ts
 *
 * Qué se comprueba: quien gestiona productos fija el precio propio de cada seminuevo y los detalles para el comprador;
 * quien solo recibe no ve el campo y el equipo queda sin precio, pendiente; el paso 3 dice dónde queda cada línea
 * (modelo → línea → variante → unidades) y qué falta; el paso 4 dice qué queda por completar; y la recepción no
 * publica, no activa ni cambia el precio de ningún producto. Los precios y las series son inventados.
 */

const SHOTS = process.env.COM_SHOTS ?? "test-results/com-shots";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3031";
const PRICES = ["1899.00", "1750.50"];

type Env = { name: string; touch: boolean; kind: "chromium" | "webkit"; options: Parameters<Browser["newContext"]>[0] };
const profile = (name: string): Parameters<Browser["newContext"]>[0] => {
  const { defaultBrowserType: _ignored, ...rest } = devices[name];
  void _ignored;
  return rest;
};
const ENVS: Env[] = [
  { name: "chromium-1280x800", touch: false, kind: "chromium", options: { viewport: { width: 1280, height: 800 } } },
  { name: "chromium-iphone13-390x844", touch: true, kind: "chromium", options: profile("iPhone 13") },
  { name: "webkit-iphone13", touch: true, kind: "webkit", options: profile("iPhone 13") },
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

/** Pega dos seminuevos de la variante que ya existe y deja la lista lista para agregar. */
async function pasteTwo(page: Page, env: Env, tag: string) {
  await page.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 90_000 });
  await press(button(page, "Siguiente: artículos"), env.touch);
  await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
  await press(button(page, /^Pegar una lista/), env.touch);
  const text = [0, 1].map((n) => [
    MODELS.existing, "128 GB", "Color: negro", "Batería 90%", `NS: COM${tag}${n}`, `IMEI 1: ${nextImei()}`,
  ].join("\n")).join("\n\n");
  await page.getByLabel("Línea declarada").selectOption({ label: "Seminuevos" });
  await page.getByLabel("Texto para reconocer").fill(text);
  await press(button(page, "Analizar y preparar"), env.touch);
  await expect(page.getByText("Buscando en el catálogo…").first()).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByRole("list", { name: "Avance de la lista" }).locator("li").first().locator("strong")).toHaveText("2", { timeout: 30_000 });
}

const row = (page: Page, n: number) =>
  page.locator("details").filter({ has: page.locator("summary").filter({ hasText: new RegExp(`^\\s*Equipo ${n}(?!\\d)`) }) }).first();

async function openRow(page: Page, n: number) {
  const target = row(page, n);
  if ((await target.getAttribute("open")) === null) await target.locator("summary").first().click();
  await expect(target.getByRole("button", { name: /^Quitar/ })).toBeVisible();
  return target;
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  sembrar();
  sembrarLista();
  await stateFor(browser, RL_USERS.manager);
});

test.afterAll(async () => {
  if (process.env.E2E_KEEP === "1") return;
  const result = limpiarRecepcion();
  expect(result.left, `INV-COM-01: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

async function openAs(engine: Browser, user: string, env: Env) {
  const state = await stateFor(engine, user);
  const context = await engine.newContext({ ...env.options, storageState: state, baseURL: BASE });
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept());
  return { context, page };
}

test.describe("recepción comercial · quien gestiona productos", () => {
  for (const env of ENVS) {
    test(`1 · ${env.name}: precio propio, detalles, destino comercial y lo que queda por completar`, async ({ browser }) => {
      test.setTimeout(420_000);
      let engine: Browser = browser;
      if (env.kind === "webkit") {
        try { engine = await webkit.launch(); }
        catch (error) { test.skip(true, `WebKit no está instalado o no abre: ${String(error).slice(0, 120)}`); return; }
      } else if (env.name !== "chromium-1280x800") {
        engine = await chromium.launch();
      }
      const { context, page } = await openAs(engine, RL_USERS.manager, env);
      const tag = `${env.kind[0].toUpperCase()}${env.touch ? "T" : "D"}`;
      try {
        const before = articulos(MODELS.existing)[0];
        await pasteTwo(page, env, tag);
        for (const [index, price] of PRICES.entries()) {
          const target = await openRow(page, index + 1);
          await target.getByLabel(/Precio de venta del equipo/).fill(price);
          await target.getByLabel(/Detalles para el comprador/).fill(`Incluye cargador ${index + 1}`);
          await target.getByLabel(/Observaciones internas/).fill("Rayón leve en el marco");
        }
        await noOverflow(page, `${env.name} fichas de la lista`);
        await press(button(page, /^Agregar 2 equipos listos/), env.touch);
        await press(button(page, /Siguiente: revisar/), env.touch);
        await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible({ timeout: 60_000 });

        // Paso 3: el destino comercial y el precio de cada equipo; ningún identificador completo.
        const destination = page.getByRole("region", { name: /^Destino comercial de / });
        await expect(destination).toBeVisible();
        await expect(destination).toContainText(`${MODELS.existing} → Seminuevos →`);
        await expect(destination).toContainText("2 unidades identificadas");
        await expect(destination).toContainText("página de su modelo");
        await expect(destination).toContainText("Pendiente de completar para publicar");
        await expect(destination).not.toContainText("sin precio propio"); // los dos llevan precio
        await page.getByText(/^Ver los 2 equipos/).click();
        await expect(page.getByText(`precio S/ ${PRICES[0]}`)).toBeVisible();
        await expect(page.getByText(`precio S/ ${PRICES[1]}`)).toBeVisible();
        await noOverflow(page, `${env.name} revisión`);
        mkdirSync(SHOTS, { recursive: true });
        await page.screenshot({ path: `${SHOTS}/${env.name}-review.png`, fullPage: false });

        await page.getByLabel(/Revisé los datos/).check();
        await press(button(page, "Confirmar recepción"), env.touch);
        await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 60_000 });
        const todo = page.getByRole("status", { name: "Para completar" });
        await expect(todo).toContainText("Las fotos de estos 2 equipos se añaden en su ficha");
        await expect(todo).not.toContainText("sin precio propio");
        await noOverflow(page, `${env.name} terminado`);
        await page.screenshot({ path: `${SHOTS}/${env.name}-done.png`, fullPage: false });

        // El servidor: cada equipo con su precio y sus detalles; el artículo, intacto.
        const after = articulos(MODELS.existing)[0];
        const received = after.units.filter((u) => u.serial.startsWith(`COM${tag}`));
        expect(received).toHaveLength(2);
        expect(received.map((u) => u.price).sort()).toEqual([...PRICES].sort());
        expect(received.map((u) => u.publicNotes).sort()).toEqual(["Incluye cargador 1", "Incluye cargador 2"]);
        expect(received.every((u) => u.notes === "Rayón leve en el marco")).toBe(true);
        expect(after.units.length - before.units.length).toBe(2);
        expect([after.published, after.active, after.line]).toEqual([before.published, before.active, before.line]);
        expect(after.id).toBe(before.id);
      } finally {
        await context.close().catch(() => undefined);
        if (engine !== browser) await engine.close().catch(() => undefined);
      }
    });
  }
});

test("2 · quien solo recibe no ve el campo de precio: el equipo se recibe sin precio y queda pendiente", async ({ browser }) => {
  test.setTimeout(420_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, RL_USERS.plain, env);
  try {
    await pasteTwo(page, env, "PL");
    const first = await openRow(page, 1);
    await expect(first.getByLabel(/Precio de venta del equipo/)).toHaveCount(0);
    await first.getByLabel(/Detalles para el comprador/).fill("Caja original");
    await press(button(page, /^Agregar 2 equipos listos/), false);
    await press(button(page, /Siguiente: revisar/), false);
    await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("region", { name: /^Destino comercial de / })).toContainText("el precio propio de 2 equipos");
    await page.getByLabel(/Revisé los datos/).check();
    await press(button(page, "Confirmar recepción"), false);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("status", { name: "Para completar" })).toContainText("2 equipos están sin precio propio");
    await expect(page.getByRole("status", { name: "Para completar" })).toContainText("alguien con permiso para gestionar productos");
    const received = articulos(MODELS.existing)[0].units.filter((u) => u.serial.startsWith("COMPL"));
    expect(received).toHaveLength(2);
    expect(received.every((u) => u.price === null)).toBe(true);
    expect(received.some((u) => u.publicNotes === "Caja original")).toBe(true);
  } finally {
    await context.close();
  }
});
