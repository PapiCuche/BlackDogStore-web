"""
Reconcile the two branches that grew from 0042 — P0-F.

WHY THERE WERE TWO
------------------
Two lines of work left `0042_enforce_line_uniqueness` at the same time and
neither knew about the other:

    0042 ─┬─ 0043_payment_transaction → 0044_carry_legacy_payments
          │       → 0045_drop_legacy_gateway_fields        (P0-F: pasarela)
          └─ 0043_repair_execution_and_part_usage → 0044_seed_execution_statuses
                  → 0045_repair_capability_for_untouched_admin_presets   (M10)

Both are published and both are correct. The repeated 0043/0044/0045 prefixes
are not the problem and are not a mistake: a migration's identity is its NAME
and its declared dependencies, not the digits it happens to start with. What
Django refuses is a graph with TWO LEAVES, because it cannot know which order
was intended — and it is right to refuse, since the answer is not in the files.

WHY THIS FILE IS EMPTY
----------------------
The two branches touch disjoint schema. P0-F adds `PaymentTransaction`, widens
`Order.payment_method`'s vocabulary and drops the previous gateway's two
columns. M10 adds `RepairExecution`, `PartUsage` and their seeded statuses and
capabilities — the technical-service side, which owns no payment column and no
order line. Nothing collides, so there is nothing to reconcile in SQL: this
migration exists only to give the graph a single leaf again.

Which is why the alternative — renumbering one branch to sit after the other —
was not taken, for exactly the reason 0041 records. Those migrations are applied
in a real database. Renumbering them would make Django see migrations it has
never run, against tables that already exist, and the recovery from that is
manual. An empty merge node costs one row in `django_migrations`.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0045_drop_legacy_gateway_fields'),
        ('store', '0045_repair_capability_for_untouched_admin_presets'),
    ]

    operations = []
