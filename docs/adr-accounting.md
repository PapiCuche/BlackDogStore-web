# ADR — ACCOUNTING-01 · Contabilidad → Centro Contable (sólo lectura)

Estado: **PROPUESTA**. Nada de lo descrito aquí está implementado. Este documento audita las fuentes y diseña las
fases ACCOUNTING-02 en adelante.

Orden de autoridad: código > migraciones > tests > docs. Las referencias tienen la forma `archivo::símbolo` sobre
`master` en `ac3fbd7`. Sin rutas: `backend/store/`.

## 0. Qué es y qué no es

- Es un **centro de consulta**: reúne en tres vistas lo que el sistema ya registra (ventas, cobros, documentos
  internos y fiscales, anulaciones, devoluciones y reembolsos) y lo exporta con su evidencia.
- **No es contabilidad completa.** No hay compras, gastos, bancos, caja ni asientos (§3). La interfaz lo dice en
  cada pantalla y en cada exportación: «Centro contable de ventas e ingresos. No incluye compras, gastos, bancos
  ni asientos contables.»
- **No crea un libro mayor.** Ninguna fase de este ADR crea un `JournalEntry`, un `Ledger` ni una tabla de
  movimientos contables. Las vistas son una capa de consulta sobre los modelos existentes.
- **No emite ni declara nada.** Lee los documentos fiscales como fuente de datos. Su emisión es de
  FISCAL-PROD-01, una fase paralela, y este ADR no la diseña. Ninguna pantalla dice «declarado» por tener XML o
  CDR. Un cierre interno no es una declaración presentada.
- **Una nota de venta interna no tiene validez tributaria.** Aun así, la venta no queda exenta de sus
  obligaciones: la vista C la lista como operación sin comprobante fiscal.

**Hecho que condiciona todo:** producción (R1) corre con `FISCAL_ENABLED=0` (`02_ESTADO_ACTUAL.md`, R1). La
caja no ofrece boleta ni factura. Se espera que **ninguna venta de producción tenga un comprobante aceptado**:
serán notas internas o ventas sin documento. El centro contable no lo supone: lo lee de la base y lo muestra tal
cual. Se espera una vista C grande, y es la respuesta correcta.

**Hallazgo para FISCAL-PROD-01** (aquí no se corrige):
- `POST /api/admin/orders/<id>/fiscal-document/` (`fiscal_views::AdminOrderFiscalDocumentView.post`) **no
  consulta `fiscal_config.fiscal_enabled()`**; sólo la caja lo hace (`pos_services`).
- Hoy lo frena la falta de una serie o de un certificado, no el interruptor.
- Por eso el centro contable no da por hecho que «apagado» signifique «sin documentos». Muestra los que haya,
  con su ambiente.

## 1. Auditoría de fuentes

| Fuente | ¿Existe? | Modelo | Clasificación |
|---|---|---|---|
| Venta (web y caja) | Sí | `models::Order`, `models::OrderItem` | IMPLEMENTADO |
| Intentos de pago Izipay | Sí | `models::PaymentTransaction` | IMPLEMENTADO |
| Caja (POS) | Sí: es un `Order` con `sales_channel='pos'` | `pos_services::create_pos_sale` | IMPLEMENTADO |
| Comprobante (factura y boleta) | Sí | `models::FiscalDocument` (01/03) | IMPLEMENTADO, apagado en producción |
| Resumen diario (RC) | Sí | `FiscalDailySummary`, `FiscalDailySummaryDocument` | IMPLEMENTADO (sólo `ConditionCode=1`) |
| NC/ND de factura | Sí | `FiscalDocument` (07/08), `original_document` | IMPLEMENTADO |
| NC/ND de boleta | **No** | `fiscal_note_services::_original_is_eligible` falla cerrado | PENDIENTE (fiscal) |
| Baja (RA) | Sólo facturas y sus notas | `FiscalVoidCommunication(+Document)` | IMPLEMENTADO; baja de boleta PENDIENTE |
| Nota de venta interna (NV-) | Sí | `models::SalesNote` (1:1 con `Order`) | IMPLEMENTADO |
| Cancelación | Sí | `models::OrderCancellation` (1:1) | IMPLEMENTADO |
| Reembolso | Sí, como registro | `models::OrderRefund` | IMPLEMENTADO (no llama a la pasarela) |
| Devolución post-entrega | Sí, de **todo** el pedido | `models::OrderReturn`, `OrderReturnLine` | IMPLEMENTADO fase 1; parcial PENDIENTE |
| Cobro de servicio técnico | Sí | `models::RepairPayment` (+ `RepairQuote`) | IMPLEMENTADO, sin comprobante |
| Compras / cuentas por pagar | **No** | sólo `StockMovement.PURCHASE_ENTRY` y `supply_services::pending_settlement` | BLOCKED |
| Gastos, bancos, caja, asientos | **No** | — | BLOCKED |
| Movimientos relacionados | Sí | `SalesCommission`, `AppliedPromotion`, `StockMovement` | IMPLEMENTADO (no son ingreso) |

### 1.1 `Order` — la raíz de toda venta

- **Empresa:** `company` (PROTECT).
- **Sucursal:** `fulfillment_branch`. Es nula en el historial anterior a la fase 2D. En la caja es la sucursal de
  la venta (`pos_services`, `fulfillment_branch=branch`). `tenancy::visible_orders` acota por este campo.
- **Canal:** `sales_channel` (`online` / `pos`).
- **Usuario:** `sold_by` y `seller_name_snapshot` sólo en la caja.
- **Estado:** `status` (`pending_payment`, `paid`, `failed`, `cancelled`, `expired`, `refunded`), con `paid`
  (bool) y `paid_at`.
- **Importes**, congelados por C2.1:
  - `total` es lo cobrado, **con el impuesto incluido**;
  - `subtotal_amount` es el bruto de las líneas antes del descuento;
  - `discount_amount`, `discount_source` y `discount_reason` explican el descuento;
  - `taxable_amount` y `tax_amount` son la base y el tributo;
  - `tax_rate` es la tasa total (18 %; Ley 32387 reparte IGV e IPM, pero la venta guarda una sola tasa);
  - `tax_treatment` y `currency` completan el desglose.
- **Precio con impuesto:** los precios del catálogo ya lo incluyen. `tax_services` lo descompone hacia atrás:
  `base = total / (1 + tasa)` y `impuesto = total − base`. `tax_services::breakdown_for_order` lee el snapshot.
  Si la venta es anterior a C2.1 y no lo tiene, lo reconstruye con la tasa de la migración. Esa fila se marca
  **«desglose reconstruido»** y nunca se presenta como congelada.
- **Documento pedido:** `receipt_type` (`boleta` / `factura` / vacío). Una venta de caja con nota interna o sin
  documento queda con `''` (`pos_services`, `receipt_type if … in Order.ReceiptType.values else ''`).
- **Líneas:** `OrderItem` lleva `price`, que incluye el impuesto, y `net_amount`, `unit_price_net` y
  `discount_net`. También lleva el costo (`unit_cost`, `received_unit_cost`), que sólo se muestra con
  `costs.view`.

### 1.2 `PaymentTransaction` — intentos, no ventas

- **Campos:** `order` (FK, sin `company` propia), `provider`, `transaction_id` (único por proveedor),
  `amount`, `currency` y `status`.
- **Estados:** `pending`, `authorized`, `rejected`, `integrity_failed`, `abandoned` y `duplicate`.
- **Más campos:** `environment` (`test` / producción), `confirmed_at`.
- **Importe:** **nunca suma ingreso.** Lo cobrado es `Order.total` cuando el pedido está pagado. Sólo
  `duplicate` sobre un pedido cancelado es dinero adicional. Es el «cobro tardío» de
  `sales_analytics_views::_collections` y `order_refunds::collected`, siempre sin `environment='test'`.
- **Brecha:** un pedido **pagado con claves TEST** cuenta hoy como venta en `_collections.live`. El centro
  contable lo marcará en la vista C («pagado en ambiente de pruebas»). Ver la pregunta P4.
- **La caja no tiene `PaymentTransaction`.** El medio es `Order.payment_method` (`cash`, `card`, `transfer`,
  `other`), con `payment_reference`, `amount_received` y `change_amount`.

### 1.3 `FiscalDocument` — factura, boleta, NC y ND

- **Enlace:** `order` es una FK, no 1:1. Una venta puede tener su original y sus notas. La NC/ND hereda el pedido
  del original (`fiscal_note_services`, `order=original.order`).
- **Unicidad:** `fiscal_document_one_live_per_order` permite un solo original 01/03 no rechazado por venta.
- **Sucursal:** no tiene FK propia. Se usa `order.fulfillment_branch`, la misma frontera que `visible_orders`.
  `series_ref.branch` es sólo informativa.
- **Importes:** `taxable_amount`, `tax_amount`, `total` y `tax_rate` se copian del snapshot de la venta, sin
  recalcular. En una nota el importe es positivo: el signo sale de `document_type`.
- **Fechas:**
  - `issued_at` es la fecha legal y deriva de `paid_at`;
  - `cdr_accepted_at`;
  - `granted_at` / `not_granted_at` registran el otorgamiento.
- **Ambiente:** `environment` (`beta` / `production`). **Un documento BETA no tiene validez tributaria** y nunca
  se suma a una vista fiscal por defecto.
- **Estados:** `pending`, `generated`, `signed`, `submitted`, `accepted`, `accepted_observed`, `rejected` y
  `submission_error`.
- **Boletas:** se quedan en `signed`. Su reporte vive en el resumen (`ADR-26`, `adr-fiscal-c22a1.md`).
- **Evidencia** en columnas de texto:
  - `signed_xml` con `signed_xml_sha256`, sobre los bytes exactos (`decode('utf-8')` estricto);
  - `cdr_xml` con `cdr_sha256`, que se guarda con `decode('utf-8','replace')`. Por eso **el texto guardado puede
    no reproducir los bytes del hash** (§7.3);
  - el ZIP de respuesta de SUNAT que envolvía el CDR **no se conserva**.
- **Autenticidad del CDR:** está `unverified` (`ADR-23`). Nunca se presenta como «firmado por SUNAT verificado».
- **Emisión:** no es automática al pagar. Se hace desde la caja, en la misma transacción, o desde
  `POST /api/admin/orders/<id>/fiscal-document/` (`fiscal_views::AdminOrderFiscalDocumentView`).

### 1.4 Resumen diario y baja

- **`FiscalDailySummary`:**
  - un resumen por empresa, ambiente y fecha;
  - sus estados son `generated`, `signed`, `submitted` (ticket), `accepted`, `accepted_observed`, `rejected`,
    `submission_error` y `submission_unknown`;
  - lleva `signed_xml` y `cdr_xml` con sus hashes.
- **`FiscalDailySummaryDocument`:** guarda la pertenencia de cada boleta. Hay una sola fila viva por boleta
  (`superseded=False`). Un rechazo del resumen marca sus filas `superseded`, y la boleta vuelve a estar
  «pendiente de resumen».
- **`FiscalVoidCommunication(+Document)`:** la baja (RA), sólo para facturas y sus notas. Para la boleta haría
  falta `ConditionCode 3` en el resumen, y no existe. Hay una sola fila viva por documento.

### 1.5 `SalesNote` — nota interna

- **Campos:** es 1:1 con `Order`, sin `company` propia; se llega por `order.company` o `sequence.company`. Lleva
  `number` (NV-…), `status` (`issued` / `void`), `issued_at`, `voided_at` y `void_reason_type`.
- **Importes:** **no tiene.** Los toma de `Order`.
- **Cuándo se crea:** sólo sobre un pedido pagado (`sales_note_services::get_or_create_sales_note`). En la caja
  se crea con `receipt_type='sales_note'`. En el panel, sobre **cualquier** pedido pagado
  (`inventory_views`, `POST`), también uno que ya tiene boleta o factura. **Una venta puede tener a la vez una NV
  y un comprobante.**
- **Anulación:** al cancelar el pedido, la NV se anula (`OrderCancellation.sales_note_voided`).

### 1.6 Cancelación, reembolso y devolución

- **`OrderCancellation`** (1:1):
  - `was_paid`, `previous_status`, `stock_restored`, `sales_note_voided` y `goods_returned`;
  - `fiscal_pending` con `fiscal_document`: un comprobante vivo se **conserva** y queda pendiente de una NC o una
    baja;
  - **cancelar no es reembolsar:** el pedido sigue `paid=True`.
- **`OrderRefund`:**
  - `amount > 0`, `method`, `status` (`pending` / `confirmed`), `reference`, `evidence`, `created_at` y
    `confirmed_at`;
  - un reembolso confirmado exige su fecha y su referencia (CHECK);
  - **es un registro**: la pasarela no se llama;
  - su techo es `order_refunds::collected`.
- **`OrderReturn`:**
  - en la fase 1 se devuelve el pedido **entero** (D1);
  - al cerrarse guarda `paid_amount`, `deduction_amount` (≥ 0 y ≤ pagado) y
    `refund_amount = paid − deduction` (CHECK);
  - el cierre cancela el pedido (`cancel_for_return`) y crea **un** `OrderRefund` pendiente;
  - lo descontado se lo queda la tienda (`order_refunds::kept`).
- **Hoy no existe la devolución parcial de líneas.** Un reembolso parcial sí existe: es una devolución entera
  con deducción, o un reembolso menor que lo cobrado.

### 1.7 Cobro de servicio técnico

- **`RepairPayment`:**
  - `company`, `repair_order`, `amount > 0`, `currency`, `method` (nunca `online`), `received_at` y
    `received_by`;
  - es de sólo agregar: se reversa con `reversed_at`, nunca se borra;
  - **no tiene desglose de impuesto ni comprobante**, ni NV ni boleta;
  - la sucursal es `repair_order.branch`.
- **`RepairQuote`:** `subtotal`, `discount_amount`, `tax_amount`, `total` y `currency`. La cotización aprobada
  es la referencia de lo que se debe.
- **DEC-012:** el servicio técnico es un dominio propio y no se representa como `Order`.

### 1.8 Movimientos relacionados (no son ingreso)

- `SalesCommission` es lo que se debe a un vendedor.
- `AppliedPromotion` explica un descuento.
- `StockMovement` registra el Kardex (`sale_exit`, `customer_return`, `order_cancel_return`, `purchase_entry`).
- Se muestran como contexto, enlazados por `order`, y **nunca suman ingreso ni gasto**.

### 1.9 Lo que ya hay y se reutiliza

- **Período:** `sales_analytics_views::_company_tz`, `_day_bounds` y `_window` cortan el día en la zona de la
  empresa con rangos UTC indexables. Se importan; no se copian ni se mueven.
- **Dinero:** `sales_analytics_views::_collections` define `live`, `cancelled_paid`, `late_charges`, `gross`,
  `refunds_confirmed`, `refunds_pending` y `net`. El centro contable usa **las mismas reglas**, y una prueba lo
  ata a ellas (§11, T-14).
- **Alcance:** `tenancy::visible_orders` y `visible_branches`.
- **Exportación:** `inventory_exports`, con `MAX_EXPORT_ROWS=20000`, `MAX_ZIP_BYTES` de 25 MB, un throttle propio
  y `AdminAuditLog` sin celdas.
- **Evidencia fiscal:** `fiscal_views::AdminFiscalDocumentXmlView` / `CdrView` / `PdfView` (nombres
  `RUC-TT-SERIE-N.XML` y `R-…`) y `fiscal_pdf_services`, que dibuja desde el XML firmado.

## 2. Clave de deduplicación

**Una operación económica = una raíz.** Hay dos familias:

```
operation_key = "sale:<order.id>"      para toda venta (web o caja)
              | "repair:<repair_order.id>"   para todo servicio técnico cobrado
```

Reglas:

1. **El importe de una venta sale de una sola fuente: `Order`.** Bruto = `Order.total` (y su desglose congelado).
   Ni el `FiscalDocument`, ni la `SalesNote`, ni la `PaymentTransaction` aportan importe de ingreso. Sólo aportan
   **estado y evidencia** a la misma fila.
2. **Los ajustes son eventos de la misma clave, nunca operaciones nuevas.**
   - **Dinero:** un `OrderRefund` confirmado resta en su `confirmed_at`. Un pendiente se informa y no resta. Un
     cobro tardío `duplicate` (sin TEST) suma en su `confirmed_at`.
   - **Fiscal:** una NC o ND se lista en la vista B con su importe y su signo. **No resta del ingreso de la vista
     A**: lo que sale del ingreso es el dinero devuelto. La conciliación (§10) compara las dos cosas.
3. **El período de atribución es el mismo que en `_collections`.**
   - La venta cuenta por `paid_at`.
   - El reembolso, por `confirmed_at`.
   - El cobro tardío, por `confirmed_at`.
   - El cobro de servicio, por `received_at`.
   - Las vistas fiscales usan `issued_at` (fecha local de emisión).
   - Siempre en la zona horaria de la empresa.
4. **Qué entra en la vista A:** los pedidos con `paid=True`, sean `paid`, `cancelled` o `refunded`. También
   `pending_payment` con un cobro tardío. Nunca `failed`, `expired` ni `pending_payment` sin dinero: no son
   operaciones económicas. Se pueden consultar como «intentos» en un filtro aparte.
5. **Cobro de servicio:** la operación es la orden de reparación. Su ingreso del período es la suma de los
   `RepairPayment` **no reversados** con `received_at` en el período (criterio de caja; PROPUESTA, ver P1). La
   cotización aprobada se muestra como referencia. Varios pagos (adelanto y saldo) son eventos de la misma clave.
6. Una prueba de propiedad (T-01) comprueba que `Σ bruto(vista A) = _collections.gross` y
   `Σ neto = _collections.net` para la misma empresa, sucursales y período.

### Casos límite, probados contra la clave

| Caso | Qué existe en la base | Resultado con la clave |
|---|---|---|
| Devolución parcial | Hoy no hay devolución parcial de líneas. Sí hay devolución entera con deducción: `OrderReturn` cerrado, `OrderCancellation`, `OrderRefund(refund_amount)` y `status=cancelled`. En una factura, una NC con importe explícito. | **Una** fila `sale:<id>` con estado «devuelta». Bruto = `total` el día del pago; reembolso confirmado = `refund_amount` el día que se confirma; retenido = deducción. No aparece otra vez como «cancelada» ni como venta nueva. La NC parcial va a la vista B. Si la NC no coincide con el reembolso, la vista C avisa. |
| NC tras una boleta informada en un resumen | Hoy es **imposible**: NC-BOL falla cerrado. Cuando FISCAL la habilite, será un `FiscalDocument` 07 con `order` = el de la boleta. | Misma clave. Vista B: la boleta sigue «informada por resumen aceptado», y la NC es un documento aparte con su propio recorrido (su resumen). Vista A: el ingreso sólo cambia por el `OrderRefund`. Nunca se resta dos veces (NC y reembolso). |
| Pedido pagado y cancelado | `status=cancelled`, `paid=True`, `OrderCancellation(was_paid, fiscal_pending)`, NV `void`, comprobante vivo conservado | Una fila, estado «anulada comercialmente». Bruto en `paid_at`, como `_collections.cancelled_paid`. El reembolso, en su fecha. **Vista C:** «comprobante vigente de una venta anulada: falta NC o baja» cuando `fiscal_pending`. |
| Caja con NV y después boleta | Hoy la caja **no puede** pasar de NV a boleta, porque `receipt_type=''` y nadie lo cambia. Lo contrario sí ocurre: un pedido web con boleta al que el panel le crea una NV. | Una sola clave `sale:<id>` con dos documentos: NV (interno) y B001-n (fiscal). Ingreso una vez. La vista C lo marca como **informativo** («documento interno y fiscal sobre la misma venta»). Si una fase futura permite convertir, la NV debe anularse con motivo, y mientras no lo esté se avisa. |
| Cobro de servicio | `RepairPayment` (adelanto y saldo, alguno reversado) | `repair:<id>`. Suma los no reversados del período. Vista C: «cobro de servicio sin comprobante». Nunca se mezcla con `Order`. |
| Comprobante rechazado y reemitido | Dos originales para el mismo pedido, uno `rejected` | Una clave. La vista B muestra los dos. La vista A toma el vivo como documento principal. |
| Cobro duplicado en un pedido cancelado | `PaymentTransaction.duplicate` | Evento «cobro tardío» de la misma clave, que se suma como en `_collections.late_charges`. |

## 3. Brechas (explícitas)

| Ámbito | Estado | Por qué |
|---|---|---|
| Compras (RCE, crédito fiscal) | **BLOCKED** | No hay factura de proveedor, ni IGV de compra, ni modelo de compra. `PURCHASE_ENTRY` es stock con costo, no una compra contable. Se mostrará, como mucho, «Entradas de mercadería (no son compras contables)». |
| Cuentas por pagar | **BLOCKED** (PAYABLES-01 sin empezar) | Sólo existe el gancho de lectura `GET /api/admin/suppliers/settlement/`: una suma, sin deuda ni pagos. |
| Gastos | **BLOCKED** | No existe ningún modelo. |
| Bancos y conciliación bancaria | **BLOCKED** | No existe ningún modelo. El reembolso guarda una referencia escrita, no un movimiento bancario. |
| Caja (apertura, arqueo, cierre) | **BLOCKED** | No existe ningún modelo. La caja registra ventas, no sesiones. |
| Asientos y libro mayor | **BLOCKED (no se hará aquí)** | Fuera de alcance por decisión del propietario. |
| NC/ND de boleta y baja de boleta | **PENDIENTE (FISCAL)** | `_original_is_eligible` y `ConditionCode` sólo `1`. |
| Devolución parcial de líneas | **PENDIENTE** (ORDER-RETURN fase 2) | D1 de la fase 1. |
| Comprobante del servicio técnico | **PENDIENTE** | `RepairPayment` no emite nada. |
| Separar IGV e IPM | **PENDIENTE** | Se guarda una tasa total, y el RVIE la acepta así. Para el detalle: P8. |
| CDR original intacto | **PARCIAL** | El ZIP de SUNAT no se conserva y el CDR se guarda con `replace` (§7.3). Corregirlo es de FISCAL-PROD-01. |
| SIRE / RVIE / RCE automáticos | **BLOCKED** | Faltan contrato, credenciales, obligación confirmada y una decisión sobre la Clave SOL (§10.2). |

## 4. Modelo de operación (consulta, sin libro nuevo)

Paquete nuevo `store/accounting/` (PROPUESTA, ACCOUNTING-02). Funciones puras sobre consultas, sin escrituras:

```
accounting/
  periods.py     Period(start, end, kind, label) desde ?period=YYYY-MM | ?from&to | day/week/month/year;
                 usa sales_analytics_views._company_tz/_day_bounds; tope de 366 días
  scope.py       Scope(company, branches, channel, kind, seller) desde visible_orders/visible_branches
  operations.py  OperationRow (dataclass) + operations(scope, period, filters) -> iterador paginado
  fiscal_states.py  fiscal_state(document, inclusions, voids) -> FiscalState (§5); tabla exhaustiva
  pending.py     reglas de la vista C (§4.3), cada una con código estable
  totals.py      totales del período, mismas reglas que _collections
```

### 4.1 `OperationRow` (vista A)

Lo que lleva cada fila:

- **Clave e identidad:** `key`, `kind` (`sale` / `repair`), `company_id`, `branch` (id y nombre, o «sin
  sucursal»), `channel` (`online` / `pos` / `service`).
- **Fecha y quién:** `date` (fecha local del hecho que la trae al período) y `seller` (sólo con `sold_by`).
- **Comprador:** `customer_display`, sólo con `accounting.export` o en pantalla con máscara (§6).
- **Importes:** `currency`, `gross`, `taxable`, `tax` y `tax_rate`, con `breakdown_source` (`frozen` /
  `rebuilt`). Después, `refunds_confirmed`, `refunds_pending`, `late_charges`, `net` y `kept`.
- **Estado:** `commercial_state` (`pagada`, `anulada`, `devuelta`, `reembolsada`, `servicio`).
- **Documentos:**
  - `documents` es una lista de `{tipo, número, estado, ambiente}`;
  - `primary_document` es el original vivo, o la NV, o `sin_documento`;
  - `document_class` toma uno de estos valores: `factura`, `boleta`, `nc`, `nd`, `nota_interna`,
    `sin_documento`, `anulado`, `pendiente` o `rechazado`.
- **Pagos:** `payment` (`method` y referencia en la caja; proveedor y estado del intento autorizado en la web).
- **Avisos:** `flags`, los códigos de la vista C que afectan a la fila.

Consulta. Como todo, sale de `visible_orders`:

- se filtra `paid=True`, con `_window` sobre `paid_at`;
- `select_related('fulfillment_branch', 'sales_note', 'cancellation')`;
- `prefetch_related` de `fiscal_documents` (con `summary_inclusions` y `void_inclusions` filtrados a
  `superseded=False`, y sus cabeceras), `refunds`, `returns` y los `payment_transactions` autorizados o duplicados;
- se pagina por conjunto de claves `(paid_at, id)`, 100 filas por página, con un cursor opaco.

Los eventos de otra fecha también entran en el período: un reembolso confirmado en octubre de una venta de
septiembre aparece en octubre como **evento de ajuste** (`event='refund'`, la clave de la venta), no como una
venta. La lista distingue `row_type = operation | adjustment`. El total del período es la suma de los dos. Es la
misma regla que `_collections.net`.

### 4.2 Vista B — documentos fiscales

- Una fila por `FiscalDocument` de la empresa, por `issued_at` local.
- El ambiente por defecto es `production`. `beta` aparece sólo con `?environment=beta`, con una franja «PRUEBAS
  — sin validez tributaria».
- Columnas: tipo, serie-número, fecha de emisión, cliente (documento enmascarado sin `accounting.export`), base,
  impuesto, total con signo, estado de la tabla §5, original (en NC/ND), clave de la operación y otorgamiento.
  Luego CDR (presente o ausente, código y observaciones), hash guardado e integridad del hash (§7.3).
- Por boleta, también su resumen (identificador y estado).

### 4.3 Vista C — interno y pendiente

Cada regla tiene un código estable, una gravedad (`regularizar` / `revisar` / `info`) y un texto:

| Código | Regla | Gravedad |
|---|---|---|
| `NO_DOCUMENT` | venta pagada sin original vivo y sin NV | regularizar |
| `INTERNAL_ONLY` | venta con NV emitida y sin original fiscal vivo: «la nota no tiene validez tributaria» | regularizar |
| `REQUESTED_NOT_ISSUED` | `receipt_type` boleta o factura y sin `FiscalDocument` | regularizar |
| `FISCAL_NOT_SENT` | factura `generated`/`signed` sin envío | regularizar |
| `FISCAL_UNCERTAIN` | `submitted` / `submission_error` sin CDR: consultar, no reemitir | revisar |
| `FISCAL_REJECTED` | original `rejected` sin otro vivo | regularizar |
| `BOLETA_NOT_SUMMARISED` | boleta `signed` sin inclusión viva en un resumen | regularizar |
| `SUMMARY_PENDING` | inclusión viva en un resumen no aceptado (incluye `submission_unknown`) | revisar |
| `CANCELLED_WITH_LIVE_DOC` | `OrderCancellation.fiscal_pending` y ninguna NC ni baja aceptada | regularizar |
| `REFUND_WITHOUT_NOTE` | reembolso confirmado y comprobante vivo sin NC por un importe igual | revisar |
| `NOTE_WITHOUT_REFUND` | NC aceptada sin reembolso confirmado igual | revisar |
| `REFUND_PENDING` | `OrderRefund` pendiente | revisar |
| `INTERNAL_AND_FISCAL` | NV emitida y original vivo sobre la misma venta | info |
| `TEST_PAYMENT` | pedido pagado cuyo único intento autorizado es `environment='test'` | revisar |
| `BETA_DOCUMENT` | el documento principal es BETA | revisar |
| `AMOUNT_MISMATCH` | `FiscalDocument.total ≠ Order.total` en un original vivo | regularizar |
| `BREAKDOWN_REBUILT` | venta sin snapshot C2.1 | info |
| `NO_BRANCH` | `fulfillment_branch` nulo (historial) | info |
| `SERVICE_NO_DOCUMENT` | cobro de servicio no reversado | regularizar |
| `CDR_HASH_MISMATCH` | `sha256(cdr_xml) ≠ cdr_sha256` (§7.3) | revisar |

«Regularizar» describe lo que falta. **No lo ejecuta.** La vista C no tiene botones de emitir, anular ni enviar.
Enlaza a la pantalla del pedido, donde rigen sus propias capacidades.

## 5. Estados de la vista B

Se deriva de varias columnas. Nunca se lee sólo de `status`. Una prueba falla si aparece un valor de
`FiscalDocumentStatus`, `FiscalSummaryStatus` o `FiscalVoidStatus` sin mapear: protege contra un cambio de
FISCAL-PROD-01 (T-09).

| Situación en la base | Estado mostrado |
|---|---|
| `pending` / `generated` | Generado, sin firmar |
| factura `signed` | Firmado, no enviado |
| factura `submitted` sin CDR | Enviado, sin constancia (pendiente de conciliar) |
| `submission_error` | Error de envío: se reintenta el mismo documento |
| factura `accepted` con CDR | Aceptado por SUNAT (CDR) · *autenticidad del CDR no verificada* |
| `accepted_observed` | Aceptado con observaciones (se listan las notas del CDR) |
| `rejected` | Rechazado (código y mensaje) |
| boleta `signed`, sin inclusión viva | Firmada, pendiente de resumen |
| boleta con inclusión viva en un RC `generated`/`signed`/`submission_error` | En resumen no enviado |
| … RC `submitted` | Resumen enviado (ticket), sin veredicto |
| … RC `submission_unknown` | Resumen con envío incierto: consultar ticket |
| … RC `accepted`/`accepted_observed` | **Informada por resumen aceptado** |
| boleta cuyas inclusiones están todas `superseded` (resumen rechazado) | Pendiente de resumen (resumen anterior rechazado) |
| inclusión viva en una RA `accepted` | Dado de baja |
| inclusión viva en una RA no aceptada | Baja en curso |
| NC/ND | Su propio estado, con la tabla anterior, y «sobre F001-n» |
| entorno `beta` | Cualquiera de los anteriores, con «PRUEBAS» |

Lo que **no** se muestra nunca: «Declarado», «Conciliado con SUNAT» o «Presentado». Esos estados no existen
hasta ACCOUNTING-06 y ACCOUNTING-07 (§10), y exigen un registro con evidencia.

## 6. Filtros y permisos

**Filtros**, todos validados en el servidor:

- **Período:**
  - `?period=day|week|month|year` con `?date=`;
  - `?from=&to=` (rango propio, como mucho 366 días);
  - `?tax_period=YYYYMM` (mes calendario local; la vista B lo filtra por `issued_at`).
- **Alcance:**
  - `?branch=` sólo estrecha `visible_branches`; un id ajeno da un resultado vacío, igual que en analítica;
  - `?channel=online|pos|service`;
  - `?kind=sale|repair` y `?operation=` (pagada, anulada, devuelta, reembolsada);
  - `?seller=`, que sólo acepta un usuario con membresía activa en la empresa.
- **Documento:** `?doc=factura|boleta|nc|nd|nota_interna|sin_documento|anulado|pendiente|rechazado`, que admite
  varios valores.
- **Ambiente fiscal:** `?environment=production|beta`.

**Capacidades** nuevas en `capabilities.py`, con estado ACTIVE:

| Código | Qué abre |
|---|---|
| `accounting.view` | las vistas A, B y C en pantalla y los totales. El documento del cliente se enmascara (`****5678`). Sin costos. |
| `accounting.export` (**sensible**) | CSV y XLSX con datos sin enmascarar, PDF de resumen y ZIP de evidencia con XML y CDR. Exige además `accounting.view`. |
| `accounting.reconcile` (RESERVED hasta ACCOUNTING-06) | registrar una conciliación manual |
| `accounting.close` (RESERVED hasta ACCOUNTING-07) | marcar un período revisado o cerrado internamente, y reabrirlo |

Reglas:

- **Empresa:** se resuelve en el servidor. Un recurso ajeno responde 404.
- **Sucursal:** todo parte de `visible_orders`, así que un miembro SELECTED sólo ve sus sucursales.
  - El **ZIP de evidencia** y los resúmenes RC, que son de toda la empresa, exigen alcance de empresa
    (`tenancy::has_company_wide_scope`). Un RC lleva boletas de todas las sucursales, y entregar su XML a quien
    ve una sola filtraría las demás.
  - Un miembro SELECTED ve el estado del resumen de sus boletas, pero no el XML del RC.
- **Costos:** las columnas de costo y margen sólo aparecen con `costs.view`.
- **Asignación por defecto** (PROPUESTA; P3): una migración de datos al estilo `0126_costs_view_capability`
  concede `accounting.view` al preset `Administrador`. `accounting.export` **no** se concede de oficio: lo asigna
  el propietario. Los roles legacy ADMIN y SUPERADMIN ya obtienen todo lo asignable.

## 7. Exportaciones

### 7.1 Tabulares

- **Formatos:** CSV (UTF-8 con BOM, `;`, decimales con punto y texto protegido con `xlsx_reader.guard_text`
  contra fórmulas) y XLSX en modo `write_only` (patrón `inventory_exports`).
- **Contenido:** tres libros, uno por vista, más una hoja «Totales» y una hoja «Alcance» que dice qué no incluye
  (§0).
- **Tope:** 20 000 filas (`MAX_EXPORT_ROWS`). Más allá se pide estrechar el período, y nunca se trunca en
  silencio.
- **PDF de resumen** (reportlab, ya es dependencia):
  - totales del período por canal y documento, y el conteo de la vista C por código;
  - el pie: «Documento de control interno. No es una declaración ni un libro contable».

### 7.2 ZIP de evidencia por período (`?tax_period=YYYYMM`, un mes)

Estructura:

```
00_LEEME.txt                     alcance, qué es y qué no es, cómo verificar hashes
manifest.json                    ver abajo
SHA256SUMS                       sha256 de cada archivo del ZIP (formato sha256sum)
reportes/operaciones.csv  operaciones.xlsx  documentos_fiscales.csv  pendientes.csv  resumen.pdf
fiscal/01-facturas/{RUC}-01-{SERIE}-{N}.xml        y cdr/R-{RUC}-01-{SERIE}-{N}.xml
fiscal/03-boletas/…                                (sin CDR individual: no existe por diseño)
fiscal/07-notas-credito/…   fiscal/08-notas-debito/…
fiscal/resumenes/RC-….xml   y cdr/R-RC-….xml        resúmenes cuya fecha de referencia cae en el mes
fiscal/bajas/RA-….xml       y cdr/R-RA-….xml
fiscal/pdf/…                                       representación impresa, sólo si se pide y bajo tope
internas/notas-de-venta/notas.csv  [+ NV-….pdf bajo tope]   SIEMPRE en carpeta aparte
estados/estados_y_errores.csv    estado §5, intentos (FiscalSubmissionAttempt: n.º, resultado, código, safe_message)
faltantes.csv                    lo que falta, con motivo
```

**`manifest.json`:**

- **Cabecera:**
  - versión del formato, empresa (id, RUC y razón social desde `FiscalDocument` o identidad), período y zona
    horaria;
  - ambiente (sólo `production`; BETA va en otro ZIP, marcado PRUEBAS), filtros, `generated_at`,
    `generated_by` (id de usuario) y SHA de la aplicación;
  - conteos por tipo.
- **Entradas:** una por archivo, con `path`, `source` (`modelo:id`), `bytes`, `sha256_export` (lo que se
  escribió), `sha256_stored` (la columna) y `hash_ok` (bool).
- **Faltantes:** `missing[]`, con `{documento, artefacto, motivo}`. Los motivos son `sin_firmar`, `sin_cdr`,
  `rechazado_sin_cdr`, `pdf_omitido_por_tope` y `hash_no_coincide`.
- **Aviso fijo:** «Este paquete no es una declaración ni prueba de presentación ante SUNAT».

### 7.3 Autenticidad de XML y CDR

- **El XML firmado se escribe con los bytes guardados.** Si `sha256(signed_xml.encode('utf-8'))` no coincide con
  `signed_xml_sha256`, se incluye igual, con `hash_ok=false`, y figura en `faltantes.csv` como
  `hash_no_coincide`. **Nunca se recalcula ni se sobrescribe un hash guardado.**
- **El CDR, igual.** Como se guarda con `decode('utf-8','replace')`, un CDR con bytes no UTF-8 dará
  `hash_ok=false`. Se informa como «texto conservado, bytes originales no recuperables». Arreglarlo guardando los
  bytes crudos es de FISCAL-PROD-01, no de esta fase.
- **Nunca se inventa un CDR.** Una boleta no tiene CDR individual (lo que la cubre es el CDR del resumen); una
  factura sin CDR va a `faltantes.csv`. No se genera ningún archivo «R-…» que SUNAT no haya devuelto.
- Todo CDR lleva en `00_LEEME.txt` la nota «autenticidad de la firma no verificada (ADR-23)».

### 7.4 Recursos, límites y concurrencia

- **El servidor tiene un proceso de gunicorn con 8 hilos y timeout de 60 s** (`backend/Dockerfile.prod`). Un ZIP
  no puede ocupar memoria ni la CPU de un hilo durante minutos.
- **Por streaming:** `StreamingHttpResponse` sobre `zipfile.ZipFile` escrito en un búfer sin `seek`, con
  descriptores de datos, y archivo por archivo con `iterator(chunk_size=200)`. El XML y el CDR ya son texto en
  la base: son baratos.
- **Topes** (PROPUESTA):
  - un período = un mes;
  - 6 000 documentos fiscales por ZIP;
  - 150 MB sin comprimir: al superarse se corta **con error explícito en el propio ZIP** (`TRUNCADO.txt` y
    `manifest.complete=false`), y el registro de auditoría lo dice. Mejor aún, se cuenta antes de empezar y se
    rechaza con 400;
  - PDF de comprobantes y notas de venta **apagados por defecto**: hasta 200 con `?pdf=1`, porque dibujarlos
    ocupa la CPU del proceso.
- **Concurrencia:** un ZIP a la vez por empresa, con un bloqueo de PostgreSQL (`pg_try_advisory_lock` sobre la
  empresa, liberado al terminar el generador). El throttle es propio (`AccountingExportThrottle`, p. ej. 6/hora
  por usuario).
- **Fase posterior** (sólo si los topes no bastan): un trabajo en segundo plano que deja el ZIP en el
  almacenamiento privado (`evidence_storage`), con caducidad. Exige un modelo y una decisión sobre la retención
  (P9).

## 8. Auditoría de exportaciones

- Cada exportación (tabular, PDF o ZIP) deja un `AdminAuditLog` **con `company`**:
  - `action='accounting_exported'`, `target_type='accounting_export'`;
  - `metadata`: `{kind, format, period, filters, rows, files, bytes, missing_count, hash_mismatch_count,
    manifest_sha256, pdf_included, unmasked: true|false, complete}`.
- **Nunca** una celda, un documento de cliente, un nombre ni un XML.
- **Cuándo se escribe:** se registra al empezar la exportación (`status='started'`). Si el ZIP se corta, queda un
  segundo registro `accounting_export_failed`. Así una descarga interrumpida no desaparece del registro.
- **Las vistas en pantalla no se auditan** por cada consulta: son lecturas. La exportación sí.

## 9. Rendimiento

- **Siempre con período:** sin período se usa el mes en curso. Las vistas A y B admiten como mucho 366 días; el
  ZIP, un mes.
- **Índices que ya existen y sirven:**
  - `Order(company, paid_at)`, `(company, sales_channel, paid_at)` y `(company, sold_by, paid_at)`;
  - `FiscalDocument(company, issued_at)` y `(company, status)`;
  - `RepairPayment(company, received_at)`;
  - `OrderRefund(company, status, created_at)` y `confirmed_at`;
  - `OrderCancellation(company, -created_at)`;
  - `FiscalDailySummary(company, reference_date)`.
- **Ninguna fase añade un índice sin antes medir** con `EXPLAIN ANALYZE` sobre datos de volumen sintético.
  Candidato si hace falta: `FiscalDocument(company, environment, issued_at)`, con la migración en su propio
  turno (§10).
- **Paginación por cursor** `(fecha, id)`, de 100 filas, sin `OFFSET`. Los totales se calculan con una sola
  agregación en la base, no sumando páginas.
- **Sin N+1:** se usan `select_related` y `prefetch_related` (§4.1). Una prueba con `assertNumQueries`
  constante para 1 y para 50 operaciones (T-17).

## 10. Conciliación SUNAT y paquete del contador (fases posteriores)

### 10.1 Seis estados, separados

1. **Emitido:** existe `FiscalDocument` con número.
2. **Enviado:** hay un intento transmitido (`FiscalSubmissionAttempt`), o para la boleta una inclusión viva en un
   RC enviado.
3. **Aceptado:** hay CDR, con estado `accepted`/`accepted_observed`.
4. **Boleta informada por resumen:** hay una inclusión viva en un RC aceptado.
5. **Conciliado con SUNAT/SIRE:** **no existe hoy.** Exige un registro nuevo: quién comparó, contra qué archivo
   de SUNAT (la propuesta del RVIE descargada), con qué resultado por documento y con qué evidencia.
6. **Declaración mensual presentada con evidencia:** **no existe hoy.** Exige un registro del período con la
   constancia de presentación subida (PDF de SUNAT) y su hash. **Un cierre interno no es este estado.**

Los estados 1 a 4 se derivan de lo que ya existe (§5). El 5 y el 6 son hechos que registra una persona, y la
interfaz nunca los infiere.

### 10.2 SIRE / RVIE / RCE (fuentes oficiales)

- **Qué es el SIRE.** Es el Sistema Integrado de Registros Electrónicos. El RVIE es el Registro de Ventas e
  Ingresos Electrónico; el RCE, el de Compras. Portal: <https://sire.sunat.gob.pe/>. Formas de acceso:
  <https://cpe.sunat.gob.pe/node/158>. RCE: <https://cpe.sunat.gob.pe/node/160>. Las páginas HTML responden 403
  a una descarga automática; los PDF, no.
- **API del RVIE**, manual v30 (2026-06):
  <https://cpe.sunat.gob.pe/sites/default/files/2026-06/Manual%20del%20servicio%20API%20del%20Registro%20de%20Ventas%20e%20Informaci%C3%B3n%20Electr%C3%B3nica.%20v30.pdf>.
  Lo que dice:
  - el flujo es descargar la propuesta (las CPE que SUNAT ya tiene), aceptarla o reemplazarla con un `.txt`,
    registrar el preliminar, consultar el ticket y descargar el archivo, inconsistencias y constancia;
  - la base es `https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros/rvie/…`;
  - **para autenticarse**, se registra la aplicación en SOL («Credenciales de API SUNAT», URI «MIGE RCE y RVIE -
    SIRE») y se obtienen `client_id` y `client_secret`;
  - el token sale de `https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/` con
    `grant_type=password`, **usuario SOL y Clave SOL**.
- **API del RCE** (v22):
  <https://cpe.sunat.gob.pe/sites/default/files/inline-files/Manual%20de%20servicios%20Web%20Api%20-%20SIRE_Compras%20v22.pdf>.
  No sirve aquí: no hay compras (§3).
- **Obligación y plazos.** Se han postergado varias veces. Véanse las RS 000217-2025
  (<https://www.sunat.gob.pe/legislacion/superin/2025/000217-2025.pdf>), 000392-2025
  (<https://cpe.sunat.gob.pe/sites/default/files/inline-files/RESOLUCI%C3%93N%20DE%20SUPERINTENDENCIA%20N%C2%B0%20000392-2025.pdf>),
  000075-2026 (<https://www.sunat.gob.pe/legislacion/superin/2026/000075-2026.pdf>) y 000125-2026
  (<https://www.sunat.gob.pe/legislacion/superin/2026/000125-2026.pdf>). Este ADR **no** afirma si la empresa
  piloto está obligada ni desde qué período. Lo confirma el contador (P6).

**Decisión (PROPUESTA): la integración automática queda `BLOCKED`.** Faltan cuatro cosas:

- la obligación confirmada;
- una aplicación registrada en SOL;
- la decisión de guardar o no la **Clave SOL** en la consola de integraciones (cifrada, sólo MASTER). Es una
  credencial que abre todo el RUC, no sólo el SIRE;
- un ambiente de prueba, que SUNAT no ofrece para el SIRE como BETA.

Mientras tanto, la conciliación **manual auditada** es viable. La persona descarga la propuesta del RVIE desde SOL
y la sube al sistema. El sistema compara por `(tipo, serie, número, total, fecha)` contra la vista B del mismo
período y muestra coincidencias, faltantes en SUNAT, faltantes en el sistema y diferencias de importe. La
persona guarda el resultado con su evidencia (el archivo subido, su hash y quién lo hizo).

### 10.3 Paquete del contador y registro de revisión

- **Mensual:** el ZIP de §7.2, el PDF de resumen y, cuando exista, el resultado de la conciliación del mes.
- **Anual:** los doce manifiestos y los totales por mes, sin volver a meter los XML.
- **Registro de revisión** (`AccountingPeriodReview`, ACCOUNTING-07):
  - empresa, período, estado (`abierto` → `revisado` → `cerrado_interno`, con reapertura auditada);
  - el revisor: usuario, o nombre y colegiatura del contador externo escritos por quien registra;
  - fecha, notas y el hash del manifiesto revisado;
  - opcionalmente, la constancia de presentación subida. Sólo esa constancia permite mostrar el estado 6.

## 11. Pruebas previstas

| # | Prueba | Fase |
|---|---|---|
| T-01 | **Sin ingreso duplicado:** una venta con NV, boleta y pago autorizado aparece una vez; `Σ bruto = _collections.gross` y `Σ neto = _collections.net` (misma empresa, sucursales y período) | 02 |
| T-02 | Devolución cerrada con deducción: una operación, el reembolso en su fecha y lo retenido = deducción; no aparece como «cancelada» además | 02 |
| T-03 | Pedido pagado y cancelado con comprobante vivo: una fila y `CANCELLED_WITH_LIVE_DOC` en la vista C | 02 |
| T-04 | Reembolso confirmado en un mes posterior: evento de ajuste en ese mes, no en el de la venta | 02 |
| T-05 | **Notas internas aparte:** una venta sólo con NV sale en la vista A con `nota_interna`, en la C con `INTERNAL_ONLY` y nunca en la B | 02 |
| T-06 | Cobro de servicio: adelanto + saldo − reversado = ingreso; `repair:` nunca se cruza con `sale:` | 02 |
| T-07 | Pedidos `failed`, `expired` y `pending_payment` sin dinero no aparecen; un intento rechazado no suma | 02 |
| T-08 | BETA fuera de la vista B por defecto y marcado con `?environment=beta` | 02 |
| T-09 | Cada valor de `FiscalDocumentStatus`, `FiscalSummaryStatus` y `FiscalVoidStatus` tiene estado en §5; un valor nuevo hace fallar la prueba | 02 |
| T-10 | Boleta: firmada → en resumen → resumen aceptado = «informada»; resumen rechazado → «pendiente de resumen» | 02 |
| T-11 | **Períodos:** día, semana, mes, año, rango, `tax_period`; venta a las 23:30 de Lima en el día correcto; rango > 366 días → 400 | 02 |
| T-12 | **Filtros:** cada valor de `doc`, `channel`, `kind`, `seller` y `branch`; una sucursal ajena no amplía nada | 02 |
| T-13 | **Multiempresa:** otra empresa con ventas, documentos y NV no aparece en ninguna vista, total, exportación ni ZIP; un id ajeno da 404 | 02–05 |
| T-14 | Miembro SELECTED: sólo sus sucursales; sin ZIP de empresa ni XML de RC | 02 / 05 |
| T-15 | Capacidades: sin `accounting.view` → 403; sin `accounting.export`, los documentos van enmascarados y no hay exportación | 02 / 04 |
| T-16 | `TEST_PAYMENT`, `AMOUNT_MISMATCH`, `BREAKDOWN_REBUILT` y `NO_BRANCH` | 02 |
| T-17 | Número de consultas constante para 1 y 50 operaciones | 02 |
| T-18 | CSV/XLSX: protección contra fórmulas, tope de filas con error explícito, hoja «Alcance» | 04 |
| T-19 | **XML/CDR auténticos en el ZIP:** los bytes escritos dan el hash guardado; un hash alterado da `hash_ok=false` y entra en `faltantes`; una factura sin CDR no tiene `R-…`; una boleta nunca tiene CDR individual | 05 |
| T-20 | ZIP: `SHA256SUMS` verificable, `manifest.json` completo, carpeta `internas/` separada, tope y un solo ZIP a la vez por empresa | 05 |
| T-21 | Auditoría: cada exportación deja `accounting_exported` con `company` y sin PII; un corte deja `accounting_export_failed` | 04 / 05 |
| T-22 | Ninguna vista ni exportación escribe en la base, salvo el registro de auditoría | 02–05 |
| T-23 | Ningún texto de la interfaz ni de la exportación dice «declarado», «presentado» ni «conciliado» antes de ACCOUNTING-06/07 | 03–05 |

Las pruebas corren sobre PostgreSQL. T-20 va con `TransactionTestCase` por el bloqueo consultivo, en el último
fragmento del CI.

## 12. Fases y archivos

| Fase | Contenido | Archivos (exclusivos de la fase mientras dura) | ¿Ahora? |
|---|---|---|---|
| **ACCOUNTING-02** · capa de consulta y API | §4, §5, §6, capacidades `accounting.view`/`accounting.export` (y las dos RESERVED) | `store/accounting/**` (nuevo), `store/accounting_views.py` (nuevo), `urls.py` (sólo el tramo `admin/accounting/`), `capabilities.py` (cuatro entradas), **una** migración de datos (concesión al `Administrador`; número al fusionar), `store/test_accounting_*.py` | **Sí.** No toca `fiscal_*`, `order_*`, `pos_*`, `sales_analytics_views.py` (sólo importa) ni `models.py`. |
| **ACCOUNTING-03** · pantalla | Contabilidad → Centro contable: pestañas A, B y C, filtros, avisos de alcance | `frontend/app/admin/accounting/**`, `frontend/app/admin/lib/accounting-api.ts` (nuevos), `frontend/__tests__/accounting-*.test.tsx`, **una** entrada en `admin/lib/internal-modules.ts` | **Sí, en paralelo**, con simulaciones del contrato §4; se fusiona después de 02 |
| **ACCOUNTING-04** · exportaciones tabulares y PDF | §7.1, §8 | `store/accounting/exports.py`, `store/accounting/summary_pdf.py`, vistas de exportación en `accounting_views.py`, `test_accounting_exports.py` | Después de 02 |
| **ACCOUNTING-05** · ZIP de evidencia | §7.2–§7.4, §8 | `store/accounting/evidence_zip.py`, `test_accounting_evidence_zip.py` | Después de 04 |
| **ACCOUNTING-06** · conciliación manual | §10.1 (estado 5), §10.2 manual | modelos `AccountingReconciliation` y `…Line`, su migración, `store/accounting/reconciliation.py`, `accounting.reconcile` ACTIVE | **BLOCKED** por P6 y P7 (formato del archivo SUNAT que se suba) |
| **ACCOUNTING-07** · revisión y paquete del contador | §10.3 (estado 6) | modelo `AccountingPeriodReview` y migración, `accounting.close` ACTIVE, subida privada vía `evidence_storage` | Después de 05; P5, P10 |
| **ACCOUNTING-08** · SIRE automático | API RVIE | `store/integrations/providers/sire.py` | **BLOCKED** (§10.2) |

**Para no chocar:**

- **FISCAL-PROD-01:** ACCOUNTING **no edita** `fiscal/**`, `fiscal_*.py` ni `fiscal_config.py`. Si FISCAL cambia
  un estado, T-09 lo detecta y se ajusta `accounting/fiscal_states.py`. La conservación de los bytes crudos del
  CDR (§7.3) es de FISCAL-PROD-01.
- **LABEL y MIGRATION:** no comparten archivos con ACCOUNTING-02 a 05. Hay tres puntos comunes:
  - `capabilities.py`, que MIGRATION-C también toca con una entrada;
  - `urls.py` y `internal-modules.ts`, que LABEL-03 y MIGRATION-F también tocan;
  - la **numeración de migraciones**: la hoja es `0145`, #162 trae `0146` y MIGRATION-C reserva `0147`–`0148`.
  Se toman tramos distintos y se fusiona de uno en uno; la migración de datos de ACCOUNTING-02 toma el número
  libre al fusionar.
- **ORDER-RETURN fase 2** (devolución parcial) cambiará §1.6 y T-02. Hay que fusionarla antes que
  ACCOUNTING-02, o adaptar después la regla del reembolso. La clave no cambia.
- **02, 05, 07 y NEXT_ACTION:** cada fase sólo añade su propia entrada al cerrar.

## 13. Decisiones (todas PROPUESTA)

| ID | Decisión | Alternativas descartadas |
|---|---|---|
| AC-1 | Capa de consulta sobre los modelos existentes; ningún libro ni tabla de asientos | Tabla `AccountingEntry` alimentada por señales: un segundo registro que diverge del primero y que habría que reconciliar |
| AC-2 | Clave `sale:<order>` / `repair:<repair_order>`; el importe sólo de `Order` o `RepairPayment` | Sumar documentos (cuenta tres veces); clave por documento (una venta sin documento desaparece) |
| AC-3 | La NC/ND no resta del ingreso de la vista A; resta el reembolso confirmado; la conciliación compara | Restar la NC (una NC sin devolución de dinero bajaría el ingreso cobrado); restar las dos (doble resta) |
| AC-4 | El período de la venta va por `paid_at` y el del documento por `issued_at`, en la zona de la empresa | `created_at` (cuenta pedidos no pagados); la fecha UTC de la conexión |
| AC-5 | Servicio técnico con criterio de caja (pagos no reversados) | Devengo por cotización aprobada (P1) |
| AC-6 | BETA fuera por defecto, nunca sumado con producción | Mezclarlos con una columna |
| AC-7 | `accounting.view` / `accounting.export` aparte de `sales.analytics.view` y `sales.fiscal.view` | Reutilizar `reports.view` (AVAILABLE, legacy, demasiado amplio) o `sales.fiscal.view` (lo tiene el preset Ventas) |
| AC-8 | ZIP por streaming síncrono con topes y un bloqueo por empresa; PDF apagados por defecto | Armarlo en memoria (25 MB no alcanzan); cola en segundo plano desde el día 1 (infraestructura nueva sin necesidad medida) |
| AC-9 | Hashes verificados al exportar, nunca reescritos; la discrepancia se informa | Recalcular el hash del texto guardado (borraría la prueba de la alteración) |
| AC-10 | SIRE automático `BLOCKED`; conciliación manual auditada | Guardar la Clave SOL y llamar a la API sin contrato ni decisión del propietario |

## 14. Riesgos

- **Expectativa.** Con `FISCAL_ENABLED=0`, toda venta de R1 aparece «sin comprobante» o «sólo interna». Es
  cierto. El propietario debe saberlo antes de ver la pantalla.
- **Lectura como contabilidad completa.** Se mitiga con el aviso de alcance en pantalla, en el PDF, en el ZIP y en
  cada hoja.
- **Datos personales.** Los documentos de los clientes salen en las exportaciones. Se mitiga con la máscara sin
  `accounting.export`, la auditoría y la concesión no automática.
- **Carga del único proceso.** Un ZIP grande o muchos PDF. Se mitiga con topes, streaming, un bloqueo por
  empresa, throttle y PDF apagados.
- **Hash del CDR que no coincide.** Viene del `replace` del guardado. Se informa; FISCAL-PROD-01 lo corrige.
- **Pagos TEST contados como venta en la analítica actual.** El centro los marca. Excluirlos cambiaría
  `_collections` (P4), y no se hace en silencio.
- **Deriva frente a la analítica.** Dos cálculos del mismo dinero pueden separarse. T-01 los ata.
- **Historial sin sucursal ni desglose.** Se marca y nunca se inventa.
- **Choque con fases paralelas.** Ver los archivos, la numeración y T-09 (§12).
- **Que el contador o SUNAT tomen el ZIP como prueba de presentación.** Va el aviso en `00_LEEME.txt` y en el
  manifiesto.

## 15. Preguntas al propietario

- **P1.** El ingreso del servicio técnico: ¿se cuenta por cobro (pagos no reversados, propuesto) o por la
  cotización aprobada? ¿Se emitirá comprobante a los servicios? Hoy no se emite ninguno.
- **P2.** ¿La venta cuenta en el período de su pago (`paid_at`, propuesto)? Hoy coincide con la fecha de emisión
  del comprobante.
- **P3.** ¿Quién recibe `accounting.view` por defecto (propuesto: el preset Administrador)? ¿Y
  `accounting.export`: sólo por asignación explícita? ¿Hace falta un rol «Contador» de sólo lectura para un
  externo?
- **P4.** Los pedidos pagados con claves de prueba de Izipay: ¿se excluyen del ingreso? Eso cambiaría también el
  tablero de ventas.
- **P5.** ¿El ZIP debe llevar los PDF de las boletas y facturas, o bastan XML + CDR con los PDF bajo pedido?
- **P6.** SIRE: ¿la empresa está obligada al RVIE, y desde qué período? ¿Quién tiene la Clave SOL? ¿Se autoriza a
  guardarla cifrada para la API, o sólo conciliación manual?
- **P7.** ¿Qué formato pide el contador: Excel, el `.txt` de reemplazo del RVIE, otro?
- **P8.** ¿Hace falta separar IGV e IPM en los reportes, o basta la tasa total?
- **P9.** ¿Se guarda una copia de cada ZIP generado (retención y caducidad), o sólo se descarga?
- **P10.** ¿Quién puede marcar un período «revisado» o «cerrado internamente», y quién reabrirlo?
- **P11.** ¿En qué orden van PAYABLES-01, los gastos y los bancos? Hasta entonces, compras, gastos y bancos siguen
  `BLOCKED`.
