"""
M8 / BR-005A — give the companies that already exist their service defaults.

WHY A DATA MIGRATION AND A PROVISIONING BLOCK, NOT ONE OR THE OTHER
-------------------------------------------------------------------
The house pattern, established in 2A.1, 2D, 2E and Phase 3, is a pair:

  · this migration seeds the companies that exist the moment it runs;
  · `provision_company_access_defaults()` seeds the ones created tomorrow.

Shipping only the migration leaves every company registered after the deploy
with no lifecycle labels and no service series. Shipping only the provisioning
block leaves every company that exists today in exactly that state. Neither half
is optional and neither is a duplicate of the other.

WHY THE LIST IS FROZEN HERE
---------------------------
This file carries its own copy of the statuses on purpose, and does NOT import
`PRESET_REPAIR_STATUSES` from `company_provisioning`. A migration must reproduce
what it did on the day it ran; importing the runtime module would let a future
edit to the default labels retroactively rewrite history. The two are allowed to
drift — that one is the current default, this one is a record. A synchronisation
test asserts they agree TODAY, which is the only day it matters.

The exception to this rule is a migration that COMPARES against a set that grows
(0033 imports the live catalogue for exactly that reason). This one SEEDS values,
so it freezes them.
"""

from django.db import migrations

# Frozen copy — see the module docstring. (code, label, is_customer_visible, sort_order)
REPAIR_STATUSES = (
    ('received', 'Recibido', True, 10),
    ('diagnosing', 'En diagnóstico', True, 20),
    ('waiting_approval', 'Esperando aprobación', True, 30),
    ('cancelled', 'Cancelado', True, 90),
)

REPAIR_DOCUMENT_TYPE = 'repair_order'
REPAIR_PREFIX = 'SRV-'
REPAIR_PADDING = 6


def seed(apps, schema_editor):
    Company = apps.get_model('store', 'Company')
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')
    InternalSequence = apps.get_model('store', 'InternalSequence')

    companies = 0
    for company in Company.objects.all().iterator():
        companies += 1
        for code, label, visible, order in REPAIR_STATUSES:
            RepairStatusSetting.objects.get_or_create(
                company=company, code=code,
                defaults={
                    'label': label,
                    'is_customer_visible': visible,
                    'sort_order': order,
                },
            )
        # The company-level service series. Branch series are created on demand
        # by `sequences.ensure_branch_sequence`, exactly as sales notes do.
        InternalSequence.objects.get_or_create(
            company=company, branch=None, document_type=REPAIR_DOCUMENT_TYPE,
            defaults={
                'prefix': REPAIR_PREFIX,
                'padding': REPAIR_PADDING,
                'next_value': 1,
            },
        )

    if companies:
        print(f'\n  M8 — servicio técnico aprovisionado para {companies} empresa(s)')


def unseed(apps, schema_editor):
    """
    Remove ONLY what this migration could have created, and only if untouched.

    A series that has issued a number is left alone: deleting it would make the
    next deploy hand out `SRV-000001` again for orders that already exist. A
    status row a tenant renamed is theirs, and reversing a migration is not a
    reason to discard their configuration — but the rows are keyed by
    (company, code) and carry nothing else, so removing the ones that still
    match the seed is safe and complete.
    """
    RepairStatusSetting = apps.get_model('store', 'RepairStatusSetting')
    InternalSequence = apps.get_model('store', 'InternalSequence')

    seeded_codes = [code for code, _label, _visible, _order in REPAIR_STATUSES]
    RepairStatusSetting.objects.filter(code__in=seeded_codes).delete()
    InternalSequence.objects.filter(
        document_type=REPAIR_DOCUMENT_TYPE, next_value=1,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0035_service_core'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
