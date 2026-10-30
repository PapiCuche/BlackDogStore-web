import { mkdirSync, writeFileSync } from "node:fs";
import { chromium, webkit, devices, test, expect, type Browser, type BrowserContext, type BrowserContextOptions, type Locator, type Page, type Request } from "@playwright/test";
import { TAG, sembrar, ficha, type Seed } from "./inventory-first-support";
import { limpiarRecepcion, recibos } from "./inventory-receive-support";
import { apiBase } from "./inventory-first-support";

/**
 * INV-OPS-01 — verificación del «atrás» del navegador en «Recibir mercadería», con interacciones REALES.
 *
 * Los mismos 8 casos en tres entornos: Chromium de escritorio (1280 px), Chromium con contexto móvil táctil (iPhone 13,
 * 390 px, `hasTouch`) y WebKit con el descriptor del iPhone 13. Cada caso anota «esperado / observado / PASA» y la
 * captura; el informe se escribe al final en `$BACKNAV_OUT` (carpeta; por omisión el directorio de capturas).
 * Sin simulacros: lo único interceptado es la RESPUESTA de «confirm» (caso 5), cortada después de que el servidor la procesó.
 */

const OUT = process.env.BACKNAV_OUT
  ?? "/private/tmp/claude-501/-Users-cmaucorp-Desktop-BlackDogStore-web/7a0ba6be-01d7-46a0-a74d-d9c68236ac71/scratchpad/backnav";

type Env = { name: string; engine: "chromium" | "webkit"; opts: BrowserContextOptions };
const ENVS: Env[] = [
  { name: "d1280", engine: "chromium", opts: { viewport: { width: 1280, height: 800 } } },
  { name: "m390", engine: "chromium", opts: { ...devices["iPhone 13"], viewport: { width: 390, height: 844 } } },
  { name: "webkit", engine: "webkit", opts: { ...devices["iPhone 13"] } },
];
/** Por omisión los dos Chromium; WebKit (requiere su navegador instalado) se pide con `BACKNAV_ENVS=webkit` o `=d1280,m390,webkit`. */
const ONLY = (process.env.BACKNAV_ENVS ?? "d1280,m390").split(",").filter(Boolean);

type Row = { env: string; case: string; label: string; expected: string; observed: string; pass: boolean; shot: string };
const ROWS: Row[] = [];

let base: Seed;

type W = { env: Env; browser: Browser; ctx: BrowserContext; page: Page; touch: boolean; errors: string[] };

// ── ayudas ─────────────────────────────────────────────────────────────────────────────────────────────────────

const HEADINGS = [/¿De dónde viene y adónde llega\?/, /¿Qué llegó\?/, /Revisa antes de registrar/, /Recepción registrada/];
const HUB = /\/admin\/inventory\/operations$/;

async function press(w: W, target: Locator) {
  if (w.touch) await target.tap(); else await target.click();
}
const button = (w: W, name: string | RegExp) => w.page.getByRole("button", { name });
const dialog = (w: W) => w.page.getByRole("alertdialog");

async function stepNow(w: W): Promise<number> {
  for (let i = 0; i < 4; i++) if (await w.page.getByRole("heading", { name: HEADINGS[i] }).count()) return i + 1;
  return 0;
}
async function hist(w: W) {
  return w.page.evaluate(() => {
    const st = (history.state ?? {}) as { rcv?: number; pos?: number };
    return { url: location.pathname + location.search, len: history.length, rcv: st.rcv ?? null, pos: st.pos ?? null };
  });
}
const settle = (w: W, ms = 700) => w.page.waitForTimeout(ms);
const shotName = (w: W, c: string, name: string) => `${w.env.name}-c${c}-${name}`;
async function shot(w: W, c: string, name: string) {
  mkdirSync(`${OUT}/shots`, { recursive: true });
  const file = shotName(w, c, name);
  await w.page.screenshot({ path: `${OUT}/shots/${file}.png`, fullPage: false });
  return `${file}.png`;
}
function rec(w: W, c: string, label: string, expected: string, observed: string, pass: boolean, shotFile = "") {
  ROWS.push({ env: w.env.name, case: c, label, expected, observed, pass, shot: shotFile });
  expect.soft(pass, `[${w.env.name}] caso ${c} · ${label}\n  esperado: ${expected}\n  observado: ${observed}`).toBe(true);
}

async function openWizardFromHub(w: W) {
  await w.page.goto("/admin", { waitUntil: "networkidle" });
  await w.page.goto("/admin/inventory/operations", { waitUntil: "networkidle" });
  const card = w.page.getByRole("list", { name: "Tareas del inventario" }).getByRole("link", { name: /Recibir mercadería/ });
  await expect(card).toBeVisible({ timeout: 60_000 });
  await press(w, card);
  await expect(w.page.getByRole("heading", { name: HEADINGS[0] })).toBeVisible({ timeout: 60_000 });
}

async function toItems(w: W) {
  await press(w, button(w, "Siguiente: artículos"));
  await expect(w.page.getByRole("heading", { name: HEADINGS[1] })).toBeVisible();
}
async function addLine(w: W, name: string, qty = "1") {
  await w.page.locator("#receive-product").fill(name);
  await w.page.getByRole("option").filter({ hasText: name }).first().click({ timeout: 30_000 });
  const card = w.page.getByRole("article", { name, exact: true });
  await expect(card).toBeVisible();
  await card.getByLabel("Cantidad", { exact: true }).fill(qty);
}
async function toReview(w: W) {
  await press(w, button(w, "Siguiente: revisar"));
  await expect(w.page.getByRole("heading", { name: HEADINGS[2] })).toBeVisible();
  await expect(w.page.getByLabel(/Revisé los datos y quiero registrar/)).toBeVisible({ timeout: 30_000 });
}
async function accept(w: W) { await w.page.getByLabel(/Revisé los datos y quiero registrar/).check(); }
const confirmBtn = (w: W) => button(w, "Confirmar recepción");

/** Del paso 1 al resultado con una línea de cantidad. */
async function receiveOne(w: W, qty = "1") {
  await toItems(w);
  await addLine(w, base.cableName, qty);
  await toReview(w);
  await accept(w);
  await press(w, confirmBtn(w));
  await expect(w.page.getByRole("heading", { name: HEADINGS[3] })).toBeVisible({ timeout: 30_000 });
}

async function back(w: W) { await w.page.goBack().catch(() => null); await settle(w); }
async function forward(w: W) { await w.page.goForward().catch(() => null); await settle(w); }

function watchConfirm(w: W, page = w.page) {
  const seen: { key: string }[] = [];
  const onRequest = (req: Request) => {
    if (req.method() === "POST" && /receipts\/confirm/.test(req.url()) && req.redirectedFrom() === null) seen.push({ key: req.headers()["idempotency-key"] ?? "" });
  };
  page.on("request", onRequest);
  return { seen, stop: () => page.off("request", onRequest) };
}

async function login(browser: Browser, opts: BrowserContextOptions): Promise<BrowserContext> {
  const ctx = await browser.newContext({ baseURL: process.env.E2E_BASE_URL, ...opts });
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await ctx.request.post(`${apiBase()}/auth/login/`, { data: { username: "dev_admin", password: "Demo123!" } });
    if (res.status() === 200) { await ctx.request.get(`${apiBase()}/auth/csrf/`); return ctx; }
    if (res.status() !== 429 || attempt === 2) throw new Error(`login ${res.status()}`);
    await new Promise((r) => setTimeout(r, 62_000));
  }
  throw new Error("login");
}

async function newWorld(env: Env): Promise<W> {
  const browser = env.engine === "webkit" ? await webkit.launch() : await chromium.launch();
  const ctx = await login(browser, env.opts);
  const page = await ctx.newPage();
  page.setDefaultTimeout(25_000); // una acción que no puede hacerse falla pronto, con su localizador
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
  page.on("dialog", (d) => void d.accept()); // «¿Salir de la página?» nativo (recarga con la lista armada)
  return { env, browser, ctx, page, touch: Boolean(env.opts.hasTouch), errors };
}

// ── los casos ──────────────────────────────────────────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

test.beforeAll(() => { test.setTimeout(120_000); base = sembrar(); });
test.afterAll(async () => {
  mkdirSync(OUT, { recursive: true });
  writeFileSync(`${OUT}/report-${ONLY.join("+") || "all"}.json`, JSON.stringify(ROWS, null, 1));
  if (process.env.E2E_KEEP === "1") return;
  const result = limpiarRecepcion();
  expect(result.left, `la limpieza no pudo borrar: ${result.left.join(" | ")}`).toEqual([]);
});

for (const env of ENVS.filter((e) => ONLY.includes(e.name))) {
  test.describe(`atrás · ${env.name}`, () => {
    let w: W;
    test.beforeAll(async () => { test.setTimeout(240_000); w = await newWorld(env); });
    test.afterAll(async () => {
      // WebKit informa «Load failed» / «access control checks» de las peticiones que una navegación (cierre de sesión,
      // recarga) interrumpe, y del visor de errores de `next dev`: ruido de navegación, no un error de la aplicación.
      const real = w.errors.filter((e) => !/Load failed|access control checks/.test(e));
      if (real.length !== w.errors.length) ROWS.push({ env: env.name, case: "8", label: "peticiones interrumpidas por la navegación (WebKit)", expected: "ruido ignorable", observed: `${w.errors.length - real.length} avisos «Load failed»`, pass: true, shot: "" });
      expect.soft(real, `errores de página sin capturar en ${env.name}`).toEqual([]);
      await w.ctx.close();
      await w.browser.close();
    });

    // 1 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("1 · camino normal: 1→2→3, atrás 3→2→1 sin diálogo, adelante", async () => {
      test.setTimeout(240_000);
      await openWizardFromHub(w);
      await toItems(w);
      await addLine(w, base.cableName, "2");
      await toReview(w);
      const s3 = await shot(w, "1", "paso3");
      await back(w);
      rec(w, "1", "atrás desde el paso 3", "paso 2, sin diálogo", `paso ${await stepNow(w)}, diálogo=${await dialog(w).count()}`, (await stepNow(w)) === 2 && (await dialog(w).count()) === 0, await shot(w, "1", "atras-a-paso2"));
      await expect(w.page.getByRole("article", { name: base.cableName, exact: true }), "la lista sigue ahí").toBeVisible();
      await back(w);
      rec(w, "1", "atrás desde el paso 2", "paso 1, sin diálogo", `paso ${await stepNow(w)}, diálogo=${await dialog(w).count()}`, (await stepNow(w)) === 1 && (await dialog(w).count()) === 0, await shot(w, "1", "atras-a-paso1"));
      await forward(w);
      rec(w, "1", "adelante a 2", "paso 2 con su lista", `paso ${await stepNow(w)}`, (await stepNow(w)) === 2 && (await w.page.getByRole("article", { name: base.cableName, exact: true }).count()) === 1, await shot(w, "1", "adelante-paso2"));
      await forward(w);
      const review = (await w.page.getByLabel(/Revisé los datos/).count()) > 0 ? "revisión lista" : (await w.page.getByText("Revisar de nuevo").count()) ? "pide «Revisar de nuevo»" : "revisando";
      rec(w, "1", "adelante a 3", "paso 3 (revisión lista o pide revisar de nuevo)", `paso ${await stepNow(w)}, ${review}`, (await stepNow(w)) === 3 && (await dialog(w).count()) === 0, await shot(w, "1", "adelante-paso3"));
      void s3;
    });

    // 2 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("2 · atrás desde el paso 1: con lista pregunta (ambas salidas); sin lista sale", async () => {
      test.setTimeout(240_000);
      // 2a con líneas escritas
      await openWizardFromHub(w);
      await toItems(w);
      await addLine(w, base.idleName, "3");
      await back(w);
      rec(w, "2a", "atrás 2→1 con lista", "paso 1 sin diálogo", `paso ${await stepNow(w)}, diálogo=${await dialog(w).count()}`, (await stepNow(w)) === 1 && (await dialog(w).count()) === 0);
      await back(w);
      const shown = (await dialog(w).count()) === 1;
      const sFile = await shot(w, "2a", "dialogo-salir");
      rec(w, "2a", "atrás desde el paso 1 con lista", "diálogo «Tienes una recepción sin confirmar» (Seguir aquí / Salir)", `diálogo=${shown}, url=${(await hist(w)).url}`, shown && (await dialog(w).innerText()).includes("Tienes una recepción sin confirmar") && !HUB.test((await hist(w)).url), sFile);
      if (shown) await press(w, w.page.getByRole("button", { name: "Seguir aquí" }));
      await settle(w);
      const stay = await hist(w);
      rec(w, "2a", "«Seguir aquí»", "se queda en el paso 1, sin diálogo, lista intacta", `paso ${await stepNow(w)}, url=${stay.url}, diálogo=${await dialog(w).count()}`, (await stepNow(w)) === 1 && (await dialog(w).count()) === 0 && /receive/.test(stay.url), await shot(w, "2a", "tras-seguir-aqui"));
      await toItems(w);
      rec(w, "2a", "la lista sobrevive a «Seguir aquí»", "la tarjeta del artículo sigue en el paso 2", `tarjetas=${await w.page.getByRole("article", { name: base.idleName, exact: true }).count()}`, (await w.page.getByRole("article", { name: base.idleName, exact: true }).count()) === 1);
      await back(w); await back(w);
      const again = (await dialog(w).count()) === 1;
      if (again) await press(w, w.page.getByRole("button", { name: "Salir" }));
      await w.page.waitForURL(HUB, { timeout: 15_000 }).catch(() => null);
      rec(w, "2a", "«Salir»", "sale al centro de operaciones", `url=${(await hist(w)).url}, diálogo=${await dialog(w).count()}`, again && HUB.test((await hist(w)).url), await shot(w, "2a", "tras-salir"));

      // 2b sin líneas
      await openWizardFromHub(w);
      await back(w);
      rec(w, "2b", "atrás desde el paso 1 sin lista", "sale al hub sin diálogo", `url=${(await hist(w)).url}, diálogo=${await dialog(w).count()}`, HUB.test((await hist(w)).url) && (await dialog(w).count()) === 0, await shot(w, "2b", "sin-lista"));
      await openWizardFromHub(w);
      await toItems(w);
      await back(w);
      await back(w);
      rec(w, "2b", "atrás 2→1→hub con el paso 2 vacío", "sale al hub sin diálogo", `url=${(await hist(w)).url}, diálogo=${await dialog(w).count()}`, HUB.test((await hist(w)).url) && (await dialog(w).count()) === 0);
    });

    // 3 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("3 · tras confirmar: atrás sale al hub de un salto, sin reabrir pasos ni crecer el historial", async () => {
      test.setTimeout(240_000);
      await openWizardFromHub(w);
      await receiveOne(w);
      const before = await hist(w);
      const sDone = await shot(w, "3", "resultado");
      const seen: string[] = [];
      await w.page.evaluate(() => {
        const log: string[] = [];
        (window as unknown as { __steps: string[] }).__steps = log;
        new MutationObserver(() => {
          const h = [...document.querySelectorAll("h2")].map((x) => x.id).filter((id) => id.startsWith("step-"));
          if (h.length) log.push(h.join(","));
        }).observe(document.body, { childList: true, subtree: true });
      });
      await back(w);
      await w.page.waitForURL(HUB, { timeout: 15_000 }).catch(() => null);
      const after = await hist(w);
      const flashed = await w.page.evaluate(() => ((window as unknown as { __steps?: string[] }).__steps ?? []).join("|")).catch(() => "(documento nuevo)");
      seen.push(flashed);
      rec(w, "3", "un «atrás» desde el resultado", "va al hub (de un salto)", `url=${after.url} (antes ${before.url}, entradas ${before.len}→${after.len})`, HUB.test(after.url), await shot(w, "3", "tras-atras-1"));
      rec(w, "3", "no reabre pasos 1–3 ni parpadean", "ningún encabezado de paso 1–3 apareció", `pasos vistos tras el atrás: «${flashed}», pasos visibles ahora=${await stepNow(w)}`, (await stepNow(w)) === 0 && !/step-(origin|items|review)/.test(flashed));
      await back(w);
      const second = await hist(w);
      rec(w, "3", "segundo «atrás»", "sale a la página anterior al hub (/admin), sin pasos", `url=${second.url}, pasos=${await stepNow(w)}, entradas=${second.len}`, /^\/admin$/.test(second.url) && (await stepNow(w)) === 0, await shot(w, "3", "tras-atras-2"));
      rec(w, "3", "el historial no crece con los «atrás»", `entradas ≤ ${before.len}`, `entradas=${second.len}`, second.len <= before.len);
      void sDone;
    });

    // 4 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("4 · «Registrar otra recepción»: pila sana", async () => {
      test.setTimeout(240_000);
      await openWizardFromHub(w);
      await receiveOne(w);
      const done = await hist(w);
      await press(w, button(w, "Registrar otra recepción"));
      await settle(w, 900);
      const restarted = await hist(w);
      rec(w, "4", "tras «Registrar otra recepción»", "paso 2 vacío; las entradas no crecen respecto al resultado", `paso ${await stepNow(w)}, url=${restarted.url}, entradas ${done.len}→${restarted.len}, rcv=${restarted.rcv}, pos=${restarted.pos}`, (await stepNow(w)) === 2 && restarted.len <= done.len && (await w.page.getByRole("article").count()) === 0, await shot(w, "4", "otra-recepcion"));
      await back(w);
      rec(w, "4", "atrás desde el nuevo paso 2", "paso 1 sin diálogo", `paso ${await stepNow(w)}, diálogo=${await dialog(w).count()}`, (await stepNow(w)) === 1 && (await dialog(w).count()) === 0);
      await forward(w);
      await addLine(w, base.cableName, "1");
      await toReview(w);
      await accept(w);
      await press(w, confirmBtn(w));
      await expect(w.page.getByRole("heading", { name: HEADINGS[3] })).toBeVisible({ timeout: 30_000 });
      const second = await hist(w);
      await back(w);
      await w.page.waitForURL(HUB, { timeout: 15_000 }).catch(() => null);
      rec(w, "4", "la segunda recepción también sale de un salto", "hub", `url=${(await hist(w)).url}, pasos=${await stepNow(w)} (resultado tenía rcv=${second.rcv}, pos=${second.pos})`, HUB.test((await hist(w)).url) && (await stepNow(w)) === 0, await shot(w, "4", "salida-2a-recepcion"));
    });

    // 5 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("5 · confirmación AMBIGUA: bloqueos, aviso fuerte, reintento con la misma clave y desbloqueo", async () => {
      test.setTimeout(300_000);
      const before = ficha(base.cable).stock;
      const receiptsBefore = recibos();
      const watch = watchConfirm(w);
      let armed = true;
      await w.page.route(/receipts\/confirm/, async (route) => {
        if (armed && route.request().method() === "POST" && route.request().redirectedFrom() === null) {
          armed = false;
          await route.fetch(); // el servidor la procesa...
          await route.abort("failed"); // ...y la respuesta se pierde
        } else await route.continue();
      });
      await openWizardFromHub(w);
      await toItems(w);
      await addLine(w, base.cableName, "2");
      await toReview(w);
      await accept(w);
      await press(w, confirmBtn(w));
      await expect(w.page.getByText(/Puede que la recepción ya se haya registrado/)).toBeVisible({ timeout: 30_000 });
      const panel = await shot(w, "5", "panel-ambiguo");
      rec(w, "5", "estado tras perder la respuesta", "el servidor guardó +2; el panel ofrece reintentar", `stock ${before}→${ficha(base.cable).stock}`, ficha(base.cable).stock === before + 2, panel);
      rec(w, "5", "«Atrás» del asistente", "desactivado (no se puede editar)", `disabled=${await button(w, "Atrás").isDisabled()}`, await button(w, "Atrás").isDisabled());
      const h0 = await hist(w);
      await back(w);
      const h1 = await hist(w);
      rec(w, "5", "«atrás» del navegador", "se queda en el paso 3 (no retrocede)", `paso ${await stepNow(w)}, url=${h1.url}, entradas ${h0.len}→${h1.len}`, (await stepNow(w)) === 3 && /receive/.test(h1.url), await shot(w, "5", "atras-bloqueado"));
      await back(w); await back(w);
      rec(w, "5", "«atrás» repetido ×3", "sigue en el paso 3; sin salir", `paso ${await stepNow(w)}, url=${(await hist(w)).url}`, (await stepNow(w)) === 3 && /receive/.test((await hist(w)).url));
      const crumb = w.page.getByRole("navigation", { name: "Ruta de navegación" }).getByRole("link", { name: "Inventario" });
      await press(w, crumb);
      const strong = (await dialog(w).count()) === 1 && (await dialog(w).innerText()).includes("Hay una confirmación sin respuesta");
      rec(w, "5", "enlace de la página (ruta de navegación)", "aviso FUERTE «Hay una confirmación sin respuesta»", `diálogo fuerte=${strong}, url=${(await hist(w)).url}`, strong && /receive/.test((await hist(w)).url), await shot(w, "5", "aviso-fuerte"));
      if (strong) await press(w, w.page.getByRole("button", { name: "Seguir aquí" }));
      await settle(w);
      rec(w, "5", "«Seguir aquí»", "se queda en el paso 3", `paso ${await stepNow(w)}, diálogo=${await dialog(w).count()}`, (await stepNow(w)) === 3 && (await dialog(w).count()) === 0);
      await press(w, button(w, "Reintentar confirmación"));
      await expect(w.page.getByRole("heading", { name: HEADINGS[3] })).toBeVisible({ timeout: 30_000 });
      watch.stop();
      const sameKey = watch.seen.length === 2 && watch.seen[0].key === watch.seen[1].key && /^rcv-/.test(watch.seen[0].key);
      rec(w, "5", "reintento", "misma clave, «ya estaba registrada», +2 UNA vez", `claves=${watch.seen.map((s) => s.key.slice(0, 12)).join(",")}, stock ${before}→${ficha(base.cable).stock}, recibos +${recibos() - receiptsBefore}`, sameKey && ficha(base.cable).stock === before + 2 && recibos() === receiptsBefore + 1 && (await w.page.getByText(/ya estaba registrada/).count()) > 0, await shot(w, "5", "reintento-repetido"));
      await w.page.unroute(/receipts\/confirm/);

      // Desbloqueo: la petición muere ANTES de llegar al servidor; la persona revisa el historial y edita.
      const stock2 = ficha(base.cable).stock;
      const watch2 = watchConfirm(w);
      let cut = true;
      await w.page.route(/receipts\/confirm/, async (route) => {
        if (cut && route.request().method() === "POST" && route.request().redirectedFrom() === null) { cut = false; await route.abort("failed"); }
        else await route.continue();
      });
      await openWizardFromHub(w);
      await toItems(w);
      await addLine(w, base.cableName, "1");
      await toReview(w);
      await accept(w);
      await press(w, confirmBtn(w));
      await expect(w.page.getByText(/Puede que la recepción ya se haya registrado/)).toBeVisible({ timeout: 30_000 });
      await press(w, button(w, "Revisé el historial y no se registró nada, quiero editar"));
      await settle(w);
      rec(w, "5", "«Revisé el historial…»", "habilita «Atrás» y deja editar", `Atrás disabled=${await button(w, "Atrás").isDisabled()}`, !(await button(w, "Atrás").isDisabled()), await shot(w, "5", "desbloqueado"));
      await press(w, button(w, "Atrás"));
      await expect(w.page.getByRole("heading", { name: HEADINGS[1] })).toBeVisible();
      await w.page.getByRole("article", { name: base.cableName, exact: true }).getByLabel("Cantidad", { exact: true }).fill("3");
      await toReview(w);
      await accept(w);
      await press(w, confirmBtn(w));
      await expect(w.page.getByRole("heading", { name: HEADINGS[3] })).toBeVisible({ timeout: 30_000 });
      watch2.stop();
      rec(w, "5", "tras editar: otra clave y +3", "clave nueva y el stock sube exactamente 3", `claves=${watch2.seen.map((s) => s.key.slice(0, 12)).join(",")}, stock ${stock2}→${ficha(base.cable).stock}`, watch2.seen.length === 2 && watch2.seen[0].key !== watch2.seen[1].key && ficha(base.cable).stock === stock2 + 3);
      await w.page.unroute(/receipts\/confirm/);
    });

    // 6 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("6 · enlace directo en una pestaña NUEVA: completar y pulsar atrás repetidamente", async () => {
      test.setTimeout(240_000);
      const state = await w.ctx.storageState();
      const ctx2 = await w.browser.newContext({ baseURL: process.env.E2E_BASE_URL, ...env.opts, storageState: state });
      const page2 = await ctx2.newPage();
      page2.on("dialog", (d) => void d.accept());
      const v: W = { ...w, ctx: ctx2, page: page2 };
      await page2.goto("/admin/inventory/receive", { waitUntil: "networkidle" });
      await expect(page2.getByRole("heading", { name: HEADINGS[0] })).toBeVisible({ timeout: 60_000 });
      const start = await hist(v);
      await receiveOne(v);
      const done = await hist(v);
      await shot(v, "6", "resultado");
      const trail: string[] = [`inicio: entradas=${start.len}`, `resultado: rcv=${done.rcv} pos=${done.pos} entradas=${done.len}`];
      let reopened = false;
      let lastUrl = done.url;
      for (let i = 1; i <= 6; i++) {
        await page2.goBack().catch(() => null);
        await page2.waitForTimeout(600);
        const h = await hist(v);
        const step = await stepNow(v);
        if (step >= 1 && step <= 3) reopened = true;
        trail.push(`atrás ${i}: url=${h.url} paso=${step} rcv=${h.rcv} pos=${h.pos} entradas=${h.len} diálogo=${await dialog(v).count()}`);
        lastUrl = h.url;
      }
      const sEnd = await shot(v, "6", "tras-6-atras");
      const stillResult = (await stepNow(v)) === 4;
      rec(w, "6", "6 «atrás» seguidos tras completar desde un enlace directo", "no reabre los pasos 1–3 (OBSERVACIÓN: sin página anterior el usuario puede quedarse en el resultado)", `${trail.join(" | ")} ; al final url=${lastUrl}, resultado visible=${stillResult}`, !reopened, sEnd);
      rec(w, "6", "¿atrapado? (informativo)", "el botón «Volver al centro de operaciones» sigue disponible como salida", `enlace visible=${await page2.getByRole("link", { name: "Volver al centro de operaciones" }).count()}`, (await page2.getByRole("link", { name: "Volver al centro de operaciones" }).count()) > 0 || !stillResult);
      await ctx2.close();
    });

    // 7 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("7 · recargar a mitad del flujo y luego atrás/adelante", async () => {
      test.setTimeout(240_000);
      await openWizardFromHub(w);
      await toItems(w);
      await addLine(w, base.cableName, "4");
      await toReview(w);
      const beforeReload = await hist(w);
      await w.page.reload({ waitUntil: "networkidle" });
      await settle(w, 900);
      const afterReload = await hist(w);
      const stepAfter = await stepNow(w);
      rec(w, "7", "recarga en el paso 3", "vuelve al paso 1 y NO conserva la lista (nada en el navegador)", `paso ${stepAfter}, tarjetas=${await w.page.getByRole("article").count()}, rcv=${afterReload.rcv}, entradas ${beforeReload.len}→${afterReload.len}`, stepAfter === 1 && (await w.page.getByRole("article").count()) === 0, await shot(w, "7", "tras-recarga"));
      const seq: string[] = [];
      let ok = true;
      for (const action of ["atrás", "atrás", "adelante", "adelante", "adelante"] as const) {
        if (action === "atrás") await back(w); else await forward(w);
        const h = await hist(w);
        const s = await stepNow(w);
        seq.push(`${action}→url=${h.url},paso=${s},rcv=${h.rcv},diálogo=${await dialog(w).count()}`);
        // Nunca un paso 3 o 4 vacío (sin lista): sería un estado imposible.
        if (s >= 3) ok = false;
      }
      rec(w, "7", "atrás, atrás, adelante ×3 tras recargar", "sólo pasos 1–2 o el hub; nunca un paso 3/4 vacío ni errores", seq.join(" | "), ok, await shot(w, "7", "tras-navegar"));
    });

    // 8 ─────────────────────────────────────────────────────────────────────────────────────────────────────
    test("8 · borrador y otra persona en la misma pestaña: no se filtra", async () => {
      test.setTimeout(420_000);

      /** Sale de la sesión por la tienda (botón «Salir» o menú móvil) y entra como `username` por la tarjeta de desarrollo. */
      async function switchUser(username: string) {
        await w.page.waitForURL((u) => !u.pathname.startsWith("/admin"), { timeout: 30_000 }).catch(() => null);
        // El aviso de cookies de la tienda tapa el menú móvil: se resuelve como lo haría una persona.
        const consent = w.page.locator('section[aria-labelledby="consent-title"]');
        if (await consent.isVisible().catch(() => false)) await press(w, consent.getByRole("button").first());
        if (w.touch || (w.env.opts.viewport?.width ?? 1280) < 1024) {
          await press(w, w.page.getByRole("button", { name: /Abrir menú/ }));
          await press(w, w.page.getByRole("button", { name: "Cerrar sesión" }));
        } else {
          await press(w, w.page.getByRole("button", { name: "Salir", exact: true }));
        }
        await w.page.waitForURL((u) => u.pathname === "/", { timeout: 30_000 }).catch(() => null);
        await settle(w, 1500);
        await w.page.goto("/auth", { waitUntil: "networkidle" });
        const card = w.page.locator("section").filter({ hasText: "Accesos de desarrollo" });
        await press(w, card.locator("li").filter({ hasText: username }).getByRole("button", { name: "Usar cuenta" }));
        for (let attempt = 0; attempt < 3; attempt++) {
          await press(w, w.page.getByRole("button", { name: /iniciar sesión/i }).first());
          try { await w.page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 12_000 }); break; }
          catch { if (attempt === 2) throw new Error(`no se pudo iniciar sesión como ${username}`); await w.page.waitForTimeout(62_000); }
        }
      }
      async function emptyWizard(who: string, shotName: string) {
        await w.page.goto("/admin/inventory/operations", { waitUntil: "networkidle" });
        await press(w, w.page.getByRole("list", { name: "Tareas del inventario" }).getByRole("link", { name: /Recibir mercadería/ }));
        await expect(w.page.getByRole("heading", { name: HEADINGS[0] })).toBeVisible({ timeout: 60_000 });
        const note = await w.page.locator("#receive-notes").inputValue();
        const doc = await w.page.locator("#receive-doc").inputValue();
        await toItems(w);
        const cards = await w.page.getByRole("article").count();
        const text = await w.page.locator("main").innerText();
        rec(w, "8", `${who} abre el asistente en la misma pestaña`, "paso 1 y 2 vacíos: ni el borrador vivo ni el guardado se filtran", `tarjetas=${cards}, observaciones='${note}', documento='${doc}', contiene el cable=${text.includes(base.cableName)}`, cards === 0 && !text.includes(base.cableName) && note === "" && doc === "", await shot(w, "8", shotName));
      }

      // A · borrador GUARDADO en memoria («No existe: crear artículo»), salida por la tienda y otra persona.
      await openWizardFromHub(w);
      await toItems(w);
      await addLine(w, base.cableName, "7");
      await w.page.locator("a", { hasText: "No existe: crear artículo" }).first().click();
      await w.page.waitForURL(/products\/register/, { timeout: 30_000 });
      rec(w, "8", "«crear artículo» no pregunta (guarda el borrador)", "va a registrar artículo sin diálogo", `url=${(await hist(w)).url}, diálogo=${await dialog(w).count()}`, (await dialog(w).count()) === 0);
      // «Volver a la tienda» no existe por debajo de 640 px (lo oculta la barra): allí se abre la tienda por la dirección.
      const storeLink = w.page.getByRole("link", { name: "Volver a la tienda" }).first();
      if (await storeLink.isVisible()) await press(w, storeLink); else await w.page.goto("/", { waitUntil: "networkidle" });
      await switchUser("dev_inventory");
      await emptyWizard("dev_inventory (tras borrador guardado)", "otra-persona-A");

      // B · borrador VIVO: «Volver a la tienda» pregunta; se sale, se cierra la sesión y vuelve la primera persona.
      await expect(w.page.getByRole("heading", { name: HEADINGS[1] })).toBeVisible(); // el asistente vacío ya está en el paso 2
      await addLine(w, base.cableName, "9");
      const exitLink = (await storeLink.isVisible())
        ? storeLink
        : w.page.getByRole("navigation", { name: "Ruta de navegación" }).getByRole("link", { name: "Inventario" });
      await press(w, exitLink);
      const asked = (await dialog(w).count()) === 1;
      rec(w, "8", "enlace de salida con borrador vivo (tienda o ruta de navegación)", "pregunta antes de salir", `diálogo=${asked}`, asked, await shot(w, "8", "pregunta-salida"));
      if (asked) await press(w, w.page.getByRole("button", { name: "Salir" }));
      if (asked) await w.page.waitForURL((u) => u.pathname !== "/admin/inventory/receive", { timeout: 30_000 }); // «Salir» navega
      if (/^\/admin/.test(new URL(w.page.url()).pathname)) await w.page.goto("/", { waitUntil: "networkidle" });
      await switchUser("dev_admin");
      await emptyWizard("dev_admin (tras borrador vivo de otra persona)", "otra-persona-B");
    });
  });
}

export { TAG };
