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
    AdminAuditLog, CompanySettings, FiscalDocument, Order, OrderCancellation, OrderRefund, SalesCommission,
    StockMovement,
)
from store import test_order_cancel as cancel_tests
from store.test_order_cancel import REASON, RETURN
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
