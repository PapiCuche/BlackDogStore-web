"""
CATALOG FASE 1.2 · Z — load finish pictures (model + colour) from a manifest with their provenance.

    python manage.py import_finish_images --company <slug> --manifest <file.json|file.csv> --dir <folder> \
        --actor <user id or email> [--dry-run]

Every row is validated and reported first (ok / skip-duplicate / error). With any error nothing is written and the
command exits non-zero (also with --dry-run). --dry-run writes nothing and stores no file. Re-running a manifest
imports nothing new. Licences are never marked verified: a person verifies them in the panel. The actor must be an
active user with `products.manage` in that company; every write is audited under that user and that company.
Format: docs/finish-images-manifest.example.csv and docs/adr-catalog-reference.md. Logic: store/finish_manifest.py.
"""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from store import finish_manifest
from store.models import Company
from store.tenancy import has_capability


class Command(BaseCommand):
    help = 'Importa fotos por acabado (modelo + color) desde un manifiesto con su procedencia.'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug de la empresa.')
        parser.add_argument('--manifest', required=True, help='Manifiesto .json o .csv.')
        parser.add_argument('--dir', required=True, help='Carpeta con las fotos (rutas del manifiesto relativas a ella).')
        parser.add_argument('--actor', required=True, help='Id o correo del usuario que firma (con products.manage).')
        parser.add_argument('--dry-run', action='store_true', help='Valida e informa sin escribir ni guardar nada.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=str(options['company']).strip()).first()
        if company is None:
            raise CommandError('Empresa no encontrada.')
        actor = self._actor(str(options['actor']).strip())
        if actor is None or not has_capability(actor, company, 'products.manage'):
            raise CommandError('--actor debe ser un usuario activo con products.manage en esa empresa.')
        root = Path(options['dir'])
        if not root.is_dir():
            raise CommandError('--dir no es una carpeta.')
        try:
            report = finish_manifest.run(company=company, manifest=Path(options['manifest']), root=root,
                                         actor=actor, dry_run=options['dry_run'])
        except finish_manifest.ManifestError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self._summary(company, report))
        if report.errors:
            raise CommandError(f'{len(report.errors)} filas con error: no se escribió nada.')

    @staticmethod
    def _actor(value):
        users = get_user_model().objects.filter(is_active=True)
        if value.isascii() and value.isdigit():
            return users.filter(pk=int(value)).first()
        if '@' in value:
            found = list(users.filter(email__iexact=value)[:2])
            return found[0] if len(found) == 1 else None
        return None

    @staticmethod
    def _summary(company, report) -> str:
        lines = [f'{company.name} · manifiesto {report.manifest_sha256}']
        for row in report.rows:
            where = f'{row.model.name} / {row.colour.label}' if row.model and row.colour else '-'
            text = f'fila {row.number} · {row.status} · {where} · {row.file or "-"}'
            if row.sha256:
                text += f' · sha256 {row.sha256[:12]}'
            if row.reason:
                text += f' — {row.reason}'
            lines.append(text)
        if report.dry_run:
            lines.append(f'simulación: no se escribió nada · {report.to_import} por importar, '
                         f'{report.skipped} ya estaban, {len(report.errors)} con error')
        elif report.errors:
            lines.append(f'{len(report.errors)} con error: no se escribió nada')
        else:
            lines.append(f'{report.imported} nuevas, {report.skipped} ya estaban · '
                         'licencias sin verificar: revisarlas en el panel')
        return '\n'.join(lines)
