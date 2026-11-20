"""
CATALOG-REFERENCE-01 · PR B — load a manufacturers' reference file into the platform tables.

    python manage.py load_reference_catalog --file backend/catalog_reference/<maker>-<family>.json [--dry-run]

The file is validated first (`catalog_reference.validate`): an invalid file is refused and nothing is written.
Rows are upserted by key, so a second run reports 0 changes; nothing is ever deleted (what left the file is
reported as a candidate to retire). `--dry-run` reports the same and writes nothing. A real run leaves one
`AdminAuditLog` entry without company (platform data) nor actor (an operator command), with `source: command`,
like `load_legal_policy`. No company sees anything until it imports (`catalog_reference_import`).
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from store import catalog_reference_load as loader
from store.models import AdminAuditLog


class Command(BaseCommand):
    help = 'Carga (o simula cargar) un archivo del catálogo de referencia de fabricantes.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Archivo JSON de backend/catalog_reference/.')
        parser.add_argument('--dry-run', action='store_true', help='Informa los cambios sin escribir nada.')

    def handle(self, *args, **options):
        path = Path(options['file'])
        if not path.is_file():
            raise CommandError('Archivo no encontrado.')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError(f'El archivo no es JSON válido: {exc}') from exc
        try:
            report = loader.load(data, dry_run=options['dry_run'])
        except loader.InvalidReference as exc:
            raise CommandError('El archivo no valida: ' + '; '.join(exc.errors[:20])) from exc
        if not report['dry_run']:
            AdminAuditLog.log(
                actor=None, action='reference_catalog_loaded', target_type='reference_catalog',
                target_id=f'{report["manufacturer"]}/{report["family"]}'[:50], company=None,
                metadata={'source': 'command', 'file': path.name, 'dataset_version': report['dataset_version'],
                          'models': report['models'], 'options': report['options'],
                          'model_numbers': report['model_numbers'], 'retired': report['retired'],
                          'retired_options': report['retired_options'],
                          'retired_model_numbers': report['retired_model_numbers']})
        self.stdout.write(self._summary(report))

    @staticmethod
    def _summary(report) -> str:
        def counts(item):
            return f'{item["created"]} creados, {item["updated"]} actualizados, {item["unchanged"]} sin cambios'

        lines = [
            f'{report["manufacturer"]} · {report["family"]} · versión {report["dataset_version"]}'
            + (' (simulación: no se escribió nada)' if report['dry_run'] else ''),
            f'modelos: {counts(report["models"])}',
            f'opciones: {counts(report["options"])}',
            f'números de modelo: {counts(report["model_numbers"])}',
        ]
        retired = report['retired'] + report['retired_options'] + report['retired_model_numbers']
        if retired:
            lines.append(f'candidatos a retirar (fuera del archivo; se conservan): {", ".join(retired)}')
        return '\n'.join(lines)
