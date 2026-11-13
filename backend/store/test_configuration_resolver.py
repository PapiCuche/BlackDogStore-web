"""
CATALOG-COMMERCIAL-01 · A2 — el resolutor de configuraciones (`configuration_resolver`).

Lo que estas pruebas fijan (decisiones del propietario del 2026-11-13: P3/P5, P6, P7, P8 y P9):

  * la lectura es SÓLO LECTURA: nunca crea modelos, opciones, variantes, ofertas, alias ni stock;
  * R-COL: primero la frase más larga dentro de la paleta del modelo, después las opciones de la empresa,
    después los alias; si hay más de una lectura, la configuración es «ambigua»; nunca se parte un color
    compuesto en silencio;
  * un color desconocido es una duda («opcion_desconocida») con sus acciones, nunca el rechazo de la lista;
  * las 14 líneas de entrenamiento (`docs/catalog-commercial/training-lines.json`) se leen tal como las
    escribe el proveedor; los textos de cada campo son fragmentos literales de la línea original;
  * «Blanco» se prueba en dos escenarios aislados: (A) color de la empresa fuera de la paleta del modelo,
    (B) inexistente. Nunca se añade a la paleta del modelo para que pase el fixture;
  * R-4 sigue abierto: las lecturas de 23 y de 22 quedan como pruebas pendientes con nombre propio.

Los datos de prueba (paletas, variantes y alias) se siembran SÓLO aquí, desde el fixture; el código del
resolutor no conoce ningún color, marca ni modelo.
"""
import json
import re
import unittest
from collections import Counter
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase

from store import catalog_aliases, configuration_resolver as resolver, device_catalog, model_palette
from store.models import (
    BranchStock, CatalogAlias, ModelOption, Product, ProductModel, StockMovement, StockUnit, SupplierOffer,
    VariantAttribute, VariantOption,
)
from store.tests import _p2d_member, _p3_company, _prod

FIXTURE = Path(__file__).resolve().parents[2] / 'docs' / 'catalog-commercial' / 'training-lines.json'
ROLES = ('capacity', 'color', 'sim', 'region')

#: Each line as the supplier wrote it, split into the fields a reader hands the resolver. Every text is a
#: literal fragment of the original line (checked by `test_every_field_is_a_fragment_of_its_line`).
LINE_FIELDS = {
    1: dict(model_text='IPH 15', capacity_text='128gb', colour_text='negro'),
    2: dict(model_text='Iph 17 pro', capacity_text='256gb', sim_text='esim', region_text='japan'),
    3: dict(model_text='iph 17 pro max', capacity_text='256gb', sim_text='esim', region_text='lla',
            colour_text='blanco azul naranja'),
    4: dict(model_text='iph 17 pro max', sim_text='chip', colour_text='azul naranja'),
    5: dict(model_text='iph 18 pro', capacity_text='256gb', sim_text='esim', region_text='LLA', colour_text='borgoña'),
    6: dict(model_text='Iph 18 pro', capacity_text='256gb', sim_text='esim', region_text='LLA',
            colour_text='glaciar blanco'),
    7: dict(model_text='iph 18 pro', capacity_text='512gb', sim_text='esim', region_text='japan', colour_text='negro'),
    8: dict(model_text='iph 18 pro max', capacity_text='256gb', sim_text='esim', region_text='LLA',
            colour_text='borgoña'),
    9: dict(model_text='Iph 18 pro max', capacity_text='256gb', sim_text='esim', region_text='LLA',
            colour_text='glaciar negro'),
    10: dict(model_text='Iph 18 pro max', capacity_text='256gb', sim_text='chip', colour_text='borgoña'),
    11: dict(model_text='Iph 18 pro max', capacity_text='256gb', sim_text='chip', colour_text='glaciar negro'),
    12: dict(model_text='iph 18 pro max', capacity_text='512gb', sim_text='esim', region_text='LLA',
             colour_text='borgoña'),
    13: dict(model_text='Iph 18 pro max', sim_text='esim', colour_text='glaciar negro'),
    14: dict(model_text='iph 18 pro max', capacity_text='512gb', sim_text='chip', colour_text='borgoña azul y negro'),
}
#: The fixture's role names → the resolver's.
FIXTURE_ROLE = {'capacity': 'capacity', 'color': 'color', 'sim': 'sim', 'region': 'region'}


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


class TrainingCatalog:
    """
    One company built from a tenant of `training-lines.json`: four role attributes, the options the
    palettes name, each model with its palette and its declared variants, and the company's aliases.
    `compounds=False` builds the «palette without compound colours» reading (no «Glaciar …» option).
    `extra_colours` adds colours to the COMPANY (never to a palette): scenario A of «Blanco».
    """

    SWATCH = '#777777'

    def build_tenant(self, slug, tenant, *, compounds=True, extra_colours=(), model_words=None):
        cache.clear()
        company = _p3_company(slug, f'Empresa {slug}')
        attrs = {
            'capacity': device_catalog.create_attribute(company=company, name='Capacidad', role='capacity', sort_order=0),
            'color': device_catalog.create_attribute(company=company, name='Color', kind='color', role='color', sort_order=1),
            'sim': device_catalog.create_attribute(company=company, name='Tipo de SIM', role='sim', sort_order=2),
            'region': device_catalog.create_attribute(company=company, name='Región', role='region', sort_order=3),
        }
        options = {role: {} for role in ROLES}

        def option(role, label):
            if label not in options[role]:
                options[role][label] = device_catalog.create_option(
                    company=company, attribute_id=attrs[role].pk, label=label,
                    swatch_hex=self.SWATCH if role == 'color' else '')
            return options[role][label]

        def keep(label):
            return compounds or ' ' not in label

        models = {}
        for n, (name, data) in enumerate(tenant['palette'].items()):
            model = device_catalog.create_model(company=company, name=name)
            models[name] = model
            chosen = [option(role, label) for role in ROLES for label in data[role]
                      if role != 'color' or keep(label)]
            model_palette.set_palette(model, option_ids=[o.pk for o in chosen])
            for v, variant in enumerate(data['variants']):
                if not all(keep(variant[role]) for role in variant if role == 'color'):
                    continue
                product = _prod(company, f'{name} {v}', f'{slug}-{n}-{v}', price='1000.00', inventory=0)
                device_catalog.set_variant(product, model_id=model.pk, condition_line='new',
                                           option_ids=[option(role, variant[role]).pk for role in variant])
        for label in extra_colours:
            option('color', label)
        staff, _ = _p2d_member(company, f'{slug}_staff', ['company.view', 'products.view', 'products.manage'])
        for role, pairs in tenant.get('aliases', {}).items():
            for text, label in pairs.items():
                target = options[FIXTURE_ROLE[role]].get(label)
                if target is None or resolver.phrase_key(text) == resolver.phrase_key(label):
                    # «esim» → «eSIM» or «borgona» → «Borgoña» fold alike: no alias is needed (nor allowed).
                    continue
                catalog_aliases.create_alias(company=company, scope='option', alias=text, option_id=target.pk,
                                             actor=staff)
        for word, replacement in (model_words or {}).items():
            catalog_aliases.create_alias(company=company, scope='model_word', alias=word, replacement=replacement,
                                         actor=staff)
        return company, models, options, staff


def resolve_lines(company, numbers=None):
    vocabulary = resolver.Vocabulary(company)
    out = {}
    for n, fields in LINE_FIELDS.items():
        if numbers and n not in numbers:
            continue
        out[n] = resolver.resolve_configuration(vocabulary, **fields)
    return out


def statuses(result):
    return [c['status'] for c in result['configurations']]


class TrainingLinesTest(TrainingCatalog, TestCase):
    """The 14 lines of the owner's list, read through the company's data alone."""

    def setUp(self):
        self.fixture = load_fixture()
        # Scenario A of «Blanco»: a colour the company has, outside the palette of «iPhone 17 Pro Max».
        self.company, self.models, self.options, self.staff = self.build_tenant(
            'a2-lineas', self.fixture['tenant_a'], extra_colours=['Blanco'], model_words={'iph': 'iphone'})

    def test_every_field_is_a_fragment_of_its_line(self):
        texts = {line['n']: line['text'] for line in self.fixture['lines']}
        self.assertEqual(sorted(texts), sorted(LINE_FIELDS))
        for n, fields in LINE_FIELDS.items():
            for value in fields.values():
                self.assertIn(value.lower(), texts[n].lower(), (n, value))

    def test_each_candidate_gets_the_expected_status_and_missing_roles(self):
        results = resolve_lines(self.company)
        for line in self.fixture['lines']:
            n = line['n']
            with self.subTest(line=n):
                result = results[n]
                self.assertEqual(result['model']['model_name'], line['model'])
                got = result['configurations']
                self.assertEqual(len(got), len(line['candidates']), (n, got))
                for config, expected in zip(got, line['candidates']):
                    self.assertEqual(config['status'], expected['expected'], (n, config))
                    if 'missing' in expected:
                        self.assertEqual(sorted(config['missing']), sorted(expected['missing']), (n, config))

    def test_nineteen_configurations_four_five_two_eight(self):
        counts = Counter(s for r in resolve_lines(self.company).values() for s in statuses(r))
        self.assertEqual(sum(counts.values()), 19)
        self.assertEqual(counts, Counter({'variante_existente': 4, 'combinacion_valida_sin_variante': 5,
                                          'fuera_de_paleta': 2, 'incompleta': 8}))

    def test_complete_configurations_are_the_existing_and_the_valid_ones(self):
        for result in resolve_lines(self.company).values():
            for config in result['configurations']:
                self.assertEqual(config['complete'], config['status'] in (
                    'variante_existente', 'combinacion_valida_sin_variante'), config)

    def test_line_11_is_incomplete_and_the_existing_variant_is_only_a_candidate(self):
        """H3: the variant with LL/A exists; the line does not say the region, so it is never chosen."""
        config = resolve_lines(self.company, {11})[11]['configurations'][0]
        self.assertEqual((config['status'], config['missing'], config['product_id']), ('incompleta', ['region'], None))
        self.assertEqual(len(config['candidates']), 1)
        held = Product.objects.get(pk=config['candidates'][0]['product_id'])
        self.assertIn('LL/A', [o.label for o in device_catalog.variant_options_of(held)])

    def test_existing_and_valid_carry_what_the_screen_needs(self):
        results = resolve_lines(self.company, {5, 12})
        existing = results[5]['configurations'][0]
        self.assertIsNotNone(existing['product_id'])
        self.assertFalse(existing['draft_pending'])
        valid = results[12]['configurations'][0]
        self.assertIsNone(valid['product_id'])
        labels = sorted(VariantOption.objects.get(pk=pk).label for pk in valid['option_ids'])
        self.assertEqual(labels, sorted(['512 GB', 'Borgoña', 'eSIM', 'LL/A']))

    def test_every_alias_that_was_applied_is_reported(self):
        result = resolve_lines(self.company, {5})[5]
        said = {(r['role'], r['text'], r['label'], r['via']) for r in result['readings']}
        self.assertIn(('region', 'LLA', 'LL/A', 'alias'), said)
        self.assertIn(('color', 'borgoña', 'Borgoña', 'label'), said)
        self.assertIn(('capacity', '256gb', '256 GB', 'capacity'), said)
        self.assertEqual(result['model']['via'], 'model_word')

    def test_amounts_quantities_and_identifiers_never_appear(self):
        payload = json.dumps(resolve_lines(self.company), ensure_ascii=False, default=str)
        for n, line in enumerate(self.fixture['lines'], start=1):
            self.assertNotIn(str(line['amount']), payload)
        for key in ('quantity', 'cost', 'price', 'imei', 'amount'):
            self.assertNotIn(f'"{key}', payload)

    def test_reading_writes_nothing(self):
        def snapshot():
            return [m.objects.count() for m in (Product, ProductModel, VariantOption, ModelOption, CatalogAlias,
                                                StockUnit, BranchStock, StockMovement, SupplierOffer)]

        before = snapshot()
        enabled = Product.objects.exclude(sale_enabled_at=None).count()
        with self.assertNumQueries(self._queries_for_all_lines()):
            resolve_lines(self.company)
        self.assertEqual(snapshot(), before)
        self.assertEqual(Product.objects.exclude(sale_enabled_at=None).count(), enabled)

    def _queries_for_all_lines(self):
        # Measured once: the bound is what matters — it does not grow with the number of lines of one model.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            resolve_lines(self.company)
        self.assertLessEqual(len(captured), 40, [q['sql'][:120] for q in captured])
        return len(captured)


class BlancoScenarioTest(TrainingCatalog, TestCase):
    """H4 / P5: «blanco azul naranja» depends on whether the COMPANY knows «Blanco»; never on the code."""

    def test_b_blanco_unknown_to_the_company_is_one_ambiguous_configuration_with_azul(self):
        fixture = load_fixture()
        company, *_ = self.build_tenant('a2-blanco-b', fixture['tenant_a'], model_words={'iph': 'iphone'})
        self.assertFalse(VariantOption.objects.filter(company=company, label__iexact='blanco').exists())
        result = resolve_lines(company, {3})[3]
        self.assertEqual(statuses(result), ['ambigua', 'combinacion_valida_sin_variante'])
        ambiguous = result['configurations'][0]
        self.assertEqual(ambiguous['colour_text'], 'blanco azul')
        readings = [[part['text'] for part in reading] for reading in ambiguous['doubts'][0]['readings']]
        self.assertIn(['blanco azul'], readings)
        self.assertIn(['blanco', 'azul'], readings)
        self.assertEqual(ambiguous['doubts'][0]['actions'], ['add_to_palette', 'alias_of', 'keep_pending'])

    def test_a_blanco_of_the_company_outside_the_palette_is_outside(self):
        fixture = load_fixture()
        company, models, options, _ = self.build_tenant('a2-blanco-a', fixture['tenant_a'], extra_colours=['Blanco'],
                                                        model_words={'iph': 'iphone'})
        self.assertNotIn(options['color']['Blanco'].pk,
                         ModelOption.objects.filter(model=models['iPhone 17 Pro Max']).values_list('option_id', flat=True))
        self.assertEqual(statuses(resolve_lines(company, {3})[3]),
                         ['fuera_de_paleta', 'variante_existente', 'combinacion_valida_sin_variante'])


class CompoundlessPaletteTest(TrainingCatalog, TestCase):
    """The palette WITHOUT «Glaciar …»: the four compound phrases become ambiguous, never two colours."""

    def test_nineteen_with_four_ambiguous(self):
        fixture = load_fixture()
        company, *_ = self.build_tenant('a2-sin-compuestos', fixture['tenant_a'], compounds=False,
                                        extra_colours=['Blanco'], model_words={'iph': 'iphone'})
        results = resolve_lines(company)
        counts = Counter(s for r in results.values() for s in statuses(r))
        self.assertEqual(sum(counts.values()), 19)
        self.assertEqual(counts['ambigua'], 4)
        self.assertEqual([n for n, r in results.items() if 'ambigua' in statuses(r)], [6, 9, 11, 13])
        self.assertEqual(counts, Counter({'variante_existente': 4, 'combinacion_valida_sin_variante': 3,
                                          'fuera_de_paleta': 2, 'incompleta': 6, 'ambigua': 4}))

    @unittest.skip('R4_SPLIT_READING_23: lectura «glaciar X» = dos colores, pendiente de P1 del propietario.')
    def test_R4_SPLIT_READING_23(self):
        """Would hold if the company's palettes had «Glaciar» as a colour of its own (DATA, never code)."""

    @unittest.skip('R4_OWNER_COUNT_22: el «22» del encargo no se explica sin P1, P2 y P4 del propietario.')
    def test_R4_OWNER_COUNT_22(self):
        """Two readings give 22 (see the A2 plan, §2.2); none is adopted until the owner answers."""


class TenancyTest(TrainingCatalog, TestCase):
    def test_no_line_of_company_a_resolves_to_company_b(self):
        fixture = load_fixture()
        a, *_ = self.build_tenant('a2-ten-a', fixture['tenant_a'], extra_colours=['Blanco'], model_words={'iph': 'iphone'})
        b, b_models, *_ = self.build_tenant('a2-ten-b', fixture['tenant_b'])
        b_ids = set(VariantOption.objects.filter(company=b).values_list('pk', flat=True))
        for n, result in resolve_lines(a).items():
            for config in result['configurations']:
                self.assertFalse(set(config['option_ids']) & b_ids, n)
        # In B there is no «iph» word nor «LLA» alias: the model is unknown and the region an unknown option.
        b_result = resolve_lines(b, {8})[8]
        self.assertEqual(b_result['model']['status'], 'unknown')
        exact = resolver.resolve_configuration(resolver.Vocabulary(b), model_text='iPhone 18 Pro Max',
                                               capacity_text='256gb', sim_text='esim', region_text='LLA',
                                               colour_text='borgoña')
        self.assertEqual(exact['model']['model_id'], b_models['iPhone 18 Pro Max'].pk)
        self.assertEqual(statuses(exact), ['opcion_desconocida'])
        self.assertEqual(exact['configurations'][0]['doubts'][0]['role'], 'region')


class ColourRunTest(TestCase):
    """R-COL with small, invented data: the rule is the data's, not a list of colour words."""

    def setUp(self):
        cache.clear()
        self.company = _p3_company('a2-rcol', 'R-COL')
        self.colour = device_catalog.create_attribute(company=self.company, name='Tono', kind='color', role='color')
        self.size = device_catalog.create_attribute(company=self.company, name='Memoria', role='capacity')
        self.cap = device_catalog.create_option(company=self.company, attribute_id=self.size.pk, label='64 GB')
        self.opt = {label: device_catalog.create_option(company=self.company, attribute_id=self.colour.pk,
                                                        label=label, swatch_hex='#123456')
                    for label in ('Lunar gris', 'Gris', 'Verde', 'Coral', 'Arena')}
        self.model = device_catalog.create_model(company=self.company, name='Equipo Z')

    def palette(self, *labels):
        model_palette.set_palette(self.model, option_ids=[self.cap.pk] + [self.opt[l].pk for l in labels])

    def colours(self, text):
        result = resolver.resolve_configuration(resolver.Vocabulary(self.company), model_text='Equipo Z',
                                                capacity_text='64gb', colour_text=text)
        return [(c['colour_text'], c['status']) for c in result['configurations']]

    def test_the_longest_phrase_of_the_palette_is_one_colour(self):
        self.palette('Lunar gris', 'Gris', 'Verde')
        self.assertEqual(self.colours('lunar gris'), [('lunar gris', 'combinacion_valida_sin_variante')])

    def test_a_compound_outside_the_palette_is_read_whole_never_as_the_palette_colour(self):
        # «Gris» is in the palette, «Lunar gris» only in the company. Reading «Gris» would leave «lunar» unknown:
        # the one VALID reading is the company's colour, outside this palette. Never «Gris» in silence.
        self.palette('Gris', 'Verde')
        self.assertEqual(self.colours('lunar gris'), [('lunar gris', 'fuera_de_paleta')])

    def test_two_valid_readings_are_one_ambiguous_configuration(self):
        # «Lunar» + «Gris» (two colours) and «Lunar gris» (one) are both real: the person decides.
        device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Lunar',
                                     swatch_hex='#222222')
        self.palette('Gris', 'Verde')
        result = resolver.resolve_configuration(resolver.Vocabulary(self.company), model_text='Equipo Z',
                                                capacity_text='64gb', colour_text='lunar gris')
        self.assertEqual([c['status'] for c in result['configurations']], ['ambigua'])
        readings = [[p['label'] for p in r] for r in result['configurations'][0]['doubts'][0]['readings']]
        self.assertEqual(sorted(readings), sorted([['Lunar gris'], ['Lunar', 'Gris']]))

    def test_an_unknown_word_glued_to_a_colour_is_ambiguous(self):
        self.palette('Gris', 'Verde')
        self.assertEqual(self.colours('humo gris verde'), [('humo gris', 'ambigua'),
                                                            ('verde', 'combinacion_valida_sin_variante')])

    def test_an_isolated_unknown_word_is_an_unknown_option_with_its_actions(self):
        self.palette('Gris', 'Verde')
        result = resolver.resolve_configuration(resolver.Vocabulary(self.company), model_text='Equipo Z',
                                                capacity_text='64gb', colour_text='verde, humo')
        self.assertEqual([c['status'] for c in result['configurations']],
                         ['combinacion_valida_sin_variante', 'opcion_desconocida'])
        doubt = result['configurations'][1]['doubts'][0]
        self.assertEqual((doubt['role'], doubt['text'], doubt['reason']), ('color', 'humo', 'unknown'))
        self.assertEqual(doubt['actions'], ['add_to_palette', 'alias_of', 'keep_pending'])

    def test_two_unknown_words_are_one_ambiguous_configuration(self):
        self.palette('Gris')
        self.assertEqual(self.colours('humo niebla'), [('humo niebla', 'ambigua')])

    def test_explicit_separators_always_split(self):
        self.palette('Gris', 'Verde', 'Coral')
        for text in ('gris y verde', 'gris, verde', 'gris / verde', 'gris + verde'):
            with self.subTest(text):
                self.assertEqual([s for _t, s in self.colours(text)], ['combinacion_valida_sin_variante'] * 2)

    def test_two_palette_colours_side_by_side_are_two(self):
        self.palette('Gris', 'Verde', 'Coral')
        self.assertEqual(len(self.colours('verde coral')), 2)

    def test_a_company_colour_outside_the_palette_is_outside(self):
        self.palette('Gris')
        self.assertEqual(self.colours('arena'), [('arena', 'fuera_de_paleta')])

    def test_an_alias_reads_after_labels_and_resolves_the_compound(self):
        self.palette('Lunar gris', 'Verde')
        staff, _ = _p2d_member(self.company, 'a2-rcol-staff', ['products.view', 'products.manage'])
        catalog_aliases.create_alias(company=self.company, scope='option', alias='gris lunar',
                                     option_id=self.opt['Lunar gris'].pk, actor=staff)
        self.assertEqual(self.colours('gris lunar'), [('gris lunar', 'combinacion_valida_sin_variante')])

    def test_accents_and_case_never_matter(self):
        model_palette.set_palette(self.model, option_ids=[self.cap.pk, self.opt['Verde'].pk])
        option = device_catalog.create_option(company=self.company, attribute_id=self.colour.pk, label='Café',
                                              swatch_hex='#654321')
        model_palette.set_palette(self.model, option_ids=[self.cap.pk, option.pk])
        self.assertEqual(self.colours('CAFE'), [('CAFE', 'combinacion_valida_sin_variante')])

    def test_too_many_colours_is_a_row_error_not_a_crash(self):
        self.palette('Gris')
        result = resolver.resolve_configuration(resolver.Vocabulary(self.company), model_text='Equipo Z',
                                                colour_text=', '.join(['gris'] * 40))
        self.assertTrue(result['error'])
        self.assertEqual(result['configurations'], [])


class NormalisationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('a2-norm', 'Normal')
        self.size = device_catalog.create_attribute(company=self.company, name='Memoria', role='capacity')
        self.labels = {label: device_catalog.create_option(company=self.company, attribute_id=self.size.pk, label=label)
                       for label in ('256 GB', '1 TB', '64GB')}
        self.model = device_catalog.create_model(company=self.company, name='Tableta Ñandú Pro')
        self.vocabulary = resolver.Vocabulary(self.company)

    def capacity(self, text):
        result = resolver.resolve_configuration(self.vocabulary, model_text='Tableta Ñandú Pro', capacity_text=text)
        options = result['configurations'][0]['options']
        return [o['label'] for o in options if o['role'] == 'capacity']

    def test_capacity_without_spaces_or_case(self):
        for text, label in (('256gb', '256 GB'), ('256 GB', '256 GB'), ('256 gb', '256 GB'), ('1tb', '1 TB'),
                            ('1 TB', '1 TB'), ('64 gb', '64GB')):
            with self.subTest(text):
                self.assertEqual(self.capacity(text), [label])

    def test_a_bare_number_reads_in_the_capacity_field(self):
        self.assertEqual(self.capacity('256'), ['256 GB'])

    def test_never_a_conversion_between_units(self):
        result = resolver.resolve_configuration(self.vocabulary, model_text='Tableta Ñandú Pro', capacity_text='1024gb')
        self.assertEqual(result['configurations'][0]['status'], 'opcion_desconocida')

    def test_model_name_without_accents_case_or_extra_spaces(self):
        result = resolver.resolve_configuration(self.vocabulary, model_text='  tableta   NANDU pro ')
        self.assertEqual((result['model']['status'], result['model']['model_id']), ('exact', self.model.pk))


class ModelResolutionTest(TestCase):
    def setUp(self):
        cache.clear()
        self.company = _p3_company('a2-model', 'Modelos')
        self.staff, _ = _p2d_member(self.company, 'a2-model-staff', ['products.view', 'products.manage'])
        self.region = device_catalog.create_attribute(company=self.company, name='Zona', role='region')
        self.sim = device_catalog.create_attribute(company=self.company, name='Línea SIM', role='sim')
        self.zone = device_catalog.create_option(company=self.company, attribute_id=self.region.pk, label='ZN/1')
        self.esim = device_catalog.create_option(company=self.company, attribute_id=self.sim.pk, label='eSIM')
        self.pro = device_catalog.create_model(company=self.company, name='Fono 9 Pro')
        self.pro_max = device_catalog.create_model(company=self.company, name='Fono 9 Pro Max')
        catalog_aliases.create_alias(company=self.company, scope='model_word', alias='fon', replacement='fono',
                                     actor=self.staff)
        catalog_aliases.create_alias(company=self.company, scope='option', alias='zona1', option_id=self.zone.pk,
                                     actor=self.staff)

    def model(self, text):
        return resolver.resolve_model(resolver.Vocabulary(self.company), text)

    def test_a_word_alias_reads_abbreviated_names(self):
        for text in ('FON 9 PRO MAX', 'fon 9 pro max', 'Fon9 pro max'):
            with self.subTest(text):
                found = self.model(text)
                self.assertEqual((found['status'], found['model_id']), ('model_word', self.pro_max.pk))

    def test_the_exact_name_always_wins(self):
        catalog_aliases.create_alias(company=self.company, scope='model', alias='F9P', model_id=self.pro.pk,
                                     actor=self.staff)
        self.assertEqual(self.model('Fono 9 Pro Max')['model_id'], self.pro_max.pk)
        self.assertEqual(self.model('f9p')['model_id'], self.pro.pk)
        self.assertEqual(self.model('f9p')['status'], 'alias')

    def test_left_over_words_are_an_unknown_model_never_another(self):
        found = self.model('fon 9 pro ultra')
        self.assertEqual((found['status'], found['model_id']), ('unknown', None))

    def test_options_inside_the_name_go_to_review_and_create_nothing(self):
        before = ProductModel.objects.count()
        found = self.model('Fono 9 Pro Max (eSIM zona1)')
        self.assertEqual((found['status'], found['model_id']), ('name_with_options', self.pro_max.pk))
        self.assertEqual(sorted((e['role'], e['label']) for e in found['extracted']),
                         [('region', 'ZN/1'), ('sim', 'eSIM')])
        self.assertEqual(ProductModel.objects.count(), before)

    def test_two_models_for_one_text_is_ambiguous(self):
        # The company said «fon 9 pro» is the Pro Max; the word «fon» reads the same text as the Pro.
        catalog_aliases.create_alias(company=self.company, scope='model', alias='fon 9 pro', model_id=self.pro_max.pk,
                                     actor=self.staff)
        found = self.model('fon 9 pro')
        self.assertEqual((found['status'], found['model_id']), ('ambiguous', None))
        self.assertEqual(sorted(c['id'] for c in found['candidates']), sorted([self.pro.pk, self.pro_max.pk]))
        result = resolver.resolve_configuration(resolver.Vocabulary(self.company), model_text='fon 9 pro')
        self.assertEqual([c['status'] for c in result['configurations']], ['ambigua'])

    def test_an_unknown_model_is_one_unknown_configuration(self):
        result = resolver.resolve_configuration(resolver.Vocabulary(self.company), model_text='Otra cosa 3',
                                                colour_text='verde, azul')
        self.assertEqual([c['status'] for c in result['configurations']], ['modelo_desconocido'])


class NoVocabularyInCodeTest(TestCase):
    """The engine names no colour, maker, shop or model (AGENTS §2): everything is the company's data."""

    def test_the_resolver_and_the_alias_service_hold_no_commercial_words(self):
        words = r'\b(negro|blanco|azul|naranja|plata|borgo[nñ]a|glaciar|black|white|blue|apple|iphone|ipad|samsung|' \
                r'galaxy|black dog|lla|japan|japon)\b'
        for module in (resolver, catalog_aliases):
            source = Path(module.__file__).read_text(encoding='utf-8')
            code = re.sub(r'(?s)""".*?"""', '', source)
            code = '\n'.join(line.split('#', 1)[0] for line in code.splitlines())
            self.assertIsNone(re.search(words, code, re.I), (module.__name__, re.search(words, code, re.I)))

    def test_the_ai_never_reads_aliases(self):
        from store import inventory_ai

        source = Path(inventory_ai.__file__).read_text(encoding='utf-8')
        for word in ('CatalogAlias', 'catalog_aliases', 'configuration_resolver', 'Vocabulary'):
            self.assertNotIn(word, source)
