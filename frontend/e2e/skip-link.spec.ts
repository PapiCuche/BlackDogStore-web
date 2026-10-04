import { expect, test, type Page } from "@playwright/test";

/**
 * «Saltar al contenido» con un teclado de verdad.
 *
 * La prueba de componentes comprueba a dónde apunta el enlace. Aquí se comprueba
 * lo que importa a quien lo usa: que es lo primero que alcanza el tabulador, que
 * se ve al recibir el foco, y que al activarlo el FOCO —no sólo la vista— queda
 * en el contenido.
 */

async function firstTabStop(page: Page) {
  await page.keyboard.press("Tab");
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    const box = el?.getBoundingClientRect();
    return {
      text: el?.textContent?.trim() ?? "",
      visible: Boolean(box && box.width > 1 && box.height > 1 && box.top >= 0 && box.left >= 0),
    };
  });
}

async function focusAfterActivating(page: Page) {
  await page.keyboard.press("Enter");
  return page.evaluate(() => document.activeElement?.id ?? "");
}

for (const route of ["/", "/product", "/services", "/cart"]) {
  test(`en ${route} el primer tabulador llega al enlace de salto y lleva al contenido`, async ({ page }) => {
    await page.goto(route, { waitUntil: "networkidle" });

    const first = await firstTabStop(page);
    expect(first.text).toBe("Saltar al contenido");
    expect(first.visible).toBe(true);

    expect(await focusAfterActivating(page)).toBe("contenido");

    // Y el siguiente tabulador ya está dentro del contenido, no en la cabecera.
    await page.keyboard.press("Tab");
    const insideContent = await page.evaluate(() =>
      Boolean(document.activeElement?.closest("#contenido")),
    );
    expect(insideContent).toBe(true);
  });
}

test("en el panel el enlace de salto lleva al área principal", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  test.skip((await card.count()) === 0, "no hay tarjeta de accesos de desarrollo en este entorno");
  await card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" }).click();
  // El inicio de sesión se limita a 5 por minuto y por dirección, y en la
  // pasada completa las pruebas anteriores pueden haber gastado la ventana.
  // Se respeta el límite: se espera y se reintenta.
  for (let attempt = 0; ; attempt += 1) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((url) => !url.pathname.startsWith("/auth"), { timeout: 12_000 });
      break;
    } catch (error) {
      if (attempt === 2) throw error;
      await page.waitForTimeout(62_000);
    }
  }

  await page.goto("/admin", { waitUntil: "networkidle" });
  await expect(page.locator("#admin-main-content")).toBeVisible({ timeout: 20_000 });

  const first = await firstTabStop(page);
  expect(first.text).toBe("Saltar al contenido");
  expect(first.visible).toBe(true);
  expect(await focusAfterActivating(page)).toBe("admin-main-content");
});
