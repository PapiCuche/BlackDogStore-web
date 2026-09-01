"""
Reconcile the two migration branches that grew from 0033 — Phase C1.4.

WHY THERE WERE TWO
------------------
Two lines of work left 0033 at the same time and neither knew about the other:

    0033 ─┬─ 0034_checkout_idempotency          (master: native v1 checkout)
          └─ 0034_commercial_pos_barcode → 0035 → … → 0040   (POS / promotions)

Both are published and both are correct. Django refuses to run a graph with two
leaves because it cannot know which order was intended, and it is right to
refuse: the answer is not in the files.

WHY THIS FILE IS EMPTY, AND WHY THAT IS THE WHOLE POINT
-------------------------------------------------------
The two branches touch disjoint schema. `0034_checkout_idempotency` adds
`Order.idempotency_key` / `idempotency_fingerprint` and their partial unique
constraint, for the authenticated checkout. The commercial branch adds barcodes,
POS attribution, commissions and promotions — including `Order`'s own
`pos_idempotency_key` / `pos_request_fingerprint`, which are DIFFERENT columns
belonging to a DIFFERENT surface (see the note in `models.py`). Nothing
collides, so there is nothing to reconcile in SQL: this migration exists only to
give the graph a single leaf again.

Which is why the alternative — renumbering the commercial branch to sit after
0034_checkout — was not taken. Those seven migrations are applied in a real
database. Renumbering them would make Django see seven migrations it has never
run, against tables that already exist, and the recovery from that is manual.
An empty merge node costs one row in `django_migrations` and is reversible.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0034_checkout_idempotency'),
        ('store', '0040_promotion_capabilities_for_untouched_presets'),
    ]

    operations = []
