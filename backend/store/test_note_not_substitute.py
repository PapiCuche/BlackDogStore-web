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


# ---------------------------------------------------------------------------
# The receipt issued OUTSIDE the system, registered to reconcile
# ---------------------------------------------------------------------------

class ExternalReceiptTest(CoverageBase):
    def setUp(self):
        super().setUp()
        self.order_ = self.order()
        self.url = f'/api/admin/orders/{self.order_.pk}/external-receipt/'

    def body(self, **over):
        data = {'document_type': '03', 'series': 'eb01', 'number': '17',
                'issue_date': timezone.localdate().isoformat(), 'channel': 'sunat_sol',
                'reference': 'Consulta SEE-SOL, PDF descargado'}
        data.update(over)
        return data

    def register(self, order=None, user=None, **over):
        url = f'/api/admin/orders/{(order or self.order_).pk}/external-receipt/'
        return self.api(user).post(url, self.body(**over), format='json')

    def test_registering_marks_the_sale_external_never_accepted(self):
        from store.models import AdminAuditLog, ExternalFiscalReceipt

        response = self.register()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        row = body['registrations'][0]
        self.assertEqual((row['series'], row['number'], row['status']), ('EB01', 17, 'registered'))
        self.assertEqual(body['coverage']['state'], 'external')
        self.assertEqual(body['coverage']['receipt'], 'EB01-17')
        self.assertIn('Comprobante externo registrado', body['coverage']['warning'])
        self.assertIn('no verifica su aceptación en SUNAT', body['coverage']['warning'])
        self.assertNotIn('aceptado', ' '.join(str(v) for v in row.values()).lower())
        self.assertFalse(body['can_register'])
        # Nothing on the row says SUNAT accepted it: no such field exists.
        self.assertFalse({'is_accepted', 'sunat_status', 'cdr'} & {f.name for f in ExternalFiscalReceipt._meta.fields})
        # It leaves «ventas sin comprobante».
        self.assertFalse(receipt_coverage.pending(Order.objects.filter(pk=self.order_.pk)).exists())
        log = AdminAuditLog.objects.get(action='external_receipt_registered')
        self.assertEqual(log.metadata['order_id'], self.order_.pk)
        self.assertEqual((log.metadata['series'], log.metadata['number']), ('EB01', 17))
        self.assertEqual(log.company_id, self.company.pk)

    def test_the_order_list_no_longer_lists_it_as_pending(self):
        self.register()
        ids = [row['id'] for row in self.api().get('/api/admin/orders/', {'receipt': 'pending'}).json()['results']]
        self.assertNotIn(self.order_.pk, ids)

    def test_one_live_registration_per_sale(self):
        self.assertEqual(self.register().status_code, 201)
        second = self.register(number='18')
        self.assertEqual(second.status_code, 400)
        self.assertIn('ya tiene registrado', second.json()['detail'])

    def test_one_live_registration_per_number_in_the_company(self):
        self.assertEqual(self.register().status_code, 201)
        other_sale = self.order()
        response = self.register(order=other_sale)
        self.assertEqual(response.status_code, 400)
        self.assertIn(f'pedido N.º {self.order_.pk}', response.json()['detail'])

    def test_the_database_holds_both_rules(self):
        from django.db import IntegrityError, transaction

        from store.models import ExternalFiscalReceipt

        fields = dict(company=self.company, document_type='03', series='EB01', number=5,
                      issue_date=timezone.localdate(), channel='sunat_sol', reference='ref')
        ExternalFiscalReceipt.objects.create(order=self.order_, **fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExternalFiscalReceipt.objects.create(order=self.order(), **fields)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExternalFiscalReceipt.objects.create(order=self.order_, **{**fields, 'number': 6})

    def test_the_same_number_in_another_company_is_another_receipt(self):
        other = _p3_company('nsub-ext', 'Otra EXT', tax_id='20100066613', legal_name='OTRA EXT SAC')
        other_staff, _ = _p2d_member(other, 'nsub_ext_staff', ['company.view', 'sales.fiscal.view', 'sales.fiscal.issue'])
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register(order=self.order(company=other), user=other_staff).status_code, 201)

    def test_voiding_needs_a_reason_and_frees_the_sale_and_the_number(self):
        from store.models import AdminAuditLog

        row = self.register().json()['registrations'][0]
        void_url = f'{self.url}{row["id"]}/void/'
        short = self.api().post(void_url, {'reason': 'error'}, format='json')
        self.assertEqual(short.status_code, 400)
        response = self.api().post(void_url, {'reason': 'Número mal escrito, era el 71'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['coverage']['state'], 'pending')
        self.assertEqual(response.json()['registrations'][0]['status'], 'void')
        self.assertTrue(AdminAuditLog.objects.filter(action='external_receipt_voided').exists())
        again = self.api().post(void_url, {'reason': 'Otra vez el mismo registro'}, format='json')
        self.assertEqual(again.status_code, 400)
        # The voided number can be registered again, on the right sale.
        self.assertEqual(self.register().status_code, 201)

    def test_validation(self):
        cases = {
            'document_type': ({'document_type': '07'}, 'factura (01) o boleta (03)'),
            'series': ({'series': 'E-01'}, 'cuatro letras'),
            'factura with B': ({'document_type': '01', 'series': 'B001'}, 'no lleva una serie'),
            'boleta with F': ({'series': 'F001'}, 'no lleva una serie'),
            'number': ({'number': '0'}, 'entero de 1 a 99999999'),
            'future': ({'issue_date': '2999-01-01'}, 'no puede ser futura'),
            'date': ({'issue_date': 'ayer'}, 'fecha de emisión'),
            'channel': ({'channel': 'whatsapp'}, 'medio'),
            'reference': ({'reference': ' '}, 'referencia verificable'),
        }
        for name, (over, words) in cases.items():
            with self.subTest(name):
                response = self.register(**over)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(words, response.json()['detail'])

    def test_only_a_sale_that_owes_a_receipt(self):
        for order in (self.order(paid=False, status=Order.Status.PENDING_PAYMENT),
                      self.order(status=Order.Status.CANCELLED)):
            with self.subTest(status=order.status):
                response = self.register(order=order)
                self.assertEqual(response.status_code, 400)
                self.assertIn('venta pagada y vigente', response.json()['detail'])

    @override_settings(**FISCAL_ON)
    def test_a_number_the_system_issued_is_refused(self):
        document, _ = get_or_create_fiscal_document(self.order(receipt=Order.ReceiptType.FACTURA))
        response = self.register(document_type=document.document_type, series=document.series,
                                 number=str(document.number))
        self.assertEqual(response.status_code, 400)
        self.assertIn('emitido por el sistema', response.json()['detail'])

    @override_settings(**FISCAL_ON)
    def test_a_sale_with_a_settled_system_receipt_takes_no_external_one(self):
        order = self.order(receipt=Order.ReceiptType.FACTURA)
        document = self.accepted_document(order)
        FiscalDocument.objects.filter(pk=document.pk).update(environment=FiscalEnvironment.PRODUCTION)
        response = self.register(order=order, document_type='01', series='E001')
        self.assertEqual(response.status_code, 400)
        self.assertIn('ya tiene un comprobante electrónico aceptado', response.json()['detail'])

    def test_permissions(self):
        viewer, _ = _p2d_member(self.company, 'nsub_viewer', ['company.view', 'sales.fiscal.view'])
        nobody, _ = _p2d_member(self.company, 'nsub_nobody', ['company.view', 'sales.orders.view'])
        self.assertEqual(self.api(viewer).get(self.url).status_code, 200)
        self.assertFalse(self.api(viewer).get(self.url).json()['can_register'])
        self.assertEqual(self.register(user=viewer).status_code, 403)
        self.assertEqual(self.api(nobody).get(self.url).status_code, 403)
        row = self.register().json()['registrations'][0]
        voided = self.api(viewer).post(f'{self.url}{row["id"]}/void/', {'reason': 'No corresponde a esta venta'},
                                       format='json')
        self.assertEqual(voided.status_code, 403)

    def test_tenant_and_branch_isolation(self):
        from store.models import ExternalFiscalReceipt
        from store.tests import _saas_branch

        other = _p3_company('nsub-iso', 'Otra ISO', tax_id='20100066614', legal_name='OTRA ISO SAC')
        foreign = self.order(company=other)
        self.assertEqual(self.api().get(f'/api/admin/orders/{foreign.pk}/external-receipt/').status_code, 404)
        self.assertEqual(self.register(order=foreign).status_code, 404)
        north = _saas_branch(self.company, 'Sucursal Norte NSUB')
        clerk, _ = _p2d_member(self.company, 'nsub_clerk', ['company.view', 'sales.fiscal.view', 'sales.fiscal.issue'],
                               branches=[self.branch])
        far = self.order(branch=north)
        self.assertEqual(self.register(order=far, user=clerk).status_code, 404)
        # A registration id of another sale is not reachable through this one.
        row = self.register().json()['registrations'][0]
        other_sale = self.order()
        response = self.api().post(f'/api/admin/orders/{other_sale.pk}/external-receipt/{row["id"]}/void/',
                                   {'reason': 'Intento desde otro pedido'}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(ExternalFiscalReceipt.objects.get(pk=row['id']).status, 'registered')

    def test_issued_after_the_day_of_payment_is_said(self):
        Order.objects.filter(pk=self.order_.pk).update(paid_at=timezone.now() - timezone.timedelta(days=3))
        row = self.register().json()['registrations'][0]
        self.assertTrue(row['issued_after_payment'])


class ExternalReceiptInTheAccountingCentreTest(CoverageBase):
    def test_a_registered_external_receipt_replaces_the_missing_document_codes(self):
        from store.accounting import operations, pending as view_c
        from store.external_receipts import register

        order = self.order()
        get_or_create_sales_note(order, actor=self.staff)
        facts = operations.analyse(operations.load_orders([order.pk])[0])
        self.assertIn('INTERNAL_ONLY', facts.flags)
        self.assertIn('REQUESTED_NOT_ISSUED', facts.flags)

        register(order, {'document_type': '03', 'series': 'EB01', 'number': '3',
                         'issue_date': timezone.localdate().isoformat(), 'channel': 'sunat_sol',
                         'reference': 'SOL'}, actor=self.staff)
        facts = operations.analyse(operations.load_orders([order.pk])[0])
        self.assertIn('EXTERNAL_RECEIPT', facts.flags)
        for code in ('NO_DOCUMENT', 'INTERNAL_ONLY', 'REQUESTED_NOT_ISSUED'):
            self.assertNotIn(code, facts.flags)
        self.assertEqual(view_c.RULES['EXTERNAL_RECEIPT'][0], view_c.INFO)
        self.assertNotIn('aceptad', view_c.RULES['EXTERNAL_RECEIPT'][1].replace('aceptación', ''))
