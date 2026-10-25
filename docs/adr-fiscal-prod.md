# ADR — FISCAL-PROD-01 · De SUNAT BETA a PRODUCCIÓN

Estado: **PROPUESTA** (auditoría y diseño). Fecha: 2026-10-25, sobre `master` `573bba8`.
Continúa [adr-fiscal-c22a1.md](adr-fiscal-c22a1.md) (ADR-1…43), que sigue vigente.
Las decisiones de aquí llevan el prefijo **FP-** para no chocar con aquella numeración.

Autoridad: código > migraciones > tests > docs. Cada afirmación sobre el código lleva `archivo:línea` o
`archivo::símbolo`, siempre bajo `backend/store/`.

**Límites de esta fase, sin excepción.** No se emitió ningún documento real. No se activó producción ni se
llamó a un endpoint de producción de SUNAT. No se tocó ningún certificado ni credencial, no se gastó ningún
correlativo, no se accedió al servidor y no se desplegó nada. El código se leyó sin modificarlo.

**Hecho que condiciona todo.** Producción (R1) corre con `FISCAL_ENABLED=0`, y la consola de integraciones
no tiene SUNAT activado. `deploy/preflight.py:555` rechaza `FISCAL_ENABLED` encendido.

---

## 1. Requisitos oficiales de SUNAT, verificados el 2026-10-23

Marcas: **OFICIAL** significa que se leyó la fuente de SUNAT. **SIN CONFIRMAR** significa que no se
encontró fuente oficial.

`cpe.sunat.gob.pe` devuelve 403 a lecturas automatizadas. Sus páginas se leyeron en copias de Wayback, y se
indica la fecha de cada copia.

| # | Tema | Hallazgo | Marca | Fuente |
|---|---|---|---|---|
| N1 | Homologación | **Eliminada.** Desde la R.S. 287-2017 no hay solicitud de autorización ni homologación: «eliminándose la presentación de la solicitud de autorización así como el procedimiento de homologación». | OFICIAL | https://www.sunat.gob.pe/legislacion/superin/2017/287-2017.pdf |
| N2 | Alta como emisor | Se indica en SOL que se desea ser emisor electrónico. Además hay que **registrar en SOL por lo menos un certificado digital vigente** y un correo (art. 6 de la R.S. 097-2012 en el texto de la 287-2017). La condición rige desde el día calendario siguiente. | OFICIAL | misma; https://cpe.sunat.gob.pe/sistema_emision/see_contribuyente (copia 2026-10-17) |
| N3 | Endpoints de producción | Envío: `https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService?wsdl` (`sendBill`, `sendSummary`, `getStatus`, `sendPack`). Consulta de CDR: `https://e-factura.sunat.gob.pe/ol-it-wsconscpegem/billConsultService?wsdl`. Validez: `…/ol-it-wsconsvalidcpe/billValidService?wsdl`. **No hay** servicio de consulta en BETA. | OFICIAL | «Servicios Web Disponibles» (2026-05, copia 2026-06-13); https://cpe.sunat.gob.pe/comunicados (copia 2026-08-07) |
| N4 | REST | Facturas y boletas **siguen siendo SOAP** con WS-Security UsernameToken, también en el Anexo 6 que rige desde 2027. REST/OAuth existe sólo para otros servicios, como guías y factoring. | OFICIAL | https://www.sunat.gob.pe/legislacion/superin/2026/anexo-000048-2026.pdf |
| N5 | Certificado | Debe ser de un prestador inscrito en el **ROPS de INDECOPI** y llevar la razón social y el **RUC del titular**, con nivel de seguridad al menos medio. No se encontró ninguna norma que admita un certificado autofirmado en producción. | OFICIAL | https://cpe.sunat.gob.pe/informacion_general/certificados_digitales (25/09/2025); R.S. 097-2012 art. 2.12 |
| N6 | Algoritmo de firma | El Anexo 6 de 2019 no fija el algoritmo; el de 2012 citaba SHA-1. BETA aceptó RSA-SHA256. **Que producción acepte SHA-256** no tiene fuente que lo diga. | SIN CONFIRMAR | Anexo VI R.S. 114-2019 |
| N7 | Usuario SOL | El Anexo 6 (6.1.2) exige «código de usuario y la Clave SOL»; no impone un usuario secundario. Usar uno con perfil de emisor es buena práctica, y el procedimiento para crearlo no se pudo abrir. | OFICIAL / SIN CONFIRMAR | Anexo 6 |
| N8 | Plazo de factura y notas vinculadas | Hasta **3 días calendario** desde el día siguiente a la emisión. | OFICIAL | https://www.sunat.gob.pe/legislacion/superin/2023/000003-2023.pdf |
| N9 | Plazo del resumen diario | «a más tardar, hasta el sétimo día calendario siguiente de emitidos» (art. 21.1). | OFICIAL | https://www.sunat.gob.pe/legislacion/superin/2019/114-2019.pdf |
| N10 | Boleta por envío individual | **Permitido** en lugar del resumen (art. 7.3). El plazo que da el texto de 2019 es de «cinco días calendario»; pasado ese plazo sólo cabe el resumen. Si ese texto sigue vigente no se confirmó tras la renumeración. | OFICIAL / plazo SIN CONFIRMAR | misma |
| N11 | Baja, hasta el 31/12/2026 | Factura y notas: 7 días calendario desde el día siguiente a **recibir la CDR aceptada**. Boleta: por resumen, en 7 días. | OFICIAL | art. 14, R.S. 097-2012 modificado |
| N12 | Baja, desde el 1/1/2027 | El plazo se cuenta desde el día siguiente a la **generación** del documento, para factura y boleta (R.S. 000048-2026 art. 9). | OFICIAL | anexo-000048-2026 |
| N13 | **Corrección: entrada en vigor** | La R.S. 000143-2026 (31/07/2026) **aplaza la R.S. 000048-2026 al 1 de enero de 2027**. `sunat-cpe-requisitos.md` la daba por vigente desde el 1.8.2026, y está desactualizado. | OFICIAL | https://www.sunat.gob.pe/legislacion/superin/2026/000143-2026.pdf |
| N14 | **Corrección: reglas de validación** | Hay un archivo de reglas más nuevo, «actualizado al 24.04.2026». ADR-43 se apoyó en el del 21.04.2025. | OFICIAL | https://cpe.sunat.gob.pe/guias-y-manuales (copia 2026-07-21) |
| N15 | Versiones | UBL 2.1 es obligatorio para factura, boleta y notas desde 2019. El resumen y la baja usan el formato 2.0. La R.S. 000108-2026 cambia los catálogos 55 y 61 (vigente 1/6/2026). La 000048-2026 cambia los catálogos 01, 10, 11 y 51 y los Anexos 1–6, 8 y 9 desde 2027. | OFICIAL | https://www.sunat.gob.pe/legislacion/superin/2019/133-2019.pdf; anexo-000108-2026 |
| N16 | ¿Se cierra el SEE-Del contribuyente? | No hay indicio de cierre: las resoluciones de 2026 lo mantienen y lo modifican. | SIN CONFIRMAR que se cierre | índice de resoluciones 2026 |

**Consecuencias para el diseño.**
- No hay homologación que pasar, pero **sí** hay pasos del propietario en SOL: alta como emisor y registro
  del certificado.
- Las reglas de validación y los catálogos cambian el 1/1/2027. Producción debe salir con el archivo
  24.04.2026 releído, y con una fase propia para los cambios de 2027 (FISCAL-PROD-09).
- Un timeout en producción **no tiene red de seguridad automática** si no se habilita `billConsultService`,
  porque en BETA no existe.

---

## 2. Matriz: qué está implementado y qué falta

Estados: IMPLEMENTADO · PARCIAL · PENDIENTE · BLOCKED. «Bloquea PROD» usa estas siglas:
- **C**: código;
- **O**: datos del propietario;
- **N**: confirmación normativa;
- —: no bloquea.

| Área | Estado | Evidencia | Qué falta | Bloquea PROD |
|---|---|---|---|---|
| Datos del emisor por empresa (RUC, razón social, domicilio) | PARCIAL | `Company.tax_id` y `legal_name` por `order_identity` (`fiscal_services.py:188-190`, `:331-336`); `rules.py:271` valida el dígito del RUC | No se envía el ubigeo: `Party.district_code` nunca se llena, así que se omite `RegistrationAddress/cbc:ID` (`builder.py:193`). `AddressTypeCode` fijo en `0000` (`builder.py:198`), sin código de establecimiento anexo por sucursal | C + O |
| Credenciales SOL por empresa | **PENDIENTE** | La consola SUNAT tiene `scope = SCOPE_PLATFORM` (`integrations/providers/sunat.py:49`). `resolve_credentials(company)` ignora `company` (`fiscal_config.py:257-296`). DEC-INT-03 y FISCAL-TENANT-SCOPE lo declaran | Una sola credencial para toda la instalación. Ninguna comprobación de que el RUC de la SOL sea `Company.tax_id` | **C** + O |
| Almacenamiento cifrado | IMPLEMENTADO | Fernet con `APP_CONFIG_ENCRYPTION_KEY`, sellado con «para» = proveedor, empresa y slot (`integrations/secret_store.py:85-104`). Escritura sin lectura (DEC-INT-01). `IntegrationConfig.company` ya admite alcance por empresa (WhatsApp) | — | — |
| Certificado: carga | IMPLEMENTADO | PKCS#12 en memoria (`fiscal/certificate.py`), P12 o PEM pero nunca ambos (ADR-16), subida por consola validada (`sunat.py::clean`) | — | — |
| Certificado: vigencia | PARCIAL | `validity()` (`certificate.py:54`) sólo se usa en la **prueba** de la consola (`sunat.py:96-101`) | Firmar no rechaza un certificado vencido. No se comprueba que el RUC del sujeto sea el del emisor. No hay alerta de vencimiento ni rastro del registro en SOL | **C** + O |
| Series por ambiente | IMPLEMENTADO (modelo) | Única por `(company, document_type, series, environment)` (`models.py:8514`). `resolve_series` filtra por ambiente y sucursal y falla ante ambigüedad (ADR-11) | No hay **ninguna superficie** para crear series: sólo `seed_demo_users --fiscal-beta` y el admin de Django, que está apagado en producción. No hay política de series de producción | **C** + O |
| Separación BETA/PROD | IMPLEMENTADO, fail-closed | `resolve_environment()` levanta ante todo lo que no sea `beta` (`fiscal_config.py:109-125`). Tabla de endpoints sólo con BETA (`:416`). La consulta va aparte (`FISCAL_CONSULT_ENABLED`, ADR-22) | No hay conmutador por empresa (FP-2). `fiscal_enabled()` es de la instalación. `FISCAL_CONSULT_ENDPOINT` vale `''` por defecto en `settings.py:292`, y entonces `getattr` devuelve `''` y no la constante de producción (`fiscal_config.py:470`) | **C** |
| Firma XML | IMPLEMENTADO | `signxml`, C14N 1.0, RSA-SHA256 (`fiscal/signing.py:67-70`) | Aceptación de SHA-256 en producción (N6) | N |
| UBL 2.1 y reglas | IMPLEMENTADO (gravado) | XSD versionado (ADR-7), `rules.validate` y matriz UBL | Sólo afectación `10` (`rules.py:126-130`). Reglas transcritas del xlsx de 2025; falta releer el de 24.04.2026 (N14). Cambios de 2027 (N15) | C + N |
| Envío `sendBill` | IMPLEMENTADO | `submit_fiscal_document` (`fiscal_services.py:801`): intento reservado y bloqueado (ADR-14, DEC-FISCAL-DOUBLE-SUBMIT-01) | Ver H-03, H-04 y H-05 | **C** |
| Reintento y reconciliación | PARCIAL | `reconcile_fiscal_document` consulta `getStatusCdr` y aplica bajo bloqueo (ADR-20). Exige `FISCAL_CONSULT_ENABLED` | Un timeout de lectura y un 5xx se reenvían sin consultar (H-04). En producción no hay automatismo | **C** |
| Persistencia de XML/CDR y hashes | PARCIAL | `signed_xml` y SHA-256 (`fiscal_services.py:711-712`); `cdr_xml` y `cdr_sha256`; hashes del intento | H-02: el CDR se guarda con `decode('replace')` y el ZIP original de SUNAT no se conserva | **C** |
| Rechazos y observaciones | IMPLEMENTADO | Rangos 0100–1999, 2000–3999 y 4000+ (`provider.py:337-350`); 4000+ sólo dentro de un CDR (ADR-13); un rechazo es terminal (ADR-12) | El CDR de `sendBill` no se compara con el documento (H-06). No hay flujo explícito de reemisión tras un rechazo | C |
| NC/ND de factura | PARCIAL | 07/08 contra factura e idempotentes por clave (ADR-29…33) | La NC siempre es total: se aceptan los motivos 01–13 de Catálogo 09, pero se emite una reversión completa. `FULL_REVERSAL_CREDIT_REASONS` no se usa (`fiscal_note_services.py:43`). Nada impide dos NC totales con claves distintas. No hay NC parcial por línea | **C** |
| NC/ND de boleta | PENDIENTE (fail-closed) | `fiscal_note_services.py:77-89`; envío `fiscal_services.py:827-830` | Pendiente, también según la auditoría contable | C (si se venden boletas) |
| Resumen diario (RC) | PARCIAL | `FiscalDailySummary` (ADR-24…28), bloques de 500, ticket, `getStatus` 0/98/99, `SUBMISSION_UNKNOWN` | Sólo `ConditionCode=1`. Ni baja de boleta (código 3) ni notas de boleta. No vigila el plazo de 7 días (N9). No hay tarea programada. H-07 y H-08. `SUBMISSION_UNKNOWN` sin salida (H-09) | **C** |
| Baja (RA) | PARCIAL | Factura y notas de factura; plazo de 7 días al crear y al enviar (ADR-38); atestación de no otorgado (ADR-34) | Baja de boleta PENDIENTE. H-10: una nota en curso no se detiene ante una baja. Desde 2027 cambia la base del plazo (N12). `SUBMISSION_UNKNOWN` sin salida | **C** |
| Plazos y alertas | **PENDIENTE** | — | No se comprueba ni avisa de ninguna factura de más de 3 días, resumen pendiente, intento abierto ni documento incierto. `ops_status` no tiene sección fiscal | **C** |
| Auditoría | PARCIAL | Cada acción fiscal exitosa deja `AdminAuditLog` con `company` (`fiscal_views.py:242,282,386,420,482,543`; vistas de RC y RA) | No se auditan las negativas (400/409/500) ni las descargas de XML, CDR o PDF. El humo BETA no audita | C (menor) |
| Idempotencia | IMPLEMENTADO | Un original vivo por venta (`fiscal_document_one_live_per_order`), clave de nota y de baja, intento reservado | — | — |
| Multiempresa | PARCIAL | Documentos y bajas acotados a empresa y sucursal; lo ajeno responde 404 (`fiscal_views.py:59-110`) | H-08: los resúmenes sólo se acotan por empresa, no por sucursal | C |
| Interruptor de emisión | **PARCIAL** | La caja consulta `fiscal_enabled()` (`pos_services.py:1216`) | H-01: las cuatro vistas de creación reservan el correlativo **antes** de mirarlo | **C** |
| Afectación tributaria | PENDIENTE | §6 | Sin campo por producto; un solo tratamiento por venta | **C** + O |

---

## 3. Hallazgos verificados en código

Severidad de producción. **P1** bloquea la salida a producción. **P2** se corrige antes o en paralelo. **P3**
es deuda.

| ID | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| **H-01** | **P1** | `AdminOrderFiscalDocumentView.post` no consulta `fiscal_enabled()`. Con la emisión apagada **crea el documento y gasta un correlativo** si existe una serie activa del ambiente. **No puede firmar ni enviar**: `resolve_credentials` falla cerrado y responde 503 (`fiscal_config.py:278-284`), igual que `resolve_provider`. En producción hoy no hay series (ninguna superficie las crea), así que responde 400 sin gastar nada: el riesgo está **latente, no explotado**. El mismo orden (crear antes de mirar) se repite en notas (`fiscal_views.py:374→404`), bajas (`fiscal_void_views.py:229→249`) y resúmenes (`fiscal_summary_views.py:133→134`, ver H-07) | `fiscal_views.py:231-271`; `fiscal_services.py:542-680` |
| **H-02** | **P1** | El CDR se guarda como texto con `decode('utf-8', 'replace')`, mientras su hash se calcula sobre los bytes (`fiscal_services.py:905-906`, `:1062-1063`). Si el CDR no es UTF-8 válido, el texto guardado **no reproduce su hash**. El ZIP que devuelve SUNAT se abre en `provider.py:374` y se descarta. La obligación de conservar las constancias (requisito P3 de `sunat-cpe-requisitos.md`) y la evidencia contable (ACCOUNTING-05) piden los bytes originales | idem |
| **H-03** | **P1** | Una excepción del proveedor que no sea de transporte, al armar el sobre o al leer la respuesta, deja el intento abierto (`finished_at` nulo) y la vista responde 500 sin auditoría. A los 10 minutos (`STALE_ATTEMPT_MINUTES`, `fiscal_services.py:746`) se **reenvía sin consultar el CDR** (`:779-782`) | `fiscal_services.py:859`; `fiscal_views.py:474-480` |
| **H-04** | **P1** | Un timeout incierto se reenvía. `_post` marca `never_sent` sólo para `ConnectTimeout` (`provider.py:311-318`), pero `submit_invoice` ignora esa marca, y `ReadTimeout`, 5xx y `UNKNOWN_RESPONSE` acaban en `SUBMISSION_ERROR` con `can_retry` (`provider.py:456-478`; `fiscal_services.py:73-74`; `fiscal_views.py:143`). Los resúmenes y las bajas ya distinguen `SUBMISSION_UNKNOWN` (ADR-28/39); la factura no | idem |
| **H-05** | P2 | Un reenvío que SUNAT conteste con 01xx–1999 se queda en `SUBMISSION_ERROR` para siempre, sin salida guiada (inferido, sin test) | `provider.py:349-350` |
| **H-06** | P2 | El CDR de `sendBill` **no se compara** con el documento por `ReferenceID` ni por RUC. Sólo lo hacen la reconciliación, el RC y la RA (`cdr_matches_document`) | `fiscal_services.py:887-907` frente a `:1016` |
| **H-07** | P2 | `generate_daily_summaries` crea los resúmenes antes de `resolve_credentials`. Si fallan las credenciales o la firma, quedan en GENERATED sin firmar, sin endpoint que los firme, y sus boletas quedan tomadas | `fiscal_summary_views.py:133-134`; `fiscal_summary_services.py:266` |
| **H-08** | P2 | Los resúmenes se acotan sólo por empresa. Quien tenga `sales.fiscal.issue` en una sola sucursal genera y envía resúmenes con boletas de otras | `fiscal_summary_views.py:79-133` |
| **H-09** | P2 | `SUBMISSION_UNKNOWN` en RC y RA sólo expone `can_recover`: **no hay acción de recuperación** y las boletas o documentos quedan bloqueados | `fiscal_summary_views.py:53`; `fiscal_void_views.py:61`; `urls.py:345-353` |
| **H-10** | P2 | Deuda «una baja en curso no bloquea el envío», que en verdad es más estrecha. El original no corre riesgo, porque la baja exige que esté aceptado. Una **nota** creada antes de la baja (GENERATED, SIGNED o `SUBMISSION_ERROR`) sí puede enviarse y aceptarse contra un original en baja o ya dado de baja: la baja sólo mira las notas ACEPTADAS | `fiscal_void_services.py:290-296`; `fiscal_note_services.py:64-69`; `fiscal_services.py:801-859` |
| **H-11** | P1 | Si la pasarela está con claves TEST y la emisión encendida, **un pedido pagado con claves TEST puede recibir un comprobante real**. `get_or_create_fiscal_document` sólo mira `order.paid` y `status` (`fiscal_services.py:553`). `PaymentTransaction.environment='test'` existe (`models.py:1127-1132`) y nadie lo consulta. La auditoría contable lo detectó para la analítica, y aquí es fiscal | idem |
| **H-12** | P2 | Un pedido web con cupón conserva `discount_source='none'` y la emisión lo rechaza («descuento sin origen»): falla cerrado y bloquea el comprobante | `checkout_services.py:459-484,541`; `fiscal_services.py:359-365` |
| **H-13** | P2 | La NC siempre es total y acepta motivos que piden una NC parcial (07, 09…). Además pueden emitirse dos NC totales sobre el mismo original | `fiscal_note_services.py:43,111-141,206-221` |
| **H-14** | P3 | `FISCAL_CONSULT_ENDPOINT` vacío anula la constante de producción, porque `getattr` encuentra el ajuste | `settings.py:292`; `fiscal_config.py:470` |
| **H-15** | P3 | La firma del CDR verifica la integridad pero no la autenticidad (ADR-23, CDR-TRUST-01) | `fiscal/cdr_signature.py` |
| **H-16** | P3 | `breakdown_for_order` usa la tasa de hoy en una venta sin snapshot, aunque su docstring diga lo contrario. La vuelta atrás de `0080` borra filas posteriores con tasa 0,18. Es contable, no se emite sobre ello | `tax_services.py:256-260`; `0080_backfill_order_tax.py:85-89` |

---

## 4. Bloqueos por tipo

**Código (C), por orden:**
- H-01, H-02, H-03, H-04 y H-11;
- credenciales y conmutador por empresa (FP-2);
- política y superficie de series (FP-3);
- vigencia y RUC del certificado al firmar;
- plazos y alertas;
- H-06 a H-10 y H-12/H-13;
- afectación por producto (FP-5);
- ubigeo y establecimiento anexo.

**Datos del propietario (O):**
- RUC y razón social exactos, domicilio fiscal y ubigeo;
- alta como emisor en SOL;
- usuario SOL (secundario) y su clave;
- certificado de un prestador ROPS a nombre del RUC, **registrado en SOL**, con su contraseña;
- series de producción;
- código de establecimiento anexo por local, si factura desde un anexo;
- revisión de la afectación del catálogo, con su contador.

**Confirmación normativa (N):**
- SHA-256 en producción (N6);
- plazo de la boleta individual (N10);
- relectura de las reglas 24.04.2026 (N14);
- cambios del 1/1/2027 (N12, N15);
- obligatoriedad del ubigeo del emisor y del código de anexo en las reglas vigentes;
- exoneraciones o inafectaciones aplicables a los productos del piloto (pregunta para el contador).

---

## 5. Decisiones PROPUESTAS

### FP-1 · `manage.py fiscal_preflight`: lista GO/NO-GO de sólo lectura

**Propuesta.** Un comando que **no escribe nada, no llama a la red y nunca imprime un secreto**. Se ejecuta por
empresa (`--company <slug>`) y por ambiente objetivo (`--target production|beta`). Sale con código 0 sólo si
todo es GO.

| # | Comprobación | GO si |
|---|---|---|
| 1 | Interruptores | `FISCAL_ENVIRONMENT` y la fila por empresa (FP-2) coinciden con `--target`; `FISCAL_BETA_SMOKE_ENABLED=0` en producción |
| 2 | Emisor | `Company.tax_id` pasa `check_ruc`; razón social y domicilio no vacíos; ubigeo de 6 dígitos |
| 3 | Credenciales | Existe la configuración activa **de esa empresa**; RUC de la SOL == `Company.tax_id`; usuario no vacío; la clave existe (se informa «configurada», nunca su valor ni su longitud) |
| 4 | Certificado | Abre en memoria; vigente; vence en más de 30 días (aviso entre 30 y 60). El sujeto contiene el RUC del emisor. No es autofirmado si el destino es producción. Se imprime sujeto, emisor, número de serie y fechas; nada de la clave |
| 5 | Registro en SOL | El propietario lo atestigua: fecha y quién (FP-2). El sistema no lo puede comprobar |
| 6 | Series | Hay exactamente una serie activa por tipo, sucursal y ambiente objetivo. Ninguna serie de producción comparte nombre con una de BETA sin decisión registrada. `next_number` es coherente con lo emitido |
| 7 | Deuda de código | Banderas de versión: H-01/H-03/H-04/H-11 cerrados (se comprueba por constante de versión y prueba, no por texto) |
| 8 | Cola pendiente | 0 documentos `SUBMISSION_ERROR`/inciertos, 0 intentos abiertos, 0 RC/RA `SUBMITTED`/`SUBMISSION_UNKNOWN`, 0 facturas firmadas con más de 3 días |
| 9 | Pasarela | Si el destino es producción: la pasarela activa no está en TEST, o la emisión excluye los pagos TEST (H-11) |
| 10 | Afectación | 0 productos activos con afectación `pending_review` (FP-5) |
| 11 | Consulta | `FISCAL_CONSULT_ENABLED` decidido (FP-4) y el endpoint sale de la tabla del código |
| 12 | Respaldo | `APP_CONFIG_ENCRYPTION_KEY` presente; última copia de seguridad de menos de 24 h (lee `ops_status`) |

Salida: una línea por comprobación (`GO` · `NO-GO` · `AVISO`) y `--json` para el ensayo. El comando importa
sólo lectores y se prueba con `assertNumQueries` y sin escrituras (`CaptureQueriesContext`: sólo `SELECT`).
Un test busca en su salida cada secreto sembrado.

**Alternativas.**
- (a) Ampliar `deploy/preflight.py`. Se descarta: ese script lee el archivo de variables y no la base, y las
  credenciales viven en la consola.
- (b) Una vista HTTP. Se descarta en esta fase, porque añade superficie. Puede venir después, para el MASTER.

### FP-2 · Conmutador BETA→PRODUCCIÓN por empresa, con doble confirmación y fallo cerrado

**Propuesta.**
1. La consola SUNAT pasa a **`SCOPE_COMPANY`**. Cada empresa tiene sus propias credenciales y su propio
   certificado, sellados con su contexto. **Nunca se comparten**: no hay respaldo de instalación en
   producción, y la variable `FISCAL_SOL_*` del entorno sólo vale para BETA.
2. El ambiente pasa a ser un **dato de la empresa** (`CompanyFiscalProfile.environment`, por defecto `beta`).
   Hay además un techo de instalación `FISCAL_ALLOWED_ENVIRONMENTS`, por defecto `beta`. Producción exige
   **las dos cosas**. `resolve_environment(company)` falla cerrado si falta alguna, si la empresa no tiene
   credenciales propias o si el preflight no se pasó.
3. Pasar a producción es una acción del **MASTER** con dos pasos:
   1. escribir `PRODUCCION` y el RUC de la empresa;
   2. un segundo MASTER distinto, o el mismo después de 10 minutos y con `fiscal_preflight` GO
      registrado, confirma.
   Queda en `AdminAuditLog` con la empresa, el resultado del preflight (hash) y la atestación del registro
   del certificado en SOL.
4. Volver de producción a BETA **no está permitido** con documentos de producción emitidos. Sólo se puede
   **pausar** (`enabled=False`), y pausar no cambia el ambiente.
5. El endpoint de producción entra a la tabla del código (`fiscal_config.resolve_provider`) sólo en esta
   fase, junto con su test. Nunca viaja en una petición.
6. `fiscal_enabled()` pasa a recibir la empresa, y lo consultan **todas** las vistas antes de reservar
   (cierra H-01).

**Alternativas.**
- (a) Una sola bandera de instalación, como hoy. Se descarta: con dos empresas, una saldría a producción con
  las credenciales de la otra.
- (b) Elegir el ambiente por serie. Se descarta: ADR-10 dice que el ambiente no lo decide el contenido de una
  tabla.
- (c) Confirmación simple con palabra. Es insuficiente para un acto sin vuelta atrás que además gasta
  correlativos reales.

### FP-3 · Política de series

**Propuesta.**
- La serie de producción es una fila **nueva**, con `environment=production`. Nunca se reetiqueta una de
  BETA: los números de BETA no existen para SUNAT.
- Formato:
  - factura `F` + 3 alfanuméricos, boleta `B` + 3;
  - notas con el prefijo de su original (ADR-33);
  - el nombre lo **decide el propietario**: puede repetir `F001`, porque la unicidad incluye el ambiente.
- Una serie por tipo y por sucursal que emite, o una por empresa (ADR-11). Si la sucursal es un
  establecimiento anexo ante SUNAT, la serie guarda su **código de anexo** para `AddressTypeCode`, que hoy
  está fijo en `0000`.
- `next_number` empieza en 1 y nunca se edita a mano. Un número rechazado se queda gastado (ADR-12).
- Crear y desactivar series: comando `fiscal_series` (crear, listar, desactivar) y, en una fase posterior,
  una pantalla del MASTER. Las dos con auditoría. Una serie con documentos no se borra (`PROTECT`).

**Alternativa.** Prefijos distintos por ambiente (por ejemplo `FB01` en BETA). Se descarta como regla: no
aporta nada, porque la unicidad ya incluye el ambiente.

### FP-4 · Respuestas inciertas y reintentos

**Propuesta.** Hoy se reenvía (H-04). La propuesta es preguntar antes.

| Resultado del intento | Estado del documento | Acción permitida |
|---|---|---|
| `ConnectTimeout` o fallo de DNS/TLS **antes** de transmitir (`never_sent=True`) | `SUBMISSION_ERROR` | reenviar el mismo XML |
| `ReadTimeout`, corte tras enviar, 5xx o respuesta ilegible | **`SUBMISSION_UNKNOWN`** (nuevo) | sólo **reconciliar** (`getStatusCdr`); reenviar únicamente si la consulta dice «no existe» con un código identificado, y por acción explícita y auditada |
| Excepción del proveedor fuera del transporte | `SUBMISSION_UNKNOWN`; el intento se cierra en `finally` con `result='provider_exception'` | igual que la fila anterior (cierra H-03) |
| Fault 0100–1999 | `SUBMISSION_ERROR`, con su código | reenviar, con un tope de N intentos y espera creciente; luego, revisión manual |
| CDR 2000–3999 | `REJECTED` | terminal; nuevo documento por una acción explícita |
| CDR que no coincide con el documento | `SUBMISSION_UNKNOWN` y auditoría de conflicto | revisión manual (cierra H-06) |

- En producción, `FISCAL_CONSULT_ENABLED` se enciende **junto** con la emisión, porque es la única red de
  seguridad (N3).
- El intento abandonado (10 min) **no habilita el reenvío**: pasa a `SUBMISSION_UNKNOWN`.
- El CDR se guarda en **bytes** (o base64) y además el ZIP original con su hash (cierra H-02).
- Un comando `fiscal_reconcile_pending` (cron, sólo consulta) recorre los documentos inciertos.
- Lo mismo se aplica a RC y RA, con una acción de recuperación que pregunta por el ticket si existe y, si no,
  exige una decisión manual registrada (cierra H-09).

**Alternativas.**
- (a) Seguir reenviando. Se descarta: SUNAT ya pudo registrar el documento, y entonces el reenvío vuelve
  como duplicado o deja dos rastros.
- (b) No reintentar nunca. Se descarta: un fallo de conexión demostrado es seguro de reintentar.

### FP-5 · Revisión de la afectación del catálogo

**Regla del propietario, vinculante:**
- el precio publicado es **final** e incluye el impuesto que corresponda; nunca se suma un 18 % encima;
- la clasificación histórica no es autoridad;
- la venta gravada ordinaria conserva su tratamiento legal;
- las excepciones se revisan;
- lo emitido no se toca;
- nada se fija como «todas gravadas» a nivel de la plataforma SaaS.

**Lo que hay hoy, verificado:**
- `breakdown_from_total` separa base = `money(total/(1+tasa))` e IGV = total − base, con `ROUND_HALF_UP`
  (`tax_services.py:128,182-228`). Nunca suma encima, y `C21TaxBreakdownTest` lo prueba de 0,01 a 1000.
- Se calcula **una vez por venta**, con un único `tax_treatment` por pedido (`models.py:673-675`, sin
  `choices`). Nadie escribe otro valor que `taxed`.
- La tasa es la constante `DEFAULT_TAX_RATE = 0.18`, y `resolve_tax_rate` ignora la empresa
  (`tax_services.py:69,167-179`).
- Las líneas se reconcilian por mayor residuo contra el snapshot (ADR-17).
- Descuentos según Catálogo 53: `02` global para cupón y manual, `00` por línea para promoción, y se omite
  `AllowanceTotalAmount` (ADR-43; `builder.py:34-39,287-293`).
- **No hay ningún campo de afectación** en `Product`, `Category` ni variante. La importación reconoce las
  columnas «Afectación» y «Código SUNAT» y las descarta a propósito (`import_formats.py:246,291,301`).
  Su nota todavía dice que «esta plataforma no emite comprobantes electrónicos».
- La migración `0080` dio por gravadas al 18 % todas las ventas históricas, y no se emite sobre ellas.
- El emisor sólo sabe declarar el código `10` (`rules.py:55,126-130`; `fiscal_services.py:197-202`). Una
  venta exonerada o inafecta falla cerrado, sin gastar correlativo (`C22A1DomainTest`).

**Propuesta.**
1. `Product.tax_affectation`, con las opciones del Catálogo 07: `10`, `20`, `30` y `pending_review`.
   - La migración llena todo el catálogo existente con **`pending_review`**, sin leer la clasificación
     histórica y sin suponer gravado.
   - Las altas nuevas toman el **predeterminado de la empresa** (`CompanyFiscalProfile.default_affectation`,
     que nace vacío). Sin predeterminado, el producto queda en `pending_review`.
2. La venta **congela** la afectación por línea (`OrderItem.tax_affectation`) y el impuesto por línea.
   - El total y el precio no cambian: el precio final se descompone hacia atrás con la tasa de la afectación
     de esa línea (0 % para 20 y 30).
   - `Order.tax_treatment` pasa a ser derivado (`taxed` · `mixed` · `exempt` · `unaffected`).
   - Las ventas históricas no se reescriben.
3. Emitir **falla cerrado** con un producto `pending_review`, en la caja y en el checkout ya en el momento
   de cotizar, no al facturar. En BETA puede avisar en lugar de bloquear.
4. **Flujo de revisión:**
   - una pantalla o exportación «Afectación por revisar» lista los productos con su categoría, su precio y,
     sólo como referencia marcada «no es autoridad», el valor histórico de la importación;
   - el responsable (capacidad nueva `products.tax.review`) elige el código uno a uno o en bloque con una
     confirmación escrita;
   - cada cambio deja `AdminAuditLog` con el valor anterior y el nuevo, y quién lo hizo;
   - un cambio no afecta a las ventas ya hechas.
5. Exonerado e inafecto exigen soporte del generador: `TaxScheme` 9997 y 9998, `TaxExemptionReasonCode` 20
   y 30, totales por tipo de operación y resumen con `InstructionID` 02/03 (hoy fijo en 0,00,
   `fiscal_summary_services.py:209`). Va en su propia fase y con BETA.

**Alternativas.**
- (a) La afectación por categoría. Se descarta: una categoría mezcla productos de distinto tratamiento.
- (b) Tomar la clasificación histórica. **Prohibido** por el propietario.
- (c) Gravado por defecto para todos. **Prohibido**: sería «hardcodear» a todas las empresas de la
  plataforma SaaS.

---

## 6. Plan por fases y alcance de archivos

Las rutas son de `backend/store/`. Para no chocar con las otras fases en curso:
- **LABEL** posee `product_codes.py`, `sequences.py`, `import_*`, `pos_views.py` (código de barras) y
  `printing/**`;
- **MIGRATION** posee `inventory_services.py` (recuentos), `count_*` y `serializers.py` (recuentos);
- **ACCOUNTING-02…05** posee `store/accounting/**`, `accounting_views.py`, el tramo `admin/accounting/` de
  `urls.py` y cuatro entradas de `capabilities.py`.

Ninguna fase de aquí toca esos archivos, salvo donde se indica. Las migraciones toman su número **al
fusionar**: hoy la hoja es `0146`, y MIGRATION-C reserva `0147` y `0148`.

| Fase | Contenido | Archivos (exclusivos mientras dura) | ¿Ahora? |
|---|---|---|---|
| **FISCAL-PROD-02** · fallo cerrado y deuda de envío | H-01 (interruptor antes de reservar, en las 4 vistas y la caja), H-03, H-04 y H-05 (`SUBMISSION_UNKNOWN` para la factura, intento cerrado en `finally`, sin reenvío tras abandono), H-06, H-07 (credenciales antes de generar), H-10 (envío de nota bloqueado si su original está en una RA viva o aceptada), H-11 (sin comprobante para un pago TEST fuera de BETA), H-14 | `fiscal_services.py`, `fiscal_views.py`, `fiscal_summary_views.py`, `fiscal_void_views.py`, `fiscal_note_services.py` (sólo la comprobación de la baja), `fiscal/provider.py`, `fiscal_config.py`, `models.py` (sólo la opción nueva de `FiscalDocumentStatus`, una migración sin SQL), `test_fiscal_prod02.py` (nuevo) | **Sí.** No toca LABEL, MIGRATION ni ACCOUNTING |
| **FISCAL-PROD-03** · evidencia | H-02: `cdr_bytes` (binario) y `sunat_response_zip` con su hash, en documento, RC y RA. El texto pasa a ser una vista derivada; lo antiguo queda como está y se marca | `models.py` (campos `Fiscal*`), una migración aditiva, `fiscal_services.py`, `fiscal_summary_services.py`, `fiscal_void_services.py`, `fiscal/provider.py` (devolver el ZIP), `test_fiscal_evidence.py` | Tras 02, por los mismos archivos. ACCOUNTING-05 lo **lee** |
| **FISCAL-PROD-04** · preflight y alertas | FP-1 y una sección fiscal en `ops_status` (factura de más de 3 días, inciertos, intentos abiertos, RC pendiente cerca de los 7 días, certificado a 30/60 días) | `management/commands/fiscal_preflight.py` (nuevo), `fiscal_preflight.py` (nuevo, lectores puros), `ops_status.py` (una sección), `test_fiscal_preflight.py` | **Sí**, en paralelo con 02, porque sólo lee. El punto 7 se activa al fusionar 02 |
| **FISCAL-PROD-05** · empresa, credenciales y conmutador | FP-2 y FP-3: `CompanyFiscalProfile` (ambiente, ubigeo, predeterminado de afectación, atestación SOL), consola `SCOPE_COMPANY`, RUC de la SOL == RUC del emisor == RUC del certificado, vigencia al firmar, comando `fiscal_series`, código de anexo en la serie, endpoint de producción en la tabla (todavía con el techo `FISCAL_ALLOWED_ENVIRONMENTS=beta`) | `fiscal_config.py`, `integrations/providers/sunat.py`, `models.py` (perfil y serie), una migración, `management/commands/fiscal_series.py`, `fiscal/builder.py` y `fiscal/data.py` (ubigeo y anexo), `test_fiscal_tenant_prod.py`; frontend `admin/settings/integrations/**` (sólo SUNAT) | Tras 02. **Datos del propietario** para cerrarla en producción, no para escribirla |
| **FISCAL-PROD-06** · afectación por producto | FP-5, pasos 1 a 4: modelo, congelado por línea, revisión, bloqueo `pending_review`. Sólo gravado en el XML; 20 y 30 siguen fallando cerrado | `models.py` (`Product`, `OrderItem`), una migración, `tax_services.py`, `checkout_services.py` (`_tax_snapshot_fields`), `pos_services.py` (cálculo de totales), `capabilities.py` (una entrada), `product_tax_review.py` y su vista (nuevos), `test_tax_affectation.py`; frontend `admin/products/tax-review/**` | Tras 05. **Choca** con `import_formats.py`: la columna «Afectación» se mapea en una subfase **después** de LABEL-MIGRATION-01 |
| **FISCAL-PROD-07** · exonerado e inafecto en el XML | FP-5, paso 5: generador, reglas, resumen y PDF (FISCAL-PRINT-EXO) | `fiscal/builder.py`, `fiscal/rules.py`, `fiscal/data.py`, `fiscal_summary_services.py`, `fiscal_pdf_services.py`, `fiscal/representation.py`, tests | Tras 06, si el propietario confirma que existen esas ventas (Q6) |
| **FISCAL-PROD-08** · NC parcial y boletas | NC parcial por línea (motivos 07 y 09), un tope de NC por original, NC/ND de boleta en el RC, baja de boleta (`ConditionCode 3`), RC por sucursal (H-08), plazo de 7 días, H-12 (`discount_source` en la web) | `fiscal_note_services.py`, `fiscal_summary_services.py`, `fiscal/summary.py`, `fiscal_summary_views.py`, `checkout_services.py` (una línea), tests | Tras 03. H-12 puede adelantarse a 02 si el propietario lo prioriza |
| **FISCAL-PROD-09** · normativa 2027 | Releer las reglas 24.04.2026, los catálogos 01/10/11/51 y la base del plazo de baja desde el 1/1/2027, con fecha de corte en el código | `fiscal/rules.py`, `fiscal/data.py`, `fiscal_void_services.py`, `docs/sunat-cpe-requisitos.md` | Antes de diciembre de 2026 |
| **FISCAL-PROD-10** · salida a producción | Preflight GO con datos reales, techo de instalación a `production`, una factura y una boleta de prueba **autorizadas por el propietario**, seguimiento de 7 días | operación y documentación, sin código | **BLOCKED**: datos y orden del propietario |

**Implementables ya, con seguridad:** FISCAL-PROD-02 y FISCAL-PROD-04, en paralelo. Ninguna requiere datos
del propietario ni toca producción, y las dos se prueban con proveedores inyectados.

---

## 7. Pruebas previstas

| Prueba | Fase |
|---|---|
| Con la emisión apagada, `POST fiscal-document/`, nota, baja y resumen no crean filas ni avanzan `next_number` (PostgreSQL) | 02 |
| `ReadTimeout`, 5xx y respuesta ilegible en `sendBill` → `SUBMISSION_UNKNOWN`; `submit` se niega; sólo reconciliar | 02 |
| `ConnectTimeout` → `SUBMISSION_ERROR`, y un reenvío con el mismo XML (hash igual) | 02 |
| Excepción del proveedor → intento cerrado, estado incierto, auditoría, y nada de reenvío a los 10 minutos | 02 |
| Un CDR con otro `ReferenceID` o RUC en `sendBill` no acepta y se audita | 02 |
| Una nota creada antes de una baja no se envía mientras la baja esté viva o aceptada | 02 |
| Un pedido pagado con claves TEST no recibe comprobante en producción; en BETA, sí | 02 |
| El CDR guardado en bytes reproduce su hash; el ZIP original reproduce el suyo; un CDR que no es UTF-8 se conserva | 03 |
| `fiscal_preflight` no escribe (sólo `SELECT`), no abre ningún socket, y su salida no contiene ningún secreto sembrado (clave SOL, contraseña del P12, clave privada) | 04 |
| Preflight NO-GO por cada causa (RUC distinto, certificado vencido, autofirmado, a menos de 30 días, serie ambigua, cola pendiente) | 04 |
| Credenciales por empresa: A no puede usar ni ver las de B; la fila sellada de A copiada a B no se abre; sin credenciales propias, producción falla cerrado aunque la instalación tenga | 05 |
| Firmar con un certificado vencido o de otro RUC → `FiscalConfigError` sin gastar correlativo | 05 |
| El conmutador exige las dos confirmaciones, el techo de instalación y el preflight; no se vuelve a BETA con documentos de producción | 05 |
| Las series de producción y de BETA con el mismo nombre no se mezclan; dos activas → fallo; `next_number` no se edita | 05 |
| IGV incluido en el precio final: 118,00 → 100,00 + 18,00; 59,90 × 2 → 101,53 + 18,27; total nunca alterado; sumar encima es imposible (propiedad para 0,01…10 000) | 06 (regresión de C21) |
| Carrito mixto 10 + 30: el total igual al cobrado, el IGV sólo sobre la línea gravada, el congelado por línea | 06 |
| `pending_review` bloquea la cotización y la emisión, sin gastar correlativo; la venta histórica no cambia al revisar | 06 |
| La revisión de afectación deja auditoría con el valor anterior y el nuevo; sin capacidad → 403 | 06 |
| Descuento por línea (`00`) y global (`02`) con afectación mixta; `AllowanceTotalAmount` sigue omitido | 07 |
| NC parcial: la suma de NC ≤ el original; una segunda NC total → fallo; NC/ND de boleta en el RC | 08 |
| RC: boletas de otra sucursal invisibles; aviso a 6 días y bloqueo a 7 sin decisión | 08 |
| Baja: el plazo cambia de base el 1/1/2027 (reloj fijado a ambos lados) | 09 |

---

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Un reenvío tras timeout duplica un comprobante en SUNAT | FP-4; consulta encendida junto con la emisión |
| Las credenciales de una empresa firman los documentos de otra | FP-2: alcance por empresa, RUC triple, sin respaldo de instalación |
| Producción rechaza la firma SHA-256 (N6) | Primera emisión real controlada (FISCAL-PROD-10); el perfil de firma ya es configurable en `signing.py` |
| Afectación mal clasificada → IGV indebido o no declarado | FP-5: `pending_review` bloquea, la revisión la hace una persona con su contador, lo histórico no se toca |
| Cambios del 1/1/2027 a mitad de la operación | FISCAL-PROD-09 antes de diciembre |
| Certificado vencido sin aviso deja la caja sin comprobantes | Alerta a 60/30 días (fase 04) y bloqueo al firmar (fase 05) |
| Boletas fuera del plazo de 7 días por falta de tarea programada | Alerta (04); tarea programada sólo de generación, con el envío confirmado por una persona hasta que se decida otra cosa (Q8) |
| Comprobante real para un pago de prueba | H-11 en la fase 02 |
| Choque de archivos con LABEL, MIGRATION o ACCOUNTING | Tabla del §6; migraciones numeradas al fusionar, de una en una |
| Pérdida de `APP_CONFIG_ENCRYPTION_KEY` → credenciales ilegibles | Ya documentado en DEC-INT-01; el preflight lo comprueba; se reescriben (no hay pérdida fiscal) |

---

## 9. Preguntas al propietario

1. **Q1 · Emisor.**
   - RUC, razón social exacta, nombre comercial, domicilio fiscal y ubigeo del piloto.
   - ¿Hay locales declarados como establecimientos anexos? ¿Con qué código?
2. **Q2 · SOL.**
   - ¿Ya figura como emisor electrónico «desde los sistemas del contribuyente»?
   - ¿Crea un usuario secundario sólo para la emisión?
3. **Q3 · Certificado.**
   - ¿Qué prestador ROPS? ¿O un Certificado Digital Tributario gratuito, si califica?
   - ¿Ya está registrado en SOL?
   - ¿Quién recibe el aviso de vencimiento?
4. **Q4 · Series.**
   - ¿Qué nombres en producción (`F001`/`B001` o por local)?
   - ¿Una serie por sucursal o una por empresa?
5. **Q5 · Conmutador.** ¿Acepta la doble confirmación (dos MASTER, o el mismo con espera y preflight)?
   ¿Quién es el segundo?
6. **Q6 · Afectación.**
   - ¿Vende productos o servicios exonerados o inafectos?
   - ¿Quién revisa el catálogo, con qué contador y en qué plazo?
   - Mientras tanto, ¿se bloquea la emisión de los productos sin revisar, como propone FP-5?
7. **Q7 · Boletas.** ¿Por resumen diario (7 días) o por envío individual?
8. **Q8 · Automatización.**
   - ¿Se emite la factura automáticamente al pagar, o la prepara una persona (FISCAL-FLOW-01)?
   - ¿Se envía el resumen diario solo, o lo confirma una persona?
9. **Q9 · Pasarela.** ¿Se confirma que con claves TEST no se emite ningún comprobante en producción?
10. **Q10 · Primera emisión real.** ¿Qué venta real, qué día y quién la autoriza (FISCAL-PROD-10)?
11. **Q11 · NC parcial.** ¿Se necesita ya, por las devoluciones parciales, o basta la NC total hasta la
    fase 08?
