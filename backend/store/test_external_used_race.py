"""
INV-USED-EXT-01 — races, with real threads on PostgreSQL. One device is sold
once, offered once, and one wrong reception opens one incident, whatever the
order the requests land in.
"""
import threading
from datetime import timedelta

from django.db import connection
from django.test import TransactionTestCase

from store import external_used
from store.models import ExternalUsedCard, StockUnit, SupplierOffer, SupplyReceptionIncident
from store.test_external_used import IMEIS, ExtShop
from store.test_supplier_offers import today


class ExternalUsedRaceTest(ExtShop, TransactionTestCase):
    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('Row locks: PostgreSQL only.')
        self.start, self.end = today(), today() + timedelta(days=6)
        self.build('ext-race')

    def race(self, *steps):
        barrier, outcomes = threading.Barrier(len(steps)), {}

        def run(name, step):
            try:
                barrier.wait(timeout=10)
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 - the outcome is the exception
                outcomes[name] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=step) for step in steps]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(set(outcomes), {name for name, _ in steps}, outcomes)
        return outcomes

    def test_two_payments_at_once_reserve_the_one_card_once(self):
        self.card(0)
        self.add_on_order(self.used, session='a')
        self.add_on_order(self.used, session='b')
        first, second = self.checkout('a'), self.checkout('b')
        self.race(('a', lambda: self.pay(first)), ('b', lambda: self.pay(second)))
        statuses = sorted(order.items.get().supply_status for order in (first, second))
        self.assertEqual(statuses, ['pending', 'to_confirm'])
        self.assertEqual(ExternalUsedCard.objects.filter(status='reserved').count(), 1)
        offer = SupplierOffer.objects.get(product=self.used)
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity), (1, 1))

    def test_the_same_device_offered_twice_at_once_is_one_card(self):
        def offer(serial):
            return lambda: self.card(0, reported_serial=serial)

        outcomes = self.race(('a', offer('SERA0001')), ('b', offer('SERB0001')))
        self.assertEqual(sum(isinstance(value, ExternalUsedCard) for value in outcomes.values()), 1, outcomes)
        self.assertEqual(ExternalUsedCard.objects.count(), 1)
        self.assertEqual(SupplierOffer.objects.get(product=self.used).confirmed_quantity, 1)

    def test_two_cards_of_one_variant_at_once_share_one_offer(self):
        # Both say the same terms: whichever lands first opens the offer, the other joins it.
        outcomes = self.race(('a', lambda: self.card(0)), ('b', lambda: self.card(1)))
        self.assertTrue(all(isinstance(value, ExternalUsedCard) for value in outcomes.values()), outcomes)
        self.assertEqual(SupplierOffer.objects.filter(product=self.used).count(), 1)
        self.assertEqual(SupplierOffer.objects.get(product=self.used).confirmed_quantity, 2)

    def test_two_wrong_receptions_at_once_open_one_incident_and_write_nothing(self):
        self.card(0)
        order = self.paid_order(self.used, session='a')
        wrong = {'serial_number': 'OTRO0001', 'imei': IMEIS[9]}
        outcomes = self.race(('a', lambda: self.receive(order, wrong).status_code),
                             ('b', lambda: self.receive(order, wrong).status_code))
        self.assertEqual(sorted(outcomes.values()), [409, 409])
        self.assertEqual(SupplyReceptionIncident.objects.filter(status='open').count(), 1)
        self.assertEqual(StockUnit.objects.count(), 0)

    def test_a_right_and_a_wrong_reception_at_once_never_both_land(self):
        self.card(0)
        order = self.paid_order(self.used, session='a')
        outcomes = self.race(
            ('right', lambda: self.receive(order, {'serial_number': 'SERU0000', 'imei': IMEIS[0]}).status_code),
            ('wrong', lambda: self.receive(order, {'serial_number': 'OTRO0001', 'imei': IMEIS[9]}).status_code))
        units = StockUnit.objects.count()
        incidents = SupplyReceptionIncident.objects.count()
        # Either the right device came first (received; the wrong one is told it already happened), or the
        # wrong one did (an incident; the right one is blocked by it). Never a device AND an incident.
        self.assertIn((units, incidents), [(1, 0), (0, 1)], outcomes)
        self.assertFalse(StockUnit.objects.filter(imei=IMEIS[9]).exists())
        self.assertEqual(external_used.open_incident_for(order.items.get()) is not None, incidents == 1)
