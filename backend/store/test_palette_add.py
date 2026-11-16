"""
B1-SIMREG — adding ONE option to a model's palette never removes anything (`PUT … {"add_option_ids": [...]}`).

The supplier-list assistant used to read the palette and PUT the whole set back: an option somebody added in another
tab between the read and the write was taken out again (a lost update). The additive mode joins under the model's lock.
"""
from django.test import TestCase, TransactionTestCase

from store import model_palette
from store.models import ModelOption
from store.test_device_commerce import _client
from store.test_model_palette import PaletteShop
from store.tests import _p2d_member, _p3_company
from store import device_catalog


class PaletteAddTest(PaletteShop, TestCase):
    def setUp(self):
        self.build('simreg-add')
        self.palette(self.gb128, self.black, self.esim)
        self.url = f'/api/admin/catalog/models/{self.model.pk}/palette/{self.q}'

    def ids(self):
        return set(ModelOption.objects.filter(model=self.model).values_list('option_id', flat=True))

    def test_adding_keeps_what_another_tab_added_meanwhile(self):
        # Another tab adds «LL/A» after this screen read the palette; this screen now adds «J/A».
        model_palette.set_palette(self.model, option_ids=[self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk],
                                  actor=self.staff)
        res = self.api.put(self.url, {'add_option_ids': [self.ja.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.ids(), {self.gb128.pk, self.black.pk, self.esim.pk, self.lla.pk, self.ja.pk})
        self.assertEqual(res.json()['changed']['removed'], 0)

    def test_adding_an_option_already_there_changes_nothing(self):
        res = self.api.put(self.url, {'add_option_ids': [self.esim.pk]}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['changed']['added'], 0)
        self.assertEqual(self.ids(), {self.gb128.pk, self.black.pk, self.esim.pk})

    def test_an_option_of_another_company_is_not_found_and_nothing_changes(self):
        other = _p3_company('simreg-add-b', 'Otra')
        attr = device_catalog.create_attribute(company=other, name='SIM', role='sim')
        foreign = device_catalog.create_option(company=other, attribute_id=attr.pk, label='Dual')
        res = self.api.put(self.url, {'add_option_ids': [foreign.pk]}, format='json')
        self.assertEqual(res.status_code, 404, res.content)
        self.assertEqual(self.ids(), {self.gb128.pk, self.black.pk, self.esim.pk})

    def test_without_products_manage_it_is_refused(self):
        viewer, _ = _p2d_member(self.company, 'simreg_viewer', ['company.view', 'products.view'])
        res = _client(viewer).put(self.url, {'add_option_ids': [self.ja.pk]}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertNotIn(self.ja.pk, self.ids())

    def test_both_modes_at_once_or_a_bad_list_is_400(self):
        for body in ({'add_option_ids': 'x'}, {'option_ids': [self.gb128.pk], 'add_option_ids': [self.ja.pk]}):
            with self.subTest(body=body):
                self.assertEqual(self.api.put(self.url, body, format='json').status_code, 400)


class PaletteAddOwnsItsTransactionTest(PaletteShop, TransactionTestCase):
    """`add_to_palette` locks the model: it must open its own transaction (a TestCase would hide a missing one)."""

    def test_it_can_be_called_without_a_surrounding_transaction(self):
        self.build('simreg-add-tx')
        result = model_palette.add_to_palette(self.model, option_ids=[self.esim.pk], actor=self.staff)
        self.assertEqual(result['added'], 1)
