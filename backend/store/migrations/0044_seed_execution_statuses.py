"""
M10 / BR-005C — give the companies that already exist their three new states.

Same pair as always: this migration seeds the companies that exist the moment it
runs, and `provision_company_access_defaults()` seeds the ones created tomorrow.
Neither half is optional and neither is a duplicate of the other.

The list is FROZEN here on purpose and is not imported from
`company_provisioning`: a migration must reproduce what it did on the day it
ran, and importing the runtime module would let a future edit to the default
labels retroactively rewrite history. A synchronisation test asserts the two
agree today, which is the only day it matters.

`repaired` is labelled "Reparado" and NOT "Listo para recoger". The technician
finished; nobody has checked the work and nobody has told the customer to come
in. Naming it after a step that has not shipped would put a promise in every
tenant's database.

Labels are neutral. Nothing here names a tenant.
"""

from django.db import migrations

# Frozen copy — see the module docstring. (code, label, is_customer_visible, sort_order)
NEW_STATUSES = (
    ('in_repair', 'En reparación', True, 60),
    ('waiting_parts', 'Esperando repuestos', True, 70),
    ('repaired', 'Reparado', True, 80),
)


def seed(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')

    companies = 0
    for company in Company.objects.all().iterator():
        companies += 1
        for code, label, visible, order in NEW_STATUSES:
            RepairStatusSetting.objects.get_or_create(
                company=company, code=code,
                defaults={
                    'label': label,
                    'is_customer_visible': visible,
                    'sort_order': order,
                },
            )

    if companies:
        print(
            f'\n  M10 — estados de ejecución sembrados para {companies} empresa(s)'
        )


def unseed(apps, schema_editor):
    """
    Remove only the three rows this migration could have created.

    An order that already reached one of these states keeps its history — that
    lives in `RepairStatusHistory`, which nothing here touches. What disappears
    is the presentation row, and rolling the code back removes the states it
    presented anyway.
    """
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')
    RepairStatusSetting.objects.filter(
        code__in=[code for code, _l, _v, _o in NEW_STATUSES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0043_repair_execution_and_part_usage'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
