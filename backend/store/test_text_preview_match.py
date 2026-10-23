"""
INV-TEXT-02 — reconciling what the local recognizer read with this company's
REAL variants. Read-only: it answers which product a candidate is (one, several
to choose from, or none and why) and writes nothing. Company-scoped; costs and
quantities never travel in it.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from store import device_catalog
from store.models import AdminAuditLog, Product
from store.test_device_commerce import DeviceShop
from store.tests import _p2d_member, _p3_company, _prod

URL = '/api/admin/inventory/text-preview/match/'


class _Match(DeviceShop, TestCase):
    def setUp(self):
        self.build('txt-match')
        self.client_ = APIClient()
        self.client_.force_authenticate(self.staff)
        self.cable = _prod(self.company, 'Cable USB-C 1m', 'cable-usbc-1m', price='45.00', inventory=0)

    def match(self, *candidates, client=None):
        return (client or self.client_).post(URL, {'candidates': list(candidates)}, format='json')

    def one(self, **candidate):
        response = self.match({'key': 'c1', **candidate})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['matches']['c1']


class MatchTest(_Match):
    def test_model_capacity_colour_and_line_name_one_variant(self):
        found = self.one(kind='device', model='Teléfono X', capacityGb=256, colour='negro', line='new')
        self.assertEqual(found['status'], 'exact')
        self.assertEqual([o['product_id'] for o in found['options']], [self.new256.pk])

    def test_without_the_colour_several_fit_and_none_is_chosen(self):
        Product.objects.filter(pk=self.new128.pk)  # 128 GB Negro (new)
        found = self.one(kind='device', model='Teléfono X', capacityGb=None, colour='negro', line='new')
        self.assertEqual(found['status'], 'ambiguous')
        self.assertEqual(sorted(o['product_id'] for o in found['options']), sorted([self.new128.pk, self.new256.pk]))

    def test_an_unknown_model_or_option_says_why(self):
        found = self.one(kind='device', model='Teléfono Z', capacityGb=128, colour='negro', line='new')
        self.assertEqual(found['status'], 'none')
        self.assertIn('modelo', found['reason'].lower())
        found = self.one(kind='device', model='Teléfono X', capacityGb=1024, colour='negro', line='new')
        self.assertEqual(found['status'], 'none')
        self.assertIn('1024 GB', found['reason'])

    def test_the_line_matters(self):
        found = self.one(kind='device', model='Teléfono X', capacityGb=128, colour='azul', line='new')
        self.assertEqual(found['status'], 'none')
        found = self.one(kind='device', model='Teléfono X', capacityGb=128, colour='azul', line='used')
        self.assertEqual([o['product_id'] for o in found['options']], [self.used128.pk])

    def test_an_accessory_by_its_name(self):
        found = self.one(kind='accessory', model='Cable USB-C 1m', line='accessory')
        self.assertEqual((found['status'], [o['product_id'] for o in found['options']]), ('exact', [self.cable.pk]))
        found = self.one(kind='accessory', model='cable usb-c', line='accessory')
        self.assertEqual(found['status'], 'ambiguous')

    def test_nothing_is_written_and_nothing_is_audited(self):
        before = (Product.objects.count(), AdminAuditLog.objects.count())
        self.match({'key': 'a', 'kind': 'device', 'model': 'Teléfono X', 'capacityGb': 256, 'colour': 'negro',
                    'line': 'new'})
        self.assertEqual((Product.objects.count(), AdminAuditLog.objects.count()), before)

    def test_options_say_only_what_a_catalogue_reader_sees(self):
        found = self.one(kind='device', model='Teléfono X', capacityGb=256, colour='negro', line='new')
        self.assertEqual(set(found['options'][0]), {'product_id', 'name', 'condition_line', 'options'})


class GuardTest(_Match):
    def test_another_companys_variants_never_match(self):
        other = _p3_company('txt-other', 'Otra')
        device_catalog.create_model(company=other, name='Teléfono Y')
        found = self.one(kind='device', model='Teléfono Y', capacityGb=128, colour='', line='new')
        self.assertEqual(found['status'], 'none')

    def test_it_needs_products_view(self):
        nobody, _ = _p2d_member(self.company, 'txt-nobody', ['company.view'])
        client = APIClient()
        client.force_authenticate(nobody)
        self.assertEqual(self.match({'key': 'a', 'model': 'x'}, client=client).status_code, 403)
        self.assertEqual(APIClient().post(URL, {}, format='json').status_code, 401)

    def test_a_bad_request_is_400_and_the_batch_is_bounded(self):
        for body in ({}, {'candidates': 'x'}, {'candidates': [1]}, {'candidates': [{'key': 'a' * 80}]},
                     {'candidates': [{'key': f'k{i}', 'model': 'x'} for i in range(301)]},
                     {'candidates': [{'key': 'a', 'capacityGb': 'mucho'}]}):
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.client_.post(URL, body, format='json').status_code, 400)

    def test_costs_and_identifiers_are_refused_not_carried(self):
        response = self.match({'key': 'a', 'model': 'Teléfono X', 'supplierCost': '2300', 'imei': '490154203237518'})
        self.assertEqual(response.status_code, 400)


class ReviewFixesTest(_Match):
    def test_a_subset_of_options_is_never_exact(self):
        # Only «Teléfono X 128 GB Azul usado» is used: a candidate with the capacity only fits it, but is not it.
        found = self.one(kind='device', model='Teléfono X', capacityGb=128, colour='', line='used')
        self.assertEqual(found['status'], 'ambiguous')
        self.assertEqual([o['product_id'] for o in found['options']], [self.used128.pk])

    def test_a_capacity_is_looked_up_in_the_capacity_attribute_only(self):
        ram = device_catalog.create_attribute(company=self.company, name='RAM')
        device_catalog.create_option(company=self.company, attribute_id=ram.pk, label='512 GB')
        found = self.one(kind='device', model='Teléfono X', capacityGb=512, colour='negro', line='new')
        self.assertEqual(found['status'], 'none')
        self.assertIn('512 GB', found['reason'])

    def test_fields_that_are_not_used_are_refused(self):
        for field in ('ramGb', 'sim', 'region'):
            with self.subTest(field=field):
                response = self.match({'key': 'a', 'model': 'Teléfono X', field: 'x'})
                self.assertEqual(response.status_code, 400)

    def test_a_repeated_key_is_refused(self):
        response = self.match({'key': 'a', 'model': 'x'}, {'key': 'a', 'model': 'y'})
        self.assertEqual(response.status_code, 400)

    def test_the_same_candidate_many_times_costs_the_same_queries(self):
        one = [{'key': 'k0', 'kind': 'device', 'model': 'Teléfono X', 'capacityGb': 256, 'colour': 'negro'}]
        many = [{**one[0], 'key': f'k{i}'} for i in range(50)]
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as single:
            self.match(*one)
        with CaptureQueriesContext(connection) as repeated:
            self.match(*many)
        self.assertLessEqual(len(repeated), len(single) + 2)


class FinalReviewTest(_Match):
    def test_a_device_without_a_capacity_is_never_exact(self):
        bare = _prod(self.company, 'Teléfono X base', 'tx-base', price='1.00', inventory=0)
        device_catalog.set_variant(bare, model_id=self.model.pk, condition_line='new', option_ids=[])
        found = self.one(kind='device', model='Teléfono X', capacityGb=None, colour='', line='new')
        self.assertNotEqual(found['status'], 'exact')

    def test_many_different_candidates_stay_within_a_bounded_number_of_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        rows = [{'key': f'k{i}', 'kind': 'device', 'model': 'Teléfono X', 'capacityGb': 128 if i % 2 else 256,
                 'colour': ['negro', 'azul', ''][i % 3], 'line': ['new', 'used'][i % 2]} for i in range(12)]
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.match(*rows).status_code, 200)
        self.assertLess(len(queries), 80, len(queries))


class IdentifierInModelTest(_Match):
    """Pre-merge review: an identifier hidden in the allowed `model` field is refused, never echoed."""

    def test_a_model_with_an_imei_or_a_serial_is_not_searched_nor_echoed(self):
        models = ('iPhone 13 356789012345678', 'iPhone 13 IMEI 3567 8901 2345 678', 'iPhone 13 NS: F2LXK9ABCD',
                  'iPhone 13 S/N F2LXK9ABCD', 'iPhone 13 serie ABCDEFGH', 'iPhone 13 F2LX-K9AB-CD',
                  'iPhone 13 F2LXKABCDE', 'iPhone 13 N.S. F2LXKABC', 'iPhone 13 SN 4567m')
        response = self.match(*[{'key': str(i), 'kind': 'device', 'line': 'used', 'model': model, 'capacityGb': 128}
                                for i, model in enumerate(models)])
        self.assertEqual(response.status_code, 200)
        for i in range(len(models)):
            self.assertEqual(response.data['matches'][str(i)]['status'], 'none')
            self.assertIn('identificador', response.data['matches'][str(i)]['reason'])
        for value in ('F2LX', '5678', 'ABCDEFGH'):
            self.assertNotIn(value, str(response.data))

    def test_real_names_are_not_taken_for_identifiers_and_one_bad_row_does_not_fail_the_list(self):
        names = ('Funda Snoopy iPhone 15', 'Galaxy Z Flip6 Snapdragon', 'Cable Thunderbolt-4', 'Cargador 35W-USB-C',
                 'iPad Air 10.9 2022', 'Apple Watch Serie 45mm', 'Apple Watch Serie 9', 'Galaxy S24 Ultra',
                 'AirPods Pro 2', 'Cargador USB-C 20W', 'iPad Air M2 11', 'Redmi Note 13 Pro+ 5G', 'MacBook Pro M3 Max 14',
                 'Funda SN-Design')
        rows = [{'key': str(i), 'kind': 'accessory', 'line': 'accessory', 'model': name} for i, name in enumerate(names)]
        rows.append({'key': 'bad', 'kind': 'device', 'line': 'used', 'model': 'iPhone 13 356789012345678'})
        response = self.match(*rows)
        self.assertEqual(response.status_code, 200)
        for i, name in enumerate(names):
            with self.subTest(name=name):
                self.assertNotIn('identificador', response.data['matches'][str(i)]['reason'])
        self.assertEqual(response.data['matches']['bad']['status'], 'none')
