import { execFileSync } from "node:child_process";
import { test, expect, type Page } from "@playwright/test";

/**
 * ORDER-FULFILLMENT-SAFETY-01 — el caso de la orden #4, en navegador real.
 *
 * SIN MOCKS DEL BACKEND. Un pedido de recojo en tienda, pagado, que el
 * selector anterior dejó «Enviado» y luego «Cancelado operativo»: su
 * cancelación está bloqueada porque «ya salió». Un superior corrige ESE
 * registro (motivo + declaración), el registro original sigue en el
 * historial marcado «Corregido», y la cancelación vuelve a estar disponible.
 * Nada se cancela solo.
 */

const RUN = Date.now().toString(36);
const BACKEND_DIR = process.env.E2E_BACKEND_DIR ?? "../backend";

type Siembra = { order: number; event: number };

function sembrar(): Siembra {
  const codigo = `
import json, os
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from store.fulfillment_test_support import legacy_move
from store.models import Company, Order, OrderItem, Product

actor = get_user_model().objects.get(username='dev_admin')
company = Company.objects.get(slug='black-dog-store')
branch = company.default_inventory_branch
product = Product.objects.create(
    company=company, name='Funda despacho ${RUN}', slug='funda-despacho-${RUN}', price=Decimal('49.00'),
    inventory=0, is_active=True, is_published_online=False,
)
order = Order.objects.create(
    company=company, fulfillment_branch=branch, customer_email='e2e-despacho@example.com',
    total=product.price, status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
    delivery_method=Order.DeliveryMethod.PICKUP_STORE,
)
OrderItem.objects.create(order=order, product=product, quantity=1, price=product.price)
event = legacy_move(order, 'shipped', actor=actor)
legacy_move(order, 'cancelled', actor=actor)
print('E2E_SEEDED ' + json.dumps({'order': order.pk, 'event': event.pk}))
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

test("orden #4: corregir el marcado de salida accidental y recién entonces poder cancelar", async ({ page }) => {
  const siembra = sembrar();
  await signIn(page);
  await page.goto(`/admin/orders/${siembra.order}`, { waitUntil: "networkidle" });

  const despacho = page.getByRole("region", { name: "Estado de despacho" });
  await expect(despacho).toBeVisible({ timeout: 30_000 });
  await expect(despacho.getByTestId("dispatch-current")).toHaveText("Cancelado operativo");
  // El historial muestra lo que pasó, aunque no corresponda a un recojo.
  await expect(despacho.getByText("Historial de despacho")).toBeVisible();
  await expect(despacho).toContainText("Enviado");

  let { body } = await pedido(page, siembra.order);
  expect(body.cancel_blockers.map((b: { code: string }) => b.code)).toContain("goods_left");

  // Sin motivo ni declaración, el botón no corrige.
  await despacho.getByRole("button", { name: "Corregir este registro" }).click();
  const corregir = despacho.getByRole("button", { name: "Corregir registro" });
  await expect(corregir).toBeDisabled();
  await despacho.getByLabel("Motivo de la corrección").fill("Marcado por error durante las pruebas de pago");
  await expect(corregir).toBeDisabled();
  await despacho.getByLabel("Confirmo que el producto nunca salió de la tienda").check();
  await corregir.click();

  await expect(despacho.getByText(/Corregido el/)).toBeVisible({ timeout: 20_000 });
  ({ body } = await pedido(page, siembra.order));
  // La venta sigue vigente y el registro original sigue ahí, marcado corregido.
  expect(body.status).toBe("paid");
  expect(body.dispatch.ever_left).toBe(false);
  const original = body.dispatch.history.find((h: { id: number }) => h.id === siembra.event);
  expect(original).toMatchObject({ new_status: "shipped", corrected: true });
  expect(body.dispatch.history.at(-1)).toMatchObject({ action: "order_left_marking_corrected", corrects_id: siembra.event });
  // La cancelación vuelve a evaluarse con sus propias reglas: ya no la bloquea «salió».
  expect(body.cancel_blockers.map((b: { code: string }) => b.code)).not.toContain("goods_left");
  await expect(page.getByRole("button", { name: "Cancelar pedido" }).first()).toBeVisible();
});
