# ADR — Transferir equipos con serie entre sucursales por lectura (SERIAL-TRANSFER-01)

Estado: **Propuesta**. Nada de esto está implementado. Espera las decisiones del propietario de §11 antes de
escribir código o migraciones.

Fecha: 2026-11-17. Base auditada: `master` @ `08e2897` (última migración `0172_storefront_design`).
Origen:
- auditoría del ingreso de inventario del 2026-11-17, hueco bloqueante **B2** y PR propuesto **#5**;
- pendiente `SERIAL-TRANSFER` de `docs/AUDIT_MEMORY.md:680`.

Orden de autoridad: código > migraciones > tests > docs. Las referencias tienen la forma `archivo:línea`; sin ruta,
el archivo está en `backend/store/`.

Convención: **FIJADO** (lo decide un invariante existente), **PROPUESTA** (diseño técnico recomendado),
**DECISIÓN** (regla de negocio del propietario, §11) y **NO VERIFICADO**.

## 1. Problema

- La web sólo vende desde **la sucursal de despacho** (`tenancy.company_fulfillment_branch`; `offered_units` filtra
  por esa sucursal, `device_catalog.py:1218-1233`).
- Un equipo recibido en otra sucursal **nunca** se vende en línea. Además, no hay forma de moverlo:
  `set_transfer_item` rechaza cualquier producto con serie al escribir la línea (`inventory_services.py:1180-1186`).
- Volver a ingresarlo en la otra sucursal tampoco sirve, porque su serie e IMEI ya existen (`docs/AUDIT_MEMORY.md:680`:
  «la serie no entra dos veces»).

Con dos sucursales activas, esto parte el inventario en dos. Lo mismo vale para la caja: un equipo sólo se vende en
la sucursal donde está (`unit_identification.sellable_unit`, `unit_identification.py:158-172`).

## 2. Lo que el código ya hace (hechos)

### 2.1 El documento de transferencia

- `StockTransfer` (`models.py:3046-3172`): `draft → in_transit → received`, o `draft → cancelled`. El stock se mueve en
  los **bordes**, nunca por un cambio de estado:
  - despachar resta del origen (`TRANSFER_OUT`);
  - recibir suma al destino (`TRANSFER_IN`);
  - no hay ninguna escritura que haga las dos cosas (`models.py:3047-3066`).
- `StockTransferItem` (`models.py:3175-3210`): una línea por producto, `unique(transfer, product)`, `quantity > 0`.
- Servicios en `inventory_services.py`:
  - `create_stock_transfer` (`:1135-1158`): mismas empresa, sucursales distintas y activas;
  - `set_transfer_item` (`:1161-1209`): sólo en borrador, con la transferencia bloqueada y releída;
  - `dispatch_transfer` (`:1212-1297`): todo o nada; bloquea las filas de origen en orden; idempotente;
  - `receive_transfer` (`:1300-1380`): recepción **completa** (la parcial está excluida a propósito); costo de llegada
    igual al de salida; idempotente;
  - `cancel_transfer` (`:1383-1430`): **sólo borrador**. Una transferencia en tránsito no se anula: «revertirla exige
    movimientos compensatorios que todavía no están implementados».
- Vistas:
  - panel: `inventory_views.py:1008-1255`, con `inventory.view` para ver e `inventory.adjust` para actuar;
  - API móvil: `v1_transfer_views.py`.
  - **Ver** exige acceso a cualquiera de los dos extremos; **actuar** exige acceso a **los dos**
    (`_assert_both_ends`, `inventory_views.py:992`; `v1_transfer_views.py:27-37`).
- Pantallas: `frontend/app/admin/inventory/transfers/` (lista y `[id]`).

**Hallazgo:** el diagrama del docstring de `v1_transfer_views.py:13-16` dibuja `in_transit → cancel`, pero el servicio
lo rechaza (`inventory_services.py:1399-1406`). Manda el código. El docstring se corrige en el PR de implementación.

### 2.2 Equipos con serie

- I1, **FIJADO:** `BranchStock.quantity == count(StockUnit AVAILABLE)` por (sucursal, producto con serie)
  (`stock_unit_services.py:1-20`; DEC-SERIAL-01).
- I2, **FIJADO:** un producto con serie sólo se mueve con sus equipos. `_units_for_movement` exige exactamente
  `quantity` equipos **de esa sucursal y ese producto**, los bloquea y, si se indica, comprueba
  `expected_unit_status` (`inventory_services.py:425-486`).
- I3, **FIJADO:** orden de bloqueos:
  - documento (si lo hay);
  - filas de `BranchStock` en orden `(branch_id, product_id)` (`_locked_branch_stocks`, `inventory_services.py:145-164`);
  - equipos.
- `StockUnit.Status` tiene `available`, `reserved`, `sold` y `written_off` (`models.py:10210-10214`). **No hay estado
  «en tránsito».** El estado no tiene restricción `CHECK` en la base: sólo `choices`.
- **Precedente de cambio de sucursal:** `order_returns._device_back` (`order_returns.py:493-530`). Cuando un equipo
  vendido vuelve a otra sucursal, primero bloquea el estante del destino, luego el equipo, actualiza `branch`
  («un equipo vendido no está en el conteo de ninguna sucursal») y escribe la entrada. SERIAL-TRANSFER reutiliza ese
  patrón.
- `holds_stock` **ya cuenta** las líneas de transferencias en tránsito (`stock_unit_services.py:344-359`). Por eso un
  producto con equipos en camino no puede cambiar su modo de control (`set_serialized`, `:362-395`).
- Costo (DC1, `cost_services.py:45-51`, `:141-159`):
  - un equipo lleva su costo específico, sin promedio por sucursal;
  - una transferencia llega «al costo con que salió»;
  - un equipo sin costo propio que entra con un costo declarado toma ese costo. Esta regla ya está escrita
    «para cuando exista» la transferencia de equipos (`05_DECISIONES_TECNICAS.md:2752-2755`).
- La venta exige `AVAILABLE` bajo el bloqueo del estante, tanto en caja (`_sell_chosen_units` estricto,
  `inventory_services.py:763-800`) como en la web. El apartado web y el del personal también parten de `AVAILABLE`
  (`unit_hold_services.py:282`, `:399`; `stock_unit_services.reserve`).
- La valorización del inventario suma sólo equipos `AVAILABLE` (`_inventory_at_cost`, `inventory_services.py:2552-2591`).
  Los accesorios en tránsito tampoco se valorizan hoy: ya salieron del estante de origen.

## 3. Modelo de datos (PROPUESTA)

### 3.1 Tabla nueva: `StockTransferUnit`

Qué equipo viaja en qué línea, cómo se identificó y quién lo leyó en cada extremo.

| Campo | Notas |
|---|---|
| `company` FK `PROTECT` | Aislamiento; igual a `transfer.company` |
| `transfer` FK `StockTransfer` `PROTECT` | Denormalizado desde `item` para la restricción única y las consultas |
| `item` FK `StockTransferItem` `CASCADE` | La línea del producto. En borrador, quitar la línea quita sus equipos |
| `unit` FK `StockUnit` `PROTECT` | El equipo |
| `method` | `scanner` o `manual`, el mismo vocabulario que `OrderUnit.Method` (`models.py:10393-10420`) |
| `added_by`, `added_at` | Quién lo agregó en origen |
| `received_method`, `received_by`, `received_at` (nulos) | Lectura en destino, si DECISIÓN ST-D2 la exige |

Restricciones:
- `UniqueConstraint(transfer, unit)`: un equipo aparece una sola vez por documento.
- Índice `(unit, transfer)`: el historial de un equipo.

Un mismo equipo **puede** figurar en dos borradores. El primero que se despacha lo deja `in_transit` y el segundo
despacho lo rechaza (§5). No se puede imponer «un solo documento abierto por equipo» con un índice parcial, porque el
estado vive en otra tabla. Además, el servicio ya lo garantiza en el despacho, bajo bloqueo.

Para un producto con serie, `StockTransferItem.quantity` **es** el número de equipos de la línea. Lo mantiene el
servicio en cada alta o baja de equipo, y el despacho lo vuelve a comprobar. La restricción `quantity > 0` sigue
valiendo: una línea sin equipos se borra, como hoy una cantidad 0.

### 3.2 Estado nuevo del equipo: `in_transit` («En tránsito»)

- Mientras viaja, el equipo **no** está `AVAILABLE` en ninguna sucursal. Así I1 se cumple en el origen (el estante
  bajó) y en el destino (todavía no subió).
- Su `branch` **sigue siendo el origen** hasta la recepción. La columna es NOT NULL, y así ninguna lectura nueva ve un
  equipo «en ninguna parte».
- Alternativas descartadas:
  - usar `reserved`: `release` lo devolvería al estante del origen y los tableros lo contarían como apartado;
  - cambiar `branch` al destino al despachar y dejarlo `available`: el destino vendería lo que todavía viaja en un
    vehículo, lo mismo que `StockTransfer` prohíbe para los accesorios (`models.py:3064-3066`).

### 3.3 Esquema de la migración (`0173_serial_transfer_units`, el número se fija al escribirla)

```python
operations = [
    # Sólo `choices`: no emite SQL en PostgreSQL (verificar con `sqlmigrate`).
    migrations.AlterField('stockunit', 'status', models.CharField(
        max_length=16, db_index=True, default='available',
        choices=[('available', 'Disponible'), ('reserved', 'Apartado'), ('sold', 'Vendido'),
                 ('written_off', 'Dado de baja'), ('in_transit', 'En tránsito')])),
    # Tabla nueva y vacía: no bloquea tablas existentes más allá de las llaves foráneas.
    migrations.CreateModel('StockTransferUnit', fields=[...], options={
        'constraints': [models.UniqueConstraint(fields=['transfer', 'unit'], name='unique_unit_per_transfer')],
        'indexes': [models.Index(fields=['unit', 'transfer'], name='transfer_unit_history_idx')],
    }),
]
```

Si INV-UNIT-CORRECT-01 (`docs/adr-unit-correction.md`) se implementa antes, las `choices` incluyen también `voided`.
Las dos migraciones tocan el mismo campo, así que se escriben en serie, nunca en paralelo.

## 4. Ciclo de vida

| Estado del documento | Acción | Equipo: `status` | Equipo: `branch` | `BranchStock` origen | `BranchStock` destino | Kardex |
|---|---|---|---|---|---|---|
| `draft` | agregar o quitar equipo por lectura | `available` (sin cambio; ver ST-D1) | origen | sin cambio | sin cambio | nada |
| `draft → in_transit` | despachar | `available → in_transit` | origen | −n por producto | sin cambio | `TRANSFER_OUT` por producto, con `unit_ids` |
| `in_transit → received` | recibir | `in_transit → available` | origen → **destino** | sin cambio | +n por producto | `TRANSFER_IN` por producto, con `unit_ids` |
| `draft → cancelled` | anular | sin cambio | origen | sin cambio | sin cambio | nada |
| `in_transit → ?` | anular el despacho | **FIJADO: rechazado hoy** (§2.1). DECISIÓN ST-D4 |

### 4.1 Agregar un equipo (borrador)

1. Bloquear la transferencia y releerla. Tiene que seguir en borrador (mismo patrón que `set_transfer_item`).
2. Resolver el código con `unit_identification.find_unit` en la empresa de la transferencia. A mano, un equipo con
   IMEI se identifica por su IMEI (DEC-SERIAL-02; `_typed_by_hand_is_an_imei`, `unit_identification.py:145-155`).
3. Comprobar, sin bloquear el estante (nada se mueve):
   - el equipo está en la sucursal de **origen**. Si no, «Ese equipo no está en esta sucursal», sin decir dónde está
     (DEC-SERIAL-02);
   - su estado es `available`. Si no, se dice el estado;
   - su producto es con serie y de la empresa.
4. Crear o actualizar la línea del producto y crear `StockTransferUnit`. Una segunda lectura del mismo equipo responde
   «ya está en esta transferencia» sin duplicar (restricción única).

### 4.2 Despachar

Extiende `dispatch_transfer`, en la misma transacción:
1. Bloquear la transferencia; si ya está `in_transit`, devolver sus movimientos (idempotente, como hoy).
2. Bloquear las filas de `BranchStock` del origen de **todos** los productos (accesorios y equipos), en el orden del
   módulo.
3. Para cada línea con serie: `quantity == count(StockTransferUnit)`. Bloquear los equipos, ordenados por `pk`.
4. `create_stock_movement(TRANSFER_OUT, units=[…], expected_unit_status='available')`. Si un equipo se vendió o se
   apartó desde que entró al borrador, el escritor lo rechaza y **todo** el despacho se deshace. El error nombra la
   línea y el equipo, igual que una cantidad insuficiente.
5. `StockUnit.objects.filter(pk__in=…).update(status='in_transit')`.
6. Auditoría `stock_transfer_dispatched`, como hoy, con `units` = total de equipos y cantidades.

### 4.3 Recibir

Extiende `receive_transfer`, en la misma transacción:
1. Bloquear la transferencia; si ya está `received`, devolver sus movimientos (idempotente).
2. Bloquear las filas de `BranchStock` del **destino**, en orden. Después, los equipos de la transferencia, por `pk`.
3. Comprobar que cada equipo sigue `in_transit` y que su `branch` es el origen. Si no, `TransferError`: alguien lo
   tocó por fuera del documento, y eso es un error de integridad que no se repara en silencio.
4. Si ST-D2 exige lectura en destino: todos los equipos tienen `received_at`. Si no, se rechaza con la lista de los que
   faltan (ver ST-D3).
5. `update(branch=destino)` y después `create_stock_movement(TRANSFER_IN, branch=destino, units=[…],
   expected_unit_status='in_transit', unit_cost=…, cost_source=…)`. El costo es el de salida, con la regla de §2.2.
6. `update(status='available')`.
7. Auditoría `stock_transfer_received`.

Desde ese momento:
- la caja de la sucursal destino puede vender el equipo;
- si el destino es la sucursal de despacho, `offered_units` lo ofrece en la web sin ningún otro paso (con precio
  válido, si es seminuevo).

## 5. Invariantes de stock en las dos sucursales (FIJADO salvo lo marcado)

| Momento | Origen | Destino | Empresa |
|---|---|---|---|
| Borrador | `quantity == AVAILABLE` (el equipo sigue disponible) | sin cambio | — |
| En tránsito | `quantity` bajó n; el equipo es `in_transit` y no cuenta: I1 se cumple | sin cambio: I1 se cumple | equipos vivos = `AVAILABLE + RESERVED + IN_TRANSIT`; `holds_stock` es verdadero por la línea en tránsito |
| Recibida | sin cambio | `quantity` subió n; el equipo es `available` en el destino: I1 se cumple | sin cambio |

- `holds_stock` (`stock_unit_services.py:344-359`) ya mira `StockTransferItem` en tránsito. **PROPUESTA:** sumar
  `in_transit` a su filtro de estados como defensa en profundidad.
- El Kardex de cada sucursal se reconstruye con sus propias líneas (`stock_before` y `stock_after` por sucursal,
  `models.py:1662-1670`, `:1778-1779`).
- Una comprobación de integridad para `ops_status` u otra orden de solo lectura: ningún equipo `in_transit` sin una
  transferencia `in_transit` que lo nombre, y al revés. **PROPUESTA.**

## 6. Carreras con ventas y apartados

Todas se resuelven con los bloqueos que ya existen. No se agrega ningún bloqueo nuevo.

| Carrera | Qué pasa |
|---|---|
| Venta en caja del equipo mientras está en un **borrador** | La venta gana (el borrador no aparta: ST-D1). El despacho posterior falla en el paso 4.2.4 y nombra el equipo. Hay que quitarlo del borrador |
| Venta en caja **durante** el despacho | Las dos toman primero la fila de `BranchStock` del origen. La que llega segunda relee el equipo: si el despacho ganó, la caja ve `in_transit` y responde «Ese equipo está en tránsito: no se puede vender». Si ganó la caja, el despacho ve `sold` y se deshace |
| Apartado web o del personal durante el despacho | Igual: el apartado pasa por `_move`, con el mismo bloqueo de estante y `expected=AVAILABLE` |
| Pago web de un producto con serie sin elección de equipo (`assign_units_for_sale`) | Toma los `AVAILABLE` más antiguos de la sucursal bajo el bloqueo; un equipo `in_transit` no es candidato |
| Equipo en tránsito: baja, apartado, anulación de ingreso | Los tres exigen `AVAILABLE`: se rechazan. Un equipo transferido tampoco cumple el «historial limpio» de INV-UNIT-CORRECT-01 |
| Editar datos de un equipo en tránsito (`update_unit`) | Hoy no mira el estado salvo en vendido y dado de baja. DECISIÓN ST-D9 |
| Dos despachos de borradores que comparten un equipo | Los dos bloquean el estante del origen. El segundo ve `in_transit` y se deshace |
| Recibir mientras alguien despacha otra transferencia hacia el origen | Estantes distintos o el mismo estante en orden: hacen cola, sin interbloqueo (I3) |

## 7. Movimientos de Kardex

- **Origen, al despachar:** un `TRANSFER_OUT` por producto, cantidad = equipos, `transfer=…`,
  `reference_type='transfer'`, `metadata = {transfer_id, destination_branch_id, unit_ids}`. Costo: media de los costos
  específicos, fuente `unit` (o `reference`/`unknown` según la regla de salida).
- **Destino, al recibir:** un `TRANSFER_IN` por producto, `metadata = {transfer_id, source_branch_id, unit_ids}`.
  Costo: el de los equipos; los que no tienen costo toman el de su `TRANSFER_OUT` (`cost_services.py:150-155`).
- No hay tipos nuevos: `TRANSFER_OUT` y `TRANSFER_IN` ya existen y no son movimientos manuales (`MANUAL_TYPES`,
  `models.py:1748-1751`).
- Ninguna línea en el borrador ni al anularlo.

## 8. Lectura por escáner (PROPUESTA de interfaz)

En `frontend/app/admin/inventory/transfers/[id]`:
- En borrador, una línea de un producto con serie no tiene campo de cantidad. Tiene «Escanear equipos»: un campo con
  foco que acepta ráfagas del lector (Enter por código) y el botón «a mano», con la misma regla de ritmo y de IMEI
  que la caja (DEC-SERIAL-02).
- Cada lectura responde al instante con el equipo encontrado (producto, serie e IMEI completos: es la pantalla del
  personal de inventario) o con el motivo del rechazo. La lista muestra cada equipo con «Quitar».
- «Pegar una lista» de IMEI o series, con el mismo lector de la recepción (INV-INTAKE-BULK-01, si ya existe). Resuelve
  cada código por separado y lista los rechazados, sin cortar en el primero.
- En destino, si ST-D2 lo exige: «Verificar llegada» con el mismo campo de lectura, contador «n de m», los que faltan y
  «Recibir», que sólo se activa con todo verificado.
- La API móvil v1: DECISIÓN ST-D10.

Endpoints propuestos:
- `POST /api/admin/inventory/transfers/{pk}/units/` `{code, method}`;
- `DELETE /api/admin/inventory/transfers/{pk}/units/{unit_id}/`;
- `POST /api/admin/inventory/transfers/{pk}/units/verify/` `{code, method}`.

El `PUT …/items/` sigue rechazando productos con serie: sus líneas nacen de los equipos.

## 9. Permisos y auditoría

- **FIJADO:**
  - actuar exige `inventory.adjust` y acceso a **los dos** extremos (`_assert_both_ends`);
  - ver exige `inventory.view` y acceso a cualquiera de los dos;
  - la empresa sale de la sesión;
  - un equipo de otra empresa o de una sucursal no visible responde lo mismo que uno inexistente.
- **FIJADO:** el costo de las líneas y de los equipos viaja sólo con `costs.view`.
- **Auditoría (PROPUESTA):**
  - `stock_transfer_unit_added`, `stock_transfer_unit_removed` y `stock_transfer_unit_verified`, con `transfer_id`,
    `unit_id`, `product_id` y método;
  - las acciones de despacho y recepción de siempre, con el total de equipos;
  - **nunca** IMEI ni serie en la auditoría (regla de `stock_unit_services.py:518-523`).
- DECISIÓN ST-D6: si se mantiene «los dos extremos» también para recibir equipos, y si hace falta una capacidad propia.

## 10. Despliegue y vuelta atrás

**Migración:** aditiva. Tabla nueva vacía y `choices`. La versión anterior sigue funcionando mientras corre:
- no escribe `in_transit`;
- no conoce la tabla nueva;
- `set_transfer_item` sigue rechazando productos con serie.

**Vuelta atrás de código** (la preferida, como en las releases anteriores):
- Antes de volver a la imagen anterior, **ninguna transferencia con equipos puede estar en tránsito**. La versión
  anterior no sabría recibirla: su `receive_transfer` llamaría al escritor sin equipos para un producto con serie, y
  el escritor lo rechazaría. Los equipos quedarían en `in_transit` para siempre.
- Comprobación de solo lectura antes de volver atrás:

  ```sql
  SELECT count(*) FROM store_stockunit WHERE status = 'in_transit';
  SELECT DISTINCT t.id FROM store_stocktransfer t JOIN store_stocktransferunit u ON u.transfer_id = t.id
   WHERE t.status = 'in_transit';
  ```

  Las dos deben dar cero. Si no, se reciben primero, con la versión nueva.
- Los borradores con equipos no molestan a la versión anterior. Su despacho fallaría con el mensaje de siempre
  («se controla por número de serie»), porque sus líneas nombran un producto con serie.

**Vuelta atrás de esquema:**
- Sólo si la tabla está vacía. La reversa de la migración incluye un `RunPython` que **se detiene** si hay filas en
  `StockTransferUnit` o equipos `in_transit` (precedente: `0166_catalog_model_normalized_name.py`).
- Borrar el historial de qué equipo viajó en qué documento no se hace nunca en silencio.

**Ensayo:** el script de ensayo de siempre, con `lock_timeout`. Medir la migración sobre una copia con datos y probar
la vuelta atrás de código con una transferencia en tránsito (debe bloquearse) y sin ella.

## 11. Decisiones del propietario

| # | Decisión | Propuesta de partida |
|---|---|---|
| ST-D1 | **¿El borrador aparta los equipos?** Si los aparta, dejan de venderse al agregarse. Si no, siguen a la venta hasta el despacho, que los revalida | No los aparta |
| ST-D2 | **Recepción:** un clic, o lectura obligatoria de cada equipo en destino | Lectura obligatoria |
| ST-D3 | **Falta un equipo al llegar, o llega otro:** bloquear la recepción hasta resolverlo, o recepción parcial con discrepancia (hoy V1 sólo recibe completo, y la parcial es una fase propia) | Bloquear y avisar; parcial en otra fase |
| ST-D4 | **Anular un despacho en tránsito:** devolverlo al origen con movimientos compensatorios, o sólo con una transferencia de vuelta después de recibir (hoy rechazado) | Transferencia de vuelta |
| ST-D5 | **Equipo perdido o dañado en el camino:** baja directa desde «en tránsito», o recibir y después dar de baja en destino | Recibir y dar de baja (con ST-D3) |
| ST-D6 | **Permisos:** mantener «los dos extremos» para despachar y recibir, o permitir que el destino reciba sin acceso al origen; ¿una capacidad propia para transferir equipos? | Mantener lo de hoy |
| ST-D7 | **Valor en tránsito:** mostrar en reportes el costo de lo que viaja (hoy ni los accesorios en tránsito se valorizan) | Mostrar «en tránsito» aparte, sin sumarlo al estante |
| ST-D8 | **Tope de equipos por transferencia** (precedente: 200 por recepción, `stock_unit_services.py:37`) | 200 |
| ST-D9 | **Editar un equipo en tránsito** (precio de seminuevo, notas, costo) | Permitido salvo identificadores y estado, como hoy |
| ST-D10 | **API móvil v1** en la primera fase o después | Después; la v1 sigue rechazando productos con serie |
| ST-D11 | **Relación con ECOM D-B** (`docs/adr-ecom-branch-stock.md` §5): este ADR **no** autoriza que la web cree transferencias ni venda desde otra sucursal. Confirmar que sigue siendo así | Confirmar |

## 12. Plan de pruebas (RED → GREEN cuando se implemente)

Backend (`test_serial_transfer.py`, PostgreSQL, datos sintéticos):
- **Ciclo completo:**
  - borrador → agregar 3 equipos por IMEI, serie y `label_code` → despachar → recibir;
  - después de cada paso: I1 en origen y destino, `Product.inventory`, `status` y `branch` de cada equipo, y los
    movimientos (tipo, cantidad, `stock_before` y `stock_after`, `unit_ids`, costo y fuente).
- **Costo:**
  - equipos con costo, sin costo y mixtos;
  - llegada al costo de salida;
  - ningún promedio por sucursal cambia.
- **Rechazos al agregar:**
  - equipo de otra sucursal, sin revelar cuál;
  - de otra empresa (igual que inexistente);
  - `reserved`, `sold` o `written_off`;
  - ya en la lista;
  - transferencia ya despachada;
  - a mano por serie cuando el equipo tiene IMEI.
- **Despacho:**
  - todo o nada cuando un equipo se vendió o se apartó después de agregarlo;
  - `quantity` distinto del número de equipos;
  - idempotente al repetir.
- **Recepción:**
  - idempotente;
  - rechazo si un equipo ya no está `in_transit`;
  - con ST-D2, rechazo mientras falte verificar alguno.
- **Carreras** (`TransactionTestCase`, dos hilos): venta en caja del mismo equipo frente al despacho; apartado web
  frente al despacho; dos despachos que comparten un equipo. Siempre gana uno, sin descuadre ni interbloqueo.
- **Exclusión de la venta:** un equipo `in_transit` no aparece en `offered_units` ni en `sellable_unit`, no lo toma
  `assign_units_for_sale`, y no se aparta, no se da de baja ni se anula.
- **Web:** después de recibir en la sucursal de despacho, la variante pasa a «disponible» y el equipo se ofrece; antes
  no.
- `holds_stock` y `set_serialized` con equipos en tránsito.
- **Permisos:**
  - sin `inventory.adjust`, 403;
  - con un solo extremo, rechazo al actuar y lectura permitida;
  - sin `costs.view`, sin costos en la respuesta.
- Auditoría sin IMEI ni serie.
- **Migración:**
  - hacia adelante con datos;
  - reversa vacía;
  - reversa con filas o equipos `in_transit` que se detiene;
  - la versión anterior frente al esquema nuevo (despacho de un borrador con equipos rechazado con el mensaje de
    siempre).
- **Regresión:** las transferencias de accesorios no cambian (`test_transfer_line_lock.py` y las de `tests.py`).

Frontend (Vitest + E2E con datos sintéticos):
- ráfaga del lector y «a mano»;
- rechazos con su texto;
- quitar un equipo;
- verificación en destino y «Recibir» desactivado hasta completar;
- una transferencia mixta con accesorios y equipos.

## 13. Qué NO se verificó

- Las pantallas `frontend/app/admin/inventory/transfers/` en detalle (sólo se ubicaron) y la app móvil.
- Si alguna exportación o tablero enumera los estados del equipo a mano y dejaría fuera `in_transit`. Hay conteos por
  estado en `availability_views.py:165-176` y en `device_catalog.py:535`, `:546`.
- Datos de producción: transferencias abiertas, número de sucursales activas y productos con serie.
- Si `sqlmigrate` de la `AlterField` de `choices` sale vacío en la versión de Django del proyecto.
- El comportamiento del lector de cajas Apple con una «S» delante de la serie (auditoría, fricción 10). Afecta también
  a esta lectura.
