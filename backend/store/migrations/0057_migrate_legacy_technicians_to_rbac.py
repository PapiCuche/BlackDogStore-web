"""
M12A — give legacy technicians back the tools the module decomposition took.

THE SYMPTOM. A technician logs in and the technical-service section is not in
their sidebar. `/admin/service` refuses them. Measured against a real database
before writing anything: a membership with `role='technician'` and no custom
role resolves to exactly

    company.view, service.manage

and every module in the console asks for one of `service.orders.view`,
`service.orders.create`, `service.diagnostic.manage`, `service.repair.manage`
or `service.quality.manage`. None of which they hold. So the answer is not
"some screens are missing" — it is all of them.

THE CAUSE, AND WHY IT IS NOT A CAPABILITY BUG. `LEGACY_ROLE_CAPABILITIES` was
written in Phase 2A, when `service.manage` WAS the technical-service module:
holding it meant "this person works in technical service". M8, M9, M10 and
quality then decomposed that module into nine granular capabilities, and the
legacy matrix never learned about the decomposition. `service.manage` still
exists and still resolves — it simply no longer opens anything on its own.

So these technicians were not denied authority anybody decided to withhold.
They were narrowed by a refactor.

WHY THIS MIGRATES INSTEAD OF WIDENING THE LEGACY MATRIX
-------------------------------------------------------
Adding the nine codes to `LEGACY_ROLE_CAPABILITIES['technician']` would fix the
symptom in one line and would be the wrong fix. That matrix is a compatibility
bridge for memberships nobody has modelled yet; growing it hands new capability
codes to every legacy technician in every tenant, forever, with no record that
it happened and no way for a company to decline. M11 froze it deliberately for
exactly that reason.

Assigning the tenant's own `Servicio Técnico` role instead means the authority
arrives through the mechanism that was built to carry it: a row somebody can
see in the console, revoke, or narrow. It is also what a technician hired
tomorrow gets, so it removes a difference rather than adding one.

THIS IS ONE-WAY, AND THAT IS THE POINT. Creating the assignment makes
`has_custom_role_history()` true, so these memberships can never fall back to
the legacy matrix again. That is the M11 rule working as designed: once a
company's RBAC has spoken about a person, the bridge stops answering for them.

WHAT IT REFUSES TO TOUCH
------------------------
  · Memberships that already have ANY role assignment, active or not. Their
    company has already decided about them and this migration is not a second
    opinion.
  · Companies whose `Servicio Técnico` role was edited — capability equality
    with the current preset, both directions. A tenant that narrowed the role
    on purpose does not get it widened back by a platform migration.
  · Inactive memberships and inactive companies.
  · Every other legacy role. `sales`, `inventory` and `admin` did not lose
    anything to the decomposition; only the service module was split.
"""

from django.db import migrations


def migrate_legacy_technicians(apps, schema_editor):
    Membership = apps.get_model('store', 'Membership')
    CompanyRole = apps.get_model('store', 'CompanyRole')
    MembershipRoleAssignment = apps.get_model('store', 'MembershipRoleAssignment')

    # Read from the live preset: this migration must compare against what an
    # untouched `Servicio Técnico` means TODAY, after every phase that extended
    # it. A frozen copy here would stop matching the first time it grows again.
    from store.company_provisioning import _TECHNICIAN_CAPS
    expected = frozenset(_TECHNICIAN_CAPS)

    migrated = 0
    skipped_customised = 0
    for membership in (
        Membership.objects
        .filter(role='technician', is_active=True, company__is_active=True)
        .select_related('company')
        .iterator()
    ):
        # Already modelled by their company — leave them alone.
        if MembershipRoleAssignment.objects.filter(membership=membership).exists():
            continue

        role = CompanyRole.objects.filter(
            company=membership.company, slug='servicio-tecnico', is_active=True,
        ).first()
        if role is None:
            continue
        if frozenset(role.capabilities or []) != expected:
            # The tenant edited it. Their definition wins over the platform's.
            skipped_customised += 1
            continue

        MembershipRoleAssignment.objects.create(
            membership=membership, role=role, area=None, is_active=True,
        )
        migrated += 1

    if migrated:
        print(
            f'\n  M12A — {migrated} técnico(s) heredado(s) migrado(s) al rol '
            f'"Servicio Técnico" de su empresa.'
        )
    if skipped_customised:
        print(
            f'  M12A — {skipped_customised} técnico(s) sin migrar: su empresa '
            f'personalizó el rol y esa definición manda.'
        )


def unmigrate(apps, schema_editor):
    """
    Deliberately a no-op.

    Deleting the assignments would not restore the previous state — it would
    leave memberships whose `has_custom_role_history()` marker is gone, which
    silently re-arms the legacy fallback for people a company may since have
    narrowed on purpose. Reversing a grant is a decision for the console, where
    it is one click and it is audited.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0056_role_assignment_uniqueness'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_technicians, unmigrate),
    ]
