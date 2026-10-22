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
        found = self.one(kind='device', model='Teléfono X', capacityGb=128, colour='', line='used')
        self.assertEqual(found['status'], 'exact')
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
