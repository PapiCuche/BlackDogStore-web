import { test, expect, type BrowserContext, type Page } from "@playwright/test";

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";

/**
 * AUTH-C — the idle session against the REAL backend, in Chromium and WebKit.
 *
 * The backend runs with a short idle limit so this takes minutes, not hours:
 *
 *   AUTH_SESSION_IDLE_SECONDS=60 AUTH_SESSION_ACTIVITY_GRANULARITY=5
 *
 * so the warning comes at 50 s (the last sixth) and the session ends at 60 s.
 * Every case skips, SAYING WHY, when the backend is running the 30-minute
 * default: waiting half an hour is not a test.
 *
 * Playwright's clicks and key presses are trusted input, like a person's:
 * that is what lets «Continuar sesión» count and a background poll not.
 */

const IDLE_LIMIT_S = 60;
/** Through the app's own proxy: that is where the browser's session cookies live. */
const SESSION_API = "/api";

async function idleTimeout(page: Page): Promise<number> {
  const res = await page.request.get(`${SESSION_API}/auth/session/`);
  if (!res.ok()) return 0;
  return (await res.json()).idle_timeout_s as number;
}

async function signIn(page: Page, account = "dev_admin") {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  // The card loads its accounts after the page paints (h411-auth-interop, note
  // 3), and right after a session expired WebKit sometimes paints it late: wait,
  // and reload once, before concluding the environment has none.
  for (let attempt = 0; attempt < 2 && (await card.locator("li").count()) === 0; attempt++) {
    await card.locator("li").first().waitFor({ timeout: 20_000 }).catch(() => {});
    if ((await card.locator("li").count()) === 0) await page.reload({ waitUntil: "networkidle" });
  }
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  const use = card.locator("li").filter({ hasText: account }).getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) test.skip(true, `${account} no existe: siembra con seed_demo_users`);
  await use.click();
  // 5 inicios por minuto y por IP: se espera la ventana, como las demás pruebas.
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 15_000 });
      break;
    } catch {
      if (attempt === 2) throw new Error("no se pudo iniciar sesión: el limitador no cedió");
      await page.waitForTimeout(62_000);
    }
  }
}

async function requireShortIdle(page: Page) {
  const limit = await idleTimeout(page);
  test.skip(limit !== IDLE_LIMIT_S, `el backend corre con ${limit} s de inactividad; arráncalo con AUTH_SESSION_IDLE_SECONDS=${IDLE_LIMIT_S}`);
}

test.describe.configure({ mode: "serial" });

test("a los 50 s sin actividad avisa con cuenta atrás, y «Continuar sesión» la renueva", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  await requireShortIdle(page);
  await page.goto("/admin", { waitUntil: "networkidle" });

  const dialog = page.getByRole("alertdialog");
  await expect(dialog, "el aviso no apareció").toBeVisible({ timeout: 70_000 });
  await expect(dialog).toContainText("Tu sesión está por cerrarse");
  await expect(dialog).toContainText(/0:\d\d/);

  await dialog.getByRole("button", { name: "Continuar sesión" }).click();
  await expect(dialog).toBeHidden();

  const status = await (await page.request.get(`${SESSION_API}/auth/session/`)).json();
  expect(status.idle_remaining_s, "«Continuar» no renovó la sesión").toBeGreaterThan(45);
});

test("sin actividad, a los 60 s la pantalla interna se va al login con el motivo, y Atrás no la devuelve", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  await requireShortIdle(page);
  await page.goto("/admin", { waitUntil: "networkidle" });

  await page.waitForURL(/\/auth\?reason=idle/, { timeout: 90_000 });
  await expect(page.getByText(/Tu sesión se cerró por inactividad/)).toBeVisible();

  const status = await page.request.get(`${SESSION_API}/auth/session/`);
  expect(status.status(), "el servidor no dio la sesión por terminada").toBe(401);

  await page.goBack();
  await page.waitForURL((u) => u.pathname.startsWith("/auth"), { timeout: 30_000 });
});

test("dos pestañas comparten la sesión: «Continuar» en una quita el aviso de la otra", async ({ context }: { context: BrowserContext }) => {
  test.setTimeout(240_000);
  const first = await context.newPage();
  await signIn(first);
  await requireShortIdle(first);
  const second = await context.newPage();
  await first.goto("/admin", { waitUntil: "networkidle" });
  await second.goto("/admin", { waitUntil: "networkidle" });

  await expect(first.getByRole("alertdialog")).toBeVisible({ timeout: 70_000 });
  await expect(second.getByRole("alertdialog")).toBeVisible({ timeout: 15_000 });

  await first.getByRole("button", { name: "Continuar sesión" }).click();

  await expect(first.getByRole("alertdialog")).toBeHidden();
  await expect(second.getByRole("alertdialog"), "la otra pestaña no se enteró").toBeHidden({ timeout: 10_000 });
  expect(second.url()).toContain("/admin");
});

test("caja: la venta se registró pero la respuesta se perdió y la sesión caducó — al volver no se cobra dos veces", async ({ page }) => {
  test.setTimeout(420_000);
  const catalog = await page.request.get(`${API}/products/?page_size=50`);
  const body = await catalog.json();
  const products = (Array.isArray(body) ? body : (body.results ?? [])) as { name: string; price: string; inventory: number }[];
  const candidates = products.filter((p) => Number(p.price) > 0 && p.inventory >= 2);
  if (!candidates.length) test.skip(true, "el catálogo no tiene ningún artículo con stock suficiente");

  await signIn(page);
  await requireShortIdle(page);
  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
  const search = page.getByPlaceholder(/por nombre o c/i).first();
  await expect(search, "el punto de venta no cargó").toBeVisible({ timeout: 30_000 });

  let inBasket = false;
  let sold = "";
  for (const candidate of candidates.slice(0, 8)) {
    sold = candidate.name;
    await search.fill(candidate.name.slice(0, 12));
    await page.waitForTimeout(2500);
    const hit = page.locator("button:not([disabled])").filter({ hasText: candidate.name.slice(0, 12) })
      .filter({ hasText: /\d+\s*disp\./ }).first();
    if ((await hit.count()) === 0) continue;
    await hit.click();
    await page.waitForTimeout(1500);
    if ((await page.getByText(/Falta identificar/).count()) === 0) { inBasket = true; break; }
    await page.getByRole("button", { name: `Quitar ${candidate.name}`, exact: true }).click();
  }
  if (!inBasket) test.skip(true, "el catálogo no tiene productos sin serie con stock en esta sucursal");

  await page.getByRole("checkbox").first().check();
  await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
  const cash = page.locator("input[inputmode='decimal'], input[type='number']").last();
  if (await cash.count()) await cash.fill("99999");

  // The sale REACHES the server and is made; its answer never reaches the page.
  let key = "";
  let lost = false;
  await page.route("**/admin/pos/sales/**", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    key = (route.request().postDataJSON() as { idempotency_key: string }).idempotency_key;
    await route.fetch();
    await route.abort("failed");
    lost = true;
  });
  await page.getByRole("button", { name: /^cobrar$/i }).click();
  await expect.poll(() => lost, { timeout: 30_000 }).toBe(true);
  await page.unroute("**/admin/pos/sales/**");
  const marked = await page.evaluate(() => window.sessionStorage.getItem("pos-sale-in-flight"));
  expect(marked, "la caja no guardó la venta en curso").toContain(key);

  // Nobody touches anything: the session ends and the till goes to the login.
  await page.waitForURL(/\/auth\?reason=idle/, { timeout: 120_000 });
  await signIn(page);
  await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });

  const notice = page.getByRole("alert").filter({ hasText: "Venta anterior sin confirmar" });
  await expect(notice).toBeVisible({ timeout: 30_000 });
  await expect(notice).toContainText(/sí se registró/);
  await expect(page.getByRole("button", { name: /^cobrar$/i })).toBeDisabled();

  const lookup = await page.request.get(`${SESSION_API}/admin/pos/sales/by-key/?key=${encodeURIComponent(key)}`);
  const answer = await lookup.json();
  expect(answer.found, "la venta no estaba en el servidor").toBe(true);
  await expect(notice).toContainText(`#${answer.order_id}`);

  await notice.getByRole("button", { name: "Entendido" }).click();
  await expect(notice).toBeHidden();
  const again = await page.evaluate(() => window.sessionStorage.getItem("pos-sale-in-flight"));
  expect(again, "la marca de la venta en curso no se olvidó").toBeNull();

  // A normal sale afterwards: its mark lives only while it is in flight.
  await search.fill(sold.slice(0, 12));
  await page.waitForTimeout(2500);
  await page.locator("button:not([disabled])").filter({ hasText: sold.slice(0, 12) })
    .filter({ hasText: /\d+\s*disp\./ }).first().click();
  await page.getByRole("checkbox").first().check();
  await page.getByLabel("Tipo de comprobante").selectOption("sales_note");
  if (await cash.count()) await cash.fill("99999");
  await page.getByRole("button", { name: /^cobrar$/i }).click();
  await expect(page.getByText(/Venta registrada/i)).toBeVisible({ timeout: 40_000 });
  expect(await page.evaluate(() => window.sessionStorage.getItem("pos-sale-in-flight"))).toBeNull();
});

test("una respuesta tardía de la sesión anterior no cierra la nueva, en ninguna pestaña", async ({ context }: { context: BrowserContext }) => {
  test.setTimeout(240_000);
  const first = await context.newPage();
  await signIn(first);
  await requireShortIdle(first);

  // Tab B opens the checkout; its «who am I» request is held: an answer of the
  // OLD session that will arrive after a NEW sign-in.
  const second = await context.newPage();
  let release: (() => Promise<void>) | null = null;
  await second.route("**/api/auth/me**", async (route) => {
    if (release) return route.continue();
    release = () => route.fulfill({
      status: 401,
      contentType: "application/json",
      headers: { "X-Session-Expired": "1" },
      body: JSON.stringify({ detail: "Credenciales inválidas.", code: "session_expired" }),
    });
  });
  await second.goto("/checkout");
  await expect.poll(() => release !== null, { timeout: 30_000 }).toBe(true);

  // A new session in tab A: sign out, sign in again.
  await first.goto("/auth", { waitUntil: "networkidle" });
  await first.getByRole("button", { name: /cerrar sesión/i }).first().click();
  await first.waitForURL((u) => u.pathname === "/", { timeout: 15_000 });
  await signIn(first);
  await first.goto("/admin", { waitUntil: "networkidle" });

  await release!();
  await second.waitForTimeout(5_000);

  expect(first.url(), "la pestaña A se cerró por una respuesta vieja").toContain("/admin");
  await expect(second.getByRole("alertdialog")).toHaveCount(0);
  const status = await first.request.get(`${SESSION_API}/auth/session/`);
  expect(status.status(), "la sesión nueva no sigue viva").toBe(200);
});

test("entre pestañas, un «terminó» de un inicio de sesión anterior no cierra la sesión de ahora", async ({ context }: { context: BrowserContext }) => {
  test.setTimeout(180_000);
  const first = await context.newPage();
  await signIn(first);
  await requireShortIdle(first);
  await first.goto("/admin", { waitUntil: "networkidle" });
  const second = await context.newPage();
  await second.goto("/", { waitUntil: "networkidle" });

  const generation = await second.evaluate(() => Number(window.localStorage.getItem("bd.session.generation") ?? "0"));
  expect(generation, "el inicio de sesión no dejó su generación").toBeGreaterThan(0);

  // An «ended» from BEFORE this sign-in, as a tab that missed it would send it.
  await second.evaluate((old) => {
    new BroadcastChannel("bd-session").postMessage({ type: "ended", reason: "idle", generation: old, sentAt: Date.now() });
  }, generation - 1);
  await first.waitForTimeout(3_000);
  expect(first.url(), "una señal vieja cerró la sesión nueva").toContain("/admin");

  // The same message from THIS sign-in does end it: the guard is about age, not deafness.
  await second.evaluate((current) => {
    new BroadcastChannel("bd-session").postMessage({ type: "ended", reason: "idle", generation: current, sentAt: Date.now() });
  }, generation);
  await first.waitForURL(/\/auth\?reason=idle/, { timeout: 15_000 });
});
