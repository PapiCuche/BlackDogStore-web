# ADR — FINANCE-OBLIGATIONS-01 · Pagos fijos, cuentas por pagar, cuentas por cobrar y recordatorios por WhatsApp

Estado: **PROPUESTA** (fase FIN-A: auditoría y diseño, sólo documentación). Nada de lo descrito en §3 en adelante
está implementado. Ninguna fase de este ADR toca producción, credenciales reales de WhatsApp/Meta, bancos ni
pasarelas.

Orden de autoridad: código > migraciones > tests > docs. Las referencias `archivo:línea` son sobre `master` en
`1792f83` (hoja de migraciones `0154_auth_session`). Sin prefijo, las rutas son de `backend/store/`.

## 0. Qué es y qué no es

- Es un **control interno de obligaciones**: lo que la empresa debe pagar (pagos fijos y proveedores) y lo que sus
  clientes le deben, con su vencimiento, sus pagos, su historial y sus recordatorios.
- **Lo pendiente nunca desaparece por vencer.** Una obligación sólo sale de «Pendientes» cuando su saldo es cero
  (pagada), cuando se anula con motivo o cuando se corrige por un procedimiento autorizado y auditado. No hay
  caducidad, limpieza ni archivado automático.
- **Un recordatorio no es un movimiento.** Leer, cerrar o descartar un aviso no cambia saldo ni estado. Enviar un
  WhatsApp no registra un cobro.
- **No es contabilidad completa** (igual que el Centro Contable, `docs/adr-accounting.md` §0): no hay asientos, libro
  mayor, bancos, conciliación bancaria ni crédito fiscal. Un pago registrado **no** es un gasto deducible ni prueba
  ante SUNAT: lo es el comprobante del proveedor, y SUNAT sigue separado.
- **No emite comprobantes.** Registrar un cobro no genera boleta, factura ni nota de venta.
- **Multiempresa:** categorías, textos, plantillas, horarios y políticas salen de la configuración de cada empresa.
  Nada propio de Black Dog Store en código global (`AGENTS.md` §2).

## 1. Auditoría del estado actual

### 1.1 Resumen

| Ámbito | ¿Existe? | Dónde | Clasificación |
|---|---|---|---|
| Pagos fijos / recurrencias | **No** | — | PENDIENTE |
| Cuentas por pagar (PAYABLES-01) | Sólo un gancho de lectura | `supply_services.py:963-998`, `supplier_views.py:157-177` | PARCIAL (informativo) |
| Factura de proveedor, orden de compra, recepción de compra | **No** | — (`docs/adr-accounting.md:53`, `:254`) | BLOCKED en contabilidad, PENDIENTE aquí |
| Cuentas por cobrar | **No** | `frontend/app/admin/lib/internal-modules.ts:228` («pending») | PENDIENTE |
| Saldo de reparación (adelanto + saldo) | Sí, libro de pagos | `models.py:7292` `RepairPayment`, `service_services.py:3186` | IMPLEMENTADO |
| Venta al crédito / cuotas / vencimiento de venta | **No** | `pos_services.py:1286-1287` (toda venta de caja nace pagada); `fiscal/builder.py:73-75` (`FormaPago` siempre `Contado`) | NO EXISTE |
| Cliente | Sí | `models.py:4204` `Customer` | IMPLEMENTADO |
| Consentimiento WhatsApp | Sí, un solo alcance («avisos») | `models.py:4272-4284`, `whatsapp_services.py:202-230` | PARCIAL para cobranza |
| Cliente WhatsApp Cloud API | Sí, sólo plantillas | `messaging/whatsapp_cloud.py:37` | IMPLEMENTADO |
| Bandeja de salida (outbox) | Sí | `models.py:7488` `NotificationEvent`, `:7537` `Notification`, `:7648` `NotificationDelivery` | IMPLEMENTADO |
| Planificador | Cron del servidor + comandos | `deploy/crontab.example:20-60` | IMPLEMENTADO |
| Archivos privados | Sí (imágenes) | `evidence_storage.py`, `evidence_images.py`, `evidence_views.py` | IMPLEMENTADO; PDF sin validador |
| Secuencias internas | Sí | `models.py:3940`, `sequences.py:248` | IMPLEMENTADO |
| Centro Contable | Sí, sólo lectura | `accounting/`, `urls.py:506-510` | IMPLEMENTADO (ventas e ingresos) |

### 1.2 Trabajo previo de PAYABLES-01 / cuentas por cobrar

- No hay rama ni PR de PAYABLES-01 ni de cuentas por cobrar (`gh pr list --state all`, `git branch -a`, 2026-10-25).
  Lo único relacionado y fusionado: #111 (CLIENT-SEC-01 / DELIVERY-01), #126 (`fix/financial-closure`) y #139.
- Documentos: `02_ESTADO_ACTUAL.md:289-290` (cuentas por cobrar y PAYABLES-01 «PENDIENTE»),
  `05_DECISIONES_TECNICAS.md:2449` (sólo el gancho), `docs/NEXT_ACTION.md:185` (orden ORDER-LIFECYCLE-01 →
  PAYABLES-01), `docs/adr-accounting.md:254` (BLOCKED) y `:723` (P11: orden de PAYABLES-01, gastos y bancos).
- Catálogo de módulos del panel (metadatos de interfaz, **no** autorización, `internal-modules.ts:1-41`):
  `sales.receivables` pendiente (`:228`), `purchases.payables` parcial sin pantalla (`:250-258`),
  `purchases.orders` y `purchases.receipts` pendientes (`:248-249`), `cash.*` pendiente (`:241-243`).

### 1.3 Proveedores y `pending_settlement`

- `Supplier` (`models.py:10844`): empresa, nombre único por empresa sin mayúsculas (`:10869`), documento, contacto,
  teléfono, correo, `is_active`. Nunca se borra.
- `SupplierOffer` (`models.py:10876`): `agreed_cost` (`:10894`, sólo `costs.view`), cantidades, vigencia.
- La **única recepción de proveedor** es por línea de pedido al pase: `supply_services.receive_line`
  (`supply_services.py:676`), que congela costo acordado o real (`:801-812`) y deja Kardex. La entrada genérica de
  stock (`StockMovement.PURCHASE_ENTRY`, `models.py:1587`; `stock_unit_views.py:205`) **no tiene proveedor**.
- `pending_settlement(company, branch_ids=None)` (`supply_services.py:963-998`): suma por proveedor las líneas
  `supply_origin=supplier`, `supply_status=received`, de pedidos `paid` o `cancelled` (lo recibido se debe aunque
  el pedido se cancele, `:973-975`), al costo **acordado** congelado (`unit_cost`), y cuenta aparte las unidades sin
  costo. Su docstring lo declara «PAYABLES-01 hook, READ ONLY… No debt is written anywhere» (`:964-967`).
- Endpoint `AdminSupplierSettlementView` (`supplier_views.py:157-177`, ruta `urls.py:266`): `products.view` +
  `user_may_view_costs` (`cost_services.py:196`), acotado por `visible_branch_ids`. Pantalla
  `SuppliersManager.tsx:310-360`: «Es informativo: no registra una deuda ni un pago» (`:329-330`).
- **Conclusión:** `pending_settlement` sigue siendo informativo. Ninguna fase de este ADR convierte recepciones en
  deudas automáticamente (§5).

### 1.4 Reparaciones: `RepairPayment` y servicios de cobro

- `RepairOrder` (`models.py:5642`): sin campos de dinero, sin FK a `Order` (`:5646-5651`); `DELIVERED` no significa
  pagado (`:5559-5564`).
- `RepairQuote` (`models.py:6073`): estados `draft/sent/approved/rejected/cancelled/superseded` (`:6097-6113`),
  importes congelados (`:6138-6147`), «Approval authorises work. It settles nothing» (`:6094`).
- `RepairPayment` (`models.py:7292`): libro **sólo de altas** (`save()` congela todo salvo la reversión,
  `:7432-7456`; `delete()` lanza, `:7458`); `amount > 0` (`:7380-7384`), idempotencia única por empresa
  (`:7373-7377`), sin método `online` (`:7391-7394`); la reversión es un **sello** en la fila, no una fila negativa
  (`:7319-7330`).
- Saldo: `financial_quote` (`service_services.py:3149`, la última revisión aprobada, nunca suma revisiones),
  `_net_paid` (`:3170`, suma de no reversados), `service_payment_summary` (`:3186-3241`: `quoted_total`,
  `confirmed_paid`, `outstanding` con piso 0, `credit`, `payment_status` ∈ `no_quote/unpaid/partial/paid/overpaid`).
  Nada se guarda en caché.
- Registro: `record_service_payment` (`:3264`) → `_record_service_payment` (`:3305`), `@atomic`, bloquea la orden
  (`:3318`), mira la clave de idempotencia antes que las reglas (`:3331-3340`), rechaza sin cotización aprobada
  (`:3352-3356`) y el sobrepago (`:3358-3365`), audita `service_payment_recorded` (`:3393`).
- **Hallazgo H-1 (concurrencia):** `reverse_service_payment` (`:3415-3459`) bloquea sólo la fila del pago (`:3434`),
  **no** la `RepairOrder`. El comentario de la entrega (`service_services.py:3009-3012`) supone que el bloqueo de la orden serializa una
  entrega con una reversión; no es así. Se corrige en un PR `fix/` propio antes de FIN-E (§10).
- **Hallazgo H-2 (documentación):** el resumen dice que se puede cobrar un depósito antes de cotizar (`:3208-3209`),
  pero `_record_service_payment` lo rechaza (`:3352-3356`). Hoy un adelanto exige cotización aprobada.
- Capacidades: `service.payments.manage` (registrar y revertir, `capabilities.py:362`), `service.payments.collect`
  (sólo registrar, `:369`); vistas `v1_service_views.py:1888-2007`.

### 1.5 Pedidos, caja y crédito

- `Order` (`models.py:452`): `status` (`:453-459`), `paid`/`paid_at` (`:678`, `:698`), `total`. Sin saldo, sin
  vencimiento, sin crédito. `PaymentTransaction` (`models.py:1026`): un intento de pasarela, sin columna de empresa.
- Caja: `create_pos_sale` (`pos_services.py:1115`) escribe siempre `status=PAID`, `paid=True` (`:1286-1288`).
- **No existe la venta al crédito.** El XML fiscal emite siempre `Contado` (`fiscal/builder.py:73-75`, `:334-336`);
  las reglas de SUNAT para crédito (cuotas, monto pendiente) sólo están documentadas (`docs/sunat-cpe-requisitos.md:466-478`)
  y pertenecen a FISCAL-PROD-01 (#173). Consecuencia: el origen «venta al crédito» de cuentas por cobrar queda
  **BLOCKED** hasta que exista una venta al crédito real; **una venta pagada de caja nunca se convierte en deuda**.

### 1.6 Cliente

- `Customer` (`models.py:4204`): empresa (PROTECT), `user` opcional, documento, `phone`, `email`, `is_active`.
  Teléfono no único, sin verificación de ningún tipo (sólo formato: `normalize_customer_phone`, `models.py:4181`;
  `messaging/phones.py:18` `to_wa_id`, que nunca adivina país).
- Vínculos: `Order.customer` FK opcional con instantánea (`models.py:550`); `RepairOrder.customer` FK sin
  instantánea (`:5677`). Deduplicación determinista por cuenta o documento, sin fusión (`customer_services.py:73-106`).

### 1.7 WhatsApp

- Capas: `whatsapp_services.py` (outbox, consentimiento, webhook) → `messaging/__init__.py` (configuración por
  empresa: consola de integraciones primero, `:54`; proveedor `provider_for`, `:116`) → `messaging/whatsapp_cloud.py`
  (HTTP Graph, `send_template`, `:37`; tiempo de espera 8 s, `:22`; reintentables `:25`, `:98`; un timeout no se
  reintenta, `:67-75`).
- **Sólo plantillas**, sin texto libre (`models.py:9959-9961`). `CompanyMessagingSettings` (`models.py:9917`):
  `whatsapp_enabled` (`:9942`), `whatsapp_templates` {evento: nombre} (`:9961`), `template_language` (`:9958`),
  `default_calling_code` (`:9957`).
- Eventos elegibles: los cinco de reparación (`whatsapp_services.py:62-69`); `_eligible` exige
  `target_type == 'repair_order'` (`:235-243`); parámetros fijos nombre/orden/enlace (`:72`, `:305-311`); `deliver`
  vuelve a leer la orden de reparación (`:347-349`). **Hay que generalizar el destino** (§7.3), no crear otro cliente.
- Idempotencia: `deliver` bloquea la fila de entrega (`:335`), vuelve si ya salió (`:340`) y relee consentimiento,
  plantilla y teléfono (`:351-361`). Reintentos: 5 intentos, espera 1 m/5 m/30 m/2 h (`:54-56`, `_fail` `:314`).
- **Sin límite de envíos salientes** (sólo el webhook entrante, `throttles.py:407`) y **sin horario permitido**.
- Baja por mensaje: palabras `baja/stop/alto/cancelar/unsubscribe` (`:76`), `apply_inbound` (`:489-504`).
- Consola: `integration_views.py` (sólo MASTER, `:29`); proveedor `integrations/providers/whatsapp.py:27`; secretos
  cifrados (`integrations/secret_store.py`). Ajustes de la empresa: `whatsapp_views.py:81` (`settings.view` /
  `settings.manage`), consentimiento `:112-141` (`service.customers.manage`, siempre `source='counter'`).
- Webhook de estados: `apply_status_report` (`:444`), sólo hacia delante (`_RANK`, `:441`).

### 1.8 Notificaciones y planificador

- `NotificationEvent.event_key` **único** (`models.py:7517`): la clave de idempotencia de un evento.
  `event_key(event_type, target_type, target_id, discriminator)` (`notification_events.py:93`).
- `emit(...)` (`notification_services.py:180`) corre dentro de la transacción del negocio; una clave repetida
  devuelve `None` (`:208-218`); WhatsApp se encola con `_queue_whatsapp` (`:240`).
- `Notification` (`models.py:7537`): audiencia interna/cliente, `target_type`/`target_id`, `read_at`.
  `NotificationDelivery`: única por (notificación, canal) (`models.py:7718-7720`).
- Planificador: **cron del servidor**, sin Celery/RQ/beat (`docker-compose.prod.yml`: caddy, frontend, backend,
  postgres). `send_pending_notifications` cada minuto (`deploy/crontab.example:33`), `expire_supplier_offers` cada
  hora (`:56`), `ops_status` vía `healthcheck.sh` (`:60`; avisa de WhatsApp atascado, `management/commands/ops_status.py:170-195`).
  Concurrencia por bloqueo de fila + estado; sin bloqueos consultivos ni `skip_locked`.

### 1.9 Capacidades, sucursal, auditoría, archivos, zona horaria

- Catálogo en código (`capabilities.py:64-385`); `has_capability` lanza con un código desconocido
  (`tenancy.py:486`); preset Administrador = todo lo asignable (`company_provisioning.py:269-271`). Patrón de
  migración de datos que concede capacidades: `migrations/0149_accounting_capabilities.py`,
  `0152_label_capabilities.py`.
- Existentes relevantes: `costs.view` (`capabilities.py:131`), `accounting.view`/`accounting.export` (`:222-228`),
  `service.payments.*` (`:362`, `:369`), `settings.view`/`settings.manage`, `service.customers.manage`.
  **No hay ninguna capacidad `finance.*`.**
- Sucursal: `has_company_wide_scope` (`tenancy.py:1403`), `visible_branch_ids` (`:1555`), `assert_branch_access`
  (`:1567`). Auditoría: `AdminAuditLog.log(..., company=)` (`models.py:1417`).
- Archivos privados: clave generada en el servidor (`evidence_storage.py:52`), S3/R2 privado o disco sin URL pública
  (`:76-121`), URL firmada de 300 s (`:161`), validación por decodificador (`evidence_images.py:23-25`, `:143`), tope
  por ruta (`request_limits.py:61-89` + `deploy/Caddyfile`), servir re-autorizando y 404 entre empresas
  (`evidence_views.py:90-122`). **No hay validador de PDF subido.**
- Zona horaria: `TIME_ZONE='America/Lima'`, `USE_TZ=True` (`backend/backend/settings.py:420-422`);
  `CompanySettings.timezone` opcional (`models.py:3704`); `_company_today` / `_day_bounds`
  (`sales_analytics_views.py:89`, `:94`), reutilizados por `accounting/periods.py:63`.
- Secuencias internas: `InternalSequence` (`models.py:3940`), `allocate` con bloqueo de fila (`sequences.py:248`);
  añadir un tipo es un `AlterField` de opciones (`migrations/0146_internal_sequence_product_code.py`).

## 2. Decisiones

| ID | Decisión |
|---|---|
| D1 | Un **núcleo común** de obligaciones para pagar y cobrar (`direction`), con un solo motor de saldo, bloqueo, recordatorios y calendario. |
| D2 | La **regla** de recurrencia no es deuda. Cada **período** es una `Obligation` propia, con identidad estable (`rule`, `period_start`) única. |
| D3 | El **saldo se deriva** de movimientos confirmados (pagos no revertidos y ajustes). No se edita un saldo. |
| D4 | Pagos y ajustes son **sólo de altas**. La reversión es un sello con motivo, como `RepairPayment`. Nada confirmado se borra. |
| D5 | Concurrencia: `select_for_update` de la obligación antes de leer el saldo; idempotencia por clave única; generación idempotente por restricción única. |
| D6 | Reparaciones: la cuenta por cobrar es una **proyección** de `RepairOrder`; el dinero sigue en `RepairPayment`. No hay segundo libro. |
| D7 | Venta al crédito: **BLOCKED** hasta que exista. Una venta pagada nunca es deuda. |
| D8 | Proveedores: la cuenta por pagar se crea **a mano** desde el documento del proveedor; las líneas recibidas se vinculan explícitamente, una línea a lo sumo a una obligación. |
| D9 | Recordatorios: se calculan **justo a tiempo** por un comando en el cron existente; no se crean de antemano. Idempotencia por `NotificationEvent.event_key`. |
| D10 | WhatsApp: se generaliza `whatsapp_services` a destinos `finance_obligation`; mismo cliente, misma outbox, mismo webhook. Envío automático **apagado** por defecto. |
| D11 | Consentimiento de cobranza **propio**, con historial y teléfono asociado. El consentimiento de «avisos» actual **no** se da por válido para cobranza. |
| D12 | Permisos nuevos `finance.*`, separados por acción; se reutilizan `costs.view`, `service.payments.*`, `accounting.*`, `settings.manage`. |
| D13 | Fechas de vencimiento son fechas **locales** de la empresa (Lima por defecto). |

Todas son PROPUESTA hasta la respuesta del propietario (§12).

## 3. Modelo de datos

Nombres provisionales. Todos llevan `company` (PROTECT) y se filtran por la empresa resuelta en el servidor.

### 3.1 Vista general

```
FinanceCategory ──┐
                  │
RecurringRule ────┼──< Obligation (un período o una deuda) >── ObligationPayment (altas + sello de reversión)
  (sólo pagar)    │        │      │                         └─ ObligationAdjustment (altas, con motivo)
                  │        │      └── ObligationAttachment (privado)
Supplier/Customer ┘        │
                           └── NotificationEvent/Notification/NotificationDelivery (recordatorios, existentes)
RepairOrder ── (proyección) Obligation[origin=repair] ── dinero en RepairPayment (existente)
```

### 3.2 `FinanceCategory`

- `company`, `direction` (`payable`/`receivable`), `name`, `is_active`, `created_by`. Único (`company`, `direction`,
  `lower(name)`). Nunca se borra; se desactiva.
- Al crear una empresa **no** se siembran categorías en código global. El propietario puede pedir una lista
  sugerida editable (alquiler, servicios, internet, teléfono, suscripciones, servicios profesionales,
  mantenimiento, otros) que se ofrece **en la pantalla** como plantilla opcional (P2).

### 3.3 `RecurringObligationRule` (pagos fijos)

- Campos: `company`, `branch` (nula = general), `concept`, `payee_supplier` (FK opcional a `Supplier`) o
  `payee_name`, `category`, `amount` (> 0), `currency` (por defecto la de `CompanySettings.currency`),
  `frequency` (`once`, `weekly`, `monthly`, `quarterly`, `yearly`), `due_day` (día del mes 1–31; día de la semana
  para semanal), `start_date`, `end_date` (opcional), `owner` (usuario responsable), `planned_method`,
  `reference`, `notes`, `paused_at`/`paused_by`/`pause_reason`, `ended_at`/`end_reason`, `created_by`.
- **Calendario inmutable tras el primer período generado:** `frequency`, `due_day` y `start_date` no se editan;
  cambiar el calendario = terminar la regla y crear otra (auditado). Así la identidad de los períodos no se mueve.
- `amount` sí se edita, con auditoría, y **sólo afecta a los períodos que se generen después**. Los ya generados no
  cambian (sin efecto retroactivo).
- Día 29–31 en un mes corto → último día del mes. Trimestral y anual se anclan al mes de `start_date`.

### 3.4 `Obligation` (un período o una deuda)

| Campo | Notas |
|---|---|
| `company`, `branch` | `branch` nula = general (sólo con alcance de toda la empresa). |
| `direction` | `payable` / `receivable`. |
| `origin_type` | `fixed` (de una regla), `supplier_document`, `manual`, `repair`, (`credit_sale`, reservado, BLOCKED). |
| `origin_key` | Clave de deduplicación por origen (§3.7). Única por empresa cuando no está vacía. |
| `rule`, `period_start` | Sólo `fixed`. **Único (`rule`, `period_start`)**: el mismo período no se genera dos veces. |
| `supplier`, `customer`, `payee_name` | Contraparte; `customer` y `supplier` de la misma empresa (`clean()` y servicio). |
| `category`, `concept`, `owner`, `planned_method`, `notes` | |
| `reference` | Referencia interna no sensible (`InternalSequence`, tipo nuevo `finance_obligation`, p. ej. `OB-000123`). Es la que viaja en WhatsApp. |
| `external_reference` | Número del documento del proveedor u otro; **nunca** viaja por WhatsApp. |
| `amount_original`, `currency` | `amount_original > 0`; congelado al crear. |
| `issue_date`, `due_date` | `DateField` local de la empresa. `due_date` nula sólo en reparaciones sin vencimiento todavía (§6.2). |
| `lifecycle` | `open` / `settled` / `voided`. Sólo se cambia dentro de la transacción bloqueada de un pago, ajuste o anulación. |
| `settled_at`, `voided_at`, `voided_by`, `void_reason` | Anular exige motivo (CHECK). |
| `created_by`, `created_at`, `idempotency_key`, `request_fingerprint` | Alta manual idempotente. |

**Estado visible (derivado, nunca guardado):**

| Estado | Regla (hoy = fecha local de la empresa) |
|---|---|
| Anulada | `lifecycle = voided` |
| Pagada | `lifecycle = settled` (saldo 0) |
| Vencida | abierta, saldo > 0, `due_date < hoy` |
| Vence hoy | abierta, saldo > 0, `due_date = hoy` |
| Por vencer | abierta, saldo > 0, `hoy < due_date ≤ hoy + N` (N por empresa, 7 por defecto) |
| Parcialmente pagada | marca adicional cuando hay pagos no revertidos y saldo > 0 |
| Pendiente | el resto de abiertas con saldo > 0 |

Invariante probado: `lifecycle = settled` ⇔ saldo = 0 y no anulada. Un informe de consistencia (comando de sólo
lectura, como `ops_status`) lo vigila.

### 3.5 `ObligationPayment` y `ObligationAdjustment`

- `ObligationPayment`: `obligation`, `amount > 0`, `currency` (= la de la obligación), `method`, `paid_on` (fecha
  local) y `recorded_at`, `reference`, `notes`, `recorded_by`, `idempotency_key` (único por empresa si no vacío),
  `request_fingerprint`, `reversed_at`/`reversed_by`/`reversal_reason` (CHECK: revertido ⇒ motivo). `save()` congela
  todo salvo el sello; `delete()` lanza (copia del patrón `models.py:7432-7458`). Misma regla anti-tarjeta en
  `reference` (`models.py:7408-7430`).
- `ObligationAdjustment`: `kind` ∈ `increase` (cargo adicional), `decrease` (descuento/corrección), `write_off`
  (castigo de incobrable, sólo cobrar), `amount_change` (importe variable de **un** período de pago fijo: guarda
  importe anterior y nuevo y se traduce en `increase`/`decrease`), `amount > 0`, `reason` obligatorio, `actor`,
  `created_at`. Sólo de altas. Un ajuste equivocado se compensa con otro ajuste de signo contrario, con motivo.
- No hay importes negativos arbitrarios: el signo lo da `kind`, nunca el número.

### 3.6 Saldo y concurrencia

```
efectivo = amount_original + Σ increase − Σ decrease − Σ write_off
pagado   = Σ pagos no revertidos
saldo    = efectivo − pagado          (nunca < 0: se rechaza antes de escribir)
```

- **Sin sobrepago silencioso:** un pago mayor que el saldo se rechaza con el saldo en el mensaje (como
  `service_services.py:3358-3365`). Un `decrease` que dejaría el saldo bajo lo pagado se rechaza; si el propietario
  quiere registrar un saldo a favor, se decide aparte (P6).
- **Registrar un pago:** `atomic` → `Obligation.select_for_update()` → si la clave de idempotencia ya existe,
  devolver ese pago (409 si la huella difiere) → `lifecycle = open` → recalcular el saldo **bajo el bloqueo** →
  validar → insertar en un *savepoint* (la carrera de la clave única se resuelve como `_PaymentRaceLost`,
  `service_services.py:3264`) → si saldo = 0, `lifecycle = settled` y cancelar recordatorios pendientes (§7.6) →
  `AdminAuditLog` con `company`.
- **Revertir:** bloquea **primero la obligación** y después el pago (evita H-1); idempotente; puede reabrir
  (`settled → open`), lo que vuelve a activar recordatorios.
- **Orden de bloqueo único:** obligación → pagos/ajustes → entregas de WhatsApp. `deliver` para destinos de
  finanzas lee la entrega sin bloquear, bloquea la obligación y después la entrega (§7.5). Con reparaciones:
  `RepairOrder` → obligación proyectada.
- Lecturas de listas: el saldo se anota con subconsultas (`Subquery`/`Sum` filtrado), sin columna de caché. Si el
  rendimiento lo pide, se añade una caché con prueba de invariante, no antes.

### 3.7 Claves de deduplicación por origen (`origin_key`)

Mismo criterio que el Centro Contable (`accounting/operations.py:169-170` `sale:<id>`, `:342` `repair:<id>`): una
operación del negocio ⇒ **como máximo una** obligación, aunque aparezca en varios modelos.

| Origen | `origin_key` | Restricción |
|---|---|---|
| Pago fijo | vacía; identidad = (`rule`, `period_start`) | único (`rule`, `period_start`) |
| Documento de proveedor | `supdoc:<supplier_id>:<tipo>:<serie-número normalizado>` | único (`company`, `origin_key`) |
| Reparación | `repair:<repair_order_id>` | único (`company`, `origin_key`) |
| Venta al crédito (futuro) | `sale:<order_id>` | único; BLOCKED |
| Manual | `manual:<uuid>` generado en el servidor | idempotencia por `Idempotency-Key` |

### 3.8 Adjuntos privados (`ObligationAttachment`)

Patrón de evidencias (§1.9): clave `companies/<cid>/finance/<obligation_id>/<uuid>.<ext>` generada en el servidor;
modelo con `storage_key` único, `mime_type`, `byte_size`, `sha256`, `uploaded_by`, anulación con motivo e
idempotencia; tope por ruta en `request_limits.tiers()` **y** en `deploy/Caddyfile`; servir re-autorizando empresa,
sucursal y capacidad, 404 entre empresas, `no-store`; auditoría sin clave ni URL. Imágenes por
`evidence_images.process`. **PDF:** no hay validador; FIN-B3 decide uno estructural (cabecera, estructura
parseable, sin JavaScript embebido) y, si exige una dependencia nueva, la justifica en su PR. Sin PDF válido, sólo
imágenes.

## 4. Pagos fijos (FIN-B)

- **Generación:** comando `generate_finance_obligations` (o una fase de `run_finance_schedule`, §6.4) que, por
  empresa y regla activa, crea los períodos cuyo `due_date` cae dentro del horizonte (por defecto hoy + 35 días,
  local). `get_or_create` sobre (`rule`, `period_start`) en un *savepoint*; un `IntegrityError` por la carrera con
  otro proceso se trata como «ya existía». Dos procesos a la vez producen **un** período.
- **Nunca se borra un período anterior.** Terminar o pausar una regla no toca los períodos generados.
- **Pausa:** `paused_at` detiene la generación de períodos con `period_start` posterior. Los abiertos siguen
  abiertos y vencen. Al reanudar, por defecto **no** se rellenan los períodos del tiempo en pausa (P4); hay una
  acción manual «Generar este período» si hace falta.
- **Importe variable:** «Cambiar el importe de este período» = `ObligationAdjustment(kind=amount_change)` con motivo,
  sólo sobre ese período. Cambiar el importe de la regla afecta sólo a los futuros.
- **Pago único** (`once`): una regla con un único período, o una obligación manual sin regla; ambas valen. Se
  propone obligación manual sin regla (más simple).

## 5. Cuentas por pagar a proveedores (FIN-D)

- Alta a mano desde el documento del proveedor: `supplier`, tipo y serie-número (→ `origin_key`), fecha de emisión,
  vencimiento, importe, moneda, adjunto. El número del documento es `external_reference`.
- **Vínculo con lo recibido, explícito:** tabla `ObligationSupplyLine(obligation, order_item)` con **único
  (`order_item`)**: una línea recibida se liquida a lo sumo en una cuenta por pagar. La pantalla propone las líneas
  de `pending_settlement` del proveedor (sólo con `costs.view`) y la persona elige. Nada se crea solo.
- `pending_settlement` sigue siendo informativo y pasa a restar lo ya vinculado (columna «vinculado a una cuenta
  por pagar») sin dejar de ser lectura.
- **Pago fijo y factura del mismo proveedor (doble conteo):** si la regla tiene `payee_supplier` y llega la factura
  de ese período, la factura **se vincula al período existente** (rellena `external_reference` y adjunto) en vez de
  crear otra obligación. Al dar de alta un documento de un proveedor con un período fijo abierto del mismo mes, el
  servidor avisa y ofrece vincular; crear otra exige confirmar «no es el mismo cargo» (auditado). La unicidad de
  `origin_key` impide el mismo documento dos veces, también entre los dos tipos.
- Entradas genéricas de stock (`PURCHASE_ENTRY`) no tienen proveedor: no se vinculan en esta etapa (P9).
- Un pago a proveedor no es un gasto válido ante SUNAT; la cuenta muestra «sin comprobante del proveedor» si no
  hay documento ni adjunto.

## 6. Cuentas por cobrar (FIN-E)

### 6.1 Orígenes de la primera etapa (cerrado)

| Origen | Cuándo existe | Cuándo **no** |
|---|---|---|
| 1. Saldo de reparación | Orden con cotización **aprobada** vigente (`financial_quote`) y `outstanding > 0` | Cotización rechazada, cancelada, enviada sin decidir, borrador; propuesta no aprobada; orden sin cotización |
| 2. Venta al crédito | **BLOCKED**: no existe (§1.5) | Una venta pagada de caja o web; un pedido sin pagar (`pending_payment`, `failed`, `expired`); un carrito abandonado |
| 3. Manual | Alta justificada (motivo obligatorio, cliente obligatorio de la empresa, permiso `finance.receivables.manual`) | Sin motivo o sin cliente |

### 6.2 Reparación: proyección, no segundo libro

- `Obligation(origin_type=repair, origin_key=repair:<id>, customer=repair.customer, branch=repair.branch)`.
  `amount_original` = total de la cotización aprobada vigente; los pagos son los `RepairPayment` (no hay
  `ObligationPayment` para este origen). El saldo se toma de `service_payment_summary` (§1.4).
- Se crea o actualiza **en la misma transacción** que aprueba la cotización, registra o revierte un pago de
  reparación (ganchos en `service_services`, con el orden de bloqueo `RepairOrder → Obligation`). Un comando de
  conciliación de sólo lectura detecta desajustes.
- **Re-cotización** (`reopen_approved_quote`, `service_services.py:1651`): mientras no haya nueva aprobación, la
  obligación queda abierta en estado «en revisión de cotización», sin recordatorios a cliente. Nunca se anula sola.
- **Sobrepago** (`overpaid`, saldo a favor): la obligación queda «pagada» y el crédito se muestra aparte; no hay
  devolución automática (`docs/saas-multiempresa.md:4033-4038`).
- **Vencimiento:** una reparación no tiene fecha de vencimiento. Propuesta: `due_date` = fecha local en que la orden
  llega a «Listo para recoger» (o se entrega con saldo) + N días configurables (0 por defecto). Antes de eso, «sin
  vencimiento»: aparece en pendientes pero nunca como vencida (P5).
- Registrar el cobro desde «Finanzas» llama a `record_service_payment` y exige las capacidades de servicio
  existentes (`service.payments.collect`/`manage`). Una sola autoridad para un solo libro.

### 6.3 Ajustes, anulación, castigo, reembolso

- Ajustes y castigos: `ObligationAdjustment` con motivo y permiso `finance.adjust`. Anular: `finance.void`, motivo
  obligatorio, sólo con saldo pagado 0 o tras revertir los pagos (P7).
- En reparaciones, la corrección del importe se hace en la cotización (re-cotizar) y la reversión en
  `RepairPayment`; `finance.*` no escribe en ese libro.
- Reembolsos de dinero: no se modelan aquí (no hay pasarela que los haga); igual que `OrderRefund`, un registro
  manual con evidencia, fuera de FIN-E salvo decisión (P6).
- Pagado ⇔ saldo 0. Registrar un cobro no emite ningún documento fiscal.

## 7. Recordatorios y WhatsApp

### 7.1 Recordatorios internos (FIN-C)

- Política por empresa (`FinanceReminderPolicy`): desfases en días respecto al vencimiento, por defecto
  `[7, 3, 1, 0, -1]` y después de vencida cada `k` días (por defecto 7) con tope; «por vencer» = N días; canales
  (campana interna; correo opcional).
- Destinatarios: el `owner` de la obligación y quienes tengan `finance.view` con acceso a la sucursal (o alcance de
  empresa si es general).
- `emit(...)` con `event_key = event_key('finance.obligation.due', 'finance_obligation', <id>, '<desfase>@<due_date>')`.
  La clave única impide dos avisos por el mismo evento y período, también con dos procesos. Si cambia el
  vencimiento (sólo por ajuste autorizado), el discriminador cambia y el aviso es nuevo, a propósito.
- **Leer o descartar un aviso sólo escribe `Notification.read_at`.** No hay ningún camino de código desde la campana
  hasta la obligación (prueba explícita).
- Tablero «Finanzas → Pendientes»: total pendiente, vencido, por vencer, vence hoy, calendario por fecha local,
  filtro por responsable, sucursal, categoría y dirección, historial por obligación (pagos, ajustes, reversiones,
  anulación, recordatorios y su estado). Es una consulta sobre `Obligation`, **no** sobre las notificaciones: lo
  vencido sigue en la lista mientras esté abierto.

### 7.2 Planificador: el existente

- Se reutiliza el **cron del servidor** (§1.8). Un comando nuevo `run_finance_schedule` (cada 15 min, línea nueva en
  `deploy/crontab.example` con su nota de vuelta atrás) hace, por empresa y en su fecha local: (1) generar
  períodos, (2) emitir recordatorios internos cuyo desfase ya llegó, (3) en FIN-G, encolar recordatorios de cobranza
  programados. La entrega de WhatsApp la sigue haciendo `send_pending_notifications` (cada minuto).
- No se añade Celery ni otro planificador: el volumen (decenas o cientos de obligaciones por empresa) no lo
  justifica, y la corrección no depende de que corra una sola instancia (restricciones únicas). Opcionalmente
  `pg_try_advisory_lock` para no repetir trabajo, nunca como garantía.
- Si el cron no corre, nada se pierde: el tablero calcula estados al leer y la siguiente pasada emite lo atrasado
  (sin duplicar). `ops_status` gana una línea «finanzas: última pasada hace más de 1 h».

### 7.3 Cobranza por WhatsApp: generalizar, no duplicar

- Mismo cliente (`messaging/whatsapp_cloud.py`), misma configuración por empresa, misma outbox
  (`NotificationDelivery`), mismo webhook de estados y de baja.
- Cambios en `whatsapp_services.py`: un registro de destinos `{'repair_order': …, 'finance_obligation': …}` que
  define para cada uno elegibilidad, parámetros, comprobaciones en el envío y su plantilla. El de reparación queda
  igual (sus pruebas actuales siguen verdes sin cambios).
- Evento nuevo `finance.collection.reminder` en `CompanyMessagingSettings.whatsapp_templates`. Notificación con
  `audience=customer`, `target_type='finance_obligation'`.
- Acciones (FIN-F, manual): preparar (vista previa del texto exacto con los parámetros), enviar (confirmación
  explícita), historial (entregas y estado), programar el siguiente, desactivar para esta obligación.
  FIN-G: envío programado según la política, **apagado por defecto** (`collections_auto_enabled = False`).

### 7.4 Condiciones de envío (todas, en el servidor, y otra vez dentro de la transacción del envío)

1. La obligación existe, es de la empresa, `direction = receivable`, `lifecycle = open`, **saldo > 0** (releído con
   la obligación bloqueada), no está «en revisión de cotización» ni en disputa.
2. Cliente de la misma empresa, activo, con teléfono que `to_wa_id` acepta.
3. **Consentimiento de cobranza vigente** para ese mismo teléfono (§7.7), sin baja posterior.
4. Sin baja general de WhatsApp (`whatsapp_opt_out_at` posterior al consentimiento) ni baja de cobranza.
5. WhatsApp de la empresa configurado y encendido (`readiness`, `whatsapp_enabled`) y proveedor no `disabled`.
6. Plantilla configurada para `finance.collection.reminder` y marcada **aprobada** (§7.8).
7. No enviado ya: una entrega por evento (clave única) y como máximo uno por obligación cada `k` días.
8. Dentro del **horario permitido** de la empresa (§7.9) y bajo el **tope diario** de la empresa y por cliente.
9. Envío manual: capacidad `finance.reminders.send` y acceso a la sucursal de la obligación.
10. El mensaje sólo lleva nombre, empresa y referencia interna; nunca a un contacto que no sea el titular.

Si una condición falla antes de encolar: la entrega queda `skipped` con su motivo visible. Si falla en el envío
(p. ej. la deuda se pagó mientras esperaba): `skipped`, motivo «deuda saldada antes del envío», sin llamar a Meta.

### 7.5 Transacción del envío

`deliver(delivery_id)` para `finance_obligation`: leer la entrega sin bloquear → `Obligation.select_for_update()` →
`NotificationDelivery.select_for_update(of=('self',))` → si ya salió, volver → releer §7.4 → llamar a Meta →
guardar. El bloqueo de la obligación durante la llamada (≤ 8 s, `whatsapp_cloud.py:22`) hace que un pago
concurrente espere y vea el envío ya hecho, o que el envío vea la deuda saldada. Un *timeout* no se reintenta
(regla actual, `whatsapp_cloud.py:67-75`): una persona decide.

### 7.6 Al saldar, anular o pausar la cobranza

En la misma transacción que deja `lifecycle ≠ open` (o que desactiva la cobranza de esa obligación): las entregas
`pending`/`failed` con reintento de esa obligación pasan a `skipped` («deuda saldada», «anulada», «cobranza
desactivada»). El historial se conserva; los estados `sent/delivered/read/failed` se siguen mostrando con su
error. Como los recordatorios futuros no existen de antemano (D9), no hay nada más que cancelar: el planificador ya
no los crea.

### 7.7 Consentimiento: análisis

- **Lo que hay:** el personal registra «El cliente aceptó recibir avisos por WhatsApp»
  (`frontend/app/admin/service/components/CustomerNoticesPanel.tsx:75-105`), siempre `source='counter'`
  (`whatsapp_views.py:112-141`), sin texto versionado, sin alcance y sin el teléfono al que se dio. El contexto es
  el seguimiento de la reparación (`docs/seguimiento-whatsapp-equipos.md` §4).
- **Meta:** exige opt-in antes de escribir, que diga claramente que la persona acepta comunicaciones de la empresa
  y el nombre de la empresa; puede ser general; recomienda opt-in separado por categoría; las bajas se respetan
  siempre, dentro o fuera de WhatsApp (§13, [opt-in], [política]).
- **Perú:** la Ley 29733 de protección de datos pide consentimiento para una finalidad, con excepciones (entre
  ellas, la ejecución de una relación contractual de la que el titular es parte, art. 14). Que una cobranza por
  WhatsApp entre en esa excepción es una **cuestión legal**, no técnica (P11).
- **Conclusión:** el consentimiento actual **no** cubre la cobranza (alcance distinto, sin texto, sin teléfono). Se
  diseña uno propio:
  - `CustomerContactConsent` (sólo altas): `company`, `customer`, `channel='whatsapp'`, `purpose` ∈
    `service_notices`, `collections`; `action` ∈ `grant`/`revoke`; `phone_wa_id` al que se dio; `source`
    (`counter`, `web`, `message`, `document`); `wording_version` y el texto mostrado; `actor`; `created_at`.
    Vigente = último registro `grant` del propósito, sin `revoke` posterior, y con `phone_wa_id` igual al teléfono
    actual (cambiar el teléfono invalida el consentimiento de cobranza).
  - La baja por mensaje (`apply_inbound`) revoca **todos** los propósitos de WhatsApp (conservador).
  - Los campos actuales de `Customer` siguen gobernando los avisos de reparación; no se migra su significado.
  - Texto propuesto para el mostrador (configurable por empresa): «Acepto que {{empresa}} me escriba por WhatsApp al
    número {{teléfono}} para recordarme saldos pendientes. Puedo darme de baja respondiendo BAJA.»

### 7.8 Plantilla

- Categoría **Utility** (alerta de cuenta: Meta pone como ejemplo de utilidad el recordatorio de pago mensual y el
  saldo disponible). Sin contenido promocional: una plantilla mixta pasa a Marketing, y Meta re-categoriza con aviso
  de 1 día y después al instante ([categorías], [utility]).
- Texto por defecto (del propietario), **sin importe**:
  «Hola, {{1}}. Te contactamos de {{2}} porque registramos un saldo pendiente asociado a la referencia {{3}}. Puedes
  comunicarte con nosotros para consultar los detalles o informar si ya realizaste el pago. Gracias.»
  Parámetros: `{{1}}` nombre del cliente, `{{2}}` nombre comercial de la empresa, `{{3}}` `Obligation.reference`.
  El cliente actual manda parámetros posicionales (`whatsapp_cloud.py:37-59`); el propietario escribe la plantilla
  en Meta con `{{1}}…{{3}}` en ese orden.
- **Nunca:** número de documento (DNI/RUC), número de comprobante, dirección, datos del equipo, IMEI, datos
  tributarios, importe (salvo que el propietario active una plantilla con importe, P12).
- Debe estar **APPROVED** en Meta; cualquier otro estado (PENDING, REJECTED, PAUSED, DISABLED…) no se puede enviar
  ([revisión], [calidad], [pausa]). El sistema no lee hoy el estado de las plantillas (`docs/integraciones-y-secretos.md:57`).
  FIN-F: el administrador marca «plantilla aprobada en Meta» (auditado) y, si el envío devuelve 132001 o 132000, la
  plantilla se marca como no utilizable y se para la cobranza de la empresa hasta revisarla. Leer el estado por la
  API Graph con el mismo cliente queda como mejora (sin cliente HTTP nuevo).
- Ventana de 24 h: fuera de ella sólo plantillas; el sistema nunca manda texto libre, así que no depende de la
  ventana ([política], [precios]).
- Coste: desde el 1-10-2026 las plantillas de utilidad se cobran también dentro de la ventana ([precios]). El tope
  diario protege el gasto.

### 7.9 Horario, límites y tono

- **Horario permitido por defecto:** lunes a viernes de 07:00 a 20:00 hora local, sin feriados (lista de feriados
  por empresa). Es la regla del art. 62 del Código de Protección y Defensa del Consumidor (Ley 29571) para llamadas
  y visitas de cobranza; aplicarla a WhatsApp es la lectura prudente, a confirmar por asesoría (P11). Fuera de
  horario, la entrega no se envía: su `next_attempt_at` pasa a la próxima apertura (no se salta).
- **Límites:** por cliente, un recordatorio de cobranza por obligación cada `k` días (7 por defecto) y como máximo
  uno al día en total; por empresa, tope diario configurable (50 por defecto). Meta impone además: límite de
  usuarios únicos por 24 h según el nivel (250 en cuentas nuevas), 1 mensaje cada 6 s al mismo usuario (error
  131056), rendimiento por número (130429) ([límites], [rendimiento], [plataforma]).
- **Errores de Meta:** temporales (reintento con espera): 130429, 131056, 131016, 131000, 5xx (ya en
  `whatsapp_cloud.py:25`). 131048 (spam) y 131049: reintento sólo tras ≥ 24 h y aviso en `ops_status`. Permanentes
  (no se reintentan, se muestra el error): 131026, 131047, 131051, 132000, 132001, 368, 131042. La lista de
  reintentables existente se amplía con 131016/131000 y se excluye 131048 del reintento corto en destinos de
  finanzas ([errores]).
- **Tono:** nada de amenazas, mayúsculas, plazos falsos ni mensajes repetidos; nunca a terceros (avales, familiares,
  empleador). La pantalla no permite editar el texto: sólo elegir la plantilla aprobada.

## 8. Permisos

### 8.1 Se reutilizan

| Capacidad existente | Uso en finanzas |
|---|---|
| `costs.view` | Ver y vincular líneas recibidas con su costo (FIN-D); `pending_settlement`. |
| `service.payments.collect` / `.manage` | Cobrar / revertir en el origen reparación (un solo libro). |
| `accounting.view` / `.export` | Vista «Obligaciones» del Centro Contable (FIN-H); documentos del comprador sin máscara sólo con `export`. |
| `settings.view` / `.manage` | Configuración de WhatsApp de la empresa (existente). |
| `company.manage` | Titulares de partida para conceder las nuevas en la migración de datos (patrón `0149`). |

### 8.2 Nuevas (módulo `finance`)

| Código | Permite |
|---|---|
| `finance.view` | Ver obligaciones, tablero «Pendientes», calendario, historial. |
| `finance.manage` | Crear y editar reglas, categorías, obligaciones de pagar y manuales; pausar/terminar reglas. |
| `finance.receivables.manual` | Alta manual justificada de cuentas por cobrar. |
| `finance.payments.record` | Registrar pagos (pagar y cobrar, salvo reparación). |
| `finance.payments.reverse` | Revertir pagos, con motivo. |
| `finance.adjust` | Ajustes, importe variable de un período, castigos. |
| `finance.void` | Anular obligaciones, con motivo. |
| `finance.export` | Exportar listados y antigüedad. |
| `finance.reminders.send` | Preparar y enviar recordatorios manuales; registrar consentimiento de cobranza. |
| `finance.automation.manage` | Política de recordatorios, horario, topes y envío programado. |

- Migración de datos: se conceden todas a los roles con `company.manage` y el preset Administrador las incluye por
  ser asignables. `finance.export`, `finance.void` y `finance.automation.manage` podrían pedir asignación explícita
  (P3).
- Sucursal: obligación con sucursal ⇒ hace falta acceso a esa sucursal (`assert_branch_access`); general ⇒
  `has_company_wide_scope`. Listas filtradas por `visible_branch_ids`.
- Entre empresas: obligaciones, pagos, adjuntos, teléfonos (siempre enmascarados en listas, `phones.mask`) y
  documentos responden 404 si no son de la empresa resuelta. **Ningún endpoint público** muestra saldos de
  finanzas (el seguimiento de reparación del cliente ya muestra su propio saldo de servicio y no cambia).
- Toda escritura: `AdminAuditLog` con `company`, sin importes de costo en metadatos si el actor no tiene
  `costs.view`.

## 9. Integración con el Centro Contable (FIN-H)

- Vista nueva «Obligaciones» (sólo lectura, `accounting.view`): cuentas por cobrar y por pagar abiertas y vencidas,
  pagos fijos programados (proyección por vencimiento), pagos y cobros confirmados de finanzas, proveedores,
  antigüedad (0–30, 31–60, 61–90, > 90 días desde `due_date`).
- Exportaciones (`finance.export`, y `accounting.export` para datos sin máscara) por mes, año, rango, empresa,
  sucursal, cliente, proveedor y estado; auditadas como `docs/adr-accounting.md` §8.
- **Sin doble conteo:**
  - Los cobros de reparación ya están en la vista A como `repair:<id>`; la vista «Obligaciones» muestra el saldo,
    no vuelve a sumar los `RepairPayment` al ingreso.
  - Los cobros manuales de finanzas **no** entran en el ingreso de la vista A (P10).
  - Una cuenta por cobrar no es dinero recibido; una por pagar no es dinero pagado; un recordatorio no es un
    movimiento.
  - Una factura de proveedor vinculada a un período fijo es un solo cargo.
- SUNAT sigue separado: nada de esto alimenta RVIE/RCE ni dice «gasto deducible». Cada pantalla y exportación
  lleva el aviso «Control interno de obligaciones. No es contabilidad completa ni sustituye a los registros de
  SUNAT.»

## 10. Fases, PR y migraciones

Números de migración **provisionales**: la hoja de `master` es `0154_auth_session` y #184 puede tomar `0155`. Se
propone empezar en `0156` y renumerar al fusionar, siempre en PR propios de finanzas, nunca mezclados con PR de
autenticación, fiscales o de etiquetas.

| Fase | PR | Contenido | Migraciones (provisionales) | Depende de |
|---|---|---|---|---|
| FIN-A | `docs/finance-obligations-adr` | Este ADR | — | — |
| fix H-1 | `fix/service-payment-reversal-lock` | `reverse_service_payment` bloquea la `RepairOrder` primero | — | — |
| FIN-B1 | `feat/finance-core` (backend) | `FinanceCategory`, `RecurringObligationRule`, `Obligation`, `ObligationPayment`, `ObligationAdjustment`; generación idempotente; pagos, reversión, ajustes, anulación; capacidades; API; secuencia `finance_obligation` | `0156_finance_core`, `0157_finance_capabilities` (datos), `0158_internal_sequence_finance` (opciones) | FIN-A |
| FIN-B2 | `feat/finance-fixed-ui` (frontend) | Finanzas › Pagos fijos, detalle, historial | — | FIN-B1 |
| FIN-B3 | `feat/finance-attachments` | Adjuntos privados, validador PDF, `request_limits` + Caddy | `0159_finance_attachment` | FIN-B1 |
| FIN-C | `feat/finance-reminders` | Política, `run_finance_schedule`, avisos internos, tablero «Pendientes» y calendario; línea de cron | `0160_finance_reminder_policy` | FIN-B1 |
| FIN-D | `feat/finance-payables` | Documento de proveedor, `ObligationSupplyLine`, vínculo con período fijo | `0161_finance_supplier_link` | FIN-B1 |
| FIN-E | `feat/finance-receivables` | Proyección de reparaciones, manuales, ajustes/castigos | `0162_finance_receivable_origin` (+ relleno de reparaciones con saldo) | FIN-B1, fix H-1 |
| FIN-F | `feat/finance-whatsapp-manual` | Destinos generalizados, `CustomerContactConsent`, plantilla, envío manual | `0163_customer_contact_consent` | FIN-E |
| FIN-G | `feat/finance-whatsapp-scheduled` | Programación, horario, topes, cola | `0164_finance_collection_schedule` | FIN-C, FIN-F |
| FIN-H | `feat/finance-accounting` | Vista «Obligaciones», antigüedad, exportaciones | — | FIN-D, FIN-E |
| FIN-I | `test/finance-e2e` | Playwright, auditoría final, docs | — | todas |

- Los PR con `backend/**` tardan ~70 min de CI (`AGENTS.md` §5): FIN-B1 + FIN-B3 pueden apilarse; los de sólo
  frontend van al final de cada tramo.
- Todas las migraciones son aditivas (tablas nuevas, capacidades por datos, opciones). La vuelta atrás de código es
  segura con las tablas presentes; `0162` rellena proyecciones y su reversa las borra (son proyecciones, no dinero).
- Cada PR actualiza `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md` y `docs/NEXT_ACTION.md` (este PR de ADR, como #173 y
  #180, sólo añade el ADR).

## 11. Plan de pruebas

PostgreSQL siempre (`AGENTS.md` §6). Las de concurrencia con `TransactionTestCase` e hilos, como las
`M12BConcurrencyTest` existentes de pagos de servicio (`tests.py:41773`).

| # | Caso del propietario | Fase | Archivo | Qué se comprueba |
|---|---|---|---|---|
| 1 | Pago mensual sin pagar persiste | B1 | `test_finance_obligations.py` | Tras vencer (reloj +90 días) sigue abierta, «vencida», en la lista y en el total; ningún comando la cierra. |
| 2 | Generación repetida sin duplicados | B1 | `test_finance_generation.py` | Correr la generación 3 veces ⇒ un período por (`rule`, `period_start`). |
| 3 | Dos procesos, mismo vencimiento | B1 | `test_finance_generation.py` | Dos hilos generan a la vez ⇒ un período, sin error visible. |
| 4 | Pago parcial | B1 | `test_finance_payments.py` | Saldo = efectivo − pagado; estado «parcialmente pagada». |
| 5 | Pago total | B1 | `test_finance_payments.py` | Saldo 0 ⇒ `settled`; un pago más se rechaza (sin sobrepago). |
| 6 | Reversión | B1 | `test_finance_payments.py` | Con motivo, auditada, idempotente; reabre; el pago sigue en el historial; `delete()` lanza. |
| 7 | Pago concurrente | B1 | `test_finance_payments.py` | Dos hilos pagan el saldo completo ⇒ uno gana, el otro ve saldo 0; misma clave de idempotencia ⇒ un pago. |
| 8 | Regla pausada | B1 | `test_finance_generation.py` | No genera futuros; los abiertos siguen y vencen. |
| 9 | Anulación auditada | B1 | `test_finance_obligations.py` | Sin motivo o sin `finance.void` se rechaza; con ellos queda anulada, auditada con `company`, visible en historial. |
| 10 | Leer un recordatorio no cambia saldo | C | `test_finance_reminders.py` | Marcar leída/descartar ⇒ saldo, `lifecycle` y lista iguales. |
| 11 | Reparación con adelanto | E | `test_finance_receivables.py` | Cotización aprobada 500, adelanto 200 ⇒ una cuenta `repair:<id>` con saldo 300. |
| 12 | Reparación parcial + reversión | E | `test_finance_receivables.py` | Revertir el adelanto ⇒ saldo 500 y la cuenta se reabre; nada en `ObligationPayment`. |
| 13 | Cotización rechazada | E | `test_finance_receivables.py` | Rechazada, enviada o borrador ⇒ ninguna cuenta. |
| 14 | Venta de caja pagada | E | `test_finance_receivables.py` | Ninguna cuenta por cobrar; tampoco pedidos `pending_payment`, `failed`, `expired`. |
| 15 | Cliente correcto | E | `test_finance_receivables.py` | El cliente es `RepairOrder.customer`; un cliente de otra empresa en un alta manual se rechaza. |
| 16 | Deuda de otra empresa | B1 (y cada fase) | `test_finance_tenancy.py` | 404 en detalle, pagos, adjuntos, envío; no aparece en listas ni totales. |
| 17 | Sucursal no autorizada | B1 | `test_finance_tenancy.py` | 404/403 según regla; una general exige alcance de empresa. |
| 18 | WhatsApp sin consentimiento | F | `test_finance_whatsapp.py` | `skipped` «sin consentimiento de cobranza»; el de «avisos» no basta; cambiar el teléfono lo invalida. |
| 19 | Sin plantilla aprobada | F | `test_finance_whatsapp.py` | Sin plantilla o sin marca de aprobada ⇒ no se envía; 132001 deshabilita la plantilla. |
| 20 | Deuda pagada antes del envío | F/G | `test_finance_whatsapp.py` | Encolado, pago, `deliver` ⇒ `skipped` «deuda saldada», el proveedor falso no recibe llamada. |
| 21 | Recordatorio repetido | C, G | `test_finance_reminders.py`, `test_finance_whatsapp_schedule.py` | Dos pasadas del planificador ⇒ un aviso por (obligación, desfase, vencimiento); respeta `k` días. |
| 22 | Error temporal del proveedor | G | `test_finance_whatsapp_schedule.py` | 130429/131056 ⇒ `failed` con `next_attempt_at`; reintento envía una sola vez; 131026 no se reintenta. |
| 23 | Baja de WhatsApp | F | `test_finance_whatsapp.py` | «BAJA» entrante revoca cobranza y avisos; entregas pendientes `skipped`. |
| 24 | Exportar sin permiso | H | `test_finance_accounting.py` | Sin `finance.export` ⇒ 403 y sin auditoría de exportación. |
| 25 | Informes sin pagos duplicados | H | `test_finance_accounting.py` | Σ de cobros de reparación en «Obligaciones» + vista A no cuenta dos veces; factura vinculada a período fijo = un cargo. |
| 26 | Zona horaria de Perú | B1, C, G | `test_finance_obligations.py`, `test_finance_reminders.py`, `test_finance_whatsapp_schedule.py` | 23:30 de Lima del día de vencimiento (04:30 UTC del siguiente) = «vence hoy», no «vencida»; los desfases se cuentan en días locales; el horario permitido usa la hora local. |

Otras: invariante `settled ⇔ saldo 0` (B1); importe variable de un período sin tocar los demás (B1); la regla no
cambia su calendario tras generar (B1); `pending_settlement` resta lo vinculado y una línea no se vincula dos
veces (D); mismo documento de proveedor dos veces ⇒ 409 (D); horario y tope diario (G); frontend
`frontend/__tests__/finance-*.test.tsx` (la interfaz no ofrece lo que el servidor niega); E2E
`frontend/e2e/finance.spec.ts` (FIN-I).

### 11.1 Lo primero de FIN-B1 (RED antes que GREEN)

1. `test_finance_generation.py::test_monthly_rule_generates_one_period_per_month_idempotently` — falla: no existe
   el modelo.
2. `…::test_two_workers_generate_the_same_period_once` (hilos).
3. `…::test_paused_rule_stops_future_periods_only`.
4. `test_finance_obligations.py::test_overdue_unpaid_period_stays_open_and_listed` (reloj +90 días).
5. `…::test_overdue_state_uses_company_local_date` (23:30 Lima).
6. `test_finance_payments.py::test_partial_then_total_payment_settles` y `test_overpayment_is_refused`.
7. `…::test_concurrent_full_payments_only_one_wins` y `test_same_idempotency_key_records_once`.
8. `…::test_reversal_reopens_keeps_history_and_is_idempotent`; `test_payment_rows_cannot_be_deleted`.
9. `…::test_variable_amount_changes_one_period_only`.
10. `test_finance_obligations.py::test_void_requires_reason_and_capability_and_is_audited`.
11. `test_finance_tenancy.py::test_other_company_obligation_is_404` y `test_unauthorised_branch_is_refused`.
12. `test_finance_capabilities.py::test_finance_codes_in_catalog_and_granted_to_company_managers` (patrón 0149).

## 12. Preguntas al propietario

1. ¿Un solo módulo «Finanzas» con pagar y cobrar, o permisos separados para pagar y cobrar?
2. ¿Se ofrece una lista sugerida de categorías al crear la primera regla, o cada empresa empieza vacía?
3. ¿Quién recibe por defecto `finance.export`, `finance.void` y `finance.automation.manage`: el Administrador o
   sólo por asignación explícita?
4. Al reanudar una regla pausada, ¿se generan los períodos del tiempo en pausa? (Propuesto: no.)
5. ¿Cuándo vence el saldo de una reparación: al quedar «Listo para recoger» + N días, a la entrega, otro?
6. ¿Se permite registrar un saldo a favor del cliente o del proveedor (sobrepago), o siempre se rechaza?
7. ¿Se puede anular una obligación con pagos, o primero hay que revertirlos?
8. Recordatorios internos: ¿los desfases 7/3/1/0/−1 y cada 7 días después de vencer están bien?
9. ¿Las entradas genéricas de stock deben poder llevar proveedor para vincularlas a una cuenta por pagar?
10. ¿Los cobros de cuentas por cobrar manuales cuentan como ingreso en el Centro Contable? (Propuesto: no.)
11. Legal: ¿la cobranza por WhatsApp necesita consentimiento propio (propuesto) o basta la relación contractual?
    ¿El horario de lunes a viernes 07:00–20:00 sin feriados aplica a WhatsApp? Requiere asesoría.
12. ¿La plantilla debe llevar el importe alguna vez, o nunca? (Propuesto: nunca por defecto.)
13. ¿Se registrará la venta al crédito en el futuro (con FISCAL-PROD-01) o queda fuera del producto?
14. Tope diario de recordatorios por empresa (propuesto 50) y por cliente (1 al día, 1 por obligación cada 7 días).

## 13. Bloqueos

- **BLOCKED — envíos reales de cobranza:** configuración de WhatsApp de la empresa en la consola, consentimiento de
  cobranza registrado por cliente, plantilla de utilidad **aprobada** por Meta con los tres parámetros, respuesta
  legal (P11) y **GO del propietario** para producción. Hasta entonces sólo el proveedor falso (`messaging/fake.py`).
- **BLOCKED — venta al crédito** como origen de cuentas por cobrar: no existe (§1.5).
- **BLOCKED — compras contables, gastos, bancos, asientos:** fuera de este ADR (como `docs/adr-accounting.md` §3).
- **PENDIENTE de decisión:** validador de PDF y si exige dependencia nueva (FIN-B3).

## 14. Fuentes oficiales (consultadas el 2026-10-25)

- [categorías] https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization.md
- [utility] https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/utility-templates/utility-templates.md
- [revisión] https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-review.md
- [estados de plantilla] https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/message_template_status_update.md
- [calidad] https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-quality.md
- [pausa] https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-pausing.md
- [opt-in] https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in.md
- [política] https://whatsappbusiness.com/policy/ (WhatsApp Business Messaging Policy)
- [precios] https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing.md
- [límites] https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits.md
- [rendimiento] https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput.md
- [plataforma] https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform.md
- [errores] https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
- [estados de mensaje] https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status.md
- Ley 29571, Código de Protección y Defensa del Consumidor, art. 62 (métodos abusivos de cobranza) y Ley 29733 de
  Protección de Datos Personales, art. 14: texto vigente a verificar en el portal del Congreso antes de citarlos
  formalmente.

No verificado en la documentación de Meta: una regla literal de que la plantilla de utilidad deba estar ligada a una
operación con opt-in; que 131049 aplique sólo a marketing; las tarifas por mensaje de Perú.
