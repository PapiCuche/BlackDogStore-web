# ADR — FINANCE-OBLIGATIONS-01 · Pagos fijos, cuentas por pagar, cuentas por cobrar y recordatorios

Estado: **PROPUESTA** (fase FIN-A: auditoría y diseño, sólo documentación). Nada de lo descrito en §3 en adelante
está implementado. Ninguna fase de este ADR toca producción, credenciales reales de WhatsApp/Meta, bancos ni
pasarelas.

**Revisión 2 (2026-10-25).** Incorpora la revisión independiente de #185 (P0–P3) y la decisión del propietario
**FINANCE-MANUAL-REMINDERS-01**:
- **No hay envío automático** de mensajes de cobro o de pago, por ningún canal: ni WhatsApp Cloud API, ni Meta, ni
  SMS, ni correo.
- FIN-F pasa a ser **mensajes manuales**: plantilla, vista previa, edición y «Copiar mensaje».
- FIN-G queda **BLOCKED**.
- Los avisos de reparación por WhatsApp no cambian.
- Los valores marcados «por defecto provisional» rigen hasta que el propietario responda (§12).

Orden de autoridad: código > migraciones > tests > docs. Las referencias `archivo:línea` son sobre `master` en
`1792f83` (hoja de migraciones `0154_auth_session`). Sin prefijo, las rutas son de `backend/store/`.

## 0. Qué es y qué no es

- Es un **control interno de obligaciones**: lo que la empresa debe pagar (pagos fijos y proveedores) y lo que sus
  clientes le deben, con su vencimiento, sus pagos, su historial y sus recordatorios.
- **Lo pendiente nunca desaparece por vencer.** Una obligación sólo sale de «Pendientes» cuando su saldo es cero
  (pagada), cuando se anula con motivo o cuando se corrige por un procedimiento autorizado y auditado. No hay
  caducidad, limpieza ni archivado automático.
- **Un recordatorio no es un movimiento.** Leer, cerrar o descartar un aviso no cambia saldo ni estado. Copiar un
  mensaje no es enviarlo, y ni copiar ni enviar registran un cobro.
- **Nada sale del sistema por sí solo.** Los mensajes a clientes y proveedores se preparan aquí y los envía una
  persona por el medio que elija. El sistema no llama a ningún proveedor de mensajería para finanzas.
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
  **no** la `RepairOrder`. El comentario de la entrega (`service_services.py:3009-3012`) supone que el bloqueo de la
  orden serializa una entrega con una reversión, y no es así. Con «exigir pago antes de entregar» activado, una
  reversión sellada en medio de una entrega dejaba salir el equipo con saldo. Se corrige en **#186**.
- **Hallazgo H-3 (P2, dinero no registrado):** el panel (`frontend/app/admin/service/orders/[id]/page.tsx:1112-1120`,
  `:1196`) guardaba la clave de idempotencia por `orden:importe:medio:referencia` durante toda la sesión.
  - Dos pagos reales idénticos reutilizaban la clave, el servidor respondía el segundo como repetición y ese segundo
    pago nunca se registraba.
  - Al consumo de repuestos (`:816`, `:871`) le pasaba lo mismo.
  - Se corrige en **#186**: una clave aleatoria por intento, que se descarta tras el éxito.
- **Hallazgo H-4 (P3):** la clave es única por empresa y la huella no incluía la orden (`_payment_fingerprint`,
  `:3244`, y la de la entrega). La misma clave en otra reparación devolvía el pago de otra orden. Se corrige en
  **#186**: la huella incluye la orden. **Lección para finanzas:** la huella de un pago incluye la obligación
  (§3.6).
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
  vuelve a leer la orden de reparación (`:347-349`). Finanzas **no usa** esta capa (§7.4).
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
| D1 | Un **núcleo común** de obligaciones para pagar y cobrar (`direction`), con un solo motor de saldo, bloqueo, recordatorios internos y calendario. |
| D2 | La **regla** de recurrencia no es deuda. Cada **período** es una `Obligation` propia, con identidad estable (`rule`, `period_start`) única. El calendario se **calcula** desde el ancla y el índice `n`, nunca sumando intervalos al período anterior. |
| D3 | El **saldo se deriva** de movimientos confirmados (pagos no revertidos y ajustes). No se edita un saldo. |
| D4 | Pagos y ajustes son **sólo de altas**. La reversión es un sello con motivo, como `RepairPayment`. Nada confirmado se borra. |
| D5 | Concurrencia: `select_for_update` de la obligación antes de leer el saldo; idempotencia por clave única con huella que **incluye la obligación**; generación idempotente por restricción única. |
| D6 | Reparaciones: la cuenta por cobrar guarda **sólo identidad, vencimiento y disputa**; importes y estado se **derivan al leer** de `RepairQuote` + `RepairPayment`. No hay segundo libro ni importes copiados. |
| D7 | Venta al crédito: **BLOCKED** hasta que exista. Una venta pagada nunca es deuda. |
| D8 | Proveedores: un **`SupplierDocument`** (clave única del documento) se vincula a una sola obligación, sea un período fijo o una cuenta por pagar. Las líneas recibidas se vinculan a mano; una línea, a lo sumo una obligación. |
| D9 | Recordatorios **internos**: se calculan justo a tiempo por un comando en el cron existente, sin crearlos de antemano. Idempotencia por `NotificationEvent.event_key`. Al ponerse al día, sólo el último desfase vencido. |
| D10 | **Mensajes a clientes y proveedores: sólo manuales** (FINANCE-MANUAL-REMINDERS-01). El servidor redacta desde el saldo actual; la persona edita, copia y envía por su cuenta. Ninguna llamada a WhatsApp, Meta, SMS ni correo para finanzas. |
| D11 | Una obligación puede marcarse **«en disputa»** (motivo, auditoría, capacidad propia): no se preparan mensajes de cobro; el recordatorio interno sigue. |
| D12 | Capacidades nuevas `finance.*`, separadas por acción. Se reutilizan `costs.view`, `service.payments.*`, `accounting.*` y `settings.*`. Por defecto sólo `view`, `manage` y `payments.record` llegan al Administrador. |
| D13 | Los vencimientos son fechas **locales** de la empresa (Lima por defecto). Los totales se agrupan **por moneda**; PEN y USD nunca se suman. |

Valores **por defecto provisionales** hasta que el propietario responda (§12): P1 un módulo `finance.*` con
capacidades separadas para pagar y cobrar (§8); P2 categorías sugeridas que cada empresa puede editar o borrar;
P3 `export`, `void`, `automation` y las demás sensibles **no** se conceden por defecto; P4 sin relleno de períodos
pausados; P6 sobrepago rechazado; P7 no se anula una obligación con pagos confirmados sin revertirlos antes; P10 los
cobros manuales de cuentas por cobrar no cuentan como ingreso.

## 3. Modelo de datos

Nombres provisionales. Todos llevan `company` (PROTECT) y se filtran por la empresa resuelta en el servidor.

### 3.1 Vista general

```
FinanceCategory ──┐
                  │
RecurringRule ────┼──< Obligation (un período o una deuda) >── ObligationPayment (altas + sello de reversión)
  └─ RulePause    │        │  │  │                         └─ ObligationAdjustment (altas, con motivo)
                  │        │  │  └── ObligationAttachment (privado)
Supplier/Customer ┘        │  └── SupplierDocument (0..1, clave única del documento)
                           └── Notification (recordatorio interno, existente)
RepairOrder ── Obligation[origin=repair]: sólo identidad + vencimiento + disputa; dinero en RepairQuote/RepairPayment
```

### 3.2 `FinanceCategory`

- `company`, `direction` (`payable`/`receivable`), `name`, `is_active`, `created_by`. Único (`company`, `direction`,
  `lower(name)`).
- **Por defecto provisional (P2):** al crear la primera regla, la pantalla ofrece una lista sugerida (alquiler,
  servicios, internet, teléfono, suscripciones, servicios profesionales, mantenimiento, otros). La empresa la acepta,
  la edita o la borra. La lista vive en la plantilla de la pantalla, no en datos globales. Borrar una categoría en
  uso no se permite: se desactiva.

### 3.3 `RecurringObligationRule` (pagos fijos) y `RulePause`

- Campos: `company`, `branch` (nula = general), `concept`, `payee_supplier` (FK opcional a `Supplier`) o
  `payee_name`, `category`, `amount` (> 0), `currency` (por defecto la de `CompanySettings.currency`), `frequency`
  (`weekly`, `monthly`, `quarterly`, `yearly`), `anchor_date`, `due_day` (día del mes 1–31, o día de la semana en
  las semanales), `end_date` (opcional), `generate_from` (cursor, §4.1), `owner`, `planned_method`, `reference`,
  `notes`, `ended_at`/`end_reason`, `created_by`. El pago único es una obligación manual sin regla.
- **Calendario inmutable tras el primer período:** `frequency`, `anchor_date` y `due_day` no se editan. Cambiar el
  calendario es terminar la regla y crear otra, con auditoría.
- `amount` se edita con auditoría y **sólo afecta a los períodos que se generen después**.
- `RulePause(rule, paused_from, resumed_on nula, reason, paused_by, resumed_by)`: intervalos de pausa, con
  historial. Un período cuyo `period_start` cae dentro de un intervalo no se genera.

### 3.4 `Obligation`

| Campo | Notas |
|---|---|
| `company`, `branch` | `branch` nula = general (sólo con alcance de toda la empresa). |
| `direction` | `payable` / `receivable`. |
| `origin_type` | `fixed`, `supplier_document`, `manual`, `repair`, (`credit_sale` reservado, BLOCKED). |
| `origin_key` | Clave de deduplicación (§3.7). Única por empresa cuando no está vacía. |
| `rule`, `period_index`, `period_start` | Sólo `fixed`. **Único (`rule`, `period_start`)** y (`rule`, `period_index`). |
| `supplier`, `customer`, `payee_name` | Contraparte de la misma empresa (`clean()` y servicio). |
| `category`, `concept`, `owner`, `planned_method`, `notes` | |
| `reference` | Referencia interna no sensible (`InternalSequence`, tipo nuevo `finance_obligation`, p. ej. `OB-000123`). Es la que usan los mensajes. |
| `external_reference` | Texto libre del usuario; **nunca** entra en un mensaje. |
| `amount_original`, `currency` | `amount_original > 0` y congelado, salvo en `repair`, donde es nulo porque se deriva (§6.2). |
| `issue_date`, `due_date` | `DateField` local. `due_date` nula sólo en reparaciones sin vencimiento todavía. |
| `lifecycle` | `open` / `settled` / `voided`. Sólo cambia dentro de la transacción bloqueada de un pago, ajuste o anulación. En `repair` no se guarda: se deriva. |
| `settled_at`, `voided_at`, `voided_by`, `void_reason`, `void_kind` | Anular exige motivo (CHECK). `void_kind` ∈ `manual`, `never_due` (§4.2). |
| `disputed_at`, `disputed_by`, `dispute_reason`, `dispute_resolved_at`, `dispute_resolved_by`, `dispute_resolution` | «En disputa» (D11). Marcar y resolver exigen motivo, `finance.dispute` y auditoría. CHECK: en disputa ⇒ motivo. |
| `created_by`, `created_at`, `idempotency_key`, `request_fingerprint` | Alta manual idempotente. |

**Estado visible (derivado, nunca guardado):**

| Estado | Regla (hoy = fecha local de la empresa) |
|---|---|
| Anulada | `lifecycle = voided` |
| Pagada | `lifecycle = settled` (saldo 0) |
| Vencida | abierta, saldo > 0, `due_date < hoy` |
| Vence hoy | abierta, saldo > 0, `due_date = hoy` |
| Por vencer | abierta, saldo > 0, `hoy < due_date ≤ hoy + N` (N por empresa, 7 por defecto) |
| Parcialmente pagada | marca adicional: hay pagos no revertidos y el saldo es > 0 |
| En disputa | marca adicional: no la oculta ni cambia su saldo ni su vencimiento |
| Pendiente | el resto de abiertas con saldo > 0 |

Invariante probado: `lifecycle = settled` ⇔ saldo = 0 y no anulada. Un comando de sólo lectura lo vigila, como
`ops_status`.

### 3.5 `ObligationPayment` y `ObligationAdjustment`

- `ObligationPayment`:
  - campos: `obligation`, `amount > 0`, `currency` (la de la obligación), `method`, `paid_on` (fecha local),
    `recorded_at`, `reference`, `notes`, `recorded_by`;
  - idempotencia: `idempotency_key` única por empresa si no está vacía, más `request_fingerprint`;
  - reversión: `reversed_at`, `reversed_by` y `reversal_reason` (CHECK: si está revertido, hay motivo);
  - `save()` congela todo salvo el sello y `delete()` lanza, el mismo patrón de `models.py:7432-7458`. La regla que
    impide guardar números de tarjeta en `reference` también se copia.
  - No se usa en el origen `repair`.
- `ObligationAdjustment`:
  - `kind`:
    - `increase`;
    - `decrease`;
    - `write_off`, sólo en cuentas por cobrar;
    - `amount_change`, el importe variable de **un** período: guarda el importe anterior y el nuevo.
  - `amount > 0`, `reason` obligatorio, `actor`, `created_at`.
  - Sólo de altas: un error se compensa con otro ajuste de signo contrario. No se usa en `repair`.
- Un importe negativo arbitrario no existe: el signo lo da `kind`.

### 3.6 Saldo, idempotencia y concurrencia

```
efectivo = amount_original + Σ increase − Σ decrease − Σ write_off   (amount_change → increase/decrease)
pagado   = Σ pagos no revertidos
saldo    = efectivo − pagado          (nunca < 0: se rechaza antes de escribir)
```

- **Sin sobrepago (P6, por defecto provisional):** un pago mayor que el saldo se rechaza, con el saldo en el mensaje.
  Un `decrease` que dejaría lo efectivo bajo lo pagado también se rechaza.
- **Idempotencia:** la huella del pago es SHA-256 de (`obligation_id`, importe, medio, referencia, fecha). Una clave ya
  usada en **otra** obligación es 409, nunca la repetición de otra deuda (lección H-4). Lo mismo vale para
  ajustes, anulaciones y altas manuales.
- **Registrar un pago:**
  1. `atomic`, y `Obligation.select_for_update()`;
  2. se mira la clave: si ya existe para esta obligación y con la misma huella, se devuelve ese pago; si no, 409;
  3. se comprueba `lifecycle = open`;
  4. se recalcula el saldo **bajo el bloqueo** y se valida;
  5. se inserta dentro de un *savepoint*; si pierde la carrera de la clave única, se resuelve como
     `_PaymentRaceLost`;
  6. si el saldo queda en 0, `lifecycle = settled`;
  7. `AdminAuditLog` con `company`.
- **Revertir:** bloquea **primero la obligación** y después el pago. Es idempotente y reabre (`settled → open`).
- **Orden de bloqueo único:** obligación → pagos y ajustes → (otros). En reparaciones: `RepairOrder` → `Obligation`.
- Las listas anotan el saldo con subconsultas (`Sum` filtrado), sin columna de caché.
- **Totales por moneda.** El tablero, el calendario y las exportaciones devuelven `{moneda: importe}`; nunca suman
  PEN con USD ni convierten.

### 3.7 Claves de deduplicación por origen (`origin_key`)

Mismo criterio que el Centro Contable (`accounting/operations.py:169-170` `sale:<id>`, `:342` `repair:<id>`): una
operación del negocio ⇒ **como máximo una** obligación.

| Origen | Identidad | Restricción |
|---|---|---|
| Pago fijo | (`rule`, `period_start`) | único |
| Documento de proveedor | `SupplierDocument` (§5.1); la obligación la referencia | `SupplierDocument.obligation` único |
| Reparación | `repair:<repair_order_id>` | único (`company`, `origin_key`) |
| Venta al crédito (futuro) | `sale:<order_id>` | único; BLOCKED |
| Manual | `manual:<uuid>` del servidor | idempotencia por `Idempotency-Key` |

### 3.8 Adjuntos privados (`ObligationAttachment`)

Se sigue el patrón de evidencias (§1.9):
- clave `companies/<cid>/finance/<obligation_id>/<uuid>.<ext>`, generada en el servidor;
- `storage_key` único, `mime_type`, `byte_size`, `sha256`, `uploaded_by`, anulación con motivo e idempotencia;
- tope por ruta en `request_limits.tiers()` **y** en `deploy/Caddyfile`;
- se sirve re-autorizando empresa, sucursal y capacidad, con 404 entre empresas y `no-store`.

Las imágenes pasan por `evidence_images.process`. El validador de PDF lo decide FIN-B3 (§13).

## 4. Pagos fijos (FIN-B)

### 4.1 Calendario calculado

- El período `n` (n = 0, 1, 2…) se calcula desde `anchor_date` y `n`, **nunca** sumando un intervalo al anterior:
  - **mensual:** el mes `anchor + n`; el vencimiento es `min(due_day, último día de ese mes)`. Un 31 cae el 28 o 29
    en febrero y vuelve al 31 en marzo. Esto no se desliza, que es justo el error de sumar meses al período anterior.
  - **trimestral:** igual que el mensual, con `anchor + 3n`.
  - **anual:** igual que el mensual, con `anchor + 12n`; un 29 de febrero cae el 28 en los años no bisiestos.
  - **semanal:** `anchor_date + 7n`, alineado al `due_day` de la semana.
- `period_start` = inicio del período `n`. Hay restricciones únicas por (`rule`, `period_index`) y por (`rule`,
  `period_start`).
- El período 0 es el primero que vence **en o después** del ancla.
- `end_date`: un período existe sólo si **vence** en `end_date` o antes.
- **Inicio en el pasado:** `generate_from` es el cursor (índice) desde el que se genera. Por defecto es el **primer
  período que vence hoy o después**, así que crear hoy una regla con ancla en enero **no** crea diez deudas
  atrasadas. El formulario puede pedir expresamente generar los anteriores; esa acción se audita y deja el cursor
  en 0.
- Horizonte: se generan los períodos con `due_date ≤ hoy + 35 días` (fecha local).
- Generación: `get_or_create` sobre (`rule`, `period_index`) dentro de un *savepoint*. Un `IntegrityError` por la
  carrera con otro proceso se trata como «ya existía». Dos procesos a la vez producen **un** período.

### 4.2 Pausa, reanudación y fin

- **Pausar** crea un `RulePause(paused_from = hoy)` y detiene la generación de los períodos que **vencen** después de
  `paused_from` y antes de `resumed_on`. Un período que vence el mismo día de la pausa se debe y se genera; la regla
  es la misma que la de la anulación.
- **Períodos ya generados y sin pagos con vencimiento posterior a la pausa:** se anulan con
  `void_kind = never_due`, motivo «regla pausada antes de su vencimiento» y auditoría, porque nunca llegaron a deberse.
  Lo mismo al terminar la regla, para los posteriores a `end_date`. Con un pago confirmado, el período se queda
  abierto y se avisa.
- Los períodos ya vencidos **siguen abiertos y vencidos**: pausar no perdona deudas.
- **Reanudar (P4, por defecto provisional):** cierra el intervalo (`resumed_on`) y **no** rellena los períodos de la
  pausa. Para uno concreto hay una acción manual «Generar este período», auditada.
- **Nunca se borra un período.**

### 4.3 Importe variable

«Cambiar el importe de este período» crea un `ObligationAdjustment(kind=amount_change)` con motivo, sólo sobre ese
período. Cambiar el importe de la regla afecta sólo a los períodos futuros.

## 5. Cuentas por pagar a proveedores (FIN-D)

### 5.1 `SupplierDocument`: una sola clave del documento

- Campos: `company`, `supplier`, `doc_type` (catálogo cerrado: factura, recibo por honorarios, boleta, nota de
  crédito, otro), `series`, `number`, `issue_date`, `amount`, `currency`, adjunto, `obligation` (FK **única**).
- Normalización: serie en mayúsculas y sin espacios; número sin ceros a la izquierda (`F001-00012` = `F001-12`).
- Identidad del proveedor:
  - con RUC (`document_type = RUC`), la clave usa el RUC: `ruc:<número>`. Dos fichas del mismo RUC no pueden registrar
    el mismo documento, y la pantalla avisa del duplicado de ficha;
  - sin RUC, se usa el id: `supplier:<id>`.
- Único (`company`, identidad del proveedor, `doc_type`, `series`, `number`).
- El documento se vincula a **una** obligación: un período fijo existente (§5.3) o una obligación
  `supplier_document` nueva. Así no se registra el mismo documento dos veces en ninguno de los dos caminos.

### 5.2 Vínculo con lo recibido

- `ObligationSupplyLine(obligation, order_item)` con **único (`order_item`)**: una línea recibida se liquida, a lo
  sumo, en una cuenta por pagar.
- El servidor comprueba que la línea es de la misma empresa y que la oferta es del mismo proveedor de la obligación.
- La pantalla propone las líneas de `pending_settlement` del proveedor y la persona elige. Nada se crea solo.
- **Anular la obligación libera sus líneas**: se marca el vínculo como liberado con motivo y la línea vuelve a estar
  disponible. El historial se conserva.
- `pending_settlement` sigue siendo de sólo lectura; añade «vinculado a una cuenta por pagar».
- **Costos:** ver los importes de una cuenta por pagar `supplier_document` y sus líneas exige `costs.view`, además de
  `finance.view`. Sin `costs.view` se ve la obligación, su estado y su vencimiento, pero no los importes. Los pagos
  fijos no revelan costos de mercadería y no lo exigen.

### 5.3 Pago fijo y factura del mismo cargo

Si la regla tiene `payee_supplier` y llega la factura de ese período, el `SupplierDocument` **se vincula al período
existente**. Al registrar un documento de un proveedor que tiene un período fijo abierto en el mismo mes:
- el servidor avisa y propone vincularlo;
- crear otra obligación exige confirmar «no es el mismo cargo», y queda auditado.

Las entradas genéricas de stock no tienen proveedor y no se vinculan (P9). Un pago a un proveedor no es un gasto
válido ante SUNAT.

## 6. Cuentas por cobrar (FIN-E)

### 6.1 Orígenes de la primera etapa (cerrado)

| Origen | Cuándo existe | Cuándo **no** |
|---|---|---|
| 1. Saldo de reparación | Orden **no cancelada**, con cotización **aprobada** vigente (`financial_quote`) de total > 0 | Cotización rechazada, cancelada, enviada sin decidir o en borrador; total 0; orden `CANCELLED`; orden sin cotización |
| 2. Venta al crédito | **BLOCKED**: no existe (§1.5) | Una venta pagada de caja o web; un pedido sin pagar (`pending_payment`, `failed`, `expired`); un carrito abandonado |
| 3. Manual | Alta justificada: motivo obligatorio, cliente de la empresa y `finance.receivables.manual` | Sin motivo o sin cliente. **Nunca** para reexpresar el saldo de una reparación o una venta: el formulario no admite un vínculo a esos orígenes, y el servidor rechaza un alta manual para un cliente con una cuenta `repair:` abierta de la misma referencia |

### 6.2 Reparación: derivada al leer

- Se guarda sólo `Obligation(origin_type=repair, origin_key=repair:<id>, customer, branch, due_date, disputa)`.
  Importe, pagado, saldo y estado salen **al leer** de `service_payment_summary` (§1.4). No se copia ningún importe.
- Cuándo se crea: en la misma transacción que aprueba la cotización (gancho en `service_services`, con el orden de
  bloqueo `RepairOrder → Obligation`). Las reparaciones que ya existen se rellenan con un **comando**
  (`backfill_repair_receivables`, idempotente y con `--dry-run`), no con una migración de datos.
- **Orden cancelada:** no hay cuenta por cobrar. Una ya creada sale de las listas de pendientes; el historial la
  muestra como «origen cancelado».
- **Total 0:** no hay cuenta.
- **Re-cotización:** mientras no se apruebe la nueva revisión, la cuenta queda «en revisión de cotización». El saldo
  sigue el de la última revisión aprobada, y una cotización nunca se suma con otra.
- **Sobrepago:** queda pagada y el saldo a favor se muestra aparte.
- **Vencimiento (P5):** la fecha local en que la orden llega a «Listo para recoger», o en que se entrega con saldo,
  más N días (0 por defecto). Antes de eso: «sin vencimiento», nunca vencida.
- El cobro se registra con `record_service_payment` y las capacidades de servicio. `finance.*` no escribe en ese
  libro.

### 6.3 Ajustes, anulación, castigo, disputa

- Ajustes y castigos: `finance.adjust`, siempre con motivo.
- **Anular (P7, por defecto provisional):** exige `finance.void` y motivo. **Una obligación con pagos confirmados no se
  anula** hasta revertirlos.
- **Disputa:** `finance.dispute`, con motivo, y también al resolverla. No toca el saldo.
- En una reparación, el importe se corrige re-cotizando y la reversión se hace en `RepairPayment`.
- Una obligación está pagada cuando su saldo es 0. Registrar un cobro no emite ningún documento fiscal.

## 7. Recordatorios internos y mensajes manuales

### 7.1 Recordatorios internos (FIN-C)

- Política por empresa (`FinanceReminderPolicy`):
  - desfases en días respecto al vencimiento, por defecto `[7, 3, 1, 0, -1]`;
  - una vez vencida, un aviso cada `k` días (7 por defecto), con tope;
  - N días para «por vencer».
- Destinatarios: el `owner` de la obligación y quien tenga `finance.view` con acceso a la sucursal, o alcance de
  empresa si la obligación es general.
- `emit(...)` usa `event_key = event_key('finance.obligation.due', 'finance_obligation', <id>, '<desfase>@<due_date>')`.
  La clave única impide repetir el aviso, también con dos procesos a la vez.
- **Al ponerse al día** (el cron no corrió unos días), se emite sólo el **último desfase ya vencido**, no todos.
- **Leer o descartar un aviso sólo escribe `Notification.read_at`.** Ningún camino de código va de la campana a la
  obligación.
- Una obligación en disputa sigue recibiendo el recordatorio interno.
- Texto del recordatorio interno de un pago fijo (del propietario, §7.5).
- Tablero «Finanzas → Pendientes»:
  - totales **por moneda**: pendiente, vencido, por vencer, vence hoy;
  - calendario en fecha local;
  - filtros: responsable, sucursal, categoría, dirección y disputa;
  - historial de cada obligación.

  Es una consulta sobre `Obligation`, no sobre las notificaciones: lo vencido sigue en la lista mientras esté
  abierto.

### 7.2 Planificador: el existente

- Se reutiliza el **cron del servidor** (§1.8) con un comando nuevo, `run_finance_schedule`, cada 15 min. Por cada
  empresa, en su fecha local:
  1. genera los períodos;
  2. anula como `never_due` lo que corresponda (§4.2);
  3. emite los recordatorios internos.
- No se añade otro planificador. La corrección se apoya en las restricciones únicas. `ops_status` añade una línea:
  «finanzas: última pasada hace más de 1 h».

### 7.3 Mensajes manuales (FIN-F, FINANCE-MANUAL-REMINDERS-01)

- **Sin envío.** El sistema no llama a WhatsApp Cloud API, a Meta, a SMS ni al correo para estos mensajes. Tampoco
  genera enlaces que manden el contenido a otra plataforma (`wa.me?text=`, `mailto:?body=`). Los avisos de reparación
  siguen exactamente igual.
- Dónde aparece «Preparar recordatorio»:
  - Finanzas → Cuentas por cobrar;
  - Finanzas → Cuentas por pagar;
  - Finanzas → Pagos fijos;
  - la ficha del cliente, si tiene una deuda válida;
  - la ficha del proveedor, si tiene una obligación válida.

  En cuentas por cobrar, la fila ofrece: «Ver saldo», «Registrar abono», «Ver historial», «Preparar recordatorio» y
  «Copiar mensaje».
- Ventana de preparación:
  - datos: nombre de la contraparte, empresa acreedora, referencia, importe original, abonos confirmados, saldo
    pendiente actual, vencimiento (si existe), tipo de recordatorio y mensaje editable;
  - flujo: seleccionar la obligación → «Preparar mensaje» → elegir la plantilla → ver los datos actuales → editar →
    «Copiar mensaje» → «Mensaje copiado».
- **Lo redacta el servidor** (`POST …/obligations/<id>/message-preview/`, de sólo lectura). Usa el saldo actual de la
  base y la moneda de la obligación, y devuelve el texto final. El cliente nunca sustituye variables, y la edición
  posterior es sólo texto.
- Copiar:
  - con la API del portapapeles; si no está disponible, el texto queda seleccionable con el aviso «Selecciona y copia
    el texto»;
  - **nunca** se copia solo al abrir y **nada** se guarda en `localStorage`.
  - Copiar no es enviar: la pantalla nunca dice «Cliente notificado» por copiar, y no hay una tabla para contar clics.
- **Registro opcional de contacto** (`ObligationContactLog`): fecha, canal (texto: WhatsApp, llamada, correo,
  presencial, otro), responsable, resultado declarado y notas. Lo escribe una persona y no cambia ni saldo ni
  estado.
- **Cuándo no hay mensaje:**
  - Saldo 0: «Esta cuenta ya está pagada».
  - Obligación anulada, pagada o cancelada, reparación cancelada o cotización rechazada: no se prepara.
  - **En disputa:** no hay plantillas de cobro (sí el recordatorio interno).
  - Proveedor «C. Informar pago realizado»: sólo con un pago **confirmado** en el sistema. Nunca se afirma una
    transferencia que sólo estaba programada.
- **Variables permitidas** (lista cerrada): `{{empresa}}` (nombre comercial de la configuración de la empresa, nunca
  fijo en el código), `{{cliente}}`, `{{proveedor}}`, `{{beneficiario}}`, `{{referencia}}` (la interna, §3.4),
  `{{saldo}}`, `{{importe_pagado}}`, `{{vencimiento}}` (fecha local), `{{concepto}}`, `{{estado}}`.
  - Importes con la moneda de la obligación, con su símbolo y sin conversión.
  - Una variable sin dato **no** se inventa. Si es obligatoria para la plantilla (`{{saldo}}`, `{{referencia}}`), el
    mensaje se bloquea con el motivo. Si es opcional (`{{vencimiento}}`), se quita la frase que la contiene. Nunca
    queda un `{{…}}` sin resolver.
- **Nunca** entran: el DNI completo, datos bancarios, direcciones, IMEI, números de serie, información técnica,
  contraseñas ni datos de otros clientes. El tono es respetuoso y neutro.
- **Plantillas por empresa:** con `finance.templates.manage`, la empresa adapta los textos. Se validan las variables
  (sólo las de la lista), la longitud (máx. 1 000 caracteres) y que no haya enlaces ni datos prohibidos. Una
  plantilla de la empresa sustituye a la predeterminada, que se conserva para «restaurar».
- Permisos: ver y copiar mensajes exige `finance.messages.prepare` y acceso a la sucursal de la obligación.

### 7.4 WhatsApp para finanzas: descartado (registro)

Antes de la decisión del propietario se estudió enviar los cobros por WhatsApp Cloud API. Se guarda la conclusión
por si el tema vuelve:
- **P0 (revisión):** la WhatsApp Business Messaging Policy lista el **cobro de deudas** («debt collection») entre los
  usos prohibidos ([política]). Con la decisión FINANCE-MANUAL-REMINDERS-01 la cuestión **no aplica** a la cobranza,
  porque no se envía nada. Retomar el envío exigiría primero:
  - una respuesta legal y de política (P11);
  - plantillas presentadas como actualización de cuenta o de servicio, nunca como cobro;
  - consentimiento propio de cobranza;
  - parada automática de toda la empresa ante los errores 368 y 131048 o si baja la calidad;
  - un diseño en dos fases (reservar bajo el bloqueo, enviar fuera del bloqueo, cerrar bajo el bloqueo volviendo a
    comprobar);
  - aplazar con `next_attempt_at` sin gastar intentos;
  - un `retry()` que respete el orden obligación → entrega;
  - que un STOP revoque siempre el consentimiento de cobranza.

  Nada de esto se construye.
- Análisis que se conserva:
  - el consentimiento actual («avisos», `models.py:4272-4284`) no cubre la cobranza;
  - la plantilla debería ser de categoría *utility* y estar APPROVED;
  - fuera de la ventana de 24 h sólo se envían plantillas;
  - se aplican los límites por usuario y por número, y los errores reintentables o permanentes ([errores]);
  - Ley 29571, art. 62: la cobranza de lunes a viernes de 07:00 a 20:00, sin feriados y nunca a terceros.
- **Nota legal que sigue vigente para los mensajes manuales:** el art. 62 de la Ley 29571 (horarios, terceros y
  métodos abusivos de cobranza) se aplica a quien contacta. La pantalla recuerda «Contacta sólo al titular, en días
  hábiles de 07:00 a 20:00» como orientación, no como bloqueo. Hay además un proyecto de ley para prohibir los
  recordatorios **antes** del vencimiento sin aceptación expresa; queda en P11.

### 7.5 Plantillas predeterminadas (texto literal del propietario)

**Clientes**

*A. Recordatorio amable*
> Hola, {{cliente}}. Te saludamos de {{empresa}}.
>
> Queremos recordarte que registramos un saldo pendiente de {{saldo}} correspondiente a {{referencia}}.
>
> Si ya realizaste el pago, por favor comunícanoslo para verificarlo y actualizar nuestros registros.
>
> Si necesitas alguna información adicional, estaremos encantados de ayudarte.
>
> Muchas gracias.

*B. Próximo vencimiento*
> Hola, {{cliente}}.
>
> Te recordamos que el pago de {{saldo}}, correspondiente a {{referencia}}, tiene como fecha de vencimiento el {{vencimiento}}.
>
> Si ya realizaste el pago, puedes comunicárnoslo para verificarlo.
>
> Gracias por tu atención.
>
> {{empresa}}

*C. Pago vencido*
> Hola, {{cliente}}. Buen día.
>
> Nos comunicamos de {{empresa}} para informarte que, según nuestros registros, permanece pendiente un saldo de {{saldo}} correspondiente a {{referencia}}, cuyo vencimiento fue el {{vencimiento}}.
>
> Agradeceremos que nos confirmes si ya efectuaste el pago o si necesitas coordinarlo con nosotros.
>
> Muchas gracias.

*D. Pago parcial*
> Hola, {{cliente}}.
>
> Hemos registrado un abono a tu cuenta correspondiente a {{referencia}}.
>
> Actualmente, el saldo pendiente es de {{saldo}}.
>
> Gracias por tu pago. Si ya completaste el importe restante, comunícanoslo para verificarlo.
>
> {{empresa}}

*E. Mensaje personalizado:* texto libre con los datos autorizados de la operación, editable antes de copiar. El
editor nunca modifica registros económicos.

**Proveedores / acreedores**

*A. Solicitar estado de cuenta*
> Buen día, {{proveedor}}.
>
> Te saludamos de {{empresa}}.
>
> Estamos revisando nuestras obligaciones pendientes y agradeceremos que nos confirmes el estado de cuenta correspondiente a {{referencia}}.
>
> Según nuestros registros, el importe pendiente es de {{saldo}}.
>
> Gracias por tu apoyo.

*B. Coordinar pago*
> Buen día, {{proveedor}}.
>
> Te contactamos de {{empresa}} para coordinar el pago pendiente de {{saldo}}, relacionado con {{referencia}}.
>
> Agradeceremos que nos confirmes los datos necesarios para completar la operación.
>
> Muchas gracias.

*C. Informar pago realizado* (sólo con un pago confirmado en el sistema)
> Buen día, {{proveedor}}.
>
> Te informamos que hemos registrado un pago por {{importe_pagado}} correspondiente a {{referencia}}.
>
> Agradeceremos confirmar su recepción.
>
> Saludos,
> {{empresa}}

**Pagos fijos — recordatorio interno**
> Recordatorio de pago
>
> Concepto: {{concepto}}
> Beneficiario: {{beneficiario}}
> Importe pendiente: {{saldo}}
> Vencimiento: {{vencimiento}}
> Estado: {{estado}}
> Empresa: {{empresa}}

Más una versión editable para coordinar el pago con el proveedor, que parte de «Proveedores B».

## 8. Permisos

### 8.1 Se reutilizan

| Capacidad existente | Uso en finanzas |
|---|---|
| `costs.view` | Importes de cuentas por pagar de proveedor y de sus líneas recibidas (§5.2); `pending_settlement`. |
| `service.payments.collect` / `.manage` | Cobrar y revertir en el origen reparación (un solo libro). |
| `accounting.view` / `.export` | Vista «Obligaciones» del Centro Contable (FIN-H). |
| `company.manage` | Titulares de partida en la migración de datos (patrón `0149`). |

### 8.2 Nuevas (módulo `finance`)

| Código | Permite | ¿Al Administrador por defecto? (P3 provisional) |
|---|---|---|
| `finance.view` | Ver obligaciones, «Pendientes», calendario, historial | Sí |
| `finance.manage` | Reglas, categorías, obligaciones de pagar; pausar y terminar reglas | Sí |
| `finance.payments.record` | Registrar pagos (salvo los de reparación) | Sí |
| `finance.payments.reverse` | Revertir pagos, con motivo | No |
| `finance.adjust` | Ajustes, importe variable de un período, castigos | No |
| `finance.void` | Anular, con motivo | No |
| `finance.dispute` | Marcar y resolver «en disputa» | No |
| `finance.receivables.manual` | Alta manual de cuentas por cobrar | No |
| `finance.export` | Exportar | No |
| `finance.messages.prepare` | Ver, preparar y copiar mensajes; registro de contacto | No |
| `finance.templates.manage` | Plantillas de la empresa | No |
| `finance.automation.manage` | Política de recordatorios **internos** | No |

- «No» significa que se asigna explícitamente desde Roles.
  - La migración concede las tres primeras a los roles con `company.manage`.
  - El preset Administrador de las empresas nuevas se calcula como lo asignable **menos** `EXPLICIT_GRANT_CODES`
    (`capabilities.py`).
  - El master de la plataforma y el puente de roles heredados (admin anterior a RBAC) siguen resolviendo a todo.
- **Consecuencia de la regla de delegación** (`access_views.can_delegate_capabilities`): nadie delega una capacidad
  que no tiene. Por eso, al principio, **sólo el master de la plataforma** puede conceder las de asignación
  explícita. Después también puede hacerlo quien ya las haya recibido. Es intencional mientras el propietario no
  responda P3. Si prefiere que el Administrador pueda delegarlas sin tenerlas, hay que cambiar la regla de
  delegación, y eso es una decisión aparte.
- Sucursal: una obligación con sucursal exige acceso a esa sucursal; una general exige `has_company_wide_scope`. Las
  listas se acotan con `visible_branch_ids`.
- Entre empresas: obligaciones, pagos, adjuntos, mensajes y documentos responden 404. **Ningún endpoint público**
  muestra saldos de finanzas.
- Toda escritura deja `AdminAuditLog` con `company`.

## 9. Integración con el Centro Contable (FIN-H)

- Vista nueva «Obligaciones» (sólo lectura, `accounting.view`):
  - cuentas por cobrar y por pagar abiertas y vencidas;
  - proyección de los pagos fijos;
  - pagos confirmados;
  - antigüedad por `due_date` (0–30, 31–60, 61–90, > 90 días), **por moneda**.
- Exportaciones: exigen `finance.export`, y además `accounting.export` para los datos sin máscara. Se auditan como
  en `docs/adr-accounting.md` §8.
- **Sin doble conteo:**
  - los cobros de reparación ya están en la vista A como `repair:<id>`;
  - los cobros manuales de cuentas por cobrar **no** son ingreso (P10, por defecto provisional);
  - una cuenta por cobrar no es dinero recibido ni una por pagar dinero pagado;
  - un mensaje copiado o un recordatorio no son movimientos;
  - un `SupplierDocument` es un solo cargo.
- SUNAT sigue separado. El aviso fijo dice: «Control interno de obligaciones. No es contabilidad completa ni
  sustituye a los registros de SUNAT.»

## 10. Fases, PR y migraciones

Los números de migración son **provisionales**. La hoja de `master` es `0154_auth_session` y #184 puede tomar
`0155`. Se empieza en `0156` y se renumera al fusionar, siempre en PR propios de finanzas, nunca mezclados con
autenticación, fiscal o etiquetas.

| Fase | PR | Contenido | Migraciones (provisionales) | Depende de |
|---|---|---|---|---|
| FIN-A | #185 | Este ADR | — | — |
| FIN-H1 | #186 | H-1, H-3, H-4 y H-2 (§1.4) | — | — |
| FIN-B1 | `feat/finance-core` | Categorías, reglas, pausas, obligaciones (con disputa), pagos, ajustes, anulación; calendario calculado; generación idempotente (`generate_finance_obligations`, cada hora); capacidades; API | `0156_finance_core` (incluye la opción de secuencia), `0157_finance_capabilities` (datos) | FIN-A |
| FIN-B2 | `feat/finance-fixed-ui` | Finanzas › Pagos fijos (frontend) | — | FIN-B1 |
| FIN-B3 | `feat/finance-attachments` | Adjuntos privados, validador PDF, `request_limits` + Caddy | `0159_finance_attachment` | FIN-B1 |
| FIN-C | `feat/finance-reminders` | Política, `run_finance_schedule`, avisos internos, «Pendientes», calendario | `0160_finance_reminder_policy` | FIN-B1 |
| FIN-D | `feat/finance-payables` | `SupplierDocument`, `ObligationSupplyLine`, vínculo con período fijo | `0161_finance_supplier_document` | FIN-B1 |
| FIN-E | `feat/finance-receivables` | Reparaciones derivadas, manuales, ajustes, castigos; comando de relleno | `0162_finance_receivable_origin` | FIN-B1, #186 |
| FIN-F | `feat/finance-manual-messages` | Plantillas, vista previa del servidor, copiar, registro de contacto | `0163_finance_message_templates` | FIN-B1 (pagar y fijos), FIN-E (cobrar) |
| FIN-G | — | **BLOCKED** (automatización de mensajes externos) | — | decisión del propietario |
| FIN-H | `feat/finance-accounting` | Vista «Obligaciones», antigüedad, exportaciones | — | FIN-D, FIN-E |
| FIN-I | `test/finance-e2e` | Playwright, auditoría final, docs | — | todas |

- La disputa vive en `0156` (FIN-B1), así FIN-F no necesita otra migración en `Obligation`.
- FIN-F se puede partir: las plantillas de pagos fijos y de proveedores tras FIN-B1 y FIN-D, y las de clientes tras
  FIN-E.
- Todas las migraciones son aditivas. La vuelta atrás del código es segura con las tablas presentes.

## 11. Plan de pruebas

PostgreSQL siempre. Las pruebas de concurrencia son `TransactionTestCase` con hilos y van en `flushing`, en orden y
sin `reset_sequences`.

### 11.1 Casos del propietario (FINANCE-OBLIGATIONS-01)

| # | Caso | Fase | Archivo | Qué se comprueba |
|---|---|---|---|---|
| 1 | Pago mensual sin pagar persiste | B1 | `test_finance_obligations.py` | Tras +90 días sigue abierta, vencida y listada; ningún comando la cierra. |
| 2 | Generación repetida sin duplicados | B1 | `test_finance_generation.py` | 3 pasadas ⇒ un período por índice. |
| 3 | Dos procesos, mismo vencimiento | B1 | `test_finance_generation.py` | Dos hilos ⇒ un período. |
| 4 | Pago parcial | B1 | `test_finance_payments.py` | Saldo derivado; «parcialmente pagada». |
| 5 | Pago total | B1 | `test_finance_payments.py` | `settled`; otro pago se rechaza. |
| 6 | Reversión | B1 | `test_finance_payments.py` | Motivo, auditoría, idempotente, reabre, historial; `delete()` lanza. |
| 7 | Pago concurrente | B1 | `test_finance_payments.py` | Dos hilos con el saldo completo ⇒ uno gana; la misma clave ⇒ un pago. |
| 8 | Regla pausada | B1 | `test_finance_generation.py` | No genera en la pausa; anula `never_due` lo futuro sin pagos; lo vencido sigue abierto. |
| 9 | Anulación auditada | B1 | `test_finance_obligations.py` | Sin motivo, sin `finance.void` o con pagos ⇒ rechazo; con todo ⇒ anulada y auditada. |
| 10 | Leer un recordatorio no cambia el saldo | C | `test_finance_reminders.py` | `read_at` y nada más. |
| 11 | Reparación con adelanto | E | `test_finance_receivables.py` | 500 aprobado, 200 de adelanto ⇒ saldo 300, sin importes guardados. |
| 12 | Reparación parcial + reversión | E | `test_finance_receivables.py` | Saldo 500 de nuevo; nada en `ObligationPayment`. |
| 13 | Cotización rechazada | E | `test_finance_receivables.py` | Rechazada, borrador o enviada ⇒ ninguna cuenta; total 0 ⇒ ninguna. |
| 14 | Venta de caja pagada | E | `test_finance_receivables.py` | Ninguna cuenta; tampoco `pending_payment`, `failed` ni `expired`. |
| 15 | Cliente correcto | E | `test_finance_receivables.py` | `RepairOrder.customer`; un cliente de otra empresa se rechaza. |
| 16 | Deuda de otra empresa | todas | `test_finance_tenancy.py` | 404 en todo; fuera de listas y totales. |
| 17 | Sucursal no autorizada | B1 | `test_finance_tenancy.py` | Rechazo; una general exige alcance de empresa. |
| 18–23 | WhatsApp (sin consentimiento, sin plantilla, deuda pagada antes, repetido, error temporal, baja) | — | — | **Sustituidos** por 11.2: no hay envío. |
| 24 | Exportar sin permiso | H | `test_finance_accounting.py` | 403 y sin registro de exportación. |
| 25 | Informes sin pagos duplicados | H | `test_finance_accounting.py` | La reparación no se cuenta dos veces; un `SupplierDocument` es un cargo. |
| 26 | Zona horaria de Perú | B1, C | `test_finance_obligations.py`, `test_finance_reminders.py` | A las 23:30 de Lima del día de vencimiento, «vence hoy»; los desfases se cuentan en días locales. |

### 11.2 Mensajes manuales (FINANCE-MANUAL-REMINDERS-01) — FIN-F

Backend en `test_finance_messages.py` y frontend en `frontend/__tests__/finance-messages.test.tsx`.

| Caso | Qué se comprueba |
|---|---|
| Pendiente | Plantilla A con el saldo actual y la moneda de la obligación. |
| Parcial | Plantilla D con el saldo restante. |
| Totalmente pagada | «Esta cuenta ya está pagada»; no hay mensaje. |
| Vencida | Plantilla C con `{{vencimiento}}` local. |
| Aún no vence | Plantilla B. |
| En disputa | Ninguna plantilla de cobro; el recordatorio interno sigue. |
| Cancelada o anulada | Sin mensaje. |
| Reparación con y sin saldo | Con saldo, el mensaje; sin saldo, el aviso de pagada. Cotización rechazada o reparación cancelada: sin mensaje. |
| Proveedor con y sin obligación | Con obligación, A o B; sin obligación, nada que preparar. «C» sólo con un pago confirmado. |
| Pago fijo vencido | Recordatorio interno con `{{beneficiario}}` y `{{estado}}`. |
| PEN y USD | El símbolo correcto; nunca un total que mezcle monedas. |
| Variables faltantes | Una obligatoria bloquea con el motivo; una opcional quita la frase; nunca queda un `{{…}}`. |
| Dos empresas | 404; `{{empresa}}` sale de la configuración de cada una. |
| Restricción de sucursal | Rechazo fuera de las sucursales visibles. |
| Sin permiso | Sin `finance.messages.prepare` ⇒ 403; la interfaz no muestra el botón. |
| Copia correcta | Llama a `navigator.clipboard.writeText` con el texto final y muestra «Mensaje copiado». |
| Portapapeles no disponible | Texto seleccionable y aviso; ningún error. |
| Mensaje editado | Se copia el texto editado; ninguna escritura en el servidor. |
| Copiado sin enviar | Nada cambia a «notificado»; no hay registro de contacto automático. |
| **Ninguna llamada a WhatsApp, Meta, correo ni SMS** | Backend: `provider_for`, `send_template`, `urlopen` y el envío de correo, simulados para que fallen si se llaman. Frontend: `fetch` sólo a `API_BASE`, sin `wa.me` ni `mailto:`. |
| Copiar no cambia el saldo | Saldo, `lifecycle` y auditoría iguales antes y después. |
| Sin datos privados | El texto no contiene DNI, dirección, IMEI, serie, banco ni `external_reference`. |
| Plantilla de la empresa | Variables fuera de la lista, enlaces o más de 1 000 caracteres ⇒ 400; sin `finance.templates.manage` ⇒ 403. |
| Sin `localStorage` | Ninguna escritura durante el flujo. |

### 11.3 Casos añadidos por la revisión

| Caso | Fase | Archivo |
|---|---|---|
| Día 31 mensual: 31 ene → 28/29 feb → 31 mar (sin deslizamiento) | B1 | `test_finance_calendar.py` |
| 29 feb anual en año no bisiesto; semanal; trimestral | B1 | `test_finance_calendar.py` |
| `end_date`: ningún período posterior | B1 | `test_finance_calendar.py` |
| Ancla en el pasado: sólo desde el período en curso salvo petición expresa | B1 | `test_finance_calendar.py` |
| Pausa y reanudación: no se rellena; «Generar este período» manual | B1 | `test_finance_generation.py` |
| La misma clave de idempotencia en otra obligación ⇒ 409 | B1 | `test_finance_payments.py` |
| Disputa: motivo, capacidad, auditoría; no cambia el saldo | B1 | `test_finance_obligations.py` |
| Totales por moneda, sin sumar PEN y USD | B1, C | `test_finance_obligations.py` |
| Preset de empresa nueva sin capacidades de asignación explícita | B1 | `test_finance_capabilities.py` |
| Al ponerse al día, sólo el último desfase | C | `test_finance_reminders.py` |
| Mismo documento con serie y número normalizados; dos fichas con un RUC ⇒ 409 | D | `test_finance_payables.py` |
| Línea de otra empresa u otro proveedor ⇒ rechazo; anular libera la línea | D | `test_finance_payables.py` |
| Importes de cuentas por pagar ocultos sin `costs.view` | D | `test_finance_payables.py` |
| Reparación cancelada; total 0; re-cotización; relleno por comando idempotente | E | `test_finance_receivables.py` |
| Alta manual no reexpresa una reparación | E | `test_finance_receivables.py` |

### 11.4 Lo primero de FIN-B1 (RED antes que GREEN)

1. `test_finance_calendar.py`: día 31, 29 de febrero, semanal, `end_date`, ancla en el pasado.
2. `test_finance_generation.py::test_monthly_rule_generates_one_period_per_month_idempotently`,
   `test_two_workers_generate_the_same_period_once`, `test_paused_rule_stops_future_periods_only`,
   `test_pause_voids_pre_generated_unpaid_periods_as_never_due`.
3. `test_finance_obligations.py::test_overdue_unpaid_period_stays_open_and_listed`,
   `test_overdue_state_uses_company_local_date`, `test_void_requires_reason_capability_and_no_payments`,
   `test_dispute_requires_reason_and_capability_and_keeps_balance`, `test_totals_are_grouped_by_currency`.
4. `test_finance_payments.py`: parcial y total, sobrepago, concurrencia, clave repetida, clave de otra obligación,
   reversión, borrado imposible, importe variable.
5. `test_finance_tenancy.py`: otra empresa da 404; sucursal no autorizada.
6. `test_finance_capabilities.py`: catálogo, concesión según el patrón `0149` y preset sin las de asignación
   explícita.

## 12. Preguntas al propietario

Las marcadas **(provisional)** ya tienen un valor por defecto en §2.

1. (provisional: un módulo, capacidades separadas) ¿Hacen falta roles distintos para pagar y para cobrar?
2. (provisional: sugeridas y editables) ¿La lista sugerida de categorías está bien?
3. (provisional: no) ¿Alguna capacidad sensible debe llegar al Administrador por defecto?
4. (provisional: no) ¿Se rellenan los períodos de una pausa al reanudar?
5. ¿Cuándo vence el saldo de una reparación: al quedar «Listo para recoger» más N días, al entregarse, otro?
6. (provisional: no) ¿Se admite un saldo a favor por sobrepago?
7. (provisional: no) ¿Se puede anular una obligación con pagos confirmados?
8. ¿Están bien los desfases 7/3/1/0/−1 y un aviso cada 7 días después de vencer?
9. ¿Las entradas genéricas de stock deben poder llevar proveedor?
10. (provisional: no) ¿Los cobros manuales de cuentas por cobrar son ingreso?
11. Legal: ¿los recordatorios **antes** del vencimiento son aceptables (hay un proyecto de ley)? ¿La orientación
    «sólo al titular, días hábiles 07:00–20:00» basta, o debe ser un bloqueo?
12. ¿Se registrará alguna vez la venta al crédito (con FISCAL-PROD-01)?
13. ¿Quién debe tener `finance.messages.prepare` y `finance.dispute`?
14. ¿El registro de contacto es útil, o se omite en la primera versión?

## 13. Bloqueos

- **BLOCKED: FIN-G y cualquier envío externo de mensajes de finanzas** (decisión FINANCE-MANUAL-REMINDERS-01; P0 de
  política de WhatsApp, §7.4).
- **BLOCKED: la venta al crédito** como origen (§1.5).
- **BLOCKED: compras contables, gastos, bancos y asientos** (como en `docs/adr-accounting.md` §3).
- **PENDIENTE de decisión:** el validador de PDF de FIN-B3 y si exige una dependencia nueva.

## 14. Fuentes (consultadas el 2026-10-25)

- [política] https://whatsappbusiness.com/policy/ (WhatsApp Business Messaging Policy; prohíbe el cobro de deudas)
- [categorías] https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization.md
- [opt-in] https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in.md
- [precios] https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing.md
- [límites] https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits.md
- [errores] https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
- Ley 29571, Código de Protección y Defensa del Consumidor, art. 62 (métodos abusivos de cobranza), y Ley 29733,
  art. 14. El texto vigente está por verificar en el portal del Congreso.
