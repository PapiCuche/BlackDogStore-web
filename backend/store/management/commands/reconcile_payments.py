"""
Ask the gateway about the payments whose notification never arrived.

    python manage.py reconcile_payments
    python manage.py reconcile_payments --reference 12345678901234567890   # a customer says they paid
    python manage.py reconcile_payments --hours 48      # everything still waiting, two days back

Only «Mi Cuenta Web» has a web service for it (`Order/Get`); with the other
product active, or with nothing waiting, this does nothing and prints nothing.

Safe to run every five minutes and safe to run twice at once: an order is paid
under a row lock, once, by the same code that takes a notification. What it
prints is a count: no reference, no key, nothing about a buyer.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from store.payments import reconcile


class Command(BaseCommand):
    help = 'Consulta a la pasarela los pagos que siguen sin notificación (Order/Get). Idempotente.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours', type=int, default=None,
            help='Hasta cuántas horas atrás mirar (1 o más). Por defecto, las dos últimas.')
        parser.add_argument(
            '--limit', type=int, default=reconcile.LIMIT,
            help='Cuántos intentos consultar como mucho en esta pasada.')
        parser.add_argument(
            '--reference', default='',
            help='Consultar UN intento, por su referencia (la del pago en la pasarela), tenga la '
                 'antigüedad que tenga y aunque se cerrara como rechazado o sin intento.')

    def handle(self, *args, **options):
        hours = options['hours']
        if hours is not None and hours < 1:
            # 0 used to mean «the default», silently: asking for nothing and getting two hours.
            raise CommandError('--hours tiene que ser 1 o más.')
        window = timedelta(hours=hours) if hours else reconcile.WINDOW
        summary = reconcile.reconcile_micuentaweb(
            window=window, limit=options['limit'], reference=(options['reference'] or '').strip())
        if summary.unconfigured:
            self.stdout.write('La pasarela no está configurada: no se consultó nada.')
        elif summary.asked:
            self.stdout.write(summary.line())
        elif options['reference']:
            self.stdout.write('No hay ningún intento de «Mi Cuenta Web» sin pago con esa referencia.')
