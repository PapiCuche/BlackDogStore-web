"""
CATALOG-REFERENCE-01 · PR B — el catálogo de referencia entra a la base de la plataforma (`docs/adr-catalog-reference.md`).

    python manage.py load_reference_catalog --file backend/catalog_reference/<fabricante>-<familia>.json [--dry-run]

Lo que estas pruebas fijan:

  * un archivo que no valida (`catalog_reference.validate`) se rechaza entero y no escribe nada;
  * la carga es idempotente por clave: la segunda vez son 0 cambios; un cambio del archivo actualiza sólo esa fila;
  * nada se borra: lo que salió del archivo queda marcado fuera del dataset y se informa como «candidato a retirar»,
    sólo dentro del mismo fabricante y familia;
  * `--dry-run` informa lo mismo y no escribe ni audita;
  * cada carga real queda auditada (sin empresa: son datos de la plataforma) con `source: command`;
  * SIM y región: cada entrada `sim` del archivo es un par SIM × región; la región es el texto del archivo, con una
    clave derivada estable; los números de modelo guardan su texto de regiones;
  * ninguna empresa ve nada por cargar el catálogo.

Los datos de prueba son inventados (AGENTS §2); el archivo real sólo se carga para comprobar que valida y entra.
"""
import copy
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from store import catalog_reference as ref
from store import catalog_reference_load as loader
from store.models import (
    AdminAuditLog, ProductModel, ReferenceModel, ReferenceModelNumber, ReferenceOption, VariantOption,
)
from store.tests import _p3_company

DATA_DIR = Path(__file__).resolve().parent.parent / 'catalog_reference'


def model(**over):
    base = {
        'key': 'phone-9', 'name': 'Phone 9', 'line': 'standard', 'generation': '9', 'announced': '2025-09-01',
        'capacities_gb': [128, 256],
        'colors': [
            {'key': 'ink', 'label_en': 'Ink', 'label_es': 'Tinta',
             'official': {'en': 'Ink', 'es_419': 'Tinta', 'es_es': 'tinta'}},
            {'key': 'sand', 'label_en': 'Sand', 'label_es': 'Arena',
             'official': {'en': 'Sand', 'es_419': 'Arena', 'es_es': 'Arena'}},
        ],
        'sim': [
            {'key': 'esim-only', 'regions': 'Country A, Country B', 'config': 'eSIM only'},
            {'key': 'nano-sim-esim', 'regions': 'default (other countries and regions)', 'config': 'nano-SIM + eSIM'},
            {'key': 'dual-nano-sim', 'regions': 'Country C', 'config': 'two nano-SIM'},
        ],
        'model_numbers': [{'number': 'X9001', 'regions': 'Country A, Country B'},
                          {'number': 'X9002', 'regions': None}],
        'sources': ['https://support.maker.test/specs/9'],
        'verified_at': '2026-11-20', 'completeness': 'complete', 'missing': [],
    }
    base.update(over)
    return base


def pro(**over):
    return model(**{
        'key': 'phone-9-pro', 'name': 'Phone 9 Pro', 'line': 'pro', 'capacities_gb': [256, 512, 1024],
        'colors': [
            {'key': 'ink', 'label_en': 'Ink', 'label_es': 'Tinta',
             'official': {'en': 'Ink', 'es_419': 'Tinta', 'es_es': 'tinta'}},
            {'key': 'sky', 'label_en': 'Sky', 'label_es': 'Cielo',
             'official': {'en': 'Sky', 'es_419': 'Cielo', 'es_es': 'Cielo'}},
        ],
        'sim': [{'key': 'nano-sim-esim', 'regions': 'default (other countries and regions)',
                 'config': 'nano-SIM + eSIM'}],
        'model_numbers': [{'number': 'X9101', 'regions': None}],
        **over})


def old(**over):
    return model(**{
        'key': 'phone-1', 'name': 'Phone 1', 'line': 'original', 'generation': '1', 'capacities_gb': [8],
        'colors': [], 'sim': [], 'model_numbers': [], 'completeness': 'partial',
        'missing': ['colours are not published'], **over})


def dataset(*models, **over):
    data = {'schema_version': 1, 'manufacturer': 'Maker', 'family': 'Phone', 'dataset_version': '2026-11-20',
            'official_source_hosts': ['maker.test'], 'models': list(models) or [model(), pro(), old()]}
    data.update(over)
    return data


def write(data, directory):
    path = Path(directory) / 'maker-phone.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class DerivationTest(SimpleTestCase):
    """What the loader derives from a model of the file, without a database."""

    def test_the_options_of_a_model_carry_their_role_key_and_labels(self):
        options = {(o['role'], o['key']): o for o in ref.options_of(model())}
        self.assertEqual(options[('capacity', '128gb')]['label_en'], '128 GB')
        self.assertEqual(options[('capacity', '256gb')]['capacity_gb'], 256)
        self.assertEqual((options[('color', 'ink')]['label_en'], options[('color', 'ink')]['label_es']), ('Ink', 'Tinta'))
        self.assertEqual(options[('color', 'ink')]['official'], {'en': 'Ink', 'es_419': 'Tinta', 'es_es': 'tinta'})
        self.assertEqual(options[('sim', 'esim-only')]['official'], {'config': ['eSIM only']})
        self.assertEqual(options[('region', 'default')]['label_en'], 'default')
        self.assertEqual(options[('region', 'country-a-country-b')]['label_en'], 'Country A, Country B')
        self.assertEqual({role for role, _key in options}, {'capacity', 'color', 'sim', 'region'})

    def test_a_terabyte_capacity_reads_in_terabytes(self):
        options = {(o['role'], o['key']): o for o in ref.options_of(pro())}
        self.assertEqual(options[('capacity', '1024gb')]['label_en'], '1 TB')

    def test_each_sim_entry_is_one_sim_region_pair(self):
        self.assertEqual(ref.pairs_of(model()), {('esim-only', 'country-a-country-b'), ('nano-sim-esim', 'default'),
                                                 ('dual-nano-sim', 'country-c')})

    def test_a_long_region_text_gets_a_bounded_key_and_label(self):
        text = ', '.join(f'Country {n:02d}' for n in range(30))
        key = ref.region_key(text)
        self.assertLessEqual(len(key), 64)
        self.assertEqual(key, ref.region_key(text))
        self.assertNotEqual(key, ref.region_key(text + ', Country 99'))
        self.assertLessEqual(len(ref.region_label(text)), 60)

    def test_the_spanish_labels_of_a_sim_entry_name_the_sim_and_region_options(self):
        sims = [{'key': 'esim-only', 'regions': 'Country A, Country B', 'config': 'eSIM only',
                 'label_es': 'Solo eSIM', 'region_label_es': 'Países A y B'},
                {'key': 'nano-sim-esim', 'regions': 'default (other countries)', 'config': 'nano-SIM + eSIM',
                 'label_es': 'nano-SIM y eSIM', 'region_label_es': 'Internacional (otros países)'}]
        options = {(o['role'], o['key']): o for o in ref.options_of(model(sim=sims))}
        self.assertEqual(options[('sim', 'esim-only')]['label_es'], 'Solo eSIM')
        self.assertEqual(options[('sim', 'nano-sim-esim')]['label_es'], 'nano-SIM y eSIM')
        self.assertEqual(options[('region', 'country-a-country-b')]['label_es'], 'Países A y B')
        self.assertEqual(options[('region', 'default')]['label_es'], 'Internacional (otros países)')
        # The keys do not change: links and re-imports stay stable.
        self.assertEqual(set(options), {(o['role'], o['key']) for o in ref.options_of(model(
            sim=[{k: v for k, v in e.items() if 'label' not in k} for e in sims]))})

    def test_a_sim_key_reads_as_words(self):
        self.assertEqual(ref.sim_label('nano-sim-esim'), 'nano sim esim')

    def test_the_real_files_derive_without_errors(self):
        for path in sorted(DATA_DIR.glob('*.json')):
            for m in ref.load(path)['models']:
                keys = [(o['role'], o['key']) for o in ref.options_of(m)]
                self.assertEqual(len(keys), len(set(keys)), m['key'])
                for o in ref.options_of(m):
                    self.assertLessEqual(len(o['label_es']), 60, (m['key'], o))
                    self.assertLessEqual(len(o['key']), 64, (m['key'], o))


class LoadTest(TestCase):
    def test_a_file_loads_models_options_and_model_numbers(self):
        report = loader.load(dataset())
        self.assertEqual(report['models'], {'created': 3, 'updated': 0, 'unchanged': 0})
        phone = ReferenceModel.objects.get(key='phone-9')
        self.assertEqual((phone.manufacturer, phone.family, phone.name, phone.line), ('Maker', 'Phone', 'Phone 9', 'standard'))
        self.assertEqual((phone.completeness, phone.dataset_version, str(phone.verified_at)),
                         ('complete', '2026-11-20', '2026-11-20'))
        self.assertEqual(phone.sources, ['https://support.maker.test/specs/9'])
        self.assertTrue(phone.in_dataset)
        self.assertEqual(sorted(phone.options.filter(role='color').values_list('key', flat=True)), ['ink', 'sand'])
        self.assertEqual(ReferenceModelNumber.objects.get(number='X9001').regions, 'Country A, Country B')
        self.assertEqual(ReferenceModelNumber.objects.get(number='X9002').regions, '')
        self.assertEqual(len(phone.sim_entries), 3)
        self.assertEqual(ReferenceModel.objects.get(key='phone-1').missing, ['colours are not published'])

    def test_loading_the_same_file_twice_changes_nothing(self):
        loader.load(dataset())
        report = loader.load(dataset())
        self.assertEqual(report['models'], {'created': 0, 'updated': 0, 'unchanged': 3})
        self.assertEqual(report['options']['created'] + report['options']['updated'], 0)
        self.assertEqual(report['model_numbers']['created'] + report['model_numbers']['updated'], 0)
        self.assertEqual(report['retired'], [])

    def test_notes_written_as_a_list_reload_without_changes(self):
        # The iPhone file keeps Apple-page discrepancies as a list of notes; a reload must not rewrite them.
        listed = dataset(model(notes=['first note', 'second note']), pro(), old())
        loader.load(listed)
        report = loader.load(listed)
        self.assertEqual(report['models'], {'created': 0, 'updated': 0, 'unchanged': 3})
        self.assertEqual(ReferenceModel.objects.get(key='phone-9').notes, 'first note\nsecond note')

    def test_the_committed_files_reload_without_changes(self):
        from pathlib import Path

        from store import catalog_reference as ref

        for path in sorted((Path(__file__).resolve().parent.parent / 'catalog_reference').glob('*.json')):
            with self.subTest(path.name):
                data = ref.load(path)
                loader.load(data)
                report = loader.load(data)
                self.assertEqual(report['models']['updated'], 0, path.name)
                self.assertEqual(report['options']['updated'], 0, path.name)
                self.assertEqual(report['model_numbers']['updated'], 0, path.name)

    def test_a_changed_model_is_updated_alone(self):
        loader.load(dataset())
        changed = dataset(model(name='Phone 9 (2025)'), pro(), old())
        report = loader.load(changed)
        self.assertEqual(report['models'], {'created': 0, 'updated': 1, 'unchanged': 2})
        self.assertEqual(ReferenceModel.objects.get(key='phone-9').name, 'Phone 9 (2025)')

    def test_nothing_is_deleted_what_left_the_file_is_reported_and_marked(self):
        loader.load(dataset())
        report = loader.load(dataset(model(colors=model()['colors'][:1]), pro()))
        self.assertEqual(report['retired'], ['phone-1'])
        self.assertIn('phone-9:color:sand', report['retired_options'])
        self.assertFalse(ReferenceModel.objects.get(key='phone-1').in_dataset)
        self.assertFalse(ReferenceOption.objects.get(model__key='phone-9', role='color', key='sand').in_dataset)
        self.assertEqual(ReferenceModel.objects.count(), 3)
        # Back in the file: back in the dataset.
        loader.load(dataset())
        self.assertTrue(ReferenceModel.objects.get(key='phone-1').in_dataset)

    def test_another_family_never_retires_this_one(self):
        loader.load(dataset())
        report = loader.load(dataset(model(key='tablet-1', name='Tablet 1', model_numbers=[{'number': 'T1001', 'regions': None}]), family='Tablet'))
        self.assertEqual(report['retired'], [])
        self.assertTrue(ReferenceModel.objects.get(key='phone-1').in_dataset)

    def test_an_invalid_file_is_refused_whole(self):
        bad = dataset()
        bad['models'][0]['price'] = 999
        with self.assertRaises(loader.InvalidReference) as caught:
            loader.load(bad)
        self.assertTrue(any('price' in e for e in caught.exception.errors))
        self.assertFalse(ReferenceModel.objects.exists())

    def test_a_dry_run_reports_and_writes_nothing(self):
        report = loader.load(dataset(), dry_run=True)
        self.assertEqual(report['models']['created'], 3)
        self.assertTrue(report['dry_run'])
        self.assertFalse(ReferenceModel.objects.exists())
        self.assertFalse(ReferenceOption.objects.exists())

    def test_the_input_is_not_changed(self):
        data = dataset()
        before = copy.deepcopy(data)
        loader.load(data)
        self.assertEqual(data, before)

    def test_no_company_sees_anything_by_loading(self):
        company = _p3_company('ref-load-nothing', 'Nada')
        loader.load(dataset())
        self.assertFalse(ProductModel.objects.filter(company=company).exists())
        self.assertFalse(VariantOption.objects.filter(company=company).exists())


class CommandTest(TestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command('load_reference_catalog', *args, stdout=out)
        return out.getvalue()

    def test_the_command_loads_reports_and_audits_without_a_company(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.run_command('--file', str(write(dataset(), directory)))
        self.assertIn('modelos: 3 creados, 0 actualizados, 0 sin cambios', output)
        log = AdminAuditLog.objects.get(action='reference_catalog_loaded')
        self.assertIsNone(log.company)
        self.assertIsNone(log.actor)
        self.assertEqual(log.metadata['source'], 'command')
        self.assertEqual(log.metadata['file'], 'maker-phone.json')
        self.assertEqual(log.metadata['models']['created'], 3)

    def test_a_second_run_reports_no_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(write(dataset(), directory))
            self.run_command('--file', path)
            output = self.run_command('--file', path)
        self.assertIn('modelos: 0 creados, 0 actualizados, 3 sin cambios', output)

    def test_dry_run_writes_and_audits_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = self.run_command('--file', str(write(dataset(), directory)), '--dry-run')
        self.assertIn('simulación', output)
        self.assertFalse(ReferenceModel.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(action='reference_catalog_loaded').exists())

    def test_an_invalid_or_missing_file_is_a_command_error(self):
        bad = dataset()
        bad['models'][0]['stock'] = 3
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CommandError) as caught:
                self.run_command('--file', str(write(bad, directory)))
            self.assertIn('stock', str(caught.exception))
            with self.assertRaises(CommandError):
                self.run_command('--file', str(Path(directory) / 'nope.json'))
            broken = Path(directory) / 'broken.json'
            broken.write_text('{not json', encoding='utf-8')
            with self.assertRaises(CommandError):
                self.run_command('--file', str(broken))
        self.assertFalse(ReferenceModel.objects.exists())

    def test_the_committed_files_load(self):
        for path in sorted(DATA_DIR.glob('*.json')):
            self.run_command('--file', str(path))
            data = ref.load(path)
            self.assertEqual(ReferenceModel.objects.filter(manufacturer=data['manufacturer'],
                                                           family=data['family']).count(), len(data['models']))
