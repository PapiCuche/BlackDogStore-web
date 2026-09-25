# ADR — Dominio fiscal peruano (C2.2A.1)

Decisiones tomadas al construir la emisión de facturas electrónicas, con su
motivo y lo que se descartó. Están juntas porque se sostienen entre sí: cambiar
una obliga a revisar las demás.

---

## ADR-1 · `FiscalDocument` es un dominio aparte de `SalesNote`

**Decisión.** Un modelo nuevo, con su propia tabla, su propio ciclo de vida y su
propia numeración.

**Por qué.** `SalesNote` declara en su propia documentación que es un documento
INTERNO, y esa afirmación es lo que permite que se pueda anular sin
consecuencias, que su correlativo no tenga efecto tributario y que su PDF lleve
impreso «no válido como comprobante electrónico SUNAT».

Un comprobante electrónico es lo contrario en las tres cosas. Reutilizar
`SalesNote` habría dado validez fiscal a un papel que no la tiene — y peor, la
habría dado de forma invisible: el mismo modelo significando dos cosas según una
bandera.

**Descartado.** Añadir campos fiscales a `SalesNote` y un `is_fiscal`. Habría
obligado a que cada consulta existente recordara filtrarlo, y la que se olvidara
mezclaría documentos internos con comprobantes ante SUNAT.

**Consecuencia.** Una venta puede tener nota interna Y factura. Son dos papeles
distintos y el panel los muestra por separado.

---

## ADR-2 · `FiscalSeries` no es `InternalSequence`

**Decisión.** Un contador fiscal independiente, con la misma forma que el interno
—el número es un número, no una cadena que se parsea— pero en su propia tabla.

**Por qué.** Un correlativo fiscal entregado está gastado para siempre. Si el
documento acaba rechazado, ese número NO vuelve al contador: reciclarlo produciría
dos documentos que alguna vez compartieron identificador ante SUNAT.

`InternalSequence` no tiene esa obligación. Mezclarlos significaría que un hueco
en la numeración fiscal podría venir de un documento interno, y ante SUNAT un
hueco hay que poder explicarlo.

**Descartado.** Añadir `document_type='invoice'` a `InternalSequence`. Un solo
`select_for_update` serviría a los dos dominios y una emisión interna bloquearía
una fiscal sin motivo.

**Alcance.** La serie es única por `(empresa, tipo, serie, entorno)`. No global:
dos empresas pueden usar `F001` legítimamente, y la `F001` de pruebas no es la de
producción.

---

## ADR-3 · El dominio no conoce SOAP

**Decisión.** Una interfaz `FiscalProvider` que devuelve `ProviderResult`, un
objeto plano con el resultado ya interpretado. `SunatSoapProvider` la implementa.

**Por qué.** Mañana esto puede ir por SUNAT directo, por un PSE o por un OSE. Si
el dominio supiera de sobres SOAP, cambiar de proveedor obligaría a tocar el
dominio — y el dominio es justo lo que no debe cambiar cuando cambia el
transporte.

**Consecuencia.** Nada fuera de `provider.py` ve un `faultstring`, un
`applicationResponse` ni un árbol XML de SUNAT.

**No se implementan adaptadores ficticios.** Existe el de SUNAT y un doble para
pruebas. Un adaptador de PSE vacío marcado como «listo» sería una promesa falsa.

---

## ADR-4 · Un error de transporte NO es un rechazo

**Decisión.** `ProviderOutcome` separa `TRANSPORT_ERROR` y `UNKNOWN_RESPONSE` de
`REJECTED`. Un código que no encaja en ningún rango conocido es
`UNKNOWN_RESPONSE`, nunca rechazo.

**Por qué.** Un timeout, un 500 o una conexión cortada dejan la venta en estado
INCIERTO: puede que SUNAT la haya recibido. Tratar eso como rechazo llevaría a
emitir un segundo comprobante por una venta que quizá ya está registrada, y el
correlativo del primero ya está gastado.

**Cómo se sale de cada uno.** De un rechazo, corrigiendo y emitiendo otro
documento. De un error de transporte, reintentando EL MISMO: misma serie, mismo
correlativo, mismo XML firmado. Lo único nuevo es una fila de intento.

**Rangos.** 0100-1999 excepción reintentable · 2000-3999 rechazo · 4000+
observación. Del Manual del programador y de la hoja de códigos de retorno.

---

## ADR-5 · Los artefactos se guardan enteros, con su hash

**Decisión.** `signed_xml` y `cdr_xml` en columnas de texto, más su SHA-256.

**Por qué.** El XML firmado ES el comprobante: sin él no se puede reimprimir ni
demostrar qué se declaró. El CDR es la prueba de la aceptación. Son unos pocos
kilobytes de texto cada uno.

**Descartado.** Guardarlos en el sistema de archivos. Habría metido rutas locales
en la base de datos, habría separado el documento de su propia copia de
seguridad, y en una plataforma multiempresa habría añadido una superficie de
permisos nueva para un dato que ya está protegido por la fila que lo contiene.

**Los hashes** existen para correlacionar y para detectar alteración sin comparar
cuerpos. El del envío se calcula sobre el ZIP y **nunca sobre el sobre SOAP**,
que lleva la Clave SOL dentro.

---

## ADR-6 · Los secretos no entran en el modelo

**Decisión.** `FiscalDocument` y `FiscalSubmissionAttempt` no tienen ninguna
columna para la Clave SOL, la contraseña del certificado ni la clave privada. El
proveedor las recibe por constructor.

**Por qué.** Una columna existe para llenarse. Un modelo con un campo
`sol_password` acaba teniéndolo poblado, apareciendo en un serializer, en un
volcado o en una bitácora.

**El endpoint tampoco viaja en una petición.** `SunatSoapProvider` lo recibe por
constructor y exige HTTPS. Un inquilino que pudiera elegir la URL de SUNAT
tendría un SSRF servido.

**Producción no se declara** en `provider.py`. Está en la documentación, no en
una constante del código: habilitarla no puede estar a un cambio de literal de
distancia.

---

## ADR-7 · Los esquemas se versionan; no se descargan

**Decisión.** `UBL-Invoice-2.1.xsd` y sus dependencias viven en el repositorio,
con su procedencia y el SHA-256 del paquete original.

**Por qué.** Un test que descarga un XSD depende de que SUNAT esté disponible, de
que la URL no cambie y de que el contenido de hoy sea el de ayer. Las tres cosas
convierten un fallo de red en un test rojo que no señala ningún defecto.

**Se recortó** `maindoc/` a la factura: los otros 63 documentos raíz no se emiten.
`common/` se conserva entero porque la cadena de imports lo atraviesa.

---

## ADR-8 · El generador es una función pura

**Decisión.** `store/fiscal/` no importa Django. Recibe datos planos, devuelve
bytes.

**Por qué.** Permite validar contra el esquema de SUNAT sin levantar una base de
datos, y —más importante— impide que el generador «arregle» un importe
consultando algo. El dinero lo decidió C2.1 en el momento de la venta.

**Consecuencia.** `fiscal_services.py` es el único que traduce un `Order` a esos
datos, y ahí es donde se comprueba que el snapshot de C2.1 esté completo.

---

## ADR-9 · Nada de red dentro de la transacción

**Decisión.**

    transacción:  crear documento, reservar correlativo, congelar snapshot
    commit
    después:      generar, firmar, enviar, registrar el resultado

**Por qué.** Mantener abierta una transacción esperando a SUNAT bloquearía la
fila del contador durante segundos de red, y un fallo de SUNAT podría deshacer
una venta ya cobrada.

**Consecuencia buscada.** Si la transmisión falla, la venta sigue pagada, el
comprobante queda pendiente y se puede reintentar. No se genera otro correlativo.


---

# ADR — C2.2A.1B (superficie y endurecimiento)

Seis decisiones más, tomadas al convertir el dominio en producto. Salieron de
auditar cinco invariantes ANTES de exponer nada, y las cinco tenían un defecto.

---

## ADR-10 · El ambiente lo decide el servidor, y producción falla cerrado

**Decisión.** `FISCAL_ENVIRONMENT` en la configuración del servidor.
`resolve_environment()` levanta ante cualquier valor distinto de `beta`.

**Por qué.** La consulta de series no filtraba por ambiente: una serie de
producción se elegía si era la más antigua. Y no existía ninguna configuración
fiscal, así que «producción» estaba a un literal de distancia.

Que el fallo sea explícito evita que «funcione por accidente» el día que alguien
copie una variable de entorno de un sitio a otro. Crear una `FiscalSeries` de
producción a mano tampoco basta: el ambiente no lo decide el contenido de una
tabla.

**El endpoint tampoco viaja en una petición.** Sale de una tabla del código a
partir del ambiente. Un inquilino que pudiera escribir la URL de SUNAT tendría
una petición saliente arbitraria desde nuestro servidor.

---

## ADR-11 · La serie se resuelve; no se toma la primera

**Decisión.** `resolve_series()` filtra por empresa, ambiente y sucursal,
prefiere la serie de la sucursal sobre la de empresa, y **falla ante ambigüedad**.

**Por qué.** Antes era `.filter(...).order_by('pk').first()`. Eso convierte «el
id más bajo» en política tributaria.

**Ambigüedad = fallo, y es lo importante.** Un desempate improvisado —«la más
antigua», «la última»— se convierte en la política de la empresa sin que nadie la
haya decidido, y sale a la luz cuando SUNAT recibe dos documentos de series
distintas para el mismo mostrador. Que el sistema diga «hay dos y no sé cuál» es
peor experiencia y mejor comportamiento.

---

## ADR-12 · Un rechazo es terminal para el botón genérico

**Decisión.** El modelo sigue admitiendo un segundo documento para la misma
venta; `get_or_create_fiscal_document` se niega.

**Por qué.** El queryset excluía `REJECTED`, así que volver a pulsar «Emitir»
creaba otro documento y gastaba otro correlativo. **SUNAT considera USADO el
número de un documento rechazado**: cada clic distraído dejaba un hueco que hay
que explicar.

La flexibilidad del modelo hace falta para el flujo de corrección de una fase
futura. Lo que no puede pasar es que se use por accidente.

---

## ADR-13 · «Aceptada» exige una constancia

**Decisión.** Un código 4000+ significa «aceptada con observaciones» **sólo
dentro de un CDR**. En un `faultstring`, sin constancia, es
`UNKNOWN_RESPONSE`.

**Por qué.** La constancia es la prueba de que SUNAT registró el comprobante. El
mismo número llegando en un fault no demuestra registro alguno, y afirmarlo
pondría «ACEPTADA POR SUNAT» en una pantalla sobre un documento que quizá no
existe para ellos.

---

## ADR-14 · El intento se reserva antes de la red

**Decisión.** `_claim_attempt` crea la fila con `finished_at` nulo y cierra la
transacción; la llamada a SUNAT ocurre después. Un segundo envío ve el intento en
curso y no llama.

**Por qué.** `attempts.count() + 1` se calculaba justo antes de la red: dos
peticiones simultáneas obtenían el mismo número y **ambas llamaban a SUNAT**. Dos
transmisiones del mismo comprobante pueden dejar dos registros allí y sólo uno de
nuestro lado.

**Con plazo de abandono** (10 minutos). Sin él, un proceso caído a mitad de envío
dejaría el comprobante bloqueado para siempre y sin forma de desbloquearlo desde
el producto.

Verificado contra PostgreSQL real, contando las llamadas al proveedor con una
demora deliberada para que la ventana de carrera fuese real.

---

## ADR-15 · Una venta con descuento no se emite

**Decisión.** Se falla cerrado ante `discount_amount > 0`, y una regla local
impide que un importe de línea difiera de `cantidad × valor unitario`.

**Por qué.** C2.1 vende con descuento y el generador no sabe declararlo. Una
venta de 2 × 118,00 con 18,00 de rebaja producía una línea que decía «cantidad 2,
valor unitario 100,00, importe 184,75». La aritmética no cerraba y el descuento
no aparecía en ninguna parte: SUNAT habría recibido un precio unitario que nadie
cobró.

**El XSD lo aceptaba**, porque no comprueba aritmética. Hay un test que lo
demuestra, y existe para que nadie confunda «pasa el esquema» con «es correcto».

Un descuento se declara con `cac:AllowanceCharge`. Escribirlo exige leer su
semántica en la guía de SUNAT: inventarla sería la misma clase de error, sólo que
más difícil de ver.

**FACTURA CON DESCUENTO queda PENDIENTE**, declarado.

---

# ADR — ERP-FISCAL-1

## ADR-16 · El material de firma se carga en memoria; PKCS#12 XOR PEM

**Decisión.** El certificado de firma se resuelve en `fiscal_config` desde UNA
de dos fuentes, nunca ambas: un contenedor PKCS#12 (`FISCAL_CERT_P12_PATH` +
`FISCAL_CERT_P12_PASSWORD`) o el par PEM heredado (`FISCAL_CERT_PEM` +
`FISCAL_KEY_PEM`). Si están configuradas las dos a la vez, se **falla cerrado**
en vez de elegir una. El `.p12` se abre y se convierte a PEM **en memoria**
(`store/fiscal/certificate.py`, API `cryptography`): nunca se escribe la clave a
disco ni se invoca `openssl -passin`. La contraseña del contenedor y la Clave
SOL nunca aparecen en un error, log, repr ni respuesta.

**Por qué.** El CDT del contribuyente llega como `.p12`; la firma trabaja con
PEM. Convertir escribiendo `key.pem` en `/tmp` o pasando `-passin pass:...` deja
material sensible donde otro proceso lo lee. Mantener dos fuentes simultáneas
ambiguas invita a que producción firme con el certificado equivocado; una config
ambigua es un error de servidor, no un desempate silencioso.

**Alcance.** La ruta la da el entorno; el código no la busca por el disco. El
par PEM heredado sigue siendo válido para tests, BETA y CI. Producción sigue
deshabilitada por ADR-10; esto sólo prepara la carga del certificado, no habilita
emisión real.

---

# ADR — ERP-FISCAL-2

## ADR-17 · El snapshot tributario es la única autoridad; la reconciliación es determinista

**Decisión.** Los importes del XML se cuadran contra el snapshot de la Order
(`taxable_amount`, `tax_amount`, `total`), fijado al pagar. **Nunca** se modifican
Order ni OrderItem para que el XML reconcilie. Cuando la suma de los brutos de
línea no reparte limpiamente sobre la base imponible del snapshot, el déficit se
asigna por **resto mayor**: se toma el piso (`ROUND_DOWN`) de cada base ideal
`bruto / (1 + tasa)` y se reparte un céntimo, uno a uno, a las líneas con mayor
parte fraccionaria, con desempate estable por índice. El déficit está **acotado en
`[0, n]` céntimos** (n = número de líneas); la cota se **demuestra**, no se supone,
y fuera de ese rango se **falla cerrado** (`ReconciliationError` → `FiscalError` →
400 de dominio, sin gastar correlativo).

**Por qué.** El snapshot es lo que el cliente pagó y lo que la contabilidad ya
registró; es la verdad monetaria. Reescribir OrderItem para que el XML «cuadre»
falsificaría esa verdad. El reparto por resto mayor es el único que garantiza a la
vez que Σ bases = base imponible **al céntimo exacto** y que cada línea queda tan
cerca de su base ideal como permite el céntimo. Como el déficit es un múltiplo
entero del céntimo, la igualdad no depende de la precisión de la división Decimal.
El IGV de línea se deriva como `bruto − base`, de modo que Σ IGV cuadra por
construcción y no hay una segunda reconciliación que pudiera contradecir a la
primera. La cota `[0, n]` es la que separa «un redondeo normal» de «un snapshot
corrupto»: sin ella, un descuadre grande se disimularía repartiendo céntimos que
no existen.

**Alcance.** Sólo FACTURA gravada, al contado, sin descuento. EXEMPT/UNAFFECTED y
`discount_amount > 0` siguen **PENDIENTE** y fallan cerrado (ADR-15). El caso
`59.90 × 2` (base 101,53 / IGV 18,27) lo acepta SUNAT BETA.

---

## ADR-18 · Precisión por campo y modo de redondeo explícito

**Decisión.** Cada campo se cuantiza a la precisión que exige SUNAT, con
formateadores separados en el generador: valor unitario ex-IGV (`cbc:PriceAmount`)
a **n(12,10)**; importes, IGV y totales a **n(12,2)**; tasas (`cbc:Percent`) a
**n(3,5)**. Todo redondeo monetario usa **`ROUND_HALF_UP` explícito**, nunca el
`ROUND_HALF_EVEN` que `Decimal` aplica por defecto. El valor unitario se calcula
desde la base de línea ya reconciliada, a 10 decimales, de modo que
`round(cantidad × unitario, 2) == importe_línea`.

**Por qué.** SUNAT valida la aritmética línea a línea: un `PriceAmount` redondeado
a 2 decimales rompe `cantidad × unitario = importe` cuando la base no es divisible
(59,90 → base 50,76/50,77 → unitario con más de 2 decimales). Los 10 decimales lo
absorben. El `ROUND_HALF_EVEN` por defecto de Decimal redondea 0,005 a 0,00 o a
0,01 según la paridad del dígito anterior: para dinero eso es un céntimo que
aparece y desaparece sin regla visible. Hacer el modo explícito en un solo sitio
(`money()`) evita que un `quantize` olvidado herede el default. El formateador
recorta los ceros finales del unitario a un mínimo de dos decimales y prohíbe la
notación científica, para que dos ejecuciones den byte a byte el mismo XML
(la firma no perdona un espacio de diferencia).

---

## ADR-19 · El XML firmado es la autoridad de representación (FISCAL-03/04)

**Decisión.** El PDF y el QR se construyen **exclusivamente** desde el XML firmado,
no desde la fila de la venta ni desde OrderItem/Product. Un módulo puro de
sólo-lectura (`store/fiscal/representation.py`,
`parse_signed_invoice_for_representation()`) lee el XML firmado con el parser
endurecido (`xmlsafe`, ADR de FISCAL-1) y devuelve la fecha legal (`cbc:IssueDate`
+ `cbc:IssueTime`), las líneas, los totales y el `DigestValue`. La fecha del QR y
del impreso es la del XML firmado (**FISCAL-03**), no el `paid_at` ni el timestamp
de la fila. Los importes y las líneas del impreso salen del XML (**FISCAL-04**), no
de un OrderItem que pudo cambiar después de firmar.

**Por qué.** Una vez firmado, el comprobante es inmutable: el `DigestValue` del QR
tiene que ser el del XML que SUNAT recibió, y la fecha legal es la que va dentro de
la firma, no la del reloj de la aplicación. Si el PDF leyera OrderItem, editar la
venta tras firmar produciría un impreso que miente respecto del XML sellado —y del
CDR—. Leer siempre del XML firmado hace que el impreso no pueda contradecir al
comprobante. El parser es el endurecido y de sólo-lectura porque un XML firmado,
aunque lo generamos nosotros, se trata como entrada no confiable: generar el PDF
**no** debe poder mutar el documento (SHA del `signed_xml` estable), y así se
prueba de forma adversaria (mutar OrderItem tras firmar → impreso sin cambios).

---

# ADR — ERP-FISCAL-3

## ADR-20 · Reconciliar consulta el CDR ya emitido; nunca reenvía a ciegas

**Decisión.** Cuando un envío queda incierto (`SUBMISSION_ERROR`: un timeout, un
corte —no sabemos si SUNAT lo recibió—), se reconcilia CONSULTANDO el CDR del
comprobante **ya emitido** (`getStatusCdr`, por RUC/tipo/serie/número que se
derivan del documento local). Si SUNAT ya tiene un veredicto, se aplica; si no,
el comprobante queda NO TERMINAL y **no se reenvía nada**. Reconciliar **nunca**
reenvía, reserva otro correlativo ni crea otro documento. La red va FUERA de
transacción; el estado terminal se fija bajo un bloqueo breve (`select_for_update`)
que **relee** el estado y no envuelve ninguna llamada externa. Un aceptado no se
re-consulta (idempotente); un rechazo no se convierte en aceptado por una consulta;
un CDR que contradice un estado terminal ya guardado es un CONFLICTO que se audita,
no una sobrescritura.

**Por qué.** Un timeout deja la venta en un estado que sólo SUNAT conoce. Reenviar
«por si acaso» produciría un segundo documento por una venta que quizá ya está
registrada, y un correlativo gastado no se recicla. La única acción segura es
PREGUNTAR por el identificador que ya se emitió, no emitir otro. «No consta ahora»
no es «nunca se recibió»: por eso no dispara un reenvío automático. La red fuera de
la transacción evita bloquear la fila del contador durante segundos de espera
(misma disciplina que el envío, ADR-9/ADR-14); releer bajo bloqueo antes de aplicar
es lo que hace que dos reconciliaciones —o una reconciliación y un envío—
simultáneas no se pisen.

**Autoridad y modelo.** Reconciliar NO crea `FiscalSubmissionAttempt` (ese modelo
es historial de ENVÍO); su rastro va a `AdminAuditLog` (actor, empresa, documento,
acción, estado anterior/posterior, código SUNAT crudo). Exige `sales.fiscal.issue`
y no `.view`: llevar un comprobante a ACEPTADO/RECHAZADO es declarar que el asunto
quedó zanjado con SUNAT, del mismo tenor que emitir, no de consultar. Sin migración.

## ADR-21 · `getStatusCdr` ≠ `getStatus(ticket)`: dos servicios, dos contratos

**Decisión.** Se modelan como operaciones y tipos SEPARADOS, aunque compartan
infraestructura (WS-Security, POST acotado, parser endurecido y **un único
intérprete de CDR**). `getStatusCdr` vive en `billConsultService` y recupera el CDR
de un comprobante emitido por su identificador; `getStatus(ticket)` vive en
`billService` y da el estado de un proceso asíncrono (Resumen/Baja) por ticket, con
los códigos oficiales `0`/`98`/`99`. No se implementó ningún `getStatusCpe`: no es
una operación real del SEE-Del Contribuyente.

**Por qué.** El Manual del programador los publica como servicios distintos con
entradas y estados distintos; mezclarlos en un `sunat_request` genérico haría que
un cambio en uno arrastrase al otro. El Manual **no publica** la tabla de códigos de
`getStatusCdr`, así que la reconciliación se apoya en lo verificable —hay CDR o no
lo hay— y en el `ResponseCode` del propio CDR, no en códigos inventados; el código
crudo de `statusCode` se conserva siempre como evidencia. `getStatus(ticket)` se
implementa hoy sólo como contrato con mocks, como fundación de FISCAL-4: no se emite
ningún resumen todavía.

## ADR-22 · Consultar en producción y emitir en producción son capacidades separadas

**Decisión.** La consulta/reconciliación en línea (`getStatusCdr`) tiene su **propia
bandera** (`FISCAL_CONSULT_ENABLED`, apagada por defecto) y su propio resolutor
(`resolve_consult_provider`), independientes de la emisión (`resolve_environment` /
`resolve_provider`, fijados en BETA). `billConsultService` sólo existe en producción
según el Manual, así que su URL se declara como constante en la capa de proveedor,
pero **no se invoca en esta fase**: el resolutor **falla cerrado** salvo que la
bandera esté encendida —lo que ocurre en una fase de producción, con su revisión—.

**Por qué.** Necesitábamos conocer la frontera sin abrir un agujero: encender la
consulta **no** debe habilitar `sendBill` producción, ni al revés. Reutilizar una
sola bandera de «producción» para ambas cosas dejaría la emisión real a un
descuido de configuración de distancia. Dos capacidades, dos banderas, dos
resolutores: `resolve_environment()` sigue levantando ante cualquier ambiente que
no sea BETA pase lo que pase con la consulta. Toda la reconciliación se prueba con
un proveedor inyectado; nunca toca la red en ERP-FISCAL-3.

## ADR-23 · La firma del CDR: integridad comprobable, autenticidad PENDIENTE

**Decisión.** Se audita la firma del CDR con `signxml` (biblioteca madura, la misma
que firma; nunca XMLDSig a mano): se comprueba la **integridad** matemática contra
el certificado EMBEBIDO, pero la **autenticidad** queda `unverified`. `inspect_cdr_
signature` **nunca** devuelve `TRUSTED`, y la comprobación **no** se enchufa a la
aceptación fiscal. **CDR-TRUST-01 (autenticidad) = PROPUESTA/PENDIENTE.**

**Por qué.** El Manual afirma que las constancias van firmadas por SUNAT, pero SUNAT
**no publica** un ancla de confianza (su certificado raíz) que permita validar la
cadena de forma programática. Sin ancla, la firma «valida» contra el certificado que
el propio CDR trae —que un atacante puede autofirmar—: eso es integridad, no
autenticidad, y confundirlas sería marcar como «de SUNAT» un XML que cualquiera pudo
producir. Se implementa lo que SÍ se puede hacer con honestidad (integridad, con la
librería madura que además mitiga el *signature wrapping* al verificar QUÉ se
firmó) y se clasifica con honestidad lo que no. El CDR crudo se conserva siempre,
válida o no la firma: la evidencia no se destruye.

---

# ADR — ERP-FISCAL-4

## ADR-24 · El Resumen Diario es una entidad propia, con alcance por empresa y día

**Decisión.** El Resumen Diario de Boletas (RC) se persiste en dos modelos nuevos
(`FiscalDailySummary` y su `FiscalDailySummaryDocument`), no en un JSON dentro de
otra fila. Se agrupa por **empresa + ambiente + fecha de referencia** (la fecha de
emisión de las boletas), no por sucursal. La pertenencia «qué boletas fueron en
qué resumen» se **congela** en la tabla puente al armar el resumen. Una boleta
está, como mucho, en un resumen NO superado (índice único parcial); un resumen
rechazado marca sus filas `superseded` y así libera sus boletas para un resumen
nuevo. Migración `0085`, puramente aditiva.

**Por qué.** Identidad (`RC-yyyyMMdd-NNN`), correlativo único, ticket, estado y la
membresía de boletas tienen que poder restringirse, consultarse, bloquearse y ser
idempotentes: eso es esquema, no un diccionario (§11). El alcance por empresa
—y no por sucursal— es fiel a SUNAT: el RC es un reporte del RUC y admite varias
series/establecimientos; el aislamiento operativo por sucursal se hace en permisos,
no fragmentando el reporte. Congelar la pertenencia evita reconstruirla meses
después por `issue_date = X`, que falla cuando hay varios resúmenes por día,
bloques de 500 o anulaciones.

## ADR-25 · Un ticket no es una aceptación; la red va fuera de la transacción

**Decisión.** `sendSummary` devuelve un TICKET que se **persiste antes de
cualquier consulta** (§8); el veredicto llega después con `getStatus(ticket)`. El
envío se **reclama** bajo un bloqueo breve (`submitting_since`) antes de salir a la
red, y la red NO se hace con ningún bloqueo de fila sostenido (§48/§50): claim →
red → finalize. Dos envíos simultáneos no crean dos tickets; un fallo de transporte
deja el resumen reintentable **sin ticket inventado** (§49); `98 en proceso` deja el
resumen ENVIADO para volver a consultar. El correlativo del RC se reserva bajo
bloqueo con la restricción única de respaldo, y ante una carrera se re-selecciona
(las boletas que otro proceso ya tomó quedan excluidas).

**Por qué.** El resumen es asíncrono: confundir «recibido para procesar» con
«aceptado» pondría por buena una venta que SUNAT aún no registró. Perder el ticket
dejaría el proceso remoto irrastreable, así que se guarda antes de nada. Mantener
una transacción abierta durante la espera de SUNAT bloquearía filas durante
segundos; reclamar antes de la red es lo que impide el doble envío sin sostener el
bloqueo. Es la misma disciplina que el envío de la factura (ADR-9/ADR-14/ADR-20).

## ADR-26 · El Resumen se acepta o se rechaza entero; el CDR no toca las boletas

**Decisión.** El CDR del resumen se valida contra el RESUMEN (su `ReferenceID` es
el del RC, no una boleta) y su veredicto es del resumen completo (§52): SUNAT no
hace procesamiento parcial de un RC. Un rechazo NO anula las boletas —marca sus
filas `superseded` para poder informarlas en un resumen nuevo—; una aceptación NO
cambia el estado individual de la boleta. La condición de «informada en un resumen
aceptado» la lleva la existencia de una fila de inclusión no superada en un resumen
aceptado, distinta de un «CDR individual aceptado» (que sería el estado propio de
la boleta vía `sendBill`, un camino que esta fase no usa). El `ConditionCode` es
`1` (Adicionar); `2`/`3` no se usan como parche.

**Por qué.** Tratar el rechazo de un resumen como anulación de sus boletas
destruiría comprobantes que se entregaron y son válidos; el rechazo pertenece al
proceso de reporte, que se corrige reenviando un resumen. Y marcar la boleta como
«aceptada» por la aceptación del resumen colapsaría dos hechos distintos —el
comprobante y su reporte— en una sola columna (§41/§43): se mantienen separados
para poder representar «boleta entregada, resumen aún en proceso».

**XSD del Resumen (nota).** El esquema `SummaryDocuments-1` (árbol UBL 2.0 del
paquete de SUNAT) no está incluido en el repositorio y no pudo obtenerse en este
entorno (la descarga del ZIP no fue posible). La estructura del generador se apoya
en la Guía del Resumen Diario y en ejemplos oficiales, se comprueba con pruebas de
estructura y se confirma contra BETA. La boleta sí valida contra el
`UBL-Invoice-2.1.xsd` incluido. Bundlear el XSD 2.0 queda como deuda declarada.

---

# ADR — ERP-FISCAL-4.1

## ADR-27 · La identidad del resumen: correlativo en el cbc:ID y fecha de generación

**Decisión.** El `cbc:ID` del Resumen Diario es `RC-<YYYYMMDD>-<correlativo>` —con
el correlativo dentro— y su fecha es la de **GENERACIÓN** del resumen, no la de
emisión de las boletas. El nombre del archivo (`<RUC>-RC-<YYYYMMDD>-<correlativo>`)
usa la misma fecha y el mismo correlativo, y el `cbc:ID` coincide con la base del
nombre. El correlativo es único por `(empresa, ambiente, fecha de generación)`. La
fecha de emisión de las boletas informadas va, aparte, en `cbc:ReferenceDate`.

**Por qué.** Las reglas de validación vigentes lo imponen: **2210** exige el formato
`RC-fecha-correlativo`, **2220** que el `cbc:ID` coincida con el nombre del archivo,
y **2346** que la fecha del nombre sea la de generación. La prosa de la guía de
enero-2018 que muestra `RC-20180123` (sin correlativo) es una inconsistencia del
propio documento —contradicha por sus ejemplos XML (`RC-20171227-00001`)— y
superada por las reglas. Sin el correlativo en el `cbc:ID`, dos bloques de 500 del
mismo día tendrían identificadores idénticos: SUNAT no podría distinguirlos y el
`ReferenceID` del CDR sería ambiguo. Y usar la fecha de EMISIÓN en el id —como se
hacía— rompía la regla 2346 en cuanto un resumen se generaba en un día distinto al
de sus boletas (SUNAT admite hasta siete días para informarlas). La aceptación en
BETA de `RC-20260922-1` no era, por sí sola, prueba de la norma; la evidencia es la
hoja de reglas de validación.

## ADR-28 · Un envío transmitido sin ticket es INCIERTO, no un reintento seguro

**Decisión.** Si `sendSummary` transmite la petición pero no devuelve ticket
(timeout de lectura, corte tras enviar, 5xx, respuesta ilegible), el resumen pasa a
`SUBMISSION_UNKNOWN`: el resultado remoto es incierto —puede existir un ticket que
nunca recibimos—. Es un estado DISTINTO de `SUBMISSION_ERROR`, que queda reservado
para un fallo **demostrablemente no transmitido** (sólo la fase de conexión —un
`ConnectTimeout`—), que sí es seguro reintentar. Un `SUBMISSION_UNKNOWN` NO se
reenvía por el flujo normal, no inventa un ticket, no crea otro resumen, y sus
boletas quedan bloqueadas (no se re-informan). Recuperarlo es una decisión manual y
consciente.

**Por qué.** Reenviar a ciegas un resumen que quizá SUNAT ya encoló produce un
duplicado. La distinción no puede apoyarse en el nombre de una excepción: sólo un
fallo en la fase de CONEXIÓN prueba que el cuerpo no se transmitió; ante cualquier
otra cosa —incluida una respuesta rara— se asume incierto (§16). No hay un
`getStatusCdr` para un ticket que nunca recibimos, así que no existe recuperación
automática segura: el detalle expone `can_recover` en vez de `can_submit`, y la
auditoría del envío conserva el hash del XML firmado y la fecha de referencia para
poder demostrar exactamente qué ZIP pudo haberse enviado, sin secretos.

**Nota XSD (RC-XSD-01).** El paquete XSD `SummaryDocuments-1` (UBL 2.0) no pudo
incorporarse (el sitio de SUNAT devuelve 403 a descargas automatizadas y la fase
prohíbe *mirrors*). Una validación ESTRUCTURAL local —no el XSD oficial, y así se
declara— corre antes de firmar; la estructura se confirma además contra BETA.

**Corrección posterior (ERP-FISCAL-5B).** Lo de «no pudo incorporarse» era cierto de
`cpe.sunat.gob.pe`, pero no del todo: SUNAT publica el paquete completo en su host de
contenidos, y allí sí se descargó (está en `schemas/2.0/`, con su SHA-256). El
hallazgo, sin embargo, fue el contrario del esperado: ese `SummaryDocuments` es el
Resumen **por RANGOS** de 2012 —línea con `DocumentSerialID` y
`Start/EndDocumentNumberID`, sin `cac:Status`, sin `cbc:ConditionCode` y sin
adquirente— y aquí se emite el Resumen **por DOCUMENTO**, que ese paquete no trae.
Así que RC-XSD-01 sigue PARCIAL, pero por un motivo distinto y más preciso: el
esquema publicado NO es el que aplica, y adoptarlo rechazaría documentos correctos y
empujaría el generador a un formato superado. La validación estructural local se
mantiene como única red del Resumen.

Lo que el mismo paquete SÍ resolvió es la **Comunicación de Baja**:
`UBLPE-VoidedDocuments-1.0.xsd` es oficial y utilizable tal cual, y es lo que permitió
construir la baja de ERP-FISCAL-5B contra un esquema de verdad en vez de una
imitación. Ver `schemas/PROCEDENCIA.md`.

---

## ADR-29 · Una nota es un `FiscalDocument`, no una entidad nueva

**Decisión.** La Nota de Crédito (07) y la de Débito (08) son filas de
`FiscalDocument`, con un tipo propio y una FK `original_document` que apunta al
comprobante que corrigen. No hay tabla de notas.

**Por qué.** Una nota necesita EXACTAMENTE la misma tubería que un comprobante:
serie y correlativo fiscales, firma XML-DSig, validación XSD, envío, CDR,
reconciliación, PDF y QR. Una entidad separada habría duplicado esas siete cosas
o —peor— habría inventado una segunda forma de numerar y firmar que se
desincroniza con la primera en cuanto una cambie. Compartir el modelo hace que
enviar, reconciliar e imprimir una nota sean el MISMO código ya probado.

**Descartado.** Un modelo `FiscalNote` con su ciclo de vida. Habría obligado a
mantener dos numeradores, dos firmadores y dos superficies de envío en paralelo.

**Consecuencia.** Un comprobante y sus notas se distinguen por `document_type` y
por la presencia de `original_document`. Los endpoints de envío, XML, CDR y PDF
ya existentes sirven a una nota sin cambios: una nota ES un comprobante.

---

## ADR-30 · El original es inmutable; la anulación total lo REFLEJA, no lo recalcula

**Decisión.** Emitir una nota no toca el `signed_xml`, el CDR ni los totales del
original. Para una anulación total (Catálogo 09, motivos 01 y 06) la nota copia
los importes y las líneas del original, y esas líneas se leen de su XML FIRMADO,
no de la `Order` (que pudo cambiar tras la venta).

**Por qué.** El comprobante firmado es lo que SUNAT recibió; es la autoridad. Si
la nota releyera `OrderItem`/`Product`, una edición posterior del catálogo
produciría una nota que no cuadra con el documento que dice anular. Leer del XML
firmado garantiza que la nota de anulación refleja el original tal como existió.

**Descartado.** Recalcular la nota desde la venta. Reintroduciría el descuadre de
un céntimo que C2.1 ya cerró, y encima sobre un documento que debe ser espejo
exacto de otro.

**Consecuencia.** La nota de importe explícito (una ND que cobra una mora, p. ej.)
sí toma su base e IGV de quien la pide —no del original—, porque no es un espejo;
es un cargo nuevo que referencia al original.

---

## ADR-31 · Varias notas por original son legales; la idempotencia es por CLAVE, no por original

**Decisión.** No hay `get_or_create(original)`. Un original admite tantas notas
como haga falta. La repetición se controla con `note_request_key`: una restricción
única `(original_document, note_request_key)` —con `note_request_key` no vacío—
hace que reintentar la MISMA petición devuelva la misma nota, mientras que dos
correcciones distintas producen dos notas distintas.

**Por qué.** Sobre una factura caben una nota que corrige el RUC y, más tarde,
otra que descuenta un producto devuelto: son dos hechos económicos reales. Atar la
idempotencia al original prohibiría el segundo. Atarla a una clave de intención
—que el cliente genera por operación— evita el duplicado por doble clic sin
prohibir la segunda nota legítima.

**Consecuencia.** El cliente que quiere idempotencia manda `request_key`; el que
manda uno vacío obtiene siempre una nota nueva. El correlativo sólo se gasta
cuando de verdad se crea una nota.

---

## ADR-32 · La identidad de la nota sale del ORIGINAL, nunca del cuerpo de la petición

**Decisión.** El endpoint recibe el `pk` del comprobante original (resuelto dentro
del tenant y la sucursal de quien llama). La empresa, la sucursal, la serie de la
nota, el adquirente y —en una anulación total— los importes se DERIVAN de ese
original. Del cuerpo sólo se aceptan el motivo, su descripción, una clave de
idempotencia y, para una ND, el importe del cargo.

**Por qué.** Aceptar la empresa, el adquirente o la serie del cuerpo sería dejar
que el cliente emita una nota a nombre de otro, contra un comprobante ajeno, o con
una serie que no le corresponde: un IDOR de emisión. La identidad de una nota no
es una opinión del cliente; es un hecho del comprobante que corrige.

**Consecuencia.** Un cuerpo que trae otro RUC, otra empresa u otra serie no cambia
nada: la nota sigue derivándose del original. Se prueba con un test que inyecta
esos campos y verifica que la nota los ignora (§40/§47).

---

## ADR-33 · La nota comparte el canal de envío del comprobante que corrige

**Decisión.** Una nota de FACTURA se envía por `sendBill`, igual que la factura.
Una nota de BOLETA se informaría por el Resumen Diario, igual que la boleta —y
como el Resumen todavía no transmite líneas de nota, esta fase NO emite notas de
boleta: se falla cerrado en la emisión y, por defensa en profundidad, también en
el envío individual (una serie B en un 07/08 es, sin ambigüedad, nota de boleta).

**Por qué.** El canal lo fija el tipo del original, no el de la nota. Mandar una
nota de boleta por `sendBill` la dejaría rechazada, y un correlativo fiscal
gastado no vuelve. Antes que emitir algo que no se puede transmitir, no se emite:
NC-FAC y ND-FAC quedan IMPLEMENTADAS y correctas; NC-BOL y ND-BOL, PENDIENTES y
declaradas, para la fase que extienda el Resumen.

**Consecuencia.** El prefijo de la serie de la nota (F/B) lo resuelve
`resolve_note_series` a partir del original, con la misma regla determinista que
`resolve_series`: ambigüedad = fallo, nunca un desempate improvisado.
