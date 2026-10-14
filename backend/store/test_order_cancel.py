"""
ORDER-CANCEL-01 · cancelling a PAID online order from the admin panel.

The owner's rules (2026-10-14):

  D1  Only an online order that is PAID and whose goods have NOT left the shop
      (pending, confirmed, preparing, ready for pickup). Sent or delivered is a
      return, a later flow. A till sale and an unpaid order are not this.
  D2  The stock comes back, once: every sale exit of the order returns with its
      own Kardex line into the same branch, and a serialized device goes back
      to the shelf and stops being the device of that order.
  D3  `sales.orders.manage`, scoped like every order view. No new capability.
  D4  A live boleta/factura is kept untouched and the cancellation says that a
      credit note / void is pending. Nothing fiscal is issued automatically.

And what it is NOT: cancelling is not refunding. `paid` stays true, no payment
row changes and no gateway is called. A payment that arrives for a cancelled
order is filed as evidence — never as a payment of it.
"""
from decimal import Decimal
from threading import Barrier, Thread
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import checkout_services as checkout
from store import inventory_services as inventory
from store import order_cancellation as cancelling
from store import order_fulfillment_services as fulfillment
from store import unit_identification as picking
from store.models import (
    AdminAuditLog, BranchStock, Company, FiscalDocument, FiscalDocumentType, FiscalEnvironment,
    FiscalSeries, Order, OrderCancellation, OrderItem, OrderUnit, PaymentTransaction, Product,
    SalesNote, StockMovement, StockUnit,
)
from store.test_document_design import text_of
from store.test_payment_hardening import Base as PaymentBase
from store.test_stock_units import IMEI_A, IMEI_B, UnitsBase
from store.tests import _m7_stock, _p2d_member, _prod

REASON = 'El cliente desistió de la compra'
RETURN = 'order_cancel_return'


class _Base(UnitsBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.manager, _ = _p2d_member(
            self.company, 'pedidos_cancel',
            ['company.view', 'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage'])
        self.cable = _prod(self.company, 'Cable USB-C', 'cable-usb-c-cancel', price='49.00', inventory=0)
        _m7_stock(self.branch_a, self.cable, 20)
        self.desk = APIClient()
        self.desk.force_authenticate(self.manager)
        self.q = f'company={self.company.pk}'

    def web_order(self, *lines, fulfillment_status='pending', **fields):
        """A paid storefront order whose sale exits went through the Kardex."""
        lines = lines or ((self.cable, 3),)
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch_a, customer_name='Rosa Quispe',
            customer_email='rosa@example.com', total=sum(p.price * q for p, q in lines),
            status=Order.Status.PAID, paid=True, paid_at=timezone.now(), **fields)
        for product, quantity in lines:
            OrderItem.objects.create(order=order, product=product, quantity=quantity, price=product.price)
        inventory.record_sale_stock_movements(order)
        if fulfillment_status != 'pending':
            Order.objects.filter(pk=order.pk).update(fulfillment_status=fulfillment_status)
        order.refresh_from_db()
        return order

    def post_cancel(self, order, reason=REASON, client=None):
        return (client or self.desk).post(
            f'/api/admin/orders/{order.pk}/cancel/?{self.q}', {'reason': reason}, format='json')

    def detail(self, order, client=None):
        return (client or self.desk).get(f'/api/admin/orders/{order.pk}/?{self.q}').json()

    def cable_stock(self):
        return inventory.branch_quantity(self.branch_a, self.cable)

    def returns(self, order=None):
        movements = StockMovement.objects.filter(movement_type=RETURN)
        return movements.filter(order=order) if order is not None else movements


# -- D1: which orders ----------------------------------------------------------------

class WhichOrdersTest(_Base):
    def test_a_paid_order_whose_goods_have_not_left_is_cancelled(self):
        for state in ('pending', 'confirmed', 'preparing', 'ready_for_pickup'):
            with self.subTest(state):
                order = self.web_order(fulfillment_status=state)

                response = self.post_cancel(order)

                self.assertEqual(response.status_code, 200, response.content)
                order.refresh_from_db()
                self.assertEqual(order.status, Order.Status.CANCELLED)
                self.assertEqual(order.fulfillment_status, Order.FulfillmentStatus.CANCELLED)
                self.assertTrue(order.paid)                              # the money was taken
                self.assertEqual(response.json()['cancellation']['previous_fulfillment_status'], state)

    def test_an_order_set_aside_as_cancelado_operativo_can_still_be_cancelled(self):
        """The old «Cancelado operativo» moved nothing: its goods never left."""
        order = self.web_order(fulfillment_status='cancelled')
        self.assertEqual(self.post_cancel(order).status_code, 200)
        self.assertEqual(self.cable_stock(), 20)

    def test_an_order_whose_goods_left_needs_a_return_not_a_cancellation(self):
        for state in ('shipped', 'delivered'):
            with self.subTest(state):
                order = self.web_order(fulfillment_status=state)

                response = self.post_cancel(order)

                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(response.json()['code'], 'goods_left')
                self.assertIn('requiere devolución', response.json()['detail'])
                order.refresh_from_db()
                self.assertEqual((order.status, order.fulfillment_status), ('paid', state))
                self.assertFalse(self.returns(order).exists())

    def test_an_unpaid_order_is_not_this_flow(self):
        for status in ('pending_payment', 'failed'):
            with self.subTest(status):
                order = Order.objects.create(
                    company=self.company, fulfillment_branch=self.branch_a, customer_email='x@example.com',
                    total=Decimal('49.00'), status=status, paid=False)
                response = self.post_cancel(order)
                self.assertEqual((response.status_code, response.json()['code']), (409, 'not_paid'))
                order.refresh_from_db()
                self.assertEqual(order.status, status)

    def test_a_till_sale_is_not_cancelled_here(self):
        order = self.web_order(sales_channel='pos')

        response = self.post_cancel(order)

        self.assertEqual((response.status_code, response.json()['code']), (409, 'pos_sale'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')

    def test_another_company_or_a_branch_out_of_reach_answers_like_nothing(self):
        order = self.web_order()
        foreign, _ = _p2d_member(self.other, 'pedidos_ajenos', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        north, _ = _p2d_member(self.company, 'pedidos_norte', ['company.view', 'sales.orders.view', 'sales.orders.manage'],
                               branches=[self.branch_b])
        for user, query in ((foreign, f'company={self.other.pk}'), (north, self.q)):
            with self.subTest(user.username):
                client = APIClient()
                client.force_authenticate(user)
                response = client.post(f'/api/admin/orders/{order.pk}/cancel/?{query}', {'reason': REASON}, format='json')
                self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')

    def test_reading_orders_is_not_enough(self):
        order = self.web_order()
        reader, _ = _p2d_member(self.company, 'lectura_cancel', ['company.view', 'sales.orders.view'])
        client = APIClient()
        client.force_authenticate(reader)

        self.assertEqual(self.post_cancel(order, client=client).status_code, 403)
        self.assertFalse(self.detail(order, client=client)['can_cancel'])
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')
        # Why it could not be cancelled is said to whoever could cancel it.
        sent = self.web_order(fulfillment_status='shipped')
        self.assertEqual(self.detail(sent, client=client)['cancel_blockers'], [])
        self.assertEqual([b['code'] for b in self.detail(sent)['cancel_blockers']], ['goods_left'])

    def test_the_reason_is_required_and_bounded(self):
        order = self.web_order()
        for reason, code in (('', 'reason_required'), ('    ', 'reason_required'), ('  abcd  ', 'reason_required'),
                             ('x' * 501, 'reason_too_long'), (None, 'reason_required')):
            with self.subTest(reason=reason):
                response = self.post_cancel(order, reason=reason)
                self.assertEqual((response.status_code, response.json()['code']), (400, code), response.content)
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')

        response = self.post_cancel(order, reason='  Error  ')                  # five characters, trimmed
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(OrderCancellation.objects.get(order=order).reason, 'Error')


class GoodsThatLeftTest(_Base):
    """
    «Has not left» is about the order's history, not its current label. Moving
    an order back from sent or delivered does not bring the goods back.
    """

    def move(self, order, *states):
        for state in states:
            fulfillment.change_fulfillment_status(
                order=Order.objects.get(pk=order.pk), new_status=state, actor=self.manager, company=self.company)

    def test_a_device_that_left_does_not_come_back_by_moving_the_order_backwards(self):
        self.receive(self.row('F2LXK1ONE01', IMEI_A), self.row('F2LXK1TWO02', IMEI_B))
        order = self.web_order((self.phone, 1))
        picking.identify_for_order(order=order, code=IMEI_A, method='scanner', actor=self.manager)
        self.move(order, 'delivered', 'preparing')
        unit = StockUnit.objects.get(order=order)

        response = self.post_cancel(order)

        self.assertEqual((response.status_code, response.json()['code']), (409, 'goods_left'), response.content)
        unit.refresh_from_db()
        self.assertEqual((unit.status, unit.order_id), ('sold', order.pk))
        self.assertEqual(self.quantity(), 1)
        self.assertTrue(OrderUnit.objects.get(order=order).left_at)
        self.assertFalse(self.returns(order).exists())
        body = self.detail(order)
        self.assertFalse(body['can_cancel'])
        self.assertEqual([b['code'] for b in body['cancel_blockers']], ['goods_left'])

    def test_a_device_stamped_as_gone_is_enough_even_without_the_audit_trail(self):
        """A device that left before moves were audited (migration 0116 copied those) still left."""
        self.receive(self.row('F2LXK1ONE01', IMEI_A))
        order = self.web_order((self.phone, 1))
        picking.identify_for_order(order=order, code=IMEI_A, method='scanner', actor=self.manager)
        OrderUnit.objects.filter(order=order).update(left_at=timezone.now())
        self.assertFalse(AdminAuditLog.objects.filter(action='order_fulfillment_status_changed').exists())

        self.assertEqual(self.post_cancel(order).json()['code'], 'goods_left')
        self.assertEqual(self.quantity(), 0)

    def test_a_delivered_order_relabelled_cancelado_operativo_is_not_cancellable(self):
        order = self.web_order((self.cable, 3))
        self.move(order, 'delivered', 'cancelled')

        response = self.post_cancel(order)

        self.assertEqual((response.status_code, response.json()['code']), (409, 'goods_left'))
        self.assertEqual(self.cable_stock(), 17)
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')

    def test_a_sent_order_moved_back_is_not_cancellable_either(self):
        order = self.web_order((self.cable, 3))
        self.move(order, 'shipped', 'pending')
        self.assertEqual(self.post_cancel(order).json()['code'], 'goods_left')
        self.assertEqual(self.cable_stock(), 17)

    def test_an_order_moved_back_after_ready_for_pickup_only_can_still_be_cancelled(self):
        order = self.web_order((self.cable, 3))
        self.move(order, 'ready_for_pickup', 'pending', 'cancelled')

        self.assertEqual(self.post_cancel(order).status_code, 200)
        self.assertEqual(self.cable_stock(), 20)

    def test_a_shipping_date_written_on_the_delivery_counts_as_having_left(self):
        from store.models import OrderDelivery

        order = self.web_order((self.cable, 3))
        OrderDelivery.objects.update_or_create(
            order=order, defaults={'company': self.company, 'shipped_at': timezone.now()})

        self.assertEqual(self.post_cancel(order).json()['code'], 'goods_left')
        self.assertEqual(self.cable_stock(), 17)


# -- D2: the stock ------------------------------------------------------------------

class StockComesBackOnceTest(_Base):
    def test_each_sale_exit_comes_back_once_into_the_same_branch(self):
        order = self.web_order((self.cable, 3))
        self.assertEqual(self.cable_stock(), 17)
        sale_exit = StockMovement.objects.get(order=order, movement_type='sale_exit')

        self.assertEqual(self.post_cancel(order).status_code, 200)

        self.assertEqual(self.cable_stock(), 20)
        back = self.returns(order).get()
        self.assertEqual((back.branch, back.product, back.quantity), (self.branch_a, self.cable, 3))
        self.assertEqual((back.stock_before, back.stock_after), (17, 20))
        self.assertEqual(back.actor, self.manager)
        self.assertEqual(back.metadata['sale_exit_id'], sale_exit.pk)
        self.assertTrue(back.is_entry)
        self.assertEqual(back.get_movement_type_display(), 'Devolución por pedido cancelado')

        again = self.post_cancel(order)

        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_cancelled'))
        self.assertIn('ya está cancelado', again.json()['detail'])
        self.assertEqual(self.cable_stock(), 20)
        self.assertEqual(self.returns(order).count(), 1)
        self.assertEqual(OrderCancellation.objects.filter(order=order).count(), 1)

    def test_a_device_goes_back_to_the_shelf_and_stops_being_this_orders(self):
        self.receive(self.row('F2LXK1ONE01', IMEI_A), self.row('F2LXK1TWO02', IMEI_B))
        order = self.web_order((self.phone, 1), (self.cable, 1))
        picking.identify_for_order(order=order, code=IMEI_B, method='scanner', actor=self.manager)
        sold = StockUnit.objects.get(order=order)
        self.assertEqual((sold.serial_number, sold.status), ('F2LXK1TWO02', 'sold'))
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 1)
        self.assertEqual(self.quantity(), 1)

        self.assertEqual(self.post_cancel(order).status_code, 200)

        sold.refresh_from_db()
        self.assertEqual((sold.status, sold.order_id, sold.sold_at), ('available', None, None))
        self.assertFalse(OrderUnit.objects.filter(order=order).exists())
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()
        device_line = self.returns(order).get(product=self.phone)
        self.assertEqual((device_line.quantity, device_line.metadata['unit_ids']), (1, [sold.pk]))
        self.assertEqual(self.returns(order).get(product=self.cable).quantity, 1)
        self.assertEqual(self.cable_stock(), 20)

    def test_a_device_given_back_before_the_cancellation_is_not_restored_twice(self):
        from store import stock_unit_services as units

        self.receive(self.row('F2LXK1ONE01', IMEI_A), self.row('F2LXK1TWO02', IMEI_B))
        order = self.web_order((self.phone, 2))
        first, second = StockUnit.objects.filter(order=order).order_by('pk')
        units.return_unit(first, reason='Vuelve antes de entregar', actor=self.manager)
        self.assertEqual(self.quantity(), 1)

        response = self.post_cancel(order)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.quantity(), 2)
        self.assert_invariant()
        back = self.returns(order).get()
        self.assertEqual(back.metadata['unit_ids'], [second.pk])
        second.refresh_from_db()
        self.assertEqual((second.status, second.order_id), ('available', None))
        audit = AdminAuditLog.objects.get(action='order_cancelled')
        self.assertEqual(audit.metadata['units_returned'], [second.pk])
        self.assertEqual(audit.metadata['units_already_returned'], [first.pk])

    def test_a_line_that_was_short_at_payment_has_nothing_to_give_back(self):
        _m7_stock(self.branch_a, self.cable, 1)
        order = self.web_order((self.cable, 3))                       # the exit was skipped: shortfall
        self.assertFalse(StockMovement.objects.filter(order=order, movement_type='sale_exit').exists())

        self.assertEqual(self.post_cancel(order).status_code, 200)

        self.assertFalse(self.returns(order).exists())
        self.assertEqual(self.cable_stock(), 1)
        self.assertFalse(OrderCancellation.objects.get(order=order).stock_restored)


# -- the money is not touched ------------------------------------------------------

class CancellingIsNotRefundingTest(_Base):
    def test_paid_stays_and_no_payment_row_changes(self):
        order = self.web_order()
        attempt = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='T0001', order_number='T0001',
            amount=order.total, currency='PEN', status=PaymentTransaction.Status.AUTHORIZED,
            environment='test')
        before = PaymentTransaction.objects.filter(order=order).values().get()
        paid_at = order.paid_at

        self.assertEqual(self.post_cancel(order).status_code, 200)

        order.refresh_from_db()
        self.assertTrue(order.paid)
        self.assertEqual(order.paid_at, paid_at)
        self.assertEqual(PaymentTransaction.objects.filter(order=order).values().get(), before)
        self.assertEqual(attempt.status, PaymentTransaction.Status.AUTHORIZED)

    def test_marking_a_payment_failure_leaves_a_cancelled_order_alone(self):
        order = self.web_order()
        self.post_cancel(order)

        checkout.mark_payment_failure(order, 'la pasarela no respondió')

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.payment_error, '')

    def test_the_stock_exit_cannot_be_reprocessed_on_a_cancelled_order(self):
        order = self.web_order()
        self.post_cancel(order)
        with self.assertRaises(inventory.InventoryError):
            inventory.reprocess_order_stock_exit(order, actor=self.manager)


# -- the fulfilment cannot move afterwards -----------------------------------------

class AFulfilmentChangeAfterwardsTest(_Base):
    def test_a_cancelled_order_cannot_be_sent_or_moved(self):
        order = self.web_order()
        self.post_cancel(order)
        for state in ('shipped', 'delivered', 'preparing', 'pending'):
            with self.subTest(state):
                with self.assertRaises(fulfillment.FulfillmentBlocked) as blocked:
                    fulfillment.change_fulfillment_status(
                        order=Order.objects.get(pk=order.pk), new_status=state, actor=self.manager,
                        company=self.company)
                self.assertEqual(blocked.exception.code, 'order_cancelled')

        response = self.desk.patch(f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}',
                                   {'fulfillment_status': 'shipped'}, format='json')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'order_cancelled'))
        self.assertEqual(Order.objects.get(pk=order.pk).fulfillment_status, 'cancelled')

    def test_a_stale_object_is_re_read_under_the_lock(self):
        order = self.web_order()
        stale = Order.objects.get(pk=order.pk)                        # read before the cancellation
        self.post_cancel(order)

        with self.assertRaises(fulfillment.FulfillmentBlocked):
            fulfillment.change_fulfillment_status(
                order=stale, new_status='shipped', actor=self.manager, company=self.company)
        self.assertEqual(Order.objects.get(pk=order.pk).fulfillment_status, 'cancelled')


# -- D4 and the internal note --------------------------------------------------------

class PapersTest(_Base):
    def fiscal_document(self, order, status='accepted'):
        series = FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        return FiscalDocument.objects.create(
            order=order, company=self.company, series_ref=series, document_type=FiscalDocumentType.RECEIPT,
            series='B001', number=12, issued_at=timezone.now(), environment=FiscalEnvironment.BETA,
            issuer_tax_id='20800000001', issuer_legal_name='Inventario S.A.C.', customer_doc_type='1',
            customer_doc_number='40404040', customer_legal_name='ROSA QUISPE', currency='PEN',
            taxable_amount=Decimal('124.58'), tax_amount=Decimal('22.42'), total=Decimal('147.00'),
            tax_rate=Decimal('0.18'), status=status, signed_xml='<Invoice/>')

    def test_a_live_boleta_is_kept_and_the_cancellation_says_a_credit_note_is_pending(self):
        order = self.web_order()
        document = self.fiscal_document(order)
        self.assertIn('B001-12', self.detail(order)['cancel_fiscal_document'])     # the dialog warns first
        before = FiscalDocument.objects.filter(pk=document.pk).values().get()
        documents = FiscalDocument.objects.count()

        body = self.post_cancel(order).json()

        self.assertEqual(FiscalDocument.objects.filter(pk=document.pk).values().get(), before)
        self.assertEqual(FiscalDocument.objects.count(), documents)          # no credit note, no void issued
        cancellation = OrderCancellation.objects.get(order=order)
        self.assertTrue(cancellation.fiscal_pending)
        self.assertIn('B001-12', cancellation.fiscal_document)
        self.assertTrue(body['cancellation']['fiscal_pending'])
        self.assertIn('B001-12', body['cancellation']['fiscal_document'])
        audit = AdminAuditLog.objects.get(action='order_cancelled')
        self.assertTrue(audit.metadata['fiscal_pending'])
        self.assertIn('B001-12', audit.metadata['fiscal_document'])

    def test_a_boleta_is_not_issued_for_an_order_cancelled_meanwhile(self):
        from store.fiscal_services import FiscalError, get_or_create_fiscal_document

        order = self.web_order(
            receipt_type='boleta', document_type='dni', document_number='40404040',
            subtotal_amount=Decimal('147.00'), taxable_amount=Decimal('124.58'), tax_amount=Decimal('22.42'),
            tax_rate=Decimal('0.18'), tax_treatment='taxed', currency='PEN')
        series = FiscalSeries.objects.create(
            company=self.company, document_type=FiscalDocumentType.RECEIPT, series='B001')
        stale = Order.objects.get(pk=order.pk)                          # read before the cancellation
        self.post_cancel(order)

        with self.assertRaises(FiscalError) as refused:
            get_or_create_fiscal_document(stale)

        self.assertIn('cancelado', str(refused.exception))
        self.assertFalse(FiscalDocument.objects.filter(order=order).exists())
        series.refresh_from_db()
        self.assertEqual(series.next_number, 1)                                    # no number spent

    def test_a_sales_note_is_not_issued_for_an_order_cancelled_meanwhile(self):
        from store.models import InternalSequence
        from store.sales_note_services import SalesNoteError, get_or_create_sales_note

        order = self.web_order()
        stale = Order.objects.get(pk=order.pk)
        self.post_cancel(order)
        sequences = list(InternalSequence.objects.values())

        with self.assertRaises(SalesNoteError) as refused:
            get_or_create_sales_note(stale, actor=self.manager)

        self.assertIn('cancelado', str(refused.exception))
        self.assertFalse(SalesNote.objects.filter(order=order).exists())
        self.assertEqual(list(InternalSequence.objects.values()), sequences)            # no NV- number spent

    def test_a_rejected_document_leaves_nothing_pending(self):
        order = self.web_order()
        self.fiscal_document(order, status='rejected')
        self.post_cancel(order)
        cancellation = OrderCancellation.objects.get(order=order)
        self.assertFalse(cancellation.fiscal_pending)
        self.assertEqual(cancellation.fiscal_document, '')

    def test_the_internal_note_is_voided_kept_and_its_papers_say_so(self):
        from store.sales_note_services import generate_sales_note_pdf, get_or_create_sales_note
        from store.ticket_services import generate_sales_note_ticket_pdf

        order = self.web_order()
        note, _ = get_or_create_sales_note(order, actor=self.manager)
        self.assertNotIn('ANULAD', text_of(generate_sales_note_pdf(note)))

        self.post_cancel(order)

        note.refresh_from_db()
        self.assertEqual(note.status, SalesNote.STATUS_VOID)
        self.assertEqual(SalesNote.objects.filter(order=order).count(), 1)
        self.assertTrue(OrderCancellation.objects.get(order=order).sales_note_voided)
        a4 = text_of(generate_sales_note_pdf(note))
        self.assertIn('NOTA DE VENTA', a4)
        self.assertIn('ANULADA', a4)
        self.assertIn(note.number, a4)
        self.assertIn('ANULADA', text_of(generate_sales_note_ticket_pdf(note)))
        for formato in ('a4', 'ticket80'):
            response = self.desk.get(f'/api/admin/orders/{order.pk}/sales-note/pdf/?{self.q}&formato={formato}')
            self.assertEqual(response.status_code, 200, response.content)

    def test_the_order_receipt_stays_downloadable_with_the_mark(self):
        from store.pdf_services import generate_order_receipt_pdf

        order = self.web_order()
        self.assertNotIn('ANULADO', text_of(generate_order_receipt_pdf(order)))
        self.post_cancel(order)
        order.refresh_from_db()

        response = self.desk.get(f'/api/admin/orders/{order.pk}/receipt-pdf/?{self.q}')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('PEDIDO ANULADO', text_of(response.content))

    def test_the_thermal_queue_does_not_print_a_cancelled_order(self):
        from store.models import Printer
        from store.printing import services as printing
        from store.sales_note_services import get_or_create_sales_note

        order = self.web_order()
        get_or_create_sales_note(order, actor=self.manager)
        printer = Printer.objects.create(company=self.company, branch=self.branch_a, name='Caja', host='192.168.1.9')
        self.post_cancel(order)
        order.refresh_from_db()

        with self.assertRaises(printing.PrintError) as refused:
            printing.enqueue_manual(order=order, printer=printer, key='reimpresion-1', actor=self.manager)
        self.assertIn('cancelado', str(refused.exception))


# -- what the panel is told ----------------------------------------------------------

class PanelAndAuditTest(_Base):
    def test_the_detail_says_whether_it_can_be_cancelled_and_why_not(self):
        waiting = self.web_order(fulfillment_status='preparing')
        sent = self.web_order(fulfillment_status='shipped')

        body = self.detail(waiting)
        self.assertTrue(body['can_cancel'])
        self.assertEqual(body['cancel_blockers'], [])
        self.assertIsNone(body['cancellation'])
        self.assertEqual(body['cancel_fiscal_document'], '')

        body = self.detail(sent)
        self.assertFalse(body['can_cancel'])
        self.assertEqual([b['code'] for b in body['cancel_blockers']], ['goods_left'])

    def test_after_the_cancellation_the_detail_shows_it(self):
        from store.sales_note_services import get_or_create_sales_note

        order = self.web_order(fulfillment_status='preparing')
        get_or_create_sales_note(order, actor=self.manager)

        response = self.post_cancel(order)

        self.assertEqual(response.status_code, 200)
        body = self.detail(order)
        self.assertEqual(response.json()['cancellation'], body['cancellation'])
        block = body['cancellation']
        self.assertEqual(block['reason'], REASON)
        self.assertEqual(block['cancelled_by'], self.manager.username)
        self.assertEqual((block['previous_status'], block['previous_fulfillment_status']), ('paid', 'preparing'))
        self.assertTrue(block['stock_restored'])
        self.assertFalse(block['fiscal_pending'])
        self.assertEqual(block['fiscal_document'], '')
        self.assertTrue(block['sales_note_voided'])
        self.assertTrue(block['cancelled_at'])
        self.assertFalse(body['can_cancel'])
        self.assertEqual([b['code'] for b in body['cancel_blockers']], ['already_cancelled'])
        self.assertEqual(body['available_fulfillment_transitions'], [])
        self.assertEqual((body['status'], body['paid']), ('cancelled', True))

    def test_the_audit_tells_who_when_why_and_what_moved(self):
        self.receive(self.row('F2LXK1ONE01', IMEI_A))
        order = self.web_order((self.phone, 1), (self.cable, 2), fulfillment_status='ready_for_pickup')
        unit = StockUnit.objects.get(order=order)

        self.post_cancel(order)

        audit = AdminAuditLog.objects.get(action='order_cancelled')
        self.assertEqual((audit.actor, audit.company, audit.target_type, audit.target_id),
                         (self.manager, self.company, 'order', str(order.pk)))
        meta = audit.metadata
        self.assertEqual(meta['reason'], REASON)
        self.assertEqual(meta['branch_id'], self.branch_a.pk)
        self.assertEqual((meta['previous_status'], meta['new_status']), ('paid', 'cancelled'))
        self.assertEqual((meta['previous_fulfillment_status'], meta['new_fulfillment_status']),
                         ('ready_for_pickup', 'cancelled'))
        self.assertEqual(sorted(meta['movements']), sorted(self.returns(order).values_list('pk', flat=True)))
        self.assertEqual(len(meta['movements']), 2)
        self.assertEqual(meta['units_returned'], [unit.pk])
        self.assertFalse(meta['fiscal_pending'])
        self.assertFalse(meta['sales_note_voided'])
        self.assertTrue(meta['paid_kept'])

    def test_a_refusal_writes_nothing(self):
        order = self.web_order(fulfillment_status='delivered')
        self.post_cancel(order)
        self.assertFalse(AdminAuditLog.objects.filter(action='order_cancelled').exists())
        self.assertFalse(OrderCancellation.objects.exists())


# -- the pilot's legacy bridge ------------------------------------------------------

class LegacyBridgeTest(TestCase):
    """On the pilot, without a Membership, the legacy role decides: warehouse staff do not cancel sales."""

    def setUp(self):
        from store.tests import _p60_paid_order, _p60_product, _p60_users

        cache.clear()
        self.users = _p60_users()
        product = _p60_product(name='Cable legacy', inventory=10, price='49.00')
        self.orders = []
        for _ in range(4):
            order = _p60_paid_order(product, quantity=1)
            inventory.record_sale_stock_movements(order)
            self.orders.append(order)

    def cancel(self, role, order):
        client = APIClient()
        client.force_authenticate(self.users[role])
        return client.post(f'/api/admin/orders/{order.pk}/cancel/', {'reason': REASON}, format='json')

    def test_inventory_and_customer_cannot_cancel(self):
        for role, order in (('inventory', self.orders[0]), ('customer', self.orders[1])):
            with self.subTest(role):
                self.assertEqual(self.cancel(role, order).status_code, 403)
                self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')

    def test_sales_and_admin_can(self):
        for role, order in (('sales', self.orders[2]), ('admin', self.orders[3])):
            with self.subTest(role):
                response = self.cancel(role, order)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(Order.objects.get(pk=order.pk).status, 'cancelled')


# -- a payment that arrives afterwards ---------------------------------------------

def _late_charge(attempt, unique='d' * 32):
    """The payment pipeline's own decision for an authorised charge of `attempt`."""
    from store import views

    result = SimpleNamespace(signed=True, response_code='00', pay_method='CARD', authorization_code='A1',
                             reference_number='R1', unique_id=unique)
    return views.IzipayNotificationView()._confirm(PaymentTransaction.objects.get(pk=attempt.pk), result)


class ALateChargeInEitherOrderTest(_Base):
    """The two orders of arrival of a cancellation and a second charge, each pinned."""

    def setUp(self):
        super().setUp()
        self.order = self.web_order()
        self.attempt = PaymentTransaction.objects.create(
            order=self.order, provider='izipay', transaction_id='LATE0002', order_number='LATE0002',
            amount=self.order.total, currency='PEN', environment='test')

    def test_cancelled_first_the_charge_is_filed_as_money_to_give_back_for_a_cancelled_order(self):
        self.post_cancel(self.order)

        with self.assertLogs('store.views', level='ERROR') as logs:
            self.assertEqual(_late_charge(self.attempt), 'duplicate')

        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.DUPLICATE)
        self.assertIn('cancelado', self.attempt.failure_reason)
        self.assertEqual(AdminAuditLog.objects.get(action='payment_duplicate').metadata['order_status'], 'cancelled')
        text = '\n'.join(logs.output)
        self.assertIn('cancelado', text)
        self.assertNotIn('ya estaba pagado', text)
        self.assertEqual((Order.objects.get(pk=self.order.pk).status, self.returns(self.order).count()),
                         ('cancelled', 1))

    def test_charged_first_it_is_a_second_charge_and_the_cancellation_still_happens_once(self):
        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(_late_charge(self.attempt), 'duplicate')
        self.attempt.refresh_from_db()
        self.assertIn('ya estaba pagado', self.attempt.failure_reason)
        self.assertNotIn('order_status', AdminAuditLog.objects.get(action='payment_duplicate').metadata)

        self.assertEqual(self.post_cancel(self.order).status_code, 200)

        self.assertEqual((Order.objects.get(pk=self.order.pk).status, self.returns(self.order).count()),
                         ('cancelled', 1))
        self.assertEqual(self.cable_stock(), 20)



class APaymentAfterTheCancellationTest(PaymentBase):
    """A second charge for a cancelled order is evidence to give back, never a payment of it."""

    def setUp(self):
        super().setUp()
        self.second = self.another_attempt()
        self.assertEqual(self.notify().status_code, 200)              # the order is paid
        self.refresh()
        self.assertEqual(self.order.status, Order.Status.PAID)
        operator = get_user_model().objects.create_user(username='cancela_pago', password='Pass123!')
        cancelling.cancel_paid_order(self.order, operator, REASON)
        self.refresh()
        self.stock_after_cancel = self.stock()

    def assert_still_cancelled(self):
        self.refresh()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertTrue(self.order.paid)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)
        self.assertEqual(self.stock(), self.stock_after_cancel)

    def assert_filed_as_evidence(self, source):
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, PaymentTransaction.Status.DUPLICATE)
        self.assertIn('cancelado', self.second.failure_reason)
        log = self.audits('payment_duplicate').get()
        self.assertEqual(log.metadata['order_status'], 'cancelled')
        self.assertEqual(log.metadata['source'], source)

    def test_a_late_notification_is_filed_and_the_order_stays_cancelled(self):
        with self.assertLogs('store.views', level='ERROR'):
            response = self.notify(order_id=self.second.transaction_id, uuid='c' * 32)

        self.assertEqual(response.status_code, 200)
        self.assert_still_cancelled()
        self.assert_filed_as_evidence('notification')

    def test_a_charge_found_by_asking_is_filed_and_the_order_stays_cancelled(self):
        self.fake.hold(self.second.transaction_id, self.paid(order_id=self.second.transaction_id, uuid='c' * 32))

        with self.assertLogs('store.views', level='ERROR'):
            summary = self.ask()

        self.assertEqual((summary.paid, summary.duplicates), (0, 1))
        self.assert_still_cancelled()
        self.assert_filed_as_evidence('Order/Get')

    def test_the_first_payment_notified_again_changes_nothing(self):
        self.assertEqual(self.notify().status_code, 200)
        self.assert_still_cancelled()
        self.assertFalse(self.audits('payment_duplicate').exists())


# -- at the same instant -------------------------------------------------------------

class CancelAtTheSameInstantTest(TransactionTestCase):
    """Two connections, one order. The order row decides."""

    def setUp(self):
        from store.models import Branch

        self.company = Company.objects.create(
            name='Concurrente', legal_name='Concurrente SAC', tax_id='20911111112', slug='cancel-conc')
        self.branch = self.company.branches.first() or Branch.objects.create(company=self.company, name='Centro')
        self.user, _ = _p2d_member(
            self.company, 'cancela_conc', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        self.cable = Product.objects.create(
            company=self.company, name='Cable', slug='cable-conc', price=Decimal('10.00'))
        BranchStock.objects.update_or_create(branch=self.branch, product=self.cable, defaults={'quantity': 10})
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=Decimal('30.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now())
        OrderItem.objects.create(order=self.order, product=self.cable, quantity=3, price=Decimal('10.00'))
        inventory.record_sale_stock_movements(self.order)

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

    def cancel(self):
        return cancelling.cancel_paid_order(Order.objects.get(pk=self.order.pk), self.user, REASON)

    def stock(self):
        return BranchStock.objects.get(branch=self.branch, product=self.cable).quantity

    def test_two_cancellations_at_once_cancel_once(self):
        outcomes = self.race(('a', self.cancel), ('b', self.cancel))

        won = [o for o in outcomes.values() if isinstance(o, OrderCancellation)]
        lost = [o for o in outcomes.values() if isinstance(o, cancelling.OrderCancelError)]
        self.assertEqual((len(won), len(lost)), (1, 1), outcomes)
        self.assertEqual(lost[0].code, 'already_cancelled')
        self.assertEqual(OrderCancellation.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type=RETURN).count(), 1)
        self.assertEqual(self.stock(), 10)

    def test_a_cancellation_and_a_shipment_at_once_end_in_one_coherent_state(self):
        def ship():
            return fulfillment.change_fulfillment_status(
                order=Order.objects.get(pk=self.order.pk), new_status='shipped', actor=self.user,
                company=self.company)

        outcomes = self.race(('cancel', self.cancel), ('ship', ship))

        order = Order.objects.get(pk=self.order.pk)
        if order.status == Order.Status.CANCELLED:
            self.assertIsInstance(outcomes['cancel'], OrderCancellation, outcomes)
            self.assertIsInstance(outcomes['ship'], fulfillment.FulfillmentBlocked, outcomes)
            self.assertEqual(order.fulfillment_status, 'cancelled')
            self.assertEqual(self.stock(), 10)
        else:
            self.assertIsInstance(outcomes['cancel'], cancelling.OrderCancelError, outcomes)
            self.assertEqual(outcomes['cancel'].code, 'goods_left')
            self.assertEqual((order.status, order.fulfillment_status), ('paid', 'shipped'))
            self.assertEqual(self.stock(), 7)
            self.assertFalse(StockMovement.objects.filter(movement_type=RETURN).exists())

    def test_a_cancellation_and_a_late_charge_at_once_keep_the_order_cancelled(self):
        """
        Whichever commits first, the order ends cancelled and paid, restored
        exactly once, and the charge is ONE duplicate whose reason and audit
        agree about what the order was when it arrived. Each order of arrival
        is pinned deterministically in `ALateChargeInEitherOrderTest`.
        """
        from store import views

        attempt = PaymentTransaction.objects.create(
            order=self.order, provider='izipay', transaction_id='LATE0001', order_number='LATE0001',
            amount=Decimal('30.00'), currency='PEN', environment='test')
        result = SimpleNamespace(signed=True, response_code='00', pay_method='CARD', authorization_code='A1',
                                 reference_number='R1', unique_id='d' * 32)

        def charge():
            return views.IzipayNotificationView()._confirm(PaymentTransaction.objects.get(pk=attempt.pk), result)

        outcomes = self.race(('cancel', self.cancel), ('charge', charge))

        self.assertIsInstance(outcomes['cancel'], OrderCancellation, outcomes)
        self.assertEqual(outcomes['charge'], 'duplicate', outcomes)
        order = Order.objects.get(pk=self.order.pk)
        self.assertEqual((order.status, order.paid), ('cancelled', True))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentTransaction.Status.DUPLICATE)
        self.assertEqual(StockMovement.objects.filter(movement_type='sale_exit').count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type=RETURN).count(), 1)
        self.assertEqual(self.stock(), 10)
        filed = AdminAuditLog.objects.get(action='payment_duplicate')
        after_cancel = filed.metadata.get('order_status') == 'cancelled'
        self.assertEqual('cancelado' in attempt.failure_reason, after_cancel, attempt.failure_reason)
        self.assertEqual('ya estaba pagado' in attempt.failure_reason, not after_cancel)
