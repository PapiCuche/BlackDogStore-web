"""
M11 — collapse duplicate role assignments before the constraint can refuse them.

WHY THERE MAY BE ANY. `unique_role_assignment_per_area` covers
`(membership, role, area)`, and in SQL two NULLs are never equal — so the
common case, a role assigned with no area, was never actually unique. The view
checked with `.exists()` before inserting, which stops the honest double-click
and not two concurrent requests.

WHAT THIS DOES. For each `(membership, role)` group with no area and more than
one row, ONE row survives and the rest are deactivated. Nothing is deleted:
these rows are the audit trail of who was given what and when, and 0049 only
needs them not to collide.

WHICH ONE SURVIVES. An ACTIVE row if there is one, and the oldest of those, so
the surviving record carries the original grant date rather than a later
duplicate's. If every duplicate was already inactive, the oldest stays inactive
— consolidating them must not hand back authority that had been revoked.
"""

from django.db import migrations
from django.db.models import Count


def consolidate(apps, schema_editor):
    MembershipRoleAssignment = apps.get_model('store', 'MembershipRoleAssignment')

    groups = (
        MembershipRoleAssignment.objects
        .filter(area__isnull=True)
        .values('membership_id', 'role_id')
        .annotate(n=Count('id'))
        .filter(n__gt=1)
    )

    collapsed = 0
    for group in groups:
        rows = list(
            MembershipRoleAssignment.objects
            .filter(
                membership_id=group['membership_id'],
                role_id=group['role_id'],
                area__isnull=True,
            )
            .order_by('id')
        )
        # Prefer an active row; otherwise the oldest, still inactive.
        survivor = next((r for r in rows if r.is_active), rows[0])
        for row in rows:
            if row.pk == survivor.pk or not row.is_active:
                continue
            row.is_active = False
            row.save(update_fields=['is_active'])
            collapsed += 1

    if collapsed:
        print(
            f'\n  M11 — {collapsed} asignación(es) de rol duplicada(s) desactivada(s); '
            f'se conserva una activa por (membresía, rol).'
        )


def unconsolidate(apps, schema_editor):
    """
    Deliberately a no-op.

    Reactivating the duplicates would recreate rows the next migration forbids,
    and "which of these identical rows was active last Tuesday" is not a
    question the data can answer.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0047_sales_reception_and_service_supervisor'),
    ]

    operations = [
        migrations.RunPython(consolidate, unconsolidate),
    ]
