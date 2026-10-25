"""
ACCOUNTING-02 · vista A — operaciones completas (`docs/adr-accounting.md` §2, §4.1).

    GET /api/admin/accounting/operations/
    GET /api/admin/accounting/summary/

LA REGLA QUE ESTAS PRUEBAS GUARDAN: una operación económica se cuenta UNA vez.
Su clave es `sale:<order.id>` o `repair:<repair_order.id>`; el importe sale sólo
de `Order` (o de los cobros del servicio), y la nota interna, el comprobante y
el intento de pago aportan estado, nunca dinero. Los reembolsos y los cobros
tardíos son ajustes de la MISMA clave, en su propia fecha.

Y que los totales cuadran con `sales_analytics_views._collections`, el cálculo
del tablero de ventas: dos sumas del mismo dinero que se separan son un
descuadre, no dos opiniones.

Fechas fijas en Lima (zona de la empresa): septiembre y octubre de 2026.
"""
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from store.models import (
    Device, FiscalDailySummary, FiscalDailySummaryDocument, FiscalDocument, FiscalDocumentStatus,
    FiscalSeries, FiscalSummaryStatus, Order, OrderCancellation, OrderRefund, OrderReturn,
    PaymentTransaction, RepairOrder, RepairPayment, SalesChannel, SalesNote,
)
from store.tax_services import breakdown_from_total
from store.tests import _p2d_branch, _p2d_member, _p3_company, _p4_customer

D = Decimal
LIMA = ZoneInfo('America/Lima')
OPS = '/api/admin/accounting/operations/'
SUMMARY = '/api/admin/accounting/summary/'
FISCAL = '/api/admin/accounting/fiscal-documents/'
PENDING = '/api/admin/accounting/pending/'

VIEW = ['company.view', 'accounting.view']
EXPORT = VIEW + ['accounting.export']


def at(day, hour=12, minute=0, month=9):
    return datetime(2026, month, day, hour, minute, tzinfo=LIMA)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class AccountingFixture(TestCase):
    """A company with two branches, a viewer, and builders for every source."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('acct-a', 'Tienda Contable')
        self.other = _p3_company('acct-b', 'Otra Tienda')
        self.centro = self.company.default_inventory_branch
        self.norte = _p2d_branch(self.company, 'Norte')
        self.viewer, _ = _p2d_member(self.company, 'acct_viewer', VIEW)
        self.exporter, _ = _p2d_member(self.company, 'acct_exporter', EXPORT)
        self._numbers = {}

    # -- builders ----------------------------------------------------------

    def sale(self, total='118.00', *, branch=None, paid_at=None, pay=True, channel=SalesChannel.ONLINE,
             receipt_type='boleta', company=None, status=None, frozen=True, seller=None, **extra):
        company = company or self.company
        tax = breakdown_from_total(total=D(total), subtotal=D(total))
        fields = dict(
            company=company, fulfillment_branch=branch if branch is not None else (
                company.default_inventory_branch),
            customer_name='Ana Compradora', customer_email='ana@example.com',
            document_type='dni', document_number='45678912',
            total=D(total), sales_channel=channel, receipt_type=receipt_type,
            status=Order.Status.PENDING_PAYMENT, sold_by=seller,
            seller_name_snapshot=getattr(seller, 'username', ''),
        )
        if frozen:
            fields.update(subtotal_amount=tax.subtotal, taxable_amount=tax.taxable_amount,
                          tax_amount=tax.tax_amount, tax_rate=tax.tax_rate)
        if channel == SalesChannel.POS:
            fields['payment_method'] = 'cash'
        fields.update(extra)
        order = Order.objects.create(**fields)
        if pay:
            Order.objects.filter(pk=order.pk).update(
                status=status or Order.Status.PAID, paid=True, paid_at=paid_at or at(15))
        elif status:
            Order.objects.filter(pk=order.pk).update(status=status)
        order.refresh_from_db()
        return order

    def payment(self, order, *, environment='production', status=PaymentTransaction.Status.AUTHORIZED,
                amount=None, confirmed_at=None):
        n = PaymentTransaction.objects.count() + 1
        return PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id=f'tx-{order.pk}-{n}', order_number=f'ON-{order.pk}-{n}',
            amount=amount if amount is not None else order.total, currency='PEN', status=status,
            environment=environment, confirmed_at=confirmed_at or order.paid_at,
        )

    def series(self, company, document_type, environment='production', *, letter=None):
        letter = letter or ('F' if document_type == '01' else 'B')
        code = f'{letter}001' if document_type in ('01', '03') else f'{letter}C01'
        series, _ = FiscalSeries.objects.get_or_create(
            company=company, document_type=document_type, series=code, environment=environment)
        return series

    def fiscal(self, order, document_type='03', *, status=FiscalDocumentStatus.SIGNED,
               environment='production', original=None, total=None, issued_at=None, cdr=''):
        letter = ('F' if original.document_type == '01' else 'B') if original is not None else None
        series = self.series(order.company, document_type, environment, letter=letter)
        key = (series.pk,)
        self._numbers[key] = self._numbers.get(key, 0) + 1
        amount = D(total) if total is not None else order.total
        tax = breakdown_from_total(total=amount, subtotal=amount)
        signed = '<Invoice>firmado</Invoice>'
        import hashlib
        return FiscalDocument.objects.create(
            order=order, company=order.company, series_ref=series, document_type=document_type,
            series=series.series, number=self._numbers[key], issued_at=issued_at or order.paid_at or at(15),
            environment=environment, original_document=original,
            issuer_tax_id='20100066603', issuer_legal_name='Tienda Contable SAC',
            customer_doc_type='1', customer_doc_number='45678912', customer_legal_name='Ana Compradora',
            currency='PEN', taxable_amount=tax.taxable_amount, tax_amount=tax.tax_amount, total=amount,
            tax_rate=tax.tax_rate, status=status,
            signed_xml=signed, signed_xml_sha256=hashlib.sha256(signed.encode()).hexdigest(),
            cdr_xml=cdr, cdr_sha256=hashlib.sha256(cdr.encode()).hexdigest() if cdr else '',
        )

    def summary(self, boleta, status=FiscalSummaryStatus.ACCEPTED, *, superseded=False):
        n = FiscalDailySummary.objects.filter(company=boleta.company).count() + 1
        rc = FiscalDailySummary.objects.create(
            company=boleta.company, identifier=f'RC-20260915-{n}', correlativo=n,
            reference_date=date(2026, 9, 15), issue_date=date(2026, 9, 15),
            environment=boleta.environment, status=status,
        )
        FiscalDailySummaryDocument.objects.create(summary=rc, document=boleta, line_id=1, superseded=superseded)
        return rc

    def note(self, order, *, void=False):
        from store.sales_note_services import get_or_create_sales_note

        note, _ = get_or_create_sales_note(order)
        if void:
            SalesNote.objects.filter(pk=note.pk).update(status=SalesNote.STATUS_VOID, voided_at=at(20))
        return note

    def cancel(self, order, *, fiscal_pending=False, when=None):
        cancellation = OrderCancellation.objects.create(
            order=order, company=order.company, reason='Cliente desistió',
            previous_status=order.status, previous_fulfillment_status=order.fulfillment_status,
            fiscal_pending=fiscal_pending, was_paid=order.paid,
        )
        OrderCancellation.objects.filter(pk=cancellation.pk).update(created_at=when or at(18))
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)
        order.refresh_from_db()
        return cancellation

    def refund(self, order, amount, *, confirmed_at=None, created_at=None):
        refund = OrderRefund.objects.create(
            company=order.company, order=order, amount=D(amount), method=OrderRefund.METHOD_CASH,
            status=OrderRefund.STATUS_CONFIRMED if confirmed_at else OrderRefund.STATUS_PENDING,
            reference='OP-1' if confirmed_at else '', confirmed_at=confirmed_at,
        )
        OrderRefund.objects.filter(pk=refund.pk).update(created_at=created_at or confirmed_at or at(18))
        return refund

    def closed_return(self, order, *, deduction='0.00', confirmed_at=None):
        cancellation = self.cancel(order)
        paid = order.total
        ret = OrderReturn.objects.create(
            company=order.company, order=order, status=OrderReturn.STATUS_CLOSED,
            kind=OrderReturn.KIND_WITHDRAWAL, reason='Desistimiento del cliente',
            received_at=at(17), receiving_branch=order.fulfillment_branch, closed_at=at(18),
            cancellation=cancellation, paid_amount=paid, deduction_amount=D(deduction),
            refund_amount=paid - D(deduction), deduction_reason='Uso' if D(deduction) else '',
        )
        self.refund(order, paid - D(deduction), confirmed_at=confirmed_at)
        return ret

    def repair(self, *, branch=None, company=None):
        company = company or self.company
        branch = branch or company.default_inventory_branch
        customer = _p4_customer(company)
        device = Device.objects.create(company=company, customer=customer, brand='Marca', model='Modelo')
        n = RepairOrder.objects.filter(company=company).count() + 1
        return RepairOrder.objects.create(
            company=company, branch=branch, customer=customer, device=device,
            number=f'OS-{n:05d}', sequence_value=n, reported_issue='No enciende',
        )

    def repair_payment(self, repair, amount, *, received_at=None, reversed_at=None):
        payment = RepairPayment.objects.create(
            company=repair.company, repair_order=repair, amount=D(amount), currency='PEN',
            method='cash', received_at=received_at or at(16),
        )
        if reversed_at:
            RepairPayment.objects.filter(pk=payment.pk).update(
                reversed_at=reversed_at, reversal_reason='Error de registro')
        return payment

    # -- reading -----------------------------------------------------------

    def get(self, url=OPS, user=None, **query):
        cache.clear()
        query.setdefault('tax_period', '202609')
        return client_for(user or self.viewer).get(url, query)

    def body(self, url=OPS, user=None, **query):
        response = self.get(url, user, **query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def rows(self, url=OPS, user=None, **query):
        return self.body(url, user, **query)['results']

    def keys(self, url=OPS, user=None, **query):
        return [row['key'] for row in self.rows(url, user, **query)]


class DeduplicationTest(AccountingFixture):
    """ADR §2 · una operación, una vez, por mucho papel que tenga."""

    def test_a_sale_with_a_note_a_receipt_and_a_payment_is_counted_once(self):
        order = self.sale('118.00')
        self.payment(order)
        self.note(order)
        self.fiscal(order, '03')

        body = self.body()
        operations = [r for r in body['results'] if r['row_type'] == 'operation']
        self.assertEqual([r['key'] for r in operations], [f'sale:{order.pk}'])
        self.assertEqual(operations[0]['gross'], '118.00')
        self.assertEqual(body['totals']['sales_gross'], '118.00')
        self.assertEqual(body['totals']['operations'], 1)
        kinds = sorted(d['kind'] for d in operations[0]['documents'])
        self.assertEqual(kinds, ['boleta', 'nota_interna'])

    def test_the_breakdown_is_the_frozen_one(self):
        order = self.sale('118.00')
        row = self.rows()[0]
        self.assertEqual((row['taxable'], row['tax'], row['tax_rate']), ('100.00', '18.00', '0.1800'))
        self.assertEqual(row['breakdown_source'], 'frozen')
        self.assertEqual(row['key'], f'sale:{order.pk}')

    def test_a_sale_without_a_snapshot_is_marked_rebuilt(self):
        self.sale('59.00', frozen=False)
        row = self.rows()[0]
        self.assertEqual(row['breakdown_source'], 'rebuilt')
        self.assertIn('BREAKDOWN_REBUILT', row['flags'])

    def test_a_rejected_receipt_and_its_reissue_are_one_operation(self):
        order = self.sale('118.00')
        self.fiscal(order, '03', status=FiscalDocumentStatus.REJECTED)
        self.fiscal(order, '03')
        rows = self.rows()
        self.assertEqual([r['key'] for r in rows], [f'sale:{order.pk}'])
        self.assertEqual(len(rows[0]['documents']), 2)

    def test_retried_payment_attempts_add_nothing(self):
        order = self.sale('118.00')
        self.payment(order, status=PaymentTransaction.Status.REJECTED)
        self.payment(order, status=PaymentTransaction.Status.ABANDONED)
        self.payment(order)
        self.assertEqual(self.body()['totals']['gross'], '118.00')

    def test_unpaid_failed_and_expired_orders_are_not_income(self):
        self.sale(pay=False)
        self.sale(pay=False, status=Order.Status.FAILED)
        self.sale(pay=False, status=Order.Status.EXPIRED)
        body = self.body()
        self.assertEqual(body['results'], [])
        self.assertEqual(body['totals']['gross'], '0.00')

    def test_a_sale_and_a_repair_never_share_a_key(self):
        order = self.sale('118.00')
        repair = self.repair()
        self.repair_payment(repair, '50.00')
        self.assertEqual(sorted(self.keys()), sorted([f'sale:{order.pk}', f'repair:{repair.pk}']))


class AdjustmentsTest(AccountingFixture):
    """Anular, reembolsar y devolver: ajustes de la misma clave, en su fecha."""

    def test_a_paid_then_cancelled_order_is_one_row_marked_cancelled(self):
        order = self.sale('118.00')
        self.cancel(order)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['commercial_state'], 'anulada')
        self.assertEqual(self.body()['totals']['sales_gross'], '118.00')

    def test_a_confirmed_refund_is_an_adjustment_on_its_own_date(self):
        order = self.sale('118.00', paid_at=at(28))
        self.cancel(order)
        self.refund(order, '118.00', confirmed_at=at(3, month=10))

        september = self.body()
        self.assertEqual(september['totals']['refunds_confirmed'], '0.00')
        self.assertEqual(september['totals']['net'], '118.00')

        october = self.body(tax_period='202610')
        self.assertEqual([r['row_type'] for r in october['results']], ['adjustment'])
        self.assertEqual(october['results'][0]['key'], f'sale:{order.pk}')
        self.assertEqual(october['results'][0]['amount'], '-118.00')
        self.assertEqual(october['totals']['operations'], 0)
        self.assertEqual(october['totals']['refunds_confirmed'], '118.00')
        self.assertEqual(october['totals']['net'], '-118.00')

    def test_a_pending_refund_is_said_but_not_subtracted(self):
        order = self.sale('118.00')
        self.cancel(order)
        self.refund(order, '50.00', created_at=at(19))
        totals = self.body()['totals']
        self.assertEqual(totals['refunds_pending'], '50.00')
        self.assertEqual(totals['net'], '118.00')

    def test_a_closed_return_with_a_deduction(self):
        order = self.sale('118.00', paid_at=at(5))
        self.closed_return(order, deduction='18.00', confirmed_at=at(20))
        body = self.body()
        operations = [r for r in body['results'] if r['row_type'] == 'operation']
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]['commercial_state'], 'devuelta')
        self.assertEqual(operations[0]['kept'], '18.00')
        self.assertEqual(body['totals']['gross'], '118.00')
        self.assertEqual(body['totals']['refunds_confirmed'], '100.00')
        self.assertEqual(body['totals']['net'], '18.00')

    def test_a_late_duplicate_charge_is_an_adjustment_of_the_same_key(self):
        order = self.sale('118.00')
        self.payment(order)
        self.cancel(order)
        self.payment(order, status=PaymentTransaction.Status.DUPLICATE, confirmed_at=at(19))
        body = self.body()
        adjustments = [r for r in body['results'] if r['row_type'] == 'adjustment']
        self.assertEqual([(r['event'], r['key'], r['amount']) for r in adjustments],
                         [('late_charge', f'sale:{order.pk}', '118.00')])
        self.assertEqual(body['totals']['late_charges'], '118.00')
        self.assertEqual(body['totals']['gross'], '236.00')


class ServiceRevenueTest(AccountingFixture):
    """
    P1 provisional: el servicio cuenta por cobro, en la fecha del cobro.

    Un reverso NO reescribe el período del cobro: es una fila negativa propia
    (`service_reversal`) en la fecha del reverso. Así un mes ya revisado no
    cambia porque alguien reverse un pago meses después.
    """

    def test_deposit_and_balance_and_a_reversal_in_the_same_month(self):
        repair = self.repair()
        self.repair_payment(repair, '100.00', received_at=at(10))
        self.repair_payment(repair, '50.00', received_at=at(12))
        self.repair_payment(repair, '30.00', received_at=at(13), reversed_at=at(14))
        body = self.body()
        rows = [(r['key'], r['row_type'], r['event'], r['amount']) for r in body['results']]
        self.assertEqual(rows, [
            (f'repair:{repair.pk}', 'operation', 'service_collection', '180.00'),
            (f'repair:{repair.pk}', 'adjustment', 'service_reversal', '-30.00'),
        ])
        operation = body['results'][0]
        self.assertEqual((operation['channel'], operation['payments']), ('service', 3))
        self.assertIn('SERVICE_NO_DOCUMENT', operation['flags'])
        self.assertEqual(body['totals']['service_collections'], '180.00')
        self.assertEqual(body['totals']['service_reversals'], '30.00')
        self.assertEqual(body['totals']['income'], '150.00')

    def test_a_reversal_in_a_later_month_does_not_rewrite_the_past(self):
        repair = self.repair()
        self.repair_payment(repair, '100.00', received_at=at(20), reversed_at=at(2, month=10))
        september = self.body()
        self.assertEqual(september['totals']['income'], '100.00')
        self.assertEqual([r['row_type'] for r in september['results']], ['operation'])
        october = self.body(tax_period='202610')
        self.assertEqual([(r['event'], r['amount']) for r in october['results']], [('service_reversal', '-100.00')])
        self.assertEqual(october['totals']['service_collections'], '0.00')
        self.assertEqual(october['totals']['income'], '-100.00')

    def test_a_payment_of_another_month_stays_there(self):
        repair = self.repair()
        self.repair_payment(repair, '100.00', received_at=at(2, month=10))
        self.assertEqual(self.body()['results'], [])

    def test_the_policy_is_stated_as_provisional(self):
        policy = self.body()['policy']
        self.assertEqual(policy['status'], 'provisional')
        self.assertEqual(policy['service_revenue_basis'], 'collection')
        self.assertEqual(policy['sale_period_field'], 'paid_at')
        self.assertEqual(policy['pending_owner_decision'], ['P1', 'P2', 'P3', 'P4'])


class TestPaymentsTest(AccountingFixture):
    """P4 provisional: un pago sólo con claves TEST queda fuera del ingreso y aparte."""

    def test_an_order_paid_only_with_test_keys_is_excluded_and_listed_apart(self):
        real = self.sale('118.00')
        self.payment(real)
        test = self.sale('59.00')
        self.payment(test, environment='test')
        body = self.body()
        excluded = [r for r in body['results'] if r['excluded_from_income']]
        self.assertEqual([r['key'] for r in excluded], [f'sale:{test.pk}'])
        self.assertIn('TEST_PAYMENT', excluded[0]['flags'])
        self.assertEqual(body['totals']['gross'], '118.00')
        self.assertEqual(body['totals']['excluded_test'], {
            'operations': 1, 'gross': '59.00', 'refunds_confirmed': '0.00'})

    def test_an_excluded_order_does_not_ask_for_a_receipt(self):
        test = self.sale('59.00', receipt_type='boleta')
        self.payment(test, environment='test')
        flags = self.rows()[0]['flags']
        self.assertIn('TEST_PAYMENT', flags)
        self.assertNotIn('NO_DOCUMENT', flags)
        self.assertNotIn('REQUESTED_NOT_ISSUED', flags)

    def test_a_production_payment_wins_over_an_earlier_test_one(self):
        order = self.sale('118.00')
        self.payment(order, environment='test')
        self.payment(order)
        row = self.rows()[0]
        self.assertFalse(row['excluded_from_income'])
        self.assertNotIn('TEST_PAYMENT', row['flags'])

    def test_an_unclassifiable_payment_counts_and_is_flagged(self):
        order = self.sale('118.00')
        self.payment(order, environment='')
        row = self.rows()[0]
        self.assertFalse(row['excluded_from_income'])
        self.assertIn('PAYMENT_ENV_UNKNOWN', row['flags'])

    def test_a_till_sale_has_no_gateway_environment_to_doubt(self):
        self.sale('118.00', channel=SalesChannel.POS, receipt_type='')
        row = self.rows()[0]
        self.assertNotIn('PAYMENT_ENV_UNKNOWN', row['flags'])
        self.assertFalse(row['excluded_from_income'])

    def test_a_refund_of_an_excluded_order_is_excluded_too(self):
        order = self.sale('59.00')
        self.payment(order, environment='test')
        self.cancel(order)
        self.refund(order, '59.00', confirmed_at=at(20))
        totals = self.body()['totals']
        self.assertEqual(totals['refunds_confirmed'], '0.00')
        self.assertEqual(totals['excluded_test']['refunds_confirmed'], '59.00')


class CollectionsParityTest(AccountingFixture):
    """ADR T-01: los totales son los del tablero de ventas, salvo lo excluido por P4."""

    def collections(self, since, until):
        from store.sales_analytics_views import _collections, _company_tz

        branches = [self.centro, self.norte]
        return _collections(self.company, branches, since, until, _company_tz(self.company))

    def test_the_totals_match_collections(self):
        a = self.sale('118.00', paid_at=at(3))
        self.payment(a)
        self.note(a)
        b = self.sale('236.00', branch=self.norte, channel=SalesChannel.POS, receipt_type='', paid_at=at(9))
        self.note(b)
        c = self.sale('59.00', paid_at=at(12))
        self.payment(c)
        self.fiscal(c, '03')
        self.cancel(c)
        self.refund(c, '59.00', confirmed_at=at(14))
        d = self.sale('177.00', paid_at=at(20))
        self.payment(d)
        self.closed_return(d, deduction='27.00', confirmed_at=at(25))
        e = self.sale('300.00', paid_at=at(21))
        self.cancel(e)
        self.refund(e, '100.00', created_at=at(22))
        self.payment(e, status=PaymentTransaction.Status.DUPLICATE, amount=D('300.00'), confirmed_at=at(23))
        self.sale(pay=False)

        totals = self.body()['totals']
        expected = self.collections(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(totals['sales_gross'], f"{D(expected['live']['amount']) + D(expected['cancelled_paid']['amount']):.2f}")
        self.assertEqual(totals['late_charges'], expected['late_charges']['amount'])
        self.assertEqual(totals['gross'], expected['gross'])
        self.assertEqual(totals['refunds_confirmed'], expected['refunds_confirmed'])
        self.assertEqual(totals['refunds_pending'], expected['refunds_pending'])
        self.assertEqual(totals['net'], expected['net'])

    def test_refunded_status_and_unbranched_orders_are_the_stated_difference(self):
        """
        The dashboard leaves out an order in the legacy status REFUNDED and an
        order without a branch; a company-wide accounting view counts both, and
        flags them. The difference is exactly those two, nothing else.
        """
        kept = self.sale('118.00')
        self.payment(kept)
        refunded = self.sale('59.00', status=Order.Status.REFUNDED)
        self.payment(refunded)
        unbranched = self.sale('20.00')
        self.payment(unbranched)
        Order.objects.filter(pk=unbranched.pk).update(fulfillment_branch=None)
        body = self.body()
        expected = self.collections(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(D(body['totals']['gross']), D(expected['gross']) + D('59.00') + D('20.00'))
        flags = {r['key']: r['flags'] for r in body['results']}
        self.assertIn('REFUNDED_STATUS', flags[f'sale:{refunded.pk}'])
        self.assertIn('NO_BRANCH', flags[f'sale:{unbranched.pk}'])
        self.assertNotIn('REFUNDED_STATUS', flags[f'sale:{kept.pk}'])
        by_branch = self.body(branch=self.centro.pk)['totals']['gross']
        self.assertEqual(D(by_branch), D(expected['gross']) + D('59.00'))

    def test_with_test_payments_the_difference_is_exactly_the_excluded_part(self):
        real = self.sale('118.00')
        self.payment(real)
        test = self.sale('59.00')
        self.payment(test, environment='test')
        totals = self.body()['totals']
        expected = self.collections(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(D(totals['gross']) + D(totals['excluded_test']['gross']), D(expected['gross']))


class PeriodTest(AccountingFixture):
    """Día, semana, mes, año, rango y período tributario, en la hora de Lima."""

    def test_a_sale_at_half_past_eleven_at_night_belongs_to_its_local_day(self):
        late = self.sale('118.00', paid_at=at(30, 23, 30))
        self.assertEqual(self.keys(tax_period='202609'), [f'sale:{late.pk}'])
        self.assertEqual(self.keys(tax_period='202610'), [])
        self.assertEqual(self.keys(tax_period='', period='day', date='2026-09-30'), [f'sale:{late.pk}'])
        self.assertEqual(self.keys(tax_period='', period='day', date='2026-10-01'), [])

    def test_week_month_year_and_range(self):
        monday = self.sale('10.00', paid_at=at(14))    # lunes 14
        sunday = self.sale('20.00', paid_at=at(20))    # domingo 20
        nextweek = self.sale('30.00', paid_at=at(21))
        week = self.keys(tax_period='', period='week', date='2026-09-16')
        self.assertEqual(sorted(week), sorted([f'sale:{monday.pk}', f'sale:{sunday.pk}']))
        self.assertEqual(len(self.keys(tax_period='', period='month', date='2026-09-02')), 3)
        # A year exceeds what view A assembles (92 days, ADR §9); view B answers it.
        self.assertEqual(self.get(tax_period='', period='year', date='2026-01-01').status_code, 400)
        year = self.body(FISCAL, tax_period='', period='year', date='2026-06-01')['period']
        self.assertEqual((year['from'], year['to']), ('2026-01-01', '2026-12-31'))
        ranged = self.keys(tax_period='', period='range', **{'from': '2026-09-20', 'to': '2026-09-21'})
        self.assertEqual(sorted(ranged), sorted([f'sale:{sunday.pk}', f'sale:{nextweek.pk}']))

    def test_the_period_is_described(self):
        period = self.body(tax_period='202609')['period']
        self.assertEqual((period['kind'], period['from'], period['to'], period['timezone']),
                         ('tax_period', '2026-09-01', '2026-09-30', 'America/Lima'))

    def test_bad_periods_are_refused(self):
        for query in (
            {'tax_period': '202613'}, {'tax_period': 'abc'},
            {'tax_period': '', 'period': 'decade'},
            {'tax_period': '', 'period': 'range', 'from': '2026-10-02', 'to': '2026-10-01'},
            {'tax_period': '', 'period': 'range', 'from': '2025-01-01', 'to': '2026-03-01'},
            {'tax_period': '', 'period': 'day', 'date': '2026-02-30'},
            {'tax_period': '000001'}, {'tax_period': '999912'},
            {'tax_period': '', 'period': 'year', 'date': '9999-12-31'},
            {'tax_period': '', 'period': 'week', 'date': '9999-12-31'},
            {'tax_period': '', 'period': 'day', 'date': '0001-01-01'},
        ):
            self.assertEqual(self.get(**query).status_code, 400, query)


class PeriodCapTest(AccountingFixture):
    """
    ADR §9 · hasta que las vistas sumen en la base, A, C y el resumen se arman en
    memoria: como mucho 92 días, y un tope de filas que responde 400 en vez de
    agotar el único proceso del servidor. B pagina documentos: hasta 366 días,
    con su propio tope.
    """

    LONG = {'tax_period': '', 'period': 'range', 'from': '2026-01-01', 'to': '2026-04-30'}   # 120 días

    def test_ledger_views_refuse_more_than_92_days(self):
        for url in (OPS, PENDING, SUMMARY):
            response = self.get(url, **self.LONG)
            self.assertEqual(response.status_code, 400, url)
            self.assertIn('92', response.json()['detail'], url)
        ok = {'tax_period': '', 'period': 'range', 'from': '2026-07-01', 'to': '2026-09-30'}     # 92 días
        self.assertEqual(self.get(OPS, **ok).status_code, 200)

    def test_the_fiscal_view_admits_a_longer_period(self):
        self.assertEqual(self.get(FISCAL, **self.LONG).status_code, 200)
        self.assertEqual(self.get(FISCAL, tax_period='', period='year', date='2026-05-01').status_code, 200)

    def test_too_many_rows_answer_400(self):
        from unittest import mock

        for _ in range(3):
            self.sale('10.00')
        with mock.patch('store.accounting.operations.MAX_LEDGER_ROWS', 2):
            for url in (OPS, PENDING, SUMMARY):
                response = self.get(url)
                self.assertEqual(response.status_code, 400, url)
                self.assertIn('estrecha', response.json()['detail'].lower(), url)
        with mock.patch('store.accounting.operations.MAX_LEDGER_ROWS', 3):
            self.assertEqual(self.get(OPS).status_code, 200)

    def test_too_many_documents_answer_400(self):
        from unittest import mock

        for _ in range(3):
            self.fiscal(self.sale('10.00'), '03')
        with mock.patch('store.accounting.fiscal.MAX_DOCUMENTS', 2):
            self.assertEqual(self.get(FISCAL).status_code, 400)


class CapAfterFiltersTest(AccountingFixture):
    """
    The caps count what the question asks for. Narrowing by channel, type,
    seller or document — what the 400 tells the person to do — must bring a
    period back under the cap; and no query materialises more than the cap + 1.
    """

    def test_narrowing_brings_the_fiscal_view_under_its_cap(self):
        from unittest import mock

        seller, _ = _p2d_member(self.company, 'acct_cap_cajero', ['company.view', 'sales.pos.use'])
        for _ in range(3):
            self.fiscal(self.sale('10.00'), '03')
        till = self.sale('20.00', channel=SalesChannel.POS, receipt_type='factura', seller=seller)
        self.fiscal(till, '01', status=FiscalDocumentStatus.ACCEPTED, cdr='<R/>')
        rejected = self.sale('30.00')
        self.fiscal(rejected, '03', status=FiscalDocumentStatus.REJECTED)
        with mock.patch('store.accounting.fiscal.MAX_DOCUMENTS', 2):
            self.assertEqual(self.get(FISCAL).status_code, 400)
            self.assertEqual([r['document_id'] for r in self.rows(FISCAL, channel='pos')], ['F001-1'])
            self.assertEqual(len(self.rows(FISCAL, seller=seller.pk)), 1)
            self.assertEqual(len(self.rows(FISCAL, doc='factura')), 1)
            self.assertEqual(len(self.rows(FISCAL, doc='rechazado')), 1)
            self.assertEqual(self.rows(FISCAL, kind='repair'), [])
            self.assertEqual(self.get(FISCAL, doc='boleta').status_code, 400)

    def test_every_ledger_query_stops_at_the_cap(self):
        import re

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        order = self.sale('10.00')
        self.cancel(order)
        self.refund(order, '5.00', confirmed_at=at(20))
        repair = self.repair()
        self.repair_payment(repair, '5.00', reversed_at=at(20))
        with CaptureQueriesContext(connection) as ctx:
            self.get(OPS)
        sources = ('store_orderrefund', 'store_paymenttransaction', 'store_repairpayment')
        checked = 0
        for query in ctx.captured_queries:
            sql = query['sql']
            head = sql.split(' WHERE ')[0]
            # The period-window reads (a date bound). The prefetches are bounded
            # by order ids that were already capped.
            windowed = re.search(r'_at" >= ', sql)
            if re.search(r'FROM "(%s)"' % '|'.join(sources), head) and windowed:
                self.assertIn('LIMIT', sql, sql[:160])
                checked += 1
        self.assertGreaterEqual(checked, 5)


class CompanyTimezoneTest(AccountingFixture):
    """Las fechas se escriben en la zona de la empresa, no en la del servidor."""

    def test_rows_are_dated_in_the_company_zone(self):
        settings = self.company.settings
        settings.timezone = 'Asia/Tokyo'
        settings.save()
        order = self.sale('118.00', paid_at=at(15, 12))     # 17:00 UTC = 02:00 del 16 en Tokio
        self.fiscal(order, '03')
        self.assertTrue(self.rows()[0]['date'].startswith('2026-09-16T02:00:00+09:00'), self.rows()[0]['date'])
        self.assertTrue(self.rows(FISCAL)[0]['issued_at'].startswith('2026-09-16T02:00:00+09:00'))
        self.assertEqual(self.body()['period']['timezone'], 'Asia/Tokyo')


class FilterTest(AccountingFixture):
    """Cada filtro estrecha; ninguno amplía lo que la persona puede ver."""

    def setUp(self):
        super().setUp()
        self.web_boleta = self.sale('118.00')
        self.fiscal(self.web_boleta, '03')
        self.web_factura = self.sale('236.00', receipt_type='factura')
        self.fiscal(self.web_factura, '01', status=FiscalDocumentStatus.ACCEPTED)
        self.seller, _ = _p2d_member(self.company, 'acct_cajero', ['company.view', 'sales.pos.use'])
        self.till_note = self.sale('59.00', branch=self.norte, channel=SalesChannel.POS, receipt_type='',
                                   seller=self.seller)
        self.note(self.till_note)
        self.till_bare = self.sale('20.00', branch=self.norte, channel=SalesChannel.POS, receipt_type='')
        self.repair_order = self.repair(branch=self.norte)
        self.repair_payment(self.repair_order, '40.00')

    def test_channel_and_kind(self):
        self.assertEqual(sorted(self.keys(channel='pos')), sorted([f'sale:{self.till_note.pk}', f'sale:{self.till_bare.pk}']))
        self.assertEqual(self.keys(channel='service'), [f'repair:{self.repair_order.pk}'])
        self.assertEqual(self.keys(kind='repair'), [f'repair:{self.repair_order.pk}'])
        self.assertEqual(len(self.keys(kind='sale')), 4)

    def test_branch(self):
        keys = self.keys(branch=self.norte.pk)
        self.assertEqual(sorted(keys), sorted([
            f'sale:{self.till_note.pk}', f'sale:{self.till_bare.pk}', f'repair:{self.repair_order.pk}']))

    def test_a_foreign_branch_narrows_to_nothing(self):
        self.assertEqual(self.keys(branch=self.other.default_inventory_branch.pk), [])

    def test_document(self):
        self.assertEqual(self.keys(doc='factura'), [f'sale:{self.web_factura.pk}'])
        self.assertEqual(self.keys(doc='boleta'), [f'sale:{self.web_boleta.pk}'])
        self.assertEqual(self.keys(doc='nota_interna'), [f'sale:{self.till_note.pk}'])
        self.assertEqual(sorted(self.keys(doc='sin_documento')),
                         sorted([f'sale:{self.till_bare.pk}', f'repair:{self.repair_order.pk}']))
        self.assertEqual(self.keys(doc='pendiente'), [f'sale:{self.web_boleta.pk}'])
        self.assertEqual(sorted(self.keys(doc='factura,boleta')),
                         sorted([f'sale:{self.web_factura.pk}', f'sale:{self.web_boleta.pk}']))

    def test_rejected_and_voided_documents(self):
        rejected = self.sale('10.00')
        self.fiscal(rejected, '03', status=FiscalDocumentStatus.REJECTED)
        voided = self.sale('11.00', channel=SalesChannel.POS, receipt_type='')
        self.note(voided, void=True)
        self.assertEqual(self.keys(doc='rechazado'), [f'sale:{rejected.pk}'])
        self.assertEqual(self.keys(doc='anulado'), [f'sale:{voided.pk}'])

    def test_seller(self):
        self.assertEqual(self.keys(seller=self.seller.pk), [f'sale:{self.till_note.pk}'])

    def test_a_seller_of_another_company_is_refused(self):
        stranger, _ = _p2d_member(self.other, 'acct_ajeno', ['company.view'])
        self.assertEqual(self.get(seller=stranger.pk).status_code, 400)

    def test_operation_state(self):
        self.cancel(self.web_boleta)
        self.assertEqual(self.keys(operation='anulada'), [f'sale:{self.web_boleta.pk}'])
        self.assertEqual(self.keys(operation='servicio'), [f'repair:{self.repair_order.pk}'])

    def test_unknown_filter_values_are_refused(self):
        for query in ({'channel': 'fax'}, {'kind': 'gift'}, {'doc': 'recibo'}, {'operation': 'x'},
                      {'branch': 'abc'}, {'seller': 'abc'}):
            self.assertEqual(self.get(**query).status_code, 400, query)

    def test_totals_follow_the_filters(self):
        self.assertEqual(self.body(channel='pos')['totals']['gross'], '79.00')

    def test_pagination_walks_every_row_once(self):
        seen, cursor = [], None
        while True:
            query = {'page_size': 2}
            if cursor:
                query['cursor'] = cursor
            body = self.body(**query)
            seen += [r['key'] for r in body['results']]
            cursor = body['next_cursor']
            if not cursor:
                break
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(self.body()['count'], 5)

    def test_a_bad_cursor_is_refused(self):
        self.assertEqual(self.get(cursor='nonsense').status_code, 400)


class MaskingTest(AccountingFixture):
    """El documento del comprador sale entero sólo con `accounting.export`."""

    def test_masked_without_export(self):
        self.sale('118.00')
        self.assertEqual(self.rows()[0]['customer']['document_number'], '****8912')

    def test_whole_with_export(self):
        self.sale('118.00')
        self.assertEqual(self.rows(user=self.exporter)[0]['customer']['document_number'], '45678912')


class NotesStaySeparateTest(AccountingFixture):
    """ADR T-05: una venta sólo con nota interna no es un documento fiscal."""

    def test_a_note_only_sale_is_in_a_and_c_never_in_b(self):
        order = self.sale('59.00', channel=SalesChannel.POS, receipt_type='')
        note = self.note(order)
        row = self.rows()[0]
        self.assertEqual(row['primary_document'], {'kind': 'nota_interna', 'number': note.number})
        self.assertIn('INTERNAL_ONLY', row['flags'])
        self.assertEqual(self.rows(FISCAL), [])
        codes = [(r['key'], r['code']) for r in self.rows(PENDING)]
        self.assertIn((f'sale:{order.pk}', 'INTERNAL_ONLY'), codes)


class QueryCountTest(AccountingFixture):
    """ADR T-17: las consultas no crecen con las filas."""

    def build(self, n):
        for i in range(n):
            order = self.sale('118.00', paid_at=at(1 + (i % 28)))
            self.payment(order)
            self.note(order)
            self.fiscal(order, '03')
            repair = self.repair()
            self.repair_payment(repair, '10.00')

    def count_queries(self, url=OPS):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        cache.clear()
        client = client_for(self.viewer)
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url, {'tax_period': '202609', 'page_size': 200})
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_one_and_twenty(self):
        urls = (OPS, FISCAL, PENDING, SUMMARY)
        self.build(1)
        few = {url: self.count_queries(url) for url in urls}
        self.build(19)
        self.assertEqual({url: self.count_queries(url) for url in urls}, few)
