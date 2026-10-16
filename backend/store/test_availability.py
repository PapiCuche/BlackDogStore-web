"""
COMMERCE-ADMIN-UX-01 · «Ventas › Consultar disponibilidad».

Lo que estas pruebas fijan:

  * la abre quien puede vender en mostrador (`sales.pos.use`), nadie más, y
    siempre dentro de su empresa y de sus sucursales;
  * el stock propio sale de `BranchStock` y de los estados de `StockUnit`
    (disponible, apartado web / apartado por personal, vendido, de baja);
  * las ofertas de proveedores van APARTE y nunca se suman al stock propio:
    sólo las vigentes, con precio al cliente, saldo, vigencia y plazo; el
    nombre del proveedor sólo para quien puede ver proveedores (`products.view`);
  * nunca un costo —tampoco para `costs.view`—, nunca un IMEI o una serie
    completos, nunca la llave ni el origen de un apartado web;
  * el número de consultas no crece con el número de productos.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from store import inventory_services as inventory
from store import stock_unit_services as units
from store import supply_services as supply
from store import unit_hold_services as holds
from store.models import Branch, Category, Product, ProductBarcode, StockMovement, StockUnit, SupplierOffer
from store.test_device_commerce import DeviceShop, _client, assert_no_amount
from store.tests import _p2d_member, _p3_company, _prod

URL = '/api/admin/availability/'
D = Decimal


def today():
    return timezone.localdate()


class AvailabilityShop(DeviceShop):
    """DC2's shop plus a second branch, an accessory sold by quantity and a supplier."""

    def build(self, slug='avail-tienda'):
        super().build(slug)
        self.branch2 = Branch.objects.create(company=self.company, name='Sucursal Norte', is_active=True)
        self.phones = Category.objects.create(company=self.company, name='Celulares', slug='celulares')
        self.accessories = Category.objects.create(company=self.company, name='Accesorios', slug='accesorios')
        self.model.category = self.phones
        self.model.save()
        Product.objects.filter(product_model=self.model).update(category=self.phones)
        # An accessory counted by quantity, sold also «bajo pedido».
        self.charger = _prod(self.company, 'Cargador 20W', f'{slug}-cargador', category=self.accessories,
                             price='120.00', inventory=0)
        Product.objects.filter(pk=self.charger.pk).update(
            sale_mode=Product.SALE_MODE_STOCK_OR_ORDER, reference_cost=D('41.00'))
        ProductBarcode.objects.create(company=self.company, product=self.charger, code='7750001112223',
                                      is_primary=True)
        self.entry(self.branch, self.charger, 4)
        self.entry(self.branch2, self.charger, 7)

        self.costly, _ = _p2d_member(self.company, f'{slug}_admin', [
            'company.view', 'sales.pos.use', 'products.view', 'costs.view', 'inventory.view'])
        # A seller of the main branch only, who may see suppliers (the Ventas preset has products.view).
        self.seller, _ = _p2d_member(self.company, f'{slug}_vende', ['sales.pos.use', 'products.view'],
                                     branches=[self.branch])
        # A seller whose role does NOT include seeing suppliers.
        self.bare, _ = _p2d_member(self.company, f'{slug}_caja', ['sales.pos.use'], branches=[self.branch])
        self.stockroom, _ = _p2d_member(self.company, f'{slug}_almacen', ['inventory.view', 'products.view'])

        self.supplier = supply.create_supplier(company=self.company, name='Mayorista Lima', actor=self.staff)
        self.other_supplier = supply.create_supplier(company=self.company, name='Importadora Sur',
                                                     actor=self.staff)

    def entry(self, branch, product, n):
        inventory.create_stock_movement(branch=branch, product_id=product.pk,
                                        movement_type=StockMovement.PURCHASE_ENTRY, quantity=n,
                                        reason='Compra', unit_cost=D('40.00'))

    def offer(self, product, supplier=None, **extra):
        values = dict(agreed_cost='68.00', commercial_price='110.00', confirmed_quantity=5,
                      valid_from=today() - timedelta(days=1), valid_until=today() + timedelta(days=6),
                      lead_time_days=4)
        values.update(extra)
        return supply.create_offer(company=self.company, supplier_id=(supplier or self.supplier).pk,
                                   product_id=product.pk, actor=self.staff, may_view_costs=True, **values)

    def get(self, user, **params):
        return _client(user).get(URL, params)

    def row(self, body, product):
        return next(r for r in body['results'] if r['id'] == product.pk)


class AvailabilityAuthorityTest(AvailabilityShop, TestCase):
    def setUp(self):
        self.build()

    def test_a_seller_opens_it(self):
        self.assertEqual(self.get(self.seller).status_code, 200)
        self.assertEqual(self.get(self.bare).status_code, 200)

    def test_without_the_till_it_is_refused(self):
        self.assertEqual(self.get(self.stockroom).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertIn(_client().get(URL).status_code, (401, 403))

    def test_another_company_is_never_listed_nor_reachable(self):
        other = _p3_company('avail-otra', 'Otra')
        foreign = _prod(other, 'Cargador 20W', 'avail-otra-cargador', price='99.00', inventory=3)
        body = self.get(self.costly, q='Cargador').json()
        self.assertNotIn(foreign.pk, {r['id'] for r in body['results']})
        self.assertIn(self.get(self.seller, company=other.pk).status_code, (403, 404))
        units_answer = self.get(self.seller, product=foreign.pk, units=1)
        self.assertEqual(units_answer.status_code, 404)


class AvailabilityOwnStockTest(AvailabilityShop, TestCase):
    def setUp(self):
        self.build()
        self.receive(self.new128, 4)
        units.receive_units(branch=self.branch2, product=self.new128, actor=self.staff, reason='Compra',
                            units=[{'serial_number': 'SERNORTE1', 'imei': next(self._imei)}])
        live = list(StockUnit.objects.filter(product=self.new128, branch=self.branch).order_by('pk'))
        units.reserve(live[0], reason='Cliente vuelve mañana', actor=self.staff)
        holds.hold(product=self.new128, branch=self.branch, session_key='carrito-web')
        units.write_off(live[2], reason='Pantalla rota', actor=self.staff)
        # A sale: the unit leaves the shelf (reserve moves the BranchStock) and is sold.
        units.reserve(live[3], reason='Venta', actor=self.staff)
        StockUnit.objects.filter(pk=live[3].pk).update(status=StockUnit.Status.SOLD, sold_at=timezone.now())

    def test_serialized_counts_by_status_per_branch(self):
        row = self.row(self.get(self.costly, q='Teléfono').json(), self.new128)
        by_branch = {b['branch_id']: b for b in row['stock']['by_branch']}
        main = by_branch[self.branch.pk]
        self.assertEqual(main['available'], 0)
        self.assertEqual(main['reserved_staff'], 1)
        self.assertEqual(main['reserved_web'], 1)
        self.assertEqual(main['sold'], 1)
        self.assertEqual(main['written_off'], 1)
        self.assertEqual(by_branch[self.branch2.pk]['available'], 1)
        self.assertEqual(row['stock']['total']['available'], 1)
        self.assertEqual(row['stock']['total']['reserved_web'], 1)
        self.assertTrue(row['is_serialized'])
        self.assertEqual(row['line'], 'new')

    def test_quantity_product_reads_the_shelf(self):
        row = self.row(self.get(self.costly, q='Cargador').json(), self.charger)
        by_branch = {b['branch_id']: b['available'] for b in row['stock']['by_branch']}
        self.assertEqual(by_branch, {self.branch.pk: 4, self.branch2.pk: 7})
        self.assertEqual(row['stock']['total']['available'], 11)
        self.assertFalse(row['is_serialized'])

    def test_a_seller_of_one_branch_sees_only_that_branch(self):
        body = self.get(self.seller, q='Cargador').json()
        row = self.row(body, self.charger)
        self.assertEqual({b['branch_id'] for b in row['stock']['by_branch']}, {self.branch.pk})
        self.assertEqual(row['stock']['total']['available'], 4)
        self.assertEqual({b['id'] for b in body['scope']['branches']}, {self.branch.pk})
        self.assertEqual(self.get(self.seller, branch=self.branch2.pk).status_code, 404)

    def test_branch_filter_narrows_the_columns(self):
        row = self.row(self.get(self.costly, q='Cargador', branch=self.branch2.pk).json(), self.charger)
        self.assertEqual([b['branch_id'] for b in row['stock']['by_branch']], [self.branch2.pk])
        self.assertEqual(row['stock']['total']['available'], 7)

    def test_inactive_products_are_not_offered(self):
        Product.objects.filter(pk=self.charger.pk).update(is_active=False)
        body = self.get(self.costly, q='Cargador').json()
        self.assertNotIn(self.charger.pk, {r['id'] for r in body['results']})


class AvailabilitySupplierTest(AvailabilityShop, TestCase):
    def setUp(self):
        self.build()
        self.best = self.offer(self.charger, commercial_price='105.00', confirmed_quantity=6, lead_time_days=3)
        self.best.committed_quantity = 2
        self.best.save(update_fields=['committed_quantity'])
        self.second = self.offer(self.charger, supplier=self.other_supplier, commercial_price='115.00',
                                 confirmed_quantity=10, lead_time_days=8)
        # None of these may sell today.
        self.offer(self.charger, valid_from=today() - timedelta(days=20), valid_until=today() - timedelta(days=1))
        self.offer(self.charger, valid_from=today() + timedelta(days=2), valid_until=today() + timedelta(days=9))
        self.offer(self.charger, confirmed_quantity=None, valid_from=today(), valid_until=today() + timedelta(days=3))
        exhausted = self.offer(self.charger, supplier=self.other_supplier, confirmed_quantity=1,
                               valid_from=today(), valid_until=today() + timedelta(days=2))
        SupplierOffer.objects.filter(pk=exhausted.pk).update(committed_quantity=1)
        inactive = self.offer(self.charger, supplier=self.other_supplier, valid_from=today() - timedelta(days=2),
                              valid_until=today() + timedelta(days=8))
        SupplierOffer.objects.filter(pk=inactive.pk).update(is_active=False)

    def test_only_valid_offers_best_first_and_apart_from_own_stock(self):
        row = self.row(self.get(self.seller, q='Cargador').json(), self.charger)
        offers = row['supplier_offers']
        self.assertEqual([o['commercial_price'] for o in offers], ['105.00', '115.00'])
        self.assertEqual(offers[0]['remaining'], 4)
        self.assertEqual(offers[0]['lead_time_days'], 3)
        self.assertEqual(offers[0]['valid_until'], self.best.valid_until.isoformat())
        self.assertEqual(offers[0]['supplier_name'], 'Mayorista Lima')
        # Own stock is the shelf, untouched by what suppliers confirmed.
        self.assertEqual(row['stock']['total']['available'], 4)
        self.assertTrue(row['sells_on_order'])

    def test_supplier_name_needs_products_view(self):
        row = self.row(self.get(self.bare, q='Cargador').json(), self.charger)
        self.assertEqual(len(row['supplier_offers']), 2)
        for offer in row['supplier_offers']:
            self.assertNotIn('supplier_name', offer)
            self.assertNotIn('supplier_id', offer)
        self.assertFalse(self.get(self.bare).json()['can_see_suppliers'])
        self.assertTrue(self.get(self.seller).json()['can_see_suppliers'])

    def test_an_inactive_supplier_offers_nothing(self):
        self.supplier.is_active = False
        self.supplier.save(update_fields=['is_active'])
        row = self.row(self.get(self.seller, q='Cargador').json(), self.charger)
        self.assertEqual([o['commercial_price'] for o in row['supplier_offers']], ['115.00'])

    def test_a_product_sold_only_from_stock_still_shows_its_offers_but_says_so(self):
        Product.objects.filter(pk=self.charger.pk).update(sale_mode=Product.SALE_MODE_STOCK)
        row = self.row(self.get(self.seller, q='Cargador').json(), self.charger)
        self.assertEqual(len(row['supplier_offers']), 2)
        self.assertFalse(row['sells_on_order'])


class AvailabilityNoCostTest(AvailabilityShop, TestCase):
    """Not even `costs.view` gets a cost here: costs live on «Costos y precios»."""

    def setUp(self):
        self.build()
        self.offer(self.charger, agreed_cost='68.00')
        self.receive(self.used128, 1, cost='2222.00', battery_health=91, cosmetic_grade=self.grade.pk,
                     sale_price='2600.00')

    def assert_cost_free(self, body):
        text = repr(body)
        for key in ('cost', 'agreed_cost', 'reference_cost', 'average_cost', 'unit_cost', 'margin'):
            self.assertNotIn(f"'{key}'", text)
        assert_no_amount(self, body, '68.00', '41.00', '40.00', '2222.00')

    def test_list_carries_no_cost(self):
        for user in (self.costly, self.seller):
            with self.subTest(user=user.username):
                self.assert_cost_free(self.get(user).json())

    def test_units_carry_no_cost_nor_full_identifiers(self):
        unit = StockUnit.objects.get(product=self.used128)
        body = self.get(self.costly, product=self.used128.pk, units=1).json()
        self.assert_cost_free(body)
        text = repr(body)
        self.assertNotIn(unit.imei, text)
        self.assertNotIn(unit.serial_number, text)


class AvailabilityUnitsTest(AvailabilityShop, TestCase):
    def setUp(self):
        self.build()
        self.receive(self.used128, 2, battery_health=88, cosmetic_grade=self.grade.pk, sale_price='2500.00')
        units.receive_units(branch=self.branch2, product=self.used128, actor=self.staff, reason='Compra',
                            units=[{'serial_number': 'SERNORTE9', 'imei': next(self._imei), 'sale_price': '2400.00'}])
        self.held = holds.hold(product=self.used128, branch=self.branch, session_key='carrito-web',
                               unit_public_id=StockUnit.objects.filter(
                                   product=self.used128, branch=self.branch).order_by('pk').first().public_id,
                               origin='f' * 64)

    def test_units_of_a_product(self):
        body = self.get(self.costly, product=self.used128.pk, units=1).json()
        self.assertEqual(body['product']['id'], self.used128.pk)
        self.assertEqual(body['product']['line'], 'used')
        rows = body['results']
        self.assertEqual(len(rows), 3)
        held = next(r for r in rows if r['id'] == self.held.pk)
        self.assertEqual(held['status'], 'reserved')
        self.assertEqual(held['hold_kind'], 'web')
        self.assertIsNotNone(held['hold_until'])
        self.assertEqual(held['imei_last4'], self.held.imei[-4:])
        self.assertEqual(held['battery_health'], 88)
        self.assertEqual(held['cosmetic_grade_label'], self.grade.label)
        self.assertEqual(held['sale_price'], '2500.00')
        for key in ('hold_key', 'hold_origin', 'hold_price', 'imei', 'serial_number', 'notes', 'cost'):
            self.assertNotIn(key, held)
        self.assertNotIn('f' * 64, repr(body))

    def test_units_follow_the_branch_scope(self):
        rows = self.get(self.seller, product=self.used128.pk, units=1).json()['results']
        self.assertEqual({r['branch_id'] for r in rows}, {self.branch.pk})

    def test_sold_and_written_off_only_on_request(self):
        unit = StockUnit.objects.filter(product=self.used128, branch=self.branch2).first()
        units.write_off(unit, reason='Daño', actor=self.staff)
        default = self.get(self.costly, product=self.used128.pk, units=1).json()['results']
        self.assertNotIn(unit.pk, {r['id'] for r in default})
        everything = self.get(self.costly, product=self.used128.pk, units=1, unit_status='all').json()['results']
        self.assertIn(unit.pk, {r['id'] for r in everything})
        only = self.get(self.costly, product=self.used128.pk, units=1, unit_status='written_off').json()['results']
        self.assertEqual([r['id'] for r in only], [unit.pk])

    def test_used_price_range_on_the_list(self):
        row = self.row(self.get(self.costly, line='used').json(), self.used128)
        self.assertEqual(row['line'], 'used')
        self.assertEqual(row['unit_price_range'], {'min': '2400.00', 'max': '2500.00'})

    def test_bad_unit_status_is_refused(self):
        self.assertEqual(self.get(self.costly, product=self.used128.pk, units=1, unit_status='x').status_code, 400)


class AvailabilitySearchTest(AvailabilityShop, TestCase):
    def setUp(self):
        self.build()

    def ids(self, user=None, **params):
        response = self.get(user or self.costly, **params)
        self.assertEqual(response.status_code, 200, response.content)
        return {r['id'] for r in response.json()['results']}

    def test_by_name_tokens_model_and_option(self):
        self.assertEqual(self.ids(q='teléfono 256'), {self.new256.pk})
        self.assertEqual(self.ids(q='Teléfono X azul'), {self.used128.pk})
        self.assertEqual(self.ids(q='cargador'), {self.charger.pk})

    def test_by_model_name_even_when_the_product_name_differs(self):
        Product.objects.filter(pk=self.new256.pk).update(name='TX grande')
        self.assertIn(self.new256.pk, self.ids(q='Teléfono X'))

    def test_by_barcode(self):
        self.assertEqual(self.ids(q='7750001112223'), {self.charger.pk})
        self.assertEqual(self.ids(q='77500011'), {self.charger.pk})

    def test_filters(self):
        self.assertEqual(self.ids(line='used'), {self.used128.pk})
        self.assertEqual(self.ids(category=self.accessories.pk), {self.charger.pk})
        self.assertEqual(self.ids(model=self.model.pk), {self.new128.pk, self.new256.pk, self.used128.pk})
        self.assertEqual(self.ids(option=self.gb128.pk), {self.new128.pk, self.used128.pk})
        both = _client(self.costly).get(URL + f'?option={self.gb128.pk}&option={self.black.pk}')
        self.assertEqual({r['id'] for r in both.json()['results']}, {self.new128.pk})

    def test_bad_parameters_are_refused(self):
        for params in ({'line': 'x'}, {'category': 'x'}, {'option': 'x'}, {'model': 'x'}, {'product': 'x'}):
            with self.subTest(params=params):
                self.assertEqual(self.get(self.costly, **params).status_code, 400)

    def test_a_foreign_filter_finds_nothing(self):
        other = _p3_company('avail-otra2', 'Otra')
        foreign = Category.objects.create(company=other, name='Celulares', slug='celulares')
        self.assertEqual(self.ids(category=foreign.pk), set())

    def test_facets_on_request(self):
        body = self.get(self.costly, facets=1).json()
        self.assertEqual({c['id'] for c in body['facets']['categories']}, {
            c.pk for c in Category.objects.filter(company=self.company)})
        self.assertEqual([m['id'] for m in body['facets']['models']], [self.model.pk])
        attributes = {a['name']: sorted(o['label'] for o in a['options']) for a in body['facets']['attributes']}
        self.assertEqual(attributes['Capacidad'], ['128 GB', '256 GB'])
        self.assertEqual(attributes['Color'], ['Azul', 'Negro'])
        self.assertNotIn('facets', self.get(self.costly).json())

    def test_pagination(self):
        body = self.get(self.costly, page_size=2).json()
        self.assertEqual(body['page_size'], 2)
        self.assertEqual(len(body['results']), 2)
        self.assertGreaterEqual(body['count'], 4)


class AvailabilityQueryCountTest(AvailabilityShop, TestCase):
    """No N+1: the page costs the same number of queries for 2 products or 8."""

    def setUp(self):
        self.build()

    def count_queries(self, n):
        Product.objects.filter(company=self.company).exclude(slug__startswith='qc-').update(is_active=False)
        existing = Product.objects.filter(company=self.company, slug__startswith='qc-').count()
        for index in range(existing, n):
            product = _prod(self.company, f'QC {index}', f'qc-{index}', price='10.00', inventory=0)
            Product.objects.filter(pk=product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
            ProductBarcode.objects.create(company=self.company, product=product, code=f'990000{index:04d}')
            self.entry(self.branch, product, 2)
            self.offer(product)
        client = _client(self.costly)
        client.get(URL)  # warm the caches a first request fills
        with CaptureQueriesContext(connection) as captured:
            response = client.get(URL, {'page_size': 50})
        self.assertEqual(len(response.json()['results']), n)
        return len(captured)

    def test_constant_query_count(self):
        few = self.count_queries(2)
        many = self.count_queries(8)
        self.assertEqual(few, many)
        self.assertLessEqual(many, 20)


class UnitListVariantFiltersTest(AvailabilityShop, TestCase):
    """«Inventario › Equipos»: by model and by option (capacity, colour), on top of status and line."""

    URL = '/api/admin/inventory/units/'

    def setUp(self):
        self.build()
        self.receive(self.new128, 1)
        self.receive(self.new256, 1)
        self.receive(self.used128, 1)

    def products(self, **params):
        response = _client(self.staff).get(self.URL, {'branch': 'all', **params})
        self.assertEqual(response.status_code, 200, response.content)
        return {row['product_id'] for row in response.json()['results']}

    def test_by_model_and_options(self):
        self.assertEqual(self.products(model=self.model.pk), {self.new128.pk, self.new256.pk, self.used128.pk})
        self.assertEqual(self.products(option=self.gb128.pk), {self.new128.pk, self.used128.pk})
        self.assertEqual(self.products(option=self.blue.pk), {self.used128.pk})
        self.assertEqual(self.products(option=self.gb128.pk, line='new'), {self.new128.pk})

    def test_bad_values_are_refused(self):
        for params in ({'model': 'x'}, {'option': 'x'}):
            with self.subTest(params=params):
                self.assertEqual(_client(self.staff).get(self.URL, params).status_code, 400)

    def test_another_companys_model_finds_nothing(self):
        from store import device_catalog
        other = _p3_company('avail-otra3', 'Otra')
        foreign = device_catalog.create_model(company=other, name='Teléfono X')
        self.assertEqual(self.products(model=foreign.pk), set())
