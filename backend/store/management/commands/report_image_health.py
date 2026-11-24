"""
IMG-HEALTH (IMG-GATE-01) — what is wrong with the pictures of ONE company's catalogue.

    python manage.py report_image_health --company <slug> [--format csv|json] [--skip-storage]

READ-ONLY: it writes nothing, audits nothing and never deletes a picture or a file. Five classes of problem, one row
each (`store.image_health`): broken managed files, external hotlinks (by host), pictures the shop still shows without
a verified licence, the same file on two colours of one model, and NEW device variants on sale that show «Imagen
pendiente» (`own_hidden`: they have pictures of their own that IMG-GATE-01 hides until their licence is verified —
the number the release rehearsal must count). Every row, no cap. `--skip-storage` does not ask the storage whether
each file is still there (one check per distinct file otherwise). In CSV the summary goes to stderr, so the standard
output stays a clean CSV file.
"""
import csv
import io
import json

from django.core.management.base import BaseCommand, CommandError

from store import image_health
from store.models import Company


def _summary_line(company, summary) -> str:
    hosts = ', '.join(f'{row["host"]} ({row["images"]})' for row in summary['external_hosts']) or 'ninguno'
    return (f'{company.slug}: {summary["broken_file"]} archivo(s) roto(s), {summary["external_hotlink"]} enlace(s) '
            f'externo(s) [{hosts}], {summary["unverified_public"]} imagen(es) visibles sin licencia verificada, '
            f'{summary["shared_across_colours"]} archivo(s) en dos colores de un modelo, '
            f'{summary["variant_without_photo"]} variante(s) nueva(s) en venta con «Imagen pendiente» '
            f'({summary["variants_hidden_by_gate"]} con fotos propias ocultas hasta verificar la licencia)'
            f'{"" if summary["storage_checked"] else "; almacenamiento sin comprobar"}.')


class Command(BaseCommand):
    help = 'Informa (sólo lectura) los problemas de las imágenes del catálogo de una empresa.'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug de la empresa.')
        parser.add_argument('--format', choices=('csv', 'json'), default='csv')
        parser.add_argument('--skip-storage', action='store_true',
                            help='No comprobar en el almacenamiento que cada archivo sigue ahí.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError(f'No existe la empresa «{options["company"]}».')
        report = image_health.report(company, check_storage=not options['skip_storage'])
        if options['format'] == 'json':
            self.stdout.write(json.dumps({'company': company.slug, **report}, ensure_ascii=False, indent=1))
            return
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=image_health.COLUMNS, lineterminator='\n')
        writer.writeheader()
        for row in report['rows']:
            writer.writerow({key: '' if row[key] is None else row[key] for key in image_health.COLUMNS})
        self.stdout.write(out.getvalue(), ending='')
        self.stderr.write(_summary_line(company, report['summary']))
