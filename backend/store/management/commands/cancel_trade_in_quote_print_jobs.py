"""
TRADE-IN VALUATION PRO · 5b — `manage.py cancel_trade_in_quote_print_jobs [--apply]`

BEFORE ROLLING THE CODE BACK to a release without trade-in quote tickets (0199). That release's queue cancels a ticket
kind it does not know when it has no order, so nothing would crash; this cancels the trade-in quote tickets that have
not finished (pending or printing) or FAILED (someone could retry a failed one from the older screen) on purpose, with
one audit line per company, so none is left half-way. Without `--apply` it only counts them. Idempotent. Printed and
cancelled ones are left as they are.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from store.models import AdminAuditLog, Company, PrintJob

LIVE = (PrintJob.Status.PENDING, PrintJob.Status.PRINTING, PrintJob.Status.FAILED)
REASON = 'Cancelado antes de volver a una versión sin cotizaciones de recepción.'


class Command(BaseCommand):
    help = ('Cancela los tickets de cotización de recepción sin terminar o fallidos antes de volver a una versión '
            'anterior.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Cancelarlos (sin esto, sólo los cuenta).')

    def handle(self, *args, **options):
        jobs = PrintJob.objects.filter(kind=PrintJob.Kind.TRADE_IN_QUOTE_TICKET, status__in=LIVE)
        count = jobs.count()
        if not options['apply']:
            self.stdout.write(f'Tickets de cotización de recepción sin terminar o fallidos: {count} '
                              '(simulación; usa --apply para cancelarlos)')
            return
        with transaction.atomic():
            locked = list(jobs.select_for_update().order_by('pk'))
            by_company = Counter()
            for job in locked:
                job.status = PrintJob.Status.CANCELLED
                job.claim_token = ''
                job.lease_expires_at = None
                job.last_error = REASON
                job.save(update_fields=['status', 'claim_token', 'lease_expires_at', 'last_error', 'updated_at'])
                by_company[job.company_id] += 1
            companies = Company.objects.in_bulk(list(by_company))
            for company_id, cancelled in by_company.items():
                AdminAuditLog.log(actor=None, action='trade_in_quote_print_jobs_cancelled_before_rollback',
                                  target_type='company', target_id=company_id,
                                  metadata={'company_id': company_id, 'cancelled': cancelled},
                                  company=companies[company_id])
        self.stdout.write(f'Tickets de cotización de recepción cancelados: {sum(by_company.values())}')
