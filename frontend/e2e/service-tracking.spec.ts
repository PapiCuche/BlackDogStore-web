import { expect, test, type Cookie, type Page } from "@playwright/test";
import { SLUG, api, signIn } from "./media-helpers";

/**
 * TRACKING · QUOTE-DECISION · SERIALIZED-STOCK · STOREFRONT-CATEGORIES — en un
 * navegador de verdad, sin mocks.
 *
 *   1. un equipo dejado en el taller sin cuenta se sigue por su enlace, y la
 *      cotización se aprueba desde ahí;
 *   2. el personal anota una aprobación dada por teléfono y queda disponible
 *      el ticket;
 *   3. dos equipos entran al inventario con su serie y el tablero los cuenta;
 *   4. la tienda quita una familia de su portada y vuelve a ponerla; el
 *      carrusel se arrastra y sus tarjetas siguen llevando al producto.
 *
 * UNA SOLA ENTRADA para todo: el inicio de sesión tiene un límite de cinco por
 * minuto y por IP que comparte la suite.
 *
 * DATOS. Todo lo que se crea lleva «[E2E]». Las órdenes con cotización no se
 * pueden borrar (y está bien que no se pueda): quedan en la base desechable de
 * la corrida. Los equipos se dan de baja y la categoría vuelve a como estaba.
 */

const RUN = Date.now().toString(36);
const INTERNAL = `/api/v1/internal/${SLUG}`;
const MARK = "[E2E]";

let SESSION: Cookie[] = [];

test.beforeAll(async ({ browser, baseURL }) => {
  test.setTimeout(240_000);
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  SESSION = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ context }) => {
  if (SESSION.length) await context.addCookies(SESSION);
});

/** Un IMEI válido (con su dígito de control) que no se repite entre corridas. */
function imei(seed: number): string {
  const body = `35${String(Date.now() + seed).slice(-12)}`;
  let total = 0;
  [...body].reverse().forEach((char, index) => {
    let digit = Number(char);
    if (index % 2 === 0) { digit *= 2; if (digit > 9) digit -= 9; }
    total += digit;
  });
  return body + String((10 - (total % 10)) % 10);
}

/** Una orden con su cotización publicada, hecha por la API como lo haría el panel. */
async function orderWithPublishedQuote(page: Page, label: string) {
  const must = async (method: string, url: string, body?: unknown, expected = [200, 201]) => {
    const res = await api(page, method, url, body);
    expect(expected, `${method} ${url} → ${res.status} ${JSON.stringify(res.data)}`).toContain(res.status);
    return res.data;
  };
  const context = await must("GET", `${INTERNAL}/service/context/`);
  const customer = await must("POST", "/api/admin/customers/", {
    customer_type: "person", first_name: "Seguimiento", last_name: `${label} ${RUN}`,
    notes: `${MARK} cliente de ${RUN}`,
  });
  const device = await must("POST", `${INTERNAL}/service/devices/`, {
    customer_id: customer.id, device_type: "phone", brand: "Prueba", model: `${label} ${RUN}`,
    serial_number: `E2E${RUN}${label}`.toUpperCase().slice(0, 20), imei: imei(label.length),
    notes: `${MARK} equipo de ${RUN}`,
  });
  const order = await must("POST", `${INTERNAL}/service/orders/`, {
    customer_id: customer.id, device_id: device.id, branch_id: context.available_branches[0].id,
    reported_issue: `${MARK} ${label} ${RUN}: no enciende`, physical_condition: "", received_accessories: "",
  });
  const base = `${INTERNAL}/service/orders/${order.id}`;
  await must("POST", `${base}/transition/`, { status: "diagnosing" });
  const diagnostic = await must("POST", `${base}/diagnostics/`, {
    description: "No carga.", recommended_action: "Cambiar el conector.",
  });
  const quote = await must("POST", `${base}/quotes/`, { diagnostic_id: diagnostic.id });
  await must("POST", `${base}/quotes/${quote.id}/items/`, {
    item_type: "labor", description: `Cambio de conector ${RUN}`, quantity: "1", unit_price: "120.00",
  });
  await must("POST", `${base}/quotes/${quote.id}/publish/`);
  // El estado no trae el enlace: se pide aparte, y queda registrado quién lo pidió.
  const status = await must("GET", `${base}/tracking-link/`);
  expect(status.active).toBe(true);
  expect(JSON.stringify(status)).not.toContain("seguimiento");
  const link = await must("POST", `${base}/tracking-link/reveal/`);
  return { order, quote, device, path: link.path as string };
}

test("un equipo sin cuenta se sigue por su enlace y la cotización se aprueba desde ahí", async ({ page, browser, baseURL }) => {
  test.setTimeout(180_000);
  await page.goto("/admin");
  const { order, device, path } = await orderWithPublishedQuote(page, "ENLACE");
  expect(path).toMatch(/^\/seguimiento\/[A-Za-z0-9_-]{43}$/);
  expect(path).not.toContain(String(order.id));

  // El cliente: un navegador sin ninguna sesión.
  const guest = await browser.newContext({ baseURL });
  const visitor = await guest.newPage();
  const errors: string[] = [];
  visitor.on("pageerror", (error) => errors.push(String(error)));
  await visitor.goto(path, { waitUntil: "networkidle" });

  await expect(visitor.getByRole("heading", { name: order.number })).toBeVisible({ timeout: 20_000 });
  await expect(visitor.getByText(`Cambio de conector ${RUN}`)).toBeVisible();
  await expect(visitor.getByTestId("quote-total")).toContainText("120.00");
  // El IMEI no viaja entero: sólo sus últimos dígitos.
  const body = await visitor.locator("main").innerText();
  expect(body).not.toContain(device.imei);
  expect(body).toContain(String(device.imei).slice(-4));
  expect(await visitor.locator('meta[name="robots"]').getAttribute("content")).toContain("noindex");

  await visitor.getByRole("button", { name: "Aprobar cotización" }).click();
  await visitor.getByRole("button", { name: "Sí, aprobar" }).click();
  await expect(visitor.getByText(/Aprobaste esta cotización/)).toBeVisible();
  expect(errors).toEqual([]);

  // Un enlace alterado no dice nada de la orden.
  const middle = path.length - 20;
  const altered = `${path.slice(0, middle)}${path[middle] === "A" ? "B" : "A"}${path.slice(middle + 1)}`;
  await visitor.goto(altered, { waitUntil: "networkidle" });
  await expect(visitor.getByText("Este enlace no está disponible")).toBeVisible();
  await guest.close();

  // El taller ve que respondió el cliente, y por dónde.
  await page.goto(`/admin/service/orders/${order.id}`, { waitUntil: "networkidle" });
  await expect(page.getByText(/El cliente aprobó/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/Enlace de seguimiento ·/)).toBeVisible();
  await expect(page.getByLabel("Enlace de seguimiento")).toHaveCount(0);
  await page.getByRole("button", { name: "Mostrar enlace" }).click();
  await expect(page.getByLabel("Enlace de seguimiento")).toHaveValue(new RegExp(`${path}$`));
});

test("el personal anota una aprobación por teléfono y el ticket queda disponible", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/admin");
  const { order, quote } = await orderWithPublishedQuote(page, "LLAMADA");

  await page.goto(`/admin/service/orders/${order.id}`, { waitUntil: "networkidle" });
  const channel = page.getByLabel("¿Por dónde respondió?");
  await expect(channel).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Registrar aprobación" })).toBeDisabled();
  await channel.selectOption("phone");
  await page.getByLabel("Nota (opcional)").fill(`${MARK} llamó a las 10:15`);
  await page.getByRole("button", { name: "Registrar aprobación" }).click();
  await page.getByRole("button", { name: "Sí", exact: true }).click();

  // Lo que queda escrito: quién lo anotó y por dónde llegó.
  await expect(page.getByText(/Aprobación registrado por/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/· Llamada ·/)).toBeVisible();
  // El diálogo de impresión puede no abrirse en un navegador sin pantalla: en
  // cualquiera de los dos casos la pantalla lo dice y el botón sigue ahí.
  await expect(page.getByText(/Ticket enviado a la impresora|el ticket se descargó/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Imprimir ticket" })).toBeVisible();

  // El ticket es un PDF que sólo existe porque el servidor confirmó la aprobación.
  const ticket = await page.request.get(`${INTERNAL}/service/orders/${order.id}/quotes/${quote.id}/ticket/?formato=ticket80`);
  expect(ticket.status()).toBe(200);
  expect(ticket.headers()["content-type"]).toBe("application/pdf");
  expect((await ticket.body()).subarray(0, 4).toString()).toBe("%PDF");
});

test("«Registrar equipo»: cada equipo entra con su serie y su IMEI, y el stock y el tablero lo cuentan", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/admin");
  // Un producto nuevo, todavía SIN seguimiento por serie y con stock cero.
  const created = await api(page, "POST", "/api/admin/products/", {
    name: `${MARK} Equipo serializado ${RUN}`, price: "999.00", description: "", is_active: true,
  });
  expect(created.status, JSON.stringify(created.data)).toBe(201);
  const productId = created.data.id as number;
  const before = await api(page, "GET", "/api/me/internal-dashboard/");
  const availableBefore = before.data.inventory.equipment_available as number;
  const serials = [`E2E${RUN}A`.toUpperCase(), `E2E${RUN}B`.toUpperCase()];
  const imeis = [imei(1), imei(2)];

  try {
    await page.goto("/admin/inventory/units", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "+ Registrar equipo" }).click();

    // Los campos que identifican al equipo están a la vista ANTES de elegir producto.
    await expect(page.getByLabel(/^Número de serie/)).toBeVisible();
    await expect(page.getByLabel(/^IMEI \*/)).toBeVisible();
    await expect(page.getByLabel(/^IMEI 2/)).toBeVisible();

    const branch = page.getByLabel("Sucursal");
    if ((await branch.inputValue()) === "") await branch.selectOption({ index: 1 });
    await page.getByLabel("Producto / modelo").selectOption(String(productId));

    // El producto no lleva serie todavía: la pantalla lo dice y ofrece activarlo.
    await expect(page.getByRole("note")).toContainText("se controla por cantidad");
    await expect(page.getByRole("button", { name: "Guardar equipo" })).toBeDisabled();
    await page.getByLabel("Es un equipo con línea celular (lleva IMEI)").check();
    await page.getByRole("button", { name: "Activar seguimiento por serie" }).click();
    await expect(page.getByRole("note")).toHaveCount(0);

    // --- equipo 1: con un IMEI mal copiado primero ---------------------------
    await page.getByLabel(/^Motivo o documento de ingreso/).fill(`${MARK} compra de ${RUN}`);
    await page.getByLabel(/^Número de serie/).fill(serials[0]);
    await page.getByLabel(/^IMEI \*/).fill("356938035643800");
    await page.getByRole("button", { name: "Guardar y añadir otro" }).click();
    // El error aparece en SU campo, y lo escrito no se pierde.
    await expect(page.getByLabel(/^IMEI \*/)).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel(/^Número de serie/)).toHaveValue(serials[0]);

    await page.getByLabel(/^IMEI \*/).fill(imeis[0]);
    await page.getByRole("button", { name: "Guardar y añadir otro" }).click();
    const done = page.getByRole("list", { name: "Equipos registrados ahora" });
    await expect(done.getByRole("listitem")).toHaveCount(1);
    await expect(done).toContainText(serials[0]);
    await expect(page.getByLabel(/^Número de serie/)).toHaveValue("");

    // --- equipo 2: el mismo modelo es OTRO registro --------------------------
    await page.getByLabel(/^Número de serie/).fill(serials[1]);
    await page.getByLabel(/^IMEI \*/).fill(imeis[1]);
    await page.getByRole("button", { name: "Guardar equipo" }).click();

    // La tabla: una fila por equipo, cada una con su serie y su IMEI.
    for (const [index, serial] of serials.entries()) {
      const row = page.getByRole("row").filter({ has: page.getByRole("cell", { name: serial }) });
      await expect(row).toHaveCount(1);
      await expect(row).toContainText(imeis[index]);
      await expect(row).toContainText("Disponible");
    }

    // El stock del producto ES la cantidad de equipos, y el tablero los cuenta.
    const stock = await api(page, "GET", `/api/admin/inventory/stock/?branch=all&product=${productId}`);
    const total = (stock.data.results as Array<{ quantity: number }>).reduce((sum, r) => sum + r.quantity, 0);
    expect(total).toBe(2);
    const after = await api(page, "GET", "/api/me/internal-dashboard/");
    expect(after.data.inventory.equipment_available).toBe(availableBefore + 2);
    await page.goto("/admin", { waitUntil: "networkidle" });
    await expect(page.getByText("Equipos disponibles")).toBeVisible();

    // Por cantidad no se toca: el servidor lo rechaza y dice por qué.
    const typed = await api(page, "POST", "/api/admin/inventory/movements/", {
      product_id: productId, movement_type: "manual_entry", quantity: 5, reason: `${MARK} no debería`,
    });
    expect(typed.status).toBe(400);
    expect(String(typed.data.detail)).toContain("serie");
  } finally {
    const listed = await api(page, "GET", `/api/admin/inventory/units/?branch=all&product=${productId}&status=available`);
    for (const unit of listed.data?.results ?? []) {
      await api(page, "POST", `/api/admin/inventory/units/${unit.id}/write-off/`, { reason: `${MARK} limpieza de ${RUN}` });
    }
    await api(page, "PATCH", `/api/admin/products/${productId}/`, { is_active: false });
  }
});

test("la tienda decide qué familias ilustra su portada; el carrusel se arrastra y lleva al producto", async ({ page }) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/admin");
  const categories = (await api(page, "GET", "/api/admin/categories/")).data as Array<{
    id: number; name: string; show_on_home: boolean; is_active: boolean;
  }>;
  const target = categories.find((c) => c.is_active && c.show_on_home);
  test.skip(!target, "la base de pruebas no tiene una categoría en la portada");
  const families = page.locator("section", { has: page.getByRole("heading", { name: "Encuentra lo que necesitas." }) });

  try {
    await page.goto("/admin/products/categories", { waitUntil: "networkidle" });
    const toggle = page.getByLabel(`Mostrar «${target!.name}» en la portada`);
    await expect(toggle).toBeChecked({ timeout: 20_000 });
    await toggle.click();
    await expect(toggle).not.toBeChecked();

    await page.goto("/", { waitUntil: "networkidle" });
    await expect(families.getByRole("heading", { level: 3, name: target!.name })).toHaveCount(0);
  } finally {
    await page.goto("/admin");
    await api(page, "PATCH", `/api/admin/categories/${target!.id}/`, { show_on_home: true });
  }

  await page.goto("/", { waitUntil: "networkidle" });
  await expect(families.getByRole("heading", { level: 3, name: target!.name })).toBeVisible();

  // --- carrusel: se arrastra con el ratón, y soltar no abre un producto ---
  const carousel = page.getByRole("region", { name: "Productos destacados" });
  await carousel.scrollIntoViewIfNeeded();
  const track = carousel.locator("[data-carousel-track]");
  await track.focus();
  await page.keyboard.press("Home");
  await expect.poll(() => track.evaluate((el) => el.scrollLeft)).toBeLessThan(2);
  const scrollable = await track.evaluate((el) => el.scrollWidth - el.clientWidth);
  const home = page.url();
  if (scrollable > 120) {
    const box = (await track.boundingBox())!;
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width * 0.7, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.4, y, { steps: 8 });
    await expect(track).toHaveAttribute("data-dragging", "true");
    await page.mouse.up();
    await expect.poll(() => track.evaluate((el) => el.scrollLeft)).toBeGreaterThan(60);
    expect(page.url(), "soltar tras arrastrar no navega").toBe(home);
    await expect(track).not.toHaveAttribute("data-dragging", "true");
  }

  // La rueda vertical sobre la fila sigue moviendo la página.
  const before = await page.evaluate(() => window.scrollY);
  const box = (await track.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, 300);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before);

  // Y cada tarjeta sigue siendo un enlace a su producto.
  await carousel.scrollIntoViewIfNeeded();
  const link = carousel.getByRole("link").first();
  const href = await link.getAttribute("href");
  expect(href).toMatch(/^\/product\//);
  await link.click();
  await page.waitForURL((url) => url.pathname === href, { timeout: 30_000 });
});
