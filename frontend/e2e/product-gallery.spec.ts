import { expect, test, type Page } from "@playwright/test";

/**
 * PRODUCT-MEDIA — del panel a la tienda, con un navegador de verdad.
 *
 * Se suben dos imágenes a un producto desde el panel, se elige la segunda como
 * principal, y el catálogo público y la ficha la muestran. Después se quitan:
 * la prueba deja el producto como lo encontró.
 */

// 40 × 30, rojo opaco en el centro y transparente en las esquinas.
const CUTOUT = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAACgAAAAeCAYAAABe3VzdAAAAPklEQVR42u3QsQkAIAxFweggzuH+lXNkEV3BQlDwrg78RyIA/lZ2D0dr8+Rwz9zarq9/UKBAgQIFChQIwE0LKCMEHNqb/bcAAAAASUVORK5CYII=", "base64");
// 1 × 1, opaco.
const DOT = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "base64");

const MANAGED = /^\/api\/storefront\/images\/[0-9a-f]{32}$/;

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  test.skip((await card.count()) === 0, "no hay tarjeta de accesos de desarrollo en este entorno");
  await card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" }).click();
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 20_000 });
}

async function removeAll(page: Page) {
  for (;;) {
    const remove = page.getByRole("button", { name: /^Quitar imagen \d+$/ }).first();
    if ((await remove.count()) === 0) break;
    const before = await page.getByRole("button", { name: /^Quitar imagen \d+$/ }).count();
    await remove.click();
    await page.getByRole("button", { name: "Sí, quitar" }).click();
    await expect(page.getByRole("button", { name: /^Quitar imagen \d+$/ })).toHaveCount(before - 1);
  }
}

test("las imágenes subidas en el panel llegan al catálogo y a la ficha", async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await signIn(page);

  // El primer producto del catálogo público, por su nombre.
  const listed = await page.request.get("/api/products/?ordering=name");
  const [product] = (await listed.json()) as Array<{ id: number; slug: string; name: string; image_url: string }>;
  expect(product, "la base de pruebas no tiene productos").toBeTruthy();

  await page.goto(`/admin/products/${product.id}`, { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Imágenes" })).toBeVisible({ timeout: 20_000 });
  await removeAll(page);

  try {
    // --- subir dos, de una vez ---------------------------------------------
    await page.getByLabel("Elegir imágenes").setInputFiles([
      { name: "frontal.png", mimeType: "image/png", buffer: CUTOUT },
      { name: "detalle.png", mimeType: "image/png", buffer: DOT },
    ]);
    const items = page.getByRole("listitem").filter({ has: page.getByLabel(/^Texto alternativo de la imagen/) });
    await expect(items).toHaveCount(2, { timeout: 30_000 });
    await expect(items.nth(0).getByText("Principal")).toBeVisible();
    const first = (await items.nth(0).getByRole("img").getAttribute("src"))!;
    const second = (await items.nth(1).getByRole("img").getAttribute("src"))!;
    expect(first).toMatch(MANAGED);
    expect(second).toMatch(MANAGED);

    // --- texto alternativo y principal ---------------------------------------
    const alt = page.getByLabel("Texto alternativo de la imagen 2");
    await alt.fill("Detalle del producto");
    await alt.blur();
    await page.getByRole("button", { name: "Marcar como principal: imagen 2" }).click();
    await expect(items.nth(1).getByText("Principal")).toBeVisible();

    // --- persiste al recargar -------------------------------------------------
    await page.reload({ waitUntil: "networkidle" });
    await expect(items).toHaveCount(2);
    await expect(items.nth(1).getByText("Principal")).toBeVisible();
    await expect(page.getByLabel("Texto alternativo de la imagen 2")).toHaveValue("Detalle del producto");
    // Con galería, la dirección no se escribe a mano.
    await expect(page.getByLabel(/Dirección de imagen externa/)).toHaveCount(0);

    // --- la tienda -------------------------------------------------------------
    const api = await page.request.get(`/api/products/?slug=${product.slug}`);
    const [published] = await api.json();
    expect(published.image_url).toBe(second);
    expect(published.images.map((image: { url: string }) => image.url)).toEqual([first, second]);

    await page.goto("/product", { waitUntil: "networkidle" });
    const card = page.locator(`a[href="/product/${product.slug}"]`).first();
    await expect(card.locator("img")).toHaveAttribute("src", second);

    await page.goto(`/product/${product.slug}`, { waitUntil: "networkidle" });
    const main = page.getByTestId("product-main-image").locator("img");
    await expect(main).toHaveAttribute("src", second);
    await expect(main).toHaveAttribute("alt", "Detalle del producto");
    expect(await main.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    const thumbs = page.getByRole("group", { name: "Imágenes del producto" }).getByRole("button");
    await expect(thumbs).toHaveCount(2);
    await thumbs.nth(0).click();
    await expect(main).toHaveAttribute("src", first);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  } finally {
    await page.goto(`/admin/products/${product.id}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "Imágenes" })).toBeVisible({ timeout: 20_000 });
    await removeAll(page);
  }

  // Sin galería, el producto vuelve a no tener imagen subida.
  const after = await page.request.get(`/api/products/?slug=${product.slug}`);
  const [restored] = await after.json();
  expect(restored.images).toEqual([]);
  expect(restored.image_url).toBe("");
  expect(errors).toEqual([]);
});
