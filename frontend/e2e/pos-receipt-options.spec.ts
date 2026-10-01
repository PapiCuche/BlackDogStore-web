import { test, expect } from "@playwright/test";

/**
 * ERP-FISCAL-6 §31 — la caja ofrece los tres documentos en desarrollo BETA.
 *
 * QUÉ PRUEBA ESTO QUE NADA MÁS PRUEBA
 * -----------------------------------
 * Que el contrato nuevo de `receipt_options` llega a la pantalla y la pantalla lo
 * respeta: con `dev_admin` (que tiene `sales.fiscal.issue`), el servidor con
 * `FISCAL_ENABLED=True` en BETA y las series DEMO preparadas por
 * `seed_demo_users --fiscal-beta`, el selector «Tipo de comprobante» muestra
 * Nota de venta interna, Boleta electrónica · BETA y Factura electrónica · BETA,
 * las tres habilitadas para «Tienda principal». Y NO muestra nota de crédito,
 * nota de débito, baja ni resumen: eso son flujos posteriores.
 *
 * Si falta cualquiera de las precondiciones, se salta DICIENDO POR QUÉ.
 */

test("dev_admin ve nota interna, boleta y factura habilitadas en la caja", async ({ page }) => {
  test.setTimeout(180_000);

  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) {
    test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  }
  const row = card.locator("li").filter({ hasText: "dev_admin" });
  const use = row.getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) {
    test.skip(true, "dev_admin no existe o está inactiva: siembra con seed_demo_users");
  }
  await use.click();

  // El limitador son 5 intentos por minuto y por IP (H4.1.2A); se espera la ventana.
  for (let intento = 0; intento < 3; intento++) {
    await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
    try {
      await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 15_000 });
      break;
    } catch {
      if (intento === 2) throw new Error("no se pudo iniciar sesión: el limitador no cedió");
      await page.waitForTimeout(62_000);
    }
  }

  // EL PAYLOAD REAL del contexto, tal como lo manda el servidor. La ruta es la
  // de `fetchPosContext` (`/admin/pos/context/`), no la del contrato v1 interno,
  // y se compara SIN la barra final: el proxy de Next responde 308 quitándola y
  // la respuesta que interesa llega en la URL ya redirigida.
  const contextResponse = page.waitForResponse(
    (r) => r.url().includes("/admin/pos/context") && r.status() !== 307 && r.status() !== 308,
    { timeout: 60_000 },
  );
  await page.goto("/admin/sales/pos", { waitUntil: "domcontentloaded" });
  const context = await (await contextResponse).json();
  const options: { value: string; label: string; branches: number[]; enabled: boolean;
    disabled_code: string; disabled_reason: string }[] = context.receipt_options ?? [];
  console.log("receipt_options =", JSON.stringify(options));

  const byValue = Object.fromEntries(options.map((o) => [o.value, o]));
  for (const value of ["sales_note", "boleta", "factura"]) {
    expect(byValue[value], `falta la opción ${value} en el contexto`).toBeTruthy();
  }
  if (!byValue.factura.enabled || !byValue.boleta.enabled) {
    test.skip(true, `BETA no preparado: ${byValue.factura.disabled_reason || byValue.boleta.disabled_reason}`);
  }

  const select = page.getByLabel("Tipo de comprobante");
  await expect(select, "el selector de comprobante no cargó").toBeVisible({ timeout: 30_000 });
  const labels = await select.locator("option").allInnerTexts();
  console.log("opciones visibles =", JSON.stringify(labels));

  for (const label of ["Nota de venta interna", "Boleta electrónica · BETA", "Factura electrónica · BETA"]) {
    const option = select.locator("option", { hasText: label });
    await expect(option, `${label} debería estar en el selector`).toHaveCount(1);
    await expect(option, `${label} debería poder elegirse en esta sucursal`).toBeEnabled();
  }
  expect(labels.join(" ")).not.toMatch(/cr[ée]dito|d[ée]bito|baja|resumen/i);
  await expect(page.getByRole("list", { name: "Documentos no disponibles" })).toHaveCount(0);

  await select.selectOption("boleta");
  await expect(page.getByText(/Solo BETA: se prepara y firma el documento/)).toBeVisible();
});
