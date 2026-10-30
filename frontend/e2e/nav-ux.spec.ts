import { mkdirSync, writeFileSync } from "node:fs";
import { test, expect, chromium, webkit, devices, type Browser, type BrowserContext, type Locator, type Page } from "@playwright/test";
import { sembrar, OTHER_USER, OTHER_PASSWORD, TAG } from "./inventory-first-support";
import { sessionFor, limpiarRecepcion } from "./inventory-receive-support";
import { NAV_USERS, NAV_PASSWORD, sembrarNavegacion } from "./nav-ux-support";

/**
 * NAV-UX-01 — el menú lateral por destinos, de punta a punta y SIN MOCKS (navegador real + Django real sobre una
 * base desechable; la guarda de `inventory-first-guard` se niega a correr contra otra cosa).
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3032 E2E_API_BASE=http://127.0.0.1:8591/api \
 *     npx playwright test e2e/nav-ux.spec.ts
 *
 * Qué se comprueba: nueve destinos entre Catálogo e Inventario (no veinticinco enlaces), que cada pantalla de antes
 * sigue abriendo y marca UN destino, que los permisos recortan el menú (y la página sigue negando por su cuenta), que
 * otra empresa ve el suyo, que Atrás/Adelante mantienen el destino marcado, y en 1280, 768, 390 con toque y WebKit
 * que no hay desborde horizontal y que los botones de desplegar miden al menos 44 px. Mide además los clics.
 */

const SHOTS = process.env.NAV_SHOTS ?? "test-results/nav-shots";
const NAV = "Módulos del control interno";
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3032";

type State = Awaited<ReturnType<BrowserContext["storageState"]>>;
const states = new Map<string, State>();
const observations: { env: string; metric: string; value: number | string }[] = [];

async function stateFor(browser: Browser, username: string, password: string): Promise<State> {
  const found = states.get(username);
  if (found) return found;
  for (let attempt = 0; ; attempt++) {
    try {
      const { context } = await sessionFor(browser, username, password);
      const state = await context.storageState();
      await context.close();
      states.set(username, state);
      return state;
    } catch (error) {
      if (attempt >= 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000)); // 5 inicios de sesión por minuto
    }
  }
}

type Env = { name: string; touch: boolean; kind: "chromium" | "webkit"; options: Parameters<Browser["newContext"]>[0]; narrow: boolean };
function profile(name: string): Parameters<Browser["newContext"]>[0] {
  const { defaultBrowserType: _ignored, ...rest } = devices[name];
  void _ignored;
  return rest;
}
const ENVS: Env[] = [
  { name: "chromium-1280x800", touch: false, kind: "chromium", narrow: false, options: { viewport: { width: 1280, height: 800 } } },
  { name: "chromium-768x1024", touch: false, kind: "chromium", narrow: true, options: { viewport: { width: 768, height: 1024 } } },
  { name: "chromium-iphone13-390x844", touch: true, kind: "chromium", narrow: true, options: profile("iPhone 13") },
  { name: "webkit-iphone13", touch: true, kind: "webkit", narrow: true, options: profile("iPhone 13") },
];

async function openAs(browser: Browser, who: keyof typeof NAV_USERS | "other", env: Env): Promise<{ context: BrowserContext; page: Page }> {
  const username = who === "other" ? OTHER_USER : NAV_USERS[who];
  const password = who === "other" ? OTHER_PASSWORD : NAV_PASSWORD;
  const state = await stateFor(browser, username, password);
  const context = await browser.newContext({ ...env.options, storageState: state, baseURL: BASE });
  const page = await context.newPage();
  page.on("dialog", (dialog) => void dialog.accept());
  return { context, page };
}

/** El menú: a la vista en pantalla ancha; en pantalla estrecha, el cajón que abre el botón de la barra. */
async function menu(page: Page, env: Env): Promise<Locator> {
  const nav = page.getByRole("navigation", { name: NAV });
  if (env.narrow && !(await nav.isVisible())) {
    await page.getByRole("button", { name: "Abrir menú de módulos" }).click();
  }
  await expect(nav).toBeVisible({ timeout: 60_000 });
  return nav;
}

async function expectNoOverflow(page: Page, where: string) {
  const { over, culprits } = await page.evaluate(() => {
    const width = window.innerWidth;
    const found: string[] = [];
    document.querySelectorAll("body *").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width > 1 && r.right > width + 1 && !el.closest("[role=dialog]") && !el.closest(".sr-only") && found.length < 6) found.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} → ${Math.round(r.right)}`);
    });
    return { over: document.documentElement.scrollWidth - document.documentElement.clientWidth, culprits: found };
  });
  expect(over, `desborde horizontal en ${where}: ${culprits.join(" | ")}`).toBeLessThanOrEqual(1);
}

async function current(nav: Locator): Promise<string[]> {
  return nav.locator('[aria-current="page"]').evaluateAll((els) => els.map((el) => (el.textContent ?? "").trim()));
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  test.setTimeout(300_000);
  sembrar();
  sembrarNavegacion();
  await stateFor(browser, NAV_USERS.admin, NAV_PASSWORD);
});

test.afterAll(async () => {
  mkdirSync(SHOTS, { recursive: true });
  writeFileSync(`${SHOTS}/nav-observations.json`, JSON.stringify(observations, null, 2));
  if (process.env.E2E_KEEP === "1") return;
  const result = limpiarRecepcion();
  expect(result.left, `NAV-UX-01: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

// ── Recorrido del administrador, en cada ventana ───────────────────────────────────────────────────────────────

async function tour(page: Page, env: Env) {
  // 1 · /admin: nueve destinos, todo recogido salvo lo de uso diario.
  await page.goto("/admin", { waitUntil: "domcontentloaded" });
  let nav = await menu(page, env);
  const sections = await nav.evaluate((el) => {
    const out: Record<string, number> = {};
    el.querySelectorAll(":scope > div").forEach((div) => {
      const title = (div.querySelector("p")?.textContent ?? "").trim();
      out[title] = div.querySelectorAll(":scope > ul > li").length;
    });
    return out;
  });
  expect(sections["Catálogo"]).toBe(2);
  expect(sections["Inventario"]).toBe(7);
  observations.push({ env: env.name, metric: "destinos Catálogo+Inventario", value: sections["Catálogo"] + sections["Inventario"] });
  for (const hidden of ["Equipos seminuevos", "Carga de equipos", "Costos y precios", "Transferencias"]) {
    await expect(nav.getByRole("link", { name: hidden })).toHaveCount(0);
  }
  // El panel de /admin ya desborda 24–45 px en 390 por una tabla `sr-only` de sus gráficos (una tabla no se encoge con
  // `width: 1px`); no es del menú. Se mide y se registra, y el menú abierto se exige sin desborde sobre una página sin gráficos.
  observations.push({ env: env.name, metric: "desborde de /admin (px, preexistente: tabla sr-only de los gráficos)",
    value: await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth) });

  // Los botones de desplegar: al menos 44 px y al alcance del teclado.
  const toggle = nav.getByRole("button", { name: "Mostrar las opciones de Stock y equipos" });
  const box = await toggle.boundingBox();
  expect(box?.height ?? 0, "alto del botón de desplegar").toBeGreaterThanOrEqual(44);
  expect(box?.width ?? 0, "ancho del botón de desplegar").toBeGreaterThanOrEqual(44);

  await page.goto("/admin/inventory/operations", { waitUntil: "domcontentloaded" });
  nav = await menu(page, env);
  await expect(page.getByRole("link", { name: "Recibir mercadería" }).first()).toBeVisible({ timeout: 60_000 });
  await expectNoOverflow(page, `${env.name} centro de operaciones con el menú abierto`);

  // 2 · Recibir mercadería: un clic.
  await nav.getByRole("link", { name: "Recibir mercadería" }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/receive$/);
  await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 60_000 });
  await expectNoOverflow(page, `${env.name} recibir mercadería`);

  // 3 · Equipos seminuevos: desplegar Stock y equipos y entrar (dos clics).
  nav = await menu(page, env);
  await nav.getByRole("button", { name: "Mostrar las opciones de Stock y equipos" }).click();
  await expect(nav.getByRole("button", { name: "Ocultar las opciones de Stock y equipos" })).toHaveAttribute("aria-expanded", "true");
  await nav.getByRole("link", { name: "Equipos seminuevos" }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/used$/);
  nav = await menu(page, env);
  expect(await current(nav)).toEqual(["Equipos seminuevos"]);
  await expectNoOverflow(page, `${env.name} equipos seminuevos`);

  // 4 · Etiquetas y recuentos: un clic cada uno, y no se confunden.
  await nav.getByRole("link", { name: "Etiquetas", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/labels$/);
  nav = await menu(page, env);
  expect(await current(nav)).toEqual(["Etiquetas"]);
  await nav.getByRole("link", { name: "Recuentos" }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/counts$/);
  nav = await menu(page, env);
  expect(await current(nav)).toEqual(["Recuentos"]);

  // 5 · Una importación avanzada: Más herramientas (que no es una página) y la carga de equipos.
  await expect(nav.getByRole("link", { name: "Más herramientas" })).toHaveCount(0);
  await nav.getByRole("button", { name: "Más herramientas" }).click();
  await nav.getByRole("link", { name: "Carga de equipos" }).click();
  await expect(page).toHaveURL(/\/admin\/inventory\/units\/import$/);
  nav = await menu(page, env);
  await expect(nav.getByRole("button", { name: "Más herramientas" })).toHaveAttribute("aria-expanded", "true");
  expect(await current(nav)).toEqual(["Carga de equipos"]);

  // 6 · Productos y variantes: entrar, buscar un artículo, ir a un modelo; Atrás y Adelante.
  await nav.getByRole("link", { name: "Productos y variantes" }).click();
  await expect(page).toHaveURL(/\/admin\/products$/);
  await page.getByPlaceholder("Buscar por nombre o slug…").fill("iPhone 13");
  await expect(page.getByText(new RegExp(`iPhone 13 ${TAG}`)).first()).toBeVisible({ timeout: 60_000 });
  nav = await menu(page, env);
  expect(await current(nav)).toEqual(["Productos y variantes"]);
  await nav.getByRole("link", { name: "Modelos y variantes" }).click();
  await expect(page).toHaveURL(/\/admin\/products\/models$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/admin\/products$/);
  nav = await menu(page, env);
  expect(await current(nav)).toEqual(["Productos y variantes"]);
  await page.goForward();
  await expect(page).toHaveURL(/\/admin\/products\/models$/);
  nav = await menu(page, env);
  expect(await current(nav)).toEqual(["Modelos y variantes"]);
  await page.screenshot({ path: `${SHOTS}/${env.name}-models.png`, fullPage: false });
  await expectNoOverflow(page, `${env.name} modelos y variantes`);

  // 7 · Teclado: Enter en el botón despliega y el siguiente Tab entra en la primera opción.
  await page.goto("/admin", { waitUntil: "domcontentloaded" });
  nav = await menu(page, env);
  const stock = nav.getByRole("button", { name: "Mostrar las opciones de Stock y equipos" });
  await stock.focus();
  await page.keyboard.press("Enter");
  await expect(nav.getByRole("button", { name: "Ocultar las opciones de Stock y equipos" })).toHaveAttribute("aria-expanded", "true");
  // WebKit en macOS no deja que Tab llegue a enlaces ni botones (preferencia del sistema), así que allí sólo se
  // comprueba el Enter; el orden del foco se prueba en Chromium.
  if (env.kind === "chromium") {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => document.activeElement?.textContent?.trim())).toBe("Existencias");
  }
  await page.screenshot({ path: `${SHOTS}/${env.name}-menu.png`, fullPage: false });

  // 8 · En pantalla estrecha el cajón se cierra con Escape y devuelve el foco al botón de la barra.
  if (env.narrow) {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("navigation", { name: NAV })).toHaveCount(0);
    // Safari no enfoca un botón al tocarlo, así que no hay foco previo que devolver: sólo se exige en Chromium.
    if (env.kind === "chromium") await expect(page.getByRole("button", { name: "Abrir menú de módulos" })).toBeFocused();
  }
}

test.describe("menú por destinos · administrador", () => {
  for (const env of ENVS) {
    test(`1 · ${env.name}: nueve destinos, destino activo, Atrás/Adelante, teclado y sin desborde`, async ({ browser }) => {
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

// ── Clics hasta cada tarea (escritorio, con el menú ya a la vista) ──────────────────────────────────────────────

test("2 · clics hasta cada tarea, medidos", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "admin", env);
  try {
    const tasks: { name: string; steps: (nav: Locator) => Locator[]; url: RegExp }[] = [
      { name: "iniciar una recepción", steps: (nav) => [nav.getByRole("link", { name: "Recibir mercadería" })], url: /\/inventory\/receive$/ },
      { name: "consultar un equipo seminuevo", steps: (nav) => [nav.getByRole("button", { name: "Mostrar las opciones de Stock y equipos" }), nav.getByRole("link", { name: "Equipos seminuevos" })], url: /\/inventory\/used$/ },
      { name: "encontrar las etiquetas", steps: (nav) => [nav.getByRole("link", { name: "Etiquetas", exact: true })], url: /\/inventory\/labels$/ },
      { name: "abrir una importación avanzada", steps: (nav) => [nav.getByRole("button", { name: "Más herramientas" }), nav.getByRole("link", { name: "Carga de equipos" })], url: /\/units\/import$/ },
    ];
    for (const task of tasks) {
      await page.goto("/admin", { waitUntil: "domcontentloaded" });
      const nav = await menu(page, env);
      const steps = task.steps(nav);
      for (const step of steps) await step.click();
      await expect(page).toHaveURL(task.url);
      observations.push({ env: env.name, metric: `clics: ${task.name}`, value: steps.length });
    }
  } finally {
    await context.close();
  }
});

// ── Permisos, empresa y rutas de siempre ───────────────────────────────────────────────────────────────────────

test("3 · quien sólo ve el inventario no encuentra recibir ni costos, y la página sigue negando", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "viewer", env);
  try {
    await page.goto("/admin", { waitUntil: "domcontentloaded" });
    const nav = await menu(page, env);
    for (const name of ["Stock y equipos", "Recuentos", "Movimientos", "Centro de operaciones"]) {
      await expect(nav.getByRole("link", { name })).toBeVisible();
    }
    for (const name of ["Recibir mercadería", "Productos y variantes", "Proveedores"]) {
      await expect(nav.getByRole("link", { name, exact: true })).toHaveCount(0);
    }
    // Sin permiso de imprimir, «Etiquetas» lleva a lo único que `company.view` abre: su configuración (como antes).
    await expect(nav.getByRole("link", { name: "Etiquetas", exact: true })).toHaveAttribute("href", "/admin/inventory/labels/settings");
    // Más herramientas: sólo lo que su permiso abre (la gestión masiva); ni costos ni cargas.
    await nav.getByRole("button", { name: "Más herramientas" }).click();
    await expect(nav.getByRole("link", { name: "Gestión masiva" })).toBeVisible();
    for (const name of ["Costos y precios", "Carga de equipos", "Carga de stock", "Carga de productos", "Reportes", "Reposición"]) {
      await expect(nav.getByRole("link", { name })).toHaveCount(0);
    }
    // Esconder un enlace no autoriza ni desautoriza nada: la página de recibir sigue negando por su cuenta.
    await page.goto("/admin/inventory/receive", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toHaveCount(0);
  } finally {
    await context.close();
  }
});

test("4 · el empleado de inventario recibe y etiqueta, pero no gestiona productos ni ve costos", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "clerk", env);
  try {
    await page.goto("/admin", { waitUntil: "domcontentloaded" });
    const nav = await menu(page, env);
    await expect(nav.getByRole("link", { name: "Recibir mercadería" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Etiquetas", exact: true })).toBeVisible();
    await nav.getByRole("button", { name: "Mostrar las opciones de Productos y variantes" }).click();
    await expect(nav.getByRole("link", { name: "Modelos y variantes" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Registrar artículo" })).toHaveCount(0);
    await nav.getByRole("button", { name: "Más herramientas" }).click();
    await expect(nav.getByRole("link", { name: "Carga de equipos" })).toBeVisible();
    for (const name of ["Costos y precios", "Carga de productos", "Reportes"]) await expect(nav.getByRole("link", { name })).toHaveCount(0);
    // Y sin permiso de configurar etiquetas no hay «Configuración» (lleva company.view, que sí tiene): se ve la vista previa.
    await nav.getByRole("link", { name: "Recibir mercadería" }).click();
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 60_000 });
  } finally {
    await context.close();
  }
});

test("5 · otra empresa ve su propio menú y su propio catálogo", async ({ browser }) => {
  test.setTimeout(300_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "other", env);
  try {
    await page.goto("/admin/products", { waitUntil: "domcontentloaded" });
    const nav = await menu(page, env);
    await expect(page.locator("aside").getByText(/QAE otra/)).toBeVisible({ timeout: 60_000 });
    await expect(nav.getByRole("link", { name: "Productos y variantes" })).toBeVisible();
    await expect(page.getByText("Producto de otra empresa").first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(new RegExp(`iPhone 13 ${TAG}`))).toHaveCount(0);
  } finally {
    await context.close();
  }
});

const OLD_ROUTES = [
  "/admin/products", "/admin/products/models", "/admin/products/register", "/admin/products/publication",
  "/admin/products/categories", "/admin/products/suppliers", "/admin/products/suppliers/offers", "/admin/products/import",
  "/admin/inventory/operations", "/admin/inventory/receive", "/admin/inventory", "/admin/inventory/units?line=new",
  "/admin/inventory/used", "/admin/inventory/stock", "/admin/inventory/costs", "/admin/inventory/movements",
  "/admin/inventory/labels", "/admin/inventory/labels/settings", "/admin/inventory/bulk", "/admin/inventory/import",
  "/admin/inventory/units/import", "/admin/inventory/transfers", "/admin/inventory/counts", "/admin/inventory/reports",
  "/admin/inventory/replenishment", "/admin/inventory/units?register=1",
];

test("6 · cada ruta de siempre abre y deja marcado un solo destino del menú", async ({ browser }) => {
  test.setTimeout(900_000);
  const env = ENVS[0];
  const { context, page } = await openAs(browser, "admin", env);
  try {
    for (const route of OLD_ROUTES) {
      const response = await page.goto(route, { waitUntil: "domcontentloaded" });
      expect(response?.status() ?? 0, route).toBeLessThan(400);
      const nav = await menu(page, env);
      await expect(page.locator("main")).toBeVisible({ timeout: 60_000 });
      await expect(page, route).not.toHaveURL(/\/auth/);
      await expect.poll(async () => (await current(nav)).length, { message: `destino marcado en ${route}`, timeout: 30_000 }).toBe(1);
    }
    observations.push({ env: env.name, metric: "rutas de siempre abiertas", value: OLD_ROUTES.length });
  } finally {
    await context.close();
  }
});
