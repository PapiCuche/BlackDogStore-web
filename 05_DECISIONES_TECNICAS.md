# Decisiones técnicas

Este archivo no existía en el baseline. Es la entrada resumida a las decisiones
de arquitectura del proyecto; el registro completo y su historia viven en los
ADR por dominio, que no se reescriben.

## Registro por dominio

| Dominio | Registro completo |
|---|---|
| Fiscal peruano (SUNAT, UBL 2.1) | [docs/adr-fiscal-c22a1.md](docs/adr-fiscal-c22a1.md) — ADR-1 … ADR-43 |
| Matriz de conformidad UBL 2.1 | [docs/sunat-factura-ubl21-matriz.md](docs/sunat-factura-ubl21-matriz.md) |
| Requisitos y catálogos SUNAT | [docs/sunat-cpe-requisitos.md](docs/sunat-cpe-requisitos.md) |

## Decisiones registradas en esta entrada

### DEC-DEVICE-01 · Un equipo se identifica por lo que su tipo lleva, y lo que no tiene es NULL

- **La regla es del servidor** (`device_identity`): serie obligatoria en teléfono,
  tablet, laptop, computadora, consola y reloj; IMEI obligatorio sólo en teléfono;
  segundo IMEI opcional. El formulario no la repite.
- **El IMEI se valida con su dígito de control (Luhn).** Un marcador («N/A», «0000»)
  no es un identificador: se rechaza. Lo ausente se guarda vacío/NULL, porque dos
  equipos con el mismo marcador chocarían en las reglas que impiden registrar el mismo
  equipo dos veces.
- **Lo que no se puede leer no bloquea la recepción**, pero se explica: un motivo
  permite crear la orden con un identificador obligatorio vacío.
- **El historial del equipo cruza órdenes dentro de la empresa** y, al mostrarse,
  sólo cuenta las órdenes de las sucursales de quien pregunta.

### DEC-TRACKING-01 · El seguimiento del cliente es por enlace, y el enlace abre una orden

- **Un token opaco, no un número.** 128 bits aleatorios más un sello HMAC con clave
  derivada; se comprueba el sello antes de tocar la base. No contiene ni permite
  deducir el id o el número de la orden, tiene una sola escritura válida, y todo lo
  que no resuelve responde el mismo 404. Revocable y reemplazable; un enlace vivo
  por orden; una lectura nunca recrea uno revocado.
- **La vista pública es una lista de lo que sí sale**, armada con los serializadores
  de la superficie del cliente: sin notas internas, sin personal, sin sucursal, con
  serie e IMEI enmascarados. Las fotos son las que el taller compartió.
- **La superficie Bearer del cliente (ADR de autenticación v1) no cambia.** La web
  lista las reparaciones de la cuenta por cookie (`/api/account/repairs/`) y el
  detalle se lee siempre por el enlace: una sola vista del cliente.
- **Quien tiene el enlace decide como el cliente.** Por eso el personal no lo recibe
  por poder abrir la orden: se revela con un acto explícito y auditado que exige
  `service.quotes.record_decision`.
- **Sumar una orden a una cuenta entrega un cliente entero**, así que pide el enlace
  y el documento registrado, con intentos limitados. No se vincula por correo,
  teléfono ni nombre, y la tienda puede deshacerlo.

### DEC-QUOTE-01 · Una aprobación dice quién la dio y por dónde, y vale para lo que se aprobó

- **El canal lo pone la puerta, no el cuerpo de la petición**: cuenta del cliente,
  enlace de seguimiento, o personal (presencial, llamada, WhatsApp, otro).
- **El personal anota; no finge.** Una decisión registrada por el personal lleva
  `source=staff`, quién la anotó y el canal; la cuenta del cliente no figura como
  autora. Tiene capacidad propia (`service.quotes.record_decision`), separada de
  cotizar.
- **Una cotización aprobada no se edita.** Cambiar el trabajo es reabrirla: queda
  `superseded`, la orden vuelve a diagnóstico y nace una revisión nueva. Sólo antes de
  empezar la reparación.
- **El ticket de 80 mm existe sólo para una aprobación vigente**, lo decide el
  servidor, reutiliza el trazado de los demás tickets y no es un comprobante de pago.

### DEC-WHATSAPP-01 · WhatsApp avisa por la API oficial; las credenciales son referencias

- **Sólo la Cloud API de WhatsApp Business.** Nada de WhatsApp Web ni automatización
  de navegador. El proveedor está detrás de una interfaz (`store/messaging`) con un
  falso que no envía, como el de Izipay.
- **Ningún secreto en la base de datos.** Por empresa se guarda el NOMBRE de la
  variable de entorno de cada credencial; lo fija quien opera la instalación
  (`configure_whatsapp`), nunca la API, y sólo en el espacio `WHATSAPP_`. La API
  informa con booleanos.
- **Consentimiento explícito.** Sin `opt-in` registrado no se escribe; darse de baja
  gana, también por mensaje.
- **El aviso es un resumen y un enlace.** Plantilla aprobada con tres parámetros
  (nombre, número de orden, enlace). Nunca IMEI, notas ni importes.
- **Bandeja de salida en la transacción.** La fila de entrega se escribe con el
  cambio de negocio y se envía al confirmarse; lo demás lo reintenta una tarea. Un
  fallo queda anotado y no deshace nada. Un aviso es un mensaje: fila única y
  bloqueada mientras se envía. Una petición sin respuesta no se reenvía sola.
- **El webhook cree lo que viene firmado** (`X-Hub-Signature-256` sobre el cuerpo
  crudo), sólo toca lo de su empresa y los estados sólo avanzan.

### DEC-SERIAL-01 · Un equipo con serie es stock, no un inventario paralelo

- **`BranchStock.quantity` sigue siendo la única cifra que decide una venta.** Para
  un producto con serie equivale siempre a los equipos DISPONIBLES de la sucursal.
- **Se impone en el escritor único del Kardex.** `create_stock_movement` rechaza
  mover un producto con serie sin los equipos de que se trata; como todo cambio de
  stock pasa por ahí, ningún ajuste, carga, transferencia o recuento puede
  desajustar la cifra.
- **La venta asigna equipos concretos** (los más antiguos de la sucursal) bajo el
  bloqueo de la fila de stock, en la misma transacción que la salida.
- **Identificadores únicos por empresa, en cualquier estado.** Un equipo vendido
  conserva su fila y, si vuelve, es la misma.
- **El modo sólo cambia con stock cero**, y el escritor relee el modo cuando ya tiene
  bloqueado el producto.
- **«Equipos disponibles» es un subconjunto de «Unidades en stock»**, no un sumando.

### DEC-CUSTOMER-01 · La cuenta es la identidad; no se crean fichas vacías

- Registrarse (con contraseña o con Google) crea una cuenta, no un cliente del CRM.
  El registro de cliente nace cuando hay una relación: una compra o una orden.
- No se fusiona por nombre, correo ni teléfono. Una orden de mostrador se suma a una
  cuenta con el enlace y el documento (DEC-TRACKING-01).

### DEC-STOREFRONT-CAT-01 · Las familias de la portada las decide cada tienda

- `Category.is_active`, `show_on_home` y `home_order`. La API pública devuelve ya lo
  que la tienda debe ver y en su orden; el frontend no tiene una lista ni un orden
  propios. Retirar una categoría no oculta sus productos.

### DEC-GOOGLE-01 · «Continuar con Google» se verifica en el servidor y abre la sesión de siempre

- **Flujo de ID token, sin secreto de cliente.** El servidor comprueba firma (sólo
  RS256, con las claves publicadas), audiencia, emisor, caducidad, correo verificado y
  un `nonce` que él firmó y además dejó en una cookie HttpOnly del navegador.
- **La identidad es `sub`, no el correo.** Se guarda proveedor y sujeto; ningún token.
- **Un correo que ya tiene cuenta no se enlaza solo** (`User.email` no está verificado
  ni es único): 409 y contraseña de esa cuenta para vincular.
- **Sin contraseña inventada**: la cuenta nueva tiene una inutilizable.

### DEC-MEDIA-01 · Una sola tubería de imágenes públicas; la galería sólo dice quién muestra qué

- **No hay un segundo almacén.** Las imágenes de producto pasan por
  `storefront_media`, la tubería del hero y de las categorías: recodifica desde los
  píxeles (ningún metadato sobrevive), genera la ruta en el servidor, pertenece a una
  empresa y borra lo que nadie muestra. El almacenamiento sigue detrás de
  `evidence_storage` (disco hoy, S3/R2 cuando haga falta) sin que el dominio lo sepa.
- **`ProductImage` no guarda archivos.** Guarda la DIRECCIÓN de la imagen
  (`/api/storefront/images/<id>`), el orden, el texto alternativo y cuál es la
  principal. Por ser una dirección en un campo de texto, el recuento de referencias que
  ya existía la ve sin conocer el modelo: una imagen de galería nunca se limpia.
- **`Product.image_url` se queda.** Lo leen el catálogo, el carrito, las líneas de
  pedido y la caja desde la fase 0. Es la dirección de la principal, mantenida por
  `product_media` en la misma transacción; un producto sin galería conserva la
  dirección que tuviera. Pasa de `URLField` a `CharField` validado, como la de las
  categorías, porque una ruta del propio sitio es un valor legítimo.
- **Una imagen subida no se coloca escribiendo su dirección.** Sólo la galería la
  coloca, y comprueba que es de la empresa. Así no hay forma de citar la imagen de otra.
- **Sin sucursal.** El catálogo es de la empresa, no de una sucursal; la autoridad es
  `products.view` / `products.manage`.
- **El contrato público crece sin romper.** `images` se añade al producto;
  `image_url` no cambia de significado.

### DEC-IMPORT-MEDIA-01 · Las imágenes de una carga masiva viajan con el libro y esperan sin almacén propio

- **Nombres de archivo, no direcciones.** «Imagen principal» e «Imágenes» (separadas
  por `|`) citan archivos que llegan en la misma petición, sueltos o en un ZIP, y se
  casan por nombre.
- **Inspeccionar → previsualizar → aplicar no cambia.** Lo que está mal con una imagen
  es un error de SU fila, con el archivo nombrado. Un trabajo con errores no se aplica.
- **Sin almacén temporal.** Entre previsualizar y aplicar, las imágenes que alguna
  fila usará esperan como imágenes «sin colocar» de la empresa del trabajo, en la
  tubería de siempre. Aplicar las coloca; la limpieza diaria (24 h) retira las de una
  previsualización abandonada. Un trabajo con errores no deja ninguna.
- **Aplicar no confía en la fila guardada.** Vuelve a comprobar la autoridad y coloca
  cada imagen con `claim`: tiene que existir todavía y ser de la empresa del trabajo.
  Si no, se deshace la importación entera.
- **El ZIP no se extrae.** Se revisa su índice antes de leer nada (rutas que salen,
  enlaces simbólicos, cifrado, número de entradas, tamaño expandido) y se lee entrada
  por entrada en memoria, con tope. Una entrada hostil rechaza el archivo completo.
- **Importar dos veces no duplica.** La huella del archivo original reconoce, dentro
  del mismo producto, una imagen que ya está en su galería. Nunca se compara entre
  productos ni entre empresas.
- **El Excel dentro del ZIP no se admite.** El libro se inspecciona antes de adjuntar
  nada; meterlo en el ZIP habría obligado a rehacer ese paso.

### DEC-EVIDENCE-01 · La evidencia de servicio se describe a sí misma y nunca se reescribe

- **Se extiende lo que había.** `RepairEvidence` ya era privada, por etapa, interna al
  nacer, anulable y nunca borrada. Se le añade una nota y tres etapas (repuestos, listo
  para entrega, garantía/reingreso); no hay un modelo nuevo ni una tabla por etapa.
- **La etapa no es un estado.** Subir una foto no mueve la orden. Por eso la evidencia
  no cuelga de `RepairStatusHistory`: explica qué pasó con su etapa, su autor y su hora,
  no con una transición.
- **La autoridad es la de producir la etapa.** Repuestos: quien repara. Listo para
  entrega: quien entrega. Garantía/reingreso: quien abre órdenes. No se creó ninguna
  capacidad de «evidencias».
- **La foto no cambia; la nota sí, con registro.** Corregir una nota deja el texto
  anterior en la auditoría. Una evidencia anulada conserva la nota que tenía.
- **El cliente lee la nota de lo que se le comparte.** Compartir es un acto explícito y
  el panel lo avisa. La lista de campos del cliente sigue siendo una lista cerrada.
- **Una petición por foto.** Subir varias a la vez son varias peticiones, cada una con
  su clave de idempotencia: la que falla se reintenta sin duplicar y las demás no
  dependen de ella.

### DEC-MEAS-01 · Quien visita decide, en el navegador y en el servidor

Fase ANALYTICS-MARKETING-INTEGRATIONS-01. Operación: [docs/analytics-marketing.md](docs/analytics-marketing.md).

- Dos categorías opcionales, apagadas hasta que alguien las enciende: **analítica**
  (Google Analytics) y **marketing** (Meta, TikTok). Rechazar cuesta un clic, como aceptar.
- La respuesta vive en el navegador (`localStorage`): es una preferencia del dispositivo
  y tiene que poder leerse antes de iniciar sesión. No lleva ningún identificador.
- **El servidor obedece la misma respuesta.** Viaja con el pedido al empezar a pagar y es
  lo único que permite planificar una conversión. `is True`, no «algo que parezca sí».
- No se guarda un registro de consentimientos en el servidor (MEAS-CONSENT-LOG): sólo el
  del pedido, y se borra con él.

### DEC-MEAS-02 · La tienda dice sus propios eventos; tres adaptadores traducen

- Un vocabulario (`frontend/app/lib/analytics/events.ts`) cuyos tipos no tienen un campo
  para una persona, un equipo o un token: lo que no cabe no se envía por descuido.
- Un servicio (`service.ts`) es lo único a lo que hablan las páginas. `gtag`, `fbq` y
  `ttq` existen sólo en `adapters/`; una prueba lee el código y falla si aparecen fuera.
- Cada adaptador decide a qué evento del proveedor corresponde cada uno, o a ninguno. No
  se inventan eventos para rellenar: Meta y TikTok no tienen «ver carrito».

### DEC-MEAS-03 · La compra nace donde el pedido queda pagado, y es una fila

- `ConversionDelivery`, única por pedido, proveedor y evento, escrita en la transacción de
  `_confirm` bajo un punto de guardado propio: tan duradera como el pago, e incapaz de
  deshacerlo. Se envía tras el commit; los reintentos llevan el mismo identificador.
- El navegador emite su copia sólo cuando el servidor dice que el pedido está pagado, con
  el identificador que le da el servidor. Donde el servidor la envía solo (GA4 con
  secreto), el navegador no la envía.
- **Por qué una tabla y no una tarea en memoria:** una conversión que se pierde si el
  proceso muere, o que se repite si se reintenta a ciegas, es justo lo que hay que evitar.
- `MeasurementContext` guarda lo mínimo y poco tiempo: nada sin consentimiento; IP y
  navegador sólo con el de marketing; se borra al terminar o a los siete días.

### DEC-MEAS-04 · En una dirección privada no hay ningún script

El script de un proveedor lee `location.href` por su cuenta. Sanear lo que se le pasa no
basta: la única protección de una dirección con token es que el script no esté.

- En seguimiento, restablecer contraseña, verificar correo, invitación, pedidos,
  reparaciones y el panel no se carga ni se envía nada, con el permiso dado y todo.
- **Y un script ya cargado no llega a verlas.** Callar nuestros eventos no bastaba (lo
  encontró la revisión de seguridad): el script lee la dirección solo. Desde que hay uno
  cargado, la API de historial está envuelta: ir a una dirección que ese script no puede
  ver se hace con una carga completa, tras decirle que pare. Cuesta una recarga.
- Meta y TikTok tampoco están en las páginas con formulario de datos personales: sus
  paneles pueden activar una lectura automática de campos que este código no puede apagar.
- La referencia del pago salió de la dirección de la página de éxito (`sessionStorage`).
- Desde «Mis reparaciones», el enlace de seguimiento es una navegación completa.
- A Google se le dice una ruta; nunca los parámetros.

### DEC-MEAS-05 · Un identificador público, ninguna dirección configurable

- Cada proveedor tiene un ID que es tan público como una etiqueta `<script>`, y es lo
  único que sale por `GET /api/measurement/config/`. Se construye con lo que el proveedor
  DECLARA público (`runtime_public`), no filtrando la fila guardada.
- De dónde se carga cada script y adónde van los eventos son constantes del código. Un
  campo de URL en la consola dejaría a una sesión MASTER robada apuntar a los clientes de
  la tienda a un script ajeno; un ID con la forma de su proveedor, no.
- Nada de esto usa `NEXT_PUBLIC_*`: cambiar un ID no exige recompilar.

### DEC-MEAS-06 · Una prueba nunca crea un evento real

Con código de evento de prueba, Meta y TikTok se verifican por su canal de pruebas. Sin
él, y en los modos «sólo píxel» y en GA4, el resultado es «Coherente, sin verificar»: el
servidor de validación de Google dice de sí mismo que no comprueba el ID ni el secreto.

### DEC-MEAS-07 · La CSP no se toca en esta fase

La tienda no tiene `script-src` (CSP-01). Añadirla exige un *nonce* en cada script que
emite Next y probarla con el SDK de la pasarela real, que no hay. Abrir una política
laxa «para que funcionen los píxeles» sería peor que no tenerla. Queda la lista exacta
de dominios en `docs/analytics-marketing.md` §7.

### DEC-MEAS-08 · Lo que el propietario decidió al cerrar la fase (2026-10-07)

- **CSP-01 es obligatoria antes de activar un proveedor de analítica o marketing en
  producción.** No se escribe una lista de dominios por adelantado: la política se cierra
  cuando puedan validarse los dominios reales, sobre todo los de la pasarela y los que
  cargan de verdad los SDK.
- **La IP y el navegador van a la API de conversiones de Meta y a Events API de TikTok**,
  sólo con eventos cubiertos por el consentimiento de marketing. Sin ese consentimiento
  no se envían. La minimización de `MeasurementContext` no cambia. Antes de producción,
  la política de privacidad y cookies de la tienda tiene que decirlo (MEAS-PRIVACY-NOTICE).
- **La coincidencia avanzada automática sigue desactivada** en Meta y TikTok.
- **Los dos ciclos de imports de la fase (INT-IMPORT-CYCLE) se corrigen aparte**, en la
  microfase INT-IMPORT-CYCLE-01, para no alterar la línea base ni la regresión de una
  fase ya cerrada.

### DEC-FRESH-DATA-01 · Producción nace de una base vacía; lo que sobra se retira con un paso, no con una migración

- Una base de producción es PostgreSQL vacío y `migrate`. Ni copia de desarrollo, ni
  volcado, ni semilla.
- La migración `0002` dejó tres productos de ejemplo con stock y sin Kardex. No se edita
  una migración aplicada, y **una migración nueva que los borrase correría también en
  cada base de desarrollo y de pruebas**. Se retiran con `bootstrap_pilot_store`, que
  alguien ejecuta una vez sobre la base que va a ser la real.
- **Todo o nada.** Sólo retira un producto que está exactamente como lo dejó la
  migración, sin referencias y en una base sin actividad; si no, se niega y no cambia
  nada. Las referencias se preguntan al modelo, no a una lista: una relación nueva
  cuenta el día que existe.
- **Lo de la tienda piloto no es un valor por defecto de la plataforma.** Sus categorías
  y su campaña están ligadas a su slug; `company_provisioning`, que es de donde nace
  una empresa nueva, no copia catálogo.
- **El MASTER es `is_superuser` y no recibe una membresía por serlo.** Se crea con
  `createsuperuser`, sin contraseña en el repositorio. Puede configurar cualquier
  empresa nombrándola; para vender o constar como personal hace falta una membresía
  explícita, que se da por invitación.

### DEC-BRAND-ICON-01 · El icono de la pestaña es el isotipo, en dos contrastes

El isotipo es una silueta de un color: el oscuro desaparece en una pestaña oscura. Hay
dos juegos y el navegador elige con `prefers-color-scheme`; no hay un tercero sin
condición, que podría ganar a los otros dos. Sin fondo, sin letra y sin realce: a 16 px
el realce rellenaba la cara y borraba las gafas. Es marca de plataforma, no de empresa.

### DEC-INT-01 · Los secretos de las integraciones van cifrados, con una clave que no está en la base

Fase INTEGRATIONS-CONSOLE-01. Operación: [docs/integraciones-y-secretos.md](docs/integraciones-y-secretos.md).

- **Qué:** los campos secretos de cada integración se guardan juntos, cifrados con Fernet
  (`cryptography`: AES-128-CBC + HMAC-SHA-256), en `IntegrationConfig.sealed_secrets`. La
  clave raíz es `APP_CONFIG_ENCRYPTION_KEY`, del entorno del servidor.
- **Por qué no una columna por secreto:** una columna legible acaba en un serializer, un
  volcado o una bitácora. Aquí no hay ninguna que se pueda leer sin la clave, y la clave
  no viaja con la base.
- **Por qué Fernet y no algo propio:** es cifrado autenticado estándar de una biblioteca
  que el proyecto ya usa para firmar los comprobantes. No se diseña criptografía.
- **Atado a su sitio:** el texto cifrado lleva dentro integración, empresa y si es borrador
  o activo. Una fila copiada a otra empresa no se abre.
- **Escritura sin lectura:** ninguna API devuelve un secreto; devuelve `configured`, cuándo
  y quién. No se guarda en claro nada de él: los cuatro últimos caracteres que se
  guardaban al principio eran un tercio de una contraseña de doce (revisión).
- **Rotación:** `APP_CONFIG_ENCRYPTION_KEYS_PREVIOUS` + `reseal_integration_secrets`.
- **Lo que se paga:** perder la clave raíz es perder las credenciales guardadas (se vuelven
  a escribir). Es el precio de que una copia de la base no las contenga.

### DEC-INT-02 · El dominio no nombra a ningún proveedor

- **Registro de proveedores** (`store.integrations.registry`): cada uno declara id,
  categoría, alcance, campos públicos y secretos, validación, modos, prueba, confirmación
  de activación y respaldo de entorno. La API y la pantalla se construyen con eso.
- **Adaptadores:** el checkout llega a la pasarela por `store.integrations.payments`
  (un adaptador por producto); el correo, por un backend de Django
  (`RuntimeEmailBackend`) que elige en cada envío; WhatsApp, por `store.messaging`.
- **Por qué:** añadir un proveedor es una clase y su declaración, no un `if` nuevo en el
  checkout. Hay una prueba que falla si el checkout vuelve a nombrar una pasarela.

### DEC-INT-03 · Qué es de la instalación y qué es de cada empresa

| Integración | Alcance | Por qué |
|---|---|---|
| Correo SMTP | instalación | un remitente de transporte para todas; la identidad visible de cada mensaje ya es de cada empresa |
| Izipay | instalación | el checkout, las rutas de notificación y el frontend asumen un comercio; por empresa es otra fase (PAY-TENANT-SCOPE) |
| WhatsApp | **empresa** | cada empresa tiene su número; ya era así con las referencias a variables |
| Google | instalación | un ID de cliente por dominio |
| SUNAT | instalación | el código sólo opera BETA con credenciales comunes; `fiscal_config` ya recibe la empresa (FISCAL-TENANT-SCOPE) |

La consola declara el alcance que el código tiene, no el que sería deseable. No se
inventa un «Producción» de SUNAT ni un Izipay por empresa.

### DEC-INT-04 · Borrador, prueba, activación; y apagado es apagado

- Dos filas por integración: `active` (lo que corre) y `draft`. Guardar nunca toca lo que
  corre.
- Sólo se activa un borrador que pasó la prueba, y esa versión exacta: la prueba cambia la
  versión y la activación la exige (concurrencia optimista; 409 si no coincide). El
  resultado de una prueba sólo se escribe si la fila sigue siendo la que se leyó: una
  prueba tarda segundos y lo guardado entretanto no es lo que se probó.
- **«Coherente, sin verificar» no es «Correcto».** Las claves de producción de la pasarela
  no se pueden probar sin tocar dinero real: pasan, y se dice que nadie las verificó.
- **Parar lo que está en uso con algo en curso se escribe** (`INTERRUMPIR`): el proveedor
  declara qué quedaría cortado (hoy, cobros abiertos en la última hora).
- Orden de resolución en cada uso: consola activa → si la consola la tiene apagada, nada →
  si la consola no tiene nada, entorno.
- **Por qué apagado no recurre al entorno:** un interruptor que deja pasar por otra puerta
  no es un interruptor. Lo mismo vale para lo guardado que no se puede leer.
- Lo que cuesta dinero o no tiene vuelta atrás pide escribir una palabra (`PRODUCCION`,
  `EMITIR`, `REVOCAR`). No se pide la contraseña otra vez: no hay una reautenticación
  reutilizable y no se inventa una débil.

### DEC-INT-05 · Sólo el MASTER, y no por capacidades

`IsPlatformAdmin` en cada ruta y `platformAdminOnly` en el registro de módulos. No se creó
una capacidad `integrations.manage`: una capacidad se puede conceder desde un rol de
empresa, y estas credenciales no son de ninguna empresa (salvo WhatsApp, que el MASTER
administra por ellas).

### DEC-INT-06 · Sin caché: cada uso pregunta

La configuración activa se lee —y se descifra— en cada envío, cada cobro y cada inicio de
sesión. Así un cambio vale al instante y en todos los procesos, sin invalidar nada. Es una
consulta por clave única; si el tráfico lo pidiera, se mide antes de añadir una caché
(INTEGRATION-READ-COST). Dentro de una misma petición WhatsApp lo lee una vez.

### DEC-INT-07 · El correo ya no impide arrancar

Antes el backend no arrancaba en producción sin `EMAIL_BACKEND`. Con la consola el correo
se configura después de arrancar, así que arrancar sin él es legítimo: los envíos fallan
con un error registrado, `ops_status` da la alarma y nunca se cae al backend de consola
(MAIL-CONSOLE-DEFAULT sigue en pie). `EMAIL_BACKEND=smtp` sin `EMAIL_HOST` sigue sin
arrancar: es una contradicción del archivo, no una ausencia.

### DEC-PAY-01 · Izipay se prueba contra un Izipay falso, y lo que el falso no puede probar se dice

- La petición del token no la ejercitaba ninguna prueba: se sustituía entera.
  `store/payments/fake_izipay.py` se pone en el socket, comprueba el contrato que el
  adaptador declara (POST, HTTPS, URL configurada, `Authorization` con la clave sin
  esquema, cabecera `transactionId` igual a la del cuerpo, comercio, orden, moneda,
  importe con dos decimales, tiempo de espera) y responde como la pasarela.
- El falso firma las notificaciones con su propio HMAC, no con el del adaptador, y sabe
  enviar lo que enviaría un atacante: editado después de firmar, firmado con otra clave,
  repetido y repetido con otro contenido.
- Es un arnés de pruebas. No tiene interruptor en la configuración ni ruta: no es una
  forma de marcar pedidos como pagados.
- **Lo que no se pudo verificar.** La referencia pública de `Token/Generate` es una
  aplicación de cliente y no se pudo leer. El arnés no exige nada sobre el resto del
  cuerpo: exigir una suposición haría que la suite certificara la suposición. Queda
  abierto IZIPAY-TOKEN-CONTRACT y sólo lo cierra el sandbox real:
  `IzipaySandboxSmokeTest`, que se omite sin credenciales (**BLOCKED/CREDENTIALS**).
- Sólo un intento que sigue esperando su respuesta puede marcarse como «fallo de
  integridad». Un mensaje contradictorio que llega después de autorizar se rechaza y no
  reescribe el registro del pago bueno.

- **Actualización (PAYMENTS-EQUIPMENT-DOCUMENTS).** La misma disciplina vale para el
  segundo producto: `store/payments/fake_micuentaweb.py` exige el contrato publicado de
  `Charge/CreatePayment` y firma las notificaciones por su cuenta, incluida la copia que
  recibe el navegador. `MiCuentaWebSandboxSmokeTest` se omite sin claves
  (**BLOCKED/CREDENTIALS**). Una firma con caracteres no ASCII se compara como bytes: ya
  no es un 500.

### DEC-PAY-02 · Izipay son dos productos; una instalación usa uno

- El código hablaba con el «SDK web / Checkout» (developers.izipay.pe). La página
  oficial que indicó el propietario documenta «Mi Cuenta Web» (API REST V4): otras
  credenciales, otro guion, otra firma. Las dos son integraciones oficiales válidas.
- No se reemplazó una por otra: no se sabe de cuál tiene credenciales el propietario, y
  borrar la primera habría tirado un contrato ya probado. Se añadió la segunda como
  adaptador aparte (`store/payments/micuentaweb.py`).
- **Nunca las dos a la vez.** `PAYMENT_PROVIDER` nombra una. El checkout abre el pago
  sólo con esa, el navegador carga sólo su guion y la notificación de la otra responde
  404 antes de leer nada. Un valor desconocido impide cobrar; no cae en «la de siempre».
- **Una sola definición de «pagado».** Cada producto tiene su vista, que hace una cosa
  propia: verificar la firma y reducir el mensaje a un resultado. Lo que sigue
  (`_SignedNotificationMixin`) es común: importe, moneda, comercio y pedido contra la
  base, bloqueo de fila, y una repetición que no paga dos veces.
- **El navegador no es testigo.** En «Mi Cuenta Web» el navegador recibe una copia de la
  respuesta firmada con otra clave. La notificación sólo cree lo firmado con la
  contraseña (`kr-hash-key=password`); la copia del navegador, reenviada, se rechaza. El
  checkout ni siquiera la lee.
- **TEST o PRODUCCIÓN lo dicen las claves.** No hay variable de entorno para eso en este
  producto. Un par mezclado impide cobrar y un pago de TEST no paga un pedido con claves
  de producción.
- Pendiente (PAY-RECONCILE): si la notificación no llega nunca, un pedido cobrado queda
  esperando. La API tiene `Order/Get`; no se usa todavía.

### DEC-UNIT-IMPORT-01 · Los equipos con serie se cargan de a uno por fila, por el escritor de siempre

- La carga masiva de stock escribe cantidades. Un producto con serie no tiene una
  cantidad que alguien escribe, así que ese archivo lo rechaza por fila. Para ellos hay
  una plantilla propia: «Equipos serializados.xlsx», **una fila = un equipo físico**.
- No es otro camino al stock. Cada fila termina en
  `stock_unit_services.receive_units`, el mismo escritor que «Registrar equipo»: mismas
  reglas de serie e IMEI, misma línea de Kardex, misma invariante.
- Una columna «Cantidad» rechaza el archivo entero. Leerla como «dos equipos con una
  serie» es justo lo que la plantilla existe para impedir.
- Previsualizar no escribe. Registrar es todo o nada y vuelve a comprobar cada serie
  contra el stock de ese momento; lo que cambió desde la previsualización nombra su fila.
- La empresa sale de quien carga; la sucursal se busca por nombre dentro de la empresa y
  dentro de sus sucursales. Un trabajo que toca una sucursal fuera de alcance no existe
  para esa persona: ni se aplica ni se lee, porque sus filas llevan series e IMEI.
- Reutiliza `BulkImportJob`/`BulkImportRow` con un tipo nuevo (`units`, migración `0110`).

### DEC-DOC-01 · Los documentos de una tienda comparten un diseño, y lo que dicen sale de la venta

- La nota de venta, su ticket, el comprobante de pedido y el ticket de cotización eran
  cuatro trazados. Ahora hay un módulo de estilo (`document_style`) y una página A4
  (`document_layout.render_a4`) que recibe el documento como datos. Un documento nuevo
  es una descripción, no otro trazado.
- **Ninguna tienda en el código.** Identidad, sucursal, logotipo y textos legales salen
  de la empresa, la sucursal, la venta o la configuración. Una prueba revisa los módulos.
- **Serie e IMEI vienen del equipo vendido.** Se leen de los movimientos de Kardex de la
  venta (que guardan los ids de los equipos) y de los equipos asignados al pedido, dentro
  de la empresa. Nunca de una descripción. Una devolución posterior no cambia lo que dice
  la reimpresión de aquella venta.
- **El logotipo se guarda con la nota**, como ya se hacía con los comprobantes fiscales.
  Una nota anterior a esta fase no tiene copia y usa el logotipo actual.
- **Sólo gris.** La jerarquía la dan el tamaño, el peso, las líneas y el espacio: se
  imprime igual en una láser monocroma. Las fuentes son las incorporadas del PDF; una
  tipografía embebida haría los documentos más pesados y sus pruebas ilegibles.
- **Interno no es fiscal.** La nota dice enmarcada que no es un comprobante electrónico
  y su número, que no es una serie fiscal. Las representaciones de boleta y factura no
  se tocaron: su formato lo fija la RS 114-2019 (DEC-FISC-PRINT-01).
- Las pruebas leen el texto y la estructura (tamaño de página, imágenes, páginas). No
  comparan bytes. `DOCUMENT_SAMPLES_DIR` deja los PDF para revisarlos a la vista.

### DEC-LIMIT-01 · El tamaño de una petición se decide una vez y se aplica tres

- Nada limitaba el cuerpo de una petición. Caddy lo dejaba pasar entero y Django no
  tiene un tope que cubra esta API: un JSON se lee del flujo sin límite y un formulario
  con archivos se escribe en disco, del tamaño que sea, en cualquier ruta —el inicio de
  sesión incluido—.
- **Una tabla, tres aplicaciones.** `store/request_limits.py` dice qué admite cada ruta:
  lo que su pantalla acepta más el sobre del formulario para las que reciben archivos,
  1 MiB para el resto. Un middleware rechaza por la longitud declarada antes de leer;
  `deploy/Caddyfile` repite los mismos números para cortar en el borde; la vista
  conserva su regla, que es la que sabe explicarse.
- **Quien responde 413 es el proxy.** Compara la longitud anunciada con el tope de la
  ruta y contesta sin llamar a ninguna aplicación. Dejar que lo hiciera Django daba 502
  una de cada diez veces: Django contesta y cierra sin leer el cuerpo que el proxy
  todavía le estaba enviando. El middleware de Django se queda como segunda línea, para
  una instalación sin este proxy delante.
- **Los números están escritos dos veces a propósito.** Caddy no puede leer Python. Una
  prueba compara los dos archivos y falla si dejan de coincidir, o si una vista empieza
  a recibir archivos sin tener su tramo.
- Una ruta que nadie listó recibe el tope pequeño: lo nuevo falla cerrado.
- El cuerpo sin longitud declarada (por trozos) no es una vía: Django entrega a la vista
  un flujo limitado a la longitud declarada, que entonces es cero.
- **El proxy lee el cuerpo entero antes de pasarlo donde Django lo leería sin saber quién
  llama**: la ruta general y las dos que reciben llamadas de fuera (notificación de pago,
  webhook de WhatsApp). Django atiende con ocho hilos; sin eso, nueve cuerpos que no
  terminan de llegar dejan a la API sin hilos. Cada tramo declara si es de ésos
  (`read_without_session`) y la prueba exige que el proxy lea exactamente ésos.
- **Las rutas de archivos no se leen antes de autorizar.** Para que sea verdad, la
  comprobación CSRF toma el token sólo de la cabecera: Django lo busca primero en el
  formulario, y buscarlo ahí es leer la subida entera antes de saber si quien la envía
  puede enviarla.

### DEC-LOG-01 · Los registros no guardan lo que una dirección puede llevar

- El registro de acceso escribía la línea de cada petición. Una dirección puede ser una
  credencial (el enlace de seguimiento de una reparación, el token de una invitación, el
  token con que Meta verifica un webhook) o llevar lo que alguien escribió en un
  buscador (un IMEI, un documento, un teléfono).
- **Una ruta conserva su forma y pierde el token. Una consulta conserva sus claves y
  sólo los valores de una lista corta de claves inocuas** (`page`, `branch`, `status`…).
  Negar por omisión: un parámetro en el que nadie pensó sale oculto, no a la vista.
- La misma regla filtra las líneas que escribe la aplicación (Django nombra la ruta de
  cada petición que rechaza).
- Producción no arranca sin `EMAIL_BACKEND`: el valor de desarrollo escribe cada correo,
  con sus enlaces de un solo uso, en ese mismo registro.
- Lo que no se hizo: enmascarar el identificador de un inicio de sesión fallido. Es lo
  que permite investigar un ataque, y la contraseña nunca se registra.

### DEC-FISC-PRINT-01 · La representación impresa sigue los Anexos I y II de la RS 114-2019

- Fuente: los anexos oficiales de SUNAT (RS 114-2019, que sustituyen a los Anexos 1 y 2
  de la RS 097-2012), columna «Representación impresa – información mínima». No se
  encontró ningún «Anexo B» vigente que regule el papel; si existe otro documento, la
  auditoría se repite contra él.
- La etiqueta del documento del adquirente sale del catálogo 06 (RUC, DNI, carné de
  extranjería, pasaporte). Sin documento —la boleta a consumidor final— no se imprime
  ninguna etiqueta de documento.
- La leyenda nombra el comprobante. Cada ítem lleva su unidad de medida. El ticket de
  80 mm imprime el precio de venta unitario y el valor resumen. El importe en letras y
  la forma de pago se leen del XML firmado.
- Todo sigue saliendo del XML firmado o de la fila congelada, nunca de tablas vivas.
- Pendiente (FISCAL-PRINT-EXO): los totales de operaciones exoneradas e inafectas y los
  descuentos no se imprimen por separado; hoy el dominio sólo emite operaciones gravadas.

### DEC-FISC-LOGO-01 · El logotipo se congela con cada comprobante

- La tienda elige un logotipo para sus comprobantes: una imagen subida por ella
  (`CompanySettings.document_logo_url`). No vale una URL externa ni una ruta del
  frontend: el servidor necesita los bytes.
- Al emitir se guarda una copia propia bajo una clave que depende de su contenido
  (`companies/<id>/fiscal/logos/<sha256>.png`) y el comprobante la recuerda. Una
  reimpresión lleva el logotipo con el que se emitió aunque la tienda lo cambie o lo
  borre. Las notas heredan el del comprobante que modifican.
- La copia se aplana sobre blanco y se dibuja una vez, tal cual. Sin sombra, ni en el
  papel ni en la vista previa del panel: la sombra de la portada es para la portada.
- Un logotipo ilegible nunca detiene una emisión ni una reimpresión.

### DEC-PRINT-01 · El ticket lo imprime la tienda, no el navegador

- **Cola por sucursal.** `Printer`, `PrintAgent` y `PrintJob` pertenecen a una
  sucursal. El servidor nunca abre una conexión hacia una impresora.
- **El agente tira, el servidor no empuja.** Un programa de un solo archivo
  (`backend/print_agent/`) corre en la red del local, pregunta por HTTPS si hay trabajo
  para su sucursal y lo entrega a la térmica por el puerto 9100. No se abre ningún
  puerto del local. Su token vale para una sucursal y sólo para la cola.
- **Texto nativo, no PDF.** La térmica recibe ESC/POS: letra nítida, QR dibujado por
  la impresora y logotipo de un bit. El contenido sale del mismo contexto que el PDF
  de 80 mm, así que dicen lo mismo.
- **Sólo tras una confirmación autoritativa.** El trabajo se crea cuando un comprobante
  queda firmado o una nota de venta se crea, sobre un pedido pagado. Ni el navegador ni
  el agente pueden pedir el ticket de algo que no se cobró.
- **Idempotente en sus dos mitades.** La clave del trabajo sale del documento: confirmar
  la venta dos veces no crea dos. Cada entrega lleva un token nuevo: confirmar dos veces
  no cambia nada y una confirmación antigua se rechaza. El papel no sale dos veces
  porque el agente lleva un diario de lo impreso.
- **Sólo la red del local.** La dirección de una impresora tiene que ser privada o
  `.local`; lo comprueban el servidor al guardarla y el agente antes de conectar.
- **Del texto sólo sale texto.** Lo que escribe el comprador —nombre, dirección,
  notas— pierde todo carácter de control antes de ir a la impresora: un `ESC` dentro
  de un nombre sería una orden (abrir el cajón, cortar).
- **Espera entre intentos y caducidad.** Tras un fallo el trabajo espera 10, 30, 60 y
  120 segundos; sin eso una impresora apagada quema los cinco intentos en un segundo.
  Un ticket que nadie recogió en 12 horas ya no se imprime, y uno cuyo pedido dejó de
  estar pagado se cancela al ir a entregarlo.
- **«En cola» no es «enviado».** La venta dice si hay un agente del local escuchando.
  Sin él, la caja lo avisa y sigue ofreciendo imprimir desde el navegador. Desactivar
  una impresora da por fallidos sus trabajos pendientes, y un reenvío va a la impresora
  que el local tiene hoy.
- **El diario del agente no se fía del número de fila.** Cada trabajo lleva un
  identificador que no se repite entre bases; el número sí se repite tras una
  restauración o al cambiar de servidor.
- **Redes escritas a mano.** `is_private` cambia entre versiones de Python (una IPv4
  metida en una IPv6 pasaba por privada). Servidor y agente comparan contra la lista
  explícita de redes locales. El agente no sigue redirecciones.
- **Límite conocido.** Sólo impresoras en red. USB y Bluetooth quedan fuera.

### DEC-SF-06 · Las imágenes de la tienda las sube la tienda

Sustituye a la parte de DEC-SF-01 que decía «el hero es una losa oscura para todas
las tiendas» y cierra STOREFRONT-HERO-VARIANT.

- Una imagen de la tienda (hero, categoría, campaña) es contenido del tenant. No
  hay ninguna compilada en el frontend ni en una migración.
- El panel sube el archivo y guarda su dirección en el hueco. La dirección es una
  ruta del propio sitio (`/api/storefront/images/<id>`), así que no cambia si la
  tienda cambia de dominio ni depende de un host externo.
- Se guardan en el almacenamiento que ya existía para las evidencias, bajo otra
  ruta, y se sirven por Django. No se añade infraestructura: ni un bucket público
  ni un servidor de archivos. Son públicas porque van en la portada; las
  evidencias siguen siendo privadas.
- El formato se conserva. Las evidencias se aplanan sobre blanco y pasan a WebP
  porque son fotos; una imagen de la tienda suele ser un recorte en PNG sin fondo
  y aplanarla le pondría un rectángulo detrás.
- Presentación: cualquier imagen subida por esta tubería usa una sombra suave en
  el storefront. En un PNG/WebP transparente `drop-shadow` sigue la silueta y evita
  el efecto de recorte «pegado»; las URLs externas no reciben esta regla.
- Además de hero/categorías/campañas, la página estable puede guardar una imagen
  editorial de servicio técnico y otra de ubicación. Son opcionales y por tenant.
- El estilo del hero (`dark` o `light`) lo elige cada tienda. `dark` es el valor
  por defecto: ninguna tienda cambia de aspecto por esta decisión.
- Descartado: escribir las imágenes del piloto en el repositorio (no hay
  evidencia de licencia y otra tienda las vería), y guardar la imagen como URL
  externa escrita a mano (obliga al dueño a alojar archivos).

### DEC-42 · Atribución determinista del descuento de promociones a componentes

Regla del motor SaaS, no del tenant piloto. Una promoción reparte su descuento YA
DECIDIDO entre los componentes consumidos en proporción a su valor regular
(`unit_price × quantity_used`), con ROUND_DOWN a céntimos, céntimos restantes a los
mayores residuos y desempate por `product_id` ascendente. Cierra exacto o falla. Se
congela en `AppliedPromotion.metadata["components"]`; el snapshot congelado es la
autoridad histórica (se valida y se usa tal cual), un snapshot anterior se
reconstruye en memoria sin escribir, y un snapshot a medias falla cerrado. No cambia
precio, stacking, prioridades, unidades consumidas ni totales. Detalle, motivos y
descartes: [ADR-42](docs/adr-fiscal-c22a1.md#adr-42--atribución-determinista-del-descuento-de-una-promoción-a-sus-componentes).

### DEC-43 · Descuentos declarados en UBL sin doble descuento

Cupón y manual: un `cac:AllowanceCharge` global con código `02` del Catálogo N.º 53
vigente; promoción: uno por línea rebajada con código `00`. Importes en valor de
venta derivados del snapshot (`subtotal_amount`, `taxable_amount`, `tax_rate`).
`LegalMonetaryTotal/LineExtensionAmount` es la base imponible (regla 3278),
`PayableAmount` es `Order.total`, y `AllowanceTotalAmount` se omite porque SUNAT lo
reserva a descuentos que no afectan la base y lo resta del importe a pagar (reglas
3300/3280). Fuente: archivo oficial de reglas de validación (21.04.2025). Detalle:
[ADR-43](docs/adr-fiscal-c22a1.md#adr-43--un-descuento-se-declara-donde-nació-con-el-código-de-su-nivel-y-no-se-resta-dos-veces).
