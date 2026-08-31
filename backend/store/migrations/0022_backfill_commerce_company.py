"""
Backfill Order.company and Coupon.company — SaaS Phase 2C.

ORDERS
------
An order's tenant is derived from what it actually sold:

    Order -> OrderItem -> Product.company

If every item agrees, that is the order's company. There is no guessing.

MIXED ORDERS FAIL LOUDLY
------------------------
If one order holds products from two companies, this migration RAISES. That
state is logically incompatible with a multi-tenant system: there is no correct
answer to "whose order is this?", and picking one would quietly assign another
company's revenue, stock movements and customer data to the wrong tenant. The
error names the offending orders so the operator can split or void them.

ORDERS WITHOUT ITEMS
--------------------
Historic orders with no items cannot derive a tenant. They are assigned to the
PILOT tenant — the oldest Company, which is the signature migration 0015 left —
because an installation reaching this migration has been a single business until
now, so every pre-SaaS order demonstrably belongs to it. This is a HISTORICAL
judgement, valid once; nothing at runtime resolves a company this way.

COUPONS
-------
Same reasoning: pre-existing coupons belong to the single historic business.

A company created later starts with zero coupons and zero orders — nothing here
copies anything to a new tenant.
"""

from django.db import migrations


def _pilot(Company):
    return Company.objects.order_by('pk').first()


def backfill_commerce_company(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    Coupon = apps.get_model('store', 'Coupon')
    Order = apps.get_model('store', 'Order')
    OrderItem = apps.get_model('store', 'OrderItem')

    pending_orders = Order.objects.filter(company__isnull=True)
    pending_coupons = Coupon.objects.filter(company__isnull=True)

    if not pending_orders.exists() and not pending_coupons.exists():
        return

    pilot = _pilot(Company)
    if pilot is None:
        raise RuntimeError(
            'No existe ninguna empresa a la que asignar los pedidos y cupones '
            'existentes. La migración 0015 debería haber creado la empresa '
            'piloto; restáurela antes de aplicar 0022.'
        )

    # --- orders: derive from their items ------------------------------------
    companies_by_order: dict[int, set[int]] = {}
    for order_id, company_id in OrderItem.objects.filter(
        order__company__isnull=True,
    ).values_list('order_id', 'product__company_id'):
        companies_by_order.setdefault(order_id, set()).add(company_id)

    mixed = {
        order_id: sorted(c for c in companies if c is not None)
        for order_id, companies in companies_by_order.items()
        if len({c for c in companies if c is not None}) > 1
    }
    if mixed:
        raise RuntimeError(
            'Hay pedidos con productos de varias empresas; no existe una '
            'respuesta correcta a de quién es el pedido, así que la migración se '
            'detiene en lugar de adivinar.\n'
            + '\n'.join(
                f'  Pedido {order_id}: empresas {companies}'
                for order_id, companies in sorted(mixed.items())
            )
            + '\nDivida o anule esos pedidos y vuelva a aplicar la migración.'
        )

    for order_id, companies in companies_by_order.items():
        resolved = {c for c in companies if c is not None}
        if len(resolved) == 1:
            Order.objects.filter(pk=order_id).update(company_id=resolved.pop())

    # Orders with no items (or whose items had no company) fall back to the
    # pilot: pre-SaaS data of the single historic business.
    Order.objects.filter(company__isnull=True).update(company=pilot)

    # --- coupons ------------------------------------------------------------
    pending_coupons.update(company=pilot)


def unbackfill_commerce_company(apps, schema_editor):
    """
    Reverse: release orders and coupons again.

    Nothing is deleted — this migration created no row, so it removes none. Rows
    an operator later moved to another tenant are left alone.
    """
    Company = apps.get_model('store', 'Company')
    Coupon = apps.get_model('store', 'Coupon')
    Order = apps.get_model('store', 'Order')

    pilot = _pilot(Company)
    if pilot is None:
        return

    Order.objects.update(company=None)
    Coupon.objects.filter(company=pilot).update(company=None)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0021_commerce_company_nullable'),
    ]

    operations = [
        migrations.RunPython(backfill_commerce_company, unbackfill_commerce_company),
    ]
