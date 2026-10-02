import { expect, test } from "@playwright/test";

/**
 * STOREFRONT-V3 — la tienda pública, en un navegador de verdad.
 *
 * Lo que una prueba de componente no ve: que el enlace de una categoría del pie
 * FILTRA el catálogo, que el menú móvil lleva a las páginas nuevas, que el
 * carrusel se puede recorrer con el teclado y se queda quieto cuando se pide
 * menos movimiento, y que una línea del carrito vuelve a su ficha.
 *
 * Sin mocks. Los datos son los del catálogo de la empresa; lo que la prueba
 * necesita y no existe se salta DICIENDO POR QUÉ.
 */

const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8000/api";

type Category = { id: number; name: string; slug: string };
type Product = { id: number; slug: string; name: string; inventory: number; price: string };

test("un enlace de categoría del pie filtra el catálogo", async ({ page, request }) => {
  const categories = (await (await request.get(`${API}/categories/`)).json()) as Category[];
  test.skip(categories.length === 0, "la tienda no tiene categorías en su catálogo");
  const category = categories[0];

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "networkidle" });
  const link = page.locator("footer").getByRole("link", { name: category.name, exact: true });
  await expect(link).toHaveAttribute("href", `/product?category=${category.slug}`);
  // Ningún enlace de la tienda usa el parámetro que el catálogo no lee.
  expect(await page.locator('a[href*="?cat="]').count()).toBe(0);

  const filtered = page.waitForResponse(
    (r) => r.url().includes("/products") && r.url().includes(`category=${category.slug}`) && r.status() === 200,
  );
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/product\\?category=${category.slug}`));
  const rows = (await (await filtered).json()) as { category?: Category }[];
  for (const row of rows) expect(row.category?.slug).toBe(category.slug);
});

test("el menú móvil dice su estado y lleva a Contacto y Nosotros", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "networkidle" });

  const toggle = page.getByRole("button", { name: "Abrir menú" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(page.getByRole("button", { name: "Cerrar menú" })).toHaveAttribute("aria-expanded", "true");

  const menu = page.locator("#store-mobile-menu");
  await expect(menu.getByRole("link", { name: "Nosotros" })).toBeVisible();
  await menu.getByRole("link", { name: "Contacto" }).click();
  await expect(page).toHaveURL(/\/contact$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  // El menú se cierra al navegar.
  await expect(page.locator("#store-mobile-menu")).toHaveCount(0);
});

test("Nosotros y Contacto muestran datos de la tienda, no un texto compilado", async ({ page, request }) => {
  const config = await (await request.get(`${API}/storefront/config/`)).json();
  const name: string = config.company?.name ?? "";
  test.skip(!name, "la tienda no tiene nombre publicado");

  for (const route of ["/about", "/contact"]) {
    await page.goto(route, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { level: 1 })).toContainText(name);
    // El armazón de la tienda sigue ahí.
    await expect(page.locator("header").first()).toBeVisible();
    await expect(page.locator("footer")).toBeVisible();
  }
});

test.describe("carrusel de la portada", () => {
  test("se recorre con flechas y teclado", async ({ page, request }) => {
    const products = (await (await request.get(`${API}/products/?ordering=newest`)).json()) as Product[];
    test.skip(products.length < 2, "el catálogo no tiene dos productos para recorrer");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "networkidle" });
    const carousel = page.getByRole("region", { name: "Productos destacados" });
    await carousel.scrollIntoViewIfNeeded();
    const track = carousel.getByRole("group", { name: /Lista de productos/ });

    // Cada tarjeta conserva su enlace a la ficha.
    const cards = track.getByRole("link");
    await expect(cards.first()).toHaveAttribute("href", /\/product\/.+/);

    await track.focus();
    await page.keyboard.press("End");
    await expect(carousel.getByRole("button", { name: "Productos siguientes" })).toBeDisabled();
    await page.keyboard.press("Home");
    await expect(carousel.getByRole("button", { name: "Productos anteriores" })).toBeDisabled();
    await expect.poll(() => track.evaluate((node) => node.scrollLeft)).toBe(0);

    await carousel.getByRole("button", { name: "Productos siguientes" }).click();
    await expect.poll(() => track.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  });

  test("con «reducir movimiento» no avanza solo", async ({ page, request }) => {
    const products = (await (await request.get(`${API}/products/?ordering=newest`)).json()) as Product[];
    test.skip(products.length < 2, "el catálogo no tiene dos productos para recorrer");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "networkidle" });
    const carousel = page.getByRole("region", { name: "Productos destacados" });
    await carousel.scrollIntoViewIfNeeded();
    const track = carousel.getByRole("group", { name: /Lista de productos/ });

    await expect(carousel.locator(".v3-autoplay-control")).toBeHidden();
    const before = await track.evaluate((node) => node.scrollLeft);
    await page.waitForTimeout(1500);
    expect(await track.evaluate((node) => node.scrollLeft)).toBe(before);
  });
});

test("una línea del carrito vuelve a la ficha de su producto", async ({ page, request }) => {
  const products = (await (await request.get(`${API}/products/?ordering=newest`)).json()) as Product[];
  const product = products.find((p) => p.inventory > 0);
  test.skip(!product, "el catálogo no tiene ningún producto con stock");

  await page.goto(`/product/${product!.slug}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Agregar al carrito" }).click();
  await expect(page.getByText("Producto agregado al carrito.")).toBeVisible();

  await page.goto("/cart", { waitUntil: "networkidle" });
  const line = page.getByRole("link", { name: product!.name, exact: true }).first();
  await expect(line).toHaveAttribute("href", `/product/${product!.slug}`);
  await expect(page.getByRole("spinbutton", { name: `Cantidad de ${product!.name}` })).toBeVisible();

  // La prueba deja el carrito como lo encontró.
  await page.getByRole("button", { name: "Eliminar" }).first().click();
  await expect(page.getByRole("link", { name: product!.name, exact: true })).toHaveCount(0);
});
