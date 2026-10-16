import { test, expect, type Page, type Request } from "@playwright/test";

/**
 * DEVICE-COMMERCE-01 · DC2 — la página de un modelo, en navegador real.
 *
 * QUÉ ES REAL Y QUÉ NO
 * --------------------
 * Real: el navegador, la página `/modelo/<slug>` y su comportamiento con ratón y
 * teclado. No real: el modelo y el carrito. Las dos rutas públicas que la página
 * usa —`/api/catalog/models/<slug>/` y `/api/cart/add/`— se sirven aquí con
 * `page.route`, para comprobar la PÁGINA sin depender de lo que tenga sembrado
 * el backend local. El contrato de esas rutas lo prueban los tests de Django.
 *
 * Las etiquetas de las líneas NO son las de la plataforma: si la página pintara
 * las suyas en vez de las de la API, esta prueba no las encontraría.
 */

const SLUG = `equipo-e2e-${Date.now().toString(36)}`;
const LINE_NEW = "De caja";
const LINE_USED = "Reacondicionados";
const USED_UNIT = "0b4c7a52-2222-4a6e-9b1c-0000000000e2";

/** A 1×1 PNG, so every image of the page resolves without the network. */
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

const variant = (product_id: number, capacity: number, color: number, price: string, available: boolean) => ({
  product_id,
  slug: `${SLUG}-${product_id}`,
  name: `Equipo E2E ${product_id}`,
  options: { "3": capacity, "4": color },
  price,
  image_url: `/e2e-img/${product_id}.png`,
  images: [],
  available,
  availability: available ? "own_stock" : "none",
  sells_by_unit: true,
});

const MODEL = {
  slug: SLUG,
  name: "Equipo E2E",
  description: "Modelo servido por la prueba.",
  image_url: "/e2e-img/model.png",
  category: null,
  attributes: [
    {
      id: 3, name: "Capacidad", kind: "text", sort_order: 0,
      options: [
        { id: 11, label: "128 GB", swatch_hex: "", sort_order: 0 },
        { id: 12, label: "256 GB", swatch_hex: "", sort_order: 1 },
      ],
    },
    {
      id: 4, name: "Color", kind: "color", sort_order: 1,
      options: [
        { id: 21, label: "Azul", swatch_hex: "#1F3A93", sort_order: 0 },
        { id: 22, label: "Blanco", swatch_hex: "#FFFFFF", sort_order: 1 },
      ],
    },
  ],
  lines: { new: { label: LINE_NEW, count: 3 }, used: { label: LINE_USED, count: 1 } },
  variants: [
    variant(41, 11, 21, "3999.00", true),
    variant(42, 11, 22, "3999.00", true),
    variant(43, 12, 21, "4499.00", true),
    // Exists but cannot be sold: the swatch must be shown disabled.
    variant(44, 12, 22, "4499.00", false),
  ],
  used_units: [
    {
      id: USED_UNIT, product_id: 52,
      options: { Capacidad: "128 GB", Color: "Azul" },
      battery_health: null,
      cosmetic_grade: { code: "A", label: "Excelente", description: "Sin marcas visibles." },
      price: "2599.00", photos: [], notes: "", imei_hint: "•••• 1234",
    },
  ],
  as_of: new Date().toISOString(),
};

/** Serves the model and records every cart write. */
async function mockApi(page: Page) {
  const cartWrites: Request[] = [];
  await page.route("**/e2e-img/**", (route) => route.fulfill({ status: 200, contentType: "image/png", body: PIXEL }));
  await page.route(new RegExp(`/api/catalog/models/${SLUG}/?(\\?.*)?$`), (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MODEL) }),
  );
  await page.route(/\/api\/cart\/add\/?$/, (route) => {
    cartWrites.push(route.request());
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: 1 }) });
  });
  return cartWrites;
}

const price = (page: Page) => page.getByTestId("model-price");

test.describe("DC2 · página de modelo", () => {
  test("elegir capacidad y color cambia el precio; lo que no se vende se ve deshabilitado", async ({ page }) => {
    await mockApi(page);
    await page.goto(`/modelo/${SLUG}`);

    await expect(page.getByRole("heading", { level: 1, name: "Equipo E2E" })).toBeVisible();
    await expect(page.getByRole("tab", { name: LINE_NEW })).toHaveAttribute("aria-selected", "true");
    await expect(price(page)).toHaveText("S/ 3999.00");

    const capacity = page.getByRole("radiogroup", { name: "Capacidad" });
    const colour = page.getByRole("radiogroup", { name: /^Color/ });

    await capacity.getByRole("radio", { name: "256 GB" }).click();
    await expect(price(page)).toHaveText("S/ 4499.00");

    // Blanco 256 GB exists but is not available: visible, disabled, and said.
    const blanco = colour.getByRole("radio", { name: "Blanco, no disponible" });
    await expect(blanco).toBeVisible();
    await expect(blanco).toHaveAttribute("aria-disabled", "true");
    // A disabled option ignores the click (forced: Playwright never clicks a disabled control).
    await blanco.click({ force: true });
    await expect(colour.getByRole("radio", { name: "Azul" })).toHaveAttribute("aria-checked", "true");

    // Back to 128 GB with the keyboard: Blanco becomes available again.
    await capacity.getByRole("radio", { name: "256 GB" }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(capacity.getByRole("radio", { name: "128 GB" })).toHaveAttribute("aria-checked", "true");
    await expect(price(page)).toHaveText("S/ 3999.00");
    await colour.getByRole("radio", { name: "Blanco" }).click();
    await expect(colour).toHaveAccessibleName(/Blanco/);
  });

  test("«Agregar al carrito» manda la variante, sin equipo", async ({ page }) => {
    const writes = await mockApi(page);
    await page.goto(`/modelo/${SLUG}`);
    await page.getByRole("radiogroup", { name: "Capacidad" }).getByRole("radio", { name: "256 GB" }).click();
    await page.getByRole("button", { name: "Agregar al carrito" }).click();
    await expect(page.getByRole("status")).toContainText("Agregado al carrito.");
    expect(writes).toHaveLength(1);
    const body = writes[0].postDataJSON();
    expect(body).toMatchObject({ product: 43, quantity: 1 });
    expect(body).not.toHaveProperty("unit");
  });

  test("un seminuevo sin lectura de batería dice «no informado», y comprarlo manda ese equipo", async ({ page }) => {
    const writes = await mockApi(page);
    await page.goto(`/modelo/${SLUG}`);
    await page.getByRole("tab", { name: LINE_USED }).click();

    const card = page.getByRole("article", { name: "128 GB · Azul" });
    await expect(card.getByText("Batería: no informado")).toBeVisible();
    await expect(card.getByText("IMEI termina en 1234")).toBeVisible();
    await expect(card).not.toContainText(/Batería: \d/);

    await card.getByRole("button", { name: "Comprar este equipo" }).click();
    await expect(page.getByRole("status")).toContainText("Equipo agregado al carrito");
    expect(writes).toHaveLength(1);
    expect(writes[0].postDataJSON()).toMatchObject({ product: 52, quantity: 1, unit: USED_UNIT });
  });

  test("a 320 px no hay desplazamiento horizontal", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 760 });
    await mockApi(page);
    await page.goto(`/modelo/${SLUG}`);
    await expect(price(page)).toHaveText("S/ 3999.00");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
