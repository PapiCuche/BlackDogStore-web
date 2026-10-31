"""
FIN-REMIND-01 — the periodic finance pass: generate the periods of the fixed payments and send the internal
reminders, for every active company (or one) in ITS OWN local date.

Idempotent and safe to run twice at once: periods are unique per (rule, period) and every reminder is one event
per (obligation, schedule point, due date). It never registers a payment, closes or voids anything, and sends no
WhatsApp. Meant for the host's cron every 15 minutes (`deploy/crontab.example`); `generate_finance_obligations`
stays for running the generation alone.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from store.finance import reminders
from store.finance.services import generate_all
from store.models import Company


class Command(BaseCommand):
    help = 'Genera los pagos fijos que vencen pronto y envía los recordatorios internos (idempotente).'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Slug de una sola empresa.')
        parser.add_argument('--today', help='Fecha local AAAA-MM-DD (solo para pruebas y reparaciones).')

    def handle(self, *args, **options):
        company = None
        if options.get('company'):
            company = Company.objects.filter(slug=options['company']).first()
            if company is None:
                raise CommandError('No existe esa empresa.')
        today = None
        if options.get('today'):
            try:
                today = date.fromisoformat(options['today'])
            except ValueError:
                raise CommandError('Fecha inválida.') from None
        active = Company.objects.filter(is_active=True)
        generated = 0
        for row in (active.filter(pk=company.pk) if company else active):
            generated += generate_all(company=row, today=today)
        totals = reminders.run_all(company=company, today=today)
        self.stdout.write(
            f'Períodos generados: {generated}. Avisos enviados: {totals["emitted"]}. '
            f'Avisos pospuestos: {totals["snooze_fired"]}. Errores: {totals["errors"]}.')
