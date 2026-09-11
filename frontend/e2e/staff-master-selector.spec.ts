import { test, expect } from "@playwright/test";

/**
 * H4.1 — la pantalla de Personal vista por el MASTER de plataforma.
 *
 * Por qué existe aparte: el arreglo del defecto del spinner cambió de dónde
 * sale la empresa —del selector al panel—, y ese selector es justamente lo
 * único que tiene el master. Si la corrección hubiera roto su camino, sería
 * haber cambiado un fallo por otro.
 *
 * El master entra por `is_superuser` y SIN membresía: hasta que elige empresa
 * no hay ninguna, que es el estado que esta prueba recorre primero.
 */

test("el master elige empresa y el personal carga", async ({ page }) => {
  test.setTimeout(180_000);

  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "sin accesos de desarrollo");
  const usar = card.locator("li").filter({ hasText: "dev_master" })
    .getByRole("button", { name: "Usar cuenta" });
  if ((await usar.count()) === 0) test.skip(true, "dev_master no está sembrado");
  await usar.click();
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 20_000 });

  await page.goto("/admin/staff", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Personal", exact: true }))
    .toBeVisible({ timeout: 20_000 });

  // SIN EMPRESA SE DICE, no se gira. Los tres estados —cargando, sin empresa y
  // lista vacía— tienen que verse distintos, porque significan cosas distintas.
  const sinEmpresa = page.getByText(/No hay ninguna empresa seleccionada/i);
  if (await sinEmpresa.isVisible().catch(() => false)) {
    await expect(page.getByText(/Cargando personal/i)).toHaveCount(0);
    await expect(page.getByText(/No hay personal que coincida/i)).toHaveCount(0);
  }

  // Y ahora elige una.
  await page.getByRole("button", { name: /Selecciona una empresa|Black Dog/i })
    .first().click();
  const opciones = page.getByRole("option");
  await expect(opciones.first()).toBeVisible({ timeout: 10_000 });
  await opciones.first().click();

  await expect(page.getByText(/Cargando personal/i))
    .toHaveCount(0, { timeout: 25_000 });
  await expect(
    page.getByText(/No hay ninguna empresa seleccionada/i),
    "eligió empresa y la pantalla sigue diciendo que no hay ninguna",
  ).toHaveCount(0, { timeout: 25_000 });

  // Contenido real de la empresa elegida.
  const fichas = page.locator("ul > li").filter({ hasText: "Roles:" });
  await expect(fichas.first()).toBeVisible({ timeout: 25_000 });
  const texto = await page.locator("main").innerText();
  expect(texto).toContain("Área:");
  expect(texto).toContain("Sucursales:");

  // La elección sobrevive a la navegación: el master no puede tener que
  // reelegir empresa en cada pantalla.
  await page.goto("/admin/areas", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Áreas", exact: true }))
    .toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/Cargando áreas/i)).toHaveCount(0, { timeout: 20_000 });
  await expect(page.locator("ul > li").first()).toBeVisible({ timeout: 20_000 });
});
