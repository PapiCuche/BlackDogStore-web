"""
COMMERCE-ADMIN-UX-01 · financial closure — the owner's decisions of 2026-10-16.

  1. A till sale is annulled by whoever may cancel orders when the case is
     SIMPLE. A superior (`company.manage`; legacy: admin/superadmin) must do it
     when the sale is older than the company's `pos_cancel_simple_days`, has a
     live boleta/factura (never voided here), has a settled commission, or
     left stock from several branches. The refusal says which and who.
  2. Confirming a refund takes `company.manage`, verifiable evidence, the
     reference and the exact amount. Cancelling never confirms a refund.
  3. A cancelled sale's commission: voided while not settled; once settled,
     one compensating adjustment, never twice, the settled row untouched.
  4. Nothing here rewrites a sale's snapshots.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import order_cancellation as cancelling
from store import order_refunds as refunds
from store.models import (
    AdminAuditLog, CommissionAdjustment, CompanySettings, FiscalDocument, Order, OrderCancellation, OrderRefund,
    SalesCommission, StockMovement,
)
from store import test_order_cancel as cancel_tests
from store.test_order_cancel import REASON, RETURN
from store.test_order_cancel_dc import _CostShop
from store.test_order_cancel_ux import MANAGE, _UxBase
from store.tests import _m7_stock, _p2d_member


class _ClosureBase(_UxBase):
    def setUp(self):
        super().setUp()
        self.superior, self.superior_desk = self.admin_client('superior_cierre')

    def simple_days(self, days):
        CompanySettings.objects.update_or_create(company=self.company, defaults={'pos_cancel_simple_days': days})

    def aged(self, order, days):
        Order.objects.filter(pk=order.pk).update(
            paid_at=timezone.now() - timedelta(days=days), created_at=timezone.now() - timedelta(days=days))
        order.refresh_from_db()
        return order

    def commission(self, order, amount='1.96', status=SalesCommission.STATUS_ACCRUED, **extra):
        return SalesCommission.objects.create(
            company=self.company, order=order, seller=self.manager, seller_name_snapshot='Vendedora',
            rate_percent=D('2.00'), base_amount=D('98.00'), amount=D(amount), status=status, **extra)


# -- decision 1 · who annuls a till sale -------------------------------------------------

class PosSuperiorAuthorityTest(_ClosureBase):
    def assert_refused(self, sale, *codes, client=None):
        response = self.post_cancel(sale, goods_returned=True, client=client)
        self.assertEqual(response.status_code, 403, response.content)
        body = response.json()
        self.assertEqual(body['code'], 'superior_required')
        self.assertEqual([c['code'] for c in body['conditions']], list(codes))
        self.assertIn('superior', body['detail'])
        self.assertEqual(Order.objects.get(pk=sale.pk).status, Order.Status.PAID)
        self.assertFalse(StockMovement.objects.filter(order=sale, movement_type=RETURN).exists())
        self.assertFalse(OrderCancellation.objects.filter(order=sale).exists())
        return body

    def test_a_fresh_simple_till_sale_is_annulled_by_whoever_cancels_orders(self):
        sale = self.till_sale()
        self.assertEqual(self.detail(sale)['cancel_superior_conditions'], [])

        response = self.post_cancel(sale, goods_returned=True)

        self.assertEqual(response.status_code, 200, response.content)
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(sale.pk))
        self.assertEqual((audit.metadata['authority'], audit.metadata['superior_conditions']), ('simple', []))

    def test_an_old_sale_needs_a_superior_by_the_companys_own_window(self):
        sale = self.aged(self.till_sale(), 8)                    # default window: 7 days
        body = self.assert_refused(sale, 'older_than_window')
        self.assertIn('7 días', body['conditions'][0]['detail'])

        # The company widens its window: the same sale is simple again.
        self.simple_days(10)
        self.assertEqual(self.post_cancel(sale, goods_returned=True).status_code, 200)

    def test_the_window_is_counted_from_the_sale_and_is_inclusive(self):
        self.simple_days(3)
        inside = self.aged(self.till_sale(1), 2)
        self.assertEqual(self.detail(inside)['cancel_superior_conditions'], [])
        outside = self.aged(self.till_sale(1), 4)
        self.assertEqual([c['code'] for c in self.detail(outside)['cancel_superior_conditions']],
                         ['older_than_window'])

    def test_a_window_of_zero_days_sends_every_till_sale_to_a_superior(self):
        self.simple_days(0)
        sale = self.aged(self.till_sale(1), 0)
        Order.objects.filter(pk=sale.pk).update(paid_at=timezone.now() - timedelta(minutes=1))
        self.assert_refused(sale, 'older_than_window')

    def test_a_live_boleta_needs_a_superior_and_is_never_voided_here(self):
        sale = self.till_sale()
        document = cancel_tests.PapersTest.fiscal_document(self, sale)
        before = FiscalDocument.objects.filter(pk=document.pk).values().get()
        self.assert_refused(sale, 'live_fiscal_document')

        response = self.post_cancel(sale, client=self.superior_desk, goods_returned=True)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(FiscalDocument.objects.filter(pk=document.pk).values().get(), before)
        cancellation = OrderCancellation.objects.get(order=sale)
        self.assertTrue(cancellation.fiscal_pending)
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(sale.pk))
        self.assertEqual(audit.metadata['authority'], 'superior')
        self.assertEqual(audit.metadata['superior_conditions'], ['live_fiscal_document'])
        self.assertEqual(audit.actor, self.superior)

    def test_a_rejected_boleta_is_not_live(self):
        sale = self.till_sale()
        cancel_tests.PapersTest.fiscal_document(self, sale, status='rejected')
        self.assertEqual(self.post_cancel(sale, goods_returned=True).status_code, 200)

    def test_a_settled_commission_needs_a_superior_an_accrued_one_does_not(self):
        accrued = self.till_sale(1)
        self.commission(accrued)
        self.assertEqual(self.detail(accrued)['cancel_superior_conditions'], [])

        settled = self.till_sale(1)
        self.commission(settled, status=SalesCommission.STATUS_SETTLED, settled_at=timezone.now())
        self.assert_refused(settled, 'commission_settled')
        self.assertEqual(self.post_cancel(settled, client=self.superior_desk, goods_returned=True).status_code, 200)

    def test_stock_from_several_branches_needs_a_superior(self):
        sale = self.till_sale(1)
        _m7_stock(self.branch_b, self.cable, 5)
        inventory.create_stock_movement(
            branch=self.branch_b, product_id=self.cable.pk, movement_type=StockMovement.SALE_EXIT, quantity=1,
            reason='Venta', order=sale)
        self.assert_refused(sale, 'several_branches')

    def test_every_condition_is_said_at_once_in_a_fixed_order(self):
        sale = self.aged(self.till_sale(1), 30)
        cancel_tests.PapersTest.fiscal_document(self, sale)
        self.commission(sale, status=SalesCommission.STATUS_SETTLED, settled_at=timezone.now())
        self.assert_refused(sale, 'older_than_window', 'live_fiscal_document', 'commission_settled')

    def test_the_panel_says_a_superior_is_needed_to_whoever_is_not_one(self):
        sale = self.aged(self.till_sale(), 8)
        detail = self.detail(sale)
        self.assertFalse(detail['can_cancel'])
        self.assertEqual([b['code'] for b in detail['cancel_blockers']], ['superior_required'])
        self.assertIn('superior', detail['cancel_blockers'][0]['detail'])
        # The superior sees it open, with the conditions to warn about.
        as_superior = self.detail(sale, client=self.superior_desk)
        self.assertTrue(as_superior['can_cancel'])
        self.assertEqual(as_superior['cancel_blockers'], [])
        self.assertEqual([c['code'] for c in as_superior['cancel_superior_conditions']], ['older_than_window'])

    def test_company_manage_alone_is_not_enough(self):
        sale = self.aged(self.till_sale(), 8)
        only_company, _ = _p2d_member(self.company, 'solo_empresa', ['company.view', 'sales.orders.view',
                                                                    'company.manage'])
        client = APIClient()
        client.force_authenticate(only_company)
        self.assertEqual(self.post_cancel(sale, client=client, goods_returned=True).status_code, 403)
        self.assertEqual(Order.objects.get(pk=sale.pk).status, Order.Status.PAID)

    def test_web_orders_keep_their_rules(self):
        """The superior rule is the till's: a paid web order with a boleta is cancelled as before."""
        order = self.web_order()
        cancel_tests.PapersTest.fiscal_document(self, order)
        self.aged(order, 30)
        self.assertEqual(self.post_cancel(order).status_code, 200)

    def test_the_service_refuses_too(self):
        sale = self.aged(self.till_sale(), 8)
        with self.assertRaises(cancelling.OrderCancelError) as refused:
            cancelling.cancel_order(sale, self.manager, REASON, goods_returned=True)
        self.assertEqual((refused.exception.code, refused.exception.http_status), ('superior_required', 403))


class PosCancelWindowSettingTest(_ClosureBase):
    def patch(self, client, value):
        return client.patch(f'/api/admin/company-settings/?{self.q}', {'pos_cancel_simple_days': value},
                            format='json')

    def test_the_window_is_seven_days_bounded_and_changed_with_settings_manage(self):
        self.assertEqual(CompanySettings(company=self.company).pos_cancel_simple_days, 7)
        admin_only, admin_client = self.admin_client('admin_sin_ajustes')
        self.assertEqual(self.patch(admin_client, 3).status_code, 403)
        full, _ = _p2d_member(self.company, 'admin_ajustes', MANAGE + ['company.manage', 'settings.manage'])
        client = APIClient()
        client.force_authenticate(full)
        for bad in (-1, 366, 'x'):
            with self.subTest(bad):
                self.assertEqual(self.patch(client, bad).status_code, 400)
        response = self.patch(client, 0)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['settings']['pos_cancel_simple_days'], 0)
        self.assertEqual(self.patch(client, 365).json()['settings']['pos_cancel_simple_days'], 365)


# -- decision 2 · a confirmed refund is evidence, not a cancellation ---------------------

EVIDENCE = 'Transferencia BCP op. 00123456 del 16/10/2026'


class RefundEvidenceTest(_ClosureBase):
    def setUp(self):
        super().setUp()
        self.order = self.web_order()                         # 3 × 49.00 = 147.00

    def confirm(self, refund, client=None, **body):
        return self.post_confirm(self.order, refund, client or self.superior_desk, **body)

    def full(self, refund, **extra):
        return {'reference': 'OP-123456', 'evidence': EVIDENCE, 'amount': str(refund.amount), **extra}

    def test_cancelling_never_confirms_a_refund_in_any_mode(self):
        till = self.till_sale(1)
        unpaid = self.unpaid_order()
        for order, extra in ((self.order, {'refund_method': 'gateway'}), (till, {'goods_returned': True,
                                                                               'refund_method': 'cash'}),
                             (unpaid, {'refund_method': 'cash'})):
            with self.subTest(order.sales_channel, paid=order.paid):
                self.assertEqual(self.post_cancel(order, client=self.superior_desk, **extra).status_code, 200)
        self.assertFalse(OrderRefund.objects.filter(status=OrderRefund.STATUS_CONFIRMED).exists())
        self.assertEqual(set(OrderRefund.objects.values_list('order_id', 'status')),
                         {(self.order.pk, 'pending'), (till.pk, 'pending')})
        for order in (self.order, till):
            self.assertEqual(refunds.summary(order)['confirmed'], D('0.00'))
            self.assertEqual(refunds.summary(order)['held'], order.total)

    def test_confirming_needs_evidence_reference_and_the_exact_amount(self):
        self.post_cancel(self.order, refund_method='transfer')
        refund = OrderRefund.objects.get()
        for body, code in (
            ({'reference': 'OP-1', 'amount': '147.00'}, 'evidence_required'),
            ({'reference': 'OP-1', 'amount': '147.00', 'evidence': ' corto '}, 'evidence_required'),
            ({'reference': 'OP-1', 'amount': '147.00', 'evidence': {'op': '123'}}, 'evidence_invalid'),
            ({'reference': 'OP-1', 'amount': '147.00', 'evidence': ['Transferencia BCP 1234']}, 'evidence_invalid'),
            ({'reference': 'OP-1', 'amount': '147.00', 'evidence': 'x' * 301}, 'evidence_too_long'),
            ({'reference': 'OP-1', 'evidence': EVIDENCE}, 'amount_invalid'),
            ({'reference': 'OP-1', 'evidence': EVIDENCE, 'amount': '100.00'}, 'amount_mismatch'),
            ({'reference': 'OP-1', 'evidence': EVIDENCE, 'amount': '147.01'}, 'amount_mismatch'),
            ({'reference': ['OP-1'], 'evidence': EVIDENCE, 'amount': '147.00'}, 'reference_required'),
        ):
            with self.subTest(body=body):
                response = self.confirm(refund, **body)
                self.assertEqual((response.status_code, response.json()['code']), (400, code), response.content)
        refund.refresh_from_db()
        self.assertEqual((refund.status, refund.evidence), ('pending', ''))

        done = self.confirm(refund, **self.full(refund))

        self.assertEqual(done.status_code, 200, done.content)
        refund.refresh_from_db()
        self.assertEqual((refund.status, refund.evidence, refund.reference, refund.confirmed_by),
                         ('confirmed', EVIDENCE, 'OP-123456', self.superior))
        [item] = done.json()['refunds']['items']
        self.assertEqual(item['evidence'], EVIDENCE)

    def test_only_the_companys_administrator_confirms(self):
        self.post_cancel(self.order, refund_method='cash')
        refund = OrderRefund.objects.get()
        self.assertEqual(self.confirm(refund, client=self.desk, **self.full(refund)).status_code, 403)
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'pending')

    def test_the_audit_proves_the_evidence_without_copying_it(self):
        import hashlib

        self.post_cancel(self.order, refund_method='cash')
        refund = OrderRefund.objects.get()
        self.confirm(refund, **self.full(refund, note='Entregado a Rosa Quispe, DNI 40404040'))
        audit = AdminAuditLog.objects.get(action='order_refund_confirmed')
        self.assertEqual(audit.metadata['evidence_sha256'], hashlib.sha256(EVIDENCE.encode()).hexdigest())
        self.assertEqual(audit.metadata['amount'], '147.00')
        self.assertEqual(audit.metadata['reference'], 'OP-123456')
        self.assertEqual(audit.actor, self.superior)
        for value in audit.metadata.values():
            self.assertNotIn('BCP', str(value))
            self.assertNotIn('40404040', str(value))
            self.assertNotIn('Rosa', str(value))

    def test_a_partial_refund_is_a_smaller_pending_then_its_confirmation(self):
        self.post_cancel(self.order)                          # no refund chosen
        self.assertFalse(OrderRefund.objects.exists())
        self.assertEqual(self.post_refund(self.order, amount='47.00', method='transfer').status_code, 201)
        first = OrderRefund.objects.get()
        self.assertEqual(self.confirm(first, **self.full(first)).status_code, 200)
        figures = refunds.summary(self.order)
        self.assertEqual((figures['collected'], figures['confirmed'], figures['pending'], figures['refundable'],
                          figures['held']), (D('147.00'), D('47.00'), D('0.00'), D('100.00'), D('100.00')))
        # The rest, then nothing more: the ceiling holds.
        self.assertEqual(self.post_refund(self.order, amount='100.00', method='cash').status_code, 201)
        over = self.post_refund(self.order, amount='0.01', method='cash')
        self.assertEqual((over.status_code, over.json()['code']), (409, 'nothing_to_refund'))
        second = OrderRefund.objects.get(status='pending')
        self.assertEqual(self.confirm(second, **self.full(second, reference='CAJA-77')).status_code, 200)
        self.assertEqual(refunds.summary(self.order)['held'], D('0.00'))
        self.assertTrue(Order.objects.get(pk=self.order.pk).paid)

    def test_a_full_refund_recorded_with_the_cancellation(self):
        self.post_cancel(self.order, refund_method='gateway')
        refund = OrderRefund.objects.get()
        self.assertEqual(refund.amount, D('147.00'))
        self.assertEqual(self.confirm(refund, **self.full(refund)).status_code, 200)
        figures = refunds.summary(self.order)
        self.assertEqual((figures['confirmed'], figures['held'], figures['refundable']),
                         (D('147.00'), D('0.00'), D('0.00')))


# -- decision 3 · the commission of an annulled sale -------------------------------------

class CommissionClosureTest(_ClosureBase):
    def setUp(self):
        super().setUp()
        reporter, _ = _p2d_member(self.company, 'comisiones_lector', ['company.view', 'sales.commissions.view'])
        self.reporter = APIClient()
        self.reporter.force_authenticate(reporter)

    def settled_sale(self, amount='1.96'):
        from store import commission_services

        sale = self.till_sale(1)
        commission = self.commission(sale, amount=amount)
        commission_services.settle(commission, self.superior)
        commission.refresh_from_db()
        return sale, commission

    def test_an_unsettled_commission_is_voided_once_and_gets_no_adjustment(self):
        sale = self.till_sale(1)
        commission = self.commission(sale)
        self.assertEqual(self.post_cancel(sale, goods_returned=True).status_code, 200)
        commission.refresh_from_db()
        self.assertEqual(commission.status, SalesCommission.STATUS_VOIDED)
        self.assertFalse(CommissionAdjustment.objects.exists())
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(sale.pk))
        self.assertEqual((audit.metadata['commission_voided'], audit.metadata['commission_adjustment_id']),
                         (True, None))

    def test_settling_is_once_audited_and_only_of_an_accrued_live_sale(self):
        from store import commission_services

        sale, commission = self.settled_sale()
        self.assertEqual((commission.status, commission.settled_by), (SalesCommission.STATUS_SETTLED, self.superior))
        self.assertIsNotNone(commission.settled_at)
        self.assertTrue(AdminAuditLog.objects.filter(action='commission_settled', target_id=str(commission.pk))
                        .exists())
        with self.assertRaises(commission_services.CommissionError):
            commission_services.settle(commission, self.superior)
        other = self.till_sale(1)
        voided = self.commission(other, status=SalesCommission.STATUS_VOIDED)
        with self.assertRaises(commission_services.CommissionError):
            commission_services.settle(voided, self.superior)
        # The database refuses a settled row without its date.
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesCommission.objects.filter(pk=voided.pk).update(status=SalesCommission.STATUS_SETTLED)

    def test_a_settled_commission_keeps_its_row_and_gets_one_compensating_adjustment(self):
        sale, commission = self.settled_sale('2.50')
        before = SalesCommission.objects.filter(pk=commission.pk).values().get()

        response = self.post_cancel(sale, client=self.superior_desk, goods_returned=True, refund_method='cash')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(SalesCommission.objects.filter(pk=commission.pk).values().get(), before)
        [adjustment] = CommissionAdjustment.objects.all()
        cancellation = OrderCancellation.objects.get(order=sale)
        self.assertEqual((adjustment.commission, adjustment.cancellation, adjustment.amount, adjustment.kind,
                          adjustment.seller, adjustment.created_by),
                         (commission, cancellation, D('-2.50'), 'cancellation', self.manager, self.superior))
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(sale.pk))
        self.assertEqual((audit.metadata['commission_voided'], audit.metadata['commission_adjustment_id']),
                         (False, adjustment.pk))
        # The refund path afterwards adds nothing.
        refund = OrderRefund.objects.get(order=sale)
        self.post_confirm(sale, refund, self.superior_desk, reference='CAJA-1', evidence=EVIDENCE,
                          amount=str(refund.amount))
        self.assertEqual(OrderRefund.objects.get(pk=refund.pk).status, 'confirmed')
        self.assertEqual(CommissionAdjustment.objects.count(), 1)

    def test_a_second_adjustment_is_impossible(self):
        from django.db import IntegrityError, transaction

        sale, commission = self.settled_sale()
        self.post_cancel(sale, client=self.superior_desk, goods_returned=True)
        adjustment = CommissionAdjustment.objects.get()
        # The service gives back the one that exists…
        self.assertEqual(cancelling.compensate_commission(Order.objects.get(pk=sale.pk), adjustment.cancellation,
                                                          self.superior), adjustment)
        self.assertEqual(CommissionAdjustment.objects.count(), 1)
        # …and the database refuses another.
        with self.assertRaises(IntegrityError), transaction.atomic():
            CommissionAdjustment.objects.create(
                company=self.company, commission=commission, cancellation=adjustment.cancellation,
                amount=D('-1.96'))

    def test_the_report_shows_accrued_settled_voided_and_the_balance_to_recover(self):
        accrued_sale = self.till_sale(1)
        self.commission(accrued_sale, amount='3.00')                 # stays accrued
        voided_sale = self.till_sale(1)
        self.commission(voided_sale, amount='1.00')
        self.post_cancel(voided_sale, goods_returned=True)           # voided
        settled_sale, _settled = self.settled_sale('2.50')           # settled, sale stays
        annulled_sale, _paid = self.settled_sale('4.00')             # settled, then annulled
        self.post_cancel(annulled_sale, client=self.superior_desk, goods_returned=True)

        body = self.reporter.get(f'/api/admin/sales/commissions/?{self.q}&days=7').json()

        [row] = body['results']
        self.assertEqual((row['accrued'], row['settled'], row['voided'], row['recoverable'], row['commission']),
                         ('3.00', '6.50', '1.00', '-4.00', '5.50'))
        self.assertEqual(row['sales'], 3)
        self.assertEqual(body['total_commission'], '5.50')
        self.assertEqual((body['totals']['accrued'], body['totals']['settled'], body['totals']['voided'],
                          body['totals']['recoverable']), ('3.00', '6.50', '1.00', '-4.00'))
        [adjustment] = body['adjustments']
        self.assertEqual((adjustment['order_id'], adjustment['amount'], adjustment['label']),
                         (annulled_sale.pk, '-4.00', 'Ajuste por anulación (saldo a recuperar)'))


# -- decision 4 and the board audit · one hand-computed fixture --------------------------

class FinancialBoardsFixtureTest(_ClosureBase):
    """
    One day, one product. Mouse: price 50.00, cost 20.00, 30 on the shelf.

      W1  web paid, 2                     100.00  live sale
      W2  web unpaid, 1                    50.00  pending payment (no money)
      W3  web paid, 1, cancelled           50.00  refund PENDING 50.00
                                                  + a TEST-keys duplicate charge of 50.00 (not money)
      W4  web paid, 3, cancelled          150.00  refund CONFIRMED 60.00 (partial)
      P1  till sale, 1                     50.00  live; commission 1.00 accrued
      P2  till sale, 1, annulled simply    50.00  commission 1.00 → voided
      P3  till sale, 2, commission 2.00 settled, annulled by a superior
                                          100.00  commission kept settled, adjustment −2.00

      sales          150.00 = W1 + P1 · 2 orders · 3 units · ticket 75.00
                     online 1 / 100.00 / 2 units · pos 1 / 50.00 / 1 unit
      collected      live 150.00 + cancelled paid 350.00 (W3 W4 P2 P3) + late 0.00 = gross 500.00
      refunds        confirmed 60.00 · pending 50.00
      net collected  440.00; still held for cancellations 350.00 − 60.00 = 290.00
      margin         net sales of W1+P1 at cost 3 × 20 = 60.00; cancellations 4 orders, 7 units,
                     gross 350.00, cost 140.00, refunds 60.00 confirmed / 50.00 pending
      demand         3 units (cancelled exits left out)
      commissions    accrued 1.00 · settled 2.00 · voided 1.00 · recoverable −2.00 · net 1.00
    """

    def setUp(self):
        from store.models import Customer, PaymentTransaction, Product
        from store.tests import _prod

        super().setUp()
        self.mouse = _prod(self.company, 'Mouse', 'mouse-cierre', price='50.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.mouse.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=30, reason='Compra', unit_cost=D('20'))
        self.assertIsInstance(self.mouse, Product)
        self.client_record = Customer.objects.create(company=self.company, first_name='Rosa', last_name='Quispe')
        analyst, _ = _p2d_member(self.company, 'analista_cierre', [
            'company.view', 'sales.analytics.view', 'costs.view', 'sales.commissions.view', 'sales.orders.view',
            'inventory.reports', 'service.customers.view', 'sales.promotions.view'])
        self.analyst = APIClient()
        self.analyst.force_authenticate(analyst)

        self.w1 = self.web_order((self.mouse, 2), customer=self.client_record)
        self.w2 = self.unpaid(customer=self.client_record)
        self.w3 = self.web_order((self.mouse, 1), customer=self.client_record)
        self.w4 = self.web_order((self.mouse, 3), customer=self.client_record)
        self.p1 = self.web_order((self.mouse, 1), sales_channel='pos', fulfillment_status='delivered')
        self.p2 = self.web_order((self.mouse, 1), sales_channel='pos', fulfillment_status='delivered')
        self.p3 = self.web_order((self.mouse, 2), sales_channel='pos', fulfillment_status='delivered')
        self.commission(self.p1, amount='1.00')
        self.commission(self.p2, amount='1.00')
        from store import commission_services

        commission_services.settle(self.commission(self.p3, amount='2.00'), self.superior)
        self.snapshots = self.frozen()

        self.post_cancel(self.w3, refund_method='gateway')
        PaymentTransaction.objects.create(
            order=self.w3, provider='izipay', transaction_id='DUP-TEST', order_number='DUP-TEST', amount=D('50.00'),
            currency='PEN', environment='test', status=PaymentTransaction.Status.DUPLICATE,
            confirmed_at=timezone.now())
        self.post_cancel(self.w4)
        self.post_refund(self.w4, amount='60.00', method='transfer')
        refund = OrderRefund.objects.get(order=self.w4)
        self.post_confirm(self.w4, refund, self.superior_desk, reference='TR-60', evidence=EVIDENCE, amount='60.00')
        self.assertEqual(self.post_cancel(self.p2, goods_returned=True).status_code, 200)
        self.assertEqual(self.post_cancel(self.p3, goods_returned=True).status_code, 403)
        self.assertEqual(self.post_cancel(self.p3, client=self.superior_desk, goods_returned=True).status_code, 200)

    def unpaid(self, **fields):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a, customer_email='x@example.com',
            total=D('50.00'), status=Order.Status.PENDING_PAYMENT, paid=False, **fields)
        from store.models import OrderItem

        OrderItem.objects.create(order=order, product=self.mouse, quantity=1, price=self.mouse.price)
        return order

    def frozen(self):
        from store.models import OrderItem

        return {
            'lines': list(OrderItem.objects.filter(product=self.mouse).order_by('pk').values(
                'pk', 'price', 'quantity', 'unit_cost', 'cost_source', 'net_amount')),
            'exits': list(StockMovement.objects.filter(movement_type=StockMovement.SALE_EXIT, product=self.mouse)
                          .order_by('pk').values('pk', 'quantity', 'unit_cost', 'branch_id', 'order_id')),
            'orders': list(Order.objects.filter(company=self.company).order_by('pk').values(
                'pk', 'total', 'paid', 'paid_at', 'taxable_amount', 'tax_amount', 'discount_amount')),
            'settled': list(SalesCommission.objects.filter(status=SalesCommission.STATUS_SETTLED).values()),
        }

    def get(self, path):
        response = self.analyst.get(f'{path}{"&" if "?" in path else "?"}{self.q}')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    # decision 4 ------------------------------------------------------------------------

    def test_no_snapshot_of_a_sale_is_rewritten(self):
        self.assertEqual(self.frozen(), self.snapshots)

    # the boards ------------------------------------------------------------------------

    def test_the_commercial_summary(self):
        body = self.get('/api/admin/sales/dashboard/')
        today = body['kpis']['today']
        self.assertEqual((today['revenue'], today['orders'], today['units'], today['average_ticket']),
                         ('150.00', 2, 3, '75.00'))
        channels = body['channels']['by_channel']
        self.assertEqual((channels['online'], channels['pos']),
                         ({'orders': 1, 'revenue': '100.00', 'units': 2}, {'orders': 1, 'revenue': '50.00', 'units': 1}))
        self.assertEqual(body['trend'][-1]['revenue'], '150.00')
        collections = body['collections']['today']
        self.assertEqual(collections['live'], {'orders': 2, 'amount': '150.00'})
        self.assertEqual(collections['cancelled_paid'], {'orders': 4, 'amount': '350.00'})
        self.assertEqual(collections['late_charges'], {'count': 0, 'amount': '0.00'})
        self.assertEqual((collections['gross'], collections['refunds_confirmed'], collections['refunds_pending'],
                          collections['net'], collections['cancelled_held'], collections['cancelled_orders']),
                         ('500.00', '60.00', '50.00', '440.00', '290.00', 4))
        [mouse] = [r for r in body['top_products']['results'] if r['product_id'] == self.mouse.pk]
        self.assertEqual((mouse['units_sold'], mouse['revenue']), (3, '150.00'))

    def test_the_margin(self):
        body = self.get('/api/admin/sales/margin/?days=7')
        self.assertEqual((body['sales']['orders'], body['sales']['gross'], body['cost_of_goods_sold']),
                         (2, '150.00', '60.00'))
        self.assertEqual(body['collected']['amount'], '440.00')
        cancelled = body['cancellations']
        self.assertEqual((cancelled['orders'], cancelled['units'], cancelled['gross'], cancelled['cost'],
                          cancelled['refunds_confirmed'], cancelled['refunds_pending']),
                         (4, 7, '350.00', '140.00', '60.00', '50.00'))

    def test_the_demand_forecast_leaves_cancelled_exits_out(self):
        from store import inventory_forecasting as forecasting
        from store.sales_analytics_views import _company_today

        today = _company_today(self.company)
        demand = forecasting.collect_demand([self.branch_a], today=today)
        self.assertEqual(demand[(self.branch_a.pk, self.mouse.pk)].units_in(today, today), 3)

    def test_the_commissions(self):
        body = self.get('/api/admin/sales/commissions/?days=7')
        self.assertEqual(body['totals'], {'accrued': '1.00', 'settled': '2.00', 'voided': '1.00',
                                          'recoverable': '-2.00'})
        self.assertEqual(body['total_commission'], '1.00')

    def test_the_internal_dashboard(self):
        sales = self.get('/api/me/internal-dashboard/')['sales']
        self.assertEqual((sales['today_revenue'], sales['today_orders'], sales['total_revenue'],
                          sales['total_paid_orders'], sales['average_ticket'], sales['pending_payment']),
                         ('150.00', 2, '150.00', 2, '75.00', 1))
        self.assertEqual(sales['collections'], {
            'cancelled_paid': {'orders': 4, 'amount': '350.00'}, 'refunds_confirmed': '60.00',
            'refunds_pending': '50.00', 'cancelled_held': '290.00'})
        by_status = {row['label']: row['value'] for row in sales['orders_by_status']}
        self.assertEqual((by_status['Pagado'], by_status['Cancelado'], by_status['Pendiente de pago']), (2, 4, 1))

    def test_the_customer_summary(self):
        summary = self.get(f'/api/admin/customers/{self.client_record.pk}/')['summary']
        self.assertEqual((summary['orders_total'], summary['paid_orders'], summary['paid_amount'],
                          summary['cancelled_orders'], summary['refunds_confirmed']),
                         (4, 1, '100.00', 2, '60.00'))

    def test_the_orders_list_filters(self):
        def ids(params):
            return {row['id'] for row in self.get(f'/api/admin/orders/?{params}')['results']}

        self.assertEqual(ids('paid=true'), {self.w1.pk, self.p1.pk})
        self.assertEqual(ids('paid=false'), {self.w2.pk})
        self.assertEqual(ids('status=cancelled'), {self.w3.pk, self.w4.pk, self.p2.pk, self.p3.pk})
        self.assertEqual(ids('refund=pending'), {self.w3.pk})
        self.assertEqual(ids('refund=confirmed'), {self.w4.pk})

    def test_the_promotion_report_leaves_annulled_sales_out(self):
        from store.models import AppliedPromotion, Promotion

        promotion = Promotion.objects.create(company=self.company, name='Combo cierre',
                                             promotion_type=Promotion.BUNDLE_PERCENT, discount_percent=D('10'))
        for order in (self.p1, self.p2):
            AppliedPromotion.objects.create(
                company=self.company, order=order, promotion=promotion, promotion_name_snapshot='Combo cierre',
                promotion_type_snapshot=Promotion.BUNDLE_PERCENT, applications=1, regular_amount=D('55.00'),
                discount_amount=D('5.00'))
        [row] = [r for r in self.get('/api/admin/sales/promotions/')['results'] if r['id'] == promotion.pk]
        self.assertEqual((row['stats']['orders'], row['stats']['discount_given']), (1, '5.00'))


class SupplierPayableAfterCancelTest(_CostShop, TestCase):
    """What the supplier delivered is owed to it, even when the customer's order is cancelled afterwards."""

    def test_a_received_line_stays_payable_after_the_cancellation(self):
        from store import supply_services as supply

        self.add_on_order(quantity=2)
        order = self.checkout()
        self.assertEqual(self.pay(order), 'paid')
        [line] = order.items.all()
        supply.receive_line(line, actor=self.staff)
        [before] = supply.pending_settlement(self.company)
        self.assertEqual(before['units'], 2)

        cancelling.cancel_paid_order(order, self.staff, REASON)

        self.assertEqual(supply.pending_settlement(self.company), [before])

    def test_a_line_never_received_is_not_owed(self):
        from store import supply_services as supply

        self.add_on_order(quantity=1)
        order = self.checkout()
        self.pay(order)
        cancelling.cancel_paid_order(order, self.staff, REASON)
        self.assertEqual(supply.pending_settlement(self.company), [])
