import { readFileSync, statSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";
import { RUN, shell, signIn } from "./inventory-first-support";

/**
 * SALES-EMAIL-03 — «Enviar copia a otro correo» con Django de verdad.
 *
 * Una venta de mostrador sintética (producto, stock y cliente con la marca de la corrida) se crea por
 * `manage.py shell`; la copia se envía desde la interfaz con `dev_admin`, en escritorio y a 390 px.
 *
 * NINGÚN CORREO SALE DE LA MÁQUINA. Django corre con `EMAIL_BACKEND=…console.EmailBackend` y sin integración
 * SMTP en su base desechable: la prueba lo comprueba antes de empezar y lee el mensaje en el registro del
 * servidor (`E2E_DJANGO_LOG`). Comprueba que fue UN mensaje, a la dirección escrita y a ninguna otra, y que el
 * correo del cliente sigue siendo el mismo.
 */

const LOG = process.env.E2E_DJANGO_LOG ?? "";
const CUSTOMER_EMAIL = `cliente.${RUN.toLowerCase()}@correo-e2e.test`;

type Sale = { order: number; customer_email: string };

function logSince(offset: number): string {
  // `offset` is a size in BYTES (the log has accented text): cut the buffer, then decode.
  return readFileSync(LOG).subarray(offset).toString("utf8");
}

function logSize(): number {
  return statSync(LOG).size;
}

let sale: Sale;

test.beforeAll(() => {
  if (!LOG) throw new Error("SALES-EMAIL-03 necesita E2E_DJANGO_LOG: el registro donde Django escribe los correos.");
  // Antes de nada: este Django no puede enviar correo de verdad.
  const mailer = shell<{ backend: string; legacy: string; smtp: number }>(`
from django.conf import settings
from store.models import IntegrationConfig
out({'backend': settings.EMAIL_BACKEND, 'legacy': settings.EMAIL_BACKEND_LEGACY,
     'smtp': IntegrationConfig.objects.filter(provider__icontains='smtp').count()})
`);
  expect(mailer.legacy).toBe("django.core.mail.backends.console.EmailBackend");
  expect(mailer.smtp).toBe(0);

  sale = shell<Sale>(`
from django.contrib.auth import get_user_model
from store import inventory_services, pos_services
from store.models import Company, Customer, Product, StockMovement
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
actor = get_user_model().objects.get(username='dev_admin')
product = Product.objects.create(
    company=company, name='Funda copia ' + data['tag'], slug=('funda-copia-' + data['tag']).lower(),
    price=Decimal('59.00'), inventory=0, is_active=True, is_published_online=False,
)
inventory_services.create_stock_movement(
    branch=branch, product_id=product.pk, movement_type=StockMovement.INITIAL_STOCK, quantity=3,
    reason='[E2E] SALES-EMAIL-03', actor=actor,
)
customer = Customer.objects.create(
    company=company, first_name='Cliente', last_name='Copia ' + data['tag'], email=data['customer_email'],
    phone='900000000', document_type='dni', document_number=data['document'],
)
order, _ = pos_services.create_pos_sale(
    actor=actor, company=company, branch=branch, items=[{'product': product.pk, 'quantity': 1}],
    customer=customer.pk, terms_confirmed=True, idempotency_key='sales-email-03-' + data['tag'],
    amount_received=Decimal('100.00'),
)
out({'order': order.pk, 'customer_email': order.customer_email})
`, { customer_email: CUSTOMER_EMAIL, document: String(Date.now()).slice(-8) });
  expect(sale.customer_email).toBe(CUSTOMER_EMAIL);
});

async function sendCopy(page: Page, address: string) {
  await page.goto(`/admin/orders/${sale.order}`, { waitUntil: "networkidle" });
  // The first visit compiles the route under `next dev`.
  await expect(page.getByRole("heading", { name: `Orden #${sale.order}` })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Reenviar al correo del cliente" })).toBeVisible();
  const baseline = await pageOverflow(page);
  await page.getByRole("button", { name: "Enviar copia a otro correo" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Documento que se envía")).toBeVisible();
  // Lo que existe y lo que no, con su nombre: sin nota emitida ni comprobante, la confirmación.
  await expect(dialog.getByRole("radio", { name: /constancia de pedido/i })).toBeChecked();
  await expect(dialog.getByText("Esta venta todavía no tiene nota de venta interna.")).toBeVisible();
  await expect(dialog.getByText(CUSTOMER_EMAIL)).toBeVisible();

  // Dos direcciones en un campo: no se puede ni revisar.
  await dialog.getByLabel("Correo de destino", { exact: true }).fill(`${address}, otro@empresa-e2e.test`);
  await dialog.getByLabel("Repite el correo de destino").fill(`${address}, otro@empresa-e2e.test`);
  await dialog.getByRole("button", { name: "Revisar envío" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Escribe una sola dirección");

  await dialog.getByLabel("Correo de destino", { exact: true }).fill(address);
  await dialog.getByLabel("Repite el correo de destino").fill(address.toUpperCase());
  await dialog.getByRole("button", { name: "Revisar envío" }).click();
  const review = dialog.getByTestId("document-copy-review");
  await expect(review.getByTestId("document-copy-recipient")).toHaveText(address);
  await expect(review).toContainText(`pedido-${sale.order}.pdf`);
  await expect(review).toContainText("Contiene datos personales y comerciales");

  const offset = logSize();
  await review.getByRole("button", { name: "Confirmar y enviar" }).click();
  await expect(dialog.getByRole("status")).toContainText(`Copia enviada a ${address}`);
  return { dialog, offset, baseline };
}

/** How far the page scrolls sideways: 0 when nothing overflows. */
function pageOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

function assertDelivered(offset: number, address: string) {
  // The console backend prints every header it hands over: one `To:` line per message, none for Cc/Bcc.
  const printed = logSince(offset);
  const recipients = [...printed.matchAll(/^To: (.+)$/gm)].map((match) => match[1].trim());
  expect(recipients).toEqual([address]);
  expect(printed).not.toMatch(/^(Cc|Bcc): /m);
  expect(printed).toContain(`pedido-${sale.order}.pdf`);

  const after = shell<{ email: string; masked: string; stored: string; result: string }>(`
import json
from store.models import AdminAuditLog, Order
row = AdminAuditLog.objects.filter(action='order_document_copy_sent', target_id=str(data['order'])).order_by('-pk').first()
out({'email': Order.objects.get(pk=data['order']).customer_email, 'masked': row.metadata['recipient_masked'],
     'stored': json.dumps(row.metadata), 'result': row.metadata['result']})
`, { order: sale.order });
  expect(after.email).toBe(CUSTOMER_EMAIL);
  expect(after.result).toBe("sent");
  expect(after.masked).toMatch(/^co\*\*\*@empresa-e2e\.test$/);
  expect(after.stored).not.toContain(address);
}

test("escritorio: la copia va sólo a la dirección escrita y el cliente no cambia", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 860 });
  await signIn(page);
  const address = `contador.desk.${RUN.toLowerCase()}@empresa-e2e.test`;
  const { offset } = await sendCopy(page, address);
  await expect.poll(() => logSince(offset).includes(`To: ${address}`), { timeout: 10_000 }).toBe(true);
  assertDelivered(offset, address);
});

test("390 px: el diálogo cabe, se lee el destinatario y la copia sale una vez", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  const address = `contador.movil.${RUN.toLowerCase()}@empresa-e2e.test`;
  const { dialog, offset, baseline } = await sendCopy(page, address);
  // The dialog adds no sideways scroll of its own. (The order's product table already overflows at
  // 390 px on master — `baseline` — and is outside this change; it is reported, not hidden.)
  expect(await pageOverflow(page)).toBeLessThanOrEqual(Math.max(baseline, 0));
  if (baseline > 0) console.warn(`SALES-EMAIL-03: la página del pedido ya desborda ${baseline}px a 390 px (tabla de productos).`);
  const inner = await dialog.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(inner).toBeLessThanOrEqual(0);
  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  await expect.poll(() => logSince(offset).includes(`To: ${address}`), { timeout: 10_000 }).toBe(true);
  assertDelivered(offset, address);
});
