"""
INV-SNAPSHOT-01 — races. Real threads on PostgreSQL: two applies of the same
complete list, an apply against a payment on the absent offer, and an apply
against a manual edit. Whatever the order, nothing is sold over what was
confirmed and commitments are never lost.
"""
import threading
from datetime import timedelta

from django.db import connection
from django.test import TransactionTestCase

from store import supply_services as supply
from store.models import Product, SupplierOffer
from store.test_supplier_offers import today
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _prod

PREVIEW = '/api/admin/supplier-offers/import/preview/'


class SnapshotRaceTest(WeeklyFile, TransactionTestCase):
    def setUp(self):
        if connection.vendor == 'sqlite':
            self.skipTest('Row locks: PostgreSQL only.')
        self.build_week('snap-race')
        Product.objects.filter(pk=self.cable.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.absent = self.make_offer(product=self.cable)

    def job(self):
        return self.client_.post(PREVIEW, {'file': self.file([self.same()]), 'complete_snapshot': 'true',
                                           'supplier_id': str(self.supplier.pk)}, format='multipart').json()['id']

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

    def apply_job(self, job):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.staff)
        return client.post(f'/api/admin/supplier-offers/import/{job}/apply/',
                           {'all_messages_included': True, 'confirm_deactivations': 1}, format='json').status_code

    def test_two_applies_of_two_previews_switch_off_once(self):
        first, second = self.job(), self.job()
        outcomes = self.race(('a', lambda: self.apply_job(first)), ('b', lambda: self.apply_job(second)))
        self.assertIn(200, outcomes.values(), outcomes)
        absent = SupplierOffer.objects.get(pk=self.absent.pk)
        self.assertFalse(absent.is_active)
        self.assertEqual(sorted(outcomes.values()), [200, 400], outcomes)   # the second is stale

    def test_an_apply_against_a_payment_on_the_absent_offer_keeps_every_commitment(self):
        job = self.job()
        self.add_on_order(product=self.cable)
        order = self.checkout()
        outcomes = self.race(('apply', lambda: self.apply_job(job)), ('pay', lambda: self.pay(order)))
        absent = SupplierOffer.objects.get(pk=self.absent.pk)
        self.assertLessEqual(absent.committed_quantity, absent.confirmed_quantity)
        order.refresh_from_db()
        if order.paid:
            line = order.items.get()
            if line.supplier_offer_id == absent.pk:
                self.assertEqual(absent.committed_quantity, line.quantity)
        self.assertIn(outcomes['apply'], (200, 400), outcomes)

    def test_an_apply_against_a_manual_edit_of_the_absent_offer(self):
        job = self.job()

        def edit():
            return supply.update_offer(SupplierOffer.objects.get(pk=self.absent.pk), actor=self.staff,
                                       may_view_costs=True, lead_time_days=9).lead_time_days

        outcomes = self.race(('apply', lambda: self.apply_job(job)), ('edit', edit))
        absent = SupplierOffer.objects.get(pk=self.absent.pk)
        if outcomes['apply'] == 200:
            self.assertFalse(absent.is_active)
        else:
            self.assertEqual((outcomes['apply'], absent.is_active), (400, True))   # stale: edited first
