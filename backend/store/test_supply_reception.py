"""
SUPPLIER-DEVICE-FLOW-02 · B — recibir del proveedor lo vendido bajo pedido.

Lo que estas pruebas fijan:

  * la regla de cada equipo es la del servidor (`stock_unit_services`): serie
    siempre, IMEI cuando el producto lo exige; el error dice qué fila y qué
    campo, y la nota de cada fila llega al equipo;
  * la línea y el detalle dicen sucursal de despacho, precio cobrado,
    cantidad y proveedor; el costo pactado sólo viaja con `costs.view`;
  * el costo real lo da quien tiene `costs.view`: entra al Kardex y al equipo;
    la instantánea de la venta sigue con el costo pactado (nunca se reescribe),
    y el margen —estimado y realizado— se sigue leyendo de esa instantánea;
  * una línea ya recibida responde 409 `already_received` con lo recibido, y
    dos recepciones a la vez dejan una sola entrada;
  * avisos operativos por empresa y sucursal, con su filtro en el listado.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import inventory_services as inventory
from store import supply_services as supply
from store import supply_alerts
from store.models import Order, OrderItem, OrderUnit, Product, StockMovement, StockUnit, SupplierOffer
from store.test_stock_units import imei
from store.test_supplier_offers import SupplyShop, today
from store.tests import _p2d_member, _prod

D = Decimal
IMEIS = [imei(f'35693803577{n:03d}') for n in range(40)]

#: May receive and manage supply, but does not see costs.
NO_COST_CAPS = ['company.view', 'sales.orders.view', 'sales.orders.manage', 'inventory.view', 'inventory.adjust']


def as_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class ReceptionShop(SupplyShop):
    """The accessory shop of DC3, plus a phone (serial + IMEI) and a watch (serial only), both on order."""

    def build(self, slug='sdf2-tienda'):
        super().build(slug)
        self.phone = self.device('Equipo SDF2', f'{slug}-equipo', requires_imei=True)
        self.watch = self.device('Reloj SDF2', f'{slug}-reloj', requires_imei=False)
        self.phone_offer = self.make_offer(self.phone, agreed_cost='2500.00', commercial_price='2900.00')
        self.watch_offer = self.make_offer(self.watch, agreed_cost='800.00', commercial_price='1000.00')
        self.no_costs, _ = _p2d_member(self.company, f'{slug}_sin_costos', NO_COST_CAPS)

    def device(self, name, slug, *, requires_imei):
        product = _prod(self.company, name, slug, price='3000.00', inventory=0)
        Product.objects.filter(pk=product.pk).update(
            is_serialized=True, requires_imei=requires_imei, sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        product.refresh_from_db()
        return product

    def paid_order(self, product=None, quantity=1, session='carrito-r'):
        self.add_on_order(product or self.case, quantity=quantity, session=session)
        order = self.checkout(session)
        self.pay(order)
        return order

    def url(self, order, action='receive'):
        return f'/api/admin/orders/{order.pk}/supply/{order.items.get().pk}/{action}/'


# ---------------------------------------------------------------------------
# 1 · the form's rule is the server's
# ---------------------------------------------------------------------------

class UnitRulesTest(ReceptionShop, TestCase):
    def setUp(self):
        self.build()

    def test_every_device_needs_its_serial_and_the_error_names_the_row(self):
        order = self.paid_order(self.watch, 2)
        res = as_client(self.staff).post(self.url(order), {'units': [
            {'serial_number': 'RLJ0001'}, {'serial_number': '', 'imei': IMEIS[0]},
        ]}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual((body['field'], body['line'], body['unit_field']), ('units', 2, 'serial_number'))
        self.assertFalse(StockUnit.objects.filter(product=self.watch).exists(), 'all or nothing')

    def test_a_product_that_requires_imei_refuses_a_device_without_it(self):
        order = self.paid_order(self.phone, 1)
        res = as_client(self.staff).post(self.url(order), {'units': [{'serial_number': 'EQP0001'}]}, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual((res.json()['line'], res.json()['unit_field']), (1, 'imei'))

    def test_a_product_without_imei_is_received_by_serial_alone(self):
        order = self.paid_order(self.watch, 1)
        res = as_client(self.staff).post(self.url(order), {'units': [{'serial_number': 'RLJ0002'}]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(StockUnit.objects.get(product=self.watch).status, 'sold')

    def test_the_note_of_each_row_reaches_its_device(self):
        order = self.paid_order(self.phone, 2)
        res = as_client(self.staff).post(self.url(order), {'units': [
            {'serial_number': 'EQP0011', 'imei': IMEIS[1], 'notes': 'Caja abierta por aduana'},
            {'serial_number': 'EQP0012', 'imei': IMEIS[2], 'imei2': IMEIS[3]},
        ]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        first, second = StockUnit.objects.filter(product=self.phone).order_by('serial_number')
        self.assertEqual((first.notes, second.notes, second.imei2), ('Caja abierta por aduana', '', IMEIS[3]))

    def test_the_detail_says_whether_each_device_needs_an_imei(self):
        order = self.paid_order(self.watch, 1)
        item = as_client(self.staff).get(f'/api/admin/orders/{order.pk}/').json()['items'][0]
        self.assertEqual((item['product_is_serialized'], item['requires_imei']), (True, False))


# ---------------------------------------------------------------------------
# 2 · what the panel shows of the line, and the cost only to costs.view
# ---------------------------------------------------------------------------

class LinePayloadTest(ReceptionShop, TestCase):
    def setUp(self):
        self.build()
        self.order = self.paid_order(self.case, 2)

    def test_the_line_says_branch_price_quantity_supplier_and_the_agreed_cost(self):
        item = as_client(self.staff).get(f'/api/admin/orders/{self.order.pk}/').json()['items'][0]
        self.assertEqual(item['fulfillment_branch'], {'id': self.branch.pk, 'name': self.branch.name})
        self.assertEqual((item['price'], item['quantity'], item['supplier_name']), ('110.00', 2, 'Mayorista Lima'))
        self.assertEqual(item['agreed_unit_cost'], '70.0000')
        self.assertIsNone(item['received_unit_cost'])

    def test_without_costs_view_no_cost_travels(self):
        res = as_client(self.no_costs).get(f'/api/admin/orders/{self.order.pk}/')
        item = res.json()['items'][0]
        self.assertEqual(item['fulfillment_branch']['id'], self.branch.pk)
        for key in ('agreed_unit_cost', 'received_unit_cost', 'unit_cost_difference', 'unit_cost'):
            self.assertNotIn(key, item)
        self.assertNotIn('70.00', res.content.decode())
        line = as_client(self.no_costs).post(self.url(self.order), {}, format='json')
        self.assertEqual(line.status_code, 200, line.content)
        self.assertNotIn('agreed_unit_cost', line.json())
        self.assertNotIn('70.00', line.content.decode())

    def test_the_action_answer_carries_the_same_line(self):
        line = as_client(self.staff).post(self.url(self.order), {}, format='json').json()
        self.assertEqual((line['product_name'], line['quantity'], line['price']), ('Cargador 20W', 2, '110.00'))
        self.assertEqual(line['fulfillment_branch'], {'id': self.branch.pk, 'name': self.branch.name})
        self.assertEqual((line['agreed_unit_cost'], line['received_unit_cost']), ('70.0000', '70.0000'))

    def test_the_buyer_sees_none_of_it(self):
        from django.contrib.auth.models import User

        buyer = User.objects.create_user(username='sdf2_comprador', password='x')
        Order.objects.filter(pk=self.order.pk).update(user=buyer)
        res = APIClient(HTTP_HOST=self.host)
        res.force_authenticate(user=buyer)
        text = res.get('/api/orders/').content.decode()
        for secret in ('fulfillment_branch', 'agreed_unit_cost', 'received_unit_cost', 'Mayorista', '70.00'):
            self.assertNotIn(secret, text)


# ---------------------------------------------------------------------------
# 3 · the real cost, beside the agreed one
# ---------------------------------------------------------------------------

class RealCostTest(ReceptionShop, TestCase):
    def setUp(self):
        self.build()

    def test_the_entry_takes_the_real_cost_and_the_sale_keeps_the_agreed_one(self):
        self.stock(self.case, 2)                                   # own units at 60 on the shelf
        order = self.paid_order(self.case, 3)
        res = as_client(self.staff).post(self.url(order), {'unit_cost': '65.50'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        line = order.items.get()
        self.assertEqual((line.unit_cost, line.cost_source), (D('70.0000'), OrderItem.COST_SUPPLIER_OFFER))
        entry = StockMovement.objects.get(order=order, movement_type='purchase_entry')
        self.assertEqual((entry.quantity, entry.unit_cost), (3, D('65.5000')))
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type='sale_exit').count(), 1)
        self.assertEqual(self.shelf(self.case), 2, 'net zero on the shelf')
        item = as_client(self.staff).get(f'/api/admin/orders/{order.pk}/').json()['items'][0]
        self.assertEqual((item['agreed_unit_cost'], item['received_unit_cost'], item['unit_cost_difference']),
                         ('70.0000', '65.5000', '-4.5000'))

    def test_without_costs_view_a_sent_cost_is_ignored(self):
        order = self.paid_order(self.case, 1)
        res = as_client(self.no_costs).post(self.url(order), {'unit_cost': '1.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        entry = StockMovement.objects.get(order=order, movement_type='purchase_entry')
        self.assertEqual(entry.unit_cost, D('70.0000'))

    def test_a_cost_that_is_not_an_amount_is_refused(self):
        order = self.paid_order(self.case, 1)
        res = as_client(self.staff).post(self.url(order), {'unit_cost': 'caro'}, format='json')
        self.assertEqual((res.status_code, res.json()['field']), (400, 'unit_cost'))
        self.assertEqual(order.items.get().supply_status, 'pending')

    def test_devices_take_their_own_real_cost(self):
        order = self.paid_order(self.phone, 2)
        res = as_client(self.staff).post(self.url(order), {'units': [
            {'serial_number': 'EQP0101', 'imei': IMEIS[10], 'cost': '2450.00'},
            {'serial_number': 'EQP0102', 'imei': IMEIS[11]},          # no cost: the line's real one, else agreed
        ], 'unit_cost': '2400.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        costs = dict(StockUnit.objects.filter(product=self.phone).values_list('serial_number', 'cost'))
        self.assertEqual(costs, {'EQP0101': D('2450.00'), 'EQP0102': D('2400.00')})
        line = order.items.get()
        self.assertEqual(line.unit_cost, D('2500.0000'), 'the sale snapshot is never rewritten')
        self.assertEqual(OrderUnit.objects.filter(order=order).count(), 2)
        item = as_client(self.staff).get(f'/api/admin/orders/{order.pk}/').json()['items'][0]
        self.assertEqual((item['received_unit_cost'], item['unit_cost_difference']), ('2425.0000', '-75.0000'))

    def test_devices_without_costs_view_enter_at_the_agreed_cost(self):
        order = self.paid_order(self.phone, 1)
        res = as_client(self.no_costs).post(self.url(order), {'units': [
            {'serial_number': 'EQP0201', 'imei': IMEIS[12], 'cost': '1.00'}]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(StockUnit.objects.get(product=self.phone).cost, D('2500.00'))

    def test_the_margin_keeps_reading_the_agreed_cost(self):
        """Decision: estimated and realised margin read the sale snapshot (the agreed cost)."""
        order = self.paid_order(self.case, 2)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now())
        supply.receive_line(order.items.get(), actor=self.staff, unit_cost='50.00')
        analyst, _ = _p2d_member(self.company, 'sdf2_margen', ['company.view', 'sales.analytics.view', 'costs.view'])
        body = as_client(analyst).get('/api/admin/sales/margin/?days=7').json()
        self.assertEqual(D(body['cost_of_goods_sold']), D('140.00'))
        self.assertEqual(D(body['realised']['cost']), D('140.00'))
        self.assertEqual(body['realised']['lines'], 1)

    def test_the_settlement_keeps_the_agreed_cost(self):
        order = self.paid_order(self.case, 2)
        supply.receive_line(order.items.get(), actor=self.staff, unit_cost='50.00')
        self.assertEqual([(row['units'], row['amount']) for row in supply.pending_settlement(self.company)],
                         [(2, '140.00')])


# ---------------------------------------------------------------------------
# 5 · once
# ---------------------------------------------------------------------------

class AlreadyReceivedTest(ReceptionShop, TestCase):
    def setUp(self):
        self.build()

    def test_a_second_receive_answers_409_with_what_was_received(self):
        order = self.paid_order(self.phone, 1)
        units = [{'serial_number': 'EQP0301', 'imei': IMEIS[20]}]
        client = as_client(self.staff)
        self.assertEqual(client.post(self.url(order), {'units': units}, format='json').status_code, 200)
        again = client.post(self.url(order), {'units': units}, format='json')
        self.assertEqual(again.status_code, 409, again.content)
        body = again.json()
        self.assertEqual(body['code'], 'already_received')
        self.assertEqual([(u['serial_number'], u['imei']) for u in body['units']], [('EQP0301', IMEIS[20])])
        self.assertEqual(StockUnit.objects.filter(product=self.phone).count(), 1)
        self.assertEqual(StockMovement.objects.filter(product=self.phone, movement_type='purchase_entry').count(), 1)

    def test_the_service_says_already_received(self):
        order = self.paid_order(self.case, 1)
        supply.receive_line(order.items.get(), actor=self.staff)
        with self.assertRaises(supply.SupplyError) as caught:
            supply.receive_line(order.items.get(), actor=self.staff)
        self.assertEqual(caught.exception.code, 'already_received')

    def test_a_line_not_yet_confirmed_is_still_the_wrong_state(self):
        order = self.paid_order(self.case, 1)
        OrderItem.objects.filter(order=order).update(supply_status=OrderItem.SUPPLY_TO_CONFIRM)
        res = as_client(self.staff).post(self.url(order), {}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'wrong_state'))


class ReceiveWhileReturningTest(ReceptionShop, TestCase):
    """
    A till sale on order is open to a return from birth (ORDER-RETURN-01, D8).
    While a return stands, receiving would sell goods out to an order whose
    goods are coming back («second exit»): refused, like identifying a device.
    """

    def setUp(self):
        self.build()

    def returning(self, order, status='requested'):
        from store.models import OrderReturn

        dates = {'received': {'received_at': timezone.now(), 'receiving_branch': self.branch},
                 'withdrawn': {'withdrawn_at': timezone.now()}}.get(status, {})
        return OrderReturn.objects.create(company=self.company, order=order, status=status, kind='otro',
                                          reason='Cliente desiste', requested_by=self.staff, **dates)

    def test_a_standing_return_refuses_the_reception_with_409(self):
        order = self.paid_order(self.phone, 1)
        self.returning(order)
        res = as_client(self.staff).post(self.url(order), {'units': [
            {'serial_number': 'EQP0501', 'imei': IMEIS[35]}]}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'return_open'), res.content)
        self.assertFalse(StockUnit.objects.filter(product=self.phone).exists())
        self.assertFalse(OrderUnit.objects.filter(order=order).exists())
        self.assertEqual(order.items.get().supply_status, 'pending')

    def test_an_accessory_line_is_refused_too(self):
        order = self.paid_order(self.case, 1)
        self.returning(order, status='received')
        with self.assertRaises(supply.SupplyError) as caught:
            supply.receive_line(order.items.get(), actor=self.staff)
        self.assertEqual(caught.exception.code, 'return_open')
        self.assertFalse(StockMovement.objects.filter(order=order).exists())

    def test_a_withdrawn_return_does_not_stand(self):
        order = self.paid_order(self.case, 1)
        self.returning(order, status='withdrawn')
        supply.receive_line(order.items.get(), actor=self.staff)
        self.assertEqual(order.items.get().supply_status, 'received')


class ReceiveAtTheSameInstantTest(TransactionTestCase):
    """Two people press «Registrar recepción» together: one entry, one 409."""

    def test_one_receives_and_the_other_is_told(self):
        from threading import Barrier, Thread

        from django.db import connection

        shop = ReceptionShop()
        shop.build('sdf2-carrera')
        order = shop.paid_order(shop.case, 2)
        line = order.items.get()
        barrier, outcomes = Barrier(2), []

        def run():
            try:
                barrier.wait(timeout=5)
                supply.receive_line(line, actor=shop.staff)
                outcomes.append('received')
            except supply.SupplyError as exc:
                outcomes.append(exc.code)
            finally:
                connection.close()

        threads = [Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(outcomes), ['already_received', 'received'])
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type='purchase_entry').count(), 1)
        self.assertEqual(StockMovement.objects.filter(order=order, movement_type='sale_exit').count(), 1)
        self.assertEqual(inventory.branch_quantity(shop.branch, shop.case), 0)


# ---------------------------------------------------------------------------
# 6 · operational alerts
# ---------------------------------------------------------------------------

class SupplyAlertsTest(ReceptionShop, TestCase):
    def setUp(self):
        self.build()

    def counts(self, user=None):
        res = as_client(user or self.staff).get('/api/admin/orders/alerts/')
        self.assertEqual(res.status_code, 200, res.content)
        return {row['kind']: row['count'] for row in res.json()['alerts']}

    def listed(self, kind, user=None):
        rows = as_client(user or self.staff).get(f'/api/admin/orders/?alert={kind}').json()['results']
        return sorted(row['id'] for row in rows)

    def test_each_alert_counts_its_orders_and_the_list_filters_by_it(self):
        waiting = self.paid_order(self.case, 1, session='a')
        OrderItem.objects.filter(order=waiting).update(lead_time_days=1)        # promised for tomorrow
        late = self.paid_order(self.case, 1, session='b')
        Order.objects.filter(pk=late.pk).update(paid_at=timezone.now() - timedelta(days=5))     # promised 4 days
        self.paid_order(self.case, 1, session='c')                              # promised in 4 days: fine
        expired = self.paid_order(self.case, 1, session='d')
        old = self.make_offer(valid_from=today() - timedelta(days=1), valid_until=today() + timedelta(days=1))
        SupplierOffer.objects.filter(pk=old.pk).update(
            valid_from=today() - timedelta(days=9), valid_until=today() - timedelta(days=1))
        OrderItem.objects.filter(order=expired).update(supplier_offer=old)
        to_confirm = self.paid_order(self.case, 1, session='e')
        OrderItem.objects.filter(order=to_confirm).update(supply_status=OrderItem.SUPPLY_TO_CONFIRM)
        received = self.paid_order(self.phone, 1, session='f')
        supply.receive_line(received.items.get(), actor=self.staff,
                            units=[{'serial_number': 'EQP0401', 'imei': IMEIS[30]}])

        counts = self.counts()
        self.assertEqual(counts['supplier_pending'], 4)
        self.assertEqual(counts['promise_due'], 2, 'waiting is due tomorrow (1 day), late passed 4 days ago')
        self.assertEqual(counts['offer_expired'], 1)
        self.assertEqual(counts['to_confirm'], 1)
        self.assertEqual(counts['ready_to_deliver'], 1)
        self.assertEqual(self.listed('promise_due'), sorted([waiting.pk, late.pk]))
        self.assertEqual(self.listed('offer_expired'), [expired.pk])
        self.assertEqual(self.listed('to_confirm'), [to_confirm.pk])
        self.assertEqual(self.listed('ready_to_deliver'), [received.pk])
        body = as_client(self.staff).get('/api/admin/orders/alerts/').json()
        due = next(row for row in body['alerts'] if row['kind'] == 'promise_due')
        self.assertEqual((due['overdue'], due['href']), (1, '/admin/orders?alert=promise_due'))

    def test_devices_still_to_identify(self):
        own = self.device('Equipo propio SDF2', 'sdf2-propio', requires_imei=False)
        Product.objects.filter(pk=own.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        from store import stock_unit_services as units

        units.receive_units(branch=self.branch, product=own, units=[{'serial_number': 'PRP0001'}],
                            actor=self.staff, reason='Compra')
        from store.models import CartItem

        CartItem.objects.create(session_key='f', product=own, quantity=1)
        order = self.checkout('f')
        self.pay(order)
        self.assertEqual(self.counts()['units_pending'], 1)
        self.assertEqual(self.listed('units_pending'), [order.pk])

    def test_a_branch_scoped_viewer_counts_only_its_branches(self):
        from store.models import Branch

        self.paid_order(self.case, 1)
        other = Branch.objects.create(company=self.company, name='Sucursal Norte')
        elsewhere, _ = _p2d_member(self.company, 'sdf2_norte', ['company.view', 'sales.orders.view'],
                                   branches=[other])
        self.assertEqual(self.counts(elsewhere)['supplier_pending'], 0)
        here, _ = _p2d_member(self.company, 'sdf2_centro', ['company.view', 'sales.orders.view'],
                              branches=[self.branch])
        self.assertEqual(self.counts(here)['supplier_pending'], 1)

    def test_another_company_and_no_permission(self):
        from store.tests import _p3_company

        self.paid_order(self.case, 1)
        other = _p3_company('sdf2-ajena', 'Ajena')
        stranger, _ = _p2d_member(other, 'sdf2_ajeno', ['company.view', 'sales.orders.view'])
        self.assertEqual(self.counts(stranger)['supplier_pending'], 0)
        nobody, _ = _p2d_member(self.company, 'sdf2_nadie', ['company.view', 'products.view'])
        self.assertEqual(as_client(nobody).get('/api/admin/orders/alerts/').status_code, 403)

    def test_cancelled_or_delivered_orders_raise_nothing(self):
        order = self.paid_order(self.case, 1)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)
        self.assertEqual(sum(self.counts().values()), 0)

    def test_an_unknown_alert_filters_nothing(self):
        order = self.paid_order(self.case, 1)
        self.assertEqual(self.listed('nada'), [order.pk])

    def test_the_service_answers_a_queryset(self):
        order = self.paid_order(self.case, 1)
        self.assertEqual(list(supply_alerts.filter_orders(Order.objects.all(), 'supplier_pending')), [order])


class AlertsCostTest(ReceptionShop, TestCase):
    """The dashboard asks on every load: the device alerts cost the same for 1 order or many."""

    def setUp(self):
        self.build()
        from store import stock_unit_services as units
        from store.models import CartItem

        self.own = self.device('Equipo propio SDF2', 'sdf2-propio-n', requires_imei=False)
        Product.objects.filter(pk=self.own.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        units.receive_units(branch=self.branch, product=self.own, actor=self.staff, reason='Compra',
                            units=[{'serial_number': f'PRN{n:04d}'} for n in range(6)])
        self.orders = []
        for n in range(6):
            CartItem.objects.create(session_key=f'n{n}', product=self.own, quantity=1)
            order = self.checkout(f'n{n}')
            self.pay(order)
            self.orders.append(order)

    def queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as seen:
            rows = supply_alerts.summary(Order.objects.filter(company=self.company))
        return len(seen.captured_queries), {row['kind']: row['count'] for row in rows}

    def test_the_count_does_not_grow_with_the_orders(self):
        many, counts = self.queries()
        self.assertEqual(counts['units_pending'], 6)
        Order.objects.filter(pk__in=[o.pk for o in self.orders[1:]]).update(status=Order.Status.CANCELLED)
        one, counts = self.queries()
        self.assertEqual(counts['units_pending'], 1)
        self.assertEqual(many, one)
        self.assertLessEqual(many, 20)

    def test_the_batch_says_what_the_order_view_says(self):
        from store import unit_identification

        received = self.paid_order(self.phone, 1, session='r')
        supply.receive_line(received.items.get(), actor=self.staff,
                            units=[{'serial_number': 'EQP0601', 'imei': IMEIS[36]}])
        # A record whose device is back on the shelf (not sold to the order, never left) names nothing.
        from store import stock_unit_services as units

        shelf, = units.receive_units(branch=self.branch, product=self.own, actor=self.staff, reason='Compra',
                                     units=[{'serial_number': 'PRN9999'}])
        OrderUnit.objects.create(company=self.company, order=self.orders[0], product=self.own, unit=shelf,
                                 method='manual', identified_by=self.staff)
        ids = [o.pk for o in self.orders] + [received.pk]
        batch = supply_alerts.pending_devices(ids)
        self.assertEqual(batch[self.orders[0].pk], {self.own.pk: 1})
        for order in Order.objects.filter(pk__in=ids):
            self.assertEqual(batch.get(order.pk, {}), unit_identification.pending_units(order), order.pk)


class ReadyToDeliverTest(ReceptionShop, TestCase):
    """«Recibidos, por entregar»: received and not yet out of the shop. Shipped is on its way, not waiting."""

    def setUp(self):
        self.build()

    def test_shipped_and_delivered_are_not_waiting(self):
        orders = []
        for n, state in enumerate(('pending', 'ready_for_pickup', 'shipped', 'delivered')):
            order = self.paid_order(self.case, 1, session=f'rd{n}')
            supply.receive_line(order.items.get(), actor=self.staff)
            Order.objects.filter(pk=order.pk).update(fulfillment_status=state)
            orders.append(order)
        rows = {row['kind']: row['count'] for row in supply_alerts.summary(Order.objects.all())}
        self.assertEqual(rows['ready_to_deliver'], 2)
        self.assertEqual(sorted(supply_alerts.filter_orders(Order.objects.all(), 'ready_to_deliver')
                                .values_list('pk', flat=True)), sorted([orders[0].pk, orders[1].pk]))


class OpsStatusOverdueTest(ReceptionShop, TestCase):
    def setUp(self):
        self.build()

    def test_the_status_check_says_when_a_promised_date_passed(self):
        out = StringIO()
        try:
            call_command('ops_status', stdout=out)
        except SystemExit:
            pass
        self.assertNotIn('prometida', out.getvalue())
        order = self.paid_order(self.case, 1)
        Order.objects.filter(pk=order.pk).update(paid_at=timezone.now() - timedelta(days=9))
        out = StringIO()
        try:
            call_command('ops_status', stdout=out)
        except SystemExit:
            pass
        self.assertIn('ATENCIÓN abastecimiento: 1 pedido pagado con la fecha prometida vencida', out.getvalue())
