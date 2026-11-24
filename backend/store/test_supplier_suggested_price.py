"""
SUPPLY-MARGIN-01 — the suggested price of a supplier offer.

Owner's rule: «Precio sugerido: costo más margen configurable por empresa, presentado sólo como propuesta
revisable. No cambiar automáticamente los precios publicados.»

What these tests pin:

  * the arithmetic (`supplier_pricing.suggested_price`): cost × (1 + margin / 100), rounded UP to the cent, or UP
    to the next multiple of the company's «redondear a» (whole soles); nothing without a cost or a margin;
  * the two settings (`CompanySettings.supplier_margin_percent`, `supplier_price_round_to`): validated by the
    API and by a CHECK in the database; read and changed only with `costs.view` (a suggestion reveals the cost),
    changing them also takes `settings.manage` and the screen's `company.manage`;
  * `suggested_price` travels ONLY to `costs.view` — offer list and detail, the weekly panel, the import preview
    (Excel and the text assistant's dry run): without it the KEY is absent, whatever the settings;
  * nothing is ever written with it: an import with a margin configured writes the file's prices, a variant's
    own price never moves, and an offer keeps its price until a person saves another one;
  * migration 0190 only adds two nullable columns and their two CHECKs.
"""
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from store import supplier_pricing
from store.models import AdminAuditLog, CompanySettings, Product, SupplierOffer
from store.test_supplier_offers import OfferFile, today
from store.tests import _p2d_member, _p3_company

D = Decimal
SETTINGS = '/api/admin/company-settings/'
OFFERS = '/api/admin/supplier-offers/'
WEEKLY = '/api/admin/supplier-offers/weekly/'
MIGRATION = '0190_supplier_margin'


def rules(margin=None, round_to=None):
    return SimpleNamespace(supplier_margin_percent=None if margin is None else D(str(margin)),
                           supplier_price_round_to=round_to)


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------

class SuggestedPriceMathTest(SimpleTestCase):
    def test_cost_plus_margin_to_the_cent(self):
        self.assertEqual(supplier_pricing.suggested_price(D('100.00'), rules(30)), D('130.00'))
        self.assertEqual(supplier_pricing.suggested_price(D('68.00'), rules('12.5')), D('76.50'))
        self.assertEqual(supplier_pricing.suggested_price('70', rules(30)), D('91.00'))

    def test_a_fraction_of_a_cent_rounds_up_never_below_the_margin(self):
        # 33.33 × 1.10 = 36.663 → 36.67 (never 36.66, which would be under the margin)
        self.assertEqual(supplier_pricing.suggested_price(D('33.33'), rules(10)), D('36.67'))
        self.assertEqual(supplier_pricing.suggested_price(D('0.01'), rules('0.01')), D('0.02'))

    def test_zero_margin_is_the_cost(self):
        self.assertEqual(supplier_pricing.suggested_price(D('99.99'), rules(0)), D('99.99'))
        self.assertEqual(supplier_pricing.suggested_price(D('99.99'), rules(0, round_to=10)), D('100.00'))

    def test_a_high_margin(self):
        self.assertEqual(supplier_pricing.suggested_price(D('100.00'), rules(500)), D('600.00'))
        self.assertEqual(supplier_pricing.suggested_price(D('1234.56'), rules('499.99')), D('7407.24'))

    def test_round_up_to_the_next_multiple(self):
        cost = D('1000.00')
        self.assertEqual(supplier_pricing.suggested_price(cost, rules(30, round_to=1)), D('1300.00'))
        self.assertEqual(supplier_pricing.suggested_price(D('1000.10'), rules(30, round_to=1)), D('1301.00'))
        self.assertEqual(supplier_pricing.suggested_price(D('1001.00'), rules(30, round_to=10)), D('1310.00'))
        self.assertEqual(supplier_pricing.suggested_price(D('1001.00'), rules(30, round_to=50)), D('1350.00'))
        # Already a multiple: it stays.
        self.assertEqual(supplier_pricing.suggested_price(D('1000.00'), rules(30, round_to=50)), D('1300.00'))
        # 9 is a multiple of 9, not «ending in 9»: the rule is the same for every N.
        self.assertEqual(supplier_pricing.suggested_price(D('100.00'), rules(0, round_to=9)), D('108.00'))
        self.assertEqual(supplier_pricing.suggested_price(D('0.50'), rules(0, round_to=100)), D('100.00'))

    def test_nothing_without_a_cost_or_a_margin(self):
        for cost, settings in ((None, rules(30)), (D('100'), rules(None)), (D('100'), rules(None, round_to=10)),
                               (D('0'), rules(30)), (D('-5'), rules(30)), ('', rules(30)), ('abc', rules(30)),
                               (D('100'), None)):
            with self.subTest(cost=cost, settings=settings):
                self.assertIsNone(supplier_pricing.suggested_price(cost, settings))

    def test_a_suggestion_no_offer_could_hold_is_none(self):
        # `SupplierOffer.commercial_price` is 10 digits with 2 decimals.
        self.assertIsNone(supplier_pricing.suggested_price(D('99999999.99'), rules(1)))
        self.assertEqual(supplier_pricing.suggested_price(D('99999999.99'), rules(0)), D('99999999.99'))


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------

class PricingSettingsTest(TestCase):
    def setUp(self):
        self.company = _p3_company('supmargin-ajustes', 'Tienda margen')
        self.q = f'?company={self.company.pk}'
        base = ['company.view', 'company.manage']
        self.owner = self.client_for(base + ['settings.manage', 'costs.view'], 'supmargin_duena')
        self.no_costs = self.client_for(base + ['settings.manage'], 'supmargin_sin_costos')
        self.no_settings = self.client_for(base + ['costs.view'], 'supmargin_sin_ajustes')
        self.viewer = self.client_for(['company.view', 'costs.view'], 'supmargin_mirona')

    def client_for(self, caps, name):
        user, _ = _p2d_member(self.company, name, caps)
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def get(self, client):
        res = client.get(SETTINGS + self.q)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def patch(self, client, body):
        return client.patch(SETTINGS + self.q, body, format='json')

    def row(self):
        return CompanySettings.objects.get(company=self.company)

    def test_costs_view_reads_them_and_the_owner_changes_them(self):
        body = self.get(self.owner)
        self.assertIsNone(body['settings']['supplier_margin_percent'])
        self.assertIsNone(body['settings']['supplier_price_round_to'])
        self.assertTrue(body['can_manage_supplier_pricing'])
        res = self.patch(self.owner, {'supplier_margin_percent': '30', 'supplier_price_round_to': 10})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['settings']['supplier_margin_percent'],
                          res.json()['settings']['supplier_price_round_to']), ('30.00', 10))
        self.assertEqual((self.row().supplier_margin_percent, self.row().supplier_price_round_to), (D('30.00'), 10))
        audit = AdminAuditLog.objects.filter(action='company_settings_updated', company=self.company).latest('pk')
        self.assertEqual(audit.metadata['changed_fields'], ['supplier_margin_percent', 'supplier_price_round_to'])
        # Cleared again: no suggestion.
        res = self.patch(self.owner, {'supplier_margin_percent': None, 'supplier_price_round_to': None})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(self.row().supplier_margin_percent)

    def test_the_form_may_send_them_as_empty_text(self):
        res = self.patch(self.owner, {'supplier_margin_percent': '25.5', 'supplier_price_round_to': ''})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((self.row().supplier_margin_percent, self.row().supplier_price_round_to), (D('25.50'), None))
        res = self.patch(self.owner, {'supplier_margin_percent': ' '})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(self.row().supplier_margin_percent)

    def test_without_costs_view_the_keys_are_absent_whatever_they_hold(self):
        CompanySettings.objects.filter(company=self.company).update(supplier_margin_percent=D('30'),
                                                                    supplier_price_round_to=10)
        for client in (self.no_costs,):
            body = self.get(client)
            self.assertNotIn('supplier_margin_percent', body['settings'])
            self.assertNotIn('supplier_price_round_to', body['settings'])
            self.assertFalse(body['can_manage_supplier_pricing'])
        self.assertIn('supplier_margin_percent', self.get(self.viewer)['settings'])
        self.assertFalse(self.get(self.viewer)['can_manage_supplier_pricing'])

    def test_without_costs_view_a_stated_value_is_refused_whatever_it_is(self):
        CompanySettings.objects.filter(company=self.company).update(supplier_margin_percent=D('30'))
        # The same value and another one answer alike: no oracle on the company's margin.
        for value in ('30', '30.00', '45'):
            res = self.patch(self.no_costs, {'supplier_margin_percent': value})
            self.assertEqual(res.status_code, 403, res.content)
            self.assertEqual(res.json()['code'], 'costs_forbidden')
        res = self.patch(self.no_costs, {'supplier_price_round_to': 10})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertEqual(self.row().supplier_margin_percent, D('30'))
        self.assertIsNone(self.row().supplier_price_round_to)

    def test_without_costs_view_an_empty_value_changes_nothing(self):
        CompanySettings.objects.filter(company=self.company).update(supplier_margin_percent=D('30'),
                                                                    supplier_price_round_to=10)
        res = self.patch(self.no_costs, {'supplier_margin_percent': None, 'supplier_price_round_to': '',
                                         'city': 'Arequipa'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertNotIn('supplier_margin_percent', res.json()['settings'])
        row = self.row()
        self.assertEqual((row.supplier_margin_percent, row.supplier_price_round_to, row.city),
                         (D('30.00'), 10, 'Arequipa'))

    def test_changing_them_takes_settings_manage(self):
        res = self.patch(self.no_settings, {'supplier_margin_percent': '30'})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertIsNone(self.row().supplier_margin_percent)
        self.assertFalse(self.get(self.no_settings)['can_manage_supplier_pricing'])
        # Sent back as they are: the form it always was.
        self.assertEqual(self.patch(self.no_settings, {'supplier_margin_percent': None, 'city': 'Cusco'}).status_code,
                         200)

    def test_a_branch_limited_manager_cannot_change_them(self):
        user, _ = _p2d_member(self.company, 'supmargin_sucursal',
                              ['company.view', 'company.manage', 'settings.manage', 'costs.view'],
                              branches=[self.company.default_inventory_branch])
        client = APIClient()
        client.force_authenticate(user=user)
        self.assertEqual(self.patch(client, {'supplier_margin_percent': '30'}).status_code, 403)

    def test_bounds(self):
        for body in ({'supplier_margin_percent': '-1'}, {'supplier_margin_percent': '500.01'},
                     {'supplier_margin_percent': '12.345'}, {'supplier_margin_percent': 'treinta'},
                     {'supplier_price_round_to': 0}, {'supplier_price_round_to': -10},
                     {'supplier_price_round_to': 100001}, {'supplier_price_round_to': '2.5'}):
            with self.subTest(body=body):
                res = self.patch(self.owner, body)
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn(next(iter(body)), res.json())
        for margin in ('0', '500'):
            self.assertEqual(self.patch(self.owner, {'supplier_margin_percent': margin}).status_code, 200)
        self.assertEqual(self.patch(self.owner, {'supplier_price_round_to': 100000}).status_code, 200)

    def test_the_database_refuses_what_the_api_refuses(self):
        for values in ({'supplier_margin_percent': D('-0.01')}, {'supplier_margin_percent': D('500.01')},
                       {'supplier_price_round_to': 0}, {'supplier_price_round_to': 100001}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                CompanySettings.objects.filter(company=self.company).update(**values)


# ---------------------------------------------------------------------------
# Where the suggestion travels
# ---------------------------------------------------------------------------

class SuggestionShop(OfferFile):
    def build_margin(self, slug):
        self.build_excel(slug)
        CompanySettings.objects.update_or_create(
            company=self.company, defaults={'supplier_margin_percent': D('30'), 'supplier_price_round_to': 10})
        self.blind = self.as_(['company.view', 'products.view', 'products.manage'], f'{slug}_ciego')

    def as_(self, perms, name):
        user, _ = _p2d_member(self.company, name, perms)
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class OfferPayloadTest(SuggestionShop, TestCase):
    def setUp(self):
        self.build_margin('supmargin-ofertas')

    def test_list_and_detail_carry_it_to_costs_view(self):
        # cost 70 × 1.30 = 91 → next multiple of 10: 100
        listed = self.client_.get(OFFERS).json()['results']
        self.assertEqual([row['suggested_price'] for row in listed], ['100.00'])
        detail = self.client_.get(f'{OFFERS}{self.offer.pk}/').json()
        self.assertEqual((detail['suggested_price'], detail['commercial_price']), ('100.00', '110.00'))

    def test_none_without_a_cost_or_without_a_margin(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).update(agreed_cost=None)
        self.assertIsNone(self.client_.get(f'{OFFERS}{self.offer.pk}/').json()['suggested_price'])
        SupplierOffer.objects.filter(pk=self.offer.pk).update(agreed_cost=D('70'))
        CompanySettings.objects.filter(company=self.company).update(supplier_margin_percent=None)
        self.assertIsNone(self.client_.get(f'{OFFERS}{self.offer.pk}/').json()['suggested_price'])

    def test_without_costs_view_the_key_is_absent_whatever_the_settings(self):
        for margin in (D('30'), None):
            CompanySettings.objects.filter(company=self.company).update(supplier_margin_percent=margin)
            with self.subTest(margin=margin):
                listed = self.blind.get(OFFERS).json()['results']
                self.assertNotIn('suggested_price', listed[0])
                self.assertNotIn('suggested_price', self.blind.get(f'{OFFERS}{self.offer.pk}/').json())

    def test_saving_the_offer_never_takes_the_suggestion_by_itself(self):
        res = self.client_.patch(f'{OFFERS}{self.offer.pk}/', {'lead_time_days': 6}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()['commercial_price'], res.json()['suggested_price']), ('110.00', '100.00'))
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).commercial_price, D('110.00'))

    def test_the_list_costs_one_query_for_the_settings_whatever_the_rows(self):
        from django.test.utils import CaptureQueriesContext

        from store.tests import _prod

        with CaptureQueriesContext(connection) as few:
            self.client_.get(OFFERS)
        for n in range(3):
            product = _prod(self.company, f'Extra {n}', f'supmargin-extra-{n}', price='70.00', inventory=0)
            Product.objects.filter(pk=product.pk).update(sale_mode=Product.SALE_MODE_STOCK_OR_ORDER)
            self.make_offer(product=product)
        with CaptureQueriesContext(connection) as many:
            res = self.client_.get(OFFERS)
        self.assertEqual(len(res.json()['results']), 4)
        settings_reads = [q for q in many.captured_queries if 'store_companysettings' in q['sql']]
        self.assertEqual(len(settings_reads), 1, settings_reads)
        self.assertEqual(len(few.captured_queries), len(many.captured_queries))


class WeeklyPayloadTest(SuggestionShop, TestCase):
    def setUp(self):
        self.build_margin('supmargin-semana')

    def test_each_row_carries_it_to_costs_view_only(self):
        body = self.client_.get(WEEKLY).json()
        row = next(r for r in body['results'] if r['offer_id'] == self.offer.pk)
        self.assertEqual((row['agreed_cost'], row['suggested_price'], row['commercial_price']),
                         ('70.00', '100.00', '110.00'))
        blind = self.blind.get(WEEKLY).json()
        for row in blind['results']:
            self.assertNotIn('suggested_price', row)


class ImportPreviewTest(SuggestionShop, TestCase):
    def setUp(self):
        self.build_margin('supmargin-importa')

    def preview_row(self, body):
        self.assertEqual(body['rows_error'], 0, body['rows'])
        return body['rows'][0]

    def test_a_row_suggests_from_the_cost_it_writes(self):
        # The file states a cost of 68: 68 × 1.3 = 88.4 → 90.
        row = self.preview_row(self.preview([self.row(**{'Costo pactado': '68'})]).json())
        self.assertEqual(row['data']['suggested_price'], '90.00')

    def test_without_a_cost_in_the_file_it_suggests_from_the_offers(self):
        headers = [h for h in self._headers() if h != 'Costo pactado']
        values = dict(zip(self._headers(), self.row()))
        row = self.preview_row(self.preview([[values[h] for h in headers]], headers=headers).json())
        self.assertEqual(row['data']['suggested_price'], '100.00')

    def test_a_new_offer_without_any_cost_suggests_nothing(self):
        SupplierOffer.objects.filter(pk=self.offer.pk).delete()
        row = self.preview_row(self.preview([self.row(**{'Costo pactado': ''})]).json())
        self.assertIsNone(row['data']['suggested_price'])

    def test_without_costs_view_the_key_is_absent(self):
        body = self.preview([self.row(**{'Costo pactado': ''})], client=self.blind).json()
        for row in body['rows']:
            self.assertNotIn('suggested_price', row['data'])
        # The same job read back by who sees costs carries it (computed when read, never stored).
        from store.supplier_offer_import import job_payload
        from store.models import BulkImportJob

        job = BulkImportJob.objects.get(pk=body['id'])
        self.assertEqual(job_payload(job, include_cost=True)['rows'][0]['data']['suggested_price'], '100.00')
        self.assertNotIn('suggested_price', job.rows.get().normalized_data)

    def test_applying_writes_the_files_prices_never_the_suggestion(self):
        self.case.refresh_from_db()
        published = self.case.price
        body = self.preview([self.row(**{'Precio comercial': '110', 'Costo pactado': '70'})]).json()
        row = self.preview_row(body)
        self.assertEqual(row['data']['suggested_price'], '100.00')
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).commercial_price, D('110.00'))
        self.case.refresh_from_db()
        self.assertEqual(self.case.price, published)

    def test_a_changed_price_still_asks_for_its_decision(self):
        body = self.preview([self.row(**{'Precio comercial': '120', 'Costo pactado': '70'})]).json()
        res = self.apply(body['id'])
        self.assertEqual(res.status_code, 409, res.content)
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).commercial_price, D('110.00'))
        res = self.apply(body['id'], keep_prices=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(SupplierOffer.objects.get(pk=self.offer.pk).commercial_price, D('110.00'))

    @staticmethod
    def _headers():
        from store.test_supplier_offers import OFFER_HEADERS
        return list(OFFER_HEADERS)


class TextAssistantDryRunTest(SuggestionShop, TestCase):
    """The text assistant's dry run answers with `job_payload`: the same rule."""

    def setUp(self):
        self.build_margin('supmargin-texto')

    def dry_run(self, client, **row):
        line = {'key': 'l1', 'product_id': self.case.pk, 'commercial_price': '110', 'confirmed_quantity': 5}
        line.update(row)
        res = client.post('/api/admin/inventory/text-preview/offer-draft/', {
            'supplier_id': self.supplier.pk, 'message_date': today().isoformat(), 'complete': False,
            'valid_from': today().isoformat(), 'valid_until': (today() + timedelta(days=6)).isoformat(),
            'lead_time_days': 4, 'rows': [line],
        }, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['rows']

    def test_the_dry_run_carries_it_to_costs_view_only(self):
        self.assertEqual(self.dry_run(self.client_, agreed_cost='70')[0]['data']['suggested_price'], '100.00')
        self.assertNotIn('suggested_price', self.dry_run(self.blind)[0]['data'])


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------

class MigrationTest(TestCase):
    def test_it_only_adds_two_nullable_columns_and_their_checks(self):
        from django.db import migrations

        module = import_module(f'store.migrations.{MIGRATION}')
        self.assertEqual(module.Migration.dependencies, [('store', '0189_sales_quote')])
        operations = module.Migration.operations
        added = [op for op in operations if isinstance(op, migrations.AddField)]
        self.assertEqual(sorted(op.name for op in added), ['supplier_margin_percent', 'supplier_price_round_to'])
        for op in added:
            self.assertEqual(op.model_name, 'companysettings')
            self.assertTrue(op.field.null, op.name)
        checks = [op for op in operations if isinstance(op, migrations.AddConstraint)]
        self.assertEqual(sorted(op.constraint.name for op in checks),
                         ['companysettings_supplier_margin_0_500', 'companysettings_supplier_round_to_positive'])
        # Plus the R13 reverse guard, last: refuses to go back while quote print tickets exist; no-op forward.
        self.assertEqual(len(operations), 5)
        self.assertEqual(operations[-1].reverse_code.__name__, 'refuse_reverse_with_quote_tickets')

    def test_the_state_before_and_after(self):
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection, ignore_no_migrations=True)
        before = loader.project_state(('store', '0189_sales_quote')).apps.get_model('store', 'CompanySettings')
        after = loader.project_state(('store', MIGRATION)).apps.get_model('store', 'CompanySettings')
        names = {f.name for f in before._meta.get_fields()}
        self.assertFalse({'supplier_margin_percent', 'supplier_price_round_to'} & names)
        margin = after._meta.get_field('supplier_margin_percent')
        self.assertEqual((margin.max_digits, margin.decimal_places, margin.null), (5, 2, True))
        self.assertTrue(after._meta.get_field('supplier_price_round_to').null)
