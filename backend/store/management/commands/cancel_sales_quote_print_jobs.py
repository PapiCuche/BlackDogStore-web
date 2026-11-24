"""
SALES-QUOTE-01 — `manage.py cancel_sales_quote_print_jobs [--apply]`

BEFORE ROLLING THE CODE BACK to a release without commercial quotes. That release's print queue
(`printing.services.claim_next`) reads `job.order` of every ticket it claims; a quote ticket has no
order (a quote never charged anything), so one still waiting would raise on every claim and stop its
branch's queue. This cancels the quote tickets that have not finished (pending or printing), with one
audit line per company. Without `--apply` it only says how many there are. Idempotent.

Printed, failed and cancelled ones are left as they are: the previous release never claims them.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from store.models import AdminAuditLog, PrintJob

LIVE = (PrintJob.Status.PENDING, PrintJob.Status.PRINTING)
REASON = 'Cancelado antes de volver a una versión sin cotizaciones.'


class Command(BaseCommand):
    help = 'Cancela los tickets de cotización sin terminar antes de volver a una versión anterior a SALES-QUOTE-01.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Cancelarlos (sin esto, sólo los cuenta).')

    def handle(self, *args, **options):
        jobs = PrintJob.objects.filter(kind=PrintJob.Kind.SALES_QUOTE_TICKET, status__in=LIVE)
        count = jobs.count()
        if not options['apply']:
            self.stdout.write(f'Tickets de cotización sin terminar: {count} (simulación; usa --apply para cancelarlos)')
            return
        with transaction.atomic():
            locked = list(jobs.select_for_update().select_related('company'))
            by_company = Counter()
            companies = {}
            for job in locked:
                job.status = PrintJob.Status.CANCELLED
                job.claim_token = ''
                job.lease_expires_at = None
                job.last_error = REASON
                job.save(update_fields=['status', 'claim_token', 'lease_expires_at', 'last_error', 'updated_at'])
                by_company[job.company_id] += 1
                companies[job.company_id] = job.company
            for company_id, cancelled in by_company.items():
                AdminAuditLog.log(actor=None, action='sales_quote_print_jobs_cancelled_before_rollback',
                                  target_type='company', target_id=company_id,
                                  metadata={'company_id': company_id, 'cancelled': cancelled},
                                  company=companies[company_id])
        self.stdout.write(f'Tickets de cotización cancelados: {sum(by_company.values())}')
