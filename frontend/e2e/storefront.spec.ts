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

const PUBLIC_ROUTES = ["/", "/services", "/product", "/cart", "/auth"];
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
    test(`el hero es una losa oscura en tema ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await withTheme(page, theme);
      await page.goto("/", { waitUntil: "networkidle" });

      // LA SUPERFICIE MANDA, Y ESTA SUPERFICIE NO CAMBIA.
      //
      // M12F.2 devolvió al hero su condición de losa de marca: oscura en los
      // dos temas, a propósito. Antes de eso M12F la había convertido en
      // `bg-background` y en tema claro salía crema — la marca desaparecía de
      // su propia portada.
      //
      // Por tanto su logotipo es SIEMPRE la variante para fondo oscuro. Que
      // siguiera al tema sería el defecto contrario al que M12E cerró.
      const hero = page.locator("section").first();
      const bg = await hero.evaluate((el) => getComputedStyle(el).backgroundColor);
      const luminance = (() => {
        const [r, g, b] = bg.match(/\d+/g)!.slice(0, 3).map(Number);
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
      })();
      expect(luminance, `el hero no es oscuro en tema ${theme}: ${bg}`).toBeLessThan(90);

      const src = await hero.locator("img").first().getAttribute("src");
      expect(src, `el hero pinta ${src} sobre una losa oscura`).toContain("on-dark");
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
