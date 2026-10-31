"""
INV-COM-01C — the same rule through the HTTP surfaces: the web till and the mobile (v1) till.

Preview, sale and unit lookup come from `pos_services`/`pos_payloads` for both; these tests hold that they say the
same thing: the price of each used device on its own line, the ficha without a cost, a device without a price
refused in the preview and the sale, a price changed after the preview a 409 that carries the new total, and a
retry of a sale already made answered with that sale.
"""
from decimal import Decimal

from rest_framework.test import APIClient

from store import stock_unit_services as units
from store.models import Order, OrderItem, Product, StockMovement, StockUnit
from store.test_pos_reconcile import _web_login
from store.test_stock_units import IMEI_A, IMEI_B, IMEI_C
from store.tests import Ip1PosBase

WEB = '/api/admin/pos/'


class UsedAtTheTillApiTest(Ip1PosBase):
    def setUp(self):
        super().setUp()
        self.client = _web_login('ip1_caja')
        self.v1 = APIClient()
        self.v1.force_authenticate(self.staff)
        self.v1_base = f'/api/v1/internal/{self.company.slug}/sales/pos/'
        self.used = Product.objects.create(
            company=self.company, name='iPhone 14 128 GB Seminuevo', slug='iphone-14-seminuevo', price='0.00',
            inventory=0, is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED)
        self.a, self.b = units.receive_units(
            branch=self.branch_a, product=self.used, actor=self.staff, reason='Compra',
            units=[
                {'serial_number': 'USADO10001', 'imei': IMEI_A, 'condition': 'used', 'cost': '1400.00',
                 'sale_price': '2100.00', 'battery_health': 88},
                {'serial_number': 'USADO10002', 'imei': IMEI_B, 'condition': 'used', 'cost': '1600.00',
                 'sale_price': '2450.00'},
            ])

    def items(self, *picked):
        return [{'product': self.used.pk, 'quantity': len(picked),
                 'units': [{'id': u.pk, 'method': 'scanner'} for u in picked]}]

    def body(self, *picked, **extra):
        return self.sale_body(items=self.items(*picked), amount_received='10000.00', **extra)

    # -- preview -----------------------------------------------------------

    def test_the_web_preview_prices_each_device_and_names_it(self):
        res = self.client.post(WEB + 'preview/', {'branch': self.branch_a.pk, 'items': self.items(self.a, self.b)}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data['total'], '4550.00')
        self.assertEqual(sorted((row['price'], row['unit']) for row in data['lines']),
                         [('2100.00', self.a.pk), ('2450.00', self.b.pk)])

    def test_the_mobile_preview_is_the_same_answer(self):
        body = {'branch': self.branch_a.pk, 'items': self.items(self.a, self.b)}
        web = self.client.post(WEB + 'preview/', body, format='json').json()
        mobile = self.v1.post(self.v1_base + 'preview/', body, format='json')
        self.assertEqual(mobile.status_code, 200, mobile.content)
        self.assertEqual((mobile.json()['total'], mobile.json()['lines']), (web['total'], web['lines']))

    def test_a_device_without_price_is_refused_in_the_preview_with_the_words_of_the_screen(self):
        StockUnit.objects.filter(pk=self.a.pk).update(sale_price=None)
        for client, url in ((self.client, WEB + 'preview/'), (self.v1, self.v1_base + 'preview/')):
            res = client.post(url, {'branch': self.branch_a.pk, 'items': self.items(self.a)}, format='json')
            self.assertEqual(res.status_code, 400, res.content)
            self.assertIn('Este seminuevo todavía no tiene precio de venta. Solicita que un usuario autorizado lo configure.',
                          res.json()['detail'])

    # -- the cashier's lookup ----------------------------------------------

    def test_the_unit_lookup_shows_the_ficha_and_the_price_but_no_cost(self):
        res = self.client.get(WEB + 'units/lookup/', {'code': IMEI_A, 'branch': self.branch_a.pk, 'method': 'scanner'})
        self.assertEqual(res.status_code, 200, res.content)
        info = res.json()['unit']
        self.assertEqual((info['sale_price'], info['battery_health'], info['priced_by']), ('2100.00', 88, 'unit'))
        self.assertEqual(res.json()['product']['priced_by'], 'unit')
        self.assertNotIn('1400', res.content.decode())
        self.assertNotIn('cost', info)

    def test_a_new_device_is_priced_by_its_product(self):
        phone = Product.objects.create(
            company=self.company, name='iPhone 15 nuevo', slug='iphone-15-nuevo', price='4800.00', inventory=0,
            is_serialized=True, requires_imei=True)
        new, = units.receive_units(branch=self.branch_a, product=phone, actor=self.staff, reason='Compra',
                                   units=[{'serial_number': 'NUEVO10001', 'imei': IMEI_C}])
        info = self.client.get(WEB + 'units/lookup/', {'code': IMEI_C, 'branch': self.branch_a.pk, 'method': 'scanner'}).json()
        self.assertEqual((info['unit']['priced_by'], info['unit']['sale_price']), ('product', None))
        self.assertEqual(info['product']['price'], '4800.00')

    # -- sale --------------------------------------------------------------

    def test_the_web_sale_has_one_line_per_device_at_its_price(self):
        res = self.client.post(WEB + 'sales/', self.body(self.a, self.b), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()
        self.assertEqual(data['total'], '4550.00')
        self.assertEqual(sorted(row['price'] for row in data['items']), ['2100.00', '2450.00'])
        by_price = {row['price']: row for row in data['items']}
        self.assertEqual([u['serial_number'] for u in by_price['2100.00']['units']], ['USADO10001'])
        self.assertEqual([u['serial_number'] for u in by_price['2450.00']['units']], ['USADO10002'])
        self.assertEqual(OrderItem.objects.filter(order_id=data['order_id'], stock_unit__isnull=False).count(), 2)
        self.assertEqual(StockMovement.objects.filter(order_id=data['order_id'], movement_type='sale_exit').count(), 1)

    def test_the_mobile_sale_does_the_same(self):
        res = self.v1.post(self.v1_base + 'sales/', self.body(self.a, self.b, idempotency_key='v1-precio-00001'), format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()['total'], '4550.00')
        self.assertEqual(sorted(row['price'] for row in res.json()['items']), ['2100.00', '2450.00'])

    def test_a_device_without_price_cannot_be_sold_and_nothing_is_written(self):
        StockUnit.objects.filter(pk=self.b.pk).update(sale_price=Decimal('0.00'))
        for client, url, key in ((self.client, WEB + 'sales/', 'web-sin-precio-1'),
                                 (self.v1, self.v1_base + 'sales/', 'v1-sin-precio-01')):
            res = client.post(url, self.body(self.b, idempotency_key=key), format='json')
            self.assertEqual(res.status_code, 400, res.content)
            self.assertIn('todavía no tiene precio de venta', res.json()['detail'])
        self.assertEqual(Order.objects.count(), 0)
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, 'available')

    # -- a price that changed after the preview ----------------------------

    def test_a_changed_price_is_a_409_with_the_new_total_and_the_old_one_is_not_charged(self):
        seen = self.client.post(WEB + 'preview/', {'branch': self.branch_a.pk, 'items': self.items(self.a)}, format='json').json()['total']
        StockUnit.objects.filter(pk=self.a.pk).update(sale_price=Decimal('2300.00'))
        for client, url, key in ((self.client, WEB + 'sales/', 'web-cambio-0001'), (self.v1, self.v1_base + 'sales/', 'v1-cambio-00001')):
            res = client.post(url, self.body(self.a, idempotency_key=key, expected_total=seen), format='json')
            self.assertEqual(res.status_code, 409, res.content)
            data = res.json()
            self.assertEqual((data['code'], data['expected_total'], data['current_total']), ('price_changed', '2100.00', '2300.00'))
            self.assertEqual(data['preview']['total'], '2300.00')
        self.assertEqual(Order.objects.count(), 0)
        res = self.client.post(WEB + 'sales/', self.body(self.a, idempotency_key='web-cambio-0002', expected_total='2300.00'), format='json')
        self.assertEqual((res.status_code, res.json()['total']), (201, '2300.00'))

    def test_exact_cash_counted_for_the_old_price_still_meets_a_409_not_a_bare_cash_error(self):
        StockUnit.objects.filter(pk=self.a.pk).update(sale_price=Decimal('2300.00'))     # was 2100.00 on screen
        body = self.sale_body(items=self.items(self.a), amount_received='2100.00',
                              idempotency_key='web-efectivo-0001', expected_total='2100.00')
        res = self.client.post(WEB + 'sales/', body, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (409, 'price_changed'), res.content)
        self.assertEqual(Order.objects.count(), 0)

    def test_exact_cash_for_the_unchanged_price_sells_with_no_change_and_a_number_is_accepted(self):
        body = self.sale_body(items=self.items(self.a), amount_received='2100.00',
                              idempotency_key='web-efectivo-0002', expected_total=2100.0)
        res = self.client.post(WEB + 'sales/', body, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Order.objects.get().change_amount, Decimal('0.00'))

    def test_cash_short_of_the_unchanged_total_is_still_refused(self):
        body = self.sale_body(items=self.items(self.a), amount_received='2000.00',
                              idempotency_key='web-efectivo-0003', expected_total='2100.00')
        res = self.client.post(WEB + 'sales/', body, format='json')
        self.assertEqual((res.status_code, res.json().get('code')), (400, None), res.content)
        self.assertEqual(Order.objects.count(), 0)

    def test_a_malformed_expected_total_is_a_bad_request_not_a_price_change(self):
        for bad in ('abc', True, [], '-5', '1e30', 1e30, 'NaN'):
            res = self.client.post(WEB + 'sales/', self.body(self.a, idempotency_key='web-malo-000001', expected_total=bad), format='json')
            self.assertEqual(res.status_code, 400, (bad, res.content))
        self.assertEqual(Order.objects.count(), 0)

    def test_a_retry_with_the_same_key_returns_the_sale_once_and_takes_one_unit_out(self):
        first = self.client.post(WEB + 'sales/', self.body(self.a, idempotency_key='web-reintento-1', expected_total='2100.00'), format='json')
        again = self.client.post(WEB + 'sales/', self.body(self.a, idempotency_key='web-reintento-1', expected_total='2100.00'), format='json')
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(again.json()['order_id'], first.json()['order_id'])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(movement_type='sale_exit').count(), 1)
