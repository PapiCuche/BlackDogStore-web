# SUNAT — estado del módulo fiscal (noviembre 2026)

Fecha: 2026-11-27. Código leído: `master` `5acc743e`, en modo de sólo lectura.
Rutas relativas a `backend/store/`, en formato `archivo:línea` o `archivo::símbolo`.

**Límites de esta auditoría.**
- No se emitió ningún comprobante real.
- No se llamó a ningún endpoint de producción de SUNAT.
- No se tocó ninguna credencial, certificado ni usuario SOL real.
- No se accedió a ningún servidor.

Este documento **no es asesoría tributaria**. Lo que depende de SUNAT o del contador está marcado como tal.

Continúa y actualiza estos documentos, que siguen vigentes en lo que no se corrige aquí:
- [fiscal-prod-01-auditoria-f1.md](fiscal-prod-01-auditoria-f1.md), del 2026-10-31 sobre `6c63235`;
- el ADR abierto del PR #173 (`docs/adr-fiscal-prod.md`, del 2026-10-25);
- [adr-fiscal-c22a1.md](adr-fiscal-c22a1.md), con las ADR-1…44.

No hay un sistema fiscal paralelo: todo lo que sigue habla del módulo existente.

---

## 0. Veredicto

El módulo está **maduro para BETA**:
- genera XML UBL 2.1, lo valida con el XSD oficial y lo firma;
- envía facturas y notas de factura por `sendBill`;
- envía boletas por Resumen Diario y bajas por Comunicación de Baja;
- guarda el CDR byte a byte;
- no reenvía a ciegas una respuesta incierta;
- reconcilia por `getStatusCdr`.

En SUNAT BETA hay evidencia aceptada de factura, NC, ND, Resumen Diario y Comunicación de Baja (sección 3).

**No emite en producción mientras nadie lo decida, y es a propósito.**
- Desde C3 (sección 6.1) el código **sabe** operar producción, pero está apagado por defecto:
  `FISCAL_PRODUCTION_ENABLED=False` (`backend/settings.py`) y la consola SUNAT en BETA.
- `fiscal_config.resolve_environment(company)` sólo devuelve producción con el interruptor de instalación,
  la consola activada en modo producción (con `fiscal_preflight --target production` en GO), el RUC de la
  empresa igual al de la Clave SOL y la consulta de CDR encendida. Si se pidió producción y falta algo,
  **falla cerrado**: nunca vuelve a BETA en silencio.
- Los endpoints de producción son constantes del código (`fiscal/provider.py::PRODUCTION_ENDPOINT`,
  `PRODUCTION_CONSULT_ENDPOINT`), con su fuente oficial citada.

Para producción faltan cosas de **código**, **datos del propietario** y **confirmación normativa** (secciones 4 y 6).

**Cambios desde la auditoría del 31-oct.** Los cambios fiscales posteriores son de representación y no tocan el envío:
- diseño A4 y ticket (FISCAL-DESIGN-01);
- líneas con promoción en el A4 y en la NC (FISCAL-A4-PROMO-LINES);
- comprobantes registrados fuera del sistema (NOTE-NOT-SUBSTITUTE, `fiscal_services.py:553`).

Siguen abiertos estos hallazgos del ADR #173:
- H-05, **H-06** y H-08;
- H-09 y H-12;
- H-13, **H-14** y H-15.

H-10 está cerrado en la **creación** de la nota (`fiscal_note_services.py:64-71`): una nota no se crea sobre un original con una baja viva o aceptada.

---

## 1. Fuentes oficiales verificadas el 2026-11-27

Se leyeron directamente los PDF oficiales; están guardados fuera del repositorio. Las páginas HTML de `cpe.sunat.gob.pe` devuelven
403 a lecturas automatizadas: lo que viene de ellas se marca **(fragmento de búsqueda)** y hay que confirmarlo a mano.

| # | Hecho | Veredicto | Fuente |
|---|---|---|---|
| F1 | **Endpoints:**<br>• producción: `https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService`<br>• BETA: `https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService`<br>• consulta de CDR: `https://e-factura.sunat.gob.pe/ol-it-wsconscpegem/billConsultService`<br>`getStatusCdr` sólo admite 01, 07 y 08 | CONFIRMADO | Manual del programador v2.1 (12/05/2021), pp. 12, 22, 24-25: https://cpe.sunat.gob.pe/sites/default/files/inline-files/manual_programador%20(1).pdf |
| F2 | **Envíos y respuestas:**<br>• `sendBill` es síncrono para la factura y sus notas<br>• `sendSummary` es asíncrono para el RC y la RA, y devuelve ticket<br>• `getStatus` responde 0, 98 o 99 | CONFIRMADO | Manual, pp. 14-16 y 20 |
| F3 | **Códigos de respuesta:**<br>• 0100-0999 y 1000-1999: excepción, «no informado», se puede reenviar<br>• 2000-3999: rechazo; en factura y notas, «la numeración se considera ya utilizada»<br>• 4000 en adelante: observación; el comprobante es válido | CONFIRMADO | Manual, pp. 28-29 |
| F4 | **Boleta:**<br>• Resumen Diario a más tardar el 7.º día calendario<br>• o envío individual hasta 5 días; vencido ese plazo, sólo el resumen | CONFIRMADO, con matiz. La RS 000003-2023 renumeró el art. 12 y no se pudo releer el texto consolidado | RS 114-2019, arts. 2.1, 2.3 y 2.7: https://www.sunat.gob.pe/legislacion/superin/2019/114-2019.pdf; https://orientacion.sunat.gob.pe/04-resumen-diario-boleta-de-venta-electronica |
| F5 | Factura y nota vinculada: hasta **3 días calendario** desde el día siguiente a la emisión | CONFIRMADO | RS 000003-2023, art. 3: https://www.sunat.gob.pe/legislacion/superin/2023/000003-2023.pdf |
| F6 | La RS 000143-2026 posterga la RS 000048-2026 **al 1-ene-2027**, y sus designaciones al **1-abr-2027**. En el índice 2026, hasta la RS 000233-2026 del 26/11/2026, no aparece una norma posterior que la cambie | CONFIRMADO | https://www.sunat.gob.pe/legislacion/superin/2026/000143-2026.pdf; https://www.sunat.gob.pe/legislacion/superin/2026/indices/indcor.htm |
| F7 | **Versiones vigentes:**<br>• Reglas de validación: **«actualizado al 26.08.2026»**<br>• XSL 2.1: 17/11/2026<br>• XSD 2.1: 28/02/2022, el mismo que está versionado en `fiscal/schemas/PROCEDENCIA.md` | **Las reglas de `rules.py` (21.04.2025) van al menos dos versiones atrás** (fragmento de búsqueda) | https://cpe.sunat.gob.pe/guias-y-manuales |
| F8 | **Certificado:**<br>• X.509 v3, con el **RUC en el campo OU**<br>• comunicado a SUNAT en SOL, vigente y no revocado<br>**Usuario SOL:** secundario, con el perfil «Envío de documentos electrónicos-Grandes emisores»<br>**BETA:** sin certificado registrado, con `[RUC]MODDATOS`/`MODDATOS` | CONFIRMADO. La exigencia de un prestador ROPS/INDECOPI no se encontró en el Manual: NO VERIFICADO aquí | Manual, §2.2, §2.3 y §3.1 |
| F9 | **Conservación:**<br>• guardar las constancias de rechazo, los resúmenes y las bajas<br>• **durante un año, dar al adquirente acceso web autenticado** a sus comprobantes | CONFIRMADO | https://orientacion.sunat.gob.pe/12-constancia-de-recepcion-conservacion-otorgamiento |
| F10 | **Obligación de emitir:** el RUC del régimen general está designado desde su inscripción o, como tarde, desde el **1-jun-2026** | CONFIRMADO. El propietario debe confirmar la fecha en SOL | RS 000075-2026: https://www.sunat.gob.pe/legislacion/superin/2026/000075-2026.pdf |
| F11 | **Homologación eliminada.** Para ser emisor basta indicarlo en SOL y registrar un certificado y un correo | CONFIRMADO en la auditoría F1 (no se releyó) | RS 287-2017: https://www.sunat.gob.pe/legislacion/superin/2017/287-2017.pdf |

**Correcciones a documentos del repositorio.**
- `sunat-cpe-requisitos.md` daba por vigente la RS 000048-2026 desde el 1.8.2026. Desde la RS 000143-2026 rige el 1-ene-2027; se corrige en este PR.
- El ADR #173 cita unas reglas «24.04.2026». Las vigentes son del **26.08.2026** (F7).
- La auditoría F1 dejó en NO VERIFICADO el envío individual de boleta. El texto de la RS 114-2019 lo permite hasta 5 días (F4), pero el código no lo implementa (§2, fila 5).
- F4 responde el hueco 3 de `sunat-cpe-requisitos.md` (§10, «Plazo del resumen diario de boletas»): a más tardar el
  7.º día calendario siguiente a la emisión (RS 097-2012, art. 21.1, en el texto de la RS 114-2019).
- `sunat-factura-ubl21-matriz.md` daba el Anexo 6 de la RS 000048-2026 como «vigente 1.8.2026»; se corrige en este PR
  (rige el 1-ene-2027, F6).

---

## 2. Matriz de los 17 puntos

Estados: **IMPLEMENTADO** · **PARCIAL** · **PENDIENTE** · **BLOCKED** (necesita algo externo que no se puede obtener desde el código).

| # | Punto | Estado | Evidencia en código | Evidencia de prueba | Brecha |
|---|---|---|---|---|---|
| 1 | XML UBL 2.1 y validación por esquema | **IMPLEMENTADO** para gravado (10) | Generación: `fiscal/builder.py`.<br>Reglas: `fiscal/rules.py::validate`.<br>XSD oficial versionado de factura, NC y ND: `fiscal/schema.py:32-34`; la factura se valida antes de la red en `fiscal_services.py:740`; las notas en `fiscal_note_services.py:326-328`.<br>RA contra `UBLPE-VoidedDocuments-1.0.xsd`: `fiscal_void_services.py:489`.<br>RC con validación estructural propia, porque no hay XSD oficial del resumen por documento: `fiscal/summary.py:163` | `tests.py::C22A1XmlTest`, `C22A1RulesTest`, `FiscalSummaryStructureTest`, `Fiscal5bVoidBuilderTest`; `test_fiscal_credit_note_promo.py:145,225` | • Sólo afectación 10: exonerado, inafecto y gratuito fallan cerrado.<br>• Reglas transcritas del xlsx 21.04.2025; la vigente es la del 26.08.2026 (F7).<br>• `AddressTypeCode` fijo en `0000` (`fiscal/builder.py:198`).<br>• `FormaPago` siempre «Contado» (`fiscal/builder.py:75`). |
| 2 | Firma digital | **IMPLEMENTADO** | XMLDSig enveloped, C14N 1.0, RSA-SHA256: `fiscal/signing.py:66` (`SUNAT_PROFILE`).<br>Al pedir credenciales se comprueba que el par certificado/clave case: `fiscal_config.py:304-318` | `tests.py::C22A1SignatureTest`, `FiscalCertificateLoaderTest`; `test_fiscal_prod02.py::SummaryIsGeneratedAndSignedAsOneActTest` | • Que **producción** acepte SHA-256 no está verificado.<br>• No se comprueba la **vigencia** del certificado al firmar; sólo lo hace el botón de prueba de la consola (`integrations/providers/sunat.py:83-104`).<br>• No se comprueba que el **RUC del OU** sea el del emisor (F8). |
| 3 | Pruebas en BETA | **IMPLEMENTADO** (manual, opt-in) | Comando `management/commands/fiscal_beta_smoke.py`: apagado por defecto (`FISCAL_BETA_SMOKE_ENABLED`), sólo BETA, nunca en CI. Espolón original: `docs/spike-sunat-beta.py.txt` | **Aceptados en BETA** (sección 3): factura `F001-1`/`F001-2`, NC `FN01-1`, ND `FD01-1`, RA `RA-20260928-1`, RC `RC-20260922-1`. `tests.py::FiscalBetaSmokeCommandTest` | • La evidencia es de septiembre/octubre.<br>• Ningún test bloquea globalmente los sockets a `*.sunat.gob.pe`; las guardas son locales (K12 de F1). |
| 4 | Facturas | **IMPLEMENTADO** (BETA) | Emisión: `fiscal_services.py:565` (`get_or_create_fiscal_document`). Firma: `:714`. Envío: `:883` | `tests.py::C22A1DomainTest`, `C22BFiscalApiTest`, `C22BRejectionPolicyTest`; `test_fiscal_double_submit.py` | • No se controla el plazo de 3 días (F5): no hay guarda ni alerta.<br>• No hay venta a crédito. |
| 5 | Boletas y Resumen Diario | **PARCIAL** | La boleta se firma y nunca va por `sendBill` (`fiscal_services.py:899-902`).<br>RC: `fiscal_summary_services.py` (bloques de 500, ticket, `getStatus`); `generate_and_sign_daily_summaries` (`:112`) genera y firma en un solo acto | `tests.py::C22DBoletaTest`, `C22ESummaryTest`, `C22ESummaryConcurrencyTest`, `C22FSummaryTimeoutTest`, `C22GSummaryIdentityTest` | • Sólo `ConditionCode=1` (`fiscal_summary_services.py:191`): ni baja de boleta (3) ni notas de boleta.<br>• No vigila el plazo de 7 días ni hay tarea programada.<br>• El RC se acota por empresa y no por sucursal (H-08).<br>• No hay envío individual de boleta (F4).<br>• No hay pantalla web del RC. |
| 6 | Notas de crédito y débito | **PARCIAL** | NC/ND de factura: `fiscal_note_services.py`, idempotentes por `request_key`, con el motivo comprobado antes de reservar.<br>NC/ND de boleta: fallan cerrado (`fiscal_note_services.py:82-97`) | `tests.py::Fiscal5aNoteBase` y siguientes; `test_fiscal_credit_note_promo.py`; BETA `FN01-1` y `FD01-1` | • La NC es siempre total y no hay tope por original (H-13).<br>• No hay NC parcial por línea.<br>• No hay NC/ND de boleta.<br>• No hay pantalla web de notas: la API existe (`urls.py:488-489`). |
| 7 | Manejo del CDR | **PARCIAL** | Lectura endurecida: `fiscal/cdr.py::parse_cdr`, `fiscal/xmlsafe.py`.<br>Bytes exactos, ZIP y hashes: `fiscal_services.py:126` (`record_cdr_evidence`, 0153).<br>ZIP ilegible guardado como evidencia: `:101`.<br>Vinculación del CDR con el documento en reconciliación, RC y RA: `fiscal_services.py:1128`, `fiscal_summary_services.py:411`, `fiscal_void_services.py:624` | `test_fiscal_prod02.py::CdrEvidenceIsKeptExactlyTest`, `AnUnreadableZipIsEvidenceNotACdrTest`, `UnreadableZipRaceTest`; `tests.py::FiscalCdrParseTest` | • **H-06**: el CDR que devuelve `sendBill` **no se vincula** con el documento (`fiscal_services.py:994-1009`); se aplica tal cual.<br>• Se verifica la integridad de la firma del CDR, no su autenticidad (H-15). |
| 8 | Estados fiscales | **PARCIAL** | `FiscalDocumentStatus` (`models.py:9748`): PENDING, GENERATED, SIGNED, SUBMITTED, ACCEPTED, ACCEPTED_WITH_OBSERVATION, REJECTED, SUBMISSION_ERROR.<br>RC y RA tienen además `SUBMISSION_UNKNOWN` | `tests.py::C22BAcceptanceEvidenceTest` | • La factura incierta queda en `SUBMISSION_ERROR`, y lo incierto se lee del último intento (`fiscal_services.py:81-94`, solución provisional). El estado propio necesita migración.<br>• La boleta informada en un RC aceptado sigue SIGNED, y el PDF dice «pendiente de envío» (R16 de F1). |
| 9 | Rechazos, observaciones y fallos de transporte | **IMPLEMENTADO** | Rangos de códigos: `fiscal/provider.py:396-427`.<br>`_provably_unsent`: `fiscal/provider.py:244`.<br>Un 4000+ sin CDR es `UNKNOWN_RESPONSE`.<br>El rechazo es terminal para el botón genérico: `fiscal_services.py:601-617`.<br>Las observaciones salen con su código: `fiscal_views.py:146` | `test_fiscal_prod02.py::SendBillTransportIsHonestAboutUncertaintyTest`, `NeverSentIsProvenNotGuessedTest`, `FaultsAreReadBeforeTheStatusTest`, `AFaultUnder500SettlesTheDocumentTest` | • Un 0100-1999 persistente no tiene salida guiada (H-05).<br>• No hay flujo explícito de «corregir y reemitir» tras un rechazo. |
| 10 | Reintentos idempotentes | **IMPLEMENTADO** | Un original vivo por venta (restricción `fiscal_document_one_live_per_order`).<br>Intento reservado bajo bloqueo antes de la red: `fiscal_services.py:825`.<br>Un envío incierto no se reenvía (`FiscalSubmissionUncertain`, `:786`).<br>Un abandono a los 10 minutos tampoco libera el reenvío.<br>El reintento usa el mismo XML | `test_fiscal_double_submit.py`; `test_fiscal_prod02.py::UncertainSendIsNeverResentBlindTest`; `tests.py::C22BSubmissionClaimTest`, `C22A1ConcurrencyTest` | — |
| 11 | Archivo seguro de XML y evidencias | **PARCIAL** | Se guardan `signed_xml` y su SHA-256, `cdr_raw`, `cdr_zip` y sus hashes, y los hashes de petición y respuesta por intento.<br>Secretos cifrados con Fernet en la consola (`integrations/secret_store.py`). Nunca se registra el texto de una excepción del adaptador (`fiscal_services.py:944-963`) | `test_fiscal_prod02.py::CdrEvidenceIsKeptExactlyTest`, `TheAdapterExceptionTextNeverReachesTheLogTest` | • No hay política de retención ni copia aparte de la base: la evidencia vive sólo en PostgreSQL, sin WORM.<br>• **No hay acceso del adquirente durante 1 año** (F9). |
| 12 | Reimpresión y descarga | **IMPLEMENTADO** | Rutas `xml/`, `cdr/` y `pdf/` (A4 y ticket de 80 mm): `fiscal_views.py:637-775`.<br>El PDF sale del XML firmado y el QR lleva el `DigestValue` (`fiscal/qr.py:50`).<br>Marca de pruebas fuera de producción (`fiscal_pdf_services.py:112`).<br>Panel web: `frontend/app/admin/components/FiscalDocumentPanel.tsx` | `tests.py::C22BFiscalPdfTest`, `C22BQrTest`; `test_fiscal_print.py`, `test_fiscal_design.py` | • Las descargas no se auditan.<br>• No hay portal del cliente (punto 11). |
| 13 | Consultas de estado (`getStatus`/`getStatusCdr`) | **PARCIAL** | `getStatus` por ticket (RC/RA): `fiscal/provider.py:597`.<br>`getStatusCdr`: `fiscal/provider.py:737` y `fiscal_services.py:1061`.<br>Vista de reconciliación: `fiscal_views.py:552` | `tests.py::FiscalConsultProviderTest`, `FiscalTicketStatusTest`, `C22CReconcileServiceTest`, `C22CReconcileApiTest` | • **H-14**: con la consulta encendida, `FISCAL_CONSULT_ENDPOINT=''` (valor por defecto, `backend/settings.py:362`) llega como URL vacía a `SunatConsultProvider`, que levanta `ValueError` sin capturar (`fiscal_config.py:487`).<br>• No existe consulta en BETA (F1), así que sólo se prueba con mocks: **BLOCKED** para la prueba real.<br>• No hay tarea periódica de reconciliación ni botón en la web. |
| 14 | Numeración independiente de las notas internas (NV-) | **IMPLEMENTADO** | `FiscalSeries` (`models.py:9669`) es una autoridad de numeración aparte de `InternalSequence`.<br>`Order.ReceiptType` sólo tiene boleta y factura (`models.py:558`).<br>Un tipo que no esté en el mapa falla cerrado (`fiscal_services.py:176-179, 584-589`).<br>La nota de venta no cuenta como comprobante (`receipt_coverage.py`) | `test_sales_note_control.py::VoidTouchesNothingElseTest`; `test_note_not_substitute.py:522-540` | — |
| 15 | Configuración fiscal por empresa | **PARCIAL** | Series, documentos, notas y bajas van por `company`, y la serie se resuelve sin ambigüedad (`fiscal_config.py:145`).<br>**Las credenciales y el certificado son de la instalación**: `SCOPE_PLATFORM` (`integrations/providers/sunat.py:49`), y `resolve_credentials(company)` ignora `company` (`fiscal_config.py:257`) | `tests.py::C22A1TenantIsolationTest`, `C22BFiscalTenantIsolationTest`, `C22BSeriesResolverTest` | • **No se comprueba que el RUC de la SOL sea el RUC del emisor**: dos empresas firmarían con la misma credencial.<br>• Series de producción: **CERRADO por C3**, panel MASTER Configuración › Integraciones › Series fiscales (`fiscal_series_views.py`), sin migración.<br>• No hay código de anexo por serie (`AddressTypeCode` fijo en `0000`; necesita migración, fuera de C3). |
| 16 | Panel de diagnóstico | **PARCIAL** | Botón «Probar» de la consola SUNAT, sin red: vigencia del certificado y ambiente (`integrations/providers/sunat.py:83-104`) | `test_integrations_sunat.py` | • No hay `fiscal_preflight` (FP-1).<br>• No hay sección fiscal en el estado operativo: plazos de 3 y 7 días, inciertos, intentos abiertos, certificado por vencer. |
| 17 | Registro de auditoría | **PARCIAL** | Cada acción exitosa deja `AdminAuditLog` con la empresa: `fiscal_views.py:290,327,537,598` y las vistas de RC y RA. Las negativas 503/409 también (`fiscal_views.py:59-89`) | `test_fiscal_prod02.py::RefusalsAreAuditedTest` | • No se auditan las negativas 400 ni las descargas de XML, CDR y PDF.<br>• El humo BETA no audita. |

**Resumen:**
- 8 IMPLEMENTADO: 1, 2, 3, 4, 9, 10, 12 y 14;
- 9 PARCIAL: 5, 6, 7, 8, 11, 13, 15, 16 y 17;
- 0 PENDIENTE en su totalidad.

La activación en producción está **BLOCKED** por datos del propietario (sección 6), no por un punto concreto.

---

## 3. Evidencia BETA existente

| Documento | Resultado | Fuente en el repositorio |
|---|---|---|
| Factura `F001-1` | aceptada, «La Factura numero F001-1, ha sido aceptada», CDR `216bf0eb…` | `docs/entrega-fiscal-5a-notas.md:283` |
| NC `FN01-1` sobre `F001-1` (motivo 01) | aceptada, CDR `b55dd752…` | `docs/entrega-fiscal-5a-notas.md:217,284` |
| ND `FD01-1` sobre `F001-2` (motivo 02) | aceptada, CDR `235d69f2…` | `docs/entrega-fiscal-5a-notas.md:218` |
| Comunicación de Baja `RA-20260928-1` | aceptada, CDR `49a11c28…` | `docs/entrega-fiscal-5b-baja.md:523` |
| Resumen Diario `RC-20260922-1` | aceptado | `docs/adr-fiscal-c22a1.md:577` |

**Qué no prueba BETA:**
- BETA no exige un certificado registrado ni valida la identidad del firmante (Manual §2.3);
- no tiene `billConsultService` (F1);
- no aplica efectos fiscales.

Por eso «aceptado en BETA» no equivale a «listo para producción».

---

## 4. Brechas y riesgos priorizados

| ID | Prioridad | Brecha | ¿Necesita credenciales reales? | ¿Necesita migración? | Plan |
|---|---|---|---|---|---|
| G1 | P1 | **H-06**: el CDR de `sendBill` se aplica sin comprobar que corresponde al documento (RUC, serie-número y tipo) | No | No | #458 |
| G2 | P1 | **H-14**: un endpoint de consulta vacío anula la constante de producción, y la excepción no se captura | No | No | #458 |
| G3 | P1 | **Mezcla de credenciales entre empresas.**<br>• No se comprueba RUC de la SOL == RUC del emisor.<br>• Tampoco certificado vigente al firmar ni RUC en el OU (F8). | No | No | #461 |
| G4 | P2 | Sin diagnóstico GO/NO-GO de sólo lectura por empresa (FP-1) | No | No | #459 |
| G5 | P2 | La factura incierta no tiene estado propio (`SUBMISSION_UNKNOWN`) | No | **Sí**: opción nueva en `FiscalDocumentStatus` | Pedir número de migración antes de escribirla |
| G6 | P2 | Credenciales y certificado **por empresa** (consola `SCOPE_COMPANY`, FP-2) y un ambiente por empresa | No para escribirla; sí para activarla | Probable (`CompanyFiscalProfile`) | Fase propia, con ADR |
| G7 | P2 | Plazos y alertas: factura a +3 días, RC a +7, certificado por vencer | No | No | Parte en C (lectura); el bloqueo, aparte |
| G8 | P2 | Reglas de validación del 26.08.2026 y normativa del 1-ene-2027 | No, pero hay que **descargar el xlsx a mano** | No | **BLOCKED** hasta tener el xlsx: `cpe.sunat.gob.pe` devuelve 403 a lecturas automatizadas |
| G9 | P2 | NC parcial, tope de NC, NC/ND de boleta, baja de boleta, RC por sucursal (H-08), recuperación de `SUBMISSION_UNKNOWN` en RC y RA (H-09) | No | Posible | FISCAL-PROD-08 |
| G10 | P2 | Afectación IGV por producto (FP-5); exonerado e inafecto | No; decisión del contador | **Sí** | Bloqueado por el contador |
| G11 | P3 | Acceso del adquirente durante 1 año (F9); política de retención y copia de la evidencia | No | Posible | Decisión del propietario |
| G12 | P3 | Guarda global de red en los tests (K12) | No | No | Pequeño PR futuro |
| G13 | P3 | Auditar descargas y negativas 400 | No | No | Pequeño PR futuro |
| G14 | — | Primera emisión real, endpoint de producción en la tabla del código | **Sí** | — | **BLOCKED**: GO del propietario |

**Riesgos que siguen abiertos:**
- **Duplicado en SUNAT.** Ya está mitigado. Lo que queda es que, en producción, sin consulta encendida no hay salida para un envío incierto. Por eso la consulta se enciende **junto** con la emisión.
- **Una empresa firma con las credenciales de otra.** Sigue abierto hasta G3 y G6.
- **Certificado vencido sin aviso.** Sigue abierto hasta G3 y G4.
- **Comprobante fuera de plazo** (3 y 7 días). Sigue abierto hasta G7.
- **Reglas de 2025 frente a las de 2026.** El rechazo se descubre en producción y gasta el número (F3). Abierto hasta G8.
- **Nota de venta interna convertida en comprobante.** Cerrado (punto 14).

---

## 5. Implementación propuesta sin credenciales reales (máximo 3 PR, en este orden)

1. **A — SUNAT-CDR-BIND** (G1 y G2), PR #458:
   - el CDR de `sendBill` se vincula con el documento mediante `cdr_matches_document`;
   - si no coincide, **no** se aplica ningún estado terminal, el intento queda incierto (sólo se reconcilia) y se audita;
   - un `FISCAL_CONSULT_ENDPOINT` vacío usa la constante del código, y uno inválido falla cerrado con 503.
2. **B — SUNAT-ISSUER-GATE** (G3), PR #461. Antes de reservar un correlativo, y otra vez antes de enviar:
   - RUC de la SOL == RUC de la empresa emisora;
   - certificado vigente;
   - en producción, además, RUC del emisor en el OU del certificado.

   Falla cerrado con 503, sin gastar números, y se audita.
3. **C — SUNAT-PREFLIGHT** (G4 y parte de G7), PR #459: `manage.py fiscal_preflight --company <slug>`. Es de sólo lectura: no escribe, no usa la red y no imprime secretos. Revisa:
   - el interruptor y el ambiente;
   - el RUC;
   - las credenciales «configuradas» (nunca su valor);
   - la vigencia del certificado y su vencimiento a 30 y 60 días;
   - el RUC en el OU;
   - las series;
   - la cola pendiente: inciertos, intentos abiertos, facturas firmadas sin enviar a más de 2 días, boletas sin RC a más de 5 días;
   - la consulta.

Ninguno necesita migración. G5, G6, G9 y G10 sí la necesitan o pueden necesitarla: se piden por separado.

---

## 6. Lista de activación en producción (por empresa)

La lista mezcla tres clases de puntos:
- **datos o actos** del propietario, del contador o de SUNAT, que ningún PR puede cerrar;
- **prerrequisitos de código** (filas C1–C3, 7 y 11), que se cierran con PR revisados y fusionados, pero que por sí solos
  **no activan nada**;
- **operación** (filas 13 y 15).

La activación exige al final un GO escrito y separado, con este texto exacto:

> **«GO activación SUNAT producción &lt;empresa&gt;»**

El PR de la fila C3 **no activa nada**: con él desplegado, producción sigue apagada
(`FISCAL_PRODUCTION_ENABLED=False` y la consola en BETA). Se fusiona y despliega con el GO de despliegue de
su release, como cualquier otro; la activación es la secuencia de la sección 6.1, con sus GO propios.

**Prerrequisitos de código**

| # | Requisito | Quién | Cómo se verifica |
|---|---|---|---|
| C1 | **#458** (CDR de `sendBill` vinculado; endpoint de consulta), **#461** (RUC de la SOL = RUC de la empresa; certificado vigente; RUC en OU en producción) y **#459** (`fiscal_preflight`), fusionados | Código, con revisión independiente | PR fusionados en `master` |
| C2 | Esos tres cambios **desplegados en el SHA exacto que se va a activar**, el mismo SHA de la evidencia BETA (fila 9) | Operación, con GO de despliegue propio | SHA en producción = SHA ensayado; `fiscal_preflight --target production` sin NO-GO salvo la línea del ambiente |
| C3 | **IMPLEMENTADO en PR propio (C3a), sin fusionar**: endpoints de producción como constantes; modo producción en la consola SUNAT (texto `PRODUCCION <RUC>`); interruptor `FISCAL_PRODUCTION_ENABLED` (apagado); `resolve_environment(company)` con fallo cerrado; series BETA/producción separadas y panel MASTER de series; certificado no autofirmado al firmar en producción; auditoría de cada cambio de modo y de cada negativa. **Sin migración.** C3b (envío programado y RC, fila 15) va en otro PR | Código; revisión independiente; se despliega con el GO de su release | `test_sunat_production.py`, `test_fiscal_series_panel.py`; sección 6.1 |

**Datos, actos y operación**

| # | Requisito | Quién | Cómo se verifica |
|---|---|---|---|
| 1 | **RUC y datos legales**: RUC, razón social exacta, nombre comercial, domicilio fiscal; establecimientos anexos con su código de 4 dígitos | Propietario | Ficha RUC en SOL; `Company.tax_id`/`legal_name`; `fiscal_preflight` |
| 2 | **Situación del contribuyente**: RUC activo y habido, régimen general o tercera categoría; fecha de designación como emisor electrónico (F10) | Propietario | Consulta RUC; SOL |
| 3 | **Modalidad de emisión**: SEE-Del contribuyente (Modalidad I, lo que implementa el código), PSE u OSE. Otra modalidad cambia el adaptador | Propietario y contador | Decisión escrita |
| 4 | **Alta como emisor en SOL** e indicación del correo (RS 287-2017) | Propietario | Constancia de SOL |
| 5 | **Usuario SOL secundario** con perfil de envío (F8), sólo para la emisión | Propietario | SOL; se carga en la consola, cifrado |
| 6 | **Certificado digital válido**: X.509 v3 con el RUC en OU, vigente, registrado en SOL. Puede ser el CDT gratuito de SUNAT si la empresa califica (no confirmado) | Propietario | Botón «Probar» y `fiscal_preflight`; constancia de registro en SOL |
| 7 | **Credenciales y certificado POR EMPRESA** (G6): ninguna empresa usa los de otra | Código, antes de activar | `fiscal_preflight` comprueba que el RUC de la SOL sea el del emisor (#459, #461) |
| 8 | **Series de producción**: filas nuevas `environment=production`, nombres decididos por el propietario, una activa por tipo, sucursal y ambiente; `next_number` empieza en 1 | Propietario y código | `fiscal_preflight` |
| 9 | **Evidencia BETA reciente** sobre el `master` que se va a activar: factura, NC, ND, RC y RA aceptados, con sus CDR archivados | Operador, con el GO del propietario para tocar BETA | `fiscal_beta_smoke` + acta |
| 10 | **Validación contable**: afectación IGV del catálogo (G10), NC parciales, huecos de numeración, política para factura a +3 días y RC a +7 días | Contador | Acta del contador |
| 11 | **Reglas de validación vigentes** (26.08.2026) releídas y cambios del 1-ene-2027 aplicados (G8) | Código, con el xlsx descargado a mano | PR propio |
| 12 | **Flujo de contingencia**: qué se hace si SUNAT no responde (comprobantes de contingencia, plazos), quién reconcilia y cada cuánto; consulta `getStatusCdr` encendida **junto** con la emisión | Propietario y código | Procedimiento escrito; `FISCAL_CONSULT_ENABLED` decidido |
| 13 | **Copias de seguridad**: base con la evidencia fiscal (XML, CDR, ZIP), `APP_CONFIG_ENCRYPTION_KEY` custodiada, restauración ensayada; retención y acceso del adquirente (F9, G11) | Operación | Ensayo de restauración; `fiscal_preflight` |
| 14 | **Primera emisión real controlada**: qué venta, qué día, quién autoriza, y seguimiento de 7 días | Propietario | **GO «GO activación SUNAT producción &lt;empresa&gt;»** |
| 15 | **Envío programado y alertas (G7)**: tarea programada que genera y envía el Resumen Diario (o lo deja listo para que una persona confirme, según decida el propietario); alertas de factura o nota sin enviar a +2/+3 días y de boleta sin RC a +5/+7 días; reconciliación periódica de inciertos. Hoy no existe ninguna tarea fiscal en el cron | Operación y código | Línea de cron revisada; `fiscal_preflight` (o `ops_status`) en el cron con aviso al responsable |

### 6.1 Runbook de activación (C3)

Cada paso tiene su GO, y ninguno implica el siguiente. Un PR fusionado no es un GO.

**Paso 0 — release (operador, con GO de despliegue).** Desplegar el SHA que contiene C3 con el GO de despliegue
de esa release, por el procedimiento habitual (`prod_lock.sh`, un solo ejecutor). Con él desplegado no cambia
nada: `FISCAL_PRODUCTION_ENABLED` no existe en `deploy/.env.production` y vale `False`.

**Paso 1 — datos del propietario fuera del sistema** (filas 1–6, 10, 12): alta como emisor electrónico en SOL,
usuario SOL secundario con perfil de envío, certificado registrado en SOL, decisión de modalidad (SEE-Del
contribuyente), acta del contador y procedimiento de contingencia. **Nada de esto lo comprueba el sistema**:
lo atestigua el propietario.

**Paso 2 — series de producción (propietario, MASTER).** Configuración › Integraciones › **Series fiscales**:
elegir la empresa y crear, con ambiente **PRODUCCIÓN**, al menos la serie de factura (`F…`), y las de boleta
(`B…`) y notas que se vayan a usar; `Primer número` = 1. Son filas nuevas: las de BETA no se tocan ni se
reetiquetan. Hoy (2026-11-27) `black-dog-store` no tiene ninguna serie, ni BETA ni de producción.

**Paso 3 — evidencia BETA** (fila 9) sobre el SHA desplegado, con el GO del propietario para tocar BETA.

**Paso 4 — GO escrito del propietario:** **«GO activación SUNAT producción black-dog-store»**.

**Paso 5 — interruptor de instalación (operador, bajo su propio GO, posterior al del paso 4).** En
`deploy/.env.production`:

```
FISCAL_PRODUCTION_ENABLED=1
FISCAL_CONSULT_ENABLED=1
```

Reiniciar el backend por el procedimiento habitual. `python3 deploy/preflight.py` lo reporta (y marca
ATENCIÓN si falta la consulta). Comprobar después, en el servidor:

```
python manage.py fiscal_preflight --company black-dog-store --target production
```

Debe salir **Veredicto: GO**. La línea «la consola sigue en BETA» es un AVISO esperado: el modo es el paso 6.

**Paso 6 — modo producción en la consola (propietario, MASTER).** Configuración › Integraciones › SUNAT:
editar, elegir **Entorno de SUNAT = PRODUCCIÓN**, guardar, **Probar** (debe decir «Entorno PRODUCCIÓN… en GO») y
**Activar** escribiendo exactamente `PRODUCCION <RUC de la empresa>`. El servidor vuelve a pasar el preflight
con las credenciales que se activan y se niega, con el motivo, si algo no está en GO. Queda en la bitácora
como `integration_mode_changed` (de `test` a `production`) e `integration_activated`.

**Paso 7 — primera emisión real controlada** (fila 14) y seguimiento de 7 días.

**Volver atrás.** Desactivar la integración SUNAT en la consola detiene la emisión al momento (también
`FISCAL_PRODUCTION_ENABLED=0`: producción falla cerrada, nunca vuelve a BETA en silencio). Volver la consola a
BETA se **niega** si ya existe algún comprobante de producción: sus envíos, notas y bajas pendientes quedarían
sin ambiente.

**Lo que C3 no cubre (C3b y otros PR):** envío programado de facturas pendientes y del Resumen Diario con sus
alertas (fila 15); credenciales por empresa (G6: hoy la Clave SOL es de la instalación y sólo emite la empresa
de su RUC); código de anexo por serie (`AddressTypeCode` fijo en `0000`, `fiscal/builder.py`), que necesita
migración.

---

## 7. Qué queda BLOCKED y por qué

- **Pruebas reales de `getStatusCdr`**: BETA no tiene servicio de consulta (F1), y producción está prohibida en este trabajo.
- **Relectura de las reglas 26.08.2026 y del XSL 17/11/2026**: `cpe.sunat.gob.pe` devuelve 403 a lecturas automatizadas, y los archivos hay que bajarlos a mano.
- **Aceptación de SHA-256 en producción y validación del certificado real**: necesita un certificado registrado y la primera emisión real.
- **Toda la activación**: datos y GO del propietario (sección 6).
