"""
CATALOG-REFERENCE-01 · FASE 1.2 — photo coverage of every finish (model × colour) of ONE company's catalogue.

    python manage.py report_finish_coverage --company <slug> [--format csv|json] [--only-missing]

READ-ONLY: it writes nothing and audits nothing. The rows are `catalog_image_sources.finish_coverage` — every finish,
with no cap (the admin API names at most `MAX_PENDING_FINISHES`). A finish is covered when it has at least one
picture that is not a demo resource. `verified_photo`: at least one of its pictures has its licence verified — only
those may reach the shop (owner, 2026-11-23); it does not say that a published variant uses the colour.
`--only-missing` keeps the finishes that are not covered; the summary is always
the whole catalogue's. In CSV the summary goes to stderr, so the standard output stays a clean CSV file.
"""
import csv
import io
import json

from django.core.management.base import BaseCommand, CommandError

from store import catalog_image_sources as sources
from store.models import Company


def _summary_line(company, summary) -> str:
    return (f'{company.slug}: {summary["finishes"]} acabado(s), {summary["covered"]} con al menos una foto no '
            f'demostrativa, {summary["without_photos"]} sin fotos, {summary["demo_only"]} sólo con fotos '
            f'demostrativas, {summary["verified_photo"]} con foto de licencia verificada; cobertura '
            f'{summary["coverage_percent"]:.1f} %.')


class Command(BaseCommand):
    help = 'Informa la cobertura de fotos de cada acabado (modelo × color) del catálogo de una empresa.'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='Slug de la empresa.')
        parser.add_argument('--format', choices=('csv', 'json'), default='csv')
        parser.add_argument('--only-missing', action='store_true',
                            help='Sólo los acabados sin una foto que no sea demostrativa.')

    def handle(self, *args, **options):
        company = Company.objects.filter(slug=options['company']).first()
        if company is None:
            raise CommandError(f'No existe la empresa «{options["company"]}».')
        report = sources.finish_coverage(company)
        rows = [row for row in report['rows'] if not row['covered']] if options['only_missing'] else report['rows']
        if options['format'] == 'json':
            self.stdout.write(json.dumps({'company': company.slug, 'summary': report['summary'], 'rows': rows},
                                         ensure_ascii=False, indent=1))
            return
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=sources.FINISH_COVERAGE_COLUMNS, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ('yes' if v else 'no') if isinstance(v, bool) else v for k, v in row.items()})
        self.stdout.write(out.getvalue(), ending='')
        self.stderr.write(_summary_line(company, report['summary']))
