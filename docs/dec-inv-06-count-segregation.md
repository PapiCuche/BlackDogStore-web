# DEC-INV-06 — quien realiza un recuento no lo aprueba

Regla aprobada por el propietario: separación entre quien realiza el recuento y quien lo aprueba (hallazgo B1 de
la auditoría). PR #247.

## Qué hace

- **Quién «realizó» el recuento** (`inventory_services.count_performers`): quien anotó, cambió o borró una cantidad
  (PUT o escaneo «Sumar 1»), recontó o excluyó líneas mientras se contaba, o lo envió a revisión. No cuentan quien
  sólo lo creó sin anotar nada, quien sólo buscó un código, ni las decisiones de quien aprueba en revisión
  (excluir, devolver a edición, anular).
- La lista vive en `InventoryCount.metadata['performers']` y la escribe cada acción de conteo con la fila del
  recuento bloqueada. Un recuento nuevo nace con `[]`.
- **Recuentos abiertos antes del despliegue** (sin esa lista): su creador cuenta como participante, porque antes de
  esta versión la primera anotación no se auditaba y `counted_by` guarda sólo al último que escribió. La primera
  escritura después del despliegue crea la lista con el creador dentro.
- **Aprobar el propio recuento** → `403 count_self_approval`, sin escribir nada.
- **Equipos pequeños**: la capacidad `inventory.counts.approve_own` («Aprobar recuentos propios»). No la trae
  ningún preset, ni Administrador, ni los roles antiguos; sólo la puede dar quien ya la tiene (al principio, el
  master de la plataforma). Con ella hay que indicar un motivo (`400 self_approval_reason_required` si falta,
  máximo 250 caracteres); queda guardado en el recuento y marcado en la auditoría
  (`inventory_count_self_approved`).
- El master de la plataforma tiene todas las capacidades, así que puede aprobar su propio recuento, con motivo y
  marcado, igual que pasa con las capacidades de finanzas de concesión explícita.

## Antes de desplegar (sólo lectura, en producción)

Después del despliegue, un recuento abierto no lo puede aprobar ninguno de sus participantes, y en los recuentos
antiguos eso incluye a su creador. Si en una sucursal sólo hay una persona con permiso de aprobar y fue ella quien
abrió, contó o envió el recuento, recibirá 403.

Consulta (no escribe nada), desde el contenedor del backend:

```sh
docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell -c '
from store.models import InventoryCount
for c in InventoryCount.objects.filter(status__in=["draft", "counting", "review"]).select_related("branch", "created_by", "submitted_by").order_by("id"):
    lines = c.items.filter(counted_by__isnull=False).values_list("counted_by__username", flat=True).distinct()
    print(c.pk, c.status, c.kind, c.branch.name, "creó:", c.created_by.username if c.created_by else "-",
          "envió:", c.submitted_by.username if c.submitted_by else "-", "contaron:", ", ".join(sorted(lines)) or "-")
'
```

Para cada recuento que aparezca:

1. Si **otra persona** con «Aprobar recuentos» (que no figure como creador, ni como quien lo envió, ni entre quienes
   contaron) puede aprobarlo, no hay que hacer nada.
2. Si no la hay, hay dos caminos:
   - **Aprobarlo antes del despliegue**, con las reglas de hoy; o
   - después del despliegue, que el **master** (la única cuenta que tiene «Aprobar recuentos propios» al inicio) lo
     apruebe con un motivo, o que dé esa capacidad, a propósito, al rol de la persona que lo debe aprobar
     (Roles → editar → «Aprobar recuentos propios»). Ese cambio de rol queda en la auditoría.
3. Un recuento que ya no sirve se puede anular (en revisión, con motivo).

## Después de `migrate`

La migración `0163` le quita «Aprobar recuentos propios» al Administrador sembrado de una **instalación nueva**
(la migración `0061` lo arma con todo el catálogo vigente). Sólo lo hace en una base sin ninguna `Membership`;
en una base en uso no toca nada, para no quitar una concesión hecha a propósito al volver atrás y migrar otra vez.
Si una instalación nueva se migró por partes y alguien entró antes de terminar, la reparación se salta. Por eso el
ensayo (`deploy/rehearsal.sh`, paso 3b) comprueba que ningún rol la tenga, y en cualquier base se puede verificar
así (sólo lectura):

```sh
python manage.py shell -c 'from store.models import CompanyRole; print([(r.company_id, r.slug) for r in CompanyRole.objects.all() if "inventory.counts.approve_own" in (r.capabilities or [])])'
```

En una instalación nueva el resultado debe ser `[]`. En una base en uso, cada rol listado debe ser una concesión
hecha a propósito.
