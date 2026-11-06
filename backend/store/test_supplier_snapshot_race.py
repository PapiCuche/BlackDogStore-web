"""
INV-SNAPSHOT-01 — races. Real threads on PostgreSQL: two applies of the same
complete list, an apply against a payment on the absent offer, and an apply
against a manual edit. Whatever the order, nothing is sold over what was
confirmed and commitments are never lost.
"""
import threading
import time
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase

from store import supply_services as supply
from store.models import AdminAuditLog, OrderItem, Product, SupplierOffer
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

    @staticmethod
    def until_someone_waits_on_me(timeout=10):
        """Inside the caller's transaction: return once another backend is blocked by one of its locks."""
        deadline = time.monotonic() + timeout
        with connection.cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute('SELECT pg_stat_clear_snapshot()')
                cursor.execute('SELECT count(*) FROM pg_stat_activity WHERE pg_backend_pid() = ANY(pg_blocking_pids(pid))')
                if cursor.fetchone()[0]:
                    return
                time.sleep(0.01)
        raise AssertionError('Nobody waited on the lock: the forced interleaving did not happen.')

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
        self.assertIn(outcomes['apply'], (200, 400), outcomes)
        # The money is in whatever the order: an offer switched off never refuses a web payment.
        self.assertEqual(outcomes['pay'], 'paid', outcomes)
        order.refresh_from_db()
        self.assertTrue(order.paid)
        line = order.items.get()
        self.assertEqual(line.supplier_offer_id, absent.pk)     # the line keeps its snapshot whatever the order
        if line.supply_status == OrderItem.SUPPLY_PENDING:
            # The payment took the offer's row first: committed against it, and the apply switches it off
            # with that sale still on it.
            self.assertEqual(absent.committed_quantity, line.quantity, outcomes)
        else:
            # DEC-INV-07: the apply took the row first and switched the offer off; the payment, waiting on the
            # row lock, read it off and kept the money «por confirmar» — nothing was committed, nothing lost.
            # Both orders are pinned deterministically below.
            self.assertEqual((line.supply_status, absent.committed_quantity, absent.is_active, outcomes['apply']),
                             (OrderItem.SUPPLY_TO_CONFIRM, 0, False, 200), outcomes)

    def test_an_apply_that_holds_the_absent_offer_first_leaves_the_payment_to_confirm(self):
        # DEC-INV-07, the interleaving the race above lands in ~1% of runs: the apply holds the absent offer
        # when the payment reaches it. The payment waits on the row lock, reads it switched off and keeps the
        # money with the line «por confirmar» (as for an offer that expired before the payment): nothing was
        # committed, so nothing is owed to the offer.
        job = self.job()
        self.add_on_order(product=self.cable)
        order = self.checkout()
        holding, real = threading.Event(), supply.deactivate_offer

        def deactivate(offer, **kwargs):
            holding.set()
            self.until_someone_waits_on_me()     # the payment is blocked on this offer's row
            return real(offer, **kwargs)

        def pay():
            self.assertTrue(holding.wait(timeout=10))
            return self.pay(order)

        with mock.patch.object(supply, 'deactivate_offer', deactivate):
            outcomes = self.race(('apply', lambda: self.apply_job(job)), ('pay', pay))
        self.assertEqual((outcomes['apply'], outcomes['pay']), (200, 'paid'), outcomes)
        absent = SupplierOffer.objects.get(pk=self.absent.pk)
        order.refresh_from_db()
        line = order.items.get()
        self.assertTrue(order.paid)
        self.assertEqual((line.supplier_offer_id, line.supply_status), (absent.pk, OrderItem.SUPPLY_TO_CONFIRM))
        self.assertEqual((absent.is_active, absent.committed_quantity), (False, 0))
        self.assertIn('abastecimiento por confirmar', order.payment_error.lower())
        self.assertTrue(AdminAuditLog.objects.filter(action='order_supply_to_confirm', target_id=str(order.pk)).exists())

    def test_a_payment_that_holds_the_absent_offer_first_keeps_its_commitment_through_the_apply(self):
        job = self.job()
        self.add_on_order(product=self.cable)
        order = self.checkout()
        holding, real = threading.Event(), supply.commit_order_lines

        def commit(order_, **kwargs):
            real(order_, **kwargs)
            holding.set()
            self.until_someone_waits_on_me()     # the apply is blocked on this offer's row

        def apply():
            self.assertTrue(holding.wait(timeout=10))
            return self.apply_job(job)

        with mock.patch.object(supply, 'commit_order_lines', commit):
            outcomes = self.race(('apply', apply), ('pay', lambda: self.pay(order)))
        self.assertEqual((outcomes['apply'], outcomes['pay']), (200, 'paid'), outcomes)
        absent = SupplierOffer.objects.get(pk=self.absent.pk)
        line = order.items.get()
        self.assertEqual((line.supplier_offer_id, line.supply_status), (absent.pk, OrderItem.SUPPLY_PENDING))
        self.assertEqual((absent.is_active, absent.committed_quantity), (False, line.quantity))

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
