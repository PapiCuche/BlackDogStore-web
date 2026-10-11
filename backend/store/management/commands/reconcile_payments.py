"""
Ask the gateway about the payments whose notification never arrived.

    python manage.py reconcile_payments
    python manage.py reconcile_payments --hours 48      # a customer says they paid

Only «Mi Cuenta Web» has a web service for it (`Order/Get`); with the other
product active, or with nothing waiting, this does nothing and prints nothing.

Safe to run every five minutes and safe to run twice at once: an order is paid
under a row lock, once, by the same code that takes a notification. What it
prints is a count: no reference, no key, nothing about a buyer.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand

from store.payments import reconcile


class Command(BaseCommand):
    help = 'Consulta a la pasarela los pagos que siguen sin notificación (Order/Get). Idempotente.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours', type=int, default=None,
            help='Hasta cuántas horas atrás mirar. Por defecto, las dos últimas.')
        parser.add_argument(
            '--limit', type=int, default=reconcile.LIMIT,
            help='Cuántos intentos consultar como mucho en esta pasada.')

    def handle(self, *args, **options):
        window = timedelta(hours=options['hours']) if options['hours'] else reconcile.WINDOW
        summary = reconcile.reconcile_micuentaweb(window=window, limit=options['limit'])
        if summary.unconfigured:
            self.stdout.write('La pasarela no está configurada: no se consultó nada.')
        elif summary.asked:
            self.stdout.write(summary.line())
