"""
SALES-QUOTE-01 — `manage.py expire_sales_quotes [--company <slug>]`

Stamps `expired` on every approved, sent or accepted quote whose validity ended before today (the
shop's time zone). Idempotent: run it from cron as often as wanted. The panel never needs it — a
quote past its validity already reads as «Vencida» and cannot be sent, answered or converted — it
only makes the stored state (and reports) say so, with an audit entry per quote.
"""
from django.core.management.base import BaseCommand, CommandError

from store.models import Company
from store.sales_quote_services import expire_due


class Command(BaseCommand):
    help = 'Marca como vencidas las cotizaciones cuya vigencia terminó.'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Sólo esta empresa (slug).')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError('Empresa no encontrada.')
        count = expire_due(company=company)
        self.stdout.write(f'Cotizaciones vencidas: {count}')
