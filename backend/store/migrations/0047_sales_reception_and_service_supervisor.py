"""
M11 — give `Ventas` its reception capabilities, and offer `Supervisor Técnico`.

TWO OPERATIONS, TWO DIFFERENT RISKS, AND THEY ARE NOT THE SAME.

1. EXTENDING AN EXISTING ROLE changes what people who already hold it may do,
   without anybody deciding it. That is the failure mode migrations 0033, 0037,
   0040 and 0045 all guarded against, and this one guards it the same way: a
   role is extended only if its capability set is EXACTLY the old `Ventas`
   preset — equality in both directions. One capability added or removed and
   the role belongs to the tenant, who edited it on purpose and does not need
   the platform overruling them.

   Companies that renamed the role keep their name: the discriminator is the
   CAPABILITY SET, not the label. A shop that called it "Mostrador" but never
   touched what it grants is still running the untouched preset.

2. CREATING A NEW ROLE grants nobody anything. A `CompanyRole` with no
   assignments is an offer, not authority — somebody still has to give it to a
   person. So `Supervisor Técnico` is created for every company that lacks it,
   with no equality test to pass, and the risk is a spare row rather than a
   silent promotion.

Both halves are idempotent: re-running finds the roles already updated/created
and does nothing.
"""

from django.db import migrations

# The `Ventas` preset exactly as it stood before M11. Frozen here on purpose:
# this migration must compare against what the preset WAS, and importing the
# live tuple would compare it against what it is now and match nothing.
PREVIOUS_SALES_PRESET = frozenset({
    'company.view', 'products.view', 'reports.view',
    'sales.orders.view', 'sales.orders.manage', 'sales.notes.manage',
    'sales.pos.use',
})

RECEPTION_CAPABILITIES = (
    'service.customers.view', 'service.customers.manage',
    'service.devices.view', 'service.devices.manage',
    'service.orders.create', 'service.orders.view',
)


def extend_untouched_sales_presets(apps, schema_editor):
    CompanyRole = apps.get_model('store', 'CompanyRole')

    updated = 0
    for role in CompanyRole.objects.all().iterator():
        if frozenset(role.capabilities or []) != PREVIOUS_SALES_PRESET:
            continue
        role.capabilities = sorted(PREVIOUS_SALES_PRESET | set(RECEPTION_CAPABILITIES))
        role.save(update_fields=['capabilities'])
        updated += 1

    if updated:
        print(
            f'\n  M11 — recepción técnica otorgada a {updated} rol(es) de ventas '
            f'sin modificar.'
        )


def create_service_supervisor_preset(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    CompanyRole = apps.get_model('store', 'CompanyRole')

    # Read from the live definition: this role is NEW, so there is no "what it
    # used to be" to compare against, and the preset module is the authority on
    # what it contains.
    from store.company_provisioning import _SERVICE_SUPERVISOR_CAPS

    created = 0
    for company in Company.objects.all().iterator():
        if CompanyRole.objects.filter(company=company, slug='supervisor-tecnico').exists():
            continue
        CompanyRole.objects.create(
            company=company,
            name='Supervisor Técnico',
            slug='supervisor-tecnico',
            description='Supervisión del taller: órdenes, asignación, diagnóstico y reparación.',
            capabilities=sorted(_SERVICE_SUPERVISOR_CAPS),
            is_active=True,
        )
        created += 1

    if created:
        print(f'\n  M11 — rol Supervisor Técnico creado en {created} empresa(s).')


def drop_service_supervisor_preset(apps, schema_editor):
    """
    Reverse: remove the offered role, but only where nobody took it up.

    A role somebody was actually assigned is not this migration's to delete —
    that would revoke a person's authority as a side effect of a downgrade, and
    `PROTECT` on the assignment would refuse anyway.
    """
    CompanyRole = apps.get_model('store', 'CompanyRole')
    for role in CompanyRole.objects.filter(slug='supervisor-tecnico'):
        if not role.assignments.exists():
            role.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0046_merge_payments_and_service_execution'),
    ]

    operations = [
        migrations.RunPython(
            extend_untouched_sales_presets, migrations.RunPython.noop,
        ),
        migrations.RunPython(
            create_service_supervisor_preset, drop_service_supervisor_preset,
        ),
    ]
