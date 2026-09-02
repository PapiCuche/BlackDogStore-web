"""
M11 — reconcile the RBAC branch with the quality-control branch.

Two lines of work left `0046` at the same time:

    0046 ─┬─ 0047_sales_reception_and_service_supervisor
          │    → 0048_consolidate_duplicate_role_assignments
          │    → 0049_role_assignment_uniqueness            (M11: RBAC multirol)
          └─ 0047_service_capabilities_for_untouched_technician_presets
               → 0048_quality_control
               → 0049_seed_quality_statuses_and_checklists
               → 0050_quality_capability_for_untouched_presets   (H1B + calidad)

The repeated 0047/0048/0049 prefixes are not the problem — a migration's
identity is its name and its dependencies. TWO LEAVES are, and Django is right
to refuse a graph whose intended order is not written down anywhere.

NOTHING COLLIDES IN SCHEMA. M11 adds a partial unique index on
`MembershipRoleAssignment` and rewrites capability lists on `CompanyRole` rows;
the quality branch adds its own models and seeds their statuses. The one place
they touch the same data is preset capabilities, and they touch DIFFERENT
presets by different discriminators: M11 extends untouched `Ventas` roles,
H1B extends untouched `Servicio Técnico` roles, and the quality migration
extends the ones its own phase defined. Each runs its own exact-equality test,
so whichever order they applied in, a role that either one would decline to
touch is still declined.

Empty for that reason: this exists only to give the graph a single leaf.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0049_role_assignment_uniqueness'),
        ('store', '0050_quality_capability_for_untouched_presets'),
    ]

    operations = []
