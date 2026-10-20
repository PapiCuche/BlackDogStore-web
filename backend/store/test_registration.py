"""
COMMERCE-ADMIN-UX-01 — «Registrar artículo»: one screen, four kinds of article.

    POST /api/admin/products/register/

THE ENDPOINT WRITES NOTHING OF ITS OWN. It calls, in ONE transaction, the
services every other screen already calls:

  * the product            → `AdminProductWriteSerializer` (same validation)
  * model / variant        → `device_catalog.create_model` / `set_variant`
  * counted by serial      → `stock_unit_services.set_serialized`
  * devices on the shelf   → `stock_unit_services.receive_units`
  * quantities per branch  → `inventory_services.apply_initial_stock` (with the unit cost)
  * supplier / offer       → `supply_services.create_supplier` / `create_offer` / `set_sale_mode`

So the Kardex rows these tests look for are the writers' own rows, and a bad
line anywhere leaves NOTHING behind.

Authority per step = the authority of the action underneath: `products.manage`
to register at all; `inventory.adjust` for devices and for stock; `costs.view`
for any cost; the caller's branch grants for every branch. Idempotent by
`Idempotency-Key`; `dry_run` answers what WOULD happen and writes nothing.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from store import device_catalog
from store.models import (
    AdminAuditLog, BranchStock, Category, CompanySettings, CompanyRole, Membership, Product, ProductBarcode,
    ProductModel, StockMovement, StockUnit, Supplier, SupplierOffer,
)
from store.test_device_commerce import assert_no_amount
from store.test_stock_units import imei
from store.tests import _assign, _p2d_branch, _p2d_member, _p3_company

URL = '/api/admin/products/register/'
MODELS_BULK = '/api/admin/catalog/models/bulk-visibility/'

IMEIS = [imei(f'35693803564{n:03d}') for n in range(10, 40)]
BAD_IMEI = IMEIS[0][:-1] + str((int(IMEIS[0][-1]) + 1) % 10)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class RegistrationFixture(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('reg-tienda', 'Tienda Registro')
        self.other = _p3_company('reg-otra', 'Otra Registro')
        self.centro = self.company.default_inventory_branch
        self.norte = _p2d_branch(self.company, 'Norte Registro')
        self.phones = Category.objects.create(company=self.company, name='Celulares', slug='celulares-reg')
        self.cases = Category.objects.create(company=self.company, name='Fundas', slug='fundas-reg')
        self.foreign_category = Category.objects.create(company=self.other, name='Ajena', slug='ajena-reg')

        self.capacity = device_catalog.create_attribute(company=self.company, name='Capacidad')
        self.colour = device_catalog.create_attribute(company=self.company, name='Color', kind='color')
        self.gb128 = device_catalog.create_option(company=self.company, attribute_id=self.capacity.pk, label='128 GB')
        self.black = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk,
                                                  label='Negro', swatch_hex='#111111')
        self.model = device_catalog.create_model(company=self.company, name='iPhone 15', category_id=self.phones.pk)
        self.foreign_model = device_catalog.create_model(company=self.other, name='Ajeno')

        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.create_user('reg_admin', password='Pass123!')
        membership = Membership.objects.create(user=self.admin, company=self.company, role='customer')
        _assign(membership, CompanyRole.objects.get(company=self.company, slug='administrador'))

        self.clerk, _ = _p2d_member(self.company, 'reg_clerk', [
            'products.view', 'products.manage', 'inventory.view', 'inventory.adjust'])
        self.catalog_only, _ = _p2d_member(self.company, 'reg_catalog', ['products.view', 'products.manage'])
        self.viewer, _ = _p2d_member(self.company, 'reg_viewer', ['products.view', 'inventory.view'])
        self.centro_only, _ = _p2d_member(self.company, 'reg_centro', [
            'products.view', 'products.manage', 'inventory.view', 'inventory.adjust', 'costs.view'],
            branches=[self.centro])
        self.outsider, _ = _p2d_member(self.other, 'reg_outsider', [
            'products.view', 'products.manage', 'inventory.view', 'inventory.adjust', 'costs.view'])
        self._key = 0

    # -- helpers -------------------------------------------------------------

    def key(self):
        self._key += 1
        return f'reg-key-{self._key}'

    def post(self, user, body, *, key=None, expect=None):
        headers = {}
        if key is not False:
            headers['HTTP_IDEMPOTENCY_KEY'] = key or self.key()
        res = _client(user).post(URL, body, format='json', **headers)
        if expect is not None:
            self.assertEqual(res.status_code, expect, res.content)
        return res

    def counts(self):
        return (
            Product.objects.filter(company=self.company).count(),
            StockMovement.objects.filter(company=self.company).count(),
            StockUnit.objects.filter(company=self.company).count(),
            Supplier.objects.filter(company=self.company).count(),
            SupplierOffer.objects.filter(company=self.company).count(),
            ProductModel.objects.filter(company=self.company).count(),
            ProductBarcode.objects.filter(company=self.company).count(),
        )

    def new_device(self, **over):
        body = {
            'kind': 'new_device',
            'product': {'name': 'iPhone 15 128 GB Negro', 'category': self.phones.pk, 'price': '3999.00',
                        'sku': 'IP15-128-NEG', 'is_active': True, 'show_online': True},
            'model': {'id': self.model.pk},
            'options': [self.gb128.pk, self.black.pk],
            'requires_imei': True,
            'branch': self.centro.pk,
            'units': [{'serial_number': 'SNREG001', 'imei': IMEIS[1]},
                      {'serial_number': 'SNREG002', 'imei': IMEIS[2]}],
        }
        body.update(over)
        return body

    def used_device(self, **over):
        body = {
            'kind': 'used_device',
            'product': {'name': 'iPhone 15 128 GB Negro seminuevo', 'category': self.phones.pk,
                        'price': '2500.00', 'is_active': True, 'show_online': True},
            'model': {'id': self.model.pk},
            'options': [self.gb128.pk, self.black.pk],
            'branch': self.centro.pk,
            'unit': {'serial_number': 'SNUSED01', 'imei': IMEIS[5], 'battery_health': 88,
                     'cosmetic_grade': 'excelente', 'sale_price': '2399.00'},
        }
        body.update(over)
        return body

    def accessory(self, **over):
        body = {
            'kind': 'accessory',
            'product': {'name': 'Funda MagSafe', 'category': self.cases.pk, 'price': '89.90',
                        'sku': 'FUNDA-MS-01', 'is_active': True, 'show_online': True},
            'stock': [{'branch': self.centro.pk, 'quantity': 5}, {'branch': self.norte.pk, 'quantity': 3}],
        }
        body.update(over)
        return body

    def offer(self, **over):
        today = timezone.localdate()
        offer = {'commercial_price': '4199.00', 'confirmed_quantity': 10,
                 'valid_from': today.isoformat(), 'valid_until': (today + timedelta(days=14)).isoformat(),
                 'lead_time_days': 3}
        offer.update(over)
        return offer

    def on_order(self, **over):
        body = {
            'kind': 'supplier',
            'product': {'name': 'iPad Pro 13', 'category': self.phones.pk, 'price': '6999.00',
                        'is_active': True, 'show_online': False},
            'supplier': {'name': 'Mayorista Lima'},
            'offer': self.offer(),
        }
        body.update(over)
        return body


# ---------------------------------------------------------------------------
# The four kinds
# ---------------------------------------------------------------------------

class NewDeviceTest(RegistrationFixture):
    def test_creates_the_variant_and_receives_each_device_through_receive_units(self):
        res = self.post(self.clerk, self.new_device(), expect=201)
        data = res.json()
        product = Product.objects.get(pk=data['product']['id'], company=self.company)

        self.assertTrue(data['product']['created'])
        self.assertEqual(product.product_model_id, self.model.pk)
        self.assertEqual(product.condition_line, 'new')
        self.assertTrue(product.is_serialized)
        self.assertTrue(product.requires_imei)
        self.assertEqual(sorted(o.pk for o in device_catalog.variant_options_of(product)),
                         sorted([self.gb128.pk, self.black.pk]))
        self.assertEqual(product.inventory, 2)

        devices = StockUnit.objects.filter(product=product, branch=self.centro, status='available')
        self.assertEqual(sorted(devices.values_list('imei', flat=True)), sorted([IMEIS[1], IMEIS[2]]))
        # The writer's own Kardex line: ONE entry for the batch, naming its units.
        movement = StockMovement.objects.get(product=product)
        self.assertEqual(movement.movement_type, StockMovement.INITIAL_STOCK)
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(sorted(movement.metadata['unit_ids']), sorted(devices.values_list('pk', flat=True)))
        self.assertEqual(movement.reference_type, 'stock_units')

        self.assertTrue(ProductBarcode.objects.filter(product=product, code='IP15-128-NEG', is_primary=True).exists())
        self.assertEqual(data['links']['product'], f'/admin/products/{product.pk}')
        self.assertEqual(data['links']['storefront'], f'/modelo/{self.model.slug}')
        self.assertEqual(len(data['units']), 2)
        self.assertTrue(AdminAuditLog.objects.filter(company=self.company, action='stock_units_received').exists())

    def test_a_bad_imei_check_digit_refuses_everything_and_names_the_line(self):
        before = self.counts()
        body = self.new_device(units=[{'serial_number': 'SNREG001', 'imei': IMEIS[1]},
                                      {'serial_number': 'SNREG002', 'imei': BAD_IMEI}])
        res = self.post(self.clerk, body, expect=400)
        self.assertEqual(res.json()['step'], 'stock')
        self.assertEqual(res.json()['line'], 2)
        self.assertEqual(res.json()['field'], 'imei')
        self.assertEqual(self.counts(), before)

    def test_a_device_already_registered_is_refused(self):
        self.post(self.clerk, self.new_device(), expect=201)
        before = self.counts()
        body = self.new_device(options=[self.gb128.pk], product={
            'name': 'iPhone 15 128 GB', 'category': self.phones.pk, 'price': '3999.00', 'is_active': True,
            'show_online': False})
        res = self.post(self.clerk, body, expect=400)
        self.assertIn('IMEI', res.json()['detail'])
        self.assertEqual(self.counts(), before)

    def test_an_existing_variant_receives_the_devices_and_keeps_its_data(self):
        first = self.post(self.clerk, self.new_device(), expect=201).json()
        body = self.new_device(product={'name': 'Otro nombre', 'category': self.phones.pk, 'price': '1.00',
                                        'is_active': True, 'show_online': True},
                               units=[{'serial_number': 'SNREG003', 'imei': IMEIS[3]}])
        data = self.post(self.clerk, body, expect=201).json()
        self.assertEqual(data['product']['id'], first['product']['id'])
        self.assertFalse(data['product']['created'])
        product = Product.objects.get(pk=first['product']['id'])
        self.assertEqual(product.name, 'iPhone 15 128 GB Negro')
        self.assertEqual(product.price, D('3999.00'))
        self.assertEqual(product.inventory, 3)
        self.assertEqual(StockMovement.objects.filter(product=product).latest('pk').movement_type,
                         StockMovement.PURCHASE_ENTRY)
        self.assertTrue(data['warnings'])

    def test_a_new_model_can_be_created_by_name(self):
        body = self.new_device(model={'name': 'Galaxy S25'}, options=[], units=[])
        data = self.post(self.clerk, body, expect=201).json()
        model = ProductModel.objects.get(company=self.company, name='Galaxy S25')
        self.assertTrue(data['model']['created'])
        self.assertTrue(model.is_published_online)
        self.assertEqual(Product.objects.get(pk=data['product']['id']).product_model_id, model.pk)

    def test_without_devices_nothing_enters_the_shelf(self):
        data = self.post(self.clerk, self.new_device(units=[]), expect=201).json()
        self.assertEqual(StockMovement.objects.filter(product_id=data['product']['id']).count(), 0)
        self.assertIsNone(data['links']['units'])

    def test_a_device_needs_a_model(self):
        before = self.counts()
        res = self.post(self.clerk, self.new_device(model=None), expect=400)
        self.assertEqual(res.json()['step'], 'model')
        self.assertEqual(self.counts(), before)

    def test_own_stock_and_a_supplier_offer_together(self):
        body = self.new_device(supplier={'name': 'Distribuidor Uno'}, offer=self.offer())
        data = self.post(self.clerk, body, expect=201).json()
        product = Product.objects.get(pk=data['product']['id'])
        self.assertEqual(product.sale_mode, Product.SALE_MODE_STOCK_OR_ORDER)
        self.assertEqual(product.inventory, 2)
        self.assertTrue(SupplierOffer.objects.filter(product=product, supplier__name='Distribuidor Uno').exists())


class UsedDeviceTest(RegistrationFixture):
    def test_one_device_with_its_battery_grade_price_and_cost(self):
        body = self.used_device()
        body['unit']['cost'] = '1800.00'
        data = self.post(self.admin, body, expect=201).json()
        product = Product.objects.get(pk=data['product']['id'])
        self.assertEqual(product.condition_line, 'used')
        unit = StockUnit.objects.get(product=product)
        self.assertEqual(unit.battery_health, 88)
        self.assertEqual(unit.cosmetic_grade.code, 'excelente')
        self.assertEqual(unit.sale_price, D('2399.00'))
        self.assertEqual(unit.cost, D('1800.00'))
        movement = StockMovement.objects.get(product=product)
        self.assertEqual(movement.metadata['unit_ids'], [unit.pk])
        self.assertEqual(movement.unit_cost, D('1800.00'))
        self.assertEqual(data['units'][0]['id'], unit.pk)

    def test_a_second_used_device_of_the_same_variant_joins_its_product(self):
        first = self.post(self.clerk, self.used_device(), expect=201).json()
        body = self.used_device(unit={'serial_number': 'SNUSED02', 'imei': IMEIS[6], 'battery_health': 79,
                                      'cosmetic_grade': 'bueno', 'sale_price': '1999.00'})
        second = self.post(self.clerk, body, expect=201).json()
        self.assertEqual(second['product']['id'], first['product']['id'])
        self.assertEqual(StockUnit.objects.filter(product_id=first['product']['id']).count(), 2)

    def test_exactly_one_device(self):
        before = self.counts()
        res = self.post(self.clerk, self.used_device(unit=None), expect=400)
        self.assertEqual(res.json()['step'], 'stock')
        self.assertEqual(self.counts(), before)

    def test_a_used_device_is_never_sold_on_order(self):
        before = self.counts()
        res = self.post(self.clerk, self.used_device(supplier={'name': 'X'}, offer=self.offer()), expect=400)
        self.assertEqual(res.json()['step'], 'supplier')
        self.assertEqual(self.counts(), before)

    def test_a_battery_out_of_range_is_refused(self):
        body = self.used_device()
        body['unit']['battery_health'] = 140
        before = self.counts()
        self.post(self.clerk, body, expect=400)
        self.assertEqual(self.counts(), before)


class AccessoryTest(RegistrationFixture):
    def test_quantities_per_branch_enter_through_the_kardex_with_their_unit_cost(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 5, 'unit_cost': '20.00'},
                                     {'branch': self.norte.pk, 'quantity': 3, 'unit_cost': '24.00'}])
        body['product']['reference_cost'] = '21.00'
        data = self.post(self.admin, body, expect=201).json()
        product = Product.objects.get(pk=data['product']['id'])
        self.assertFalse(product.is_serialized)
        self.assertEqual(product.reference_cost, D('21.00'))
        self.assertEqual(product.inventory, 8)
        moves = {m.branch_id: m for m in StockMovement.objects.filter(product=product)}
        self.assertEqual(set(moves), {self.centro.pk, self.norte.pk})
        self.assertEqual(moves[self.centro.pk].movement_type, StockMovement.INITIAL_STOCK)
        self.assertEqual(moves[self.centro.pk].unit_cost, D('20.00'))
        self.assertEqual(moves[self.norte.pk].quantity, 3)
        self.assertEqual(BranchStock.objects.get(product=product, branch=self.norte).average_cost, D('24.00'))
        self.assertEqual(sorted(data['movements']), sorted(m.pk for m in moves.values()))
        self.assertTrue(ProductBarcode.objects.filter(product=product, code='FUNDA-MS-01').exists())
        self.assertEqual(data['links']['storefront'], f'/product/{product.slug}')

    def test_without_stock_rows_the_catalogue_alone_is_enough(self):
        data = self.post(self.catalog_only, self.accessory(stock=[]), expect=201).json()
        self.assertEqual(StockMovement.objects.filter(product_id=data['product']['id']).count(), 0)

    def test_a_code_of_another_article_is_refused(self):
        self.post(self.clerk, self.accessory(), expect=201)
        before = self.counts()
        body = self.accessory(product={'name': 'Funda 2', 'category': self.cases.pk, 'price': '9.00',
                                       'sku': 'FUNDA-MS-01', 'is_active': True, 'show_online': False})
        res = self.post(self.clerk, body, expect=409)
        self.assertEqual(res.json()['field'], 'sku')
        self.assertEqual(self.counts(), before)

    def test_a_huge_or_non_finite_reference_cost_is_refused_and_nothing_is_written(self):
        for raw, expected in (('1e30', 'demasiado alto'), ('Infinity', 'número'), ('NaN', 'número')):
            with self.subTest(raw):
                body = self.accessory(stock=[])
                body['product']['reference_cost'] = raw
                before = self.counts()
                res = self.post(self.admin, body, expect=400)
                self.assertEqual(res.json()['field'], 'product.reference_cost')
                self.assertIn(expected, res.json()['detail'])
                self.assertNotIn('negativo', res.json()['detail'])
                self.assertEqual(self.counts(), before)

    def test_a_zero_quantity_is_refused(self):
        before = self.counts()
        self.post(self.clerk, self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 0}]), expect=400)
        self.assertEqual(self.counts(), before)


class OnOrderTest(RegistrationFixture):
    def test_supplier_offer_and_bajo_pedido_without_any_own_stock(self):
        body = self.on_order()
        body['offer']['agreed_cost'] = '6000.00'
        data = self.post(self.admin, body, expect=201).json()
        product = Product.objects.get(pk=data['product']['id'])
        self.assertEqual(product.sale_mode, Product.SALE_MODE_STOCK_OR_ORDER)
        self.assertEqual(product.inventory, 0)
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertFalse(BranchStock.objects.filter(product=product, quantity__gt=0).exists())
        offer = SupplierOffer.objects.get(product=product)
        self.assertEqual(offer.supplier.name, 'Mayorista Lima')
        self.assertEqual(offer.agreed_cost, D('6000.00'))
        self.assertEqual(offer.confirmed_quantity, 10)
        self.assertTrue(data['supplier']['created'])
        self.assertEqual(data['offer']['id'], offer.pk)

    def test_a_second_offer_over_the_same_days_on_an_existing_variant_is_refused(self):
        # Review P1-a: the wizard obeys the panel's rule — never two active offers of one supplier and
        # variant over the same days.
        self.post(self.clerk, self.new_device(supplier={'name': 'Distribuidor Uno'}, offer=self.offer()), expect=201)
        before = self.counts()
        again = self.new_device(supplier={'name': 'Distribuidor Uno'}, offer=self.offer(),
                                units=[{'serial_number': 'SNREG003', 'imei': IMEIS[3]},
                                       {'serial_number': 'SNREG004', 'imei': IMEIS[4]}])
        res = self.post(self.clerk, again, expect=400).json()
        self.assertEqual((res['step'], res['code']), ('supplier', 'overlap'), res)
        self.assertEqual(self.counts(), before)

    def test_an_existing_supplier_is_reused(self):
        supplier = Supplier.objects.create(company=self.company, name='Ya existe')
        data = self.post(self.clerk, self.on_order(supplier={'id': supplier.pk}), expect=201).json()
        self.assertFalse(data['supplier']['created'])
        self.assertEqual(SupplierOffer.objects.get(product_id=data['product']['id']).supplier_id, supplier.pk)

    def test_needs_a_supplier_and_an_offer(self):
        before = self.counts()
        self.assertEqual(self.post(self.clerk, self.on_order(offer=None), expect=400).json()['step'], 'supplier')
        self.assertEqual(self.post(self.clerk, self.on_order(supplier=None), expect=400).json()['step'], 'supplier')
        self.assertEqual(self.counts(), before)

    def test_an_offer_that_ends_before_it_starts_is_refused(self):
        today = timezone.localdate()
        body = self.on_order(offer=self.offer(valid_until=(today - timedelta(days=1)).isoformat()))
        before = self.counts()
        res = self.post(self.clerk, body, expect=400)
        self.assertEqual(res.json()['field'], 'offer.valid_until')
        self.assertEqual(self.counts(), before)

    def test_stock_rows_are_refused_for_an_on_order_article(self):
        before = self.counts()
        self.post(self.clerk, self.on_order(stock=[{'branch': self.centro.pk, 'quantity': 2}]), expect=400)
        self.assertEqual(self.counts(), before)


# ---------------------------------------------------------------------------
# Authority per step
# ---------------------------------------------------------------------------

class AuthorityTest(RegistrationFixture):
    def test_without_products_manage_nothing_is_registered(self):
        before = self.counts()
        self.post(self.viewer, self.accessory(stock=[]), expect=403)
        self.assertEqual(self.counts(), before)

    def test_devices_need_inventory_adjust(self):
        before = self.counts()
        res = self.post(self.catalog_only, self.new_device(units=[]), expect=403)
        self.assertEqual(res.json()['step'], 'stock')
        self.assertEqual(self.counts(), before)

    def test_stock_rows_need_inventory_adjust(self):
        before = self.counts()
        res = self.post(self.catalog_only, self.accessory(), expect=403)
        self.assertEqual(res.json()['step'], 'stock')
        self.assertEqual(self.counts(), before)

    def test_every_cost_needs_costs_view(self):
        variants = []
        body = self.accessory(stock=[])
        body['product']['reference_cost'] = '10.00'
        variants.append(('product.reference_cost', body))
        variants.append(('stock.unit_cost', self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 1,
                                                                   'unit_cost': '5.00'}])))
        body = self.new_device()
        body['units'][0]['cost'] = '3000.00'
        variants.append(('units.cost', body))
        body = self.used_device()
        body['unit']['cost'] = '1500.00'
        variants.append(('unit.cost', body))
        body = self.on_order()
        body['offer']['agreed_cost'] = '6000.00'
        variants.append(('offer.agreed_cost', body))
        for field, body in variants:
            with self.subTest(field):
                before = self.counts()
                res = self.post(self.clerk, body, expect=403)
                self.assertEqual(res.json()['field'], field)
                self.assertEqual(self.counts(), before)

    def test_a_branch_outside_the_grants_is_not_found_and_nothing_is_written(self):
        before = self.counts()
        res = self.post(self.centro_only, self.accessory(), expect=404)
        self.assertEqual(res.json()['step'], 'stock')
        self.assertEqual(self.counts(), before)
        self.post(self.centro_only, self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 1}]), expect=201)

    def test_ids_of_another_company_are_never_used(self):
        cases = [
            ('category', self.accessory(product={'name': 'X', 'category': self.foreign_category.pk,
                                                 'price': '1.00', 'is_active': True, 'show_online': False},
                                        stock=[]), 400),
            ('model', self.new_device(model={'id': self.foreign_model.pk}, units=[]), 400),
            ('branch', self.accessory(stock=[{'branch': self.other.default_inventory_branch.pk,
                                              'quantity': 1}]), 404),
            ('supplier', self.on_order(supplier={'id': Supplier.objects.create(
                company=self.other, name='Ajeno').pk}), 400),
        ]
        for name, body, code in cases:
            with self.subTest(name):
                before = self.counts()
                self.post(self.admin, body, expect=code)
                self.assertEqual(self.counts(), before)

    def test_another_company_registers_in_its_own_company(self):
        data = self.post(self.outsider, self.accessory(product={
            'name': 'Suyo', 'price': '5.00', 'is_active': True, 'show_online': False}, stock=[
            {'branch': self.other.default_inventory_branch.pk, 'quantity': 2}]), expect=201).json()
        self.assertEqual(Product.objects.get(pk=data['product']['id']).company_id, self.other.pk)


# ---------------------------------------------------------------------------
# Publication, idempotency, dry run
# ---------------------------------------------------------------------------

class PublicationTest(RegistrationFixture):
    def test_ready_and_asked_for_it_is_published(self):
        data = self.post(self.clerk, self.accessory(stock=[]), expect=201).json()
        self.assertEqual(data['publication'], {'requested': True, 'published': True, 'problems': [], 'model_hidden': False})
        self.assertTrue(Product.objects.get(pk=data['product']['id']).is_published_online)

    def test_not_ready_stays_hidden_and_says_why(self):
        body = self.accessory(stock=[], product={'name': 'Sin categoría', 'price': '5.00', 'is_active': True,
                                                 'show_online': True})
        data = self.post(self.clerk, body, expect=201).json()
        self.assertEqual(data['publication'], {'requested': True, 'published': False, 'problems': ['no_category'], 'model_hidden': False})
        self.assertFalse(Product.objects.get(pk=data['product']['id']).is_published_online)

    def test_the_company_picture_rule_is_the_same_rule(self):
        CompanySettings.objects.filter(company=self.company).update(publication_requires_image=True)
        data = self.post(self.clerk, self.accessory(stock=[]), expect=201).json()
        self.assertEqual(data['publication']['problems'], ['no_image'])
        self.assertFalse(data['publication']['published'])

    def test_inactive_is_never_published(self):
        body = self.accessory(stock=[])
        body['product']['is_active'] = False
        data = self.post(self.clerk, body, expect=201).json()
        self.assertEqual(data['publication']['problems'], ['inactive'])

    def test_a_new_model_whose_first_variant_cannot_be_published_is_created_hidden(self):
        body = self.new_device(model={'name': 'Galaxy S25'}, options=[], units=[])
        body['product'] = {'name': 'Galaxy S25 sin categoría', 'price': '3999.00', 'is_active': True,
                           'show_online': True}
        data = self.post(self.clerk, body, expect=201).json()
        model = ProductModel.objects.get(company=self.company, name='Galaxy S25')
        self.assertFalse(model.is_published_online)
        self.assertEqual(data['publication'], {'requested': True, 'published': False, 'problems': ['no_category'],
                                               'model_hidden': True})
        self.assertIsNone(data['links']['storefront'])

    def test_the_dry_run_already_says_the_new_model_stays_hidden(self):
        body = self.new_device(model={'name': 'Galaxy S25'}, options=[], units=[], dry_run=True)
        body['product']['is_active'] = False
        data = self.post(self.clerk, body, key=False, expect=200).json()
        self.assertTrue(data['publication']['model_hidden'])
        self.assertFalse(ProductModel.objects.filter(company=self.company, name='Galaxy S25').exists())

    def test_a_ready_first_variant_publishes_its_new_model(self):
        body = self.new_device(model={'name': 'Galaxy S25'}, options=[], units=[])
        data = self.post(self.clerk, body, expect=201).json()
        self.assertFalse(data['publication']['model_hidden'])
        self.assertTrue(ProductModel.objects.get(company=self.company, name='Galaxy S25').is_published_online)

    def test_hidden_on_request(self):
        body = self.accessory(stock=[])
        body['product']['show_online'] = False
        data = self.post(self.clerk, body, expect=201).json()
        self.assertEqual(data['publication'], {'requested': False, 'published': False, 'problems': [], 'model_hidden': False})


class IdempotencyTest(RegistrationFixture):
    def test_the_same_key_and_body_answer_the_first_result_and_write_once(self):
        body = self.accessory()
        first = self.post(self.clerk, body, key='same-key', expect=201).json()
        after_first = self.counts()
        again = self.post(self.clerk, body, key='same-key', expect=200).json()
        self.assertTrue(again['replayed'])
        self.assertEqual(again['product']['id'], first['product']['id'])
        self.assertEqual(again['movements'], first['movements'])
        self.assertEqual(self.counts(), after_first)

    def test_the_same_key_with_another_body_is_a_conflict(self):
        self.post(self.clerk, self.accessory(), key='k-conflict', expect=201)
        after = self.counts()
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 9}])
        self.post(self.clerk, body, key='k-conflict', expect=409)
        self.assertEqual(self.counts(), after)

    def test_a_key_belongs_to_its_author(self):
        self.post(self.clerk, self.accessory(), key='k-author', expect=201)
        body = self.accessory(product={'name': 'De otro', 'price': '3.00', 'is_active': True,
                                       'show_online': False}, stock=[])
        data = self.post(self.admin, body, key='k-author', expect=201).json()
        self.assertEqual(Product.objects.get(pk=data['product']['id']).name, 'De otro')

    def test_a_write_without_a_key_is_refused(self):
        before = self.counts()
        self.post(self.clerk, self.accessory(), key=False, expect=400)
        self.assertEqual(self.counts(), before)

    def test_a_refused_attempt_does_not_burn_the_key(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 0}])
        self.post(self.clerk, body, key='k-retry', expect=400)
        self.post(self.clerk, self.accessory(), key='k-retry', expect=201)


class DryRunTest(RegistrationFixture):
    def test_answers_what_would_happen_and_writes_nothing(self):
        before = self.counts()
        audits = AdminAuditLog.objects.count()
        body = self.accessory(dry_run=True, product={'name': 'Sin categoría', 'price': '5.00',
                                                     'is_active': True, 'show_online': True})
        data = self.post(self.clerk, body, key=False, expect=200).json()
        self.assertTrue(data['dry_run'])
        self.assertIsNone(data['product']['id'])
        self.assertEqual(data['publication']['problems'], ['no_category'])
        self.assertEqual(self.counts(), before)
        self.assertEqual(AdminAuditLog.objects.count(), audits)

    def test_finds_the_same_errors_as_the_real_thing(self):
        self.post(self.clerk, self.new_device(), expect=201)
        before = self.counts()
        body = self.new_device(dry_run=True, options=[self.gb128.pk])
        body['product']['sku'] = 'OTRO-CODIGO'
        res = self.post(self.clerk, body, key=False, expect=400)
        self.assertIn('IMEI', res.json()['detail'])
        self.assertEqual(self.counts(), before)

    def test_tells_that_an_existing_variant_will_receive_the_devices(self):
        first = self.post(self.clerk, self.new_device(), expect=201).json()
        body = self.new_device(dry_run=True, units=[{'serial_number': 'SNREG009', 'imei': IMEIS[9]}])
        data = self.post(self.clerk, body, key=False, expect=200).json()
        self.assertFalse(data['product']['created'])
        self.assertEqual(data['product']['existing_id'], first['product']['id'])
        self.assertTrue(data['warnings'])


class NoCostLeaksTest(RegistrationFixture):
    def test_neither_the_answer_nor_the_audit_trail_carry_an_amount_of_cost(self):
        body = self.accessory(stock=[{'branch': self.centro.pk, 'quantity': 2, 'unit_cost': '17.31'}])
        body['product']['reference_cost'] = '18.47'
        data = self.post(self.admin, body, key='leak', expect=201).json()
        assert_no_amount(self, data, '17.31', '18.47')
        trail = list(AdminAuditLog.objects.filter(company=self.company).values_list('metadata', flat=True))
        assert_no_amount(self, trail, '18.47')
        replay = self.post(self.admin, body, key='leak', expect=200).json()
        assert_no_amount(self, replay, '17.31', '18.47')


# ---------------------------------------------------------------------------
# Publicación e-commerce — models in bulk, product list facts
# ---------------------------------------------------------------------------

class ModelBulkVisibilityTest(RegistrationFixture):
    def setUp(self):
        super().setUp()
        self.second = device_catalog.create_model(company=self.company, name='iPhone 16')

    def test_hides_and_publishes_many_models_in_one_batch(self):
        res = _client(self.clerk).post(MODELS_BULK, {'action': 'hide', 'ids': [self.model.pk, self.second.pk]},
                                       format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['changed'], 2)
        self.assertFalse(ProductModel.objects.filter(pk__in=[self.model.pk, self.second.pk],
                                                     is_published_online=True).exists())
        again = _client(self.clerk).post(MODELS_BULK, {'action': 'hide', 'ids': [self.model.pk]}, format='json')
        self.assertEqual((again.json()['changed'], again.json()['unchanged']), (0, 1))
        self.assertTrue(AdminAuditLog.objects.filter(company=self.company,
                                                     action='product_models_visibility_changed').exists())
        _client(self.clerk).post(MODELS_BULK, {'action': 'publish', 'ids': [self.second.pk]}, format='json')
        self.assertTrue(ProductModel.objects.get(pk=self.second.pk).is_published_online)

    def test_a_model_of_another_company_refuses_the_whole_batch(self):
        res = _client(self.clerk).post(MODELS_BULK, {'action': 'hide', 'ids': [self.model.pk,
                                                                              self.foreign_model.pk]},
                                       format='json')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(ProductModel.objects.get(pk=self.model.pk).is_published_online)
        self.assertTrue(ProductModel.objects.get(pk=self.foreign_model.pk).is_published_online)

    def test_needs_products_manage_and_a_valid_body(self):
        res = _client(self.viewer).post(MODELS_BULK, {'action': 'hide', 'ids': [self.model.pk]}, format='json')
        self.assertEqual(res.status_code, 403)
        for body in ({'action': 'nope', 'ids': [self.model.pk]}, {'action': 'hide', 'ids': []},
                     {'action': 'hide', 'ids': ['1']}, {'action': 'hide'}):
            with self.subTest(body):
                self.assertEqual(_client(self.clerk).post(MODELS_BULK, body, format='json').status_code, 400)
        self.assertTrue(ProductModel.objects.get(pk=self.model.pk).is_published_online)


class ProductListFactsTest(RegistrationFixture):
    def test_the_list_says_line_model_and_sale_mode_and_filters_by_them(self):
        new = self.post(self.clerk, self.new_device(units=[]), expect=201).json()['product']['id']
        acc = self.post(self.clerk, self.accessory(stock=[]), expect=201).json()['product']['id']
        order = self.post(self.clerk, self.on_order(), expect=201).json()['product']['id']
        client = _client(self.clerk)
        rows = {r['id']: r for r in client.get('/api/admin/products/?page_size=50').json()['results']}
        self.assertEqual(rows[new]['condition_line'], 'new')
        self.assertEqual(rows[new]['product_model_id'], self.model.pk)
        self.assertIsNone(rows[acc]['product_model_id'])
        self.assertEqual(rows[order]['sale_mode'], 'stock_or_order')

        def ids(query):
            res = client.get(f'/api/admin/products/?page_size=50&{query}')
            self.assertEqual(res.status_code, 200, res.content)
            return {r['id'] for r in res.json()['results']}

        self.assertEqual(ids('sale_mode=stock_or_order'), {order})
        self.assertIn(new, ids('condition_line=new&is_serialized=true'))
        self.assertNotIn(acc, ids('is_serialized=true'))
        self.assertEqual(ids('condition_line=used'), set())

    def test_bulk_visibility_all_that_match_understands_the_new_filters(self):
        order = self.post(self.clerk, self.on_order(), expect=201).json()['product']['id']
        res = _client(self.clerk).post('/api/admin/products/bulk-visibility/', {
            'action': 'publish', 'filter': {'sale_mode': 'stock_or_order'}, 'expected_count': 1}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Product.objects.get(pk=order).is_published_online)
        bad = _client(self.clerk).post('/api/admin/products/bulk-visibility/', {
            'action': 'publish', 'filter': {'sale_mode': 'nope'}, 'expected_count': 1}, format='json')
        self.assertEqual(bad.status_code, 400)


# ---------------------------------------------------------------------------
# Excel — the one field of the screen the files did not carry: «Bajo pedido»
# ---------------------------------------------------------------------------

from store import supply_services as supply  # noqa: E402
from store.test_supplier_offers import OFFER_HEADERS, OfferExcelTest, SupplyShop  # noqa: E402


class OfferOnOrderColumnTest(SupplyShop, TestCase):
    """«Bajo pedido (Sí/No)»: optional, template only; empty leaves the product's sale mode alone."""

    COLUMNS = OFFER_HEADERS + ['Activa (Sí/No)', 'Bajo pedido (Sí/No)']
    setUp = OfferExcelTest.setUp
    file = OfferExcelTest.file
    row = OfferExcelTest.row
    preview = OfferExcelTest.preview
    apply = OfferExcelTest.apply

    def test_the_template_offers_it_last(self):
        import io

        import openpyxl

        res = self.client_.get('/api/admin/supplier-offers/import/template/')
        book = openpyxl.load_workbook(io.BytesIO(res.content))
        # SUPPLIER-DEVICE-FLOW-02 adds the optional «Código del proveedor» after it.
        self.assertEqual([c.value for c in book.active[1]], self.COLUMNS + ['Código del proveedor'])

    def test_si_turns_bajo_pedido_on_and_no_turns_it_off(self):
        Product.objects.filter(pk=self.case.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        body = self.preview([self.row() + ['', 'Sí']], headers=self.COLUMNS).json()
        # SUPPLIER-DEVICE-FLOW-02 (owner): the row updates the shop's current offer in place, and its new
        # commercial price is applied only when accepted.
        self.assertEqual(body['rows'][0]['action'], 'update', body['rows'])
        self.assertEqual(self.apply(body['id'], accept_price_changes=True).status_code, 200)
        self.case.refresh_from_db()
        self.assertEqual(self.case.sale_mode, Product.SALE_MODE_STOCK_OR_ORDER)

        body = self.preview([self.row() + ['', 'No']], headers=self.COLUMNS).json()
        self.assertEqual(body['rows'][0]['action'], 'update', body['rows'])
        self.apply(body['id'])
        self.case.refresh_from_db()
        self.assertEqual(self.case.sale_mode, Product.SALE_MODE_STOCK)

    def test_empty_or_absent_leaves_the_sale_mode_alone(self):
        Product.objects.filter(pk=self.case.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
        body = self.preview([self.row()]).json()
        self.apply(body['id'])
        body = self.preview([self.row(**{'Precio comercial': '111'}) + ['', '']], headers=self.COLUMNS).json()
        self.apply(body['id'])
        self.case.refresh_from_db()
        self.assertEqual(self.case.sale_mode, Product.SALE_MODE_STOCK_OR_ORDER)

    def test_two_rows_of_one_product_disagreeing_on_bajo_pedido_warn_on_the_later_row(self):
        # SUPPLIER-DEVICE-FLOW-02 (owner): one row per supplier and variant — two rows of one product are
        # now two suppliers' offers (before: two windows of one supplier).
        supply.create_supplier(company=self.company, name='Importadora Norte', actor=self.staff)
        later = {'Proveedor': 'Importadora Norte'}
        body = self.preview([self.row() + ['', 'Sí'], self.row(**later) + ['', 'No']],
                            headers=self.COLUMNS).json()
        self.assertEqual([r['action'] for r in body['rows']], ['update', 'create'], body['rows'])
        self.assertEqual(body['rows'][0]['warnings'], [])
        self.assertTrue(any('Bajo pedido' in w and 'fila 2' in w for w in body['rows'][1]['warnings']),
                        body['rows'][1]['warnings'])

    def test_rows_that_agree_on_bajo_pedido_do_not_warn(self):
        # SUPPLIER-DEVICE-FLOW-02: three suppliers of one product (one row per supplier and variant).
        for name in ('Importadora Norte', 'Importadora Sur'):
            supply.create_supplier(company=self.company, name=name, actor=self.staff)
        body = self.preview([self.row() + ['', 'Sí'], self.row(**{'Proveedor': 'Importadora Norte'}) + ['', 'Sí'],
                             self.row(**{'Proveedor': 'Importadora Sur'}) + ['', '']],
                            headers=self.COLUMNS).json()
        self.assertEqual([r['action'] for r in body['rows']], ['update', 'create', 'create'], body['rows'])
        self.assertEqual([r['warnings'] for r in body['rows']], [[], [], []], body['rows'])

    def test_an_unreadable_value_is_an_error(self):
        body = self.preview([self.row() + ['', 'quizás']], headers=self.COLUMNS).json()
        self.assertEqual(body['rows'][0]['action'], 'error')
