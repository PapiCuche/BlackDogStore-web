"""
M11 — collapse duplicate role assignments before the constraint can refuse them.

WHY THERE MAY BE ANY. `unique_role_assignment_per_area` covers
`(membership, role, area)`, and in SQL two NULLs are never equal — so the
common case, a role assigned with no area, was never actually unique. The view
checked with `.exists()` before inserting, which stops the honest double-click
and not two concurrent requests.

WHAT THIS DOES. For each `(membership, role)` group with no area and more than
one row, ONE row survives and the redundant copies are REMOVED.

WHY REMOVED AND NOT MERELY DEACTIVATED — THE BUG THIS FIXES. The first version
of this migration only set `is_active=False` on the extras, on the stated
premise that "0049 only needs them not to collide". That premise was wrong.
0049's index is

    UNIQUE (membership, role) WHERE area IS NULL

with NO `is_active` term, so a deactivated duplicate still has a NULL area and
still collides. `migrate` then failed at 0049 with

    IntegrityError: UNIQUE constraint failed:
    store_membershiproleassignment.membership_id, ...role_id

on exactly the databases this migration exists to repair — and after 0048 had
already rewritten flags, leaving a half-applied deploy. The test suite could
never catch it: Django builds the test database from empty, so the repair path
runs against zero duplicate groups. Reproduced against a real SQLite database
seeded with two identical rows before this was rewritten.

WHAT IS LOST, SAID PLAINLY. The redundant rows recorded the same fact — this
membership holds this role, with no area — one extra time each. The survivor
keeps that fact and the earliest grant date. What disappears is the timestamp
of the duplicate grant, which is the price of a database the schema can
actually constrain. `has_custom_role_history()` in `tenancy.py` reads row
EXISTENCE, and the survivor keeps that history intact, so no membership loses
its "migrated to RBAC" marker and no legacy fallback is re-armed.

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
        redundant = [r.pk for r in rows if r.pk != survivor.pk]
        if redundant:
            MembershipRoleAssignment.objects.filter(pk__in=redundant).delete()
            collapsed += len(redundant)

    if collapsed:
        print(
            f'\n  M11 — {collapsed} asignación(es) de rol duplicada(s) eliminada(s); '
            f'se conserva una por (membresía, rol) sin área.'
        )


def unconsolidate(apps, schema_editor):
    """
    Deliberately a no-op.

    The removed rows were identical copies of a fact the survivor still records,
    and recreating them would recreate exactly what 0049 forbids. "Which of
    these identical rows was active last Tuesday" is not a question the data
    could answer even before they were collapsed.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0047_sales_reception_and_service_supervisor'),
    ]

    operations = [
        migrations.RunPython(consolidate, unconsolidate),
    ]
