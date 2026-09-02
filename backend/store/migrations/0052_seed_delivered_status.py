"""
M12 / BR-005E — give the companies that already exist the delivered state.

Same pair as always: this seeds the companies that exist the moment it runs, and
`provision_company_access_defaults()` seeds the ones created tomorrow.

The list is FROZEN here and not imported from `company_provisioning`: a
migration must reproduce what it did on the day it ran.

The label is «Entregado» and says nothing about payment, because this platform
cannot charge for a repair. Labels are neutral; nothing here names a tenant.
"""

from django.db import migrations

# Frozen copy. (code, label, is_customer_visible, sort_order)
NEW_STATUSES = (
    ('delivered', 'Entregado', True, 89),
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
        print(f'\n  M12 — estado de entrega sembrado para {companies} empresa(s)')


def unseed(apps, schema_editor):
    """
    Remove only the row this migration could have created.

    An order that already reached this state keeps its history — that lives in
    `RepairStatusHistory`, which nothing here touches.
    """
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')
    RepairStatusSetting.objects.filter(
        code__in=[code for code, _l, _v, _o in NEW_STATUSES],
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0051_repair_delivery'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
