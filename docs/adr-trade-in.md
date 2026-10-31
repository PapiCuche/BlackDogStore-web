# ADR — TRADE-IN-01 · Equipo recibido como parte de pago

Estado: **PROPUESTA** (revisada tras INV-COM-01C: §1.2, §3.3, §3.12 y §6 corregidos). Nada de TRADE-IN está implementado. Este documento audita el punto de venta, el
inventario y el marco documental, y deja el contrato de dominio listo para que el propietario decida. **No es
asesoría tributaria**: lo fiscal se presenta como alternativas con evidencia y requiere validación de un contador
antes de tocar boletas, facturas, IGV o la emisión electrónica.

Orden de autoridad: código > migraciones > tests > docs. Las referencias tienen la forma `archivo:línea` sobre
`master` en `0f143a7`. Sin rutas: `backend/store/`.

## 0. Qué es y qué no es

- Es la **venta de un equipo nuevo o seminuevo** en la que el cliente entrega **un equipo suyo** que la tienda acepta
  y valoriza, y que se reconoce como **crédito** al liquidar la venta; el resto se paga en efectivo, tarjeta o
  transferencia.
- Son **cuatro hechos distintos**, y se guardan separados: la venta al cliente, la valorización aprobada del equipo
  recibido, el crédito que esa valorización aporta a la liquidación y lo que se cobra en dinero.
- **No es un descuento.** `manual_discount` baja el total, la base y el IGV, añade un cargo de descuento al XML fiscal
  y exige `sales.discounts.apply`. Usarlo para esto falsearía la venta (§3.2).
- **No es un estado nuevo de pago.** `PaymentMethod` lo comparten reparaciones, obligaciones y los serializadores v1:
  un valor nuevo ahí rompe contratos ajenos.
- **No emite nada.** SUNAT BETA sigue desactivado. Ningún comprobante fiscal se emite en este ADR ni en sus pruebas.

## 1. Lo que el código ya hace (hechos)

### 1.1 Punto de venta

- Una venta de caja **nace pagada y entregada, en una sola transacción** (`pos_services.py:1177-1544`, estado en
  `:1355`). El único estado posterior es `CANCELLED` (`order_cancellation._commit`). No existe un estado pendiente.
- Idempotencia en tres capas: índice único `unique_pos_idempotency_key_per_company` (`models.py:793`), un bloqueo
  asesor más `PosSaleKeyReservation` para la decisión «no se cobró» (`pos_services.py:104-135`) y una huella de la
  **intención** de la petición, no de los precios resueltos (`:458`). Todo campo nuevo de la petición entra en la
  huella solo cuando está presente (precedente: `receipt_type`, `:535`).
- Los totales salen de `calculate_pos_totals` (`:711`). Los precios de catálogo incluyen IGV y se descomponen hacia
  atrás (`tax_services.py:182`); `taxable_amount`, `tax_amount` y `tax_rate` se congelan en la orden (`:1344`).
- Los descuentos son excluyentes entre sí; el manual necesita `sales.discounts.apply` y motivo y **no tiene tope**.
- **No hay una comprobación «los pagos suman el total».** Una orden tiene un solo `payment_method` (`models.py:565`);
  `resolve_cash` (`pos_services.py:763`) solo exige efectivo recibido ≥ total. No hay pago dividido ni tabla de pagos
  del POS: `PaymentTransaction` es de la pasarela web y el POS no la toca.
- **No existe cierre de caja, turno ni arqueo** (`docs/saas-multiempresa.md:2113`, `docs/adr-accounting.md:257`).
- **No hay asientos.** El Centro Contable es de lectura y toma el ingreso como `Order.total`
  (`accounting/operations.py`). `payment_method` solo lo leen `pdf_services.py:197` (PDF de la nota de venta, ticket y
  ESC/POS) y `accounting/operations.py:310`.
- El XML fiscal siempre declara «Contado» (`fiscal/builder.py:336`) y exige total = subtotal − descuento
  (`fiscal_services.py:440`).
- La API móvil v1 reutiliza `create_pos_sale` con las mismas palabras clave (`v1_pos_views.py:344-380`) e ignora
  claves desconocidas del cuerpo: añadir campos opcionales es seguro.

### 1.2 El precio de un seminuevo en caja (resuelto por INV-COM-01C)

- Hasta INV-COM-01B la caja precia por `Product.price` o por la oferta de proveedor, nunca por `StockUnit.sale_price`:
  un seminuevo se vendía al precio del producto y la caja rechazaba un producto con precio ≤ 0. Las variantes seminuevas
  creadas por el catálogo asistido o por la carga nacen a `0.00` hasta que alguien las precia.
- **INV-COM-01C cierra esa brecha** (código, sin migración): un equipo serializado de la línea seminueva se cobra en caja
  a su `StockUnit.sale_price` (`pos_services.unit_prices`, autoridad única de la vista previa y de la venta); sin precio
  válido, ESE equipo no se vende; el costo nunca decide el precio; el precio vendido queda congelado en un `OrderItem`
  por equipo (`stock_unit`) y la salida de Kardex sigue siendo una por (venta, producto). Un cambio de precio entre la
  vista previa y el cobro responde 409 `price_changed` si el cliente envió `expected_total`.
- Consecuencia para TRADE-IN: el seminuevo que se entrega como parte de pago, una vez recibido y con su precio propio,
  ya se puede vender en caja a ese precio. Un trade-in **no cambia** el precio de lo que se vende.

### 1.3 Recibir un equipo usado

- `receive_units` (`stock_unit_services.py:331`) exige un producto serializado y solo admite `PURCHASE_ENTRY`,
  `INITIAL_STOCK` y `MANUAL_ENTRY`. Crea la unidad `AVAILABLE` y le da su `label_code`.
- No existe tipo de movimiento «compra a particular» ni «parte de pago». `StockUnit.Status` es `AVAILABLE`,
  `RESERVED`, `SOLD`, `WRITTEN_OFF` (`models.py:10040`): **no hay estado «en inspección»**. La invariante
  `BranchStock.quantity == count(AVAILABLE)` hace que una unidad recibida cuente y se pueda vender de inmediato.
- Unicidad: (empresa, producto, serie), (empresa, IMEI) y (empresa, IMEI 2) cuando no son nulos, y (empresa,
  `label_code`); valen **en cualquier estado**: un equipo que esta tienda vendió y que vuelve como trade-in choca con
  su propia fila vendida. Hoy nada lo resuelve.
- El equipo recibido necesita un producto serializado del catálogo: un modelo que no existe no tiene dónde entrar.
- Los lotes de etiquetas se generan solo para `PURCHASE_ENTRY` e `INITIAL_STOCK` (`label_batches.py:55`).

### 1.4 Clientes, permisos y aprobaciones

- `Customer` (`models.py:4244`) es por empresa, con `document_type` y `document_number` únicos por empresa; una venta
  se enlaza por `resolve_pos_customer` y se congela con `_customer_snapshot` (`pos_services.py:940-983`); se permite la
  venta anónima. No hay regla de retención ni base legal para guardar el DNI de un vendedor particular.
- Capacidades relevantes: `sales.pos.use`, `sales.discounts.apply`, `sales.pos.assign_seller`, `sales.orders.manage`,
  `inventory.adjust`, `inventory.counts.approve`, `costs.view` (no la da `inventory.adjust`), `products.manage`.
- Patrones de aprobación reutilizables: la devolución en tres pasos (solicitar, recibir y cerrar con autoridades
  distintas), `discount_authorized_by` y la aprobación de recuentos con capacidad propia. **No hay PIN ni anulación
  por supervisor.** Un «superior» es `sales.orders.manage` + `company.manage` (`order_cancellation.py:349`).
- La empresa y la sucursal nunca vienen del cuerpo: `resolve_pos_branch` elige entre `visible_branches`
  (`pos_services.py:792`).

### 1.5 Devoluciones y cancelaciones

- `order_refunds.collected` y `close_return` tratan `order.total` como dinero cobrado (`order_refunds.py:115`,
  `order_returns.py:~660`), con métodos de reembolso pasarela, efectivo, transferencia y otro. **Un crédito no
  cobrado en dinero rompe esa suposición.**
- `_restore_stock` (`order_cancellation.py:788`) recorre los movimientos `SALE_EXIT` y `StockUnit.order`: una unidad
  recibida en trade-in **no debe** colgar de ninguno de los dos.
- La comisión se calcula sobre subtotal − descuento, no sobre el dinero cobrado.

## 2. Marco documental y tributario (evidencia, no asesoría)

Fuente completa y URLs en §9. Lo que dicen los textos oficiales leídos y lo que es interpretación:

1. **La venta con parte de pago es una venta por el total.** Ley del IGV art. 3 (venta = transferencia onerosa de
   bienes); la base es lo que paga el adquirente (art. 14). Entregar un bien como parte de pago no reduce esa base.
   *Primaria.*
2. **El equipo recibido es una compra aparte de la tienda.** El Reglamento del IGV art. 5.4 a dice que en la permuta
   «cada parte tiene carácter de vendedor». *Interpretación:* el equipo usado es una adquisición de la tienda, no un
   descuento al cliente.
3. **No debe modelarse como descuento.** Un descuento solo sale de la base si es usual, general, consta en el
   comprobante y no es un retiro de bienes (Ley art. 14, Reglamento art. 5.13). *Interpretación:* llamarlo descuento
   subdeclararía base e IGV.
4. **La liquidación de compra (LC) casi seguro no sirve para celulares.** El Reglamento de Comprobantes de Pago art. 6
   num. 1.3 la limita a una lista cerrada (agropecuario, pesca artesanal, madera, minería artesanal, artesanía,
   desechos), con vendedor sin RUC y hasta 75 UIT (Oficio 004-2007-SUNAT/200000). Para un particular no habitual el RCP
   prevé el Formulario 820 (art. 6 num. 2), que tramita el vendedor: *no está comprobado que sea practicable en un
   mostrador.* Un cliente con RUC debe facturar su entrega (art. 6.1.1).
5. **El anexo UBL 2.1 (R.S. 193-2020) solo admite «Contado» o «Crédito»** como forma de pago: no hay valor estándar
   para pago en especie.
6. **Bancarización** (TUO Ley 28194): umbral de S/ 2 000 o US$ 500; no está claro si la parte en especie cuenta.
7. **IMEI y receptación.** El D.Leg. 1338 y su reglamento, en la copia consultada, no imponen al comerciante de
   usados un registro de compraventa ni la consulta del IMEI; OSIPTEL recomienda comparar el IMEI físico con el
   lógico y consultar «Checa tu IMEI». Hay riesgo penal de receptación (Código Penal art. 194) visto solo en fuentes
   secundarias. **Recomendación de ingeniería, no obligación legal:** guardar por cada retoma el IMEI, el resultado y
   la fecha de la consulta, los datos y la firma del vendedor y una declaración de propiedad.
8. **No se halló ningún Informe ni Carta SUNAT sobre la retoma de celulares usados.** Todo lo anterior sale del texto
   de las normas.

### Alternativas documentales (para el propietario y el contador)

| | Descripción | Pros | Contras |
|---|---|---|---|
| **A (recomendada)** | Comprobante por el **valor total** del equipo vendido, forma de pago «Contado». Documento **interno** de liquidación comercial con el desglose (efectivo + valorización). La compra del usado se sustenta con lo que apruebe el contador (Formulario 820, factura del proveedor con RUC o documento interno firmado). | Respeta la base del art. 14; no usa descuento; trazabilidad clara; coherente con «cada parte es vendedor». | Requiere definir el sustento de compra y, si hay comprobante fiscal, cómo se anota la retoma (leyenda). |
| B | Facturar solo el saldo en dinero. | Simple. | **No recomendada:** sin respaldo normativo; subdeclara base e IGV; esconde la compra del usado. |
| C | Mientras SUNAT BETA esté desactivado: solo nota de venta interna + liquidación comercial. | No emite un comprobante incorrecto; acumula datos. | La venta sigue obligada a comprobante (RCP art. 6); la nota de venta no sustituye a la boleta o factura. Temporal. |

**Este ADR adopta, como contrato de datos, lo compatible con A y con C** (la venta conserva su total íntegro y el
crédito es un hecho aparte). Qué se imprime o emite lo decide el propietario con el contador (D1).

## 3. Decisión propuesta: el contrato de dominio

### 3.1 Principios

1. El total de la venta, su base y su IGV **no cambian** por haber un trade-in.
2. El equipo recibido es una **compra de stock** que sigue las reglas de entrada existentes; no es una devolución ni
   un ajuste.
3. Nada entra como stock disponible ni cuenta como crédito hasta que **la entrega y la aceptación sean efectivas**.
4. Ningún movimiento histórico se borra: se **compensa** con operaciones oficiales y trazables.
5. La misma operación no puede duplicar el ingreso de un IMEI ni aplicar dos veces el crédito (reintento, doble clic,
   respuesta perdida).
6. Backend manda: toda regla se comprueba en el servidor.

### 3.2 Por qué no `manual_discount`, no `PaymentMethod` y no un estado nuevo de unidad

- `manual_discount`: baja total, base e IGV; entra al XML como cargo; exige capacidad y motivo pensados para otra cosa.
- Valor nuevo de `PaymentMethod`: lo comparten `RepairPayment` (restricción CHECK en `models.py:7432`), pagos de
  obligaciones y serializadores v1. Un valor ahí es un cambio de contrato en tres superficies.
- Estado nuevo `PENDING_INSPECTION` en `StockUnit`: toca la invariante `quantity == count(AVAILABLE)` y todo filtro por
  estado. **Se descarta a favor de un registro aparte** (§3.3): hasta aprobar, la unidad no existe.

### 3.3 Modelo de datos (aditivo; migración propia en TRADE-IN-01B)

**`TradeInIntake`** — un equipo entregado por un cliente, antes y después de existir como unidad.

| Campo | Notas |
|---|---|
| `company`, `branch` | Aislamiento. La sucursal sale de las visibles del operador, nunca del cuerpo. |
| `status` | `draft` → (`pending_approval` →) `approved` → `applied`; o `rejected`, `cancelled`, `reversed`. `pending_approval` es un estado real de la máquina (§3.5 paso 3 lo usa: la valorización excede el límite o el vendedor no puede valorar); las transiciones válidas son una tabla cerrada. Transiciones solo por servicios, con `select_for_update`. |
| `customer` (FK nulo) | Quien entrega; obligatorio para aprobar (D6). |
| `product` (FK nulo) | Producto serializado del catálogo; si el modelo no existe, el intake queda en `draft` hasta crear la variante con el panel de catálogo (no se inventa). |
| `serial_number`, `imei`, `imei2` | Se guardan normalizados como `device_identity`; **enmascarados en toda respuesta de listado**. |
| `condition`, `battery_health`, `cosmetic_grade`, `notes`, `public_notes` | Los de la ficha de un seminuevo. |
| `valuation` (Decimal), `valuation_proposed_by`, `valuation_approved_by`, `approved_at`, `approval_reason` | La valorización es del intake, **no** el costo ni el precio de venta. Al aplicarse, `StockUnit.cost = valuation` (costo de adquisición). |
| `identity_checks` (JSON pequeño) | Resultado y fecha de la consulta de IMEI (OSIPTEL), declaración de propiedad aceptada, bloqueo de activación/MDM verificado. **Solo verificaciones de elegibilidad**: no se automatiza ningún desbloqueo. |
| `order` (FK nulo, **no único**), `stock_unit` (FK nulo, único), `idempotency_key` | Enlace explícito con la venta y con la unidad. **Una venta puede tener varios intakes** (§3.4 «Varios equipos»), por eso `order` NO es único; lo único es `stock_unit` (un equipo recibido nace de un solo intake) y `unique(company, idempotency_key)`. Índice único parcial por identificador entre intakes **abiertos** (`draft`/`pending_approval`/`approved`) para detectar duplicados antes de la unidad. |
| `created_by`, `created_at`, `updated_at` | Responsables. |

**`Order`** gana, con valor por omisión de base de datos (la versión anterior sigue insertando filas):
`trade_in_credit` (Decimal ≥ 0, 0 por defecto) y nada más. `payment_method` y `amount_received` conservan su
significado: **el medio y el monto del dinero**. Efectivo a cobrar = `total − trade_in_credit`.

**Movimiento de stock:** el equipo entra con `PURCHASE_ENTRY` (genera su lote de etiquetas), `reference_type='trade_in'`
y `reference_id=intake.pk`, y el motivo «Parte de pago · venta <n>». **No hay migración de opciones** y el equipo no
se enlaza con `StockUnit.order` ni con `SALE_EXIT`.

### 3.4 Cálculo y saldo

- `valuation` aprobada de todos los intakes del pago = `trade_in_credit`.
- `efectivo_a_cobrar = total − trade_in_credit`. Si `trade_in_credit > total`: **bloqueado** (no hay saldo negativo ni
  vuelto en especie) salvo política aprobada (D4).
- Con `efectivo_a_cobrar = 0` el método de pago de dinero queda vacío/«ninguno»: requiere un valor admitido (D5).
- Varios equipos: varios intakes en una misma venta; el crédito es la suma; cada uno se valoriza y aprueba por separado.

### 3.5 Orden de operaciones en la venta (una sola transacción)

1. El vendedor arma la canasta y abre «El cliente entrega un equipo como parte de pago».
2. Por cada equipo: se crea o reutiliza un intake `draft` con su `idempotency_key` (borrador editable; no toca stock).
3. Inspección y valorización: quien tiene la capacidad de **valorar** fija `valuation`; si excede el límite o el
   vendedor no tiene la capacidad, queda `pending_approval` hasta que alguien con la de **aprobar** lo apruebe. Pasa a
   `approved` (`pending_approval` → `approved` o `rejected`).
4. Confirmar la venta: `create_pos_sale` recibe las referencias a intakes `approved`, **vuelve a validar todo** bajo
   bloqueo (estado, empresa, sucursal, identificadores libres, crédito ≤ total, efectivo suficiente) y en la misma
   transacción: crea la orden con `trade_in_credit`, crea la unidad (`receive_units` con `PURCHASE_ENTRY`), la enlaza
   con el intake (`applied`) y registra la auditoría. Cualquier fallo revierte **todo**: ni unidad fantasma ni crédito
   aplicado.
5. La huella de idempotencia de la venta incluye los ids de los intakes; reintentar devuelve la misma venta; un intake
   `applied` no se aplica dos veces.

### 3.6 Permisos y aprobación (propuestas; D7)

- `sales.pos.use`: armar la canasta y **proponer** la entrega y su valoración.
- Capacidad nueva **`sales.tradein.value`**: fijar la valorización hasta el límite configurado de la empresa.
- Capacidad nueva **`sales.tradein.approve`**: aprobar por encima del límite o sin la capacidad anterior; reutiliza el
  patrón de la devolución (autoridades distintas), no un PIN.
- `inventory.adjust` **no** basta: recibir un equipo a cambio de crédito es una decisión comercial.
- `costs.view` sigue protegiendo el costo: la valorización se ve para quien valora; el costo en la ficha, solo con
  `costs.view`.
- La concesión inicial de las capacidades nuevas a roles existentes sigue el precedente de las migraciones de
  capacidades (sin ganar poder por defecto: D7).

### 3.7 Cancelaciones, devoluciones y fallos

| Caso | Qué ocurre |
|---|---|
| El cliente se arrepiente antes de confirmar | El intake pasa a `cancelled`. No hubo unidad, crédito ni movimiento. |
| El equipo no supera la inspección | `rejected` con motivo; no hay crédito; la venta puede seguir sin trade-in o con otro equipo. |
| IMEI repetido | Se detecta al crear el intake (índice parcial) y de nuevo al aplicar; si es de un equipo propio ya vendido, se resuelve con una regla explícita (D9), nunca duplicando. |
| La venta falla después de validar el equipo | Revierte todo: el intake vuelve a `approved` (reutilizable) o se cancela. Sin unidad ni crédito. |
| El pago complementario falla | Igual: la venta no existe, el intake sigue `approved`. |
| Se cancela la venta | Si la unidad sigue `AVAILABLE` en su sucursal: se da de baja con un movimiento compensatorio «Reversión de parte de pago» y el intake pasa a `reversed`; el crédito se devuelve según D8. Si la unidad ya se movió, reservó o vendió: **la cancelación se bloquea** y exige resolución manual trazada. |
| Se devuelve el equipo vendido | El reembolso máximo es el **dinero cobrado** (`total − trade_in_credit`) más lo que la política D8 diga sobre el crédito; el equipo recibido no se toca salvo reversión explícita. |
| Valorización mayor que la venta | Bloqueado (§3.4) salvo D4. |
| Varios equipos | Cada intake sigue su camino; la reversión es por intake. |

Ningún caso borra un movimiento: todo se compensa con operaciones oficiales (baja, movimiento inverso, auditoría).

### 3.8 Documentos

- **Venta:** nota de venta interna con el total íntegro, sin descuento falso; añade un bloque «Parte de pago» con
  cada equipo enmascarado (últimos 4 de serie/IMEI), la valorización aprobada, el crédito, lo pagado en dinero, el
  saldo y la referencia `TI-<id>` a la recepción.
- **Liquidación comercial interna** (documento nuevo, solo lectura, sin validez tributaria): venta, crédito, dinero,
  saldo, firmantes.
- **Comprobante fiscal:** no se modifica ninguna boleta, factura, cálculo de IGV ni XML sin la decisión D1 y la
  validación contable. Con `FISCAL_ENABLED=0` no hay emisión.
- El PDF, el ticket y ESC/POS hoy rotulan un único `payment_label`: debe cambiar para un pago mixto («Efectivo S/ x +
  parte de pago S/ y»).

### 3.9 Idempotencia y concurrencia

- Clave por intake y huella de la venta con los ids de intakes (§3.5).
- `select_for_update` sobre los intakes y bloqueo asesor por identificador (como `_lock_identifiers`) para que dos
  cajas no apliquen el mismo equipo.
- Respuesta perdida: reintentar con la misma clave devuelve la misma venta; ninguna unidad ni crédito se duplican.

### 3.10 Privacidad

- DNI y nombre del cliente son los de `Customer`; no se copian a otro lado más que en la instantánea de la venta ya
  existente. Respuestas y auditoría con ids y conteos, **nunca** IMEI, serie ni datos personales.
- Ningún IMEI se envía a un proveedor externo (la consulta de IMEI es manual hasta que el propietario apruebe una
  integración: D10).

### 3.11 Compatibilidad

- API web y v1: campos opcionales nuevos (`trade_in_intakes`); las claves desconocidas ya se ignoran; sin ellos, el
  comportamiento actual no cambia.
- Reembolsos y devoluciones: `collected` pasa a `total − trade_in_credit` para órdenes con crédito; sin crédito es
  idéntico.
- Contabilidad de lectura: el ingreso sigue siendo `Order.total`; se añade la columna «parte de pago» sin cambiar la
  cuenta.
- Comisión: se mantiene sobre subtotal − descuento hasta que D11 diga otra cosa.

### 3.12 La venta de caja nace pagada (corrección del contrato)

Una venta de caja se crea **ya pagada y entregada, en una sola transacción** (`create_pos_sale`: `PAID` + `DELIVERED`,
con el Kardex, las unidades y el margen escritos en ese mismo instante). Esto cambia lo que el crédito de un trade-in
puede significar y no se puede dejar para después:

1. **El crédito se liquida al cobrar, no se «difiere».** `trade_in_credit` forma parte de lo que cierra la venta; no hay
   un estado «pendiente de recibir el equipo» en una venta de caja. Por eso el equipo debe estar **inspeccionado,
   valorizado y aprobado ANTES** de confirmar (§3.5), y la unidad se crea en la misma transacción.
2. **Cancelación y devolución tienen que revisarse con el crédito dentro.** `order_cancellation._restore_stock` devuelve
   las unidades vendidas; con crédito hay además una unidad recibida que revertir (§3.7) y un dinero que ya no es
   `total` sino `total − trade_in_credit`. Ningún camino actual conoce esa resta: es parte obligatoria de TRADE-IN-01C,
   no un detalle.
3. **Nunca `manual_discount`.** Ni como atajo para «bajar el total», ni para absorber una diferencia de valorización:
   el descuento manual cambia base e IGV y exige otra capacidad (§3.2).
4. **La idempotencia del cobro incluye los intakes** (§3.5 paso 5) **sin** incluir precios: igual que INV-COM-01C, la
   huella es de la INTENCIÓN. Un cambio de valorización entre la vista previa y el cobro se trata como el cambio de
   precio de 01C: 409 explícito con el saldo nuevo, nunca un recálculo silencioso.
5. **Ninguna decisión fiscal se toma aquí.** Sin D1 resuelta no se toca boleta, factura, IGV ni XML.

## 4. Alternativas descartadas

1. `manual_discount` como crédito. 2. Valor nuevo en `PaymentMethod`. 3. `StockUnit.Status` nuevo para «en inspección».
4. Facturar solo el saldo. 5. Crear la unidad al empezar la inspección y «sacarla» si se rechaza.
6. Un libro de caja nuevo (no existe cierre de caja: fuera de alcance).

## 5. Decisiones pendientes (el contrato no está cerrado sin ellas)

| # | Decisión | Quién |
|---|---|---|
| D1 | Qué documento se emite y qué lleva (A, B o C de §2); cómo se anota la retoma; vigencia de SUNAT BETA | Propietario + contador |
| D2 | Sustento de compra del usado a un particular (Formulario 820, contrato, declaración jurada) y costo contable | Contador |
| D3 | Si la venta puede pagarse con un cliente anónimo o el trade-in exige cliente identificado | Propietario |
| D4 | Si el crédito puede igualar o superar el total, y qué pasa con el sobrante | Propietario |
| D5 | Valor del método de pago cuando todo el pago es en especie | Propietario / diseño |
| D6 | Datos mínimos del cliente que entrega (DNI, firma, declaración de propiedad) y su retención | Propietario + legal |
| D7 | Capacidades nuevas, límites de valorización por rol y quién aprueba | Propietario |
| D8 | Qué se devuelve al cliente cuando se cancela o devuelve una venta con crédito (dinero, crédito, nada) | Propietario |
| D9 | Equipo que vuelve y ya figuraba como vendido por esta tienda | Propietario |
| D10 | Consulta de IMEI: manual o integrada; qué se hace con un IMEI en lista negra descubierto después | Propietario + legal |
| D11 | Base de la comisión del vendedor con crédito | Propietario |
| D12 | Si el equipo recibido se vende al aprobarse o solo tras revisión técnica | Propietario |

## 6. Plan de implementación

1. **INV-COM-01A / 01B** (recepción comercial): precio propio y detalles por seminuevo y destino comercial. **Hecho en
   código** (ver `02_ESTADO_ACTUAL.md`).
2. **INV-COM-01C — la caja precia por unidad.** **Hecho en código** (§1.2): prerrequisito cumplido para vender seminuevos
   con su precio propio, con o sin trade-in.
3. **TRADE-IN-01B — recepción y valorización.** `TradeInIntake`, servicios, aprobación y entrada a stock; sin integrar
   la venta. Migración propia. Se implementa **después** de cerrar D2, D3, D6, D7, D9 y D10.
4. **TRADE-IN-01C — caja, crédito, documentos y reversión.** `trade_in_credit`, huella, reembolsos, documentos
   internos. Después de D1, D4, D5, D8 y D11; la emisión fiscal, solo con D1 y validación contable.

## 7. Pruebas obligatorias (RED → GREEN, cuando se implemente)

Una unidad y dos unidades de parte de pago; rechazo por inspección; valoración no autorizada y aprobada por otro;
IMEI repetido (entre intakes y contra la tabla de unidades); venta fallida sin unidad ni crédito; pago complementario
fallido; reintentos y doble clic sin crédito doble; respuesta perdida; cancelación con unidad disponible y con unidad
ya movida; devolución del equipo vendido; crédito mayor que la venta; dos empresas y dos sucursales; permisos
revocados a mitad; comprobante interno coherente; regresión de POS, caja, e-commerce y servicio técnico; API v1 sin
los campos nuevos; E2E con datos sintéticos en Chromium, móvil y WebKit.

## 8. Qué NO se verificó

Pantallas del POS en el frontend, exportaciones de pedidos y de Kardex, disparadores de notificaciones del POS,
internos de `unit_identification.find_unit`, detalles del modelo `SalesNote`, la paridad de pruebas v1, ningún Informe
SUNAT sobre retomas, el deber de registro de compraventa de usados, la vigencia del artículo 5 del D.Leg. 1338 y el texto
oficial del artículo 194 del Código Penal tras el D.Leg. 1578.

## 9. Fuentes (consultadas el 2026-10-30)

Primarias: TUO de la Ley del IGV (sunat.gob.pe/legislacion/igv/ley/capitul1.pdf y capitul5.pdf); Reglamento de la Ley del
IGV, cap. IV art. 5 (…/igv/regla/cap4.htm); Reglamento de Comprobantes de Pago, art. 4 y 6 (…/comprob/regla/capituloI.pdf
y capituloII.pdf); Oficio 004-2007-SUNAT/200000; R.S. 193-2020/SUNAT, Anexo IV (…/superin/2020/anexo4-193-2020.pdf);
TUO de la Ley 28194 (…/itf/ds150_07.pdf); D.Leg. 1338 y D.S. 007-2019-IN (copias en osiptel.gob.pe); nota de OSIPTEL del
3.5.2024. Secundarias, con reserva: bybconsultores.pe (retención de Renta, detracciones), jurispol.pe (estado del art.
5 del D.Leg. 1338), un blog sobre el Formulario 820. No leídos: Informes 092-2021 y 039-2022-SUNAT/7T0000, apéndice 1
de la Ley 29173, anexo 2 de la R.S. 183-2004/SUNAT, TUO de la Ley del Impuesto a la Renta.
