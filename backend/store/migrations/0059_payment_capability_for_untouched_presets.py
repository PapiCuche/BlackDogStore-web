"""
M12B — give the newly-assignable payment capability to the presets that never
diverged.

Seventh time this pattern appears. What is different here is WHICH presets it
reaches, and that is a product decision rather than a mechanical one:

  · `Administrador` — yes. It holds everything by definition.
  · `Ventas` — yes. It is the shop counter: the till, the sales note, technical
    reception since 0054, and now the money a customer hands over for a repair.
  · `Servicio Técnico` — NO, and deliberately. Authorised technicians manage
    the STATES of a repair; it does not follow that every technician handles
    cash. A shop that wants theirs to grants this to a role of its own, which is
    what a capability catalogue is for.
  · `Inventario` — no. Stock is a different kind of value.

THE DISCRIMINATORS, four fields for the small set and exact equality for the
large one, exactly as 0053 established.

`Ventas` needs the full conjunction here and did not get one in 0054 — that was
the defect G3 found and fixed, and repeating it would undo the fix in a
different file. Its historical capability set is a plausible hand-built role, so
slug, name, one of the platform's own descriptions AND the exact set must all
agree.

EVERY OTHER ROLE IS LEFT ALONE.
"""

from django.db import migrations

NEW_CAPABILITIES = ('service.payments.manage',)

SALES_NAME = 'Ventas'
SALES_SLUG = 'ventas'
#: Every description the platform has ever written for this preset. Two,
#: because the grants that extended it never rewrote the sentence — matching
#: only the current one would skip exactly the roles they repaired.
SALES_DESCRIPTIONS = (
    'Operación comercial: pedidos y notas de venta internas.',
    'Equivalente al rol legacy "sales".',
)
#: What `_SALES_CAPS` held immediately before M12B. Frozen: a live import would
#: compare the database against what the preset means today, in a process whose
#: code is always newer than its data.
SALES_PREVIOUS = frozenset({
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'sales.pos.use',
    'service.customers.view', 'service.customers.manage',
    'service.devices.view', 'service.devices.manage',
    'service.orders.create', 'service.orders.view',
})


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    from store.capabilities import ASSIGNABLE_CAPABILITY_CODES

    previous_admin_preset = (
        frozenset(ASSIGNABLE_CAPABILITY_CODES) - set(NEW_CAPABILITIES)
    )
    admins = 0
    sales = 0

    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])

        if previous_admin_preset and held == previous_admin_preset:
            role.capabilities = sorted(previous_admin_preset | set(NEW_CAPABILITIES))
            role.save(update_fields=['capabilities', 'updated_at'])
            admins += 1
            continue

        if (
            role.slug == SALES_SLUG
            and role.name == SALES_NAME
            and role.description in SALES_DESCRIPTIONS
            and held == SALES_PREVIOUS
        ):
            role.capabilities = sorted(held | set(NEW_CAPABILITIES))
            role.save(update_fields=['capabilities', 'updated_at'])
            sales += 1

    if admins or sales:
        print(
            f'\n  M12B — capacidad de cobro otorgada a {admins} rol(es) '
            f'administrador y {sales} rol(es) de ventas sin modificar'
        )


def revoke(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')
    for role in CompanyRole.objects.all().iterator():
        current = list(role.capabilities or [])
        remaining = [c for c in current if c not in NEW_CAPABILITIES]
        if len(remaining) != len(current):
            role.capabilities = remaining
            role.save(update_fields=['capabilities', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0058_service_payment_ledger'),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]
