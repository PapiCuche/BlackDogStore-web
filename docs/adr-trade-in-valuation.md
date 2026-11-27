# ADR — TRADE-IN VALUATION PRO · Tarifario, reglas, elegibilidad, evaluación y cotización de equipos usados

Estado: **PROPUESTA** (diseño) con la **fase 1 en PR #457** (`feat/trade-in-tariff-phase1`, PARCIAL: sin fusionar ni
desplegar). Ninguna otra fase está implementada.

Este documento diseña cómo una empresa **valoriza** un equipo usado que un cliente ofrece, y cómo se lo **cotiza**:
tarifario propio, reglas propias, elegibilidad, verificación asistida de IMEI, evaluación guiada, cálculo, cotización con
vigencia, ticket, PDF y página pública de estado. **No compra, no paga, no acredita y no recibe nada**: la recepción real
del equipo y el crédito en caja siguen siendo TRADE-IN-01B / 01C ([adr-trade-in.md](adr-trade-in.md)), bloqueados por
D1–D12 ([decisiones-propietario-trade-in.md](decisiones-propietario-trade-in.md)).

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
| Cotización comercial SALES-QUOTE-01 (`SalesQuote`, `SalesQuoteItem`, revisiones, aprobar/enviar/aceptar/rechazar/convertir) | `models.py` `SalesQuote`, `sales_quote_services.py`, `sales_quote_views.py`, migración `0189` | **IMPLEMENTADO** (master y prod-02) | Patrón de **revisión, no edición** (`CONTENT_FIELDS`, `REVISABLE_STATUSES`, `superseded`), auditoría por transición, numeración por `InternalSequence`. No se reutiliza la tabla: cotiza una **venta** (precio de caja por `build_pos_sale`), no una compra. |
| Documentos de la cotización: A4, ticket 80 mm, ESC/POS, cola de impresión | `sales_quote_documents.py` (`render_a4_pdf`, `ticket_pdf_from_ctx`, `escpos_from_ctx`), `printing/services.py` `enqueue_quote_ticket`, `PrintJob.Kind.SALES_QUOTE_TICKET` | **IMPLEMENTADO** | Trazado de 80 mm (`ticket_services._Cursor`), ESC/POS (`printing/escpos.py`), idempotencia por `Idempotency-Key`, impresora por sucursal. |
| Vencimiento de cotizaciones por cron | `management/commands/expire_sales_quotes.py`, `sales_quote_services.expire_due`, `test_sales_quote_expiry.py` | **IMPLEMENTADO** | Patrón cron idempotente con auditoría por fila. **PARCIAL** respecto de este diseño: vence por **fecha** (`valid_until` `DateField`) y con `timezone.localdate()` de la instalación (`TIME_ZONE='America/Lima'`), no con la zona de la empresa ni a hora exacta. |
| TRADE-IN-QUOTE-00: parte de pago **referencial** en la cotización (`SalesQuoteTradeIn`) | `models.py` `SalesQuoteTradeIn`, migración `0191`, #414 | **IMPLEMENTADO** (master y prod-02 desde R13.x). Lo que dice `decisiones-propietario-trade-in.md` («PARCIAL hasta fusionar») está **OBSOLETO**. | Texto libre, últimos 4 del IMEI, valorización estimada manual, saldo calculado y nunca cobrado. Este diseño lo **sustituye en el futuro** por una referencia opcional a la cotización de recepción (§3.6), sin romperlo. |
| TRADE-IN-01B / 01C (recepción real, crédito en caja) | `docs/adr-trade-in.md` | **PROPUESTA · BLOCKED** (D1–D12) | Lo que este diseño **no** hace. |
| Caja: «Equipo como parte de pago» deshabilitado | POS (SALES-UX-02) | **IMPLEMENTADO** (deshabilitado) | Sigue deshabilitado. |
| Cotización de servicio técnico con ticket (`RepairQuote`) | `quote_ticket.py`, `RepairQuote` | **IMPLEMENTADO** | Leyenda «no es comprobante», IMEI enmascarado en ticket. |
| Selector compartido de equipos | `frontend/app/admin/lib/device-configurator.ts`, `admin/components/DeviceConfigurator.tsx`, `pos_device_services.search_models` / `configurations`, `configuration_resolver.py` | **IMPLEMENTADO** | Elegir modelo → capacidad → color → SIM → región con la paleta real de la empresa. La evaluación lo usa tal cual (§8). |
| Catálogo: `ProductModel`, `VariantAttribute.role` (capacity/color/sim/region), `ModelOption`, `ModelOptionExclusion`, catálogo de referencia | `models.py`, `model_palette.py`, `catalog_reference*.py` | **IMPLEMENTADO** | El tarifario enlaza `ProductModel` opcionalmente; los roles dan capacidad/SIM/región. |
| Lector de escáner e IMEI | `scan_classifier.py` (`_imei_valid`), `device_identity.py` (`clean_imei` con Luhn, `mask`) | **IMPLEMENTADO** | Validar y enmascarar IMEI; leer con pistola o cámara. |
| Fotos privadas | `evidence_storage.py`, `evidence_images.py`, `UnitPhoto` (`unit_media.py`), evidencias de servicio | **IMPLEMENTADO** | Mismo almacén, autorización propia; el decodificador decide el tipo. |
| Lista de comprobación configurable por empresa con instantánea | `QualityChecklistTemplate` / `QualityCheck` / `QualityCheckItem` (servicio, M11) | **IMPLEMENTADO** (servicio técnico) | Patrón para el checklist técnico de la evaluación (plantilla de la empresa + instantánea por evaluación). |
| Página pública por enlace opaco | `RepairTrackingLink` (uid + MAC, sin guardar el token), `tracking_services.py`, `frontend/app/seguimiento/[token]` | **IMPLEMENTADO** (reparaciones) | Patrón exacto para el QR de la cotización (§9). |
| QR en documentos | `fiscal/qr.py` (`render_qr_png`), QR en `printing/escpos.py` | **IMPLEMENTADO** (sólo fiscal) | Se reutiliza el render; el contenido es otro (una URL, no la cadena SUNAT). |
| Capacidades y concesión explícita | `capabilities.py` (`EXPLICIT_GRANT_CODES`), `tenancy.has_capability`, pruebas de cadena (`test_costs_capability`, `tests.py`) | **IMPLEMENTADO** | Toda autoridad de este diseño es capacidad; ninguna por rol. |
| Guardia de paridad de pantallas H412b | `tests.H412bFrontendLegacyRoleParityTest.EXPECTED` | **IMPLEMENTADO** | Cada pantalla nueva (fases 6–7) se declara ahí. |
| `AdminAuditLog` con empresa | `models.AdminAuditLog.log(..., company=)` | **IMPLEMENTADO** | Toda escritura de este diseño. |
| Zona horaria de la empresa | `CompanySettings.timezone`; ayudante duplicado en `sales_analytics_views._company_tz` | **PARCIAL** (no hay un ayudante común) | La fase 1 añade `trade_in_tariff.company_timezone` / `company_today`; la fase 5 lo usa para la vigencia exacta. |
| Carga y descarga de Excel | `xlsx_reader.py`, `import_exports.py` (cabecera, columnas texto/número, notas), `money_input.py`, `import_freshness.py` | **IMPLEMENTADO** | El tarifario los reutiliza (fase 1). |
| Verificación de IMEI ante OSIPTEL u otra fuente | — (no hay código; `grep -i osiptel` vacío) | **PENDIENTE** | Fase 3: **registro asistido y auditado**, sin API inventada ni scraping. |
| Tarifario de recepción por empresa | PR #457, migración `0194_trade_in_tariff` | **PARCIAL** (en rama) | Fase 1 de este diseño. |
| Motor de reglas, elegibilidad, evaluación, cotización de recepción, ticket y QR | este ADR | **PROPUESTA** | Fases 2–8. |

---

## 2. Principios (no negociables)

1. **Valorizar no es comprar.** Ninguna fase crea `Order`, `StockMovement`, `StockUnit`, cuenta por pagar, crédito,
   pago, comprobante fiscal ni cambia IGV. Pruebas guardianas por fase (conteos y llaves foráneas).
2. **Un bloqueo nunca es un descuento.** La elegibilidad se decide **antes** y **aparte** del valor; un bloqueo no se
   compensa con un importe menor ni con una aprobación.
3. **Lo aprobado no se reescribe.** Tarifas, reglas y cotizaciones son versiones de sólo añadir; una cotización emitida
   guarda la versión exacta del tarifario y de las reglas que usó, con sus valores copiados.
4. **Decimal en todo importe**, redondeo explícito `ROUND_HALF_UP` a 0,01 por línea; nunca `float`.
5. **La empresa decide sus datos; el código, sus invariantes.** Tolerancias, vigencias, grupos, factores, efectos,
   fuentes de verificación y textos son de la empresa.
6. **Privacidad.** Ni el IMEI completo ni datos personales en auditoría, respuestas de lista, ticket o página pública;
   el IMEI se muestra enmascarado (últimos 4). Hasta la recepción real (01B, D6/D10) se guarda **sólo** un resumen
   con clave (`imei_digest`, HMAC con secreto del servidor y la empresa) y los últimos 4.
7. **Tiempo de la empresa.** Fechas de tarifa en el calendario de la empresa; vencimiento de cotización a la hora
   exacta en su zona.
8. **Autoridad = capacidades**, empresa resuelta en el servidor, lo ajeno responde 404.

---

## 3. Modelo de datos

Todas las tablas llevan `company` (FK `PROTECT`) y se validan contra la empresa de sus padres en `save()`.

### 3.1 Fase 1 — Tarifario (implementado en #457, migración `0194_trade_in_tariff`)

**`TradeInPolicy`** (1:1 con `Company`, se crea con el primer cambio; sin fila = valores por omisión)

| Campo | Tipo | Notas |
|---|---|---|
| `tariff_approval_mode` | `relevant` \| `all` | `relevant` por omisión. |
| `tariff_increase_tolerance_percent` | Decimal(5,2), 0–100 (CHECK) | 0 = cualquier subida es relevante. |
| `updated_by`, `updated_at` | | |

Fases siguientes añaden columnas con `db_default` (aditivas): `quote_validity_hours` (96), `imei_check_validity_hours`,
`require_identified_customer` (D3), `ownership_proof_policy`, `value_rounding_step` (§13).

**`TradeInTariffEntry`** — el equipo, con las palabras de la empresa.

| Campo | Notas |
|---|---|
| `product_model` (FK nulo, `SET_NULL`) | Modelo del catálogo de la **misma** empresa. Opcional: se reciben modelos que ya no se venden. |
| `brand`, `model_name`, `capacity`, `sim`, `region` | Texto de la empresa (como `Device.brand/model`). |
| `device_key` | Las cinco palabras plegadas (minúsculas, sin acentos, espacios colapsados; capacidad sin espacios) unidas por `|`. **UNIQUE(company, device_key)**. Lo escribe sólo `trade_in_tariff.device_key`. |
| `is_active`, `deactivated_at/by`, `deactivation_reason` | Se desactiva, nunca se borra. La identidad no cambia (`PATCH` sólo del enlace al catálogo). |
| `created_by/at`, `updated_at` | |

CHECK: `brand` y `model_name` no vacíos.

**`TradeInTariffVersion`** — los importes desde una fecha. Sólo añadir.

| Campo | Notas |
|---|---|
| `entry`, `number` | UNIQUE(entry, number), `number ≥ 1`. |
| `status` | `pending` → `approved` \| `rejected` \| `withdrawn`. |
| `currency` | Copia de `CompanySettings.currency`. |
| `base_reception_value` | Decimal(12,2) ≥ 0. Lo que la tienda paga por el equipo en buen estado, antes de reglas. |
| `refurbishment_cost`, `resale_value`, `target_margin` | Decimal(12,2) ≥ 0 o nulo (= sin estimar). **Internos**: nunca impresos ni públicos. |
| `max_reception_value` | Decimal(12,2) ≥ 0; CHECK `base ≤ max`. Tope de toda valorización y de todo ajuste manual. |
| `valid_from`, `valid_until` | `DateField` en el calendario de la empresa, fin inclusivo y nulo = hasta reemplazo. CHECK `until ≥ from`. Un cambio no empieza antes de hoy (no retroactivo). |
| `reason`, `source` (`manual`/`import`), `import_batch` | |
| `previous` | La aprobada contra la que se comparó. |
| `approval_reasons` | `new_entry`, `base_increase`, `max_increase`, `validity_extended`, `policy_all`. |
| `approval_kind` | `approver` \| `self` \| `policy`. |
| `proposed_by/at`, `decided_by/at`, `decision_note` | Quién y cuándo. |

Restricciones: **un solo `pending` por entrada** (UNIQUE parcial), importes ≥ 0, `base ≤ max`, ventana ordenada. En el
modelo: una versión decidida no se guarda ni se borra; una pendiente no cambia sus términos (`TERMS_FIELDS`).

**En vigor un día D**: la versión `approved` con número más alto y `valid_from ≤ D ≤ valid_until` (o sin fin), de una
entrada activa (`trade_in_tariff.effective_version`). Un precio futuro aprobado toma el relevo en su día.

**`TradeInTariffImport`** — vista previa y aplicación de un Excel: `file_sha256` (nunca el archivo), filas normalizadas
con su acción (`create`/`update`/`unchanged`/`error`) y la versión contra la que se comparó, resumen, quién y cuándo
previsualizó y aplicó.

### 3.2 Fase 2 — Reglas de valorización

**`TradeInFactorGroup`**: grupos de la empresa. `kind` es vocabulario técnico cerrado: `battery`, `cosmetic`,
`functional`, `repairs`, `accessories`, `custom`; `name` y `sort_order` son de la empresa. `combine` = `max` (por
omisión: dentro del grupo sólo cuenta la mayor deducción) o `sum`; `group_cap_percent` (Decimal, nulo = sin tope) sobre
el valor base.

**`TradeInFactor`**: una pregunta de la evaluación. `group`, `code` (slug único por empresa), `label`, `input`
(`check` = resultado técnico §5.2, `percent` = p. ej. salud de batería, `choice` = opciones de la empresa,
`boolean`), `options` (JSON corto para `choice`), `component` (texto de la empresa, p. ej. «pantalla»: clave para no
penalizar dos veces, §6.3), `applies_to` (filtros opcionales por `brand`/`model_name`/`product_model`), `is_active`,
`sort_order`, `requires_photo`.

**`TradeInRuleSet`** + **`TradeInRuleSetVersion`**: las reglas se versionan **en bloque** (una versión = el conjunto
completo, inmutable al aprobarse), con la misma máquina que el tarifario (`pending`/`approved`/`rejected`/`withdrawn`,
`valid_from`/`valid_until`, `approval_kind`, quién y cuándo). Una evaluación guarda el id de la versión que usó.

**`TradeInRule`** (filas de una versión): `factor`, `when` (resultado o rango: p. ej. `outcome=not_working`,
`percent_lt=80`), `effect` (cerrado):

| `effect` | Significado | Campo |
|---|---|---|
| `none` | Sin penalización | — |
| `fixed` | Resta un importe fijo | `amount` Decimal ≥ 0 |
| `percent` | Resta un % del valor **base** (no compuesto) | `percent` Decimal 0–100 |
| `repair_cost` | Resta el costo de reparación previsto | `amount` (o una tabla de costos de la empresa en una fase posterior) |
| `requires_approval` | El resultado necesita aprobación | — |
| `pending_verification` | Queda PENDIENTE DE VERIFICACIÓN hasta comprobarlo | — |
| `not_eligible` | NO ELEGIBLE por condición técnica | — |

Además `priority` (entero; mayor gana), `owner` (usuario responsable de la regla), `notes`. Validez, historial y
aprobación son los de la versión del conjunto.

### 3.3 Fase 3 — Elegibilidad y verificación de IMEI

**`TradeInBlocker`** (vocabulario cerrado en código, nombres de seguridad genéricos, sin fabricante):
`theft_or_loss_report`, `imei_invalid`, `imei_duplicate`, `imei_cloned`, `imei_tampered`, `activation_lock`,
`previous_account`, `mdm_enrolled`, `illicit_signs`, `ownership_unproven`, `identifiers_mismatch`,
`dangerous_condition`. Cada uno se responde en la evaluación como `clear`, `blocked` o `not_verified`.

**`TradeInVerificationSource`** (de la empresa): `name`, `url` (https, abre en pestaña nueva), `instructions`,
`allowed_evidence` (`none`/`screenshot`/`photo`), `is_active`. La fuente oficial del piloto (Checa tu IMEI de OSIPTEL)
se da de alta como **dato** del piloto, no en código.

**`ImeiVerificationRecord`** (append-only): `assessment`, `source`, `imei_last4`, `imei_digest`, `slot` (1/2),
`checked_at` (declarado por quien consultó, ≤ ahora), `result` (`no_report`, `reported`, `invalid`, `not_found`,
`unavailable`), `evidence` (foto privada opcional según la fuente), `recorded_by/at`, `valid_until` = `checked_at` +
`policy.imei_check_validity_hours`. **No hay API ni scraping**: la persona consulta la fuente oficial y registra lo que
vio; el sistema sólo guarda, enmascara, audita y exige vigencia. La recepción final (01B) exige un registro **vigente**:
si venció, se vuelve a consultar.

### 3.4 Fase 4 — Evaluación y cálculo

**`TradeInAssessment`**: `branch` (de las visibles del operador), `number` (`InternalSequence` kind
`trade_in_assessment`), `status` (§5.1), `purpose` (`valuation_only` \| `trade_towards`), `customer` (FK nulo a
`Customer`; obligatoriedad = política D3), identidad del equipo (`tariff_entry`, `product_model`, las cinco palabras
copiadas, `imei_last4`, `imei_digest`, `imei2_*`, `serial_last4`), procedencia (`provenance` cerrado: `own_purchase`,
`gift`, `other` + nota), `eligibility` (§5.2), `blocker_answers` (filas), `answers` (filas
`TradeInAssessmentAnswer`: factor, resultado, valor, nota, foto requerida), fotos (`TradeInAssessmentPhoto`, privadas),
`calculation` (instantánea JSON §6.5), `created_by/at`, `updated_at`.

Para `trade_towards`: `target_product` / `target_variant` (FK de la misma empresa) o `target_sales_quote` (FK nulo a
`SalesQuote`), y su precio **copiado** al calcular. La diferencia se **muestra**; nunca se guarda como descuento ni toca
IGV (§6.6).

### 3.5 Fase 5 — Cotización de recepción

**`TradeInQuote`**: `assessment`, `number` (`InternalSequence` kind `trade_in_quote`, una por cotización; las
revisiones lo comparten), `revision`, `origin` (`new`/`revision`), `source_quote` (la revisión que reemplaza),
`status` (§5.3), `issued_at`, `valid_until` (**DateTimeField** = `issued_at` + `policy.quote_validity_hours`, por
omisión 96 h, calculado en la zona de la empresa y guardado en UTC), `expired_at`, `superseded_at`,
`approved_by/at`, `decided_by/at`, `decision_note`, `public_uid` (como `RepairTrackingLink.uid`: el token es uid + MAC y
no se guarda).

Instantánea inmutable (no se recalcula nunca): `tariff_version` (FK `PROTECT`) y sus cinco importes copiados,
`rule_set_version` (FK `PROTECT`), `calculation` (líneas aplicadas y suprimidas), `computed_value`, `offered_value`,
`eligibility`, `target_price` y `difference` si `trade_towards`, texto de condiciones de la empresa copiado.
Restricciones como `SalesQuote`: UNIQUE(company, number, revision), una revisión viva por número (UNIQUE parcial
`status <> superseded`), `offered_value ≥ 0`, `offered_value ≤ max_reception_value` copiado.

**`PrintJob`** gana el kind `trade_in_quote_ticket` y la FK nula `trade_in_quote` (como `sales_quote` en 0189), con la
restricción de destino reescrita (`_v3`). La guarda de vuelta atrás de la migración de la fase 5 **amplía** la de las
cotizaciones: rechaza volver atrás mientras exista un trabajo `trade_in_quote_ticket`, y todo nodo posterior la lleva.

### 3.6 Relación con TRADE-IN-QUOTE-00

`SalesQuoteTradeIn` sigue igual. En la fase 5 puede ganar `trade_in_quote` (FK nulo): si la cotización de venta se
arma con una cotización de recepción vigente, el bloque copia su valor ofrecido en lugar del texto libre. Sigue siendo
referencial: la venta se cobra completa hasta 01C.

---

## 4. Capacidades

Todas **ACTIVE** y de **concesión explícita** (`EXPLICIT_GRANT_CODES`): nadie las recibe por defecto (recomendación
D7); el MASTER las concede desde Roles. Ninguna se deriva de `inventory.adjust`, `sales.pos.use` ni
`sales.quotes.*`.

| Código | Fase | Abre |
|---|---|---|
| `sales.tradein.tariff.view` | 1 | Ver y exportar tarifario (con estimados internos), ver reglas (fase 2) y política. |
| `sales.tradein.tariff.manage` | 1 | Proponer precios, importar, desactivar, enlazar catálogo; en la fase 2, editar borradores de reglas, grupos y factores. |
| `sales.tradein.tariff.approve` | 1 | Aprobar/rechazar cambios relevantes de tarifario y reglas, reactivar, política. |
| `sales.tradein.tariff.approve_own` | 1 | Aprobar un cambio propio con motivo (marcado en auditoría). |
| `sales.tradein.view` | 3 | Ver evaluaciones y cotizaciones de recepción, reimprimir. |
| `sales.tradein.value` | 3–5 | Crear y editar evaluaciones, registrar verificaciones, calcular, emitir una cotización que no requiere aprobación, imprimir. (Nombre ya propuesto en `adr-trade-in.md` §3.6.) |
| `sales.tradein.approve` | 4–5 | Aprobar/rechazar una cotización que requiere aprobación; ajustar el valor ofrecido por encima del calculado, hasta el máximo del tarifario. |

Las reglas (fase 2) usan las cuatro del tarifario: son la misma autoridad comercial («cuánto paga la tienda»). Ningún
permiso convierte un bloqueo en valor ni salta una verificación pendiente.

Por pantalla nueva (fases 6–7), `H412bFrontendLegacyRoleParityTest.EXPECTED` gana su línea con la capacidad que la abre
y `frozenset()` como conjunto legacy (no hay puente legacy).

---

## 5. Máquinas de estado

Todas en servicios, con `select_for_update` sobre la fila y su padre, tabla de transiciones cerrada y auditoría por
transición.

### 5.1 Evaluación

```
draft ──calcular──▶ calculated ──(editar)──▶ draft
calculated ──emitir cotización──▶ quoted        (crea TradeInQuote; la evaluación queda congelada)
draft|calculated ──cancelar──▶ cancelled
```

Una evaluación `quoted` no se edita: una revisión de la cotización copia la evaluación a una nueva en `draft`.

### 5.2 Elegibilidad (derivada en cada cálculo; precedencia de arriba abajo, gana la primera)

| Estado | Cuándo |
|---|---|
| **NO ELEGIBLE** | Cualquier bloqueo `blocked`; o una regla `not_eligible`; o `imei_invalid` por Luhn/forma. |
| **PENDIENTE DE VERIFICACIÓN** | Un bloqueo obligatorio `not_verified`; no hay registro de verificación vigente; o una regla `pending_verification` (resultado técnico `not_verified`). |
| **REQUIERE APROBACIÓN** | Alguna regla `requires_approval`; o el valor ofrecido supera el calculado (ajuste manual). |
| **APTO CON DESCUENTOS** | Hay al menos una deducción aplicada. |
| **APTO** | Nada de lo anterior. |

Resultados técnicos de un factor `check` (cerrado): `works`, `detail`, `not_working`, `replaced`, `not_verified`,
`not_applicable`.

### 5.3 Cotización de recepción y revisiones

```
draft ─┬─(requiere aprobación)─▶ pending_approval ─┬─aprobar──▶ approved ──emitir──▶ issued
       │                                           └─rechazar─▶ rejected
       └─(no requiere)───────────────────────────────────────▶ approved ──emitir──▶ issued
issued ──vence (valid_until < ahora)──▶ expired        («VENCIDA», sólo por cron; la lectura lo deriva)
issued ──el cliente acepta / rechaza──▶ accepted | declined
draft|pending_approval|approved ──cancelar──▶ cancelled
approved|issued|accepted|declined|expired|rejected ──revisar──▶ (nueva revisión N+1 en draft; la N pasa a superseded)
```

- Sólo `draft` se edita. Una revisión **recalcula** con el tarifario y las reglas **en vigor ahora**; la N conserva su
  instantánea: nada es retroactivo.
- NO ELEGIBLE nunca llega a `approved`/`issued` con valor: se puede emitir un documento de **no elegibilidad**
  (sin valor, con el motivo genérico) si la empresa lo quiere (§13).
- PENDIENTE DE VERIFICACIÓN no se emite: se completa la verificación o se emite como «valor referencial sujeto a
  verificación» sólo si la política de la empresa lo permite (§13).
- `accepted` no recibe el equipo: lo hará 01B, con una verificación de IMEI vigente.

---

## 6. Algoritmo de cálculo (`trade_in_valuation.calculate`, puro, sin escribir)

Entrada: evaluación, instante `t` (ahora), empresa. Salida: instantánea §6.5.

1. **Tarifa.** `effective_version(entry, on=company_today(t))`. Sin versión en vigor → no hay cálculo
   (`tariff_missing`): la persona elige otra entrada o pide el precio.
2. **Elegibilidad primero.** Se evalúan bloqueos y verificación (§5.2). Si NO ELEGIBLE: `computed_value = null` y se
   detiene. **Ningún bloqueo produce una deducción.**
3. **Reglas aplicables.** Versión del conjunto de reglas en vigor en `t`. Para cada respuesta, las reglas de su factor
   cuyo `when` coincide y cuyo `applies_to` incluye el equipo; **una sola por factor**: la de mayor `priority` (empate:
   la de mayor deducción, luego el id menor). Las de efecto de estado (`requires_approval`, `pending_verification`,
   `not_eligible`) se registran como banderas.
4. **Importe de cada deducción** sobre el **valor base** (no compuesto): `fixed` = `amount`; `percent` =
   `base × percent / 100`; `repair_cost` = `amount`. Cada línea se redondea a 0,01 (`ROUND_HALF_UP`).
5. **Sin doble penalización**, en este orden:
   1. **Por componente.** Entre líneas con el mismo `component` (de cualquier grupo), sólo cuenta la mayor; las demás
      quedan `suppressed_by` esa línea. Ej.: «pantalla rota» (cosmético, 15 %) y «pantalla reemplazada» (reparaciones,
      S/ 120): cuenta la mayor de las dos.
   2. **Por grupo.** `combine=max`: sólo la mayor del grupo; `combine=sum`: suma.
   3. **Tope de grupo.** `group_cap_percent` × base.
   4. **Tope total.** La suma nunca supera el valor base.
6. **Valor calculado** = `max(0, base − deducciones)` (**nunca negativo**), y `min(…, max_reception_value)`.
7. **Valor ofrecido.** Por omisión = calculado (y redondeado hacia abajo al `value_rounding_step` de la empresa, si lo
   tiene). Un ajuste manual: hacia abajo, con motivo, quien tiene `sales.tradein.value`; hacia arriba, sólo hasta
   `max_reception_value`, con motivo, y deja la cotización en REQUIERE APROBACIÓN (`sales.tradein.approve`).
8. **Elegibilidad final** con §5.2 (incluye la bandera de ajuste manual).

### 6.5 Instantánea

`{tariff: {version_id, number, base, max, currency}, rules: {version_id, number}, lines: [{factor, outcome, rule_id,
effect, amount, component, group, suppressed_by}], totals: {deductions, computed_value, offered_value, rounding},
flags: [...], eligibility, computed_at}`. Los estimados internos (reacondicionamiento, reventa, margen) se copian en
columnas **internas** de la cotización para el margen, nunca en el ticket ni en la página pública.

### 6.6 Dos casos

- **Sólo valorización** (`valuation_only`): el documento dice cuánto pagaría la tienda y hasta cuándo.
- **Parte de pago hacia otro equipo** (`trade_towards`): además, el precio del equipo destino (copiado de su producto o
  de una cotización de venta) y la **diferencia** = precio − valor ofrecido. Es un dato informativo: no es descuento,
  no cambia el IGV de nada, no crea crédito ni saldo a favor; si el valor supera el precio, se muestra «sin diferencia a
  pagar» y no hay saldo a favor (D4 pendiente).

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
| 1 | `tariff/import/preview/` (multipart `file`) · `tariff/import/<id>/apply/` | POST | manage |
| 1 | `policy/` | GET · PUT | view o approve · approve |
| 2 | `rules/groups/`, `rules/factors/`, `rules/versions/` (borrador, proponer, aprobar, rechazar, retirar), `rules/versions/<id>/export/`, `…/import/` | CRUD | tariff.* |
| 2 | `rules/preview/` (calcular un caso de prueba sin guardar) | POST | tariff.view |
| 3 | `verification-sources/` | CRUD | tariff.manage |
| 3 | `assessments/<id>/imei-checks/` | GET · POST | tradein.view · tradein.value |
| 4 | `assessments/` · `assessments/<id>/` · `…/answers/` · `…/photos/` · `…/calculate/` · `…/cancel/` | CRUD | tradein.view · tradein.value |
| 5 | `quotes/` · `quotes/<id>/` · `…/request-approval/` · `…/approve/` · `…/reject/` · `…/issue/` · `…/accept/` · `…/decline/` · `…/revise/` · `…/cancel/` | GET · POST | tradein.view · value · approve |
| 5 | `quotes/<id>/pdf/?format=a4|ticket` · `quotes/<id>/print/` (`Idempotency-Key`) | GET · POST | tradein.view |
| 5 | Público: `GET /api/trade-in/status/<token>/` | — | ninguna (token opaco; sólo estado y vigencia) |

Errores con `code` legible (`pending_exists`, `own_change`, `retroactive`, `unchanged`, `preview_stale`,
`preview_expired`, `tariff_missing`, `not_eligible`, `expired`, …). Lo ajeno o inexistente: 404 igual. Límites de
peticiones: los de cotizaciones; importación y exportación, los suyos. Subidas: tramo `workbook` (Excel) e `image`
(fotos), en `request_limits` **y** `deploy/Caddyfile` (`ProxyAgreesTest`).

---

## 8. Pantallas y flujos (fases 6–7)

**Panel · Parte de pago** (entrada de menú propia, visible con cualquier `sales.tradein.*`):

1. **Tarifario** (`/admin/trade-in/tariff`): tabla con búsqueda y filtros (marca, modelo, capacidad, SIM/región, activos,
   pendientes), insignias «Pendiente de aprobación» y «Desactivado»; ficha con historial de versiones (quién, cuándo,
   por qué, motivo de aprobación), proponer precio, aprobar/rechazar (sólo si el servidor lo ofrece en `actions`),
   desactivar/reactivar; exportar e importar con vista previa por filas (crear/cambiar/igual/error, «necesita
   aprobación»). Los estimados internos sólo con `tariff.view`.
2. **Reglas** (`/admin/trade-in/rules`): grupos y factores de la empresa; borrador de versión con reglas por factor
   (efecto, importe/%, prioridad, componente, responsable), «Probar un caso» (`rules/preview/`), proponer y aprobar.
3. **Configuración** (`/admin/trade-in/settings`): política (aprobación, tolerancia, vigencia de cotización en horas,
   vigencia de verificación, cliente identificado, redondeo, textos y condiciones), fuentes de verificación.
4. **Evaluación guiada** (`/admin/trade-in/assessments/new`), en pasos con progreso, pensada para móvil:
   1. *Identificación*: `DeviceConfigurator` (modelo → capacidad → color → SIM → región) sobre el modelo del catálogo o
      búsqueda en el tarifario; IMEI/serie por escáner (`scan_classifier`) o teclado, validado (Luhn) y enmascarado al
      salir del campo. Muestra la entrada del tarifario y su vigencia.
   2. *Procedencia y seguridad*: cliente (si la política lo exige), procedencia, bloqueos uno por uno
      (`clear`/`blocked`/`not_verified`), verificación de IMEI asistida: botón que abre la fuente oficial en pestaña
      nueva, formulario «resultado, fecha y hora de la consulta, evidencia» y aviso de vigencia. Un bloqueo marcado
      termina el flujo en NO ELEGIBLE (sin importes).
   3. *Checklist técnico*: factores agrupados, seis resultados, notas, fotos privadas donde el factor las pide.
   4. *Resumen del cálculo*: base, deducciones aplicadas y suprimidas («no se cuenta dos veces: ya se descontó por…»),
      topes, valor calculado, ajuste manual con motivo, estado de elegibilidad con su explicación; si es parte de pago,
      precio destino y diferencia («no es un descuento»).
   5. *Revisión*: guardar borrador, solicitar aprobación, aprobar/rechazar (si puede), emitir; imprimir ticket o PDF.
5. **Cotizaciones de recepción** (`/admin/trade-in/quotes`): lista por estado (incluye VENCIDA derivada), detalle con
   revisiones, reimprimir la revisión exacta, revisar (nueva revisión con recálculo).

Responsive 320–escritorio, teclado y foco visible, tablas anchas en su contenedor, textos de la empresa por tokens.

---

## 9. Impresión y QR (fase 5)

- **Un contexto, tres salidas**, como `sales_quote_documents`: `build_context(quote)` lee **sólo la instantánea** de esa
  revisión → A4 PDF, ticket 80 mm PDF y ESC/POS (`printing/escpos.py`, `ticket_services._Cursor`). La cola
  (`printing/services.enqueue_trade_in_quote_ticket`) copia `enqueue_quote_ticket`: impresora de tickets activa del
  local, `Idempotency-Key`. **Reimprimir** = mismo contexto de la misma revisión; nunca recalcula.
- **Contenido**: logo y datos de la empresa (diseño de documentos de la empresa), título y leyenda de
  `document_legends` («Cotización de recepción de equipo usado · No es comprobante de pago ni constancia de compra ·
  Valor sujeto a verificación final»), número y revisión, equipo (palabras del tarifario), **IMEI enmascarado**, estado
  de elegibilidad, valor ofrecido, vigencia exacta («Válida hasta el 01/12/2026 18:30», zona de la empresa), condiciones
  de la empresa, diferencia si es parte de pago, y el QR.
- **Nunca**: costos de reacondicionamiento, reventa, margen, notas internas, IMEI completo, DNI, reglas ni deducciones
  internas (salvo que la empresa elija imprimir el resumen de deducciones visibles para el cliente, §13).
- **QR**: `https://<dominio de la empresa>/cotizacion-equipo/<token>`, con `token = uid + MAC(secreto)` como
  `tracking_services` (no se guarda; se puede revocar rotando el uid). Render con `fiscal/qr.render_qr_png` en PDF y el
  comando QR de `escpos`. La página pública (`frontend/app/cotizacion-equipo/[token]`, `no-referrer`, sin medición)
  muestra **sólo** empresa, número-revisión, estado (vigente / vencida / reemplazada / aceptada / anulada) y vigencia.
- `names_fiscal_document` (guardián de documentos) debe seguir fallando si alguien titula esto boleta o factura.

---

## 10. Migraciones

Todas aditivas, una por fase que cambie esquema, numeradas en orden sobre la hoja de `master` del momento (la línea
SUNAT no añade migraciones de `store` sin coordinar). **Cada nodo ≥ 0189 termina con
`refuse_reverse_with_quote_tickets`** y se añade a `test_migration_reverse_guards.NODES`.

| Fase | Nodo | Contenido | Vuelta atrás de código |
|---|---|---|---|
| 1 | `0194_trade_in_tariff` | 4 tablas | Segura: la versión anterior las ignora. |
| 2 | `019x_trade_in_rules` | grupos, factores, conjuntos, versiones, reglas | Segura. |
| 3 | `019x_trade_in_eligibility` | fuentes, registros de verificación; columnas de política con `db_default` | Segura. |
| 4 | `019x_trade_in_assessment` | evaluación, respuestas, bloqueos, fotos; kind de `InternalSequence` (sólo choices) | Segura. |
| 5 | `019x_trade_in_quote` | cotización; `PrintJob.trade_in_quote` (nulo, `db_default` NULL) y kind; restricción de destino `_v3`; `SalesQuoteTradeIn.trade_in_quote` (nulo) | **Un paso previo**, como 0189: cancelar trabajos `trade_in_quote_ticket` en cola antes de volver a la imagen anterior (comando `cancel_trade_in_quote_print_jobs`); la guarda de reversa nombra el `DELETE`. |

Ninguna migración concede capacidades (concesión explícita). Si el propietario quiere que el Administrador las reciba,
se añade un nodo de concesión con la regla de 0189 y la cadena de `ADMIN_PREVIOUS` (§13). En producción:
`PGOPTIONS='-c lock_timeout=5s -c statement_timeout=30s'`.

---

## 11. Plan por PR

Cada PR: rama propia, código separado de docs (commits distintos), entradas en `02_`/`07_`, CI verde, sin desplegar.

| Fase | PR | Contenido | Depende de |
|---|---|---|---|
| 1 | #457 | Tarifario backend: modelos, versiones, aprobación, política, API, Excel, auditoría, aislamiento, 40 pruebas. | — |
| 2 | `feat/trade-in-rules` | Motor de reglas: grupos, factores, versiones del conjunto, reglas, `rules/preview/`, Excel de reglas; `trade_in_valuation.calculate` puro con pruebas de tabla (doble penalización, topes, nunca negativo, prioridad). | 1 |
| 3 | `feat/trade-in-eligibility` | Bloqueos, fuentes de verificación, registro asistido de IMEI (máscara, resumen con clave, vigencia), capacidades `sales.tradein.view/value`. | 1 |
| 4 | `feat/trade-in-assessment` | Evaluación, respuestas, fotos privadas, cálculo con instantánea, `approve`. | 2, 3 |
| 5 | `feat/trade-in-quote` | Cotización, revisiones, vencimiento exacto (`expire_trade_in_quotes`, cron horario), A4/80 mm/ESC-POS, cola, reimpresión, enlace público y su API. | 4 |
| 6 | `feat/trade-in-admin-ui` | Pantallas de tarifario, reglas y configuración; H412b; menú por capacidades. | 1, 2, 3 |
| 7 | `feat/trade-in-assessment-ui` | Evaluación guiada, cotizaciones, página pública. | 4, 5, 6 |
| 8 | `test/trade-in-e2e` | Playwright escritorio y móvil (Chromium + WebKit, 390 px) con datos sintéticos. | 7 |

Los PR con `backend/**` tardan en CI: 2 y 3 pueden ir en paralelo; 6 puede empezar con el contrato de 1.

---

## 12. Plan de pruebas

- **Por fase (backend, RED → GREEN):** reglas de base (CHECK, UNIQUE parciales), inmutabilidad (modelo y servicios),
  permisos por puerta (403 sin capacidad, nada escrito), aislamiento (404 de otra empresa en cada ruta, `?company=` no
  amplía), auditoría con empresa y sin IMEI ni datos personales, guardianes «no compra/no paga/no stock» (conteos y
  FKs prohibidas), «el motor no nombra fabricante, modelo ni tienda».
- **Cálculo (fase 2/4), tabla de casos:** sin deducciones = base; `percent` sobre base no compuesto; mismo componente
  en dos grupos cuenta una vez; `combine=max` vs `sum`; tope de grupo; suma > base → 0, nunca negativo; valor ≤ máximo;
  prioridad y empate deterministas; redondeo por línea; bloqueo → sin valor aunque haya reglas; `not_verified` →
  PENDIENTE; ajuste hacia arriba → REQUIERE APROBACIÓN; tarifa futura/vencida/desactivada → `tariff_missing`.
- **Vigencia (fase 5):** 96 h exactas en zona de la empresa (empresa en otra zona; cruce de medianoche y de cambio de
  día), lectura deriva VENCIDA sin escribir, el cron la estampa una vez (idempotente, concurrencia con
  `TransactionTestCase` → `flushing` en `ci_shard_weights.json`), revisión recalcula y la anterior conserva su
  instantánea, reimpresión idéntica byte a byte (goldens del ticket y ESC/POS).
- **Documentos:** ticket y PDF sin costos/márgenes/IMEI completo (prueba sobre el texto extraído), leyenda «no es
  comprobante», QR que resuelve a la página pública, página pública sin datos internos, token manipulado = 404.
- **Frontend (fases 6–7):** Jest por componente (acciones según `actions` del servidor, máscara del IMEI, pasos,
  accesibilidad), `tsc`, lint, build.
- **E2E (fase 8):** tarifario (alta → aprobación por otra persona → importación), evaluación guiada completa en
  escritorio y en 390 px, bloqueo → NO ELEGIBLE sin importes, emisión → ticket → página pública, vencimiento simulado.

---

## 13. Decisiones abiertas del propietario

Sólo las que no resuelven el código ni los ADR. Las recomendaciones son técnicas; el diseño se ajusta a la respuesta.

| # | Decisión | Recomendación | Afecta a |
|---|---|---|---|
| V1 | ¿El preset Administrador recibe por defecto `sales.tradein.tariff.view/manage/approve` (y luego `sales.tradein.*`)? Hoy son de concesión explícita. | No (D7); el MASTER las concede. | Nodo de concesión opcional |
| V2 | Vigencia de una verificación de IMEI (horas) y si «fuente no disponible» deja PENDIENTE o permite emitir como referencial. | 24 h; PENDIENTE, no se emite. | Fase 3 |
| V3 | Qué prueba la propiedad en el piloto (boleta, caja con IMEI, cuenta vinculada…) y si alguna basta sola. | Lista de la empresa en la política; sin prueba = `ownership_unproven`. | Fase 3 |
| V4 | ¿Se emite un documento de NO ELEGIBLE (sin valor) o sólo se informa en pantalla? | Sólo pantalla. | Fase 5 |
| V5 | Redondeo del valor ofrecido (p. ej. hacia abajo a S/ 10). | Configurable; por omisión sin redondeo. | Fase 4 |
| V6 | Texto de condiciones y leyenda del ticket (revisión legal). | Texto de la empresa + leyenda genérica de plataforma. | Fase 5 |
| V7 | ¿El ticket muestra al cliente el resumen de deducciones (sin importes internos) o sólo el valor? | Sólo el valor y el estado. | Fase 5 |

Siguen abiertas, sin cambio, D1–D12 (`decisiones-propietario-trade-in.md`). En particular **D3** (cliente
identificado), **D6** (datos y retención: este diseño guarda sólo últimos 4 + resumen con clave hasta 01B) y **D10**
(consulta manual: este diseño la implementa asistida y sin integración).

---

## 14. Lo que sigue bloqueado por D1–D12

- Recibir el equipo como stock (`receive_units`, `PURCHASE_ENTRY`), crear su unidad o su lote de etiquetas: **01B**
  (D2, D3, D6, D7, D9, D10).
- Cualquier crédito, saldo, `trade_in_credit` en `Order`, método de pago, devolución o comisión afectada: **01C**
  (D1, D4, D5, D8, D11).
- Cualquier comprobante, liquidación de compra, Formulario 820 o cambio de boleta/factura/IGV/XML: **D1/D2 con
  contador**.
- Cuándo se vende un equipo recibido: **D12**.
- Guardar el IMEI completo, documento de identidad o firma del vendedor: **D6/D10**.

Una cotización de recepción **aceptada** es la entrada natural de 01B cuando se desbloquee: 01B leerá su instantánea y
exigirá una verificación de IMEI vigente, sin cambiar nada de lo emitido.
