"""Al volver a subir R3 tras strip_r3_capabilities.sql: `python3 manage.py shell < regrant_r3_capabilities.py` (código R3).

Concede finance.view, finance.manage y finance.payments.record a todo rol con company.manage, la misma regla de
0157, pero SOLO AÑADE. A diferencia de la función `grant` de 0157 (que además retira de todos los roles los nueve
códigos de asignación explícita, correcto en una instalación nueva), este script nunca quita nada: si una empresa
asignó a propósito finance.void o finance.adjust, siguen ahí. Idempotente. Imprime solo conteos."""
from store.models import CompanyRole

NEW = ('finance.manage', 'finance.payments.record', 'finance.view')

changed = 0
for role in CompanyRole.objects.all().iterator():
    held = set(role.capabilities or [])
    if 'company.manage' not in held or held >= set(NEW):
        continue
    role.capabilities = sorted(held | set(NEW))
    role.save(update_fields=['capabilities', 'updated_at'])
    changed += 1
print(f'regrant r3: {changed} rol(es) con company.manage ampliados')
