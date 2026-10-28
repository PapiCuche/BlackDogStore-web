"""
INV-UX-A1 — the product picker's search: name, slug, SKU or scanned code.

The admin product list used to answer only to a name or a slug, so a code read
by a scanner (or the internal SKU printed on a label) found nothing. Codes live
in `ProductBarcode` (there is no `Product.sku`); the list now also matches them,
inside the caller's company only, with an exact match first.
"""
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from store.models import Category, Product, ProductBarcode
from store.tests import _p2d_member, _p3_company
from django.utils import timezone

LIST_URL = '/api/admin/products/'
BULK_URL = '/api/admin/products/bulk-visibility/'


class PickerSearchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('picker-a', 'Tienda A')
        self.other = _p3_company('picker-b', 'Tienda B')
        self.category = Category.objects.create(company=self.company, name='Cables', slug='cables')
        user, _ = _p2d_member(self.company, 'picker_op', ['company.view', 'products.view'])
        self.api = APIClient()
        self.api.force_authenticate(user=user)
        self.n = 0

    def product(self, name, *, company=None, **over):
        self.n += 1
        return Product.objects.create(
            company=company or self.company, name=name, slug=f'picker-{self.n}',
            price=Decimal('10.00'), **over,
        )

    def code(self, product, code, *, primary=False, active=True, symbology='ean13'):
        return ProductBarcode.objects.create(
            product=product, code=code, is_primary=primary, is_active=active, symbology=symbology,
        )

    def search(self, term, **extra):
        response = self.api.get(LIST_URL, {'search': term, **extra})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['results']

    # -- matching ------------------------------------------------------------

    def test_matches_a_full_barcode(self):
        cable = self.product('Cable USB-C 1m')
        self.product('Funda')
        self.code(cable, '7750001234567')
        found = self.search('7750001234567')
        self.assertEqual([row['id'] for row in found], [cable.id])

    def test_matches_a_partial_code_and_the_internal_sku(self):
        cable = self.product('Cable USB-C 1m')
        self.code(cable, 'BD-000123', primary=True, symbology='internal')
        self.assertEqual([r['id'] for r in self.search('000123')], [cable.id])
        self.assertEqual([r['id'] for r in self.search('bd-000123')], [cable.id])

    def test_name_and_slug_still_match(self):
        cable = self.product('Cable USB-C 1m')
        self.assertEqual([r['id'] for r in self.search('usb-c')], [cable.id])
        self.assertEqual([r['id'] for r in self.search(cable.slug)], [cable.id])

    def test_an_inactive_code_does_not_match(self):
        cable = self.product('Cable USB-C 1m')
        self.code(cable, '7750009999991', active=False)
        self.assertEqual(self.search('7750009999991'), [])

    def test_a_product_with_two_matching_codes_appears_once(self):
        cable = self.product('Cable USB-C 1m')
        self.code(cable, '77500011111')
        self.code(cable, '77500012222')
        self.assertEqual([r['id'] for r in self.search('775000')], [cable.id])

    # -- tenancy -------------------------------------------------------------

    def test_another_companys_barcode_never_matches(self):
        foreign = self.product('Cable ajeno', company=self.other)
        self.code(foreign, '7750005550001')
        self.assertEqual(self.search('7750005550001'), [])

    def test_the_same_code_in_two_companies_returns_only_ours(self):
        ours = self.product('Cable nuestro')
        theirs = self.product('Cable ajeno', company=self.other)
        self.code(ours, '7750007770001')
        self.code(theirs, '7750007770001')
        self.assertEqual([r['id'] for r in self.search('7750007770001')], [ours.id])

    # -- order and the exact flag -------------------------------------------

    def test_an_exact_code_comes_before_partial_matches_and_is_flagged(self):
        newer_partial = self.product('Cargador 12345 pro')
        exact = self.product('Hub')
        self.code(exact, '12345')
        self.code(self.product('Otro'), '123456')
        # `newer_partial` is the newest row: without ranking it would be first.
        found = self.search('12345')
        self.assertEqual(found[0]['id'], exact.id)
        self.assertTrue(found[0]['search_exact'])
        self.assertIn(newer_partial.id, [r['id'] for r in found])
        self.assertFalse(any(r['search_exact'] for r in found[1:]))

    def test_without_a_search_nothing_is_flagged_and_order_is_unchanged(self):
        first = self.product('Primero')
        second = self.product('Segundo')
        rows = self.api.get(LIST_URL).json()['results']
        self.assertEqual([r['id'] for r in rows], [second.id, first.id])
        self.assertFalse(any(r['search_exact'] for r in rows))

    # -- what the response shows ---------------------------------------------

    def test_the_row_carries_its_primary_code_and_nothing_private(self):
        cable = self.product('Cable USB-C 1m', reference_cost=Decimal('3.50'))
        self.code(cable, '7750001234567')
        self.code(cable, 'BD-000123', primary=True, symbology='internal')
        row = self.search('cable')[0]
        self.assertEqual(row['primary_code'], 'BD-000123')
        for private in ('reference_cost', 'cost', 'imei', 'serial_number'):
            self.assertNotIn(private, row)

    def test_primary_code_is_empty_for_a_product_without_codes(self):
        self.product('Sin código')
        self.assertEqual(self.search('sin')[0]['primary_code'], '')

    # -- cost ----------------------------------------------------------------

    def test_a_page_costs_the_same_queries_however_many_rows_it_has(self):
        def cost(rows):
            ProductBarcode.objects.all().delete()
            Product.objects.filter(company=self.company).delete()
            for i in range(rows):
                self.code(self.product(f'Lote {i}'), f'8800000{i:05d}', primary=True)
            cache.clear()
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(len(self.search('lote')), rows)
            return len(queries)

        self.assertEqual(cost(2), cost(12))

    def test_nul_in_the_search_matches_nothing(self):
        self.product('Cable')
        self.assertEqual(self.search('ab\x00c'), [])


    # -- review fixes (#195) ---------------------------------------------------

    def test_detail_returns_the_real_primary_code(self):
        cable = self.product('Cable USB-C 1m')
        self.code(cable, '7750001234567')
        self.code(cable, 'BD-000123', primary=True, symbology='internal')
        body = self.api.get(f'{LIST_URL}{cable.pk}/').json()
        self.assertEqual(body['primary_code'], 'BD-000123')
        self.assertFalse(body['search_exact'])

    def test_detail_without_codes_says_empty_and_ignores_inactive_ones(self):
        cable = self.product('Cable')
        self.code(cable, '7750009999991', active=False)
        self.assertEqual(self.api.get(f'{LIST_URL}{cable.pk}/').json()['primary_code'], '')

    def test_patch_response_carries_the_primary_code_too(self):
        manager, _ = _p2d_member(self.company, 'picker_mgr', ['company.view', 'products.view', 'products.manage'])
        api = APIClient()
        api.force_authenticate(user=manager)
        cable = self.product('Cable')
        self.code(cable, 'BD-000777', primary=True, symbology='internal')
        body = api.patch(f'{LIST_URL}{cable.pk}/', {'name': 'Cable renombrado'}, format='json').json()
        self.assertEqual(body['primary_code'], 'BD-000777')

    def test_exact_code_is_case_insensitive_and_beats_an_exact_name(self):
        named = self.product('ZZ1234')
        coded = self.product('Hub')
        self.code(coded, 'ZZ1234')
        found = self.search('zz1234')
        self.assertEqual([r['id'] for r in found], [coded.id, named.id])
        self.assertTrue(found[0]['search_exact'])
        self.assertFalse(found[1]['search_exact'])

    def test_ties_are_broken_by_id_so_pages_are_stable(self):
        rows = [self.product(f'Tie {i}') for i in range(4)]
        Product.objects.filter(pk__in=[r.pk for r in rows]).update(created_at=timezone.now())
        found = self.search('tie')
        self.assertEqual([r['id'] for r in found], sorted((r.pk for r in rows), reverse=True))

    # -- bulk visibility shares the definition ---------------------------------

    def bulk_api(self):
        manager, _ = _p2d_member(self.company, 'picker_bulk', ['company.view', 'products.view', 'products.manage'])
        api = APIClient()
        api.force_authenticate(user=manager)
        return api

    def test_bulk_by_code_selects_exactly_what_the_list_counted(self):
        hit = self.product('Cable A', is_published_online=True)
        other = self.product('Cable B', is_published_online=True)
        self.code(hit, '7750001234567')
        counted = self.api.get(LIST_URL, {'search': '77500012'}).json()['count']
        self.assertEqual(counted, 1)
        api = self.bulk_api()
        refused = api.post(BULK_URL, {'action': 'hide', 'filter': {'search': '77500012'}, 'expected_count': 2}, format='json')
        self.assertEqual(refused.status_code, 409, refused.content)
        done = api.post(BULK_URL, {'action': 'hide', 'filter': {'search': '77500012'}, 'expected_count': counted}, format='json')
        self.assertEqual(done.status_code, 200, done.content)
        hit.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((hit.is_published_online, other.is_published_online), (False, True))

    def test_bulk_by_code_never_reaches_another_company_with_the_same_code(self):
        mine = self.product('Cable A', is_published_online=True)
        theirs = self.product('Cable ajeno', company=self.other, is_published_online=True)
        self.code(mine, '7750007770001')
        self.code(theirs, '7750007770001')
        done = self.bulk_api().post(
            BULK_URL, {'action': 'hide', 'filter': {'search': '7750007770001'}, 'expected_count': 1}, format='json')
        self.assertEqual(done.status_code, 200, done.content)
        mine.refresh_from_db(); theirs.refresh_from_db()
        self.assertEqual((mine.is_published_online, theirs.is_published_online), (False, True))

    def test_a_code_row_filed_under_another_company_is_ignored_by_list_and_bulk(self):
        # Inconsistent data (a row whose company is not its product's): the lock
        # on `company_id` is what keeps it out. Bypasses save(), which would fix it.
        cable = self.product('Cable A', is_published_online=True)
        row = self.code(cable, '7750003330001')
        ProductBarcode.objects.filter(pk=row.pk).update(company=self.other)
        self.assertEqual(self.search('7750003330001'), [])
        done = self.bulk_api().post(
            BULK_URL, {'action': 'hide', 'filter': {'search': '7750003330001'}, 'expected_count': 0}, format='json')
        cable.refresh_from_db()
        self.assertTrue(cable.is_published_online, done.content)
