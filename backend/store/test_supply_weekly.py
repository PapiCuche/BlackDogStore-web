"""
SUPPLY-WEEKLY-01 — the supplier lists of a week, side by side with what the shop has.

What these tests pin:

  * `GET /api/admin/supplier-offers/weekly/` answers one row per supplier × variant, with the model and the
    option roles of the variant, the offer's price, quantities, lead time and validity, and the shop's OWN stock
    in a column of its own (never added to what the supplier declared);
  * each row says what changed against the supplier's previous list: nuevo, precio cambió, cantidad cambió,
    retirado, no mencionado or sin cambio — read from the applied lists (their message date), never guessed;
  * the agreed cost reaches only `costs.view` (no key at all otherwise);
  * a fixed number of queries whatever the number of rows; nothing of another company;
  * a PARTIAL list's preview names the supplier's running offers it does not mention — information only:
    applying it switches nothing off;
  * an Excel row whose model and options are a valid combination of the palette without a variant says so
    (`combinacion_valida_sin_variante`) with what «Crear variante» needs, instead of a generic error;
  * no list ever creates a unit, a stock row or a stock movement.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from store import device_catalog, model_palette, supplier_offer_import, supply_services as supply
from store.configuration_resolver import STATUS_VALID
from store.models import BranchStock, Product, StockMovement, StockUnit, SupplierOffer
from store.test_supplier_offers import today
from store.test_supplier_weekly_import import WeeklyFile
from store.tests import _p2d_member, _p3_company, _prod

D = Decimal
WEEKLY = '/api/admin/supplier-offers/weekly/'


def _week_of(day):
    year, week, _ = day.isocalendar()
    return f'{year}-W{week:02d}'


class WeeklyShop(WeeklyFile):
    """`WeeklyFile`'s shop plus five more variants, each with an offer of the supplier running today."""

    def build_weekly(self, slug='supweek-tienda'):
        self.build_week(slug)
        self.items = {}
        for key in ('price', 'quantity', 'same', 'silent', 'retired'):
            product = _prod(self.company, f'Accesorio {key}', f'{slug}-{key}', price='150.00', inventory=0)
            Product.objects.filter(pk=product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
            self.items[key] = product
            self.make_offer(product=product, commercial_price='100.00', agreed_cost='60.00', confirmed_quantity=5,
                            valid_from=today() - timedelta(days=8), valid_until=today() + timedelta(days=13))

    def list_row(self, key, **change):
        offer = SupplierOffer.objects.get(product=self.items[key], supplier=self.supplier)
        values = {'Código/SKU del producto': self.items[key].slug, 'Precio comercial': '100',
                  'Cantidad confirmada': 5, 'Vigente desde': offer.valid_from.isoformat(),
                  'Vigente hasta': offer.valid_until.isoformat(), 'Plazo (días)': 4, 'Costo pactado': '60'}
        values.update(change)
        return self.row(**values)

    def load(self, rows, message_date, **apply):
        body = self.preview(rows, message_date=message_date).json()
        self.assertEqual(body['rows_error'], 0, body['rows'])
        res = self.apply(body['id'], **apply)
        self.assertEqual(res.status_code, 200, res.content)
        return body

    def weekly(self, client=None, **params):
        return (client or self.client_).get(WEEKLY, params)

    def by_product(self, body):
        return {row['product_id']: row for row in body['results']}


class WeeklyPanelTest(WeeklyShop, TestCase):
    def setUp(self):
        self.build_weekly()
        self.fresh = _prod(self.company, 'Accesorio nuevo', 'supweek-tienda-nuevo', price='80.00', inventory=0)
        last_week = today() - timedelta(days=7)
        self.load([self.list_row(key) for key in ('price', 'quantity', 'same', 'silent', 'retired')], last_week)
        supply.deactivate_offer(SupplierOffer.objects.get(product=self.items['retired']), actor=self.staff,
                                reason='Ya no lo trae')
        self.load([
            self.list_row('price', **{'Precio comercial': '105'}),
            self.list_row('quantity', **{'Cantidad confirmada': 8}),
            self.list_row('same'),
            self.row(**{'Código/SKU del producto': self.fresh.slug, 'Precio comercial': '90', 'Costo pactado': '50',
                        'Cantidad confirmada': 3}),
        ], today(), accept_price_changes=True)

    def test_each_row_says_what_changed_against_the_previous_list(self):
        res = self.weekly()
        self.assertEqual(res.status_code, 200, res.content)
        rows = self.by_product(res.json())
        deltas = {key: rows[self.items[key].pk]['delta'] for key in self.items}
        self.assertEqual(deltas, {'price': 'precio_cambio', 'quantity': 'cantidad_cambio', 'same': 'sin_cambio',
                                  'silent': 'no_mencionado', 'retired': 'retirado'})
        self.assertEqual(rows[self.fresh.pk]['delta'], 'nuevo')
        price = rows[self.items['price'].pk]
        self.assertEqual((price['previous']['commercial_price'], price['listed_price'], price['commercial_price']),
                         ('100.00', '105.00', '105.00'), price)
        quantity = rows[self.items['quantity'].pk]
        self.assertEqual((quantity['previous']['declared_quantity'], quantity['declared_quantity']), (5, 8))
        self.assertEqual(quantity['changes'], ['quantity'])
        self.assertIsNone(rows[self.fresh.pk]['previous'])
        counts = res.json()['counts']
        # The supplier's other offer (`self.offer`) was created this week by hand, never listed: new too.
        self.assertEqual(rows[self.case.pk]['delta'], 'nuevo')
        self.assertEqual((counts['nuevo'], counts['no_mencionado'], counts['retirado']), (2, 1, 1), counts)

    def test_a_row_carries_the_offer_and_the_variant(self):
        body = self.weekly().json()
        row = self.by_product(body)[self.items['quantity'].pk]
        for key in ('supplier_name', 'product_name', 'offer_id', 'model', 'capacity', 'color', 'sim', 'region',
                    'commercial_price', 'variant_price', 'confirmed_quantity', 'remaining', 'lead_time_days',
                    'valid_from', 'valid_until', 'state', 'own_stock', 'message_date'):
            self.assertIn(key, row)
        self.assertEqual((row['variant_price'], row['remaining'], row['state']), ('150.00', 8, 'active'), row)
        self.assertEqual(body['week'], _week_of(today()))

    def test_own_stock_is_its_own_column_and_never_the_supplier_quantity(self):
        self.stock(self.items['same'], 3)
        row = self.by_product(self.weekly().json())[self.items['same'].pk]
        self.assertEqual((row['own_stock'], row['remaining'], row['declared_quantity']), (3, 5, 5), row)

    def test_the_lists_never_create_units_stock_rows_or_movements(self):
        products = [*self.items.values(), self.fresh]
        self.assertFalse(StockUnit.objects.filter(product__in=products).exists())
        self.assertFalse(StockMovement.objects.filter(product__in=products).exists())
        self.assertFalse(BranchStock.objects.filter(product__in=products, quantity__gt=0).exists())

    def test_the_cost_reaches_only_costs_view(self):
        with_cost = self.by_product(self.weekly().json())[self.items['same'].pk]
        self.assertEqual(with_cost['agreed_cost'], '60.00')
        viewer = self.as_(['company.view', 'products.view'], 'supweek_sin_costo')
        body = self.weekly(client=viewer).json()
        self.assertFalse(body['with_cost'])
        for row in body['results']:
            self.assertNotIn('agreed_cost', row)
            self.assertNotIn('agreed_cost', row.get('previous') or {})

    def test_without_products_view_it_is_refused(self):
        nobody = self.as_(['company.view'], 'supweek_sin_productos')
        self.assertEqual(self.weekly(client=nobody).status_code, 403)

    def test_the_supplier_filter_and_another_company(self):
        other_company = _p3_company('supweek-otra', 'Otra tienda')
        other_supplier = supply.create_supplier(company=other_company, name='Mayorista Lima', actor=None)
        other = supply.create_supplier(company=self.company, name='Otro mayorista', actor=self.staff)
        supply.create_offer(company=self.company, supplier_id=other.pk, product_id=self.fresh.pk, actor=self.staff,
                            may_view_costs=True, commercial_price='95.00', confirmed_quantity=2,
                            valid_from=today(), valid_until=today() + timedelta(days=6), lead_time_days=3)
        everything = self.weekly().json()['results']
        self.assertEqual({row['supplier_id'] for row in everything}, {self.supplier.pk, other.pk})
        only = self.weekly(supplier=other.pk).json()['results']
        self.assertEqual([(row['supplier_id'], row['product_id']) for row in only], [(other.pk, self.fresh.pk)])
        self.assertEqual(only[0]['delta'], 'nuevo')
        self.assertEqual(self.weekly(supplier=other_supplier.pk).status_code, 404)
        self.assertEqual(self.weekly(supplier='abc').status_code, 400)

    def test_another_company_sees_none_of_it(self):
        other_company = _p3_company('supweek-ajena', 'Tienda ajena')
        outsider, _ = _p2d_member(other_company, 'supweek_ajeno', ['company.view', 'products.view', 'costs.view'])
        client = APIClient()
        client.force_authenticate(user=outsider)
        body = client.get(WEEKLY).json()
        self.assertEqual(body['results'], [])

    def test_the_week_is_chosen_by_iso_week_or_by_a_day(self):
        last_week = today() - timedelta(days=7)
        by_week = self.weekly(week=_week_of(last_week)).json()
        by_day = self.weekly(week=last_week.isoformat()).json()
        self.assertEqual(by_week['week'], _week_of(last_week))
        self.assertEqual(by_week['results'], by_day['results'])
        listed = {row['product_id'] for row in by_week['results'] if row['message_date'] == last_week.isoformat()}
        self.assertEqual(listed, {product.pk for product in self.items.values()})
        for bad in ('2026-W99', 'semana', '2026-13-01'):
            self.assertEqual(self.weekly(week=bad).status_code, 400, bad)


class WeeklyQueryCountTest(WeeklyShop, TestCase):
    def setUp(self):
        self.build_weekly('supweek-consultas')

    def count(self):
        with CaptureQueriesContext(connection) as queries:
            res = self.weekly()
        self.assertEqual(res.status_code, 200, res.content)
        return len(queries), len(res.json()['results'])

    def test_a_fixed_number_of_queries_whatever_the_rows(self):
        self.load([self.list_row(key) for key in ('price', 'quantity')], today() - timedelta(days=7))
        few, few_rows = self.count()
        self.load([self.list_row(key, **{'Cantidad confirmada': 6}) for key in self.items], today())
        for n in range(4):
            product = _prod(self.company, f'Extra {n}', f'supweek-consultas-extra-{n}', price='70.00', inventory=0)
            self.stock(product, 1)
            self.make_offer(product=product, commercial_price='60.00', agreed_cost='40.00',
                            valid_from=today(), valid_until=today() + timedelta(days=6))
        many, many_rows = self.count()
        self.assertGreater(many_rows, few_rows)
        self.assertEqual(many, few)


class UnmentionedPreviewTest(WeeklyShop, TestCase):
    def setUp(self):
        self.build_weekly('supweek-parcial')

    def test_a_partial_list_names_the_running_offers_it_does_not_mention_and_switches_none_off(self):
        other = supply.create_supplier(company=self.company, name='Otro mayorista', actor=self.staff)
        theirs = supply.create_offer(company=self.company, supplier_id=other.pk, product_id=self.items['same'].pk,
                                     actor=self.staff, may_view_costs=True, commercial_price='95.00',
                                     confirmed_quantity=2, valid_from=today(), valid_until=today() + timedelta(days=6),
                                     lead_time_days=3)
        body = self.preview([self.list_row('price')], message_date=today()).json()
        unmentioned = {row['product_id']: row for row in body['unmentioned']}
        expected = {self.case.pk, *(self.items[key].pk for key in ('quantity', 'same', 'silent', 'retired'))}
        self.assertEqual(set(unmentioned), expected)
        self.assertNotIn(self.items['price'].pk, unmentioned)
        self.assertNotIn(theirs.pk, {row['offer_id'] for row in body['unmentioned']})
        row = unmentioned[self.items['silent'].pk]
        self.assertEqual((row['supplier_name'], row['product_name'], row['available']),
                         ('Mayorista Lima', 'Accesorio silent', 5), row)
        self.assertNotIn('agreed_cost', row)
        self.assertEqual(self.apply(body['id']).status_code, 200)
        self.assertEqual(SupplierOffer.objects.filter(company=self.company, is_active=False).count(), 0)

    def test_a_complete_list_keeps_its_own_absent_review(self):
        rows = [self.list_row(key) for key in ('price', 'quantity', 'same', 'silent')]
        rows.append(self.same())
        res = self.client_.post('/api/admin/supplier-offers/import/preview/', {
            'file': self.file(rows), 'message_date': today().isoformat(), 'complete_snapshot': 'true',
            'supplier_id': self.supplier.pk}, format='multipart')
        body = res.json()
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(body.get('unmentioned'), [])
        self.assertEqual([row['product_id'] for row in body['snapshot']['absent']], [self.items['retired'].pk])


class ValidCombinationWithoutVariantTest(WeeklyFile, TestCase):
    """An Excel row whose model and options are a whole combination of the palette, and no variant is it yet."""

    def setUp(self):
        self.build_week('supweek-variante')
        company = self.company
        capacity = device_catalog.create_attribute(company=company, name='Capacidad', role='capacity', sort_order=0)
        colour = device_catalog.create_attribute(company=company, name='Color', kind='color', role='color',
                                                 sort_order=1)
        self.small = device_catalog.create_option(company=company, attribute_id=capacity.pk, label='64 GB')
        self.big = device_catalog.create_option(company=company, attribute_id=capacity.pk, label='128 GB')
        self.black = device_catalog.create_option(company=company, attribute_id=colour.pk, label='Grafito',
                                                  swatch_hex='#333333')
        self.model = device_catalog.create_model(company=company, name='Tableta Serie X')
        model_palette.set_palette(self.model, option_ids=[self.small.pk, self.big.pk, self.black.pk])
        self.existing = _prod(company, 'Tableta Serie X 64 GB Grafito', 'supweek-variante-64', price='900.00',
                              inventory=0)
        device_catalog.set_variant(self.existing, model_id=self.model.pk, condition_line='new',
                                   option_ids=[self.small.pk, self.black.pk])

    def model_row(self, capacity):
        return self.row(**{'Código/SKU del producto': '', 'Modelo': 'Tableta Serie X', 'Capacidad': capacity,
                           'Color': 'Grafito', 'Precio comercial': '1000', 'Costo pactado': '800'})

    def test_the_row_says_the_combination_is_valid_and_what_creating_it_needs(self):
        body = self.preview([self.model_row('128 GB')]).json()
        row = body['rows'][0]
        self.assertEqual((row['action'], row['data'].get('error_code')), ('error', STATUS_VALID), row)
        proposal = row['data']['variant_proposal']
        self.assertEqual((proposal['model_id'], proposal['condition_line'], proposal['option_ids']),
                         (self.model.pk, 'new', sorted([self.big.pk, self.black.pk])), proposal)
        self.assertEqual(proposal['labels'], ['128 GB', 'Grafito'])
        self.assertTrue(any('combinación válida' in text for text in row['errors']), row['errors'])
        # Nothing was created by the preview.
        self.assertEqual(Product.objects.filter(company=self.company, product_model=self.model).count(), 1)

    def test_once_the_variant_is_created_the_row_finds_it(self):
        body = self.preview([self.model_row('128 GB')]).json()
        proposal = body['rows'][0]['data']['variant_proposal']
        res = self.client_.post(f'/api/admin/catalog/models/{proposal["model_id"]}/variants/', {
            'condition_line': proposal['condition_line'], 'option_ids': proposal['option_ids']}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        again = self.preview([self.model_row('128 GB')]).json()
        self.assertEqual(again['rows'][0]['action'], 'create', again['rows'])

    def test_an_existing_variant_and_a_partial_description_are_unchanged(self):
        body = self.preview([self.model_row('64 GB')]).json()
        self.assertEqual(body['rows'][0]['action'], 'create', body['rows'])
        partial = self.row(**{'Código/SKU del producto': '', 'Modelo': 'Tableta Serie X', 'Capacidad': '128 GB',
                              'Color': '', 'Precio comercial': '1000', 'Costo pactado': '800'})
        row = self.preview([partial]).json()['rows'][0]
        self.assertEqual(row['action'], 'error')
        self.assertNotIn('variant_proposal', row['data'])

    def test_the_resolver_is_what_decides(self):
        fields = {'model': 'Tableta Serie X', 'capacity': '128 GB', 'colour': 'Grafito', 'sim': '', 'region': '',
                  'line': ''}
        proposal = {}
        product, problem = supplier_offer_import._by_model(self.company, fields, proposal=proposal)
        self.assertIsNone(product)
        self.assertIn('combinación válida', problem)
        self.assertEqual(proposal['status'], STATUS_VALID)
