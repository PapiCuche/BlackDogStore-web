import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * ACCOUNTING-03 — el centro contable en un navegador de verdad, contra el
 * contrato de #174 SIMULADO con `page.route`.
 *
 * No necesita backend ni datos sembrados: cada petición a `/api` la contesta
 * esta prueba, y una que nadie esperaba responde 404 (y se anota). Así lo que se
 * comprueba es la PÁGINA:
 *   - el aviso «no es una declaración» y el de reglas provisionales;
 *   - el enmascarado tal como lo manda el servidor;
 *   - las tres vistas por teclado (flechas) y los filtros que viajan al aplicar;
 *   - sin desborde horizontal de la página a 360 px y en escritorio: las tablas
 *     se desplazan en su propio contenedor.
 *
 * Corre en Chromium (por defecto) y en WebKit (`E2E_BROWSER=webkit`): el navegador
 * se elige arriba del archivo porque Playwright no lo cambia dentro de un grupo.
 * Arranque: `next dev -p 3012` y
 * `E2E_BASE_URL=http://localhost:3012 [E2E_BROWSER=webkit] npx playwright test e2e/accounting-centre.spec.ts`.
 */

const BROWSER = process.env.E2E_BROWSER === "webkit" ? "webkit" : "chromium";
test.use({ browserName: BROWSER });

const POLICY = {
  status: "provisional", pending_owner_decision: ["P1", "P2", "P3", "P4"],
  service_revenue_basis: "collection", sale_period_field: "paid_at",
  granted_to_holders_of: "company.manage", exclude_test_payments: true,
};

const ENVELOPE = {
  company: { id: 1, name: "Tienda Contable" },
  period: { kind: "month", from: "2026-09-01", to: "2026-09-30", timezone: "America/Lima" },
  scope: { company_wide: true, branches: [{ id: 10, name: "Centro" }, { id: 11, name: "Norte" }], includes_unbranched: true },
  policy: POLICY,
  notice: "Centro contable de ventas e ingresos. No incluye compras, gastos, bancos ni asientos contables.",
  unmasked: false,
};

const TOTALS = {
  operations: 1, sales_gross: "118.00", sales_taxable: "100.00", sales_tax: "18.00", late_charges: "0.00",
  gross: "118.00", refunds_confirmed: "0.00", refunds_pending: "0.00", net: "118.00",
  service_collections: "0.00", service_reversals: "0.00", income: "118.00",
  excluded_test: { operations: 0, gross: "0.00", refunds_confirmed: "0.00" }, currencies: ["PEN"],
};

const LONG_NAME = "Comercializadora de Accesorios y Repuestos del Sur Andino Sociedad Anónima Cerrada";

function operations() {
  return {
    ...ENVELOPE, totals: TOTALS, count: 1, next_cursor: null,
    results: [{
      key: "sale:41", kind: "sale", order_id: 41, branch: { id: 10, name: "Centro" }, channel: "online", seller: null,
      customer: { name: LONG_NAME, document_type: "ruc", document_number: "****6603" }, currency: "PEN",
      commercial_state: "pagada", excluded_from_income: false,
      primary_document: { kind: "factura", number: "F001-7", environment: "production" }, flags: [],
      row_type: "operation", event: "sale", date: "2026-09-15T12:00:00-05:00", amount: "118.00", gross: "118.00",
      taxable: "100.00", tax: "18.00", tax_rate: "0.1800", breakdown_source: "frozen",
      documents: [{ kind: "factura", number: "F001-7", state: "accepted", environment: "production" }],
    }],
  };
}

function fiscal() {
  return {
    ...ENVELOPE, environment: "production", count: 1, next_cursor: null,
    totals: { "01": { count: 1, total: "118.00" } },
    excluded_totals: {
      rejected: { count: 0, total: "0.00" }, voided: { count: 0, total: "0.00" },
      notes_not_settled: { count: 0, total: "0.00" },
    },
    results: [{
      id: 5, key: "sale:41", order_id: 41, type: "01", type_label: "Factura electrónica", document_id: "F001-7",
      issued_at: "2026-09-15T12:00:00-05:00", environment: "production", test_environment: false,
      customer: { name: LONG_NAME, document_type: "6", document_number: "****6603" }, currency: "PEN",
      taxable: "100.00", tax: "18.00", total: "118.00", tax_rate: "0.1800",
      state: { code: "accepted", label: "Aceptado por SUNAT (con CDR)", settled: true },
      sunat: { code: "0", message: "" }, original: null,
      granted: { granted_at: null, method: "", not_granted: false },
      cdr: { present: true, response_code: "0", hash_ok: true, authenticity: "unverified" }, summary: null,
    }],
  };
}

function pending() {
  return {
    ...ENVELOPE, counts: { INTERNAL_ONLY: 1, REFUNDED_STATUS: 1 }, count: 2, next_cursor: null,
    results: [{
      key: "sale:42", code: "INTERNAL_ONLY", severity: "regularizar",
      message: "Sólo tiene nota de venta interna, que no tiene validez tributaria: la venta sigue sin comprobante.",
      kind: "sale", order_id: 42, repair_order_id: null, branch: { id: 11, name: "Norte" }, channel: "pos",
      date: "2026-09-16T10:00:00-05:00", document: "NV-000001",
    }, {
      key: "sale:43", code: "REFUNDED_STATUS", severity: "info",
      message: "Estado antiguo «Reembolsado»: se cuenta como venta pagada; el tablero de ventas lo deja fuera.",
      kind: "sale", order_id: 43, repair_order_id: null, branch: { id: 10, name: "Centro" }, channel: "online",
      date: "2026-09-17T10:00:00-05:00", document: null,
    }],
  };
}

const USER = { id: 9, username: "contadora", email: "c@example.com", first_name: "Carla", last_name: "Ríos", role: "customer", is_staff: false };

const DASHBOARD = {
  company: { id: 1, name: "Tienda Contable", slug: "tienda-contable", is_active: true },
  membership: { id: 3, branch: null },
  branch_scope: { mode: "all", default_branch: null, branches: [{ id: 10, name: "Centro" }, { id: 11, name: "Norte" }] },
  access: {
    is_platform_admin: false, legacy_role: null,
    roles: [{ id: 1, name: "Gerencia", slug: "gerencia", area: null }], areas: [],
    capabilities: ["company.view", "accounting.view"],
  },
  organization: null, catalog: null, sales: null, inventory: null,
  available_companies: [], requires_company_selection: false, alerts: [],
};

type Seen = { path: string; query: URLSearchParams };

type Options = {
  /** Answer the four accounting routes with this status and detail instead. */
  failAll?: { status: number; detail: string };
  /** What the server sends for buyers' documents. */
  unmasked?: boolean;
  /** Without `accounting.view` in the context. */
  noCapability?: boolean;
};

/** The server's 92-day window for the summary and views A and C (#174). */
function overLedgerWindow(query: URLSearchParams): boolean {
  if (query.get("period") === "year") return true;
  const from = query.get("from");
  const to = query.get("to");
  if (query.get("period") === "range" && from && to) {
    return (Date.parse(to) - Date.parse(from)) / 86_400_000 + 1 > 92;
  }
  return false;
}

async function mockApi(page: Page, options: Options = {}) {
  const seen: Seen[] = [];
  const unexpected: string[] = [];
  await page.route(/\/api\//, async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api/, "");
    seen.push({ path, query: url.searchParams });
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const unmask = (body: Record<string, unknown>) => (options.unmasked ? unmasked(body) : body);
    if (path.startsWith("/auth/me")) return json(USER);
    if (path.startsWith("/me/internal-dashboard")) {
      if (!options.noCapability) return json(DASHBOARD);
      return json({ ...DASHBOARD, access: { ...DASHBOARD.access, capabilities: ["company.view"] } });
    }
    if (path.startsWith("/admin/accounting/")) {
      if (options.failAll) return json({ detail: options.failAll.detail }, options.failAll.status);
      const ledger = !path.startsWith("/admin/accounting/fiscal-documents");
      if (ledger && overLedgerWindow(url.searchParams)) {
        return json({ detail: "Esta vista abarca como mucho 92 días: elige un período más corto." }, 400);
      }
    }
    if (path.startsWith("/admin/accounting/summary")) {
      return json(unmask({ ...ENVELOPE, totals: TOTALS, pending: { INTERNAL_ONLY: 1, REFUNDED_STATUS: 1 } }));
    }
    if (path.startsWith("/admin/accounting/operations")) return json(unmask(operations()));
    if (path.startsWith("/admin/accounting/fiscal-documents")) return json(unmask(fiscal()));
    if (path.startsWith("/admin/accounting/pending")) return json(unmask(pending()));
    unexpected.push(path);
    return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"No encontrado."}' });
  });
  return { seen, unexpected };
}

/** The same answer as the server gives a caller with `accounting.export`. */
function unmasked(body: Record<string, unknown>) {
  const text = JSON.stringify({ ...body, unmasked: true }).replaceAll("****6603", "20100066603");
  return JSON.parse(text);
}

async function noPageOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }));
  expect(scroll, "la página no debe desbordarse horizontalmente").toBeLessThanOrEqual(client + 1);
}

{
  test.describe(`centro contable · ${BROWSER}`, () => {

    test("avisos, enmascarado y las tres vistas por teclado", async ({ page }) => {
      const { seen } = await mockApi(page);
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.goto("/admin/accounting");

      await expect(page.getByRole("heading", { name: "Centro contable" })).toBeVisible();
      await expect(page.getByText(/Nada de lo que aparece aquí es una declaración presentada ante SUNAT/)).toBeVisible();
      await expect(page.getByTestId("accounting-policy-banner")).toContainText("Reglas provisionales");
      await expect(page.getByTestId("accounting-masked-note")).toBeVisible();
      await expect(page.getByText("****6603")).toBeVisible();
      await expect(page.getByText("sale:41")).toBeVisible();

      const operationsTab = page.getByRole("tab", { name: /Operaciones/ });
      await operationsTab.focus();
      await page.keyboard.press("ArrowRight");
      await expect(page.getByRole("tab", { name: /Comprobantes/ })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByText("Aceptado por SUNAT (con CDR)")).toBeVisible();
      await expect(page.getByText(/no está verificada como de SUNAT/)).toBeVisible();
      await page.keyboard.press("ArrowRight");
      await expect(page.getByRole("tab", { name: /pendientes/ })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByText("INTERNAL_ONLY").first()).toBeVisible();

      expect(seen.some((s) => s.path.startsWith("/admin/accounting/fiscal-documents") && s.query.get("environment") === "production")).toBe(true);
      await noPageOverflow(page);
    });

    test("los filtros viajan al aplicar", async ({ page }) => {
      const { seen } = await mockApi(page);
      await page.goto("/admin/accounting");
      await expect(page.getByText("sale:41")).toBeVisible();

      await page.getByLabel("Período", { exact: true }).selectOption("tax_period");
      await page.getByLabel("Mes", { exact: true }).selectOption("09");
      await page.getByLabel("Año", { exact: true }).fill("2026");
      await page.getByLabel("Sucursal", { exact: true }).selectOption("11");
      await page.getByLabel("Canal", { exact: true }).selectOption("pos");
      await page.getByLabel("Nota de venta interna", { exact: true }).check();
      const before = seen.length;
      await page.getByRole("button", { name: "Aplicar filtros" }).click();
      await expect.poll(() => seen.slice(before).find((s) => s.path.startsWith("/admin/accounting/operations"))?.query.toString())
        .toBe("tax_period=202609&branch=11&channel=pos&doc=nota_interna");
    });

    test("a 360 px las tablas se desplazan solas, no la página", async ({ page }) => {
      await mockApi(page);
      await page.setViewportSize({ width: 360, height: 800 });
      await page.goto("/admin/accounting");
      await expect(page.getByText("sale:41")).toBeVisible();
      await noPageOverflow(page);
      const scroller = page.getByRole("region", { name: "Tabla de operaciones" });
      const widths = await scroller.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
      expect(widths.scroll).toBeGreaterThan(widths.client);
    });

    test("la vista B de un año se muestra aunque el resumen no abarque más de 92 días", async ({ page }) => {
      const { seen } = await mockApi(page);
      await page.goto("/admin/accounting");
      await expect(page.getByText("sale:41")).toBeVisible();
      await page.getByRole("tab", { name: /Comprobantes/ }).click();
      await page.getByLabel("Período", { exact: true }).selectOption("year");
      await page.getByRole("button", { name: "Aplicar filtros" }).click();
      await expect(page.getByText("Aceptado por SUNAT (con CDR)")).toBeVisible();
      await expect(page.getByTestId("accounting-summary-note")).toContainText("92 días");
      await expect(page.getByTestId("accounting-fiscal-totals")).toContainText("118.00");
      await expect(page.getByRole("tabpanel").getByRole("alert")).toHaveCount(0);
      const fiscalAsk = seen.filter((s) => s.path.startsWith("/admin/accounting/fiscal-documents")).at(-1);
      expect(fiscalAsk?.query.get("period")).toBe("year");
    });

    test("un 400 del servidor se muestra con sus palabras", async ({ page }) => {
      await mockApi(page, { failAll: { status: 400, detail: "«channel» no admite fax." } });
      await page.goto("/admin/accounting");
      await expect(page.getByRole("tabpanel").getByRole("alert")).toContainText("«channel» no admite fax.");
    });

    test("un 403 del servidor dice que no hay permiso", async ({ page }) => {
      await mockApi(page, { failAll: { status: 403, detail: "No tienes permisos para esta operación." } });
      await page.goto("/admin/accounting");
      await expect(page.getByRole("tabpanel").getByRole("alert")).toContainText("No tienes permiso para ver el centro contable.");
    });

    test("sin accounting.view la pantalla no se ofrece", async ({ page }) => {
      const { seen } = await mockApi(page, { noCapability: true });
      await page.goto("/admin/accounting");
      await expect(page.getByRole("heading", { name: "Sin acceso al centro contable" })).toBeVisible();
      expect(seen.some((s) => s.path.startsWith("/admin/accounting/"))).toBe(false);
    });

    test("REFUNDED_STATUS aparece en la vista C como informativo", async ({ page }) => {
      await mockApi(page);
      await page.goto("/admin/accounting");
      await page.getByRole("tab", { name: /pendientes/ }).click();
      const row = page.getByRole("row", { name: /REFUNDED_STATUS/ });
      await expect(row).toContainText("Informativo");
      await expect(row).toContainText("Estado antiguo «Reembolsado»");
    });

    test("con accounting.export los documentos salen enteros y sin aviso de enmascarado", async ({ page }) => {
      await mockApi(page, { unmasked: true });
      await page.goto("/admin/accounting");
      await expect(page.getByText("20100066603")).toBeVisible();
      await expect(page.getByText("****6603")).toHaveCount(0);
      await expect(page.getByTestId("accounting-masked-note")).toHaveCount(0);
    });
  });
}
