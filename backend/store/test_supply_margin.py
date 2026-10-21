"""
SUPPLIER-MARGIN — decisión D2 del propietario (vinculante).

  * Margen ESTIMADO: el costo pactado congelado en la línea bajo pedido
    (`OrderItem.unit_cost`), como hasta ahora; nunca se reescribe.
  * Margen REALIZADO: el costo REAL con el que la mercadería entró al recibirla
    (`OrderItem.received_unit_cost`, instantánea tomada en `receive_line`
    bajo el bloqueo). Sin costo real (no recibida, o recibida antes de que
    existiera la instantánea) la línea es «costo real pendiente»: no cuenta
    como margen realizado y se lista aparte (líneas y venta neta).
  * Recibida sin `costs.view`: entró al Kardex al costo pactado; se toma como
    real, con su origen marcado (`received_cost_source='agreed'`).
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
                         (D('70.0000'), D('65.5000'), 'stated'))
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

    def test_received_without_costs_view_is_real_at_the_agreed_cost_with_its_source(self):
        order = self.paid_order(self.case, 2)
        no_costs, _ = _p2d_member(self.company, 'sdf2_m_sin_costos', NO_COST_CAPS)
        supply.receive_line(order.items.get(), actor=no_costs, unit_cost='1.00')
        line = order.items.get()
        self.assertEqual((line.received_unit_cost, line.received_cost_source), (D('70.0000'), 'agreed'))
        self.assertEqual(D(self.margin()['realised']['cost']), D('140.00'))

    def test_devices_realise_at_the_mean_of_their_real_costs(self):
        order = self.paid_order(self.phone, 2)
        supply.receive_line(order.items.get(), actor=self.staff, units=[
            {'serial_number': 'MRG0001', 'imei': IMEIS[37], 'cost': '2450.00'},
            {'serial_number': 'MRG0002', 'imei': IMEIS[38]},
        ], unit_cost='2400.00')
        line = order.items.get()
        self.assertEqual((line.unit_cost, line.received_unit_cost, line.received_cost_source),
                         (D('2500.0000'), D('2425.0000'), 'stated'))
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
                          item['received_cost_source']), ('70.0000', '65.0000', '-5.0000', 'stated'))
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
