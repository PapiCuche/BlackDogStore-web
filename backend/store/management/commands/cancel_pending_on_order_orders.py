"""
DC3 — before going back to a release that predates DC3: the online
orders still waiting for payment that carry a line sold on order (`OrderItem.supply_origin`).

The release before DC3 does not know on-order lines: paying one of these there
would take its goods from a shelf that does not have them. SIMULATION BY DEFAULT: it lists
them and changes nothing. `--apply` cancels each one (status «cancelled», a
note in `payment_error`, an audit row under its company). Paid orders are not
touched. Pause payments in the integrations console first: a form
that was already open can still be paid, and that payment needs a person.

It also EMPTIES the cart lines taken on order (`CartItem.supply_origin`
'supplier'): the previous release reads them as shelf lines and would sell
them from a shelf that does not have them. The simulation counts them; `--apply`
deletes them and writes one audit row per company with the count.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from django.db.models import Count

from store.models import AdminAuditLog, CartItem, Company, Order

NOTE = 'Cancelado antes de volver a la versión anterior: el pedido lleva productos bajo pedido.'


class Command(BaseCommand):
    help = 'Lista (o cancela con --apply) los pedidos pendientes de pago con líneas bajo pedido.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Cancela los pedidos listados.')

    def handle(self, *args, **options):
        pending = (
            Order.objects.filter(status=Order.Status.PENDING_PAYMENT, paid=False, items__supply_origin='supplier')
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
                    metadata={'reason': 'on-order lines before rollback'}, company=order.company,
                )
                cancelled += 1
        self.stdout.write(f'Pedidos bajo pedido pendientes de pago: {len(ids)}. Cancelados: {cancelled}.')

        lines = CartItem.objects.filter(supply_origin=CartItem.SUPPLY_SUPPLIER)
        total = lines.count()
        if not apply:
            self.stdout.write(f'Líneas de carrito bajo pedido: {total}.')
            return
        with transaction.atomic():
            per_company = list(lines.values('product__company_id').annotate(n=Count('pk')).order_by('product__company_id'))
            _, by_model = lines.delete()
            removed = by_model.get(CartItem._meta.label, 0)
            for row in per_company:
                AdminAuditLog.log(
                    actor=None, action='on_order_cart_lines_removed_before_rollback', target_type='cart',
                    target_id='', metadata={'count': row['n']},
                    company=Company.objects.filter(pk=row['product__company_id']).first(),
                )
        self.stdout.write(f'Líneas de carrito bajo pedido: {total}. Quitadas: {removed}.')
