import { mkdirSync, writeFileSync } from "node:fs";
import { test, expect, chromium, webkit, type Browser, type Page } from "@playwright/test";
import { sembrar } from "./inventory-first-support";
import { limpiarRecepcion } from "./inventory-receive-support";
import {
  ENVS, NAV_PASSWORD, NAV_USERS, SHOTS, expectNoOverflow, forgetStates, observations, openAs, sembrarNavegacion, stateFor, type Env,
} from "./nav-ux-support";

/**
 * NAV-UX-02 — las pantallas relacionadas se agrupan por tarea dentro de la página, de punta a punta y SIN MOCKS
 * (navegador real + Django real sobre una base desechable; la guarda de `inventory-first-guard` se niega a correr
 * contra otra cosa). Mismo entorno que `nav-ux.spec.ts`:
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3032 E2E_API_BASE=http://127.0.0.1:8591/api \
 *     npx playwright test e2e/nav-ux-02.spec.ts
 *
 * Cada archivo en su propia invocación: cada corrida siembra su propia empresa auxiliar y no es repetible en el mismo proceso.
 *
 * Qué se comprueba: la barra de pestañas de cada destino (Stock y equipos, Proveedores), la página «Más herramientas»
 * por tarea, «Recibir mercadería» a un clic desde Productos hasta el paso 2 con las cargas de Excel a la vista, y
 * que en 1280, 768, 390 con toque y WebKit no hay desborde horizontal y los objetivos miden al menos 44 px.
 */

const tabsNav = (page: Page, name: string) => page.getByRole("navigation", { name: `Secciones de ${name}` });

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  sembrar();
  sembrarNavegacion();
  await stateFor(browser, NAV_USERS.admin, NAV_PASSWORD);
});

test.afterAll(async () => {
  mkdirSync(SHOTS, { recursive: true });
  writeFileSync(`${SHOTS}/nav-02-observations.json`, JSON.stringify(observations, null, 2));
  if (process.env.E2E_KEEP === "1") return;
  forgetStates();
  const result = limpiarRecepcion();
  expect(result.left, `NAV-UX-02: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

async function tour(page: Page, env: Env) {
  // 1 · Stock y equipos: la barra con sus cuatro pantallas; la actual marcada; se mueve sin pasar por el menú.
  await page.goto("/admin/inventory/used", { waitUntil: "domcontentloaded" });
  let tabs = tabsNav(page, "Stock y equipos");
  await expect(tabs).toBeVisible({ timeout: 60_000 });
  await expect(tabs.getByRole("link")).toHaveText(["Resumen", "Existencias", "Equipos nuevos", "Equipos seminuevos"]);
  await expect(tabs.getByRole("link", { name: "Equipos seminuevos" })).toHaveAttribute("aria-current", "page");
  for (const link of await tabs.getByRole("link").all()) {
    expect((await link.boundingBox())?.height ?? 0, "alto de una pestaña").toBeGreaterThanOrEqual(44);
  }
  await expectNoOverflow(page, `${env.name} pestañas de Stock y equipos`);
  await tabs.getByRole("link", { name: "Existencias" }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/stock$/);
  tabs = tabsNav(page, "Stock y equipos");
  await expect(tabs.getByRole("link", { name: "Existencias" })).toHaveAttribute("aria-current", "page");
  await page.goBack();
  await expect(page).toHaveURL(/\/admin\/inventory\/used$/);
  await expect(tabsNav(page, "Stock y equipos").getByRole("link", { name: "Equipos seminuevos" })).toHaveAttribute("aria-current", "page");
  await page.screenshot({ path: `${SHOTS}/${env.name}-tabs.png`, fullPage: false });

  // 2 · Proveedores: el directorio y las ofertas son dos pestañas con su propio nombre.
  await page.goto("/admin/products/suppliers", { waitUntil: "domcontentloaded" });
  tabs = tabsNav(page, "Proveedores");
  await expect(tabs.getByRole("link")).toHaveText(["Directorio", "Ofertas y listas"], { timeout: 60_000 });
  await tabs.getByRole("link", { name: "Ofertas y listas" }).click();
  await expect(page).toHaveURL(/\/admin\/products\/suppliers\/offers$/);
  await expect(tabsNav(page, "Proveedores").getByRole("link", { name: "Ofertas y listas" })).toHaveAttribute("aria-current", "page");

  // 3 · Más herramientas: una página por tarea; sin pestañas; cada tarjeta lleva a la pantalla de siempre.
  await page.goto("/admin/inventory/tools", { waitUntil: "domcontentloaded" });
  const main = page.locator("main");
  await expect(main.getByRole("heading", { name: "Cargar o sacar datos con Excel" })).toBeVisible({ timeout: 60_000 });
  await expect(main.getByRole("heading", { name: "Revisar cómo va el negocio" })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Reponer" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: /^Secciones de/ })).toHaveCount(0);
  await expectNoOverflow(page, `${env.name} más herramientas`);
  await main.getByRole("link", { name: /Carga de stock/ }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/import$/);

  // 4 · Productos → «Recibir mercadería» a un clic → paso 2 con las cargas de Excel a la vista.
  await page.goto("/admin/products", { waitUntil: "domcontentloaded" });
  await expect(tabsNav(page, "Productos y variantes")).toBeVisible({ timeout: 60_000 });
  await page.locator("main").getByRole("link", { name: "Recibir mercadería", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/receive$/);
  await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: /Siguiente: artículos/ }).click();
  await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible({ timeout: 60_000 });
  const group = page.getByRole("group", { name: "Cómo agregar mercadería" });
  await expect(group.getByRole("button", { name: /^Pegar una lista/ })).toBeVisible();
  await expect(group.getByRole("link", { name: "Importar equipos desde Excel" })).toHaveAttribute("href", "/admin/inventory/units/import");
  await expect(group.getByRole("link", { name: "Importar cantidades desde Excel" })).toHaveAttribute("href", "/admin/inventory/import");
  await expectNoOverflow(page, `${env.name} recibir mercadería, paso 2`);
  await page.screenshot({ path: `${SHOTS}/${env.name}-receive-step2.png`, fullPage: false });
}

test.describe("pestañas, herramientas y recepción · administrador", () => {
  for (const env of ENVS) {
    test(`1 · ${env.name}: pestañas por destino, Más herramientas y recibir desde Productos`, async ({ browser }) => {
      test.setTimeout(420_000);
      let engine: Browser = browser;
      if (env.kind === "webkit") {
        try { engine = await webkit.launch(); }
        catch (error) { test.skip(true, `WebKit no está instalado o no abre: ${String(error).slice(0, 120)}`); return; }
      } else if (env.name !== "chromium-1280x800") {
        engine = await chromium.launch();
      }
      const { context, page } = await openAs(engine, "admin", env);
      try {
        await tour(page, env);
      } finally {
        await context.close().catch(() => undefined);
        if (engine !== browser) await engine.close().catch(() => undefined);
      }
    });
  }
});

test("2 · quien sólo ve el inventario: sus pestañas y su página de herramientas se recortan; recibir no se ofrece", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "viewer", env);
  try {
    await page.goto("/admin/inventory/used", { waitUntil: "domcontentloaded" });
    await expect(tabsNav(page, "Stock y equipos").getByRole("link")).toHaveText(["Resumen", "Existencias", "Equipos nuevos", "Equipos seminuevos"], { timeout: 60_000 });
    await page.goto("/admin/inventory/tools", { waitUntil: "domcontentloaded" });
    const main = page.locator("main");
    await expect(main.getByRole("link", { name: /Gestión masiva de inventario/ })).toBeVisible({ timeout: 60_000 });
    for (const name of [/Carga de equipos/, /Carga de stock/, /Carga de productos/, /Costos y precios/, /Reportes/, /Reposición/]) {
      await expect(main.getByRole("link", { name })).toHaveCount(0);
    }
    await page.goto("/admin/products", { waitUntil: "domcontentloaded" });
    await expect(page.locator("main").getByRole("link", { name: "Recibir mercadería", exact: true })).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("3 · clics hasta cada tarea con las pestañas, medidos", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "admin", env);
  try {
    // Desde una pantalla de Stock y equipos, otra del mismo destino: un clic.
    await page.goto("/admin/inventory/stock", { waitUntil: "domcontentloaded" });
    await tabsNav(page, "Stock y equipos").getByRole("link", { name: "Equipos seminuevos" }).click();
    await expect(page).toHaveURL(/\/admin\/inventory\/used$/);
    observations.push({ env: env.name, metric: "clics: de Existencias a Equipos seminuevos", value: 1 });
    // Desde Productos, iniciar una recepción: un clic.
    await page.goto("/admin/products", { waitUntil: "domcontentloaded" });
    await page.locator("main").getByRole("link", { name: "Recibir mercadería", exact: true }).click();
    await expect(page).toHaveURL(/\/admin\/inventory\/receive$/);
    observations.push({ env: env.name, metric: "clics: de Productos a Recibir mercadería", value: 1 });
    // Una carga avanzada por la página de herramientas: menú → Más herramientas → tarjeta = 2 clics.
    await page.goto("/admin", { waitUntil: "domcontentloaded" });
    await page.getByRole("navigation", { name: "Módulos del control interno" }).getByRole("link", { name: "Más herramientas" }).click();
    await page.locator("main").getByRole("link", { name: /Carga de equipos/ }).click();
    await expect(page).toHaveURL(/\/admin\/inventory\/units\/import$/);
    observations.push({ env: env.name, metric: "clics: abrir la carga de equipos por Más herramientas", value: 2 });
  } finally {
    await context.close();
  }
});
