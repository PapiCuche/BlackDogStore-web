"""
DEVICE-COMMERCE-01 — the owner's three decisions of 2026-10-15.

  1. HOLDS. How long a cart and a checkout hold a device is the company's
     (`CompanySettings`), within bounds; a hold whose order has a payment the
     gateway may still confirm is never released by its clock — reconciliation
     decides; releasing is idempotent and safe under concurrency.
  2. ON ORDER WHILE THE SHELF HAS IT. Own stock first, never switched silently.
     Choosing the supplier anyway is explicit: at the till with
     `sales.orders.manage` and a reason, on the web only where the company
     allows it. The line says what was chosen, why, its cost and lead time.
  3. MARGIN. (net sales without IGV − comparable net cost) / net sales × 100,
     over the lines with a known cost; estimated vs realised; unknown costs and
     refunds apart. Never «utilidad neta».
"""
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Thread
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from store import stock_unit_services as units
from store import unit_hold_services as holds
from store.models import (
    AdminAuditLog, CompanySettings, Order, PaymentTransaction, StockMovement, StockUnit,
)
from store.test_device_commerce import DeviceShop, SaleShop, _client
from store.tests import _p2d_member

D = Decimal


def _attempt(order, *, status='pending', minutes_ago=0, reason=''):
    reference = uuid4().hex[:20]
    attempt = PaymentTransaction.objects.create(
        order=order, provider='micuentaweb', transaction_id=reference, order_number=reference,
        amount=order.total, currency='PEN', status=status, failure_reason=reason,
    )
    if minutes_ago:
        PaymentTransaction.objects.filter(pk=attempt.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago))
    return attempt


def _release_lines(product):
    return StockMovement.objects.filter(product=product, movement_type='manual_entry',
                                        reason=holds.RELEASE_REASON).count()


# ---------------------------------------------------------------------------
# 1 · Holds
# ---------------------------------------------------------------------------

class HoldDurationsPerCompanyTest(SaleShop, TestCase):
    """The cart and checkout times are the company's, with today's values as they are created."""

    def minutes_until(self, unit):
        unit.refresh_from_db()
        return (unit.hold_until - timezone.now()).total_seconds() / 60

    def test_a_new_company_keeps_todays_values(self):
        row = CompanySettings.objects.get(company=self.company)
        self.assertEqual(
            (row.device_cart_hold_minutes, row.device_checkout_hold_minutes, row.device_hold_ceiling_minutes),
            (15, 45, 60))
        unit = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        self.assertAlmostEqual(self.minutes_until(unit), 15, delta=0.5)
        self.checkout()
        self.assertAlmostEqual(self.minutes_until(unit), 45, delta=0.5)

    def test_the_company_sets_its_own_cart_checkout_and_ceiling(self):
        CompanySettings.objects.filter(company=self.company).update(
            device_cart_hold_minutes=20, device_checkout_hold_minutes=40, device_hold_ceiling_minutes=70)
        unit = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        self.assertAlmostEqual(self.minutes_until(unit), 20, delta=0.5)
        self.checkout()
        self.assertAlmostEqual(self.minutes_until(unit), 40, delta=0.5)
        # The ceiling counts from when the cart took it: 50 minutes ago + 70 → 20 left.
        StockUnit.objects.filter(pk=unit.pk).update(hold_started_at=timezone.now() - timedelta(minutes=50))
        Order.objects.filter(cart_session_key='carrito-a').update(status=Order.Status.FAILED)
        self.checkout()
        self.assertAlmostEqual(self.minutes_until(unit), 20, delta=0.5)

    def test_a_company_without_a_settings_row_falls_back_to_the_platform_values(self):
        CompanySettings.objects.filter(company=self.company).delete()
        self.company.refresh_from_db()
        self.assertEqual((holds.hold_minutes(self.company), holds.order_hold_minutes(self.company),
                          holds.hold_ceiling_minutes(self.company)), (15, 45, 60))

    def test_the_bounds_hold_on_every_write(self):
        row = CompanySettings.objects.get(company=self.company)
        for fields in (
            {'device_cart_hold_minutes': 4},
            {'device_cart_hold_minutes': 61},
            # Shorter than the gateway's form lifetime: a buyer could still be paying.
            {'device_checkout_hold_minutes': 29},
            {'device_checkout_hold_minutes': 121},
            {'device_hold_ceiling_minutes': 241},
            # A checkout made in the cart's last minute must still get the whole form.
            {'device_cart_hold_minutes': 40, 'device_hold_ceiling_minutes': 69},
        ):
            with self.subTest(fields), self.assertRaises(ValidationError):
                row.refresh_from_db()
                for name, value in fields.items():
                    setattr(row, name, value)
                row.save()
        row.refresh_from_db()
        row.device_cart_hold_minutes, row.device_hold_ceiling_minutes = 40, 70
        row.save()

    def test_the_form_lifetime_floor_is_the_gateways(self):
        from store.payments import micuentaweb
        self.assertEqual(timedelta(minutes=CompanySettings.CHECKOUT_HOLD_MIN_MINUTES), micuentaweb.FORM_LIFETIME)

    def test_changing_them_takes_settings_manage_and_is_validated(self):
        boss, _ = _p2d_member(self.company, 'dec_jefe', ['company.view', 'company.manage', 'settings.manage'])
        clerk, _ = _p2d_member(self.company, 'dec_admin', ['company.view', 'company.manage'])
        url = f'/api/admin/company-settings/?company={self.company.pk}'
        refused = _client(clerk).patch(url, {'device_cart_hold_minutes': 20}, format='json')
        self.assertEqual(refused.status_code, 403, refused.content)
        bad = _client(boss).patch(url, {'device_checkout_hold_minutes': 10}, format='json')
        self.assertEqual(bad.status_code, 400, bad.content)
        bad = _client(boss).patch(url, {'device_cart_hold_minutes': 40, 'device_hold_ceiling_minutes': 60},
                                  format='json')
        self.assertEqual(bad.status_code, 400, bad.content)
        ok = _client(boss).patch(url, {'device_cart_hold_minutes': 20, 'device_checkout_hold_minutes': 50,
                                       'device_hold_ceiling_minutes': 90}, format='json')
        self.assertEqual(ok.status_code, 200, ok.content)
        settings = ok.json()['settings']
        self.assertEqual((settings['device_cart_hold_minutes'], settings['device_checkout_hold_minutes'],
                          settings['device_hold_ceiling_minutes']), (20, 50, 90))
        # Sending them back unchanged is not a change: the clerk's form still saves.
        same = _client(clerk).patch(url, {'device_cart_hold_minutes': 20, 'phone': '999'}, format='json')
        self.assertEqual(same.status_code, 200, same.content)


class PaymentKeepsTheHoldTest(SaleShop, TestCase):
    """A hold whose order the gateway may still confirm is not released by its clock."""

    def setUp(self):
        super().setUp()
        holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                   unit_public_id=str(self.used_a.public_id))
        self.order = self.checkout()

    def expire(self, minutes=1):
        StockUnit.objects.filter(pk=self.used_a.pk).update(hold_until=timezone.now() - timedelta(minutes=minutes))

    def status(self):
        self.used_a.refresh_from_db()
        return self.used_a.status

    def test_without_an_attempt_the_clock_decides_as_before(self):
        self.expire()
        self.assertEqual(holds.release_expired(), 1)
        self.assertEqual(self.status(), 'available')

    def test_a_pending_attempt_keeps_it_on_every_path(self):
        _attempt(self.order, minutes_ago=50)
        self.expire()
        self.assertEqual(holds.release_expired(), 0)
        holds.sync_cart('carrito-a', self.company)          # on read
        holds.release_expired(company=self.company, product=self.used128)
        self.assertEqual(self.status(), 'reserved')
        # Nor by hand at the till: that buyer may be paying.
        with self.assertRaises(units.StockUnitError):
            units.release(self.used_a, actor=self.staff)
        self.assertEqual(self.status(), 'reserved')

    def test_reconciliation_decides_abandoned_or_rejected_release_it(self):
        for closed in ('abandoned', 'rejected'):
            with self.subTest(closed):
                attempt = _attempt(self.order, minutes_ago=20)
                self.expire()
                self.assertEqual(holds.release_expired(), 0)
                PaymentTransaction.objects.filter(pk=attempt.pk).update(status=closed)
                self.assertEqual(holds.release_expired(), 1)
                self.assertEqual(self.status(), 'available')
                # Hold it again for the next round.
                holds.hold(product=self.used128, branch=self.branch, session_key='carrito-a',
                           unit_public_id=str(self.used_a.public_id))
                Order.objects.filter(pk=self.order.pk).update(status=Order.Status.FAILED)
                self.order = self.checkout()

    def test_past_the_reconciliation_window_the_attempt_no_longer_keeps_it(self):
        _attempt(self.order, minutes_ago=121)
        self.expire(minutes=70)
        self.assertEqual(holds.release_expired(), 1)
        self.assertEqual(self.status(), 'available')

    def test_an_answer_nobody_could_settle_keeps_it_until_a_person_decides(self):
        _attempt(self.order, minutes_ago=300, reason='undecided: dos cobros')
        self.expire(minutes=250)
        self.assertEqual(holds.release_expired(), 0)
        self.assertEqual(self.status(), 'reserved')
        # The person who looked at the gateway's panel is the reconciliation.
        units.release(self.used_a, actor=self.staff)
        self.assertEqual(self.status(), 'available')

    def test_an_attempt_opened_after_the_hold_ran_out_does_not_revive_it(self):
        self.expire(minutes=30)
        _attempt(self.order, minutes_ago=5)
        self.assertEqual(holds.release_expired(), 1)

    def test_a_paid_or_failed_order_keeps_nothing(self):
        _attempt(self.order, minutes_ago=5)
        self.expire()
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.FAILED)
        self.assertEqual(holds.release_expired(), 1)


class ReleaseOnceTest(SaleShop, TestCase):
    """Releasing is idempotent: one Kardex line however many times it is asked."""

    def test_releasing_twice_writes_one_kardex_line(self):
        unit = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        StockUnit.objects.filter(pk=unit.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(holds.release_expired(), 1)
        self.assertEqual(holds.release_expired(), 0)
        holds.sync_cart('carrito-a', self.company)
        self.assertEqual(_release_lines(self.new128), 1)
        self.assertEqual(self.shelf(self.new128), self.available(self.new128))

    def test_the_sweep_reads_the_hold_again_under_the_lock(self):
        """A checkout extended it between the sweep's list and its lock: not released."""
        unit = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        listed_at = timezone.now()
        StockUnit.objects.filter(pk=unit.pk).update(hold_until=listed_at + timedelta(minutes=30))
        self.assertFalse(holds.release_one_expired(unit.pk, now=listed_at))
        StockUnit.objects.filter(pk=unit.pk).update(hold_key='')       # a staff reservation now
        self.assertFalse(holds.release_one_expired(unit.pk, now=listed_at + timedelta(hours=1)))
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'reserved')
        self.assertEqual(_release_lines(self.new128), 0)


class ConcurrentReleaseTest(TransactionTestCase):
    """Two sweeps and a cart read at the same instant: one release, one Kardex line."""

    def test_three_releases_at_once_write_one_line(self):
        shop = DeviceShop()
        shop.build('dec-carrera')
        shop.receive(shop.new128, 2)
        unit = holds.hold(product=shop.new128, branch=shop.branch, session_key='carrito-a')
        StockUnit.objects.filter(pk=unit.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        barrier, results = Barrier(3), []

        def run(call):
            try:
                barrier.wait(timeout=5)
                results.append(call())
            finally:
                connection.close()

        calls = [holds.release_expired, holds.release_expired,
                 lambda: holds.sync_cart('carrito-a', shop.company)]
        threads = [Thread(target=run, args=(call,)) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(_release_lines(shop.new128), 1)
        unit.refresh_from_db()
        self.assertEqual((unit.status, unit.hold_key), ('available', ''))
        self.assertEqual(shop.shelf(shop.new128), shop.available(shop.new128))
        self.assertEqual(AdminAuditLog.objects.filter(action='stock_unit_web_hold_released').count(), 1)
