# ADR — Corregir o anular un equipo mal ingresado (INV-UNIT-CORRECT-01)

Estado: **Propuesta**. Nada de esto está implementado. Espera las decisiones del propietario de §9 antes de
escribir código o migraciones.

Fecha: 2026-11-17. Base auditada: `master` @ `08e2897` (última migración `0172_storefront_design`).
Origen: auditoría del ingreso de inventario del 2026-11-17, hueco bloqueante **B1** y PR propuesto **#4**.
Orden de autoridad: código > migraciones > tests > docs. Las referencias tienen la forma `archivo:línea`; sin ruta,
el archivo está en `backend/store/`.

Convención de este documento:
- **FIJADO** — lo decide un invariante o una regla que ya existe en el código o en `05_DECISIONES_TECNICAS.md`. No se
  pregunta.
- **PROPUESTA** — diseño técnico recomendado; se puede discutir, pero no es una regla de negocio.
- **DECISIÓN** — regla de negocio que sólo puede tomar el propietario (§9). No se inventa.
- **NO VERIFICADO** — no se pudo comprobar en el código o en producción; se dice por qué.

## 1. Problema

Un equipo con serie que entra mal (serie o IMEI mal escrito, variante equivocada, sucursal equivocada, entrada
duplicada) no se puede corregir:

- `update_unit` cambia estado físico, costo, notas, batería, grado, notas públicas y precio, **nunca** los
  identificadores, el producto, la sucursal ni el estado (`stock_unit_services.py:637-712`; la vista lo repite:
  `stock_unit_views.py:422-457`).
- La única salida de un equipo disponible que no es venta ni apartado es la baja (`write_off`,
  `stock_unit_services.py:554-561`): `DAMAGED_EXIT` y estado `written_off`. Pero la fila dada de baja **sigue
  ocupando** su IMEI y su serie, porque las restricciones únicas valen en cualquier estado
  (`models.py:10306-10322`):
  - `unique_stock_unit_serial_per_product` — (empresa, producto, serie), sin condición;
  - `unique_stock_unit_imei_per_company` — (empresa, IMEI) si no es nulo;
  - `unique_stock_unit_imei2_per_company` — (empresa, IMEI 2) si no es nulo.
- Además, el servicio comprueba el IMEI **en ambas columnas** y en cualquier estado antes de recibir
  (`_already_in_stock`, `stock_unit_services.py:302-317`), con un bloqueo asesor por identificador
  (`_lock_identifiers`, `:398-418`).

Consecuencia: el equipo físico correcto **no puede volver a entrar** bien. Una baja «para limpiar» no sirve, y además
falsea el Kardex (dice «daño o merma» donde hubo un error de digitación).

## 2. Lo que el código ya hace (hechos)

### 2.1 Invariantes que este ADR no puede romper (FIJADO)

| # | Invariante | Dónde |
|---|---|---|
| I1 | `BranchStock.quantity == count(StockUnit AVAILABLE)` por (sucursal, producto con serie), en cada commit | `stock_unit_services.py:1-20`; DEC-SERIAL-01 |
| I2 | El único escritor de stock es `create_stock_movement`; un producto con serie sólo se mueve **con sus equipos** (`units`, exactamente `quantity`, de esa sucursal y producto) | `inventory_services.py:220-397`, `_units_for_movement` `:425-486` |
| I3 | Orden de bloqueo: fila de `BranchStock` primero (varias, en orden `(branch_id, product_id)`), equipos después | `inventory_services.py:42-48`, `:145-164`; `stock_unit_services.py:17-19` |
| I4 | El Kardex es inmutable: nada se borra; todo se compensa con otro movimiento | `models.py:1651-1665` |
| I5 | Un movimiento manual exige actor y motivo no vacío | `inventory_services.py:36`; `_reason` en `stock_unit_services.py:193-199` |
| I6 | Un equipo vendido conserva su fila; si vuelve, vuelve la **misma** fila | `models.py:10187-10208`; DEC-SERIAL-01 |
| I7 | Los identificadores ausentes son NULL, nunca un marcador («N/A», sufijos) | `models.py:10203-10205`, `stock_unit_identifiers_null_not_blank`; DEC-DEVICE-01 |
| I8 | `label_code` se asigna una vez y nunca cambia ni se reutiliza en la empresa | `models.py:10229-10236`, `unique_stock_unit_label_code_per_company` |
| I9 | El costo de un equipo vendido o dado de baja es historia: no se reescribe | `stock_unit_services.py:651-671` |
| I10 | `costs.view` decide toda superficie que muestra o escribe un costo; sin ella la clave `cost` no viaja | `stock_unit_views.py:14-21`, `cost_services.py:216-245`; DEC-COST-AUTHORITY-01 |
| I11 | Un equipo de otra empresa o de una sucursal no visible responde 404, igual que uno inexistente | `stock_unit_views.py:397-409` |
| I12 | La auditoría guarda ids y conteos, **nunca** IMEI ni serie | `stock_unit_services.py:518-523` |
| I13 | Un producto con serie es el único que tiene equipos; el modo sólo cambia con stock cero | `set_serialized`, `stock_unit_services.py:362-395` |

### 2.2 El costo de un equipo no mueve ningún promedio (corrige una suposición del encargo)

El encargo pide «el efecto en el costo promedio». **Para productos con serie no hay promedio**:
- DC1: los equipos llevan su costo **específico** (`StockUnit.cost`); «la sucursal no guarda promedio para ellos»
  (`cost_services.py:49-51`).
- `cost_services.resolve` con `unit_costs` devuelve la media de los costos de los equipos movidos, fuente `unit`, y
  deja `average_after = average_before` (`cost_services.py:141-159`). Si el equipo no tiene costo y la salida tiene
  costo de referencia del catálogo, sale a ese costo marcado `reference`; si no, `unknown`.
- La valorización del inventario suma el `cost` de cada equipo `AVAILABLE` (`_inventory_at_cost`,
  `inventory_services.py:2552-2591`).

Por tanto, el **efecto en costos** de anular un equipo es: la línea de Kardex de reverso lleva el costo específico
del equipo (o `reference`/`unknown`, por la regla de toda salida), y la valorización del inventario baja en ese costo.
No hay promedio que recalcular. El margen de ventas no cambia, porque el equipo nunca se vendió.

### 2.3 Cómo entra hoy un equipo

- `receive_units` (`stock_unit_services.py:428-527`) es el único que crea filas: todo o nada, un `PURCHASE_ENTRY`,
  `INITIAL_STOCK` o `MANUAL_ENTRY` por lote, con `metadata.unit_ids` de los equipos creados, más el `label_code` y la
  auditoría `stock_units_received`.
- Lo llaman el asistente de recepción (`receipt_services.confirm`, `:600-676`), el alta guiada
  (`registration_services`), la carga Excel de equipos (`unit_import_services`) y proveedores (`supply_services`).
- La recepción genera un lote de etiquetas sólo para `PURCHASE_ENTRY` e `INITIAL_STOCK` (`label_batches.py:55`).
- `receipt_services.confirm` **no** crea cuentas por pagar ni obligaciones: deja un registro en la auditoría
  (`:662-676`). Anular un equipo no toca dinero.

### 2.4 Cómo se busca hoy un equipo por su identificador

Todas estas lecturas miran **todas las filas**, en cualquier estado. Con una fila anulada que conserva sus
identificadores y una fila nueva con el mismo IMEI, cada una tiene que decidir qué hacer (§5.3):

| Lectura | Dónde | Uso |
|---|---|---|
| `find_unit` | `unit_identification.py:89-142` | caja, entrega de pedidos, devoluciones (`order_returns.py:363`), recuento (`count_services.py:227`); devuelve `ambiguous` si encuentra dos |
| `_already_in_stock` / `find_conflict` | `stock_unit_services.py:302-317`, `:332-338` | rechazo de duplicados al recibir y en la vista previa de la recepción (`receipt_services.py:546`) |
| `serial_elsewhere` | `stock_unit_services.py:320-329` | aviso de serie en otro producto |
| Carga Excel de equipos | `unit_import_services.py:337-345` | duplicados de serie e IMEI |
| `is_unit_identifier` | `unit_codes.py:46-58` | un código de producto no puede ser el IMEI o la serie de un equipo |
| Carga de productos | `import_services.py:606-612` | igual que la anterior |
| Lista de equipos | `stock_unit_views.py:148-227` | búsqueda del panel |
| Conteos por estado | `availability_views.py:165-176`, `device_catalog.py:535`, `:546` | tableros y reglas de edición de variantes |

## 3. Alcance

Dos operaciones, sólo sobre un equipo que **nunca participó en nada** después de entrar (§4):

1. **«Anular ingreso»** — el equipo no debió entrar (duplicado, identificador mal escrito, sucursal equivocada, no
   llegó). Sale del stock con un movimiento de reverso, queda registrado como **anulado** y **libera** su IMEI y su
   serie para que el equipo correcto entre por el camino normal.
2. **«Mover a otra variante del mismo modelo»** — el equipo es el correcto, pero entró en la variante equivocada
   (capacidad o color). Cambia de producto con un par de movimientos de corrección.

Fuera de alcance:
- Corregir la **sucursal** de un equipo: lo resuelve SERIAL-TRANSFER-01 (`docs/adr-serial-transfer.md`) o anular y
  volver a ingresar (DECISIÓN UC-D7).
- Corregir un equipo **vendido**, recomprarlo o recibirlo como parte de pago: es B5 y TRADE-IN D9
  (`docs/adr-trade-in.md` §5). Este ADR no lo resuelve y no lo prejuzga.
- Editar identificadores **en el lugar** (cambiar el IMEI de una fila): se descarta (§8).

## 4. Estados permitidos

**FIJADO por invariantes:**
- El equipo está `AVAILABLE`. Un equipo `RESERVED` (apartado del personal o de un carrito web, con o sin pedido) no
  se toca: primero se libera por `release` (`stock_unit_services.py:574-610`), que ya protege el pago web en curso.
  `SOLD` y `WRITTEN_OFF` quedan fuera por I6 e I9.
- El producto es de la empresa del actor y la sucursal es visible para él (I11).

**PROPUESTA — «historial limpio», verificable en el Kardex:**
- El único movimiento de stock del equipo es **su entrada**: no existe otra fila de `StockMovement` de la empresa con
  `metadata.unit_ids` que contenga su id (consulta `metadata__unit_ids__contains=[pk]`, ya usada en
  `registration_services.py:547`).
- Esto cubre de una vez «nunca vendido, nunca apartado, nunca transferido, nunca devuelto»: vender, apartar
  (`MANUAL_EXIT`), liberar (`MANUAL_ENTRY`), un apartado web (pasa por `_move`, `unit_hold_services.py:399`),
  devolver y transferir escriben todos una línea con el id del equipo.
- Además, ninguna fila de `OrderUnit` lo nombra (`models.py:10393`).
- Si la regla debe ser menos estricta (por ejemplo, permitir un apartado que se liberó), es la DECISIÓN UC-D4.

**NO VERIFICADO:** que **todo** camino que movió equipos antes de que existiera `metadata.unit_ids` lo haya
escrito. Los equipos se crearon en la migración `0106`, y `create_stock_movement` añade `unit_ids` desde entonces
(`inventory_services.py:310-312`); no se revisaron movimientos de datos anteriores. La implementación debe tratar como
«historial no limpio» un equipo cuya línea de entrada no se encuentre.

## 5. Diseño propuesto

### 5.1 «Anular ingreso» — `stock_unit_services.void_unit(unit, *, reason, actor, request)`

En una transacción:
1. Bloquear la fila de `BranchStock` (sucursal, producto) y después el equipo (I3), y releer su estado bajo el
   bloqueo.
2. Comprobar §4. Si no se cumple, `StockUnitError` 409 con un código estable (`unit_not_correctable`) y la razón:
   «está apartado», «ya se vendió», «tiene movimientos posteriores a su ingreso».
3. Escribir el reverso con el escritor único: `CORRECTION_NEGATIVE`, cantidad 1, `units=[unit]`,
   `expected_unit_status=AVAILABLE`, `reference_type='stock_unit_void'`, `reference_id=unit.pk`, motivo obligatorio
   (I5) y `metadata = {voids_movement_id, voided_unit_id, reason_code}`.
   - `CORRECTION_NEGATIVE` ya existe y es una salida (`models.py:1701`, `EXIT_TYPES`). No se agrega un tipo de
     movimiento.
   - El costo de la línea lo decide `resolve` (§2.2): el costo específico del equipo. Nadie lo escribe a mano.
4. Marcar el equipo `status='voided'` (estado nuevo, §6) con `voided_at`, `voided_by` y `void_reason`. **Los
   identificadores se conservan** en la fila: son el «valor original» y el registro de qué se anuló (I7 prohíbe
   reemplazarlos por un marcador).
5. Auditoría `stock_unit_voided` con `product_id`, `branch_id`, `movement_id`, `voids_movement_id` y el motivo
   recortado; **sin** IMEI ni serie (I12).

Resultado: `BranchStock.quantity` baja en 1 y el equipo deja de ser `AVAILABLE`, así que I1 se cumple. El IMEI y la
serie quedan libres para un nuevo `receive_units`, porque las restricciones únicas y las lecturas de §2.4 dejan de
contar las filas anuladas (§5.3, §6).

El `label_code` del equipo anulado **no** se libera (I8). El equipo que vuelve a entrar recibe un código nuevo y su
etiqueta nueva sale en el lote de la recepción. La etiqueta vieja pegada en la caja deja de resolver (§5.3).

### 5.2 «Mover a otra variante del mismo modelo» — `stock_unit_services.move_unit_to_variant(unit, *, product, reason, actor, request)`

Condiciones:
- **FIJADO:** el destino es de la misma empresa (I11, 404 si no) y es un producto con serie (I13). Si el destino exige
  IMEI (`requires_imei`) y el equipo no lo tiene, se rechaza: las reglas de limpieza de `_clean_row` para el producto
  destino deben pasar. La serie no puede existir ya en el destino (`unique_stock_unit_serial_per_product`). El IMEI
  no cambia, así que su unicidad por empresa no se ve afectada.
- **FIJADO por el encargo y la auditoría:** el destino tiene el mismo `product_model` que el origen.
- **DECISIÓN UC-D5:** si el destino puede ser de otra **línea** (nuevo ↔ seminuevo). La auditoría lo propone «dentro
  del mismo modelo y línea», y el cruce de línea toca B4 (INV-INTAKE-GUARD-01) y el precio propio de los seminuevos.
- La sucursal no cambia.
- El equipo cumple §4.

En una transacción:
1. Bloquear las dos filas de `BranchStock` de la sucursal con `_locked_branch_stocks(branch, [origen, destino])`, que
   ya las ordena (I3), y después el equipo.
2. `CORRECTION_NEGATIVE` en el producto origen, `units=[unit]`, `expected_unit_status=AVAILABLE`.
3. `StockUnit.objects.filter(pk=…).update(product=destino)`. Es el mismo patrón que `order_returns._device_back`, que
   cambia la sucursal de un equipo entre bloqueo y movimiento (`order_returns.py:503-516`).
4. `CORRECTION_POSITIVE` en el producto destino, `units=[unit]`, `expected_unit_status=AVAILABLE`.
5. Las dos líneas llevan `reference_type='stock_unit_variant'`, el mismo `reference_id` y
   `metadata = {from_product_id, to_product_id, pair_movement_id}`.
6. Auditoría `stock_unit_variant_changed`, sin identificadores.

Costo: las dos líneas llevan el costo específico del equipo (fuente `unit`). Un equipo sin costo sale con `reference`
(si el origen tiene costo de referencia) y **entra** `unknown`: es la regla de hoy (`cost_services.py:141-159`).
**NO VERIFICADO** si el tablero de costos muestra ese par asimétrico de forma comprensible.

Por qué dos movimientos y no un `UPDATE` del producto: I1 e I2. El estante del origen tiene que bajar y el del
destino subir, cada uno con su línea de Kardex reconstruible.

Efectos laterales:
- La **etiqueta** impresa nombra la variante vieja. Las correcciones no generan lote de etiquetas
  (`label_batches.py:55`). DECISIÓN UC-D6: generar un lote de reimpresión o sólo avisar.
- En un **seminuevo**, `sale_price`, fotos, batería y grado viajan con el equipo (son de la fila).
- **Recuentos:** los recuentos de accesorios rechazan productos con serie (`count_services.py:205-249`), así que los
  tipos de corrección de `_OVERLAPPING_TYPES` (`inventory_services.py:1467-1470`) no chocan con ningún recuento.

### 5.3 Lecturas que deben ignorar un equipo anulado

Todas las de §2.4 que deciden **si un identificador está ocupado** o **qué equipo nombra un código** excluyen
`status='voided'`:
- `find_unit`: un IMEI, serie o `label_code` que sólo existe en una fila anulada responde `not_found`, con un texto
  propio: «Ese equipo fue anulado: su ingreso se corrigió». Así una etiqueta vieja no vende ni entrega un equipo
  anulado.
- `_already_in_stock`, `find_conflict`, `serial_elsewhere`, la carga Excel de equipos: no cuentan anulados.
- `is_unit_identifier` y la carga de productos: **PROPUESTA** — seguir contando las filas anuladas. Esto mantiene la
  regla de LABEL-02 «un código de producto nunca es el identificador de un equipo». Que siga contándolas no impide
  nada.
- Lista de equipos: los anulados se ocultan por omisión y se ven con un filtro de estado «Anulado».
- Conteos por estado (`availability_views.py:165-176`, `device_catalog.py:535`, `:546`): un anulado no cuenta como
  equipo del producto, igual que hoy un `written_off`.

### 5.4 Superficie HTTP (PROPUESTA)

- `POST /api/admin/inventory/units/<id>/void/` `{reason, reason_code?}`.
- `POST /api/admin/inventory/units/<id>/move-variant/` `{product, reason}`.
- Se agregan a `StockUnitActionView` (`stock_unit_views.py:460-481`) y reutilizan `_UnitMixin.resolve` (I11).
- La respuesta es la ficha del equipo. Lleva `cost` y el costo del movimiento sólo con `costs.view` (I10).
- En la ficha del equipo del panel: «Corregir ingreso» abre las dos opciones. Explica que un equipo con historial no
  se corrige aquí, y por qué.
- **NO VERIFICADO:** la API móvil v1 no tiene hoy acciones sobre equipos que se hayan revisado para este ADR. Esta
  fase no las agrega.

## 6. Unicidad después de anular

### 6.1 Alternativas

| | Mecanismo | Veredicto |
|---|---|---|
| **A** | Estado nuevo `voided` y restricciones únicas **parciales** que excluyen `status='voided'` | **PROPUESTA** |
| B | Mover los identificadores a columnas `voided_*` y dejar NULL los principales | Descartada: `serial_number` es NOT NULL y está prohibido en blanco; habría que volverlo nulable en toda la tabla y en cada lector, y la fila anulada perdería su forma normal |
| C | Reescribir el identificador con un sufijo («…#ANULADO-1») | Descartada: viola I7 (un marcador es un valor) y `imei` mide 15 caracteres |
| D | Borrar la fila | Descartada: `metadata.unit_ids` del Kardex, `OrderUnit` y los lotes de etiquetas la nombran; viola I4 |

Por qué A:
- La restricción nueva es **estrictamente más débil** que la de hoy: todo dato que hoy la cumple, la sigue
  cumpliendo. Crearla no puede fallar por datos existentes.
- La serie sigue siendo única por producto **entre los equipos no anulados**. Un `written_off` **sigue ocupando** sus
  identificadores (sigue siendo un equipo que existió y se perdió). Sólo `voided` («nunca debió existir») los libera.

### 6.2 Esquema de la migración (`0173_stock_unit_void`, el número se fija al escribirla)

```python
operations = [
    # 1. Estado nuevo. Sólo cambia `choices`: no emite SQL en PostgreSQL (verificar con `sqlmigrate`).
    migrations.AlterField('stockunit', 'status', models.CharField(
        max_length=16, db_index=True, default='available',
        choices=[('available', 'Disponible'), ('reserved', 'Apartado'), ('sold', 'Vendido'),
                 ('written_off', 'Dado de baja'), ('voided', 'Anulado')])),
    # 2. Datos de la anulación: nulos o con db_default (la versión anterior sigue insertando filas).
    migrations.AddField('stockunit', 'voided_at', models.DateTimeField(null=True, blank=True)),
    migrations.AddField('stockunit', 'voided_by', models.ForeignKey(
        null=True, blank=True, on_delete=models.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
    migrations.AddField('stockunit', 'void_reason', models.CharField(
        max_length=300, blank=True, default='', db_default='')),
    # 3. Unicidad sólo entre equipos no anulados. Mismo nombre: se quita y se crea en la misma transacción.
    migrations.RemoveConstraint('stockunit', 'unique_stock_unit_serial_per_product'),
    migrations.AddConstraint('stockunit', models.UniqueConstraint(
        fields=['company', 'product', 'serial_number'], condition=~Q(status='voided'),
        name='unique_stock_unit_serial_per_product')),
    migrations.RemoveConstraint('stockunit', 'unique_stock_unit_imei_per_company'),
    migrations.AddConstraint('stockunit', models.UniqueConstraint(
        fields=['company', 'imei'], condition=Q(imei__isnull=False) & ~Q(status='voided'),
        name='unique_stock_unit_imei_per_company')),
    migrations.RemoveConstraint('stockunit', 'unique_stock_unit_imei2_per_company'),
    migrations.AddConstraint('stockunit', models.UniqueConstraint(
        fields=['company', 'imei2'], condition=Q(imei2__isnull=False) & ~Q(status='voided'),
        name='unique_stock_unit_imei2_per_company')),
    # 4. Un anulado dice cuándo. Todas las filas existentes la cumplen (ninguna está anulada).
    migrations.AddConstraint('stockunit', models.CheckConstraint(
        condition=~Q(status='voided') | Q(voided_at__isnull=False),
        name='stock_unit_voided_has_date')),
]
```

- **Reverso:** antes de volver a crear las restricciones completas, un `RunPython` (con `noop` hacia adelante)
  **se detiene** si dos filas comparten serie por producto o IMEI por empresa. Lista la empresa y los ids, nunca los
  identificadores. Es el precedente de `0166_catalog_model_normalized_name.py:12-95`. Sin ese paso, el reverso
  fallaría con un `IntegrityError` sin explicación.
- **Bloqueos:** quitar y crear un índice único sobre `store_stockunit` toma un bloqueo que detiene las escrituras de
  la tabla mientras se construye. Se corre con `lock_timeout` como en los despliegues anteriores
  (`docs/release-r3-rehearsal.md:77`). **NO VERIFICADO:** el tamaño de la tabla en producción; se mide en el ensayo
  (§6.3).
- **Compatibilidad durante el despliegue:** la versión anterior sigue en línea mientras `migrate` corre. No escribe
  `voided`, y su `_already_in_stock` es más estricto que la restricción nueva. Por lo tanto, nunca crea un duplicado
  que la base rechace.

### 6.3 Verificación previa sobre producción (sólo lectura, antes del ensayo)

```sql
-- 1. Estados presentes: sólo los cuatro conocidos.
SELECT status, count(*) FROM store_stockunit GROUP BY status;
-- 2. Las restricciones existen con los nombres esperados.
SELECT conname FROM pg_constraint WHERE conrelid = 'store_stockunit'::regclass ORDER BY conname;
-- 3. Ningún IMEI de una fila es el IMEI 2 de otra en la misma empresa (la base no lo impide; sólo el servicio).
SELECT a.company_id, a.id AS fila, b.id AS otra FROM store_stockunit a
  JOIN store_stockunit b ON a.company_id = b.company_id AND a.id <> b.id
 WHERE a.imei IS NOT NULL AND a.imei = b.imei2;
-- 4. Tamaño, para estimar el bloqueo.
SELECT count(*), pg_size_pretty(pg_total_relation_size('store_stockunit')) FROM store_stockunit;
```

Las consultas 1 y 2 deben devolver lo esperado. La 3 debe devolver cero filas: si devuelve alguna, se informa al
propietario antes de seguir, porque es un duplicado que existe hoy. Los resultados se guardan **sin** IMEI ni serie.
Para ejecutarlas se sigue el acceso de producción ya documentado y la autorización del propietario.

## 7. Permisos, auditoría, idempotencia y empresa

### 7.1 Permisos

- **FIJADO:** actuar sobre un equipo exige `inventory.adjust` en la empresa, con la sucursal del equipo visible
  (`_UnitMixin`, I11). Es la misma autoridad que la baja, el apartado y la devolución.
- **FIJADO:** ver el costo de la línea de reverso o de la ficha exige `costs.view` (I10). Ninguna de las dos
  operaciones **escribe** un costo elegido por la persona: el costo sale del equipo. Por eso, con las reglas de hoy,
  `costs.view` no es necesaria para actuar.
- **DECISIÓN UC-D2:** si basta `inventory.adjust`, o si hace falta además `costs.view`, una capacidad nueva
  (`inventory.units.correct`) o la aprobación de una segunda persona (patrón del recuento, `inventory.counts.approve`).
  El rol preestablecido «inventory» tiene `inventory.adjust` y no tiene `costs.view` (`company_provisioning.py:166-173`).

### 7.2 Auditoría

- Kardex: una línea (anular) o dos (cambiar de variante), con `reference_type`, `reference_id` y `metadata` que
  enlazan con la entrada original y con su par.
- `AdminAuditLog`: `stock_unit_voided` o `stock_unit_variant_changed`, con actor, empresa, `product_id`,
  `branch_id`, los ids de los movimientos y el motivo. Sin identificadores (I12).
- La fila del equipo guarda `voided_at`, `voided_by` y `void_reason`. Los identificadores originales quedan en la
  misma fila.

### 7.3 Idempotencia

- **Por estado, bajo bloqueo**, como `dispatch_transfer` (`inventory_services.py:1222-1230`):
  - anular un equipo ya `voided` devuelve la ficha sin escribir nada;
  - mover un equipo que ya está en la variante destino devuelve la ficha sin escribir nada;
  - una carrera entre dos personas se resuelve por el bloqueo de la fila de `BranchStock`: la segunda relee y ve el
    estado nuevo.
- No hace falta una clave de idempotencia: el estado del equipo es la clave.

### 7.4 Empresa y sucursal

- La empresa sale de la sesión, nunca del cuerpo.
- El equipo se resuelve por empresa y por las sucursales visibles. El producto destino, por empresa (404 si es de
  otra).
- Los movimientos llevan `company` y `branch` explícitos, como todo el Kardex. La restricción parcial es por empresa:
  anular en la empresa A no libera nada en la B.

## 8. Alternativas descartadas

1. **Editar serie o IMEI en el lugar.** El Kardex, las etiquetas y los documentos ya nombraron ese equipo con ese
   identificador. Corregirlo en silencio rompe la trazabilidad y abre la puerta a «convertir» un equipo en otro.
2. **Usar la baja (`write_off`).** Dice «daño o merma» donde hubo un error, y no libera los identificadores.
3. **Un tipo de movimiento nuevo («anulación de ingreso»).** `CORRECTION_NEGATIVE` y `CORRECTION_POSITIVE` ya
   existen para esto. Un tipo nuevo cambiaría `MOVEMENT_TYPE_CHOICES`, los informes y las exportaciones sin aportar
   nada que `reference_type` no diga.
4. **Liberar también a los dados de baja.** Un equipo perdido o robado que reaparece es otro caso, y otra decisión.

## 9. Decisiones del propietario

| # | Decisión | Propuesta de partida |
|---|---|---|
| UC-D1 | **Plazo:** ¿se puede anular en cualquier momento mientras el historial esté limpio, o sólo N días después del ingreso? | Sin plazo: el historial limpio ya es el límite |
| UC-D2 | **Quién:** `inventory.adjust` (como la baja), `+ costs.view`, una capacidad nueva o la aprobación de una segunda persona | `inventory.adjust` |
| UC-D3 | **Motivo:** el texto es obligatorio por I5. ¿Se agrega una lista cerrada de motivos (digitación, variante equivocada, sucursal equivocada, duplicado, no llegó)? | Lista cerrada + texto |
| UC-D4 | **Historial limpio:** estricto (sólo la entrada) o se permite un apartado ya liberado | Estricto |
| UC-D5 | **Línea:** ¿mover a otra variante permite pasar de nuevo a seminuevo o al revés? | No; sólo la misma línea |
| UC-D6 | **Etiqueta** tras cambiar de variante: lote de reimpresión automático o sólo un aviso | Lote automático |
| UC-D7 | **Sucursal equivocada:** se corrige con SERIAL-TRANSFER-01, o con anular y volver a ingresar | SERIAL-TRANSFER-01 |
| UC-D8 | **«Anular y volver a ingresar» en un solo paso** (una transacción) o dos pasos separados | Dos pasos en la fase 1 |
| UC-D9 | **Parte de pago y recompra (ADR #216, D9; B5):** confirmar que la anulación **no** aplica a equipos vendidos y que la recompra de un equipo propio sigue esperando D9 | Confirmar |
| UC-D10 | **Proveedor:** si anular un equipo comprado debe dejar una nota frente al proveedor (devolución o nota de crédito). Hoy la recepción no crea deuda | Fuera de esta fase |
| UC-D11 | **Visibilidad:** los anulados se ocultan en «Equipos» por omisión. ¿Quién ve su IMEI completo? | Las mismas personas que hoy (`inventory.view`) |

## 10. Plan de pruebas (RED → GREEN cuando se implemente)

Backend (`test_stock_units.py` y uno nuevo `test_unit_correction.py`, PostgreSQL):
- **Invariante:** después de anular o mover, `BranchStock.quantity == count(AVAILABLE)` en la sucursal, para el
  producto origen y el destino. `Product.inventory` coincide con la suma.
- **Liberación:**
  - anular y volver a recibir el mismo IMEI, el mismo IMEI 2 y la misma serie en el mismo producto funciona;
  - un `written_off` sigue bloqueándolos;
  - en otra empresa no cambia nada.
- **Restricción en la base:** dos filas no anuladas con el mismo IMEI fallan con `IntegrityError`; una anulada y
  una viva no fallan. Lo mismo para el IMEI 2 y la serie por producto.
- **Rechazos (409 con código):** el equipo está `RESERVED` (personal y web con pedido vivo), `SOLD`, `WRITTEN_OFF`,
  tiene un movimiento posterior a su entrada (apartado y liberado, devuelto, transferido) o una fila de `OrderUnit`.
- **Mover a otra variante:**
  - destino de otro modelo, de otra línea (según UC-D5), sin serie, de otra empresa (404), que exige IMEI y el equipo
    no lo tiene, o con la serie ya presente;
  - par de movimientos con el mismo `reference_id`;
  - costo específico en las dos líneas y caso sin costo (`reference` → `unknown`).
- **Lecturas:**
  - `find_unit` por IMEI, serie y `label_code` de un anulado responde `not_found` con su texto;
  - con un anulado y uno vivo del mismo IMEI, devuelve el vivo (nunca `ambiguous`);
  - la caja no vende un anulado;
  - la carga Excel y la vista previa de la recepción aceptan el IMEI liberado.
- **Kardex y costos:** tipo, cantidad, `stock_before` y `stock_after`, `unit_cost` y `cost_source`, metadatos; la
  valorización baja en el costo del equipo; ningún promedio cambia.
- **Permisos:**
  - sin `inventory.adjust`, 403;
  - sucursal no visible, 404;
  - sin `costs.view`, la respuesta no trae `cost`;
  - según UC-D2.
- **Auditoría:** acción, ids y motivo; nunca IMEI ni serie en `metadata`.
- **Idempotencia y concurrencia:** doble anulación; anulación frente a una venta en caja del mismo equipo (dos hilos,
  `TransactionTestCase`): gana una y la otra se rechaza sin descuadre.
- **Migración:**
  - con datos sintéticos, hacia adelante;
  - el reverso sin duplicados funciona;
  - el reverso con un anulado y un vivo con el mismo IMEI **se detiene** con el mensaje de ids.

Frontend (Vitest y E2E con datos sintéticos): «Corregir ingreso» en la ficha; se oculta o explica cuando el equipo
tiene historial; filtro «Anulado»; sin la clave `cost` para quien no tiene `costs.view`.

## 11. Qué NO se verificó

- Datos de producción: estados, tamaño de `store_stockunit` y duplicados IMEI/IMEI 2 entre columnas (§6.3).
- Que todo movimiento de equipos anterior a `metadata.unit_ids` lo tenga (§4).
- La pantalla de la ficha del equipo en el frontend y la API móvil v1.
- Si alguna exportación o informe (`inventory_exports.py`) enumera los estados a mano y necesita el nuevo.
- Si `sqlmigrate` de la `AlterField` de `choices` sale vacío en la versión de Django del proyecto.
