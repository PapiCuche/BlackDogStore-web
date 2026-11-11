"""
SUPPLY-LOCK-SCOPE — a supply action and a web payment never wait on each other in a circle.

`SELECT … FOR UPDATE` with a JOIN locks EVERY joined row unless it names its own
table (`of=('self',)`). `supply_services._locked_line` used to lock the line's
PRODUCT that way, and `commit_order_lines` / `confirm_line` the offer's SUPPLIER.
Nobody needed those rows, but they were held to the end of the transaction:

  * «No se pudo abastecer» (or «Confirmar») held the product and asked for the
    offer; a payment of another order of that offer held the offer and, at
    COMMIT, asked for the product — PostgreSQL re-checks a foreign key
    (`FOR KEY SHARE` on store_product) when a row is updated twice in one
    transaction, and the payment updates its line twice. A cycle: one of the
    two was killed with «deadlock detected». When the payment lost, the money
    was captured and the order stayed pending until the gateway resent it.
  * «Recibir del proveedor» held the product before the branch stock; any stock
    movement of that product (a web payment from the shelf) takes the branch
    stock first and then writes the product. A cycle.
  * Two payments whose offers belong to two suppliers in opposite order locked
    the suppliers out of the offers' id order. A cycle.

Each race below forces its interleaving with hooks and events on real
connections (no timing luck): without the lock scope it ends in «deadlock
detected»; with it, both sides land and the books agree. The rest pins what
must keep holding around it: duplicates, already-paid orders, tenants, and a
transaction that loses a real deadlock (made by a foreign locker) rolling back
whole, so that its retry lands once.
"""
import ast
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from store import inventory_services as inventory
from store import supply_services as supply
from store.models import (
    AdminAuditLog, CartItem, Order, OrderItem, PaymentTransaction, Product, StockMovement, SupplierOffer,
)
from store.test_supplier_offers import SupplyShop, today
from store.test_supply_failure import counts
from store.tests import _prod

#: How long a forced step waits for the other side before it gives up (it never should).
WAIT = 15
DAY = timedelta(days=1)


def confirm(attempt):
    """The real `_confirm` of a gateway answer that passed its checks."""
    from store.views import _SignedNotificationMixin

    return _SignedNotificationMixin()._confirm(attempt, SimpleNamespace(
        signed=True, response_code='00', pay_method='CARD', authorization_code='A1', reference_number='R1',
        unique_id=f'U{attempt.pk}'))


def attempt_for(order, suffix=''):
    return PaymentTransaction.objects.create(
        order=order, provider='izipay', transaction_id=f'T{order.pk:018d}{suffix}', amount=order.total,
        currency='PEN')


def waited(event, what):
    if not event.wait(WAIT):
        raise AssertionError(f'gave up waiting for {what}')


@contextmanager
def after_call(module, name, then):
    """`module.name` runs as always, then `then()` runs in the same thread (and transaction)."""
    original = getattr(module, name)

    def hooked(*args, **kwargs):
        result = original(*args, **kwargs)
        then()
        return result

    with mock.patch.object(module, name, hooked):
        yield


def in_thread(name):
    return threading.current_thread().name == name


class Race:
    """Tasks on their own threads and connections; what each one ended with (a value or the exception)."""

    def __init__(self):
        self.outcomes, self.pids, self.done = {}, {}, {}

    def run(self, **tasks):
        threads = []
        for name, task in tasks.items():
            self.done[name] = threading.Event()
            threads.append(threading.Thread(target=self._run, args=(name, task), name=name, daemon=True))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        alive = [thread.name for thread in threads if thread.is_alive()]
        if alive:
            raise AssertionError(f'still running: {alive}')
        return self.outcomes

    def _run(self, name, task):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                self.pids[name] = cursor.fetchone()[0]
            self.outcomes[name] = task()
        except Exception as exc:  # noqa: BLE001 — the test reports it
            self.outcomes[name] = exc
        finally:
            self.done[name].set()
            connection.close()

    def blocked(self, name) -> bool:
        """
        Wait until `name` waits for a row lock (True) or has finished (False).
        `pg_locks` is read live — unlike pg_stat_activity, it is not frozen for
        the transaction that asks.
        """
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if self.done[name].is_set():
                return False
            if name in self.pids:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)',
                                   [self.pids[name]])
                    if cursor.fetchone()[0]:
                        return True
            time.sleep(0.02)
        raise AssertionError(f'{name} neither waited nor finished')


class LockShop(SupplyShop):
    """The DC3 accessory shop: an on-order line already paid (order F), and another order to pay."""

    def two_orders(self, slug, *, on_order=True, shelf=None):
        """
        F: one unit on order, paid (offer: 2 confirmed, 1 committed). The order
        to pay: one more unit of that offer (`on_order`) and/or one of `shelf()`.
        """
        self.build(slug)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(confirmed_quantity=2)
        self.add_on_order(quantity=1, session=f'{slug}-a')
        self.failing = self.checkout(f'{slug}-a')
        self.pay(self.failing)
        if on_order:
            self.add_on_order(quantity=1, session=f'{slug}-b')
        if shelf is not None:
            CartItem.objects.create(session_key=f'{slug}-b', product=shelf(), quantity=1)
        self.paying = self.checkout(f'{slug}-b')
        return self.failing.items.get()

    def shelf_product(self, stock=3):
        self.shelf = _prod(self.company, 'Funda MagSafe', f'{self.company.slug}-funda', price='50.00', inventory=0)
        self.stock(self.shelf, stock)
        return self.shelf

    def offer_of(self, product, supplier):
        return supply.create_offer(
            company=self.company, supplier_id=supplier.pk, product_id=product.pk, actor=self.staff,
            may_view_costs=True, agreed_cost='70.00', commercial_price='110.00', confirmed_quantity=3,
            valid_from=today() - DAY, valid_until=today() + 6 * DAY, lead_time_days=4)


class ShopRaceCase(TransactionTestCase):
    def setUp(self):
        self.shop = LockShop()
        self.race = Race()

    def assert_landed(self, outcomes, expected):
        self.assertEqual(outcomes, expected, {name: repr(value) for name, value in outcomes.items()})

    def assert_paid_once(self, order):
        order.refresh_from_db()
        self.assertEqual((order.status, order.paid), (Order.Status.PAID, True))
        self.assertEqual(list(order.payment_transactions.values_list('status', flat=True)),
                         [PaymentTransaction.Status.AUTHORIZED])


# ---------------------------------------------------------------------------
# The races that ended in «deadlock detected»
# ---------------------------------------------------------------------------

class SupplyActionAgainstPaymentDeadlockTest(ShopRaceCase):
    """Forced interleavings, each a deadlock before the lock scope; now both sides land."""

    def test_staff_fails_a_line_while_a_payment_of_the_same_offer_holds_it(self):
        """The production report: «No se pudo abastecer» answered 500 while a web payment committed."""
        shop = self.shop
        line = shop.two_orders('lock-falla')
        pay_has_offer, fail_has_line = threading.Event(), threading.Event()

        def pay_then():                                   # the payment holds the offer…
            if in_thread('pay'):
                pay_has_offer.set()
                waited(fail_has_line, 'the staff to hold the line')

        def fail_then():                                  # …the staff holds the line (and, before, its product)
            if in_thread('fail'):
                fail_has_line.set()

        def fail():
            waited(pay_has_offer, 'the payment to hold the offer')
            return supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status

        with after_call(supply, 'commit_order_lines', pay_then), after_call(supply, '_locked_line', fail_then):
            outcomes = self.race.run(fail=fail, pay=lambda: shop.pay(shop.paying))
        self.assert_landed(outcomes, {'fail': OrderItem.SUPPLY_FAILED, 'pay': 'paid'})
        # D3: 2 confirmed; the failed unit leaves confirmed and committed, the paid one stays committed.
        self.assertEqual(counts(shop.offer), (1, 1, 0))
        self.assertEqual(OrderItem.objects.get(order=shop.paying).supply_status, OrderItem.SUPPLY_PENDING)
        self.assert_paid_once(shop.paying)
        self.assertFalse(StockMovement.objects.filter(product=shop.case).exists())

    def test_a_payment_waiting_at_commit_is_not_the_one_killed(self):
        """
        The payment already waits at COMMIT when the staff asks for the offer:
        before the lock scope PostgreSQL killed the PAYMENT — money captured,
        order left pending. A mixed order (a shelf line too) pins the stock.
        """
        shop = self.shop
        line = shop.two_orders('lock-pago', shelf=shop.shelf_product)
        self.assertEqual(sorted(shop.paying.items.values_list('supply_origin', flat=True)), ['own', 'supplier'])
        pay_has_offer, fail_has_line = threading.Event(), threading.Event()

        def pay_then():
            if in_thread('pay'):
                pay_has_offer.set()
                waited(fail_has_line, 'the staff to hold the line')

        def fail_then():
            if in_thread('fail'):
                fail_has_line.set()
                if self.race.blocked('pay'):              # the payment waits at COMMIT…
                    time.sleep(0.3)                       # …so its deadlock check runs first

        def fail():
            waited(pay_has_offer, 'the payment to hold the offer')
            return supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status

        with after_call(supply, 'commit_order_lines', pay_then), after_call(supply, '_locked_line', fail_then):
            outcomes = self.race.run(fail=fail, pay=lambda: shop.pay(shop.paying))
        self.assert_landed(outcomes, {'fail': OrderItem.SUPPLY_FAILED, 'pay': 'paid'})
        self.assert_paid_once(shop.paying)
        self.assertEqual(counts(shop.offer), (1, 1, 0))
        # The shelf line left once; the on-order lines never touched a shelf.
        self.assertEqual(inventory.branch_quantity(shop.branch, shop.shelf), 2)
        self.assertEqual(Product.objects.get(pk=shop.shelf.pk).inventory, 2)
        self.assertEqual(StockMovement.objects.filter(order=shop.paying, movement_type='sale_exit').count(), 1)
        self.assertFalse(StockMovement.objects.filter(product=shop.case).exists())

    def test_staff_confirms_a_line_while_a_payment_of_the_same_offer_holds_it(self):
        shop = self.shop
        shop.build('lock-confirma')
        shop.add_on_order(quantity=1, session='lock-confirma-a')
        shop.failing = shop.checkout('lock-confirma-a')
        SupplierOffer.objects.filter(pk=shop.offer.pk).update(valid_from=today() - 5 * DAY,
                                                              valid_until=today() - DAY)
        shop.pay(shop.failing)                            # the offer expired before the money: «por confirmar»
        line = shop.failing.items.get()
        self.assertEqual(line.supply_status, OrderItem.SUPPLY_TO_CONFIRM)
        SupplierOffer.objects.filter(pk=shop.offer.pk).update(
            confirmed_quantity=2, valid_from=today() - DAY, valid_until=today() + 6 * DAY)
        shop.add_on_order(quantity=1, session='lock-confirma-b')
        shop.paying = shop.checkout('lock-confirma-b')
        pay_has_offer, confirm_has_line = threading.Event(), threading.Event()

        def pay_then():
            if in_thread('pay'):
                pay_has_offer.set()
                waited(confirm_has_line, 'the staff to hold the line')

        def confirm_then():
            if in_thread('confirm'):
                confirm_has_line.set()

        def confirm_line():
            waited(pay_has_offer, 'the payment to hold the offer')
            return supply.confirm_line(line, actor=shop.staff, offer_id=shop.offer.pk).supply_status

        with after_call(supply, 'commit_order_lines', pay_then), after_call(supply, '_locked_line', confirm_then):
            outcomes = self.race.run(confirm=confirm_line, pay=lambda: shop.pay(shop.paying))
        self.assert_landed(outcomes, {'confirm': OrderItem.SUPPLY_PENDING, 'pay': 'paid'})
        self.assertEqual(counts(shop.offer), (2, 2, 0))
        self.assertEqual(OrderItem.objects.get(order=shop.paying).supply_status, OrderItem.SUPPLY_PENDING)
        self.assert_paid_once(shop.paying)

    def test_staff_receives_a_line_while_the_shelf_sells_the_same_product(self):
        """
        Reception locked the product BEFORE the branch stock — the reverse of
        every stock movement (branch stock, then the product's total). A web
        payment from the shelf of the same product: a cycle.
        """
        shop = self.shop

        def stocked_case():
            shop.stock(shop.case, 2)                      # stock arrived meanwhile: the shelf sells it now
            return shop.case

        line = shop.two_orders('lock-recibe', on_order=False, shelf=stocked_case)
        self.assertEqual(shop.paying.items.get().supply_origin, OrderItem.SUPPLY_OWN)
        receive_has_line, pay_has_shelf = threading.Event(), threading.Event()

        def receive_then():
            if in_thread('receive'):
                receive_has_line.set()
                waited(pay_has_shelf, 'the payment to hold the branch stock')

        def pay_then():
            if in_thread('pay'):
                pay_has_shelf.set()

        def pay():
            waited(receive_has_line, 'the staff to hold the line')
            return shop.pay(shop.paying)

        with after_call(supply, '_locked_line', receive_then), \
                after_call(inventory, '_locked_branch_stocks', pay_then):
            outcomes = self.race.run(receive=lambda: supply.receive_line(line, actor=shop.staff).supply_status,
                                     pay=pay)
        self.assert_landed(outcomes, {'receive': OrderItem.SUPPLY_RECEIVED, 'pay': 'paid'})
        self.assert_paid_once(shop.paying)
        # 2 on the shelf; the reception enters 1 and sells it to its order; the payment sells 1.
        self.assertEqual(inventory.branch_quantity(shop.branch, shop.case), 1)
        self.assertEqual(Product.objects.get(pk=shop.case.pk).inventory, 1)
        self.assertEqual(StockMovement.objects.filter(order=shop.failing, movement_type='purchase_entry').count(), 1)
        self.assertEqual(StockMovement.objects.filter(order=shop.failing, movement_type='sale_exit').count(), 1)
        self.assertEqual(StockMovement.objects.filter(order=shop.paying, movement_type='sale_exit').count(), 1)
        self.assertEqual(counts(shop.offer), (2, 1, 1))   # receiving does not touch the offer

    def test_two_payments_whose_offers_cross_two_suppliers(self):
        """
        Offers are committed in id order (the module's lock order); their
        SUPPLIERS, locked with them, came in the opposite order for the other
        payment.
        """
        shop = self.shop
        shop.build('lock-proveedores')
        other = supply.create_supplier(company=shop.company, name='Importadora Norte', actor=shop.staff)
        products = []
        for index in range(4):
            product = _prod(shop.company, f'Accesorio {index}', f'lock-proveedores-{index}', price='120.00',
                            inventory=0)
            Product.objects.filter(pk=product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
            products.append(product)
        # Ids in this order: O1 (other), O2 (first), O3 (first), O4 (other).
        offers = [shop.offer_of(product, supplier)
                  for product, supplier in zip(products, (other, shop.supplier, shop.supplier, other))]
        orders = {}
        for name, picked in (('a', (0, 2)), ('b', (1, 3))):  # a: O1 then O3 · b: O2 then O4
            for index in picked:
                shop.add_on_order(products[index], quantity=1, session=f'lock-proveedores-{name}')
            orders[name] = shop.checkout(f'lock-proveedores-{name}')
        both_hold_one, first = threading.Barrier(2), set()

        def holding_the_first_offer():
            name = threading.current_thread().name
            if name in orders and name not in first:
                first.add(name)
                both_hold_one.wait(WAIT)

        with after_call(supply, '_offer_is_valid', holding_the_first_offer):
            outcomes = self.race.run(a=lambda: shop.pay(orders['a']), b=lambda: shop.pay(orders['b']))
        self.assert_landed(outcomes, {'a': 'paid', 'b': 'paid'})
        for offer in offers:
            self.assertEqual(counts(offer), (3, 1, 2))
        for order in orders.values():
            self.assert_paid_once(order)
            self.assertEqual(set(order.items.values_list('supply_status', flat=True)), {OrderItem.SUPPLY_PENDING})


# ---------------------------------------------------------------------------
# What already held, and must keep holding
# ---------------------------------------------------------------------------

class SupplyActionAgainstPaymentTest(ShopRaceCase):

    def test_the_staff_first_then_the_payment(self):
        shop = self.shop
        line = shop.two_orders('lock-falla-antes')
        fail_has_line = threading.Event()

        def fail_then():
            if in_thread('fail'):
                fail_has_line.set()

        def pay():
            waited(fail_has_line, 'the staff to hold the line')
            return shop.pay(shop.paying)

        with after_call(supply, '_locked_line', fail_then):
            outcomes = self.race.run(
                fail=lambda: supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status, pay=pay)
        self.assert_landed(outcomes, {'fail': OrderItem.SUPPLY_FAILED, 'pay': 'paid'})
        self.assertEqual(counts(shop.offer), (1, 1, 0))
        self.assert_paid_once(shop.paying)

    def test_staff_receives_a_line_while_a_payment_of_the_same_offer_holds_it(self):
        shop = self.shop
        line = shop.two_orders('lock-recibe-oferta')
        pay_has_offer, receive_has_line = threading.Event(), threading.Event()

        def pay_then():
            if in_thread('pay'):
                pay_has_offer.set()
                waited(receive_has_line, 'the staff to hold the line')

        def receive_then():
            if in_thread('receive'):
                receive_has_line.set()

        def receive():
            waited(pay_has_offer, 'the payment to hold the offer')
            return supply.receive_line(line, actor=shop.staff).supply_status

        with after_call(supply, 'commit_order_lines', pay_then), after_call(supply, '_locked_line', receive_then):
            outcomes = self.race.run(receive=receive, pay=lambda: shop.pay(shop.paying))
        self.assert_landed(outcomes, {'receive': OrderItem.SUPPLY_RECEIVED, 'pay': 'paid'})
        self.assertEqual(counts(shop.offer), (2, 2, 0))
        self.assertEqual(inventory.branch_quantity(shop.branch, shop.case), 0)
        self.assertEqual(StockMovement.objects.filter(product=shop.case).count(), 2)  # in and out, order F only
        self.assert_paid_once(shop.paying)

    def test_the_same_notification_twice_while_the_staff_fails_another_line(self):
        shop = self.shop
        line = shop.two_orders('lock-repetida')
        attempt = attempt_for(shop.paying)
        start = threading.Barrier(3)

        def at_once(task):
            def run():
                start.wait(WAIT)
                return task()
            return run

        outcomes = self.race.run(
            first=at_once(lambda: confirm(attempt)), second=at_once(lambda: confirm(attempt)),
            fail=at_once(lambda: supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status))
        self.assertEqual(sorted(map(str, outcomes.values())), ['already', 'failed', 'paid'], outcomes)
        self.assertEqual(counts(shop.offer), (1, 1, 0))    # committed once
        self.assert_paid_once(shop.paying)
        self.assertFalse(AdminAuditLog.objects.filter(action='payment_duplicate').exists())

    def test_a_second_charge_of_an_order_already_paid_while_its_line_fails(self):
        shop = self.shop
        line = shop.two_orders('lock-ya-pagado')
        second = attempt_for(shop.failing, suffix='2')
        fail_has_line = threading.Event()

        def fail_then():
            if in_thread('fail'):
                fail_has_line.set()
                self.race.blocked('charge')               # the charge queues on the order's lock

        def charge():
            waited(fail_has_line, 'the staff to hold the order')
            return confirm(second)

        with after_call(supply, '_locked_line', fail_then):
            outcomes = self.race.run(
                fail=lambda: supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status,
                charge=charge)
        self.assert_landed(outcomes, {'fail': OrderItem.SUPPLY_FAILED, 'charge': 'duplicate'})
        second.refresh_from_db()
        self.assertEqual(second.status, PaymentTransaction.Status.DUPLICATE)
        shop.failing.refresh_from_db()
        self.assertEqual(shop.failing.status, Order.Status.PAID)
        self.assertIn('No se pudo abastecer', shop.failing.payment_error)
        self.assertEqual(counts(shop.offer), (1, 0, 1))    # D3 released; the other order is not paid yet

    def test_two_tenants_at_once_share_nothing(self):
        shop, other = self.shop, LockShop()
        line = shop.two_orders('lock-tienda-a')
        other_line = other.two_orders('lock-tienda-b')
        with self.assertRaises(supply.SupplyError) as refused:
            supply.fail_line(line, actor=other.staff, reason='Ajena', company=other.company)
        self.assertEqual(refused.exception.code, 'not_found')
        outcomes = self.race.run(
            fail_a=lambda: supply.fail_line(line, actor=shop.staff, reason='Sin stock',
                                            company=shop.company).supply_status,
            pay_a=lambda: shop.pay(shop.paying),
            fail_b=lambda: supply.fail_line(other_line, actor=other.staff, reason='Sin stock',
                                            company=other.company).supply_status,
            pay_b=lambda: other.pay(other.paying))
        self.assert_landed(outcomes, {'fail_a': 'failed', 'pay_a': 'paid', 'fail_b': 'failed', 'pay_b': 'paid'})
        for each in (shop, other):
            self.assertEqual(counts(each.offer), (1, 1, 0))
            self.assert_paid_once(each.paying)
            self.assertEqual(
                list(AdminAuditLog.objects.filter(action='order_supply_failed', target_id=str(each.failing.pk))
                     .values_list('company_id', flat=True)), [each.company.pk])


# ---------------------------------------------------------------------------
# The one that loses rolls back whole; its retry lands once
# ---------------------------------------------------------------------------

class LoserRollsBackAndRetriesTest(ShopRaceCase):
    """
    Real deadlocks, made by a FOREIGN transaction that takes two rows in the
    opposite order. What is pinned is the loser: nothing of it stays, and its
    retry — the gateway's resend, the staff's second click — lands once.
    """

    def locks_in_turn(self, first, second, *, victim, has_first):
        """A foreign transaction: the `first` row, then — once `victim` waits for it — the `second`."""
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(*first)
            has_first.set()
            if self.race.blocked(victim):
                time.sleep(0.3)                           # the victim's deadlock check runs first
            cursor.execute(*second)
        return 'foreign'

    def test_a_payment_killed_by_a_deadlock_leaves_nothing_and_its_resend_pays_once(self):
        shop = self.shop
        shop.two_orders('lock-reintento-pago', shelf=shop.shelf_product)
        attempt = attempt_for(shop.paying)
        shelf_row = inventory.get_or_create_branch_stock(shop.branch, shop.shelf)
        pay_has_offer, foreign_has_shelf = threading.Event(), threading.Event()

        def pay_then():
            if in_thread('pay'):
                pay_has_offer.set()
                waited(foreign_has_shelf, 'the foreign transaction to hold the branch stock')

        def foreign():
            # The shelf row, then the offer: the payment takes the offer, then the shelf.
            waited(pay_has_offer, 'the payment to hold the offer')
            return self.locks_in_turn(
                ('SELECT 1 FROM store_branchstock WHERE id = %s FOR UPDATE', [shelf_row.pk]),
                ('SELECT 1 FROM store_supplieroffer WHERE id = %s FOR UPDATE', [shop.offer.pk]),
                victim='pay', has_first=foreign_has_shelf)

        with after_call(supply, 'commit_order_lines', pay_then):
            outcomes = self.race.run(pay=lambda: confirm(attempt), foreign=foreign)
        self.assertEqual(outcomes['foreign'], 'foreign', outcomes)
        self.assertIn('deadlock detected', str(outcomes['pay']), outcomes)
        # Nothing of the payment stayed: not paid, the attempt open, nothing committed, frozen or moved.
        shop.paying.refresh_from_db()
        self.assertEqual((shop.paying.status, shop.paying.paid), (Order.Status.PENDING_PAYMENT, False))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(counts(shop.offer), (2, 1, 1))
        on_order = OrderItem.objects.get(order=shop.paying, supply_origin=OrderItem.SUPPLY_SUPPLIER)
        self.assertEqual((on_order.supply_status, on_order.net_amount), ('', None))
        self.assertEqual(inventory.branch_quantity(shop.branch, shop.shelf), 3)
        self.assertFalse(StockMovement.objects.filter(order=shop.paying).exists())
        # The gateway's resend of the same attempt pays it, once.
        self.assertEqual(confirm(attempt), 'paid')
        self.assertEqual(confirm(attempt), 'already')
        self.assertEqual(counts(shop.offer), (2, 2, 0))
        self.assertEqual(inventory.branch_quantity(shop.branch, shop.shelf), 2)
        self.assertEqual(StockMovement.objects.filter(order=shop.paying).count(), 1)
        self.assert_paid_once(shop.paying)

    def test_a_failure_killed_by_a_deadlock_leaves_nothing_and_its_retry_releases_once(self):
        shop = self.shop
        line = shop.two_orders('lock-reintento-falla')
        foreign_has_offer = threading.Event()
        before = Order.objects.get(pk=shop.failing.pk).payment_error

        def fail():
            waited(foreign_has_offer, 'the foreign transaction to hold the offer')
            return supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status

        def foreign():
            # The offer, then the order: the reverse of «No se pudo abastecer».
            return self.locks_in_turn(
                ('SELECT 1 FROM store_supplieroffer WHERE id = %s FOR UPDATE', [shop.offer.pk]),
                ('SELECT 1 FROM store_order WHERE id = %s FOR UPDATE', [shop.failing.pk]),
                victim='fail', has_first=foreign_has_offer)

        outcomes = self.race.run(fail=fail, foreign=foreign)
        self.assertEqual(outcomes['foreign'], 'foreign', outcomes)
        self.assertIn('deadlock detected', str(outcomes['fail']), outcomes)
        self.assertEqual(counts(shop.offer), (2, 1, 1))
        self.assertEqual(OrderItem.objects.get(pk=line.pk).supply_status, OrderItem.SUPPLY_PENDING)
        self.assertEqual(Order.objects.get(pk=shop.failing.pk).payment_error, before)
        self.assertFalse(AdminAuditLog.objects.filter(action='order_supply_failed').exists())
        # The staff's second click releases it, once.
        self.assertEqual(supply.fail_line(line, actor=shop.staff, reason='Sin stock').supply_status,
                         OrderItem.SUPPLY_FAILED)
        with self.assertRaises(supply.SupplyError):
            supply.fail_line(line, actor=shop.staff, reason='Sin stock')
        self.assertEqual(counts(shop.offer), (1, 0, 1))
        self.assertEqual(AdminAuditLog.objects.filter(action='order_supply_failed').count(), 1)


# ---------------------------------------------------------------------------
# The SQL itself, and the source
# ---------------------------------------------------------------------------

def joined_locks(queries) -> list[str]:
    """Row-locking statements that JOIN another table without saying whose rows they lock."""
    return [
        query['sql'] for query in queries
        if ' FOR UPDATE' in query['sql'] and ' JOIN ' in query['sql'] and ' FOR UPDATE OF ' not in query['sql']
    ]


class SupplyLockScopeSqlTest(LockShop, TestCase):
    """What the supply actions and the payment lock: their own rows, never a joined product or supplier."""

    def setUp(self):
        self.build('lock-sql')
        SupplierOffer.objects.filter(pk=self.offer.pk).update(confirmed_quantity=5)

    def paid_line(self, session):
        self.add_on_order(quantity=1, session=session)
        order = self.checkout(session)
        with CaptureQueriesContext(connection) as payment:
            self.assertEqual(self.pay(order), 'paid')
        self.assertEqual(joined_locks(payment.captured_queries), [])
        return order.items.get()

    def test_payment_failure_reception_and_real_cost(self):
        failing = self.paid_line('lock-sql-a')
        with CaptureQueriesContext(connection) as failed:
            supply.fail_line(failing, actor=self.staff, reason='Sin stock')
        received = self.paid_line('lock-sql-b')
        with CaptureQueriesContext(connection) as receiving:
            supply.receive_line(received, actor=self.staff)
        with CaptureQueriesContext(connection) as costing:
            supply.record_real_cost(received, actor=self.staff, unit_cost='64.00')
        for queries in (failed, receiving, costing):
            self.assertEqual(joined_locks(queries.captured_queries), [])
        locked = [query['sql'] for query in failed.captured_queries if 'FOR UPDATE' in query['sql']]
        self.assertTrue(any('FOR UPDATE OF "store_orderitem"' in sql for sql in locked), locked)

    def test_confirmation(self):
        self.add_on_order(quantity=1, session='lock-sql-c')
        order = self.checkout('lock-sql-c')
        SupplierOffer.objects.filter(pk=self.offer.pk).update(valid_from=today() - 5 * DAY,
                                                              valid_until=today() - DAY)
        self.pay(order)
        SupplierOffer.objects.filter(pk=self.offer.pk).update(valid_from=today() - DAY,
                                                              valid_until=today() + 6 * DAY)
        with CaptureQueriesContext(connection) as confirming:
            supply.confirm_line(order.items.get(), actor=self.staff, offer_id=self.offer.pk)
        self.assertEqual(joined_locks(confirming.captured_queries), [])
        self.assertTrue(any('FOR UPDATE OF "store_supplieroffer"' in query['sql']
                            for query in confirming.captured_queries), 'the offer is still locked')


STORE = Path(__file__).resolve().parent


def unscoped_joined_locks(root: Path) -> list[str]:
    """Every `select_for_update()` without `of=` in a queryset chain that also has `select_related(...)`."""
    found = set()
    for path in sorted(root.rglob('*.py')):
        if 'test' in path.name or 'migrations' in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            calls, current = {}, node
            while isinstance(current, ast.Call) and isinstance(current.func, ast.Attribute):
                calls.setdefault(current.func.attr, current)
                current = current.func.value
            lock, related = calls.get('select_for_update'), calls.get('select_related')
            if lock is not None and related is not None and related.args \
                    and not any(keyword.arg == 'of' for keyword in lock.keywords):
                found.add(f'{path.relative_to(root.parent)}:{lock.lineno}')
    return sorted(found)


class NoUnscopedJoinedLockTest(SimpleTestCase):
    """
    A row lock with `select_related` names its own rows (`of=('self',)`):
    otherwise every joined parent — a product, a supplier, the COMPANY — is
    locked FOR UPDATE to the end of the transaction and blocks every foreign
    key check against it.
    """

    def test_none_in_the_backend(self):
        self.assertEqual(unscoped_joined_locks(STORE), [])
