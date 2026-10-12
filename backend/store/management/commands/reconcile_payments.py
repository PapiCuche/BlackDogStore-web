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
        reference = (options['reference'] or '').strip()
        summary = reconcile.reconcile_micuentaweb(window=window, limit=options['limit'], reference=reference)
        if summary.unconfigured:
            self.stdout.write('La pasarela no está configurada: no se consultó nada.')
        elif reference:
            self.stdout.write(self._about_one(summary))
        elif summary.asked or summary.foreign:
            self.stdout.write(summary.line())

    #: What asking about ONE reference came to, said as what happened.
    ONE = {
        reconcile.PAID: 'El pedido quedó pagado: la pasarela tenía el cobro.',
        reconcile.DUPLICATE: 'COBRO DUPLICADO: la pasarela cobró este intento y el pedido ya estaba pagado por otro. '
                             'Hay que devolverlo desde el panel de la pasarela.',
        reconcile.REJECTED: 'La pasarela lo tiene como rechazado.',
        reconcile.MISMATCHED: 'Lo que tiene la pasarela no coincide con lo que abrió la tienda: quedó como fallo de '
                              'integridad. Revísalo en el panel de la pasarela.',
        reconcile.ABANDONED: 'La pasarela no tiene ningún intento de pago bajo esa referencia.',
        reconcile.WAITING: 'La pasarela no tiene ningún intento de pago bajo esa referencia.',
        reconcile.RUNNING: 'La pasarela todavía lo tiene en curso: no está pagado ni rechazado.',
        reconcile.UNDECIDED: 'Lo que tiene la pasarela bajo esa referencia no se puede decidir desde aquí (dos cobros, '
                             'un reembolso o un importe parcial). Revísalo en su panel.',
        reconcile.UNREADABLE: 'La respuesta de la pasarela no se pudo leer. Revísalo en su panel.',
        reconcile.ERROR: 'La pasarela contestó con un error a la consulta de esa referencia.',
        reconcile.ALREADY: 'Ese intento ya tiene su pago.',
        reconcile.UNREACHABLE: 'La pasarela no respondió. Vuelve a intentarlo.',
        reconcile.REFUSED: 'La pasarela rechazó la consulta (credenciales o límite de llamadas).',
        reconcile.FOREIGN: 'Ese intento se abrió con otra tienda o en otro entorno que los de la pasarela que cobra '
                           'ahora: no se consulta aquí. Revísalo en el panel de la pasarela con la que se abrió.',
    }

    def _about_one(self, summary) -> str:
        if summary.inactive:
            return '«Mi Cuenta Web» no es la pasarela que cobra ahora: no se puede consultar esa referencia.'
        if not summary.outcomes:
            return 'No hay ningún intento de «Mi Cuenta Web» sin pago con esa referencia.'
        return self.ONE[next(iter(summary.outcomes.values()))]
