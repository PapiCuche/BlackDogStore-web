# ERP-FISCAL-5B — COMUNICACIÓN DE BAJA Y ANULACIÓN

Entrega de la fase. Rama `erp/fiscal-sunat`. Nada mergeado, nada empujado, nada en
producción.

---

## 1 · Resumen ejecutivo

La fase construye la **Comunicación de Baja (RA)** para la FACTURA y para las NC/ND
ligadas a una factura, y separa por primera vez dos hechos que el repositorio
confundía: **emitir** un comprobante y **otorgarlo** al adquirente.

Lo que quedó demostrado contra SUNAT BETA: `RA-20260928-1` sobre `F001-3`, ticket
`1790611064145`, **aceptada con código `0`**, CDR-Baja `49a11c28…`.

Lo que NO se hizo, a propósito: el subflujo de la BOLETA. Su camino es el Resumen
Diario con el estado oficial de anulación, y las tres fuentes que lo definen
—Catálogo N.º 19, Anexo N.º 10 y las Reglas de Validación CPE— responden 403 desde
este entorno. Emitir sin ellas sería adivinar el código y las reglas, así que se
falla cerrado y se declara PENDIENTE.

Tres decisiones gobiernan el resto:

1. **El plazo se cuenta desde un hecho, no desde una aproximación.** Siete días
   calendario desde el día siguiente de recibir la CDR aceptada, lo que obligó a un
   dato nuevo de primera clase (`cdr_accepted_at`). Sin él el plazo no es
   demostrable y no se emite baja.
2. **«Emitido» no es «otorgado».** La baja aplica a comprobantes NO otorgados, y eso
   se demuestra; no se deduce de un silencio.
3. **Dar de baja es un acto FISCAL y nada más.** No reembolsa, no repone stock, no
   cancela la `Order` ni el pago.

---

## 2 · Git — base, final, árbol

| | |
|---|---|
| Rama | `erp/fiscal-sunat` (nunca `master`) |
| Base | la punta de ERP-FISCAL-5A, `1e064d2^`, que ya incluía el commit de evidencia BETA de 5A |
| Commits de la fase | **14**, de `1e064d2` a `b54667d` (este dossier es el 15.º) |
| Migraciones | `0089`–`0092`, las cuatro **aditivas** |
| Árbol | limpio en cada commit; el borrador de prompt `docs/PROPUESTA-fiscal-5b.md` se eliminó en el preflight y **no se commiteó** |
| Suite | **4378 tests, `OK (skipped=3)`** en PostgreSQL |
| Diff de frontend | **0** — verificado: el único fichero fuera de `backend/` y `docs/` en los 14 commits es `CHANGELOG.md` |

---

## 3 · Preflight — NC contra SUNAT BETA (§3)

Cerrado antes de escribir código de 5B. `FN01-1` sobre `F001-1`, motivo 01
(anulación total): **aceptada**, código `0`, «La Nota de Credito numero FN01-1, ha
sido aceptada», CDR `b55dd752…`. La factura que le sirve de original, `F001-1`,
aceptada con CDR `216bf0eb…`.

## 4 · Preflight — ND contra SUNAT BETA (§3)

`FD01-1` sobre `F001-2`, motivo 02 (aumento de valor): **aceptada**, código `0`, «La
Nota de Debito numero FD01-1, ha sido aceptada», CDR `235d69f2…`. Su original
`F001-2`, aceptado con CDR `e8cc4f59…`.

Con eso **NC-FAC y ND-FAC de 5A pasan a IMPLEMENTADAS PARA BETA**, que era la
condición del §4 de la autorización para empezar 5B.

Todo con credenciales públicas BETA (RUC 20100066603 / MODDATOS / moddatos),
certificado autofirmado efímero fuera del repositorio, series BETA y datos de
prueba. Sin SOL productivo, sin CDT productivo, sin producción.

## 5 · Revisores y método (§43)

Dos revisiones **independientes** sobre el árbol ya verde:

- **Revisión A — normativa SUNAT/CPE:** ¿dice el código lo que dice la norma
  vigente?
- **Revisión B — seguridad y dominio:** aislamiento multiempresa y por sucursal,
  idempotencia, carreras, fronteras del acto fiscal.

Ocho hallazgos planteados, **cinco confirmados y corregidos** (§45/§46), tres
descartados por no reproducirse contra el código. Los cinco confirmados son
trazables: todos cambian código en `9eb3f6a`, que añade ocho pruebas dirigidas.

Durante la fase hubo además dos episodios de método que conviene registrar: un
diagnóstico propio **equivocado** sobre el XML del Resumen, retractado antes de
convertirse en una regresión (§14), y tres agentes de investigación que se
estancaron contra las fuentes con 403, tras lo cual se dejó de insistir y se
clasificó el asunto como «el operador debe descargarlo» en vez de seguir gastando
intentos.

## 6 · Fuentes oficiales

Autoridad usada, en este orden: código > migraciones > tests > estado técnico >
decisiones > documentación > conversaciones.

**Normativa.** El artículo 14 de la RS 097-2012 fue **sustituido en bloque** por la
**RS 114-2019, numeral 2.5**, vigente desde el 01.07.2019. De ahí salen:

- **14.1.b** — la baja «a más tardar hasta el sétimo día calendario contado a partir
  del día calendario siguiente de haber recibido la respectiva CDR con estado de
  aceptada», y la agrupación sólo de documentos «generados o emitidos en un mismo
  día».
- **14.2.b** — la boleta (y sus notas) por el Resumen, con **dos ventanas**.
- **Artículo 15** — la definición de «otorgado», que es la que hace que «emitido» no
  baste.

**Advertencia registrada:** la «Guía XML Comunicación de Baja» de 2012 que circula
dice **72 horas**. Está **superada** y no se usó para calcular ningún plazo.

**Esquemas.** Paquete XSD oficial de SUNAT (§14). Sin *mirrors* como autoridad y sin
descarga en tiempo de ejecución.

**Inaccesible desde este entorno (403 en `cpe.sunat.gob.pe`):** Catálogo N.º 19,
Anexo N.º 10 y las Reglas de Validación CPE vigentes. Es la razón material de que el
subflujo B quede PENDIENTE.

## 7 · GRANT EVIDENCE GATE (§13/§14/§15)

La puerta se abrió preguntando qué sabe el repositorio sobre la **entrega**. La
respuesta fue: nada. No había ninguna autoridad —ni un campo, ni un evento, ni un
registro— que distinguiera un comprobante entregado de uno que sólo se emitió. Y
`emitido` ≠ `otorgado`; `CDR aceptada` tampoco es `otorgado`.

Por eso `GRANT-EVIDENCE-01` se trató como **bloqueante de diseño** y se construyó la
representación mínima auditable, **genérica y no cableada a esta tienda**:

- `granted_at`, `granted_by`, `granted_evidence` (JSON) y `granted_method`, con
  `FiscalGrantMethod`: `ECOMMERCE_PORTAL`, `EMAIL`, `POS_PRINT`, `POS_ELECTRONIC`,
  `MANUAL`, `API`.
- Para el presente —donde nada registra entregas todavía— una **atestación
  administrativa auditada** de NO otorgamiento: `not_granted_at`,
  `not_granted_by`, `not_granted_reason`.
- Una restricción de base de datos los hace **mutuamente excluyentes**:
  `fiscal_document_grant_is_exclusive`.

**Nunca se acepta un booleano del cuerpo de la petición como autoridad** (§15). Un
`{"not_granted": true}` no prueba nada; lo que abre la puerta es un hecho registrado
con autor y motivo. Y la **ausencia** de evidencia de entrega no es prueba de que no
se entregó: sin atestación, el estado es DESCONOCIDO y se deniega.

> Consecuencia operativa, abierta y declarada: mientras nada registre entregas, toda
> baja exige ese paso manual. Se cierra cuando el checkout y el POS registren el
> otorgamiento (ADR-41).

## 8 · CDR ACCEPTED AT GATE (§11/§12)

Auditado primero: ¿cómo sabemos exactamente cuándo se recibió la CDR aceptada? No lo
sabíamos. `issued_at` es la fecha legal congelada del comprobante, `created_at` y
`updated_at` son ruido de persistencia, y `FiscalSubmissionAttempt` no distingue el
intento que trajo la aceptación.

De ahí `FiscalDocument.cdr_accepted_at`: *nullable*, con zona horaria, **de escritura
única**, sellado la primera vez que el documento alcanza `ACCEPTED` o
`ACCEPTED_WITH_OBSERVATION`, **por cualquiera de las dos rutas** —envío y
reconciliación— y **nunca sobrescrito** después.

**Backfill: ninguno** (§12). No se inventan timestamps históricos. Un documento
legado que está aceptado pero cuya fecha de recepción no se puede demostrar queda en
`NULL`, y eso **falla cerrado**.

Evidencia de que la regla muerde de verdad: en la base aislada del humo, la ND de 5A
`FD01-1` está `accepted` y su `cdr_accepted_at` es `NULL` — por tanto es
**inelegible** para una baja. La factura emitida en esta fase, `F001-3`, sí lo tiene
sellado.

## 9 · DB DESIGN GATE (§18)

Antes de escribir migraciones se decidió la forma. La baja **no** es
`FiscalDocument.status = ANNULLED`: es un documento que se emite, se firma, se
transmite y que SUNAT **acepta o rechaza**. Un estado en el comprobante no puede
representar un rechazo, ni un ticket, ni un CDR propio, ni una agrupación de varios
comprobantes.

De ahí dos entidades propias, y el estado del comprobante **derivado** de ellas.

## 10 · Migraciones

| Migración | Qué añade | Tipo |
|---|---|---|
| `0089_fiscal_cdr_accepted_at_and_grant_evidence` | `cdr_accepted_at` + los cinco campos de otorgamiento y la restricción de exclusividad | aditiva |
| `0090_fiscal_void_communication` | `FiscalVoidCommunication` y `FiscalVoidCommunicationDocument` | aditiva |
| `0091_fiscal_void_request_key` | `request_key` y la única parcial de idempotencia | aditiva |
| `0092_fiscal_not_granted_attestation` | `not_granted_at` / `not_granted_by` / `not_granted_reason` | aditiva |

Ninguna borra ni reescribe datos. Aplicadas sobre la base aislada del humo sin
incidencias.

## 11 · `FiscalVoidCommunication` y su línea

**`FiscalVoidCommunication`** — `company` (PROTECT), `identifier`, `correlativo`,
`reference_date` (indexada), `issue_date`, `environment`, `status`, `signed_xml` +
`signed_xml_sha256`, `ticket`, `submitting_since`, `request_key`, `cdr_xml` +
`cdr_sha256`, `sunat_response_code`, `sunat_response_message`, `created_at`,
`updated_at`.

Restricciones:

- `fiscal_void_unique_correlativo` — único por `(company, environment, issue_date, correlativo)`.
- `fiscal_void_unique_identifier` — único por `(company, environment, identifier)`.
- `fiscal_void_idempotent_request` — único parcial por `(company, environment, request_key)` **excluyendo `REJECTED`**, para que un rechazo libere su hueco y no bloquee el reintento legítimo.
- Índices por `(company, status)` y `(company, reference_date)`.

**`FiscalVoidCommunicationDocument`** — `void_communication` (CASCADE,
`related_name='lines'`), `document` (**PROTECT**, `related_name='void_inclusions'`),
`line_id`, `void_reason` (máx. 100), `superseded`. Restricciones:
`fiscal_void_line_unique` `(void_communication, document)`,
`fiscal_void_line_id_unique` `(void_communication, line_id)` y la parcial
`fiscal_void_one_active_per_document` sobre `document` donde `superseded=False`.

`FiscalVoidStatus` cubre el ciclo completo: `GENERATED`, `SIGNED`, `SUBMITTED`,
`ACCEPTED`, `ACCEPTED_WITH_OBSERVATION`, `REJECTED`, `SUBMISSION_ERROR`,
`SUBMISSION_UNKNOWN`.

El `PROTECT` sobre `document` es deliberado: borrar un comprobante incluido en una
baja no debe poder ocurrir.

## 12 · Membership

**No se toca.** Cero apariciones de `Membership` en el código añadido. La superficie
de baja resuelve autoridad como el resto de la superficie fiscal: `_company_context`
para la empresa y la capacidad, y `visible_orders` para el alcance por sucursal,
con el **puente legacy desactivado** (`_NO_LEGACY_BRIDGE`).

## 13 · `VoidedDocuments` — UBL

Estructura auditada contra el XSD oficial, **sin copiar campos de memoria**.

Raíz `VoidedDocuments` en
`urn:sunat:names:specification:ubl:peru:schema:xsd:VoidedDocuments-1`, atributo
`version="2.0"`. Cabecera: `ext:UBLExtensions` 0..1 · `cbc:UBLVersionID` 0..1 (2.0) ·
`cbc:CustomizationID` (**1.0**, no el 1.1 del Resumen) · `cbc:ID` ·
**`cbc:ReferenceDate` ANTES de `cbc:IssueDate`** · `cbc:Note` 0..n · `cac:Signature`
0..n · `cac:AccountingSupplierParty` · `sac:VoidedDocumentsLine` 1..n.

La línea tiene **cinco hijos, los cinco obligatorios**: `cbc:LineID`,
`cbc:DocumentTypeCode`, `sac:DocumentSerialID`, `sac:DocumentNumberID`,
`sac:VoidReasonDescription` — sólo los tres últimos van en `sac:`.

Nomenclatura: `cbc:ID` = `RA-YYYYMMDD-N` con la fecha de **GENERACIÓN**, correlativo
de 1 a 5 dígitos y **sin RUC**; el nombre del fichero XML/ZIP sí lleva el RUC
delante (`RUC-RA-YYYYMMDD-N`). El validador local lo exige con
`VOID_ID_RE = ^RA-\d{8}-\d{1,5}$`, y el motivo se acota a `REASON_MAX = 100`.

## 14 · XSD

El paquete oficial **sí se publica**, en `contenido.app.sunat.gob.pe` (HTTP), no en
`cpe.sunat.gob.pe`. Vendorizado sin *mirror* y sin descarga en runtime:

| | |
|---|---|
| ZIP consultado | 2026-09-24, 22 entradas (incluye entradas de directorio), 142 899 bytes |
| SHA-256 del ZIP | `391e45abc16107c54989b4f67900bbfc6f9164c10a8212aafef227b6d61a494d` |
| Vendorizado | **20 XSD** en `schemas/2.0/` — 14 en `common/`, 6 en `maindoc/` |
| `UBLPE-VoidedDocuments-1.0.xsd` | `eedb85d7d46ae1d5c9366f1a5562fc78c339cc0c1468da620bd37784ded12b08` |
| `UBLPE-SummaryDocuments-1.0.xsd` | `6c14376ffac0513a6012586307a3aa28b297d70b04cee6677c647f30eb4fca64` |
| `UBLPE-SunatAggregateComponents-1.0.xsd` | `5c32be3b710db2ba2f5d7d2fb0b88734224b4bc9a2ad3204e8f9b7d584a955c8` |

`validate_voided_documents()` valida el RA contra el XSD oficial de verdad.

**Y el hallazgo fue el opuesto al esperado.** Se había declarado que el XSD del
Resumen «no se podía descargar»; se descargó, y resultó que el `SummaryDocuments`
publicado es el Resumen **POR RANGOS** de 2012 —sin `cac:Status`, sin
`cbc:ConditionCode`, sin adquirente—, mientras aquí se emite el Resumen **POR
DOCUMENTO**. Validar contra él **rechazaría documentos correctos**. Por eso
`schema.py` **no** define un `SUMMARY_XSD`, y lo dice con un comentario explícito.

Durante esta fase se emitió un diagnóstico propio equivocado: que el XML del Resumen
era inválido, con un arreglo ya diseñado para partir `cbc:ID`. Era falso, el arreglo
habría sido una regresión, se **canceló**, y la afirmación ya commiteada se corrigió
en `PROCEDENCIA.md`, en el docstring de `summary.py`, en el CHANGELOG, en ADR-28, en
el dossier de 5A y en el estado técnico.

## 15 · Serie y correlativo del RA

El RA **no usa serie**: su identidad es `RA-` + fecha de generación + correlativo. El
correlativo es único por `(empresa, ambiente, fecha de generación)` y se reserva
**dentro de la transacción y sólo después de que todas las puertas de elegibilidad
hayan pasado** — una baja que se va a negar no gasta un número que no se recicla.

La reserva es `max()+1`, pero **no ingenua**: va bajo `select_for_update()`, con la
restricción única como red y un reintento acotado a cinco vueltas.

## 16 · Objetivos elegibles — el orden de las puertas

`check_void_eligible` aplica todas las condiciones **antes** de reservar nada, y
falla cerrado: un «no sé» nunca se lee como un «sí».

1. **Tipo** — sólo `01`, `07`, `08` (`VOIDABLE_DOCUMENT_TYPES`).
2. **Nota de boleta** — un `07`/`08` cuyo original es una boleta se rechaza aquí: su
   canal es el Resumen (14.2.b). *El tipo por sí solo no lo distingue; hay que mirar
   el original.*
3. **Firmado** — sin `signed_xml` no hay nada que dar de baja.
4. **Aceptado** — sin CDR aceptada no procede la baja.
5. **Plazo demostrable** — `cdr_accepted_at` presente; si no, REQUIERE REVISIÓN
   OPERATIVA/TRIBUTARIA.
6. **Plazo vigente** — dentro de los siete días; si venció, se deniega.
7. **No otorgado** — `granted_at` vacío **y** `not_granted_at` presente.
8. **Sin nota aceptada en contra** — si se corrigió con una nota, no se da de baja.
9. **Sin baja viva** — no se incluye el mismo comprobante en dos.

## 17 · Factura (`01`)

Camino completo y **verificado en BETA**: elegibilidad → correlativo →
`VoidedDocuments` → XSD oficial → firma → `sendSummary` → ticket → `getStatus` →
CDR-Baja. §40 tiene la evidencia.

## 18 · NC/ND vinculadas a factura (`07`/`08`)

**Son objeto de baja.** Se eliminó toda regla absoluta del tipo «una NC/ND no se da
de baja»: lo que manda es el comprobante al que están vinculadas. `VOIDABLE_TYPES`
las admite y las pruebas lo cubren.

Y no se confunde con el caso distinto del §6 de la autorización: una NC **aceptada
contra** una factura **deniega** la baja de esa factura (y la NC no se toca); una NC
que fue emitida y aceptada pero **no otorgada** puede ella misma ser objeto de baja.

Estado: **IMPLEMENTADO, con BETA no ejercitado** — el humo dio de baja una factura,
no una nota.

## 19 · Boleta (`03`)

**PENDIENTE y declarado.** Su camino es el Resumen Diario con el estado oficial de
anulación, reutilizando `FiscalDailySummary`; **no** se crea una
`FiscalVoidCommunication` para boletas. El servicio la rechaza explícitamente con el
motivo normativo (14.2.b).

Bloqueo doble:

1. **Fuentes.** Catálogo N.º 19, Anexo N.º 10 y Reglas de Validación CPE → 403.
2. **Diseño.** Exige rehacer `fiscal_summary_one_active_per_document` y la semántica
   de `superseded` al rechazarse; sin eso, un resumen de anulación rechazado
   **borraría silenciosamente su propia intención**.

## 20 · NC/ND vinculadas a boleta — estado

**PENDIENTE HASTA NC-BOL/ND-BOL.** La infraestructura es compatible con `07`/`08`
ligados a boleta —la puerta 2 del §16 existe precisamente para encaminarlos—, pero
**no se inventaron documentos NC-BOL/ND-BOL para probarlo**: en 5A siguen PENDIENTES.
La prueba de esa puerta construye la fila directamente y lo dice.

## 21 · Plazo — factura y nota de factura

`void_deadline(document)` = fecha local de `cdr_accepted_at` **+ 7 días**, o `None`
si no hay fecha. Nunca se aproxima desde `issued_at`, `created_at` ni `updated_at`.

Se comprueba **dos veces**: al **crear** y otra vez al **enviar**. Un único ayudante,
`void_send_blocked_reason`, gobierna a la vez la puerta del envío y el `can_submit`
que ve la interfaz, para que el botón y la puerta no puedan divergir — que es
exactamente cómo se abrió el agujero que encontró la revisión (§45).

**Plazo vencido = negativa.** No se emite ninguna NC automática en su lugar. Si el
comprobante no fue otorgado pero el plazo venció, el caso se marca **REQUIERE
REVISIÓN OPERATIVA/TRIBUTARIA**: es una decisión de negocio, no algo que el software
deba resolver solo.

## 22 · Plazos — boleta (dos ventanas)

Documentado, **no implementado**, porque el subflujo B no se implementó. El 14.2.b da
dos ventanas distintas: la boleta **ya informada** en un Resumen cuenta desde la CDR
aceptada de ese Resumen; la **no informada** cuenta desde su generación. Son dos
relojes y no se pueden colapsar en uno.

## 23 · Estado derivado

`is_voided(document)` responde **por la relación**, no por un estado sobrescrito:
existe una inclusión no superada en una comunicación `ACCEPTED` o
`ACCEPTED_WITH_OBSERVATION`. Así el original sigue diciendo que fue aceptado el día
que lo fue, y la baja es un hecho posterior que se **suma** — lo que permite auditar
las dos cosas.

## 24 · Conservación de la historia

No se borra ni se reescribe nada: ni XML firmado, ni CDR, ni serie, ni número, ni
auditoría, ni relaciones. Nada hace que el original parezca «nunca aceptado».

**Verificado en la base después de la baja aceptada:** `F001-3` sigue `accepted`,
conserva su XML firmado (6349 B, `fd5d57e2…`) y su CDR (3353 B, `61a6890c…`), con
`cdr_accepted_at` sellado y la atestación registrada. La línea de la baja sigue ahí
(`line_id` 1, motivo de 44 caracteres, `superseded=false`).

Y cuando una comunicación se **rechaza**, sus líneas se marcan `superseded=True` en
vez de borrarse: se liberan los comprobantes para intentarlo en otra, no se da por
dado de baja nada, y **no se borra la intención**.

## 25 · Idempotencia

`request_key` por `(company, environment)`, excluyendo `REJECTED`. Dos clics o dos
workers con la misma clave devuelven **la misma** comunicación.

Y la clave **no identifica por sí sola**: `_live_request` comprueba que el conjunto
de comprobantes coincida con el pedido. Si se reutiliza sobre otros documentos,
**levanta** en vez de devolver un «listo» que nombra un comprobante distinto — que
habría dejado el segundo sin dar de baja sin que nadie se enterara. Se valida en los
**dos** sitios: el prechequeo y la recuperación de la carrera.

## 26 · Concurrencia

Reserva bajo `select_for_update()`, restricción única como red, cinco reintentos. Y
una distinción que importa: **dos choques distintos comparten `IntegrityError`**. Si
fue la clave de idempotencia, no hay nada que reintentar y se devuelve la
comunicación existente; sólo si fue el correlativo se reintenta con el siguiente. Sin
esa distinción, una carrera de idempotencia agotaría los cinco intentos y saldría
como «no se pudo reservar un correlativo», que es falso y manda a investigar el
sitio equivocado.

La red **nunca** va dentro de la transacción.

## 27 · `sendSummary`

El RA se transmite por `sendSummary`, como el Resumen —no por `sendBill`—. El envío
se **reclama** antes de hablar con la red y se **finaliza** después, sin sostener
ningún bloqueo durante el SOAP.

## 28 · Ticket

Se **persiste antes** de consultarlo. Un ticket que llega y no se guarda es un
envío que existe en SUNAT y no existe aquí.

## 29 · `getStatus`

Polling **acotado**, nunca dentro de una transacción. `PROCESSING` devuelve
`processing` y no cambia nada; un error de transporte o una respuesta ilegible se
reportan como tales; si no hay CDR se devuelve `no_cdr`.

## 30 · `SUBMISSION_UNKNOWN`

Un `sendSummary` que transmitió pero no devolvió ticket deja el resultado **INCIERTO**
—puede existir un ticket que nunca recibimos—, distinto de un fallo que no
transmitió (seguro). No se reenvía a ciegas: se expone `can_recover`, y la
recuperación consulta, no reintenta.

## 31 · CDR-Baja

Es el **tercer** CDR del proceso, distinto del de la factura y del del Resumen, y se
liga **estrictamente** a su comunicación: `cdr_matches_document` se comprueba contra
`void.identifier` —el `ReferenceID` del CDR-Baja es el RA, no un comprobante— y el
RUC emisor. Uno de otra comunicación **no se aplica jamás**; se devuelve
`cdr_mismatch`. Un CDR ilegible da `cdr_unreadable`; un `outcome` no mapeado,
`cdr_inconclusive`. La escritura final va bajo bloqueo y respeta el estado terminal
ya fijado (`already_terminal`), así que no se sobrescribe.

## 32 · RC `ConditionCode=3`

**No implementado, y a propósito.** Pertenece al camino de la **boleta/Resumen**, no
al de la factura, y sólo se usaría si las reglas oficiales vigentes confirmaran esa
representación para la línea concreta. Esas reglas son justamente las que devuelven
403. No se escribió ningún `condition_code='3'`.

## 33 · Aislamiento por empresa y sucursal

El objetivo se **deriva** del `FiscalDocument` local autorizado; el cuerpo de la
petición sólo aporta motivo y `request_key`. Una comunicación no mezcla comprobantes
de empresas distintas. `_void` filtra por `visible_orders(request.user, company)`, de
modo que una baja de **otra sucursal** responde **404** —no 403, que confirmaría que
existe—. Un cruce entre empresas, igual: 404.

## 34 · Capacidades

`sales.fiscal.issue` (`CAP_FISCAL_ISSUE`) para crear, firmar, enviar y consultar;
`sales.fiscal.view` (`CAP_FISCAL_VIEW`) para leer el detalle. Otorgar y atestiguar
exigen `issue`.

## 35 · Auditoría

`AdminAuditLog.log(...)` en seis puntos, con actor, tipo y id del objetivo, empresa y
petición: `fiscal_document_granted`, `fiscal_document_attested_not_granted`,
`fiscal_void_created`, `fiscal_void_signed`, `fiscal_void_{estado}` y
`fiscal_void_polled`.

Los metadatos llevan identificador, método, motivo, objetivo, `xml_sha256`, ticket,
código de respuesta y acción del sondeo. **Ni credenciales, ni Clave SOL, ni clave
del certificado, ni el sobre SOAP.**

## 36 · Aislamiento del pago

Sin tocar. Verificado en la base tras la baja aceptada:
`store_paymenttransaction` = **0**, `store_repairpayment` = **0**. Ningún reembolso,
ninguna mutación de pago. El código añadido no importa ningún servicio de pago.

## 37 · Aislamiento del stock

Sin tocar. `store_stockmovement` = **0** tras la baja. No se repone inventario ni se
crea movimiento de Kardex.

## 38 · Aislamiento de la orden

Sin tocar. Órdenes canceladas = **0**. La orden de `F001-3` sigue `paid`, con total
`118.00` y IGV `18.00`. No se cambia `Order.status`, ni se cancela, ni se altera el
cumplimiento.

> Las tres fronteras están además afirmadas en el propio docstring del servicio: dar
> de baja «no reembolsa, no repone stock, no crea movimientos y no cancela la Order
> ni el Payment. Esas integraciones son de otra fase, con su propia autorización».

## 39 · API — superficie interna

| Ruta | Capacidad |
|---|---|
| `POST admin/fiscal-documents/{id}/grant/` | issue |
| `POST admin/fiscal-documents/{id}/not-granted/` | issue |
| `POST admin/fiscal-documents/{id}/void/` | issue |
| `GET admin/fiscal-void-communications/{id}/` | view |
| `POST admin/fiscal-void-communications/{id}/submit/` | issue |
| `POST admin/fiscal-void-communications/{id}/status/` | issue |

La carga útil expone `can_submit` (que consulta el plazo), `can_poll` y
`can_recover`, para que la interfaz no tenga que recalcular reglas fiscales.

## 40 · BETA — RA (§38)

**EJECUTADO Y ACEPTADO, 28-09-2026.** Entorno aislado: base `blackdog_beta_smoke`
migrada a `0092`, certificado efímero fuera del repositorio, credenciales públicas
BETA, empresa con series BETA. Todo persiste y nada se revierte: un envío a SUNAT no
se deshace con un *rollback*, y los correlativos quedan gastados.

| Paso | Resultado |
|---|---|
| Factura original `F001-3` | `accepted`, código `0`, CDR `61a6890c…` |
| `cdr_accepted_at` | sellado (sin él el comando se niega a seguir) |
| Atestación de NO otorgamiento | registrada **sin autor** — la firma un comando, no una persona, y se dice explícitamente |
| `RA-20260928-1` | firmado, validado contra el XSD oficial, `reference_date` = `issue_date` = 2026-09-28, correlativo 1 |
| `sendSummary` | ticket `1790611064145` |
| `getStatus` 1/6 | `reconciled`, `accepted`, código `0` |
| **CDR-Baja** | `49a11c28…` — «La Comunicacion de baja RA-20260928-1, ha sido aceptada» |

## 41 · BETA — baja de boleta (§39)

**NO EJECUTADO: no es posible en esta fase.** Requiere el subflujo B, que está
bloqueado por las fuentes con 403 (§19). Se falla cerrado ante la ambigüedad, como
pedía el §39, en vez de enviar una representación adivinada a SUNAT.

## 42 · Pruebas dirigidas

**67 tests** en cinco clases:

| Clase | Tests |
|---|---|
| `Fiscal5bAcceptanceDateTest` | 5 |
| `Fiscal5bGrantEvidenceTest` | 3 |
| `Fiscal5bVoidBuilderTest` | 10 |
| `Fiscal5bVoidServiceTest` | 36 |
| `Fiscal5bVoidApiTest` | 13 |

Cubren: el sellado en ambas rutas de aceptación y su no-sobrescritura; la
exclusividad otorgado/no-otorgado a nivel de restricción de BD; la estructura del XML
contra el XSD oficial y el orden `ReferenceDate`/`IssueDate`; cada puerta del §16 por
separado; el plazo al crear y al enviar; la idempotencia y su choque de claves; la
agrupación de un solo día; el rechazo que marca `superseded`; el *binding* del
CDR-Baja; y en la API el 404 entre empresas y entre sucursales.

## 43 · Suite completa en PostgreSQL

```
Ran 4378 tests in 1447.937s
OK (skipped=3)
```

`DATABASE_URL=postgres://…/blackdog`, `--noinput`. Cero fallos, cero errores.
Baseline de 5A: 4311 con 3 omitidas → **+67**, que es exactamente el número de
pruebas nuevas. El recuento de `def test_` en `tests.py` es 4378, así que la cifra
cuadra por dos caminos independientes.

## 44 · Delta de omitidas

**Cero.** 3 antes, 3 después.

No hay ningún decorador `@skip` en el árbol: las 19 omisiones son condicionales en
tiempo de ejecución. **15** están guardadas por `connection.vendor == 'sqlite'` —son
las pruebas de concurrencia real, que en PostgreSQL **se ejecutan**, y por eso
PostgreSQL omite menos, no más—; **1** exige un componente de frontend que no está en
este árbol; **3** se omiten porque el catálogo de capacidades no reserva ninguna en
esta fase. Esas tres son las que se ven.

## 45 · Revisión A — normativa SUNAT

Dos hallazgos confirmados:

1. **El plazo se exigía al CREAR pero no al ENVIAR**, y el 14.1.b lo pone en el
   envío. Un verificador lo demostró: el mismo documento que la puerta de creación
   rechazaba seguía transmitiéndose, porque `/void/` firma sin enviar y un error de
   transporte deja un RA reenviable. **Corregido**: la comprobación vive en un solo
   ayudante que consultan `submit` y `can_submit`; una comunicación cuya ventana se
   cerró se rechaza en vez de reenviarse con una `IssueDate` congelada y rancia.
2. **Una nota hereda el canal de lo que corrige**: un `07`/`08` de BOLETA pertenece
   al Resumen (14.2.b), nunca a un RA. **Corregido** como blindaje a futuro, ya que
   NC-BOL sigue PENDIENTE.

## 46 · Revisión B — seguridad y dominio

Tres hallazgos confirmados:

1. **Alcance por sucursal ausente.** Las tres rutas de baja resolvían el RA sólo por
   empresa, saltándose el filtrado por sucursal que impone el resto de la superficie
   fiscal: un operador acotado a una sucursal podía **leer, transmitir y finalizar**
   la baja de otra. **Corregido**: `_void` filtra por `visible_orders`, y una ajena
   responde **404**.
2. **Clave de idempotencia sin comprobar contra los objetivos.** Reutilizarla sobre
   otro comprobante devolvía un éxito que nombraba al equivocado, y el segundo no se
   daba de baja nunca. **Corregido** en los dos sitios de llamada.
3. **Exclusividad nota/baja en un solo sentido.** La baja rechazaba una nota
   aceptada, pero la emisión de notas no consultaba la relación de baja. **Corregido**,
   fallando cerrado también ante una baja en vuelo.

Tres hallazgos más se **descartaron** por no reproducirse contra el código. Los cinco
confirmados son trazables en `9eb3f6a`, que añade ocho pruebas dirigidas.

## 47 · Archivos

**Nuevos:** `fiscal/void.py`, `fiscal_void_services.py`, `fiscal_void_views.py`, las
cuatro migraciones `0089`–`0092`.

**Modificados:** `models.py`, `urls.py`, `tests.py`, `fiscal_services.py`,
`fiscal_note_services.py`, `fiscal/schema.py`, `fiscal/packaging.py`,
`fiscal/summary.py`, `fiscal/schemas/PROCEDENCIA.md`,
`management/commands/fiscal_beta_smoke.py`.

**Documentación:** `CHANGELOG.md`, `docs/estado-actual-y-auditoria-tecnica.md`,
`docs/adr-fiscal-c22a1.md`, `docs/entrega-fiscal-5a-notas.md`, y este dossier.

**Esquemas:** 20 XSD bajo `backend/store/fiscal/schemas/2.0/`.

Cero ficheros de frontend.

## 48 · Commits

Catorce, en unidades lógicas, del más antiguo al más reciente:

| Hash | Asunto |
|---|---|
| `1e064d2` | vendor the official SUNAT UBL 2.0 schema package |
| `42a7141` | record when an accepted CDR arrived, and whether a comprobante was granted |
| `52e87c9` | correct what the official UBL 2.0 package does and does not settle |
| `d18c42e` | the Resumen XSD was obtained, but it is the wrong version |
| `2f290e5` | model the Comunicación de Baja and its member documents |
| `0277460` | build the Comunicación de Baja against SUNAT's official schema |
| `9b90396` | issue a Comunicación de Baja, with both gates failing closed |
| `6209d36` | propagate the RC-XSD-01 correction to the four places that still said 'unobtainable' |
| `373209c` | make issuing a Comunicación de Baja idempotent |
| `6e1eb92` | establish 'not granted' as a signed fact, not an absence |
| `c5277a9` | internal surface for the baja and for otorgamiento |
| `9eb3f6a` | close the five findings the §43 review confirmed |
| `721f23e` | record the eight decisions behind the Comunicación de Baja |
| `b54667d` | record the Comunicación de Baja and what BETA accepted |

Este dossier es el 15.º. Nada mergeado, nada empujado.

## 49 · Documentación

- **CHANGELOG** — entrada de la fase con la matriz de casos y su estado.
- **Estado técnico** — bloque de actualización al frente del documento.
- **ADR-34 a ADR-41** (41 en total en el fichero fiscal):
  - **34** · El «no otorgamiento» se ATESTIGUA; una ausencia no prueba nada.
  - **35** · `cdr_accepted_at` existe porque el plazo cuenta desde la RECEPCIÓN.
  - **36** · El paquete XSD oficial se incorporó, y sólo uno de sus esquemas nos aplica.
  - **37** · Una nota también puede ser objeto de baja; la boleta no, por este canal.
  - **38** · El plazo se comprueba al CREAR y otra vez al ENVIAR.
  - **39** · Un envío transmitido sin ticket es INCIERTO, también en la baja.
  - **40** · Dar de baja es un acto FISCAL, y nada más.
  - **41** · La emisión y el otorgamiento son eventos distintos (regla para la fase de UI).
- **`PROCEDENCIA.md`** — procedencia y hashes, y qué resuelve y qué no el paquete.
- **Dossier de 5A** — actualizado con la corrección de RC-XSD-01.

**Reglas de producto documentadas, no implementadas** (para `ERP-SALES-FISCAL-UI`):
el checkout del e-commerce origina sólo BOLETA o FACTURA, **lo elige el cliente**, y
la emisión ocurre únicamente **tras el pago confirmado**, sin *default* silencioso;
en el POS el operador **elige explícitamente** y la elección queda **congelada**;
NC/ND/RA/bajas son flujos internos de administración.

## 50 · Deuda

| Deuda | Estado |
|---|---|
| **Subflujo B (boleta y sus notas)** | Abierta. Bloqueada por fuentes con 403 **y** por el rediseño de `fiscal_summary_one_active_per_document` / `superseded`. |
| **`GRANT-EVIDENCE-01` — consecuencia operativa** | Abierta. El mecanismo existe; mientras nada registre entregas, cada baja exige la atestación manual. |
| **`RA-NC-FAC` / `RA-ND-FAC` en BETA** | Abierta. Implementadas y cubiertas localmente, sin ejercitar contra BETA. |
| **`RC-XSD-01`** | PARCIAL, heredada, con motivo más preciso: el esquema publicado no aplica al Resumen por documento. |
| **`CDR-TRUST-01`** | Heredada de FISCAL-3: la autenticidad del CDR sigue `unverified` porque SUNAT no publica ancla de confianza. |
| **Legado sin `cdr_accepted_at`** | Por diseño: inelegible para baja hasta que exista evidencia exacta. No se inventa. |
| **Producción** | Deshabilitada (ADR-10). Certificado acreditado y credenciales por empresa siguen sin existir. |

## 51 · Clasificación final

| Caso | Estado |
|---|---|
| **RA-FAC** | **IMPLEMENTADO** · aceptado en SUNAT BETA |
| **RA-NC-FAC** | **IMPLEMENTADO** · BETA no ejercitado |
| **RA-ND-FAC** | **IMPLEMENTADO** · BETA no ejercitado |
| **VOID-BOL** | **PENDIENTE** |
| **VOID-NC-BOL** | **PENDIENTE** |
| **VOID-ND-BOL** | **PENDIENTE** |
| **GRANT-EVIDENCE-01** | **PARCIAL** — mecanismo implementado; el registro automático del otorgamiento queda pendiente |
| **CDR-ACCEPTED-AT** | **IMPLEMENTADO** |
| **RC-XSD-01** | **PARCIAL** |
| **SUBMISSION-UNKNOWN** | **IMPLEMENTADO** (para el RA) |

## 52 · Recomendación de próxima fase — NO iniciada

**Recomendada: `ERP-SALES-FISCAL-UI`.**

Razón: es la única de las dos candidatas que **no está bloqueada por fuentes
inaccesibles**, y cierra la deuda que hoy más limita el uso real de lo construido. El
RA funciona y SUNAT lo acepta, pero cada baja exige una atestación manual porque nada
registra las entregas. Esa fase pondría el otorgamiento donde ocurre —el checkout y
el POS—, con las reglas ya documentadas en §49 y ADR-41: elección explícita de boleta
o factura, emisión sólo tras el pago confirmado, elección congelada, y `granted_*`
escrito por el propio acto de entrega en vez de por una persona afirmándolo después.

**`ERP-FISCAL-5C` (subflujo B) queda en espera de insumos del propietario**, no de
trabajo: el Catálogo N.º 19, el Anexo N.º 10 y las Reglas de Validación CPE vigentes,
descargados manualmente. Con ellos en el repositorio, 5C es abordable y su alcance ya
está acotado: extender el Resumen para llevar líneas de anulación, rehacer
`fiscal_summary_one_active_per_document` y la semántica de `superseded` al rechazarse
—para que un resumen de anulación rechazado no borre su propia intención—, y sólo
entonces las dos ventanas del 14.2.b.

Ninguna de las dos se inicia sin autorización explícita.
