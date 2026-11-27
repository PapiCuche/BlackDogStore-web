# ADR — TRADE-IN VALUATION PRO · Tarifario, reglas, elegibilidad, evaluación y cotización de equipos usados

Estado: **PROPUESTA** (diseño) con la **fase 1 en PR #457** (`feat/trade-in-tariff-phase1`, PARCIAL: sin fusionar ni
desplegar). Ninguna otra fase está implementada.

**Mandato.** El propietario encargó el 2026-11-27 (TRADE-IN VALUATION PRO) la valorización profesional de equipos usados
**fuera** de TRADE-IN-01B: tarifario, reglas, elegibilidad, evaluación y cotización. Ese mandato no decide ninguna de
D1–D12: valorizar y cotizar no es recibir, comprar ni acreditar.

Este documento diseña cómo una empresa **valoriza** un equipo usado que un cliente ofrece, y cómo se lo **cotiza**:
tarifario propio, reglas propias, elegibilidad, verificación asistida de IMEI o serie, evaluación guiada, cálculo,
cotización con vigencia, ticket, PDF y página pública de estado. **No compra, no paga, no acredita y no recibe nada**: la
recepción real del equipo y el crédito en caja siguen siendo TRADE-IN-01B / 01C ([adr-trade-in.md](adr-trade-in.md)),
bloqueados por D1–D12 ([decisiones-propietario-trade-in.md](decisiones-propietario-trade-in.md)).

**SaaS.** Black Dog Store es la empresa piloto. Ningún precio, política, color, lista de factores, texto comercial,
fuente de verificación, marca ni modelo vive en el código global: todo es dato de la empresa (panel, Excel o una
migración de datos del piloto). Los únicos vocabularios del código son técnicos y **cerrados** (estados, tipos de efecto,
resultados de una prueba, motivos de bloqueo de seguridad), como permite AGENTS.md §2.

Orden de autoridad: código > migraciones > tests > docs. Rutas sin prefijo: `backend/store/`. Referencias sobre `master`
`19fce9fa` (2026-11-27).

---

## 1. Qué existe hoy (clasificación)

| Pieza | Dónde | Estado | Qué aporta a este diseño |
|---|---|---|---|
| Cotización comercial SALES-QUOTE-01 (`SalesQuote`, `SalesQuoteItem`, revisiones, aprobar/enviar/aceptar/rechazar/convertir) | `models.py` `SalesQuote`, `sales_quote_services.py`, `sales_quote_views.py`, migración `0189` | **IMPLEMENTADO** (master y prod-02) | Patrón de **revisión, no edición** (`CONTENT_FIELDS`, `REVISABLE_STATUSES`, `superseded`), auditoría por transición, numeración por `InternalSequence`, rechazo de transiciones sobre una cotización vencida antes del cron (`_not_expired`, `sales_quote_services.py:829`). No se reutiliza la tabla: cotiza una **venta** (precio de caja por `build_pos_sale`), no una compra. |
| Documentos de la cotización: A4, ticket 80 mm, ESC/POS, cola de impresión | `sales_quote_documents.py` (`render_a4_pdf`, `ticket_pdf_from_ctx`, `escpos_from_ctx`), `printing/services.py` `enqueue_quote_ticket`, `PrintJob.Kind.SALES_QUOTE_TICKET` | **IMPLEMENTADO** | Trazado de 80 mm (`ticket_services._Cursor`), ESC/POS (`printing/escpos.py`), idempotencia por `Idempotency-Key`, impresora por sucursal. |
| Vencimiento de cotizaciones por cron | `management/commands/expire_sales_quotes.py`, `sales_quote_services.expire_due`, `test_sales_quote_expiry.py` | **IMPLEMENTADO** | Patrón cron idempotente con auditoría por fila. **PARCIAL** respecto de este diseño: vence por **fecha** (`valid_until` `DateField`) y con `timezone.localdate()` de la instalación (`TIME_ZONE='America/Lima'`), no a una hora exacta. |
| TRADE-IN-QUOTE-00: parte de pago **referencial** en la cotización (`SalesQuoteTradeIn`) | `models.py` `SalesQuoteTradeIn`, migración `0191`, #414 | **IMPLEMENTADO** (master y prod-02). El «PARCIAL hasta fusionar» de `decisiones-propietario-trade-in.md` estaba **OBSOLETO** (corregido en este PR). | Texto libre, últimos 4 del IMEI, valorización estimada manual (≤ total cotizado), saldo calculado y nunca cobrado. Se enlaza en el futuro con la cotización de recepción (§3.6), sin romperlo. |
| TRADE-IN-01B / 01C (recepción real, crédito en caja) | `docs/adr-trade-in.md` | **PROPUESTA · BLOCKED** (D1–D12) | Lo que este diseño **no** hace. |
| Caja: «Equipo como parte de pago» deshabilitado | POS (SALES-UX-02) | **IMPLEMENTADO** (deshabilitado) | Sigue deshabilitado. |
| Cotización de servicio técnico con ticket (`RepairQuote`) | `quote_ticket.py`, `RepairQuote` | **IMPLEMENTADO** | Leyenda «no es comprobante», IMEI enmascarado en ticket. |
| Selector compartido de equipos | `frontend/app/admin/lib/device-configurator.ts`, `admin/components/DeviceConfigurator.tsx`, `pos_device_services.search_models` / `configurations`, `configuration_resolver.py` | **IMPLEMENTADO** | Elegir modelo → capacidad → color → SIM → región con la paleta real de la empresa. Cómo se traduce a una entrada del tarifario: §8.1. |
| Catálogo: `ProductModel`, `VariantAttribute.role` (capacity/color/sim/region), `ModelOption`, `ModelOptionExclusion`, catálogo de referencia | `models.py`, `model_palette.py`, `catalog_reference*.py` | **IMPLEMENTADO** | El tarifario enlaza `ProductModel` opcionalmente; los roles dan capacidad/SIM/región. |
| Lector de escáner e IMEI | `scan_classifier.py` (`_imei_valid`), `device_identity.py` (`clean_imei` con Luhn, `clean_serial`, `mask`, `requires_imei`) | **IMPLEMENTADO** | Validar y enmascarar IMEI y serie; leer con pistola o cámara. |
| Fotos privadas | `evidence_storage.py`, `evidence_images.py`, `UnitPhoto` (`unit_media.py`), evidencias de servicio | **IMPLEMENTADO** | Mismo almacén, autorización propia; el decodificador decide el tipo. |
| Lista de comprobación configurable por empresa con instantánea | `QualityChecklistTemplate` / `QualityCheck` / `QualityCheckItem` (servicio, M11) | **IMPLEMENTADO** (servicio técnico) | Patrón para el checklist técnico de la evaluación. |
| Página pública por enlace opaco | `RepairTrackingLink` (uid + MAC con clave derivada `repair-tracking-link|…`, sin guardar el token), `tracking_services.py` (`url_for` con `settings.FRONTEND_URL`), `frontend/app/seguimiento/[token]` | **IMPLEMENTADO** (reparaciones) | Patrón exacto para el QR (§9). |
| QR en documentos | `fiscal/qr.py` (`render_qr_png`), QR en `printing/escpos.py` | **IMPLEMENTADO** (sólo fiscal) | Se reutiliza el render; el contenido es una URL. |
| Capacidades y concesión explícita | `capabilities.py` (`EXPLICIT_GRANT_CODES`), `tenancy.has_capability`, pruebas de cadena; reparación de instalación nueva (`0157`, `0163`, `0194`) porque `0061` siembra el Administrador desde el catálogo vivo | **IMPLEMENTADO** | Toda autoridad de este diseño es capacidad; ninguna por rol. |
| Guardia de paridad de pantallas H412b | `tests.H412bFrontendLegacyRoleParityTest.EXPECTED` | **IMPLEMENTADO** | Cada pantalla nueva (fases 6–7) se declara ahí. |
| `AdminAuditLog` con empresa | `models.AdminAuditLog.log(..., company=)` | **IMPLEMENTADO** | Toda escritura de este diseño. |
| Zona horaria de la empresa | `CompanySettings.timezone`; ayudante duplicado en `sales_analytics_views._company_tz` | **PARCIAL** (no hay un ayudante común) | La fase 1 añade `trade_in_tariff.company_timezone` / `company_today`. |
| Carga y descarga de Excel | `xlsx_reader.py`, `import_exports.py`, `money_input.py`, `import_freshness.py` | **IMPLEMENTADO** | El tarifario los reutiliza (fase 1). |
| Verificación de IMEI o serie ante OSIPTEL u otra fuente | — (no hay código; `grep -i osiptel` vacío) | **PENDIENTE** | Fase 3: **registro asistido y auditado**, sin API inventada ni scraping. |
| Tarifario de recepción por empresa | PR #457, migración `0194_trade_in_tariff` | **PARCIAL** (en rama) | Fase 1 de este diseño. |
| Motor de reglas, elegibilidad, evaluación, cotización de recepción, ticket y QR | este ADR | **PROPUESTA** | Fases 2–8. |

---

## 2. Principios (no negociables)

1. **Valorizar no es comprar.** Ninguna fase crea `Order`, `StockMovement`, `StockUnit`, cuenta por pagar, crédito,
   pago, comprobante fiscal ni cambia IGV. Pruebas guardianas por fase (conteos y llaves foráneas).
2. **Un bloqueo nunca es un descuento.** La elegibilidad se decide **antes** y **aparte** del valor; un bloqueo no se
   compensa con un importe menor ni con una aprobación. NO ELEGIBLE = sin valor (nulo), nunca cero «a cuenta».
3. **Lo aprobado no se reescribe.** Tarifas, reglas y cotizaciones son versiones de sólo añadir; una cotización emitida
   guarda la versión exacta del tarifario y de las reglas que usó, con sus valores copiados.
4. **Decimal en todo importe**, redondeo explícito `ROUND_HALF_UP` a 0,01 por línea; nunca `float`. Un importe fuera de
   rango es un 400 con su campo, nunca un 500.
5. **La empresa decide sus datos; el código, sus invariantes.** Tolerancias, vigencias, grupos, factores, efectos,
   fuentes de verificación y textos son de la empresa; el mínimo de bloqueos obligatorios (§3.3) es del código.
6. **Privacidad.** Ni el IMEI completo ni datos personales en auditoría, respuestas, ticket o página pública; el IMEI se
   muestra enmascarado (últimos 4). Hasta la recepción real (01B, D6/D10) se guarda **sólo** un resumen con clave
   (`imei_digest`) y los últimos 4. Fotos y evidencias que puedan mostrar el IMEI completo dependen de D6 (§3.3).
7. **Tiempo.** Fechas de tarifa en el calendario de la empresa; la vigencia de una cotización es un **instante**
   absoluto (`issued_at` + horas); la zona sólo decide cómo se muestra y qué día de tarifa aplica.
8. **Autoridad = capacidades**, empresa resuelta en el servidor, lo ajeno responde 404.

---

## 3. Modelo de datos

Todas las tablas llevan `company` con `on_delete=PROTECT` (también `TradeInPolicy`, 1:1) y se validan contra la empresa
de sus padres en `save()`.

### 3.1 Fase 1 — Tarifario (PR #457, migración `0194_trade_in_tariff`)

**`TradeInPolicy`** (1:1 con `Company`, `PROTECT`; se crea con el primer cambio; sin fila = valores por omisión)

| Campo | Tipo | Notas |
|---|---|---|
| `tariff_approval_mode` | `relevant` \| `all` | `relevant` por omisión. |
| `tariff_increase_tolerance_percent` | Decimal(5,2), 0–100 (CHECK) | 0 = cualquier subida es relevante. |
| `updated_by`, `updated_at` | | |

Fases siguientes añaden columnas con `db_default` (aditivas): `quote_validity_hours` (96),
`identity_check_validity_hours` (V2), `require_identified_customer` (D3: por omisión `true`, la recomendación (a)),
`ownership_proof_policy` (V3), `value_rounding_step` (V5), `optional_blockers` y `extra_blockers` (§3.3).

**`TradeInTariffEntry`** — el equipo, con las palabras de la empresa.

| Campo | Notas |
|---|---|
| `product_model` (FK nulo, `SET_NULL`) | Modelo del catálogo de la **misma** empresa. Opcional: se reciben modelos que ya no se venden. |
| `brand`, `model_name`, `capacity`, `sim`, `region` | Texto de la empresa (como `Device.brand/model`). `sim`/`region` vacíos = la entrada no distingue ese rol (§8.1). |
| `device_key` | Las cinco palabras plegadas (`configuration_resolver.fold`: minúsculas, sin acentos, espacios colapsados; capacidad sin espacios) unidas por `|`. **UNIQUE(company, device_key)**. Lo escribe sólo `trade_in_tariff.device_key`. |
| `is_active`, `deactivated_at/by`, `deactivation_reason` | Se desactiva, nunca se borra. La identidad no cambia (`PATCH` sólo del enlace al catálogo). |
| `created_by/at`, `updated_at` | |

CHECK: `brand` y `model_name` no vacíos.

**`TradeInTariffVersion`** — los importes desde una fecha. Sólo añadir.

| Campo | Notas |
|---|---|
| `entry`, `number` | UNIQUE(entry, number), `number ≥ 1`. |
| `status` | `pending` → `approved` \| `rejected` \| `withdrawn`. |
| `currency` | Copia de `CompanySettings.currency`. |
| `base_reception_value` | Decimal(12,2) ≥ 0, ≤ 9 999 999 999,99. Lo que la tienda paga por el equipo en buen estado, antes de reglas. |
| `refurbishment_cost`, `resale_value`, `target_margin` | Decimal(12,2) ≥ 0 o nulo (= sin estimar). **Internos** (§4). |
| `max_reception_value` | Decimal(12,2) ≥ 0; CHECK `base ≤ max`. Como las reglas sólo restan, el calculado nunca pasa del base: el máximo **sólo limita los ajustes manuales hacia arriba** (§6 paso 7). |
| `valid_from`, `valid_until` | `DateField` en el calendario de la empresa, fin inclusivo, nulo = sin fin. CHECK `until ≥ from`. Un cambio no empieza antes de hoy (no retroactivo). |
| `reason`, `source` (`manual`/`import`), `import_batch` | |
| `previous` | La versión contra la que se midió la subida (ver abajo). |
| `approval_reasons` | `new_entry`, `base_increase`, `max_increase`, `validity_extended`, `starts_earlier`, `policy_all`. |
| `approval_kind` | `approver` \| `self` \| `policy`. |
| `proposed_by/at`, `decided_by/at`, `decision_note` | Quién y cuándo. |

Restricciones: **un solo `pending` por entrada** (UNIQUE parcial), importes ≥ 0, `base ≤ max`, ventana ordenada. En el
modelo: una versión decidida no se guarda ni se borra; una pendiente no cambia sus términos (`TERMS_FIELDS`).

**Cambio relevante** (`approval_reasons`, decisiones del propietario y de la revisión de #457, 2026-11-27):

- **Primer precio** del equipo: siempre.
- **Subida** del base o del máximo por encima de la tolerancia, medida contra la última versión **aprobada por una
  persona** (`approval_kind` `approver` o `self`), nunca contra una aprobada por la política: así cinco subidas
  encadenadas del 5 % con tolerancia del 10 % no esquivan al aprobador (la tercera ya pide aprobación).
- **Vigencia más larga** que la de la última aprobada.
- **Empieza antes**: un `valid_from` anterior al de la última aprobada; y además se compara contra la versión que
  estaría en vigor en el nuevo `valid_from` (si un precio aprobado empieza dentro de 30 días, proponer el mismo importe
  desde hoy adelanta ese precio y pide aprobación).
- Con el modo `all`, todo cambio.

**En vigor un día D** (decisión del propietario, 2026-11-27; `trade_in_tariff.effective_version`): de una entrada
activa, la versión `approved` con número más alto que **ya empezó** (`valid_from ≤ D`), si su fin no pasó
(`valid_until` nulo o `≥ D`); si pasó, **no hay precio**. Una versión nueva reemplaza a las anteriores **para siempre**:
cuando una nueva con fecha de fin vence, la anterior **no vuelve**; hace falta aprobar otra. Una versión aprobada con
inicio futuro deja en vigor la actual hasta su día. Si se aprueba una pendiente cuyo `valid_from` ya pasó (esperó
aprobación), su inicio se mueve a **hoy** al aprobarla (no es retroactiva); queda en la auditoría.

**`TradeInTariffImport`** — vista previa y aplicación de un Excel: `file_sha256` (nunca el archivo), filas normalizadas
con su acción (`create`/`update`/`unchanged`/`error`) y la versión contra la que se comparó, resumen, quién y cuándo
previsualizó y aplicó. Un «Vigente desde» vacío se guarda vacío y se resuelve a «hoy» **al aplicar** (una vista previa
que cruza la medianoche no queda retroactiva). Al aplicar, una entrada desactivada o con otra versión desde la vista
previa → `preview_stale` con el número de fila. El archivo va por el tramo general de 1 MiB (sin cambio de proxy) y la
vista lo limita a 960 KB.

### 3.2 Fase 2 — Reglas de valorización

**`TradeInFactorGroup`**: grupos de la empresa. `kind` es vocabulario técnico cerrado: `battery`, `cosmetic`,
`functional`, `repairs`, `accessories`, `custom`; `name` y `sort_order` son de la empresa. `combine` = `max` (por
omisión: dentro del grupo sólo cuenta la mayor deducción) o `sum`; `group_cap_percent` (Decimal, nulo = sin tope) sobre
el valor base.

**`TradeInFactor`**: una pregunta de la evaluación. `group`, `code` (slug único por empresa), `label`, `input`
(`check` = resultado técnico §5.2, `percent` = p. ej. salud de batería, `choice` = opciones de la empresa,
`boolean`), `options` (JSON corto para `choice`), `component` (texto de la empresa, p. ej. «pantalla»: clave para no
penalizar dos veces, §6 paso 5), `applies_to` (filtros opcionales por `brand`/`model_name`/`product_model`),
`is_active`, `sort_order`, `requires_photo`.

**`TradeInRuleSet`** + **`TradeInRuleSetVersion`**: las reglas se versionan **en bloque** (una versión = el conjunto
completo, inmutable al aprobarse), con la misma máquina, la misma regla de relevancia y la misma regla de vigencia que
el tarifario (una versión nueva reemplaza para siempre; si vence, no hay reglas en vigor y no se calcula). Una
evaluación guarda el id de la versión que usó.

**`TradeInRule`** (filas de una versión): `factor`, `when` (resultado o rango: p. ej. `outcome=not_working`,
`percent_lt=80`), `effect` (cerrado), `priority` (entero; mayor gana entre reglas de **importe**), `owner` (usuario
responsable), `notes`.

| `effect` | Clase | Significado | Campo |
|---|---|---|---|
| `none` | importe | Sin penalización | — |
| `fixed` | importe | Resta un importe fijo | `amount` Decimal ≥ 0 |
| `percent` | importe | Resta un % del valor **base** (no compuesto) | `percent` Decimal 0–100 |
| `repair_cost` | importe | Resta el costo de reparación previsto | `amount` |
| `requires_approval` | estado | El resultado necesita aprobación | — |
| `pending_verification` | estado | Queda PENDIENTE DE VERIFICACIÓN hasta comprobarlo | — |
| `not_eligible` | estado | NO ELEGIBLE por condición técnica | — |

### 3.3 Fase 3 — Elegibilidad y verificación de identidad

**Bloqueos** (vocabulario cerrado en código, nombres de seguridad genéricos, sin fabricante): `theft_or_loss_report`,
`imei_invalid`, `imei_duplicate`, `imei_cloned`, `imei_tampered`, `activation_lock`, `previous_account`,
`mdm_enrolled`, `illicit_signs`, `ownership_unproven`, `identifiers_mismatch`, `dangerous_condition`. Cada uno se
responde en la evaluación como `clear`, `blocked` o `not_verified`.

- **Mínimo obligatorio que ninguna empresa apaga** (constante del código): `theft_or_loss_report`, `imei_invalid`,
  `activation_lock`, `ownership_unproven`, `identifiers_mismatch`, `dangerous_condition`. Para un equipo **sin IMEI**,
  `imei_invalid` se lee sobre la serie (`device_identity.clean_serial`).
- **Los demás** son obligatorios por omisión; la empresa puede marcar como no aplicables los que su operación no
  verifica (`policy.optional_blockers`), nunca los del mínimo. La empresa **añade** los suyos (`extra_blockers`:
  código + texto) con el mismo efecto: `blocked` = NO ELEGIBLE.
- `imei_duplicate` lo calcula el servidor: el resumen del IMEI coincide con otra evaluación abierta de la empresa o el
  IMEI escrito existe en `StockUnit` de **la misma empresa** (consulta por el valor escrito en la petición, que no se
  guarda); si es una unidad que la empresa vendió, se informa (D9) sin decidir.

**`TradeInVerificationSource`** (de la empresa): `name`, `url` (https, pestaña nueva), `identifier` (`imei` \|
`serial`), `checks` (`theft_report`, `activation_lock`, …), `instructions`, `allowed_evidence`
(`none`/`screenshot`/`photo`), `is_active`. La fuente oficial del piloto (Checa tu IMEI de OSIPTEL) se da de alta como
**dato** del piloto, no en código. Para equipos sin IMEI (portátiles, tabletas sólo Wi-Fi) la empresa da de alta
fuentes por **serie** (p. ej. la comprobación de bloqueo de activación del fabricante) o, si no hay fuente, la
verificación es una **declaración** del evaluador sobre el equipo encendido (bloqueo de activación desactivado, sin
cuenta previa ni MDM), registrada igual.

**`IdentityVerificationRecord`** (append-only): `assessment`, `source`, `identifier` (`imei` \| `serial`), `slot`
(1 = IMEI 1 o serie, 2 = IMEI 2), `last4`, `digest`, `checked_at` (declarado por quien consultó, ≤ ahora), `result`
(`no_report`, `reported`, `invalid`, `not_found`, `unavailable`, `declared_clear`), `evidence` (foto privada opcional
según la fuente y D6), `recorded_by/at`, `valid_until` = `checked_at` + `policy.identity_check_validity_hours`. **No hay
API ni scraping**: la persona consulta la fuente oficial y registra lo que vio; el sistema guarda, enmascara, audita y
exige vigencia. **Doble SIM**: si el equipo declara IMEI 2, también se verifica (slot 2); un `reported` en cualquiera es
`theft_or_loss_report`. La emisión de la cotización y la recepción final (01B) exigen registros **vigentes**: si
vencieron, se vuelve a consultar.

**Resumen con clave (`digest`)**: HMAC-SHA256 con una clave **derivada** de `SECRET_KEY` y la etiqueta
`trade-in-identity|<company_id>` (un uso, una clave, como `tracking_services._key`). Nunca sale en una respuesta ni en la
auditoría. Si `SECRET_KEY` rota, los resúmenes anteriores dejan de coincidir: sólo se pierde la detección de duplicados
contra evaluaciones viejas (no hay dato que recuperar); se documenta en el runbook de rotación.

**Fotos y evidencias.** Una foto de la bandeja SIM, de la caja o de una pantalla de ajustes **suele mostrar el IMEI
completo**. Por eso:
- Toda foto lleva `may_show_identifier` (lo marca el evaluador; las de categorías «identificación», «caja» y
  «evidencia de consulta» lo llevan siempre).
- Mientras **D6** no esté decidida, las fotos con esa marca **no se guardan**: la fase 3 registra la verificación sin
  evidencia y la fase 4 sólo admite fotos de estado (cosmético/funcional) sin identificador. Con D6 decidida se activan,
  con su retención (borrado programado a los N días de cerrar la evaluación), acceso sólo con `sales.tradein.value` en
  la sucursal de la evaluación y auditoría de cada descarga.

### 3.4 Fase 4 — Evaluación y cálculo

**`TradeInAssessment`**: `branch` (de las visibles del operador), `number` (`InternalSequence` kind
`trade_in_assessment`), `status` (§5.1), `purpose` (`valuation_only` \| `trade_towards`), `customer` (FK nulo a
`Customer`; obligatorio si `policy.require_identified_customer`, D3), identidad del equipo (`tariff_entry`,
`product_model`, las cinco palabras copiadas, `has_imei`, `imei_last4`, `imei_digest`, `imei2_last4`, `imei2_digest`,
`serial_last4`, `serial_digest`), procedencia (`provenance` cerrado: `own_purchase`, `gift`, `other` + nota), filas de
bloqueos, filas `TradeInAssessmentAnswer` (factor, resultado, valor, nota), fotos (`TradeInAssessmentPhoto`, privadas,
§3.3), `calculation` (instantánea §6), `created_by/at`, `updated_at`.

Para `trade_towards`: `target_product` (o `target_unit` para un equipo concreto) o `target_sales_quote` (FK nulo a
`SalesQuote`). El precio que se **copia** al calcular es el que cobraría la caja **hoy** en la sucursal de la evaluación
(`pos_services.unit_prices` / `build_pos_sale` sin descuento manual: precio del producto, oferta o precio propio del
equipo), o el `total` de la cotización de venta enlazada. Enlazar una cotización de venta exige además
`sales.quotes.manage`. La diferencia se **muestra**; nunca se guarda como descuento ni toca IGV (§6.6).

### 3.5 Fase 5 — Cotización de recepción

**`TradeInQuote`**: `assessment`, `number` (`InternalSequence` kind `trade_in_quote`, una por cotización; las
revisiones lo comparten), `revision`, `origin` (`new`/`revision`), `source_quote` (la revisión que reemplaza),
`revision_reason` (obligatorio al revisar una `accepted`), `status` (§5.3), `issued_at`, `valid_until`
(**DateTimeField**, instante absoluto = `issued_at` + `policy.quote_validity_hours` horas, por omisión 96, en UTC; la
zona de la empresa sólo decide cómo se muestra), `expired_at`, `superseded_at`, `approved_by/at`, `approval_kind`
(`approver` \| `self`), `decided_by/at`, `decision_note`, `public_uid` (**uno por revisión**; el token es uid + MAC y no
se guarda).

Instantánea inmutable (no se recalcula nunca): `tariff_version` (FK `PROTECT`) con base y máximo copiados,
`rule_set_version` (FK `PROTECT`), `calculation` (líneas aplicadas y suprimidas), `computed_value`, `offered_value`,
`eligibility` (sin la bandera de aprobación, §5.3) y `needs_approval`, `target_price` y `difference` si `trade_towards`,
texto de condiciones de la empresa copiado. Columnas **internas** aparte: `refurbishment_cost`, `resale_value`,
`target_margin` copiados del tarifario (§4: sólo las ve quien tiene `sales.tradein.tariff.view` o `costs.view`).
Restricciones como `SalesQuote`: UNIQUE(company, number, revision), una revisión viva por número (UNIQUE parcial
`status <> superseded`), `offered_value ≥ 0` o nulo, `offered_value ≤ max_reception_value` copiado.

**`PrintJob`** gana el kind `trade_in_quote_ticket` y la FK nula `trade_in_quote` (como `sales_quote` en 0189), con la
restricción de destino reescrita (`_v3`). Ver §10 para la guarda de vuelta atrás.

### 3.6 Relación con TRADE-IN-QUOTE-00

`SalesQuoteTradeIn` sigue igual. En la fase 5 puede ganar `trade_in_quote` (FK nulo): si la cotización de venta se
arma con una cotización de recepción **emitida y vigente**, el bloque copia su valor ofrecido en lugar del texto libre.
Se mantiene la regla existente del bloque (valorización ≤ total cotizado, `trade_in_exceeds_total`): si el valor
ofrecido supera el total de la venta, **no se enlaza** (D4 pendiente; no hay saldo a favor). Enlazar exige
`sales.quotes.manage` y `sales.tradein.view`. Sigue siendo referencial: la venta se cobra completa hasta 01C.

---

## 4. Capacidades

Todas **ACTIVE** y de **concesión explícita** (`EXPLICIT_GRANT_CODES`): nadie las recibe por defecto (recomendación
D7, decisión V1); el MASTER las concede desde Roles. Ninguna se deriva de `inventory.adjust`, `sales.pos.use` ni
`sales.quotes.*`.

| Código | Fase | Abre |
|---|---|---|
| `sales.tradein.tariff.view` | 1 | Ver y exportar tarifario (con estimados internos), reglas (fase 2) y política; ver los estimados internos de una cotización. |
| `sales.tradein.tariff.manage` | 1 | Proponer precios, importar, desactivar, enlazar catálogo; en la fase 2, borradores de reglas, grupos y factores; fuentes de verificación. |
| `sales.tradein.tariff.approve` | 1 | Aprobar/rechazar cambios relevantes de tarifario y reglas, reactivar, política. |
| `sales.tradein.tariff.approve_own` | 1 | Aprobar un cambio propio con motivo (marcado en auditoría). |
| `sales.tradein.view` | 3 | Ver evaluaciones y cotizaciones de recepción (sin estimados internos), reimprimir. |
| `sales.tradein.value` | 3–5 | Crear y editar evaluaciones, registrar verificaciones, calcular, generar y emitir una cotización que no requiere aprobación, imprimir. (Nombre ya propuesto en `adr-trade-in.md` §3.6.) |
| `sales.tradein.approve` | 4–5 | Aprobar/rechazar una cotización que requiere aprobación; ajustar el valor ofrecido por encima del calculado, hasta el máximo del tarifario. |
| `sales.tradein.approve_own` | 5 | Aprobar una cotización propia (que uno generó o ajustó), con motivo y marca en la auditoría (patrón DEC-INV-06). Sin ella, quien la generó no la aprueba. |

Los **estimados internos** (reacondicionamiento, reventa, margen) sólo se devuelven a quien tiene
`sales.tradein.tariff.view` o `costs.view` (como el costo de una unidad, `adr-trade-in.md` §3.6); nunca en ticket, PDF ni
página pública. En la fase 1, una acción del tarifario hecha por alguien **sin** `tariff.view` (p. ej. sólo `approve`)
responde sólo la versión afectada, no la ficha con estimados. Ningún permiso convierte un bloqueo en valor ni salta una
verificación pendiente.

Por pantalla nueva (fases 6–7), `H412bFrontendLegacyRoleParityTest.EXPECTED` gana su línea con la capacidad que la abre
y `frozenset()` como conjunto legacy.

---

## 5. Máquinas de estado

Todas en servicios, con `select_for_update(of=('self',))` sobre la fila y su padre, tabla de transiciones cerrada y
auditoría por transición (con la nota o el motivo, si lo hay).

### 5.1 Evaluación

```
draft ──calcular──▶ calculated ──(editar)──▶ draft
calculated ──generar cotización──▶ quoted       (crea la revisión 1 de TradeInQuote en draft; la evaluación se congela)
draft|calculated ──cancelar──▶ cancelled
```

Una evaluación `quoted` no se edita: revisar la cotización copia la evaluación a una nueva en `draft`.

### 5.2 Elegibilidad (derivada en cada cálculo; precedencia de arriba abajo, gana la primera)

| Estado | Cuándo |
|---|---|
| **NO ELEGIBLE** | Cualquier bloqueo `blocked`; o una regla `not_eligible`; o identificador inválido. |
| **PENDIENTE DE VERIFICACIÓN** | Un bloqueo exigido en `not_verified`; falta un registro de verificación vigente (IMEI 1, IMEI 2 si existe, o serie); o una regla `pending_verification`. |
| **REQUIERE APROBACIÓN** | Alguna regla `requires_approval`; o el valor ofrecido supera el calculado (ajuste manual). |
| **APTO CON DESCUENTOS** | Hay al menos una deducción aplicada. |
| **APTO** | Nada de lo anterior. |

Resultados técnicos de un factor `check` (cerrado): `works`, `detail`, `not_working`, `replaced`, `not_verified`,
`not_applicable`.

### 5.3 Cotización de recepción y revisiones

```
draft ─┬─(needs_approval)──solicitar──▶ pending_approval ─┬─aprobar──▶ approved ──emitir──▶ issued
       │                                                   └─rechazar─▶ rejected
       └─(no la necesita)──confirmar──────────────────────────────────▶ approved ──emitir──▶ issued
issued ──vence (valid_until < ahora)──▶ expired        («VENCIDA»: el cron la estampa; toda lectura la deriva)
issued ──el cliente acepta / rechaza──▶ accepted | declined
draft|pending_approval|approved ──cancelar──▶ cancelled
approved|issued|accepted|declined|expired|rejected ──revisar──▶ nueva revisión N+1 en draft; la N pasa a superseded
```

- «Generar cotización» (evaluación → `quoted`) y «emitir» (`approved` → `issued`) son actos distintos: el primero crea
  el borrador; el segundo fija `issued_at` y `valid_until` y habilita imprimir y el enlace público.
- Sólo `draft` se edita. Una revisión **recalcula** con el tarifario y las reglas **en vigor ahora**; la N conserva su
  instantánea: nada es retroactivo. Revisar una `accepted` exige motivo (`revision_reason`), auditado.
- **Aprobar, emitir y aceptar rechazan una cotización vencida aunque el cron no haya pasado** (`valid_until < ahora`,
  como `sales_quote_services._not_expired`). Aprobar y emitir rechazan además una instantánea **desfasada**
  (`calculation_stale`, 409): la versión del tarifario o de las reglas que usó ya no es la que está en vigor hoy en el
  calendario de la empresa (reemplazada, vencida o entrada desactivada). Se recalcula en el borrador o con una revisión.
- El ticket y el PDF imprimen el **estado de la cotización** y la **elegibilidad sin la bandera de aprobación**
  (APTO / APTO CON DESCUENTOS): sólo se imprime una cotización `issued` (ya aprobada), así que nunca un «REQUIERE
  APROBACIÓN» congelado.
- NO ELEGIBLE nunca llega a `approved`/`issued`: no tiene valor (V4: se informa en pantalla).
- PENDIENTE DE VERIFICACIÓN no se aprueba ni se emite (V2).
- `accepted` no recibe el equipo: lo hará 01B, que leerá sólo una cotización **viva** (`accepted`, no `superseded`,
  no vencida) y exigirá verificaciones vigentes.

---

## 6. Algoritmo de cálculo (`trade_in_valuation.calculate`, puro, sin escribir)

Entrada: evaluación, instante `t` (ahora), empresa. Salida: instantánea §6.5.

1. **Tarifa.** `effective_version(entry, on=company_today(t))` (§3.1). Sin versión en vigor → no hay cálculo
   (`tariff_missing`).
2. **Reglas.** Versión del conjunto de reglas en vigor en `company_today(t)`. Sin versión → `rules_missing`.
3. **Reglas aplicables.** Para cada respuesta, las reglas de su factor cuyo `when` coincide y cuyo `applies_to` incluye
   el equipo.
   - **Reglas de estado** (`requires_approval`, `pending_verification`, `not_eligible`): **se aplican todas**, sea cual
     sea su prioridad; se registran como banderas.
   - **Reglas de importe** (`none`, `fixed`, `percent`, `repair_cost`): **una sola por factor**, la de mayor `priority`
     (empate: la de mayor deducción, luego el id menor).
4. **Importe de cada deducción** sobre el **valor base** (no compuesto): `fixed` = `amount`; `percent` =
   `base × percent / 100`; `repair_cost` = `amount`. Cada línea se redondea a 0,01 (`ROUND_HALF_UP`).
5. **Sin doble penalización**, en este orden fijo:
   1. **Por componente**, antes de agrupar y con los importes de línea: entre líneas con el mismo `component` (de
      cualquier grupo), sólo cuenta la mayor; las demás quedan `suppressed_by` esa línea. Ej.: «pantalla rota»
      (cosmético, 15 %) y «pantalla reemplazada» (reparaciones, S/ 120): cuenta la mayor. La comparación es por importe
      de línea, **antes** de los topes de grupo (a propósito: el tope de un grupo no decide qué falla del equipo pesa más).
   2. **Por grupo.** `combine=max`: sólo la mayor del grupo; `combine=sum`: suma.
   3. **Tope de grupo.** `group_cap_percent` × base.
   4. **Tope total.** La suma nunca supera el valor base.
6. **Valor calculado** = `max(0, base − deducciones)` (**nunca negativo**). Como sólo se resta, nunca pasa del base.
7. **Valor ofrecido.** Por omisión = calculado (redondeado hacia abajo a `value_rounding_step`, si existe, V5). Ajuste
   manual: hacia abajo, con motivo, `sales.tradein.value`; hacia arriba, con motivo, **sólo hasta
   `max_reception_value`**, y deja `needs_approval` (`sales.tradein.approve`).
8. **Elegibilidad final** con §5.2. Si es **NO ELEGIBLE**: `computed_value = null` y `offered_value = null` (las líneas
   se guardan para explicar, pero no hay valor); PENDIENTE DE VERIFICACIÓN conserva el valor como **provisional** y no se
   emite.

### 6.5 Instantánea

`{tariff: {version_id, number, base, max, currency}, rules: {version_id, number}, lines: [{factor, outcome, rule_id,
effect, amount, component, group, suppressed_by}], status_rules: [...], totals: {deductions, computed_value,
offered_value, rounding}, flags: [...], eligibility, needs_approval, computed_at}`. Los estimados internos se copian en
columnas internas (§3.5), nunca en la instantánea pública.

### 6.6 Dos casos

- **Sólo valorización** (`valuation_only`): el documento dice cuánto pagaría la tienda y hasta cuándo.
- **Parte de pago hacia otro equipo** (`trade_towards`): además, el precio del equipo destino (§3.4) y la
  **diferencia** = precio − valor ofrecido. Es informativa: no es descuento, no cambia el IGV, no crea crédito ni saldo.
  Si el valor ofrecido supera el precio, la diferencia se muestra como 0 («sin diferencia a pagar») y no hay saldo a
  favor (D4 pendiente).

---

## 7. API (`/api/admin/trade-in/…`; la empresa sale de la sesión, `?company=` sólo selecciona)

| Fase | Ruta | Método | Capacidad |
|---|---|---|---|
| 1 | `tariff/` (`q`, `brand`, `model`, `capacity`, `sim`, `region`, `active`, `pending`, `product_model`, `page`) | GET · POST | view · manage |
| 1 | `tariff/<id>/` | GET · PATCH (sólo `product_model`) | view · manage |
| 1 | `tariff/<id>/versions/` | POST | manage |
| 1 | `tariff/<id>/deactivate/` · `reactivate/` | POST | manage · approve |
| 1 | `tariff/versions/<id>/approve/` · `reject/` · `withdraw/` | POST | approve · approve · manage (quien propuso o quien aprueba) |
| 1 | `tariff/export/` (mismos filtros) | GET (.xlsx) | view |
| 1 | `tariff/import/preview/` (multipart `file`, ≤ 960 KB, 413 `file_too_large`) · `tariff/import/<id>/apply/` | POST | manage |
| 1 | `policy/` | GET · PUT | view o approve · approve |
| 2 | `rules/groups/`, `rules/factors/`, `rules/versions/` (borrador, proponer, aprobar, rechazar, retirar) | CRUD | tariff.* |
| 2 | `rules/preview/` (calcular un caso de prueba sin guardar) | POST | tariff.view |
| 3 | `verification-sources/` | CRUD | tariff.manage |
| 3 | `assessments/<id>/identity-checks/` | GET · POST | tradein.view · tradein.value |
| 4 | `tariff/match/` (modelo y opciones del selector → entrada, §8.1) | GET | tradein.value |
| 4 | `assessments/` · `assessments/<id>/` · `…/answers/` · `…/photos/` · `…/calculate/` · `…/cancel/` · `…/quote/` | CRUD | tradein.view · tradein.value |
| 5a | `quotes/` · `quotes/<id>/` · `…/request-approval/` · `…/approve/` · `…/reject/` · `…/issue/` · `…/accept/` · `…/decline/` · `…/revise/` · `…/cancel/` | GET · POST | tradein.view · value · approve (/approve_own) |
| 5b | `quotes/<id>/pdf/?format=a4|ticket` · `quotes/<id>/print/` (`Idempotency-Key`) | GET · POST | tradein.view |
| 5c | Público: `GET /api/trade-in/status/<token>/` | — | ninguna (token opaco; límite por IP; sólo estado y vigencia) |

Errores con `code` legible (`pending_exists`, `own_change`, `retroactive`, `unchanged`, `preview_stale`,
`preview_expired`, `file_too_large`, `tariff_missing`, `tariff_ambiguous`, `rules_missing`, `calculation_stale`,
`not_eligible`, `expired`, …). Lo ajeno o inexistente: 404 igual. Las subidas de fotos (fase 4) usan el tramo `image`
existente sólo si su ruta encaja en él; si no, tope propio en la vista dentro del tramo general: **ningún cambio de
`deploy/` ni de Caddy sin su GO propio**.

---

## 8. Pantallas y flujos (fases 6–7)

**Panel · Parte de pago** (entrada de menú propia, visible con cualquier `sales.tradein.*`):

1. **Tarifario** (`/admin/trade-in/tariff`): tabla con búsqueda y filtros, insignias «Pendiente de aprobación» y
   «Desactivado»; ficha con historial de versiones; proponer precio, aprobar/rechazar (según `actions` del servidor),
   desactivar/reactivar; exportar e importar con vista previa por filas.
2. **Reglas** (`/admin/trade-in/rules`): grupos y factores; borrador de versión con reglas por factor, «Probar un caso»,
   proponer y aprobar.
3. **Configuración** (`/admin/trade-in/settings`): política (aprobación, tolerancia, vigencia de cotización en horas,
   vigencia de verificación, cliente identificado, redondeo, bloqueos opcionales y propios, textos y condiciones),
   fuentes de verificación.
4. **Evaluación guiada** (`/admin/trade-in/assessments/new`), en pasos, pensada para móvil:
   1. *Identificación*: `DeviceConfigurator` (§8.1) o búsqueda en el tarifario; IMEI/serie por escáner o teclado,
      validado y enmascarado al salir del campo; «el equipo no tiene IMEI» (portátil, tableta Wi-Fi) pide la serie.
   2. *Procedencia y seguridad*: cliente (si la política lo exige), procedencia, bloqueos uno por uno, verificación
      asistida (abre la fuente oficial en pestaña nueva; formulario «resultado, fecha y hora, evidencia si D6 lo
      permite»; IMEI 2 si existe). Un bloqueo marcado termina el flujo en NO ELEGIBLE, sin importes.
   3. *Checklist técnico*: factores agrupados, seis resultados, notas, fotos de estado.
   4. *Resumen del cálculo*: base, deducciones aplicadas y suprimidas («no se cuenta dos veces: ya se descontó por…»),
      topes, valor calculado, ajuste manual con motivo, elegibilidad con su explicación; si es parte de pago, precio
      destino y diferencia («no es un descuento»).
   5. *Revisión*: guardar, generar cotización, solicitar aprobación, aprobar/rechazar (si puede), emitir; imprimir.
5. **Cotizaciones de recepción** (`/admin/trade-in/quotes`): lista por estado (VENCIDA derivada), detalle con
   revisiones, reimprimir la revisión exacta, revisar.

Responsive 320–escritorio, teclado y foco visible, tablas anchas en su contenedor, textos de la empresa por tokens.

### 8.1 Del selector a la entrada del tarifario (`tariff/match/`)

El selector entrega `product_model` y una opción por rol (capacidad, color, SIM, región). El **color no cuenta**. El
servidor busca, en este orden, y se queda con el **primer nivel que tenga resultados**:

1. Entradas activas con ese `product_model` cuya capacidad, SIM y región plegadas (misma regla que `device_key`,
   aplicada a las etiquetas de las opciones) son **exactamente** las elegidas.
2. Las mismas, pero aceptando `sim` y/o `region` **vacíos** en la entrada como «no distingue» (comodín). Una entrada con
   SIM o región escrita y distinta nunca coincide.
3. Sin enlace al catálogo: entradas cuyo `model_name` plegado es el nombre plegado del modelo
   (`device_catalog.normalize_model_name`), con las mismas dos pasadas.

Una coincidencia: esa entrada. **Varias en el mismo nivel** (p. ej. dos marcas con el mismo nombre de modelo):
`tariff_ambiguous`, la persona elige en la búsqueda del tarifario; nunca se elige en silencio. Ninguna:
`tariff_missing` con la opción de pedir el precio (alta de entrada por quien tiene `tariff.manage`).

---

## 9. Impresión y QR

- **Un contexto, tres salidas** (5b), como `sales_quote_documents`: `build_context(quote)` lee **sólo la instantánea** de
  esa revisión → A4 PDF, ticket 80 mm PDF y ESC/POS (`printing/escpos.py`, `ticket_services._Cursor`). La cola
  (`printing/services.enqueue_trade_in_quote_ticket`) copia `enqueue_quote_ticket`. **Reimprimir** = mismo contexto de la
  misma revisión; nunca recalcula.
- **Contenido**: logo y datos de la empresa, título y leyenda de `document_legends` («Cotización de recepción de equipo
  usado · No es comprobante de pago ni constancia de compra · Valor sujeto a verificación final»), número y revisión,
  equipo, **IMEI o serie enmascarados**, estado de la cotización y elegibilidad sin bandera de aprobación (§5.3), valor
  ofrecido, vigencia exacta («Válida hasta el 01/12/2026 18:30», mostrada en la zona de la empresa), condiciones de la
  empresa, diferencia si es parte de pago, y el QR.
- **Nunca**: costos de reacondicionamiento, reventa, margen, notas internas, IMEI o serie completos, DNI, reglas ni
  deducciones internas (V7).
- **QR y página pública (5c)**: `f'{settings.FRONTEND_URL}/cotizacion-equipo/<token>'`, como
  `tracking_services.url_for` (hoy no hay dominio por empresa). `token = public_uid + MAC` con una clave **derivada
  propia** (`sha256(b'trade-in-quote-status|' + SECRET_KEY)`, no la de seguimiento de reparaciones), **un `public_uid`
  por revisión** (el papel de la revisión 1 dice «reemplazada» cuando hay una 2). La API pública tiene límite de
  peticiones por IP, responde 404 igual para un token manipulado o inexistente, y la página lleva `noindex`,
  `no-referrer` y ninguna medición. Muestra **sólo** empresa, número-revisión, estado (vigente / vencida / reemplazada /
  aceptada / anulada) y vigencia.
- `names_fiscal_document` (guardián de documentos) debe seguir fallando si alguien titula esto boleta o factura.

---

## 10. Migraciones

Todas aditivas, una por fase que cambie esquema, numeradas sobre la hoja de `master` del momento (la línea SUNAT no
añade migraciones de `store` sin coordinar). **Cada nodo ≥ 0189 termina con `refuse_reverse_with_quote_tickets`** y se
añade a `test_migration_reverse_guards.NODES`.

**Toda fase que añade capacidades** repite la reparación de instalación nueva de `0157`/`0163`/`0194` (`strip` sin
ninguna `Membership`): `0061` siembra el Administrador desde el **catálogo vivo**, así que en una base nueva ya tiene los
códigos nuevos; sin la reparación fallan `M121PresetParityTest` y `M12CFreshInstallParityTest`.

| Fase | Nodo | Contenido | Vuelta atrás de código |
|---|---|---|---|
| 1 | `0194_trade_in_tariff` | 4 tablas + reparación de instalación nueva | Segura: la versión anterior las ignora. |
| 2 | `019x_trade_in_rules` | grupos, factores, conjuntos, versiones, reglas | Segura. |
| 3 | `019x_trade_in_eligibility` | fuentes, registros de verificación; columnas de política con `db_default`; capacidades `sales.tradein.view/value` (+ reparación) | Segura. |
| 4 | `019x_trade_in_assessment` | evaluación, respuestas, bloqueos, fotos; kind de `InternalSequence` (sólo choices); `sales.tradein.approve` (+ reparación) | Segura. |
| 5a | `019x_trade_in_quote` | cotización y revisiones; kind de `InternalSequence`; `sales.tradein.approve_own` (+ reparación); `SalesQuoteTradeIn.trade_in_quote` (nulo) | Segura. |
| 5b | `019x_trade_in_quote_print` | `PrintJob.trade_in_quote` (nulo, `db_default` NULL), kind `trade_in_quote_ticket`, restricción de destino `_v3` | **Un paso previo**, como 0189: `cancel_trade_in_quote_print_jobs --apply` antes de volver a la imagen anterior. Desde este nodo, cada migración lleva **dos** guardas al final: `refuse_reverse_with_trade_in_tickets` (penúltima; filtra `kind='trade_in_quote_ticket'`) y `refuse_reverse_with_quote_tickets` (última, intacta: `test_migration_reverse_guards` comprueba exactamente `filter(kind='sales_quote_ticket')`). La prueba gana una lista de nodos para la guarda nueva. |
| 5c | (sin esquema: `public_uid` se crea en 5a con la tabla de la cotización) | enlace público | Segura. |

Ninguna migración concede capacidades. Si el propietario decide V1 a favor, se añade un nodo de concesión con la regla
de 0189 y la cadena de `ADMIN_PREVIOUS`. En producción: `PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.

---

## 11. Plan por PR

Cada PR: rama propia, código separado de docs (commits distintos), entradas en `02_`/`07_`, CI verde, sin desplegar.

| Fase | PR | Contenido | Depende de |
|---|---|---|---|
| 1 | #457 | Tarifario backend: modelos, versiones, aprobación (relevancia según §3.1), política, API, Excel (tope 960 KB en la vista), auditoría, aislamiento, reparación de instalación nueva. | — |
| 2 | `feat/trade-in-rules` | Motor de reglas y `trade_in_valuation.calculate` puro con pruebas de tabla. | 1 |
| 3 | `feat/trade-in-eligibility` | Bloqueos (mínimo obligatorio), fuentes, registros de verificación (IMEI 1/2 y serie), capacidades `view/value`. | 1 |
| 4 | `feat/trade-in-assessment` | Evaluación, `tariff/match/`, respuestas, fotos de estado (las de identificador tras D6), cálculo con instantánea, `approve`. | 2, 3 |
| 5a | `feat/trade-in-quote` | Cotización, estados, revisiones, vencimiento exacto (`expire_trade_in_quotes`, cron horario), `calculation_stale`, `approve_own`. | 4 |
| 5b | `feat/trade-in-quote-print` | A4 / 80 mm / ESC-POS, cola, reimpresión, `PrintJob` `_v3`, migración con su guarda y su comando de cancelación. | 5a |
| 5c | `feat/trade-in-quote-public` | Enlace público, API pública con límite, página `cotizacion-equipo/[token]`. | 5a |
| 6 | `feat/trade-in-admin-ui` | Pantallas de tarifario, reglas y configuración; H412b; menú por capacidades. | 1, 2, 3 |
| 7 | `feat/trade-in-assessment-ui` | Evaluación guiada y cotizaciones. | 4, 5a–5c, 6 |
| 8 | `test/trade-in-e2e` | Playwright escritorio y móvil (Chromium + WebKit, 390 px) con datos sintéticos. | 7 |

---

## 12. Plan de pruebas

- **Por fase (backend, RED → GREEN):** reglas de base (CHECK, UNIQUE parciales), inmutabilidad, permisos por puerta
  (403 sin capacidad, nada escrito), aislamiento (404 de otra empresa en cada ruta, `?company=` no amplía), auditoría con
  empresa y sin IMEI, serie, resumen ni datos personales, guardianes «no compra/no paga/no stock», «el motor no nombra
  fabricante, modelo ni tienda», reparación de instalación nueva por cada capacidad nueva, importes fuera de rango → 400
  (API y Excel), concurrencia de propuestas (`TransactionTestCase` → `flushing` en `ci_shard_weights.json`).
- **Tarifa (#457):** precio futuro toma el relevo en su día; una versión nueva vencida **no devuelve** la anterior;
  subidas encadenadas medidas contra la última aprobada por una persona; adelantar el inicio de un precio futuro pide
  aprobación; aprobar una pendiente con inicio pasado la mueve a hoy; vista previa con entrada desactivada después →
  `preview_stale`.
- **Cálculo, tabla de casos:** sin deducciones = base; `percent` sobre base no compuesto; todas las reglas de estado
  aplican aunque una regla de importe de mayor prioridad gane; mismo componente en dos grupos cuenta una vez (antes de
  los topes); `combine=max` vs `sum`; tope de grupo; suma > base → 0, nunca negativo; NO ELEGIBLE → valores nulos;
  ajuste hacia arriba ≤ máximo y con aprobación; prioridad y empate deterministas; tarifa o reglas faltantes.
- **Correspondencia del selector (§8.1):** exacta, comodín, sin enlace, ambigua → `tariff_ambiguous`.
- **Verificación:** IMEI 2 obligatorio si existe; equipo sin IMEI por serie o declaración; registro vencido impide
  emitir; mínimo obligatorio no se apaga por política; resumen no aparece en respuestas ni auditoría.
- **Vigencia (5a):** 96 h exactas como instante (empresa en otra zona sólo cambia lo mostrado), aprobar/emitir/aceptar
  vencida antes del cron → 409, el cron la estampa una vez (concurrencia con `TransactionTestCase`), `calculation_stale`
  cuando la versión usada dejó de estar en vigor, revisión recalcula y la anterior conserva su instantánea, revisar una
  aceptada exige motivo, autoaprobación con `approve_own` y motivo.
- **Documentos (5b):** reimpresión idéntica byte a byte (goldens del ticket y ESC/POS); ticket y PDF sin costos,
  márgenes, IMEI completo ni «REQUIERE APROBACIÓN»; leyenda «no es comprobante».
- **Página pública (5c):** QR → página; token manipulado = 404; revisión reemplazada dice «reemplazada»; sin datos
  internos; `noindex`; límite por IP.
- **Frontend (6–7):** Jest por componente, `tsc`, lint, build. **E2E (8):** flujo completo en escritorio y 390 px.

---

## 13. Decisiones abiertas del propietario

Sólo las que no resuelven el código ni los ADR. Las recomendaciones son técnicas; el diseño se ajusta a la respuesta.
Ya decididas el 2026-11-27 y recogidas arriba: una versión nueva vencida no devuelve la anterior (§3.1); la subida se
mide contra la última aprobada por una persona (§3.1).

| # | Decisión | Recomendación | Afecta a |
|---|---|---|---|
| V1 (con **D7**) | ¿El preset Administrador recibe por defecto `sales.tradein.tariff.view/manage/approve` (y luego `sales.tradein.*`)? Hoy son de concesión explícita. | No (D7-b); el MASTER las concede. | Nodo de concesión opcional |
| V2 | Vigencia de una verificación de identidad (horas) y si «fuente no disponible» deja PENDIENTE. | 24 h; PENDIENTE, no se emite. | Fase 3 |
| V3 | Qué prueba la propiedad en el piloto y si alguna basta sola. | Lista de la empresa en la política; sin prueba = `ownership_unproven`. | Fase 3 |
| V4 | ¿Documento de NO ELEGIBLE o sólo pantalla? | Sólo pantalla. | Fase 5 |
| V5 | Redondeo del valor ofrecido. | Configurable; por omisión sin redondeo. | Fase 4 |
| V6 | Texto de condiciones y leyenda del ticket (revisión legal). | Texto de la empresa + leyenda genérica. | Fase 5b |
| V7 | ¿El ticket muestra el resumen de deducciones? | Sólo el valor y el estado. | Fase 5b |

Siguen abiertas D1–D12. En particular **D3** (la política nace con cliente identificado obligatorio, la recomendación
(a), y la empresa la cambia), **D6** (este diseño guarda sólo últimos 4 + resumen con clave; las fotos que pueden
mostrar un identificador esperan a D6, §3.3) y **D10** (verificación manual asistida, sin integración).

---

## 14. Lo que sigue bloqueado por D1–D12

- Recibir el equipo como stock (`receive_units`, `PURCHASE_ENTRY`), crear su unidad o su lote de etiquetas: **01B**
  (D2, D3, D6, D7, D9, D10).
- Cualquier crédito, saldo, `trade_in_credit` en `Order`, método de pago, devolución o comisión afectada: **01C**
  (D1, D4, D5, D8, D11).
- Cualquier comprobante, liquidación de compra, Formulario 820 o cambio de boleta/factura/IGV/XML: **D1/D2 con
  contador**.
- Cuándo se vende un equipo recibido: **D12**.
- Guardar el IMEI o la serie completos, documento de identidad, firma del vendedor o fotos con identificador:
  **D6/D10**.

Una cotización de recepción **aceptada y viva** es la entrada natural de 01B cuando se desbloquee: 01B leerá su
instantánea, rechazará una revisión `superseded` o vencida y exigirá verificaciones vigentes, sin cambiar nada de lo
emitido.
