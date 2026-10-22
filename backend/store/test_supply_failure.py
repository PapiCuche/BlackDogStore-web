"""
SUPPLY-FAILURE · owner decision D3 (2026-10-21) — when the SUPPLIER fails an
on-order line, the offer's sellable availability does NOT grow.

  * «No se pudo abastecer» on a PAID order is a supplier failure: the line
    stops counting as committed AND the units leave the confirmed quantity, so
    what is left to sell (confirmed − committed, D1) stays the same.
  * An ordinary cancellation — the order is cancelled (ORDER-CANCEL-01), or a
    line of an order already refunded/cancelled by another road is released —
    gives the commitment back: the supplier still has those units.
  * A line released once is never released twice; a line «por confirmar»
    committed nothing and releases nothing; never negative, never confirmed <
    committed.
"""
from django.test import TestCase, TransactionTestCase

from store import order_cancellation as cancelling
from store import supply_services as supply
from store.models import AdminAuditLog, CartItem, Order, OrderItem, SupplierOffer
from store.test_supplier_offers import OfferFile, SupplyShop

REASON = 'El cliente desistió de la compra'


def counts(offer):
    offer.refresh_from_db()
    return offer.confirmed_quantity, offer.committed_quantity, offer.remaining


class FailureShop(SupplyShop):
    def on_order(self, quantity=2, session='carrito-a'):
        self.add_on_order(quantity=quantity, session=session)
        order = self.checkout(session)
        self.pay(order)
        return order


class SupplierFailureTest(FailureShop, TestCase):
    def setUp(self):
        self.build('d3-falla')                     # offer: 5 confirmed

    def test_a_supplier_failure_keeps_what_is_left_to_sell(self):
        order = self.on_order(2)
        self.assertEqual(counts(self.offer), (5, 2, 3))
        supply.fail_line(order.items.get(), actor=self.staff, reason='El proveedor no tiene stock')
        self.assertEqual(counts(self.offer), (3, 0, 3))
        log = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual((log.metadata['released'], log.metadata['cause'], log.metadata['confirmed_released']),
                         (2, 'supplier_failure', 2))
        self.assertEqual((log.metadata['available_before'], log.metadata['available_after']), (3, 3))

    def test_a_failure_never_goes_negative_nor_below_what_is_committed(self):
        order = self.on_order(2)
        # The offer holds less than the line (an edit by hand in between): release only what it holds.
        SupplierOffer.objects.filter(pk=self.offer.pk).update(committed_quantity=1, confirmed_quantity=1)
        supply.fail_line(order.items.get(), actor=self.staff, reason='Sin stock')
        self.assertEqual(counts(self.offer), (0, 0, 0))

    def test_a_cancellation_after_a_failure_releases_nothing_more(self):
        order = self.on_order(2)
        supply.fail_line(order.items.get(), actor=self.staff, reason='Sin stock')
        before = counts(self.offer)
        # No pending on-order line is left: no supply cause is asked.
        cancelling.cancel_paid_order(order, self.staff, REASON)
        self.assertEqual(counts(self.offer), before)

    def test_a_line_to_confirm_releases_nothing(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(confirmed_quantity=1)
        self.pay(order)                          # no longer fits: «por confirmar», nothing committed
        self.assertEqual(order.items.get().supply_status, OrderItem.SUPPLY_TO_CONFIRM)
        supply.fail_line(order.items.get(), actor=self.staff, reason='Sin stock')
        self.assertEqual(counts(self.offer), (1, 0, 1))

    def test_the_offer_still_sells_what_is_left_after_a_failure(self):
        order = self.on_order(2)
        supply.fail_line(order.items.get(), actor=self.staff, reason='Sin stock')
        self.assertEqual(supply.best_offer(self.case, 3), self.offer)
        self.assertIsNone(supply.best_offer(self.case, 4))


class OrdinaryCancellationTest(FailureShop, TestCase):
    def setUp(self):
        self.build('d3-cancela')

    def test_a_cancellation_before_supply_gives_the_units_back_once(self):
        order = self.on_order(2)
        cancelling.cancel_paid_order(order, self.staff, REASON, supply_cause='ordinary')
        self.assertEqual(counts(self.offer), (5, 0, 5))
        log = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual(log.metadata['cause'], 'cancellation')
        with self.assertRaises(supply.SupplyError):
            supply.fail_line(order.items.get(), actor=self.staff, reason='otra vez')
        self.assertEqual(counts(self.offer), (5, 0, 5))

    def test_releasing_a_line_of_an_order_refunded_by_another_road_gives_the_units_back(self):
        order = self.on_order(2)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.REFUNDED)
        supply.fail_line(order.items.get(), actor=self.staff, reason='Reembolsado al cliente')
        self.assertEqual(counts(self.offer), (5, 0, 5))


class CancelSupplyCauseTest(FailureShop, TestCase):
    """D3 review P2: cancelling an order with lines still waiting for the supplier says WHY."""

    def setUp(self):
        self.build('d3-causa')

    def client_for(self, user):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_without_the_cause_nothing_is_written(self):
        order = self.on_order(2)
        with self.assertRaises(cancelling.OrderCancelError) as caught:
            cancelling.cancel_paid_order(order, self.staff, REASON)
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('supply_cause_required', 400))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(counts(self.offer), (5, 2, 3))
        self.assertEqual(order.items.get().supply_status, OrderItem.SUPPLY_PENDING)
        with self.assertRaises(cancelling.OrderCancelError) as caught:
            cancelling.cancel_paid_order(order, self.staff, REASON, supply_cause='quizás')
        self.assertEqual(caught.exception.code, 'supply_cause_required')

    def test_a_supplier_failure_keeps_what_is_left(self):
        order = self.on_order(2)
        cancelling.cancel_paid_order(order, self.staff, REASON, supply_cause='supplier_failure')
        self.assertEqual(counts(self.offer), (3, 0, 3))
        log = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual((log.metadata['cause'], log.metadata['confirmed_released'], log.metadata['available_before'],
                          log.metadata['available_after']), ('supplier_failure', 2, 3, 3))

    def test_an_ordinary_cancellation_gives_the_units_back(self):
        order = self.on_order(2)
        cancelling.cancel_paid_order(order, self.staff, REASON, supply_cause='ordinary')
        self.assertEqual(counts(self.offer), (5, 0, 5))
        log = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual((log.metadata['cause'], log.metadata['available_before'], log.metadata['available_after']),
                         ('cancellation', 3, 5))

    def test_the_cancel_endpoint_asks_for_it_only_when_a_line_waits(self):
        order = self.on_order(2)
        url = f'/api/admin/orders/{order.pk}/cancel/'
        res = self.client_for(self.staff).post(url, {'reason': REASON}, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (400, 'supply_cause_required'), res.content)
        res = self.client_for(self.staff).post(url, {'reason': REASON, 'supply_cause': 'supplier_failure'},
                                               format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(counts(self.offer), (3, 0, 3))
        # An order with nothing waiting: the contract does not change.
        CartItem.objects.all().delete()
        self.stock(self.case, 1)
        CartItem.objects.create(session_key='carrito-c', product=self.case, quantity=1)
        shelf = self.checkout('carrito-c')
        self.pay(shelf)
        res = self.client_for(self.staff).post(f'/api/admin/orders/{shelf.pk}/cancel/', {'reason': REASON},
                                               format='json')
        self.assertEqual(res.status_code, 200, res.content)

    def test_a_line_to_confirm_asks_nothing(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        SupplierOffer.objects.filter(pk=self.offer.pk).update(confirmed_quantity=1)
        self.pay(order)
        cancelling.cancel_paid_order(order, self.staff, REASON)
        self.assertEqual(counts(self.offer), (1, 0, 1))

    def test_closing_a_return_never_raises_what_is_left(self):
        # A delivered order cannot hold a line still waiting (FulfillmentBlocked); if one does anyway, the
        # return's cancellation takes the conservative road: availability stays where it was.
        order = self.on_order(2)
        cancelling.cancel_for_return(order, self.staff, REASON, refund_method='cash', refund_amount=0, return_id=0)
        self.assertEqual(counts(self.offer), (3, 0, 3))
        log = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual(log.metadata['cause'], 'return_close')


class FailCauseTest(FailureShop, TestCase):
    """D3 review P3: «No se pudo abastecer» may say the shop chose not to supply (an ordinary release)."""

    def setUp(self):
        self.build('d3-fallo-causa')

    def fail(self, order, **extra):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff)
        line = order.items.get()
        return client.post(f'/api/admin/orders/{order.pk}/supply/{line.pk}/fail/', {'reason': 'Sin stock', **extra},
                           format='json')

    def test_the_default_is_a_supplier_failure(self):
        order = self.on_order(2)
        self.assertEqual(self.fail(order).status_code, 200)
        self.assertEqual(counts(self.offer), (3, 0, 3))

    def test_other_is_an_ordinary_release(self):
        order = self.on_order(2)
        self.assertEqual(self.fail(order, cause='other').status_code, 200)
        self.assertEqual(counts(self.offer), (5, 0, 5))
        log = AdminAuditLog.objects.get(action='order_supply_failed', target_id=str(order.pk))
        self.assertEqual(log.metadata['cause'], 'other')

    def test_an_unknown_cause_is_refused(self):
        order = self.on_order(2)
        self.assertEqual(self.fail(order, cause='magia').status_code, 400)
        self.assertEqual(counts(self.offer), (5, 2, 3))


class WeeklyFileAfterFailureTest(OfferFile, TestCase):
    def setUp(self):
        self.build_excel('d3-semana')

    def test_the_weekly_file_still_sets_what_is_left(self):
        self.add_on_order(quantity=2)
        order = self.checkout()
        self.pay(order)
        supply.fail_line(order.items.get(), actor=self.staff, reason='Sin stock')
        body = self.preview([self.row(**{'Vigente desde': self.offer.valid_from.isoformat(),
                                         'Vigente hasta': self.offer.valid_until.isoformat(),
                                         'Precio comercial': '110', 'Cantidad confirmada': 4,
                                         'Plazo (días)': 4, 'Costo pactado': '70'})]).json()
        self.assertEqual(body['rows'][0]['data']['before']['available'], 3, body['rows'])
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(counts(self.offer), (4, 0, 4))


class FailureDuringPaymentTest(TransactionTestCase):
    """A supplier failure and another order's payment on the same offer at once: consistent, never oversold."""

    def test_both_land_and_the_offer_stays_consistent(self):
        from threading import Barrier, Thread

        from django.db import connection

        shop = FailureShop()
        shop.build('d3-carrera')
        SupplierOffer.objects.filter(pk=shop.offer.pk).update(confirmed_quantity=2)
        failing = shop.on_order(1, session='carrito-a')
        CartItem.objects.all().delete()
        shop.add_on_order(quantity=1, session='carrito-b')
        paying = shop.checkout('carrito-b')
        barrier, outcomes = Barrier(2), []

        def run(task):
            try:
                barrier.wait(timeout=5)
                outcomes.append(task())
            except Exception as exc:  # noqa: BLE001 — the test reports it
                outcomes.append(repr(exc))
            finally:
                connection.close()

        def fail():
            supply.fail_line(failing.items.get(), actor=shop.staff, reason='Sin stock')
            return 'failed'

        def pay():
            return shop.pay(paying)

        threads = [Thread(target=run, args=(task,)) for task in (fail, pay)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(outcomes), ['failed', 'paid'], outcomes)
        # Either order: 2 confirmed, 1 failed (out of confirmed and committed), 1 paid → 1 and 1, nothing left.
        self.assertEqual(counts(shop.offer), (1, 1, 0))
        self.assertEqual(OrderItem.objects.get(order=paying).supply_status, OrderItem.SUPPLY_PENDING)

