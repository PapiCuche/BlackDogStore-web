"""
CATALOG-REFERENCE-01 · FASE 1.2 — the verifiable matrix of a manufacturers' reference file.

    python manage.py reference_matrix [--file <file>] [--format csv|json] [--model <key>] [--output <path>]

One row per model × capacity × colour × SIM/region pair the file allows (`catalog_reference.matrix_rows`; the pairs
are the importer's, so an excluded pair never appears). READ-ONLY AND PURE: it reads the file and nothing else — no
database, no audit. The file is validated first. Without `--file` it reads the only file of
`backend/catalog_reference/`; with more than one there, `--file` is required. The output is deterministic, so the
committed `docs/catalog-reference-matrix.csv` can be regenerated and compared byte for byte:

    python manage.py reference_matrix --sources-once --output ../docs/catalog-reference-matrix.csv

`--sources-once` writes a model's source URLs only on its first row (every row of a model shares them): the same
data, a file five times smaller.
"""
import csv
import io
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from store import catalog_reference as ref

DATA_DIR = Path(__file__).resolve().parents[3] / 'catalog_reference'


def _default_file() -> Path:
    files = sorted(DATA_DIR.glob('*.json'))
    if len(files) != 1:
        raise CommandError('Hay más de un archivo de referencia (o ninguno): indica --file.')
    return files[0]


def _once(rows) -> list[dict]:
    seen = set()
    for row in rows:
        if row['model_key'] in seen:
            row['sources'] = []
        seen.add(row['model_key'])
    return rows


def _csv(rows) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=ref.MATRIX_COLUMNS, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: (ref.MATRIX_LISTS[k].join(v) if k in ref.MATRIX_LISTS else ('' if v is None else v))
                         for k, v in row.items()})
    return out.getvalue()


class Command(BaseCommand):
    help = 'Escribe la matriz verificable (modelo × capacidad × color × SIM/región) de un archivo de referencia.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='Archivo JSON de backend/catalog_reference/ (por defecto, el único).')
        parser.add_argument('--format', choices=('csv', 'json'), default='csv')
        parser.add_argument('--model', help='Sólo este modelo (su clave).')
        parser.add_argument('--output', help='Escribe en este archivo en lugar de la salida estándar.')
        parser.add_argument('--sources-once', action='store_true',
                            help='Las fuentes de un modelo sólo en su primera fila (las comparten todas).')

    def handle(self, *args, **options):
        path = Path(options['file']) if options.get('file') else _default_file()
        if not path.is_file():
            raise CommandError('Archivo no encontrado.')
        try:
            data = ref.load(path)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError(f'El archivo no es JSON válido: {exc}') from exc
        errors = ref.validate(data)
        if errors:
            raise CommandError('El archivo no valida: ' + '; '.join(errors[:20]))
        key = options.get('model')
        if key is not None and key not in {m['key'] for m in data['models']}:
            raise CommandError(f'No hay un modelo «{key}» en el archivo.')
        rows = ref.matrix_rows(data, model_key=key)
        if options['sources_once']:
            rows = _once(rows)
        if options['format'] == 'json':
            text = json.dumps({'manufacturer': data['manufacturer'], 'family': data['family'],
                               'dataset_version': data['dataset_version'], 'count': len(rows), 'rows': rows},
                              ensure_ascii=False, indent=1) + '\n'
        else:
            text = _csv(rows)
        if options.get('output'):
            Path(options['output']).write_text(text, encoding='utf-8')
            self.stderr.write(f'{len(rows)} fila(s) escritas en {options["output"]}.')
        else:
            self.stdout.write(text, ending='')
