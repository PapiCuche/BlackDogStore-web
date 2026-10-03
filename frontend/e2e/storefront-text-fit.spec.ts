import { expect, test, type Page } from "@playwright/test";

/**
 * El texto de la tienda cabe en la pantalla — no sólo la página.
 *
 * `storefront.spec` comprueba que la página no desborde a lo ancho. Eso no ve
 * un titular recortado dentro de una sección con `overflow-hidden`: la página
 * mide lo mismo y el texto se pierde en silencio. Así pasó en el hero
 * (HERO-MOBILE-CLIP) y, al barrer el resto de la tienda, en el titular del
 * catálogo y en «Productos relacionados» de la ficha.
 *
 * Aquí se mide cada trozo de texto visible contra el ancho de la pantalla. Lo
 * que vive dentro de un contenedor que se desplaza a lo ancho a propósito —el
 * carrusel, una tabla— no cuenta: eso se recorre, no se pierde.
 */

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";
const WIDTHS = [320, 360, 375, 390, 414];

async function clippedText(page: Page) {
  return page.evaluate(() => {
    const vw = window.innerWidth;
    const scrollsSideways = (el: Element | null) => {
      for (let n = el; n && n !== document.body; n = n.parentElement) {
        const overflow = getComputedStyle(n).overflowX;
        if ((overflow === "auto" || overflow === "scroll") && n.scrollWidth > n.clientWidth) return true;
      }
      return false;
    };
    const invisible = (el: Element | null) => {
      for (let n = el; n; n = n.parentElement) {
        const cs = getComputedStyle(n);
        if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) return true;
      }
      return false;
    };
    const found: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = (node.textContent ?? "").trim();
      const parent = node.parentElement;
      if (!text || !parent) continue;
      if (parent.closest('[aria-hidden="true"], .sr-only, script, style, noscript, nextjs-portal')) continue;
      if (invisible(parent) || scrollsSideways(parent)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
      if (rects.length === 0) continue;
      const right = Math.max(...rects.map((r) => r.right));
      const left = Math.min(...rects.map((r) => r.left));
      if (right > vw + 0.5 || left < -0.5) {
        found.push(`«${text.slice(0, 32)}» ocupa ${Math.round(left)}–${Math.round(right)}px de ${vw}px`);
      }
    }
    const doc = document.documentElement;
    return { found, pageOverflow: doc.scrollWidth - doc.clientWidth };
  });
}

test.describe("el texto de la tienda cabe en un teléfono", () => {
  let productPath = "";

  test.beforeAll(async ({ request }) => {
    type Row = { slug: string; category?: { slug: string } };
    const products = (await (await request.get(`${API}/products/`)).json()) as Row[];
    // Una ficha CON «Productos relacionados», que es su bloque más ancho: un
    // producto cuya categoría tenga algún otro. Si no hay, cualquiera.
    const withSiblings = products.find((product) =>
      products.some((other) => other.slug !== product.slug && other.category?.slug === product.category?.slug),
    );
    const chosen = withSiblings ?? products[0];
    productPath = chosen ? `/product/${chosen.slug}` : "";
  });

  // Cada ruta se carga UNA vez y se mide en los cinco anchos. Cargarla una vez
  // por ancho son cuarenta visitas seguidas, cada una pide el carrito, y eso
  // agota el límite de peticiones del carrito para la prueba que venga después.
  for (const route of ["/", "/product", "FICHA", "/services", "/about", "/contact", "/cart", "/auth"]) {
    test(`${route} entre ${WIDTHS[0]} y ${WIDTHS[WIDTHS.length - 1]}px`, async ({ page }) => {
      const path = route === "FICHA" ? productPath : route;
      test.skip(!path, "el catálogo no tiene ningún producto para abrir su ficha");

      await page.setViewportSize({ width: WIDTHS[0], height: 844 });
      await page.goto(path, { waitUntil: "networkidle" });

      for (const width of WIDTHS) {
        await page.setViewportSize({ width, height: 844 });
        await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width);
        const result = await clippedText(page);

        expect.soft(result.found, `texto recortado en ${path} a ${width}px`).toEqual([]);
        expect.soft(result.pageOverflow, `${path} desborda a ${width}px`).toBeLessThanOrEqual(1);
      }
    });
  }
});
