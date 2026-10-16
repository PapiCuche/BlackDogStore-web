"""
COMMERCE-ADMIN-UX-01 · cancellations the owner asked for on 2026-10-15.

  * An UNPAID web order is cancelled with a reason: it stays in the history as
    cancelled, the devices its buyer held go back to the shelf, nothing is
    counted as income. A payment that arrives later is money to give back —
    kept, flagged on the order — and never reopens it.
  * A TILL sale (born paid and delivered) is annulled once the cashier
    confirms the goods are back in the shop: stock back at the sale's cost,
    the internal note voided, a live boleta/factura kept and flagged, the
    seller's commission voided.
  * A paid cancellation is not a refund. A refund is RECORDED (pending, then
    confirmed with its reference), up to what was collected; no payment row
    changes and no gateway is called.
  * Sales metrics leave cancelled orders out; collections count the money
    still held until its refund is confirmed.
"""
from decimal import Decimal as D
from threading import Barrier, Thread

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import order_cancellation as cancelling
from store import order_refunds as refunds
from store import unit_hold_services as holds
from store.models import (
    AdminAuditLog, BranchStock, Company, FiscalDocument, Order, OrderCancellation, OrderItem, OrderRefund,
    PaymentTransaction, Product, SalesCommission, SalesNote, StockMovement, StockUnit,
)
from store.test_device_commerce import SaleShop
from store.test_order_cancel import REASON, RETURN, PapersTest, _Base, _late_charge
from store.test_payment_hardening import Base as PaymentBase
from store.tests import _p2d_member, _prod

MANAGE = ['company.view', 'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage']


class _UxBase(_Base):
    def unpaid_order(self, quantity=1, **fields):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a, customer_name='Rosa Quispe',
            customer_email='rosa@example.com', total=self.cable.price * quantity,
            status=Order.Status.PENDING_PAYMENT, paid=False, **fields)
        OrderItem.objects.create(order=order, product=self.cable, quantity=quantity, price=self.cable.price)
        return order

    def till_sale(self, quantity=2, **fields):
        return self.web_order((self.cable, quantity), sales_channel='pos', fulfillment_status='delivered', **fields)

    def post_cancel(self, order, reason=REASON, client=None, **extra):
        return (client or self.desk).post(
            f'/api/admin/orders/{order.pk}/cancel/?{self.q}', {'reason': reason, **extra}, format='json')

    def admin_client(self, username='admin_reembolsos'):
        admin, _ = _p2d_member(self.company, username, MANAGE + ['company.manage'])
        client = APIClient()
        client.force_authenticate(admin)
        return admin, client

    def post_refund(self, order, client=None, **body):
        return (client or self.desk).post(f'/api/admin/orders/{order.pk}/refunds/?{self.q}', body, format='json')

    def post_confirm(self, order, refund, client, **body):
        return client.post(f'/api/admin/orders/{order.pk}/refunds/{refund.pk}/confirm/?{self.q}', body,
                           format='json')


# -- (a) unpaid web orders ----------------------------------------------------------

class UnpaidOrderTest(_UxBase):
    def test_an_unpaid_order_is_cancelled_with_its_reason_and_kept_in_the_history(self):
        order = self.unpaid_order()
        self.assertTrue(self.detail(order)['can_cancel'])
        self.assertEqual(self.detail(order)['cancel_mode'], 'unpaid')

        response = self.post_cancel(order)

        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual((order.status, order.paid, order.paid_at), (Order.Status.CANCELLED, False, None))
        cancellation = OrderCancellation.objects.get(order=order)
        self.assertEqual((cancellation.reason, cancellation.cancelled_by, cancellation.was_paid,
                          cancellation.previous_status, cancellation.stock_restored),
                         (REASON, self.manager, False, 'pending_payment', False))
        body = response.json()['cancellation']
        self.assertEqual((body['reason'], body['was_paid']), (REASON, False))
        # No stock was taken, none comes back; no income anywhere.
        self.assertFalse(StockMovement.objects.filter(order=order).exists())
        self.assertEqual(self.cable_stock(), 20)
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(order.pk))
        self.assertEqual((audit.metadata['mode'], audit.metadata['paid_kept']), ('unpaid', False))
        # In the orders list, under «Cancelados».
        listed = self.desk.get(f'/api/admin/orders/?{self.q}&status=cancelled').json()
        self.assertIn(order.pk, [row['id'] for row in listed['results']])

    def test_the_reason_is_required_for_an_unpaid_order_too(self):
        order = self.unpaid_order()
        response = self.post_cancel(order, reason='  ')
        self.assertEqual((response.status_code, response.json()['code']), (400, 'reason_required'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PENDING_PAYMENT)

    def test_failed_or_expired_orders_are_not_cancelled_again(self):
        for status in ('failed', 'expired', 'refunded'):
            with self.subTest(status):
                order = self.unpaid_order()
                Order.objects.filter(pk=order.pk).update(status=status)
                response = self.post_cancel(order)
                self.assertEqual((response.status_code, response.json()['code']), (409, 'not_paid'))
                self.assertEqual(Order.objects.get(pk=order.pk).status, status)

    def test_reading_orders_is_not_enough_and_other_tenants_or_branches_see_nothing(self):
        order = self.unpaid_order()
        reader, _ = _p2d_member(self.company, 'lectura_impago', ['company.view', 'sales.orders.view'])
        foreign, _ = _p2d_member(self.other, 'ajeno_impago', MANAGE)
        north, _ = _p2d_member(self.company, 'norte_impago', MANAGE, branches=[self.branch_b])
        for user, query, code in ((reader, self.q, 403), (foreign, f'company={self.other.pk}', 404),
                                  (north, self.q, 404)):
            with self.subTest(user.username):
                client = APIClient()
                client.force_authenticate(user)
                response = client.post(f'/api/admin/orders/{order.pk}/cancel/?{query}', {'reason': REASON},
                                        format='json')
                self.assertEqual(response.status_code, code, response.content)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PENDING_PAYMENT)

    def test_a_refund_cannot_be_recorded_for_an_unpaid_cancellation_without_money(self):
        order = self.unpaid_order()
        self.post_cancel(order)
        response = self.post_refund(order, method='cash')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'nothing_to_refund'))
        self.assertEqual(response.json()['detail'], 'No queda dinero cobrado por devolver en este pedido.')


class UnpaidOrderLatePaymentTest(PaymentBase):
    """A payment for an order cancelled before it was paid: recorded, flagged, never a sale."""

    def test_the_money_is_recorded_the_order_flagged_and_never_reopened(self):
        operator, _ = _p2d_member(self.order.company, 'cancela_impago', MANAGE)
        stock_before = self.stock()
        cancelling.cancel_order(self.order, operator, REASON)
        self.refresh()
        self.assertEqual((self.order.status, self.order.paid), (Order.Status.CANCELLED, False))

        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(self.notify().status_code, 200)

        self.refresh()
        self.attempt.refresh_from_db()
        self.assertEqual((self.order.status, self.order.paid), (Order.Status.CANCELLED, False))
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.DUPLICATE)
        self.assertIn('cancelado', self.attempt.failure_reason)
        self.assertIn('Cobro recibido con el pedido cancelado', self.order.payment_error)
        self.assertFalse(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').exists())
        self.assertEqual(self.stock(), stock_before)
        # Charged with TEST keys: recorded, but it is not money (review P3).
        self.assertEqual(self.attempt.environment, 'test')
        self.assertEqual(refunds.summary(self.order)['refundable'], D('0.00'))
        # The same charge with PRODUCTION keys is money to give back.
        PaymentTransaction.objects.filter(pk=self.attempt.pk).update(environment='production')
        self.assertEqual(refunds.summary(self.order)['refundable'], self.attempt.amount)
        refund = refunds.record_pending(self.order, operator, method='gateway')
        self.assertEqual(refund.amount, self.attempt.amount)


class UnpaidDeviceHoldTest(SaleShop, TestCase):
    def test_the_devices_the_buyer_held_go_back_to_the_shelf_once(self):
        manager, _ = _p2d_member(self.company, 'dc2_cancel', MANAGE)
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        order = self.checkout()
        [line] = order.items.all()
        unit = StockUnit.objects.get(pk=line.stock_unit_id)
        self.assertEqual((unit.status, unit.hold_order_id), (StockUnit.Status.RESERVED, order.pk))
        self.assertEqual(self.available(self.new128), 1)

        cancelling.cancel_order(order, manager, REASON)

        unit.refresh_from_db()
        self.assertEqual((unit.status, unit.hold_order_id, unit.hold_key), (StockUnit.Status.AVAILABLE, None, ''))
        self.assertEqual((self.shelf(self.new128), self.available(self.new128)), (2, 2))
        self.assertEqual(OrderCancellation.objects.get(order=order).holds_released, 1)
        # A sweep afterwards finds nothing to release twice.
        released = StockMovement.objects.filter(product=self.new128, metadata__web_hold=True).count()
        self.assertEqual(holds.release_expired(company=self.company), 0)
        self.assertEqual(StockMovement.objects.filter(product=self.new128, metadata__web_hold=True).count(), released)
        # The DC2 rollback command does not list it: it is not pending any more.
        call_command('cancel_pending_unit_orders', '--apply', stdout=open('/dev/null', 'w'))
        self.assertFalse(AdminAuditLog.objects.filter(action='order_cancelled_before_rollback').exists())

    def test_the_rollback_command_still_cancels_an_order_left_pending(self):
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        order = self.checkout()
        call_command('cancel_pending_unit_orders', '--apply', stdout=open('/dev/null', 'w'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.CANCELLED)


# -- (b) till sales -------------------------------------------------------------------

class TillSaleTest(_UxBase):
    def test_a_till_sale_needs_the_cashier_to_say_the_goods_are_back(self):
        sale = self.till_sale()
        detail = self.detail(sale)
        self.assertEqual((detail['can_cancel'], detail['cancel_mode']), (True, 'pos'))

        response = self.post_cancel(sale)

        self.assertEqual((response.status_code, response.json()['code']), (400, 'goods_return_required'))
        self.assertEqual(Order.objects.get(pk=sale.pk).status, Order.Status.PAID)
        self.assertEqual(self.cable_stock(), 18)

    def test_annulled_with_the_goods_back_restores_stock_once_at_the_sale_cost_and_voids_the_note(self):
        from store.sales_note_control import REASON_ORDER_CANCELLED
        from store.sales_note_services import get_or_create_sales_note

        sale = self.till_sale(sold_by=self.manager)
        note, _ = get_or_create_sales_note(sale, actor=self.manager)
        commission = SalesCommission.objects.create(
            company=self.company, order=sale, seller=self.manager, rate_percent=D('2.00'),
            base_amount=D('98.00'), amount=D('1.96'))
        exit_cost = StockMovement.objects.get(order=sale, movement_type='sale_exit').unit_cost

        response = self.post_cancel(sale, goods_returned=True)

        self.assertEqual(response.status_code, 200, response.content)
        sale.refresh_from_db()
        self.assertEqual((sale.status, sale.paid), (Order.Status.CANCELLED, True))
        self.assertEqual(self.cable_stock(), 20)
        back = StockMovement.objects.get(order=sale, movement_type=RETURN)
        self.assertEqual(back.unit_cost, exit_cost)
        cancellation = OrderCancellation.objects.get(order=sale)
        self.assertEqual((cancellation.goods_returned, cancellation.was_paid, cancellation.sales_note_voided),
                         (True, True, True))
        note.refresh_from_db()
        self.assertEqual((note.status, note.void_reason_type), (SalesNote.STATUS_VOID, REASON_ORDER_CANCELLED))
        commission.refresh_from_db()
        self.assertEqual(commission.status, SalesCommission.STATUS_VOIDED)
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(sale.pk))
        self.assertEqual((audit.metadata['mode'], audit.metadata['goods_returned'],
                          audit.metadata['commission_voided']), ('pos', True, True))
        # Once.
        again = self.post_cancel(sale, goods_returned=True)
        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_cancelled'))
        self.assertEqual(StockMovement.objects.filter(order=sale, movement_type=RETURN).count(), 1)

    def test_a_till_sale_keeps_its_boleta_and_flags_its_credit_note(self):
        sale = self.till_sale()
        document = PapersTest.fiscal_document(self, sale)
        before = FiscalDocument.objects.filter(pk=document.pk).values().get()

        self.post_cancel(sale, goods_returned=True)

        self.assertEqual(FiscalDocument.objects.filter(pk=document.pk).values().get(), before)
        cancellation = OrderCancellation.objects.get(order=sale)
        self.assertTrue(cancellation.fiscal_pending)
        self.assertIn('B001-12', cancellation.fiscal_document)

    def test_a_till_sale_that_is_no_longer_paid_is_not_annulled(self):
        # Mutation pass: the «pos» mode needs a paid sale, not just the channel.
        sale = self.till_sale()
        Order.objects.filter(pk=sale.pk).update(status=Order.Status.REFUNDED)
        response = self.post_cancel(sale, goods_returned=True)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'not_paid'))
        self.assertIsNone(self.detail(sale)['cancel_mode'])
        self.assertFalse(StockMovement.objects.filter(order=sale, movement_type=RETURN).exists())

    def test_a_till_sale_without_authority_is_not_annulled(self):
        sale = self.till_sale()
        seller, _ = _p2d_member(self.company, 'cajero_sin_pedidos', ['company.view', 'sales.orders.view',
                                                                     'sales.pos.use'])
        client = APIClient()
        client.force_authenticate(seller)
        self.assertEqual(self.post_cancel(sale, client=client, goods_returned=True).status_code, 403)
        self.assertEqual(Order.objects.get(pk=sale.pk).status, Order.Status.PAID)


# -- (c) refunds ----------------------------------------------------------------------

class RefundTest(_UxBase):
    def setUp(self):
        super().setUp()
        self.order = self.web_order()                         # 3 × 49.00 = 147.00
        self.attempt = PaymentTransaction.objects.create(
            order=self.order, provider='izipay', transaction_id='PAID0001', order_number='PAID0001',
            amount=self.order.total, currency='PEN', environment='test',
            status=PaymentTransaction.Status.AUTHORIZED, confirmed_at=timezone.now())
        self.admin, self.admin_desk = self.admin_client()

    def test_cancelling_with_a_pending_refund_records_it_for_everything_collected(self):
        response = self.post_cancel(self.order, refund_method='gateway')

        self.assertEqual(response.status_code, 200, response.content)
        refund = OrderRefund.objects.get(order=self.order)
        self.assertEqual((refund.amount, refund.method, refund.status, refund.created_by),
                         (D('147.00'), 'gateway', 'pending', self.manager))
        block = response.json()['refunds']
        self.assertEqual((block['collected'], block['pending'], block['confirmed'], block['held']),
                         ('147.00', '147.00', '0.00', '147.00'))
        # Never a payment row change.
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)

    def test_cancelling_without_choosing_records_no_refund(self):
        self.post_cancel(self.order)
        self.assertFalse(OrderRefund.objects.exists())
        self.assertTrue(self.detail(self.order)['refunds']['can_record'])

    def test_a_refund_is_bounded_by_what_was_collected(self):
        self.post_cancel(self.order)
        for amount, code, status in (('0', 'amount_invalid', 400), ('-5', 'amount_invalid', 400),
                                     ('abc', 'amount_invalid', 400), ('1.001', 'amount_invalid', 400),
                                     ('147.01', 'amount_exceeds', 409)):
            with self.subTest(amount):
                response = self.post_refund(self.order, amount=amount, method='cash')
                self.assertEqual((response.status_code, response.json()['code']), (status, code))
        self.assertEqual(self.post_refund(self.order, amount='100.00', method='cash').status_code, 201)
        # The pending one already holds 100.00 of the ceiling.
        over = self.post_refund(self.order, amount='47.01', method='cash')
        self.assertEqual((over.status_code, over.json()['code']), (409, 'amount_exceeds'))
        self.assertEqual(self.post_refund(self.order, amount='47.00', method='transfer').status_code, 201)
        self.assertEqual(refunds.summary(self.order)['refundable'], D('0.00'))
        bad = self.post_refund(self.order, method='bitcoin')
        self.assertEqual((bad.status_code, bad.json()['code']), (400, 'method_invalid'))

    def test_only_a_cancelled_order_takes_a_refund(self):
        response = self.post_refund(self.order, method='cash')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'not_cancelled'))

    def test_confirming_needs_a_reference_and_the_companys_administrator_once(self):
        self.post_cancel(self.order, refund_method='gateway')
        refund = OrderRefund.objects.get()

        # The manager who cancelled cannot confirm money going out.
        self.assertEqual(self.post_confirm(self.order, refund, self.desk, reference='OP-123').status_code, 403)
        self.assertFalse(self.detail(self.order)['refunds']['can_confirm'])
        missing = self.post_confirm(self.order, refund, self.admin_desk, reference=' ab ')
        self.assertEqual((missing.status_code, missing.json()['code']), (400, 'reference_required'))

        done = self.post_confirm(self.order, refund, self.admin_desk, reference='IZI-998877', note='Voucher en caja')

        self.assertEqual(done.status_code, 200, done.content)
        refund.refresh_from_db()
        self.assertEqual((refund.status, refund.reference, refund.confirmed_by),
                         ('confirmed', 'IZI-998877', self.admin))
        self.assertIsNotNone(refund.confirmed_at)
        self.assertIn('Voucher en caja', refund.note)
        block = done.json()['refunds']
        self.assertEqual((block['confirmed'], block['pending'], block['held']), ('147.00', '0.00', '0.00'))
        self.assertEqual(AdminAuditLog.objects.filter(action='order_refund_confirmed').count(), 1)
        again = self.post_confirm(self.order, refund, self.admin_desk, reference='IZI-998877')
        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_confirmed'))
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)
        self.assertTrue(Order.objects.get(pk=self.order.pk).paid)

    def test_another_tenant_or_branch_cannot_touch_the_refunds(self):
        self.post_cancel(self.order, refund_method='cash')
        refund = OrderRefund.objects.get()
        foreign, _ = _p2d_member(self.other, 'ajeno_reembolso', MANAGE + ['company.manage'])
        north, _ = _p2d_member(self.company, 'norte_reembolso', MANAGE + ['company.manage'],
                               branches=[self.branch_b])
        for user, query in ((foreign, f'company={self.other.pk}'), (north, self.q)):
            with self.subTest(user.username):
                client = APIClient()
                client.force_authenticate(user)
                self.assertEqual(client.post(f'/api/admin/orders/{self.order.pk}/refunds/?{query}',
                                             {'method': 'cash', 'amount': '1.00'}, format='json').status_code, 404)
                self.assertEqual(client.post(
                    f'/api/admin/orders/{self.order.pk}/refunds/{refund.pk}/confirm/?{query}',
                    {'reference': 'OP-1'}, format='json').status_code, 404)
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'pending')

    def test_a_refund_of_another_order_is_not_found_through_this_one(self):
        self.post_cancel(self.order, refund_method='cash')
        refund = OrderRefund.objects.get()
        other = self.web_order()
        self.assertEqual(self.post_confirm(other, refund, self.admin_desk, reference='OP-1').status_code, 404)


# -- (d) metrics ----------------------------------------------------------------------

class MetricsFixtureTest(_UxBase):
    """
    One hand-computed day. Mouse: price 50.00, cost 20.00.

      A  web paid, 2 mice                  100.00   live sale
      B  web paid, 1, cancelled             50.00   refund confirmed 50.00
      C  web paid, 3, cancelled            150.00   refund pending 150.00
      D  till sale, 1, annulled             50.00   no refund recorded
      E  web unpaid, 1, cancelled           (0)
      F  web unpaid, 1, still pending       (0)
      G  web unpaid, 1, cancelled, then a late charge of 50.00

      sales          100.00, 1 order
      collected      live 100.00 + cancelled paid 250.00 + late 50.00 = 400.00
      refunds        confirmed 50.00, pending 150.00
      net collected  350.00 = 100.00 live + 250.00 still held for cancellations
    """

    def setUp(self):
        super().setUp()
        self.mouse = _prod(self.company, 'Mouse', 'mouse-metricas', price='50.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.mouse.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=30, reason='Compra', unit_cost=D('20'))
        self.admin, self.admin_desk = self.admin_client('admin_metricas')
        analyst, _ = _p2d_member(self.company, 'analista_metricas',
                                 ['company.view', 'sales.analytics.view', 'costs.view'])
        self.analyst = APIClient()
        self.analyst.force_authenticate(analyst)

        self.a = self.web_order((self.mouse, 2))
        self.b = self.web_order((self.mouse, 1))
        self.c = self.web_order((self.mouse, 3))
        self.d = self.web_order((self.mouse, 1), sales_channel='pos', fulfillment_status='delivered')
        self.e = self.unpaid_mouse()
        self.f = self.unpaid_mouse()
        self.g = self.unpaid_mouse()
        self.costs_before = {
            item.pk: (item.unit_cost, item.net_amount) for item in OrderItem.objects.filter(product=self.mouse)}

        self.post_cancel(self.b, refund_method='transfer')
        self.post_confirm(self.b, OrderRefund.objects.get(order=self.b), self.admin_desk, reference='TR-1')
        self.post_cancel(self.c, refund_method='gateway')
        self.post_cancel(self.d, goods_returned=True)
        self.post_cancel(self.e)
        self.post_cancel(self.g)
        PaymentTransaction.objects.create(
            order=self.g, provider='izipay', transaction_id='LATE-G', order_number='LATE-G', amount=D('50.00'),
            currency='PEN', environment='production', status=PaymentTransaction.Status.DUPLICATE,
            confirmed_at=timezone.now())
        # A late charge with TEST keys is not money: in no figure (review P3).
        PaymentTransaction.objects.create(
            order=self.e, provider='izipay', transaction_id='LATE-E-TEST', order_number='LATE-E-TEST',
            amount=D('50.00'), currency='PEN', environment='test', status=PaymentTransaction.Status.DUPLICATE,
            confirmed_at=timezone.now())

    def unpaid_mouse(self):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a, customer_email='x@example.com',
            total=D('50.00'), status=Order.Status.PENDING_PAYMENT, paid=False)
        OrderItem.objects.create(order=order, product=self.mouse, quantity=1, price=self.mouse.price)
        return order

    def test_the_dashboard_counts_live_sales_and_the_money_still_held(self):
        body = self.analyst.get(f'/api/admin/sales/dashboard/?{self.q}').json()

        today = body['kpis']['today']
        self.assertEqual((today['revenue'], today['orders'], today['units']), ('100.00', 1, 2))
        collections = body['collections']['today']
        self.assertEqual(collections['live'], {'orders': 1, 'amount': '100.00'})
        self.assertEqual(collections['cancelled_paid'], {'orders': 3, 'amount': '250.00'})
        self.assertEqual(collections['late_charges'], {'count': 1, 'amount': '50.00'})
        self.assertEqual(collections['gross'], '400.00')
        self.assertEqual(collections['refunds_confirmed'], '50.00')
        self.assertEqual(collections['refunds_pending'], '150.00')
        self.assertEqual(collections['net'], '350.00')
        self.assertEqual(collections['cancelled_held'], '250.00')
        self.assertEqual(collections['cancelled_orders'], 5)
        # The best sellers count what left and did not come back: 2 mice.
        [mouse] = [row for row in body['top_products']['results'] if row['product_id'] == self.mouse.pk]
        self.assertEqual((mouse['units_sold'], mouse['revenue']), (2, '100.00'))

    def test_the_margin_leaves_cancellations_out_and_lists_them_apart_without_touching_snapshots(self):
        body = self.analyst.get(f'/api/admin/sales/margin/?{self.q}&days=7').json()

        self.assertEqual((body['sales']['orders'], body['sales']['gross']), (1, '100.00'))
        self.assertEqual(body['cost_of_goods_sold'], '40.00')
        self.assertEqual(body['collected']['net'], '350.00')
        self.assertEqual(body['collected']['amount'], '350.00')
        cancelled = body['cancellations']
        self.assertEqual((cancelled['orders'], cancelled['gross'], cancelled['cost'], cancelled['units']),
                         (3, '250.00', '100.00', 5))
        self.assertEqual(cancelled['refunds_confirmed'], '50.00')
        after = {item.pk: (item.unit_cost, item.net_amount) for item in OrderItem.objects.filter(product=self.mouse)}
        self.assertEqual(after, self.costs_before)
        self.assertEqual(len([c for c, _n in after.values() if c is not None]), 4)   # the paid lines kept theirs

    def test_the_cancellations_history_is_the_orders_list_filtered(self):
        listed = self.desk.get(f'/api/admin/orders/?{self.q}&status=cancelled').json()
        self.assertEqual({row['id'] for row in listed['results']},
                         {self.b.pk, self.c.pk, self.d.pk, self.e.pk, self.g.pk})
        pending = self.desk.get(f'/api/admin/orders/?{self.q}&refund=pending').json()
        self.assertEqual({row['id'] for row in pending['results']}, {self.c.pk})


# -- races --------------------------------------------------------------------------

class _RaceBase(TransactionTestCase):
    def setUp(self):
        from store.models import Branch

        self.company = Company.objects.create(
            name='Carreras', legal_name='Carreras SAC', tax_id='20911111119', slug='cancel-ux-race')
        self.branch = self.company.branches.first() or Branch.objects.create(company=self.company, name='Centro')
        self.user, _ = _p2d_member(self.company, 'race_manager', MANAGE + ['company.manage'])
        self.cable = Product.objects.create(company=self.company, name='Cable', slug='cable-race', price=D('10.00'))
        BranchStock.objects.update_or_create(branch=self.branch, product=self.cable, defaults={'quantity': 10})

    def race(self, *steps):
        barrier, outcomes = Barrier(len(steps)), {}

        def run(name, step):
            try:
                barrier.wait(timeout=5)
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 - the outcome IS the exception
                outcomes[name] = exc
            finally:
                connection.close()

        threads = [Thread(target=run, args=(name, step)) for name, step in steps]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        return outcomes


class UnpaidCancelAgainstPaymentRaceTest(_RaceBase):
    def test_a_cancellation_and_a_payment_at_once_end_in_one_coherent_state(self):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=D('30.00'), status=Order.Status.PENDING_PAYMENT, paid=False)
        OrderItem.objects.create(order=order, product=self.cable, quantity=3, price=D('10.00'))
        attempt = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='RACE0001', order_number='RACE0001',
            amount=D('30.00'), currency='PEN', environment='production')

        def cancel():
            return cancelling.cancel_order(Order.objects.get(pk=order.pk), self.user, REASON)

        def pay():
            return _late_charge(PaymentTransaction.objects.get(pk=attempt.pk))

        outcomes = self.race(('cancel', cancel), ('pay', pay))

        order.refresh_from_db()
        attempt.refresh_from_db()
        stock = BranchStock.objects.get(branch=self.branch, product=self.cable).quantity
        if order.status == Order.Status.CANCELLED:
            # Cancelled first: the payment is money to give back; nothing was sold.
            self.assertIsInstance(outcomes['cancel'], OrderCancellation, outcomes)
            self.assertEqual(outcomes['pay'], 'duplicate', outcomes)
            self.assertEqual((order.paid, attempt.status, stock), (False, PaymentTransaction.Status.DUPLICATE, 10))
            self.assertEqual(refunds.summary(order)['refundable'], D('30.00'))
        else:
            # Paid first: the unpaid cancellation is refused; the sale stands.
            self.assertEqual(outcomes['pay'], 'paid', outcomes)
            self.assertIsInstance(outcomes['cancel'], cancelling.OrderCancelError, outcomes)
            self.assertEqual((order.status, order.paid, stock), (Order.Status.PAID, True, 7))
            self.assertFalse(OrderCancellation.objects.filter(order=order).exists())


class RefundRaceTest(_RaceBase):
    def setUp(self):
        super().setUp()
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=D('30.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        OrderItem.objects.create(order=self.order, product=self.cable, quantity=3, price=D('10.00'))
        inventory.record_sale_stock_movements(self.order)
        cancelling.cancel_order(self.order, self.user, REASON)

    def test_two_refunds_at_once_never_pass_what_was_collected(self):
        def record():
            return refunds.record_pending(Order.objects.get(pk=self.order.pk), self.user, amount='20.00',
                                          method='cash')

        outcomes = self.race(('a', record), ('b', record))

        won = [o for o in outcomes.values() if isinstance(o, OrderRefund)]
        lost = [o for o in outcomes.values() if isinstance(o, refunds.RefundError)]
        self.assertEqual((len(won), len(lost)), (1, 1), outcomes)
        self.assertEqual(lost[0].code, 'amount_exceeds')
        self.assertEqual(OrderRefund.objects.count(), 1)

    def test_two_confirmations_at_once_confirm_once(self):
        refund = refunds.record_pending(self.order, self.user, method='cash')

        def confirm():
            return refunds.confirm(OrderRefund.objects.get(pk=refund.pk), self.user, reference='CAJA-1')

        outcomes = self.race(('a', confirm), ('b', confirm))

        lost = [o for o in outcomes.values() if isinstance(o, refunds.RefundError)]
        self.assertEqual(len(lost), 1, outcomes)
        self.assertEqual(lost[0].code, 'already_confirmed')
        self.assertEqual(AdminAuditLog.objects.filter(action='order_refund_confirmed').count(), 1)


# -- guards pinned for the mutation pass ---------------------------------------------

class GuardEdgesTest(_UxBase):
    def test_only_a_literal_true_says_the_goods_are_back(self):
        sale = self.till_sale()
        for value in ('true', 'yes', 1, 'false'):
            with self.subTest(value=value):
                response = self.post_cancel(sale, goods_returned=value)
                self.assertEqual((response.status_code, response.json()['code']), (400, 'goods_return_required'))
        self.assertEqual(Order.objects.get(pk=sale.pk).status, Order.Status.PAID)

    def test_asking_a_refund_while_cancelling_an_unpaid_order_cancels_it_and_records_nothing(self):
        order = self.unpaid_order()
        response = self.post_cancel(order, refund_method='cash')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.CANCELLED)
        self.assertFalse(OrderRefund.objects.exists())

    def test_a_bad_refund_method_with_the_cancellation_cancels_nothing(self):
        order = self.web_order()
        response = self.post_cancel(order, refund_method='bitcoin')
        self.assertEqual((response.status_code, response.json()['code']), (400, 'method_invalid'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PAID)
        self.assertFalse(OrderCancellation.objects.exists())
        self.assertEqual(self.cable_stock(), 17)

    def test_confirming_never_gives_back_more_than_was_collected(self):
        """The confirm re-checks the ceiling itself: a pending row that passed it by another road is refused."""
        order = self.web_order()                                  # 147.00
        self.post_cancel(order, refund_method='cash')             # pending 147.00
        stray = OrderRefund.objects.create(company=self.company, order=order, amount=D('0.01'), method='cash')
        admin, _ = self.admin_client('admin_techo')
        first = OrderRefund.objects.exclude(pk=stray.pk).get()
        refunds.confirm(first, admin, reference='OP-1')
        with self.assertRaises(refunds.RefundError) as refused:
            refunds.confirm(stray, admin, reference='OP-2')
        self.assertEqual(refused.exception.code, 'amount_exceeds')
        stray.refresh_from_db()
        self.assertEqual(stray.status, 'pending')
        self.assertEqual(refunds.summary(order)['held'], D('0.00'))

    def test_an_exact_refund_of_what_remains_is_accepted(self):
        order = self.web_order()
        self.post_cancel(order)
        self.assertEqual(self.post_refund(order, amount='147.00', method='cash').status_code, 201)
        self.assertEqual(refunds.summary(order)['refundable'], D('0.00'))


class LegacyRefundTest(TestCase):
    """On the pilot's legacy bridge: sales may record, only admin and superadmin may confirm."""

    def setUp(self):
        from django.core.cache import cache

        from store.tests import _p60_paid_order, _p60_product, _p60_users

        cache.clear()
        self.users = _p60_users()
        product = _p60_product(name='Cable reembolso', inventory=10, price='49.00')
        self.order = _p60_paid_order(product, quantity=1)
        inventory.record_sale_stock_movements(self.order)
        self.client_for('sales').post(f'/api/admin/orders/{self.order.pk}/cancel/',
                                      {'reason': REASON, 'refund_method': 'cash'}, format='json')
        self.refund = OrderRefund.objects.get(order=self.order)

    def client_for(self, role):
        client = APIClient()
        client.force_authenticate(self.users[role])
        return client

    def confirm(self, role):
        return self.client_for(role).post(
            f'/api/admin/orders/{self.order.pk}/refunds/{self.refund.pk}/confirm/', {'reference': 'CAJA-9'},
            format='json')

    def test_sales_records_but_does_not_confirm_and_inventory_does_neither(self):
        self.assertEqual(self.refund.status, 'pending')
        self.assertEqual(self.confirm('sales').status_code, 403)
        self.assertEqual(self.confirm('inventory').status_code, 403)
        self.assertEqual(self.client_for('inventory').post(
            f'/api/admin/orders/{self.order.pk}/refunds/', {'method': 'cash', 'amount': '1.00'},
            format='json').status_code, 403)

    def test_admin_confirms(self):
        response = self.confirm('admin')
        self.assertEqual(response.status_code, 200, response.content)
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.status, 'confirmed')


# -- independent review of 5317114..2874caf --------------------------------------------

class PosAnnulAfterManualReturnTest(_UxBase):
    """P2: a manual return after the till sale must not put the same goods back twice."""

    def setUp(self):
        super().setUp()
        self.sale = self.till_sale(2)                            # shelf 20 → 18
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.cable.pk, movement_type=StockMovement.RETURN_ENTRY,
            quantity=1, reason='Devolución en mostrador', actor=self.manager)   # → 19

    def test_the_annulment_is_refused_and_says_why(self):
        response = self.post_cancel(self.sale, goods_returned=True)

        self.assertEqual((response.status_code, response.json()['code']), (409, 'manual_return_after_sale'))
        self.assertIn('Hay una devolución manual posterior de este producto', response.json()['detail'])
        self.assertIn('anula sin reponer stock o revisa el Kardex', response.json()['detail'])
        self.assertEqual(Order.objects.get(pk=self.sale.pk).status, Order.Status.PAID)
        self.assertEqual(self.cable_stock(), 19)

    def test_annulled_without_restoring_stock_it_is_recorded_and_audited(self):
        response = self.post_cancel(self.sale, goods_returned=True, restore_stock=False)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.cable_stock(), 19)
        self.assertFalse(StockMovement.objects.filter(order=self.sale, movement_type=RETURN).exists())
        cancellation = OrderCancellation.objects.get(order=self.sale)
        self.assertEqual((cancellation.stock_restored, cancellation.goods_returned), (False, True))
        audit = AdminAuditLog.objects.get(action='order_cancelled', target_id=str(self.sale.pk))
        self.assertEqual((audit.metadata['stock_restore_skipped'], audit.metadata['movements']), (True, []))

    def test_a_return_before_the_sale_does_not_block_it(self):
        other = self.till_sale(1)                               # after the manual return: 19 → 18
        self.assertEqual(self.post_cancel(other, goods_returned=True).status_code, 200)
        self.assertEqual(self.cable_stock(), 19)

    def test_only_a_literal_false_skips_the_stock_and_only_for_a_till_sale(self):
        refused = self.post_cancel(self.sale, goods_returned=True, restore_stock='false')
        self.assertEqual(refused.json()['code'], 'manual_return_after_sale')
        web = self.web_order()
        response = self.post_cancel(web, restore_stock=False)
        self.assertEqual((response.status_code, response.json()['code']), (400, 'restore_stock_not_allowed'))
        self.assertEqual(Order.objects.get(pk=web.pk).status, Order.Status.PAID)


class NonStringMethodTest(_UxBase):
    """P3: a method that is not text is a bad request, never a 500."""

    def test_cancel_and_refund_refuse_a_method_that_is_not_text(self):
        order = self.web_order()
        for value in (['cash'], {'m': 'cash'}, 3):
            with self.subTest(value=value):
                response = self.post_cancel(order, refund_method=value)
                self.assertEqual((response.status_code, response.json()['code']), (400, 'method_invalid'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PAID)
        self.post_cancel(order)
        for value in (['cash'], {'m': 'cash'}, 3):
            with self.subTest(value=value):
                response = self.post_refund(order, method=value)
                self.assertEqual((response.status_code, response.json()['code']), (400, 'method_invalid'))


class TestKeysAreNotMoneyTest(_UxBase):
    """P3: a charge filed as a duplicate with TEST keys is not refundable money."""

    def test_a_test_late_charge_raises_no_ceiling(self):
        order = self.unpaid_order()
        self.post_cancel(order)
        PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='LATE-T', order_number='LATE-T', amount=D('49.00'),
            currency='PEN', environment='test', status=PaymentTransaction.Status.DUPLICATE,
            confirmed_at=timezone.now())
        self.assertEqual(refunds.collected(order), D('0.00'))
        response = self.post_refund(order, method='cash')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'nothing_to_refund'))
        # A row older than the column (environment '') is counted, as reconciliation reads it.
        PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='LATE-L', order_number='LATE-L', amount=D('49.00'),
            currency='PEN', environment='', status=PaymentTransaction.Status.DUPLICATE,
            confirmed_at=timezone.now())
        self.assertEqual(refunds.collected(order), D('49.00'))


class RealTillSaleAnnulTest(_UxBase):
    """End to end: a sale rung up by `create_pos_sale`, annulled from the panel."""

    def test_annulling_a_real_till_sale(self):
        from store.models import Membership
        from store.sales_note_services import get_or_create_sales_note
        from store.tests import _c1_sale

        funda = _prod(self.company, 'Funda', 'funda-pos-real', price='30.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=funda.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=10, reason='Compra', unit_cost=D('12'))
        [device] = self.receive(self.row('SER-POS-1', None, cost='3000.00'), product=self.laptop)
        seller, _ = _p2d_member(self.company, 'cajera_real', ['company.view', 'sales.pos.use'])
        Membership.objects.filter(user=seller, company=self.company).update(commission_rate_percent=D('2.00'))
        documents = FiscalDocument.objects.count()

        sale, created = _c1_sale(actor=seller, company=self.company, branch=self.branch_a, items=[
            {'product': funda.pk, 'quantity': 2},
            {'product': self.laptop.pk, 'quantity': 1, 'units': [{'id': device.pk, 'method': 'manual'}]},
        ])
        self.assertTrue(created)
        commission = SalesCommission.objects.get(order=sale)
        self.assertEqual(commission.status, SalesCommission.STATUS_ACCRUED)
        note, _ = get_or_create_sales_note(sale, actor=self.manager)
        device.refresh_from_db()
        self.assertEqual(device.status, StockUnit.Status.SOLD)
        funda_row = BranchStock.objects.get(branch=self.branch_a, product=funda)
        self.assertEqual(funda_row.quantity, 8)

        response = self.post_cancel(sale, goods_returned=True)

        self.assertEqual(response.status_code, 200, response.content)
        commission.refresh_from_db()
        note.refresh_from_db()
        device.refresh_from_db()
        self.assertEqual(commission.status, SalesCommission.STATUS_VOIDED)
        self.assertEqual(note.status, SalesNote.STATUS_VOID)
        self.assertEqual((device.status, device.order_id), (StockUnit.Status.AVAILABLE, None))
        after = BranchStock.objects.get(branch=self.branch_a, product=funda)
        self.assertEqual((after.quantity, after.average_cost, after.uncosted_quantity),
                         (10, funda_row.average_cost, 0))
        backs = {m.product_id: m for m in StockMovement.objects.filter(order=sale, movement_type=RETURN)}
        self.assertEqual(set(backs), {funda.pk, self.laptop.pk})
        self.assertEqual(backs[funda.pk].unit_cost, D('12.0000'))
        self.assertEqual(backs[self.laptop.pk].unit_cost, D('3000.0000'))
        self.assertEqual(FiscalDocument.objects.count(), documents)
        self.assertEqual(Order.objects.get(pk=sale.pk).status, Order.Status.CANCELLED)
        again = self.post_cancel(sale, goods_returned=True)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(StockMovement.objects.filter(order=sale, movement_type=RETURN).count(), 2)
