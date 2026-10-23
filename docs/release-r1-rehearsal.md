# Ensayo local de migraciones — lanzamiento R1 (REL-R1-MIGRATION-REHEARSAL)

**Resultado: sin incompatibilidades.** Los tres pasos del plan (`docs/NEXT_ACTION.md`, «RELEASE R1») y la vuelta atrás funcionan con el código anterior y el nuevo sobre la misma base. El esquema final es idéntico al de una migración desde cero.

Ensayo **local**, 2026-10-23. No se tocó producción ni se usó SSH.

## Montaje

| | Valor |
|---|---|
| Código ANTERIOR | `6daf9ae` (producción; migraciones hasta `0141`), worktree separado, sólo su `manage.py` |
| Código NUEVO | `origin/master` `06ffb5f` (#156 no tiene migraciones: el camino es el mismo con #156) |
| Base | PostgreSQL local `blackdog_rel_b`, creada vacía para el ensayo |
| Comparación de esquema | `blackdog_rel_b_fresh`, migrada desde cero con el código nuevo y borrada después |
| Scripts | [release-r1-rehearsal/](release-r1-rehearsal/) (`counts.sh` lee la base de `REHEARSAL_DB_URL`) |

`old_writes.py` (código ANTERIOR, `TAG=<etiqueta> python3 manage.py shell < old_writes.py`) escribe sólo con los servicios y la API del código anterior. Cada vez crea una empresa nueva y en ella:
- un proveedor y una oferta con cantidad (`create_offer`);
- una oferta **sin cantidad** (`create_offer`, `confirmed_quantity=None`, como el formulario del panel);
- la importación semanal (`preview` + `apply` por la API): una fila que actualiza la oferta vigente y otra nueva **con la cantidad vacía**;
- una variante seminueva (`device_catalog.set_variant`, línea `used`);
- un pedido bajo pedido pagado por la confirmación real (`_confirm`), con su línea en `pending`.

`old_update.py` hace dos cosas con el código anterior:
- pasa una oferta existente sin ventas a cantidad desconocida (`update_offer`);
- lee el listado y la exportación de ofertas por la API.

## Pasos y resultados

### 1. Código ANTERIOR: base a `0141` y datos

```
migrate --noinput                                    # hasta 0141_orderitem_received_cost
seed_demo_users --company-slug black-dog-store --e2e-fixtures
TAG=s1 shell < old_writes.py
```

`preview` dio `rows_create 1, rows_update 1, rows_error 0`. `apply` respondió 200 (`created 1, updated 1`). El pedido 1 quedó `paid`, con la línea `pending`.

```
companies=2 users=9 products=7 suppliers=1 offers=3 offers_null_qty=2 orders=1 orderitems=1 jobs=1 audit=19
availability_confirmed: column absent · constraint=0 · ai_usage_table=f · ai_settings_table=f · última = 0141
```

### 2. Código NUEVO: `migrate store 0143`; después el código ANTERIOR escribe

```
PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=300000' \
  python3 manage.py migrate store 0143_supplier_offer_availability_confirmed --noinput
# Applying store.0142_inventory_ai_settings... OK
# Applying store.0143_supplier_offer_availability_confirmed... OK
```

```
companies=2 users=9 products=7 suppliers=1 offers=3 offers_null_qty=2 orders=1 orderitems=1 jobs=1 audit=19   (sin cambios)
null_qty_confirmed=0 null_qty_unconfirmed=2 · constraint=0 · ai_settings_table=t · última = 0143
```

Con el código **anterior** sobre ese esquema:
- `TAG=s2 old_writes.py`: `preview` y `apply` como en el paso 1, y el pedido 2 `paid` con su línea `pending`.
- `old_update.py`:
  - pasa la oferta 2 a `4` y después a `None`;
  - listado 200;
  - exportación 200.

```
offers=6 offers_null_qty=4 · null_qty_confirmed=2 null_qty_unconfirmed=2 · constraint=0
```

Las dos filas sin cantidad que insertó el código anterior quedan `availability_confirmed=True`, por el `db_default`. Es el caso previsto: el código anterior no conoce el campo. Lo corrige `0144`.

Un aviso del intento con `update_offer` no es una incompatibilidad. Elegí al principio una oferta con una venta, y el código anterior se negó con su regla de negocio: «Ya se vendieron 1 contra esta oferta: la cantidad confirmada no puede ser menor.». Con una oferta sin ventas funciona.

### 3. Código NUEVO: `migrate`

```
PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=300000' python3 manage.py migrate --noinput
# Applying store.0144_supplier_offer_confirmed_has_quantity... OK
# Applying store.0145_inventory_ai_usage... OK
```

```
companies=3 users=10 products=11 suppliers=2 offers=6 offers_null_qty=4 orders=2 orderitems=2 jobs=2 audit=32   (sin cambios)
null_qty_confirmed=0 null_qty_unconfirmed=4 · constraint=1 · ai_usage_table=t · última = 0145
```

- El relleno de `0144` corrigió las dos filas que escribió el código anterior: confirmadas sin cantidad, de 2 a 0.
- La restricción se cumple. Un `UPDATE … availability_confirmed=True` sobre una fila sin cantidad da `IntegrityError` (`supplier_offer_confirmed_has_quantity`).
- `inventory_ai._PROVIDERS == {}` y `get_provider()` es `None`.
- `makemigrations --check --dry-run`: «No changes detected».

### 4. Vuelta atrás: código NUEVO `migrate store 0143`; después el código ANTERIOR

```
PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=300000' \
  python3 manage.py migrate store 0143_supplier_offer_availability_confirmed --noinput
# Unapplying store.0145_inventory_ai_usage... OK
# Unapplying store.0144_supplier_offer_confirmed_has_quantity... OK
```

```
offers=6 offers_null_qty=4 audit=32 (sin cambios) · constraint=0 · ai_usage_table=f · última = 0143
```

Con el código **anterior**:
- `TAG=s4 old_writes.py`: `preview` y `apply` iguales, el pedido 3 `paid` con su línea `pending`, listado 200 con 3 ofertas y exportación 200.
- `old_update.py`: la oferta 2 pasa a `4` y después a `None`; listado y exportación 200.

```
companies=4 users=11 products=15 suppliers=3 offers=9 offers_null_qty=6 orders=3 orderitems=3 jobs=3 audit=45
null_qty_confirmed=2 null_qty_unconfirmed=4
```

Igual que en el paso 2.

Bajar a `0143` borra la tabla `store_inventoryaiusage`. Con el registro de proveedores vacío no tiene filas, así que no se pierde nada.

### 5. Código NUEVO: adelante otra vez y comparación de esquema

```
migrate --noinput   # Applying 0144... OK · Applying 0145... OK
```

```
companies=4 users=11 products=15 suppliers=3 offers=9 offers_null_qty=6 orders=3 orderitems=3 jobs=3 audit=45   (sin cambios)
null_qty_confirmed=0 null_qty_unconfirmed=6 · constraint=1 · ai_usage_table=t · última = 0145
```

```
pg_dump --schema-only --no-owner --no-privileges  (rehearsal vs fresh), sin comentarios ni SET
diff: 0 líneas distintas de 4510
```

**Prueba de humo del código nuevo** sobre lo que escribió el anterior:
- listado y exportación de ofertas por la API: 200 en las tres empresas;
- pedidos 1–3 `paid`.

El worktree del código anterior quedó sin cambios.

## Conclusiones para el despliegue

- **El paso 1 es seguro mientras sirve `6daf9ae`.** El código anterior sigue creando, importando, actualizando y leyendo ofertas, y vendiendo bajo pedido, sobre `0143`. Lo único que escribe distinto son filas sin cantidad marcadas como confirmadas, y `0144` las corrige antes de añadir la restricción.
- **Entre el cambio de imágenes y el `migrate` del paso 3 no hay riesgo de venta.** Las filas sin cantidad «confirmadas» que dejó la versión anterior nunca se venden ni se muestran como confirmadas con el código nuevo:
  - `supply_services.valid_offer_q` exige `confirmed_quantity__isnull=False` **y** `availability_confirmed=True`;
  - las vistas y la exportación comprueban también las dos condiciones (`supplier_views.py:75`, `inventory_exports.py:732`).

  Aun así, conviene correr `migrate` en cuanto pase el healthcheck.
- **La vuelta atrás es la documentada.** Primero `migrate store 0143_…`, después las imágenes anteriores. Con `0144` aplicado no se probó la imagen anterior, porque el plan lo prohíbe.
- Sin secretos en este documento ni en los scripts: la base se pasa por `REHEARSAL_DB_URL` y las cuentas son las de `seed_demo_users`, sólo de desarrollo.
