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
