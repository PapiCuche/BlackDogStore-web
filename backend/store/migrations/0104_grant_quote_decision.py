"""
QUOTE-DECISION — `service.quotes.record_decision` for the presets that never diverged.

Recording that a customer approved or rejected a quote —at the counter, by
phone, by message— is a new act with its own capability. It is deliberately not
implied by `service.diagnostic.manage`: whoever composes a price should not, by
that alone, be able to declare it accepted.

WHICH PRESETS GET IT, a product decision:
  · `Administrador`      — it holds everything by definition.
  · `Ventas`             — the counter, where the customer says "go ahead".
  · `Supervisor Técnico` — answers for the workshop to the customer.
`Servicio Técnico` does NOT: the technician quotes; somebody else records the
answer.

ONLY UNTOUCHED PRESETS, as in 0094 and 0095. A role is extended when its
capability set is exactly what the platform shipped immediately before this
migration (and, for the named presets, its slug is the preset's). A role a
tenant edited is theirs and is left as it is. The previous sets are FROZEN
here, `Administrador` included: importing them live would compare the database
against what the preset means in some later version of the code.

Not reversed on rollback: the capability may since have been granted by the
tenant on purpose.
"""
from django.db import migrations

NEW_CAPABILITIES = ('service.quotes.record_decision',)

ADMIN_PREVIOUS = frozenset({
    'areas.manage', 'communications.manage', 'company.manage', 'company.view',
    'inventory.adjust', 'inventory.reports', 'inventory.view', 'memberships.manage',
    'memberships.view', 'products.manage', 'products.view', 'reports.view',
    'roles.manage', 'sales.analytics.view', 'sales.commissions.manage',
    'sales.commissions.view', 'sales.discounts.apply', 'sales.fiscal.issue',
    'sales.fiscal.view', 'sales.notes.manage', 'sales.orders.manage',
    'sales.orders.view', 'sales.pos.assign_seller', 'sales.pos.use',
    'sales.promotions.manage', 'sales.promotions.view', 'service.customers.manage',
    'service.customers.view', 'service.delivery.manage', 'service.devices.manage',
    'service.devices.view', 'service.diagnostic.manage', 'service.manage',
    'service.orders.assign', 'service.orders.create', 'service.orders.manage',
    'service.orders.view', 'service.payments.collect', 'service.payments.manage',
    'service.quality.manage', 'service.repair.manage', 'settings.manage',
    'settings.view',
})

SALES_PREVIOUS = frozenset({
    'company.view', 'products.view', 'reports.view', 'sales.fiscal.view',
    'sales.notes.manage', 'sales.orders.manage', 'sales.orders.view', 'sales.pos.use',
    'service.customers.manage', 'service.customers.view', 'service.delivery.manage',
    'service.devices.manage', 'service.devices.view', 'service.orders.assign',
    'service.orders.create', 'service.orders.view', 'service.payments.manage',
})

SUPERVISOR_PREVIOUS = frozenset({
    'company.view', 'reports.view', 'service.customers.manage',
    'service.customers.view', 'service.delivery.manage', 'service.devices.manage',
    'service.devices.view', 'service.diagnostic.manage', 'service.manage',
    'service.orders.create', 'service.orders.manage', 'service.orders.view',
    'service.payments.collect', 'service.quality.manage', 'service.repair.manage',
})

PRESETS = {
    'ventas': SALES_PREVIOUS,
    'supervisor-tecnico': SUPERVISOR_PREVIOUS,
}


def grant(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')
    extended = 0
    for role in CompanyRole.objects.all().iterator():
        held = frozenset(role.capabilities or [])
        if held != ADMIN_PREVIOUS and not (role.slug in PRESETS and held == PRESETS[role.slug]):
            continue
        role.capabilities = sorted(held | frozenset(NEW_CAPABILITIES))
        role.save(update_fields=['capabilities', 'updated_at'])
        extended += 1
    if extended:
        print(f'\n  QUOTE-DECISION — registrar la decisión del cliente otorgado a {extended} rol(es) estándar sin modificar')


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0103_tracking_and_quote_decisions'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
