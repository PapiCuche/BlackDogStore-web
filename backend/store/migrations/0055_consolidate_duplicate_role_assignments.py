"""
M11.1 — collapse duplicate role assignments to ONE ROW, so 0049 can be applied.

WHAT THIS MIGRATION GOT WRONG THE FIRST TIME
--------------------------------------------
It deactivated the extra rows and kept them. That prepares the data for a
constraint on `(membership, role, is_active)` — and 0056's constraint is on
`(membership, role) WHERE area IS NULL`, which does not mention `is_active` at
all. Two rows for one pair collide whether they are active or not, so
deactivating them changed nothing that the constraint cares about and
`AddConstraint` raised `IntegrityError` on any database that actually had
duplicates. Measured, not reasoned about: applied against a database with two
duplicates and watched 0049 fail.

The mistake is worth naming because it is easy to repeat: a soft-delete is the
right instinct for revoking AUTHORITY, and the wrong one for removing a ROW the
schema forbids. Those are different operations that happen to look alike.

WHY DELETING THE EXTRAS IS SAFE HERE
------------------------------------
Checked before choosing it, not assumed:

  · NOTHING references these rows. `MembershipRoleAssignment` has no incoming
    foreign keys — no other table points at an assignment id.
  · The audit trail does not live in them. `AdminAuditLog` records every
    create, update and disable with the assignment id, the role, the company,
    the membership and the actor. Deleting a redundant row does not erase the
    record of it having existed; that record is somewhere else on purpose.
  · The only per-row evidence is `assigned_by` and `created_at`, and the
    surviving row keeps the EARLIEST of those, which is the grant that actually
    happened first.

So the duplicates are redundant data, not history, and the row that survives
carries the history the pair has.

WHICH ROW SURVIVES, AND IN WHAT STATE
-------------------------------------
The OLDEST row survives, so `created_at` still says when this person was first
given this role.

Its `is_active` becomes True if ANY of the duplicates was active, and stays
False if none were. That asymmetry is the point: consolidating rows must never
hand back authority that somebody had revoked, and must never quietly take away
authority that was in force. Collapsing storage is not a decision about
permissions.
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

    removed = 0
    reactivated = 0
    for group in groups:
        rows = list(
            MembershipRoleAssignment.objects
            .filter(
                membership_id=group['membership_id'],
                role_id=group['role_id'],
                area__isnull=True,
            )
            .order_by('created_at', 'id')
        )
        survivor, extras = rows[0], rows[1:]

        # Authority is the OR of the duplicates: active if any of them was.
        should_be_active = any(row.is_active for row in rows)
        if survivor.is_active != should_be_active:
            survivor.is_active = should_be_active
            survivor.save(update_fields=['is_active'])
            if should_be_active:
                reactivated += 1

        for row in extras:
            row.delete()
            removed += 1

    if removed:
        print(
            f'\n  M11.1 — {removed} fila(s) duplicada(s) de asignación eliminada(s); '
            f'se conserva la más antigua por (membresía, rol) sin área.'
        )
    if reactivated:
        print(
            f'  M11.1 — {reactivated} fila(s) conservada(s) quedaron activas porque '
            f'alguna de sus duplicadas lo estaba.'
        )


def unconsolidate(apps, schema_editor):
    """
    Deliberately a no-op.

    The duplicates were redundant copies of one logical assignment; there is no
    honest way to recreate "how many identical rows there used to be", and
    nothing needs them back. Reversing 0056 removes the constraint, which is
    what a downgrade actually requires.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0054_sales_reception_and_service_supervisor'),
    ]

    operations = [
        migrations.RunPython(consolidate, unconsolidate),
    ]
