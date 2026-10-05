import { expect, test } from "@playwright/test";
import { CUTOUT, SLUG, api, signIn } from "./media-helpers";

/**
 * SERVICE-EVIDENCE-CONTEXT — las fotos del servicio, con un navegador de verdad.
 *
 * En la orden que siembra `seed_demo_users --e2e-fixtures`: se eligen dos fotos
 * para «Diagnóstico», se anota una, se suben juntas, y siguen ahí —con su nota,
 * su etapa y su autor— después de recargar. Se abren en grande.
 *
 * Una evidencia no se borra: la prueba ANULA las suyas al terminar, que es lo
 * que haría una persona, y mide siempre contra lo que había antes.
 */

const INTERNAL = `/api/v1/internal/${SLUG}`;
const NOTE = `[E2E] Corrosión junto al conector ${Date.now().toString(36)}`;

test("el técnico sube varias fotos con nota y la orden las conserva", async ({ page, playwright, baseURL }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await signIn(page);

  const orders = await api(page, "GET", `${INTERNAL}/service/orders/`);
  const order = (orders.data?.results ?? orders.data ?? [])[0];
  test.skip(!order, "la base de pruebas no tiene órdenes de servicio sembradas");

  const base = `${INTERNAL}/service/orders/${order.id}/evidence`;
  const before = await api(page, "GET", `${base}/`);
  const baseline = before.data.stage_counts?.diagnosis ?? 0;
  const known = new Set<number>(before.data.results.map((row: { id: number }) => row.id));

  await page.goto(`/admin/service/orders/${order.id}`, { waitUntil: "networkidle" });
  await expect(page.getByLabel("Etapa de las fotos")).toBeVisible({ timeout: 30_000 });

  try {
    // La cámara está a un toque y no impide elegir fotos ya tomadas.
    await expect(page.getByLabel("Tomar foto")).toHaveAttribute("capture", "environment");
    expect(await page.getByLabel("Elegir fotos").evaluate((input: HTMLInputElement) => input.multiple)).toBe(true);

    await page.getByLabel("Etapa de las fotos").selectOption("diagnosis");
    await page.getByLabel("Elegir fotos").setInputFiles([
      { name: "placa.png", mimeType: "image/png", buffer: CUTOUT },
      { name: "conector.png", mimeType: "image/png", buffer: CUTOUT },
    ]);
    const queue = page.getByRole("list", { name: "Fotos por subir" });
    await expect(queue.getByRole("listitem")).toHaveCount(2);
    await page.getByLabel("Nota de placa.png").fill(NOTE);

    await page.getByRole("button", { name: "Subir 2 fotos" }).click();
    await expect(queue).toHaveCount(0, { timeout: 60_000 });

    // --- la galería ------------------------------------------------------------
    const summary = page.getByRole("list", { name: "Evidencias por etapa" });
    await expect(summary.getByText(new RegExp(`Diagnóstico · ${baseline + 2} fotos?`))).toBeVisible();
    await expect(page.getByText(NOTE)).toBeVisible();

    // --- persiste al recargar ----------------------------------------------------
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.getByText(NOTE)).toBeVisible({ timeout: 30_000 });
    const after = await api(page, "GET", `${base}/`);
    const mine = after.data.results.filter((row: { id: number }) => !known.has(row.id));
    expect(mine).toHaveLength(2);
    for (const row of mine) {
      expect(row.stage).toBe("diagnosis");
      expect(row.visibility).toBe("internal");
      expect(row.uploaded_by).toBe("dev_admin");
      expect(JSON.stringify(row)).not.toContain("storage_key");
    }
    expect(mine.map((row: { caption: string }) => row.caption).sort()).toEqual(["", NOTE].sort());

    // --- en grande ------------------------------------------------------------------
    await page.getByRole("button", { name: `Ver en grande: ${NOTE}` }).click();
    const viewer = page.getByRole("dialog", { name: "Evidencia" });
    await expect(viewer).toContainText(NOTE);
    const big = viewer.getByRole("img");
    await expect(big).toHaveAttribute("src", /\/evidence\/\d+\/content\/$/);
    await expect.poll(() => big.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Escape");
    await expect(viewer).toHaveCount(0);

    // Una evidencia interna no se sirve a quien no tiene sesión.
    // (Un contexto nuevo: `page.request` comparte las cookies de la página.)
    const stranger = await playwright.request.newContext({ baseURL });
    const anonymous = await stranger.get(`${base}/${mine[0].id}/content/`, { failOnStatusCode: false });
    expect([401, 403, 404]).toContain(anonymous.status());
    const listing = await stranger.get(`${base}/`, { failOnStatusCode: false });
    expect([401, 403, 404]).toContain(listing.status());
    await stranger.dispose();

    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  } finally {
    const now = await api(page, "GET", `${base}/`);
    for (const row of now.data?.results ?? []) {
      if (!known.has(row.id) && !row.voided_at) {
        await api(page, "POST", `${base}/${row.id}/void/`, { reason: "[E2E] limpieza de la prueba" });
      }
    }
  }
  expect(errors).toEqual([]);
});
