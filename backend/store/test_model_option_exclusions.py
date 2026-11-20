"""
CATALOG-REFERENCE-01 · PR B — pares imposibles de la paleta de un modelo (`ModelOptionExclusion`).

Lo que estas pruebas fijan:

  * una exclusión dice «estas dos opciones de la paleta no van juntas en este modelo» (p. ej. una SIM que no
    existe en una región); `check_variant` la rechaza (`combination_excluded`) y la matriz del panel no la ofrece;
  * un modelo SIN exclusiones se comporta exactamente como antes: la misma matriz, sin claves nuevas;
  * el escritor único es `model_palette.add_exclusions`: sólo opciones de la paleta del modelo, de atributos
    distintos y de la misma empresa (otra empresa = no encontrada); repetirlo no escribe nada; queda auditado;
  * lo que ya existe no se toca: una variante que ya es esa combinación sigue igual.

Los nombres de prueba son inventados (AGENTS §2).
"""
from django.db import IntegrityError, transaction
from django.test import TestCase

from store import device_catalog, model_palette
from store.models import AdminAuditLog, ModelOptionExclusion, Product
from store.test_model_palette import PaletteShop
from store.tests import _p3_company


class _Shop(PaletteShop):
    def start(self, slug):
        self.build(slug)
        self.palette(self.gb128, self.gb256, self.black, self.blue, self.esim, self.chip, self.lla, self.ja)

    def make(self, *options):
        return device_catalog.create_variant_product(
            self.company, self.model, condition_line='new', option_ids=[o.pk for o in options],
            actor=self.staff, request=None)


class ExclusionRuleTest(_Shop, TestCase):
    def setUp(self):
        self.start('excl-rule')
        model_palette.add_exclusions(self.model, pairs=[(self.chip.pk, self.lla.pk)], actor=self.staff)

    def test_an_excluded_pair_is_refused_when_a_variant_is_created(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            self.make(self.gb128, self.blue, self.chip, self.lla)
        self.assertEqual(caught.exception.code, 'combination_excluded')
        self.assertIn('Chip', str(caught.exception))

    def test_the_other_pairs_of_the_same_options_are_still_allowed(self):
        self.assertIsNotNone(self.make(self.gb128, self.blue, self.chip, self.ja))
        self.assertIsNotNone(self.make(self.gb128, self.blue, self.esim, self.lla))

    def test_check_variant_respects_exclusions_with_a_plain_palette_too(self):
        palette = dict(model_palette.palette_of(self.model))
        with self.assertRaises(device_catalog.CatalogError) as caught:
            model_palette.check_variant(self.model, [self.gb128, self.blue, self.chip, self.lla], palette=palette)
        self.assertEqual(caught.exception.code, 'combination_excluded')

    def test_a_partial_reader_spec_with_both_options_is_refused_too(self):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            model_palette.check_variant(self.model, [self.chip, self.lla], complete=False)
        self.assertEqual(caught.exception.code, 'combination_excluded')
        model_palette.check_variant(self.model, [self.chip], complete=False)

    def test_the_matrix_leaves_the_excluded_combinations_out(self):
        body = model_palette.payload(self.model)
        self.assertEqual(body['summary']['combinations'], 2 * 2 * 2 * 2 - 2 * 2)
        self.assertEqual(len(body['combinations']), 12)
        for combo in body['combinations']:
            self.assertFalse({self.chip.pk, self.lla.pk} <= set(combo['option_ids']), combo)
        self.assertEqual(body['excluded_pairs'], [sorted([self.chip.pk, self.lla.pk])])

    def test_adding_the_same_pair_again_writes_nothing(self):
        before = AdminAuditLog.objects.filter(action='product_model_exclusions_added').count()
        result = model_palette.add_exclusions(self.model, pairs=[(self.lla.pk, self.chip.pk)], actor=self.staff)
        self.assertEqual(result['added'], 0)
        self.assertEqual(ModelOptionExclusion.objects.filter(model=self.model).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(action='product_model_exclusions_added').count(), before)

    def test_adding_is_audited_with_the_company(self):
        self.assertTrue(AdminAuditLog.objects.filter(
            action='product_model_exclusions_added', company=self.company, target_id=str(self.model.pk)).exists())

    def test_the_database_keeps_one_ordered_row_per_pair(self):
        row = ModelOptionExclusion.objects.get(model=self.model)
        self.assertLess(row.option_a_id, row.option_b_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModelOptionExclusion.objects.create(company=self.company, model=self.model,
                                                option_a=row.option_b, option_b=row.option_a)


class ExclusionWriterTest(_Shop, TestCase):
    def setUp(self):
        self.start('excl-writer')

    def code(self, pairs, model=None):
        with self.assertRaises(device_catalog.CatalogError) as caught:
            model_palette.add_exclusions(model or self.model, pairs=pairs, actor=self.staff)
        return caught.exception.code

    def test_two_options_of_one_attribute_are_not_a_pair(self):
        self.assertEqual(self.code([(self.esim.pk, self.chip.pk)]), 'invalid')

    def test_an_option_outside_the_palette_is_refused(self):
        self.assertEqual(self.code([(self.red.pk, self.lla.pk)]), 'option_not_in_palette')

    def test_an_option_of_another_company_is_not_found(self):
        other = _p3_company('excl-writer-b', 'Otra')
        attribute = device_catalog.create_attribute(company=other, name='Región', role='region')
        foreign = device_catalog.create_option(company=other, attribute_id=attribute.pk, label='Z/A')
        self.assertEqual(self.code([(self.chip.pk, foreign.pk)]), 'not_found')
        self.assertFalse(ModelOptionExclusion.objects.exists())

    def test_bad_pairs_are_refused(self):
        for pairs in ([(1,)], ['x'], [(self.chip.pk, self.chip.pk)], None, [(True, False)]):
            self.assertIn(self.code(pairs), ('invalid', 'not_found'), pairs)

    def test_an_existing_variant_on_a_pair_excluded_later_is_left_as_it_is(self):
        product = self.make(self.gb128, self.blue, self.chip, self.lla)
        model_palette.add_exclusions(self.model, pairs=[(self.chip.pk, self.lla.pk)], actor=self.staff)
        product.refresh_from_db()
        self.assertTrue(Product.objects.filter(pk=product.pk, product_model=self.model).exists())
        # Saving it unchanged (price, publication) is still allowed: nothing is invalidated backwards.
        device_catalog.set_variant(product, model_id=self.model.pk, condition_line='new',
                                   option_ids=[self.gb128.pk, self.blue.pk, self.chip.pk, self.lla.pk])


class NoExclusionsBehaveAsBeforeTest(_Shop, TestCase):
    def setUp(self):
        self.start('excl-none')

    def test_the_matrix_is_the_whole_cartesian_product_and_carries_no_new_key(self):
        body = model_palette.payload(self.model)
        self.assertEqual(body['summary']['combinations'], 16)
        self.assertEqual(len(body['combinations']), 16)
        self.assertNotIn('excluded_pairs', body)

    def test_every_pair_is_allowed(self):
        self.assertIsNotNone(self.make(self.gb128, self.blue, self.chip, self.lla))
