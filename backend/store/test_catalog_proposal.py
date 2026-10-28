"""
INV-FAST-B1 — assisted catalogue creation from a pasted list.

    A PROPOSAL WRITES NOTHING. AN APPLY CREATES ONLY WHAT WAS APPROVED, ONCE.

POST …/text-preview/catalog-proposal/        what the rows lack: model, options, variants (products.view)
POST …/text-preview/catalog-proposal/apply/  creates the approved part (products.manage + inventory.adjust)

The rows are the match candidates (model, capacity, colour, line) plus two
harmless hints (`hasImei`, a suggested sale `price`). A cost, an IMEI or a
serial never travels, and the answer carries none.
"""
import threading
from unittest import mock

from django.db import connection as db_connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from store import device_catalog, product_publication
from store.models import (
    AdminAuditLog, Category, Product, ProductBarcode, ProductModel, VariantOption,
)
from store.test_device_commerce import DeviceShop
from store.tests import _p2d_member, _p3_company

PROPOSE = '/api/admin/inventory/text-preview/catalog-proposal/'
APPLY = '/api/admin/inventory/text-preview/catalog-proposal/apply/'
MATCH = '/api/admin/inventory/text-preview/match/'


def row(key, model, gb, colour, line='used', **extra):
    return {'key': key, 'model': model, 'capacityGb': gb, 'colour': colour, 'line': line, **extra}


def twelve_phones():
    """12 pasted lines -> 7 models (one exists), 10 distinct variants (two exist)."""
    return [
        row('1', 'Teléfono X', 256, 'negro', 'new'),    # exists
        row('2', 'Teléfono X', 256, 'azul'),            # model exists, variant missing
        row('3', 'Teléfono Y', 128, 'negro'),
        row('4', 'Teléfono Y', 128, 'negro'),           # same variant as 3
        row('5', 'Teléfono Y', 256, 'azul'),
        row('6', 'Teléfono Z', 128, 'azul'),
        row('7', 'Teléfono W', 256, 'negro'),
        row('8', 'Teléfono V', 128, 'negro'),
        row('9', 'Tableta T', 128, 'negro'),
        row('10', 'Teléfono U', 512, 'rojo'),           # new capacity AND new colour option
        row('11', 'Teléfono U', 512, 'rojo'),           # same variant as 10
        row('12', 'Teléfono X', 128, 'azul'),           # exists (used128)
    ]


class _Base(DeviceShop):
    def start(self, slug='cp'):
        self.build(slug)
        self.api = APIClient()
        self.api.force_authenticate(self.staff)

    def propose(self, rows, client=None, **extra):
        return (client or self.api).post(PROPOSE, {'rows': rows, **extra}, format='json')

    def apply(self, rows, approve, client=None, key=None, **extra):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        return (client or self.api).post(APPLY, {'rows': rows, 'approve': approve, **extra}, format='json', **headers)

    def everything(self, plan):
        return {'models': [m['ref'] for m in plan['models'] if m['status'] == 'missing'],
                'variants': [v['ref'] for v in plan['variants'] if v['status'] == 'missing']}

    def catalogue_counts(self, company=None):
        company = company or self.company
        return (ProductModel.objects.filter(company=company).count(),
                VariantOption.objects.filter(company=company).count(),
                Product.objects.filter(company=company).count(),
                ProductBarcode.objects.filter(company=company).count())


class ProposalTest(_Base, TestCase):
    def setUp(self):
        self.start('cp-prop')

    def test_twelve_lines_become_seven_models_and_ten_variants(self):
        response = self.propose(twelve_phones())
        self.assertEqual(response.status_code, 200, response.content)
        plan = response.json()['plan']
        self.assertEqual(len(plan['models']), 7)
        self.assertEqual(sum(1 for m in plan['models'] if m['status'] == 'existing'), 1)
        self.assertEqual(len(plan['variants']), 10)
        self.assertEqual(sum(1 for v in plan['variants'] if v['status'] == 'existing'), 2)
        # The two options the catalogue lacks: 512 GB and Rojo.
        self.assertEqual(sorted(o['label'] for o in plan['options'] if o['status'] == 'missing'), ['512 GB', 'Rojo'])
        counts = response.json()['counts']
        self.assertEqual((counts['models_missing'], counts['variants_missing'], counts['options_missing']), (6, 8, 2))
        self.assertEqual(counts['rows_resolved'], 12)
        self.assertEqual(counts['rows_unresolved'], 0)

    def test_an_existing_variant_points_at_its_product(self):
        plan = self.propose([row('1', 'Teléfono X', 256, 'negro', 'new')]).json()['plan']
        self.assertEqual(plan['variants'][0]['status'], 'existing')
        self.assertEqual(plan['variants'][0]['product_id'], self.new256.pk)
        self.assertEqual(plan['models'][0]['model_id'], self.model.pk)

    def test_a_proposal_writes_nothing(self):
        before = (self.catalogue_counts(), AdminAuditLog.objects.count())
        self.assertEqual(self.propose(twelve_phones()).status_code, 200)
        self.assertEqual((self.catalogue_counts(), AdminAuditLog.objects.count()), before)

    def test_each_row_says_which_variant_it_is_or_why_not(self):
        rows = [row('a', 'Teléfono X', 256, 'negro', 'new'), row('b', 'Teléfono X', None, 'negro'),
                row('c', 'Funda Snoopy', None, '', 'accessory')]
        body = self.propose(rows).json()
        by_key = {r['key']: r for r in body['rows']}
        self.assertTrue(by_key['a']['variant_ref'])
        self.assertIsNone(by_key['b']['variant_ref'])
        self.assertIn('capacidad', by_key['b']['reason'].lower())
        self.assertIsNone(by_key['c']['variant_ref'])
        self.assertEqual(body['counts']['rows_unresolved'], 2)

    def test_an_identifier_in_the_model_is_not_proposed(self):
        body = self.propose([row('a', 'Teléfono Q 356938035643809', 128, 'negro')]).json()
        self.assertEqual(body['plan']['models'], [])
        self.assertIsNone(body['rows'][0]['variant_ref'])

    def test_no_capacity_attribute_means_no_variant_and_says_so(self):
        from store.models import VariantAttribute
        VariantAttribute.objects.filter(company=self.company, pk=self.capacity.pk).update(name='Memoria rara')
        body = self.propose([row('a', 'Teléfono Y', 999, 'negro')]).json()
        self.assertIsNone(body['rows'][0]['variant_ref'])
        self.assertIn('capacidad', body['rows'][0]['reason'].lower())

    def test_a_colour_two_attributes_share_is_not_guessed(self):
        other = device_catalog.create_attribute(company=self.company, name='Acabado')
        device_catalog.create_option(company=self.company, attribute_id=other.pk, label='Negro')
        body = self.propose([row('a', 'Teléfono X', 128, 'negro')]).json()
        self.assertIsNone(body['rows'][0]['variant_ref'])
        self.assertIn('varios atributos', body['rows'][0]['reason'])

    def test_a_row_without_colour_is_a_variant_with_a_warning(self):
        body = self.propose([row('a', 'Teléfono Y', 128, '')]).json()
        self.assertTrue(body['rows'][0]['variant_ref'])
        self.assertTrue(body['rows'][0]['warnings'])

    def test_suggested_name_follows_model_capacity_colour_and_line(self):
        plan = self.propose([row('a', 'Teléfono Y', 128, 'negro'), row('b', 'Teléfono Y', 128, 'negro', 'new')]).json()['plan']
        names = sorted(v['suggested_name'] for v in plan['variants'])
        self.assertEqual(names, ['Teléfono Y 128 GB Negro', 'Teléfono Y 128 GB Negro Seminuevo'])

    def test_a_suggested_price_is_carried_and_a_missing_one_is_null(self):
        plan = self.propose([row('a', 'Teléfono Y', 128, 'negro', price='1500'),
                             row('b', 'Teléfono Z', 128, 'negro')]).json()['plan']
        prices = {v['suggested_name']: v['suggested_price'] for v in plan['variants']}
        self.assertEqual(prices['Teléfono Y 128 GB Negro Seminuevo'], '1500.00')
        self.assertIsNone(prices['Teléfono Z 128 GB Negro Seminuevo'])

    def test_costs_imeis_and_serials_are_refused(self):
        for extra in ({'cost': '900'}, {'imei': '356938035643809'}, {'serial': 'ABC123'}, {'supplierCost': '1'}):
            with self.subTest(extra):
                response = self.propose([row('a', 'Teléfono Y', 128, 'negro', **extra)])
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.propose([]).status_code, 400)
        self.assertEqual(self.propose([row('a', 'T', 128, '', price='-1')]).status_code, 400)
        self.assertEqual(self.propose([row('a', 'T', 128, ''), row('a', 'U', 128, '')]).status_code, 400)

    def test_the_answer_carries_no_cost_or_identifier_key(self):
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)

        names = {k.lower() for k in keys(self.propose(twelve_phones()).json())}
        self.assertFalse({n for n in names if 'cost' in n or n in ('imei', 'imei2', 'serial', 'serial_number')}, names)

    def test_another_companys_catalogue_is_never_seen_or_reused(self):
        other = _p3_company('cp-other', 'Otra')
        device_catalog.create_model(company=other, name='Teléfono Y')
        plan = self.propose([row('a', 'Teléfono Y', 128, 'negro')]).json()['plan']
        self.assertEqual(plan['models'][0]['status'], 'missing')

    def test_capabilities_and_login(self):
        nobody, _ = _p2d_member(self.company, 'cp-nobody', ['company.view'])
        viewer, _ = _p2d_member(self.company, 'cp-viewer', ['company.view', 'products.view'])
        client = APIClient()
        client.force_authenticate(nobody)
        self.assertEqual(self.propose(twelve_phones(), client=client).status_code, 403)
        client.force_authenticate(viewer)
        self.assertEqual(self.propose(twelve_phones(), client=client).status_code, 200)
        self.assertEqual(APIClient().post(PROPOSE, {}, format='json').status_code, 401)


class SpellingTest(_Base, TestCase):
    def setUp(self):
        self.start('cp-spell')

    def test_composed_decomposed_and_upper_case_are_one_model(self):
        import unicodedata
        nfd = unicodedata.normalize('NFD', 'Teléfono Y')
        self.assertNotEqual(nfd, 'Teléfono Y')
        rows = [row('a', 'Teléfono Y', 128, 'negro'), row('b', nfd, 128, 'negro'),
                row('c', 'TELÉFONO y', 128, 'NEGRO')]
        plan = self.propose(rows).json()['plan']
        self.assertEqual((len(plan['models']), len(plan['variants'])), (1, 1))
        self.assertEqual(self.apply(rows, self.everything(plan)).status_code, 200)
        self.assertEqual(self.apply(rows, self.everything(plan)).status_code, 200)
        self.assertEqual(ProductModel.objects.filter(company=self.company, name__icontains='fono y').count(), 1)
        self.assertEqual(ProductModel.objects.get(company=self.company, name__icontains='fono y').name, 'Teléfono Y')
        # …and afterwards the match endpoint finds it whichever way it is typed.
        candidates = [{'key': r['key'], 'kind': 'device', 'model': r['model'], 'capacityGb': 128,
                       'colour': r['colour'], 'line': 'used'} for r in rows]
        matches = self.api.post(MATCH, {'candidates': candidates}, format='json').json()['matches']
        self.assertEqual({m['status'] for m in matches.values()}, {'exact'})

    def test_an_existing_model_typed_decomposed_is_not_created_again(self):
        import unicodedata
        nfd = unicodedata.normalize('NFD', 'Teléfono X')
        plan = self.propose([row('a', nfd, 256, 'negro', 'new')]).json()['plan']
        self.assertEqual(plan['models'][0]['status'], 'existing')
        self.assertEqual(plan['variants'][0]['status'], 'existing')

    def test_a_model_stored_decomposed_is_found_by_its_composed_name(self):
        import unicodedata
        ProductModel.objects.filter(pk=self.model.pk).update(name=unicodedata.normalize('NFD', 'Teléfono X'))
        plan = self.propose([row('a', 'Teléfono X', 256, 'negro', 'new')]).json()['plan']
        self.assertEqual((plan['models'][0]['status'], plan['variants'][0]['status']), ('existing', 'existing'))

    def test_an_option_spelled_differently_is_the_same_option(self):
        plan = self.propose([row('a', 'Teléfono Y', 128, 'NEGRO'), row('b', 'Teléfono Y', 128, ' negro ')]).json()['plan']
        self.assertEqual([o['status'] for o in plan['options'] if o['label'].lower() == 'negro'], ['existing'])
        self.assertEqual(len(plan['variants']), 1)

    def test_an_identifier_in_the_colour_is_never_proposed_or_stored(self):
        rows = [row('a', 'Teléfono Y', 128, '356938035643809'), row('b', 'Teléfono Y', 128, 'negro IMEI 356938035643809')]
        body = self.propose(rows).json()
        self.assertEqual([r['variant_ref'] for r in body['rows']], [None, None])
        self.assertIn('color', body['rows'][0]['reason'].lower())
        self.assertNotIn('356938035643809', str(body))
        self.assertEqual(body['plan']['variants'], [])


class ApplyTest(_Base, TestCase):
    def setUp(self):
        self.start('cp-apply')

    def approve_everything(self, rows, **extra):
        plan = self.propose(rows).json()['plan']
        return plan, self.apply(rows, self.everything(plan), **extra)

    def test_approving_everything_creates_the_missing_catalogue_hidden(self):
        rows = twelve_phones()
        plan, response = self.approve_everything(rows)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((len(body['created']['models']), len(body['created']['variants']),
                          len(body['created']['options'])), (6, 8, 2))
        self.assertEqual(ProductModel.objects.filter(company=self.company).count(), 7)
        self.assertEqual(Product.objects.filter(company=self.company, product_model__isnull=False).count(), 3 + 8)
        for product in Product.objects.filter(company=self.company, name__contains='Tel').exclude(
                pk__in=[self.new128.pk, self.new256.pk, self.used128.pk]):
            self.assertFalse(product.is_published_online, product.name)
            self.assertTrue(product.is_active)
            self.assertTrue(product.is_serialized)
            self.assertTrue(ProductBarcode.objects.filter(product=product).exists(), 'a code to label it')
        self.assertFalse(ProductModel.objects.get(company=self.company, name='Teléfono Y').is_published_online)

    def test_a_variant_without_price_cannot_be_published(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        _plan, response = self.approve_everything(rows)
        self.assertEqual(response.status_code, 200, response.content)
        product = Product.objects.get(company=self.company, name='Teléfono Y 128 GB Negro Seminuevo')
        self.assertEqual(str(product.price), '0.00')
        self.assertIn('no_price', product_publication.publication_problems(product))

    def test_the_suggested_price_is_used_and_an_edit_wins(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro', price='1500'), row('b', 'Teléfono Z', 128, 'negro', price='900')]
        plan = self.propose(rows).json()['plan']
        z = next(v for v in plan['variants'] if v['suggested_name'].startswith('Teléfono Z'))
        response = self.apply(rows, self.everything(plan), prices={z['ref']: '950.50'})
        self.assertEqual(response.status_code, 200, response.content)
        prices = dict(Product.objects.filter(company=self.company, name__startswith='Teléfono ')
                      .values_list('name', 'price'))
        self.assertEqual(str(prices['Teléfono Y 128 GB Negro Seminuevo']), '1500.00')
        self.assertEqual(str(prices['Teléfono Z 128 GB Negro Seminuevo']), '950.50')

    def test_the_rows_match_exactly_afterwards(self):
        rows = twelve_phones()
        self.approve_everything(rows)
        candidates = [{'key': r['key'], 'kind': 'device', 'model': r['model'], 'capacityGb': r['capacityGb'],
                       'colour': r['colour'], 'line': r['line']} for r in rows]
        matches = self.api.post(MATCH, {'candidates': candidates}, format='json').json()['matches']
        self.assertEqual({k: m['status'] for k, m in matches.items()}, {r['key']: 'exact' for r in rows})

    def test_only_what_was_approved_is_created(self):
        rows = twelve_phones()
        plan = self.propose(rows).json()['plan']
        y = next(m for m in plan['models'] if m['name'] == 'Teléfono Y')
        y128 = next(v for v in plan['variants'] if v['suggested_name'] == 'Teléfono Y 128 GB Negro Seminuevo')
        before = self.catalogue_counts()
        response = self.apply(rows, {'models': [y['ref']], 'variants': [y128['ref']]})
        self.assertEqual(response.status_code, 200, response.content)
        models, options, products, _codes = self.catalogue_counts()
        self.assertEqual((models, options, products), (before[0] + 1, before[1], before[2] + 1))
        self.assertFalse(VariantOption.objects.filter(company=self.company, label__in=['512 GB', 'Rojo']).exists())
        self.assertFalse(ProductModel.objects.filter(company=self.company, name='Teléfono U').exists())

    def test_the_options_come_only_with_an_approved_variant(self):
        rows = [row('a', 'Teléfono U', 512, 'rojo')]
        plan = self.propose(rows).json()['plan']
        model = plan['models'][0]
        response = self.apply(rows, {'models': [model['ref']], 'variants': []})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VariantOption.objects.filter(company=self.company, label='512 GB').exists())
        self.assertTrue(ProductModel.objects.filter(company=self.company, name='Teléfono U').exists())

    def test_a_variant_whose_model_is_neither_there_nor_approved_is_refused_whole(self):
        rows = twelve_phones()
        plan = self.propose(rows).json()['plan']
        y128 = next(v for v in plan['variants'] if v['suggested_name'] == 'Teléfono Y 128 GB Negro Seminuevo')
        before = self.catalogue_counts()
        response = self.apply(rows, {'models': [], 'variants': [y128['ref']]})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['code'], 'model_not_approved')
        self.assertEqual(self.catalogue_counts(), before)

    def test_an_unknown_reference_is_refused(self):
        response = self.apply(twelve_phones(), {'models': ['model:inventado'], 'variants': []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'unknown_ref')

    def test_applying_twice_creates_nothing_new(self):
        rows = twelve_phones()
        plan, first = self.approve_everything(rows)
        after_first = (self.catalogue_counts(), AdminAuditLog.objects.filter(action='product_created').count())
        second = self.apply(rows, self.everything(plan))
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(self.catalogue_counts(), after_first[0])
        self.assertEqual(AdminAuditLog.objects.filter(action='product_created').count(), after_first[1])
        self.assertEqual(len(second.json()['created']['variants']), 0)
        self.assertEqual(len(second.json()['existing']['variants']), 8)
        # The same variants, the same products.
        self.assertEqual({v['product_id'] for v in first.json()['variants']},
                         {v['product_id'] for v in second.json()['variants']})

    def test_the_same_idempotency_key_replays_the_answer(self):
        rows = twelve_phones()
        plan = self.propose(rows).json()['plan']
        approve = self.everything(plan)
        first = self.apply(rows, approve, key='lote-1')
        again = self.apply(rows, approve, key='lote-1')
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()['replayed'])
        self.assertEqual(again.json()['created'], first.json()['created'])
        self.assertEqual(AdminAuditLog.objects.filter(action='catalog_proposal_applied').count(), 1)

    def test_the_same_key_with_another_request_is_a_conflict(self):
        rows = twelve_phones()
        plan = self.propose(rows).json()['plan']
        self.apply(rows, self.everything(plan), key='lote-2')
        other = self.apply(rows[:2], {'models': [], 'variants': []}, key='lote-2')
        self.assertEqual(other.status_code, 409, other.content)

    def test_every_write_is_audited_with_its_company(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        self.approve_everything(rows)
        actions = set(AdminAuditLog.objects.filter(company=self.company).values_list('action', flat=True))
        self.assertTrue({'product_model_created', 'product_created', 'product_variant_set',
                         'catalog_proposal_applied'} <= actions, actions)
        summary = AdminAuditLog.objects.get(company=self.company, action='catalog_proposal_applied')
        self.assertEqual(summary.metadata['variants_created'], 1)
        self.assertNotIn('rows', summary.metadata)

    def test_a_failure_halfway_leaves_nothing(self):
        rows = twelve_phones()
        plan = self.propose(rows).json()['plan']
        before = (self.catalogue_counts(), AdminAuditLog.objects.count())
        real = device_catalog.set_variant
        calls = []

        def flaky(product, **kwargs):
            calls.append(product.pk)
            if len(calls) == 4:
                raise device_catalog.CatalogError('boom', field='options')
            return real(product, **kwargs)

        with mock.patch('store.catalog_proposal.device_catalog.set_variant', flaky):
            response = self.apply(rows, self.everything(plan))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual((self.catalogue_counts(), AdminAuditLog.objects.count()), before)

    def test_it_needs_catalogue_and_inventory_authority(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        plan = self.propose(rows).json()['plan']
        approve = self.everything(plan)
        before = self.catalogue_counts()
        for name, caps in (('viewer', ['company.view', 'products.view']),
                           ('catalog-only', ['company.view', 'products.view', 'products.manage']),
                           ('stock-only', ['company.view', 'inventory.adjust'])):
            who, _ = _p2d_member(self.company, f'cp-{name}', caps)
            client = APIClient()
            client.force_authenticate(who)
            with self.subTest(name):
                self.assertEqual(self.apply(rows, approve, client=client).status_code, 403)
        self.assertEqual(self.catalogue_counts(), before)
        self.assertEqual(APIClient().post(APPLY, {}, format='json').status_code, 401)

    def test_another_company_is_untouched_and_its_models_are_not_reused(self):
        other = _p3_company('cp-apply-other', 'Otra')
        theirs = device_catalog.create_model(company=other, name='Teléfono Y')
        before = self.catalogue_counts(other)
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        self.approve_everything(rows)
        self.assertEqual(self.catalogue_counts(other), before)
        mine = ProductModel.objects.get(company=self.company, name='Teléfono Y')
        self.assertNotEqual(mine.pk, theirs.pk)

    def test_a_category_of_another_company_is_refused(self):
        other = _p3_company('cp-cat-other', 'Otra')
        foreign = Category.objects.create(company=other, name='Ajena', slug='ajena')
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        plan = self.propose(rows).json()['plan']
        before = self.catalogue_counts()
        response = self.apply(rows, self.everything(plan), category_id=foreign.pk)
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(self.catalogue_counts(), before)

    def test_the_company_category_is_given_to_what_is_created(self):
        mine = Category.objects.create(company=self.company, name='Teléfonos', slug='telefonos')
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        plan = self.propose(rows).json()['plan']
        self.assertEqual(self.apply(rows, self.everything(plan), category_id=mine.pk).status_code, 200)
        product = Product.objects.get(company=self.company, name='Teléfono Y 128 GB Negro Seminuevo')
        self.assertEqual(product.category_id, mine.pk)
        self.assertEqual(product.product_model.category_id, mine.pk)

    def test_a_variant_requires_an_imei_unless_the_operator_says_otherwise(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro'), row('b', 'Tableta T', 128, 'negro'),
                row('c', 'Teléfono Z', 128, 'negro')]
        plan = self.propose(rows).json()['plan']
        self.assertTrue(all(v['requires_imei'] for v in plan['variants']))
        tablet = next(v for v in plan['variants'] if v['suggested_name'].startswith('Tableta'))
        response = self.apply(rows, self.everything(plan), without_imei=[tablet['ref']])
        self.assertEqual(response.status_code, 200, response.content)
        flags = dict(Product.objects.filter(company=self.company, name__contains='Seminuevo')
                     .values_list('name', 'requires_imei'))
        self.assertEqual(flags, {'Teléfono Y 128 GB Negro Seminuevo': True, 'Tableta T 128 GB Negro Seminuevo': False,
                                 'Teléfono Z 128 GB Negro Seminuevo': True})

    def test_a_row_without_imei_never_makes_the_variant_optional(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        self.approve_everything(rows)
        self.assertTrue(Product.objects.get(company=self.company, name__startswith='Teléfono Y').requires_imei)

    def test_the_old_imei_hint_is_no_longer_accepted(self):
        self.assertEqual(self.propose([row('a', 'Teléfono Y', 128, 'negro', hasImei=False)]).status_code, 400)

    def test_without_imei_names_only_variants_of_the_list(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        plan = self.propose(rows).json()['plan']
        before = self.catalogue_counts()
        bad = self.apply(rows, self.everything(plan), without_imei=['variant:inventada'])
        self.assertEqual((bad.status_code, bad.json()['code']), (400, 'unknown_ref'))
        self.assertEqual(self.catalogue_counts(), before)

    def test_created_variants_and_models_are_not_in_the_storefront(self):
        from store.tenancy import company_storefront_products
        rows = twelve_phones()
        before = set(company_storefront_products(self.company).values_list('pk', flat=True))
        self.approve_everything(rows)
        self.assertEqual(set(company_storefront_products(self.company).values_list('pk', flat=True)), before)
        self.assertFalse(ProductModel.objects.filter(company=self.company, is_published_online=True)
                         .exclude(pk=self.model.pk).exists())

    def test_a_model_created_by_hand_after_the_proposal_is_found_not_duplicated(self):
        rows = [row('a', 'Teléfono Y', 128, 'negro')]
        plan = self.propose(rows).json()['plan']
        hand = device_catalog.create_model(company=self.company, name='Teléfono Y')
        response = self.apply(rows, self.everything(plan))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ProductModel.objects.filter(company=self.company, name='Teléfono Y').count(), 1)
        self.assertEqual(Product.objects.get(company=self.company, name__startswith='Teléfono Y').product_model_id,
                         hand.pk)

    def test_a_new_colour_gets_a_neutral_swatch_to_adjust(self):
        rows = [row('a', 'Teléfono U', 512, 'rojo')]
        self.approve_everything(rows)
        red = VariantOption.objects.get(company=self.company, label='Rojo')
        self.assertEqual(red.attribute_id, self.colour.pk)
        self.assertRegex(red.swatch_hex, r'^#[0-9A-F]{6}$')
        self.assertTrue(VariantOption.objects.filter(company=self.company, label='512 GB',
                                                     attribute=self.capacity).exists())


class TwoApprovalsAtOnceTest(_Base, TransactionTestCase):
    def setUp(self):
        self.start('cp-race')

    def test_two_operators_approving_the_same_list_create_it_once(self):
        rows = twelve_phones()
        plan = self.propose(rows).json()['plan']
        approve = self.everything(plan)
        barrier, results = threading.Barrier(2), [None, None]

        def run(index):
            try:
                client = APIClient()
                client.force_authenticate(self.staff)
                barrier.wait(timeout=10)
                results[index] = client.post(APPLY, {'rows': rows, 'approve': approve}, format='json')
            except Exception as failed:  # noqa: BLE001 — the outcome under test
                results[index] = failed
            finally:
                db_connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        for response in results:
            self.assertEqual(getattr(response, 'status_code', response), 200, results)
        self.assertEqual(ProductModel.objects.filter(company=self.company).count(), 7)
        self.assertEqual(Product.objects.filter(company=self.company, product_model__isnull=False).count(), 11)
        self.assertEqual(VariantOption.objects.filter(company=self.company, label__in=['512 GB', 'Rojo']).count(), 2)
        self.assertEqual(sorted(len(r.json()['created']['variants']) for r in results), [0, 8])
