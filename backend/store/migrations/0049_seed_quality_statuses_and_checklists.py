"""
M11 / BR-005D — give the companies that already exist their two new states and
a checklist to inspect against.

Same pair as always: this migration seeds the companies that exist the moment it
runs, and `provision_company_access_defaults()` seeds the ones created tomorrow.
Neither half is optional.

Both lists are FROZEN here and not imported from `company_provisioning`: a
migration must reproduce what it did on the day it ran, and importing the
runtime module would let a future edit to the default labels or the default
checklist retroactively rewrite history. Synchronisation tests assert the two
agree today, which is the only day it matters.

`ready_for_pickup` is labelled "Listo para recoger" and NOT "Cliente avisado".
The device passed its tests; this platform has no notification channel, and a
default label claiming otherwise would ship a promise the product does not keep.

The checklist is DEVICE-NEUTRAL. Every code asks something you can ask a phone,
a laptop, a tablet, a console or a thing nobody has a word for yet — nothing
names a vendor, a connector or one manufacturer's feature. A tenant edits the
labels, adds points, or creates a template for a specific device type.

Labels are neutral. Nothing here names a tenant.
"""

from django.db import migrations

# Frozen copy. (code, label, is_customer_visible, sort_order)
NEW_STATUSES = (
    ('quality_control', 'En control de calidad', True, 85),
    ('ready_for_pickup', 'Listo para recoger', True, 88),
)

# Frozen copy. (code, label, is_required, sort_order)
TEMPLATE_NAME = 'Control general'
TEMPLATE_ITEMS = (
    ('power', 'Enciende y arranca correctamente', True, 10),
    ('repaired_function', 'La falla reportada quedó resuelta', True, 20),
    ('charging', 'Carga y alimentación', True, 30),
    ('audio', 'Audio (altavoz y micrófono)', False, 40),
    ('connectivity', 'Conectividad (red inalámbrica y puertos)', False, 50),
    ('physical', 'Estado físico y cierre del equipo', True, 60),
)


def seed(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')
    Template = apps.get_model('store', 'QualityChecklistTemplate')
    TemplateItem = apps.get_model('store', 'QualityChecklistTemplateItem')

    companies = 0
    templates = 0
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

        template, created = Template.objects.get_or_create(
            company=company, device_type='',
            defaults={'name': TEMPLATE_NAME, 'is_active': True},
        )
        if created:
            templates += 1
            TemplateItem.objects.bulk_create([
                TemplateItem(
                    template=template, code=code, label=label,
                    is_required=required, sort_order=order,
                )
                for code, label, required, order in TEMPLATE_ITEMS
            ])

    if companies:
        print(
            f'\n  M11 — control de calidad sembrado para {companies} empresa(s), '
            f'{templates} lista(s) creada(s)'
        )


def unseed(apps, schema_editor):
    """
    Remove only what this migration could have created.

    An order that already reached one of these states keeps its history — that
    lives in `RepairStatusHistory`, which nothing here touches. A template that
    a tenant edited is left alone: only one still holding exactly the seeded
    items is removed, because anything else is somebody's work.
    """
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')
    Template = apps.get_model('store', 'QualityChecklistTemplate')

    RepairStatusSetting.objects.filter(
        code__in=[code for code, _l, _v, _o in NEW_STATUSES],
    ).delete()

    seeded_codes = {code for code, _l, _r, _o in TEMPLATE_ITEMS}
    for template in Template.objects.filter(device_type='', name=TEMPLATE_NAME).iterator():
        if template.checks.exists():
            continue
        if {i.code for i in template.items.all()} == seeded_codes:
            template.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0048_quality_control'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
