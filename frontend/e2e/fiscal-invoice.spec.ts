import { test, expect, type Page } from "@playwright/test";

/**
 * C2.2A.1B — la factura electrónica, vista desde el panel.
 *
 * NO LLAMA A SUNAT. Las respuestas del servicio se interceptan en el navegador,
 * así que estas pruebas comprueban NUESTRA aplicación y no la disponibilidad de
 * SUNAT. La integración real ya tiene su evidencia: una CDR aceptada de verdad,
 * documentada en docs/sunat-cpe-requisitos.md.
 *
 * Un E2E que dependiera de un servicio externo fallaría en rojo por cosas que no
 * son defectos nuestros, y acabaría desactivado.
 */

const VIEWPORTS = [
  { name: "390", width: 390, height: 844 },
  { name: "1440", width: 1440, height: 900 },
];
const THEMES = ["light", "dark"] as const;

const BASE = {
  id: 7,
  order_id: 1,
  document_type: "01",
  document_type_label: "Factura electrónica",
  identifier: "F001-1",
  series: "F001",
  number: 1,
  environment: "beta",
  environment_label: "Pruebas (SUNAT beta)",
  issued_at: "2026-09-06T12:00:00Z",
  currency: "PEN",
  taxable_amount: "100.00",
  tax_amount: "18.00",
  total: "118.00",
  customer_doc_number: "20000000001",
  customer_legal_name: "CLIENTE DE PRUEBA SAC",
  response_code: "",
  response_message: "",
  attempts: 0,
  has_xml: true,
  has_cdr: false,
};

const SIGNED = {
  ...BASE,
  status: "signed",
  status_label: "Firmado",
  is_accepted: false,
  can_submit: true,
  can_retry: false,
  can_download_pdf: true,
};

const ACCEPTED = {
  ...BASE,
  status: "accepted",
  status_label: "Aceptado por SUNAT",
  response_code: "0",
  response_message: "La Factura numero F001-1, ha sido aceptada",
  attempts: 1,
  has_cdr: true,
  is_accepted: true,
  can_submit: false,
  can_retry: false,
  can_download_pdf: true,
};

const FAILED = {
  ...BASE,
  status: "submission_error",
  status_label: "Error de envío",
  response_message: "ReadTimeout al contactar con el servicio",
  attempts: 1,
  is_accepted: false,
  can_submit: false,
  can_retry: true,
  can_download_pdf: true,
};

const REJECTED = {
  ...BASE,
  status: "rejected",
  status_label: "Rechazado por SUNAT",
  response_code: "2335",
  response_message: "El comprobante fue rechazado",
  attempts: 1,
  is_accepted: false,
  can_submit: false,
  can_retry: false,
  can_download_pdf: true,
};

/**
 * UNA SOLA SESIÓN PARA TODA LA SUITE.
 *
 * El login está limitado a 5 intentos por minuto y por IP, y eso está bien: es
 * una defensa real. Nueve pruebas entrando por separado lo activan y fallan por
 * el limitador, no por un defecto. Se entra una vez, se guarda el estado y las
 * demás lo reutilizan — sin desactivar la defensa, que es lo que convertiría
 * estas pruebas en unas que ya no prueban lo que se despliega.
 */
let SESSION_COOKIES: Awaited<ReturnType<import("@playwright/test").BrowserContext["cookies"]>> = [];

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) {
    test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  }
  const row = card.locator("li").filter({ hasText: "dev_admin" });
  const use = row.getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) {
    test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  }
  await use.click();

  // El limitador puede estar caliente de una corrida anterior. Se ESPERA la
  // ventana en vez de desactivarlo.
  for (let intento = 0; intento < 3; intento++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión");
      await page.waitForTimeout(62_000);
    }
  }
}

/**
 * Un pedido pagado que pidió factura, preguntado al propio backend.
 *
 * SE PIDE DESDE DENTRO DE LA PÁGINA, no con `page.request`. Aquél tiene su
 * propio contexto de cookies y responde 401 aunque el navegador esté
 * autenticado — ya pasó una vez en las pruebas de cuentas de desarrollo, y la
 * consecuencia fue una suite que se saltaba entera sin decir por qué.
 */
async function paidInvoiceOrder(page: Page): Promise<number | null> {
  return page.evaluate(async () => {
    const res = await fetch("/api/admin/orders/?page_size=50", {
      credentials: "include",
    });
    if (!res.ok) return null;
    const body = await res.json();
    const orders = body.results ?? body;

    // EL LISTADO NO TRAE `receipt_type`, así que filtrarlo ahí no encontraba
    // nada nunca y la suite se saltaba entera en silencio. Hay que mirar el
    // detalle, que es donde vive ese campo.
    for (const order of orders.filter((o: { status: string }) => o.status === "paid")) {
      const detail = await fetch(`/api/admin/orders/${order.id}/`, {
        credentials: "include",
      });
      if (!detail.ok) continue;
      const full = await detail.json();
      if (full.receipt_type === "factura") return full.id;
    }
    return null;
  });
}

/** Sirve un estado fijo del comprobante, sin tocar SUNAT. */
async function serveDocument(page: Page, document: object | null) {
  // EXPRESIÓN REGULAR Y NO GLOB: en Playwright `*` no cruza `/`, así que
  // `**/fiscal-document*` no casaba con la barra final de la ruta real y la
  // intercepción no se aplicaba nunca — el panel mostraba el 404 del backend de
  // verdad y la prueba fallaba por un motivo que no era el suyo.
  await page.route(/\/api\/admin\/orders\/\d+\/fiscal-document/, async (route) => {
    if (document === null) {
      await route.fulfill({
        status: 404, contentType: "application/json",
        body: JSON.stringify({ detail: "Sin comprobante." }),
      });
      return;
    }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify(document),
    });
  });
}

async function openOrder(page: Page, orderId: number) {
  await page.goto(`/admin/orders/${orderId}`, { waitUntil: "networkidle" });
  await expect(
    page.getByRole("heading", { name: /comprobante electrónico/i }),
    "el bloque del comprobante no aparece",
  ).toBeVisible({ timeout: 20_000 });
}

test.beforeAll(async ({ browser, baseURL }) => {
  // `baseURL` EXPLÍCITO: un contexto creado a mano no hereda la configuración
  // del proyecto, así que `page.goto("/auth")` no llegaba a ninguna parte y el
  // `test.skip` de `signIn` saltaba la suite ENTERA sin decir por qué.
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  // En memoria y no en fichero: `test.use({ storageState })` se evalúa ANTES de
  // que corra este bloque, así que el fichero todavía no existiría.
  SESSION_COOKIES = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ context }) => {
  if (SESSION_COOKIES.length) await context.addCookies(SESSION_COOKIES);
});

test.describe("el comprobante electrónico en el detalle del pedido", () => {
  test.describe.configure({ mode: "serial" });

  test("sin comprobante ofrece emitirlo", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/orders", { waitUntil: "networkidle" });
    const orderId = await paidInvoiceOrder(page);
    if (!orderId) test.skip(true, "no hay pedido pagado con factura solicitada");
    // `test.skip` no estrecha el tipo para TypeScript, aunque en tiempo de
    // ejecución la prueba ya no sigue.
    const id = orderId as number;

    await serveDocument(page, null);
    await openOrder(page, id);

    await expect(page.getByText(/todavía no tiene comprobante/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /emitir factura/i })).toBeVisible();
  });

  test("firmado ofrece enviar, y no dice que esté aceptado", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/orders", { waitUntil: "networkidle" });
    const orderId = await paidInvoiceOrder(page);
    if (!orderId) test.skip(true, "no hay pedido pagado con factura solicitada");
    // `test.skip` no estrecha el tipo para TypeScript, aunque en tiempo de
    // ejecución la prueba ya no sigue.
    const id = orderId as number;

    await serveDocument(page, SIGNED);
    await openOrder(page, id);

    await expect(page.getByText("F001-1")).toBeVisible();
    await expect(page.getByRole("button", { name: /enviar a sunat/i })).toBeVisible();
    // LO QUE NO PUEDE DECIR. Sin constancia no hay aceptación.
    const texto = await page.locator("body").innerText();
    expect(/aceptad[ao] por sunat/i.test(texto), texto.slice(0, 300)).toBe(false);
  });

  test("aceptado muestra la respuesta de SUNAT y el CDR", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/orders", { waitUntil: "networkidle" });
    const orderId = await paidInvoiceOrder(page);
    if (!orderId) test.skip(true, "no hay pedido pagado con factura solicitada");
    // `test.skip` no estrecha el tipo para TypeScript, aunque en tiempo de
    // ejecución la prueba ya no sigue.
    const id = orderId as number;

    await serveDocument(page, ACCEPTED);
    await openOrder(page, id);

    await expect(page.getByText(/aceptado por sunat/i).first()).toBeVisible();
    await expect(page.getByText(/ha sido aceptada/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /^cdr$/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /pdf a4/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /ticket 80/i })).toBeVisible();
  });

  test("un error de envío ofrece reintentar el MISMO comprobante", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/orders", { waitUntil: "networkidle" });
    const orderId = await paidInvoiceOrder(page);
    if (!orderId) test.skip(true, "no hay pedido pagado con factura solicitada");
    // `test.skip` no estrecha el tipo para TypeScript, aunque en tiempo de
    // ejecución la prueba ya no sigue.
    const id = orderId as number;

    await serveDocument(page, FAILED);
    await openOrder(page, id);

    // El texto importa: «reintentar el mismo» y no «emitir», para que nadie crea
    // que está creando otro documento con otro correlativo.
    const boton = page.getByRole("button", { name: /reintentar el mismo comprobante/i });
    await expect(boton).toBeVisible();
    await expect(page.getByText(/readtimeout/i)).toBeVisible();
    const texto = await page.locator("body").innerText();
    expect(/aceptad[ao] por sunat/i.test(texto)).toBe(false);
  });

  test("un rechazo no ofrece reemitir, y explica por qué", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/admin/orders", { waitUntil: "networkidle" });
    const orderId = await paidInvoiceOrder(page);
    if (!orderId) test.skip(true, "no hay pedido pagado con factura solicitada");
    // `test.skip` no estrecha el tipo para TypeScript, aunque en tiempo de
    // ejecución la prueba ya no sigue.
    const id = orderId as number;

    await serveDocument(page, REJECTED);
    await openOrder(page, id);

    await expect(page.getByText(/rechazado por sunat/i).first()).toBeVisible();
    // NINGÚN botón que cree silenciosamente otro correlativo.
    await expect(page.getByRole("button", { name: /emitir factura/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /reintentar/i })).toHaveCount(0);
    await expect(page.getByText(/considera usado este correlativo/i)).toBeVisible();
  });
});

test.describe("la superficie fiscal cabe y se lee", () => {
  for (const theme of THEMES) {
    for (const viewport of VIEWPORTS) {
      test(`${theme} · ${viewport.name}px`, async ({ page }) => {
        test.setTimeout(120_000);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await page.emulateMedia({ colorScheme: theme });
        await page.addInitScript((v) => {
          try { window.localStorage.setItem("theme", v as string); } catch { /* ver arriba */ }
        }, theme);

        // SIN `signIn` aquí: la sesión ya viene del `beforeEach`. Volver a
        // entrar chocaba con el limitador y, peor, `/auth` estando autenticado
        // no muestra la tarjeta de accesos — así que el `test.skip` de `signIn`
        // saltaba estas cuatro pruebas en silencio.
        await page.goto("/admin/orders", { waitUntil: "networkidle" });
        const orderId = await paidInvoiceOrder(page);
        if (!orderId) test.skip(true, "no hay pedido pagado con factura solicitada");
        const id = orderId as number;

        await serveDocument(page, ACCEPTED);
        await openOrder(page, id);

        // Nada se sale del ancho. `min-width: auto` en un hijo de grid o flex es
        // el mecanismo que ya rompió otras pantallas de este proyecto.
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - window.innerWidth,
        );
        expect(overflow, `la página se sale ${overflow}px`).toBeLessThanOrEqual(1);
      });
    }
  }
});
