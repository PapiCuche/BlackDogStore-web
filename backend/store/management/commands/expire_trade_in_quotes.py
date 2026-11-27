"""
TRADE-IN VALUATION PRO · Phase 5a — `manage.py expire_trade_in_quotes [--company <slug>] [--dry-run]`

Stamps `expired` on every ISSUED trade-in quote whose exact expiry instant has passed. Idempotent: run it from cron
as often as wanted (hourly is enough). The panel never needs it — an issued quote past its instant already reads as
«Vencida» and cannot be answered — it only makes the stored state (and reports) say so, with one audit entry per
quote. One line per run for its log: `Cotizaciones de recepción vencidas: <n>`. A failure exits non-zero; each quote is
its own transaction. `--dry-run` counts and writes nothing.
"""
from django.core.management.base import BaseCommand, CommandError

from store.models import Company
from store.trade_in_quote import expire_due


class Command(BaseCommand):
    help = 'Marca como vencidas las cotizaciones de recepción cuya vigencia terminó.'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Sólo esta empresa (slug).')
        parser.add_argument('--dry-run', action='store_true', help='Cuenta las que vencería y no escribe nada.')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError('Empresa no encontrada.')
        if options.get('dry_run'):
            count = expire_due(company=company, dry_run=True)
            self.stdout.write(f'Cotizaciones de recepción por vencer: {count} (--dry-run: no se escribió nada)')
            return
        count = expire_due(company=company)
        self.stdout.write(f'Cotizaciones de recepción vencidas: {count}')
