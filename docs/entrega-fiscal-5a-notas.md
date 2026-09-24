# ERP-FISCAL-5A — Entrega: Nota de Crédito (07) y Nota de Débito (08)

Rama `erp/fiscal-sunat`. Backend only, **0 diff de frontend**. Una migración
aditiva (`0088`). No habilita producción. Este documento es la evidencia de cierre
de la fase: qué se construyó, qué se decidió, qué queda PENDIENTE y por qué, y qué
recibe la fase siguiente (FISCAL-5B).

> **Alcance de una nota en esta fase.** Una nota es un hecho FISCAL y sólo eso.
> Emitir una NC **no** devuelve dinero, **no** mueve stock, **no** cancela la
> `Order` ni el `Payment`. Esos dominios se integran, explícitamente y con su
> propia autorización, en otra fase. Aquí una nota corrige un comprobante ante
> SUNAT; nada más.

---

## 1 · Puerta de catálogos (Gate §6), resuelta antes de tocar lógica de motivo

Los motivos de nota viven en dos catálogos oficiales de SUNAT:

- **Catálogo N.º 09 — Nota de Crédito**, códigos `01`..`13`.
- **Catálogo N.º 10 — Nota de Débito**, códigos `01` (interés por mora), `02`
  (aumento en el valor), `03` (penalidades / otros).

Ambos se codifican en `store/fiscal/rules.py` (`CATALOG_09`, `CATALOG_10`) y el
motivo de cada nota se valida contra el catálogo de SU tipo antes de la red. Un
motivo fuera de catálogo se rechaza en microsegundos con el dato señalado, no
minutos después por un código de SUNAT.

**Motivos que esta fase emite** (los representables correctamente):

- **NC — anulación total** (Catálogo 09, `01` «anulación de la operación» y `06`
  «devolución total»): la nota refleja el comprobante entero.
- **ND — cargo de importe explícito** (Catálogo 10, `01`/`02`/`03`): interés,
  aumento de valor o penalidad, con base e IGV dados por la operación correctiva.

**Motivos PENDIENTES, y por qué** (§7/§24): la devolución PARCIAL, el descuento
por ítem y todo ajuste que exija repartir un `cac:AllowanceCharge` quedan fuera.
Emitir uno de ellos con el descuento escondido en el importe de la línea produce
un documento que el XSD acepta y SUNAT rechaza —o, peor, acepta con un precio
unitario que nadie pactó—. Mientras `AllowanceCharge` no esté implementado (la
misma deuda que arrastra la factura), no se emiten: se clasifican PENDIENTE.

## 2 · Puerta de diseño de datos (Gate §12) y migración

`FiscalDocument` se extiende; **no** hay entidad nueva (ADR-29). Campos añadidos:

| Campo | Tipo | Para qué |
|---|---|---|
| `original_document` | FK a `self`, `on_delete=PROTECT`, `related_name='notes'` | El comprobante que la nota corrige. PROTECT: no se borra un original con notas colgando. |
| `note_reason_code` | `CharField(2)` | Código del motivo (Catálogo 09/10). |
| `note_reason_description` | `CharField(250)` | Descripción textual del motivo. |
| `note_request_key` | `CharField(64)` | Clave de idempotencia por intención (ADR-31). |

`FiscalDocumentType` gana `CREDIT_NOTE='07'` y `DEBIT_NOTE='08'`; se añaden las
constantes de módulo `FISCAL_ORIGINAL_TYPES=(01,03)` y `FISCAL_NOTE_TYPES=(07,08)`.

Restricciones:

- `fiscal_document_one_live_per_order` se **acota** a `document_type IN (01,03)`:
  las notas no compiten con el comprobante por el «único vivo por pedido».
- `fiscal_note_idempotent_per_original` = único `(original_document,
  note_request_key)` con condición `note_request_key != ''`: reintentar la misma
  petición da la misma nota; una clave vacía nunca colisiona (ADR-31).

Migración `0088` — **aditiva** (4 columnas, metadatos de choices 07/08, cambio de
las dos restricciones). `makemigrations --check` limpio. `0085`/`0086`/`0087`
intactas.

## 3 · UBL 2.1 — esquemas oficiales, sin mirror ni descarga en runtime (§37/§38)

`UBL-CreditNote-2.1.xsd` y `UBL-DebitNote-2.1.xsd` tomados de la fuente OASIS
(`docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/`), versionados en
`schemas/2.1/maindoc/`. Compilan contra el árbol `common/` ya presente. Su
procedencia, fecha y SHA-256 quedan en `schemas/PROCEDENCIA.md`. `schema.py`
expone `validate_credit_note` / `validate_debit_note`.

Que provengan de OASIS y no de SUNAT es correcto: en el árbol UBL 2.1 de SUNAT los
`maindoc` de CreditNote y DebitNote son los de OASIS sin modificar (SUNAT modifica
extensiones, no el documento base). El paquete de SUNAT no pudo descargarse
—Cloudflare devuelve 403 a descargas automatizadas y la fase prohíbe mirrors—; la
fuente OASIS es la MISMA definición, verificable por hash.

## 4 · Generación del XML (builder)

`build_note_xml(NoteData)` comparte con la factura todas las primitivas
(`_party`, `_tax_block`, `_line` parametrizado, `_ubl_extensions`,
`_signature_block`, `_monetary_total`); la refactorización de la factura para
compartirlas quedó verificada sin romperse. Lo propio de una nota:

- Raíz `CreditNote` (07) o `DebitNote` (08).
- Bloque de relación: `cac:DiscrepancyResponse` (`ReferenceID`=id del original,
  `ResponseCode`=código de motivo, `Description`) y
  `cac:BillingReference/cac:InvoiceDocumentReference` (`ID`=id del original,
  `DocumentTypeCode`=tipo del original 01/03).
- Renglón `cac:CreditNoteLine`/`cbc:CreditedQuantity` (NC) o
  `cac:DebitNoteLine`/`cbc:DebitedQuantity` (ND).
- Total: `cac:LegalMonetaryTotal` (NC) frente a `cac:RequestedMonetaryTotal` (ND)
  — **lo exige el XSD**; usar el equivocado falla la validación (comprobado).

## 5 · Servicio de nota (`fiscal_note_services.py`)

`create_fiscal_note(original, *, note_type, reason_code, reason_description,
request_key='', taxable_amount=None, tax_amount=None)`:

1. **Elegibilidad (§10).** El original existe, está firmado y —si es factura—
   ACEPTADO por SUNAT (tiene CDR). Sin CDR aceptado no hay nota: no se emite una
   nota sobre algo que para SUNAT no existe. Un original boleta se rechaza aquí
   (NC-BOL/ND-BOL es PENDIENTE, §11).
2. **Idempotencia (§20).** Con `request_key`, una nota previa no rechazada con esa
   clave sobre ese original se devuelve tal cual (`created=False`). Una carrera se
   cierra además con la restricción única.
3. **Importes (§22).** Sin importes explícitos → anulación total: los del
   original, **no recalculados**. Con importes → los dados (ND de cargo).
4. **Serie y correlativo.** `resolve_note_series` (véase §7). El correlativo se
   reserva dentro de la transacción; la red queda fuera (§50).
5. **Congelación (§21).** El emisor y el adquirente se copian congelados del
   original (§41): una nota lleva el mismo adquirente que el comprobante que
   corrige, aunque la empresa se haya renombrado después.

`sign_fiscal_note(note, *, key_pem, cert_pem)` reconstruye la `NoteData`
(las líneas de una anulación total se leen del `signed_xml` **inmutable** del
original, no de la `Order`, ADR-30), valida reglas, construye, firma (XML-DSig
enveloped) y valida contra el XSD que corresponde al tipo. Re-firmar es no-op: el
XML firmado ES el comprobante y regenerarlo cambiaría un `DigestValue` que puede
estar ya impreso.

## 6 · Reglas sin red (`rules.validate_note`)

Serie con el prefijo del original (F/B), correlativo en rango, RUC del emisor,
motivo en el catálogo de su tipo, moneda ISO, afectación gravada, y la aritmética
línea-a-línea y de totales. De lo estructural a lo aritmético, para que el mensaje
señale la causa y no una consecuencia.

## 7 · Serie 07/08 determinista y por prefijo (§18)

`resolve_note_series(company, *, branch, note_type, original_type)` en
`fiscal_config.py`: filtra por empresa / sucursal / ambiente / `document_type` **y
por el prefijo** que hereda del original (F para nota de factura, B para nota de
boleta). Un mismo `07` admite dos series legítimas (una F, una B); resolver por «la
primera» convertiría un accidente de orden en la política de la empresa. Ambigüedad
= fallo, nunca un desempate improvisado. La regla es idéntica en espíritu a
`resolve_series` (misma casa, misma disciplina).

## 8 · Canal de envío por el ORIGINAL (§29), y fallo cerrado de la boleta

- **NC/ND de FACTURA → `sendBill`.** Reutiliza `submit_fiscal_document` sin cambio
  (su guarda sólo bloquea `03`; un `07`/`08` de factura pasa). El envío incierto se
  reconcilia con la maquinaria de FISCAL-3 (`getStatusCdr`): un timeout es
  INCIERTO, nunca un reintento a ciegas (§54).
- **NC/ND de BOLETA → Resumen Diario.** Su canal es el Resumen, que **aún no**
  transmite líneas de nota. Antes que emitir un documento que no se puede
  transmitir —gastando un correlativo que no vuelve—, **no se emite**: se falla
  cerrado en la creación (§5.1) y, por defensa en profundidad, en el envío
  individual (`submit_fiscal_document` rechaza un 07/08 con serie B).

## 9 · Superficie interna (API)

`POST /api/admin/fiscal-documents/{id}/credit-notes/` y `/debit-notes/`, donde
`{id}` es el comprobante **ORIGINAL**.

- **Identidad del original, no del cuerpo (§40/§46/§47, ADR-32).** El original se
  resuelve con el mismo criterio de tenant y sucursal que el resto de la superficie
  fiscal (`_fiscal_document` → `visible_orders`). Empresa, sucursal, serie de la
  nota, adquirente e importes de anulación se derivan del original. Del cuerpo sólo
  se aceptan `reason_code`, `reason_description`, `request_key` y —para la ND—
  `taxable_amount`/`tax_amount`. Un cuerpo que inyecta otro RUC, otra empresa u
  otra serie se ignora (probado).
- **Capacidades (§39).** `sales.fiscal.issue` para emitir; ningún rol heredado la
  concede. Ver una nota usa `sales.fiscal.view` como cualquier comprobante.
- **Idempotencia sobre HTTP.** Misma `request_key` → 201 y luego 200 con el mismo
  `id`.
- **Auditoría (§45).** `fiscal_note_created` y `fiscal_note_signed` en la bitácora,
  con el identificador y el hash del XML, sin secretos ni el XML entero.

Enviar, reconciliar, descargar XML/CDR e imprimir una nota usan los endpoints ya
existentes: una nota ES un `FiscalDocument`.

## 10 · Representación (PDF/QR) (§35/§36)

El parser de representación (`fiscal/representation.py`) reconoce ahora las tres
raíces (`Invoice`/`CreditNote`/`DebitNote`) y elige por ellas los tres nombres que
cambian: renglón, cantidad y total (`RequestedMonetaryTotal` en la ND). El PDF (A4
y ticket 80 mm) y el QR se leen del **XML firmado** (autoridad de representación);
el papel muestra además «Documento que modifica» y el motivo. El QR lleva el tipo
07/08 en su segundo campo, como cualquier comprobante.

## 11 · Clasificación de casos

| Caso | Estado | Evidencia / motivo |
|---|---|---|
| **NC-FAC** | **IMPLEMENTADO · ACEPTADA EN BETA** | Anulación total espejo del original; emite, firma, valida XSD, verifica firma, referencia el original, `sendBill`, reconcilia, PDF/QR. 36 tests dirigidos. **Evidencia SUNAT BETA: `FN01-1` sobre `F001-1` (motivo 01), código `0`, «La Nota de Credito numero FN01-1, ha sido aceptada», CDR sha256 `b55dd752…`.** |
| **ND-FAC** | **IMPLEMENTADO · ACEPTADA EN BETA** | Cargo de importe explícito; `RequestedMonetaryTotal`; mismo canal y superficie. **Evidencia SUNAT BETA: `FD01-1` sobre `F001-2` (motivo 02), código `0`, «La Nota de Debito numero FD01-1, ha sido aceptada», CDR sha256 `235d69f2…`.** |
| **NC-BOL** | **PENDIENTE** | Canal = Resumen Diario, que aún no lleva líneas de nota. Fallo cerrado en creación y envío. |
| **ND-BOL** | **PENDIENTE** | Igual que NC-BOL. |

## 12 · Cobertura de pruebas

36 tests dirigidos nuevos, todos sobre PostgreSQL:

- `Fiscal5aCreditNoteTest` (11): espejo del original, firma+XSD+verificación,
  referencia al original, adquirente del original, idempotencia, segunda nota
  legal, inmutabilidad del original, elegibilidad, motivo fuera de catálogo, canal
  `sendBill`, timeout no es rechazo.
- `Fiscal5aDebitNoteTest` (2): ND de importe explícito con `RequestedMonetaryTotal`;
  motivo de crédito rechazado en una ND.
- `Fiscal5aNoteApiTest` (10): emisión firmada, `view` no puede emitir, otro tenant
  no ve el original (404), el cuerpo no inyecta identidad, idempotencia HTTP, ND
  toma importe del cuerpo, la NC IGNORA importes del cuerpo, motivo inválido → 400
  sin gastar correlativo, falta de motivo (400), original sin aceptar (400).
- `Fiscal5aNotePdfTest` (3): representación lee una NC, QR con tipo y montos, ambos
  formatos de PDF renderizan.
- `Fiscal5aBoletaNotePendingTest` (2): NC de boleta rechazada en creación; el canal
  individual rechaza una nota de boleta.
- `Fiscal5aReviewHardeningTest` (8): la NC refleja la TASA del original (18.00, no
  18.06 recomputado); la NC ignora importes del cuerpo; un original de varias
  líneas se espeja línea a línea; las líneas salen del XML firmado y no de una
  `Order` mutada tras la firma; un motivo inválido no gasta correlativo; la ND
  exige importe; reintentar tras una nota RECHAZADA con la misma clave crea una
  nota nueva (no 500); la restricción DB rechaza dos notas vivas con la misma clave.

Suite completa en PostgreSQL: **4311 tests, OK (skipped=3)** — 4275 heredados + 36
de notas, sin regresiones. (El conteo intermedio 4301 correspondía a la primera
tanda de 26 tests, antes de la ronda de revisión.)

## 12.1 · Revisión adversarial (§4) y hallazgos cerrados

La fase se sometió a una revisión adversarial multi-dimensión (8 dimensiones
independientes; cada hallazgo verificado por 3 escépticos con lentes distintas
antes de sobrevivir). Once hallazgos confirmados, todos corregidos:

| # | Severidad | Hallazgo | Corrección |
|---|---|---|---|
| F1 | media | La anulación total recomputaba la tasa por línea (18.06 en un precio no redondo) en vez de reflejar la del original (18.00). | `RepLine` conserva `cbc:Percent` del original; el espejo la usa verbatim. |
| F2/F3 | alta | La restricción de idempotencia no excluía RECHAZADO, pero el servicio sí: reintentar con la clave de una nota rechazada chocaba (500). | La restricción excluye RECHAZADO (migración `0088`), como el «único vivo por pedido». |
| F5/F7 | alta | La NC honraba importes del cuerpo, contra «los importes de la anulación se derivan del original». | La NC ignora el cuerpo (servicio y vista); sólo la ND toma importe. |
| F6/F8/F9 | media | El motivo se validaba al firmar, tras gastar un correlativo. | El catálogo se comprueba en `create_fiscal_note` ANTES de reservar. |
| F4 | alta (test) | No se probaba que el espejo lea el XML firmado y no la `Order`. | Tests de original multilínea y de `Order` mutada tras la firma. |
| F10 | media (test) | La restricción DB de idempotencia no se ejercía (sólo el pre-chequeo Python). | Test que fuerza el choque a nivel de base de datos. |
| F11 | baja | El espejo se decidía por igualdad de importes; una ND de importe casual igual al original se malclasificaba. | `_note_lines` decide por TIPO (NC espeja, ND una línea), no por importe. |

## 12.2 · Prueba de humo BETA (§51/§52/§53)

El comando `fiscal_beta_smoke` gana dos modos, `--mode credit-note` y
`--mode debit-note`: emiten una factura BETA, esperan su aceptación y sobre ella
emiten la nota (NC de anulación / ND de cargo) por `sendBill`, reportando el CDR.
Como el resto de la prueba de humo, es **opt-in del operador** y jamás corre en CI:
exige `FISCAL_BETA_SMOKE_ENABLED=true`, sólo opera BETA, persiste todo y no revierte
nada (un envío a SUNAT no se deshace con un rollback; el correlativo queda gastado).
**EJECUTADO Y ACEPTADO (2026-09-24).** Se corrió contra SUNAT BETA con las
credenciales públicas (RUC 20100066603 / MODDATOS / moddatos), certificado
autofirmado efímero y una base aislada, sobre una empresa con series BETA de
factura y de nota (F001 / FN01 / FD01). Los cuatro envíos fueron **aceptados con
código `0`**:

| Documento | Estado | Mensaje de SUNAT | CDR sha256 |
|---|---|---|---|
| `F001-1` factura | accepted | La Factura numero F001-1, ha sido aceptada | `216bf0eb…` |
| **`FN01-1` NC** sobre `F001-1` (motivo 01) | **accepted** | La Nota de Credito numero FN01-1, ha sido aceptada | `b55dd752…` |
| `F001-2` factura | accepted | La Factura numero F001-2, ha sido aceptada | `e8cc4f59…` |
| **`FD01-1` ND** sobre `F001-2` (motivo 02) | **accepted** | La Nota de Debito numero FD01-1, ha sido aceptada | `235d69f2…` |

Eso cierra la evidencia externa de 5A: **NC-FAC y ND-FAC quedan IMPLEMENTADAS PARA
BETA**. Los correlativos están gastados y los documentos persisten: un envío a
SUNAT no se deshace. La NC-BOL/ND-BOL por Resumen (§53) no se prueba: es PENDIENTE
(véase §11/§16).

## 13 · Postura de seguridad

Sólo credenciales BETA públicas (RUC 20100066603 / MODDATOS / moddatos); ningún
CDT real, ningún SOL productivo, ninguna producción. Certificado de prueba
autofirmado y efímero. Sin merge, sin push, sin frontend. El detalle de la API no
lleva el XML ni secreto alguno (test heredado). Los identificadores de una nota
salen del original autorizado, nunca del cuerpo (anti-IDOR).

## 14 · Matriz de anulación / corrección — insumo para FISCAL-5B (§51)

El instrumento correcto depende del hecho, y varios de estos NO son una nota.
FISCAL-5B parte de aquí:

| Hecho económico / de negocio | Instrumento correcto | ¿Esta fase? |
|---|---|---|
| Se anula toda la operación (error, no se concretó) | **NC motivo 01** | **Sí (NC-FAC)** |
| Devolución TOTAL de la mercancía | **NC motivo 06** | **Sí (NC-FAC)** |
| Devolución PARCIAL | NC motivo 07 + `AllowanceCharge` | No — PENDIENTE (falta AllowanceCharge) |
| Descuento por ítem posterior | NC motivo 04/05 + `AllowanceCharge` | No — PENDIENTE |
| Corrección de datos del adquirente (RUC/nombre) | NC motivo 02/03 | No — PENDIENTE (motivo representable, sin prioridad en 5A) |
| Corrección de la descripción del ítem | NC motivo 10 | No — PENDIENTE |
| Interés por mora | **ND motivo 01** | **Sí (ND-FAC)** |
| Aumento en el valor (cargo posterior) | **ND motivo 02** | **Sí (ND-FAC)** |
| Penalidad / otros conceptos | **ND motivo 03** | **Sí (ND-FAC)** |
| Boleta emitida por error, mismo día, aún no en Resumen | **Comunicación de Baja / RA** | **No — FISCAL-5B** |
| Factura emitida por error a dar de baja | **Comunicación de Baja (RA)** | **No — FISCAL-5B** |
| NC/ND sobre BOLETA (cualquier motivo) | NC/ND por **Resumen Diario** con líneas de nota | **No — PENDIENTE (extensión del Resumen)** |

> **La baja no es una nota.** Una NC corrige o revierte un comprobante que EXISTE
> y sigue existiendo (con su corrección). La Comunicación de Baja pide a SUNAT que
> un comprobante deje de existir. Son actos distintos; confundirlos es la primera
> trampa que FISCAL-5B debe evitar. Esta fase **no** implementa baja,
> `ConditionCode=3`, RA, ni un «motor de anulación universal».

## 15 · Frontera de la fase (§60) — lo que NO se hizo, a propósito

Comunicación de Baja / RA · `ConditionCode=3` · motor de anulación universal ·
devolución automática de stock · reembolso automático · cancelación de
`Order`/`Payment` · descuentos de factura / `AllowanceCharge` · NC/ND de boleta ·
exoneradas/inafectas/gratuitas · GRE · Consulta Integrada REST · producción ·
frontend. Cada uno es una decisión con su propia autorización y su propia fase.

## 16 · Blockers abiertos heredados

- **RC-XSD-01 (PARCIAL, heredado).** El XSD `SummaryDocuments-1` (UBL 2.0) sigue
  sin poder incorporarse (403 de Cloudflare, prohibido mirror). No afecta a NC/ND
  de FACTURA (usan los `maindoc` UBL 2.1, obtenidos de OASIS y verificados por
  hash). Sí condiciona a NC-BOL/ND-BOL, que además necesitan la extensión del
  Resumen: doble razón para su estado PENDIENTE.
