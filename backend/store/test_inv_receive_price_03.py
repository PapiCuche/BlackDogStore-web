"""
INV-RECEIVE-PRICE-03 — the price of a used device is ITS price, everywhere, and only the catalogue sets it.

    PATCH /api/admin/inventory/units/<id>/          the ficha of one device
    GET   /api/admin/inventory/units/?line=used&priced=no|yes   «sin precio» and the web state of each device
    POST  /api/admin/inventory/receipts/confirm/     `follow_up` per line: what is still missing, read NOW

What these tests hold:

  * a sale price is an amount greater than zero with at most two decimals — never zero, a float, a boolean or a
    rounded third decimal — on every path that writes it (the ficha, «Registrar equipos», the receipt);
  * who may change it: `inventory.adjust` reaches the ficha, `products.manage` is needed to touch its price and
    `costs.view` to see or correct its cost; the three never imply one another; another company's device is a 404;
  * a sold device keeps the price it left at; editing a price never sells, moves stock or writes Kardex;
  * the till, the model page, the cart and the checkout read the SAME `StockUnit.sale_price`, and a used device
    without a usable price is offered nowhere — never at the product's price (the fixture's used variant has a
    product price of 1.00 on purpose: it must never appear);
  * after a reception, each line says what is missing — prices, details, photos, the dispatch branch, the
    publication — read live, with no amount, also on a replay; setting a price never publishes anything.
"""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings

from store import device_catalog
from store import pos_services
from store import stock_unit_services as units
from store import unit_hold_services as holds
from store.models import (
    AdminAuditLog, BranchStock, Order, Product, ProductModel, StockMovement, StockUnit,
)
from store.test_device_commerce import DeviceShop, _client as shop_client, assert_no_amount
from store.test_inventory_receipts import ReceiptBase  # noqa: F401  (CommercialBase's base)
from store.test_receipt_commercial import PRICE, CommercialBase
from store.test_registration import _client
from store.test_stock_units import imei
from store.test_pos_reconcile import _web_login
from store.tests import Ip1PosBase, _p2d_member

UNITS = '/api/admin/inventory/units/'
IMEIS = [imei(f'35693803577{n:03d}') for n in range(60)]


def detail(unit):
    return f'{UNITS}{unit.pk}/'


class PriceFixture(CommercialBase):
    """The receipt shop of INV-COM-01A plus one person per authority."""

    def setUp(self):
        super().setUp()
        base = ['inventory.view', 'inventory.adjust']
        # products.manage WITHOUT costs.view (scenario 5) — `self.pricer` of the base class.
        # inventory.adjust WITHOUT products.manage (scenario 4) — `self.clerk` of the base class.
        self.full, _ = _p2d_member(self.company, 'price_full', base + ['costs.view', 'products.view', 'products.manage'])
        self.catalogue_only, _ = _p2d_member(self.company, 'price_catalogue', ['products.view', 'products.manage'])
        self.foreign_pricer, _ = _p2d_member(
            self.other, 'price_foreign', base + ['costs.view', 'products.view', 'products.manage'])
        [self.device] = units.receive_units(
            branch=self.centro, product=self.used, actor=self.full, reason='Compra',
            units=[{'serial_number': 'PRECIO0001', 'imei': IMEIS[20], 'condition': 'used', 'cost': '1200.00'}])

    def patch(self, user, body, unit=None):
        return _client(user).patch(detail(unit or self.device), body, format='json')

    def refresh(self):
        self.device.refresh_from_db()
        return self.device


# ---------------------------------------------------------------------------
# What a price is
# ---------------------------------------------------------------------------

class WhatAPriceIsTest(PriceFixture):
    def test_zero_negative_float_boolean_and_a_third_decimal_are_refused_on_the_ficha(self):
        for bad in ('0', '0.00', 0, '-5', '1899.999', 1899.5, True, 'abc', '1e3', 'NaN', '１８９９', [], {}):
            res = self.patch(self.full, {'sale_price': bad})
            self.assertEqual(res.status_code, 400, (bad, res.content))
            self.assertEqual(res.json().get('field'), 'sale_price', bad)
        self.assertIsNone(self.refresh().sale_price)

    def test_a_positive_amount_is_saved_and_clearing_it_leaves_the_device_unpriced(self):
        for good, stored in (('1899', '1899.00'), ('1899.5', '1899.50'), (2100, '2100.00'), (' 1999.90 ', '1999.90')):
            res = self.patch(self.full, {'sale_price': good})
            self.assertEqual(res.status_code, 200, (good, res.content))
            self.assertEqual((res.json()['sale_price'], self.refresh().sale_price), (stored, Decimal(stored)))
        res = self.patch(self.full, {'sale_price': None})
        self.assertEqual((res.status_code, res.json()['sale_price']), (200, None))
        self.assertIsNone(self.refresh().sale_price)

    def test_registrar_equipos_refuses_a_zero_price_too(self):
        before = StockUnit.objects.count()
        res = _client(self.full).post(UNITS, {
            'branch': self.centro.pk, 'product_id': self.used.pk, 'reason': 'Compra a cliente',
            'units': [{'serial_number': 'PRECIO0002', 'imei': IMEIS[21], 'condition': 'used', 'sale_price': '0'}],
        }, format='json')
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(res.json().get('field'), 'sale_price')
        self.assertEqual(StockUnit.objects.count(), before)

    def test_the_receipt_refuses_zero_invalid_and_float_prices_and_receives_an_absent_one(self):
        for bad in ('0', '0.00', '-1', 'abc', 1899.5, True):
            body = self.body(self.dev(self.used, self.priced('PRECIO0003', IMEIS[22], bad)))
            self.assertEqual(self.preview(self.pricer, body).status_code, 400, bad)
            self.assertEqual(self.confirm(self.pricer, body).status_code, 400, bad)
        self.assertFalse(StockUnit.objects.filter(serial_number='PRECIO0003').exists())
        res = self.confirm(self.pricer, self.body(self.dev(self.used, self.used_unit('PRECIO0003', IMEIS[22]))),
                           expect=201).json()
        self.assertIsNone(StockUnit.objects.get(serial_number='PRECIO0003').sale_price)
        self.assertEqual(res['lines'][0]['follow_up']['units_unpriced'], 1)


# ---------------------------------------------------------------------------
# Who may change it
# ---------------------------------------------------------------------------

class WhoMayChangeItTest(PriceFixture):
    def test_inventory_adjust_without_products_manage_edits_the_device_but_never_its_price(self):
        res = self.patch(self.clerk, {'battery_health': 88, 'public_notes': 'Caja original'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.refresh().battery_health, self.device.public_notes), (88, 'Caja original'))
        for body in ({'sale_price': '1999.00'}, {'sale_price': None}, {'battery_health': 90, 'sale_price': '1999.00'}):
            res = self.patch(self.clerk, body)
            self.assertEqual((res.status_code, res.json().get('field')), (403, 'sale_price'), body)
        # All or nothing: the battery sent with a refused price did not change either.
        self.assertEqual((self.refresh().battery_health, self.device.sale_price), (88, None))

    def test_products_manage_without_costs_view_sets_the_price_but_never_sees_or_changes_the_cost(self):
        res = self.patch(self.pricer, {'sale_price': '1999.00'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn('cost', res.json())
        assert_no_amount(self, res.json(), '1200')
        self.assertEqual(self.refresh().sale_price, Decimal('1999.00'))
        res = self.patch(self.pricer, {'cost': '1000.00', 'cost_reason': 'Factura corregida'})
        self.assertEqual((res.status_code, res.json().get('field')), (403, 'cost'))
        self.assertEqual(self.refresh().cost, Decimal('1200.00'))
        listed = _client(self.pricer).get(UNITS, {'line': 'used'}).json()['results']
        self.assertTrue(all('cost' not in row for row in listed))

    def test_products_manage_alone_does_not_reach_the_stockroom_ficha(self):
        # The price lives on the device's ficha, which is inventory: the catalogue authority alone is not enough.
        res = self.patch(self.catalogue_only, {'sale_price': '1999.00'})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertIsNone(self.refresh().sale_price)

    def test_another_companys_device_is_a_404_and_stays_as_it_was(self):
        units.update_unit(self.device, sale_price='1500.00')
        res = self.patch(self.foreign_pricer, {'sale_price': '1.00'})
        self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(res.json()['detail'], 'Equipo no encontrado.')
        self.assertEqual(self.refresh().sale_price, Decimal('1500.00'))
        missing = _client(self.foreign_pricer).patch(f'{UNITS}999999/', {'sale_price': '1.00'}, format='json')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), res.json())

    def test_a_sold_device_keeps_the_price_it_left_at(self):
        units.update_unit(self.device, sale_price='1500.00')
        StockUnit.objects.filter(pk=self.device.pk).update(status=StockUnit.Status.SOLD)
        for body in ({'sale_price': '1700.00'}, {'sale_price': None}):
            res = self.patch(self.full, body)
            self.assertEqual((res.status_code, res.json().get('field')), (400, 'sale_price'), body)
            self.assertIn('ya se vendió', res.json()['detail'])
        self.assertEqual(self.refresh().sale_price, Decimal('1500.00'))
        # Saving the same price again is not a change: the rest of the ficha may still be completed.
        same = self.patch(self.full, {'sale_price': '1500.00', 'public_notes': 'Vendido con caja'})
        self.assertEqual(same.status_code, 200, same.content)

    def test_editing_a_price_sells_nothing_moves_no_stock_and_audits_without_the_amount(self):
        before = (Order.objects.count(), StockMovement.objects.count(),
                  sorted(BranchStock.objects.values_list('product_id', 'quantity')))
        self.assertEqual(self.patch(self.full, {'sale_price': '1999.00'}).status_code, 200)
        self.assertEqual((Order.objects.count(), StockMovement.objects.count(),
                          sorted(BranchStock.objects.values_list('product_id', 'quantity'))), before)
        self.assertEqual(self.refresh().status, StockUnit.Status.AVAILABLE)
        log = AdminAuditLog.objects.filter(action='stock_unit_updated', target_id=self.device.pk).latest('pk')
        self.assertEqual((log.company_id, log.metadata['changed']), (self.company.pk, ['sale_price']))
        assert_no_amount(self, log.metadata, '1999')


# ---------------------------------------------------------------------------
# «Equipos seminuevos»: the devices without a price, and where each one stands online
# ---------------------------------------------------------------------------

class UsedListTest(PriceFixture):
    def setUp(self):
        super().setUp()
        self.priced_unit, self.zero_unit = units.receive_units(
            branch=self.centro, product=self.used, actor=self.full, reason='Compra',
            units=[{'serial_number': 'PRECIO0010', 'imei': IMEIS[23], 'condition': 'used', 'sale_price': '1999.00'},
                   {'serial_number': 'PRECIO0011', 'imei': IMEIS[24], 'condition': 'used'}])
        # A row written before this phase with a zero price: it is «sin precio», not a price.
        StockUnit.objects.filter(pk=self.zero_unit.pk).update(sale_price=Decimal('0.00'))
        [self.away] = units.receive_units(
            branch=self.norte, product=self.used, actor=self.full, reason='Compra',
            units=[{'serial_number': 'PRECIO0012', 'imei': IMEIS[25], 'condition': 'used', 'sale_price': '1500.00'}])

    def listed(self, user=None, **params):
        res = _client(user or self.full).get(UNITS, {'line': 'used', 'branch': 'all', **params})
        self.assertEqual(res.status_code, 200, res.content)
        return {row['serial_number']: row for row in res.json()['results']}

    def test_the_filter_separates_the_devices_without_a_usable_price(self):
        self.assertEqual(set(self.listed(priced='no')), {'PRECIO0001', 'PRECIO0011'})
        self.assertEqual(set(self.listed(priced='yes')), {'PRECIO0010', 'PRECIO0012'})
        self.assertEqual(len(self.listed()), 4)
        self.assertEqual(_client(self.full).get(UNITS, {'line': 'used', 'priced': 'quizas'}).status_code, 400)

    def test_each_device_says_whether_the_shop_offers_it_and_why_not(self):
        Product.objects.filter(pk=self.used.pk).update(is_published_online=True)
        rows = self.listed()
        self.assertEqual(rows['PRECIO0010']['web'], {'offered': True, 'reasons': []})
        self.assertEqual(rows['PRECIO0011']['web'], {'offered': False, 'reasons': ['no_price']})
        self.assertEqual(rows['PRECIO0012']['web'], {'offered': False, 'reasons': ['not_in_dispatch_branch']})
        ProductModel.objects.filter(pk=self.model.pk).update(is_published_online=False)
        Product.objects.filter(pk=self.used.pk).update(is_published_online=False)
        self.assertEqual(self.listed()['PRECIO0010']['web'],
                         {'offered': False, 'reasons': ['product_hidden', 'model_hidden']})
        StockUnit.objects.filter(pk=self.priced_unit.pk).update(status=StockUnit.Status.SOLD)
        self.assertIn('not_available', self.listed()['PRECIO0010']['web']['reasons'])

    def test_the_web_state_agrees_with_what_the_model_page_offers(self):
        Product.objects.filter(pk=self.used.pk).update(is_published_online=True)
        offered = set(device_catalog.offered_units([self.used], self.centro).values_list('serial_number', flat=True))
        said = {serial for serial, row in self.listed().items() if row['web']['offered']}
        self.assertEqual(said, offered)
        self.assertEqual(offered, {'PRECIO0010'})


# ---------------------------------------------------------------------------
# A reception: the six cases of the owner, and what each line says afterwards
# ---------------------------------------------------------------------------

class ReceptionFollowUpTest(PriceFixture):
    def follow(self, response):
        return [line['follow_up'] for line in response.json()['lines']]

    def test_cost_and_price_are_two_amounts_stored_apart(self):
        self.confirm(self.full, self.body(self.dev(
            self.used, self.priced('RECEP00001', IMEIS[26], '1999.00', cost='1200.00'))), expect=201)
        stored = StockUnit.objects.get(serial_number='RECEP00001')
        self.assertEqual((stored.cost, stored.sale_price), (Decimal('1200.00'), Decimal('1999.00')))

    def test_only_a_cost_leaves_the_device_unpriced_and_the_line_says_who_must_price_it(self):
        res = self.confirm(self.costed, self.body(self.dev(
            self.used, self.used_unit('RECEP00002', IMEIS[27], cost='1200.00'))), expect=201)
        [info] = self.follow(res)
        self.assertEqual((info['units_priced'], info['units_unpriced']), (0, 1))
        self.assertIn('unpriced', info['blocked_reasons'])
        self.assertEqual(info['online'], 'blocked')
        stored = StockUnit.objects.get(serial_number='RECEP00002')
        self.assertEqual((stored.cost, stored.sale_price), (Decimal('1200.00'), None))

    def test_only_a_price_by_whoever_may_set_it(self):
        self.confirm(self.pricer, self.body(self.dev(self.used, self.priced('RECEP00003', IMEIS[28]))), expect=201)
        stored = StockUnit.objects.get(serial_number='RECEP00003')
        self.assertEqual((stored.cost, stored.sale_price), (None, Decimal(PRICE)))

    def test_two_devices_of_one_variant_with_two_prices_and_one_without(self):
        res = self.confirm(self.pricer, self.body(self.dev(
            self.used, self.priced('RECEP00004', IMEIS[29], '1700.00'), self.priced('RECEP00005', IMEIS[30], '1850.50'),
            self.used_unit('RECEP00006', IMEIS[31]))), expect=201)
        [info] = self.follow(res)
        self.assertEqual((info['received'], info['units_priced'], info['units_unpriced']), (3, 2, 1))
        assert_no_amount(self, res.json(), '1700.00', '1850.50')

    def test_what_is_missing_is_told_without_any_amount(self):
        res = self.confirm(self.full, self.body(self.dev(
            self.used, self.priced('RECEP00007', IMEIS[32], cost='1200.00', battery_health=91),
            self.used_unit('RECEP00008', IMEIS[33]))), expect=201)
        [info] = self.follow(res)
        self.assertEqual((info['units_without_battery'], info['units_without_grade'], info['units_without_photos']),
                         (1, 2, 2))
        self.assertEqual(info['storage_branch'], {'id': self.centro.pk, 'name': self.centro.name})
        self.assertEqual(info['dispatch_branch'], {'id': self.centro.pk, 'name': self.centro.name})
        self.assertTrue(info['in_dispatch_branch'])
        self.assertEqual(info['priced_by'], 'unit')
        self.assertEqual(info['model'], {'id': self.model.pk, 'name': self.model.name, 'published': True})
        assert_no_amount(self, res.json(), PRICE, '1200.00')
        for log in AdminAuditLog.objects.filter(company=self.company):
            assert_no_amount(self, log.metadata, PRICE, '1200.00')

    def test_a_price_never_publishes_and_the_follow_up_is_read_live_on_a_replay(self):
        body = self.body(self.dev(self.used, self.priced('RECEP00009', IMEIS[34]), self.used_unit('RECEP00010', IMEIS[35])))
        first = self.confirm(self.pricer, body, key='seguimiento-1', expect=201)
        [info] = self.follow(first)
        self.used.refresh_from_db()
        self.assertFalse(self.used.is_published_online)                      # a price published nothing
        # Hidden, and one device still unpriced: «pendiente» (INV-COM-01B's reading), and nothing is offered.
        self.assertEqual((info['state'], info['online'], info['units_offered_online']), ('pending', 'blocked', 0))
        self.assertIn('not_published', info['blocked_reasons'])

        # Somebody publishes the variant and prices the second device; the replay tells what is true NOW. The
        # fixture's own device (another reception) is priced too: it is offered, but it is not THIS line's.
        Product.objects.filter(pk=self.used.pk).update(is_published_online=True)
        units.update_unit(StockUnit.objects.get(serial_number='RECEP00010'), sale_price='1500.00')
        units.update_unit(self.device, sale_price='1000.00')
        again = self.confirm(self.pricer, body, key='seguimiento-1', expect=200)
        self.assertTrue(again.json()['replayed'])
        [info] = self.follow(again)
        self.assertEqual((info['state'], info['online'], info['units_offered_online']), ('published', 'offered', 2))
        self.assertEqual((info['units_priced'], info['units_unpriced'], info['blocked_reasons']), (2, 0, []))
        # The record of the reception did not change: still one entry, two devices, one movement.
        self.assertEqual(StockUnit.objects.filter(product=self.used).count(), 3)     # + the fixture's device
        self.assertEqual(StockMovement.objects.filter(product=self.used).count(), 2)
        self.assertEqual(again.json()['lines'][0]['units_unpriced'], 1)              # what it was when received

    def test_a_zero_price_written_before_the_rule_reads_as_unpriced_in_the_follow_up(self):
        body = self.body(self.dev(self.used, self.priced('RECEP00015', IMEIS[40])))
        self.confirm(self.pricer, body, key='seguimiento-cero', expect=201)
        StockUnit.objects.filter(serial_number='RECEP00015').update(sale_price=Decimal('0.00'))
        [info] = self.follow(self.confirm(self.pricer, body, key='seguimiento-cero', expect=200))
        self.assertEqual((info['units_priced'], info['units_unpriced']), (0, 1))
        self.assertIn('unpriced', info['blocked_reasons'])

    def test_a_branch_that_is_not_the_dispatch_branch_blocks_the_web(self):
        Product.objects.filter(pk=self.used.pk).update(is_published_online=True)
        res = self.confirm(self.pricer, self.body(self.dev(self.used, self.priced('RECEP00011', IMEIS[36])),
                                                  branch=self.norte.pk), expect=201)
        [info] = self.follow(res)
        self.assertEqual((info['in_dispatch_branch'], info['online'], info['units_offered_online']), (False, 'blocked', 0))
        self.assertEqual(info['blocked_reasons'], ['not_in_dispatch_branch'])
        self.assertEqual(info['storage_branch']['id'], self.norte.pk)

    def test_a_company_without_a_dispatch_branch_says_so(self):
        type(self.company).objects.filter(pk=self.company.pk).update(default_inventory_branch=None)
        res = self.confirm(self.pricer, self.body(self.dev(self.used, self.priced('RECEP00012', IMEIS[37]))), expect=201)
        [info] = self.follow(res)
        self.assertIsNone(info['dispatch_branch'])
        self.assertIn('no_dispatch_branch', info['blocked_reasons'])

    def test_a_quantity_line_follows_its_product(self):
        Product.objects.filter(pk=self.cable.pk).update(is_published_online=True)
        res = self.confirm(self.clerk, self.body(self.qty(self.cable, 5)), expect=201)
        [info] = self.follow(res)
        self.assertEqual((info['priced_by'], info['units_unpriced'], info['online']), ('product', 0, 'offered'))
        self.assertIsNone(info['units_offered_online'])

    def test_a_retry_adds_no_stock_no_kardex_and_no_label(self):
        body = self.body(self.dev(self.used, self.priced('RECEP00013', IMEIS[38])))
        self.confirm(self.pricer, body, key='reintento-precio', expect=201)
        before = self.snapshot()
        for _ in range(2):
            self.assertEqual(self.confirm(self.pricer, body, key='reintento-precio', expect=200).json()['replayed'], True)
        self.assertEqual(self.snapshot(), before)

    def test_the_preview_creates_no_stock(self):
        before = self.snapshot()
        self.preview(self.full, self.body(self.dev(self.used, self.priced('RECEP00014', IMEIS[39], cost='1.00'))),
                     expect=200)
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------------------
# One price: the till, the model page, the cart and the checkout
# ---------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['.tienda.pe', 'testserver'])
class OnePriceEverywhereTest(DeviceShop, TestCase):
    """`used128` has a PRODUCT price of 1.00: if it ever shows up, something fell back to it."""

    def setUp(self):
        self.build('precio-03')
        ProductModel.objects.filter(pk=self.model.pk).update(is_published_online=True)
        Product.objects.filter(pk=self.used128.pk).update(is_published_online=True)
        self.used128.refresh_from_db()
        self.priced, self.unpriced, self.zero = self.receive(self.used128, 3, condition='used')
        units.update_unit(self.priced, sale_price='1999.00')
        StockUnit.objects.filter(pk=self.zero.pk).update(sale_price=Decimal('0.00'))   # legacy row
        self.zero.refresh_from_db()
        self.public = shop_client(host=self.host)

    def till_price(self, unit):
        products = {self.used128.pk: self.used128}
        items = [{'product': self.used128.pk, 'quantity': 1, 'units': [{'id': unit.pk}]}]
        return pos_services.unit_prices(self.company, self.branch, products, items)[self.used128.pk][0][1]

    def page(self):
        res = self.public.get(f'/api/catalog/models/{self.model.slug}/')
        self.assertEqual(res.status_code, 200, res.content)
        return {card['id']: card for card in res.json()['used_units']}

    def add(self, unit, session='precio-03-a'):
        return self.public.post('/api/cart/add/', {'session_key': session, 'product': self.used128.pk,
                                                   'unit': str(unit.public_id)}, format='json')

    def test_the_till_the_page_and_the_cart_read_the_same_price(self):
        self.assertEqual(self.till_price(self.priced), Decimal('1999.00'))
        self.assertEqual(self.page()[str(self.priced.public_id)]['price'], '1999.00')
        res = self.add(self.priced)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['units'][0]['price'], '1999.00')
        self.priced.refresh_from_db()
        self.assertEqual(self.priced.hold_price, Decimal('1999.00'))

    def test_a_device_without_a_usable_price_is_offered_nowhere_and_never_at_the_products_price(self):
        cards = self.page()
        for unit in (self.unpriced, self.zero):
            self.assertNotIn(str(unit.public_id), cards)
            res = self.add(unit, session=f'precio-03-{unit.pk}')
            self.assertEqual(res.status_code, 404, (unit.serial_number, res.content))
            with self.assertRaises(pos_services.PosValidationError):
                self.till_price(unit)
            self.assertIsNone(holds.price_for(unit, self.used128))
            self.assertIsNone(device_catalog._unit_price(unit, self.used128))
        self.assertNotIn('"1.00"', self.public.get(f'/api/catalog/models/{self.model.slug}/').content.decode())
        listing = self.public.get('/api/catalog/models/').json()['results']
        self.assertEqual([row['from_price'] for row in listing if row['slug'] == self.model.slug], ['1999.00'])

    def test_a_held_device_whose_price_was_zeroed_cannot_be_checked_out_at_any_price(self):
        from store import checkout_services as checkout
        from store.models import CartItem
        self.assertEqual(self.add(self.priced).status_code, 200)
        for price in (Decimal('0.00'), None):
            StockUnit.objects.filter(pk=self.priced.pk).update(sale_price=price)
            items = list(CartItem.objects.filter(session_key='precio-03-a').select_related('product'))
            with self.assertRaises(checkout.CheckoutError) as caught:
                checkout.lines_from_cart(self.company, self.branch, 'precio-03-a', items)
            self.assertEqual(caught.exception.errors, [f'{self.used128.name} ya no está disponible.'], price)


# ---------------------------------------------------------------------------
# A price changed while the cashier was selling
# ---------------------------------------------------------------------------

class PriceChangedDuringASaleTest(Ip1PosBase):
    def setUp(self):
        super().setUp()
        self.client = _web_login('ip1_caja')
        self.used = Product.objects.create(
            company=self.company, name='iPhone 14 Seminuevo Precio', slug='iphone-14-precio-03', price='1.00',
            inventory=0, is_serialized=True, requires_imei=True, condition_line=Product.CONDITION_USED)
        [self.a] = units.receive_units(
            branch=self.branch_a, product=self.used, actor=self.staff, reason='Compra',
            units=[{'serial_number': 'USADO30001', 'imei': IMEIS[0], 'condition': 'used', 'sale_price': '2100.00'}])
        cache.clear()
        self.pricer, _ = _p2d_member(self.company, 'pos_pricer', [
            'inventory.view', 'inventory.adjust', 'products.view', 'products.manage'])

    def items(self):
        return [{'product': self.used.pk, 'quantity': 1, 'units': [{'id': self.a.pk, 'method': 'scanner'}]}]

    def test_a_price_changed_on_the_ficha_after_the_preview_is_a_409_and_nothing_is_sold(self):
        seen = self.client.post('/api/admin/pos/preview/', {'branch': self.branch_a.pk, 'items': self.items()},
                                format='json').json()['total']
        self.assertEqual(seen, '2100.00')
        res = _client(self.pricer).patch(detail(self.a), {'sale_price': '2300.00'}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        body = self.sale_body(items=self.items(), amount_received='10000.00', idempotency_key='precio-03-409',
                              expected_total=seen)
        sale = self.client.post('/api/admin/pos/sales/', body, format='json')
        self.assertEqual(sale.status_code, 409, sale.content)
        self.assertEqual((sale.json()['code'], sale.json()['current_total']), ('price_changed', '2300.00'))
        self.assertEqual(Order.objects.count(), 0)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, StockUnit.Status.AVAILABLE)
        # Charged at the new price once the cashier saw it — never at the product's 1.00.
        body['expected_total'] = '2300.00'
        done = self.client.post('/api/admin/pos/sales/', body, format='json')
        self.assertEqual((done.status_code, done.json()['total']), (201, '2300.00'), done.content)
