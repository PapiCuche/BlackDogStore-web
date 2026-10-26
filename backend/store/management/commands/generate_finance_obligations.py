"""
FIN-B1 — create the periods of every recurring payment that fall due within the
horizon (35 local days), for every company or one (`--company <slug>`).

Idempotent and safe to run twice at once: each rule is locked while it
generates, and (rule, period) is unique besides. It never closes, deletes or
expires anything: an overdue period stays open until it is paid or voided.
FIN-C folds this into `run_finance_schedule`; until then it runs from the
host's cron like the other periodic commands (`deploy/crontab.example`).
"""
from django.core.management.base import BaseCommand, CommandError

from store.finance.services import generate_all
from store.models import Company


class Command(BaseCommand):
    help = 'Genera los períodos de los pagos fijos que vencen dentro del horizonte (idempotente).'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Slug de una sola empresa.')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError('No existe esa empresa.')
        self.stdout.write(f'Períodos de pagos fijos generados: {generate_all(company=company)}.')
