"""
NOTE-NOT-SUBSTITUTE — the sales note is never taken for the sale's receipt.

Owner requirement (2026-11-19, before the receipts release): document the tax
verification and make sure the system never hands the note over as a
substitute for the boleta or factura (TUO del Código Tributario, art. 174
num. 1; RCP art. 5: the receipt is issued at payment or delivery, whichever
comes first). The note itself keeps its clean design: every control lives on
the staff screens. docs/nota-de-venta-verificacion-tributaria.md.

What is pinned here:

  * `receipt_coverage` only counts a PRODUCTION receipt that is settled as
    evidence; a sales note, a BETA receipt or a requested `receipt_type` are not;
  * the staff warning says to issue it through SUNAT (SOL or another channel),
    and says the system's electronic invoicing is off when it is;
  * the order detail, the POS sale, the sales note (order panel and control
    list) carry that coverage; the order list and the note list filter the
    sales pending their receipt, inside the caller's company only.
"""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from store import receipt_coverage
from store.fiscal.testing import self_signed_pem
from store.fiscal_services import get_or_create_fiscal_document, sign_fiscal_document, submit_fiscal_document
from store.models import (
    FiscalDocument, FiscalDocumentType, FiscalEnvironment, FiscalSeries, Order, OrderItem, PaymentTransaction,
    SalesChannel,
)
from store.sales_note_services import get_or_create_sales_note
from store.tests import Ip1PosBase, _accepted, _assign, _role, _c1_product, _c1_stock, _FakeProvider, _p2d_member, _p3_company

RUC = '20100066611'
KEY, CERT = self_signed_pem(RUC)
FISCAL_ON = dict(
    FISCAL_ENABLED=True, FISCAL_SOL_RUC=RUC, FISCAL_SOL_USER='MODDATOS', FISCAL_SOL_PASSWORD='moddatos',
    FISCAL_CERT_PEM=CERT.decode(), FISCAL_KEY_PEM=KEY.decode(),
)

#: Words that would claim a receipt exists. None of them may appear in the
#: warning of a sale that has none.
CLAIMS = ('emitido por sunat', 'aceptado por sunat.', 'comprobante emitido:', 'ya tiene comprobante')


class CoverageBase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('nsub', 'Empresa NSUB', tax_id=RUC, legal_name='EMPRESA NSUB SAC')
        self.branch = self.company.default_inventory_branch
        for kind, name in ((FiscalDocumentType.INVOICE, 'F001'), (FiscalDocumentType.RECEIPT, 'B001')):
            FiscalSeries.objects.create(company=self.company, document_type=kind, series=name)
        self.product = _c1_product(self.company, 'Articulo NSUB', '118.00')
        _c1_stock(self.branch, self.product, 50)
        self.staff, _ = _p2d_member(self.company, 'nsub_staff', [
            'company.view', 'sales.orders.view', 'sales.notes.manage', 'sales.fiscal.view', 'sales.fiscal.issue'])

    def order(self, *, company=None, branch=None, receipt=Order.ReceiptType.BOLETA, paid=True,
              status=Order.Status.PAID, channel=SalesChannel.POS):
        company = company or self.company
        factura = receipt == Order.ReceiptType.FACTURA
        order = Order.objects.create(
            company=company, customer_name='CLIENTE SAC' if factura else 'CLIENTE',
            document_type=Order.DocumentType.RUC if factura else Order.DocumentType.DNI,
            document_number='20000000001' if factura else '45678912', receipt_type=receipt,
            total=Decimal('118.00'), discount_amount=Decimal('0.00'), subtotal_amount=Decimal('118.00'),
            taxable_amount=Decimal('100.00'), tax_amount=Decimal('18.00'), tax_rate=Decimal('0.18'),
            tax_treatment='taxed', currency='PEN', status=status, paid=paid,
            paid_at=timezone.now() if paid else None, sales_channel=channel,
            fulfillment_branch=branch or company.default_inventory_branch)
        product = self.product if company == self.company else _c1_product(company, 'Otro', '118.00')
        OrderItem.objects.create(order=order, product=product, quantity=1, price=Decimal('118.00'))
        return order

    def accepted_document(self, order):
        """A factura the fake SUNAT accepts — in BETA, the only environment this version operates."""
        document, _ = get_or_create_fiscal_document(order)
        document = sign_fiscal_document(document, key_pem=KEY, cert_pem=CERT)
        document = submit_fiscal_document(document, _FakeProvider(_accepted()))
        self.assertTrue(document.is_accepted)
        return document

    def api(self, user=None):
        client = APIClient()
        client.force_authenticate(user=user or self.staff)
        return client


class CoverageStateTest(CoverageBase):
    def test_a_paid_sale_without_a_receipt_is_pending_and_says_to_issue_it_through_sunat(self):
        order = self.order()
        result = receipt_coverage.coverage(order)
        self.assertEqual(result['state'], 'pending')
        self.assertFalse(result['integration_enabled'])
        self.assertEqual(result['warning'], receipt_coverage.PENDING_INTEGRATION_OFF)
        self.assertIn('emite la boleta o factura por SUNAT (SOL u otro medio) al cobrar o entregar',
                      result['warning'])
        self.assertIn('facturación electrónica del sistema está desactivada', result['warning'])
        self.assertIn('la nota de venta no reemplaza al comprobante', result['warning'])
        self.assertEqual(result['receipt'], '')

    def test_a_sales_note_is_not_evidence_of_a_receipt(self):
        order = self.order()
        get_or_create_sales_note(order, actor=self.staff)
        self.assertEqual(receipt_coverage.coverage(order)['state'], 'pending')

    @override_settings(**FISCAL_ON)
    def test_with_the_integration_on_the_warning_says_the_system_only_operates_beta(self):
        result = receipt_coverage.coverage(self.order())
        self.assertTrue(result['integration_enabled'])
        self.assertEqual(result['warning'], receipt_coverage.PENDING_INTEGRATION_BETA)
        self.assertIn('entorno de pruebas', result['warning'])

    @override_settings(**FISCAL_ON)
    def test_an_accepted_beta_receipt_is_not_evidence(self):
        order = self.order(receipt=Order.ReceiptType.FACTURA)
        self.accepted_document(order)
        result = receipt_coverage.coverage(order)
        self.assertEqual(result['state'], 'pending')
        for claim in CLAIMS:
            self.assertNotIn(claim, result['warning'].lower())

    @override_settings(**FISCAL_ON)
    def test_an_accepted_production_receipt_is_evidence(self):
        order = self.order(receipt=Order.ReceiptType.FACTURA)
        document = self.accepted_document(order)
        # Production cannot be issued by this version (fiscal_config fails closed);
        # the row is moved by hand to pin what the coverage would read.
        FiscalDocument.objects.filter(pk=document.pk).update(environment=FiscalEnvironment.PRODUCTION)
        result = receipt_coverage.coverage(order)
        self.assertEqual(result['state'], 'system')
        self.assertEqual(result['warning'], '')
        self.assertEqual(result['receipt'], document.document_id)

    @override_settings(**FISCAL_ON)
    def test_a_production_boleta_signed_but_not_reported_is_not_evidence(self):
        order = self.order()
        document, _ = get_or_create_fiscal_document(order)
        sign_fiscal_document(document, key_pem=KEY, cert_pem=CERT)
        FiscalDocument.objects.filter(pk=document.pk).update(environment=FiscalEnvironment.PRODUCTION)
        self.assertEqual(receipt_coverage.coverage(order)['state'], 'pending')

    def test_nothing_charged_is_not_due(self):
        for status in (Order.Status.PENDING_PAYMENT, Order.Status.FAILED, Order.Status.EXPIRED):
            with self.subTest(status=status):
                result = receipt_coverage.coverage(self.order(paid=False, status=status))
                self.assertEqual(result['state'], 'not_due')
                self.assertEqual(result['warning'], '')

    def test_a_cancelled_sale_is_left_to_the_accounting_centre(self):
        result = receipt_coverage.coverage(self.order(status=Order.Status.CANCELLED))
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['warning'], '')

    def test_a_web_order_charged_only_with_test_keys_owes_no_receipt(self):
        order = self.order(channel=SalesChannel.ONLINE)
        PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='TX-nsub-test', order_number=f'ORD-{order.pk}',
            amount=order.total, currency='PEN', environment='test',
            status=PaymentTransaction.Status.AUTHORIZED, confirmed_at=timezone.now())
        result = receipt_coverage.coverage(order)
        self.assertEqual(result['state'], 'test_payment')
        self.assertIn('claves de pruebas', result['warning'])

    def test_a_web_order_charged_for_real_owes_its_receipt(self):
        order = self.order(channel=SalesChannel.ONLINE)
        PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='TX-nsub-prod', order_number=f'ORD-{order.pk}',
            amount=order.total, currency='PEN', environment='production',
            status=PaymentTransaction.Status.AUTHORIZED, confirmed_at=timezone.now())
        self.assertEqual(receipt_coverage.coverage(order)['state'], 'pending')

    def test_the_pending_queryset_matches_the_single_answer(self):
        pending, unpaid, cancelled = (
            self.order(), self.order(paid=False, status=Order.Status.PENDING_PAYMENT),
            self.order(status=Order.Status.CANCELLED))
        ids = set(receipt_coverage.pending(Order.objects.all()).values_list('pk', flat=True))
        self.assertIn(pending.pk, ids)
        self.assertNotIn(unpaid.pk, ids)
        self.assertNotIn(cancelled.pk, ids)


class CoverageOnTheStaffScreensTest(CoverageBase):
    def setUp(self):
        super().setUp()
        self.other = _p3_company('nsub-otra', 'Otra NSUB', tax_id='20100066612', legal_name='OTRA SAC')

    def test_the_order_detail_carries_the_coverage(self):
        order = self.order()
        response = self.api().get(f'/api/admin/orders/{order.pk}/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['receipt_coverage']['state'], 'pending')
        self.assertEqual(response.json()['receipt_coverage']['warning'], receipt_coverage.PENDING_INTEGRATION_OFF)

    def test_the_order_list_filters_the_sales_pending_their_receipt_inside_the_company(self):
        pending = self.order()
        unpaid = self.order(paid=False, status=Order.Status.PENDING_PAYMENT)
        foreign = self.order(company=self.other)
        response = self.api().get('/api/admin/orders/', {'receipt': 'pending'})
        self.assertEqual(response.status_code, 200, response.content)
        ids = [row['id'] for row in response.json()['results']]
        self.assertEqual(ids, [pending.pk])
        self.assertNotIn(foreign.pk, ids)
        self.assertNotIn(unpaid.pk, ids)
        self.assertEqual(response.json()['count'], 1)
        self.assertTrue(response.json()['results'][0]['receipt_pending'])

    def test_every_row_says_whether_it_is_pending(self):
        pending = self.order()
        unpaid = self.order(paid=False, status=Order.Status.PENDING_PAYMENT)
        rows = {row['id']: row for row in self.api().get('/api/admin/orders/').json()['results']}
        self.assertTrue(rows[pending.pk]['receipt_pending'])
        self.assertFalse(rows[unpaid.pk]['receipt_pending'])

    def test_the_sales_note_of_the_order_carries_the_coverage(self):
        order = self.order()
        created = self.api().post(f'/api/admin/orders/{order.pk}/sales-note/')
        self.assertIn(created.status_code, (200, 201), created.content)
        self.assertEqual(created.json()['receipt_coverage']['state'], 'pending')
        fetched = self.api().get(f'/api/admin/orders/{order.pk}/sales-note/')
        self.assertEqual(fetched.json()['receipt_coverage']['warning'], receipt_coverage.PENDING_INTEGRATION_OFF)

    def test_the_sales_note_control_list_and_detail(self):
        order = self.order()
        note, _ = get_or_create_sales_note(order, actor=self.staff)
        foreign = self.order(company=self.other)
        get_or_create_sales_note(foreign, actor=None)
        listing = self.api().get('/api/admin/sales-notes/', {'receipt': 'pending'})
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual([row['id'] for row in listing.json()['results']], [note.pk])
        self.assertTrue(listing.json()['results'][0]['receipt_pending'])
        detail = self.api().get(f'/api/admin/sales-notes/{note.pk}/')
        self.assertEqual(detail.json()['receipt_coverage']['state'], 'pending')


class PosSaleCoverageTest(Ip1PosBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def test_a_till_sale_with_only_a_sales_note_tells_the_cashier_to_issue_the_receipt(self):
        response = self.client.post('/api/admin/pos/sales/', self.sale_body(receipt_type='sales_note'), format='json')
        self.assertIn(response.status_code, (200, 201), response.content)
        coverage = response.json()['receipt_coverage']
        self.assertEqual(coverage['state'], 'pending')
        self.assertFalse(coverage['integration_enabled'])
        self.assertEqual(coverage['warning'], receipt_coverage.PENDING_INTEGRATION_OFF)

    def test_a_till_sale_without_any_document_is_pending_too(self):
        response = self.client.post('/api/admin/pos/sales/', self.sale_body(), format='json')
        self.assertIn(response.status_code, (200, 201), response.content)
        self.assertEqual(response.json()['receipt_coverage']['state'], 'pending')

    def test_with_the_integration_off_the_till_refuses_a_boleta_it_cannot_issue(self):
        # Unchanged behaviour, pinned: the cashier is never told a receipt was issued.
        _assign(self.membership, _role(self.company, 'Rol emisor NSUB', ['sales.fiscal.issue'], slug='nsub-emisor'))
        cache.clear()
        response = self.client.post('/api/admin/pos/sales/', self.sale_body(receipt_type='boleta'), format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('no está habilitada', response.json()['detail'])
