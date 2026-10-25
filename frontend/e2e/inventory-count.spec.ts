import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * MIGRATION-D — la pantalla de recuento con un navegador de verdad.
 *
 * El motor es MIGRATION-C (#175), pero aquí el servidor se simula con
 * `page.route`, siguiendo su contrato (`docs/adr-migration-count.md` §5), para
 * probar la pantalla sin depender de datos sembrados. Lo que
 * se comprueba es la PÁGINA: el foco vive en el escáner, Enter envía, el
 * incremento lleva el valor mostrado y un 409 no suma dos veces, el total
 * reemplaza, blanco ≠ cero, la referencia no se copia, nada llama a «aprobar», y
 * en 360 px no hay desborde.
 */

const CODE = "7750001";

type Line = Record<string, unknown> & { product: number; physical_quantity: number | null };

function baseLine(over: Partial<Line>): Line {
  return {
    id: 101, product: 5, product_name: "Cargador USB-C 20W", product_slug: "cargador",
    theoretical_at_start: null, physical_quantity: null, theoretical_at_approval: null,
    difference: null, is_counted: false, note: "", updated_at: "2026-10-23T10:00:00Z",
    product_code: CODE, product_codes: [CODE], reference_quantity: 7, current_theoretical: 4,
    review_state: "pending",
    ...over,
  };
}

function fulfil(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockServer(page: Page) {
  const state = {
    lines: [
      baseLine({}),
      baseLine({
        id: 102, product: 6, product_name: "Funda MagSafe", product_code: "7750002", product_codes: ["7750002"],
        reference_quantity: null, physical_quantity: 0, is_counted: true, theoretical_at_start: 2, review_state: "counted",
      }),
    ],
    scans: [] as Record<string, unknown>[],
    puts: [] as unknown[],
    approveCalls: 0,
    forceMismatchTo: null as number | null,
  };
  const detail = () => ({
    id: 9, company: 7, branch: 11, branch_name: "Sucursal Centro", status: "counting", status_label: "En conteo",
    reason: "", items: state.lines, counted_items: state.lines.filter((l) => l.physical_quantity !== null).length,
    created_by: 1, created_by_username: "ana", created_at: "2026-10-23T09:00:00Z", approved_at: null,
    cancelled_at: null, updated_at: "2026-10-23T09:00:00Z", kind: "migration",
  });
  const setQty = (product: number, qty: number | null) => {
    state.lines = state.lines.map((l) =>
      l.product === product
        ? { ...l, physical_quantity: qty, is_counted: qty !== null, theoretical_at_start: qty === null ? null : 4, review_state: qty === null ? "pending" : "counted" }
        : l,
    );
    return state.lines.find((l) => l.product === product)!;
  };

  // Lo no simulado responde vacío: la cabecera del panel no es lo que se prueba.
  await page.route(/\/api\//, (route) => fulfil(route, 200, { results: [], count: 0, page: 1, page_size: 20 }));
  await page.route(/\/api\/auth\/me\/?(\?.*)?$/, (route) =>
    fulfil(route, 200, { id: 1, username: "ana", email: "ana@example.test", first_name: "Ana", last_name: "", role: "customer", is_staff: false }),
  );
  await page.route(/\/api\/me\/internal-dashboard\/?(\?.*)?$/, (route) =>
    fulfil(route, 200, {
      company: { id: 7, name: "Tienda de prueba", slug: "tienda-prueba", is_active: true },
      membership: null, branch_scope: null,
      access: { capabilities: ["inventory.view", "inventory.adjust"], is_platform_admin: false, legacy_role: null, roles: [], areas: [] },
      organization: null, catalog: null, sales: null, inventory: null,
      available_companies: [{ id: 7, name: "Tienda de prueba", slug: "tienda-prueba", is_active: true }],
      requires_company_selection: false, alerts: [],
    }),
  );
  await page.route(/\/api\/admin\/inventory\/counts\/9\/?(\?.*)?$/, (route) => fulfil(route, 200, detail()));
  await page.route(/\/api\/admin\/inventory\/counts\/9\/approve\/?$/, (route) => {
    state.approveCalls += 1;
    return fulfil(route, 500, { detail: "no debería llamarse" });
  });
  await page.route(/\/api\/admin\/inventory\/counts\/9\/scan\/?$/, (route) => {
    const body = route.request().postDataJSON() as { code: string; mode: string; expected?: number | null };
    state.scans.push(body);
    const current = state.lines.find((l) => l.product_codes && (l.product_codes as string[]).includes(body.code));
    if (!current) return fulfil(route, 404, { code: "not_found", detail: "Código no encontrado." });
    if (body.mode === "lookup") return fulfil(route, 200, { result: "found", line: current });
    if (state.forceMismatchTo !== null) {
      const line = setQty(current.product, state.forceMismatchTo);
      state.forceMismatchTo = null;
      return fulfil(route, 409, { code: "expected_mismatch", detail: "El valor cambió.", line });
    }
    if (current.physical_quantity !== (body.expected ?? null)) {
      return fulfil(route, 409, { code: "expected_mismatch", detail: "El valor cambió.", line: current });
    }
    const line = setQty(current.product, (current.physical_quantity ?? 0) + 1);
    return fulfil(route, 200, { result: "incremented", line });
  });
  await page.route(/\/api\/admin\/inventory\/counts\/9\/items\/?$/, (route) => {
    const body = route.request().postDataJSON() as { product: number; physical_quantity: number }[];
    state.puts.push(body);
    for (const line of body) setQty(line.product, line.physical_quantity);
    return fulfil(route, 200, detail());
  });
  return state;
}

const scanField = (page: Page) => page.getByLabel("Código de barras o búsqueda");
const countedCell = (page: Page, name: string) => page.getByRole("row", { name: new RegExp(name) }).getByTestId("counted-cell");

test("recuento de accesorios: incremento, 409 sin doble suma, total, blanco ≠ cero, sin aprobar", async ({ page }) => {
  const state = await mockServer(page);
  await page.goto("/admin/inventory/counts/9");

  await expect(page.getByRole("heading", { name: "Recuento #9" })).toBeVisible();
  await expect(scanField(page)).toBeFocused();
  await expect(countedCell(page, "Cargador USB-C 20W")).toHaveText(/Sin contar/);
  await expect(countedCell(page, "Funda MagSafe")).toHaveText(/verificado: no hay/);
  await expect(page.getByRole("region", { name: "Pendientes (1)" })).toBeVisible();

  // Teclado: el modo incremental se elige a mano y se anuncia.
  await page.getByRole("radio", { name: /Sumar 1 por escaneo/ }).check();
  await expect(page.getByRole("status")).toHaveText(/cada escaneo suma 1/i);
  await expect(scanField(page)).toBeFocused();

  await page.keyboard.type(CODE);
  await page.keyboard.press("Enter");
  await expect(countedCell(page, "Cargador USB-C 20W")).toHaveText("1");
  await expect(scanField(page)).toBeFocused();
  await page.keyboard.type(CODE);
  await page.keyboard.press("Enter");
  await expect(countedCell(page, "Cargador USB-C 20W")).toHaveText("2");
  expect(state.scans.map((s) => s.expected)).toEqual([null, 1]);

  // Otro contador cambió la línea: 409, se muestra 5 y no se suma.
  state.forceMismatchTo = 5;
  await page.keyboard.type(CODE);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("count-alerts").getByRole("alert")).toContainText(/no se sumó/i);
  await expect(countedCell(page, "Cargador USB-C 20W")).toHaveText("5");
  expect(state.scans).toHaveLength(3);

  // Total: reemplaza, no suma. La referencia (7) no se copia al campo.
  await page.getByRole("radio", { name: /Cantidad total/ }).check();
  await scanField(page).fill(CODE);
  await page.keyboard.press("Enter");
  const qty = page.getByLabel("Cantidad contada");
  await expect(qty).toBeFocused();
  await expect(qty).toHaveValue("");
  await expect(page.getByText(/Referencia del sistema anterior: 7/)).toBeVisible();
  await page.keyboard.type("3");
  await page.keyboard.press("Enter");
  await expect(countedCell(page, "Cargador USB-C 20W")).toHaveText("3");
  expect(state.puts).toEqual([[{ product: 5, physical_quantity: 3 }]]);
  await expect(scanField(page)).toBeFocused();

  await expect(page.getByRole("button", { name: /Aprobar/ })).toHaveCount(0);
  expect(state.approveCalls).toBe(0);
});

test("un código escaneado en el campo de cantidad no se guarda como cantidad", async ({ page }) => {
  const state = await mockServer(page);
  await page.goto("/admin/inventory/counts/9");
  await expect(scanField(page)).toBeFocused();
  await page.keyboard.type(CODE);
  await page.keyboard.press("Enter");
  const qty = page.getByLabel("Cantidad contada");
  await expect(qty).toBeFocused();
  // The scanner keeps going: the NEXT product's code lands in the quantity field.
  await page.keyboard.type("7750002");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("count-alerts").getByRole("alert")).toContainText(/código/i);
  await expect(qty).toHaveValue("");
  expect(state.puts).toEqual([]);
  // A burst of digits that is no known code is refused too.
  await page.keyboard.type("123456789");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("count-alerts").getByRole("alert")).toContainText(/lector/i);
  expect(state.puts).toEqual([]);
  // A person typing a real total is still heard.
  await page.keyboard.type("3", { delay: 80 });
  await page.keyboard.press("Enter");
  await expect(countedCell(page, "Cargador USB-C 20W")).toHaveText("3");
  expect(state.puts).toEqual([[{ product: 5, physical_quantity: 3 }]]);
});

test("recuento de accesorios en 360 px: sin desborde global", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await mockServer(page);
  await page.goto("/admin/inventory/counts/9");
  await expect(page.getByRole("heading", { name: "Recuento #9" })).toBeVisible();
  await expect(scanField(page)).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
