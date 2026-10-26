"""
FIN-E — create the receivables of the repairs whose quote was approved before
the receivable existed (docs/adr-finance-obligations.md §6.2).

Idempotent and safe to run twice at once: each repair is locked (RepairOrder →
Obligation) and re-checked under the lock, and one repair has at most one
receivable (unique). `--dry-run` writes nothing. `--company <slug>` limits it to
one company; without it each company is processed on its own. No amount is
copied: the balance of a repair is always read from its own quote and payments.
Not a data migration, on purpose: it can be repeated, and it can wait.
"""
from django.core.management.base import BaseCommand, CommandError

from store.finance.receivables import backfill_repair_receivables
from store.models import Company


class Command(BaseCommand):
    help = 'Crea las cuentas por cobrar de reparaciones aprobadas antes de FIN-E (idempotente).'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Slug de una sola empresa.')
        parser.add_argument('--dry-run', action='store_true', help='Cuenta lo que haría y no escribe nada.')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError('No existe esa empresa.')
        counts = backfill_repair_receivables(company=company, dry_run=bool(options.get('dry_run')))
        self.stdout.write(
            f"created={counts['created']} skipped_zero_total={counts['skipped_zero_total']} "
            f"due_dates_set={counts['due_dates_set']} errors={counts['errors']} companies={counts['companies']}"
            + (' (dry-run: nothing was written)' if counts['dry_run'] else ''))
        if counts['errors']:
            raise CommandError(f"{counts['errors']} reparaciones fallaron: revisa el registro.")
