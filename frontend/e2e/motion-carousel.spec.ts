import { test, expect } from '@playwright/test';
for (const width of [390, 1440]) {
for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test(`carrusel ${width}px con movimiento ${reducedMotion}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.emulateMedia({ reducedMotion });
    await page.goto('/');
    const carousel = page.getByRole('region', { name: 'Productos destacados' });
    await expect(carousel).toBeVisible();
    await carousel.scrollIntoViewIfNeeded();
    const track = carousel.locator('[data-carousel-track]');
    if (reducedMotion === 'no-preference') {
      // Tomar el control detiene el avance —pulsar «pausar» lo ALTERNA, y no
      // se sabe de antemano en qué estado está— y se vuelve al inicio.
      await track.focus();
      await page.keyboard.press('Home');
      await expect(carousel.getByRole('button', { name: 'Reanudar avance automático' })).toBeVisible();
    }
    await expect(carousel.getByRole('button', { name: 'Productos anteriores' })).toBeDisabled();
    await carousel.getByRole('button', { name: 'Productos siguientes' }).click();
    await expect.poll(() => track.evaluate(el => el.scrollLeft)).toBeGreaterThan(100);
    await expect(carousel.getByRole('button', { name: 'Productos anteriores' })).toBeEnabled();
    await track.focus();
    await page.keyboard.press('Home');
    await expect.poll(() => track.evaluate(el => el.scrollLeft)).toBeLessThan(2);
    await expect(carousel.getByRole('link').first()).toHaveAttribute('href', /\/product\//);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
}

test('avance automático cede el control y permite reanudar', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  const carousel = page.getByRole('region', { name: 'Productos destacados' });
  await carousel.scrollIntoViewIfNeeded();
  const track = carousel.locator('[data-carousel-track]');
  await expect.poll(() => track.evaluate(el => el.scrollLeft), { timeout: 7000 }).toBeGreaterThan(100);
  const before = await track.evaluate(el => el.scrollLeft);
  await page.waitForTimeout(1000);
  const delta = await track.evaluate(el => el.scrollLeft) - before;
  expect(delta).toBeGreaterThan(5);
  expect(delta).toBeLessThan(30);
  await track.focus();
  await page.keyboard.press('Home');
  await expect.poll(() => track.evaluate(el => el.scrollLeft)).toBeLessThan(2);
  await expect(carousel.getByRole('button', { name: 'Reanudar avance automático' })).toBeVisible();
  await page.waitForTimeout(4500);
  expect(await track.evaluate(el => el.scrollLeft)).toBeLessThan(2);
  await carousel.getByRole('button', { name: 'Reanudar avance automático' }).click();
  await page.mouse.move(0, 0);
  await expect.poll(() => track.evaluate(el => el.scrollLeft), { timeout: 7000 }).toBeGreaterThan(100);
});
