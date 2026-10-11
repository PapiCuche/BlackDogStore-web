# Pagos con Izipay, equipos con serie y documentos de venta

Guía de uso y de operación de la fase PAYMENTS-EQUIPMENT-DOCUMENTS. Las decisiones y
su porqué están en `05_DECISIONES_TECNICAS.md` (DEC-PAY-01, DEC-PAY-02,
DEC-UNIT-IMPORT-01, DEC-DOC-01). Nada de esto está publicado en Internet.

## 1. Izipay: dos productos, uno por instalación

Izipay vende **dos integraciones distintas**. Usan credenciales distintas, otro guion
en el navegador y otra firma; no son dos versiones de lo mismo.

| | «SDK web / Checkout» | «Mi Cuenta Web» (API REST V4) |
|---|---|---|
| Documentación | developers.izipay.pe | secure.micuentaweb.pe/doc/es-PE/rest/V4.0/javascript/ |
| `PAYMENT_PROVIDER` | `izipay` (el valor por defecto) | `micuentaweb` |
| Credenciales | código de comercio, clave pública, API key, clave hash | usuario, contraseña, clave pública, clave HMAC-SHA-256 |
| Variables | `IZIPAY_*` | `MICUENTAWEB_*` |
| Notificación | `/api/payments/izipay/notification/` | `/api/payments/micuentaweb/notification/` |

La plataforma tenía integrado el primero. La página oficial que indicó el propietario
documenta el segundo, que se añadió **al lado**, sin tocar el primero. Una instalación
usa uno: `PAYMENT_PROVIDER` dice cuál, el pago se abre sólo con ese y la notificación
del otro responde 404. Se cambia de uno a otro sólo cuando no hay pagos esperando
respuesta.

**Falta saber de cuál de los dos tiene credenciales el propietario.** Con eso se pone
`PAYMENT_PROVIDER` y sus variables; el otro grupo se deja vacío.

### 1.1 «Mi Cuenta Web», punto por punto contra la documentación

Revisado el 2026-10-11 contra las páginas oficiales (cada fila dice cuál; todas cuelgan
de `https://secure.micuentaweb.pe/doc/es-PE/`). El sitio de la documentación es una
aplicación: su texto se leyó de su propia API de contenido, no de memoria.

| Punto | Lo que dice la documentación | Lo que hace la plataforma |
|---|---|---|
| Crear el pago | `POST https://api.micuentaweb.pe/api-payment/V4/Charge/CreatePayment`, desde el servidor (`rest/V4.0/javascript/guide/formToken/presentation.html`) | Igual. El servidor es el único que lo llama |
| Autenticación | `Authorization: Basic base64(usuario:contraseña)` (`rest/V4.0/api/kb/authentication.html`) | Igual. La contraseña no sale del backend |
| Importe | entero en la unidad mínima (`30050` = S/ 300,50) (`rest/V4.0/api/playground/Charge/CreatePayment`) | `Order.total` en céntimos, sin pasar por decimales binarios |
| Moneda, pedido, cliente | `currency` ISO 4217, `orderId` (hasta 64 caracteres: letras, cifras, `_` y `-`), `customer.email` | `PEN`, un `orderId` nuevo de 20 cifras por intento, el correo del pedido |
| Notificación | `ipnTargetUrl` o la URL del Back Office | La «Dirección de notificación» de la consola si está; si no, la del Back Office |
| Respuesta | `answer.formToken`, que vale 15 minutos | Se entrega al navegador junto con la clave pública |
| Navegador | guion `…/krypton-client/V4.0/stable/kr-payment-form.min.js` con `kr-public-key`; tema `…/ext/neon.js` y `…/ext/neon-reset.min.css`; `<div class="kr-smart-form" kr-form-token>` (`rest/V4.0/javascript/redirection/quick_start_smartform.html`) | Igual. Las tres direcciones son constantes del código, nunca un dato de la respuesta |
| Marcos compilados (React) | «requiere» la biblioteca `embedded-form-glue` (`rest/V4.0/javascript/spa/index_smart.html`) | **Distinto.** Se inserta el mismo guion con los mismos atributos, sin añadir una dependencia. Sin comprobar en un navegador contra la pasarela real (KRYPTON-GLUE) |
| CSP | sólo si el sitio la usa: `connect-src`, `frame-src` y `script-src` deben admitir `https://static.micuentaweb.pe` (`rest/V4.0/javascript/features/reference.html`) | La política del sitio es `frame-ancestors 'none'; base-uri 'self'`: no limita guiones ni conexiones, así que no hay nada que abrir, y no lleva `*` ni `unsafe-eval`. Una prueba fija esas dos directivas |
| TEST o PRODUCCIÓN | lo decide el juego de claves (`testpassword_…` / `prodpassword_…`) | Igual. Un par mezclado impide cobrar; un pago de TEST no paga un pedido con claves de producción, ni al revés |
| Notificación (IPN) | formulario con `kr-answer`, `kr-hash`, `kr-hash-algorithm`, `kr-hash-key`, `kr-answer-type` (`rest/V4.0/api/kb/ipn_usage.html`) | Igual |
| Firma del IPN | HMAC-SHA256 en hexadecimal de `kr-answer`, con la **contraseña**. El ejemplo oficial lo calcula tras cambiar `\/` por `/` | Igual, en tiempo constante: sobre la cadena recibida o, como el ejemplo, con las barras sin escapar. Se lee lo que cubrió la firma |
| Retorno al navegador | la misma respuesta firmada con la **clave HMAC**; «los datos recibidos no deben ser procesados» (`rest/V4.0/kb/payment_done.html`, `rest/V4.0/api/kb/ipn.html`) | No se usa para confirmar nada. Enviada a la notificación, se rechaza |
| Pagado | `orderStatus = PAID` (`rest/V4.0/api/kb/status_reference.html`) | Sólo entonces, y si importe, moneda, tienda y pedido coinciden con lo guardado |
| `UNPAID` / `RUNNING` / `PARTIALLY_PAID` | rechazado / en curso / pago parcial | Un rechazo cierra el intento sólo cuando su formulario ya no puede cobrar (30 minutos); hasta entonces, y en los otros dos casos, sigue esperando. El pedido no cambia |
| Reintentos del formulario | 3 reintentos (4 intentos) con la misma referencia (`rest/V4.0/javascript/features/retry.html`) | Un rechazo seguido de un pago, bajo la misma referencia, paga el pedido |
| Qué respuesta espera el IPN | falla si la tienda no contesta `200`–`206`, `301`, `302`, `303`, `307` o `308` (`back-office/notification-rules/reejecucion-automatica-en-caso-de-fallo.html`) | `200` a lo aceptado, también a un rechazo o a un pago en curso; `400`, `404`, `413` o `500` a lo que no se cree: la pasarela lo da por fallido y avisa por correo al comercio |
| Reintentos del IPN | hasta 4, cada 15 minutos, sólo si el comercio marcó «Reejecución automática»; con un `4xx` o un `5xx` «no se puede realizar» (`rest/V4.0/api/kb/ipn.html`) | La misma notificación dos veces paga una vez. Lo que no llegue lo recoge la consulta (§1.5) |
| Consulta si el IPN no llega | `Order/Get` con el `orderId`: la lista de transacciones de esa referencia. Una llamada por minuto como mucho (`rest/V4.0/api/playground/Order/Get`) | `python manage.py reconcile_payments`, cada cinco minutos (§1.5) |
| Errores de los servicios | `INT_…`: problema de integración del vendedor (`INT_905`, usuario o contraseña no válidos); `PSP_010`: no se encontró la transacción (`rest/V4.0/api/errors_int.html`, `errors_psp.html`) | Un `INT_…`, un `401`, un `403` o un `429` terminan la pasada de consulta. `PSP_010` no se toma por «no hay nada» |

### 1.2 Lo que no cambia con ninguno de los dos

- La tarjeta se escribe en el formulario de la pasarela. Ni el número, ni la fecha ni
  el código pasan por la tienda.
- Lo que el navegador diga no marca nada como pagado: al terminar el formulario sólo
  se pasa a la pantalla que pregunta al servidor.
- Un pedido se paga cuando llega la notificación del servidor de la pasarela, firmada,
  y coincide con lo que la base ya sabía. Repetida, paga una vez.
- La notificación no lleva sesión ni límite de peticiones (limitarla sería limitar el
  enterarse de que alguien pagó). Lo que la protege es la firma y un tope de 128 KB por
  mensaje.

### 1.3 Configurar «Mi Cuenta Web» en TEST, desde el panel

Lo hace un usuario MASTER. Las claves se escriben en el panel y se guardan cifradas; no
hace falta tocar el servidor ni enviárselas a nadie.

1. **En el Back Office de Izipay** (`https://secure.micuentaweb.pe/vads-merchant/`):
   Configuración › Tienda › pestaña **Claves de la API REST**. De la columna de **TEST**
   se necesitan cuatro: usuario, contraseña (`testpassword_…`), clave pública
   (`testpublickey_…`) y clave HMAC-SHA-256. Si la pestaña no aparece, hay que pedírsela
   a Izipay.
2. **En el panel de la tienda:** Configuración › Integraciones › tarjeta **Izipay · Mi
   Cuenta Web** › «Configurar». Rellenar:

   | Campo | Qué va |
   |---|---|
   | Usuario | el usuario (8 cifras) |
   | Clave pública | `<usuario>:<clave pública>`, por ejemplo `12345678:testpublickey_…` |
   | Contraseña | la de TEST. Tras guardarla no se vuelve a mostrar |
   | Clave HMAC-SHA-256 | la de TEST. Opcional: la tienda no la usa para confirmar pagos |
   | Dirección de la API | no tocar (`https://api.micuentaweb.pe`) |
   | Moneda | `PEN` |
   | Dirección de notificación | `https://<dominio>/api/payments/micuentaweb/notification/` |

3. **Guardar borrador**, **Probar conexión** y **Activar**, en ese orden. La pantalla
   debe decir **TEST**. La prueba pide a la pasarela un `formToken` de S/ 1,80 y no cobra
   nada. Activar este producto desactiva el otro de Izipay.
4. **En el Back Office:** Configuración › Reglas de notificaciones › «URL de notificación
   al final del pago» › «Gestionar la regla». En «URL de notificación de la API REST»,
   la misma dirección del paso 2 en el campo de TEST (y más adelante en el de
   PRODUCCIÓN). Marcar **Reejecución automática en caso de fallo** y escribir un correo
   para los avisos.
5. **Un pago completo de prueba, con tu sesión de MASTER iniciada.** Entra al panel con
   tu usuario MASTER y, en el mismo navegador, compra en la tienda publicada con una
   tarjeta de la barra de pruebas del formulario (o de `rest/V4.0/api/kb/test_cards.html`;
   por ejemplo `4970 1000 0000 0055`, aceptada, y `4970 1000 0000 0113`, rechazada). El
   pedido debe quedar pagado sin recargar nada a mano, y en el Back Office el estado de
   la URL de notificación de esa transacción debe decir «Enviada».
6. Izipay exige cuatro pagos de prueba antes de dar las claves de PRODUCCIÓN; la lista
   está en la misma pestaña de claves (`rest/V4.0/api/kb/testing_phase.html`).
7. **Pasar a PRODUCCIÓN:** espera media hora desde tu último pago de prueba (§1.6),
   guarda las claves de PRODUCCIÓN en la misma tarjeta, pruébalas y actívalas escribiendo
   `PRODUCCION`. Nada más activarlas, haz un pago real pequeño.

**Mientras estén activas las claves de TEST, sólo un usuario MASTER puede pagar.** A
cualquier otro comprador —con cuenta o sin ella, y también al personal de la tienda— la
tienda le dice «El pago en línea todavía no está disponible en esta tienda», sin crear
un pedido y sin hablar con la pasarela: las tarjetas de prueba son públicas y un pedido
pagado con una no es dinero. MASTER es quien puede configurar la pasarela, y por eso es
quien la prueba. Un pedido pagado en TEST queda señalado: su intento de pago guarda el
entorno y hay un registro de auditoría `payment_confirmed_in_test` con su empresa. Con
claves de PRODUCCIÓN no cambia nada para nadie.

Esto vale para las claves puestas en la consola. Una instalación que cobra con las
variables del servidor (`PAYMENT_PROVIDER` y `MICUENTAWEB_*` / `IZIPAY_*`) no tiene esta
restricción: así se configuran las pasarelas simuladas de las pruebas y del ensayo. En
una tienda publicada, las claves de TEST van siempre en la consola (PAY-TEST-ENV).

Si el servidor filtra por dirección de origen, hay que permitir `194.50.38.0/24`. La
plataforma no filtra por origen: se fía de la firma.

Sin consola, la instalación sigue leyendo `PAYMENT_PROVIDER=micuentaweb` y las variables
`MICUENTAWEB_*` del servidor; en cuanto la consola tiene una configuración activa, manda
ella.

### 1.4 Lo que sólo se puede comprobar con credenciales

Todo lo anterior se prueba contra una pasarela falsa que exige el contrato publicado.
Que el entorno real acepte exactamente esas peticiones lo comprueban dos pruebas que se
omiten sin claves:

```
MICUENTAWEB_SANDBOX_SHOP_ID=<usuario> MICUENTAWEB_SANDBOX_PASSWORD=<contraseña de TEST> \
  python manage.py test store.test_micuentaweb.MiCuentaWebSandboxSmokeTest
```

Una pide un `formToken`; la otra pregunta por una referencia que nadie pagó, que es lo
que la documentación no describe. Lo mismo vale para el otro producto
(`IzipaySandboxSmokeTest`, IZIPAY-TOKEN-CONTRACT). Ninguna hace un cargo.

Sigue sin verse hasta que haya claves: una notificación real (si llega con las barras
escapadas, qué `detailedStatus` trae), la respuesta real de `Order/Get` y el formulario
dibujado en un navegador.

### 1.5 Si la notificación no llega: preguntar a la pasarela

`python manage.py reconcile_payments` pregunta a «Mi Cuenta Web» (`Order/Get`) por los
pagos que siguen esperando. Está en `deploy/crontab.example`, cada cinco minutos.

- **A quién pregunta.** Sólo con «Mi Cuenta Web» activo, y sólo por sus intentos que
  siguen sin respuesta, abiertos hace más de 3 minutos y menos de 2 horas. Hasta 20 por
  pasada, de uno en uno, **primero por quien lleva más tiempo sin que nadie pregunte por
  él**: ninguno se queda sin turno. Si la pasarela no contesta, o rechaza a quien llama
  (contraseña que no acepta, demasiadas llamadas, petición que dice mal formada: `401`,
  `403`, `429` o un código `INT_…`), la pasada termina ahí y lo dice.
- **Qué cree.** Una respuesta sobre esta tienda y esta referencia, del mismo entorno
  que las claves (TEST o PRODUCCIÓN), con **un** cobro pagado (`AUTHORISED` o
  `CAPTURED`) por el importe completo. Después pasa las mismas comprobaciones que una
  notificación: importe, moneda, tienda, referencia, y que el intento se abrió con esta
  misma tienda y este mismo entorno.
- **Qué cierra.** Un intento cuyo formulario ya no puede cobrar (más de 30 minutos) y
  del que la pasarela no tiene nada queda **«Sin intento de pago»**: el comprador abrió
  el formulario y no llegó a probar una tarjeta. No vuelve a consultarse solo. Un
  rechazo cierra el intento pasados esos 30 minutos; antes no, llegue por consulta o por
  notificación, porque el formulario deja reintentar con otra tarjeta.
- **Qué no decide, y avisa.** Dos cobros pagados bajo una referencia, un reembolso junto
  al cobro o un importe parcial: el intento sigue esperando, la primera vez queda un
  registro de auditoría `payment_undecided` con la empresa del pedido, y la comprobación
  de estado lo dice como **ATENCIÓN**. Hay que mirarlo en el panel de la pasarela.
- **Cobro duplicado.** Si la pasarela cobró un intento de un pedido que ya estaba
  pagado por otro, ese intento queda **«Cobro duplicado»** —no como un pago—, con un
  registro `payment_duplicate` y **ATENCIÓN** en la comprobación de estado. El pedido no
  cambia. Hay que devolver ese dinero desde el panel de la pasarela.
- **Qué deja escrito.** El intento queda autorizado **sin** firma verificada (la
  respuesta no trae firma) y, en la misma operación que el pago, un registro de auditoría
  `payment_reconciled` con la empresa del pedido. Varias pasadas a la vez, o la
  notificación llegando después, pagan una vez y dejan un registro.
- **Un cliente dice que pagó:** `python manage.py reconcile_payments --reference
  <referencia>`, con la referencia del pago (las 20 cifras que la pasarela muestra como
  número de pedido). Pregunta por ese intento tenga la antigüedad que tenga, aunque se
  cerrara como rechazado o sin intento. `--hours N` amplía la ventana para todos.
- **Otro formulario para el mismo pedido.** La app puede repetir su petición y pedir un
  formulario nuevo para un pedido que ya tiene uno. Antes de abrirlo se pregunta a la
  pasarela por el anterior: si está pagado, el pedido queda pagado y no hay segundo
  formulario; si no se puede preguntar, tampoco, hasta que se pueda.
- **El otro producto de Izipay no tiene esta consulta.** Con él, se comprueba en su panel.

**Límites conocidos.**

- Un medio de pago que tarda más de dos horas en resolverse (un pago en efectivo en
  agencia, una transacción «por validar») sale de la ventana sin respuesta: lo confirma
  su notificación cuando llegue, o `--reference`.
- Los intentos abiertos antes de esta versión no guardan con qué tienda ni en qué
  entorno se abrieron: se juzgan como hasta ahora.
- «Dirección de la API» sólo admite el servidor que nombra la documentación,
  `https://api.micuentaweb.pe`: la contraseña viaja en cada llamada y no se envía a otro
  sitio ni sigue una redirección.

**Lo que tiene que contestar la prueba con claves de TEST** (hoy no se sabe; el código
no lo supone):

1. Qué responde `Order/Get` de una referencia sin ninguna transacción: lista vacía o un
   código de error. Si es un código, va en `NO_TRANSACTION_CODES`
   (`store/payments/micuentaweb.py`); hasta entonces esos intentos no se cierran solos y
   cuentan como «errores de la pasarela». (`PSP_010`, «no se encontró la transacción»,
   cubre también una transacción del otro entorno o de otra tienda: por eso no se da
   por bueno.)
2. Si `Order/Get` sin `operationType` devuelve también reembolsos y verificaciones.
3. Si cada transacción trae `orderDetails.mode` y `shopId`.
4. Si se envía una notificación por cada tarjeta rechazada dentro del mismo formulario.
5. Si `kr-answer` llega con las barras escapadas.
6. Si «una llamada por minuto» es por referencia o por tienda.

### 1.6 Cambiar de pasarela con cobros abiertos

Un comprador que ya tiene delante el formulario de la tarjeta paga contra la pasarela
que había cuando lo abrió. Hay cambios después de los cuales ese pago ya no se podría
confirmar ni consultar: **activar el otro producto, apagar o revocar el que cobra,
cambiar de tienda (usuario) o cambiar de entorno (TEST ↔ PRODUCCIÓN).** El servidor no
los hace a ciegas.

Con «Mi Cuenta Web» cobrando, al pedir uno de esos cambios:

1. El servidor pregunta primero a la pasarela por los cobros abiertos en las dos últimas
   horas, con las claves que todavía están (la misma consulta de §1.5). Lo que esté
   cobrado queda pagado antes del cambio.
2. **Rechaza el cambio** —no cambia nada, y escribir `INTERRUMPIR` no lo salta— si algún
   cobro se abrió hace menos de 30 minutos (su formulario aún puede cobrar) o si la
   pasarela lo tiene sin resolver (en curso, o con una respuesta que hay que mirar en su
   panel: un reembolso, dos cobros…). El mensaje dice cuántos son y a partir de qué hora
   reintentar.
3. Lo hace si no queda ninguno así: los que quedan son antiguos y la pasarela no tiene
   nada de ellos.
4. Si la pasarela **no responde**, no se puede comprobar: pide escribir `INTERRUMPIR`, y
   deja en la auditoría (`payments_interrupted`, por empresa) los intentos que quedaron
   sin comprobar, para revisarlos en el panel de la pasarela.

**Renovar la contraseña, la clave pública o la clave HMAC de la misma tienda y el mismo
entorno no es un cambio de pasarela**: se permite con cobros abiertos, y el comprador
que estaba pagando paga.

Con el otro producto («SDK web / Checkout») no hay consulta posible: sigue pidiendo
`INTERRUMPIR` cuando hay cobros abiertos en la última hora, y ahora deja el mismo
registro de auditoría con los intentos interrumpidos.

Un checkout que empezó justo antes del cambio y va a escribir su intento justo después
no lo escribe: al comprador se le pide reintentar («La pasarela de pago acaba de
cambiar») y paga contra lo que hay.

Lo que el servidor no puede vigilar: cambiar `PAYMENT_PROVIDER` o las claves en las
variables del servidor y reiniciar. Eso no pasa por la consola.

## 2. Inventario › Equipos: registrar un equipo

Inventario › Equipos › **+ Registrar equipo**.

El formulario muestra desde el principio lo que identifica al equipo: sucursal,
producto, condición, **número de serie**, **IMEI**, IMEI 2, costo y el motivo o
documento de ingreso. Un equipo no tiene precio propio: se vende al precio de su
producto (SERIAL-PICK-01). «Guardar y añadir otro» conserva sucursal, producto y
motivo para el siguiente.

- **Un equipo es una unidad.** Dos teléfonos del mismo modelo son dos registros, cada
  uno con su serie y su IMEI. No hay una cantidad que los sustituya.
- Si el producto no se controla por serie, la pantalla lo dice y, con permiso y stock
  en cero, ofrece activarlo ahí mismo. No esconde los campos.
- El IMEI se pide sólo si el producto lleva línea celular. Lo que el equipo no tiene se
  deja vacío; «N/A» se rechaza.
- Un error se muestra junto a su campo y no borra lo escrito.

## 3. Cargar muchos equipos desde Excel

Inventario › **Carga de equipos** (también desde Inventario › Equipos › «Cargar desde
Excel»).

Hay tres cargas y ninguna hace el trabajo de otra (IMPORT-SPLIT-01):

| Carga | Qué hace | Dónde |
|---|---|---|
| Productos y accesorios | Crea y edita el catálogo. No mueve stock | Productos › Carga de productos |
| Equipos | Una fila = un equipo físico, con su serie e IMEI | Inventario › Carga de equipos |
| Stock | Cantidades de lo que se cuenta por número | Inventario › Carga de stock |

La plantilla de equipos, `carga-masiva-equipos.xlsx`, es una sola hoja, «Equipos»: la
fila de cabeceras y nada debajo. Cada cabecera lleva una nota con lo que va debajo:

| Columna | Qué lleva |
|---|---|
| Código | Código o código de barras del producto |
| Producto | Nombre exacto, si no hay código |
| Sucursal | Nombre de la sucursal donde entra |
| Número de serie | Obligatorio |
| IMEI | Obligatorio si el producto está marcado «lleva IMEI» |
| IMEI 2 | Opcional; sólo con el primer IMEI indicado, y distinto de él |
| Condición | Nuevo, Caja abierta, Reacondicionado o Usado. Vacío = Nuevo |
| Costo | Opcional |
| Motivo / referencia | Factura de compra, guía, inventario inicial |

**Una fila = un equipo físico.** No hay columna de cantidad; un archivo que traiga una
columna de cantidad o de stock («Cantidad», «Cantidad inicial», «Stock inicial», «Nº de
unidades», «Inventario»…) se rechaza entero.

Tres formas de fila, según lo que lleve el producto: **serie sola**, **serie + IMEI** o
**serie + dos IMEI**. Si un equipo trae IMEI y su producto no está marcado «lleva IMEI»,
el IMEI se guarda y la previsualización lo avisa (la fila puede estar bajo el producto
equivocado).

- `Sucursal` ofrece en una lista las sucursales en las que puede cargar quien descarga
  la plantilla. `Condición` ofrece las cuatro que conoce el sistema. Las listas ofrecen y
  no bloquean. Si los nombres de las sucursales no caben en una lista de celda (más de
  255 caracteres en total, o un nombre con coma, comillas o que empieza por `=`, `+`, `-`
  o `@`), la plantilla sale sin esa lista y la sucursal se escribe.
- Si alguna fila trae IMEI y su producto no está marcado «lleva IMEI», la
  previsualización lo dice arriba de la tabla, con cuántas, y en cada fila.
- Serie, IMEI y código van como texto: Excel no les quita ceros ni los convierte en
  número.
- Un archivo hecho con la plantilla anterior (con su fila de ejemplo y su hoja
  «Instrucciones») se sigue leyendo.

1. Descargar la plantilla.
2. Adjuntarla. Se puede elegir una sucursal y un motivo para las filas que no los
   traigan.
3. **Previsualizar.** No registra nada. Muestra qué pasaría con cada fila y, si alguna
   tiene un error, cuál: sin serie, IMEI que no supera el control, producto que no
   existe o no lleva serie, sucursal desconocida o ajena, repetida en el archivo o ya
   registrada.
4. **Registrar N equipos.** Entra todo el archivo o nada. Si algo cambió desde la
   previsualización (alguien registró a mano uno de esos equipos), no entra ninguno y
   se dice en qué fila.

Cada equipo entra por el mismo camino que «Registrar equipo»: mismas reglas, misma
línea de Kardex por producto, sucursal y documento. Hasta 1000 equipos por archivo.
Hace falta `inventory.adjust` y acceso a cada sucursal del archivo.

## 4. Documentos de venta

La nota de venta (A4 y ticket de 80 mm), el comprobante de pedido y el ticket de
cotización comparten un mismo diseño: cabecera, tipografía, tabla, totales y pie.

**Qué lleva la nota de venta A4**

- **Cabecera:** logotipo, nombre comercial, razón social, dirección fiscal, sucursal,
  teléfono y correo. A la derecha, enmarcado: RUC, «NOTA DE VENTA INTERNA» y el número.
- **Cliente y venta:** nombre, documento, teléfono, entrega; fecha y hora, pedido, tipo
  de pago, canal, quién atendió y comprobante solicitado.
- **Detalle:** Nº, código, descripción, U.M., precio de lista, descuento, precio
  unitario, cantidad e importe.
- **Equipos con serie:** bajo la línea, `Serie`, `IMEI` e `IMEI 2` del equipo que salió
  del stock en esa venta. Un producto sin serie no imprime identificadores.
- **Totales:** productos y unidades, importe en letras, subtotal, descuento, operación
  gravada, IGV e importe total.
- **Pie:** el aviso de que es un documento interno, la garantía de la tienda y, en cada
  página, de quién es el documento, su número y «Página X de Y».

**De dónde sale cada dato.** Todo viene de la empresa, la sucursal, la venta y la
configuración de la tienda. La identidad es la que la empresa tenía al vender. El
logotipo se elige en la configuración de la empresa («Logotipo de los comprobantes») y se
guarda con cada nota al emitirla: una reimpresión lleva el mismo.

**Serie e IMEI no se escriben a mano.** Se leen de los movimientos de Kardex de la
venta. Si el equipo se devuelve después, la reimpresión de aquella venta sigue
nombrándolo.

**Una nota de venta interna no es un comprobante SUNAT.** Lo dice enmarcada junto al
total, y su número dice que no es una serie fiscal. Las boletas y facturas electrónicas
no cambiaron: su formato es el que fija la norma.

**Descuento por línea.** La venta guarda un precio por línea y el descuento del pedido
entero. La columna existe y hoy muestra 0.00; el descuento va con los totales
(NOTE-LINE-DISCOUNT).

**Ticket de cotización.** Misma cabecera que el de venta. Lleva la nota que la tienda
escribió para el cliente («Detalle del trabajo»); el diagnóstico del técnico y las
notas internas no se imprimen.

**Revisar el diseño.** `DOCUMENT_SAMPLES_DIR=<carpeta> python manage.py test
store.test_document_design store.test_quote_ticket` deja en esa carpeta un PDF de cada
caso (con serie, sin serie, descuento, logotipo, varias páginas, textos largos,
ticket).
