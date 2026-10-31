# FISCAL-PROD-01 · F1 — Auditoría de conformidad y contrato productivo (BETA → producción)

Estado: **BLOQUEADO POR DECISIÓN** (dueño, contador y alta en SUNAT; sección 6). **Nada se activó**: `FISCAL_ENABLED`, el ambiente
BETA, las credenciales, los certificados y las series no se tocaron, y no se envió ni se emitió ningún comprobante. Este documento es
una auditoría de sólo lectura y la propuesta de contrato; no es asesoría tributaria. Lo que dependa de SUNAT o del contador está
marcado y no se da por cierto.

Relación con el ADR abierto: el PR #173 (`docs/adr-fiscal-prod.md`) está **parcialmente desactualizado** (sección 8). Este
documento no lo sustituye: lo corrige y lo actualiza con el estado de `master` `6c63235`.

Alcance: F1 (auditoría) y el contrato de F2 (secciones 5 y 7). F3 (activación) exige las diez condiciones del propietario
(sección 6) y un GO escrito específico para el primer comprobante real; **ningún automatismo ni PR de este trabajo puede
producirlo**.

---


Fecha: 2026-10-31. Código leído: `master` `6c63235`, sólo lectura. ADR comparado: PR #173 (`docs/adr-fiscal-prod.md`, escrito sobre `573bba8`, 2026-10-25).
No se editó el repo, no se llamó a SUNAT ni a producción, no se tocó ningún secreto.
Rutas relativas a `backend/store/`. Formato `archivo:línea` o `archivo::símbolo`.

## 0. Veredicto

La integración está **madura para BETA** y bien blindada contra los errores caros (doble envío, respuesta incierta, "aceptado" sin CDR, secretos en logs, PDF distinto del XML). **No puede emitir en producción**: el código lo prohíbe a propósito (`fiscal_config.py:29`, `:109`, `:422`) y faltan seis cosas de código y cuatro de dueño/contador (secciones 4 y 6). Ningún hallazgo obliga a tocar nada hoy.

El ADR #173 está **parcialmente desactualizado**: H-01, H-02, H-03, H-04, H-07 (en parte) y H-11 ya están corregidos en master (FISCAL-PROD-02, ADR-44). Siguen vigentes H-06, H-08, H-09, H-10, H-12, H-13, H-14 y las fases 04, 05, 06, 08, 09, 10.

## 1. Fuentes oficiales: qué se leyó de verdad y qué no

Leído (texto completo extraído de PDF o página):
- RS 000143-2026/SUNAT (29-jul-2026), https://www.sunat.gob.pe/legislacion/superin/2026/000143-2026.pdf: posterga la RS 000048-2026 del 1-ago-2026 al **1-ene-2027** (designaciones del 1-nov-2026 al 1-abr-2027). Confirma la corrección N13 del ADR; `docs/sunat-cpe-requisitos.md` (que da por vigente el 1.8.2026) y el comentario de `fiscal/rules.py` están desactualizados.
- RS 000048-2026/SUNAT y su anexo, https://www.sunat.gob.pe/legislacion/superin/2026/000048-2026.pdf y `.../anexo-000048-2026.pdf`: art. 14 modificado (baja de factura/notas y de boleta: 7 días desde el día siguiente a la **generación**, ya no desde la CDR aceptada); Anexo 6 numerales 6.1.2 (WS-Security UsernameToken con usuario y Clave SOL) y 6.1.3 (nombre `RUC-TT-SERIE-CORRELATIVO.ZIP`, serie F/B + 3, correlativo 1 a 8 dígitos).
- RS 114-2019/SUNAT, https://www.sunat.gob.pe/legislacion/superin/2019/114-2019.pdf: art. 7.3 y 12 (boleta: resumen diario, o envío individual hasta 5 días, y vencido el plazo sólo resumen), art. 13 (CDR), art. 14 (baja), art. 21.1.b (resumen "el día de emisión o a más tardar hasta el sétimo día calendario siguiente"), art. 15 (otorgamiento). El considerando dice que el envío individual de boleta "se permite en el SEE-SFS y en el SEE-OSE" y el resumen diario "en el SEE-Del contribuyente y en el SEE-OSE".
- RS 000003-2023/SUNAT, https://www.sunat.gob.pe/legislacion/superin/2023/000003-2023.pdf: factura y nota vinculada, hasta **3 días calendario** desde el día siguiente a la emisión.
- RS 287-2017/SUNAT, https://www.sunat.gob.pe/legislacion/superin/2017/287-2017.pdf: art. 6 sustituido. Se elimina la solicitud de autorización y la homologación. Ser emisor = indicarlo en SOL + registrar en SOL al menos un certificado digital vigente + correo (Modalidad I, sistemas propios) o autorizar a un PSE (Modalidad II). Requisitos de RUC: no "no habido", no suspendido ni de baja, afecto a renta de tercera.
- Manual del programador SEE-Sistemas del Contribuyente (mayo 2021), https://cpe.sunat.gob.pe/sites/default/files/inline-files/manual_programador%20(1).pdf (PDF descargable sí; las páginas HTML de cpe.sunat.gob.pe devuelven 403): endpoints (producción `e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService`, beta `e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService`, consulta `e-factura.sunat.gob.pe/ol-it-wsconscpegem/billConsultService`), `sendBill` síncrono sólo para facturas y notas, asíncrono para resumen y baja, `getStatusCdr` sólo tipos 01/07/08, §3.1 certificado (X.509 v3, RUC del titular, **comunicado previamente a SUNAT** en SOL, vigente y no revocado, SUNAT valida ambos), §2.2 clave SOL secundaria con perfil de envío, §2.3 BETA sin certificado registrado, §4.1 errores (0100-0999 y 1000-1999 excepción, 2000-3999 rechazo con **numeración ya utilizada**, 4000+ observación).
- Guías XML de Factura y Boleta UBL 2.1 (v1.0, mayo 2017), https://cpe.sunat.gob.pe/sites/default/files/inline-files/guia%2Bxml%2Bfactura%2Bversion%202-1%2B1%2B0%20%282%29_0%20%282%29.pdf y la de boleta equivalente: `AddressTypeCode` = código de 4 dígitos del establecimiento anexo; "0000" para el domicilio fiscal o si no se puede determinar el lugar de venta. El ubigeo (Catálogo 13) aparece sólo en campos de traslado, no en el emisor.

Leído sólo indirectamente (resumen de WebSearch/WebFetch, no el HTML original):
- https://orientacion.sunat.gob.pe/04-resumen-diario-boleta-de-venta-electronica (resumen: día de emisión o 7 días; CDR aceptada/rechazada) y https://orientacion.sunat.gob.pe/3529-operatividad ("Los archivos electrónicos [de la boleta] no son enviados a la SUNAT, estos son conservados por el emisor"; resumen rechazado = se genera uno nuevo; ese texto mezcla 3 y 7 días para facturas, es inconsistente).
- Certificado Digital Tributario gratuito (SUNAT; sólo para firmar CPE; facilidad autorizada hasta 31-dic-2027; requisitos como no ser OSE/PSE y estar en tercera): resumen de búsqueda sobre https://cpe.sunat.gob.pe/certificado-digital. Requisitos vigentes **no confirmados**.
- Requisitos de SEE-Del contribuyente en https://cpe.sunat.gob.pe/sistema_emision/see_contribuyente: sólo resumen de búsqueda. El ADR #173 cita una copia Wayback; Wayback está bloqueado en este entorno.

**No se pudo leer** (403 o inaccesible): `cpe.sunat.gob.pe/tipos_de_comprobantes/boleta`, `.../informacion_general/certificados_digitales`, `.../guias-y-manuales`, el xlsx de Reglas de validación (versiones 21.04.2025 y la que el ADR llama 24.04.2026), los catálogos vigentes, la tabla de códigos de error (parámetro 742), la normativa ROPS/INDECOPI y cualquier página de un OSE. Tampoco se comprobó nada contra el servidor de producción de SUNAT (prohibido).

Nada de lo anterior se infiere de que BETA haya aceptado una factura: BETA no exige certificado registrado ni valida la identidad del firmante (Manual §2.3).

## 2. Requisitos vs estado actual

Estados: IMPLEMENTADO · PARCIAL · FALTA · NO VERIFICADO.

| # | Requisito (fuente) | Estado | Evidencia / brecha |
|---|---|---|---|
| R1 | Calidad de emisor: indicarlo en SOL y registrar certificado + correo, o PSE (RS 287-2017 art. 6) | FALTA (acto del dueño) | No hay rastro ni atestación en el sistema; el ADR propone atestación en FP-2. |
| R2 | Certificado vigente, no revocado, RUC del titular, comunicado a SUNAT (Manual §3.1) | PARCIAL | `fiscal/certificate.py::check_signing_pair` sólo comprueba que par cert/clave case. `validity()` (`certificate.py:54`) se usa sólo en el botón de prueba (`integrations/providers/sunat.py:85`), no al firmar. No se compara el RUC del sujeto con el emisor. No hay revocación (no verificable localmente). |
| R3 | Credenciales SOL: usuario secundario, `RUC+usuario` en Username (Manual §2.2; Anexo 6 6.1.2) | PARCIAL | `fiscal/provider.py::_SunatSoapClient.__init__` forma `ruc+sol_user`. La clave es de **la instalación** (`SCOPE_PLATFORM`, `integrations/providers/sunat.py:49`; `resolve_credentials(company)` ignora `company`, `fiscal_config.py:257`). No se verifica que el RUC de la SOL sea el RUC del emisor del documento (sin comparación en `fiscal_views.py`, `fiscal_services.py`). |
| R4 | Ambiente separado de producción y decidido por servidor | PARCIAL | `ONLY_SUPPORTED_ENVIRONMENT = BETA` (`fiscal_config.py:29`), `resolve_environment` falla cerrado (`:109`), tabla de endpoints sólo BETA (`:422-438`, `provider.py:70`). El ambiente es de la instalación, no de la empresa. |
| R5 | Endpoint de producción `billService` | FALTA | No declarado a propósito (`provider.py:66-70`). Conocido por Manual §2.1, sin usarse. |
| R6 | Series: 4 caracteres, F/B + 3, correlativo 1 a 8 dígitos (Anexo 6 6.1.3; Anexo 1 campo 8) | IMPLEMENTADO | `models.py::FiscalSeries` (`UniqueConstraint company+tipo+serie+ambiente`), `fiscal/rules.py::validate`, `fiscal_config.py::resolve_series` (`:145`) determinista y falla ante ambigüedad. |
| R7 | Series de producción: alta y política | FALTA | No hay superficie para crearlas (sólo `seed_demo_users --fiscal-beta` y admin Django, apagado en prod). Sin código de establecimiento anexo por serie. |
| R8 | Correlativo no reciclado; rechazo gasta número (Manual §4.1) | IMPLEMENTADO | `fiscal_services.py::_reserve` (`:565`) bajo `select_for_update`; reserva dentro de la transacción de validación (si la regla falla, el número vuelve). Rechazo es terminal para el botón genérico (`:631`). |
| R9 | UBL 2.1 válido contra XSD oficial y reglas | IMPLEMENTADO (sólo gravado 10) | `fiscal/schema.py::validate_invoice` antes de red (`fiscal_services.py:775`), `fiscal/builder.py`, `fiscal/rules.py`. Exonerado/inafecto/gratuito fallan cerrado. Sin ubigeo (el guía 2017 no lo pide al emisor: NO VERIFICADO para reglas vigentes). |
| R10 | `AddressTypeCode` = código de establecimiento anexo | PARCIAL | Fijo `0000` (`builder.py:198`). Correcto para domicilio fiscal; incorrecto si se vende desde un anexo declarado. |
| R11 | Forma de pago `FormaPago` | PARCIAL | Siempre `Contado` (`builder.py:73-75,336`). Venta a crédito no soportada y no hay guarda explícita. |
| R12 | Firma XMLDSig C14N 1.0, enveloped, en `ext:ExtensionContent` | IMPLEMENTADO | `fiscal/signing.py` (`SUNAT_PROFILE`, RSA-SHA256). Aceptación de SHA-256 en **producción** NO VERIFICADO (el Anexo 6 no fija algoritmo; BETA lo aceptó). |
| R13 | Nombre `RUC-TT-SERIE-NUM.ZIP` | IMPLEMENTADO | `fiscal/packaging.py::document_name`, usado en `fiscal_services.py::submit_fiscal_document` (`:944`). |
| R14 | Factura: `sendBill` síncrono, CDR, plazo 3 días (RS 003-2023) | PARCIAL | Envío y CDR sí (`provider.py:494-` `submit_invoice`, `_read_cdr` `:429`). **No hay control de plazo de 3 días** en `submit_fiscal_document` ni alerta. SUNAT: lo enviado fuera de plazo "no tendrá la calidad de factura electrónica" (RS 114-2019 art. 12). |
| R15 | Boleta: resumen diario (SEE-Del contribuyente) en 7 días | PARCIAL | Boleta no se envía por `sendBill` (`fiscal_services.py:960`), coherente con Manual §2.4. RC con bloques de 500, ticket, `getStatus` 0/98/99 (`fiscal_summary_services.py`). Sólo `ConditionCode=1` (`:191`). **Sin vigilancia del plazo de 7 días, sin tarea programada** (no hay cron fiscal: ver `management/commands/`). Resúmenes por empresa, no por sucursal (H-08). |
| R16 | Estado de boleta tras RC aceptado | PARCIAL | ADR-26: la boleta sigue `SIGNED`; el PDF dice "Emitida, pendiente de envío a SUNAT" (`fiscal_pdf_services.py:86`) aunque su RC esté aceptado. No miente en el sentido peligroso, pero confunde. |
| R17 | Nota de crédito/débito de factura | PARCIAL | `fiscal_note_services.py`; sólo total en NC (motivos parciales aceptados pero emitidos como reversión total), sin tope acumulado (H-13). NC/ND de boleta: fallan cerrado (`:77-89`). |
| R18 | Comunicación de baja | PARCIAL | `fiscal_void_services.py` (plazo 7 días desde `cdr_accepted_at`, `VOID_PLAZO_DAYS=7`, otorgamiento atestiguado). Desde 1-ene-2027 el plazo cuenta desde la **generación** (RS 000048-2026 art. 14): el código usa la regla vieja. Baja de boleta: no soportada. |
| R19 | CDR: "Aceptado" sólo con constancia | IMPLEMENTADO | `provider.py::_classify` (`:396`), 4000+ sin CDR es `UNKNOWN_RESPONSE`; PDF imprime "Aceptada" sólo con estado aceptado (`fiscal_pdf_services.py:86`). |
| R20 | Conservar CDR/XML/ZIP exactos | IMPLEMENTADO | `FiscalDocument.cdr_raw/cdr_zip` + hashes, `record_cdr_evidence`, `keep_unreadable_zip` (FISCAL-PROD-02 H-02). Filas anteriores a 0153 conservan sólo texto. |
| R21 | CDR corresponde al documento | PARCIAL | Reconciliación, RC y RA llaman `cdr_matches_document` (`fiscal_services.py:1189`); la respuesta directa de `sendBill` **no** (H-06). |
| R22 | Autenticidad de la firma del CDR | FALTA (declarado) | `fiscal/cdr_signature.py` verifica integridad, nunca "trusted"; no se usa para decidir estado (sólo tests). Sin ancla de confianza oficial. |
| R23 | Respuesta incierta: no reenviar a ciegas | IMPLEMENTADO | `provider.py::_provably_unsent` (`:244`), `TRANSPORT_UNKNOWN`, `_claim_attempt` (`fiscal_services.py:886`), `FiscalSubmissionUncertain`, reconciliación por `getStatusCdr`. Estado propio `SUBMISSION_UNKNOWN` existe sólo para RC y RA; la factura usa `SUBMISSION_ERROR` + lectura del último intento (provisional, `fiscal_services.py:77`). |
| R24 | Reconciliación real habilitable | PARCIAL | `resolve_consult_provider` (`fiscal_config.py:467`) y `getStatusCdr` OK. **Bug H-14 vigente**: `FISCAL_CONSULT_ENDPOINT` default `''` (`settings.py:325`) anula la constante de producción (`fiscal_config.py:487`): al encender la consulta, `SunatConsultProvider('')` lanza `ValueError` ("debe ser HTTPS") sin captura en la vista. Boletas no se pueden consultar por `getStatusCdr` (Manual: sólo 01/07/08; sólo ticket de RC). |
| R25 | Secretos cifrados y sin lectura | IMPLEMENTADO | Consola de integraciones: Fernet con `APP_CONFIG_ENCRYPTION_KEY`, sellado por proveedor/empresa/slot, sin API de lectura; P12 en memoria (`fiscal/certificate.py`); `safe_message` saneado; el adaptador registra sólo el tipo de excepción (`fiscal_services.py` ~`:1010`). |
| R26 | Multiempresa en datos fiscales | PARCIAL | Documentos, series, notas y bajas por `company`; vistas resuelven por `visible_orders` y 404 uniforme (`fiscal_views.py::_fiscal_order`). Credenciales/certificado: uno por instalación. |
| R27 | PDF = XML firmado, QR con DigestValue | IMPLEMENTADO | `fiscal_pdf_services.py::build_fiscal_context` lee `signed_xml`; `fiscal/qr.py` 10 campos, DigestValue leído del XML. Marca de pruebas si ambiente ≠ producción (`:199`). |
| R28 | Fuente económica = snapshot | PARCIAL | Importes: snapshot de la venta (`_order_to_invoice_data`). Emisor: `order_identity` usa `company_snapshot`. Pero la **descripción de línea sale de `Product.name` vivo** (`fiscal_services.py:375`; `OrderItem` no guarda nombre) y `sign_fiscal_document` (`:775`) reconstruye desde Order vivo sin comparar contra los importes ya congelados en `FiscalDocument`. |
| R29 | Interruptor antes de reservar correlativo | IMPLEMENTADO | `fiscal_views.py::issuing_credentials` (`:59`), caja (`pos_services.py:1436`). |
| R30 | Pago de prueba no recibe comprobante real | IMPLEMENTADO | `fiscal_services.py::_refuse_rehearsal_payment` (`:605`) (H-11). |
| R31 | Alertas de plazo, de inciertos y de certificado por vencer | FALTA | Sin sección fiscal en `ops_status`, sin comando de reconciliación periódica. |
| R32 | Pruebas sin red real | PARCIAL | CI fija `FISCAL_ENABLED=0` (`.github/workflows/backend-postgres-validation.yml:91`); pruebas inyectan proveedores; el humo BETA es comando opt-in. **No hay guarda global** que bloquee sockets a hosts SUNAT (sólo guardas locales en `test_fiscal_prod02.py`). |
| R33 | Afectación IGV por producto | FALTA | Sin campo; un tratamiento por venta; sólo 10 se emite. |
| R34 | Validación del RUC/estado del adquirente (activo/habido) | NO VERIFICADO | `rules.check_ruc` sólo formato, a propósito. Regla vigente de SUNAT no leída (xlsx inaccesible). |
| R35 | Consulta web al adquirente por ≥1 año, conservación (Anexo/RS 117-2017 en `sunat-cpe-requisitos.md` P2/P3) | NO VERIFICADO | El código guarda evidencia; la consulta pública del adquirente no existe en lo auditado. |

## 3. BETA vs producción

| Aspecto | BETA (hoy) | Producción (necesario) |
|---|---|---|
| Endpoint envío | `e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService` | `e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService` (Manual §2.1); no declarado en código |
| Consulta CDR | No existe (Manual) | `billConsultService` en `e-factura.sunat.gob.pe/ol-it-wsconscpegem/` (Manual §2.8). El Anexo 2 del mismo manual lista una URL `www.sunat.gob.pe/...` para el servicio de consulta antiguo: discrepancia interna del manual, hay que confirmar con SUNAT/WSDL antes de fijar |
| Autenticación | `RUCMODDATOS` + clave pública de pruebas; SOL real irrelevante | `RUC+usuario SOL secundario` + clave SOL real, perfil de envío (Manual §2.2). Nombre del perfil actual NO VERIFICADO (el manual 2021 cita "Envío de documentos electrónicos-Grandes emisores") |
| Certificado | Cualquiera, incluso autofirmado; sin registro | Emitido por prestador acreditado (ROPS/INDECOPI) o CDT gratuito de SUNAT si califica; con RUC; **registrado en SOL**; vigente y no revocado (SUNAT valida ambos) |
| Alta del emisor | Ninguna | SOL: indicar emisor + registrar certificado + correo (RS 287-2017); RUC activo/habido, renta de 3.ª |
| Series | `F001`/`B001` de prueba; libres | Series de la empresa, filas nuevas `environment=production`; nombres decididos por el dueño; si hay anexo, código de anexo en `AddressTypeCode` |
| Efecto fiscal | Ninguno; numeración desechable | Real: rechazo gasta número, aceptación es declaración tributaria |
| Retención de CDR | Convención interna | Obligación de conservar CPE, resúmenes, bajas y constancias de rechazo (P3 de `sunat-cpe-requisitos.md`); definir período y respaldo (NO VERIFICADO el plazo legal exacto) |
| Consulta de reconciliación | Apagada, mock | Imprescindible: único recurso ante timeout |
| Datos de clientes | Se envían a un servicio de pruebas público | Envío a SUNAT (esperado) |
| Normativa | Sin plazos efectivos | 3 días factura, 7 días RC/baja; cambios 1-ene-2027 |

## 4. Riesgos

| ID | Riesgo | Estado en código | Acción |
|---|---|---|---|
| K1 | Doble envío por respuesta incierta | Mitigado (`_provably_unsent`, `_claim_attempt`, ADR-44). Resto: la factura incierta queda en `SUBMISSION_ERROR` (no hay `SUBMISSION_UNKNOWN`), y la consulta está apagada por defecto. En producción sin consulta no hay salida. | Habilitar consulta junto con emisión (corregir H-14); estado propio. |
| K2 | Reuso de serie/correlativo | Mitigado (lock de fila, unicidad por ambiente). Riesgo nuevo: reetiquetar una serie BETA como producción. | Series de producción = filas nuevas; `next_number` no editable; auditoría. |
| K3 | Credencial/certificado de otra empresa firma documentos | **Abierto**. Una credencial por instalación y sin comprobar RUC SOL = RUC emisor = RUC del certificado. | FP-2/FP-5: alcance por empresa y comprobación triple antes de firmar. |
| K4 | Certificado vencido o ajeno al RUC | Abierto (sólo prueba de consola). El rechazo llega "minutos después" y gasta un número. | Validar al firmar, antes de reservar. |
| K5 | Factura enviada tras 3 días / RC tras 7 días | **Abierto**: sin guarda ni alerta. Factura GENERATED desde el POS sin firma ni envío indefinido. | Guarda de plazo + alertas + tarea de generación (envío confirmado por persona). |
| K6 | Secretos en logs | Bajo: el adaptador registra tipo de excepción; el hash del sobre no se calcula; `safe_message` acotado a 500. Resto: `faultstring` de SUNAT se guarda tal cual (sanitizado en longitud). | Test que busque secretos sembrados en logs y respuestas (propuesto en el ADR). |
| K7 | Mutación del XML tras firmar | Mitigado: `sign_fiscal_document` no refirma; reenvío usa el mismo `signed_xml`; hash guardado. Brecha: reconstrucción desde Order/Product vivos al firmar (R28). | Comparar `data` contra los importes congelados de `FiscalDocument` y congelar nombre de línea. |
| K8 | Datos fiscales mezclados entre inquilinos | Bajo en datos; alto en credenciales (K3). Resúmenes sin filtro de sucursal (H-08). | Alcance por sucursal; credenciales por empresa. |
| K9 | Rechazo mal tratado | Mitigado (terminal, no se reintenta ciego). Falta flujo explícito de reemisión y mensaje guiado para 0100-1999 persistente (H-05). | Flujo "corregir y emitir" auditado. |
| K10 | Boleta informada en RC aceptado se ve "pendiente" | Abierto (R16). | Estado derivado "informada en RC aceptado" en API/PDF. |
| K11 | Baja con plazo viejo desde 2027 | Abierto. | Fecha de corte en código (FISCAL-PROD-09). |
| K12 | Tests que toquen SUNAT real | Mitigado por convención, no por mecanismo. | Guarda global de red en tests. |
| K13 | Huecos de numeración (documentos preparados y nunca enviados, p. ej. pedido cancelado) | Abierto: la baja de factura exige CDR aceptada y otorgamiento; un número reservado nunca enviado no tiene tratamiento definido. | Pregunta al contador. |
| K14 | SHA-256 no aceptado por producción | NO VERIFICADO. | Primera emisión real controlada; perfil configurable en `signing.py`. |

## 5. Contrato seguro propuesto para producción

1. **Ambiente por empresa, doble llave.** `CompanyFiscalProfile.environment` (default `beta`) **y** techo de instalación `FISCAL_ALLOWED_ENVIRONMENTS` (default `beta`). Producción exige las dos más un `fiscal_preflight` GO. Pasar a producción es acto de MASTER con confirmación escrita (RUC) y segundo confirmante o espera; no hay vuelta a BETA con documentos reales (sólo pausa). El endpoint vive en una tabla del código, nunca en petición ni en datos de empresa.
2. **Credenciales por empresa.** Consola SUNAT pasa a `SCOPE_COMPANY`; sin respaldo de instalación en producción. Antes de reservar número: `RUC SOL == RUC emisor == RUC en el sujeto del certificado`, certificado vigente (aviso a 60/30 días), par cert/clave coherente. SOL y P12 sólo cifrados, sin lectura, nunca en logs ni auditoría.
3. **Series por empresa, tipo y (opcional) sucursal.** Filas `production` nuevas; una activa por combinación; `next_number` sólo por reserva; creación vía comando/pantalla MASTER con auditoría; código de anexo en la serie si procede.
4. **Emisión idempotente en tres pasos separados.** `preparar` (reserva + snapshot, local) → `firmar` (local, una vez) → `enviar` (red, fuera de transacción, intento reservado antes). Un original vivo por venta; clave de idempotencia por nota; ningún paso reserva número si interruptor, credenciales o par de firma fallan.
5. **sign → send → CDR → reconciliación.** Estado terminal sólo con CDR leído de los bytes exactos, ligado al documento (id + RUC + tipo) **también en la respuesta directa de `sendBill`** (cierra H-06). Respuesta incierta: estado `SUBMISSION_UNKNOWN` para la factura; sólo `getStatusCdr` (habilitado junto con la emisión), cron de sólo lectura; reenvío únicamente si la consulta es concluyente y por acción humana auditada. Boletas: se reconcilian por ticket del RC.
6. **Nunca "Aceptado por SUNAT" sin CDR.** Ya cierto para facturas; extender: boleta = "Informada en RC aceptado (ticket X)" sólo con CDR del RC; PDF y API usan el mismo estado derivado.
7. **PDF = XML firmado.** Ya cierto (líneas, importes, fecha, QR y DigestValue salen de `signed_xml`). Añadir: marca de pruebas por ambiente (ya existe), y que nombre/descripción de línea se congelen antes de firmar.
8. **Snapshots como fuente económica.** Firmar compara los datos reconstruidos contra los importes congelados de `FiscalDocument` y aborta si difieren. Afectación IGV congelada por línea (FISCAL-PROD-06) antes de emitir algo que no sea gravado.
9. **Plazos como reglas de sistema.** Factura/NC/ND: bloquear envío vencido el día +3 (decisión del dueño sobre qué hacer); RC: alerta desde día +5, bloqueo del envío automático al +7; baja: regla vieja hasta 31-dic-2026 y regla de "generación" desde 1-ene-2027 con fecha de corte en código.
10. **Aislamiento de pruebas.** Guarda global que haga fallar cualquier socket a `*.sunat.gob.pe` en `manage.py test`; el humo BETA queda como comando opt-in; no existe equivalente para producción.

## 6. Requiere GO del dueño / validación del contador / alta en SUNAT

**Alta y decisiones en SUNAT (dueño, antes de cualquier emisión real)**
- Elegir canal: **SEE-Del contribuyente directo (Modalidad I, es lo que implementa el código)**, PSE (Modalidad II) u OSE. Cambiar de canal cambia credenciales, plazos y el contrato de proveedor (el diseño admite adaptador, pero hoy sólo existe SOAP directo). Decisión pendiente.
- Indicar en SOL la calidad de emisor electrónico por elección y el correo del adquirente; verificar RUC activo/habido y afecto a renta de tercera.
- Obtener certificado digital (prestador acreditado, o CDT gratuito de SUNAT si califica: **condiciones vigentes no confirmadas**), a nombre del RUC, y **registrarlo en SOL**.
- Crear usuario SOL secundario con el perfil de envío adecuado (nombre actual del perfil NO VERIFICADO).
- Datos del emisor exactos: RUC, razón social, domicilio fiscal, nombre comercial; ¿hay establecimientos anexos? ¿con qué código?
- Nombres de series de producción y si es una por empresa o por local.
- Autorización de la primera factura y boleta reales: venta concreta, día y quién autoriza.

**Contador (validación)**
- Afectación IGV del catálogo (gravado/exonerado/inafecto), revisión producto por producto.
- Boletas: ¿resumen diario siempre (opción actual) o envío individual (RS 114-2019 art. 12, 5 días)? El considerando de esa RS dice que el envío individual se permite en SFS y OSE, no en SEE-Del contribuyente: **NO VERIFICADO** para este canal.
- Tratamiento de huecos de numeración y de documentos preparados nunca enviados.
- Política ante factura vencida (+3 días) y RC vencido (+7).
- Notas de crédito parciales (devoluciones parciales) y baja de boleta.
- Conservación (período, formato, respaldo) y consulta del adquirente.
- Tratamiento de cobros de pasarela en TEST (ya bloqueado) y de ventas a crédito (no soportadas).

**Código/normativa por confirmar (no del dueño)**
- Aceptación de SHA-256 en producción; releer reglas de validación actuales (xlsx), catálogos 01/10/11/51 y cambios del 1-ene-2027; endpoint exacto de `billConsultService`; si el ubigeo del emisor es exigido.

## 7. Plan por fases (PR pequeños)

Principio: BETA y producción nunca comparten credenciales, series ni filas; ningún PR activa producción; ningún test envía a SUNAT.

| PR | Contenido | Toca | Depende |
|---|---|---|---|
| P0 | Corregir `docs/adr-fiscal-prod.md` (PR #173) con este estado: marcar H-01/02/03/04/11 como cerrados, N13 verificado, N10 degradado a NO VERIFICADO, §2.8 del manual; actualizar `docs/sunat-cpe-requisitos.md` (RS 000143-2026) | docs | — |
| P1 | Guarda global de red en tests (falla ante `*.sunat.gob.pe`) + test de secretos sembrados en logs/respuestas | tests, `settings` de test | — |
| P2 | H-14: resolver endpoint de consulta (vacío = constante) y captura de `ValueError`; H-06: ligar el CDR de `sendBill` con `cdr_matches_document` | `fiscal_config.py`, `fiscal_services.py`, tests | — |
| P3 | Estado `SUBMISSION_UNKNOWN` para facturas/notas (migración sin SQL) y comando de sólo lectura `fiscal_reconcile_pending` | `models.py`, `fiscal_services.py`, comando | P2 |
| P4 | `fiscal_preflight` (sólo lectura, sin red, sin secretos) + sección fiscal en `ops_status` (plazos 3/7 días, inciertos, certificado por vencer) | comando, `ops_status` | — |
| P5 | Guarda de plazo: bloquear/avisar envío de factura > día +3 y RC > día +7; estado derivado "boleta informada en RC aceptado" en API/PDF | `fiscal_services.py`, `fiscal_summary_*`, `fiscal_pdf_services.py` | P3 |
| P6 | Firmar compara datos reconstruidos con importes congelados y congela descripción de línea | `fiscal_services.py`, `models.py` (campo), migración | — |
| P7 | `CompanyFiscalProfile` (ambiente, atestación SOL), consola SUNAT `SCOPE_COMPANY`, comprobación RUC SOL = emisor = certificado y vigencia al firmar, comando `fiscal_series`, código de anexo en la serie; techo `FISCAL_ALLOWED_ENVIRONMENTS=beta` | `fiscal_config.py`, `integrations/providers/sunat.py`, `models.py`, migración, frontend de integraciones | P4; datos del dueño sólo para cerrarla |
| P8 | Afectación por producto (pending_review bloquea), luego exonerado/inafecto en XML | modelos, `tax_services`, `fiscal/*` | contador |
| P9 | NC parcial y tope por original; NC/ND de boleta en RC; RC por sucursal; H-10; H-12 | `fiscal_note_services.py`, `fiscal_summary_*` | P3 |
| P10 | Normativa 1-ene-2027 (plazo de baja desde generación, reglas actualizadas) con fecha de corte | `fiscal_void_services.py`, `fiscal/rules.py` | lectura del xlsx vigente; antes de dic-2026 |
| P11 | Tabla de endpoints con producción detrás del techo; documento de salida. **No se funde sin GO escrito del dueño**; luego primera emisión real autorizada, seguimiento de 7 días | operación | todo lo anterior, sección 6 |

Cada PR: pruebas con proveedores inyectados, sin red; migraciones numeradas al fusionar; commits locales y permiso del dueño antes de push o despliegue.

## 8. Comparación con el ADR #173

Vigente y correcto: estructura de bloqueos C/O/N, FP-1 (preflight), FP-2 (conmutador por empresa), FP-3 (series), FP-5 (afectación), matriz de "falta" para credenciales por empresa, certificado, series, plazos, NC parcial, RC, baja.
Desactualizado (ya en master por FISCAL-PROD-02 y ADR-44): H-01, H-02 (se guardan bytes y ZIP, `models.py` `cdr_raw/cdr_zip`), H-03, H-04, H-11 y parte de H-07 (generar y firmar el RC es un acto). Las fases 02 y 03 del plan están hechas.
Sigue abierto: H-05, H-06, H-08, H-09, H-10, H-12, H-13, H-14, H-15, el estado `SUBMISSION_UNKNOWN` de la factura (el ADR lo pide; el código usa una solución provisional declarada).
A corregir o matizar en el ADR: N10 (envío individual de boleta "permitido"): el considerando de la RS 114-2019 lo limita a SFS/OSE, y el Manual §2.4 sólo describe `sendBill` para facturas y notas; N3 sobre `billConsultService` (el Manual da dos URL distintas); falta la comprobación de RUC del sujeto del certificado frente al RUC emisor (está en FP-5 pero no en el preflight de `fiscal_services`), el guard de plazos (sólo como "alerta"), el estado visible de la boleta tras RC (R16), la guarda global de red en tests (K12), la reconstrucción desde datos vivos al firmar (R28) y que `getStatusCdr` no cubre boletas.
