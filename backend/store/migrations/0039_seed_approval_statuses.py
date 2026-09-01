"""
M9 / BR-005B — give the companies that already exist their two new states.

Same pair as always: this migration seeds the companies that exist the moment it
runs, and `provision_company_access_defaults()` seeds the ones created tomorrow.
Neither half is optional and neither is a duplicate of the other.

The list is FROZEN here on purpose and is not imported from
`company_provisioning`: a migration must reproduce what it did on the day it
ran, and importing the runtime module would let a future edit to the default
labels retroactively rewrite history. A synchronisation test asserts the two
agree today, which is the only day it matters.

Labels are neutral. Nothing here names a tenant.
"""

from django.db import migrations

# Frozen copy — see the module docstring. (code, label, is_customer_visible, sort_order)
NEW_STATUSES = (
    ('approved', 'Aprobado', True, 40),
    ('rejected', 'Rechazado', True, 50),
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
            f'\n  M9 — estados de aprobación sembrados para {companies} empresa(s)'
        )


def unseed(apps, schema_editor):
    """
    Remove only the two rows this migration could have created.

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
        ('store', '0038_service_diagnostics_and_quotes'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
