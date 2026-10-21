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
        cancelling.cancel_paid_order(order, self.staff, REASON)
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

