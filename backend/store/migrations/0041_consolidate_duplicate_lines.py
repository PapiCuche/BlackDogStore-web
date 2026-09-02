"""
Consolidate duplicate basket and order lines — Phase 0.3 / P0-E.

WHY THIS RUNS BEFORE THE CONSTRAINTS
------------------------------------
The next migration adds `UNIQUE(session_key, product)` to `CartItem` and
`UNIQUE(order, product)` to `OrderItem`. Applying either against a database that
already holds a duplicate fails at the moment of the ALTER, in production, with
an error that names an index rather than the problem. So the rows are reconciled
first, in their own step, and the constraints go on afterwards.

WHAT A DUPLICATE MEANS IN EACH TABLE
------------------------------------
`CartItem` — two rows are one shopper's intent, split by a race between reading
and writing. Summing the quantities is what they meant: they asked for two, then
for three, and they want five. Nothing is lost.

`OrderItem` — two lines of one product carry no information a single line with
the summed quantity does not, BECAUSE the price on a line is the product's price
at checkout, so both rows carry the same one. That is an assumption about the
data, not a law, and it is CHECKED rather than trusted: if two lines of one
product in one order disagree about price, this migration STOPS.

    Adding the two quantities under one of the two prices would silently change
    what a customer was charged, on an order that has already been paid, in a
    migration nobody is watching. Refusing to run is the smaller harm: it is
    visible, it is reversible, and the person who knows what those rows mean can
    decide.

Nothing here touches money. Quantities are added; prices are only compared.
"""

from django.db import migrations
from django.db.models import Count


def consolidate(apps, schema_editor):
    CartItem = apps.get_model('store', 'CartItem')
    OrderItem = apps.get_model('store', 'OrderItem')

    # --- baskets ----------------------------------------------------------
    cart_groups = (
        CartItem.objects.values('session_key', 'product_id')
        .annotate(n=Count('id')).filter(n__gt=1)
    )
    cart_merged = 0
    for group in cart_groups:
        rows = list(
            CartItem.objects
            .filter(session_key=group['session_key'], product_id=group['product_id'])
            .order_by('pk')
        )
        keeper, rest = rows[0], rows[1:]
        keeper.quantity = sum(row.quantity for row in rows)
        keeper.save(update_fields=['quantity'])
        CartItem.objects.filter(pk__in=[row.pk for row in rest]).delete()
        cart_merged += len(rest)

    # --- order lines ------------------------------------------------------
    order_groups = (
        OrderItem.objects.values('order_id', 'product_id')
        .annotate(n=Count('id')).filter(n__gt=1)
    )
    conflicting = []
    order_merged = 0
    for group in order_groups:
        rows = list(
            OrderItem.objects
            .filter(order_id=group['order_id'], product_id=group['product_id'])
            .order_by('pk')
        )
        prices = {row.price for row in rows}
        if len(prices) > 1:
            conflicting.append(
                f"pedido {group['order_id']} producto {group['product_id']}: "
                f"precios {sorted(str(p) for p in prices)}"
            )
            continue
        keeper, rest = rows[0], rows[1:]
        keeper.quantity = sum(row.quantity for row in rows)
        keeper.save(update_fields=['quantity'])
        OrderItem.objects.filter(pk__in=[row.pk for row in rest]).delete()
        order_merged += len(rest)

    if conflicting:
        raise RuntimeError(
            'Fase P0-E: hay líneas repetidas del mismo producto con PRECIOS '
            'DISTINTOS, y sumarlas cambiaría lo que se cobró en un pedido ya '
            'emitido. Esta migración se detiene a propósito; decide caso por '
            'caso antes de continuar.\n  ' + '\n  '.join(conflicting)
        )

    if cart_merged or order_merged:
        print(
            f'\n  Fase P0-E — {cart_merged} línea(s) de carrito y '
            f'{order_merged} línea(s) de pedido consolidadas por duplicidad.'
        )


def unconsolidate(apps, schema_editor):
    """
    Deliberately a no-op.

    Splitting a merged line back into the rows it came from would mean inventing
    a division that no longer exists anywhere — how the five units were once two
    and three is not recorded, and guessing it would fabricate history. The
    forward step is safe to re-run, which is what reversibility is for here.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0040_diagnostic_capability_for_untouched_admin_presets'),
    ]

    operations = [
        migrations.RunPython(consolidate, unconsolidate),
    ]
