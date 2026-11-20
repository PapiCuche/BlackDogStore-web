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

    def test_each_model_has_its_own_palette(self):
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
