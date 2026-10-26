"""Rollback of R2, step 1, with the NEW code: `python3 manage.py shell < cancel_migration_counts.py`.
Cancels every OPEN migration count (0147's docstring): the previous version cannot handle their pending
lines. Approved and ordinary counts stay as they are. Prints counts only."""
from store import inventory_services as inv
from store.models import InventoryCount

open_ = InventoryCount.objects.filter(kind=InventoryCount.KIND_MIGRATION, status__in=['draft', 'counting', 'review'])
print('open_migration_counts_before', open_.count())
for count in open_.order_by('pk'):
    inv.cancel_inventory_count(count)
print('open_migration_counts_after', open_.count())
