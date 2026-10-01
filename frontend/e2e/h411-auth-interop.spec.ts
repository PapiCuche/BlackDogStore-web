import { execFileSync } from "node:child_process";
import path from "node:path";

import { test, expect, type BrowserContext, type Page, type Response } from "@playwright/test";

/**
 * H4.1.1 — la web entra por cookie a la API interna v1, en navegador real.
 *
 * EL DEFECTO. El panel consume `/api/v1/internal/<empresa>/…` —servicio técnico,
 * evidencias, notificaciones— y esa superficie sólo aceptaba Bearer. Con la
 * sesión de cookie perfectamente válida, todo respondía 401: la consola del
 * taller vacía, la campana muda, las fotos sin subir.
 *
 * SIN MOCKS DEL BACKEND. Cada petición llega al servidor de verdad por el mismo
 * proxy que usa la aplicación, con las cookies HttpOnly que puso el login y el
 * CSRF que exige. Un mock aquí probaría el mock: lo que se cambió es justo la
 * autenticación.
 *
 * LIMPIEZA. Lo que estas pruebas crean lleva la marca «[E2E]» (o un correo en
 * `e2e.invalid`) y `purge_e2e_data` lo borra antes y después. H4.1 dejó la base
 * de desarrollo llena de invitaciones de prueba; esto no repite eso. Requiere el
 * backend local: `E2E_BACKEND_DIR` si no está en `../backend`.
 *
 * LIMITADOR. El login admite 5 intentos por minuto por IP, y es una defensa
 * real. Seis cuentas lo alcanzan, así que la entrada espera en lugar de
 * desactivarlo.
 */

const SLUG = process.env.E2E_COMPANY_SLUG ?? "black-dog-store";
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const RUN = Date.now().toString(36);
const MARK = "[E2E]";
const INTERNAL = `/api/v1/internal/${SLUG}`;

type Cookies = Awaited<ReturnType<BrowserContext["cookies"]>>;
const sessions = new Map<string, Cookies>();

const shared: { orderId?: number; orderNumber?: string } = {};

/**
 * La respuesta FINAL de una petición, no su redirección.
 *
 * El proxy de Next responde 308 a las rutas con barra final y el navegador las
 * repite sin ella. La primera versión de esta prueba esperó a la 308 y la tomó
 * por la respuesta de la API — que además ya no termina en barra. Se filtra por
 * ESTADO y no por `redirectedTo()`: cuando llega la 308, la petición siguiente
 * todavía no existe y ese campo aún está vacío.
 */
function isRedirect(r: Response) {
  return r.status() >= 300 && r.status() < 400;
}

function finalResponse(fragment: string, method = "GET") {
  return (r: Response) =>
    r.url().includes(fragment) && r.request().method() === method && !isRedirect(r);
}

function purge() {
  execFileSync("python3", ["manage.py", "purge_e2e_data", "--company-slug", SLUG], {
    cwd: BACKEND_DIR,
    stdio: "pipe",
  });
}

/** Entra por la tarjeta de accesos de desarrollo DESDE la página actual de /auth. */
async function signInHere(page: Page, username: string) {
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  // La tarjeta pide sus cuentas al servidor DESPUÉS de pintar la página. Contarla
  // en cuanto cambia la URL la encuentra vacía y convierte el escenario en un
  // «skipped» silencioso, que se parece demasiado a un aprobado.
  await card.first().waitFor({ state: "visible", timeout: 15_000 }).catch(() => {});
  if ((await card.count()) === 0) {
    test.skip(true, "sin tarjeta de accesos de desarrollo: el backend no corre con DEBUG");
  }
  const row = card.locator("li").filter({ has: page.getByText(username, { exact: true }) });
  const use = row.getByRole("button", { name: "Usar cuenta" });
  await use.first().waitFor({ state: "visible", timeout: 15_000 }).catch(() => {});
  if ((await use.count()) === 0) {
    test.skip(true, `${username} no está sembrado: python manage.py seed_demo_users --company-slug ${SLUG}`);
  }
  await use.first().click();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (attempt === 2) throw new Error(`no se pudo iniciar sesión como ${username}`);
      await page.waitForTimeout(62_000);
    }
  }
}

async function signIn(page: Page, username: string) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  await signInHere(page, username);
  sessions.set(username, await page.context().cookies());
}

/** Reutiliza la sesión de una cuenta ya autenticada en esta ejecución. */
async function resume(context: BrowserContext, username: string) {
  const cookies = sessions.get(username);
  if (!cookies) throw new Error(`${username} no inició sesión antes en esta ejecución`);
  await context.addCookies(cookies);
}

/**
 * Una llamada desde la página, como la haría la aplicación: mismo origen,
 * cookies HttpOnly que JavaScript no ve, y el CSRF de la cookie legible.
 */
async function api(
  page: Page,
  method: string,
  url: string,
  body?: unknown,
  { csrf = method !== "GET" }: { csrf?: boolean } = {},
) {
  return page.evaluate(
    async ({ method, url, body, csrf }) => {
      const read = () => document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (csrf) {
        if (!read()) await fetch("/api/auth/csrf/", { credentials: "include" });
        const token = read();
        if (token) headers["X-CSRFToken"] = decodeURIComponent(token);
      }
      const send = () =>
        fetch(url, {
          method,
          credentials: "include",
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      // EL LIMITADOR SE RESPETA, NO SE ESQUIVA. En la suite completa las
      // pruebas anteriores gastan el cupo de peticiones del panel con la misma
      // cuenta, y el servidor responde 429 diciendo cuánto falta. Se espera eso
      // y se repite: el 429 se decide antes de procesar nada, así que repetir
      // no duplica ninguna escritura. El CSRF se evalúa ANTES que el limitador,
      // así que la prueba del 403 no cambia.
      let res = await send();
      for (let attempt = 0; attempt < 3 && res.status === 429; attempt += 1) {
        const detail = await res.text();
        const seconds = Number(res.headers.get("Retry-After") ?? detail.match(/(\d+)\s*segundo/)?.[1] ?? 5);
        await new Promise((resolve) => setTimeout(resolve, (seconds + 1) * 1000));
        res = await send();
      }
      let data: unknown = null;
      try {
        data = await res.json();
      } catch {
        // sin cuerpo
      }
      return { status: res.status, data: data as Record<string, unknown> };
    },
    { method, url, body, csrf },
  );
}

test.beforeAll(() => {
  purge();
});

test.afterAll(() => {
  purge();
});

test.describe("H4.1.1 · web ↔ v1 interno", () => {
  test.describe.configure({ mode: "serial" });

  test("B · el admin recibe una orden y la asigna, por cookie y con CSRF", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page, "dev_admin");
    // Sin `next`: quien trabaja en una empresa aterriza en el panel.
    await expect(page).toHaveURL(/\/admin$/);

    const context = await api(page, "GET", `${INTERNAL}/service/context/`);
    expect(context.status, "service/context por cookie").toBe(200);
    const branches = context.data.available_branches as { id: number }[];
    expect(branches.length).toBeGreaterThan(0);

    const customer = await api(page, "POST", "/api/admin/customers/", {
      customer_type: "person",
      first_name: "Prueba",
      last_name: `H411 ${RUN}`,
      notes: `${MARK} cliente de ${RUN}`,
    });
    expect(customer.status, JSON.stringify(customer.data)).toBe(201);

    const device = await api(page, "POST", `${INTERNAL}/service/devices/`, {
      customer_id: customer.data.id,
      device_type: "phone",
      brand: "Prueba",
      model: "H4.1.1",
      notes: `${MARK} equipo de ${RUN}`,
    });
    expect(device.status, JSON.stringify(device.data)).toBe(201);

    const intake = {
      customer_id: customer.data.id,
      device_id: device.data.id,
      branch_id: branches[0].id,
      reported_issue: `${MARK} H4.1.1 ${RUN}: no enciende`,
    };

    // F · LA MISMA MUTACIÓN SIN CSRF NO PASA. La cookie viaja sola en cualquier
    // petición del navegador; el token de CSRF es lo que prueba que la pidió
    // esta aplicación y no una página ajena.
    const forged = await api(page, "POST", `${INTERNAL}/service/orders/`, intake, { csrf: false });
    expect(forged.status, "una mutación por cookie sin CSRF debe fallar").toBe(403);
    expect(String(forged.data?.detail)).toContain("CSRF");

    const order = await api(page, "POST", `${INTERNAL}/service/orders/`, intake);
    expect(order.status, JSON.stringify(order.data)).toBe(201);
    shared.orderId = order.data.id as number;
    shared.orderNumber = order.data.number as string;

    const options = await api(page, "GET", `${INTERNAL}/service/orders/${shared.orderId}/assignment/`);
    expect(options.status).toBe(200);
    const technician = (options.data.candidates as { id: number; name: string }[])
      .find((c) => c.name.includes("dev_technician"));
    expect(technician, "dev_technician no figura entre los técnicos elegibles").toBeTruthy();

    const assigned = await api(page, "POST", `${INTERNAL}/service/orders/${shared.orderId}/assignment/`, {
      technician_id: technician!.id,
    });
    expect(assigned.status, JSON.stringify(assigned.data)).toBe(200);
  });

  test("A · el técnico descubre el panel y ve su reparación", async ({ page }) => {
    test.setTimeout(240_000);
    expect(shared.orderId, "el escenario B no dejó orden").toBeTruthy();
    await signIn(page, "dev_technician");
    await expect(page).toHaveURL(/\/admin$/);

    // Sin escribir la URL: la tienda ofrece «Control interno».
    await page.goto("/", { waitUntil: "networkidle" });
    const entry = page.getByRole("link", { name: "Control interno" }).first();
    await expect(entry, "un técnico con membresía debe ver el acceso al panel").toBeVisible();
    await entry.click();
    await expect(page).toHaveURL(/\/admin$/);

    const refreshes: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/auth/refresh/")) refreshes.push(r.url());
    });

    const contextResponse = page.waitForResponse(finalResponse(`${INTERNAL}/service/context`));
    const unreadResponse = page.waitForResponse(finalResponse(`${INTERNAL}/notifications/unread-count`));
    await page
      .locator('nav[aria-label="Módulos del control interno"] a[href="/admin/service"]')
      .first()
      .click();
    expect((await contextResponse).status(), "service/context").toBe(200);
    expect((await unreadResponse).status(), "notifications/unread-count").toBe(200);

    await expect(page.getByRole("heading", { name: "Órdenes de servicio" })).toBeVisible();
    // «Mis reparaciones» es el filtro por defecto y lo resuelve el servidor.
    const row = page.locator(`a[href="/admin/service/orders/${shared.orderId}"]`).first();
    await expect(row, "la orden asignada no aparece en Mis reparaciones").toBeVisible({ timeout: 20_000 });
    expect(refreshes, "tormenta de refresh: la sesión estaba recién iniciada").toEqual([]);
  });

  test("G · una foto real sube por multipart y aparece en la galería", async ({ page, context }) => {
    test.setTimeout(180_000);
    await resume(context, "dev_technician");
    await page.goto(`/admin/service/orders/${shared.orderId}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: shared.orderNumber! })).toBeVisible({ timeout: 20_000 });

    const png = await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 480;
      const ctx = canvas.getContext("2d")!;
      for (let i = 0; i < 40; i += 1) {
        ctx.fillStyle = `hsl(${i * 9}, 70%, 50%)`;
        ctx.fillRect(i * 16, 0, 16, 480);
      }
      ctx.fillStyle = "#ffffff";
      ctx.font = "48px sans-serif";
      ctx.fillText("H4.1.1", 200, 260);
      return canvas.toDataURL("image/png").split(",")[1];
    });

    await page.locator("select").filter({ has: page.locator('option[value="intake"]') }).selectOption("intake");
    await page.locator('input[type="file"][accept*="image/"]').setInputFiles({
      name: `h411-${RUN}.png`,
      mimeType: "image/png",
      buffer: Buffer.from(png, "base64"),
    });

    const uploaded = page.waitForResponse(
      finalResponse(`/service/orders/${shared.orderId}/evidence`, "POST"),
    );
    await page.getByRole("button", { name: "Subir" }).click();
    const response = await uploaded;
    expect(response.status(), await response.text()).toBe(201);
    // EL DEFECTO DE FORMDATA: con `application/json` forzado no había boundary
    // y el servidor respondía «Adjunta una imagen.». `allHeaders()` y no
    // `headers()`: el Content-Type de un FormData lo pone el navegador al
    // enviar, y las cabeceras provisionales no lo traen.
    const sent = await response.request().allHeaders();
    expect(sent["content-type"] ?? "").toMatch(/^multipart\/form-data; boundary=/);

    await expect(page.getByText(/Ingreso · 1/)).toBeVisible({ timeout: 20_000 });
  });

  test("H · notificaciones: contador, bandeja y marcar leída por cookie", async ({ page, context }) => {
    test.setTimeout(180_000);
    await resume(context, "dev_technician");
    await page.goto("/admin/notifications", { waitUntil: "networkidle" });

    const before = await api(page, "GET", `${INTERNAL}/notifications/unread-count/`);
    expect(before.status).toBe(200);
    const unread = Number(before.data.unread);
    expect(unread, "la asignación del escenario B debía notificar al técnico").toBeGreaterThan(0);

    const mark = page.getByRole("button", { name: "Marcar leída" }).first();
    await expect(mark).toBeVisible({ timeout: 20_000 });
    const read = page.waitForResponse(
      (r) =>
        /\/notifications\/\d+\/read\/?$/.test(new URL(r.url()).pathname) &&
        r.request().method() === "POST" &&
        !isRedirect(r),
    );
    await mark.click();
    expect((await read).status(), "marcar leída por cookie, con CSRF").toBe(200);

    const after = await api(page, "GET", `${INTERNAL}/notifications/unread-count/`);
    expect(Number(after.data.unread)).toBe(unread - 1);
    // Por el MENSAJE y no por `role="alert"`: el anunciador de rutas de Next
    // también tiene ese rol y está en todas las páginas.
    await expect(page.getByText(/No se pudo marcar/)).toHaveCount(0);
  });

  test("C · un cliente puro se queda en la tienda", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page, "dev_customer");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("link", { name: "Pedidos" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Control interno" })).toHaveCount(0);

    await page.goto("/admin", { waitUntil: "networkidle" });
    await expect(page.getByText("Sin acceso interno")).toBeVisible({ timeout: 20_000 });

    // La API tampoco: una empresa en la que no trabajas no existe para ti.
    const probe = await api(page, "GET", `${INTERNAL}/context/`);
    expect(probe.status).toBe(404);
  });

  test("D · quien es cliente y técnico conserva las dos superficies", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page, "dev_customer_technician");
    await expect(page).toHaveURL(/\/admin$/);

    await page.goto("/", { waitUntil: "networkidle" });
    await expect(page.getByRole("link", { name: "Pedidos" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Control interno" }).first()).toBeVisible();

    await page.goto("/orders", { waitUntil: "networkidle" });
    await expect(page).toHaveURL(/\/orders$/);

    const contextResponse = page.waitForResponse(finalResponse(`${INTERNAL}/service/context`));
    await page.goto("/admin/service");
    expect((await contextResponse).status()).toBe(200);
    await expect(page.getByRole("heading", { name: "Órdenes de servicio" })).toBeVisible();
  });

  test("E · el master elige empresa y el servicio responde para ella", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page, "dev_master");
    await expect(page).toHaveURL(/\/admin$/);

    await page.getByRole("button", { name: /Selecciona una empresa|Black Dog/i }).first().click();
    const option = page.getByRole("option").first();
    await expect(option).toBeVisible({ timeout: 10_000 });
    await option.click();

    const contextResponse = page.waitForResponse(finalResponse(`${INTERNAL}/service/context`));
    await page.goto("/admin/service");
    expect((await contextResponse).status(), "el master, por cookie, en la empresa elegida").toBe(200);
  });

  test("F · una invitación sobrevive al login gracias a un next seguro", async ({ page, context, browser, baseURL }) => {
    test.setTimeout(240_000);
    await resume(context, "dev_admin");
    await page.goto("/admin", { waitUntil: "networkidle" });

    // Por el mismo helper que el resto: respeta el limitador y manda CSRF.
    const areas = await api(page, "GET", "/api/admin/areas/");
    const areaRows = (areas.data?.results ?? []) as { id: number; company: number }[];
    const company = areaRows[0]?.company;
    const roles = await api(page, "GET", "/api/admin/roles/");
    const roleRows = ((roles.data?.results ?? []) as { id: number; company: number }[])
      .filter((role) => role.company === company);
    const invitation = await api(page, "POST", "/api/admin/staff/invitations/", {
      company,
      email: `h411.${RUN}@e2e.invalid`,
      first_name: "Invitación",
      last_name: "H411",
      role: roleRows[0]?.id,
      area: areaRows[0]?.id,
      branch_access_mode: "all",
    });
    expect(invitation.status, JSON.stringify(invitation.data)).toBe(201);
    const token = invitation.data.debug_invitation_token as string | undefined;
    if (!token) test.skip(true, "sin enlace en claro: el backend no corre con DEBUG");

    // Otra persona, sin sesión, abre la invitación.
    const visitor = await browser.newContext({ baseURL });
    const guest = await visitor.newPage();
    await guest.goto(`/invitacion?token=${encodeURIComponent(token!)}`, { waitUntil: "networkidle" });
    const toAuth = guest.getByRole("link", { name: /Crear cuenta|Iniciar sesión/ }).first();
    await expect(toAuth).toBeVisible({ timeout: 20_000 });
    await toAuth.click();
    await guest.waitForURL((url) => url.pathname === "/auth" && url.searchParams.has("next"));
    await guest.waitForLoadState("networkidle");

    await signInHere(guest, "dev_customer");
    await expect(guest, "tras el login no volvió a la invitación").toHaveURL(/\/invitacion\?token=/);
    await expect(guest.getByRole("heading", { name: /Te han invitado a/i })).toBeVisible({ timeout: 20_000 });
    await visitor.close();
  });
});
