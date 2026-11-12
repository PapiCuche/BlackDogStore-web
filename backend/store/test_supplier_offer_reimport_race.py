"""
B0 — re-imports at the same instant, on real threads and real transactions
(docs/adr-catalog-commercial.md §7.8): the content check runs under the job and
pair locks, a payment never makes the declared quantity sell twice, and two
messages of different days never leave the older one registered.
"""
from datetime import timedelta
from threading import Barrier, Thread

from django.db import connection
from django.test import TransactionTestCase

from store import supplier_offer_import as offer_import
from store.models import BulkImportJob, OrderItem, SupplierOffer
from store.test_supplier_offers import today
from store.test_supplier_weekly_import import WeeklyFile


def _race(*tasks):
    barrier, outcomes = Barrier(len(tasks)), {}

    def run(name, task):
        try:
            barrier.wait(timeout=5)
            outcomes[name] = task()
        except offer_import.OfferImportError as exc:
            outcomes[name] = exc.code
        except Exception as exc:  # noqa: BLE001 — the test reports it
            outcomes[name] = repr(exc)
        finally:
            connection.close()

    threads = [Thread(target=run, args=(name, task)) for name, task in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


def _apply(shop, job_id):
    def task():
        offer_import.apply(job=BulkImportJob.objects.get(pk=job_id), actor=shop.staff, may_override_reapply=True)
        return 'applied'
    return task


class TwoIdenticalAppliesTest(TransactionTestCase):
    def test_the_second_waits_and_is_told_the_first_applied_it(self):
        shop = WeeklyFile()
        shop.build_week('b0-dos-iguales')
        rows = [shop.same(**{'Cantidad confirmada': 4})]
        first, second = (shop.preview(rows).json()['id'] for _ in range(2))
        outcomes = _race(('a', _apply(shop, first)), ('b', _apply(shop, second)))
        self.assertEqual(sorted(outcomes.values()), ['already_applied_content', 'applied'], outcomes)
        offer = SupplierOffer.objects.get(pk=shop.offer.pk)
        self.assertEqual((offer.confirmed_quantity, offer.remaining, offer.last_declared_quantity), (4, 4, 4))
        self.assertEqual(BulkImportJob.objects.filter(pk__in=[first, second], status='applied').count(), 1)


class ApplyAgainstPaymentTest(TransactionTestCase):
    def test_a_sale_since_the_message_comes_off_whoever_goes_first(self):
        shop = WeeklyFile()
        shop.build_week('b0-pago')
        shop.add_on_order(quantity=1)
        order = shop.checkout()
        job = shop.preview([shop.same(**{'Cantidad confirmada': 4})]).json()['id']

        def pay():
            shop.pay(order)
            return 'paid'

        outcomes = _race(('apply', _apply(shop, job)), ('pay', pay))
        self.assertEqual(outcomes, {'apply': 'applied', 'pay': 'paid'}, outcomes)
        offer = SupplierOffer.objects.get(pk=shop.offer.pk)
        # The message is today's: the sale paid today is after its cut-off — 4 declared, 1 sold, 3 left either way.
        self.assertEqual((offer.confirmed_quantity, offer.committed_quantity, offer.remaining), (4, 1, 3))
        self.assertEqual(OrderItem.objects.get(order=order).supply_status, 'pending')


class TwoMessageDatesTest(TransactionTestCase):
    def test_the_older_message_never_ends_up_registered(self):
        shop = WeeklyFile()
        shop.build_week('b0-dos-fechas')
        older = shop.preview([shop.same(**{'Cantidad confirmada': 3})],
                             message_date=today() - timedelta(days=1)).json()['id']
        newer = shop.preview([shop.same(**{'Cantidad confirmada': 4})]).json()['id']
        outcomes = _race(('older', _apply(shop, older)), ('newer', _apply(shop, newer)))
        self.assertEqual(sorted(outcomes.values()), ['applied', 'stale'], outcomes)
        if outcomes['newer'] == 'stale':
            # The older one won the race: previewed again, the newer message is still a new declaration.
            again = shop.preview([shop.same(**{'Cantidad confirmada': 4})]).json()
            self.assertEqual(again['rows'][0]['data']['declaration'], 'new')
            self.assertEqual(shop.apply(again['id']).status_code, 200)
        else:
            again = shop.preview([shop.same(**{'Cantidad confirmada': 3})],
                                 message_date=today() - timedelta(days=1)).json()
            self.assertEqual(again['rows'][0]['data']['declaration'], 'older')
            self.assertEqual(shop.apply(again['id']).status_code, 200)
        offer = SupplierOffer.objects.get(pk=shop.offer.pk)
        self.assertEqual((offer.declared_message_date, offer.last_declared_quantity, offer.remaining),
                         (today(), 4, 4))
