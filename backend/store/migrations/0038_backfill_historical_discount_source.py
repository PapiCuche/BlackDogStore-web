"""
Commercial Phase C1.3 — repair `discount_source` on orders that predate it.

THE DEFECT THIS FIXES
---------------------
Migration 0036 added `Order.discount_source` with a default of `none`, and the
migration's own note claimed that default "is what every historical order was".

That was wrong, and it was wrong about data the platform already had.
`Order.discount_amount` and `Order.coupon_code` have existed since long before
C1.2, and the storefront has been applying coupons at checkout since Phase 1. So
any order that came through with a coupon carried a real, coupon-sourced
discount — and 0036 labelled every one of them as having had no discount at all.

Nothing about the money was wrong: totals, `discount_amount` and `coupon_code`
were untouched then and are untouched now. What was wrong is the label, which is
exactly what a reporting screen reads to say WHY a sale was cheaper.

THE RULE, AND WHERE IT STOPS
----------------------------
    coupon_code != '' AND discount_amount > 0   →  coupon
    discount_amount == 0                        →  none (already correct)
    discount_amount > 0 AND coupon_code == ''    →  LEFT AS none, and counted

That third case is the one worth being careful about. A discount with no coupon
code could have come from an old manual adjustment, a data fix, a partially
migrated order, or a code path that no longer exists. Labelling it `manual`
would invent a decision — and `Order.discount_authorized_by` would be empty,
implying somebody authorised it and leaving no trace of who. An honest `none`
plus a printed count is better than a plausible fiction; the count tells an
operator exactly how many rows deserve a human look.

Nothing else is written. `total`, `discount_amount`, `coupon_code`,
`discount_reason` and `discount_authorized_by` are all left alone.
"""

from django.db import migrations
from django.db.models import Q


def repair(apps, schema_editor):
    Order = apps.get_model('store', 'Order')

    # Only rows 0036 could have mislabelled: it set every order to `none`, so
    # anything already carrying another source was written by C1.2 code and is
    # correct by construction.
    coupon_backed = Order.objects.filter(
        discount_source='none',
        discount_amount__gt=0,
    ).exclude(coupon_code='')

    repaired = coupon_backed.update(discount_source='coupon')

    ambiguous = Order.objects.filter(
        discount_source='none', discount_amount__gt=0, coupon_code='',
    ).count()

    if repaired or ambiguous:
        print(
            f'\n  Fase C1.3 — origen de descuento reparado en {repaired} pedido(s) '
            f'con cupón.'
        )
        if ambiguous:
            print(
                f'  {ambiguous} pedido(s) tienen descuento sin código de cupón. Se '
                f'dejan como "sin origen" a propósito: etiquetarlos como manual '
                f'inventaría una autorización que nadie dio. Requieren revisión '
                f'humana si importa clasificarlos.'
            )


def unrepair(apps, schema_editor):
    """
    Put the coupon-sourced rows back to `none`.

    Only touches rows this migration could have written — a coupon-backed
    discount labelled `coupon` — so a C1.2 sale that legitimately recorded
    `coupon` is indistinguishable and would also be reverted. That is accepted:
    the reverse exists to undo the forward step immediately, not to survive
    weeks of trading, and the forward step is idempotent so re-applying repairs
    both.
    """
    Order = apps.get_model('store', 'Order')
    Order.objects.filter(
        discount_source='coupon', discount_amount__gt=0,
    ).exclude(coupon_code='').update(discount_source='none')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0037_commission_capabilities_for_untouched_presets'),
    ]

    operations = [
        migrations.RunPython(repair, unrepair),
    ]
