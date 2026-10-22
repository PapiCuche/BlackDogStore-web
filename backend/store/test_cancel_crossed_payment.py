"""
CANCEL-CROSSED-PAYMENT — a cancellation decided on an order the person saw
unpaid never goes through once the payment landed first: the order changed
under them (409 `order_changed`) and nothing is written. Before, it silently
became the cancellation of a paid order, with no refund decided.
"""
from decimal import Decimal as D

from store import order_cancellation as cancelling
from store.models import Order, OrderCancellation, OrderItem, PaymentTransaction
from store.test_order_cancel import REASON, _Base, _late_charge


class _Crossed(_Base):
    def unpaid_order(self):
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a, customer_email='x@example.com',
            total=D('147.00'), status=Order.Status.PENDING_PAYMENT, paid=False)
        OrderItem.objects.create(order=order, product=self.cable, quantity=3, price=self.cable.price)
        attempt = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='CROSS001', order_number='CROSS001',
            amount=order.total, currency='PEN', environment='test')
        return order, attempt

    def post(self, order, **body):
        return self.desk.post(f'/api/admin/orders/{order.pk}/cancel/?{self.q}', {'reason': REASON, **body},
                              format='json')


class CrossedPaymentServiceTest(_Crossed):
    def test_the_payment_landed_first_so_the_unpaid_cancellation_is_refused(self):
        order, attempt = self.unpaid_order()
        self.assertEqual(_late_charge(attempt), 'paid')
        stock = self.cable_stock()

        with self.assertRaises(cancelling.OrderCancelError) as caught:
            cancelling.cancel_order(order, self.manager, REASON, expected_mode='unpaid')

        self.assertEqual((caught.exception.code, caught.exception.http_status), ('order_changed', 409))
        order.refresh_from_db()
        self.assertEqual((order.status, order.paid, self.cable_stock()), (Order.Status.PAID, True, stock))
        self.assertFalse(OrderCancellation.objects.filter(order=order).exists())

    def test_the_mode_the_person_saw_still_holds_so_it_is_cancelled(self):
        order, _ = self.unpaid_order()
        cancelling.cancel_order(order, self.manager, REASON, expected_mode='unpaid')
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_without_an_expected_mode_nothing_changes_for_older_callers(self):
        order = self.web_order()
        cancelling.cancel_order(order, self.manager, REASON)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_an_unknown_mode_is_a_bad_request_and_writes_nothing(self):
        order, _ = self.unpaid_order()
        with self.assertRaises(cancelling.OrderCancelError) as caught:
            cancelling.cancel_order(order, self.manager, REASON, expected_mode='paid-ish')
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('expected_mode_invalid', 400))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)


class CrossedPaymentEndpointTest(_Crossed):
    def test_the_desk_that_saw_it_unpaid_gets_409_after_the_payment(self):
        order, attempt = self.unpaid_order()
        self.assertEqual(self.detail(order)['cancel_mode'], 'unpaid')
        self.assertEqual(_late_charge(attempt), 'paid')

        response = self.post(order, expected_mode='unpaid')

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'order_changed')
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.PAID)

    def test_the_desk_that_sees_it_paid_cancels_it_as_paid(self):
        order = self.web_order()
        response = self.post(order, expected_mode=self.detail(order)['cancel_mode'])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.CANCELLED)
