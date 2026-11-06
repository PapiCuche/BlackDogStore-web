"""
POS-WEB-DEADLOCK — a till sale and a web payment confirmation never deadlock.

Real threads, real PostgreSQL row locks. The documented order of every writer
that touches supplier offers and shelves is (`supply_services`, module doc):

    the order row → offer rows (ascending id) → shelf rows (ascending
    `(branch_id, product_id)`, `inventory_services._locked_branch_stocks`) → units

Two ways a writer left that order were found by reading the code and are
reproduced here deterministically. A hook (patched in the TEST only) stops one
side right after its first lock, waits until the other side holds its first
lock — or, where it matters which side PostgreSQL aborts, until the other
backend is seen waiting on a lock in `pg_stat_activity` — and only then lets it
go on. Every wait has a timeout, so the same test also runs to the end once the
locks are taken in one order: then one side simply queues behind the other.

  1. OFFERS / SHELF. The till prices a used device under the lock of its shelf
     row (`pos_services.unit_prices(lock=True)`) BEFORE the supplier offer of
     an on-order line is locked (`commit_order_lines`, inside
     `record_sale_stock_movements`). The web confirmation takes the offer first
     and then the shelf. A till basket «used device + on-order line» against a
     web order of the same used model + the same offer closes the cycle.

  2. SHELF / SHELF. The web confirmation (non-strict) locks each shelf row as
     it writes that line's exit, in the order of the order's lines — the order
     of the cart — while the till locks the whole basket ascending. A web cart
     «B, A» (B with the higher id) against a till basket «A, B» closes the cycle.

What PostgreSQL does with a cycle: after `deadlock_timeout` (1 s by default)
the backend that has waited longest finds it and aborts ITS transaction with
`deadlock detected` (psycopg `errors.DeadlockDetected`, which Django raises as
`django.db.OperationalError`). Both sides here run whole inside one atomic
block, so the victim writes nothing.
"""
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.db import OperationalError, connection
from django.test import TransactionTestCase

from store import checkout_services as checkout
from store import inventory_services
from store import pos_services
from store import stock_unit_services as units
from store import supply_services
from store import unit_hold_services as holds
from store.models import (
    BranchStock, CartItem, Order, OrderItem, PaymentTransaction, Product, StockMovement, StockUnit, SupplierOffer,
)
from store.test_device_commerce import DeviceShop
from store.test_supplier_offers import today
from store.tests import _c1_sale, _p2d_member, _prod

#: How long a hook waits for the other side before going on. The deadlock
#: detector fires after 1 s; a side that queues correctly never needs this.
WAIT = 4


def is_deadlock(outcome) -> bool:
    return isinstance(outcome, OperationalError) and 'deadlock detected' in str(outcome)


def other_backend_waits_on_a_lock(timeout=WAIT) -> bool:
    """True once another backend of this database waits on a heavyweight lock (a row lock wait)."""
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND wait_event_type = 'Lock'")
            if cursor.fetchone()[0]:
                return True
            time.sleep(0.02)
    return False


class LockOrderShop(DeviceShop):
    """`DeviceShop`, plus a cashier, an accessory sold on order and its supplier's offer."""

    def build_shop(self, slug):
        self.build(slug)
        self.cashier, _ = _p2d_member(self.company, f'{slug}_cajero', ['company.view', 'sales.pos.use'])
        self.case = _prod(self.company, 'Cargador 20W', f'{slug}-cargador', price='120.00', inventory=0)
        Product.objects.filter(pk=self.case.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        self.case.refresh_from_db()
        self.supplier = supply_services.create_supplier(company=self.company, name='Mayorista', actor=self.staff)
        self.offer = supply_services.create_offer(
            company=self.company, supplier_id=self.supplier.pk, product_id=self.case.pk, actor=self.staff,
            may_view_costs=True, agreed_cost='70.00', commercial_price='110.00', confirmed_quantity=5,
            valid_from=today() - timedelta(days=1), valid_until=today() + timedelta(days=6), lead_time_days=4)

    def web_order(self, session='web'):
        items = list(CartItem.objects.filter(session_key=session).select_related('product').order_by('pk'))
        lines = checkout.lines_from_cart(self.company, self.branch, session, items)
        subtotal = checkout.validate_lines_and_subtotal(self.branch, lines)
        pricing = checkout.price_checkout(self.company, subtotal)
        order = checkout.create_pending_order(
            company=self.company, branch=self.branch, lines=lines, pricing=pricing,
            details=checkout.CustomerDetails(
                name='Ana', email='ana@example.com', phone='987654321', document_type='DNI',
                document_number='12345678', delivery_method='pickup', receipt_type='boleta',
                accepted_terms=True, accepted_warranty_policy=True),
            cart_session_key=session,
        )
        attempt = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id=f'T{order.pk:019d}', amount=order.total, currency='PEN')
        return order, attempt

    @staticmethod
    def confirm(attempt):
        """The real `_confirm` of a notification, with an answer that passed its checks."""
        from store.views import _SignedNotificationMixin

        return _SignedNotificationMixin()._confirm(attempt, SimpleNamespace(
            signed=True, response_code='00', pay_method='CARD', authorization_code='A1',
            reference_number='R1', unique_id=f'U{attempt.pk}'))

    def till(self, items, key):
        order, _created = _c1_sale(actor=self.cashier, company=self.company, branch=self.branch, items=items,
                                   idempotency_key=key)
        return order

    def race(self, web, pos, *, pos_starts_after):
        """Run `web` and `pos` in their own threads (and connections); `pos` starts once `pos_starts_after` is set."""
        outcomes = {}

        def run(name, step, gate=None):
            try:
                if gate is not None:
                    gate.wait(WAIT)
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 - the outcome IS the exception
                outcomes[name] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=('web', web), name='web'),
                   threading.Thread(target=run, args=('pos', pos, pos_starts_after), name='pos')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(set(outcomes), {'web', 'pos'}, outcomes)
        return outcomes

    def assert_no_deadlock(self, outcomes):
        dead = {name: str(exc).splitlines()[0] for name, exc in outcomes.items() if is_deadlock(exc)}
        self.assertEqual(dead, {}, f'PostgreSQL aborted a side with a deadlock: {outcomes}')
        failed = {name: repr(exc) for name, exc in outcomes.items() if isinstance(exc, Exception)}
        self.assertEqual(failed, {}, outcomes)


class OfferShelfLockOrderTest(LockOrderShop, TransactionTestCase):
    """1. A till basket «used device + on-order line» against a web payment of the same model and offer."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Row locks and deadlock detection: PostgreSQL only.')
        self.build_shop('posdl-oferta')
        self.web_unit, self.pos_unit = self.receive(self.used128, 2)
        units.update_unit(self.web_unit, sale_price='1000.00')
        units.update_unit(self.pos_unit, sale_price='900.00')
        holds.hold(product=self.used128, branch=self.branch, session_key='web',
                   unit_public_id=str(self.web_unit.public_id))
        CartItem.objects.create(session_key='web', product=self.case, quantity=1,
                                supply_origin=CartItem.SUPPLY_SUPPLIER)
        self.order, self.attempt = self.web_order()
        self.basket = [
            {'product': self.used128.pk, 'quantity': 1, 'units': [{'id': self.pos_unit.pk, 'method': 'scanner'}]},
            {'product': self.case.pk, 'quantity': 1, 'on_order': True},
        ]

    def interleave(self, *, victim):
        """
        The web takes the offer and stops; the till starts, takes the shelf in
        `unit_prices` and goes on; the web goes on to the shelf. `victim` says
        which side is made to wait FIRST — the one PostgreSQL then aborts.
        """
        web_has_offer, pos_has_shelf = threading.Event(), threading.Event()
        real_commit, real_prices = supply_services.commit_order_lines, pos_services.unit_prices

        def commit(order, *args, **kwargs):
            real_commit(order, *args, **kwargs)
            if threading.current_thread().name == 'web':
                web_has_offer.set()
                pos_has_shelf.wait(WAIT)
                if victim == 'pos':
                    other_backend_waits_on_a_lock()      # the till is already waiting: it is found first

        def prices(*args, **kwargs):
            out = real_prices(*args, **kwargs)
            if threading.current_thread().name == 'pos' and kwargs.get('lock'):
                pos_has_shelf.set()
                if victim == 'web':
                    other_backend_waits_on_a_lock()      # the web is already waiting: it is found first
            return out

        with mock.patch.object(supply_services, 'commit_order_lines', commit), \
                mock.patch.object(pos_services, 'unit_prices', prices):
            return self.race(lambda: self.confirm(self.attempt), lambda: self.till(self.basket, 'posdl-oferta-1'),
                             pos_starts_after=web_has_offer)

    def assert_both_sold(self, outcomes):
        self.assert_no_deadlock(outcomes)
        self.assertEqual(outcomes['web'], 'paid')
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).committed_quantity, 2)
        sold = dict(StockUnit.objects.filter(pk__in=[self.web_unit.pk, self.pos_unit.pk]).values_list('pk', 'status'))
        self.assertEqual(sold, {self.web_unit.pk: 'sold', self.pos_unit.pk: 'sold'})
        self.assertEqual(BranchStock.objects.get(branch=self.branch, product=self.used128).quantity, 0)
        self.assertEqual(Order.objects.filter(pos_idempotency_key='posdl-oferta-1').count(), 1)

    def test_the_till_waiting_first_is_not_aborted(self):
        self.assert_both_sold(self.interleave(victim='pos'))

    def test_the_web_payment_waiting_first_is_not_aborted(self):
        self.assert_both_sold(self.interleave(victim='web'))


class ShelfShelfLockOrderTest(LockOrderShop, TransactionTestCase):
    """2. A web cart «B, A» against a till basket «A, B» (A has the lower id)."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Row locks and deadlock detection: PostgreSQL only.')
        self.build_shop('posdl-estante')
        self.low = _prod(self.company, 'Funda', 'posdl-funda', price='50.00', inventory=0)
        self.high = _prod(self.company, 'Mica', 'posdl-mica', price='30.00', inventory=0)
        self.assertLess(self.low.pk, self.high.pk)
        for product in (self.low, self.high):
            inventory_services.create_stock_movement(
                branch=self.branch, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY, quantity=5,
                reason='Compra')
        # The cart's order is the order of the lines: the higher id first.
        CartItem.objects.create(session_key='web', product=self.high, quantity=1)
        CartItem.objects.create(session_key='web', product=self.low, quantity=1)
        self.order, self.attempt = self.web_order()
        self.basket = [{'product': self.low.pk, 'quantity': 1}, {'product': self.high.pk, 'quantity': 1}]

    def test_a_web_cart_in_another_order_than_the_till_does_not_deadlock(self):
        web_has_first = threading.Event()
        real_movement = inventory_services.create_stock_movement
        calls = []

        def movement(*args, **kwargs):
            out = real_movement(*args, **kwargs)
            if threading.current_thread().name == 'web' and not calls:
                calls.append(kwargs.get('product_id'))
                web_has_first.set()
                other_backend_waits_on_a_lock()          # the till holds A and waits for B
            return out

        with mock.patch.object(inventory_services, 'create_stock_movement', movement):
            outcomes = self.race(lambda: self.confirm(self.attempt),
                                 lambda: self.till(self.basket, 'posdl-estante-1'), pos_starts_after=web_has_first)
        self.assert_no_deadlock(outcomes)
        self.assertEqual(outcomes['web'], 'paid')
        for product in (self.low, self.high):
            self.assertEqual(BranchStock.objects.get(branch=self.branch, product=product).quantity, 3)
        self.assertEqual(OrderItem.objects.filter(order__pos_idempotency_key='posdl-estante-1').count(), 2)


# ---------------------------------------------------------------------------
# What a deadlock victim leaves behind — the web side (passes before and after the proposal)
# ---------------------------------------------------------------------------

from store.test_payment_reconcile import ReconcileBase  # noqa: E402

DEADLOCK = OperationalError('deadlock detected')


class AWebConfirmationAbortedByADeadlockTest(ReconcileBase):
    """
    The IPN whose confirmation PostgreSQL aborted answers 500 and writes
    nothing: the order waits, the attempt stays PENDING, the money is at the
    gateway. What pays it afterwards is the gateway's replay of the IPN or the
    reconciliation (`reconcile_payments`, every 5 minutes, «Mi Cuenta Web» only).
    """

    def setUp(self):
        super().setUp()
        self.client.raise_request_exception = False

    def aborted(self):
        with mock.patch('store.views.record_sale_stock_movements', side_effect=DEADLOCK):
            response = self.notify(self.fake.ipn(self.paid_answer()))
        self.assertEqual(response.status_code, 500)
        self.assert_still_waiting()

    def paid_answer(self):
        return self.fake.answer(order_id=self.attempt.transaction_id, amount_cents=29800)

    def test_the_notification_answers_500_and_nothing_is_written(self):
        self.aborted()
        self.assertEqual(self.attempt.response_code, '')
        self.assertFalse(CartItem.objects.filter(session_key=self.session_key).count() == 0)

    def test_the_replayed_notification_pays_once(self):
        self.aborted()
        form = self.fake.ipn(self.paid_answer())
        self.assertEqual(self.notify(form).status_code, 200)
        self.assertEqual(self.notify(form).status_code, 200)
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.attempt.status, PaymentTransaction.Status.AUTHORIZED)
        self.assertEqual(self.stock(), 3)
        self.assertEqual(StockMovement.objects.filter(order=self.order, movement_type='sale_exit').count(), 1)

    def test_without_a_replay_the_reconciliation_pays_it(self):
        self.aborted()
        self.fake.hold(self.attempt.transaction_id, self.paid())
        self.assertEqual(self.ask().paid, 1)
        self.refresh()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.stock(), 3)

    def test_a_reconciliation_run_that_meets_the_deadlock_stops_and_the_next_one_pays(self):
        self.fake.hold(self.attempt.transaction_id, self.paid())
        with mock.patch('store.views.record_sale_stock_movements', side_effect=DEADLOCK), \
                self.assertRaises(OperationalError):
            self.ask()
        self.assert_still_waiting()
        self.assertEqual(self.ask().paid, 1)
        self.refresh()
        self.assertTrue(self.order.paid)


# ---------------------------------------------------------------------------
# …and the till side
# ---------------------------------------------------------------------------

from django.test import TestCase  # noqa: E402


class ATillSaleAbortedByADeadlockTest(LockOrderShop, TestCase):
    """
    The till's sale is one transaction: aborted, nothing of it exists — no
    order, no commitment, no exit, the device still on the shelf. The view
    answers 500 and the till keeps the basket AND its key
    (`pos-in-flight.ts · keyAfterFailure`), so charging again sells it once.
    """

    def setUp(self):
        self.build_shop('posdl-caja')
        self.unit, = self.receive(self.used128, 1)
        units.update_unit(self.unit, sale_price='900.00')
        self.basket = [
            {'product': self.used128.pk, 'quantity': 1, 'units': [{'id': self.unit.pk, 'method': 'scanner'}]},
            {'product': self.case.pk, 'quantity': 1, 'on_order': True},
        ]

    def test_nothing_is_written_and_the_same_key_sells_once(self):
        with mock.patch.object(inventory_services, 'record_sale_stock_movements', side_effect=DEADLOCK), \
                self.assertRaises(OperationalError):
            self.till(self.basket, 'posdl-caja-1')
        self.assertFalse(Order.objects.filter(pos_idempotency_key='posdl-caja-1').exists())
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).committed_quantity, 0)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status, 'available')
        order = self.till(self.basket, 'posdl-caja-1')
        self.assertEqual(Order.objects.filter(pos_idempotency_key='posdl-caja-1').count(), 1)
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).committed_quantity, 1)
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.status, self.unit.order_id), ('sold', order.pk))
