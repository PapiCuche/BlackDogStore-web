"""
CATALOG-REFERENCE-01 · FASE 1.2 — la matriz verificable del catálogo de referencia (`reference_matrix`).

    python manage.py reference_matrix [--file <archivo>] [--format csv|json] [--model <clave>] [--output <ruta>]

Lo que estas pruebas fijan:

  * una fila por modelo × capacidad × color × par SIM/región que el archivo permite; los pares SIM × región son los
    mismos que usa el importador (`catalog_reference.pairs_of`): un par excluido nunca aparece;
  * un modelo sin colores o sin SIM sigue en la matriz (una fila con ese campo vacío), marcado por su `completeness`;
  * los números de modelo se unen a una entrada SIM sólo cuando el archivo nombra las mismas regiones; si no, van
    todos los del modelo y la columna `model_numbers_link` lo dice;
  * la salida es determinista y el comando no toca la base (SimpleTestCase no admite consultas);
  * el motor no nombra fabricante, modelo ni color (AGENTS §2).

Los datos son inventados (`test_catalog_reference_load`); el archivo real sólo se recorre para contar.
"""
import csv
import io
import json
import re
import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from store import catalog_reference as ref
from store.test_catalog_reference_load import DATA_DIR, dataset, model, old, pro, write


def _run(*args):
    out = io.StringIO()
    call_command('reference_matrix', *args, stdout=out, stderr=io.StringIO())
    return out.getvalue()


def _expected(m):
    pairs = list(dict.fromkeys((e['key'], ref.region_key(e['regions'])) for e in m.get('sim') or []))
    return len(m.get('capacities_gb') or [None]) * len(m.get('colors') or [None]) * len(pairs or [None])


class MatrixRowsTest(SimpleTestCase):
    def test_one_row_per_capacity_colour_and_existing_pair(self):
        rows = ref.matrix_rows(dataset())
        by_model = {}
        for row in rows:
            by_model.setdefault(row['model_key'], []).append(row)
        # phone-9: 2 capacities × 2 colours × 3 pairs; phone-9-pro: 3 × 2 × 1; phone-1: 1 × (no colour) × (no SIM).
        self.assertEqual({k: len(v) for k, v in by_model.items()}, {'phone-9': 12, 'phone-9-pro': 6, 'phone-1': 1})

    def test_no_excluded_pair_appears(self):
        rows = ref.matrix_rows(dataset())
        seen = {(r['sim_key'], r['region_key']) for r in rows if r['model_key'] == 'phone-9'}
        self.assertEqual(seen, ref.pairs_of(model()))
        options = ref.options_of(model())
        sims = [o['key'] for o in options if o['role'] == 'sim']
        regions = [o['key'] for o in options if o['role'] == 'region']
        excluded = {(s, r) for s in sims for r in regions} - ref.pairs_of(model())
        self.assertEqual(len(excluded), 6)
        self.assertFalse(excluded & seen)

    def test_a_row_carries_the_labels_the_importer_gives(self):
        sims = [{'key': 'esim-only', 'regions': 'Country A, Country B (source)', 'config': 'eSIM only',
                 'label_es': 'Solo eSIM', 'region_label_es': 'Países A y B'}]
        row = ref.matrix_rows(dataset(model(sim=sims)))[0]
        self.assertEqual(row['model_name'], 'Phone 9')
        self.assertEqual(row['completeness'], 'complete')
        self.assertEqual(row['capacity_gb'], 128)
        self.assertEqual((row['color_key'], row['color_label_es']), ('ink', 'Tinta'))
        self.assertEqual((row['color_official_es_419'], row['color_official_es_es']), ('Tinta', 'tinta'))
        self.assertEqual((row['sim_key'], row['sim_label_es']), ('esim-only', 'Solo eSIM'))
        self.assertEqual((row['region_key'], row['region_label_es']), ('country-a-country-b', 'Países A y B'))
        self.assertEqual(row['sources'], ['https://support.maker.test/specs/9'])
        self.assertEqual(row['verified_at'], '2026-11-20')

    def test_model_numbers_follow_the_region_names_or_fall_back_to_the_whole_model(self):
        numbers = [{'number': 'X9001', 'regions': 'Country A'}, {'number': 'X9003', 'regions': 'Country B'},
                   {'number': 'X9004', 'regions': 'Country C'}, {'number': 'X9002', 'regions': None}]
        rows = ref.matrix_rows(dataset(model(model_numbers=numbers)))
        by_pair = {(r['sim_key'], r['region_key']): r for r in rows}
        a_b = by_pair[('esim-only', 'country-a-country-b')]
        self.assertEqual((a_b['model_numbers'], a_b['model_numbers_link']), (['X9001', 'X9003'], 'region'))
        c = by_pair[('dual-nano-sim', 'country-c')]
        self.assertEqual((c['model_numbers'], c['model_numbers_link']), (['X9004'], 'region'))
        # «default» names no region of its own: every number of the model, said so.
        default = by_pair[('nano-sim-esim', 'default')]
        self.assertEqual((default['model_numbers'], default['model_numbers_link']),
                         (['X9001', 'X9003', 'X9004', 'X9002'], 'model'))

    def test_a_number_naming_a_region_outside_the_entry_is_not_linked(self):
        numbers = [{'number': 'X9001', 'regions': 'Country A, Country Z'}]
        rows = ref.matrix_rows(dataset(model(model_numbers=numbers)))
        row = next(r for r in rows if r['sim_key'] == 'esim-only')
        self.assertEqual((row['model_numbers'], row['model_numbers_link']), (['X9001'], 'model'))

    def test_a_model_without_colours_or_sim_is_kept_and_flagged(self):
        rows = [r for r in ref.matrix_rows(dataset()) if r['model_key'] == 'phone-1']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['completeness'], rows[0]['color_key'], rows[0]['sim_key']), ('partial', '', ''))
        self.assertEqual(rows[0]['missing'], ['colours are not published'])

    def test_one_model_only(self):
        rows = ref.matrix_rows(dataset(), model_key='phone-9-pro')
        self.assertEqual({r['model_key'] for r in rows}, {'phone-9-pro'})
        self.assertEqual(len(rows), 6)

    def test_the_real_files_give_the_computed_product_and_no_excluded_pair(self):
        for path in sorted(DATA_DIR.glob('*.json')):
            data = ref.load(path)
            rows = ref.matrix_rows(data)
            for m in data['models']:
                mine = [r for r in rows if r['model_key'] == m['key']]
                self.assertEqual(len(mine), _expected(m), m['key'])
                self.assertTrue({(r['sim_key'], r['region_key']) for r in mine if r['sim_key']} <= ref.pairs_of(m))
            self.assertEqual(len(rows), len({(r['model_key'], r['capacity_gb'], r['color_key'], r['sim_key'],
                                              r['region_key']) for r in rows}))


class MatrixCommandTest(SimpleTestCase):
    """SimpleTestCase refuses any query: the command reads the file and nothing else."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = write(dataset(), self.dir.name)

    def test_csv_has_a_header_and_one_line_per_row(self):
        text = _run('--file', str(self.path))
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 19)
        self.assertEqual(list(rows[0]), list(ref.MATRIX_COLUMNS))
        self.assertEqual(rows[0]['model_numbers'], 'X9001')
        self.assertEqual(rows[0]['model_numbers_link'], 'region')
        self.assertEqual(rows[0]['sources'], 'https://support.maker.test/specs/9')

    def test_json_and_one_model(self):
        data = json.loads(_run('--file', str(self.path), '--format', 'json', '--model', 'phone-9'))
        self.assertEqual(data['dataset_version'], '2026-11-20')
        self.assertEqual(data['count'], 12)
        self.assertEqual(len(data['rows']), 12)
        self.assertEqual(data['rows'][0]['model_numbers'], ['X9001'])
        self.assertEqual(data['rows'][2]['model_numbers'], ['X9001', 'X9002'])   # default

    def test_the_output_is_deterministic_and_can_go_to_a_file(self):
        first = _run('--file', str(self.path))
        self.assertEqual(first, _run('--file', str(self.path)))
        target = Path(self.dir.name) / 'matrix.csv'
        _run('--file', str(self.path), '--output', str(target))
        self.assertEqual(target.read_text(encoding='utf-8'), first)

    def test_sources_once_keeps_every_url_on_the_first_row_of_each_model(self):
        rows = list(csv.DictReader(io.StringIO(_run('--file', str(self.path), '--sources-once'))))
        first = {}
        for row in rows:
            first.setdefault(row['model_key'], row)
        self.assertEqual({k: r['sources'] for k, r in first.items()},
                         {k: 'https://support.maker.test/specs/9' for k in ('phone-9', 'phone-9-pro', 'phone-1')})
        self.assertEqual(sum(1 for r in rows if r['sources']), 3)

    def test_an_unknown_model_or_an_invalid_file_is_refused(self):
        with self.assertRaises(CommandError):
            _run('--file', str(self.path), '--model', 'phone-99')
        bad = write(dataset(model(capacities_gb=[256, 128])), tempfile.mkdtemp(dir=self.dir.name))
        with self.assertRaises(CommandError):
            _run('--file', str(bad))
        with self.assertRaises(CommandError):
            _run('--file', str(Path(self.dir.name) / 'missing.json'))

    def test_without_file_it_reads_the_only_reference_file(self):
        files = sorted(DATA_DIR.glob('*.json'))
        if len(files) != 1:
            self.skipTest('more than one reference file: --file is required')
        rows = list(csv.DictReader(io.StringIO(_run())))
        self.assertEqual(len(rows), sum(_expected(m) for m in ref.load(files[0])['models']))

    def test_the_command_names_nothing(self):
        from store.management.commands import reference_matrix
        from store.test_catalog_reference_import import NoVocabularyInTheEngineTest as guard

        source = guard.code(None, Path(reference_matrix.__file__).read_text(encoding='utf-8'))
        self.assertIsNone(re.search(guard.WORDS, source, re.I))
