import { expect, test, type Page } from "@playwright/test";

/**
 * ADMIN-INVENTORY-MOBILE-OVERFLOW
 *
 * A wide table inside a grid Panel used to impose its 640px min-content width on
 * the panel, so /admin/inventory widened the whole document on phones. The table
 * may stay wide, but only its local TableWrap is allowed to scroll.
 */

const WIDTHS = [320, 360, 375, 390, 414];

let SESSION_COOKIES: Awaited<
  ReturnType<import("@playwright/test").BrowserContext["cookies"]>
> = [];

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) {
    test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  }
  const use = card.locator("li").filter({ hasText: "dev_admin" })
    .getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) {
    test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  }
  await use.click();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 12_000 });
      return;
    } catch {
      if (attempt === 2) throw new Error("no se pudo iniciar sesión");
      await page.waitForTimeout(62_000);
    }
  }
}

test.beforeAll(async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await signIn(page);
  SESSION_COOKIES = await context.cookies();
  await context.close();
});

test.beforeEach(async ({ context }) => {
  if (SESSION_COOKIES.length) await context.addCookies(SESSION_COOKIES);
});

test("el dashboard de inventario no ensancha la página en un teléfono", async ({ page }) => {
  test.setTimeout(120_000);

  await page.setViewportSize({ width: WIDTHS[0], height: 844 });
  await page.goto("/admin/inventory", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Inventario", exact: true }))
    .toBeVisible({ timeout: 20_000 });

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width);

    const measurement = await page.evaluate(() => {
      const doc = document.documentElement;
      const scrollers = Array.from(document.querySelectorAll<HTMLElement>(".overflow-x-auto"))
        .filter((node) => node.querySelector("table"));
      return {
        pageOverflow: doc.scrollWidth - doc.clientWidth,
        tableScrollers: scrollers.map((node) => ({
          clientWidth: node.clientWidth,
          scrollWidth: node.scrollWidth,
        })),
      };
    });

    expect.soft(measurement.pageOverflow, `/admin/inventory desborda a ${width}px`)
      .toBeLessThanOrEqual(1);
    expect.soft(measurement.tableScrollers.length, "no se encontró la tabla desplazable")
      .toBeGreaterThan(0);
    expect.soft(
      measurement.tableScrollers.some((row) => row.scrollWidth >= row.clientWidth),
      "la tabla dejó de vivir dentro de un viewport desplazable",
    ).toBe(true);
  }
});
