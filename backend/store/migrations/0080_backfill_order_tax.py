"""
C2.1 — desglosar las ventas ya registradas SIN cambiar lo que cobraron.

LA INVARIANTE QUE ESTA MIGRACIÓN NO PUEDE ROMPER
------------------------------------------------
    `total` no se toca. Ni una fila.

Los precios del catálogo son finales: `total` es el importe que la tienda cobró
de verdad. Añadir el 18 % encima convertiría una venta de S/ 118 en S/ 139,24 y
reescribiría la historia contable. El desglose se obtiene HACIA ATRÁS, separando
del total lo que ya era tributo.

    base     = total / 1.18   redondeado a céntimos
    impuesto = total − base   POR DIFERENCIA

Restando y no multiplicando, `base + impuesto = total` se cumple exactamente en
todas las filas, sea cual sea el importe.

LA TASA SE ESCRIBE, NO SE DEDUCE
--------------------------------
Se guarda 0.18 literal en cada fila. Podría parecer redundante hoy, pero la Ley
N.º 32387 cambia el reparto interno del 18 % cada año hasta 2029: una lectura
futura que consultara «la tasa vigente» devolvería otra cosa para un documento
de 2026. La fila lleva la suya.

El literal está aquí escrito a mano a propósito, y no importado del motor de
cálculo: una migración tiene que seguir haciendo lo mismo dentro de cinco años,
y para eso no puede depender de lo que el código signifique entonces.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

CENT = Decimal('0.01')

#: La tasa vigente EN EL MOMENTO DE ESTA MIGRACIÓN. Congelada aquí.
RATE_AT_MIGRATION_TIME = Decimal('0.18')


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def backfill(apps, schema_editor):
    Order = apps.get_model('store', 'Order')

    updated = 0
    # Por lotes: una tienda con historia larga no cabe en memoria de golpe.
    queryset = Order.objects.filter(tax_rate__isnull=True).only(
        'id', 'total', 'discount_amount',
    )
    batch = []
    for order in queryset.iterator(chunk_size=500):
        total = _money(order.total or 0)
        discount = _money(order.discount_amount or 0)
        taxable = _money(total / (Decimal('1') + RATE_AT_MIGRATION_TIME))

        order.currency = 'PEN'
        order.subtotal_amount = _money(total + discount)
        order.taxable_amount = taxable
        # Por diferencia. Nunca `taxable * rate`.
        order.tax_amount = _money(total - taxable)
        order.tax_rate = RATE_AT_MIGRATION_TIME
        order.tax_treatment = 'taxed'
        batch.append(order)
        if len(batch) >= 500:
            Order.objects.bulk_update(batch, [
                'currency', 'subtotal_amount', 'taxable_amount',
                'tax_amount', 'tax_rate', 'tax_treatment',
            ])
            updated += len(batch)
            batch = []

    if batch:
        Order.objects.bulk_update(batch, [
            'currency', 'subtotal_amount', 'taxable_amount',
            'tax_amount', 'tax_rate', 'tax_treatment',
        ])
        updated += len(batch)

    if updated:
        print(
            f'\n  C2.1 — {updated} venta(s) desglosadas al 18 %; '
            f'ningún total modificado\n'
        )


def unbackfill(apps, schema_editor):
    """Retira sólo el desglose. `total` nunca se tocó, así que no hay nada que restaurar."""
    Order = apps.get_model('store', 'Order')
    Order.objects.filter(tax_rate=RATE_AT_MIGRATION_TIME).update(
        subtotal_amount=None, taxable_amount=None, tax_amount=None, tax_rate=None,
    )


class Migration(migrations.Migration):

    dependencies = [('store', '0079_order_tax_snapshot')]

    operations = [migrations.RunPython(backfill, unbackfill)]
