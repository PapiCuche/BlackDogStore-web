# ADR — Migración y recuento inicial: el lado del recuento (LABEL-MIGRATION-02 · MIGRATION-A)

Estado: **PROPUESTA**. Nada de esto está implementado. Cada decisión lleva su estado (`PROPUESTA`, `BLOCKED`).
Base auditada: `master` @ `f71ccda` (migraciones hasta `0145`). Producción sirve R1.
Orden de autoridad: código > migraciones > tests > docs. Referencias `archivo::símbolo` (líneas de `f71ccda`).

Contexto: `docs/adr-inventory-labels.md` §1.1 (decisiones del propietario), D12 (importación histórica) y §7
(fases y archivos). Este ADR cubre **sólo el recuento**: la reconciliación del catálogo y la vista previa de la
importación son de MIGRATION-B (`feat/label-migration-01`); los códigos, de LABEL-01; los diseños y el PDF, de
LABEL-03.

**Regla de este ADR: no hay un segundo motor de recuento ni un segundo escritor de stock.** Se extiende
`InventoryCount` / `InventoryCountItem` y se aplica por `inventory_services::create_stock_movement` y
`stock_unit_services`. Todo lo nuevo es una extensión mínima de lo que existe.

## 1. Auditoría: qué existe

### 1.1 Modelo — `models.py::InventoryCount` (L3060), `InventoryCountItem` (L3160)

- **Un recuento = una sucursal** (`company`, `branch`, `reason`, `metadata`), con `created_by`, `approved_by`,
  `cancelled_by` y sus fechas. `clean()` impide una sucursal de otra empresa.
- **Estados:** `draft` → `counting` → (`review`) → `approved` · `cancelled`.
  - `APPROVABLE_STATUSES = {counting, review}`; `EDITABLE_STATUSES = {draft, counting, review}`.
  - **`review` no lo pone ningún código** (`rg STATUS_REVIEW` sólo lo encuentra en el modelo): existe en las
    opciones, el filtro y la pantalla, pero no hay transición hacia él.
  - `draft` pasa a `counting` con la primera línea (`set_count_item`).
- **Línea:** única por `(count, product)` (`unique_product_per_count`).
  - `theoretical_at_start` (entero, por defecto 0): la foto del stock;
  - `physical_quantity` (nulo = **no contado**, distinto de 0);
  - `theoretical_at_approval` y `difference`: se escriben al aprobar, como evidencia;
  - `note`. No hay quién contó, cuándo, ni cantidad de referencia.
- Sin restricción de un recuento abierto por sucursal: puede haber varios a la vez.

### 1.2 Servicios — `inventory_services.py` L1383–1608

- `create_inventory_count`: sucursal de la empresa y activa. La vista añade `inventory_count_created` a la
  auditoría con `company`.
- `set_count_item` (L1403):
  - rechaza producto ajeno, cantidad negativa y **producto serializado** («se revisa equipo por equipo en
    Inventario › Equipos»);
  - **la foto se toma al crear la línea** (`branch_quantity`, lectura sin bloqueo), aunque se cree con
    `physical_quantity=None`, y no se vuelve a tomar nunca (`test_theoretical_at_start_is_captured_and_never_overwritten`);
  - **no bloquea el recuento**: decide `is_editable` sobre el objeto que trae el llamador. Una anotación puede
    escribirse en paralelo con la aprobación (que sí bloquea) y quedar en un recuento ya aprobado sin aplicarse;
  - no deja fila de auditoría (`AUDIT-03`, LOW, PROPUESTA).
- `approve_inventory_count` (L1454) — **ERP-1 · INV-02**:
  - `select_for_update` del recuento; si ya está `approved`, devuelve sus movimientos (**idempotente**);
  - sólo líneas contadas; las no contadas **se saltan** (no se dan de baja);
  - bloquea las filas `BranchStock` en orden `(branch_id, product_id)` (`_locked_branch_stocks`);
  - **corrección = `physical − theoretical_at_start`, sumada como delta al stock actual**, nunca
    `physical − theoretical_at_approval` (que reescribiría el estante a la foto y «desvendería» lo vendido
    durante el conteo). `theoretical_at_approval` queda como evidencia;
  - `correction_positive` / `correction_negative` con `inventory_count`, `reference_type='inventory_count'` y la
    aritmética en `metadata`; todo en una transacción; auditoría `inventory_count_approved` con `company`.
- `cancel_inventory_count`: bloquea; uno aprobado no se anula.
- Pruebas: `tests.py::Phase2dInventoryCountTest` (L12640): casos A–E de ERP-1, doble aprobación, no contado,
  anulación, auditoría y agregado `Product.inventory`.

**Lo que la aritmética da por supuesto:** que la cantidad física se observó **en el momento en que se anotó por
primera vez**. Los movimientos *posteriores* a esa anotación son correctos (casos C y E). No cubre:
1. una línea creada en blanco y contada horas después (la foto es de la creación, no de la observación);
2. una línea **corregida** después de una venta (`test_theoretical_at_start_…`): ¿el 7 nuevo es el estante antes o
   después de la venta? Se resta dos veces o ninguna;
3. **conteo por escaneos sucesivos** (+1 durante una hora): la observación dura lo que dura el escaneo;
4. **dos correcciones del mismo descuadre**: otro recuento, un ajuste manual o un `reconcile_target` de la carga de
   stock aprobados sobre el mismo `(sucursal, producto)` después de la foto se suman a la corrección de éste;
5. **mercadería pagada en la web y aún no despachada**: la venta descontó el stock al pagarse
   (`views.py:686` → `record_sale_stock_movements`) pero sigue en la tienda hasta «Enviado»/«Entregado», y su
   salida física **no deja movimiento** (`order_fulfillment_services`). Contarla da un sobrante falso.

### 1.3 Vistas y permisos — `inventory_views.py` L1260–1495, `urls.py` L378–380

- `GET/POST /api/admin/inventory/counts/`, `GET …/<pk>/`, `PUT …/<pk>/items/` (aditivo por producto),
  `POST …/<pk>/approve/`, `POST …/<pk>/cancel/`. **No hay rutas v1** (`docs/internal-parity-matrix.md` L97–99: C).
- Ver: `inventory.view`. Crear, anotar, **aprobar** y anular: **`inventory.adjust`** — la misma capacidad. No hay
  separación entre quien cuenta y quien aprueba, ni capacidad propia de aprobación.
- Alcance: `_scoped_count` filtra por `visible_branches`; ajeno = 404.
- `InventoryCountSerializer` (`serializers.py` L1445) **no expone costos**.

### 1.4 Pantalla — `frontend/app/admin/inventory/counts/`

- Lista y alta por sucursal; detalle con una cuadrícula de **los primeros 100 productos** (`page_size: 100`),
  un campo numérico por producto, «Guardar cantidades» y «Aprobar».
- Sólo envía lo escrito: blanco = no contado (bien). Nunca envía `null`, así que una línea no vuelve a «no contado».
- **Sin escáner, sin búsqueda, sin pendientes, sin modo incremental, sin equipos.**

### 1.5 Lo que rodea al recuento

| Pieza | Qué hace hoy | Para el recuento |
|---|---|---|
| `create_stock_movement` (L220) | único escritor; bloquea `BranchStock`; serializado sólo con sus unidades; costo DC1 bajo el mismo bloqueo; acepta `inventory_count=` | el camino de aplicación, sin cambios |
| `StockMovement` | `company`, `branch`, `stock_before/after`, `inventory_count`, `metadata`; el `id` se asigna **con la fila `BranchStock` bloqueada** | marca de agua por línea (§3) |
| `stock_import_services` | `reconcile_target`: `target − current` **al aplicar** (reescribe a la foto: bien para un archivo, mal para un conteo de horas); `initial`: `_assert_initial_still_valid` exige stock 0 **y** sin Kardex, bajo bloqueo; rechaza serializados | la regla INITIAL se reutiliza; el modo `reconcile_target` **no** sirve para el recuento |
| `unit_import_services::apply_units` | todo o nada por `receive_units`; `PURCHASE_ENTRY` | no sirve para sobrantes de un recuento (no es una compra) |
| `stock_unit_services` | `receive_units` (tipos `purchase_entry`, `initial_stock`, `manual_entry`); `_move` (una unidad + su Kardex); `write_off` (`damaged_exit`); `reserve`/`release`; `return_unit` | resoluciones de equipos (§4) |
| Unidades: estados | `available`, `reserved` (apartado o carrito web), `sold`, `written_off`. Un `written_off` **no tiene vuelta**; `receive_units` rechaza un IMEI ya registrado en **cualquier** estado | un faltante dado de baja y luego hallado queda atascado (§4.5) |
| Transferencias | `set_transfer_item` **rechaza serializados** («todavía no se transfiere») | no hay camino canónico para mover un equipo de sucursal |
| `unit_identification::find_unit` (L88) | IMEI (Luhn, `imei` o `imei2`) → serie; `invalid_code`, `not_found`, `ambiguous` | clasificador de escaneos (§4.2), sólo lectura |
| `OrderUnit` | qué equipo salió en qué pedido; `left_at` nulo = vendido, aún en tienda | «vendido pendiente de entrega» |
| Caja: `AdminPosLookupView` | `ProductBarcode` exacto por `normalize_barcode` (no cambia mayúsculas ni ceros), producto activo; lógica en la vista, sin servicio | el recuento resuelve igual, en su propio módulo |
| `registration_services` | alta guiada idempotente (`Idempotency-Key` + huella en la auditoría + bloqueo consultivo), `dry_run` | patrón de idempotencia, si hiciera falta |
| #162 (sin fusionar, `0146`) | `ExternalUsedCard` (ficha de seminuevo externo con `reported_imei`) y `SupplyReceptionIncident` | un IMEI de ficha no recibida se manda a la recepción, no al recuento (§6) |

## 2. Mapa de reutilización y brechas por requisito

| # | Requisito | Se reutiliza | Brecha → fase |
|---|---|---|---|
| 1 | Asistente A–I reanudable | `BulkImportJob` (A–E, MIGRATION-B), `InventoryCount` (F–H), `LabelBatch` (I, LABEL-03) | el vínculo trabajo → recuentos y la pantalla que deduce el paso del estado del servidor → **C** (vínculo), **F** (pantalla) |
| 2 | Accesorios por sucursal | `InventoryCountItem`, nulo ≠ 0, ERP-1, `normalize_barcode` | escaneo/búsqueda, modo incremental, pendientes, referencia antigua, foto al observar, revisión → **C** (API), **D** (pantalla) |
| 3 | Equipos serializados | `find_unit`, `StockUnit`, `receive_units`, `_move` | líneas por unidad, clasificación, incidencias, resoluciones por los servicios canónicos → **E** |
| 4 | Corte y conciliación | foto + delta (ERP-1), bloqueos ordenados, regla INITIAL | marca de agua, ventana de observación, reconteo obligado, correcciones solapadas, pendientes de despacho → **C** |
| 5 | Aprobación | idempotencia por estado bajo bloqueo, auditoría con `company`, todo en una transacción | transición a `review`, bloqueo del recuento al anotar, resumen previo, capacidad propia, valorización con `costs.view`, ninguna línea pendiente sin explicar → **C** (reglas), **F** (resumen y pantalla) |
| 6 | Etiquetas tras aprobar | (LABEL-03: plantillas y lotes; LABEL-02: símbolo IMEI 1) | «Impresión inicial» desde un recuento aprobado → **G** |
| 7 | Ofertas de proveedor fuera | `SupplierOffer` y `ExternalUsedCard` no tienen stock | el recuento nunca las lista; un IMEI de una ficha no recibida remite a la recepción → **E** |

## 3. Política de corte y conciliación (MC1) — PROPUESTA

### 3.1 Alternativas

| | Política | Veredicto |
|---|---|---|
| A | Congelar la sucursal (sin ventas) mientras dura el conteo | **No**: el propietario no quiere detener una sucursal, y menos todas |
| B | Foto al abrir el recuento y **reemplazo** al aprobar (`target − current`, como `reconcile_target`) | **No**: desvende lo vendido durante el conteo (ERP-1) |
| C | Foto por línea en la primera anotación y **delta** al aprobar (lo que hay) | Correcto sólo si cada línea se observa en un instante; falla en los casos 1–5 de §1.2 |
| D | **C + marca de agua por línea + ventana de observación + reconteo obligado** | **Elegida** |
| E | Congelar sólo la línea mientras se cuenta (la caja rechaza ese producto unos minutos) | Opción futura para productos de alta rotación; invasiva; no ahora |
| F | Contar con la tienda cerrada | Operativa, no de código: D la cubre igual (ventanas vacías) |

### 3.2 La regla elegida (D)

Por línea de accesorio:

1. **La foto se toma al observar, no al crear la línea.** Una línea creada para listar un pendiente (con su
   cantidad de referencia) **no tiene foto** hasta su primera cantidad. Al anotarla, bajo el bloqueo de su
   `BranchStock` (`_locked_branch_stocks`, mismo orden del módulo), se guardan:
   - `theoretical_at_start` = cantidad actual;
   - `snapshot_movement_id` = el mayor `StockMovement.id` de ese `(sucursal, producto)`;
   - `first_counted_at`, `counted_by`.

   La marca de agua es exacta porque todo movimiento de ese par se crea con esa misma fila bloqueada: lo que
   tenga un id mayor ocurrió después de la foto.
2. **Cada anotación posterior** guarda `last_entry_movement_id` y `last_counted_at`.
3. **Ventana de observación** = movimientos del par con `snapshot_movement_id < id ≤ last_entry_movement_id`.
   - Vacía: la línea es limpia, la aritmética de ERP-1 es exacta.
   - **No vacía: la línea es ambigua** (no se sabe si lo contado incluye o no ese movimiento). No se aprueba
     hasta que se **recuente**: la línea vuelve a «no contado» y la siguiente anotación toma foto y marca nuevas.
   - Una corrección con la ventana vacía (error de tecleo) mantiene la foto. Con la ventana no vacía, la API
     exige elegir: `recount` (nueva observación) o `correction` (la misma observación mal escrita; queda
     registrada con quién y por qué).
4. **Movimientos posteriores a la última anotación:** correctos y se respetan (casos C y E de ERP-1), **salvo
   las correcciones**: un `correction_*` del mismo par con id mayor que `snapshot_movement_id` que no sea de este
   recuento (otro recuento, un ajuste manual, una carga de stock) es **corrección solapada** y bloquea la línea:
   aplicar ambas corregiría dos veces el mismo descuadre.
5. **Transferencias en tránsito hacia la sucursal** del producto: aviso en la línea («llega mercadería en
   camino: si ya está en el estante y no se ha recibido, recíbela antes de contar»). La recepción crea
   `transfer_in`; si cae dentro de la ventana, la regla 3 la atrapa.
6. **Pagado en la web y no despachado** (§1.2, caso 5): la línea muestra «N apartadas para pedidos pagados: no
   las cuentes». La regla propuesta es contar **sólo lo vendible** y separar físicamente lo pagado (P1). La
   alternativa —contarlo y restarlo— necesita una foto de los pendientes de despacho que cambia sin movimiento:
   no se elige.
7. **Nunca:** sumar lo contado al stock actual; aplicar la cantidad antigua; registrar `initial_stock` donde ya
   hay Kardex (se reverifica bajo bloqueo, como `_assert_initial_still_valid`); compensar en silencio una venta
   del conteo; reescribir un movimiento.

El recuento guarda además `counting_started_at` (primera anotación), `submitted_at/by` (paso a revisión) y, en
cada movimiento de la aprobación, `theoretical_at_start`, `snapshot_movement_id`, `last_entry_movement_id`,
`theoretical_at_approval`, `physical_quantity`, `difference` y los ids de la ventana posterior
(`after_entry_movement_ids`): quien audita reconstruye la línea sin recalcular nada.

## 4. Recuento de equipos serializados (MC2) — PROPUESTA

### 4.1 Líneas por unidad o registro de escaneos

| | Diseño | Veredicto |
|---|---|---|
| a | **Registro de escaneos** (cada lectura una fila, el estado se deriva) | auditoría máxima, pero cada pantalla y la aprobación recalculan todo; los escaneos inválidos llenan la tabla |
| b | **Una línea por identificador** (`InventoryCountUnit`), con las lecturas resumidas en la línea | **Elegido**: la línea *es* la incidencia, lleva su resolución, es idempotente (único por recuento y unidad) |

`InventoryCountUnit` (MIGRATION-E):
- `count`, `company` (para filtrar), `unit` (FK nula a `StockUnit`, `PROTECT`), `product` (nulo hasta decidirse);
- lo leído: `scanned_code` (normalizado; el IMEI completo sólo con `inventory.view`, nunca público),
  `scan_count`, `first_scanned_at`, `last_scanned_at`, `scanned_by`;
- `classification` (§4.2), recalculada al leer y al aprobar;
- para un equipo **no registrado**: `imei`, `imei2`, `serial_number`, `condition`, `cosmetic_grade`,
  `battery_health` (sólo usado y **medido**; nulo = «no informado»), `cost` (sólo `costs.view`), siempre como
  los escribió una persona: **nunca se inventa un IMEI ni una serie**, se validan con `device_identity`;
- `resolution` + `resolution_reason` + `resolved_by`;
- únicos: `(count, unit)` si hay unidad; `(count, scanned_code)` si no.

Escanear dos veces la misma unidad sólo sube `scan_count` («ya contado»): nunca cuenta dos. Un código inválido
**se responde al instante y no se guarda** (no hay nada que aplicar); si el equipo existe pero su etiqueta no
sirve, la persona crea a mano una línea «no registrado» con la serie y una nota.

### 4.2 Clasificación (lectura con `find_unit`, sin modificar `unit_identification`)

| Resultado de `find_unit` y estado de la unidad | Clase | ¿Bloquea la aprobación? |
|---|---|---|
| `invalid_code` | inválido | no se guarda |
| `ambiguous` (serie compartida entre productos) | identificador duplicado | sí, hasta leer el IMEI |
| `not_found` | **encontrado, no registrado** | sí, hasta resolver |
| `available`, en esta sucursal | **registrado y disponible aquí** | no (verificado) |
| `available`, en otra sucursal | **registrado en otra sucursal** | sí, hasta resolver |
| `reserved` (apartado o carrito web) | **apartado** | no: se verifica, **no se toca** |
| `sold`, con `OrderUnit.left_at` nulo | vendido, pendiente de entrega | no: se verifica, no se toca |
| `sold` y ya salió | **vendido** | sí: incidencia; el escaneo **no lo devuelve** |
| `written_off` | dado de baja | sí: incidencia |
| esperado y no escaneado al cerrar | **registrado, no encontrado** | sí, hasta resolver |

Con #162 fusionado: un `not_found` cuyo IMEI está en una `ExternalUsedCard` sin recibir se anota «llegó un
seminuevo de proveedor sin recibir: regístralo por la recepción» y **no** se resuelve en el recuento.

**Esperados** (se materializan como líneas «no encontrado» al pasar a revisión): unidades de la empresa con
`branch` = la del recuento y estado `available` o `reserved`. Una unidad esperada que entró o cambió después de
`counting_started_at` (`received_at` o `updated_at` posteriores; conservador) y no se escaneó queda **«por
escanear»**, no «faltante». Una vendida durante el conteo ya no es esperada: no hay aritmética de cantidades en
equipos, la identidad resuelve el corte.

### 4.3 Resoluciones (sólo por los escritores canónicos, sólo al aprobar)

| Clase | Resoluciones posibles | Cómo se escribe |
|---|---|---|
| no registrado | `register` (producto o variante elegidos, identificadores escritos) · `ignore` con motivo | `receive_units(..., movement_type=initial_stock` si el par no tiene historia, reverificado bajo bloqueo; si no, `correction_positive`, `inventory_count=`)` |
| no encontrado | `missing` · `ignore` con motivo (p. ej. en servicio técnico) | `stock_unit_services.count_missing` (nuevo, §4.5): `correction_negative` con la unidad |
| en otra sucursal | `leave` (queda donde figura; incidencia abierta) · `relocate` (P4) | `relocate` sólo si el propietario lo aprueba: traslado mínimo de una unidad (`transfer_out` + `transfer_in` con la unidad, bloqueando las dos filas en orden) |
| vendido y salido · dado de baja | `leave` con motivo | nada en el recuento; la devolución (`return_unit`) o la recuperación se hacen fuera, por quien tiene permiso |
| duplicado | volver a escanear por IMEI | — |

Ninguna resolución recrea una unidad existente, cambia una vendida o apartada, ni escribe fuera de
`stock_unit_services` → `create_stock_movement`.

### 4.4 Lo que el escaneo muestra

Producto, variante (las `VariantOption` del producto), sucursal, IMEI 1 y 2, serie, condición, grado estético,
batería (sólo usado y medido) y **costo sólo con `costs.view`** (`unit_identification.describe` más esos campos).
Nunca un endpoint público.

### 4.5 Extensiones mínimas de `stock_unit_services` (MIGRATION-E)

- `receive_units`: acepta además `correction_positive` y un `inventory_count` que pasa al movimiento.
- `_move`: acepta `inventory_count` opcional.
- `count_missing(unit, count, reason)`: `available` → `written_off`, `correction_negative`, motivo «Faltante en
  recuento #N». Reutiliza el estado existente para no tocar las consultas de estados (P3).
- `count_found(unit, count, reason)`: `written_off` → `available`, `correction_positive`, **sólo** para una
  unidad dada de baja por `count_missing`. Hoy un faltante hallado después no tiene vuelta, porque
  `receive_units` rechaza su IMEI en cualquier estado.

## 5. Modo incremental, total, blanco y cero (MC3) — PROPUESTA

- **Total** (`PUT …/items/` como hoy, más `entry`): fija la cantidad; idempotente por naturaleza.
- **Incremental** (`POST …/scan/` con `mode: "increment"`): el servidor bloquea la línea y suma 1 (o `step`).
  Para que un reintento de red no sume dos veces, la petición lleva **`expected`**, el valor que mostraba la
  pantalla. Si no coincide, responde 409 con el valor actual y no suma. Es una comparación y escritura, **sin
  tabla nueva**. Dos personas en el mismo producto se turnan.
- La pantalla dice siempre en qué modo está, con un interruptor visible, y el modo incremental no se activa solo.
- Un escaneo de un producto serializado en el modo de accesorios se rechaza y remite al de equipos, y al revés.
- **Blanco = no contado** (`physical_quantity` nulo). **Cero explícito = verificado que no hay.**
  - Nunca se rellena lo contado con la cantidad antigua; la antigua se ve en su columna «Referencia», sin edición.
  - En un recuento de **migración (completo)**, cada línea pendiente (teórico > 0 o referencia > 0) tiene que
    estar contada o **excluida con motivo** (`excluded`, `exclusion_reason`) para aprobar. En uno ordinario se
    mantiene la regla actual: las no contadas se saltan.
  - Los productos con stock y sin línea se vuelven a leer con las filas de stock ya bloqueadas (#183). Queda
    aceptada una ventana residual: un producto que no tenía stock en esa segunda lectura y lo recibe antes de que
    la aprobación confirme no la frena, porque bloquear toda la sucursal para cerrarla serializaría cada venta
    con la aprobación. Ese stock entra con su movimiento de Kardex y aparece en el siguiente recuento.
  - «Volver a no contado» es una acción explícita (`recount`), no un campo vacío.

## 6. Aprobación por el camino existente (MC4) — PROPUESTA

- **Transición nueva** `submit_for_review` (`counting` → `review`, bloqueando): congela la anotación de los
  contadores, materializa los equipos «no encontrados» y deja la lista lista para revisar. En `review` sólo
  quien aprueba puede pedir un reconteo de líneas (vuelven a `counting`). Un recuento de migración se aprueba
  **sólo desde `review`**.
- `set_count_item` y las anotaciones nuevas **bloquean el recuento** (`select_for_update`) y deciden el estado
  bajo ese bloqueo: ninguna anotación cae en un recuento aprobado.
- **Resumen previo** (`GET …/approval-preview/`, no escribe): líneas revisadas y pendientes, excluidas, ambiguas,
  solapadas; equipos verificados, sobrantes, faltantes y conflictos; **los movimientos de Kardex previstos**
  (tipo y cantidad por línea, calculados como en la aprobación); valorización sólo con `costs.view`
  (diferencia × `average_cost` o costo de referencia; costo de la unidad en equipos), si no, el campo no viaja.
- **`approve_inventory_count` sigue siendo el único camino de aplicación.** Se extiende:
  - bloqueo del recuento → **todas** las filas `BranchStock` del recuento (accesorios y equipos) en una sola
    llamada ordenada → unidades. El mismo orden del módulo;
  - se niega con un detalle por línea si hay pendientes sin explicar, ambiguas, solapadas o equipos sin
    resolver. **Todo o nada**: nunca movimientos parciales;
  - tipo por línea: `initial_stock` si el par tiene stock 0 y ningún Kardex, reverificado bajo bloqueo (la
    misma regla que `_assert_initial_still_valid`, sin degradar en silencio); si no, `correction_*`. La cantidad
    es siempre `physical − theoretical_at_start`;
  - equipos: sus resoluciones por §4.3, dentro de la misma transacción;
  - idempotencia: la de hoy (estado bajo bloqueo); un segundo «Aprobar» devuelve los movimientos existentes;
  - auditoría `inventory_count_approved` con `company`, sucursal, responsable, motivo, cantidades por tipo y
    resoluciones de equipos (cuántos, nunca los IMEI).
- **Quién aprueba:** capacidad nueva **`inventory.counts.approve`**. La migración de datos la da a todo preajuste
  que hoy tenga `inventory.adjust` (sin regresión; el propietario la quita después si quiere) — patrón `0126`.
  En el puente heredado (sin membresía) aprueban los mismos roles que hoy (`_LEGACY_INVENTORY_ADJUST_ROLES`).
  Para un recuento de migración se exige además (P2) que quien aprueba no sea el único que contó, si la empresa
  lo activa.
- La anotación sigue dejando su rastro en la propia línea (`counted_by`, fechas); `AUDIT-03` se cierra con la
  auditoría de `submit_for_review`, `recount` y `correction`.

## 7. Extensiones de modelo y migraciones (MC5) — PROPUESTA

| Fase | Cambio | SQL |
|---|---|---|
| C | `InventoryCount`: `kind` (`regular` · `migration`, `db_default='regular'`), `source_job` (FK nula a `BulkImportJob`, `PROTECT`), `counting_started_at`, `submitted_at`, `submitted_by` | columnas nulas o con `db_default`; sin reescritura |
| C | `InventoryCountItem`: `theoretical_at_start` **nulo** (sin foto hasta contar), `reference_quantity` (nulo), `snapshot_movement_id`, `last_entry_movement_id` (`BigIntegerField` nulos, no FK), `first_counted_at`, `last_counted_at`, `counted_by`, `excluded` (`db_default=False`), `exclusion_reason`, `entry_note` | `DROP NOT NULL` + columnas nulas |
| C | Restricción condicional: **un recuento de migración abierto por sucursal** (`kind='migration'` y estado abierto) | índice único parcial (`lock_timeout`) |
| C | Capacidad `inventory.counts.approve` en el catálogo y en los preajustes (migración de datos) | datos |
| E | `InventoryCountUnit` (§4.1) | tabla nueva |

Numeración: hoy la hoja de `master` es **`0145`**. **#162 trae `0146` sin fusionar**, y LABEL-01 (1), LABEL-02
(2–3) y LABEL-03 (1–2) también añaden. Regla: **el número se toma al fusionar**. Cada PR con migraciones se pone
al día con `master`, renombra la suya a hoja + 1 y corrige `dependencies`, corre `makemigrations --check
--dry-run` y se fusiona solo (nunca dos PR con migraciones a la vez). Provisionales: C `0147_inventory_count_migration`
+ `0148_inventory_counts_approve_capability`; E la siguiente libre. Despliegue: aditivo; la vuelta atrás es
de código, porque las columnas nuevas son nulas o tienen `db_default`.

Las filas existentes quedan `kind='regular'` y con su foto: su comportamiento no cambia, salvo dos
endurecimientos que valen para todos: el bloqueo del recuento al anotar y el rechazo de correcciones solapadas.

## 8. Persistencia del asistente y entrega de MIGRATION-B (MC6) — PROPUESTA

El asistente no tiene una «sesión» propia: **cada paso es un registro del servidor**, y la pantalla deduce dónde
va de su estado. Volver otro día no pierde nada; nada se guarda sólo en el navegador (salvo comodidades).

| Paso | Registro | Dueño |
|---|---|---|
| A–E archivo, columnas, actual/nuevo/ambiguo, códigos, catálogo | `BulkImportJob(import_type=products)` + `BulkImportRow` (vista previa sin escribir; aplicado = catálogo listo) | MIGRATION-B |
| F recuento físico | un `InventoryCount(kind=migration, source_job=job)` **por sucursal** | C / D / E |
| G diferencias · H aprobación | el mismo recuento en `review` → `approved` | C / F |
| I etiquetas | el `LabelBatch` de la impresión inicial (único por recuento) | G (LABEL-03) |

**Lo que la vista previa de MIGRATION-B tiene que entregar** (contrato; MIGRATION-B lo escribe en
`BulkImportRow.normalized_data`, y C sólo lo lee):
1. `product_id` resuelto **también para las filas creadas**: hoy `apply_products` no lo devuelve a la fila. Se
   escribe al aplicar.
2. `reference_quantities`: lista de `{branch_id, quantity}` con la cantidad antigua por sucursal, enteros ≥ 0
   o ausentes. La sucursal es la que eligió la persona para cada columna (como `branch_map` de la carga de
   stock), nunca deducida de un número en la cabecera. **Sólo referencia: nunca se aplica.**
3. El trabajo en `applied` antes de abrir los recuentos: un recuento nunca cuenta contra un catálogo sin
   reconciliar. `create_migration_count(job, branch)` (C) se niega si no lo está.

`create_migration_count` crea las líneas pendientes (productos no serializados con referencia o stock en esa
sucursal) **sin foto y sin cantidad**, con `reference_quantity`. Las unidades no llevan referencia: las
esperadas salen de `StockUnit`.

## 9. Fases MIGRATION-C a G, archivos y paralelismo

| Fase | Contenido | Archivos (exclusivos de la fase mientras dura) | Depende de |
|---|---|---|---|
| **C** · motor de accesorios (backend) | §3, §5, §6 (reglas, `submit_for_review`, resumen de accesorios, capacidad), §7 (C), §8 (`create_migration_count`) | `models.py` (sólo `InventoryCount`/`InventoryCountItem`), sus 2 migraciones, `capabilities.py` (una entrada), `inventory_services.py` (**sólo** la sección «Physical counts», L1383–1608), `count_services.py` (nuevo: escaneo de productos, incremento, reconteo, revisión, resumen, migración), `count_views.py` (nuevo), `serializers.py` (sólo los de recuento, L1429–1485), `urls.py` (sólo el tramo de `counts/`), `test_count_migration.py` (nuevo); los ajustes que pidan las pruebas de `tests.py::Phase2dInventoryCountTest` | ninguno; la parte de §8 lee el contrato, no el código, de MIGRATION-B |
| **D** · pantalla de accesorios | escáner/búsqueda, total/incremental, pendientes, referencia, ambiguas, reconteo, revisión | `frontend/app/admin/inventory/counts/**`, `frontend/app/admin/lib/counts-api.ts` (nuevo; `app/lib/inventory.ts` sólo para quitar lo que se mueva), `frontend/__tests__/inventory-count-*.test.tsx` | contrato de C (puede empezar en paralelo con datos simulados; se fusiona después de C) |
| **E** · equipos (backend) | §4 | `models.py` (sólo `InventoryCountUnit`) y su migración, `count_unit_services.py` (nuevo: clasificación, esperados, resoluciones), `stock_unit_services.py` (§4.5), el tramo de equipos de `approve_inventory_count`, `count_views.py` (rutas de equipos), `test_count_units.py` | C fusionada; **LABEL-02 fusionada** (también edita `stock_unit_services.py` y `find_unit`) |
| **F** · aprobación y asistente | resumen completo (accesorios + equipos + valorización), pantalla de revisión y aprobación, escaneo de equipos en la pantalla, asistente «Inventario → Migración y recuento inicial» que enlaza los pasos A–E de MIGRATION-B **sin editar `ImportWizard.tsx`** | `frontend/app/admin/inventory/migration/**` (nuevo), `frontend/app/admin/components/CountUnitScanner.tsx`, `CountApprovalSummary.tsx` (nuevos), `internal-modules.ts` (una entrada; LABEL-03 añade otra: fusionar de una en una), `count_views.py` (resumen) | C, D, E; MIGRATION-B fusionada |
| **G** · impresión inicial | «Impresión inicial» de un recuento aprobado: accesorios 30×20 con copias configurables (por defecto, lo contado; P9) y su código existente; equipos 50×25, uno por unidad confirmada, símbolo = IMEI 1 válido (regla de LABEL-02), batería sólo si usado y medida; único por recuento; nunca automática al importar | `count_labels.py` (nuevo, llama al servicio de lotes de LABEL-03), una ruta, el botón del paso I en `admin/inventory/migration/**`, `test_count_labels.py` | F, LABEL-02, LABEL-03 (`LabelBatch`) |

Paralelismo seguro:
- **Ahora:** C (backend) y D (frontend, con simulaciones) no comparten archivos. MIGRATION-B
  (`import_services`, `import_formats`, `import_exports`, `import_views`, `ImportWizard.tsx`), LABEL-01 y LABEL-03
  (dibujo puro) tampoco.
- `models.py` lo tocan C, E, LABEL-01/02/03 y #162: tramos distintos, pero **las migraciones se fusionan de una en
  una** (§7).
- E **no** empieza su parte de `stock_unit_services.py` hasta que LABEL-02 esté en `master`. Su parte nueva
  (`count_unit_services.py`, modelo) puede adelantarse en una rama apilada sobre C.
- `urls.py`, `internal-modules.ts`, `02_` y `07_`: tramos distintos, fusión de una en una.
- Ninguna fase toca 02/05/07/NEXT_ACTION fuera de su propia entrada al cerrar.

## 10. Pruebas (requisitos del propietario → fase)

| Prueba | Fase |
|---|---|
| Blanco = no contado: no se aplica ni se da de baja; cero explícito = verificado (crea la corrección a 0) | C |
| Nunca se rellena lo contado con la cantidad antigua; la referencia se ve y no se aplica | C / D |
| Total frente a incremental: el total fija; el incremento suma 1; un reintento con `expected` viejo → 409 sin sumar; dos contadores alternan | C |
| La foto se toma en la primera cantidad, no al crear la línea pendiente (venta entre ambas: no se descuenta dos veces) | C |
| Venta, transferencia o recepción **dentro** de la ventana de observación → línea ambigua, aprobación negada; reconteo → foto nueva → se aprueba bien | C |
| Movimientos **después** de la última anotación se respetan (casos C y E de ERP-1 siguen verdes) | C |
| Corrección solapada (otro recuento, ajuste manual o `reconcile_target` tras la foto) → bloquea | C |
| Nunca se suma lo contado al stock actual; nunca `initial_stock` donde hay Kardex (reverificado bajo bloqueo) | C |
| Doble aprobación: mismos movimientos, ninguno nuevo; anotar durante la aprobación no deja líneas sin aplicar (`TransactionTestCase` con hilos, PostgreSQL) | C |
| Migración completa: pendiente sin contar ni excluir → no se aprueba; excluida con motivo → se aprueba sin movimiento | C |
| Un recuento de migración abierto por sucursal; otra sucursal no queda bloqueada | C |
| Aprobar exige `inventory.counts.approve`; ajeno = 404; ver sin `costs.view` no devuelve valorización | C / F |
| Producto serializado en modo accesorio → rechazado y remitido; al revés, igual | C / E |
| Escaneo de equipo: cada una de las 8 clases de §4.2; repetir no cuenta dos; inválido no se guarda | E |
| Nunca se recrea una unidad existente; nunca cambia una vendida o apartada al escanear; nunca se inventa un IMEI o una serie | E |
| Sobrante → `receive_units` (`initial_stock` o `correction_positive`); faltante → `count_missing`; hallado después → `count_found` | E |
| Esperados: una unidad entrada durante el conteo y no escaneada queda «por escanear», no «faltante» | E |
| IMEI de una ficha de proveedor sin recibir → remite a la recepción; una oferta nunca aparece en el recuento | E |
| Aprobación con accesorios y equipos de varios productos: un solo orden de bloqueos, todo o nada | E / F |
| Resumen previo = movimientos aplicados (mismos tipos y cantidades) | F |
| Asistente reanudable: recargar en cada paso vuelve al mismo paso desde el servidor | F |
| Impresión inicial: una por recuento; accesorio sin código nuevo si ya tiene; equipo con IMEI 1; batería sólo usado y medido; nada se imprime al importar | G |
| Multiempresa en todo lo anterior: el mismo código o IMEI de otra empresa = 404 | C / E |

## 11. Riesgos

1. **Muchas líneas ambiguas en productos de alta rotación** si se cuentan con la tienda abierta. Mitigación:
   contar cada producto de una vez y cerrar la línea; contar esos productos fuera de horario; la opción E de §3.1.
2. **Cambiar cuándo se toma la foto** toca una regla con historia (ERP-1). Las pruebas A–E se conservan; sólo
   cambia la línea creada en blanco, que hoy la pantalla no crea.
3. **Retención de bloqueos** en la aprobación de una sucursal entera (cientos de filas `BranchStock` y unidades en
   una transacción): la caja espera segundos. Aprobar fuera de la hora punta; medir en el ensayo.
4. **Equipos en otra sucursal** sin traslado canónico de unidades: si el propietario no aprueba `relocate`,
   quedan incidencias abiertas que la otra sucursal verá como faltantes.
5. **`written_off` reutilizado para faltantes**: los reportes de bajas mezclan dañados y faltantes (el tipo de
   movimiento los distingue).
6. **IMEI en pantalla de recuento**: sólo `inventory.view`, nunca en endpoint público ni en la auditoría.
7. **Numeración de migraciones** con #162, LABEL-01/02/03 y C/E en vuelo: fusión estricta de una en una.
8. **Lector como teclado** que manda dos Enter: el modo incremental lo absorbe con `expected`; el total, por ser
   absoluto.
9. **Contrato con MIGRATION-B** (`product_id` en filas creadas, `reference_quantities`): si cambia, C sólo pierde
   la referencia, nunca stock.

## 12. Preguntas al propietario

1. **Pagado en la web y aún no despachado:** ¿se separa físicamente y no se cuenta (propuesto) o se cuenta y el
   sistema lo resta?
2. **Cuatro ojos:** en la migración, ¿quien aprueba tiene que ser distinto de quien contó?
3. **Faltantes de equipos:** ¿basta «Dado de baja» con motivo «Faltante en recuento» o se quiere un estado propio
   «Faltante»?
4. **Equipo registrado en otra sucursal y encontrado aquí:** ¿se traslada al aprobar (un traslado mínimo de una
   unidad, que hoy no existe) o queda como incidencia?
5. **Vendido y entregado, pero en el estante:** ¿sólo incidencia, y la devolución la registra después quien tiene
   permiso?
6. ¿Qué roles reciben `inventory.counts.approve`? (Propuesto: los que hoy tienen `inventory.adjust`.)
7. Sin Kardex previo, ¿el primer recuento entra como «Stock inicial» (propuesto) o como corrección?
8. **Costo de ese stock inicial:** ¿el costo de referencia del producto (de la importación), uno escrito en el
   recuento por quien tiene `costs.view`, o «costo pendiente»?
9. **Copias de la etiqueta de accesorio** en la impresión inicial: ¿lo contado (propuesto), una por producto, o
   ninguna por defecto?
10. En la migración, ¿toda línea con stock o referencia debe contarse o excluirse con motivo antes de aprobar?
11. **Productos de alta rotación:** ¿reconteo obligado (propuesto) o congelar la línea unos minutos en caja?
12. **Equipo usado sin batería medida** hallado en el conteo: ¿se registra «no informado» o se exige medirla?
13. ¿Un solo recuento de migración abierto por sucursal?
