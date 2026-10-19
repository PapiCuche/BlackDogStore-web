"""
POST-DELIVERY-RETURN · phase 1 — the whole order comes back (owner, 2026-10-19; D1–D8).

  request  `sales.orders.manage`; a paid order whose goods LEFT (online, `ever_left`)
           or a till sale. Past `return_simple_days` after delivery, a superior.
           Nothing moves.
  receive  `inventory.adjust`, into a branch the person operates. Each device is
           named and must be one that left with THIS order; quantities whole. One
           `customer_return` line per line at the sale exit's cost, once. States:
           sellable / review (set aside) / damaged (written off); a quantity only
           sellable or damaged.
  close    a superior. Refund = paid − deduction (with reason), never above paid;
           the order is cancelled through the existing tail without restoring
           stock again: note voided, fiscal pending, commission voided or
           compensated, ONE pending refund. No payment row changes.
  withdraw before reception: the requester or a superior.
"""
from datetime import timedelta
from decimal import Decimal as D
from threading import Barrier, Thread

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import order_cancellation as cancelling
from store import order_returns as returns
from store import stock_unit_services as units
from store import unit_identification as picking
from store.fulfillment_test_support import walk_to
from store.models import (
    AdminAuditLog, BranchStock, CommissionAdjustment, Company, CompanySettings, FiscalDocument, Order,
    OrderCancellation, OrderItem, OrderRefund, OrderReturn, OrderReturnLine, OrderUnit, PaymentTransaction,
    Product, SalesCommission, SalesNote, StockMovement, StockUnit,
)
from store.test_financial_closure import _ClosureBase
from store.test_order_cancel import REASON, RETURN, PapersTest
from store.test_order_cancel_ux import MANAGE
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C
from store.tests import _m7_stock, _p2d_member, _prod

CUSTOMER_RETURN = 'customer_return'
WHY = 'El cliente devolvió el pedido completo'
INVENTORY = ['company.view', 'sales.orders.view', 'inventory.view', 'inventory.adjust']


class _ReturnBase(_ClosureBase):
    def setUp(self):
        super().setUp()
        self.keeper, _ = _p2d_member(self.company, 'almacen_devoluciones', INVENTORY)
        self.store_desk = APIClient()
        self.store_desk.force_authenticate(self.keeper)

    # -- orders ----------------------------------------------------------------------
    def delivered(self, *lines, devices=()):
        """A paid web order walked to «Entregado» through the real service."""
        order = self.web_order(*lines)
        for code in devices:
            picking.identify_for_order(order=order, code=code, method='scanner', actor=self.manager)
        walk_to(order, 'delivered', actor=self.manager, company=self.company)
        order.refresh_from_db()
        return order

    def phones(self, *imeis):
        self.receive(*[self.row(f'F2LXK1SER{i:02d}', imei, cost='3800.00') for i, imei in enumerate(imeis)])

    # -- endpoints ---------------------------------------------------------------------
    def url(self, order, step=''):
        return f'/api/admin/orders/{order.pk}/return/{step}?{self.q}'

    def post_request(self, order, client=None, kind='desistimiento', reason=WHY):
        return (client or self.desk).post(self.url(order), {'kind': kind, 'reason': reason}, format='json')

    def post_receive(self, order, lines, client=None, **body):
        return (client or self.store_desk).post(self.url(order, 'receive/'), {'lines': lines, **body}, format='json')

    def post_close(self, order, client=None, **body):
        body.setdefault('refund_method', 'transfer')
        return (client or self.superior_desk).post(self.url(order, 'close/'), body, format='json')

    def post_withdraw(self, order, client=None, reason='El cliente se quedó con el pedido'):
        return (client or self.desk).post(self.url(order, 'withdraw/'), {'reason': reason}, format='json')

    def block(self, order, client=None):
        return self.detail(order, client)['return_block']

    def line(self, order, product=None, **extra):
        item = order.items.get(product=product or self.cable)
        return {'order_item': item.pk, 'condition': 'sellable', **extra}

    def whole(self, order, condition='sellable'):
        """The lines of a cable-only order, all back in one state."""
        item = order.items.get(product=self.cable)
        return [{'order_item': item.pk, 'quantity': item.quantity, 'condition': condition}]

    def requested(self, order):
        response = self.post_request(order)
        self.assertEqual(response.status_code, 201, response.content)
        return order

    def received(self, order, lines=None, **body):
        self.requested(order)
        response = self.post_receive(order, lines if lines is not None else self.whole(order), **body)
        self.assertEqual(response.status_code, 200, response.content)
        return order

    def customer_returns(self, order):
        return StockMovement.objects.filter(order=order, movement_type=CUSTOMER_RETURN)


# -- which orders ----------------------------------------------------------------------

class EligibilityTest(_ReturnBase):
    def test_a_paid_web_order_that_was_delivered_is_open_to_a_return_and_nothing_moves(self):
        order = self.delivered((self.cable, 3))
        stock = self.cable_stock()
        block = self.block(order)
        self.assertEqual((block['eligible'], block['can_request'], block['superior_needed']), (True, True, False))
        self.assertEqual((block['window_days'], block['return'], block['paid_amount']), (7, None, '147.00'))

        response = self.post_request(order)

        self.assertEqual(response.status_code, 201, response.content)
        made = OrderReturn.objects.get(order=order)
        self.assertEqual((made.status, made.kind, made.reason, made.requested_by),
                         ('requested', 'desistimiento', WHY, self.manager))
        self.assertEqual(self.cable_stock(), stock)
        self.assertFalse(self.customer_returns(order).exists())
        order.refresh_from_db()
        self.assertEqual((order.status, order.fulfillment_status), ('paid', 'delivered'))
        body = response.json()['return_block']
        self.assertEqual((body['return']['status'], body['can_request'], body['can_receive']),
                         ('requested', False, False))         # the sales desk does not receive goods
        self.assertEqual([b['code'] for b in body['blockers']], ['return_exists'])

    def test_a_till_sale_is_delivered_at_birth_and_open_to_a_return(self):
        sale = self.till_sale()
        self.assertTrue(self.block(sale)['eligible'])
        self.assertEqual(self.post_request(sale).status_code, 201)

    def test_goods_that_never_left_are_cancelled_not_returned(self):
        order = self.web_order((self.cable, 1), fulfillment_status='preparing')
        block = self.block(order)
        self.assertFalse(block['eligible'])
        self.assertEqual([b['code'] for b in block['blockers']], ['goods_not_left'])

        response = self.post_request(order)

        self.assertEqual((response.status_code, response.json()['code']), (409, 'goods_not_left'))
        self.assertIn('Cancelar pedido', response.json()['detail'])
        self.assertFalse(OrderReturn.objects.exists())
        # A label alone is not «left»: the audit trail decides.
        labelled = self.web_order((self.cable, 1), fulfillment_status='delivered')
        self.assertEqual(self.post_request(labelled).json()['code'], 'goods_not_left')

    def test_unpaid_and_cancelled_orders_are_refused(self):
        unpaid = self.unpaid_order()
        self.assertEqual(self.post_request(unpaid).json()['code'], 'not_paid')
        cancelled = self.web_order((self.cable, 1))
        self.assertEqual(self.post_cancel(cancelled).status_code, 200)
        response = self.post_request(cancelled)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'already_cancelled'))
        self.assertFalse(OrderReturn.objects.exists())

    def test_kind_and_reason_are_required(self):
        order = self.delivered((self.cable, 1))
        for kind, reason, code in (('cambio', WHY, 'kind_invalid'), (None, WHY, 'kind_invalid'),
                                   ('otro', '  abcd ', 'reason_required'), ('otro', 'x' * 501, 'reason_too_long')):
            with self.subTest(kind=kind, reason=reason[:10] if reason else reason):
                response = self.post_request(order, kind=kind, reason=reason)
                self.assertEqual((response.status_code, response.json()['code']), (400, code))
        self.assertFalse(OrderReturn.objects.exists())

    def test_one_return_at_a_time_and_the_database_says_so(self):
        order = self.requested(self.delivered((self.cable, 1)))
        self.assertEqual(self.post_request(order).json()['code'], 'return_exists')
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderReturn.objects.create(company=self.company, order=order, kind='otro', reason=WHY)
        self.assertEqual(OrderReturn.objects.filter(order=order).count(), 1)

    def test_while_a_return_is_open_the_till_cannot_annul_the_sale(self):
        sale = self.requested(self.till_sale())
        response = self.post_cancel(sale, goods_returned=True)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'return_in_progress'))
        self.assertFalse(OrderCancellation.objects.filter(order=sale).exists())

    def test_the_cancellation_of_a_delivered_order_points_to_the_return(self):
        order = self.delivered((self.cable, 1))
        [blocker] = self.detail(order)['cancel_blockers']
        self.assertEqual(blocker['code'], 'goods_left')
        self.assertIn('requiere devolución', blocker['detail'])


# -- the window (D5) -----------------------------------------------------------------------

class WindowTest(_ReturnBase):
    def delivered_days_ago(self, days, *lines):
        order = self.delivered(*(lines or ((self.cable, 1),)))
        AdminAuditLog.objects.filter(
            action='order_fulfillment_status_changed', target_id=str(order.pk),
        ).update(created_at=timezone.now() - timedelta(days=days))
        return order

    def test_past_the_window_only_a_superior_registers_it(self):
        order = self.delivered_days_ago(8)
        block = self.block(order)
        self.assertTrue(block['superior_needed'])
        self.assertFalse(block['can_request'])
        self.assertEqual([b['code'] for b in block['blockers']], ['superior_required'])
        self.assertEqual([c['code'] for c in block['superior_conditions']], ['older_than_window'])

        response = self.post_request(order)

        self.assertEqual((response.status_code, response.json()['code']), (403, 'superior_required'))
        self.assertIn('7 días', response.json()['detail'])
        self.assertEqual([c['code'] for c in response.json()['conditions']], ['older_than_window'])
        self.assertFalse(OrderReturn.objects.exists())

        self.assertTrue(self.block(order, self.superior_desk)['can_request'])
        self.assertEqual(self.post_request(order, client=self.superior_desk).status_code, 201)
        audit = AdminAuditLog.objects.get(action='order_return_requested')
        self.assertEqual((audit.metadata['authority'], audit.metadata['superior_conditions'], audit.actor),
                         ('superior', ['older_than_window'], self.superior))

    def test_the_window_is_the_companys_and_counts_from_the_latest_delivery(self):
        order = self.delivered_days_ago(8)
        CompanySettings.objects.update_or_create(company=self.company, defaults={'return_simple_days': 10})
        self.company.refresh_from_db()
        self.assertEqual(self.post_request(order).status_code, 201)

    def test_a_till_sale_counts_from_when_it_was_paid(self):
        sale = self.aged(self.till_sale(), 9)
        self.assertEqual(self.post_request(sale).json()['code'], 'superior_required')

    def test_the_window_setting_is_bounded_and_takes_settings_manage(self):
        def patch(client, value):
            return client.patch(f'/api/admin/company-settings/?{self.q}', {'return_simple_days': value},
                                format='json')

        self.assertEqual(CompanySettings(company=self.company).return_simple_days, 7)
        self.assertEqual(patch(self.superior_desk, 3).status_code, 403)
        full, _ = _p2d_member(self.company, 'ajustes_devolucion', MANAGE + ['company.manage', 'settings.manage'])
        client = APIClient()
        client.force_authenticate(full)
        for bad in (-1, 366, 'x'):
            with self.subTest(bad):
                self.assertEqual(patch(client, bad).status_code, 400)
        self.assertEqual(patch(client, 0).json()['settings']['return_simple_days'], 0)


# -- who ------------------------------------------------------------------------------------

class PermissionsTest(_ReturnBase):
    def client_of(self, username, capabilities, **extra):
        user, _ = _p2d_member(self.company, username, capabilities, **extra)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_each_step_has_its_authority(self):
        order = self.delivered((self.cable, 2))
        reader = self.client_of('lector_dev', ['company.view', 'sales.orders.view'])
        # Request: orders.manage — not a reader, not the warehouse.
        self.assertEqual(self.post_request(order, client=reader).status_code, 403)
        self.assertEqual(self.post_request(order, client=self.store_desk).status_code, 403)
        self.requested(order)
        # Receive: inventory.adjust — not the sales desk, not even a superior without it.
        self.assertEqual(self.post_receive(order, self.whole(order), client=self.desk).status_code, 403)
        self.assertEqual(self.post_receive(order, self.whole(order), client=self.superior_desk).status_code, 403)
        self.assertEqual(self.post_receive(order, self.whole(order)).status_code, 200)
        # Close: a superior — not the sales desk, not the warehouse.
        refused = self.post_close(order, client=self.desk, refund_amount='98.00')
        self.assertEqual((refused.status_code, refused.json()['code']), (403, 'superior_required'))
        self.assertEqual(self.post_close(order, client=self.store_desk, refund_amount='98.00').status_code, 403)
        self.assertEqual(OrderReturn.objects.get(order=order).status, 'received')
        self.assertEqual(self.post_close(order, refund_amount='98.00').status_code, 200)

    def test_another_company_answers_like_nothing_on_every_step(self):
        order = self.delivered((self.cable, 1))
        foreign, _ = _p2d_member(self.other, 'ajeno_dev', MANAGE + INVENTORY + ['company.manage'])
        client = APIClient()
        client.force_authenticate(foreign)
        q = f'company={self.other.pk}'
        base = f'/api/admin/orders/{order.pk}/return/'
        self.assertEqual(client.get(f'{base}?{q}').status_code, 404)
        for step, body in (('', {'kind': 'otro', 'reason': WHY}), ('receive/', {'lines': []}),
                           ('close/', {'refund_amount': '49.00', 'refund_method': 'cash'}),
                           ('withdraw/', {'reason': WHY})):
            with self.subTest(step=step):
                self.assertEqual(client.post(f'{base}{step}?{q}', body, format='json').status_code, 404)
        self.assertFalse(OrderReturn.objects.exists())

    def test_a_branch_scoped_member_receives_only_into_their_branches(self):
        order = self.requested(self.delivered((self.cable, 2)))
        only_north = self.client_of('norte_dev', INVENTORY, branches=[self.branch_b])
        self.assertEqual(self.post_receive(order, self.whole(order), client=only_north).status_code, 404)
        only_centre = self.client_of('centro_dev', INVENTORY, branches=[self.branch_a])
        response = self.post_receive(order, self.whole(order), client=only_centre, branch=self.branch_b.pk)
        self.assertEqual((response.status_code, response.json()['code']), (400, 'branch_invalid'))
        foreign = self.post_receive(order, self.whole(order), branch=self.foreign_branch.pk)
        self.assertEqual(foreign.json()['code'], 'branch_invalid')
        self.assertFalse(self.customer_returns(order).exists())

        both = self.client_of('ambas_dev', INVENTORY, branches=[self.branch_a, self.branch_b])
        north_before = inventory.branch_quantity(self.branch_b, self.cable)
        self.assertEqual(self.post_receive(order, self.whole(order), client=both, branch=self.branch_b.pk).status_code,
                         200)
        self.assertEqual(inventory.branch_quantity(self.branch_b, self.cable), north_before + 2)
        self.assertEqual(self.customer_returns(order).get().branch, self.branch_b)


class LegacyBridgeTest(_ReturnBase):
    """On the pilot, without a Membership: sales registers, inventory receives, admin closes."""

    def test_each_legacy_role_does_its_own_step(self):
        from store.tests import _p60_paid_order, _p60_product, _p60_users

        users = _p60_users()
        product = _p60_product(name='Cable legacy dev', inventory=10, price='49.00')
        sale = _p60_paid_order(product, quantity=1, sales_channel='pos', fulfillment_status='delivered')
        inventory.record_sale_stock_movements(sale)
        item = sale.items.get()

        def post(role, step, body):
            client = APIClient()
            client.force_authenticate(users[role])
            return client.post(f'/api/admin/orders/{sale.pk}/return/{step}', body, format='json')

        request = {'kind': 'defectuoso', 'reason': WHY}
        self.assertEqual(post('inventory', '', request).status_code, 403)
        self.assertEqual(post('sales', '', request).status_code, 201)
        lines = {'lines': [{'order_item': item.pk, 'quantity': 1, 'condition': 'sellable'}]}
        self.assertEqual(post('sales', 'receive/', lines).status_code, 403)
        self.assertEqual(post('inventory', 'receive/', lines).status_code, 200)
        close = {'refund_amount': '49.00', 'refund_method': 'cash'}
        self.assertEqual(post('sales', 'close/', close).json()['code'], 'superior_required')
        self.assertEqual(post('admin', 'close/', close).status_code, 200)
        self.assertEqual(Order.objects.get(pk=sale.pk).status, 'cancelled')


# -- receiving: which goods ------------------------------------------------------------------

class ReceptionTest(_ReturnBase):
    def test_the_exact_device_that_left_is_the_one_that_comes_back(self):
        self.phones(IMEI_A, IMEI_B, IMEI_C)
        order = self.requested(self.delivered((self.phone, 1), devices=[IMEI_B]))
        other = self.delivered((self.phone, 1), devices=[IMEI_C])
        line = self.line(order, self.phone)

        for code, expected in ((IMEI_A, 'unit_not_of_order'),         # on the shelf, never sold
                               (IMEI_C, 'unit_not_of_order'),         # left with another order
                               ('NO-EXISTE-001', 'unit_not_of_order'),      # nobody's device
                               ('', 'unit_code_required')):
            with self.subTest(code=code):
                response = self.post_receive(order, [{**line, 'code': code}])
                self.assertEqual(response.json()['code'], expected, response.content)
        self.assertFalse(self.customer_returns(order).exists())
        self.assertEqual(StockUnit.objects.get(imei=IMEI_C).order_id, other.pk)

        response = self.post_receive(order, [{**line, 'code': IMEI_B}])

        self.assertEqual(response.status_code, 200, response.content)
        unit = StockUnit.objects.get(imei=IMEI_B)
        self.assertEqual((unit.status, unit.order_id, unit.branch), ('available', None, self.branch_a))
        self.assertTrue(OrderUnit.objects.get(order=order, unit=unit).left_at)   # the sale named it
        self.assert_invariant()

    def test_the_whole_order_comes_back_or_nothing_does(self):
        self.phones(IMEI_A, IMEI_B)
        order = self.requested(self.delivered((self.phone, 2), (self.cable, 3), devices=[IMEI_A, IMEI_B]))
        phone = self.line(order, self.phone)
        cable = self.line(order)
        cases = (
            ([{**phone, 'code': IMEI_A}, {**cable, 'quantity': 3}], 'lines_incomplete'),     # a device missing
            ([{**phone, 'code': IMEI_A}, {**phone, 'code': IMEI_B}, {**cable, 'quantity': 2}], 'lines_incomplete'),
            ([{**phone, 'code': IMEI_A}, {**phone, 'code': IMEI_B}, {**cable, 'quantity': 4}], 'lines_incomplete'),
            ([{**phone, 'code': IMEI_A}, {**phone, 'code': IMEI_A}, {**cable, 'quantity': 3}], 'lines_invalid'),
            ([{**phone, 'code': IMEI_A, 'quantity': 2}, {**cable, 'quantity': 3}], 'lines_invalid'),
            ([{'order_item': 999999, 'condition': 'sellable', 'quantity': 1}], 'lines_invalid'),
            ([], 'lines_required'),
            ([{**cable, 'quantity': 3, 'condition': 'perfecto'}], 'condition_invalid'),
        )
        for lines, code in cases:
            with self.subTest(code=code, lines=len(lines)):
                response = self.post_receive(order, lines)
                self.assertEqual((response.status_code, response.json()['code']), (400, code), response.content)
        self.assertFalse(StockMovement.objects.filter(movement_type=CUSTOMER_RETURN).exists())
        self.assertEqual(OrderReturn.objects.get(order=order).status, 'requested')

        response = self.post_receive(order, [{**phone, 'code': IMEI_A}, {**phone, 'code': IMEI_B},
                                             {**cable, 'quantity': 2}, {**cable, 'quantity': 1}])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.customer_returns(order).count(), 4)               # one per line

    def test_a_quantity_that_never_left_the_stock_does_not_come_into_it(self):
        order = self.requested(self.delivered((self.cable, 2)))
        StockMovement.objects.filter(order=order, movement_type='sale_exit').update(quantity=1)
        response = self.post_receive(order, self.whole(order))
        self.assertEqual((response.status_code, response.json()['code']), (409, 'stock_mismatch'))

    def test_nothing_is_received_without_a_return_registered(self):
        order = self.delivered((self.cable, 1))
        self.assertEqual(self.post_receive(order, self.whole(order)).json()['code'], 'no_return')


# -- receiving: the stock ---------------------------------------------------------------------

class StockTest(_ReturnBase):
    def setUp(self):
        super().setUp()
        self.mouse = _prod(self.company, 'Mouse', 'mouse-devolucion', price='50.00', inventory=0)
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.mouse.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=30, reason='Compra', unit_cost=D('20'))

    def test_one_customer_return_per_line_at_the_cost_the_sale_left_with(self):
        order = self.delivered((self.mouse, 3))
        sale = StockMovement.objects.get(order=order, movement_type='sale_exit')
        self.assertEqual(sale.unit_cost, D('20.0000'))
        # The average moves after the sale: the goods do not come back at it.
        inventory.create_stock_movement(
            branch=self.branch_a, product_id=self.mouse.pk, movement_type=StockMovement.PURCHASE_ENTRY,
            quantity=27, reason='Compra cara', unit_cost=D('60'))
        self.assertEqual(BranchStock.objects.get(branch=self.branch_a, product=self.mouse).average_cost,
                         D('40.0000'))
        item = order.items.get()

        self.received(order, [{'order_item': item.pk, 'quantity': 3, 'condition': 'sellable'}])

        back = self.customer_returns(order).get()
        self.assertEqual((back.quantity, back.branch, back.actor, back.is_entry), (3, self.branch_a, self.keeper, True))
        self.assertEqual((back.unit_cost, back.cost_source), (sale.unit_cost, sale.cost_source))
        self.assertEqual(back.metadata['cost_from_movement_ids'], [sale.pk])
        self.assertEqual((back.reference_type, back.reference_id), ('order_return', str(OrderReturn.objects.get().pk)))
        self.assertEqual(back.get_movement_type_display(), 'Devolución de cliente')
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.mouse), 57)
        line = OrderReturnLine.objects.get()
        self.assertEqual((line.movement, line.quantity, line.condition, line.unit), (back, 3, 'sellable', None))
        self.assertFalse(StockMovement.objects.filter(order=order, movement_type=RETURN).exists())

    def test_a_device_comes_back_at_its_own_cost(self):
        self.phones(IMEI_A)
        order = self.requested(self.delivered((self.phone, 1), devices=[IMEI_A]))
        self.assertEqual(self.post_receive(order, [{**self.line(order, self.phone), 'code': IMEI_A}]).status_code, 200)
        back = self.customer_returns(order).get()
        unit = StockUnit.objects.get(imei=IMEI_A)
        self.assertEqual((back.unit_cost, back.cost_source, back.metadata['unit_ids']), (D('3800.0000'), 'unit',
                                                                                        [unit.pk]))

    def test_receiving_twice_is_refused_and_nothing_moves_twice(self):
        order = self.delivered((self.mouse, 2))
        item = order.items.get()
        self.received(order, [{'order_item': item.pk, 'quantity': 2, 'condition': 'sellable'}])
        stock = inventory.branch_quantity(self.branch_a, self.mouse)

        again = self.post_receive(order, [{'order_item': item.pk, 'quantity': 2, 'condition': 'sellable'}])

        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_received'))
        self.assertEqual(inventory.branch_quantity(self.branch_a, self.mouse), stock)
        self.assertEqual(self.customer_returns(order).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='order_return_received').count(), 1)

    def test_a_device_received_into_another_branch_moves_there(self):
        self.phones(IMEI_A)
        order = self.requested(self.delivered((self.phone, 1), devices=[IMEI_A]))
        response = self.post_receive(order, [{**self.line(order, self.phone), 'code': IMEI_A}],
                                     branch=self.branch_b.pk)
        self.assertEqual(response.status_code, 200, response.content)
        unit = StockUnit.objects.get(imei=IMEI_A)
        self.assertEqual((unit.branch, unit.status), (self.branch_b, 'available'))
        self.assert_invariant(branch=self.branch_b)
        self.assert_invariant(branch=self.branch_a)


class ConditionTest(_ReturnBase):
    def device_back(self, condition):
        self.phones(IMEI_A)
        order = self.requested(self.delivered((self.phone, 1), devices=[IMEI_A]))
        response = self.post_receive(order, [{**self.line(order, self.phone), 'code': IMEI_A, 'condition': condition}])
        self.assertEqual(response.status_code, 200, response.content)
        return order, StockUnit.objects.get(imei=IMEI_A)

    def test_a_sellable_device_is_on_the_shelf(self):
        order, unit = self.device_back('sellable')
        self.assertEqual(unit.status, 'available')
        self.assertEqual(self.quantity(), 1)
        self.assert_invariant()

    def test_a_device_to_review_is_set_aside_until_somebody_releases_it(self):
        order, unit = self.device_back('review')
        self.assertEqual(unit.status, 'reserved')
        self.assertEqual(self.quantity(), 0)
        self.assert_invariant()
        kinds = list(StockMovement.objects.filter(product=self.phone, created_at__gte=order.paid_at)
                     .exclude(movement_type='sale_exit').order_by('pk').values_list('movement_type', 'reason'))
        self.assertEqual(kinds[0][0], CUSTOMER_RETURN)
        self.assertEqual(kinds[1][0], 'manual_exit')
        self.assertIn(f'En revisión por devolución del pedido #{order.pk}', kinds[1][1])

        units.release(unit, actor=self.keeper)
        self.assertEqual((StockUnit.objects.get(pk=unit.pk).status, self.quantity()), ('available', 1))

    def test_a_damaged_device_enters_and_is_written_off(self):
        order, unit = self.device_back('damaged')
        self.assertEqual(unit.status, 'written_off')
        self.assertEqual(self.quantity(), 0)
        self.assertTrue(StockMovement.objects.filter(product=self.phone, movement_type='damaged_exit').exists())
        self.assert_invariant()

    def test_a_quantity_is_sellable_or_damaged_never_to_review(self):
        order = self.requested(self.delivered((self.cable, 3)))
        response = self.post_receive(order, self.whole(order, 'review'))
        self.assertEqual((response.status_code, response.json()['code']), (400, 'condition_invalid'))
        self.assertEqual(self.cable_stock(), 17)

        item = order.items.get()
        self.assertEqual(self.post_receive(order, [
            {'order_item': item.pk, 'quantity': 2, 'condition': 'sellable'},
            {'order_item': item.pk, 'quantity': 1, 'condition': 'damaged'},
        ]).status_code, 200)

        self.assertEqual(self.cable_stock(), 19)
        self.assertEqual(self.customer_returns(order).count(), 2)
        damaged = StockMovement.objects.get(order=order, movement_type='damaged_exit')
        self.assertEqual((damaged.quantity, damaged.reference_type), (1, 'order_return'))


# -- closing ----------------------------------------------------------------------------------

class CloseTest(_ReturnBase):
    def ready(self, quantity=3):
        return self.received(self.delivered((self.cable, quantity)))        # 3 × 49.00 = 147.00

    def test_close_cancels_the_order_once_with_one_pending_refund_and_restores_nothing_again(self):
        order = self.ready()
        payment = PaymentTransaction.objects.create(
            order=order, provider='izipay', transaction_id='PAY-DEV', order_number='PAY-DEV', amount=D('147.00'),
            currency='PEN', environment='production', status=PaymentTransaction.Status.AUTHORIZED,
            confirmed_at=timezone.now())
        before = PaymentTransaction.objects.filter(pk=payment.pk).values().get()
        stock = self.cable_stock()

        response = self.post_close(order, refund_amount='137.00', deduction_amount='10.00',
                                   deduction_reason='Flete de vuelta')

        self.assertEqual(response.status_code, 200, response.content)
        order.refresh_from_db()
        self.assertEqual((order.status, order.fulfillment_status, order.paid), ('cancelled', 'cancelled', True))
        cancellation = OrderCancellation.objects.get(order=order)
        self.assertEqual((cancellation.goods_returned, cancellation.stock_restored, cancellation.cancelled_by,
                          cancellation.previous_fulfillment_status), (True, False, self.superior, 'delivered'))
        self.assertIn(WHY, cancellation.reason)
        made = OrderReturn.objects.get(order=order)
        self.assertEqual((made.status, made.paid_amount, made.deduction_amount, made.refund_amount,
                          made.deduction_reason, made.refund_method, made.cancellation, made.closed_by),
                         ('closed', D('147.00'), D('10.00'), D('137.00'), 'Flete de vuelta', 'transfer',
                          cancellation, self.superior))
        refund = OrderRefund.objects.get(order=order)
        self.assertEqual((refund.amount, refund.status, refund.method, refund.confirmed_at),
                         (D('137.00'), 'pending', 'transfer', None))
        self.assertEqual(self.cable_stock(), stock)                      # nothing restored twice
        self.assertFalse(StockMovement.objects.filter(order=order, movement_type=RETURN).exists())
        self.assertEqual(PaymentTransaction.objects.filter(pk=payment.pk).values().get(), before)
        audit = AdminAuditLog.objects.get(action='order_return_closed')
        self.assertEqual((audit.metadata['refund_id'], audit.metadata['cancellation_id'],
                          audit.metadata['refund_amount']), (refund.pk, cancellation.pk, '137.00'))
        cancelled = AdminAuditLog.objects.get(action='order_cancelled')
        self.assertEqual((cancelled.metadata['return_id'], cancelled.metadata['authority'],
                          cancelled.metadata['movements']), (made.pk, 'superior', []))

        again = self.post_close(order, refund_amount='137.00', deduction_amount='10.00',
                                deduction_reason='Flete de vuelta')
        self.assertEqual((again.status_code, again.json()['code']), (409, 'already_closed'))
        self.assertEqual((OrderRefund.objects.filter(order=order).count(),
                          OrderCancellation.objects.filter(order=order).count()), (1, 1))

    def test_the_amount_is_the_servers(self):
        order = self.ready()
        cases = (
            ({'refund_amount': '147.00', 'deduction_amount': '10.00'}, 'deduction_reason_required'),
            ({'refund_amount': '0.00', 'deduction_amount': '200.00', 'deduction_reason': 'Flete de vuelta'},
             'deduction_exceeds_paid'),
            ({'refund_amount': '140.00'}, 'amount_mismatch'),
            ({'refund_amount': '-1'}, 'amount_invalid'),
            ({'refund_amount': '10.001'}, 'amount_invalid'),
            ({}, 'amount_invalid'),
            ({'refund_amount': '147.00', 'refund_method': 'bitcoin'}, 'method_invalid'),
        )
        for body, code in cases:
            with self.subTest(code=code):
                response = self.post_close(order, **body)
                self.assertEqual((response.status_code, response.json()['code']), (400, code), response.content)
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')
        self.assertFalse(OrderRefund.objects.exists())
        self.assertEqual(OrderReturn.objects.get(order=order).status, 'received')

        response = self.post_close(order, refund_amount='147.00')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(OrderRefund.objects.get(order=order).amount, D('147.00'))

    def test_a_deduction_of_everything_records_no_refund(self):
        order = self.ready(1)
        response = self.post_close(order, refund_amount='0', deduction_amount='49.00',
                                   deduction_reason='Producto usado y sin caja')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(OrderRefund.objects.filter(order=order).exists())
        self.assertEqual(OrderReturn.objects.get(order=order).refund_amount, D('0.00'))

    def test_close_waits_for_the_goods(self):
        order = self.requested(self.delivered((self.cable, 1)))
        response = self.post_close(order, refund_amount='49.00')
        self.assertEqual((response.status_code, response.json()['code']), (409, 'not_received'))
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'paid')

    def test_close_never_confirms_the_refund_and_the_existing_evidence_flow_does(self):
        order = self.ready()
        self.post_close(order, refund_amount='147.00')
        refund = OrderRefund.objects.get(order=order)
        self.assertEqual(refund.status, 'pending')
        from store.test_order_cancel_ux import EVIDENCE

        response = self.post_confirm(order, refund, self.superior_desk, reference='TR-77', evidence=EVIDENCE,
                                     amount='147.00')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(OrderRefund.objects.get(pk=refund.pk).status, 'confirmed')

    def test_an_accrued_commission_is_voided(self):
        order = self.ready()
        commission = self.commission(order)
        self.post_close(order, refund_amount='147.00')
        commission.refresh_from_db()
        self.assertEqual(commission.status, SalesCommission.STATUS_VOIDED)
        self.assertFalse(CommissionAdjustment.objects.exists())

    def test_a_settled_commission_gets_one_compensating_adjustment(self):
        order = self.ready()
        commission = self.commission(order, status=SalesCommission.STATUS_SETTLED, settled_at=timezone.now())
        self.post_close(order, refund_amount='147.00')
        commission.refresh_from_db()
        self.assertEqual(commission.status, SalesCommission.STATUS_SETTLED)
        adjustment = CommissionAdjustment.objects.get()
        self.assertEqual((adjustment.amount, adjustment.cancellation), (-commission.amount,
                                                                         OrderCancellation.objects.get(order=order)))

    def test_the_sales_note_is_voided_and_a_live_boleta_left_pending_untouched(self):
        from store.sales_note_services import get_or_create_sales_note

        order = self.delivered((self.cable, 3))
        note, _ = get_or_create_sales_note(order, actor=self.manager)
        document = PapersTest.fiscal_document(self, order)
        snapshot = FiscalDocument.objects.filter(pk=document.pk).values().get()
        self.received(order)

        self.assertEqual(self.post_close(order, refund_amount='147.00').status_code, 200)

        self.assertEqual(SalesNote.objects.get(pk=note.pk).status, SalesNote.STATUS_VOID)
        self.assertEqual(FiscalDocument.objects.filter(pk=document.pk).values().get(), snapshot)
        cancellation = OrderCancellation.objects.get(order=order)
        self.assertEqual((cancellation.fiscal_pending, cancellation.sales_note_voided), (True, True))
        self.assertIn('B001-12', cancellation.fiscal_document)

    def test_a_till_sale_closes_the_same_way(self):
        sale = self.received(self.till_sale())
        self.assertEqual(self.post_close(sale, refund_amount='98.00', refund_method='cash').status_code, 200)
        self.assertEqual(Order.objects.get(pk=sale.pk).status, 'cancelled')
        self.assertEqual(AdminAuditLog.objects.get(action='order_cancelled').metadata['mode'], 'pos')


class DashboardTest(_ReturnBase):
    def test_a_closed_return_leaves_the_sales_and_shows_in_collections(self):
        analyst, _ = _p2d_member(self.company, 'analista_dev', ['company.view', 'sales.analytics.view', 'costs.view'])
        client = APIClient()
        client.force_authenticate(analyst)

        def today():
            body = client.get(f'/api/admin/sales/dashboard/?{self.q}').json()
            return body['kpis']['today'], body['collections']['today']

        order = self.received(self.delivered((self.cable, 3)))
        kpis, _ = today()
        self.assertEqual((kpis['revenue'], kpis['orders']), ('147.00', 1))      # received is not closed

        self.post_close(order, refund_amount='137.00', deduction_amount='10.00', deduction_reason='Flete de vuelta')

        kpis, collections = today()
        self.assertEqual((kpis['revenue'], kpis['orders']), ('0.00', 0))
        self.assertEqual(collections['live'], {'orders': 0, 'amount': '0.00'})
        self.assertEqual(collections['cancelled_paid'], {'orders': 1, 'amount': '147.00'})
        self.assertEqual(collections['refunds_pending'], '137.00')
        self.assertEqual(collections['cancelled_held'], '147.00')


# -- withdrawing ---------------------------------------------------------------------------------

class WithdrawTest(_ReturnBase):
    def test_the_requester_withdraws_it_and_a_new_one_may_be_registered(self):
        order = self.requested(self.delivered((self.cable, 1)))
        self.assertTrue(self.block(order)['can_withdraw'])
        refused = self.post_withdraw(order, reason='no')
        self.assertEqual(refused.json()['code'], 'reason_required')

        response = self.post_withdraw(order)

        self.assertEqual(response.status_code, 200, response.content)
        withdrawn = OrderReturn.objects.get(order=order)
        self.assertEqual((withdrawn.status, withdrawn.withdrawn_by, withdrawn.withdrawn_reason),
                         ('withdrawn', self.manager, 'El cliente se quedó con el pedido'))
        self.assertEqual(response.json()['return_block']['return']['status'], 'withdrawn')
        self.assertTrue(response.json()['return_block']['can_request'])
        self.assertEqual(self.post_request(order).status_code, 201)
        self.assertEqual(OrderReturn.objects.filter(order=order).count(), 2)

    def test_another_sales_person_may_not_a_superior_may(self):
        order = self.requested(self.delivered((self.cable, 1)))
        colleague, _ = _p2d_member(self.company, 'colega_dev', MANAGE)
        client = APIClient()
        client.force_authenticate(colleague)
        self.assertFalse(self.block(order, client)['can_withdraw'])
        response = self.post_withdraw(order, client=client)
        self.assertEqual((response.status_code, response.json()['code']), (403, 'not_the_requester'))
        self.assertEqual(self.post_withdraw(order, client=self.superior_desk).status_code, 200)

    def test_once_received_it_is_closed_not_withdrawn(self):
        order = self.received(self.delivered((self.cable, 1)))
        response = self.post_withdraw(order)
        self.assertEqual((response.status_code, response.json()['code']), (409, 'already_received'))
        self.assertEqual(OrderReturn.objects.get(order=order).status, 'received')


# -- what the panel and the record say -------------------------------------------------------------

class PanelHistoryAndAuditTest(_ReturnBase):
    def test_each_step_is_in_the_dispatch_history_and_the_audit_carries_no_device_numbers(self):
        self.phones(IMEI_A)
        order = self.requested(self.delivered((self.phone, 1), devices=[IMEI_A]))
        self.assertEqual(self.post_receive(order, [{**self.line(order, self.phone), 'code': IMEI_A}]).status_code, 200)
        self.assertEqual(self.post_close(order, refund_amount='4500.00').status_code, 200)

        history = self.detail(order)['dispatch']['history']
        steps = [(h['action'], h['label']) for h in history if h['action'].startswith('order_return')
                 or h['action'] == 'order_cancelled']
        self.assertEqual(steps, [
            ('order_return_requested', 'Devolución registrada'), ('order_return_received', 'Devolución recibida'),
            ('order_cancelled', 'Pedido cancelado'), ('order_return_closed', 'Devolución cerrada'),
        ])
        self.assertEqual([h['note'] for h in history if h['action'] == 'order_return_requested'], [WHY])
        unit = StockUnit.objects.get(imei=IMEI_A)
        for audit in AdminAuditLog.objects.filter(action__startswith='order_return'):
            with self.subTest(audit.action):
                self.assertNotIn(IMEI_A, str(audit.metadata))
                self.assertNotIn(unit.serial_number, str(audit.metadata))
                self.assertNotIn('rosa', str(audit.metadata).lower())
        received = AdminAuditLog.objects.get(action='order_return_received')
        self.assertEqual(received.metadata['lines'][0]['unit_id'], unit.pk)

    def test_the_block_per_role_and_the_device_numbers_masked(self):
        self.phones(IMEI_A)
        order = self.requested(self.delivered((self.phone, 1), devices=[IMEI_A]))
        reader, _ = _p2d_member(self.company, 'lector_bloque', ['company.view', 'sales.orders.view'])
        reading = APIClient()
        reading.force_authenticate(reader)

        as_reader = self.block(order, reading)
        self.assertEqual({k: as_reader[k] for k in ('can_request', 'can_receive', 'can_close', 'can_withdraw')},
                         dict.fromkeys(('can_request', 'can_receive', 'can_close', 'can_withdraw'), False))
        self.assertEqual(as_reader['blockers'], [])
        self.assertEqual(as_reader['return']['status'], 'requested')
        as_keeper = self.block(order, self.store_desk)
        self.assertTrue(as_keeper['can_receive'])
        self.assertIn({'id': self.branch_a.pk, 'name': self.branch_a.name}, as_keeper['receive_branches'])
        self.assertEqual(self.store_desk.get(self.url(order)).json(), as_keeper)

        self.post_receive(order, [{**self.line(order, self.phone), 'code': IMEI_A}])
        as_superior = self.block(order, self.superior_desk)
        self.assertEqual((as_superior['can_close'], as_superior['can_receive'], as_superior['can_withdraw']),
                         (True, False, False))
        self.assertFalse(self.block(order)['can_close'])                     # the sales desk is not a superior
        [line] = as_superior['return']['lines']
        self.assertEqual((line['condition'], line['quantity'], line['product_name']), ('sellable', 1, 'iPhone 16'))
        self.assertTrue(line['imei'].endswith(IMEI_A[-4:]))
        self.assertNotIn(IMEI_A, str(as_superior))
        self.assertEqual(as_superior['return']['received_by'], self.keeper.username)


# -- at the same instant ---------------------------------------------------------------------------

class ReceiveAtTheSameInstantTest(TransactionTestCase):
    """Two connections, one return. The order row decides."""

    def setUp(self):
        from store.models import Branch

        self.company = Company.objects.create(
            name='Devolución concurrente', legal_name='Devolución SAC', tax_id='20911111113', slug='return-conc')
        self.branch = self.company.branches.first() or Branch.objects.create(company=self.company, name='Centro')
        self.user, _ = _p2d_member(self.company, 'devuelve_conc', MANAGE + INVENTORY + ['company.manage'])
        self.cable = Product.objects.create(company=self.company, name='Cable', slug='cable-dev-conc',
                                            price=D('10.00'))
        BranchStock.objects.update_or_create(branch=self.branch, product=self.cable, defaults={'quantity': 10})
        self.order = Order.objects.create(
            company=self.company, fulfillment_branch=self.branch, customer_email='x@example.com',
            total=D('30.00'), status=Order.Status.PAID, paid=True, paid_at=timezone.now(), sales_channel='pos',
            fulfillment_status='delivered')
        self.item = OrderItem.objects.create(order=self.order, product=self.cable, quantity=3, price=D('10.00'))
        inventory.record_sale_stock_movements(self.order)
        returns.request_return(self.order, self.user, 'defectuoso', WHY)

    def race(self, step):
        barrier, outcomes = Barrier(2), {}

        def run(name):
            try:
                barrier.wait(timeout=5)
                outcomes[name] = step()
            except Exception as exc:  # noqa: BLE001 - the outcome IS the exception
                outcomes[name] = exc
            finally:
                connection.close()

        threads = [Thread(target=run, args=(name,)) for name in ('a', 'b')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        return outcomes

    def receive(self):
        return returns.receive_return(
            Order.objects.get(pk=self.order.pk), self.user, branch=self.branch.pk,
            lines=[{'order_item': self.item.pk, 'quantity': 3, 'condition': 'sellable'}])

    def close(self):
        return returns.close_return(Order.objects.get(pk=self.order.pk), self.user, refund_amount='30.00',
                                    refund_method='cash')

    def test_two_receptions_at_once_receive_once(self):
        outcomes = self.race(self.receive)

        won = [o for o in outcomes.values() if isinstance(o, OrderReturn)]
        lost = [o for o in outcomes.values() if isinstance(o, returns.OrderReturnError)]
        self.assertEqual((len(won), len(lost)), (1, 1), outcomes)
        self.assertEqual(lost[0].code, 'already_received')
        self.assertEqual(StockMovement.objects.filter(movement_type=CUSTOMER_RETURN).count(), 1)
        self.assertEqual(BranchStock.objects.get(branch=self.branch, product=self.cable).quantity, 10)

    def test_two_closes_at_once_close_once(self):
        self.receive()
        outcomes = self.race(self.close)

        lost = [o for o in outcomes.values() if isinstance(o, returns.OrderReturnError)]
        self.assertEqual(len(lost), 1, outcomes)
        self.assertIn(lost[0].code, ('already_closed', 'already_cancelled'))
        self.assertEqual((OrderCancellation.objects.count(), OrderRefund.objects.count()), (1, 1))
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, 'cancelled')


class DispatchWhileReturnedTest(_ReturnBase):
    """Once the goods are back in the shop, the dispatch path does not move on."""

    def sent(self):
        from store.models import OrderDelivery

        order = self.web_order(delivery_method=Order.DeliveryMethod.DELIVERY_AREQUIPA)
        OrderDelivery.objects.create(company=self.company, order=order, mode=OrderDelivery.Mode.LOCAL_DELIVERY)
        walk_to(order, 'shipped', actor=self.manager, company=self.company)
        return order

    def test_a_requested_return_still_lets_the_parcel_be_delivered(self):
        order = self.requested(self.sent())
        response = self.desk.patch(f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}',
                                   {'fulfillment_status': 'delivered'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_received_return_stops_the_dispatch(self):
        order = self.received(self.sent())
        response = self.desk.patch(f'/api/admin/orders/{order.pk}/fulfillment-status/?{self.q}',
                                   {'fulfillment_status': 'delivered'}, format='json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'return_received')
        detail = self.detail(order)
        self.assertEqual(detail['available_fulfillment_transitions'], [])
        self.assertIsNone(detail['dispatch']['forward'])
