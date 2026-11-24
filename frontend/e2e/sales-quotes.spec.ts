import { test, expect, type Page } from "@playwright/test";
import { shell, signIn, RUN } from "./inventory-first-support";

/**
 * SALES-QUOTE-01 — cotizaciones en navegador real y con el servidor de Django real (guarda de base desechable de
 * `inventory-first-guard`). Datos inventados. NO EJECUTADO en el PR que lo añade: se corre con la pasada completa.
 *
 *   E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3002 E2E_API_BASE=http://127.0.0.1:8100/api \
 *     npx playwright test e2e/sales-quotes.spec.ts
 *
 * A · se compone, se aprueba y se envía; ni el stock ni las ventas cambian;
 * B · editar lo ya enviado crea la revisión 2 y la 1 queda «Reemplazada»;
 * C · convertir en venta con el precio cambiado: muestra los dos totales y cobra el de hoy al confirmarlo;
 * D · la lista en un móvil de 390 px, sin desborde.
 * E · TRADE-IN-QUOTE-00: un equipo ofrecido en parte de pago se valoriza y se imprime aparte; al convertir se avisa
 *     que la parte de pago no se aplica y la venta se cobra COMPLETA (sin crédito, sin descuento, sin stock de más).
 */

const NAME = `Accesorio cotizable ${RUN}`;

function sembrar() {
  return shell<{ product: number; stock: number }>(`
from decimal import Decimal
from store.models import BranchStock, Company, Product
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
product = Product.objects.create(company=company, name=data['name'], slug=data['name'].lower().replace(' ', '-'),
                                 price=Decimal('50.00'), inventory=0, is_active=True, is_published_online=False)
BranchStock.objects.update_or_create(branch=branch, product=product, defaults={'quantity': 10})
out({'product': product.pk, 'stock': 10})
`, { slug: "black-dog-store", name: NAME });
}

function estado(product: number) {
  return shell<{ stock: number; orders: number; movements: number }>(`
from store.models import BranchStock, OrderItem, StockMovement
out({'stock': sum(BranchStock.objects.filter(product_id=data['product']).values_list('quantity', flat=True)),
     'orders': OrderItem.objects.filter(product_id=data['product']).count(),
     'movements': StockMovement.objects.filter(product_id=data['product']).count()})
`, { product });
}

function cambiarPrecio(product: number, price: string) {
  shell(`
from store.models import Product
Product.objects.filter(pk=data['product']).update(price=data['price'])
out({'ok': True})
`, { product, price });
}

async function componer(page: Page) {
  await page.goto("/admin/sales/quotes/new", { waitUntil: "networkidle" });
  await page.getByRole("combobox", { name: "Buscar producto" }).fill(NAME.slice(0, 20));
  await page.getByRole("button", { name: "Agregar" }).first().click();
  await page.getByLabel(`Cantidad de ${NAME}`).fill("2");
  await page.getByLabel("Nombre (prospecto)").fill("Cliente de prueba");
  await page.getByRole("button", { name: "Guardar borrador" }).click();
  await expect(page.getByRole("heading", { name: /COT-/ })).toBeVisible({ timeout: 20_000 });
}

test.describe("cotizaciones comerciales", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(180_000);
  let seed: { product: number; stock: number };

  test.beforeAll(() => { seed = sembrar(); });

  test("A · componer, aprobar y enviar no mueve stock ni vende", async ({ page }) => {
    await signIn(page);
    const before = estado(seed.product);
    await componer(page);
    await page.getByRole("button", { name: "Aprobar" }).click();
    await expect(page.getByText("Aprobada").first()).toBeVisible();
    await page.getByRole("button", { name: "Marcar como enviada" }).click();
    await expect(page.getByText("Enviada").first()).toBeVisible();
    expect(estado(seed.product)).toEqual(before);
  });

  test("B · editar lo enviado crea la revisión 2", async ({ page }) => {
    await signIn(page);
    await page.goto("/admin/sales/quotes", { waitUntil: "networkidle" });
    await page.getByLabel("Estado").selectOption("sent");
    await page.getByRole("link", { name: /COT-/ }).first().click();
    await page.getByRole("button", { name: "Nueva revisión" }).click();
    await page.getByRole("button", { name: "Guardar como revisión nueva" }).click();
    await expect(page.getByRole("heading", { name: /-R2$/ })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Reemplazada")).toBeVisible();
  });

  test("C · convertir con el precio cambiado pide decidir y cobra el de hoy", async ({ page }) => {
    await signIn(page);
    await componer(page);
    await page.getByRole("button", { name: "Aprobar" }).click();
    cambiarPrecio(seed.product, "60.00");
    await page.getByRole("button", { name: "Convertir en venta" }).click();
    await page.getByLabel("Efectivo recibido").fill("500");
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: /^Cobrar S\/ 100/ }).click();
    await expect(page.getByText("El precio cambió desde la cotización.")).toBeVisible();
    await page.getByRole("button", { name: "Cobrar precio de hoy" }).click();
    await expect(page.getByText(/Convertida en la venta/)).toBeVisible({ timeout: 20_000 });
    expect(estado(seed.product).stock).toBe(seed.stock - 2);
  });

  test("D · la lista cabe en un móvil", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    await signIn(page);
    await page.goto("/admin/sales/quotes", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "Cotizaciones" })).toBeVisible();
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over).toBeLessThanOrEqual(1);
    await context.close();
  });

  test("E · la parte de pago se valoriza aparte y la venta se cobra completa", async ({ page }) => {
    await signIn(page);
    const before = estado(seed.product);
    await componer(page);
    await page.getByRole("button", { name: "Editar" }).click();
    await page.getByRole("button", { name: "Agregar equipo en parte de pago" }).click();
    await page.getByLabel("Equipo (modelo)").fill("Equipo usado de prueba");
    await page.getByLabel("Estado declarado").selectOption("good");
    await page.getByLabel("IMEI: últimos 4 dígitos").fill("356789012344321");
    await expect(page.getByLabel("IMEI: últimos 4 dígitos")).toHaveValue("4321");
    await page.getByLabel("Valorización estimada").fill("100");
    await page.getByRole("button", { name: "Guardar borrador" }).click();
    const block = page.getByRole("region", { name: "Equipo ofrecido en parte de pago (referencial)" });
    await expect(block.getByText("****4321")).toBeVisible({ timeout: 20_000 });
    await expect(block.getByText("Saldo estimado a pagar")).toBeVisible();
    await page.getByRole("button", { name: "Aprobar" }).click();
    await page.getByRole("button", { name: "Convertir en venta" }).click();
    await expect(page.getByRole("note")).toContainText("La parte de pago aún no se puede aplicar: la venta se cobra completa");
    await page.getByLabel("Efectivo recibido").fill("500");
    await page.getByRole("checkbox").check();
    // The full total of two units at today's price (test C left it at 60.00): no credit for the offered device.
    await page.getByRole("button", { name: /^Cobrar S\/ 120/ }).click();
    await expect(page.getByText(/Convertida en la venta/)).toBeVisible({ timeout: 20_000 });
    await expect(block.getByText("****4321")).toBeVisible();
    const after = estado(seed.product);
    expect(after.stock).toBe(before.stock - 2);
    expect(after.orders).toBe(before.orders + 1);
  });
});
