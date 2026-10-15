"""
DC2 review — before going back to a release that predates DC2: the online
orders still waiting for payment that carry a device line (`OrderItem.stock_unit`).

The release before DC2 does not know device lines: paying one of these there
would not sell the device the buyer chose. SIMULATION BY DEFAULT: it lists
them and changes nothing. `--apply` cancels each one (status «cancelled», a
note in `payment_error`, an audit row under its company), so its devices stop
being kept for it. Pause payments in the integrations console first: a form
that was already open can still be paid, and that payment needs a person.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from store.models import AdminAuditLog, Order

NOTE = 'Cancelado antes de volver a la versión anterior: el pedido lleva equipos elegidos en la web.'


class Command(BaseCommand):
    help = 'Lista (o cancela con --apply) los pedidos web pendientes de pago con líneas de equipo.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Cancela los pedidos listados.')

    def handle(self, *args, **options):
        pending = (
            Order.objects.filter(status=Order.Status.PENDING_PAYMENT, paid=False, items__stock_unit__isnull=False)
            .distinct().order_by('pk')
        )
        ids = list(pending.values_list('pk', flat=True))
        apply = options['apply']
        if not apply:
            self.stdout.write('Simulación: no se cambia nada. Repite con --apply para cancelarlos.')
        cancelled = 0
        for pk in ids:
            self.stdout.write(f'Pedido #{pk}')
            if not apply:
                continue
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=pk)
                if order.status != Order.Status.PENDING_PAYMENT or order.paid:
                    continue        # paid or changed meanwhile: whoever changed it decided
                order.status = Order.Status.CANCELLED
                order.payment_error = ((order.payment_error + '\n') if order.payment_error else '') + NOTE
                order.save(update_fields=['status', 'payment_error'])
                AdminAuditLog.log(
                    actor=None, action='order_cancelled_before_rollback', target_type='order', target_id=order.pk,
                    metadata={'reason': 'device lines before rollback'}, company=order.company,
                )
                cancelled += 1
        self.stdout.write(f'Pedidos con equipos pendientes de pago: {len(ids)}. Cancelados: {cancelled}.')
