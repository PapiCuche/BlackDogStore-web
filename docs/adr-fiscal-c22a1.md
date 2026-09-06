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
