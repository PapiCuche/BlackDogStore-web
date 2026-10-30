import { mkdirSync, writeFileSync } from "node:fs";
import { test, expect, chromium, webkit, devices, type Browser, type BrowserContext, type Locator, type Page, type Request } from "@playwright/test";
import { TAG, OTHER_USER, OTHER_PASSWORD, sembrar, signIn } from "./inventory-first-support";
import { sessionFor, jsonCall, recibos, limpiarRecepcion, serial } from "./inventory-receive-support";
import {
  RL_USERS, RL_PASSWORD, MODELS, ACCESSORY, sembrarLista, articulos, catalogoTotales, nextImei, breakImei, type ListSeed,
} from "./inventory-receive-list-support";

/**
 * INV-RECEIVE-UX-02 — «Pegar una lista» dentro de «Recibir mercadería», de punta a punta y SIN MOCKS
 * (navegador real + API real de Django sobre una base desechable). Sólo se intercepta la RESPUESTA perdida
 * del escenario 6 (el servidor procesa y luego se corta la red), igual que `inventory-receive.spec.ts`.
 *
 * Mismo molde que `inventory-first.spec.ts` e `inventory-receive.spec.ts`: guarda de base local y desechable,
 * datos sintéticos con la marca `TAG`, limpieza por marca al final (`E2E_KEEP=1` la omite), escenarios seriales.
 * Los usuarios `qaer_<corrida>_l*` los crea la propia prueba (`sembrarLista`).
 *
 * ENTORNO: el de los otros recorridos (DEBUG=1, DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store, `next dev` con la
 * tarjeta de accesos de desarrollo para `dev_admin`, base cuyo nombre lleva «e2e»). Con `next dev` en puerto propio:
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3031 E2E_API_BASE=http://127.0.0.1:8591/api \
 *     npx playwright test e2e/inventory-receive-list.spec.ts
 * WebKit (perfil iPhone 13) corre si está instalado; si no, se omite a propósito y lo dice.
 */

const SHOTS = process.env.RUX_SHOTS ?? "test-results/rux-shots";
const DESKTOP = { width: 1280, height: 800 };
/** Milisegundos entre teclas del «lector» (un lector real escribe de 1 a 20 ms por carácter). Con 0 ms —ráfaga instantánea— se pierde un carácter (hallazgo P2; ver el informe de la corrida): `RUX_SCAN_DELAY=0` lo reproduce. */
const SCAN_DELAY = Number(process.env.RUX_SCAN_DELAY ?? 5);
/** Lecturas por motor de la prueba 5b (ráfaga de 0 ms). */
const BURST_RUNS = Number(process.env.RUX_BURST_RUNS ?? 10);
const BASE = process.env.E2E_BASE_URL ?? "http://localhost:3002";

let list: ListSeed;
const sessions = new Map<string, { context: BrowserContext; page: Page }>();
const observations: { env: string; scenario: string; state: string; kind: string; detail: string }[] = [];

// ── Ayudas de página ───────────────────────────────────────────────────────────────────────────────────────────

async function shot(page: Page, env: string, scenario: string, state: string) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${env}-${scenario}-${state}.png`, fullPage: true });
}

/** Entra por la API (la misma ruta que la pantalla); el límite es de 5 inicios por minuto, así que espera y reintenta. */
async function login(browser: Browser, username: string, password: string) {
  for (let attempt = 0; ; attempt++) {
    try { return await sessionFor(browser, username, password); }
    catch (error) {
      if (attempt >= 2 || !/429/.test(String(error))) throw error;
      await new Promise((resolve) => setTimeout(resolve, 62_000));
    }
  }
}

async function as(browser: Browser, key: "manager" | "plain" | "other") {
  const found = sessions.get(key);
  if (found) return found;
  const username = key === "other" ? OTHER_USER : RL_USERS[key];
  const password = key === "other" ? OTHER_PASSWORD : RL_PASSWORD;
  const made = await login(browser, username, password);
  made.page.on("dialog", (dialog) => void dialog.accept());
  sessions.set(key, made);
  return made;
}

const button = (page: Page, name: string | RegExp) => page.getByRole("button", { name });
const main = (page: Page) => page.locator("main");

/** El texto de la página SIN el <title> (con `next dev` viaja en el cuerpo). */
function pageText(page: Page, matcher: string | RegExp): Locator {
  return page.getByText(matcher).and(page.locator(":not(title)"));
}

/** Abre el asistente, deja la sucursal por defecto, pasa al paso 2 y elige «Pegar una lista». */
async function openPaste(page: Page, touch = false) {
  await page.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" }), "el asistente no cargó").toBeVisible({ timeout: 90_000 });
  await press(button(page, "Siguiente: artículos"), touch);
  await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
  await press(button(page, /^Pegar una lista/), touch);
  await expect(page.getByLabel("Texto para reconocer")).toBeVisible();
}

async function press(target: Locator, touch: boolean) {
  await target.scrollIntoViewIfNeeded({ timeout: 30_000 });
  if (touch) await target.tap({ timeout: 30_000 }); else await target.click({ timeout: 30_000 });
}

type Dev = { model: string; gb: number; colour: string; serial?: string; imei?: string; battery?: number; extra?: string[] };
const block = (d: Dev) => [
  d.model, `${d.gb} GB`, `Color: ${d.colour}`,
  ...(d.battery !== undefined ? [`Batería ${d.battery}%`] : []),
  ...(d.serial ? [`NS: ${d.serial}`] : []),
  ...(d.imei ? [`IMEI 1: ${d.imei}`] : []),
  ...(d.extra ?? []),
].join("\n");

async function paste(page: Page, text: string, declared: "Seminuevos" | "Nuevos" | "Accesorios" = "Seminuevos", touch = false) {
  await page.getByLabel("Línea declarada").selectOption({ label: declared });
  await page.getByLabel("Texto para reconocer").fill(text);
  await press(button(page, "Analizar y preparar"), touch);
}

const progress = (page: Page) => page.getByRole("list", { name: "Avance de la lista" });

/** Los cuatro contadores del avance: listos, por asociar, por completar, duplicados o inválidos. */
async function counters(page: Page) {
  const read = async (index: number) => Number((await progress(page).locator("li").nth(index).locator("strong").innerText()).trim());
  return { ready: await read(0), catalog: await read(1), info: await read(2), invalid: await read(3) };
}

async function settled(page: Page) {
  await expect(page.getByText("Buscando en el catálogo…").first()).toHaveCount(0, { timeout: 30_000 });
}

/** La tarjeta de un grupo (variante) por el título de su encabezado; el color viene como lo escribió el texto («negro»). */
const group = (page: Page, name: RegExp) =>
  page.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 4, name: new RegExp(name.source, "i") }) }).last();

/** La fila «Equipo n» (o «Accesorio n») de la lista, con su <details>. */
const row = (page: Page, n: number, what: "Equipo" | "Accesorio" = "Equipo") =>
  page.locator("details").filter({ has: page.locator("summary").filter({ hasText: new RegExp(`^\\s*${what} ${n}(?!\\d)`) }) }).first();

async function openRow(page: Page, n: number, what: "Equipo" | "Accesorio" = "Equipo") {
  const target = row(page, n, what);
  if ((await target.getAttribute("open")) === null) await target.locator("summary").first().click();
  return target;
}

/** Crea, aprobando UNA propuesta con todo, lo que falta en el catálogo. */
async function approveEverything(page: Page, touch = false, inspect?: () => Promise<void>) {
  const panel = page.getByRole("region", { name: "Proponer lo que falta" });
  await press(panel.getByRole("button", { name: "Proponer lo que falta" }), touch);
  await expect(panel.getByText(/por crear ·/)).toBeVisible({ timeout: 30_000 });
  await panel.getByLabel("Seleccionar todo").check();
  await inspect?.();
  await press(panel.getByRole("button", { name: "Crear lo aprobado" }), touch);
  await expect(panel.getByText(/^Creado:/)).toBeVisible({ timeout: 30_000 });
}

const addButton = (page: Page) => page.locator("[data-commit]");

async function toReview(page: Page, touch = false) {
  await press(button(page, "Siguiente: revisar"), touch);
  await expect(page.getByRole("heading", { name: "Revisa antes de registrar" })).toBeVisible({ timeout: 30_000 });
}

const confirmButton = (page: Page) => button(page, /Confirmar recepción|Registrando/);

async function accept(page: Page, touch = false) {
  const box = page.getByLabel(/Revisé los datos y quiero registrar/);
  if (touch) await box.scrollIntoViewIfNeeded();
  await box.check();
}

function watchConfirm(page: Page) {
  const seen: { key: string; body: unknown }[] = [];
  const onRequest = (req: Request) => {
    if (req.method() === "POST" && /receipts\/confirm/.test(req.url()) && req.redirectedFrom() === null) {
      seen.push({ key: req.headers()["idempotency-key"] ?? "", body: req.postDataJSON() });
    }
  };
  page.on("request", onRequest);
  return { seen, stop: () => page.off("request", onRequest) };
}

const waitConfirmResponse = (page: Page) =>
  page.waitForResponse((res) => /receipts\/confirm/.test(res.url()) && res.request().method() === "POST" && res.status() !== 308);

/** Lo que la página guarda en el navegador y en la URL (para probar que nada de la lista queda ahí). */
async function browserTraces(page: Page): Promise<string> {
  return page.evaluate(async () => {
    const dump = (store: Storage) => Object.keys(store).map((key) => `${key}=${store.getItem(key)}`).join("\n");
    let idb = "";
    try { idb = ((await indexedDB.databases?.()) ?? []).map((db) => db.name).join(","); } catch { /* sin acceso */ }
    return [location.href, document.title, dump(localStorage), dump(sessionStorage), idb, JSON.stringify(history.state ?? {})].join("\n");
  });
}

// ── Medidas de interfaz ────────────────────────────────────────────────────────────────────────────────────────

type Audit = {
  scroll: number; inner: number; offenders: string[];
  clipped: string[];
  /** Texto recortado fuera de <main> (la barra y el menú de la aplicación): se anota, no es de esta pantalla. */
  shellClipped: string[];
  small: { what: string; h: number; w: number }[];
};

/** Desborde de la página, texto recortado (rectángulos de texto, AGENTS.md §7) y objetivos táctiles menores de 44 px. */
async function audit(page: Page): Promise<Audit> {
  return page.evaluate(() => {
    const inner = window.innerWidth;
    const scroll = document.scrollingElement!.scrollWidth;
    const visible = (el: Element) => {
      const style = getComputedStyle(el);
      if (style.visibility === "hidden" || style.display === "none") return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const inScroller = (el: Element | null) => {
      for (let a = el; a && a !== document.body; a = a.parentElement) {
        const x = getComputedStyle(a).overflowX;
        if ((x === "auto" || x === "scroll") && a.scrollWidth > a.clientWidth) return true;
      }
      return false;
    };
    const offenders = scroll <= inner ? [] : [...document.body.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > inner + 1 && visible(el) && !inScroller(el.parentElement))
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).slice(0, 60)} -> ${Math.round(el.getBoundingClientRect().right)}px`);

    const clipped: string[] = [];
    const shellClipped: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && clipped.length + shellClipped.length < 20) {
      const node = walker.currentNode as Text;
      const text = node.textContent?.trim() ?? "";
      const el = node.parentElement;
      if (!text || !el || ["OPTION", "SCRIPT", "STYLE", "TITLE", "NOSCRIPT"].includes(el.tagName) || !visible(el)) continue;
      if (el.closest(".sr-only, [hidden], template, select, details:not([open]) > :not(summary)")) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
      if (!rects.length) continue;
      const left = Math.min(...rects.map((r) => r.left));
      const right = Math.max(...rects.map((r) => r.right));
      const bottom = Math.max(...rects.map((r) => r.bottom));
      const top = Math.min(...rects.map((r) => r.top));
      const say = (why: string) => (el.closest("main") ? clipped : shellClipped).push(`${why}: «${text.slice(0, 50)}» en <${el.tagName.toLowerCase()}>`);
      if (!inScroller(el) && (right > inner + 1 || left < -1)) { say("fuera de la ventana"); continue; }
      for (let a: Element | null = el; a && a !== document.body; a = a.parentElement) {
        const style = getComputedStyle(a);
        if (style.display === "contents") continue;
        const box = a.getBoundingClientRect();
        const cutsX = style.overflowX === "hidden" || style.overflowX === "clip";
        const cutsY = style.overflowY === "hidden" || style.overflowY === "clip";
        if ((cutsX && (right > box.right + 1.5 || left < box.left - 1.5)) || (cutsY && (bottom > box.bottom + 1.5 || top < box.top - 1.5))) {
          say(`recortado por <${a.tagName.toLowerCase()}>`);
          break;
        }
        if (style.textOverflow === "ellipsis" && right > box.right - parseFloat(style.paddingRight || "0") + 1.5) { say("puntos suspensivos"); break; }
      }
    }

    const small: Audit["small"] = [];
    for (const el of document.querySelectorAll("main button, main a[href], main select, main input:not([type=hidden]), main textarea, main summary")) {
      if (!visible(el) || el.closest(".sr-only")) continue;
      const rect = el.getBoundingClientRect();
      if (rect.height < 43.5) {
        const label = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 48);
        small.push({ what: `${el.tagName.toLowerCase()}${(el as HTMLInputElement).type ? `[${(el as HTMLInputElement).type}]` : ""} «${label}»`, h: Math.round(rect.height), w: Math.round(rect.width) });
      }
    }
    return { scroll, inner, offenders, clipped, shellClipped, small };
  });
}

/** Mide una pantalla: el desborde y el texto recortado fallan; los objetivos pequeños se anotan como hallazgo de UX. */
async function measure(page: Page, env: string, scenario: string, state: string, mustBeBig: (string | RegExp)[] = []) {
  const result = await audit(page);
  expect(result.scroll, `${env}/${scenario}/${state}: la página mide ${result.scroll}px en ${result.inner}px; se pasan: ${result.offenders.join(" | ")}`)
    .toBeLessThanOrEqual(result.inner);
  expect(result.clipped, `${env}/${scenario}/${state}: texto recortado`).toEqual([]);
  for (const name of mustBeBig) {
    const target = page.getByRole("button", { name }).first();
    if (await target.count() === 0 || !(await target.isVisible())) continue;
    const box = (await target.boundingBox())!;
    expect(box.height, `${env}/${scenario}/${state}: «${String(name)}» mide ${Math.round(box.height)}px de alto (mínimo 44)`).toBeGreaterThanOrEqual(43.5);
  }
  for (const item of result.shellClipped) observations.push({ env, scenario, state, kind: "texto recortado en la barra de la aplicación", detail: item });
  for (const item of result.small) observations.push({ env, scenario, state, kind: "objetivo < 44 px", detail: `${item.what} ${item.w}x${item.h}` });
  return result;
}

/** El foco por teclado se ve: contorno o sombra en el elemento enfocado tras pulsar Tab. */
async function expectFocusRing(page: Page, env: string, where: string) {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  let seen: { tag: string; ring: boolean; text: string } | null = null;
  for (let index = 0; index < 4 && !seen?.ring; index++) {
    await page.keyboard.press("Tab");
    seen = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return { tag: "body", ring: false, text: "" };
      const style = getComputedStyle(el);
      const outline = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
      const shadow = style.boxShadow !== "none" && /\d/.test(style.boxShadow);
      return { tag: el.tagName.toLowerCase(), ring: (outline || shadow) && el.matches(":focus-visible"), text: (el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 40) };
    });
  }
  expect(seen?.ring, `${where}: el foco por teclado no se ve en <${seen?.tag}> «${seen?.text}»`).toBe(true);
}

// ── Preparación ────────────────────────────────────────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

let adminContext: BrowserContext;
let admin: Page;

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  test.setTimeout(300_000);
  sembrar();
  list = sembrarLista();
  adminContext = await browser.newContext({ viewport: DESKTOP });
  admin = await adminContext.newPage();
  admin.on("dialog", (dialog) => void dialog.accept());
  await signIn(admin);
});

test.afterAll(async () => {
  for (const { context } of sessions.values()) await context.close().catch(() => undefined);
  await adminContext?.close();
  mkdirSync(SHOTS, { recursive: true });
  writeFileSync(`${SHOTS}/ux-observations.json`, JSON.stringify(observations, null, 2));
  if (process.env.E2E_KEEP === "1") { console.warn(`INV-RECEIVE-UX-02: datos conservados con la marca ${TAG}`); return; }
  const result = limpiarRecepcion();
  if (process.env.E2E_VERBOSE === "1") console.warn("INV-RECEIVE-UX-02: limpieza ->", JSON.stringify(result));
  expect(result.left, `INV-RECEIVE-UX-02: la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

// 11 teléfonos seminuevos, 5 combinaciones: la primera ya existe (3), las otras no (2 + 2 + 2 + 2).
const M15 = MODELS.existing;
const M17 = `iPhone 17 ${TAG}`;
const COMBOS: { model: string; gb: number; colour: string; count: number; name: string; exists: boolean }[] = [
  { model: M15, gb: 128, colour: "negro", count: 3, name: `${M15} 128 GB Negro Seminuevo`, exists: true },
  { model: M15, gb: 256, colour: "azul", count: 2, name: `${M15} 256 GB Azul`, exists: false },   // modelo y opciones existen
  { model: M15, gb: 128, colour: "azul", count: 2, name: `${M15} 128 GB Azul`, exists: false },   // ídem
  { model: M17, gb: 128, colour: "negro", count: 2, name: `${M17} 128 GB Negro`, exists: false }, // el MODELO no existe
  { model: M17, gb: 256, colour: "azul", count: 2, name: `${M17} 256 GB Azul`, exists: false },   // el mismo modelo nuevo
];

const shared: { eleven: { serial: string; imei: string; battery: number }[]; receiptsBefore: number; totalsBefore?: ReturnType<typeof catalogoTotales> } = {
  eleven: [], receiptsBefore: 0,
};

test.describe("recibir mercadería · pegar una lista", () => {
  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("1 · aceptación: 11 seminuevos en 5 combinaciones, una aprobación, una confirmación", async () => {
    test.setTimeout(420_000);
    const manager = await as(admin.context().browser()!, "manager");
    const page = manager.page;
    await page.setViewportSize(DESKTOP);
    shared.receiptsBefore = recibos();
    const totalsBefore = catalogoTotales();
    const devices: (Dev & { n: number })[] = [];
    let n = 0;
    for (const combo of COMBOS) {
      for (let i = 0; i < combo.count; i++) {
        n += 1;
        devices.push({ n, model: combo.model, gb: combo.gb, colour: combo.colour, serial: serial("L", n), imei: nextImei(), battery: 80 + n });
      }
    }
    shared.eleven = devices.map((d) => ({ serial: d.serial!, imei: d.imei!, battery: d.battery! }));
    const watch = watchConfirm(page);

    await openPaste(page);
    await shot(page, "chromium-1280x800", "s1", "pegar-vacio");
    await paste(page, devices.map(block).join("\n\n"));
    await expect(pageText(page, /Reconocimos 11 equipos y accesorios/)).toBeVisible();
    await settled(page);
    await expect(page.getByRole("list", { name: "Equipos por variante del catálogo" }).locator("> li")).toHaveCount(5);
    // Avance tras la búsqueda automática: las 3 exactas ya están listas; 8 esperan al catálogo.
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 3, catalog: 8, info: 0, invalid: 0 });
    await expect(button(page, "Agregar 3 equipos listos a la recepción")).toBeEnabled();
    await expect(group(page, new RegExp(`${M15} 128 GB Negro · Seminuevo`))).toContainText("Asociado automáticamente");
    await expect(group(page, new RegExp(`${M17} 256 GB Azul · Seminuevo`))).toContainText("No existe");
    await shot(page, "chromium-1280x800", "s1", "analizado");

    // «Siguiente» está bloqueado mientras queden filas pegadas sin resolver.
    await button(page, "Siguiente: revisar").click();
    await expect(page.getByRole("alert").filter({ hasText: /Quedan 11 filas de la lista sin agregar/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();

    // Una sola propuesta para todo lo que falta.
    const proposals: string[] = [];
    page.on("request", (req) => { if (req.method() === "POST" && /catalog-proposal/.test(req.url()) && req.redirectedFrom() === null) proposals.push(req.url().replace(/\?.*$/, "")); });
    await approveEverything(page);
    await shot(page, "chromium-1280x800", "s1", "propuesta-aprobada");
    expect(proposals.filter((url) => /\/apply\/$/.test(url)), "una sola aprobación").toHaveLength(1);
    expect(proposals, "una propuesta y una aprobación, nada más").toHaveLength(2);
    await settled(page);
    // Los cinco grupos quedan asociados SIN perder la lista.
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 11, catalog: 0, info: 0, invalid: 0 });
    await expect(page.getByRole("list", { name: "Equipos por variante del catálogo" }).locator("> li")).toHaveCount(5);
    for (const combo of COMBOS) {
      await expect(group(page, new RegExp(`${combo.model} ${combo.gb} GB ${combo.colour === "negro" ? "Negro" : "Azul"} · Seminuevo`))).toContainText(/Asociado/);
    }

    // Cada equipo conserva lo suyo: la batería viene del texto; el estado estético y las observaciones los pone la persona.
    const gradeLabels: string[] = [];
    for (const d of devices) {
      const open = await openRow(page, d.n);
      if (d.n === 1) await shot(page, "chromium-1280x800", "s1", "fila-abierta");
      await expect(open.locator('[data-field="battery"]')).toHaveValue(String(d.battery));
      const select = open.getByLabel("Estado estético");
      const options = await select.locator("option").allInnerTexts();
      const label = options[1 + (d.n % (options.length - 1))];
      await select.selectOption({ label });
      gradeLabels.push(label);
      await open.locator('[data-field="notes"]').fill(`caja ${d.n} sin cargador`);
      if (d.n === 2) await open.locator('[data-field="battery"]').fill("99");
    }
    shared.eleven[1].battery = 99;
    await expect(button(page, "Agregar 11 equipos listos a la recepción")).toBeEnabled();
    await button(page, "Agregar 11 equipos listos a la recepción").click();
    await expect(pageText(page, /11 equipos agregados a la recepción/)).toBeVisible();
    await expect(progress(page)).toHaveCount(0); // la lista quedó vacía: todo entró
    await shot(page, "chromium-1280x800", "s1", "agregado");
    await toReview(page);
    await expect(page.getByRole("status").filter({ hasText: "Se prepararán" })).toContainText("11 equipos identificados");
    await expect(page.getByRole("status").filter({ hasText: "Se prepararán" })).toContainText("11 etiquetas");
    // Identificadores enmascarados en la revisión.
    const reviewText = await main(page).innerText();
    for (const d of devices) {
      expect(reviewText, `la revisión mostró el IMEI completo ${d.n}`).not.toContain(d.imei!);
      expect(reviewText, `la revisión mostró la serie completa ${d.n}`).not.toContain(d.serial!);
    }
    await shot(page, "chromium-1280x800", "s1", "revisar");
    await expect(confirmButton(page)).toBeDisabled();
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    await shot(page, "chromium-1280x800", "s1", "resultado");
    watch.stop();
    expect(watch.seen).toHaveLength(1);

    // La base: 11 equipos, una entrada y un lote por artículo (5), datos por equipo y variantes ocultas de la web.
    const found = articulos(TAG).filter((a) => a.serialized && a.units.length > 0);
    expect(found).toHaveLength(5);
    expect(found.flatMap((a) => a.units)).toHaveLength(11);
    for (const combo of COMBOS) {
      const article = found.find((a) => a.name.startsWith(combo.name));
      expect(article, `falta el artículo «${combo.name}»`).toBeDefined();
      expect(article!.units, combo.name).toHaveLength(combo.count);
      expect(article!.movements.filter((m) => m.type === "purchase_entry"), combo.name).toHaveLength(1);
      expect(article!.movements, combo.name).toHaveLength(1);
      expect(article!.movements[0]).toMatchObject({ quantity: combo.count, before: 0, after: combo.count });
      expect(article!.batches, `un lote de etiquetas por artículo: ${combo.name}`).toHaveLength(1);
      expect(article!.batches[0].copies).toBe(combo.count);
      expect(article!.line).toBe("used");
      if (!combo.exists) expect(article!.published, `la variante creada «${combo.name}» no debe publicarse`).toBe(false);
    }
    for (const d of devices) {
      const unit = found.flatMap((a) => a.units).find((u) => u.serial === d.serial);
      expect(unit, `falta el equipo ${d.n}`).toBeDefined();
      expect(unit).toMatchObject({ imei: d.imei, condition: "used", status: "available", battery: d.n === 2 ? 99 : d.battery, grade: gradeLabels[d.n - 1], notes: `caja ${d.n} sin cargador`, branch: list.branchId });
    }
    const models = articulos(M17);
    expect(models.every((a) => a.model === M17 && a.published === false)).toBe(true);
    expect(recibos()).toBe(shared.receiptsBefore + 1);
    const totalsAfter = catalogoTotales();
    expect(totalsAfter.products - totalsBefore.products, "se crearon 4 artículos, no uno por equipo").toBe(4);
    shared.totalsBefore = totalsAfter;
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("1b · doble clic en «Confirmar recepción» y recarga tras confirmar: no se duplica nada", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const devices = [0, 1].map((i) => ({ model: M15, gb: 128, colour: "negro", serial: serial("DC", i + 1), imei: nextImei(), battery: 90 + i }));
    const receiptsBefore = recibos();
    const unitsBefore = articulos(`${M15} 128 GB Negro`)[0].units.length;
    const entriesBefore = articulos(`${M15} 128 GB Negro`)[0].movements.filter((m) => m.type === "purchase_entry").length;
    const watch = watchConfirm(page);
    await openPaste(page);
    await paste(page, devices.map(block).join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 0 });
    await addButton(page).click();
    await toReview(page);
    await accept(page);
    await confirmButton(page).dblclick();
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    watch.stop();
    const keys = new Set(watch.seen.map((s) => s.key));
    expect(keys.size, "todos los envíos comparten la misma clave de idempotencia").toBe(1);
    expect(articulos(`${M15} 128 GB Negro`)[0].units).toHaveLength(unitsBefore + 2);
    expect(recibos()).toBe(receiptsBefore + 1);
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 60_000 });
    expect(articulos(`${M15} 128 GB Negro`)[0].units).toHaveLength(unitsBefore + 2);
    expect(articulos(`${M15} 128 GB Negro`)[0].movements.filter((m) => m.type === "purchase_entry")).toHaveLength(entriesBefore + 1);
    expect(recibos()).toBe(receiptsBefore + 1);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("2 · lista mezclada: nuevos y seminuevos, un accesorio con cantidad escrita por la persona", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const M16 = MODELS.mixed;
    const used = [0, 1].map((i) => ({ model: M15, gb: 128, colour: "negro", serial: serial("MU", i + 1), imei: nextImei(), battery: 85 + i }));
    // Se declaran seminuevos, pero son NUEVOS: la persona corrige el grupo.
    const fresh = [0, 1].map((i) => ({ model: M16, gb: 256, colour: "azul", serial: serial("MN", i + 1), imei: nextImei() }));
    const text = [...used.map(block), ...fresh.map(block), ACCESSORY].join("\n\n");
    await openPaste(page);
    await paste(page, text);
    await settled(page);
    // El grupo «nuevo» no existe como seminuevo; el accesorio pide su cantidad (no se adivina).
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 2, catalog: 2, info: 1, invalid: 0 });
    await expect(page.getByRole("list", { name: "Equipos por variante del catálogo" }).locator("> li")).toHaveCount(3);
    const accessory = page.getByRole("list", { name: "Equipos por variante del catálogo" }).locator("> li").last();
    await expect(accessory).toContainText("Asociado automáticamente");
    await expect(accessory.getByRole("button", { name: /Son nuevos|Son seminuevos/ })).toHaveCount(0);
    await shot(page, "chromium-1280x800", "s2", "mezclada");

    const wrong = group(page, new RegExp(`${M16} 256 GB Azul · Seminuevo`));
    await expect(wrong).toContainText("No existe");
    await wrong.getByRole("button", { name: "Son nuevos" }).click();
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 4, catalog: 0, info: 1, invalid: 0 });
    const corrected = group(page, new RegExp(`${M16} 256 GB Azul · Nuevo`));
    await expect(corrected).toContainText("Asociado");
    await expect(corrected.getByRole("button", { name: "Son seminuevos" })).toBeVisible();

    // «Resolver pendientes» lleva el foco a la cantidad del accesorio.
    await button(page, "Resolver pendientes").click();
    await expect(page.locator('[data-field="quantity"]')).toBeFocused();
    await page.keyboard.type("5");
    await expect.poll(() => counters(page)).toEqual({ ready: 5, catalog: 0, info: 0, invalid: 0 });
    await shot(page, "chromium-1280x800", "s2", "corregida");
    await addButton(page).click();
    await toReview(page);
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });

    const freshArticle = articulos(`${M16} 256 GB Azul Nuevo`)[0];
    expect(freshArticle.units.map((u) => u.condition)).toEqual(["new", "new"]);
    expect(freshArticle.line).toBe("new");
    const cargador = articulos(ACCESSORY)[0];
    expect(cargador.units).toHaveLength(0);
    expect(cargador.movements).toEqual([{ type: "purchase_entry", quantity: 5, before: 0, after: 5 }]);
    expect(cargador.inventory).toBe(5);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("3 · sin products.manage: se explica, no se crea nada y lo que existe se puede recibir", async ({ browser }) => {
    test.setTimeout(300_000);
    const { page } = await as(browser, "plain");
    const totalsBefore = catalogoTotales();
    const ok = { model: M15, gb: 128, colour: "negro", serial: serial("PL", 1), imei: nextImei(), battery: 91 };
    const gone = [0, 1].map((i) => ({ model: `iPhone 14 ${TAG}`, gb: 64, colour: "rojo", serial: serial("PL", 2 + i), imei: nextImei() }));
    await openPaste(page);
    await paste(page, [block(ok), ...gone.map(block)].join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 1, catalog: 2, info: 0, invalid: 0 });
    const missing = group(page, new RegExp(`iPhone 14 ${TAG} 64 GB Rojo`));
    await expect(missing).toContainText("Tu usuario no puede crear artículos");
    await expect(main(page).getByText(/requiere permiso para gestionar productos/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Proponer lo que falta" })).toHaveCount(0);
    await shot(page, "chromium-1280x800", "s3", "sin-permiso");

    await addButton(page).click();
    await expect(pageText(page, /1 equipo agregado a la recepción\. Quedan 2 filas en la lista/)).toBeVisible();
    await button(page, "Siguiente: revisar").click();
    const blocked = page.getByRole("alert").filter({ hasText: /Quedan 2 filas de la lista sin agregar/ });
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText("Usa «Resolver pendientes»");
    await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    await shot(page, "chromium-1280x800", "s3", "siguiente-bloqueado");
    // Lo que no puede resolver, lo descarta: la lista ya agregada no cambia y el resto sigue.
    await button(page, "Descartar lo que falta").click();
    await expect(pageText(page, /Se descartó la lista que faltaba resolver/)).toBeVisible();
    await toReview(page);
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    expect(catalogoTotales()).toEqual(totalsBefore);
    expect(articulos(`iPhone 14 ${TAG}`)).toHaveLength(0);
    expect(articulos(`${M15} 128 GB Negro`)[0].units.map((u) => u.serial)).toContain(ok.serial);

    // El servidor niega lo mismo aunque la pantalla no lo ofrezca.
    const denied = await jsonCall(page.context().request, "/admin/inventory/text-preview/catalog-proposal/apply/",
      { rows: [{ key: "1", kind: "device", model: `iPhone 14 ${TAG}`, capacityGb: 64, colour: "rojo", line: "used" }], approve: { models: [], variants: [] } }, { "Idempotency-Key": `rux-${TAG}-sinpermiso` });
    expect(denied.status).toBe(403);
    expect(catalogoTotales()).toEqual(totalsBefore);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("4 · IMEI repetido y IMEI inválido: sólo esas filas se bloquean, con su motivo", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const shared1 = nextImei();
    const good = [0, 1].map((i) => ({ model: M15, gb: 128, colour: "negro", serial: serial("IV", i + 1), imei: nextImei(), battery: 88 }));
    const twinA = { model: M15, gb: 128, colour: "negro", serial: serial("IV", 3), imei: shared1 };
    const twinB = { model: M15, gb: 128, colour: "negro", serial: serial("IV", 4), imei: shared1 };
    const broken = { model: M15, gb: 128, colour: "negro", serial: serial("IV", 5), imei: breakImei(nextImei()) };
    await openPaste(page);
    await paste(page, [...good, twinA, twinB, broken].map(block).join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 3 });
    await button(page, "Resolver pendientes").click();
    await expect(page.locator('[data-field="imei"]:focus')).toHaveCount(1);
    for (const n of [3, 4, 5]) {
      await openRow(page, n);
    }
    const reasons = await page.getByRole("alert").allInnerTexts();
    expect(reasons.join("\n")).toMatch(/IMEI está repetido/);
    expect(reasons.join("\n")).toMatch(/no supera su dígito de control/);
    await expect(row(page, 1)).toContainText("Listo");
    await expect(row(page, 2)).toContainText("Listo");
    await expect(row(page, 3)).toContainText("repetido");
    await expect(row(page, 4)).toContainText("repetido");
    await expect(row(page, 5)).toContainText("dígito de control");
    await shot(page, "chromium-1280x800", "s4", "bloqueadas");
    // Las listas siguen: se agregan las dos buenas, las otras tres quedan con su motivo.
    await addButton(page).click();
    await expect(pageText(page, /2 equipos agregados a la recepción\. Quedan 3 filas en la lista/)).toBeVisible();
    await expect.poll(() => counters(page)).toEqual({ ready: 0, catalog: 0, info: 0, invalid: 3 });
    await expect(addButton(page)).toBeDisabled();
    // Quedan 3 filas, renumeradas de nuevo («Equipo 1…3»). Corregir una gemela libera a la otra.
    const repaired = await openRow(page, 1);
    await repaired.locator('[data-field="imei"]').fill(nextImei());
    await expect.poll(() => counters(page)).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 1 });
    await expect(row(page, 3)).toContainText("dígito de control");
    await (await openRow(page, 3)).locator('[data-field="imei"]').fill(nextImei());
    await expect.poll(() => counters(page)).toEqual({ ready: 3, catalog: 0, info: 0, invalid: 0 });
    // La persona decide no recibir una de ellas: se quita de la lista (la serie y el IMEI no quedan en ninguna parte).
    await (await openRow(page, 2)).getByRole("button", { name: /Quitar el equipo \d+ de la lista/ }).click();
    await expect.poll(() => counters(page)).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 0 });
    await addButton(page).click();
    await toReview(page);
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    const serials = articulos(`${M15} 128 GB Negro`)[0].units.map((u) => u.serial);
    for (const d of [...good, twinA, broken]) expect(serials).toContain(d.serial);
    expect(serials).not.toContain(twinB.serial);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("5 · lector de códigos: serie e IMEI sólo con teclado y Enter; el foco avanza al siguiente vacío", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const blank = [0, 1, 2].map(() => ({ model: M15, gb: 128, colour: "negro" }));
    const scans = [0, 1, 2].map((i) => ({ serial: serial("SC", i + 1), imei: nextImei() }));
    await openPaste(page);
    await paste(page, blank.map(block).join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 0, catalog: 0, info: 3, invalid: 0 });
    const focused = () => page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return { unit: el?.getAttribute("data-unit") ?? "", field: el?.getAttribute("data-field") ?? "", commit: el?.hasAttribute("data-commit") ?? false };
    });
    await button(page, "Resolver pendientes").click();
    const first = await expect.poll(focused).toMatchObject({ field: "serial" }).then(() => focused());
    const unitOf = async () => (await focused()).unit;
    const units: string[] = [first.unit];
    for (let i = 0; i < 3; i++) {
      const at = await focused();
      expect(at, `lectura ${i + 1}: debe estar en la serie`).toMatchObject({ field: "serial", unit: units[i] });
      await page.keyboard.type(scans[i].serial, { delay: SCAN_DELAY });
      expect(await page.evaluate(() => (document.activeElement as HTMLInputElement).value), `serie ${i + 1} tal como la escribió el lector`).toBe(scans[i].serial);
      await page.keyboard.press("Enter");
      await expect.poll(focused, { message: `tras la serie ${i + 1} el foco va a su IMEI` }).toMatchObject({ field: "imei", unit: units[i] });
      await page.keyboard.type(scans[i].imei, { delay: SCAN_DELAY });
      expect(await page.evaluate(() => (document.activeElement as HTMLInputElement).value), `IMEI ${i + 1} tal como lo escribió el lector`).toBe(scans[i].imei);
      await page.keyboard.press("Enter");
      if (i < 2) {
        await expect.poll(focused, { message: `tras el IMEI ${i + 1} el foco va a la serie de la fila siguiente` }).toMatchObject({ field: "serial" });
        units.push(await unitOf());
        expect(units[i + 1]).not.toBe(units[i]);
      }
    }
    // Al terminar, el foco queda en «Agregar … a la recepción».
    await expect.poll(focused).toMatchObject({ commit: true });
    await shot(page, "chromium-1280x800", "s5", "lector-terminado");
    await expect.poll(() => counters(page)).toEqual({ ready: 3, catalog: 0, info: 0, invalid: 0 });
    await page.keyboard.press("Enter");
    await expect(pageText(page, /3 equipos agregados a la recepción/)).toBeVisible();
    await toReview(page);
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    const stored = articulos(`${M15} 128 GB Negro`)[0].units;
    for (const s of scans) expect(stored.find((u) => u.serial === s.serial)?.imei).toBe(s.imei);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  for (const engine of ["chromium", "webkit"] as const) {
    test(`5b · lector en ráfaga sin pausa (0 ms), ${BURST_RUNS} lecturas en ${engine}: ningún carácter se pierde`, async ({ browser }) => {
      test.setTimeout(600_000);
      let owned: Browser | null = null;
      let context: BrowserContext | null = null;
      try {
        let page: Page;
        if (engine === "webkit") {
          try { owned = await webkit.launch(); } catch (error) { test.skip(true, `WebKit no abre: ${String(error).slice(0, 100)}`); return; }
          const made = await login(owned, RL_USERS.manager, RL_PASSWORD);
          const state = await made.context.storageState();
          await made.context.close();
          context = await owned.newContext({ viewport: DESKTOP, storageState: state, baseURL: BASE });
          page = await context.newPage();
          page.on("dialog", (dialog) => void dialog.accept());
        } else {
          page = (await as(browser, "manager")).page;
        }
        const dropped: string[] = [];
        for (let run = 1; run <= BURST_RUNS; run++) {
          const blank = [0, 1, 2].map(() => ({ model: M15, gb: 128, colour: "negro" }));
          const scans = [0, 1, 2].map((i) => ({ serial: serial(`B${engine === "webkit" ? "W" : "C"}${run}`, i + 1), imei: nextImei() }));
          await openPaste(page);
          await paste(page, blank.map(block).join("\n\n"));
          await settled(page);
          await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 0, catalog: 0, info: 3, invalid: 0 });
          const field = () => page.evaluate(() => (document.activeElement as HTMLElement | null)?.getAttribute("data-field") ?? "");
          const value = () => page.evaluate(() => (document.activeElement as HTMLInputElement).value);
          await button(page, "Resolver pendientes").click();
          for (let i = 0; i < 3; i++) {
            await expect.poll(field, { message: `lectura ${i + 1}: debe estar en la serie` }).toBe("serial");
            await page.keyboard.type(scans[i].serial, { delay: 0 });
            const gotSerial = await value();
            if (gotSerial !== scans[i].serial) dropped.push(`${engine} #${run} serie ${i + 1}: «${scans[i].serial}» -> «${gotSerial}»`);
            await page.keyboard.press("Enter");
            await expect.poll(field, { message: `tras la serie ${i + 1} va al IMEI` }).toBe("imei");
            await page.keyboard.type(scans[i].imei, { delay: 0 });
            const gotImei = await value();
            if (gotImei !== scans[i].imei) dropped.push(`${engine} #${run} IMEI ${i + 1}: «${scans[i].imei}» -> «${gotImei}»`);
            await page.keyboard.press("Enter");
            if (i < 2) await expect.poll(field).toBe("serial");
          }
        }
        expect(dropped, `caracteres perdidos con escritura instantánea (${BURST_RUNS} lecturas de 3 equipos)`).toEqual([]);
      } finally {
        await context?.close().catch(() => undefined);
        await owned?.close().catch(() => undefined);
      }
    });
  }

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("6 · respuesta perdida al confirmar: el reintento reutiliza la clave y el stock sube una vez", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const devices = [0, 1].map((i) => ({ model: M15, gb: 128, colour: "negro", serial: serial("LR", i + 1), imei: nextImei(), battery: 92 }));
    const before = articulos(`${M15} 128 GB Negro`)[0];
    const receiptsBefore = recibos();
    const watch = watchConfirm(page);
    let lost: { receipt_id: string } | null = null;
    let armed = true;
    await page.route(/receipts\/confirm/, async (route) => {
      if (armed && route.request().method() === "POST" && route.request().redirectedFrom() === null) {
        armed = false;
        const reached = await route.fetch(); // llega al servidor y se guarda
        lost = await reached.json();
        await route.abort("failed");         // ...pero el navegador nunca ve la respuesta
      } else {
        await route.continue();
      }
    });
    await openPaste(page);
    await paste(page, devices.map(block).join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 0 });
    await addButton(page).click();
    await toReview(page);
    await accept(page);
    await confirmButton(page).click();
    await expect(page.getByText(/Puede que la recepción ya se haya registrado/)).toBeVisible({ timeout: 30_000 });
    await shot(page, "chromium-1280x800", "s6", "respuesta-perdida");
    expect(lost, "el servidor no llegó a procesarla").not.toBeNull();
    expect(articulos(`${M15} 128 GB Negro`)[0].units).toHaveLength(before.units.length + 2);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    const reply = await answered;
    expect(reply.status()).toBe(200);
    const replayed = await reply.json();
    expect(replayed.replayed).toBe(true);
    expect(replayed.receipt_id).toBe(lost!.receipt_id);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    watch.stop();
    expect(watch.seen).toHaveLength(2);
    expect(watch.seen[1].key).toBe(watch.seen[0].key);
    expect(watch.seen[1].body).toEqual(watch.seen[0].body);
    const after = articulos(`${M15} 128 GB Negro`)[0];
    expect(after.units).toHaveLength(before.units.length + 2);
    expect(after.movements.filter((m) => m.type === "purchase_entry")).toHaveLength(before.movements.filter((m) => m.type === "purchase_entry").length + 1);
    expect(recibos()).toBe(receiptsBefore + 1);
    await page.unroute(/receipts\/confirm/);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("7 · memoria y privacidad: nada de la lista en almacenamiento, URL ni consola; salir pregunta", async ({ browser }) => {
    test.setTimeout(420_000);
    // Una sesión y una pestaña propias: el historial del navegador debe empezar limpio para probar atrás/adelante.
    const clean = await login(browser, RL_USERS.manager, RL_PASSWORD);
    const page = clean.page;
    page.on("dialog", (dialog) => void dialog.accept());
    await page.setViewportSize(DESKTOP);
    const devices = [0, 1].map((i) => ({ model: M15, gb: 128, colour: "negro", serial: serial("PV", i + 1), imei: nextImei(), battery: 87 }));
    const secrets = devices.flatMap((d) => [d.serial, d.imei, d.serial.toLowerCase()]);
    const logs: string[] = [];
    const onConsole = (msg: import("@playwright/test").ConsoleMessage) => logs.push(`${msg.type()}: ${msg.text()}`);
    const onError = (error: Error) => logs.push(`pageerror: ${error.message}`);
    page.on("console", onConsole);
    page.on("pageerror", onError);
    const urls: string[] = [];
    const onRequest = (req: Request) => urls.push(req.url());
    page.on("request", onRequest);

    await openPaste(page);
    await paste(page, devices.map(block).join("\n\n"));
    await settled(page);
    await (await openRow(page, 1)).locator('[data-field="notes"]').fill("nota de prueba");
    const traces = await browserTraces(page);
    for (const secret of secrets) expect(traces, `almacenamiento/URL guardó «${secret}»`).not.toContain(secret);
    expect(traces).not.toContain("nota de prueba");
    for (const url of urls) for (const secret of secrets) expect(decodeURIComponent(url), `una URL llevó «${secret}»`).not.toContain(secret);
    // Lo que viaja al catálogo no trae identificadores (el cuerpo de la búsqueda).
    page.off("request", onRequest);

    // «Atrás» y «adelante»: el paso 1 conserva la lista; el comienzo del flujo pregunta antes de salir.
    await page.goBack();
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible();
    await page.goForward();
    await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    await expect(button(page, /^Pegar una lista \(2 /)).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await expect(page.getByRole("alertdialog")).toContainText("Tienes una recepción sin confirmar");
    await shot(page, "chromium-1280x800", "s7", "salir-pregunta");
    await page.getByRole("button", { name: "Seguir aquí" }).click();
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" }).or(page.getByRole("heading", { name: "¿Qué llegó?" }))).toBeVisible();

    // Un enlace fuera del asistente pregunta antes; «Seguir aquí» no pierde la lista.
    await page.goto("/admin/inventory/receive", { waitUntil: "networkidle" }); // dispara beforeunload (aceptado) y vuelve vacío
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible({ timeout: 60_000 });
    await press(button(page, "Siguiente: artículos"), false);
    await press(button(page, /^Pegar una lista/), false);
    await expect(progress(page), "recargar vacía la lista").toHaveCount(0);
    await paste(page, block(devices[0]));
    await settled(page);
    await expect(progress(page)).toBeVisible();
    const sidebarLink = page.getByRole("navigation").getByRole("link", { name: /Inventario|Productos|Pedidos|Panel/ }).first();
    await sidebarLink.click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    await page.getByRole("button", { name: "Seguir aquí" }).click();
    await expect(progress(page)).toBeVisible();
    await expect(page).toHaveURL(/\/admin\/inventory\/receive/);
    await sidebarLink.click();
    await page.getByRole("button", { name: "Salir", exact: true }).click();
    await expect(page).not.toHaveURL(/\/admin\/inventory\/receive$/);

    // Ni la consola ni los errores de página llevaron datos de la lista.
    page.off("console", onConsole);
    page.off("pageerror", onError);
    for (const secret of secrets) expect(logs.join("\n"), `la consola llevó «${secret}»`).not.toContain(secret);
    expect(logs.filter((l) => l.startsWith("pageerror")), "errores de página").toEqual([]);

    // Cambiar de usuario en la MISMA pestaña no muestra el borrador del anterior. Va en una sesión propia: sale la sesión.
    const solo = await login(browser, RL_USERS.manager, RL_PASSWORD);
    const tab = solo.page;
    tab.on("dialog", (dialog) => void dialog.accept());
    await tab.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
    await press(button(tab, "Siguiente: artículos"), false);
    await press(button(tab, /^Pegar una lista/), false);
    await paste(tab, block(devices[1]));
    await settled(tab);
    // «crear artículo» vive en la pestaña de búsqueda: se abre y se pulsa ahí (conserva la lista en memoria).
    await press(button(tab, /^Buscar o escanear un artículo/), false);
    await tab.getByRole("link", { name: "No existe: crear artículo" }).click({ timeout: 15_000 });
    await expect(tab).toHaveURL(/\/admin\/products\/register/, { timeout: 60_000 });
    const goTo = (to: string) => tab.evaluate((target) => (window as unknown as { next: { router: { push: (to: string) => void } } }).next.router.push(target), to);
    // Control positivo: la MISMA persona que vuelve recupera su lista (así se sabe que el borrador sí se retuvo)...
    await goTo("/admin/inventory/receive");
    await expect(tab.getByText("Recuperamos tu lista de la recepción.")).toBeVisible({ timeout: 60_000 });
    await expect(button(tab, /^Pegar una lista \(1 /)).toBeVisible();
    // ...y otra vez a «crear artículo» (que vive en la pestaña de búsqueda) para retener el borrador antes de cambiar de persona.
    await press(button(tab, /^Buscar o escanear un artículo/), false);
    await tab.getByRole("link", { name: "No existe: crear artículo" }).click({ timeout: 15_000 });
    await expect(tab).toHaveURL(/\/admin\/products\/register/, { timeout: 60_000 });
    // Sale la sesión de la gestora por la pantalla de salida de la aplicación (navegación interna, sin recargar)...
    await tab.evaluate(() => (window as unknown as { next: { router: { push: (to: string) => void } } }).next.router.push("/auth/logout"));
    await tab.waitForURL(/\/auth/, { timeout: 60_000 }).catch(() => undefined);
    // ...y entra OTRA persona en la misma pestaña, también sin recargar.
    const signInAs = () => tab.evaluate(async ({ username, password }) => {
      const csrf = await fetch("/api/auth/csrf/", { credentials: "include" });
      void csrf;
      const token = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "";
      const login = await fetch("/api/auth/login/", {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRFToken": token },
        body: JSON.stringify({ username, password }),
      });
      return login.status;
    }, { username: RL_USERS.plain, password: RL_PASSWORD });
    let other = await signInAs();
    if (other === 429) { await tab.waitForTimeout(62_000); other = await signInAs(); } // límite de 5 inicios por minuto
    expect(other).toBe(200);
    await tab.evaluate(() => (window as unknown as { next: { router: { push: (to: string) => void } } }).next.router.push("/admin/inventory/receive"));
    await expect(tab.getByRole("heading").filter({ hasText: /¿De dónde viene y adónde llega\?|¿Qué llegó\?/ }).first()).toBeVisible({ timeout: 60_000 });
    await expect(tab.getByRole("heading", { name: "¿De dónde viene y adónde llega?" }), "el borrador del usuario anterior no debe saltar al paso 2").toBeVisible();
    await expect(tab.getByText("Recuperamos tu lista de la recepción.")).toHaveCount(0);
    await press(button(tab, "Siguiente: artículos"), false);
    await expect(button(tab, /^Pegar una lista \(\d+ /)).toHaveCount(0);
    expect(await main(tab).innerText()).not.toContain(devices[1].serial);
    await solo.context.close();
    await clean.context.close();
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("3b · dejar filas para otra recepción: el resto se confirma y lo dejado sobrevive a «Registrar otra recepción»", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const M32 = `iPhone 32 ${TAG}`;
    const ok = { model: M15, gb: 128, colour: "negro", serial: serial("PK", 1), imei: nextImei(), battery: 90 };
    const waiting = [0, 1].map((i) => ({ model: M32, gb: 64, colour: "rojo", serial: serial("PK", 2 + i), imei: nextImei(), battery: 80 }));
    await openPaste(page);
    await paste(page, [block(ok), ...waiting.map(block)].join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 1, catalog: 2, info: 0, invalid: 0 });
    await addButton(page).click();
    await expect(pageText(page, /1 equipo agregado a la recepción\. Quedan 2 filas en la lista/)).toBeVisible();
    await button(page, "Siguiente: revisar").click();
    await expect(page.getByRole("alert").filter({ hasText: /déjalas para otra recepción/ })).toBeVisible();
    await shot(page, "chromium-1280x800", "s3b", "bloqueado-con-opcion");
    await button(page, /^Dejar estas 2 filas para otra recepción/).click();
    await expect(page.getByRole("status").filter({ hasText: "2 filas en espera" })).toBeVisible();
    await shot(page, "chromium-1280x800", "s3b", "en-espera");
    await toReview(page);
    await expect(page.getByRole("status").filter({ hasText: /2 filas de la lista quedan en espera: no entran a esta recepción/ })).toBeVisible();
    await shot(page, "chromium-1280x800", "s3b", "revisar-con-espera");
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
    expect(articulos(M32)).toHaveLength(0);
    expect(articulos(`${M15} 128 GB Negro`)[0].units.map((u) => u.serial)).toContain(ok.serial);
    expect(articulos(`${M15} 128 GB Negro`)[0].units.map((u) => u.serial)).not.toContain(waiting[0].serial);
    // Las filas dejadas siguen en la página para la próxima recepción.
    await button(page, "Registrar otra recepción").click();
    await expect(page.getByRole("heading", { name: /¿De dónde viene y adónde llega\?|¿Qué llegó\?/ }).first()).toBeVisible();
    if (await page.getByRole("heading", { name: "¿Qué llegó?" }).count() === 0) await press(button(page, "Siguiente: artículos"), false);
    await expect(button(page, /^Pegar una lista \(2 en la lista\)/)).toBeVisible();
    await press(button(page, /^Pegar una lista/), false);
    await expect.poll(() => counters(page)).toEqual({ ready: 0, catalog: 2, info: 0, invalid: 0 });
    await shot(page, "chromium-1280x800", "s3b", "otra-recepcion");
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("3c · la misma combinación pegada dos veces: la segunda tanda se asocia sola, sin otra propuesta", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const M31 = `iPhone 31 ${TAG}`;
    const dev = (n: number): Dev => ({ model: M31, gb: 64, colour: "rojo", serial: serial("DP", n), imei: nextImei(), battery: 70 + n });
    const proposals: string[] = [];
    const onRequest = (req: Request) => { if (req.method() === "POST" && /catalog-proposal/.test(req.url()) && req.redirectedFrom() === null) proposals.push(req.url()); };
    page.on("request", onRequest);
    await openPaste(page);
    await paste(page, block(dev(1)));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 0, catalog: 1, info: 0, invalid: 0 });
    await approveEverything(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 1, catalog: 0, info: 0, invalid: 0 });
    const before = proposals.length;
    // Segunda tanda de la MISMA combinación: se asocia por lo que el catálogo ya respondió.
    await paste(page, [dev(2), dev(3)].map(block).join("\n\n"));
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 3, catalog: 0, info: 0, invalid: 0 });
    expect(proposals.length, "no hace falta otra propuesta").toBe(before);
    // Nada falta: ya no hay botón «Proponer lo que falta».
    await expect(page.getByRole("button", { name: "Proponer lo que falta" })).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Equipos por variante del catálogo" }).locator("> li")).toHaveCount(1);
    await shot(page, "chromium-1280x800", "s3c", "dos-tandas");
    await addButton(page).click();
    await toReview(page);
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    page.off("request", onRequest);
    const article = articulos(M31)[0];
    expect(article.units.map((u) => u.serial).sort()).toEqual([dev(1), dev(2), dev(3)].map((d) => d.serial!).sort());
    expect(article.movements.filter((m) => m.type === "purchase_entry")).toHaveLength(1);
    expect(article.published).toBe(false);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("3d · volver al paso 1 y regresar (botón y navegador), y pegar más: la lista y sus filas no se mezclan", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const devs = [1, 2, 3, 4, 5, 6].map((n) => ({ model: M15, gb: 128, colour: "negro", serial: serial("BK", n), imei: nextImei(), battery: 80 + n }));
    await openPaste(page);
    await paste(page, devs.slice(0, 2).map(block).join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 0 });
    // Atrás con el botón del asistente.
    await button(page, "Atrás").click();
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible();
    await press(button(page, "Siguiente: artículos"), false);
    await expect(button(page, /^Pegar una lista \(2 en la lista\)/)).toBeVisible();
    await press(button(page, /^Pegar una lista/), false);
    await expect.poll(() => counters(page)).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 0 });
    await paste(page, devs.slice(2, 4).map(block).join("\n\n"));
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 4, catalog: 0, info: 0, invalid: 0 });
    // Atrás y adelante con el navegador.
    await page.goBack();
    await expect(page.getByRole("heading", { name: "¿De dónde viene y adónde llega?" })).toBeVisible();
    await page.goForward();
    await expect(page.getByRole("heading", { name: "¿Qué llegó?" })).toBeVisible();
    await expect(button(page, /^Pegar una lista \(4 en la lista\)/)).toBeVisible();
    await press(button(page, /^Pegar una lista/), false);
    await paste(page, devs.slice(4, 6).map(block).join("\n\n"));
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 6, catalog: 0, info: 0, invalid: 0 });
    // Seis filas con seis números distintos y cada una con su serie (ningún identificador de fila repetido).
    const titles = await page.locator("details > summary").filter({ hasText: /^\s*Equipo \d+/ }).allInnerTexts();
    expect(titles.map((t) => t.match(/Equipo (\d+)/)![1])).toEqual(["1", "2", "3", "4", "5", "6"]);
    expect(new Set(titles.map((t) => t.replace(/\s+/g, " "))).size).toBe(6);
    const ids = await page.locator('[data-field="serial"]').evaluateAll((els) => els.map((el) => el.getAttribute("data-unit")));
    expect(new Set(ids).size, "cada fila tiene su identificador").toBe(6);
    expect(await page.locator('[data-field="serial"]').evaluateAll((els) => els.map((el) => (el as HTMLInputElement).value))).toEqual(devs.map((d) => d.serial));
    await shot(page, "chromium-1280x800", "s3d", "seis-filas");
    await addButton(page).click();
    await toReview(page);
    await accept(page);
    const answered = waitConfirmResponse(page);
    await confirmButton(page).click();
    expect((await answered).status()).toBe(201);
    const stored = articulos(`${M15} 128 GB Negro`)[0].units;
    for (const d of devs) expect(stored.find((u) => u.serial === d.serial)?.imei, d.serial).toBe(d.imei);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("3e · corregir color y modelo tecla por tecla: el foco no se pierde y se aplica al salir o con Enter", async () => {
    test.setTimeout(300_000);
    const { page } = await as(admin.context().browser()!, "manager");
    const a = { model: M15, gb: 128, colour: "rojo", serial: serial("TY", 1), imei: nextImei(), battery: 90 };
    const b = { model: `${M15}x`, gb: 128, colour: "negro", serial: serial("TY", 2), imei: nextImei(), battery: 91 };
    await openPaste(page);
    await paste(page, [a, b].map(block).join("\n\n"));
    await settled(page);
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 0, catalog: 2, info: 0, invalid: 0 });
    const activeLabel = () => page.evaluate(() => {
      const el = document.activeElement as HTMLInputElement | null;
      return { tag: el?.tagName ?? "", label: el?.labels?.[0]?.textContent ?? "", value: el?.value ?? "" };
    });

    // Color: «rojo» → «negro» escrito letra por letra; el foco sigue en el campo mientras se escribe.
    const first = await openRow(page, 1);
    await first.locator("summary").filter({ hasText: "Corregir modelo, capacidad, color o línea" }).click();
    const colour = first.getByLabel("Color", { exact: true });
    await colour.click();
    await page.keyboard.press("ControlOrMeta+A");
    for (const key of "negro") {
      await page.keyboard.press(key);
      expect(await activeLabel(), `tras «${key}»`).toMatchObject({ tag: "INPUT", label: "Color" });
    }
    expect((await activeLabel()).value).toBe("negro");
    await expect.poll(() => counters(page), { message: "no se aplica mientras se escribe" }).toEqual({ ready: 0, catalog: 2, info: 0, invalid: 0 });
    await page.keyboard.press("Enter");
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 1, catalog: 1, info: 0, invalid: 0 });
    await shot(page, "chromium-1280x800", "s3e", "color-corregido");

    // Modelo: «…x» → el nombre bueno, letra por letra, y se aplica al salir del campo con Tab.
    const second = await openRow(page, 2);
    await second.locator("summary").filter({ hasText: "Corregir modelo, capacidad, color o línea" }).click();
    const model = second.getByLabel("Modelo", { exact: true });
    await model.click();
    await page.keyboard.press("ControlOrMeta+A");
    for (const key of M15) {
      await page.keyboard.press(key === " " ? "Space" : key);
    }
    expect(await activeLabel()).toMatchObject({ tag: "INPUT", label: "Modelo", value: M15 });
    await page.keyboard.press("Tab");
    await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 2, catalog: 0, info: 0, invalid: 0 });
    // Las dos quedaron en el mismo grupo ya asociado y conservan lo suyo.
    await expect(page.getByRole("list", { name: "Equipos por variante del catálogo" }).locator("> li")).toHaveCount(1);
    expect(await page.locator('[data-field="serial"]').evaluateAll((els) => els.map((el) => (el as HTMLInputElement).value))).toEqual([a.serial, b.serial]);
  });

  // ───────────────────────────────────────────────────────────────────────────────────────────────────────────
  test("8 · otra empresa: no ve las variantes de la primera (la búsqueda responde «ninguna»)", async ({ browser }) => {
    test.setTimeout(120_000);
    const outsider = await as(browser, "other");
    const rows = [
      { key: "1", kind: "device", model: M15, capacityGb: 128, colour: "negro", line: "used" },
      { key: "2", kind: "device", model: MODELS.mixed, capacityGb: 256, colour: "azul", line: "new" },
      { key: "3", kind: "accessory", model: ACCESSORY, capacityGb: null, colour: "", line: "accessory" },
    ];
    const answer = await jsonCall<{ matches: Record<string, { status: string; options: unknown[] }> }>(outsider.context.request, "/admin/inventory/text-preview/match/", { candidates: rows });
    expect(answer.status).toBe(200);
    for (const key of ["1", "2", "3"]) {
      expect(answer.body.matches[key], `clave ${key}`).toMatchObject({ status: "none", options: [] });
    }
    // Y la misma consulta de la primera empresa SÍ las ve (el control del experimento).
    const own = await as(browser, "manager");
    const mine = await jsonCall<{ matches: Record<string, { status: string }> }>(own.context.request, "/admin/inventory/text-preview/match/", { candidates: rows });
    expect(mine.body.matches["1"].status).toBe("exact");
    expect(mine.body.matches["2"].status).toBe("exact");
    expect(mine.body.matches["3"].status).toBe("exact");
  });
});

// ── 9 · Ventanas: la ruta de aceptación, en pantallas de verdad ────────────────────────────────────────────────

type Env = { name: string; n: number; touch: boolean; kind: "chromium" | "webkit"; options: Parameters<Browser["newContext"]>[0] };

function profile(name: string): Parameters<Browser["newContext"]>[0] {
  const { defaultBrowserType: _ignored, ...rest } = devices[name];
  void _ignored;
  return rest;
}

const ENVS: Env[] = [
  { name: "chromium-1280x800", n: 21, touch: false, kind: "chromium", options: { viewport: DESKTOP } },
  { name: "chromium-768x1024", n: 22, touch: false, kind: "chromium", options: { viewport: { width: 768, height: 1024 } } },
  { name: "chromium-iphone13-390x844", n: 23, touch: true, kind: "chromium", options: profile("iPhone 13") },
  { name: "webkit-iphone13", n: 24, touch: true, kind: "webkit", options: profile("iPhone 13") },
];

/** Las pantallas clave de la lista pegada en un entorno: pegar, analizar, aprobar, revisar cada fila, agregar, revisar, confirmar. */
async function keyScreens(page: Page, env: Env) {
  const model = `iPhone ${env.n} ${TAG}`;
  const touch = env.touch;
  const big = ["Analizar y preparar", "Proponer lo que falta", /^Agregar \d+ equipos? listos?/, "Resolver pendientes", "Siguiente: revisar", "Confirmar recepción"];
  const devicesIn = [
    { model: M15, gb: 128, colour: "negro", serial: serial(`V${env.n}`, 1), imei: nextImei(), battery: 90 },
    { model, gb: 128, colour: "negro", serial: serial(`V${env.n}`, 2), imei: nextImei(), battery: 88 },
    { model, gb: 128, colour: "negro", serial: serial(`V${env.n}`, 3), imei: nextImei(), battery: 86 },
  ];
  await openPaste(page, touch);
  await shot(page, env.name, "s9", "pegar-vacio");
  await measure(page, env.name, "s9", "pegar-vacio", big);
  await expectFocusRing(page, env.name, `${env.name}: pantalla de pegar`);
  await paste(page, devicesIn.map(block).join("\n\n"), "Seminuevos", touch);
  await settled(page);
  await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 1, catalog: 2, info: 0, invalid: 0 });
  await shot(page, env.name, "s9", "analizado");
  await measure(page, env.name, "s9", "analizado", big);
  await approveEverything(page, touch, async () => {
    // Con la propuesta abierta: botones y casillas del panel de catálogo miden al menos 44 px (también a 390 px).
    await shot(page, env.name, "s9", "propuesta-abierta");
    await measure(page, env.name, "s9", "propuesta-abierta", [...big, "Crear lo aprobado", "Cancelar"]);
    const panel = page.getByRole("region", { name: "Proponer lo que falta" });
    const targets = panel.locator('button, input[type="checkbox"]');
    // Una casilla se toca por su etiqueta: se mide la etiqueta que la envuelve (la casilla suelta mide 20 px).
    const sizes = await targets.evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().width > 0)
      .map((el) => {
        const hit = el instanceof HTMLInputElement ? (el.closest("label") ?? el) : el;
        const box = hit.getBoundingClientRect();
        return { what: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 50), h: box.height, w: box.width };
      }));
    expect(sizes.length, "el panel muestra botones y casillas").toBeGreaterThan(3);
    for (const item of sizes) {
      expect(item.h, `${env.name}: «${item.what}» del panel de catálogo mide ${Math.round(item.w)}x${Math.round(item.h)} px`).toBeGreaterThanOrEqual(43.5);
    }
  });
  await settled(page);
  await expect.poll(() => counters(page), { timeout: 30_000 }).toEqual({ ready: 3, catalog: 0, info: 0, invalid: 0 });
  await expect(page.getByRole("button", { name: "Proponer lo que falta" }), "tras crear no queda el botón «Proponer»").toHaveCount(0);
  await shot(page, env.name, "s9", "propuesta-aprobada");
  await measure(page, env.name, "s9", "propuesta-aprobada", big);
  await press(row(page, 2).locator("summary").first(), touch);
  await shot(page, env.name, "s9", "fila-abierta");
  await measure(page, env.name, "s9", "fila-abierta", big);
  await press(addButton(page), touch);
  await expect(pageText(page, /3 equipos agregados a la recepción/)).toBeVisible();
  await shot(page, env.name, "s9", "agregado");
  await measure(page, env.name, "s9", "agregado", big);
  await toReview(page, touch);
  await shot(page, env.name, "s9", "revisar");
  await measure(page, env.name, "s9", "revisar", big);
  await accept(page, touch);
  await expectFocusRing(page, env.name, `${env.name}: pantalla de revisión`);
  const answered = waitConfirmResponse(page);
  await press(confirmButton(page), touch);
  expect((await answered).status()).toBe(201);
  await expect(page.getByRole("heading", { name: "Recepción registrada" })).toBeVisible({ timeout: 30_000 });
  await shot(page, env.name, "s9", "resultado");
  await measure(page, env.name, "s9", "resultado");
  const stored = articulos(TAG).flatMap((a) => a.units).map((u) => u.serial);
  for (const d of devicesIn) expect(stored, `${env.name}: falta ${d.serial}`).toContain(d.serial);
}

test.describe("recibir mercadería · pegar una lista · ventanas", () => {
  for (const env of ENVS) {
    test(`9 · ${env.name}: pantallas clave sin desborde, con objetivos táctiles y foco visibles`, async ({ browser }) => {
      test.setTimeout(420_000);
      let engine: Browser | null = null;
      let context: BrowserContext | null = null;
      try {
        if (env.kind === "webkit") {
          try { engine = await webkit.launch(); }
          catch (error) { test.skip(true, `WebKit no está instalado o no abre: ${String(error).slice(0, 120)}`); return; }
        } else {
          engine = env.name === "chromium-1280x800" ? browser : await chromium.launch();
        }
        // Una sola sesión: entra la gestora por la API en este motor y se abre un contexto con el perfil del entorno.
        const login_ = await login(engine, RL_USERS.manager, RL_PASSWORD);
        const state = await login_.context.storageState();
        await login_.context.close();
        context = await engine.newContext({ ...env.options, storageState: state, baseURL: BASE });
        const page = await context.newPage();
        page.on("dialog", (dialog) => void dialog.accept());
        await keyScreens(page, env);
      } finally {
        await context?.close().catch(() => undefined);
        if (engine && engine !== browser) await engine.close().catch(() => undefined);
      }
    });
  }
});
