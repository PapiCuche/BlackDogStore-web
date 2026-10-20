"""
ORDER-RETURN-01 · what a closed return refunds when the sale had a commercial discount.

The order's `total` is what was collected, already net of its commercial
discount (`discount_amount`). A return refunds that total minus a deduction
made for the return itself — the commercial discount is never taken off twice.
"""
from decimal import Decimal as D

from store import order_refunds
from store.fulfillment_test_support import walk_to
from store.models import (
    Order, OrderRefund, OrderReturn, PaymentTransaction, StockMovement,
)
from store.test_order_returns import CUSTOMER_RETURN, _ReturnBase
from store.tests import _m7_stock, _prod


class CommercialDiscountTest(_ReturnBase):
    """A S/ 5,000 sale paid S/ 4,500 after a S/ 500 discount refunds S/ 4,500, never S/ 4,000."""

    def setUp(self):
        super().setUp()
        self.mac = _prod(self.company, 'MacBook PRUEBA', 'macbook-prueba-acc', price='5000.00', inventory=0)
        _m7_stock(self.branch_a, self.mac, 3)

    def discounted(self):
        order = self.web_order((self.mac, 1))
        Order.objects.filter(pk=order.pk).update(total=D('4500.00'), discount_amount=D('500.00'))
        order.refresh_from_db()
        walk_to(order, 'delivered', actor=self.manager, company=self.company)
        order.refresh_from_db()
        return order

    def receive_all(self, order):
        self.requested(order)
        item = order.items.get()
        r = self.post_receive(order, [{'order_item': item.pk, 'quantity': 1, 'condition': 'sellable'}])
        self.assertEqual(r.status_code, 200, r.content)

    def test_the_block_offers_what_was_collected_not_the_list_price(self):
        order = self.discounted()
        self.assertEqual(self.block(order)['paid_amount'], '4500.00')

    def test_no_deduction_refunds_what_was_paid(self):
        order = self.discounted()
        self.receive_all(order)
        self.assertEqual(self.post_close(order, refund_amount='4000.00').status_code, 400)   # discount twice: refused
        r = self.post_close(order, refund_amount='4500.00')
        self.assertEqual(r.status_code, 200, r.content)
        refund = OrderRefund.objects.get(order=order)
        self.assertEqual((refund.amount, refund.status), (D('4500.00'), 'pending'))
        made = OrderReturn.objects.get(order=order)
        self.assertEqual((made.paid_amount, made.deduction_amount, made.refund_amount),
                         (D('4500.00'), D('0.00'), D('4500.00')))
        summary = order_refunds.summary(order)
        self.assertEqual(summary['refundable'], D('0.00'))
        self.assertEqual(summary['confirmed'], D('0.00'))           # pending is not money given back

    def test_a_return_deduction_comes_off_what_was_paid_once(self):
        order = self.discounted()
        self.receive_all(order)
        r = self.post_close(order, refund_amount='4400.00', deduction_amount='100.00', deduction_reason='Flete de vuelta')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(OrderRefund.objects.get(order=order).amount, D('4400.00'))
        again = self.desk.post(f'/api/admin/orders/{order.pk}/refunds/?{self.q}',
                               {'amount': '100.00', 'method': 'cash'}, format='json')
        self.assertIn(again.status_code, (400, 409), again.content)
        self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)

    def test_a_deduction_above_what_was_paid_is_refused(self):
        order = self.discounted()
        self.receive_all(order)
        r = self.post_close(order, refund_amount='0.00', deduction_amount='4600.00', deduction_reason='No procede')
        self.assertIn(r.status_code, (400, 409), r.content)
        self.assertFalse(OrderRefund.objects.filter(order=order).exists())

    def test_reception_moves_stock_once_at_the_sale_cost(self):
        order = self.discounted()
        exit_ = StockMovement.objects.get(order=order, movement_type='sale_exit')
        self.receive_all(order)
        entry = StockMovement.objects.get(order=order, movement_type=CUSTOMER_RETURN)
        self.assertEqual((entry.quantity, entry.unit_cost), (1, exit_.unit_cost))
        item = order.items.get()
        dup = self.post_receive(order, [{'order_item': item.pk, 'quantity': 1, 'condition': 'sellable'}])
        self.assertEqual(dup.status_code, 409, dup.content)
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type=CUSTOMER_RETURN).count(), 1)

    def test_the_payment_row_is_kept_and_no_gateway_money_moves(self):
        order = self.discounted()
        pay = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='PRUEBA-T', order_number='PRUEBA-T', amount=D('4500.00'),
            currency='PEN', environment='test', status=PaymentTransaction.Status.AUTHORIZED)
        before = PaymentTransaction.objects.filter(pk=pay.pk).values().get()
        self.receive_all(order)
        self.post_close(order, refund_amount='4500.00')
        self.assertEqual(PaymentTransaction.objects.filter(pk=pay.pk).values().get(), before)
        self.assertEqual(PaymentTransaction.objects.filter(order=order).count(), 1)
