import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page, type Response } from "@playwright/test";

/**
 * SVC-FUNC-01 — del mostrador al taller, en un navegador de verdad.
 *
 * QUÉ PRUEBA ESTO QUE NADA MÁS PRUEBA
 * -----------------------------------
 * Que quien atiende la caja recibe un equipo SIN salir del punto de venta, elige
 * al técnico por su nombre, y que ese técnico encuentra la orden en «Mis
 * reparaciones» y la abre. Y que el técnico puede registrar un cobro sobre una
 * reparación aprobada, pero no reversarlo.
 *
 * SIN MOCKS. Las peticiones llegan al backend por el proxy de la aplicación, con
 * las cookies del login. Lo que deciden los permisos lo decide el servidor: los
 * botones sólo lo reflejan.
 *
 * DATOS
 * -----
 *  · El cliente, el equipo y la orden que crea el mostrador llevan la marca
 *    «[E2E]» y `purge_e2e_data` los borra antes y después: esa orden no pasa de
 *    la recepción.
 *  · El cobro NO puede hacerse sobre una orden desechable — la limpieza se niega,
 *    con razón, a borrar una orden con cotización o pagos. Usa la reparación que
 *    siembra `seed_demo_users --e2e-fixtures`: aprobada, asignada a
 *    `dev_technician`, con saldo. Cada corrida le cobra 1.00; cuando se salda, la
 *    siguiente siembra abre otra.
 *
 * No desactiva ni modifica ninguna cuenta: no repite E2E-02.
 */

const SLUG = process.env.E2E_COMPANY_SLUG ?? "black-dog-store";
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? path.resolve(process.cwd(), "../backend");
const RUN = Date.now().toString(36);
const MARK = "[E2E]";
const INTERNAL = `/api/v1/internal/${SLUG}`;
const TECHNICIAN = "dev_technician";
const FIXTURE_CUSTOMER = "Cobro E2E";

const shared: { orderId?: number; orderNumber?: string } = {};

function finalResponse(fragment: string, method = "GET") {
  return (r: Response) =>
    r.url().includes(fragment) && r.request().method() === method
    && !(r.status() >= 300 && r.status() < 400);
}

function purge() {
  execFileSync("python3", ["manage.py", "purge_e2e_data", "--company-slug", SLUG], {
    cwd: BACKEND_DIR,
    stdio: "pipe",
  });
}

async function signIn(page: Page, username: string) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
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
  // El limitador son 5 intentos por minuto y por IP. Se espera la ventana.
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

/** Una llamada desde la página, como la haría la aplicación: cookies y CSRF. */
async function api(page: Page, method: string, url: string, body?: unknown) {
  return page.evaluate(
    async ({ method, url, body }) => {
      const read = () => document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1];
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (method !== "GET") {
        if (!read()) await fetch("/api/auth/csrf/", { credentials: "include" });
        const token = read();
        if (token) headers["X-CSRFToken"] = decodeURIComponent(token);
      }
      const response = await fetch(url, {
        method, credentials: "include", headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data: any = null; // eslint-disable-line @typescript-eslint/no-explicit-any
      try { data = await response.json(); } catch { /* sin cuerpo */ }
      return { status: response.status, data };
    },
    { method, url, body },
  );
}

/**
 * El desplegable de un campo del formulario de recepción.
 *
 * El `<select>` vive DENTRO de su `<label>`, así que el texto de la etiqueta
 * incluye el de sus opciones y `getByLabel(…, { exact: true })` no la encuentra.
 */
function picker(page: Page, label: string) {
  return page.locator("label").filter({ hasText: new RegExp(`^${label}`) }).locator("select");
}

test.beforeAll(() => {
  purge();
});

test.afterAll(() => {
  purge();
});

test.describe("SVC-FUNC-01 · servicio técnico desde la caja", () => {
  test.describe.configure({ mode: "serial" });

  test("la caja recibe un equipo y elige al técnico por su nombre", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page, "dev_sales");

    // El cliente y su equipo existen antes de llegar al mostrador. Los crea la
    // misma cuenta de ventas: su rol administra clientes y equipos.
    const customer = await api(page, "POST", "/api/admin/customers/", {
      customer_type: "person",
      first_name: "Prueba",
      last_name: `SVCPOS ${RUN}`,
      notes: `${MARK} cliente de ${RUN}`,
    });
    expect(customer.status, JSON.stringify(customer.data)).toBe(201);
    const device = await api(page, "POST", `${INTERNAL}/service/devices/`, {
      customer_id: customer.data.id,
      device_type: "phone",
      brand: "Prueba",
      model: `SVCPOS ${RUN}`,
      notes: `${MARK} equipo de ${RUN}`,
    });
    expect(device.status, JSON.stringify(device.data)).toBe(201);

    const orders = await api(page, "GET", "/api/admin/orders/?page_size=1");

    await page.goto("/admin/sales/pos", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Servicio técnico" }).click();
    await expect(page.getByText("Nueva orden de servicio")).toBeVisible();

    await page.getByLabel("Buscar por nombre o documento").fill(`SVCPOS ${RUN}`);
    await page.getByRole("button", { name: "Buscar cliente" }).click();
    const customerPicker = picker(page, "Cliente");
    await expect(customerPicker.locator(`option[value="${customer.data.id}"]`)).toHaveCount(1);
    await customerPicker.selectOption(String(customer.data.id));

    const devicePicker = picker(page, "Equipo");
    await expect(devicePicker.locator(`option[value="${device.data.id}"]`)).toHaveCount(1);
    await devicePicker.selectOption(String(device.data.id));

    // Los candidatos son los del servidor PARA ESA SUCURSAL.
    const branchPicker = picker(page, "Sucursal");
    if ((await branchPicker.inputValue()) === "") {
      const candidates = page.waitForResponse(finalResponse(`${INTERNAL}/service/technicians`));
      await branchPicker.selectOption({ index: 1 });
      expect((await candidates).status()).toBe(200);
    }

    await page.getByLabel("Falla reportada").fill(`${MARK} SVC-POS ${RUN}: pantalla rota`);

    // Sin técnico no se registra: en la caja es obligatorio.
    const submit = page.getByRole("button", { name: "Crear servicio" });
    await expect(submit).toBeDisabled();

    const technicianPicker = picker(page, "Técnico asignado");
    const option = technicianPicker.locator("option", { hasText: TECHNICIAN });
    await expect(option, `${TECHNICIAN} no figura entre los técnicos de la sucursal`).toHaveCount(1);
    // Se elige por NOMBRE. El identificador lo pone el servidor en la opción.
    await technicianPicker.selectOption({ label: (await option.textContent())!.trim() });
    await expect(submit).toBeEnabled();

    const created = page.waitForResponse(finalResponse(`${INTERNAL}/service/orders`, "POST"));
    await submit.click();
    const response = await created;
    expect(response.status(), "crear y asignar en una sola petición").toBe(201);
    const order = await response.json();
    shared.orderId = order.id;
    shared.orderNumber = order.number;
    expect(order.technician_name).toContain(TECHNICIAN);

    await expect(page.getByText(order.number)).toBeVisible();
    await expect(page.getByRole("link", { name: "Abrir orden" }))
      .toHaveAttribute("href", `/admin/service/orders/${order.id}`);

    // Un servicio no es una venta: no nació ningún pedido.
    const ordersAfter = await api(page, "GET", "/api/admin/orders/?page_size=1");
    if (orders.status === 200 && typeof orders.data?.count === "number") {
      expect(ordersAfter.data.count, "recibir un equipo no crea un pedido").toBe(orders.data.count);
    }
  });

  test("el técnico la encuentra en «Mis reparaciones» y la abre", async ({ page }) => {
    test.setTimeout(240_000);
    expect(shared.orderId, "el escenario anterior no dejó orden").toBeTruthy();
    await signIn(page, TECHNICIAN);

    await page.goto("/admin/service/orders", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "Órdenes de servicio" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Mis reparaciones" }))
      .toHaveAttribute("aria-pressed", "true");

    const row = page.locator(`a[href="/admin/service/orders/${shared.orderId}"]`).first();
    await expect(row, "la orden creada en caja no aparece en Mis reparaciones").toBeVisible({ timeout: 20_000 });
    await row.click();
    await expect(page.getByRole("heading", { name: shared.orderNumber! })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(`${MARK} SVC-POS ${RUN}: pantalla rota`)).toBeVisible();
  });

  test("el técnico registra un cobro y no puede reversarlo", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page, TECHNICIAN);

    // La cola de Reparación: sólo lo aprobado y en curso, y por defecto lo suyo.
    await page.goto("/admin/service/repairs", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { level: 1, name: "Reparación" })).toBeVisible();
    await page.getByLabel("Buscar").fill(FIXTURE_CUSTOMER);
    const row = page.locator("tbody tr").filter({ hasText: FIXTURE_CUSTOMER }).first();
    if ((await row.count()) === 0) {
      await page.waitForTimeout(3_000);
    }
    if ((await row.count()) === 0) {
      test.skip(true, "sin reparación fixture: python manage.py seed_demo_users --e2e-fixtures");
    }
    await row.getByRole("link").first().click();
    await expect(page.getByText("Pago del servicio")).toBeVisible({ timeout: 20_000 });

    const orderId = Number(new URL(page.url()).pathname.split("/").pop());
    const before = await api(page, "GET", `${INTERNAL}/service/orders/${orderId}/payments/`);
    expect(before.status).toBe(200);

    await page.getByLabel(/^Importe/).fill("1.00");
    await page.getByLabel("Referencia").fill(`E2E ${RUN}`);
    await page.getByRole("button", { name: "Registrar pago" }).click();
    const paid = page.waitForResponse(finalResponse(`/service/orders/${orderId}/payments`, "POST"));
    await page.getByRole("button", { name: "Sí" }).click();
    expect((await paid).status(), "registrar el cobro").toBe(201);

    const after = await api(page, "GET", `${INTERNAL}/service/orders/${orderId}/payments/`);
    expect(after.data.count).toBe(before.data.count + 1);
    expect(Number(after.data.summary.confirmed_paid))
      .toBeCloseTo(Number(before.data.summary.confirmed_paid) + 1, 2);

    // Reversar es otra autoridad: no se ofrece, y el servidor lo rechaza igual.
    await expect(page.getByRole("button", { name: "Reversar" })).toHaveCount(0);
    const payment = (after.data.results as { id: number; reference: string }[])
      .find((p) => p.reference === `E2E ${RUN}`);
    expect(payment, "el pago recién registrado no figura").toBeTruthy();
    const reversal = await api(
      page, "POST", `${INTERNAL}/service/orders/${orderId}/payments/${payment!.id}/reverse/`,
      { reason: "intento de una cuenta sin autoridad" },
    );
    expect(reversal.status, "reversar sin service.payments.manage").toBe(403);
  });
});
