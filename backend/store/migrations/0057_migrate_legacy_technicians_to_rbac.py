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

#: The `Servicio Técnico` preset EXACTLY as it stands when this migration runs.
#:
#: FROZEN, and the first version of this migration was not. It read
#: `_TECHNICIAN_CAPS` live from `store.company_provisioning` on the argument
#: that the preset keeps growing and a copy would go stale. That reasoning is
#: backwards, and this branch's own sales migration says so in as many words: a
#: live import compares the database against what the preset means TODAY, in a
#: process whose code is always newer than its data.
#:
#: The failure is concrete rather than theoretical, and renumbering did not fix
#: it — it only postponed it. Ordering is now guaranteed (0053 grants
#: `service.delivery.manage` before this node runs), so a database that is
#: CURRENT migrates correctly. A database that is BEHIND does not: let a future
#: phase add a thirteenth capability in 0058, and a tenant upgrading across both
#: in one `migrate` reaches this node with twelve-code rows and a thirteen-code
#: live tuple. Every technician is skipped, silently, and the printed reason
#: blames the tenant for a customisation they never made.
#:
#: A migration is a statement about a moment. This is that moment.
UNTOUCHED_TECHNICIAN_PRESET = frozenset({
    'company.view',
    'service.manage',
    'service.customers.view',
    'service.devices.view',
    'service.devices.manage',
    'service.orders.view',
    'service.orders.create',
    'service.orders.manage',
    'service.diagnostic.manage',
    'service.repair.manage',
    'service.quality.manage',
    'service.delivery.manage',
})


def migrate_legacy_technicians(apps, schema_editor):
    Membership = apps.get_model('store', 'Membership')
    CompanyRole = apps.get_model('store', 'CompanyRole')
    MembershipRoleAssignment = apps.get_model('store', 'MembershipRoleAssignment')

    expected = UNTOUCHED_TECHNICIAN_PRESET

    migrated = 0
    skipped_no_role = 0
    skipped_already_modelled = 0
    skipped_customised = []
    for membership in (
        Membership.objects
        .filter(role='technician', is_active=True, company__is_active=True)
        .select_related('company')
        .iterator()
    ):
        # Already modelled by their company — leave them alone.
        if MembershipRoleAssignment.objects.filter(membership=membership).exists():
            skipped_already_modelled += 1
            continue

        role = CompanyRole.objects.filter(
            company=membership.company, slug='servicio-tecnico', is_active=True,
        ).first()
        if role is None:
            skipped_no_role += 1
            continue
        if frozenset(role.capabilities or []) != expected:
            # The tenant edited it. Their definition wins over the platform's.
            skipped_customised.append(membership.company.slug)
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
    if skipped_already_modelled:
        print(
            f'  M12A — {skipped_already_modelled} técnico(s) ya tenían rol asignado; '
            f'su empresa ya decidió sobre ellos.'
        )
    if skipped_no_role:
        print(
            f'  M12A — {skipped_no_role} técnico(s) sin migrar: su empresa no tiene '
            f'un rol "Servicio Técnico" activo.'
        )
    if skipped_customised:
        # THE SLUGS, not just a count. The three reasons for skipping look
        # identical in a total, and only one of them is a tenant decision. An
        # operator who sees a number cannot tell "they chose this" from "the
        # platform compared against the wrong set", which is exactly the failure
        # the frozen list above exists to prevent — so name the companies and
        # let somebody check.
        names = ', '.join(sorted(set(skipped_customised)))
        print(
            f'  M12A — {len(skipped_customised)} técnico(s) sin migrar: su empresa '
            f'personalizó el rol y esa definición manda ({names}).'
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
