"""With the NEW code, after a code-only rollback that ran strip_r2_capabilities.sql (or when roles were
created by the old version while 0148–0152 were already applied): `python3 manage.py shell < regrant_r2_capabilities.py`.
Runs the same frozen, idempotent grants as 0148, 0149 and 0152, in that order (0152 compares with the
Administrador that 0148+0149 leave)."""
from importlib import import_module

from django.apps import apps

for name in ('0148_inventory_counts_approve_capability', '0149_accounting_capabilities', '0152_label_capabilities'):
    import_module(f'store.migrations.{name}').grant(apps, None)
print('regrant done')
