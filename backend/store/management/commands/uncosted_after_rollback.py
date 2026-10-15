"""
DEVICE-COMMERCE-01 · DC1 — after going BACK to a release before DC1 with the
cost migrations left applied, and FORWARD again.

The release before DC1 writes Kardex lines without a cost (`cost_source` ''
— the column's `db_default`) and never touches `BranchStock.average_cost` /
`uncosted_quantity`, while it does change the quantity. Back on this release,
a shelf may then count as «of known cost» units that came in without one.

    python manage.py uncosted_after_rollback --since 2026-10-20T14:00:00-05:00
    python manage.py uncosted_after_rollback --since 2026-10-20T14:00:00-05:00 --apply

`--since`: the moment the previous release went back online (a little earlier
is fine). Lines without a cost source written BEFORE DC1 are history and are
never touched — hence the timestamp.

REPORT BY DEFAULT: one line per shelf (branch · product counted by quantity)
with lines without a cost since then, and what `--apply` would leave. Writes
nothing.

`--apply`, per shelf, under its stock row lock: the units those lines BROUGHT
IN join the pending part, conservatively — as if every exit since then took
units of known cost, so the known-cost part is never overstated:

    pending ← min(quantity, pending + units entered without a cost since --since)

and the average is cleared when no unit of known cost is left, as the cost
rule does. It NEVER invents a cost: no line gets one, the average of the
known part is not recalculated. The lines it counted are marked `unknown`
(which is what they are), so running it again changes nothing. One audit row
per shelf (`uncosted_recomputed_after_rollback`).

Devices (serialized products) carry their own cost per unit and the branch
keeps no average for them: they are counted in the report, never written.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from store import inventory_services
from store.models import AdminAuditLog, Branch, BranchStock, Product, StockMovement

ACTION = 'uncosted_recomputed_after_rollback'


def _since(value: str) -> datetime:
    moment = parse_datetime((value or '').strip())
    if moment is None:
        raise CommandError('--since necesita una fecha y hora ISO, p. ej. 2026-10-20T14:00:00-05:00.')
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


def _lines(branch_id, product_id, since):
    return StockMovement.objects.filter(branch_id=branch_id, product_id=product_id, created_at__gte=since,
                                        cost_source='')


def _plan(stock: BranchStock, since) -> tuple[int, int, int, object]:
    """(lines, units in without cost, pending after, average after) of one shelf, from its current row."""
    lines = _lines(stock.branch_id, stock.product_id, since)
    count = lines.count()
    entered = lines.filter(movement_type__in=StockMovement.ENTRY_TYPES).aggregate(n=Sum('quantity'))['n'] or 0
    quantity = max(int(stock.quantity or 0), 0)
    pending = min(quantity, int(stock.uncosted_quantity or 0) + entered)
    average = stock.average_cost if (quantity == 0 or quantity - pending > 0) else None
    return count, entered, pending, average


class Command(BaseCommand):
    help = ('Tras volver a una versión anterior a DC1 y regresar: estantes con movimientos sin costo desde '
            '--since. Sólo informa; con --apply los marca como costo pendiente, sin inventar costos.')

    def add_arguments(self, parser):
        parser.add_argument('--since', required=True,
                            help='Cuándo volvió a estar en línea la versión anterior (ISO 8601).')
        parser.add_argument('--apply', action='store_true',
                            help='Marca como costo pendiente lo que entró sin costo.')

    def handle(self, *args, **options):
        since = _since(options['since'])
        apply = options['apply']
        shelves = sorted(set(
            StockMovement.objects.filter(created_at__gte=since, cost_source='')
            .values_list('branch_id', 'product_id')
        ))
        if not apply:
            self.stdout.write('Informe: no se cambia nada. Repite con --apply para marcar el costo pendiente.')
        devices = changed = 0
        for branch_id, product_id in shelves:
            product = Product.objects.get(pk=product_id)
            branch = Branch.objects.get(pk=branch_id)
            label = f'{branch.name} · {product.name}'
            if product.is_serialized:
                devices += 1
                self.stdout.write(f'{label}: equipos con costo por unidad; no se escribe nada (revisa sus costos '
                                  'en Inventario › Equipos).')
                continue
            if not apply:
                stock = BranchStock.objects.filter(branch_id=branch_id, product_id=product_id).first()
                if stock is None:
                    continue
                count, entered, pending, average = _plan(stock, since)
                self.stdout.write(
                    f'{label}: {count} movimiento(s) sin costo, {entered} unidad(es) entraron sin costo; '
                    f'stock {stock.quantity}, costo pendiente {stock.uncosted_quantity} → {pending}'
                    f'{"; el promedio quedaría vacío" if average is None and stock.average_cost is not None else ""}.')
                continue
            with transaction.atomic():
                stock = inventory_services._locked_branch_stocks(branch, [product])[product.pk]
                count, entered, pending, average = _plan(stock, since)
                if not count:
                    continue        # another run got here first
                before = (stock.uncosted_quantity, stock.average_cost)
                BranchStock.objects.filter(pk=stock.pk).update(uncosted_quantity=pending, average_cost=average)
                _lines(branch_id, product_id, since).update(cost_source=StockMovement.COST_UNKNOWN)
                AdminAuditLog.log(
                    actor=None, action=ACTION, target_type='branch_stock', target_id=stock.pk,
                    company=branch.company,
                    metadata={'branch_id': branch_id, 'product_id': product_id, 'since': since.isoformat(),
                              'lines': count, 'units_entered_without_cost': entered,
                              'uncosted_before': before[0], 'uncosted_after': pending,
                              'average_cleared': before[1] is not None and average is None},
                )
                changed += 1
                self.stdout.write(f'{label}: costo pendiente {before[0]} → {pending}.')
        self.stdout.write(f'Estantes con movimientos sin costo desde {since.isoformat()}: {len(shelves)} '
                          f'(equipos: {devices}). Actualizados: {changed}.')
