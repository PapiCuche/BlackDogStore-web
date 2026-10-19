import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";

/**
 * Devolución posterior a la entrega (fase 1), en navegador real. SIN MOCKS.
 *
 * Un pedido de recojo pagado y entregado (por el servicio real, paso a paso).
 * Su cancelación está bloqueada: «requiere devolución». Se registra la
 * devolución, se recibe la mercadería (el stock entra al costo de la venta) y
 * un superior la cierra con un descuento: la venta queda cancelada por
 * devolución y el reembolso PENDIENTE por lo cobrado menos el descuento.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";

type Siembra = { order: number; stock: number };

function sembrar(): Siembra {
  const codigo = `
import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from store import inventory_services
from store.fulfillment_test_support import walk_to
from store.models import BranchStock, Company, Order, OrderItem, Product

actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug='black-dog-store')
branch = company.default_inventory_branch
product = Product.objects.create(
    company=company, name='Cargador devolución ${RUN}', slug='cargador-devolucion-${RUN}', price=Decimal('80.00'),
    inventory=0, is_active=True, is_published_online=False,
)
BranchStock.objects.update_or_create(branch=branch, product=product, defaults={'quantity': 5})
order = Order.objects.create(
    company=company, fulfillment_branch=branch, customer_email='e2e-devolucion@example.com',
    total=Decimal('160.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
    delivery_method=Order.DeliveryMethod.PICKUP_STORE,
)
OrderItem.objects.create(order=order, product=product, quantity=2, price=product.price)
inventory_services.record_sale_stock_movements(order)
walk_to(order, 'delivered', actor=actor, company=company)
stock = BranchStock.objects.get(branch=branch, product=product).quantity
print('E2E_SEEDED ' + json.dumps({'order': order.pk, 'stock': stock}))
`;
  const out = execFileSync("python3", ["manage.py", "shell", "-c", codigo], {
    cwd: BACKEND_DIR,
    stdio: ["ignore", "pipe", "pipe"],
    env: process.env,
  }).toString();
  const line = out.split("\n").find((row) => row.startsWith("E2E_SEEDED "));
  if (!line) throw new Error(`la siembra no terminó: ${out.slice(-400)}`);
  return JSON.parse(line.slice("E2E_SEEDED ".length));
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

async function pedido(page: Page, id: number) {
  return page.evaluate(async (raw) => {
    const url = new URL(raw, window.location.origin);
    url.hostname = window.location.hostname;
    const res = await fetch(url.toString(), { credentials: "include" });
    return { status: res.status, body: await res.json().catch(() => null) };
  }, `${process.env.E2E_API_BASE ?? "/api"}/admin/orders/${id}/`);
}

test("devolución de un pedido entregado: registrar, recibir y cerrar con reembolso pendiente", async ({ page }) => {
  const siembra = sembrar();
  await signIn(page);
  await page.goto(`/admin/orders/${siembra.order}`, { waitUntil: "networkidle" });

  let { body } = await pedido(page, siembra.order);
  expect(body.cancel_blockers.map((b: { code: string }) => b.code)).toContain("goods_left");

  const panel = page.getByRole("region", { name: "Devolución" });
  await expect(panel).toBeVisible({ timeout: 30_000 });

  // 1 · Registrar.
  await panel.getByRole("button", { name: "Registrar devolución" }).click();
  await panel.getByLabel("Tipo de devolución").selectOption("desistimiento");
  await panel.getByLabel("Motivo").fill("El cliente desistió y trajo el pedido completo");
  await panel.getByRole("button", { name: "Registrar devolución" }).click();
  await expect.poll(async () => (await pedido(page, siembra.order)).body.return_block.return?.status,
    { timeout: 20_000 }).toBe("requested");

  // 2 · Recibir: el stock entra al confirmar, no antes.
  await panel.getByLabel("Estado en que llega").selectOption("sellable");
  await panel.getByRole("button", { name: "Recibir mercadería" }).click();
  await panel.getByRole("group", { name: "Confirmar recepción" }).getByRole("button", { name: "Sí, recibir" }).click();
  await expect.poll(async () => (await pedido(page, siembra.order)).body.return_block.return?.status,
    { timeout: 20_000 }).toBe("received");
  ({ body } = await pedido(page, siembra.order));
  expect(body.available_fulfillment_transitions).toEqual([]);

  // 3 · Cerrar con un descuento justificado: reembolso PENDIENTE por 160 − 15.
  await panel.getByLabel(/Descuento/).fill("15");
  await panel.getByLabel(/Motivo del descuento/).fill("Flete de vuelta");
  await panel.getByLabel("Medio del reembolso").selectOption("transfer");
  await panel.getByRole("button", { name: "Cerrar devolución" }).click();
  await panel.getByRole("group", { name: "Confirmar cierre" }).getByRole("button", { name: "Sí, cerrar devolución" }).click();
  await expect.poll(async () => (await pedido(page, siembra.order)).body.status, { timeout: 20_000 }).toBe("cancelled");

  ({ body } = await pedido(page, siembra.order));
  expect(body.return_block.return).toMatchObject({ status: "closed", refund_amount: "145.00", deduction_amount: "15.00" });
  expect(body.cancellation.by_return).toBe(true);
  expect(body.refunds.items.map((r: { status: string; amount: string }) => [r.status, r.amount]))
    .toEqual([["pending", "145.00"]]);
  await expect(page.getByRole("region", { name: "Cancelado por devolución" })).toBeVisible();
});
