import { expect, test, type Page, type Request } from "@playwright/test";

/**
 * CATALOG-REFERENCE-01 · PR D — the configurator of the PUBLIC model page (`/modelo/<slug>`, the shop in V3), with a
 * real browser: 375 px and 1280 px, mouse and keyboard.
 *
 * QUÉ ES REAL Y QUÉ NO
 * --------------------
 * Real: `next dev`, the page, its V3 header and footer, the browser, the keyboard. Not real: the model and the cart,
 * served with `page.route` as in `device-commerce.spec.ts` (the slug carries `E2E_MOCKED_MODEL_PREFIX`, so the server
 * leaves the read to the browser). The pictures are drawings made here (a rounded rectangle in the finish's colour):
 * no product photo, no licence question, nothing saved in the repository.
 *
 * What is checked:
 *  · the configurator is the V4 block and the V3 chrome is not (font and data attribute);
 *  · a colour of another capacity moves the capacity and says so; the photo becomes that finish's; a finish without
 *    pictures is «Imagen pendiente» in the SAME box (no layout shift: the box keeps its size);
 *  · the address says the variant without a navigation; reloading it opens the same variant;
 *  · «Bajo pedido» only for the variant with an offer, with its own button;
 *  · keyboard: Tab reaches each group, arrows choose, the focus ring is visible;
 *  · no horizontal overflow at 375 px; the block follows a dark theme.
 *
 * With `PRD_SCREENS=<dir>` it also saves the visual review screenshots there (never in the repository).
 */

const MOCKED_PREFIX = "equipo-e2e-";
const SLUG = `${MOCKED_PREFIX}cfg-${Date.now().toString(36)}`;
const SCREENS = process.env.PRD_SCREENS ?? "";

const CAP = 3, COL = 4;
const C128 = 11, C256 = 12, C512 = 13;
const GRAFITO = 21, ARENA = 22, NIEBLA = 23;
const HEX: Record<number, string> = { [GRAFITO]: "#3B3B3D", [ARENA]: "#C9B79C", [NIEBLA]: "#9DB4C0" };

/** A drawing of a phone in a finish's colour (served as SVG): enough to see which finish is on show. */
function drawing(colour: string, back = false): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
<rect x="270" y="90" width="260" height="620" rx="48" fill="${colour}" stroke="#00000022" stroke-width="4"/>
${back ? '<rect x="300" y="120" width="110" height="110" rx="28" fill="#00000030"/>' : '<rect x="290" y="112" width="220" height="576" rx="34" fill="#0a0a0a"/>'}
</svg>`;
}

const pic = (name: string, alt: string, primary: boolean) => ({ url: `/e2e-cfg/${name}.svg`, alt_text: alt, is_primary: primary, width: 800, height: 800 });
const FINISHES: Record<number, ReturnType<typeof pic>[]> = {
  [GRAFITO]: [pic("grafito-front", "Equipo Configurador Grafito", true), pic("grafito-back", "Equipo Configurador Grafito, dorso", false)],
  [NIEBLA]: [pic("niebla-front", "Equipo Configurador Niebla", true)],
  // Arena: no pictures yet — «Imagen pendiente».
};

function variant(id: number, cap: number, col: number, price: string, availability: "own_stock" | "supplier" | "none") {
  const gallery = FINISHES[col] ?? [];
  return {
    product_id: id,
    slug: `${SLUG}-${id}`,
    name: `Equipo Configurador ${{ [C128]: "128 GB", [C256]: "256 GB", [C512]: "512 GB" }[cap]} ${{ [GRAFITO]: "Grafito", [ARENA]: "Arena", [NIEBLA]: "Niebla" }[col]}`,
    options: { [CAP]: cap, [COL]: col },
    price,
    image_url: "",
    images: [],
    gallery,
    image_pending: gallery.length === 0,
    available: availability !== "none",
    availability,
    on_order: availability === "supplier" ? { lead_time_days: 5, price: "5899.00" } : null,
    sells_by_unit: true,
  };
}

const MODEL = {
  slug: SLUG,
  name: "Equipo Configurador",
  description: "Pantalla de 6,1 pulgadas.\n\n- Doble cámara\n- Carga inalámbrica",
  image_url: "",
  category: null,
  attributes: [
    {
      id: CAP, name: "Capacidad", kind: "text", role: "capacity", sort_order: 0,
      options: [
        { id: C128, label: "128 GB", swatch_hex: "", sort_order: 0 },
        { id: C256, label: "256 GB", swatch_hex: "", sort_order: 1 },
        { id: C512, label: "512 GB", swatch_hex: "", sort_order: 2 },
      ],
    },
    {
      id: COL, name: "Color", kind: "color", role: "color", sort_order: 1,
      options: [
        { id: GRAFITO, label: "Grafito", swatch_hex: HEX[GRAFITO], sort_order: 0 },
        { id: ARENA, label: "Arena", swatch_hex: HEX[ARENA], sort_order: 1 },
        { id: NIEBLA, label: "Niebla", swatch_hex: HEX[NIEBLA], sort_order: 2 },
      ],
    },
  ],
  lines: { new: { label: "Nuevo", count: 5 }, used: { label: "Seminuevo", count: 0 } },
  variants: [
    variant(61, C128, GRAFITO, "4299.00", "own_stock"),
    variant(62, C128, ARENA, "4299.00", "own_stock"),
    variant(63, C256, GRAFITO, "4799.00", "own_stock"),
    // Niebla exists only in 512 GB, and only from a supplier's offer.
    variant(64, C512, NIEBLA, "5799.00", "supplier"),
    variant(65, C512, GRAFITO, "5799.00", "none"),
  ],
  finish_images: Object.fromEntries(Object.entries(FINISHES).map(([key, value]) => [key, value])),
  used_units: [],
  accessories: [],
  sold_out: false,
  as_of: new Date().toISOString(),
};

async function mockApi(page: Page) {
  const writes: Request[] = [];
  await page.route("**/e2e-cfg/**", (route) => {
    const name = new URL(route.request().url()).pathname.split("/").pop()?.replace(".svg", "") ?? "";
    const [colour, side] = name.split("-");
    const hex = { grafito: HEX[GRAFITO], arena: HEX[ARENA], niebla: HEX[NIEBLA] }[colour] ?? "#888888";
    return route.fulfill({ status: 200, contentType: "image/svg+xml", body: drawing(hex, side === "back") });
  });
  await page.route(new RegExp(`/api/catalog/models/${SLUG}/?(\\?.*)?$`), (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MODEL) }));
  await page.route(/\/api\/cart\/add\/?$/, (route) => {
    writes.push(route.request());
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: 1 }) });
  });
  return writes;
}

async function open(page: Page, query = "") {
  const response = await page.goto(`/modelo/${SLUG}${query}`);
  expect(response?.status(), `arranca next dev con E2E_MOCKED_MODEL_PREFIX=${MOCKED_PREFIX}`).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "Equipo Configurador" })).toBeVisible();
}

const capacity = (page: Page) => page.getByRole("radiogroup", { name: "Capacidad" });
const colour = (page: Page) => page.getByRole("radiogroup", { name: /^Color/ });
const stage = (page: Page) => page.getByTestId("v4-stage");
const price = (page: Page) => page.getByTestId("v4-price");
const availability = (page: Page) => page.getByTestId("v4-summary").locator("dt", { hasText: "Disponibilidad" }).locator("+ dd");

/** Starts counting layout shifts that no input caused, from now on. */
async function watchShifts(page: Page) {
  await page.evaluate(() => {
    const scope = window as unknown as { __shifts: number };
    scope.__shifts = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) {
        if (!entry.hadRecentInput) scope.__shifts += entry.value;
      }
    }).observe({ type: "layout-shift" });
  });
}

/** The shifts counted since `watchShifts`, after the pictures had time to arrive. */
async function shiftsAfterPaint(page: Page): Promise<number> {
  await page.waitForTimeout(1500);
  return page.evaluate(() => (window as unknown as { __shifts: number }).__shifts);
}

async function screenshot(page: Page, name: string) {
  if (!SCREENS) return;
  // The consent banner is not part of the review: declined, as a visitor would.
  const decline = page.getByRole("button", { name: "Rechazar opcionales" });
  if (await decline.isVisible()) await decline.click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${SCREENS}/${name}.png`, fullPage: true });
}

for (const viewport of [{ width: 375, height: 812 }, { width: 1280, height: 900 }]) {
  test.describe(`configurador público a ${viewport.width} px`, () => {
    test.use({ viewport });

    test("bloque V4 en la ficha V3: color de otra capacidad, foto del acabado, imagen pendiente sin salto", async ({ page }) => {
      await mockApi(page);
      await open(page);

      // The block is V4 (Montserrat), the V3 header is not.
      const block = page.locator("[data-design='v4'][data-v4-scope='block']");
      await expect(block).toHaveCount(1);
      expect(await block.evaluate((el) => getComputedStyle(el).fontFamily)).toMatch(/montserrat|__font|--font-v4/i);
      const header = page.locator("header").first();
      expect(await header.evaluate((el) => el.closest("[data-design='v4']"))).toBeNull();

      await expect(price(page)).toHaveText("S/ 4299.00");
      await expect(stage(page).getByRole("img")).toHaveAttribute("alt", "Equipo Configurador Grafito");
      // Nothing of the page covers a chip: each one is the topmost element at its own centre (the summary never sits
      // on them). The consent banner is declined first: it is the visitor's to close, not the configurator's.
      const decline = page.getByRole("button", { name: "Rechazar opcionales" });
      if (await decline.isVisible()) await decline.click();
      for (const chip of await page.locator(".v4-chip").all()) {
        // At the top of the page, and with the chip at the bottom edge of the screen (where a sticky box would sit).
        const covered = await chip.evaluate((el) => {
          window.scrollTo(0, 0);
          let r = el.getBoundingClientRect();
          if (r.bottom > window.innerHeight) {
            window.scrollBy(0, r.bottom - window.innerHeight + 8);
            r = el.getBoundingClientRect();
          }
          const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return !(top === el || el.contains(top));
        });
        expect(covered, `«${await chip.textContent()}» queda tapado`).toBe(false);
      }
      await screenshot(page, `acabado-completo-${viewport.width}-claro`);
      // The photo box keeps its size whatever it holds (the page may scroll to the chip: compare size, not position).
      const size = async () => {
        const rect = await stage(page).boundingBox();
        return rect ? { width: Math.round(rect.width), height: Math.round(rect.height) } : null;
      };
      const box = await size();

      // Arena exists in 128 GB but has no pictures: «Imagen pendiente», same box.
      await colour(page).getByRole("radio", { name: "Arena" }).click();
      await expect(stage(page).getByRole("img", { name: /Imagen pendiente/ })).toBeVisible();
      await expect(stage(page).locator("img")).toHaveCount(0);
      expect(await size()).toEqual(box);
      await screenshot(page, `imagen-pendiente-${viewport.width}-claro`);

      // Niebla exists only in 512 GB: it can be chosen; the capacity moves and the page says so.
      await colour(page).getByRole("radio", { name: "Niebla" }).click();
      await expect(capacity(page).getByRole("radio", { name: "512 GB" })).toHaveAttribute("aria-checked", "true");
      await expect(page.getByTestId("v4-choice-adjusted")).toHaveText("Capacidad cambió a 512 GB: 128 GB no está disponible en Niebla.");
      await expect(stage(page).getByRole("img")).toHaveAttribute("alt", "Equipo Configurador Niebla");
      expect(await size()).toEqual(box);

      // Supplier offer: «Bajo pedido» with its time and price, and its own button — never «En stock».
      await expect(availability(page)).toHaveText("Bajo pedido — entrega en 5 días");
      await expect(price(page)).toHaveText("S/ 5899.00");
      await expect(page.getByRole("button", { name: "Pedir bajo pedido" })).toBeEnabled();
      await expect(page.getByTestId("v4-summary")).not.toContainText("En stock");
      await screenshot(page, `bajo-pedido-${viewport.width}-claro`);

      // The address says the variant, without a navigation; reloading opens it again.
      await expect.poll(() => new URL(page.url()).searchParams.get("variante")).toBe(`${SLUG}-64`);
      await page.reload();
      await expect(colour(page).getByRole("radio", { name: "Niebla" })).toHaveAttribute("aria-checked", "true");
      await watchShifts(page);

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, "desborde horizontal").toBeLessThanOrEqual(0);

      // Once the configurator is painted, its pictures load into reserved boxes: no layout shift while they arrive.
      // (A mocked model is read by the browser, so the server's HTML is the loading skeleton; what this measures is
      // the page after it, which is what the server's first HTML is for a real model.)
      expect(await shiftsAfterPaint(page), "desplazamientos tras pintar la ficha").toBeLessThan(0.01);
    });

    test("tema oscuro: el bloque sigue al tema y se ve igual de completo", async ({ page }) => {
      await page.emulateMedia({ colorScheme: "dark" });
      await mockApi(page);
      await open(page);
      const background = await page.locator("[data-v4-scope='block']").evaluate((el) => getComputedStyle(el).backgroundColor);
      const [r, g, b] = (background.match(/\d+/g) ?? []).map(Number);
      expect(r + g + b, `fondo del bloque en oscuro: ${background}`).toBeLessThan(150);
      await screenshot(page, `acabado-completo-${viewport.width}-oscuro`);
      await colour(page).getByRole("radio", { name: "Arena" }).click();
      await expect(stage(page).getByRole("img", { name: /Imagen pendiente/ })).toBeVisible();
      await screenshot(page, `imagen-pendiente-${viewport.width}-oscuro`);
      await colour(page).getByRole("radio", { name: "Niebla" }).click();
      await expect(availability(page)).toHaveText("Bajo pedido — entrega en 5 días");
      await screenshot(page, `bajo-pedido-${viewport.width}-oscuro`);
    });
  });
}

test.describe("al desplazarse en el teléfono", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  for (const scheme of ["light", "dark"] as const) {
    test(`la foto no es fija y nada tapa la cabecera (${scheme === "light" ? "claro" : "oscuro"})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await mockApi(page);
      await open(page);
      await colour(page).getByRole("radio", { name: "Arena" }).click();
      await expect(stage(page).getByRole("img", { name: /Imagen pendiente/ })).toBeVisible();
      const decline = page.getByRole("button", { name: "Rechazar opcionales" });
      if (await decline.isVisible()) await decline.click();

      // Below the desktop breakpoint, no ancestor of the photo box is sticky or fixed: it scrolls with the page.
      const pinned = await stage(page).evaluate((el) => {
        const found: string[] = [];
        for (let node: HTMLElement | null = el; node && node !== document.body; node = node.parentElement) {
          const position = getComputedStyle(node).position;
          if (position === "sticky" || position === "fixed") found.push(`${node.tagName}.${node.className}`);
        }
        return found;
      });
      expect(pinned, "la foto o su columna son fijas en el teléfono").toEqual([]);

      const banner = page.getByRole("banner").first();
      for (const y of [0, 400, 800]) {
        await page.evaluate((top) => window.scrollTo(0, top), y);
        await page.waitForTimeout(250);
        // The header (V3) is where it always is and is the topmost element at its centre: nothing slides over it.
        const state = await banner.evaluate((el) => {
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return { top: Math.round(r.top), height: Math.round(r.height), onTop: Boolean(hit && el.contains(hit)) };
        });
        expect(state.onTop, `cabecera tapada con desplazamiento ${y}`).toBe(true);
        expect(state.top, `cabecera fuera de su sitio con desplazamiento ${y}`).toBeLessThanOrEqual(0);
        expect(state.top + state.height).toBeGreaterThan(0);
        if (SCREENS) await page.screenshot({ path: `${SCREENS}/imagen-pendiente-375-${scheme === "light" ? "claro" : "oscuro"}-scroll${y}.png` });
      }
    });
  }

  test("en escritorio la foto sí acompaña la elección (columna fija)", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await mockApi(page);
    await open(page);
    const sticky = await stage(page).evaluate((el) => {
      for (let node: HTMLElement | null = el; node && node !== document.body; node = node.parentElement) {
        if (getComputedStyle(node).position === "sticky") return true;
      }
      return false;
    });
    expect(sticky).toBe(true);
  });
});

test.describe("teclado", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("Tab llega a cada grupo, las flechas eligen, el foco se ve y el carrito recibe la variante", async ({ page }) => {
    const writes = await mockApi(page);
    await open(page);
    const first = capacity(page).getByRole("radio", { name: "128 GB" });
    let guard = 0;
    while (!(await first.evaluate((el) => el === document.activeElement)) && guard++ < 60) await page.keyboard.press("Tab");
    await expect(first).toBeFocused();
    expect(await first.evaluate((el) => getComputedStyle(el).outlineStyle)).not.toBe("none");

    await page.keyboard.press("ArrowRight");
    await expect(capacity(page).getByRole("radio", { name: "256 GB" })).toHaveAttribute("aria-checked", "true");
    await expect(price(page)).toHaveText("S/ 4799.00");

    await page.keyboard.press("Tab");
    await expect(colour(page).getByRole("radio", { name: "Grafito" })).toBeFocused();
    await page.keyboard.press("End");
    // Niebla only in 512 GB: chosen from the keyboard too, the capacity follows and the live region says it.
    await expect(colour(page).getByRole("radio", { name: "Niebla" })).toBeFocused();
    await expect(capacity(page).getByRole("radio", { name: "512 GB" })).toHaveAttribute("aria-checked", "true");
    await expect(page.getByTestId("v4-choice-announcement")).toContainText("Capacidad cambió a 512 GB");

    await page.keyboard.press("Home");
    await expect(colour(page).getByRole("radio", { name: "Grafito" })).toHaveAttribute("aria-checked", "true");
    // Grafito 512 GB is sold out: the button says so and adds nothing.
    await expect(page.getByRole("button", { name: "Agotado" })).toBeDisabled();
    await capacity(page).getByRole("radio", { name: "128 GB" }).click();
    await page.getByRole("button", { name: "Agregar al carrito" }).press("Enter");
    await expect(page.getByRole("status").filter({ hasText: "Agregado al carrito." })).toBeVisible();
    expect(writes).toHaveLength(1);
    expect(writes[0].postDataJSON()).toMatchObject({ product: 61, quantity: 1 });
  });
});
