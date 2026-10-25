import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * LABEL-03b — «Configuración de etiquetas» y la pantalla de etiquetas sin
 * «Ver inventario», en un navegador de verdad, contra el contrato de LABEL-02
 * SIMULADO con `page.route` (no necesita backend ni datos sembrados):
 *   - el prefijo de los equipos junto al de los productos, la regla de que no
 *     coinciden, el aviso previo y el 400 del servidor mostrado tal cual;
 *   - «IMEI 1» con su aviso (permiso sensible y auditoría) y el PATCH que envía
 *     sólo lo que cambió;
 *   - sin `can_manage`, se ve y no se cambia;
 *   - con sólo `inventory.labels.print`, la pantalla de etiquetas explica qué
 *     falta y no llama a ningún endpoint de inventario;
 *   - sin desborde horizontal a 360 px.
 *
 * Corre en Chromium (por defecto) y en WebKit (`E2E_BROWSER=webkit`).
 * Arranque: `next dev -p 3503` y
 * `E2E_BASE_URL=http://localhost:3503 [E2E_BROWSER=webkit] npx playwright test e2e/label-settings.spec.ts`.
 */

const BROWSER = process.env.E2E_BROWSER === "webkit" ? "webkit" : "chromium";
test.use({ browserName: BROWSER });

const SYMBOLS = [
  { value: "internal", label: "Código interno del equipo" },
  { value: "imei1", label: "IMEI 1 (requiere permiso de datos sensibles)" },
];

function fulfil(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

type Server = { patches: Record<string, unknown>[]; inventoryCalls: string[] };

async function mockServer(page: Page, opts: { capabilities: string[]; canManage?: boolean }) {
  const server: Server = { patches: [], inventoryCalls: [] };
  let settings = {
    unit_code_prefix: "U", device_symbol: "internal", device_symbol_label: SYMBOLS[0].label,
    device_symbols: SYMBOLS, can_manage: opts.canManage ?? true,
  };

  await page.route(/\/api\//, (route) => fulfil(route, 200, { results: [], count: 0, page: 1, page_size: 20 }));
  await page.route(/\/api\/admin\/inventory\//, (route) => {
    server.inventoryCalls.push(route.request().url());
    return fulfil(route, 403, { detail: "No tienes permisos sobre el inventario." });
  });
  await page.route(/\/api\/auth\/me\/?(\?.*)?$/, (route) =>
    fulfil(route, 200, { id: 1, username: "ana", email: "ana@example.test", first_name: "Ana", last_name: "", role: "customer", is_staff: false }),
  );
  await page.route(/\/api\/me\/internal-dashboard\/?(\?.*)?$/, (route) =>
    fulfil(route, 200, {
      company: { id: 7, name: "Tienda de prueba", slug: "tienda-prueba", is_active: true },
      membership: null, branch_scope: null,
      access: { capabilities: opts.capabilities, is_platform_admin: false, legacy_role: null, roles: [], areas: [] },
      organization: null, catalog: null, sales: null, inventory: null,
      available_companies: [{ id: 7, name: "Tienda de prueba", slug: "tienda-prueba", is_active: true }],
      requires_company_selection: false, alerts: [],
    }),
  );
  await page.route(/\/api\/admin\/sequences\/?(\?.*)?$/, (route) =>
    fulfil(route, 200, {
      scope: "company", can_change_scope: true, can_manage: true, notice: "",
      results: [{ id: 2, company: 7, branch: null, document_type: "product_code", prefix: "C" }],
    }),
  );
  await page.route(/\/api\/admin\/label-settings\/?(\?.*)?$/, (route) => {
    const request = route.request();
    if (request.method() !== "PATCH") return fulfil(route, 200, settings);
    const body = request.postDataJSON() as Record<string, string>;
    server.patches.push(body);
    if (body.unit_code_prefix === "ZZ") {
      return fulfil(route, 400, { unit_code_prefix: ["«ZZ» no se admite en esta prueba: mensaje del servidor."] });
    }
    settings = { ...settings, ...body };
    return fulfil(route, 200, settings);
  });
  return server;
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test.describe("Configuración de etiquetas", () => {
  test("prefijo con la regla de productos, 400 tal cual, IMEI 1 con su aviso", async ({ page }) => {
    const server = await mockServer(page, { capabilities: ["company.view", "company.manage", "inventory.labels.print"] });
    await page.goto("/admin/inventory/labels/settings");

    await expect(page.getByRole("heading", { name: "Configuración de etiquetas" })).toBeVisible();
    const prefix = page.getByLabel("Prefijo de los equipos");
    await expect(prefix).toHaveValue("U");
    await expect(page.getByTestId("product-prefix")).toHaveText("C");
    const save = page.getByRole("button", { name: "Guardar" });
    await expect(save).toBeDisabled();

    await prefix.fill("c");
    await expect(prefix).toHaveValue("C");
    await expect(page.getByText("«C» es el prefijo de los productos: elige otro.")).toBeVisible();
    await expect(save).toBeDisabled();

    await prefix.fill("zz");
    await save.click();
    await expect(page.getByText("«ZZ» no se admite en esta prueba: mensaje del servidor.")).toBeVisible();
    await expect(prefix).toHaveAttribute("aria-invalid", "true");

    await prefix.fill("EQ");
    await save.click();
    await expect(page.getByRole("status")).toHaveText("Guardado.");
    expect(server.patches.at(-1)).toEqual({ unit_code_prefix: "EQ" });

    await expect(page.getByRole("note", { name: "Aviso IMEI 1" })).toHaveCount(0);
    await page.getByLabel("IMEI 1 (requiere permiso de datos sensibles)").check();
    const warning = page.getByRole("note", { name: "Aviso IMEI 1" });
    await expect(warning).toContainText("inventory.labels.sensitive");
    await expect(warning).toContainText("queda registrado");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByRole("status")).toHaveText("Guardado.");
    expect(server.patches.at(-1)).toEqual({ device_symbol: "imei1" });
  });

  test("sin can_manage se ve pero no se cambia", async ({ page }) => {
    await mockServer(page, { capabilities: ["company.view"], canManage: false });
    await page.goto("/admin/inventory/labels/settings");
    await expect(page.getByLabel("Prefijo de los equipos")).toBeDisabled();
    await expect(page.getByLabel("IMEI 1 (requiere permiso de datos sensibles)")).toBeDisabled();
    await expect(page.getByRole("button", { name: "Guardar" })).toHaveCount(0);
    await expect(page.getByText(/Sólo quien administra la empresa/)).toBeVisible();
  });

  test("a 360 px no desborda", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await mockServer(page, { capabilities: ["company.view", "company.manage"] });
    await page.goto("/admin/inventory/labels/settings");
    await page.getByLabel("IMEI 1 (requiere permiso de datos sensibles)").check();
    await expect(page.getByRole("note", { name: "Aviso IMEI 1" })).toBeVisible();
    await noHorizontalOverflow(page);
  });
});

test.describe("Etiquetas con sólo «Imprimir etiquetas»", () => {
  test("explica que falta Ver inventario y no llama al inventario", async ({ page }) => {
    const server = await mockServer(page, { capabilities: ["inventory.labels.print"] });
    await page.goto("/admin/inventory/labels");
    const note = page.getByRole("note", { name: "Falta Ver inventario" });
    await expect(note).toBeVisible();
    await expect(note).toContainText("inventory.view");
    expect(server.inventoryCalls).toEqual([]);
    await noHorizontalOverflow(page);
  });
});
