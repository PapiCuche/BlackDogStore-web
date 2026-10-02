import { expect, test, type Page } from "@playwright/test";

/**
 * HERO-MOBILE-CLIP — el texto del hero cabe en lo que se ve.
 *
 * EL DEFECTO. Hasta 414 px de ancho, el titular y el párrafo del hero se
 * cortaban por la derecha: «CON RESPALDO», «ESPECIALIZADO». La columna de texto
 * medía lo que su palabra más larga a 36 px —más que la pantalla— y la sección
 * lleva `overflow-hidden`, así que el sobrante se escondía en vez de desbordar.
 *
 * POR QUÉ NO LO VEÍA NINGUNA PRUEBA. `storefront.spec` comprueba que la PÁGINA
 * no desborde (`scrollWidth` contra `clientWidth`). Un texto recortado dentro de
 * una sección que oculta su sobrante no desborda la página: se pierde en
 * silencio. Aquí se mide el TEXTO, nodo por nodo, contra el área visible.
 *
 * Y se mide lo contrario también: que arreglarlo no parta una palabra por la
 * mitad —un titular roto no es un titular responsive— ni cambie el hero de
 * escritorio.
 */

const MOBILE_WIDTHS = [320, 360, 375, 390, 414];
const THEMES = ["light", "dark"] as const;

async function withTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.addInitScript((v) => {
    try { window.localStorage.setItem("ui-theme", v); } catch { /* ventana privada */ }
  }, theme);
}

/** Cada trozo de texto del hero, con la caja que ocupa en pantalla. */
async function heroText(page: Page) {
  return page.evaluate(() => {
    const hero = document.querySelector("section") as HTMLElement;
    const heroBox = hero.getBoundingClientRect();
    const visibleLeft = Math.max(0, heroBox.left);
    const visibleRight = Math.min(window.innerWidth, heroBox.right);

    const pieces: { text: string; left: number; right: number; inTitle: boolean; brokenWords: string[] }[] = [];
    const walker = document.createTreeWalker(hero, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = (node.textContent ?? "").trim();
      const parent = node.parentElement as HTMLElement;
      // La marca de agua es textura, no contenido; y lo oculto no se lee.
      if (!text || parent.closest('[aria-hidden="true"], .sr-only, [hidden]')) continue;

      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = Array.from(range.getClientRects()).filter((r) => r.width > 0);
      if (rects.length === 0) continue;

      // Una palabra que ocupa más de una caja está partida entre dos líneas.
      const brokenWords: string[] = [];
      const raw = node.textContent ?? "";
      for (const match of raw.matchAll(/\S+/g)) {
        const word = document.createRange();
        word.setStart(node, match.index!);
        word.setEnd(node, match.index! + match[0].length);
        const lines = new Set(Array.from(word.getClientRects()).filter((r) => r.width > 0).map((r) => Math.round(r.top)));
        if (lines.size > 1) brokenWords.push(match[0]);
      }

      pieces.push({
        text: text.slice(0, 40),
        left: Math.min(...rects.map((r) => r.left)),
        right: Math.max(...rects.map((r) => r.right)),
        inTitle: Boolean(parent.closest("h1")),
        brokenWords,
      });
    }
    const h1 = hero.querySelector("h1") as HTMLElement;
    return {
      visibleLeft, visibleRight, pieces,
      fontSize: parseFloat(getComputedStyle(h1).fontSize),
      background: getComputedStyle(hero).backgroundColor,
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
}

for (const theme of THEMES) {
  for (const width of MOBILE_WIDTHS) {
    test(`a ${width}px en tema ${theme} el texto del hero cabe entero`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await withTheme(page, theme);
      await page.goto("/", { waitUntil: "networkidle" });
      const hero = await heroText(page);

      expect(hero.pieces.some((piece) => piece.inTitle), "el hero no tiene titular").toBe(true);
      expect(hero.pieces.length, "el hero no tiene texto que medir").toBeGreaterThan(2);

      // 1. NADA SE SALE DE LO VISIBLE. Medio píxel de tolerancia por el redondeo.
      const clipped = hero.pieces
        .filter((piece) => piece.right > hero.visibleRight + 0.5 || piece.left < hero.visibleLeft - 0.5)
        .map((piece) => `«${piece.text}» llega a ${Math.round(piece.right)}px de ${Math.round(hero.visibleRight)}px visibles`);
      expect(clipped, `texto del hero recortado a ${width}px`).toEqual([]);

      // 2. NINGUNA PALABRA DEL TITULAR SE PARTE. Caber no puede costar un
      //    titular roto por la mitad.
      const broken = hero.pieces.filter((piece) => piece.inTitle).flatMap((piece) => piece.brokenWords);
      expect(broken, `palabras del titular partidas a ${width}px`).toEqual([]);

      // 3. El titular sigue siendo un titular.
      expect(hero.fontSize, "el titular quedó demasiado pequeño para leerse como titular").toBeGreaterThanOrEqual(20);

      // 4. La losa sigue siendo oscura y la página no desborda.
      const [r, g, b] = hero.background.match(/[\d.]+/g)!.slice(0, 3).map(Number);
      expect(0.2126 * r + 0.7152 * g + 0.0722 * b, `la losa dejó de ser oscura: ${hero.background}`).toBeLessThan(90);
      expect(hero.pageOverflow).toBeLessThanOrEqual(1);
    });
  }

  test(`en escritorio y tema ${theme} el hero no cambia de tamaño`, async ({ page }) => {
    for (const width of [1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await withTheme(page, theme);
      await page.goto("/", { waitUntil: "networkidle" });
      const hero = await heroText(page);

      // El tamaño de escritorio es el que ya estaba: clamp(2.25rem, 4.2vw, 4rem).
      const expected = Math.min(64, Math.max(36, width * 0.042));
      expect(Math.abs(hero.fontSize - expected), `titular a ${width}px: ${hero.fontSize}px`).toBeLessThan(0.5);

      const clipped = hero.pieces.filter((piece) => piece.right > hero.visibleRight + 0.5);
      expect(clipped.map((piece) => piece.text)).toEqual([]);
      expect(hero.pieces.filter((piece) => piece.inTitle).flatMap((piece) => piece.brokenWords)).toEqual([]);
    }
  });
}
