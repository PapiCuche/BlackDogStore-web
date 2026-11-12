import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";

/**
 * F1 · DELIVERY-SHORTFALL-GUARD — un pedido pagado con faltante no se entrega.
 *
 * SIN MOCKS DEL BACKEND. Un pedido web de recojo, pagado, cuya salida de stock
 * no pudo hacerse (el estante estaba vacío): el detalle lo avisa arriba, el
 * despacho no ofrece «Listo para retiro» y el servidor lo rechaza con 409
 * `stock_shortfall`. Se repone el stock (siembra, como haría una compra), se
 * reintenta la salida desde el panel y recién entonces el pedido avanza hasta
 * «Entregado». Nada de esto toca el pago.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";
const PRODUCTO = `Funda faltante ${RUN}`;

function shell(codigo: string): string {
  return execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: process.env,
  }).toString();
}

type Siembra = { order: number; product: number };

function sembrar(): Siembra {
  const out = shell(`
import json
from decimal import Decimal
from django.utils import timezone
from store import inventory_services
from store.models import BranchStock, Company, Order, OrderItem, Product

company = Company.objects.get(slug='black-dog-store')
branch = company.default_inventory_branch
product = Product.objects.create(
    company=company, name='${PRODUCTO}', slug='funda-faltante-${RUN}', price=Decimal('59.00'),
    inventory=0, is_active=True, is_published_online=False,
)
BranchStock.objects.update_or_create(branch=branch, product=product, defaults={'quantity': 0})
order = Order.objects.create(
    company=company, fulfillment_branch=branch, customer_email='e2e-faltante@example.com',
    total=Decimal('118.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
    delivery_method=Order.DeliveryMethod.PICKUP_STORE,
)
OrderItem.objects.create(order=order, product=product, quantity=2, price=product.price)
inventory_services.record_sale_stock_movements(order)          # estante vacío: queda el faltante
assert inventory_services.order_stock_shortfall(order), 'la siembra debía quedar con faltante'
print('E2E_SEEDED ' + json.dumps({'order': order.pk, 'product': product.pk}))
`);
  const line = out.split("\n").find((row) => row.startsWith("E2E_SEEDED "));
  if (!line) throw new Error(`la siembra no terminó: ${out.slice(-400)}`);
  return JSON.parse(line.slice("E2E_SEEDED ".length));
}

function reponer(product: number, cantidad: number) {
  shell(`
from store import inventory_services
from store.models import Company, StockMovement

company = Company.objects.get(slug='black-dog-store')
inventory_services.create_stock_movement(
    branch=company.default_inventory_branch, product_id=${product},
    movement_type=StockMovement.PURCHASE_ENTRY, quantity=${cantidad}, reason='Reposición de prueba E2E',
)
`);
}

async function signIn(page: Page) {
  await page.goto("/auth", { waitUntil: "networkidle" });
  const card = page.locator("section").filter({ hasText: "Accesos de desarrollo" });
  if ((await card.count()) === 0) test.skip(true, "no hay tarjeta de accesos de desarrollo en este entorno");
  const use = card.locator("li").filter({ hasText: "dev_admin" }).getByRole("button", { name: "Usar cuenta" });
  if ((await use.count()) === 0) test.skip(true, "dev_admin no existe: siembra con seed_demo_users");
  await use.click();
  await page.getByRole("button", { name: /iniciar sesión/i }).first().click();
  await page.waitForURL((u) => !u.pathname.startsWith("/auth"), { timeout: 20_000 });
}

async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ raw, method, body }) => {
    const url = new URL(raw, window.location.origin);
    url.hostname = window.location.hostname;
    const csrf = decodeURIComponent(document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] ?? "");
    const res = await fetch(url.toString(), {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRFToken": csrf } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: res.status, body: await res.json().catch(() => null) };
  }, { raw: `${process.env.E2E_API_BASE ?? "/api"}${path}`, method, body });
}

test("pedido pagado con faltante: entrega bloqueada, reponer y reprocesar, y recién entonces entregar", async ({ page }) => {
  const siembra = sembrar();
  await signIn(page);
  await page.goto(`/admin/orders/${siembra.order}`, { waitUntil: "networkidle" });

  // El faltante se ve arriba y dice que la entrega está bloqueada.
  const faltante = page.getByTestId("stock-shortfall-section");
  await expect(faltante).toBeVisible({ timeout: 30_000 });
  await expect(faltante).toContainText("entrega bloqueada");
  await expect(faltante).toContainText(PRODUCTO);
  await expect(page.getByTestId("stock-shortfall-handover")).toContainText("«Entregado»");

  // Prepararlo sigue siendo posible; anunciarlo listo, no.
  const despacho = page.getByRole("region", { name: "Estado de despacho" });
  await despacho.getByRole("button", { name: "Marcar como En preparación" }).click();
  const listo = despacho.getByRole("button", { name: "Marcar como Listo para retiro" });
  await expect(listo).toBeDisabled({ timeout: 20_000 });
  await expect(despacho.getByTestId("dispatch-blockers")).toContainText("Falta stock de lo vendido");
  await expect(despacho.getByTestId("dispatch-blockers")).toContainText(PRODUCTO);

  // El servidor también lo rechaza, con su código y lo que falta.
  const rechazo = await api(page, "PATCH", `/admin/orders/${siembra.order}/fulfillment-status/`,
    { fulfillment_status: "ready_for_pickup", note: "" });
  expect(rechazo.status).toBe(409);
  expect(rechazo.body.code).toBe("stock_shortfall");
  expect(rechazo.body.shortfall).toEqual([expect.objectContaining({ product_id: siembra.product, shortfall: 2 })]);
  let { body } = await api(page, "GET", `/admin/orders/${siembra.order}/`);
  expect(body.fulfillment_status).toBe("preparing");
  expect(body.status).toBe("paid");

  // Se repone el estante y se reintenta la salida desde el panel.
  reponer(siembra.product, 2);
  await page.reload({ waitUntil: "networkidle" });
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByTestId("reprocess-stock-exit-button").click();
  await expect(page.getByTestId("reprocess-feedback")).toContainText("ya no tiene faltante", { timeout: 20_000 });
  await expect(page.getByTestId("stock-shortfall-section")).toHaveCount(0);

  // Ahora sí: listo para retiro y entregado.
  await expect(listo).toBeEnabled({ timeout: 20_000 });
  await listo.click();
  await despacho.getByRole("button", { name: "Marcar como Entregado" }).click();
  await despacho.getByRole("button", { name: "Sí, se entregó" }).click();
  await expect.poll(async () => (await api(page, "GET", `/admin/orders/${siembra.order}/`)).body.fulfillment_status,
    { timeout: 20_000 }).toBe("delivered");
  ({ body } = await api(page, "GET", `/admin/orders/${siembra.order}/`));
  expect(body.stock_shortfall).toEqual([]);
  expect(body.status).toBe("paid");
});
