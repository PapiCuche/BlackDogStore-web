import { expect, test, type Locator, type Page, type Request } from "@playwright/test";

/**
 * V4 — la página de modelo de la vista previa (`/v4/modelo/<slug>`), con un navegador de verdad.
 *
 * QUÉ ES REAL Y QUÉ NO
 * --------------------
 * Real: `next dev` y el backend del entorno de pruebas (la configuración de la tienda, la portada `/v4`, el
 * diseño elegido), el navegador, el teclado y el ratón. No real: el modelo y el carrito. Como en
 * `device-commerce.spec.ts`, las rutas públicas que la página pide desde el NAVEGADOR —el modelo, el alta en el
 * carrito y las fotos— se sirven aquí con `page.route`: la prueba necesita a la vez agotados, combinaciones que no
 * existen, SIM y región, seminuevos con y sin fotos subidas y accesorios, y un backend local no los tiene. El
 * contrato de esas rutas lo prueban los tests de Django.
 *
 * El servidor no conoce el slug (es único por pasada), así que el primer HTML dice «Modelo no encontrado» y el
 * navegador, que siempre vuelve a leer el modelo (`useCatalogModel`), pinta el de la prueba. Lo que se comprueba
 * es lo que hace la página con ese modelo.
 *
 * Nada se compra de verdad: «Comprar este equipo» y «Agregar al carrito» llegan a una ruta simulada.
 *
 * Las etiquetas de las líneas y de los atributos NO son las de la plataforma: si la página pintara las suyas en
 * vez de las de la API, esta prueba no las encontraría.
 */

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";

const RUN = Date.now().toString(36);
const SLUG = `v4-config-e2e-${RUN}`;
/** A second model: new line only, no accessories. */
const SLUG_PLAIN = `v4-plain-e2e-${RUN}`;
const NAME = "Equipo V4 E2E";
const LINE_NEW = "De caja";
const LINE_USED = "Reacondicionados";
const UNIT_A = "0b4c7a52-2222-4a6e-9b1c-0000000000a1";
const UNIT_B = "0b4c7a52-2222-4a6e-9b1c-0000000000b2";

/** A 1×1 PNG, so every picture of the page resolves without the network. */
const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

/** A picture uploaded from the panel (`isManagedStorefrontImage`). */
const managed = (n: number) => `/api/storefront/images/${n.toString(16).padStart(32, "0")}`;
/** A picture the shop did not upload: «Imagen referencial». */
const external = (name: string) => `https://img.e2e-v4.invalid/${name}.png`;

const img = (url: string, primary = false) => ({ url, alt_text: "", is_primary: primary, width: 800, height: 800 });

// Attribute and option ids. `sort_order` is deliberately the REVERSE of the roles' order: the page must read
// capacity → colour → SIM → region whatever the company's sort_order says.
const CAP = 3, COL = 4, SIM = 5, REG = 6;
const C128 = 11, C256 = 12, C512 = 13;
const AZUL = 21, BLANCO = 22, ROJO = 23;
const ESIM = 31, FISICA = 32;
const US = 41, JP = 42;

const LABELS: Record<number, string> = {
  [C128]: "128 GB", [C256]: "256 GB", [C512]: "512 GB",
  [AZUL]: "Azul", [BLANCO]: "Blanco", [ROJO]: "Rojo",
  [ESIM]: "eSIM", [FISICA]: "Física + eSIM",
  [US]: "EE. UU.", [JP]: "Japón",
};

function variant(
  product_id: number, [cap, col, sim, reg]: [number, number, number, number], price: string, available: boolean,
  pictures: { images?: string[]; image_url?: string } = {},
) {
  return {
    product_id,
    slug: `${SLUG}-${product_id}`,
    name: `${NAME} ${[cap, col, sim, reg].map((id) => LABELS[id]).join(" ")}`,
    options: { [CAP]: cap, [COL]: col, [SIM]: sim, [REG]: reg },
    price,
    image_url: pictures.image_url ?? "",
    images: (pictures.images ?? []).map((url, at) => img(url, at === 0)),
    available,
    availability: available ? "own_stock" : "none",
    sells_by_unit: true,
  };
}

const VARIANTS = [
  variant(101, [C128, AZUL, ESIM, US], "3999.00", true, { images: [managed(1), managed(2)] }),
  // No picture of its own: the gallery falls back to `image_url`, a supplier's — «Imagen referencial».
  variant(102, [C128, BLANCO, ESIM, US], "3999.00", true, { image_url: external("blanco") }),
  variant(103, [C256, AZUL, ESIM, US], "4499.00", true, { images: [managed(3)] }),
  // Exists but cannot be sold: chosen, priced, said «Agotado», and its button adds nothing.
  variant(104, [C256, BLANCO, ESIM, US], "4499.00", false, { images: [managed(4)] }),
  variant(105, [C512, ROJO, ESIM, US], "5499.00", true, { images: [managed(5)] }),
  variant(106, [C128, AZUL, FISICA, JP], "3799.00", true, { images: [managed(6)] }),
  variant(107, [C128, AZUL, ESIM, JP], "3899.00", true, { images: [managed(7)] }),
];

const option = (id: number, sort_order: number, swatch_hex = "") => ({ id, label: LABELS[id], swatch_hex, sort_order });

const ATTRIBUTES = [
  { id: REG, name: "Región", kind: "text", role: "region", sort_order: 0, options: [option(US, 0), option(JP, 1)] },
  { id: SIM, name: "SIM", kind: "text", role: "sim", sort_order: 1, options: [option(ESIM, 0), option(FISICA, 1)] },
  {
    id: COL, name: "Color", kind: "color", role: "color", sort_order: 2,
    options: [option(AZUL, 0, "#1F3A93"), option(BLANCO, 1, "#FFFFFF"), option(ROJO, 2, "#B91C1C")],
  },
  {
    id: CAP, name: "Capacidad", kind: "text", role: "capacity", sort_order: 3,
    options: [option(C128, 0), option(C256, 1), option(C512, 2)],
  },
];

const MODEL = {
  slug: SLUG,
  name: NAME,
  description: "Modelo servido por la prueba.",
  image_url: managed(1),
  category: { slug: "e2e-v4", name: "Teléfonos E2E" },
  attributes: ATTRIBUTES,
  lines: { new: { label: LINE_NEW, count: VARIANTS.length }, used: { label: LINE_USED, count: 2 } },
  variants: VARIANTS,
  used_units: [
    {
      id: UNIT_A, product_id: 201,
      options: { Capacidad: "128 GB", Color: "Azul" },
      battery_health: 91,
      cosmetic_grade: { code: "A", label: "Muy bueno", description: "Marcas leves en el marco." },
      price: "2599.00",
      photos: [managed(11), managed(12)].map((url) => ({ url, alt_text: "", width: 800, height: 800 })),
      notes: "", imei_hint: "•••• 1234",
    },
    {
      id: UNIT_B, product_id: 202,
      options: { Capacidad: "256 GB", Color: "Rojo" },
      battery_health: null,
      cosmetic_grade: null,
      price: "2899.00",
      // Not uploaded from the panel: it is not presented as «this device».
      photos: [{ url: external("seminuevo"), alt_text: "", width: 800, height: 800 }],
      notes: "", imei_hint: "",
    },
  ],
  accessories: [
    {
      id: 901, slug: `funda-${SLUG}`, name: "Funda E2E", price: "99.00", image_url: "",
      images: [img(managed(21), true)], available: true, availability: "own_stock", sells_by_unit: false,
    },
    {
      id: 902, slug: `cargador-${SLUG}`, name: "Cargador E2E", price: "129.00", image_url: external("cargador"),
      images: [], available: false, availability: "none", sells_by_unit: false,
    },
  ],
  as_of: new Date().toISOString(),
};

const MODEL_PLAIN = {
  ...MODEL,
  slug: SLUG_PLAIN,
  name: "Equipo V4 E2E sencillo",
  lines: { new: { label: LINE_NEW, count: VARIANTS.length }, used: { label: LINE_USED, count: 0 } },
  used_units: [],
  accessories: [],
};

/** Serves both models, the pictures and the cart; records every cart write. */
async function mockApi(page: Page) {
  const cartWrites: Request[] = [];
  const png = { status: 200, contentType: "image/png", body: PIXEL };
  // A panel picture is requested through the image optimizer
  // (`/_next/image?url=%2Fapi%2Fstorefront%2Fimages%2F…`), and the optimizer
  // fetches the original on the SERVER, where `page.route` cannot reach. So the
  // browser's request to the optimizer is the one answered here.
  await page.route(/\/_next\/image\?url=%2Fapi%2Fstorefront%2Fimages%2F[0-9a-f]{32}&/, (route) => route.fulfill(png));
  await page.route("https://img.e2e-v4.invalid/**", (route) => route.fulfill(png));
  for (const model of [MODEL, MODEL_PLAIN]) {
    await page.route(new RegExp(`/api/catalog/models/${model.slug}/?(\\?.*)?$`), (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(model) }),
    );
  }
  await page.route(/\/api\/cart\/add\/?$/, (route) => {
    cartWrites.push(route.request());
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: 1 }) });
  });
  return cartWrites;
}

async function openModel(page: Page, slug = SLUG, name = NAME) {
  await page.goto(`/v4/modelo/${slug}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
}

const summary = (page: Page) => page.getByTestId("v4-summary");
const price = (page: Page) => summary(page).getByTestId("v4-price");
const announcement = (page: Page) => page.getByTestId("v4-choice-announcement");
const group = (page: Page, name: string | RegExp) => page.getByRole("radiogroup", { name });
const gallery = (page: Page) => page.getByTestId("v4-gallery");

/** The fact `term` of the summary (`<dt>term</dt><dd>…</dd>`). */
function fact(page: Page, term: string): Locator {
  return summary(page).locator("dt", { hasText: new RegExp(`^${term}$`) }).locator("xpath=following-sibling::dd[1]");
}

function announced(variantName: string, money: string, availability: string) {
  return `${variantName}: S/ ${money}, ${availability}`;
}

const VIEWPORTS = [
  { label: "teléfono", width: 390, height: 844 },
  { label: "escritorio", width: 1280, height: 800 },
] as const;

for (const viewport of VIEWPORTS) {
  test.describe(`V4 · página de modelo · ${viewport.label} (${viewport.width}×${viewport.height})`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test("línea nueva: los chips con teclado y ratón cambian precio, disponibilidad y anuncio", async ({ page }) => {
      await mockApi(page);
      await openModel(page);

      // The condition is a radio group with the API's labels; the new line comes first.
      const condition = group(page, "Condición");
      await expect(condition.getByRole("radio", { name: LINE_NEW })).toHaveAttribute("aria-checked", "true");
      await expect(condition.getByRole("radio", { name: LINE_USED })).toHaveAttribute("aria-checked", "false");

      // The pickers in the roles' order, whatever the company's sort_order.
      const names = await page.getByRole("radiogroup").evaluateAll((groups) =>
        groups.map((g) => document.getElementById(g.getAttribute("aria-labelledby") ?? "")?.childNodes[0]?.textContent?.trim()),
      );
      expect(names).toEqual(["Condición", "Capacidad", "Color", "SIM", "Región"]);

      // First available combination.
      const first = VARIANTS[0];
      await expect(price(page)).toHaveText("S/ 3999.00");
      await expect(fact(page, "Opción")).toHaveText(first.name);
      await expect(fact(page, "Condición")).toHaveText(LINE_NEW);
      await expect(fact(page, "Disponibilidad")).toHaveText("En stock");
      await expect(announcement(page)).toHaveText(announced(first.name, "3999.00", "En stock"));
      await expect(announcement(page)).toHaveAttribute("aria-live", "polite");
      await expect(announcement(page)).toHaveAttribute("aria-atomic", "true");
      await expect(summary(page).getByRole("button", { name: "Agregar al carrito" })).toBeEnabled();
      // Panel photos carry no caption; two of them, two thumbnails.
      await expect(gallery(page).locator(".v4-image-label")).toHaveCount(0);
      await expect(gallery(page).getByRole("group", { name: "Elegir foto" }).getByRole("button")).toHaveCount(2);

      // --- capacity with the keyboard: one tab stop, arrows move AND choose -------------------------------
      const capacity = group(page, "Capacidad");
      await expect(capacity.locator('[role="radio"][tabindex="0"]')).toHaveCount(1);
      await capacity.getByRole("radio", { name: "128 GB" }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(capacity.getByRole("radio", { name: "256 GB" })).toBeFocused();
      await expect(capacity.getByRole("radio", { name: "256 GB" })).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 4499.00");
      await expect(announcement(page)).toHaveText(announced(VARIANTS[2].name, "4499.00", "En stock"));

      // End → 512 GB: only Rojo exists there; the colour re-fits to it.
      await page.keyboard.press("End");
      await expect(capacity.getByRole("radio", { name: "512 GB" })).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 5499.00");
      const colour = group(page, /^Color/);
      await expect(colour.getByRole("radio", { name: "Rojo" })).toHaveAttribute("aria-checked", "true");
      await expect(colour).toHaveAccessibleName(/Rojo/);
      // CATALOG-REFERENCE-01 · PR D: Azul does not exist in 512 GB but can be chosen — the capacity moves to the
      // nearest one with Azul (256 GB) and the page says so, visibly and in the live region.
      const azul = colour.getByRole("radio", { name: "Azul" });
      await expect(azul).not.toHaveAttribute("aria-disabled", "true");
      await azul.click();
      await expect(capacity.getByRole("radio", { name: "256 GB" })).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 4499.00");
      await expect(page.getByTestId("v4-choice-adjusted")).toHaveText("Capacidad cambió a 256 GB: 512 GB no está disponible en Azul.");
      await expect(announcement(page)).toContainText("Capacidad cambió a 256 GB");

      // Home → back to 128 GB; Azul exists there and is kept.
      await capacity.getByRole("radio", { name: "256 GB" }).focus();
      await page.keyboard.press("Home");
      await expect(capacity.getByRole("radio", { name: "128 GB" })).toHaveAttribute("aria-checked", "true");
      await expect(colour.getByRole("radio", { name: "Azul" })).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 3999.00");

      // --- colour: arrows skip the option with no variant (128 GB Rojo) and wrap ------------------------------
      await colour.getByRole("radio", { name: "Azul" }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(colour.getByRole("radio", { name: "Blanco" })).toBeFocused();
      await expect(colour.getByRole("radio", { name: "Blanco" })).toHaveAttribute("aria-checked", "true");
      await expect(colour).toHaveAccessibleName(/Blanco/);
      // Blanco 128 GB has no panel photo: the model's supplier picture, said to be one.
      await expect(gallery(page).locator(".v4-image-label")).toHaveText("Imagen referencial");
      // PR D: Rojo (only in 512 GB) is reachable from the keyboard too, and takes the capacity with it.
      await page.keyboard.press("ArrowRight");
      await expect(colour.getByRole("radio", { name: "Rojo" })).toBeFocused();
      await expect(capacity.getByRole("radio", { name: "512 GB" })).toHaveAttribute("aria-checked", "true");
      // Wraps to Azul (back to 256 GB, the nearest with it), then the capacity back to 128 GB.
      await page.keyboard.press("ArrowRight");
      await expect(colour.getByRole("radio", { name: "Azul" })).toBeFocused();
      await expect(colour.getByRole("radio", { name: "Azul" })).toHaveAttribute("aria-checked", "true");
      await capacity.getByRole("radio", { name: "128 GB" }).click();
      await expect(price(page)).toHaveText("S/ 3999.00");
      await expect(gallery(page).locator(".v4-image-label")).toHaveCount(0);

      // --- SIM and region: shown because the variants differ by them -------------------------------------------
      const sim = group(page, "SIM");
      const region = group(page, "Región");
      await sim.getByRole("radio", { name: "Física + eSIM" }).click();
      await expect(price(page)).toHaveText("S/ 3799.00");
      await expect(region.getByRole("radio", { name: "Japón" })).toHaveAttribute("aria-checked", "true");
      // PR D: EE. UU. does not exist with «Física + eSIM», but choosing it moves the SIM back: it is not disabled.
      await expect(region.getByRole("radio", { name: "EE. UU." })).not.toHaveAttribute("aria-disabled", "true");
      // PR D: what moved by itself is said first.
      await expect(announcement(page)).toHaveText(
        `Región cambió a Japón: EE. UU. no está disponible en Física + eSIM. ${announced(VARIANTS[5].name, "3799.00", "En stock")}`);

      await sim.getByRole("radio", { name: "eSIM", exact: true }).click();
      // Japón still exists with eSIM, so it is kept.
      await expect(region.getByRole("radio", { name: "Japón" })).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 3899.00");
      await region.getByRole("radio", { name: "Japón" }).focus();
      await page.keyboard.press("ArrowLeft");
      await expect(region.getByRole("radio", { name: "EE. UU." })).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 3999.00");

      // --- a sold-out combination: chosen, priced, said, and not sold ------------------------------------------
      await capacity.getByRole("radio", { name: "256 GB" }).click();
      const blancoSoldOut = colour.getByRole("radio", { name: "Blanco, agotado" });
      await expect(blancoSoldOut).not.toHaveAttribute("aria-disabled", "true");
      await expect(blancoSoldOut).toHaveAttribute("data-state", "sold-out");
      await blancoSoldOut.click();
      await expect(blancoSoldOut).toHaveAttribute("aria-checked", "true");
      await expect(price(page)).toHaveText("S/ 4499.00");
      await expect(fact(page, "Disponibilidad")).toHaveText("Agotado");
      await expect(summary(page).getByRole("button", { name: "Agotado" })).toBeDisabled();
      await expect(announcement(page)).toHaveText(announced(VARIANTS[3].name, "4499.00", "Agotado"));
    });

    test("«Agregar al carrito» manda la variante elegida (carrito simulado)", async ({ page }) => {
      const writes = await mockApi(page);
      await openModel(page);
      await group(page, "Capacidad").getByRole("radio", { name: "256 GB" }).click();
      await expect(price(page)).toHaveText("S/ 4499.00");
      await summary(page).getByRole("button", { name: "Agregar al carrito" }).click();
      await expect(summary(page).getByRole("status")).toContainText("Agregado al carrito.");
      expect(writes).toHaveLength(1);
      const body = writes[0].postDataJSON();
      expect(body).toMatchObject({ product: 103, quantity: 1 });
      expect(body).not.toHaveProperty("unit");
    });

    test("seminuevos: lista, filtros, resumen y «Comprar este equipo» (carrito simulado)", async ({ page }) => {
      const writes = await mockApi(page);
      await openModel(page);

      // Switching the condition with the keyboard keeps the focus on the picker (review of #308).
      const condition = group(page, "Condición");
      await condition.getByRole("radio", { name: LINE_NEW }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(condition.getByRole("radio", { name: LINE_USED })).toHaveAttribute("aria-checked", "true");
      await expect(condition.getByRole("radio", { name: LINE_USED })).toBeFocused();

      const list = page.getByRole("list", { name: "Equipos" });
      const cards = list.getByRole("button");
      await expect(cards).toHaveCount(2);
      await expect(page.getByText("2 equipos", { exact: true })).toBeVisible();
      // The new line's pickers are gone; the used line's filters are radio groups with «Todas».
      await expect(group(page, "SIM")).toHaveCount(0);
      const capFilter = group(page, "Capacidad");
      const colFilter = group(page, "Color");
      await expect(capFilter.getByRole("radio")).toHaveText(["Todas", "128 GB", "256 GB"]);
      await expect(colFilter.getByRole("radio")).toHaveText(["Todas", "Azul", "Rojo"]);
      await expect(capFilter.getByRole("radio", { name: "Todas" })).toHaveAttribute("aria-checked", "true");

      // Until one is picked, the first device is on show, with what the shop published about it.
      await expect(fact(page, "Equipo")).toHaveText("128 GB · Azul");
      await expect(fact(page, "Condición")).toHaveText(LINE_USED);
      await expect(fact(page, "Estado")).toHaveText("Muy bueno");
      await expect(fact(page, "Batería")).toHaveText("91%");
      await expect(price(page)).toHaveText("S/ 2599.00");
      await expect(gallery(page).locator(".v4-image-label")).toHaveText("Fotos de este equipo");
      await expect(announcement(page)).toHaveText(announced("128 GB · Azul", "2599.00", "En stock"));

      // Filter by keyboard: Todas → 128 GB → 256 GB.
      await capFilter.getByRole("radio", { name: "Todas" }).focus();
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("ArrowRight");
      await expect(capFilter.getByRole("radio", { name: "256 GB" })).toHaveAttribute("aria-checked", "true");
      await expect(page.getByText("1 equipo", { exact: true })).toBeVisible();
      await expect(cards).toHaveCount(1);
      await expect(fact(page, "Equipo")).toHaveText("256 GB · Rojo");
      // No grade, no battery reading: not said at all.
      await expect(fact(page, "Estado")).toHaveCount(0);
      await expect(fact(page, "Batería")).toHaveCount(0);
      await expect(price(page)).toHaveText("S/ 2899.00");
      // Its photo was not uploaded from the panel: not «Fotos de este equipo».
      await expect(gallery(page).locator(".v4-image-label")).toHaveText("Imagen referencial");
      await expect(announcement(page)).toHaveText(announced("256 GB · Rojo", "2899.00", "En stock"));

      // A filter that leaves nothing: no summary, and the live region stays mounted with nothing to say.
      await colFilter.getByRole("radio", { name: "Azul" }).click();
      await expect(page.getByText("Ningún equipo coincide con estos filtros.")).toBeVisible();
      await expect(summary(page)).toHaveCount(0);
      await expect(announcement(page)).toHaveCount(1);
      await expect(announcement(page)).toHaveText("");

      await colFilter.getByRole("radio", { name: "Todas" }).click();
      await capFilter.getByRole("radio", { name: "Todas" }).click();
      await expect(cards).toHaveCount(2);

      // Choosing a device does not buy it: it fills the summary.
      const second = cards.filter({ hasText: "256 GB · Rojo" });
      await second.click();
      await expect(second).toHaveAttribute("aria-pressed", "true");
      await expect(cards.filter({ hasText: "128 GB · Azul" })).toHaveAttribute("aria-pressed", "false");
      await expect(fact(page, "Equipo")).toHaveText("256 GB · Rojo");
      expect(writes).toHaveLength(0);

      const buy = summary(page).getByRole("button", { name: "Comprar este equipo" });
      await expect(buy).toBeEnabled();
      await buy.click();
      await expect(summary(page).getByRole("status")).toContainText("Equipo agregado al carrito");
      expect(writes).toHaveLength(1);
      expect(writes[0].postDataJSON()).toMatchObject({ product: 202, quantity: 1, unit: UNIT_B });
    });

    test("accesorios sólo cuando la API los manda", async ({ page }) => {
      await mockApi(page);
      await openModel(page);

      const block = page.getByTestId("v4-accessories");
      await expect(block).toBeVisible();
      await expect(block.getByRole("heading", { name: `Compatible con ${NAME}` })).toBeVisible();
      const funda = block.getByRole("article", { name: "Funda E2E" });
      const cargador = block.getByRole("article", { name: "Cargador E2E" });
      await expect(funda).toContainText("En stock");
      await expect(funda).toContainText("S/ 99.00");
      await expect(funda.locator(".v4-image-label")).toHaveCount(0);
      await expect(funda.getByRole("button", { name: "Agregar al carrito: Funda E2E" })).toBeEnabled();
      await expect(cargador).toContainText("Agotado");
      await expect(cargador.locator(".v4-image-label")).toHaveText("Imagen referencial");
      await expect(cargador.getByRole("button", { name: "Agotado: Cargador E2E" })).toBeDisabled();

      // A model with no accessories and one line: no block, no condition picker.
      await openModel(page, SLUG_PLAIN, MODEL_PLAIN.name);
      await expect(price(page)).toHaveText("S/ 3999.00");
      await expect(page.getByTestId("v4-accessories")).toHaveCount(0);
      await expect(page.getByText(/Compatible con/)).toHaveCount(0);
      await expect(group(page, "Condición")).toHaveCount(0);
    });

    test("chips de 44 px, foco visible y sin desbordamiento horizontal", async ({ page }) => {
      await mockApi(page);
      await openModel(page);
      await expect(price(page)).toHaveText("S/ 3999.00");

      const measure = async () => {
        const fit = await page.evaluate(() => ({
          scroll: document.scrollingElement!.scrollWidth,
          inner: window.innerWidth,
          chips: Array.from(document.querySelectorAll<HTMLElement>('[role="radiogroup"] [role="radio"]')).map((chip) => ({
            name: chip.getAttribute("aria-label") ?? chip.textContent?.trim() ?? "",
            height: chip.getBoundingClientRect().height,
            width: chip.getBoundingClientRect().width,
            right: chip.getBoundingClientRect().right,
          })),
        }));
        // Soft: an overflow is reported, and the chips are still measured.
        expect.soft(fit.scroll, `la página desborda a ${viewport.width}px`).toBeLessThanOrEqual(fit.inner);
        expect(fit.chips.length).toBeGreaterThan(0);
        for (const chip of fit.chips) {
          expect.soft(chip.height, `«${chip.name}» mide menos de 44 px de alto`).toBeGreaterThanOrEqual(44);
          expect.soft(chip.width, `«${chip.name}» mide menos de 44 px de ancho`).toBeGreaterThanOrEqual(44);
          expect.soft(chip.right, `«${chip.name}» se sale de la pantalla`).toBeLessThanOrEqual(fit.inner);
        }
      };

      await measure();

      // Focus reached with the keyboard is visible: a ring (outline or box-shadow) on the focused chip.
      await group(page, "Capacidad").getByRole("radio", { name: "128 GB" }).focus();
      await page.keyboard.press("ArrowRight");
      const ring = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const style = getComputedStyle(el);
        return {
          role: el.getAttribute("role"),
          focusVisible: el.matches(":focus-visible"),
          outline: style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0,
          shadow: style.boxShadow !== "none",
        };
      });
      expect(ring.role).toBe("radio");
      expect(ring.focusVisible).toBe(true);
      expect(ring.outline || ring.shadow, "el chip con foco no muestra anillo").toBe(true);

      // The used line too: filters and device cards.
      await group(page, "Condición").getByRole("radio", { name: LINE_USED }).click();
      await expect(page.getByRole("list", { name: "Equipos" }).getByRole("button")).toHaveCount(2);
      await measure();
      const cards = await page.getByRole("list", { name: "Equipos" }).getByRole("button").evaluateAll((els) =>
        els.map((el) => el.getBoundingClientRect().height),
      );
      for (const height of cards) expect.soft(height).toBeGreaterThanOrEqual(44);
    });

    test("la barra superior dibuja un solo logotipo", async ({ page }) => {
      await mockApi(page);
      await openModel(page);
      const logos = page.locator("header.v4-header img[data-logo]");
      test.skip((await logos.count()) === 0, "la tienda no tiene logotipos para fondo claro: la barra dice su nombre");
      // The isotype on a phone, the horizontal lockup from `lg`; never both.
      const shown = await logos.evaluateAll((imgs) =>
        // Rendered at all (no box when the image or any ancestor is `display: none`).
        imgs.filter((el) => el.getClientRects().length > 0).map((el) => el.getAttribute("data-logo")),
      );
      expect(shown).toEqual([viewport.width >= 1024 ? "desktop" : "mobile"]);
      const header = await page.locator("header.v4-header").evaluate((el) => el.scrollWidth - el.clientWidth);
      expect(header, "la barra superior desborda").toBeLessThanOrEqual(0);
    });
  });
}

test.describe("V4 · vista previa fuera de buscadores", () => {
  test("/v4 y /v4/modelo/<slug> llevan noindex y ninguna canónica mientras el diseño es V3", async ({ page, request }) => {
    const config = await (await request.get(`${API}/storefront/config/`)).json();
    test.skip(
      config?.page?.storefront_design === "v4",
      "la tienda tiene el diseño V4 elegido: la vista previa es la portada indexada",
    );
    await mockApi(page);
    for (const path of ["/v4", `/v4/modelo/${SLUG}`]) {
      await page.goto(path, { waitUntil: "networkidle" });
      const robots = page.locator('meta[name="robots"]');
      await expect(robots, `${path} sin meta robots`).toHaveCount(1);
      expect(await robots.getAttribute("content")).toMatch(/^noindex\b/);
      expect(await robots.getAttribute("content")).toContain("nofollow");
      await expect(page.locator('link[rel="canonical"]'), `${path} declara una canónica`).toHaveCount(0);
      // The preview emits no structured data of its own (the root layout's `Store` is every page's).
      const types = await page.locator('script[type="application/ld+json"]').evaluateAll((scripts) =>
        scripts.flatMap((script) => {
          const data = JSON.parse(script.textContent || "null");
          return (Array.isArray(data) ? data : [data]).map((item) => String(item?.["@type"] ?? ""));
        }),
      );
      for (const type of ["ProductGroup", "Product", "ItemList", "BreadcrumbList"]) {
        expect(types, `${path} emite ${type} siendo una vista previa`).not.toContain(type);
      }
    }
  });
});

test.describe("V4 · «Saltar al contenido»", () => {
  for (const path of ["/v4", `/v4/modelo/${SLUG}`]) {
    test(`en ${path.replace(SLUG, "<slug>")} el primer tabulador llega al enlace y lo lleva a #contenido-v4`, async ({ page }) => {
      test.skip(
        page.context().browser()?.browserType().name() === "webkit",
        "WebKit en macOS no alcanza enlaces con Tab (ver skip-link.spec.ts)",
      );
      await mockApi(page);
      await page.goto(path, { waitUntil: "networkidle" });
      await expect(page.locator("main#contenido-v4")).toBeAttached();

      await page.keyboard.press("Tab");
      const first = page.getByRole("link", { name: "Saltar al contenido" });
      await expect(first).toBeFocused();
      const box = await first.boundingBox();
      expect(box && box.width > 1 && box.height > 1, "el enlace de salto no se ve al recibir el foco").toBe(true);

      await page.keyboard.press("Enter");
      await expect.poll(() => page.evaluate(() => document.activeElement?.id ?? "")).toBe("contenido-v4");

      // The next Tab is inside the content, past the V4 top bar — when the content has something to focus (the
      // `/v4` home of a shop with no published models has nothing).
      const focusable = await page.locator("#contenido-v4").locator("a[href], button:not([disabled])").count();
      if (focusable === 0) {
        test.info().annotations.push({ type: "note", description: `${path}: el contenido no tiene nada enfocable` });
        return;
      }
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => Boolean(document.activeElement?.closest("#contenido-v4")))).toBe(true);
    });
  }
});
