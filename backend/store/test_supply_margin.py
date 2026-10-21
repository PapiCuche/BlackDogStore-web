"""
SUPPLIER-MARGIN — decisión D2 del propietario (vinculante).

  * Margen ESTIMADO: el costo pactado congelado en la línea bajo pedido
    (`OrderItem.unit_cost`), como hasta ahora; nunca se reescribe.
  * Margen REALIZADO: el costo REAL con el que la mercadería entró al recibirla
    (`OrderItem.received_unit_cost`, instantánea tomada en `receive_line`
    bajo el bloqueo). Sin costo real (no recibida, o recibida antes de que
    existiera la instantánea) la línea es «costo real pendiente»: no cuenta
    como margen realizado y se lista aparte (líneas y venta neta).
  * Recibida sin `costs.view`: entró al Kardex al costo pactado, pero nadie
    registró su costo real → «costo real pendiente» (`received_cost_source=
    'agreed'`, `received_unit_cost` vacío) hasta que alguien con `costs.view`
    lo registre (`POST …/supply/<línea>/real-cost/`). Recibir con el
    campo «Costo real» vacío tampoco lo confirma: queda pendiente (D2).
  * Las líneas de stock propio no cambian. Cancelados y reembolsados, fuera
    como hasta ahora. Nada de esto viaja sin `costs.view`.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import supply_services as supply
from store.models import CartItem, Order, OrderItem, SupplierOffer
from store.test_supply_reception import IMEIS, NO_COST_CAPS, ReceptionShop, as_client
from store.tests import _p2d_member

D = Decimal


class MarginShop(ReceptionShop):
    def build(self, slug='sdf2-margen'):
        super().build(slug)
        self.analyst, _ = _p2d_member(self.company, f'{slug}_analista',
                                      ['company.view', 'sales.analytics.view', 'costs.view'])

    def paid_order(self, product=None, quantity=1, session='carrito-m'):
        order = super().paid_order(product, quantity, session)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now())
        return order

    def own_order(self, quantity=1, session='propio'):
        self.stock(self.case, quantity)                       # own units at 60
        CartItem.objects.create(session_key=session, product=self.case, quantity=quantity)
        order = self.checkout(session)
        self.pay(order)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now())
        return order

    def margin(self):
        res = as_client(self.analyst).get('/api/admin/sales/margin/?days=7')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    @staticmethod
    def net(order):
        return sum((item.net_amount for item in order.items.all()), D('0.00'))


class RealisedMarginTest(MarginShop, TestCase):
    def setUp(self):
        self.build()

    def test_own_stock_lines_are_unchanged(self):
        order = self.own_order(2)
        line = order.items.get()
        self.assertEqual((line.supply_origin, line.cost_source, line.unit_cost), ('own', 'average', D('60.0000')))
        self.assertIsNone(line.received_unit_cost)
        body = self.margin()
        self.assertEqual((D(body['estimated']['cost']), D(body['realised']['cost'])), (D('120.00'), D('120.00')))
        self.assertEqual(body['realised']['lines'], 1)
        self.assertEqual(body['real_cost_pending']['lines'], 0)

    def test_received_with_a_real_cost_realises_at_it_and_estimates_at_the_agreed(self):
        order = self.paid_order(self.case, 2)
        supply.receive_line(order.items.get(), actor=self.staff, unit_cost='65.50')
        line = order.items.get()
        self.assertEqual((line.unit_cost, line.received_unit_cost, line.received_cost_source),
                         (D('70.0000'), D('65.5000'), 'real'))
        self.assertIsNotNone(line.received_at)
        body = self.margin()
        net = self.net(order)
        self.assertEqual(D(body['estimated']['cost']), D('140.00'))
        self.assertEqual(D(body['cost_of_goods_sold']), D('140.00'), 'the headline stays the estimate')
        self.assertEqual(D(body['realised']['cost']), D('131.00'))
        self.assertEqual(D(body['realised']['margin']), net - D('131.00'))
        self.assertEqual(body['real_cost_pending']['lines'], 0)

    def test_a_line_not_received_is_real_cost_pending_never_realised(self):
        order = self.paid_order(self.case, 2)
        body = self.margin()
        self.assertEqual(D(body['estimated']['cost']), D('140.00'))
        self.assertEqual((body['realised']['lines'], D(body['realised']['cost'])), (0, D('0.00')))
        pending = body['real_cost_pending']
        self.assertEqual((pending['lines'], pending['orders'], D(pending['net_sales'])), (1, 1, self.net(order)))
        self.assertEqual((pending['not_received_lines'], pending['received_without_real_cost_lines']), (1, 0))

    def test_a_line_received_before_the_snapshot_is_pending_not_backfilled(self):
        order = self.paid_order(self.case, 1)
        supply.receive_line(order.items.get(), actor=self.staff, unit_cost='65.00')
        OrderItem.objects.filter(order=order).update(received_unit_cost=None, received_cost_source='', received_at=None)
        body = self.margin()
        self.assertEqual(body['realised']['lines'], 0)
        self.assertEqual((body['real_cost_pending']['lines'], body['real_cost_pending']['received_without_real_cost_lines']),
                         (1, 1))
        self.assertIsNone(order.items.get().received_unit_cost, 'reading the margin writes nothing')

    def test_received_without_costs_view_is_real_cost_pending(self):
        order = self.paid_order(self.case, 2)
        no_costs, _ = _p2d_member(self.company, 'sdf2_m_sin_costos', NO_COST_CAPS)
        supply.receive_line(order.items.get(), actor=no_costs, unit_cost='1.00')
        line = order.items.get()
        self.assertEqual((line.received_unit_cost, line.received_cost_source), (None, 'agreed'))
        body = self.margin()
        self.assertEqual(body['realised']['lines'], 0)
        self.assertEqual(body['real_cost_pending']['received_without_real_cost_lines'], 1)

    def test_costs_view_receiving_with_the_real_cost_empty_stays_pending(self):
        """D2 «si falta costo real → pendiente»: an empty field is not a confirmation of the agreed cost."""
        order = self.paid_order(self.case, 2)
        supply.receive_line(order.items.get(), actor=self.staff)
        line = order.items.get()
        self.assertEqual((line.received_unit_cost, line.received_cost_source), (None, 'agreed'))
        body = self.margin()
        self.assertEqual(body['realised']['lines'], 0)
        self.assertEqual(body['real_cost_pending']['received_without_real_cost_lines'], 1)
        supply.record_real_cost(order.items.get(), actor=self.staff, unit_cost='70.00')
        self.assertEqual(D(self.margin()['realised']['cost']), D('140.00'))

    def test_devices_without_a_cost_each_and_no_line_cost_stay_pending(self):
        order = self.paid_order(self.phone, 2)
        supply.receive_line(order.items.get(), actor=self.staff, units=[
            {'serial_number': 'MRG0011', 'imei': IMEIS[33], 'cost': '2450.00'},
            {'serial_number': 'MRG0012', 'imei': IMEIS[34]},                 # entered at the agreed cost
        ])
        line = order.items.get()
        self.assertEqual((line.received_unit_cost, line.received_cost_source), (None, 'agreed'))

    def test_failed_lines_are_not_pending_forever_and_to_confirm_is_said_apart(self):
        failed = self.paid_order(self.case, 1, session='f1')
        supply.fail_line(failed.items.get(), actor=self.staff, reason='El proveedor no tiene')
        to_confirm = self.paid_order(self.case, 1, session='f2')
        OrderItem.objects.filter(order=to_confirm).update(supply_status=OrderItem.SUPPLY_TO_CONFIRM)
        self.paid_order(self.case, 1, session='f3')                        # pending
        received = self.paid_order(self.case, 1, session='f4')
        supply.receive_line(received.items.get(), actor=self.staff)       # no real cost
        pending = self.margin()['real_cost_pending']
        self.assertEqual((pending['lines'], pending['not_received_lines'], pending['to_confirm_lines'],
                          pending['received_without_real_cost_lines']), (3, 1, 1, 1))

    def test_estimated_only_says_not_received_and_without_real_cost_apart(self):
        self.paid_order(self.case, 1, session='e1')
        received = self.paid_order(self.case, 1, session='e2')
        supply.receive_line(received.items.get(), actor=self.staff)
        only = self.margin()['estimated_only']
        self.assertEqual((only['lines'], only['on_order_not_received_lines'], only['on_order_without_real_cost_lines']),
                         (2, 1, 1))

    def test_devices_realise_at_the_mean_of_their_real_costs(self):
        order = self.paid_order(self.phone, 2)
        supply.receive_line(order.items.get(), actor=self.staff, units=[
            {'serial_number': 'MRG0001', 'imei': IMEIS[37], 'cost': '2450.00'},
            {'serial_number': 'MRG0002', 'imei': IMEIS[38]},
        ], unit_cost='2400.00')
        line = order.items.get()
        self.assertEqual((line.unit_cost, line.received_unit_cost, line.received_cost_source),
                         (D('2500.0000'), D('2425.0000'), 'real'))
        body = self.margin()
        self.assertEqual((D(body['estimated']['cost']), D(body['realised']['cost'])), (D('5000.00'), D('4850.00')))

    def test_the_snapshot_does_not_follow_the_offer_or_a_second_receive(self):
        order = self.paid_order(self.case, 1)
        supply.receive_line(order.items.get(), actor=self.staff, unit_cost='66.00')
        SupplierOffer.objects.filter(pk=self.offer.pk).update(agreed_cost=D('1'))
        with self.assertRaises(supply.SupplyError):
            supply.receive_line(order.items.get(), actor=self.staff, unit_cost='10.00')
        line = order.items.get()
        self.assertEqual((line.unit_cost, line.received_unit_cost), (D('70.0000'), D('66.0000')))

    def test_cancelled_and_refunded_orders_stay_out(self):
        cancelled = self.paid_order(self.case, 1, session='c1')
        supply.receive_line(cancelled.items.get(), actor=self.staff, unit_cost='60.00')
        refunded = self.paid_order(self.case, 1, session='c2')
        Order.objects.filter(pk=cancelled.pk).update(status=Order.Status.CANCELLED)
        Order.objects.filter(pk=refunded.pk).update(status=Order.Status.REFUNDED)
        body = self.margin()
        self.assertEqual((body['realised']['lines'], body['real_cost_pending']['lines'], body['estimated']['lines']),
                         (0, 0, 0))

    def test_estimated_only_adds_up_with_realised(self):
        self.own_order(1, session='p1')
        received = self.paid_order(self.case, 1, session='m1')
        supply.receive_line(received.items.get(), actor=self.staff, unit_cost='65.00')
        self.paid_order(self.case, 1, session='m2')                       # not received
        body = self.margin()
        self.assertEqual(body['estimated']['lines'], 3)
        self.assertEqual(body['realised']['lines'], 2)
        self.assertEqual(body['estimated_only']['lines'], 1)
        self.assertEqual(D(body['estimated_only']['net_sales']) + D(body['realised']['net_sales']),
                         D(body['estimated']['net_sales']))


class NoCostLeakTest(MarginShop, TestCase):
    def setUp(self):
        self.build()
        self.order = self.paid_order(self.case, 1)
        supply.receive_line(self.order.items.get(), actor=self.staff, unit_cost='65.00')

    def test_the_order_detail_says_the_real_cost_and_its_source_only_to_costs_view(self):
        item = as_client(self.staff).get(f'/api/admin/orders/{self.order.pk}/').json()['items'][0]
        self.assertEqual((item['agreed_unit_cost'], item['received_unit_cost'], item['unit_cost_difference'],
                          item['received_cost_source']), ('70.0000', '65.0000', '-5.0000', 'real'))
        res = as_client(self.no_costs).get(f'/api/admin/orders/{self.order.pk}/')
        for key in ('received_unit_cost', 'received_cost_source', 'received_at', '65.00'):
            self.assertNotIn(key, res.content.decode())

    def test_the_margin_needs_costs_view_and_the_buyer_sees_nothing(self):
        self.assertEqual(as_client(self.no_costs).get('/api/admin/sales/margin/?days=7').status_code, 403)
        from django.contrib.auth.models import User

        buyer = User.objects.create_user(username='sdf2_m_comprador', password='x')
        Order.objects.filter(pk=self.order.pk).update(user=buyer)
        client = APIClient(HTTP_HOST=self.host)
        client.force_authenticate(user=buyer)
        text = client.get('/api/orders/').content.decode()
        for secret in ('received_unit_cost', 'received_cost_source', '65.00', '70.00'):
            self.assertNotIn(secret, text)


class RecordRealCostTest(MarginShop, TestCase):
    """«Registrar costo real»: the way out of «costo real pendiente» for a received line."""

    def setUp(self):
        self.build()
        self.order = self.paid_order(self.case, 2)
        supply.receive_line(self.order.items.get(), actor=self.no_costs)        # agreed, real pending
        self.line = self.order.items.get()

    def url(self, order=None, line=None):
        order = order or self.order
        return f'/api/admin/orders/{order.pk}/supply/{(line or self.line).pk}/real-cost/'

    def audits(self):
        from store.models import AdminAuditLog

        return AdminAuditLog.objects.filter(action='order_supply_real_cost_recorded')

    def test_pending_until_set_then_realised_and_the_kardex_untouched(self):
        from store.models import StockMovement

        self.assertEqual(self.margin()['realised']['lines'], 0)
        kardex = list(StockMovement.objects.filter(order=self.order).values_list('pk', 'unit_cost'))
        res = as_client(self.staff).post(self.url(), {'unit_cost': '64.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['received_unit_cost'], res.json()['received_cost_source']), ('64.0000', 'real'))
        line = self.order.items.get()
        self.assertEqual((line.unit_cost, line.received_unit_cost, line.received_cost_source),
                         (D('70.0000'), D('64.0000'), 'real'))
        self.assertIsNotNone(line.received_cost_at)
        body = self.margin()
        self.assertEqual((body['realised']['lines'], D(body['realised']['cost'])), (1, D('128.00')))
        self.assertEqual(body['real_cost_pending']['lines'], 0)
        self.assertEqual(list(StockMovement.objects.filter(order=self.order).values_list('pk', 'unit_cost')), kardex)
        audit = self.audits().get()
        self.assertEqual((audit.actor_id, audit.metadata['line_id'], audit.metadata['previous_source']),
                         (self.staff.pk, self.line.pk, 'agreed'))
        self.assertNotIn('64.0', str(audit.metadata), 'the amount never goes to the log')

    def test_the_same_value_twice_is_200_without_a_new_audit_and_another_is_409(self):
        client = as_client(self.staff)
        self.assertEqual(client.post(self.url(), {'unit_cost': '64.00'}, format='json').status_code, 200)
        self.assertEqual(client.post(self.url(), {'unit_cost': '64'}, format='json').status_code, 200)
        self.assertEqual(self.audits().count(), 1)
        res = client.post(self.url(), {'unit_cost': '60.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'already_real'))
        self.assertEqual(self.order.items.get().received_unit_cost, D('64.0000'))

    def test_a_cost_stated_at_reception_is_already_real(self):
        order = self.paid_order(self.case, 1, session='otro')
        supply.receive_line(order.items.get(), actor=self.staff, unit_cost='66.00')
        res = as_client(self.staff).post(self.url(order, order.items.get()), {'unit_cost': '50.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'already_real'))

    def test_only_a_received_line_and_a_positive_amount(self):
        waiting = self.paid_order(self.case, 1, session='espera')
        res = as_client(self.staff).post(self.url(waiting, waiting.items.get()), {'unit_cost': '50.00'}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'not_received'))
        for bad in ('0', '-1', 'caro', ''):
            res = as_client(self.staff).post(self.url(), {'unit_cost': bad}, format='json')
            self.assertEqual((res.status_code, res.json().get('field')), (400, 'unit_cost'), bad)
        self.assertIsNone(self.order.items.get().received_unit_cost)

    def test_without_costs_view_403_and_nothing_leaks(self):
        res = as_client(self.no_costs).post(self.url(), {'unit_cost': '64.00'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertNotIn('70.00', res.content.decode())
        manager_without_stock, _ = _p2d_member(self.company, 'sdf2_rc_sin_inv',
                                               ['company.view', 'sales.orders.view', 'sales.orders.manage', 'costs.view'])
        self.assertEqual(as_client(manager_without_stock).post(self.url(), {'unit_cost': '64.00'},
                                                               format='json').status_code, 403)
        self.assertIsNone(self.order.items.get().received_unit_cost)
        self.assertEqual(self.audits().count(), 0)

    def test_another_company_answers_404(self):
        from store.tests import _p3_company

        other = _p3_company('sdf2-rc-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'sdf2_rc_ajeno', ['company.view', 'sales.orders.view', 'sales.orders.manage',
                                                            'inventory.adjust', 'costs.view'])
        self.assertEqual(as_client(stranger).post(self.url(), {'unit_cost': '64.00'}, format='json').status_code, 404)
        with self.assertRaises(supply.SupplyError):
            supply.record_real_cost(self.line, actor=stranger, unit_cost='64.00', company=other)
        self.assertIsNone(self.order.items.get().received_unit_cost)
