"""CATALOG-REFERENCE-01 · PR A: the manufacturers' reference catalog is data, checked without a database.

`docs/adr-catalog-reference.md`. The files live in `backend/catalog_reference/`; the validator in
`store/catalog_reference.py` names no maker, model or colour (AGENTS §2).
"""
import copy
import re
from pathlib import Path

from django.test import SimpleTestCase

from store import catalog_reference as ref

DATA_DIR = Path(__file__).resolve().parent.parent / 'catalog_reference'


def model(**over):
    base = {
        'key': 'phone-1-pro', 'name': 'Phone 1 Pro', 'line': 'pro', 'generation': '1', 'announced': '2020-01-02',
        'capacities_gb': [128, 256],
        'colors': [{'key': 'grey', 'label_en': 'Grey', 'label_es': 'Gris',
                    'official': {'en': 'Grey', 'es_419': 'Gris', 'es_es': 'gris'}}],
        'sim': [{'key': 'nano-sim-esim', 'regions': 'default', 'config': 'nano-SIM + eSIM'}],
        'model_numbers': [{'number': 'X1001', 'regions': None}],
        'sources': ['https://support.maker.test/specs/1'],
        'verified_at': '2026-11-20', 'completeness': 'complete', 'missing': [],
    }
    base.update(over)
    return base


def dataset(*models):
    return {'schema_version': 1, 'manufacturer': 'Maker', 'family': 'Phone', 'dataset_version': '2026-11-20',
            'official_source_hosts': ['maker.test'], 'models': list(models) or [model()]}


class ValidatorTest(SimpleTestCase):
    def errors(self, data):
        return ref.validate(data)

    def assertRejected(self, data, fragment):
        errors = self.errors(data)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_a_well_formed_dataset_has_no_errors(self):
        self.assertEqual(self.errors(dataset()), [])

    def test_the_header_is_required(self):
        data = dataset()
        del data['official_source_hosts']
        self.assertRejected(data, 'official_source_hosts')
        self.assertRejected({**dataset(), 'schema_version': 2}, 'schema_version')

    def test_keys_names_and_model_numbers_are_unique_in_the_file(self):
        self.assertRejected(dataset(model(), model(name='Other')), 'duplicate key')
        self.assertRejected(dataset(model(), model(key='phone-2')), 'duplicate name')
        self.assertRejected(dataset(model(), model(key='phone-2', name='Phone 2')), 'X1001')

    def test_a_key_is_a_lowercase_slug(self):
        self.assertRejected(dataset(model(key='Phone 1')), 'key')

    def test_the_line_comes_from_the_closed_list(self):
        self.assertRejected(dataset(model(line='ultra-mega')), 'line')

    def test_capacities_are_positive_unique_and_ascending(self):
        for bad in ([0, 64], [64, 64], [256, 128], ['64']):
            self.assertRejected(dataset(model(capacities_gb=bad)), 'capacities_gb')

    def test_each_colour_has_a_key_and_both_labels_and_keys_are_unique_in_the_model(self):
        colour = model()['colors'][0]
        self.assertRejected(dataset(model(colors=[{**colour, 'label_es': ''}])), 'label_es')
        self.assertRejected(dataset(model(colors=[colour, dict(colour)])), 'duplicate colour')

    def test_a_sim_entry_names_its_configuration(self):
        self.assertRejected(dataset(model(sim=[{'key': 'x', 'regions': 'default', 'config': ''}])), 'sim')

    def test_sources_are_https_pages_of_the_official_hosts_only(self):
        self.assertRejected(dataset(model(sources=['https://blog.example/specs'])), 'official')
        self.assertRejected(dataset(model(sources=['http://support.maker.test/specs'])), 'official')
        self.assertRejected(dataset(model(sources=[])), 'sources')
        self.assertEqual(self.errors(dataset(model(sources=['https://www.maker.test/x']))), [])

    def test_dates_are_iso(self):
        self.assertRejected(dataset(model(verified_at='20/11/2026')), 'verified_at')
        self.assertRejected(dataset(model(announced='2020')), 'announced')
        self.assertEqual(self.errors(dataset(model(announced=None))), [])

    def test_complete_means_nothing_missing_and_every_option_kind_present(self):
        self.assertRejected(dataset(model(missing=['colours unknown'])), 'complete')
        self.assertRejected(dataset(model(colors=[])), 'complete')

    def test_partial_or_pending_must_say_what_is_missing(self):
        self.assertRejected(dataset(model(completeness='partial')), 'missing')
        self.assertEqual(self.errors(dataset(model(completeness='partial', colors=[], missing=['no colours']))), [])
        self.assertRejected(dataset(model(completeness='sure')), 'completeness')

    def test_prices_stock_and_images_never_enter_a_reference_file(self):
        for field in ('price', 'stock', 'image_url', 'images'):
            self.assertRejected(dataset(model(**{field: 1})), 'unknown field')

    def test_the_validator_does_not_change_its_input(self):
        data = dataset()
        before = copy.deepcopy(data)
        ref.validate(data)
        self.assertEqual(data, before)


class MalformedInputTest(SimpleTestCase):
    """A broken file is a list of errors, never an exception (review of #370)."""

    BAD_MODELS = [
        {'colors': [None]}, {'colors': ['grey']}, {'colors': [5]}, {'colors': 'grey'},
        {'colors': [{'key': ['x'], 'label_en': 'A', 'label_es': 'A', 'official': {'en': 'A'}}]},
        {'sim': [None]}, {'sim': [{'key': {}, 'regions': ['x'], 'config': 'c'}]}, {'sim': 'nano'},
        {'model_numbers': 5}, {'model_numbers': [None]}, {'model_numbers': [{'number': ['X'], 'regions': None}]},
        {'model_numbers': [{'number': 'X1', 'regions': 5}]},
        {'key': ['a']}, {'name': {}}, {'sources': ['https://[::1/x']}, {'sources': [5]},
        {'missing': 'x'}, {'capacities_gb': [True]}, {'verified_at': None}, {'announced': 5},
    ]

    def test_malformed_values_are_reported_not_raised(self):
        for over in self.BAD_MODELS:
            with self.subTest(over):
                errors = ref.validate(dataset(model(**over)))
                self.assertIsInstance(errors, list)
                self.assertTrue(errors, over)

    def test_malformed_roots_and_models(self):
        for data in (None, [], 'x', {**dataset(), 'models': [5]}, {**dataset(), 'models': []},
                     {**dataset(), 'models': 'x'}, {**dataset(), 'official_source_hosts': 'maker.test'}):
            with self.subTest(data):
                self.assertTrue(ref.validate(data))

    def test_partial_models_still_need_well_typed_lists(self):
        for field in ('colors', 'sim', 'model_numbers'):
            with self.subTest(field):
                bad = model(completeness='partial', missing=['x'])
                del bad[field]
                self.assertTrue(ref.validate(dataset(bad)))

    def test_schema_version_must_be_the_integer_one(self):
        for version in (True, 1.0, '1'):
            self.assertTrue(ref.validate({**dataset(), 'schema_version': version}), version)

    def test_a_key_with_a_trailing_newline_is_not_a_slug(self):
        self.assertTrue(ref.validate(dataset(model(key='phone-1\n'))))
        colour = {**model()['colors'][0], 'key': 'grey\n'}
        self.assertTrue(ref.validate(dataset(model(colors=[colour]))))

    def test_each_colour_keeps_the_official_strings(self):
        colour = dict(model()['colors'][0])
        del colour['official']
        self.assertTrue(ref.validate(dataset(model(colors=[colour]))))
        colour['official'] = {'en': 'Grey', 'es_419': 5, 'es_es': None}
        self.assertTrue(ref.validate(dataset(model(colors=[colour]))))

    def test_sim_entries_are_checked(self):
        sim = model()['sim'][0]
        self.assertTrue(ref.validate(dataset(model(sim=[sim, dict(sim)]))))
        self.assertTrue(ref.validate(dataset(model(sim=[{**sim, 'key': 'Nano SIM'}]))))
        self.assertTrue(ref.validate(dataset(model(sim=[{**sim, 'regions': ''}]))))

    def test_unknown_fields_inside_entries_are_rejected(self):
        m = model()
        for field, entry in (('colors', m['colors'][0]), ('sim', m['sim'][0]), ('model_numbers', m['model_numbers'][0])):
            with self.subTest(field):
                self.assertTrue(ref.validate(dataset(model(**{field: [{**entry, 'price': 1}]}))))

    def test_pending_review_needs_what_is_missing(self):
        self.assertTrue(ref.validate(dataset(model(completeness='pending_review'))))
        self.assertEqual(ref.validate(dataset(model(completeness='pending_review', missing=['all']))), [])

    def test_summary_counts(self):
        data = dataset(model(), model(key='phone-2', name='Phone 2', completeness='partial', missing=['x'],
                                      model_numbers=[{'number': 'X2', 'regions': None}], line='plus'))
        summary = ref.summary(data)
        self.assertEqual(summary['models'], 2)
        self.assertEqual(summary['by_completeness'], {'complete': 1, 'partial': 1})
        self.assertEqual(summary['by_line'], {'pro': 1, 'plus': 1})


class FilesTest(SimpleTestCase):
    """Every committed reference file validates; this is what CI checks on each change of the data."""

    def files(self):
        return sorted(DATA_DIR.glob('*.json'))

    def test_there_is_at_least_one_reference_file(self):
        self.assertTrue(self.files(), DATA_DIR)

    def test_every_file_validates(self):
        for path in self.files():
            with self.subTest(path.name):
                self.assertEqual(ref.validate(ref.load(path)), [])

    def test_summary_counts_models_by_completeness(self):
        for path in self.files():
            summary = ref.summary(ref.load(path))
            self.assertEqual(sum(summary['by_completeness'].values()), summary['models'])


class PhoneFileFactsTest(SimpleTestCase):
    """Spot checks of the iPhone file against Apple Tech Specs (sources in each model). Owner's example included."""

    def models(self):
        data = ref.load(DATA_DIR / 'apple-iphone.json')
        return {m['key']: m for m in data['models']}

    def test_neighbouring_models_have_their_own_palettes(self):
        models = self.models()
        palettes = {key: tuple(sorted(c['key'] for c in m['colors'])) for key, m in models.items() if m['colors']}
        self.assertNotEqual(palettes['iphone-17-pro'], palettes['iphone-16-pro'])
        self.assertNotEqual(palettes['iphone-16-pro'], palettes['iphone-15'])

    def test_owner_example_iphone_17_pro(self):
        pro = self.models()['iphone-17-pro']
        self.assertEqual(sorted(c['label_en'] for c in pro['colors']), ['Cosmic Orange', 'Deep Blue', 'Silver'])
        self.assertEqual(pro['capacities_gb'], [256, 512, 1024])

    def test_capacities_belong_to_their_model(self):
        models = self.models()
        self.assertIn(2048, models['iphone-17-pro-max']['capacities_gb'])
        self.assertNotIn(2048, models['iphone-17-pro']['capacities_gb'])

    def test_a_second_imei_is_never_written_as_a_second_physical_sim(self):
        for m in self.models().values():
            for sim in m['sim']:
                self.assertNotIn('imei', sim['config'].lower(), m['key'])

    def test_spanish_labels_are_clean(self):
        for m in self.models().values():
            for c in m['colors']:
                self.assertIsNone(re.match(r'(?i)color\s', c['label_es']), (m['key'], c['label_es']))
                self.assertEqual(c['label_es'][:1], c['label_es'][:1].upper(), (m['key'], c['label_es']))


class NoVocabularyInValidatorTest(SimpleTestCase):
    def test_the_validator_names_no_maker_model_or_colour(self):
        words = r'\b(apple|iphone|ipad|samsung|galaxy|black dog|negro|blanco|azul|plata|silver|black|white|blue)\b'
        source = Path(ref.__file__).read_text(encoding='utf-8')
        self.assertIsNone(re.search(words, source, re.I), re.search(words, source, re.I))
