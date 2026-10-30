"""
INV-COM-01A — the commercial file of a used device travels with its reception.

    POST /api/admin/inventory/receipts/preview/ · confirm/

A unit of a USED-line product may carry its own `sale_price` and `public_notes` (the fields «Registrar equipos»
already accepts). Three amounts are never confused: the cost (`costs.view`), this price (`products.manage`) and,
later, a trade-in valuation. What these tests hold:

  * a priced used device is stored with its price and its notes; several devices of one variant keep each its own;
  * whoever cannot manage products is refused (400 `prices_forbidden`) WHEN THEY SEND a price, writing nothing — and
    can still receive the device without one, which then waits, unpriced, to be completed by somebody with authority;
  * a price is an ASCII amount with at most two decimals and greater than zero; never a float, a boolean or a blank;
  * a new-line product (or a quantity line) never carries a price of its own — a receipt never changes `Product.price`,
    `is_active` or `is_published_online`, and never creates a product;
  * the preview warns how many used devices are unpriced, without repeating a price; the answer and the trail never
    carry an amount;
  * a changed price under the same key is a conflict; a replay adds nothing; a later loss of authority is respected.
"""
from decimal import Decimal

from django.core.cache import cache

from store import device_catalog
from store.models import AdminAuditLog, MembershipRoleAssignment, Product, StockUnit
from store.test_device_commerce import assert_no_amount
from store.test_inventory_receipts import ReceiptBase, unit
from store.test_registration import IMEIS
from store.tests import _p2d_member

PRICE = '1899.00'


class CommercialBase(ReceiptBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.model = device_catalog.create_model(company=self.company, name='iPhone 13 Comercial', is_published_online=True)
        self.used = self.serialized('iPhone 13 128 GB Seminuevo', 'iphone13-seminuevo', imei=True)
        Product.objects.filter(pk=self.used.pk).update(price=Decimal('0.00'), is_published_online=False)
        device_catalog.set_variant(self.used, model_id=self.model.pk, condition_line=Product.CONDITION_USED)
        self.used.refresh_from_db()
        self.pricer, _ = _p2d_member(
            self.company, 'ops_pricer', ['inventory.view', 'inventory.adjust', 'products.view', 'products.manage'])

    def used_unit(self, serial, imei, **extra):
        return unit(serial, imei, condition='used', **extra)

    def priced(self, serial, imei, price=PRICE, **extra):
        return self.used_unit(serial, imei, sale_price=price, **extra)


class PricedUsedDeviceTest(CommercialBase):
    def test_a_used_device_keeps_its_own_price_and_its_public_notes(self):
        res = self.confirm(self.pricer, self.body(self.dev(
            self.used, self.priced('SEMI00001', IMEIS[0], battery_health=91, notes='Interno: rayón leve',
                                   public_notes='Incluye cargador original'))), expect=201)
        [created] = StockUnit.objects.filter(company=self.company, product=self.used)
        self.assertEqual(created.sale_price, Decimal(PRICE))
        self.assertEqual((created.public_notes, created.notes, created.battery_health),
                         ('Incluye cargador original', 'Interno: rayón leve', 91))
        # The product's own price is the catalogue's: a receipt never changes it, publishes or creates anything.
        self.used.refresh_from_db()
        self.assertEqual((self.used.price, self.used.is_published_online, self.used.is_active),
                         (Decimal('0.00'), False, True))
        [line] = res.json()['lines']
        self.assertEqual(line['units_unpriced'], 0)

    def test_several_devices_of_one_variant_keep_their_own_prices(self):
        self.confirm(self.pricer, self.body(self.dev(
            self.used, self.priced('SEMI00011', IMEIS[1], '1700.00'), self.priced('SEMI00012', IMEIS[2], '1850.50'),
            self.used_unit('SEMI00013', IMEIS[3]))), expect=201)
        prices = dict(StockUnit.objects.filter(product=self.used).values_list('serial_number', 'sale_price'))
        self.assertEqual(prices, {'SEMI00011': Decimal('1700.00'), 'SEMI00012': Decimal('1850.50'), 'SEMI00013': None})

    def test_the_answer_and_the_trail_carry_no_amount(self):
        res = self.confirm(self.pricer, self.body(self.dev(self.used, self.priced('SEMI00021', IMEIS[4]))), expect=201)
        assert_no_amount(self, res.json(), PRICE)
        for log in AdminAuditLog.objects.filter(company=self.company):
            assert_no_amount(self, log.metadata, PRICE)
        pre = self.preview(self.pricer, self.body(self.dev(self.used, self.priced('SEMI00022', IMEIS[5]))), expect=200)
        assert_no_amount(self, pre.json(), PRICE)

    def test_a_price_is_part_of_the_idempotency_fingerprint(self):
        body = self.body(self.dev(self.used, self.priced('SEMI00031', IMEIS[6])))
        first = self.confirm(self.pricer, body, key='price-key', expect=201)
        again = self.confirm(self.pricer, body, key='price-key', expect=200)
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(again.json()['receipt_id'], first.json()['receipt_id'])
        self.assertEqual(StockUnit.objects.filter(product=self.used).count(), 1)
        other = self.body(self.dev(self.used, self.priced('SEMI00031', IMEIS[6], '1999.00')))
        self.assertEqual(self.confirm(self.pricer, other, key='price-key').status_code, 409)
        self.assertEqual(StockUnit.objects.get(product=self.used).sale_price, Decimal(PRICE))


class AuthorityTest(CommercialBase):
    def test_without_authority_over_prices_a_price_is_refused_and_nothing_is_written(self):
        before = self.snapshot()
        body = self.body(self.dev(self.used, self.priced('SEMI00041', IMEIS[7])))
        res = self.confirm(self.clerk, body, expect=400)
        self.assertEqual(res.json()['code'], 'prices_forbidden')
        self.assertEqual(res.json()['field'], 'lines[0].units[0].sale_price')
        self.assertEqual(self.preview(self.clerk, body).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_without_authority_the_device_is_still_received_unpriced_and_the_preview_says_so(self):
        body = self.body(self.dev(self.used, self.used_unit('SEMI00042', IMEIS[8], public_notes='Caja original')))
        pre = self.preview(self.clerk, body, expect=200).json()
        [line] = pre['lines']
        self.assertTrue(any('sin precio propio' in w for w in line['warnings']))
        res = self.confirm(self.clerk, body, expect=201).json()
        [created] = StockUnit.objects.filter(product=self.used)
        self.assertIsNone(created.sale_price)
        self.assertEqual(created.public_notes, 'Caja original')   # notes need only inventory.adjust
        self.assertEqual(res['lines'][0]['units_unpriced'], 1)

    def test_costs_and_prices_are_separate_authorities(self):
        # costs.view does not allow prices, and products.manage does not allow costs.
        both = self.body(self.dev(self.used, self.priced('SEMI00043', IMEIS[9], cost='900.00')))
        self.assertEqual(self.confirm(self.costed, both).json()['code'], 'prices_forbidden')
        self.assertEqual(self.confirm(self.pricer, both).json()['code'], 'costs_forbidden')

    def test_losing_the_authority_is_respected_on_the_next_request(self):
        body = lambda s, i: self.body(self.dev(self.used, self.priced(s, i)))   # noqa: E731
        self.confirm(self.pricer, body('SEMI00051', IMEIS[10]), expect=201)
        assignment = MembershipRoleAssignment.objects.get(membership__user=self.pricer)
        role = assignment.role
        role.capabilities = [c for c in role.capabilities if c != 'products.manage']
        role.save(update_fields=['capabilities'])
        res = self.confirm(self.pricer, body('SEMI00052', IMEIS[11]))
        self.assertEqual((res.status_code, res.json()['code']), (400, 'prices_forbidden'))

    def test_a_foreign_product_with_a_price_is_a_404_like_any_other_foreign_product(self):
        foreign = self.serialized('iPhone ajeno usado', 'iphone-ajeno-usado', imei=True, company=self.other)
        res = self.confirm(self.pricer, self.body(self.dev(foreign, self.priced('SEMI00061', IMEIS[0]))))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(StockUnit.objects.filter(product=foreign).count(), 0)

    def test_the_branch_scope_is_unchanged(self):
        limited, _ = _p2d_member(self.company, 'ops_limited_pricer',
                                 ['inventory.view', 'inventory.adjust', 'products.view', 'products.manage'],
                                 branches=[self.centro])
        body = self.body(self.dev(self.used, self.priced('SEMI00071', IMEIS[1])), branch=self.norte.pk)
        self.assertEqual(self.confirm(limited, body).status_code, 404)
        self.assertEqual(StockUnit.objects.filter(product=self.used).count(), 0)


class InvalidPriceTest(CommercialBase):
    def test_a_price_is_a_positive_ascii_amount_with_two_decimals_at_most(self):
        for bad in ('0', '0.00', '-5', 'abc', '1.234', '1e3', '١٢٣', 12.5, True, [], {}):
            with self.subTest(price=bad):
                before = self.snapshot()
                res = self.confirm(self.pricer, self.body(self.dev(self.used, self.priced('SEMI00081', IMEIS[2], bad))))
                self.assertEqual(res.status_code, 400, res.content)
                self.assertEqual(res.json()['field'], 'lines[0].units[0].sale_price')
                self.assertEqual(self.snapshot(), before)

    def test_a_blank_price_is_no_price_and_an_integer_is_a_price(self):
        self.confirm(self.pricer, self.body(self.dev(
            self.used, self.priced('SEMI00091', IMEIS[3], ''), self.priced('SEMI00092', IMEIS[4], None),
            self.priced('SEMI00093', IMEIS[5], 2000))), expect=201)
        prices = dict(StockUnit.objects.filter(product=self.used).values_list('serial_number', 'sale_price'))
        self.assertEqual(prices, {'SEMI00091': None, 'SEMI00092': None, 'SEMI00093': Decimal('2000.00')})

    def test_public_notes_have_a_limit(self):
        res = self.confirm(self.clerk, self.body(self.dev(
            self.used, self.used_unit('SEMI00101', IMEIS[6], public_notes='x' * 501))))
        self.assertEqual((res.status_code, res.json()['field']), (400, 'lines[0].units[0].public_notes'))


class NewProductsCarryNoPriceTest(CommercialBase):
    def test_a_new_line_device_refuses_a_price_of_its_own(self):
        body = self.body(self.dev(self.phone, unit('NUEVO10001', IMEIS[7], sale_price=PRICE)))
        before = self.snapshot()
        pre = self.preview(self.pricer, body, expect=200).json()
        self.assertFalse(pre['ok'])
        [issue] = pre['lines'][0]['unit_issues']
        self.assertEqual((issue['field'], issue['kind']), ('sale_price', 'error'))
        res = self.confirm(self.pricer, body, expect=400)
        self.assertEqual(res.json()['code'], 'invalid_lines')
        self.assertEqual(self.snapshot(), before)
        self.phone.refresh_from_db()
        self.assertEqual(self.phone.price, Decimal('4500.00'))

    def test_a_new_line_device_refuses_buyer_notes_but_a_plain_one_is_received(self):
        res = self.preview(self.pricer, self.body(self.dev(self.phone, unit('NUEVO10002', IMEIS[8], public_notes='x'))))
        self.assertFalse(res.json()['ok'])
        self.confirm(self.pricer, self.body(self.dev(self.phone, unit('NUEVO10003', IMEIS[9]))), expect=201)

    def test_a_quantity_line_keeps_the_catalogue_price_and_changes_nothing_of_it(self):
        pre = self.preview(self.clerk, self.body(self.qty(self.cable, 5)), expect=200).json()
        [line] = pre['lines']
        self.assertEqual(line['product']['price'], '25.00')
        self.confirm(self.clerk, self.body(self.qty(self.cable, 5)), expect=201)
        self.cable.refresh_from_db()
        self.assertEqual(self.cable.price, Decimal('25.00'))

    def test_a_reception_never_creates_publishes_or_prices_a_product(self):
        before = self.snapshot()['products']
        self.confirm(self.pricer, self.body(self.dev(self.used, self.priced('SEMI00111', IMEIS[10]))), expect=201)
        self.assertEqual(Product.objects.count(), before)
        self.used.refresh_from_db()
        self.assertFalse(self.used.is_published_online)
