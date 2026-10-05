# Seguimiento, cotizaciones, WhatsApp, equipos con serie y Google

Guía de uso y de operación de lo que añade la fase SERVICE-TRACKING. Qué hace cada
cosa, quién puede hacerla y qué hay que configurar. Las decisiones de diseño y su
porqué están en `05_DECISIONES_TECNICAS.md` (DEC-DEVICE-01 … DEC-GOOGLE-01).

Nada de esto está publicado en Internet. El envío real por WhatsApp y el acceso con
Google necesitan datos que sólo puede dar el propietario (§8).

## 1. Identificar un equipo en la recepción

Qué pide cada tipo de equipo lo decide el servidor:

| Tipo | Número de serie | IMEI |
|---|---|---|
| Teléfono | obligatorio | obligatorio |
| Tablet, laptop, computadora, consola, reloj | obligatorio | opcional |
| Otros | opcional | opcional |

- El IMEI son 15 dígitos con su dígito de control. Uno mal copiado se rechaza y se
  dice que no supera el control.
- El segundo IMEI es opcional, acompaña al primero y no puede ser el mismo.
- Lo que el equipo no tiene se deja vacío. «N/A», «no tiene», «0000» se rechazan.
- Si un identificador obligatorio no se puede leer (pantalla rota, equipo que no
  enciende) se escribe el motivo y la orden se crea igual.
- Al escribir la serie o el IMEI, la recepción ve si ese equipo ya estuvo en el
  taller, con cuántas órdenes y cuál fue la última. El mismo equipo no se registra
  dos veces para el mismo cliente.

## 2. Seguimiento del cliente

Cada orden nace con un **enlace de seguimiento**: `/seguimiento/<código>`.

- Lo ve quien tiene el enlace, sin crear una cuenta: estado, avance con fechas,
  cotización, pagos, fotos que el taller compartió y, una vez entregado, la política
  de garantía de la tienda.
- No muestra notas internas, el estado físico anotado en la recepción, la sucursal,
  el técnico ni quién cobró. La serie y el IMEI salen enmascarados (sólo los últimos
  dígitos).
- El código no se puede adivinar ni deducir del número de orden, y un enlace alterado
  responde lo mismo que uno que no existe. La página no se indexa.
- Desde el enlace se puede **aprobar o rechazar la cotización**. Queda registrado que
  la respuesta llegó por el enlace.

**Quién puede ver el enlace.** Quien tiene el enlace puede responder la cotización en
nombre del cliente. Por eso la orden muestra a todos si el enlace existe y cuántas
veces se abrió, pero el enlace mismo se pide con «Mostrar enlace», sólo lo recibe
quien puede anotar la decisión del cliente (`service.quotes.record_decision`) y queda
registrado quién lo pidió. Quien cotiza y no registra decisiones no puede obtenerlo.

Con `service.orders.manage` se puede **reemplazar** (el anterior deja de funcionar) o
**desactivar**. Un enlace desactivado no vuelve solo: hay que crear uno nuevo.

**Con cuenta.** Quien inició sesión ve sus reparaciones en `/repairs`. Una orden que
se dejó en el mostrador sin cuenta se suma ahí con **dos cosas**: su enlace y el
número de documento con el que el cliente se registró en la tienda. El enlace abre
una orden; sumarla a la cuenta da acceso a todo el historial de ese cliente, y eso no
puede depender de un enlace que se reenvía.

- No se vincula nada por correo, teléfono o nombre.
- Cinco intentos fallidos bloquean la vinculación de ese cliente durante una hora.
- Un cliente sin documento registrado se vincula en la tienda.
- Si la cuenta ya tiene otro registro de cliente en la tienda, se indica que la
  tienda debe unirlos.
- La tienda puede deshacer una vinculación equivocada (con
  `service.customers.manage`; hoy por la API, sin pantalla: CUSTOMER-UNLINK-UI).

## 3. Cotizaciones: quién aprobó y por dónde

La decisión es del cliente y hay tres caminos. El registro dice cuál fue:

| Camino | Quién pulsa | Queda como |
|---|---|---|
| Su cuenta (app) | el cliente | cliente · cuenta del cliente |
| Su enlace de seguimiento | el cliente | cliente · enlace de seguimiento |
| Se lo dijo a alguien del taller | el personal | personal · presencial, llamada, WhatsApp u otro |

- Anotar la respuesta del cliente necesita la capacidad
  **`service.quotes.record_decision`** («Registrar decisión del cliente»). La tienen
  Administrador, Ventas y Supervisor Técnico. No la tiene quien sólo cotiza.
- Hay que elegir por dónde respondió; la nota es opcional. Queda quién lo anotó.
- La misma respuesta dos veces es una sola. La contraria es un conflicto.
- **Una aprobación vale para lo que se aprobó.** Una cotización aprobada no se edita.
  Si el trabajo cambia antes de empezar la reparación, «Volver a cotizar» (con su
  motivo) anula la aprobación, devuelve la orden a diagnóstico y abre una revisión
  nueva que el cliente tiene que aprobar otra vez.

**Ticket.** Al registrar una aprobación se intenta imprimir el ticket de 80 mm; si el
diálogo de impresión no se abre, el botón «Imprimir ticket» sigue ahí. El ticket
lleva la tienda, el número de orden y la revisión, el cliente, el equipo con serie e
IMEI enmascarados, las líneas, el total, «APROBADA», por dónde se aprobó, cuándo y
quién atendió. No es un comprobante de pago y lo dice. Sólo existe para una
cotización aprobada y vigente: una reemplazada ya no se imprime.

## 4. Avisos por WhatsApp

Los avisos salen por la **API oficial de WhatsApp Business (Cloud API de Meta)**, con
el número y las plantillas aprobadas de cada empresa. No se usa WhatsApp Web ni
ninguna automatización de navegador.

Un mensaje es un resumen y el enlace de seguimiento: nombre, número de orden y
enlace. No lleva IMEI, serie, notas ni importes.

| Aviso | Cuándo |
|---|---|
| Equipo recibido | al crear la orden |
| Cotización lista | al publicarla |
| Avance | al empezar la reparación y al quedar esperando un repuesto |
| Equipo listo para recoger | al pasar el control de calidad |
| Equipo entregado | al entregar |

**Consentimiento.** Tener un teléfono no es permiso para escribirle. En la orden,
«Avisos al cliente» permite registrar que el cliente acepta (o que ya no). Si el
cliente responde BAJA o STOP, deja de recibir avisos. Sin consentimiento el aviso
queda como «no enviado», con el motivo.

**Qué se ve en la orden.** Cada aviso muestra su estado tal como lo informa el
proveedor: pendiente, enviado, entregado, leído, falló o no enviado, con el motivo y
el número enmascarado. Un mensaje que falló se puede reintentar con
`service.orders.manage`.

**Un fallo no deshace nada.** La orden avanza aunque el mensaje no salga; el fallo
queda anotado y se reintenta con espera creciente (1 min, 5 min, 30 min, 2 h; cinco
intentos). Un mismo aviso no se envía dos veces.

### 4.1 Configuración (por empresa)

1. **Quien administra la instalación** define tres variables de entorno con el
   prefijo `WHATSAPP_` (token de acceso, secreto de la aplicación y token de
   verificación del webhook) y las enlaza con la empresa:

   ```
   python manage.py configure_whatsapp <empresa> \
       --phone-number-id <id del número> \
       --access-token-env WHATSAPP_TOKEN_<EMPRESA> \
       --app-secret-env   WHATSAPP_SECRET_<EMPRESA> \
       --verify-token-env WHATSAPP_VERIFY_<EMPRESA>
   ```

   En la base de datos se guarda el NOMBRE de cada variable, nunca el secreto. Sólo
   se aceptan nombres que empiecen por `WHATSAPP_`.
2. **El administrador de la empresa**, en Administración › Mensajería, escribe el
   nombre de la plantilla aprobada de cada aviso, el código de país por defecto y el
   idioma, y activa el envío. La pantalla dice si las credenciales están o faltan;
   no tiene dónde escribirlas.
3. En Meta se registra el webhook que muestra esa pantalla
   (`/api/v1/webhooks/whatsapp/<empresa>/`) con el token de verificación.

Cada plantilla recibe tres parámetros, en este orden: `{{1}}` nombre del cliente,
`{{2}}` número de orden, `{{3}}` enlace de seguimiento. Un aviso sin plantilla no se
envía.

`WHATSAPP_PROVIDER` decide el entorno: `cloud_api` (real), `fake` (anota lo que
enviaría, para pruebas y desarrollo) o `disabled`. La tarea programada está en
`docs/despliegue-produccion.md` §6.1.3.

## 5. Inventario › Equipos

Un producto puede controlarse **por número de serie**. Cada equipo es entonces una
unidad con su serie, IMEI, segundo IMEI, condición, estado, costo y precio.

**No es un inventario aparte.** El stock del producto en una sucursal es, siempre, la
cantidad de equipos disponibles en ella. Por eso:

- el stock de un producto con serie no se ajusta por cantidad: ni ajuste manual, ni
  stock inicial, ni carga masiva, ni transferencia, ni recuento. Cada intento se
  rechaza y dice por qué;
- cada operación sobre un equipo deja su línea en el Kardex.

| Operación | Efecto | Movimiento |
|---|---|---|
| Registrar equipos (lote) | entran como disponibles; todo el lote o nada | entrada por compra |
| Apartar / liberar | deja de estar a la venta / vuelve | salida manual / entrada manual |
| Dar de baja | sale del stock, con motivo | salida por daño |
| Venta (caja o web) | se asignan los equipos más antiguos de la sucursal | salida por venta |
| Registrar devolución | el mismo equipo vuelve a estar disponible | entrada por devolución |

- La misma serie o el mismo IMEI no entran dos veces en una empresa, en ningún
  estado. Un equipo vendido conserva su fila.
- Dos cajas no pueden vender el último equipo: una vende y la otra recibe «stock
  insuficiente».
- Un producto sólo cambia de modo (con serie / sin serie) mientras su stock es cero.
- Ver necesita `inventory.view`; registrar, apartar o dar de baja, `inventory.adjust`.
  Todo queda dentro de las sucursales de quien opera.

El tablero muestra **Equipos disponibles**: es parte de las «Unidades en stock», no
se suma a ellas.

Todavía no: transferir equipos entre sucursales, recuento por lectura de series y
elegir un equipo concreto al vender (hoy se asigna el más antiguo y se cobra el
precio de catálogo; el precio propio de un equipo se guarda y se muestra).

## 6. Portada: categorías y carrusel

En Productos › Categorías cada tienda decide qué familias ofrece (activa), cuáles
ilustra su portada y en qué orden. Retirar una categoría no oculta sus productos.
La imagen de cada categoría se sigue colocando en Administración › Escaparate.

El carrusel de productos se arrastra con el ratón, no trabaja fuera de pantalla, no
retiene la rueda de la página y respeta «reducir movimiento».

## 7. «Continuar con Google»

Aparece en la página de acceso sólo si la instalación tiene `GOOGLE_OAUTH_CLIENT_ID`.

- El navegador recibe de Google un token y lo entrega al servidor, que comprueba la
  firma, que es para esta aplicación, que no venció, que el correo está verificado y
  que el intento empezó en este sitio. La sesión es la de siempre (cookies HttpOnly).
- No se guarda ningún token de Google ni se inventa una contraseña.
- La primera vez crea la cuenta. Si ese correo ya tenía cuenta, no se enlaza solo:
  se pide la contraseña de esa cuenta y entonces queda vinculada.

En Google Cloud, el «origen de JavaScript autorizado» es el dominio público de la
tienda. No hay secreto de cliente.

## 8. Lo que falta y sólo puede dar el propietario

| Dato | Para qué |
|---|---|
| Número de WhatsApp Business, plantillas aprobadas por Meta, token, secreto de la aplicación y token de verificación | Enviar avisos reales |
| ID de cliente OAuth de Google con el dominio autorizado | Mostrar «Continuar con Google» |

Hasta entonces: los avisos quedan en la cuenta del cliente y en el enlace de
seguimiento, y el acceso es con usuario y contraseña.
