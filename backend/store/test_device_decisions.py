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


class PaymentKeptHoldsCountTowardTheCapsTest(SaleShop, TestCase):
    """
    A hold a pending payment keeps past its clock is still a hold: it counts
    toward the per-cart and per-origin caps, the shelf share and the company
    ceiling (integration review of PR #123, P1). Otherwise one origin repeats
    «add, checkout, open the payment form, close the tab» and reserves a shelf.
    """

    def abandon_at_the_payment_form(self, session, origin='', unit=None, product=None):
        """Add, checkout, open the payment form, close the tab — and let the clock run out."""
        held = holds.hold(product=product or self.new128, branch=self.branch, session_key=session,
                          origin=origin, unit_public_id=str(unit.public_id) if unit else None)
        order = self.checkout(session)
        _attempt(order, minutes_ago=5)
        StockUnit.objects.filter(pk=held.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        holds.release_expired()
        return held

    def test_one_origin_cannot_reserve_a_shelf_through_abandoned_payment_forms(self):
        self.receive(self.new128, 8)
        accepted = 0
        with self.assertRaises(holds.HoldError) as refused:
            for cycle in range(8):
                self.abandon_at_the_payment_form(f'pestana-{cycle}', origin='un-origen')
                accepted += 1
        self.assertEqual(refused.exception.code, 'too_many')
        self.assertEqual(accepted, holds.max_holds_per_origin())
        self.assertEqual(StockUnit.objects.filter(product=self.new128, status='reserved').count(), 3)

    def test_one_cart_cannot_either(self):
        self.receive(self.new128, 8)
        for _ in range(holds.max_holds_per_cart()):
            holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        order = self.checkout('carrito-a')
        _attempt(order, minutes_ago=5)
        StockUnit.objects.filter(hold_order=order).update(hold_until=timezone.now() - timedelta(minutes=1))
        holds.release_expired()
        self.assertEqual(StockUnit.objects.filter(product=self.new128, status='reserved').count(), 3)
        with self.assertRaises(holds.HoldError) as refused:
            holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a')
        self.assertEqual(refused.exception.code, 'too_many')

    def test_the_shelf_share_counts_payment_kept_holds(self):
        self.receive(self.new128, 2)          # 4 on the shelf: the share is 2
        self.abandon_at_the_payment_form('pestana-1')
        self.abandon_at_the_payment_form('pestana-2')
        with self.assertRaises(holds.HoldError) as refused:
            holds.hold(product=self.new128, branch=self.branch, session_key='pestana-3')
        self.assertEqual(refused.exception.code, 'held_by_others')

    def test_the_company_ceiling_counts_payment_kept_holds(self):
        # 4 web devices (2 new, 2 used): the ceiling is 3; no shelf reaches the share's 4.
        self.abandon_at_the_payment_form('pestana-1')
        self.abandon_at_the_payment_form('pestana-2')
        self.abandon_at_the_payment_form('pestana-3', unit=self.used_a, product=self.used128)
        with self.assertRaises(holds.HoldError) as refused:
            holds.hold(product=self.used128, branch=self.branch, session_key='pestana-4',
                       unit_public_id=str(self.used_b.public_id))
        self.assertEqual(refused.exception.code, 'held_by_others')

    def test_an_expired_hold_no_live_order_keeps_blocks_nobody(self):
        """Not swept yet and no order that may be paid: it is going back to the shelf."""
        held = holds.hold(product=self.new128, branch=self.branch, session_key='carrito-a', origin='un-origen')
        StockUnit.objects.filter(pk=held.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        self.assertEqual(holds._live_web_holds(self.company, timezone.now()).count(), 0)


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


# ---------------------------------------------------------------------------
# 2 · On order while the shelf has it
# ---------------------------------------------------------------------------

from django.test import override_settings  # noqa: E402

from store import pos_services  # noqa: E402
from store.models import CartItem, OrderItem  # noqa: E402
from store.test_supplier_offers import SupplyShop  # noqa: E402


class TillOnOrderOverStockTest(SupplyShop, TestCase):
    """At the till: own stock first; the supplier anyway only with `sales.orders.manage` and a reason."""

    def setUp(self):
        self.build('dec-caja')
        self.stock(self.case, 3)
        self.cashier, _ = _p2d_member(self.company, 'dec_cajero', ['company.view', 'sales.pos.use'])
        self.manager, _ = _p2d_member(self.company, 'dec_encargado',
                                      ['company.view', 'sales.pos.use', 'sales.orders.manage'])

    def sell(self, user, reason=None, quantity=2, preview=False, key=None):
        line = {'product': self.case.pk, 'quantity': quantity, 'on_order': True}
        if reason is not None:
            line['supply_reason'] = reason
        body = {'branch': self.branch.pk, 'payment_method': 'cash', 'items': [line]}
        if preview:
            return _client(user).post('/api/admin/pos/preview/', body, format='json')
        body.update(terms_confirmed=True, idempotency_key=key or uuid4().hex, amount_received='1000.00')
        return _client(user).post('/api/admin/pos/sales/', body, format='json')

    def test_without_a_reason_the_refusal_is_unchanged_whoever_asks(self):
        for user in (self.cashier, self.manager):
            with self.subTest(user.username):
                res = self.sell(user)
                self.assertEqual((res.status_code, res.json().get('code')), (409, 'stock_available'), res.content)
                res = self.sell(user, reason='   ')
                self.assertEqual((res.status_code, res.json().get('code')), (409, 'stock_available'), res.content)
        self.assertFalse(OrderItem.objects.exists())

    def test_without_the_permission_a_reason_is_not_enough(self):
        for preview in (True, False):
            res = self.sell(self.cashier, reason='El cliente quiere caja sellada', preview=preview)
            self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(OrderItem.objects.exists())
        self.assertEqual(self.shelf(self.case), 3)

    def test_with_the_permission_and_a_reason_it_is_sold_on_order_and_recorded(self):
        preview = self.sell(self.manager, reason='Caja sellada de fábrica', preview=True)
        self.assertEqual(preview.status_code, 200, preview.content)
        res = self.sell(self.manager, reason='  Caja sellada   de fábrica ')
        self.assertEqual(res.status_code, 201, res.content)
        line = OrderItem.objects.get()
        self.assertEqual(
            (line.supply_origin, line.supplier_offer_id, line.lead_time_days, line.unit_cost, line.cost_source,
             line.supply_chosen_over_stock, line.supply_choice_reason, line.price),
            ('supplier', self.offer.pk, 4, D('70.0000'), 'supplier_offer', True, 'Caja sellada de fábrica',
             D('110.00')))
        self.assertEqual(self.shelf(self.case), 3)              # nothing left the shelf
        log = AdminAuditLog.objects.get(action='pos_on_order_over_stock')
        self.assertEqual(log.actor_id, self.manager.pk)
        self.assertEqual(
            {k: log.metadata[k] for k in ('product_id', 'quantity', 'own_stock', 'offer_id', 'lead_time_days',
                                          'has_cost', 'reason')},
            {'product_id': self.case.pk, 'quantity': 2, 'own_stock': 3, 'offer_id': self.offer.pk,
             'lead_time_days': 4, 'has_cost': True, 'reason': 'Caja sellada de fábrica'})
        self.assertNotIn('70', str(log.metadata))               # the cost is on the line, behind costs.view

    def test_a_line_the_shelf_cannot_cover_needs_no_reason_and_is_not_flagged(self):
        res = self.sell(self.cashier, quantity=4)
        self.assertEqual(res.status_code, 201, res.content)
        line = OrderItem.objects.get()
        self.assertEqual((line.supply_origin, line.supply_chosen_over_stock, line.supply_choice_reason),
                         ('supplier', False, ''))

    def test_the_reason_is_short(self):
        res = self.sell(self.manager, reason='x' * 201)
        self.assertEqual(res.status_code, 400, res.content)

    def test_a_retry_with_another_reason_is_another_sale(self):
        first = self.sell(self.manager, reason='Motivo A', key='clave-0001')
        self.assertEqual(first.status_code, 201, first.content)
        again = self.sell(self.manager, reason='Motivo A', key='clave-0001')
        self.assertEqual(again.status_code, 200, again.content)
        other = self.sell(self.manager, reason='Motivo B', key='clave-0001')
        self.assertEqual(other.status_code, 409, other.content)


@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class WebOnOrderOverStockTest(SupplyShop, TestCase):
    """On the web: only where the company allows it, and the buyer is told supplier and lead time."""

    def setUp(self):
        self.build('dec-web')
        self.stock(self.case, 3)
        self.public = _client(host=self.host)

    def allow(self, value=True):
        CompanySettings.objects.filter(company=self.company).update(web_on_order_over_stock=value)

    def add(self, quantity=2, on_order=True):
        body = {'session_key': 'carrito-a', 'product': self.case.pk, 'quantity': quantity}
        if on_order:
            body['on_order'] = True
        return self.public.post('/api/cart/add/', body, format='json')

    def product(self):
        body = self.public.get(f'/api/products/?slug={self.case.slug}').json()
        return (body['results'] if isinstance(body, dict) else body)[0]

    def test_off_by_default_the_refusal_is_unchanged(self):
        res = self.add()
        self.assertEqual((res.status_code, res.json()['code']), (409, 'stock_available'))
        # The public offer keeps its shape: lead time and price, nothing more.
        self.assertEqual(self.product()['on_order'], {'lead_time_days': 4, 'price': '110.00'})

    def test_on_the_buyer_may_choose_the_supplier_and_is_told_so(self):
        self.allow()
        offer = self.product()['on_order']
        self.assertEqual(offer, {'lead_time_days': 4, 'price': '110.00', 'over_stock': True})
        res = self.add()
        self.assertEqual(res.status_code, 200, res.content)
        item = CartItem.objects.get()
        self.assertEqual((item.supply_origin, item.quantity), ('supplier', 2))
        # Without asking for it, the same buyer gets the shelf: never switched silently.
        res = self.public.post('/api/cart/add/', {'session_key': 'carrito-b', 'product': self.case.pk,
                                                  'quantity': 1}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(CartItem.objects.get(session_key='carrito-b').supply_origin, 'own')
        order = self.checkout()
        line = order.items.get()
        self.assertEqual(
            (line.supply_origin, line.supply_chosen_over_stock, line.lead_time_days, line.unit_cost,
             line.cost_source),
            ('supplier', True, 4, D('70.0000'), 'supplier_offer'))
        self.assertTrue(line.supply_choice_reason)
        log = AdminAuditLog.objects.get(action='web_on_order_over_stock')
        self.assertEqual((log.metadata['order_id'], log.metadata['product_id'], log.metadata['own_stock']),
                         (order.pk, self.case.pk, 3))

    def test_switched_off_again_the_web_refuses_it(self):
        self.allow()
        self.assertEqual(self.add().status_code, 200)
        self.allow(False)
        item = CartItem.objects.get()
        patched = self.public.patch(f'/api/cart/{item.pk}/?session_key=carrito-a', {'quantity': 1}, format='json')
        self.assertEqual((patched.status_code, patched.json()['code']), (409, 'stock_available'))

    def test_a_line_the_shelf_covers_exactly_is_flagged(self):
        self.allow()
        self.assertEqual(self.add(quantity=3).status_code, 200)
        self.assertTrue(self.checkout().items.get().supply_chosen_over_stock)

    def test_a_line_the_shelf_cannot_cover_is_not_flagged(self):
        res = self.add(quantity=4)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertFalse(self.checkout().items.get().supply_chosen_over_stock)


# ---------------------------------------------------------------------------
# 3 · Margin: estimated vs realised, unknown costs and refunds apart
# ---------------------------------------------------------------------------

from store.models import Product  # noqa: E402
from store.tax_services import breakdown_from_total  # noqa: E402
from store.test_margin_analytics import MarginFixture  # noqa: E402
from store.tests import _prod  # noqa: E402


class MarginEstimatedRealisedTest(MarginFixture):
    """
    The DC1 fixture (A, B, C, D refunded …) plus, all paid, prices with IGV:

        G  web  Centro  1 Cargador 59,00  neto  50  costo  30 de REFERENCIA
        H  web  Centro  1 Parlante 236,00 neto 200  costo 150 pactado, RECIBIDO
        I  web  Centro  1 Parlante 236,00 neto 200  costo 150 pactado, sin recibir

    Estimado (costo conocido): A 200/120 · B 100/70 · G 50/30 · H 200/150 · I 200/150
        neto 750 · costo 520 · margen 230 · 230/750 = 30,67 %
    Realizado (costo confirmado: stock propio vendido o bajo pedido recibido): A · B · H
        neto 500 · costo 340 · margen 160 · 160/500 = 32,00 %
    Sólo estimado: G (referencia) e I (sin recibir): 2 líneas, neto 250
    Costo desconocido: C · 1 línea · neto 50 (fuera de los dos márgenes)
    Reembolsos: D · 1 pedido · 118,00 con IGV · 100,00 neto
    Ventas: 6 pedidos · brutas 944 · netas 800 (= 750 + 50)
    """

    def setUp(self):
        super().setUp()
        charger = _prod(self.company, 'Cargador', 'cargador-margen', price='59.00', inventory=0)
        Product.objects.filter(pk=charger.pk).update(reference_cost=D('30.00'))
        charger.refresh_from_db()
        self.entry(self.centro, charger, 5, None)
        self.web(self.centro, [(charger, 1)])                               # G
        speaker = _prod(self.company, 'Parlante', 'parlante-margen', price='236.00', inventory=0)
        self.on_order(speaker, 'received')                                  # H
        self.on_order(speaker, 'pending')                                   # I

    def on_order(self, product, supply_status):
        """A paid on-order line, its snapshots as the payment and the checkout froze them."""
        tax = breakdown_from_total(total=product.price, subtotal=product.price)
        order = Order.objects.create(
            company=self.company, fulfillment_branch=self.centro, customer_email='x@example.com',
            total=product.price, subtotal_amount=tax.subtotal, taxable_amount=tax.taxable_amount,
            tax_amount=tax.tax_amount, tax_rate=tax.tax_rate, status=Order.Status.PAID, paid=True,
            paid_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=order, product=product, quantity=1, price=product.price,
            unit_price_net=tax.taxable_amount, discount_net=D('0.00'), net_amount=tax.taxable_amount,
            unit_cost=D('150.0000'), cost_source=OrderItem.COST_SUPPLIER_OFFER,
            supply_origin=OrderItem.SUPPLY_SUPPLIER, supply_status=supply_status, lead_time_days=4,
        )
        return order

    def test_the_hand_computed_figures(self):
        res = self.get(branch='all')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['sales'], {'orders': 6, 'gross': '944.00', 'net': '800.00'})
        self.assertEqual(body['estimated'], {'lines': 5, 'net_sales': '750.00', 'cost': '520.00',
                                             'margin': '230.00', 'margin_percent': '30.67'})
        self.assertEqual(body['realised'], {'lines': 3, 'net_sales': '500.00', 'cost': '340.00',
                                            'margin': '160.00', 'margin_percent': '32.00'})
        self.assertEqual(body['estimated_only'], {'lines': 2, 'net_sales': '250.00', 'reference_cost_lines': 1,
                                                  'on_order_not_received_lines': 1})
        self.assertEqual(body['unknown_cost'], {'lines': 1, 'orders': 1, 'net_sales': '50.00'})
        self.assertEqual(body['refunds'], {'orders': 1, 'amount': '118.00', 'net': '100.00'})
        # The top-level figures are the estimated margin, as before.
        self.assertEqual((body['net_sales_with_cost'], body['cost_of_goods_sold'], body['margin'],
                          body['margin_percent']), ('750.00', '520.00', '230.00', '30.67'))
        self.assertEqual(body['pending_cost'], {'lines': 1, 'orders': 1, 'amount': '50.00'})
        # Estimated net + unknown net = net sales: nothing counted twice or lost.
        self.assertEqual(D(body['estimated']['net_sales']) + D(body['unknown_cost']['net_sales']),
                         D(body['sales']['net']))
        self.assertNotIn('utilidad', res.content.decode().lower())

    def test_a_failed_supply_is_not_realised(self):
        OrderItem.objects.filter(supply_status='received').update(supply_status='failed')
        body = self.get(branch='all').json()
        self.assertEqual((body['realised']['lines'], body['realised']['net_sales']), (2, '300.00'))
        self.assertEqual(body['estimated_only']['on_order_not_received_lines'], 2)

    def test_no_known_cost_means_no_percentage_not_zero(self):
        OrderItem.objects.update(unit_cost=None, cost_source='unknown')
        body = self.get(branch='all').json()
        self.assertIsNone(body['estimated']['margin_percent'])
        self.assertIsNone(body['realised']['margin_percent'])
        self.assertEqual(body['unknown_cost']['net_sales'], '800.00')


class SweepAgainstCheckoutRaceTest(TransactionTestCase):
    """
    A checkout extends a hold (row locked, not yet committed) while the sweep
    finds it expired: the sweep waits for the device's row and reads it again
    — it does not release the hold the checkout just bound.
    """

    def test_the_sweep_waits_for_the_checkout_and_leaves_the_hold(self):
        import threading
        import time

        from django.db import transaction as tx

        shop = DeviceShop()
        shop.build('dec-carrera-checkout')
        shop.receive(shop.new128, 2)
        unit = holds.hold(product=shop.new128, branch=shop.branch, session_key='carrito-a')
        StockUnit.objects.filter(pk=unit.pk).update(hold_until=timezone.now() - timedelta(minutes=1))
        locked = threading.Event()

        def checkout():
            try:
                with tx.atomic():
                    StockUnit.objects.filter(pk=unit.pk).update(hold_until=timezone.now() + timedelta(minutes=45))
                    locked.set()
                    time.sleep(1.5)
            finally:
                connection.close()

        thread = Thread(target=checkout)
        thread.start()
        self.assertTrue(locked.wait(timeout=10))
        released = holds.release_one_expired(unit.pk)
        thread.join(timeout=20)
        self.assertFalse(released)
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'reserved')
        self.assertEqual(_release_lines(shop.new128), 0)
