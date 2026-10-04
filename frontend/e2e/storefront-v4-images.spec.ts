import { expect, test, type Page } from "@playwright/test";

/**
 * V4 — la tienda coloca sus imágenes desde el panel.
 *
 * De punta a punta y con un navegador de verdad: se sube un PNG sin fondo en el
 * panel, se guarda, y la portada pública lo muestra SIN fondo. Es lo que pidió
 * el propietario: «cuando se sube así, que no aparezca el fondo».
 *
 * La portada tiene un solo hero, que sigue al tema: la misma imagen lleva
 * sombra oscura en el tema claro y halo claro en el oscuro.
 *
 * LA PRUEBA DEJA LA TIENDA COMO LA ENCONTRÓ: quita la imagen al terminar, pase
 * lo que pase.
 */

// 40 × 30, rojo opaco en el centro y transparente en las esquinas.
const CUTOUT = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAYAAABe3VzdAAAAPklEQVR42u3QsQkAIAxFweggzuH+lXNkEV3BQlDwrg78RyIA/lZ2D0dr8+Rwz9zarq9/UKBAgQIFChQIwE0LKCMEHNqb/bcAAAAASUVORK5CYII=", "base64");

const WIDTHS = [320, 360, 375, 390, 414];

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  test.skip((await card.count()) === 0, "no hay tarjeta de accesos de desarrollo en este entorno");
  await card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" }).click();
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 20_000 });
}

async function openHeroForm(page: Page) {
  await page.goto("/admin/settings/storefront", { waitUntil: "networkidle" });
  // «Subir imagen» sin imagen colocada, «Cambiar imagen» con ella.
  await expect(page.getByLabel(/^(Subir|Cambiar) imagen: Imagen del hero$/)).toBeAttached({ timeout: 20_000 });
  // El diseño anterior ya no es una opción del panel.
  await expect(page.getByLabel("Estilo del hero")).toHaveCount(0);
}

async function removeHeroImage(page: Page) {
  await openHeroForm(page);
  const remove = page.getByRole("button", { name: "Quitar Imagen del hero" });
  if (await remove.count()) await remove.click();
  await page.getByRole("button", { name: "Guardar portada" }).click();
  await expect(page.getByText("Portada actualizada.")).toBeVisible();
}

test("un PNG sin fondo subido en el panel llega a la portada sin fondo", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);

  try {
    await openHeroForm(page);
    await page.getByLabel("Subir imagen: Imagen del hero").setInputFiles({
      name: "recorte.png", mimeType: "image/png", buffer: CUTOUT,
    });
    // La vista previa aparece cuando el servidor devolvió la dirección.
    const preview = page.getByRole("img", { name: "Imagen del hero" });
    await expect(preview).toHaveAttribute("src", /^\/api\/storefront\/images\/[0-9a-f]{32}$/);
    const address = (await preview.getAttribute("src"))!;

    await page.getByRole("button", { name: "Guardar portada" }).click();
    await expect(page.getByText("Portada actualizada.")).toBeVisible();

    // --- la portada pública ------------------------------------------------
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/", { waitUntil: "networkidle" });
    await page.emulateMedia({ colorScheme: "light" });
    await page.evaluate(() => localStorage.setItem("ui-theme", "light"));
    await page.reload({ waitUntil: "networkidle" });
    const hero = page.locator("section[data-hero]");
    await expect(hero).toBeVisible();
    const art = hero.locator("[data-hero-art] img");
    await expect(art).toHaveAttribute("src", address);

    const pixels = await art.evaluate(async (img: HTMLImageElement) => {
      if (!img.complete) await new Promise((resolve) => img.addEventListener("load", resolve, { once: true }));
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      const at = (x: number, y: number) => Array.from(ctx.getImageData(x, y, 1, 1).data);
      return { size: [img.naturalWidth, img.naturalHeight], corner: at(0, 0), centre: at(20, 15) };
    });
    expect(pixels.size).toEqual([40, 30]);
    expect(pixels.corner[3], "la esquina del recorte dejó de ser transparente").toBe(0);
    expect(pixels.centre).toEqual([200, 30, 30, 255]);

    // Ni la imagen ni su contenedor pintan un fondo detrás del recorte.
    const backgrounds = await art.evaluate((img) => {
      const out: string[] = [];
      for (let n: Element | null = img; n && !n.matches("section"); n = n.parentElement) {
        out.push(getComputedStyle(n).backgroundColor);
      }
      return out;
    });
    expect(backgrounds.every((c) => c === "rgba(0, 0, 0, 0)")).toBe(true);

    // La profundidad sigue la silueta y se ve: sombra oscura sobre claro.
    const depth = (locator: typeof art) => locator.evaluate((img) => getComputedStyle(img).filter);
    expect(await depth(art)).toMatch(/drop-shadow\(rgba\(0, 0, 0, 0\.18\)/);

    // --- y cabe en un teléfono ---------------------------------------------
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 844 });
      await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width);
      const fit = await page.evaluate(() => {
        const doc = document.documentElement;
        const h1 = document.querySelector("section[data-hero] h1")!.getBoundingClientRect();
        const img = document.querySelector("[data-hero-art] img")!.getBoundingClientRect();
        return {
          overflow: doc.scrollWidth - doc.clientWidth,
          titleRight: Math.round(h1.right), imageRight: Math.round(img.right), imageLeft: Math.round(img.left),
          vw: window.innerWidth,
        };
      });
      expect.soft(fit.overflow, `la portada desborda a ${width}px`).toBeLessThanOrEqual(1);
      expect.soft(fit.titleRight, `el titular se sale a ${width}px`).toBeLessThanOrEqual(fit.vw);
      expect.soft(fit.imageRight, `la imagen se sale a ${width}px`).toBeLessThanOrEqual(fit.vw);
      expect.soft(fit.imageLeft).toBeGreaterThanOrEqual(0);
    }
    // --- la misma imagen en el tema oscuro ----------------------------------
    // Una sombra negra sobre negro no se ve: en el tema oscuro la profundidad
    // es un halo claro, por la misma variable.
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => localStorage.setItem("ui-theme", "dark"));
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const inDark = page.locator("section[data-hero] [data-hero-art] img");
    await expect(inDark).toHaveAttribute("src", address);
    const halo = await inDark.evaluate((img) => getComputedStyle(img).filter);
    expect(halo).toMatch(/rgba\(255, 255, 255/);
    expect(halo).not.toMatch(/rgba\(0, 0, 0/);
  } finally {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.evaluate(() => localStorage.removeItem("ui-theme")).catch(() => {});
    await removeHeroImage(page);
  }

  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.locator("section[data-hero]")).toBeVisible();
  await expect(page.locator("section[data-hero] [data-hero-art]")).toHaveCount(0);
});
