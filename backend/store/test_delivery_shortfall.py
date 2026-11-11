"""
F1 · DELIVERY-SHORTFALL-GUARD — an order paid for goods the shelf did not have
is not handed over, sent or announced as ready until its stock has left.

What the investigation on `fc72201` reproduced (`docs/adr-catalog-commercial.md`
§11): a web payment for a quantity product whose last unit was already gone
leaves the order PAID with a shortfall — the money is captured, the sale exit
is not written (`record_sale_stock_movements(strict=False)`) and the only trace
is a line of `payment_error`. Nobody was told, and the order could still be
walked to «Entregado» with no stock ever leaving.

What these tests pin:
  * every route that moves an order to «Listo para retiro», «Enviado» or
    «Entregado» goes through `change_fulfillment_status`, and it refuses with
    409 `stock_shortfall` while the derived shortfall is positive — read on
    the LOCKED order, the same lock the reprocess takes;
  * the refusal names each product and how many units are missing, and leaves
    an audit entry (actor, order, lines) under the order's company;
  * the payment that leaves the shortfall tells the staff who can act — once,
    through the notification centre, inside the company and the branch scope —
    and writes one audit entry with the company;
  * once the shelf is restocked and the exit reprocessed, the handover goes on;
  * POS sales, on-order lines and serialized devices keep their own rules.

This is DELIVERY protection. It does not touch checkout, payments, refunds,
holds, carts or stock levels: an order still ends up paid with a shortfall,
exactly as before — it just cannot leave the shop that way.
"""
import ast
import pathlib
from decimal import Decimal
from types import SimpleNamespace
from threading import Barrier, Event, Thread
from unittest import mock

from django.contrib import admin as django_admin
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import get_resolver
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import order_fulfillment_services as fulfillment
from store import order_shortfall
from store.fulfillment_test_support import place_at
from store.models import (
    AdminAuditLog, BranchStock, CartItem, Company, Notification, NotificationEvent, Order, OrderItem,
    PaymentTransaction, Product, StockMovement,
)
from store.notification_events import COMMERCE_ORDER_STOCK_SHORTFALL
from store.test_order_cancel import _Base
from store.test_stock_units import IMEI_A
from store.test_supplier_offers import SupplyShop
from store.tests import _c1_sale, _m7_stock, _p2d_company, _p2d_member, _prod

FS = Order.FulfillmentStatus
HANDOVER = (FS.READY_FOR_PICKUP, FS.SHIPPED, FS.DELIVERED)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class _Shortfall(_Base):
    """A paid web order of `self.case`, two units ordered and none on the shelf."""

    def setUp(self):
        super().setUp()
        self.case = _prod(self.company, 'Funda MagSafe', 'funda-magsafe-f1', price='120.00', inventory=0)
        _m7_stock(self.branch_a, self.case, 0)
        self.adjuster, _ = _p2d_member(
            self.company, 'f1_almacen', ['company.view', 'sales.orders.view', 'inventory.view', 'inventory.adjust'])

    def short_order(self, *lines, delivery_method=Order.DeliveryMethod.PICKUP_STORE, **fields):
        order = self.web_order(*(lines or ((self.case, 2),)), delivery_method=delivery_method, **fields)
        self.assertTrue(inventory.order_stock_shortfall(order), 'the fixture must be short')
        return order

    def move(self, order, status, note='', client=None):
        return (client or self.desk).patch(
            f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}',
            {'fulfillment_status': status, 'note': note}, format='json')

    def restock(self, product, quantity):
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=product.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=quantity, reason='Reposición')

    def reprocess(self, order, client=None):
        return (client or _client(self.adjuster)).post(
            f'/api/admin/orders/{order.pk}/reprocess-stock-exit/?{self.q}', {}, format='json')

    def status_of(self, order):
        order.refresh_from_db()
        return order.fulfillment_status

    def assert_short_refusal(self, response, *, product=None, missing=2):
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(body['code'], 'stock_shortfall')
        product = product or self.case
        self.assertIn(product.name, body['detail'])
        rows = {row['product_id']: row for row in body['shortfall']}
        self.assertEqual(rows[product.pk]['shortfall'], missing)
        return body


# -- 0. the reproduction -------------------------------------------------------------

class ReproductionTest(_Shortfall):
    def test_a_short_order_is_not_delivered(self):
        """The finding of §11: preparing → ready → delivered with no sale exit."""
        order = self.short_order()
        self.assertEqual(self.move(order, FS.PREPARING).status_code, 200)      # getting it ready is free
        self.assert_short_refusal(self.move(order, FS.READY_FOR_PICKUP))
        self.assertEqual(self.status_of(order), FS.PREPARING)

        place_at(order, FS.READY_FOR_PICKUP)                                    # an order already announced
        self.assert_short_refusal(self.move(order, FS.DELIVERED))
        self.assertEqual(self.status_of(order), FS.READY_FOR_PICKUP)
        self.assertFalse(StockMovement.objects.filter(order=order, movement_type=StockMovement.SALE_EXIT).exists())

    def test_the_old_finding_is_flipped(self):
        order = self.short_order()
        place_at(order, FS.PREPARING)
        with self.assertRaises(fulfillment.FulfillmentBlocked) as raised:
            fulfillment.change_fulfillment_status(
                order=order, new_status=FS.READY_FOR_PICKUP, actor=self.manager, company=self.company)
        self.assertEqual(raised.exception.code, fulfillment.FulfillmentBlocked.STOCK_SHORTFALL)
        self.assertEqual(raised.exception.shortfall[0]['shortfall'], 2)


# -- 1. which statuses ---------------------------------------------------------------

class WhichStatusesTest(_Shortfall):
    def test_a_shipping_order_is_not_sent_nor_delivered(self):
        order = self.short_order(delivery_method=Order.DeliveryMethod.DELIVERY_AREQUIPA)
        place_at(order, FS.PREPARING)
        self.assert_short_refusal(self.move(order, FS.SHIPPED))
        place_at(order, FS.SHIPPED)                                             # legacy: it already left
        self.assert_short_refusal(self.move(order, FS.DELIVERED))
        self.assertEqual(self.status_of(order), FS.SHIPPED)

    def test_steps_inside_the_shop_stay_free(self):
        order = self.short_order()
        self.assertEqual(self.move(order, FS.PREPARING).status_code, 200)
        self.assertEqual(self.move(order, FS.PENDING, note='volvemos a esperar').status_code, 200)

    def test_the_order_detail_says_why_and_offers_nothing_that_fails(self):
        order = self.short_order()
        place_at(order, FS.PREPARING)
        detail = self.desk.get(f'/api/admin/orders/{order.pk}/?{self.q}').json()
        codes = [b['code'] for b in detail['dispatch']['blockers']]
        self.assertIn('stock_shortfall', codes)
        blocker = next(b for b in detail['dispatch']['blockers'] if b['code'] == 'stock_shortfall')
        self.assertIn(self.case.name, blocker['detail'])
        self.assertEqual(detail['stock_shortfall'][0]['shortfall'], 2)

    def test_no_blocker_while_the_next_step_stays_in_the_shop(self):
        order = self.short_order()
        detail = self.desk.get(f'/api/admin/orders/{order.pk}/?{self.q}').json()
        self.assertEqual(detail['dispatch']['forward'], FS.PREPARING)
        self.assertNotIn('stock_shortfall', [b['code'] for b in detail['dispatch']['blockers']])


# -- 2. the message, the audit, partial shortfalls --------------------------------

class RefusalContentTest(_Shortfall):
    def test_the_refusal_names_each_missing_line_in_spanish(self):
        order = self.short_order()
        place_at(order, FS.PREPARING)
        body = self.assert_short_refusal(self.move(order, FS.READY_FOR_PICKUP))
        self.assertIn('falta', body['detail'].lower())
        self.assertIn('2', body['detail'])
        self.assertEqual(body['pending'], 2)
        self.assertEqual(body['shortfall'][0]['ordered'], 2)
        self.assertEqual(body['shortfall'][0]['fulfilled'], 0)

    def test_every_blocked_attempt_is_audited_with_the_company(self):
        order = self.short_order()
        place_at(order, FS.PREPARING)
        self.move(order, FS.READY_FOR_PICKUP)
        self.move(order, FS.READY_FOR_PICKUP)
        rows = AdminAuditLog.objects.filter(action='order_handover_blocked_shortfall', target_id=str(order.pk))
        self.assertEqual(rows.count(), 2)
        row = rows.first()
        self.assertEqual((row.company_id, row.actor_id), (self.company.pk, self.manager.pk))
        self.assertEqual(row.metadata['requested_status'], FS.READY_FOR_PICKUP)
        self.assertEqual(row.metadata['lines'][0]['product_id'], self.case.pk)
        self.assertEqual(row.metadata['lines'][0]['shortfall'], 2)
        # A refused move writes no dispatch entry.
        self.assertFalse(AdminAuditLog.objects.filter(
            action='order_fulfillment_status_changed', target_id=str(order.pk),
            metadata__new_fulfillment_status=FS.READY_FOR_PICKUP).exists())

    def test_a_partial_shortfall_names_only_the_short_line(self):
        order = self.web_order((self.cable, 1), (self.case, 3), delivery_method=Order.DeliveryMethod.PICKUP_STORE)
        place_at(order, FS.PREPARING)
        body = self.assert_short_refusal(self.move(order, FS.READY_FOR_PICKUP), missing=3)
        self.assertEqual([row['product_id'] for row in body['shortfall']], [self.case.pk])
        self.assertNotIn(self.cable.name, body['detail'])


# -- 3. after the restock the handover goes on ------------------------------------

class AfterReprocessTest(_Shortfall):
    def test_restock_and_reprocess_then_deliver(self):
        order = self.short_order()
        place_at(order, FS.PREPARING)
        self.assert_short_refusal(self.move(order, FS.READY_FOR_PICKUP))

        self.restock(self.case, 1)                                             # 1 of the 2: the line waits whole
        response = self.reprocess(order)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['stock_shortfall'][0]['shortfall'], 2)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assert_short_refusal(self.move(order, FS.READY_FOR_PICKUP))

        self.restock(self.case, 1)
        self.assertEqual(self.reprocess(order).json()['stock_shortfall'], [])
        for status in (FS.READY_FOR_PICKUP, FS.DELIVERED):
            self.assertEqual(self.move(order, status).status_code, 200)
        self.assertEqual(self.status_of(order), FS.DELIVERED)
        self.assertEqual(
            sum(StockMovement.objects.filter(order=order, movement_type=StockMovement.SALE_EXIT)
                .values_list('quantity', flat=True)), 2)

    def test_the_reprocess_touches_no_payment(self):
        order = self.short_order()
        before = Order.objects.values('status', 'paid', 'paid_at', 'total').get(pk=order.pk)
        self.restock(self.case, 2)
        self.reprocess(order)
        self.assertEqual(Order.objects.values('status', 'paid', 'paid_at', 'total').get(pk=order.pk), before)


# -- 4. who sees what, who can act, tenants and branches --------------------------

class AuthorityTest(_Shortfall):
    def test_a_reader_sees_the_shortfall_and_cannot_move_or_reprocess(self):
        reader, _ = _p2d_member(self.company, 'f1_lector', ['company.view', 'sales.orders.view'])
        order = self.short_order()
        place_at(order, FS.PREPARING)
        detail = _client(reader).get(f'/api/admin/orders/{order.pk}/?{self.q}').json()
        self.assertEqual(detail['stock_shortfall'][0]['shortfall'], 2)
        self.assertFalse(detail['can_reprocess_stock_exit'])
        self.assertEqual(self.move(order, FS.READY_FOR_PICKUP, client=_client(reader)).status_code, 403)
        self.assertEqual(self.reprocess(order, client=_client(reader)).status_code, 403)
        self.assertFalse(AdminAuditLog.objects.filter(action='order_handover_blocked_shortfall').exists())

    def test_another_companys_order_does_not_exist(self):
        theirs, _ = _p2d_member(self.other, 'f1_ajeno', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        order = self.short_order()
        place_at(order, FS.PREPARING)
        response = _client(theirs).patch(
            f'/api/admin/orders/{order.pk}/fulfillment-status/?company={self.other.pk}',
            {'fulfillment_status': FS.READY_FOR_PICKUP}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.case.name, response.content.decode())
        self.assertEqual(self.status_of(order), FS.PREPARING)

    def test_a_member_of_another_branch_does_not_see_it(self):
        elsewhere, _ = _p2d_member(
            self.company, 'f1_norte', ['company.view', 'sales.orders.view', 'sales.orders.manage'],
            branches=[self.branch_b])
        order = self.short_order()
        place_at(order, FS.PREPARING)
        self.assertEqual(self.move(order, FS.READY_FOR_PICKUP, client=_client(elsewhere)).status_code, 404)


# -- 5. what is NOT blocked --------------------------------------------------------

class NoFalseBlockTest(_Shortfall):
    def test_a_covered_web_order_walks_to_delivered(self):
        order = self.web_order((self.cable, 2), delivery_method=Order.DeliveryMethod.PICKUP_STORE)
        for status in (FS.PREPARING, FS.READY_FOR_PICKUP, FS.DELIVERED):
            self.assertEqual(self.move(order, status).status_code, 200)

    def test_a_till_sale_is_untouched(self):
        cashier, _ = _p2d_member(self.company, 'f1_caja', ['company.view', 'sales.pos.use'])
        order, _ = _c1_sale(actor=cashier, company=self.company, branch=self.branch_a,
                            items=[{'product': self.cable.pk, 'quantity': 1}])
        self.assertEqual(order.fulfillment_status, FS.DELIVERED)
        self.assertEqual(inventory.order_stock_shortfall(order), [])
        self.assertFalse(AdminAuditLog.objects.filter(
            action__in=['order_handover_blocked_shortfall', 'order_stock_shortfall_flagged']).exists())

    def test_a_till_sale_still_refuses_what_the_shelf_lacks(self):
        cashier, _ = _p2d_member(self.company, 'f1_caja2', ['company.view', 'sales.pos.use'])
        with self.assertRaises(Exception):
            _c1_sale(actor=cashier, company=self.company, branch=self.branch_a,
                     items=[{'product': self.case.pk, 'quantity': 1}])
        self.assertFalse(Order.objects.filter(items__product=self.case).exists())

    def test_a_serialized_line_short_on_the_shelf_is_a_shortfall(self):
        order = self.short_order((self.phone, 1))
        place_at(order, FS.PREPARING)
        self.assert_short_refusal(self.move(order, FS.READY_FOR_PICKUP), product=self.phone, missing=1)

    def test_a_serialized_line_on_the_shelf_keeps_its_identification_rule(self):
        self.receive(self.row('SER-F1-1', IMEI_A))
        order = self.web_order((self.phone, 1), delivery_method=Order.DeliveryMethod.PICKUP_STORE)
        self.assertEqual(inventory.order_stock_shortfall(order), [])
        place_at(order, FS.PREPARING)
        self.assertEqual(self.move(order, FS.READY_FOR_PICKUP).status_code, 200)   # announced: devices set aside
        response = self.move(order, FS.DELIVERED)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'units_pending')                 # not a shortfall


class OnOrderLinesTest(SupplyShop, TestCase):
    """DC3: a line sold on order owes the shelf nothing until its goods arrive."""

    def setUp(self):
        self.build('f1-bajo-pedido')

    def test_an_on_order_line_waits_for_its_supplier_not_for_the_shelf(self):
        self.add_on_order(quantity=1)
        order = self.checkout()
        self.pay(order)
        order.refresh_from_db()
        self.assertEqual(inventory.order_stock_shortfall(order), [])
        place_at(order, FS.PREPARING)
        response = _client(self.staff).patch(
            f'/api/admin/orders/{order.pk}/fulfillment-status/',
            {'fulfillment_status': FS.READY_FOR_PICKUP}, format='json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'supply_pending')

    def test_once_received_it_is_handed_over(self):
        from store import supply_services as supply

        self.add_on_order(quantity=1)
        order = self.checkout()
        self.pay(order)
        supply.receive_line(order.items.get(), actor=self.staff)
        order.refresh_from_db()
        self.assertEqual(inventory.order_stock_shortfall(order), [])
        place_at(order, FS.PREPARING)
        for status in (FS.READY_FOR_PICKUP, FS.DELIVERED):
            response = _client(self.staff).patch(
                f'/api/admin/orders/{order.pk}/fulfillment-status/', {'fulfillment_status': status}, format='json')
            self.assertEqual(response.status_code, 200, response.content)


# -- 6. every route, and nothing around them ----------------------------------------

STORE = pathlib.Path(__file__).resolve().parent
WRITE_CALLS = {'update', 'create', 'get_or_create', 'update_or_create', 'bulk_create', 'bulk_update', 'Order'}

#: Every production function that writes `Order.fulfillment_status`, and why it
#: cannot hand over a short order. A new writer fails the scan below until it is
#: routed through `change_fulfillment_status` or added here with its reason.
KNOWN_WRITERS = {
    # THE choke point: both HTTP routes call `change_fulfillment_status`, whose
    # locked body this is, and it refuses a short order (checked below).
    ('order_fulfillment_services.py', '_change_locked'),
    # Writes CANCELLED only — the goods stay (or come back) in the shop.
    ('order_cancellation.py', '_commit'),
    # A superior's correction of an accidental «sent/delivered»: it only ever
    # restores a status that is NOT leaving (`left_marking.restore_target`).
    ('left_marking.py', 'correct'),
    # A till sale is born DELIVERED inside a strict transaction: a shelf that
    # cannot cover it raises and no order exists (on-order lines start PENDING).
    ('pos_services.py', 'create_pos_sale'),
}


def _writers():
    found = set()
    for path in sorted(STORE.glob('*.py')):
        if path.name.startswith('test') or path.name.endswith('_test_support.py'):
            continue
        tree = ast.parse(path.read_text())
        stack = []

        def visit(node):
            pushed = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if pushed:
                stack.append(node.name)
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Attribute) and t.attr == 'fulfillment_status' for t in targets):
                    found.add((path.name, stack[0] if stack else '<module>'))
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
                if name in WRITE_CALLS and any(kw.arg == 'fulfillment_status' for kw in node.keywords):
                    found.add((path.name, stack[0] if stack else '<module>'))
            for child in ast.iter_child_nodes(node):
                visit(child)
            if pushed:
                stack.pop()

        visit(tree)
    return found


class EveryRouteTest(_Shortfall):
    ROUTES = {'admin-order-fulfillment', 'v1-internal-order-fulfillment'}

    def test_only_the_known_writers_set_the_fulfillment_status(self):
        self.assertEqual(_writers(), KNOWN_WRITERS)

    def test_the_locked_body_has_one_caller(self):
        callers = set()
        for path in sorted(STORE.glob('*.py')):
            if path.name.startswith('test'):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.FunctionDef):
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.Call) and getattr(inner.func, 'id', getattr(
                                inner.func, 'attr', '')) == '_change_locked':
                            callers.add((path.name, node.name))
        self.assertEqual(callers, {('order_fulfillment_services.py', 'change_fulfillment_status')})

    def test_every_fulfilment_route_is_one_of_the_guarded_two(self):
        names = set()

        def walk(patterns):
            for pattern in patterns:
                if hasattr(pattern, 'url_patterns'):
                    walk(pattern.url_patterns)
                elif pattern.name and 'fulfillment' in pattern.name and 'branch' not in pattern.name:
                    names.add(pattern.name)

        walk(get_resolver().url_patterns)
        self.assertEqual(names, self.ROUTES)

    def test_neither_route_hands_over_a_short_order(self):
        order = self.short_order()
        manager_v1, _ = _p2d_member(
            self.company, 'f1_v1', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        routes = {
            'admin-order-fulfillment': lambda status: self.move(order, status),
            'v1-internal-order-fulfillment': lambda status: _client(manager_v1).patch(
                f'/api/v1/internal/{self.company.slug}/orders/{order.pk}/fulfillment/',
                {'fulfillment_status': status}, format='json'),
        }
        self.assertEqual(set(routes), self.ROUTES)
        for name, call in routes.items():
            for at, target in ((FS.PREPARING, FS.READY_FOR_PICKUP), (FS.READY_FOR_PICKUP, FS.DELIVERED)):
                with self.subTest(route=name, target=target):
                    place_at(order, at)
                    response = call(target)
                    self.assertEqual(response.status_code, 409, response.content)
                    self.assertEqual(response.json()['code'], 'stock_shortfall')
                    self.assertEqual(response.json()['shortfall'][0]['shortfall'], 2)
                    self.assertEqual(self.status_of(order), at)

    def test_the_django_admin_cannot_write_it(self):
        model_admin = django_admin.site._registry[Order]
        self.assertIn('fulfillment_status', model_admin.readonly_fields)
        self.assertFalse([a for a in (model_admin.actions or []) if a != 'delete_selected'])


# -- 7. the payment that leaves a shortfall tells somebody --------------------------

class _PaidShort(SupplyShop):
    """Two web orders for the last unit; the second payment finds the shelf empty."""

    def build_shop(self, slug):
        self.build(slug)
        Product.objects.filter(pk=self.case.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        self.case.refresh_from_db()
        self.stock(self.case, 1)
        self.manager, _ = _p2d_member(
            self.company, f'{slug}_ventas', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        self.adjuster, _ = _p2d_member(
            self.company, f'{slug}_almacen', ['company.view', 'sales.orders.view', 'inventory.adjust'])
        self.reader, _ = _p2d_member(self.company, f'{slug}_lector', ['company.view', 'sales.orders.view'])
        self.blind_adjuster, _ = _p2d_member(
            self.company, f'{slug}_ciego', ['company.view', 'inventory.view', 'inventory.adjust'])
        other_branch = self.company.branches.create(name='Otra sede', is_active=True)
        self.elsewhere, _ = _p2d_member(
            self.company, f'{slug}_otra', ['company.view', 'sales.orders.view', 'sales.orders.manage'],
            branches=[other_branch])
        self.foreign = _p2d_company(f'{slug}-ajena')
        self.foreigner, _ = _p2d_member(
            self.foreign, f'{slug}_ajeno', ['company.view', 'sales.orders.view', 'sales.orders.manage'])

    def order_for_last_unit(self, session):
        CartItem.objects.create(session_key=session, product=self.case, quantity=1)
        return self.checkout(session)


class PaymentAnnouncesShortfallTest(_PaidShort, TestCase):
    def setUp(self):
        self.build_shop('f1-aviso')

    def paid_pair(self):
        first, second = self.order_for_last_unit('s1'), self.order_for_last_unit('s2')
        with self.captureOnCommitCallbacks(execute=True):
            self.pay(first)
        with self.captureOnCommitCallbacks(execute=True):
            self.pay(second)
        first.refresh_from_db()
        second.refresh_from_db()
        return first, second

    def notices(self, order):
        return Notification.objects.filter(
            event__event_type=COMMERCE_ORDER_STOCK_SHORTFALL, target_type='order', target_id=order.pk)

    def test_one_notice_to_the_staff_who_can_act(self):
        first, second = self.paid_pair()
        self.assertEqual((first.status, second.status), (Order.Status.PAID, Order.Status.PAID))
        self.assertEqual(inventory.order_stock_shortfall(second)[0]['shortfall'], 1)
        self.assertFalse(self.notices(first).exists())
        told = set(self.notices(second).values_list('user_id', flat=True))
        self.assertEqual(told, {self.staff.pk, self.manager.pk, self.adjuster.pk})
        self.assertEqual(NotificationEvent.objects.filter(event_type=COMMERCE_ORDER_STOCK_SHORTFALL).count(), 1)
        notice = self.notices(second).first()
        self.assertEqual(notice.company_id, self.company.pk)
        self.assertEqual(notice.audience, Notification.Audience.INTERNAL)
        self.assertIn(f'#{second.pk}', notice.title + notice.body)

    def test_nobody_outside_the_company_or_the_branch_is_told(self):
        _first, second = self.paid_pair()
        told = set(self.notices(second).values_list('user_id', flat=True))
        for user in (self.reader, self.blind_adjuster, self.elsewhere, self.foreigner):
            self.assertNotIn(user.pk, told)
        self.assertFalse(Notification.objects.filter(company=self.foreign).exists())

    def test_one_audit_entry_with_the_company(self):
        _first, second = self.paid_pair()
        rows = AdminAuditLog.objects.filter(action='order_stock_shortfall_flagged', target_id=str(second.pk))
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.company_id, self.company.pk)
        self.assertIsNone(row.actor_id)
        self.assertEqual(row.metadata['lines'][0]['product_id'], self.case.pk)
        self.assertEqual(row.metadata['lines'][0]['shortfall'], 1)

    def test_replays_and_reprocesses_do_not_repeat_it(self):
        _first, second = self.paid_pair()
        from store.views import _SignedNotificationMixin

        attempt = PaymentTransaction.objects.get(order=second)
        replay = SimpleNamespace(signed=True, response_code='00', pay_method='CARD', authorization_code='A1',
                                 reference_number='R1', unique_id=f'U{second.pk}')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(_SignedNotificationMixin()._confirm(attempt, replay), 'already')   # a replay
        with self.captureOnCommitCallbacks(execute=True):
            inventory.reprocess_order_stock_exit(second, actor=self.staff)     # still short
        with self.captureOnCommitCallbacks(execute=True):
            order_shortfall.announce(second.pk)                                # anything that asks again
        self.assertEqual(NotificationEvent.objects.filter(event_type=COMMERCE_ORDER_STOCK_SHORTFALL).count(), 1)
        self.assertEqual(self.notices(second).count(), 3)
        self.assertEqual(AdminAuditLog.objects.filter(
            action='order_stock_shortfall_flagged', target_id=str(second.pk)).count(), 1)

    def test_a_failing_notice_never_undoes_the_payment(self):
        first = self.order_for_last_unit('s1')
        second = self.order_for_last_unit('s2')
        self.pay(first)
        with mock.patch('store.order_shortfall._announce', side_effect=RuntimeError('boom')):
            with self.captureOnCommitCallbacks(execute=True):
                self.pay(second)
        second.refresh_from_db()
        self.assertEqual((second.status, second.paid), (Order.Status.PAID, True))

    def test_the_shortfall_left_at_payment_blocks_the_handover(self):
        _first, second = self.paid_pair()
        place_at(second, FS.PREPARING)
        response = _client(self.manager).patch(
            f'/api/admin/orders/{second.pk}/fulfillment-status/',
            {'fulfillment_status': FS.READY_FOR_PICKUP}, format='json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'stock_shortfall')


# -- 8. concurrency: the order row decides -----------------------------------------

class HandoverAgainstReprocessTest(TransactionTestCase):
    """Real threads, real row locks. A short order is never delivered short."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL only: row locks are the point')
        cache.clear()
        self.company = Company.objects.create(
            name='Concurrente F1', legal_name='Concurrente F1 SAC', tax_id='20911111119', slug='f1-conc')
        self.branch = self.company.branches.first() or self.company.branches.create(name='Centro', is_active=True)
        self.manager, _ = _p2d_member(
            self.company, 'f1_conc_ventas', ['company.view', 'sales.orders.view', 'sales.orders.manage'])
        self.case = Product.objects.create(company=self.company, name='Funda', slug='funda-f1-conc',
                                           price=Decimal('10.00'))
        BranchStock.objects.update_or_create(branch=self.branch, product=self.case, defaults={'quantity': 0})
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=Decimal('20.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now(),
            delivery_method=Order.DeliveryMethod.PICKUP_STORE, fulfillment_status=FS.READY_FOR_PICKUP)
        OrderItem.objects.create(order=self.order, product=self.case, quantity=2, price=Decimal('10.00'))
        inventory.record_sale_stock_movements(self.order)
        self.assertTrue(inventory.order_stock_shortfall(self.order))
        inventory.create_stock_movement(
            branch=self.branch, product_id=self.case.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=2, reason='Reposición')

    def deliver(self):
        return fulfillment.change_fulfillment_status(
            order=Order.objects.get(pk=self.order.pk), new_status=FS.DELIVERED,
            actor=self.manager, company=self.company)

    def reprocess(self):
        return inventory.reprocess_order_stock_exit(Order.objects.get(pk=self.order.pk), actor=self.manager)

    def exits(self):
        return sum(StockMovement.objects.filter(order=self.order, movement_type=StockMovement.SALE_EXIT)
                   .values_list('quantity', flat=True))

    def test_a_delivery_waits_for_the_reprocess_that_holds_the_order(self):
        holding, release = Event(), Event()
        real = inventory.record_sale_stock_movements
        outcomes = {}

        def slow(order, **kwargs):
            holding.set()                      # the order row is locked by now
            release.wait(5)
            return real(order, **kwargs)

        def run(name, step):
            try:
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 — the outcome IS the exception
                outcomes[name] = exc
            finally:
                connection.close()

        with mock.patch.object(inventory, 'record_sale_stock_movements', side_effect=slow):
            first = Thread(target=run, args=('reprocess', self.reprocess))
            first.start()
            self.assertTrue(holding.wait(5))
            second = Thread(target=run, args=('deliver', self.deliver))
            second.start()
            second.join(0.5)
            self.assertTrue(second.is_alive(), 'the delivery must wait for the order lock')
            release.set()
            first.join(10)
            second.join(10)

        self.assertNotIsInstance(outcomes['reprocess'], Exception, outcomes)
        self.assertNotIsInstance(outcomes['deliver'], Exception, outcomes)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fulfillment_status, FS.DELIVERED)
        self.assertEqual(self.exits(), 2)

    def test_at_the_same_instant_it_is_never_delivered_short(self):
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                Order.objects.filter(pk=self.order.pk).update(fulfillment_status=FS.READY_FOR_PICKUP)
                StockMovement.objects.filter(order=self.order, movement_type=StockMovement.SALE_EXIT).delete()
                BranchStock.objects.filter(branch=self.branch, product=self.case).update(quantity=2)
                barrier, outcomes = Barrier(2), {}

                def run(name, step):
                    try:
                        barrier.wait(5)
                        outcomes[name] = step()
                    except Exception as exc:  # noqa: BLE001
                        outcomes[name] = exc
                    finally:
                        connection.close()

                threads = [Thread(target=run, args=(n, s)) for n, s in
                           (('deliver', self.deliver), ('reprocess', self.reprocess))]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(20)

                self.order.refresh_from_db()
                self.assertNotIsInstance(outcomes['reprocess'], Exception, outcomes)
                if self.order.fulfillment_status == FS.DELIVERED:
                    self.assertEqual(self.exits(), 2, 'delivered only once the stock left')
                else:
                    self.assertIsInstance(outcomes['deliver'], fulfillment.FulfillmentBlocked)
                    self.assertEqual(outcomes['deliver'].code, 'stock_shortfall')
                    self.assertEqual(self.order.fulfillment_status, FS.READY_FOR_PICKUP)
