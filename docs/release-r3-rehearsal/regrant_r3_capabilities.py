"""Al volver a subir R3 tras strip_r3_capabilities.sql: `python3 manage.py shell < regrant_r3_capabilities.py` (codigo R3).
Ejecuta el mismo grant congelado e idempotente de 0157 (finance.view/manage/payments.record a todo rol con company.manage)."""
from importlib import import_module

from django.apps import apps

import_module('store.migrations.0157_finance_capabilities').grant(apps, None)
print('regrant r3 done')
