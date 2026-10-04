import { test, expect, type Page } from "@playwright/test";

/**
 * M12F.1 — la matriz que hasta ahora se declaraba NO DISPONIBLE.
 *
 * Cuatro anchos, dos temas, las rutas que un cliente y un administrador pisan
 * de verdad. No comprueba que se vea bonito —eso lo mira una persona— sino los
 * hechos que una persona no puede comprobar ocho veces sin equivocarse.
 */

const VIEWPORTS = [
  { name: "320", width: 320, height: 800 },
  { name: "390", width: 390, height: 844 },
  { name: "768", width: 768, height: 1024 },
  { name: "1440", width: 1440, height: 900 },
];

// STOREFRONT-V3 añade «Nosotros» y «Contacto» a la misma batería: mismo ancho,
// mismo tema, mismas exigencias que el resto de la tienda.
const PUBLIC_ROUTES = ["/", "/services", "/product", "/cart", "/auth", "/about", "/contact"];
const ADMIN_ROUTES = ["/admin", "/admin/settings/storefront"];

/** Fija el tema ANTES de cargar, como haría un visitante que ya eligió. */
async function withTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem("ui-theme", value);
    } catch {
      /* ventana privada: la página tiene que funcionar igual */
    }
  }, theme);
}

/** Errores de consola que importan. Se ignoran los fallos de red del backend. */
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (/Failed to load resource|net::ERR_|favicon/i.test(text)) return;
    errors.push(text);
  });
  page.on("pageerror", (err) => errors.push(String(err)));
  return errors;
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${viewport.name}px · tema ${theme}`, () => {
      for (const route of [...PUBLIC_ROUTES, ...ADMIN_ROUTES]) {
        test(`${route} se comporta`, async ({ page }) => {
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await withTheme(page, theme);
          const errors = collectErrors(page);

          await page.goto(route, { waitUntil: "networkidle" });

          // 1. CERO DESBORDAMIENTO HORIZONTAL.
          //
          // Es el defecto que un ancho fijo produce y que sólo se ve abriendo
          // la página. Se tolera 1 px por el redondeo del navegador.
          const overflow = await page.evaluate(() => {
            const doc = document.documentElement;
            return doc.scrollWidth - doc.clientWidth;
          });
          expect(
            overflow,
            `${route} desborda ${overflow}px a lo ancho en ${viewport.name}px`,
          ).toBeLessThanOrEqual(1);

          // 2. EL TEMA QUE SE PIDIÓ ES EL QUE SE PINTA, y desde el primer
          //    paint: lo escribe el script del <head>, no un efecto.
          await expect(page.locator("html")).toHaveAttribute("data-theme", theme);

          // 3. SIN ERRORES DE CONSOLA NI AVISOS DE HIDRATACIÓN.
          const hydration = errors.filter((e) => /hydrat/i.test(e));
          expect(hydration, `avisos de hidratación en ${route}`).toEqual([]);
          expect(errors, `errores de consola en ${route}`).toEqual([]);
        });
      }
    });
  }
}

test.describe("el logotipo se elige por la superficie real", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`la firma de marca sigue al tema en tema ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await withTheme(page, theme);
      await page.goto("/", { waitUntil: "networkidle" });

      // LA SUPERFICIE MANDA. En la V3 la portada no abre con una losa oscura
      // fija: el hero y la firma de marca se pintan con `bg-background`, así
      // que su contraste es el del tema. El isotipo de la firma tiene que ser
      // la variante para ESA superficie: oscuro sobre claro, claro sobre oscuro.
      const hero = page.locator("section[data-hero]");
      const signature = page.getByTestId("brand-statement");
      for (const [name, surface] of [["el hero", hero], ["la firma de marca", signature]] as const) {
        const bg = await surface.evaluate((el) => getComputedStyle(el).backgroundColor);
        const [r, g, b] = bg.match(/\d+/g)!.slice(0, 3).map(Number);
        const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        if (theme === "dark") expect(luminance, `${name} no es oscuro en tema dark: ${bg}`).toBeLessThan(90);
        else expect(luminance, `${name} no es claro en tema light: ${bg}`).toBeGreaterThan(160);
      }

      const src = await signature.locator("img").first().getAttribute("src");
      expect(src, `la firma pinta ${src} en tema ${theme}`).toContain(theme === "dark" ? "on-dark" : "on-light");

      // Marca de agua: decorativa y tenue, igual en los dos temas.
      const watermark = signature.locator(".v3-brand-watermark");
      await expect(watermark).toHaveAttribute("aria-hidden", "true");
      expect(await watermark.evaluate((el) => getComputedStyle(el).opacity)).toBe("0.13");
    });
  }

  test("la cabecera SÍ sigue al tema", async ({ page }) => {
    // La cabecera se pinta con `bg-background`, así que su contraste es el del
    // tema. Es la otra mitad de la regla: no todo es losa, y no todo sigue.
    await page.setViewportSize({ width: 1440, height: 900 });
    await withTheme(page, "light");
    await page.goto("/", { waitUntil: "networkidle" });
    const src = await page.locator("header img").first().getAttribute("src");
    expect(src).toContain("on-light");
  });

  test("la cabecera en oscuro usa la variante blanca", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await withTheme(page, "dark");
    await page.goto("/", { waitUntil: "networkidle" });
    const src = await page.locator("header img").first().getAttribute("src");
    expect(src).toContain("on-dark");
  });
});

test.describe("el selector de tema funciona de verdad", () => {
  test("cambiar a claro persiste tras recargar", async ({ page }) => {
    // SIN `withTheme` AQUÍ, y es el motivo por el que este test falló primero.
    // `addInitScript` se reinyecta en CADA navegación, así que al recargar
    // volvía a escribir «dark» y pisaba la elección que el test acababa de
    // hacer. El fallo era de la prueba, no de la aplicación: el selector
    // funcionaba.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/", { waitUntil: "networkidle" });

    await page.getByRole("button", { name: /^Tema:/ }).click();
    await page.getByRole("menuitemradio", { name: "Oscuro" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.getByRole("button", { name: /^Tema:/ }).click();
    await page.getByRole("menuitemradio", { name: "Claro" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await page.reload({ waitUntil: "networkidle" });
    // Sin parpadeo: el script del <head> lo resuelve antes del primer paint.
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    expect(await page.evaluate(() => localStorage.getItem("ui-theme"))).toBe("light");
  });

  test("el menú del tema se puede usar con el teclado", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/", { waitUntil: "networkidle" });
    const toggle = page.getByRole("button", { name: /^Tema:/ });
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menu", { name: "Tema" })).toBeVisible();
    // Escape cierra: un menú que sólo se cierra pulsando fuera deja atrapado a
    // quien navega con teclado.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu", { name: "Tema" })).toHaveCount(0);
  });
});

test.describe("las afirmaciones retiradas no vuelven", () => {
  const RETIRED = [
    "5,000+",
    "Nasan Originales",
    "certificado de autenticidad",
    "Abril 2025",
    "Sin msg",
    "Diagnóstico Gratuito",
    "incluyen 6 meses",
  ];

  for (const route of ["/", "/services"]) {
    test(`${route} no publica ninguna`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(route, { waitUntil: "networkidle" });
      const text = await page.locator("body").innerText();
      for (const claim of RETIRED) {
        expect(text, `${route} volvió a publicar «${claim}»`).not.toContain(claim);
      }
    });
  }

  test("/services publica la garantía que el manual sí respalda", async ({ page }) => {
    await page.goto("/services", { waitUntil: "networkidle" });
    const text = await page.locator("body").innerText();
    // Distingue producto de reparación, que es justo lo que la versión
    // anterior había borrado.
    expect(text).toContain("seminuevos");
    expect(text).toContain("depende del trabajo");
  });
});
