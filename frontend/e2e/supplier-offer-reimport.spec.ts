import { test, expect, type Page } from "@playwright/test";
import { call, pedirCsrf, shell, signIn } from "./inventory-first-support";

/**
 * B0 — reimportar una lista de WhatsApp sin riesgos (docs/adr-catalog-commercial.md §7), en navegador real.
 *
 * Recorrido: pegar → aplicar → pegar lo mismo → rechazado con explicación → una venta → un mensaje más nuevo →
 * lo disponible es «lo declarado − lo vendido desde el mensaje».
 *
 * QUÉ ES REAL Y QUÉ NO
 * --------------------
 * Real: la pantalla «Carga masiva» (/admin/inventory/bulk), el reconocimiento local del texto, el ensayo del
 * borrador en el servidor, el libro .xlsx que arma el servidor, el importador oficial con su vista previa y su
 * «Aplicar», la venta (carrito → checkout → confirmación firmada de la pasarela, en el shell de Django) y la base.
 * No real: SÓLO `/text-preview/match/` (sólo lectura), servido con `page.route` para que el texto señale la variante
 * de esta corrida sin sembrar un modelo con opciones; su contrato lo prueban los tests de Django.
 *
 * Usa el arnés de `inventory-first-support.ts` (guarda de base local y desechable, marca `TAG`). Limpia lo suyo.
 */

type Seed = { product: number; productName: string; supplier: number; supplierName: string };

const iso = (offsetDays: number) => {
  const day = new Date();
  day.setDate(day.getDate() + offsetDays);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
};

function sembrar(): Seed {
  return shell<Seed>(`
from store.models import Company, Product, Supplier
company = Company.objects.get(slug=data['slug'])
tag = data['tag']
product = Product.objects.create(
    company=company, name='Equipo B0 ' + tag, slug=('equipo-b0-' + tag).lower(), price=Decimal('3200.00'),
    # Sold on the web on order (the sale below is a web checkout); the run's TAG keeps it apart and it is removed after.
    inventory=0, is_active=True, is_published_online=True, sale_mode=Product.SALE_MODE_STOCK_OR_ORDER,
)
supplier = Supplier.objects.create(company=company, name='Proveedor B0 ' + tag)
out({'product': product.pk, 'productName': product.name, 'supplier': supplier.pk, 'supplierName': supplier.name})
`);
}

/** Una venta REAL bajo pedido: carrito, checkout y la confirmación firmada de la pasarela (compromete la oferta). */
function vender(productId: number) {
  return shell<{ order: number; supply_status: string }>(`
from types import SimpleNamespace
from store import checkout_services as checkout
from store.models import CartItem, Company, OrderItem, PaymentTransaction, Product
from store.views import _SignedNotificationMixin
company = Company.objects.get(slug=data['slug'])
branch = company.default_inventory_branch
product = Product.objects.get(company=company, pk=data['product'])
session = 'b0-e2e-' + data['tag']
CartItem.objects.filter(session_key=session).delete()
CartItem.objects.create(session_key=session, product=product, quantity=1, supply_origin=CartItem.SUPPLY_SUPPLIER)
items = list(CartItem.objects.filter(session_key=session).select_related('product'))
lines = checkout.lines_from_cart(company, branch, session, items)
try:
    subtotal = checkout.validate_lines_and_subtotal(branch, lines)
except checkout.CheckoutError as exc:
    raise SystemExit('checkout refused: ' + repr(getattr(exc, 'errors', exc)))
pricing = checkout.price_checkout(company, subtotal)
order = checkout.create_pending_order(
    company=company, branch=branch, lines=lines, pricing=pricing,
    details=checkout.CustomerDetails(
        name='Cliente B0', email='b0@example.com', phone='987654321', document_type='DNI', document_number='12345678',
        delivery_method='pickup', receipt_type='boleta', accepted_terms=True, accepted_warranty_policy=True),
    cart_session_key=session)
attempt = PaymentTransaction.objects.create(order=order, provider='izipay', transaction_id='B0' + str(order.pk).zfill(18),
                                            amount=order.total, currency='PEN')
_SignedNotificationMixin()._confirm(attempt, SimpleNamespace(signed=True, response_code='00', pay_method='CARD',
                                    authorization_code='A1', reference_number='R1', unique_id='U' + str(order.pk)))
out({'order': order.pk, 'supply_status': OrderItem.objects.get(order=order).supply_status})
`, { product: productId });
}

function oferta(productId: number) {
  return shell<{ confirmed: number | null; committed: number; remaining: number | null; declared: number | null;
    messageDate: string | null; validUntil: string }>(`
from store.models import SupplierOffer
offer = SupplierOffer.objects.get(product_id=data['product'])
out({'confirmed': offer.confirmed_quantity, 'committed': offer.committed_quantity, 'remaining': offer.remaining_known,
     'declared': offer.last_declared_quantity, 'messageDate': offer.declared_message_date,
     'validUntil': offer.valid_until})
`, { product: productId });
}

function limpiarB0(seed: Seed) {
  return shell(`
from django.db import transaction
from store.models import *
product = Product.objects.filter(pk=data['product'])
try:
    with transaction.atomic():
        orders = Order.objects.filter(items__product__in=product).distinct()
        OrderItem.objects.filter(order__in=orders).delete()
        PaymentTransaction.objects.filter(order__in=orders).delete()
        orders.delete()
        SupplierOffer.objects.filter(product__in=product).delete()
        SupplierProductRef.objects.filter(product__in=product).delete()
        CartItem.objects.filter(product__in=product).delete()
        product.delete()
        Supplier.objects.filter(pk=data['supplier']).delete()
    out({'ok': True})
except Exception as exc:
    # Something the sale left (a document, a movement) protects the order: the run's data is switched off instead.
    product.update(is_active=False)
    SupplierOffer.objects.filter(product__in=product).update(is_active=False)
    out({'ok': False, 'left': exc.__class__.__name__ + ' ' + str(exc)[:120]})
`, { product: seed.product, supplier: seed.supplier });
}

/** Pega la lista, elige la variante de la corrida y escribe precio, cantidad, proveedor, fecha del mensaje y plazo. */
async function pegar(page: Page, seed: Seed, messageDate: string, quantity: string) {
  await page.goto("/admin/inventory/bulk", { waitUntil: "networkidle" });
  await page.getByLabel("Línea declarada").selectOption("new");
  await page.getByLabel("Origen declarado").selectOption("supplier");
  await page.getByLabel("Texto para reconocer").fill("IPH 15 128 negro 2300");
  await page.getByRole("button", { name: /Analizar/ }).click();
  await page.getByRole("button", { name: "Buscar en el catálogo" }).click();
  await page.getByLabel("Precio comercial del registro 1").fill("3200");
  await page.getByLabel("Cantidad del registro 1").fill(quantity);
  await page.getByLabel("Proveedor del borrador").selectOption(String(seed.supplier));
  await page.getByLabel("Fecha del mensaje del borrador").fill(messageDate);
  // B0: the validity comes from the message date, never from today.
  await expect(page.getByLabel("Vigente desde del borrador")).toHaveValue(messageDate);
  await page.getByLabel("Plazo del borrador").fill("3");
  await page.getByRole("button", { name: "Generar borrador" }).click();
  await expect(page.getByRole("region", { name: "Resumen del borrador" })).toBeVisible();
}

/** «Continuar al importador oficial»: the server's workbook, previewed by the official importer. Its job id too. */
async function alImportador(page: Page) {
  // `next dev` answers `/api/…/` with a 308 to the path without its slash (the route-handler proxy adds it back for
  // Django): the redirect carries no body, and the final answer's URL ends in `/preview`. Match that one.
  const previewed = page.waitForResponse((res) => /\/supplier-offers\/import\/preview\/?$/.test(new URL(res.url()).pathname)
    && res.request().method() === "POST" && res.status() < 300);
  await page.getByRole("button", { name: "Continuar al importador oficial" }).click();
  const job = (await (await previewed).json()) as { id: number };
  const importer = page.getByRole("region", { name: "Importador oficial de ofertas" });
  await expect(importer.getByTestId("supplier-import-preview")).toBeVisible();
  return { importer, job: job.id };
}

test.describe("B0 · reimportar una lista de proveedor", () => {
  test.describe.configure({ mode: "serial" });
  let seed: Seed;

  test.beforeAll(() => { seed = sembrar(); });
  test.afterAll(() => { if (seed) limpiarB0(seed); });

  test("pegar → aplicar → pegar lo mismo (rechazado) → venta → mensaje nuevo: lo vendido no vuelve a venderse", async ({ page }) => {
    await page.route("**/api/admin/inventory/text-preview/match/**", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ matches: { 1: { status: "exact", reason: "", options: [
        { product_id: seed.product, name: seed.productName, condition_line: "new", options: [] }] } } }),
    }));
    await signIn(page);
    const older = iso(-2);

    // 1 · Pegar y aplicar: 3 declaradas el día del mensaje.
    await pegar(page, seed, older, "3");
    let { importer } = await alImportador(page);
    await expect(importer.getByLabel("Fecha del mensaje del proveedor")).toHaveValue(older);
    await expect(importer.getByText("Declaración nueva")).toBeVisible();
    await importer.getByRole("button", { name: "Aplicar cambios" }).click();
    await expect(importer.getByRole("status")).toContainText("Ofertas actualizadas");
    let state = oferta(seed.product);
    expect(state).toMatchObject({ confirmed: 3, committed: 0, remaining: 3, declared: 3, messageDate: older });

    // 2 · Pegar EXACTAMENTE lo mismo: el borrador ya lo señala, y el importador lo rechaza con explicación.
    await pegar(page, seed, older, "3");
    await expect(page.getByTestId("draft-already-applied")).toContainText("Esta lista ya se aplicó");
    const again = await alImportador(page);
    importer = again.importer;
    const refused = importer.getByTestId("already-applied-content");
    await expect(refused).toContainText(/carga n\.º \d+/);
    await expect(refused).toContainText(/aplicada el/);
    await expect(importer.getByText("Repetición: no cambia nada")).toBeVisible();
    await expect(importer.getByRole("button", { name: "Aplicar cambios" })).toBeDisabled();
    // The server says the same, whoever skips the screen: 409 with which load, when and by whom; nothing written.
    await pedirCsrf(page.request);
    const direct = await call<{ code: string; detail: string; already_applied_content: { job_id: number } }>(
      page.request, "POST", `/admin/supplier-offers/import/${again.job}/apply/`, {});
    expect(direct.status).toBe(409);
    expect(direct.body.code).toBe("already_applied_content");
    expect(direct.body.detail).toMatch(/Esta lista ya se aplicó: es idéntica a la carga n\.º \d+/);
    const unreasoned = await call<{ code: string }>(
      page.request, "POST", `/admin/supplier-offers/import/${again.job}/apply/`, { confirm_reapply: true });
    expect(unreasoned.body.code).toBe("reapply_reason_required");
    expect(oferta(seed.product)).toMatchObject({ confirmed: 3, remaining: 3, declared: 3 });

    // 3 · Una venta real bajo pedido (pagada hoy).
    expect(vender(seed.product).supply_status).toBe("pending");
    state = oferta(seed.product);
    expect(state).toMatchObject({ committed: 1, remaining: 2 });

    // 4 · El mismo texto con el mensaje de HOY (más nuevo): el proveedor declara 3; la venta de hoy es posterior al
    // inicio del día del mensaje, así que se descuenta: quedan 2, no 3. Como nada de lo vendido vuelve a venderse,
    // la misma cantidad con una fecha posterior no pide confirmación (revisión final de B0: el control es por fila
    // y sólo cuando se repondría algo vendido).
    await pegar(page, seed, iso(0), "3");
    ({ importer } = await alImportador(page));
    await expect(importer.getByText("Declaración nueva")).toBeVisible();
    await expect(importer.getByText(/se vendieron 1 de este proveedor y variante/)).toBeVisible();
    await expect(importer.getByTestId("same-rows-review")).toHaveCount(0);
    await importer.getByRole("button", { name: "Aplicar cambios" }).click();
    await expect(importer.getByRole("status")).toContainText("Ofertas actualizadas");
    state = oferta(seed.product);
    expect(state).toMatchObject({ committed: 1, remaining: 2, declared: 3, messageDate: iso(0) });
  });
});
