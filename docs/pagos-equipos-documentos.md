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

| Punto | Lo que dice la documentación | Lo que hace la plataforma |
|---|---|---|
| Crear el pago | `POST https://api.micuentaweb.pe/api-payment/V4/Charge/CreatePayment`, desde el servidor | Igual. El servidor es el único que lo llama |
| Autenticación | `Authorization: Basic base64(usuario:contraseña)` | Igual. La contraseña no sale del backend |
| Importe | entero en la unidad mínima (`180` = S/ 1,80) | `Order.total` en céntimos, sin pasar por decimales binarios |
| Moneda, pedido, cliente | `currency`, `orderId`, `customer.email` | `PEN`, un `orderId` nuevo por intento, el correo del pedido |
| Notificación | `ipnTargetUrl` o la URL del Back Office | `MICUENTAWEB_IPN_URL` si está; si no, la del Back Office |
| Respuesta | `answer.formToken` (vale 15 minutos) | Se entrega al navegador junto con la clave pública |
| Navegador | guion `kr-payment-form.min.js` con `kr-public-key`; `<div class="kr-smart-form" kr-form-token>` | Igual. La dirección del guion es una constante del código, nunca un dato de la respuesta |
| TEST o PRODUCCIÓN | lo decide el par de claves (`testpassword_…` / `prodpassword_…`) | Igual. Un par mezclado impide cobrar; un pago de TEST no paga un pedido con claves de producción |
| Notificación (IPN) | formulario con `kr-answer`, `kr-hash`, `kr-hash-algorithm`, `kr-hash-key` | Igual |
| Firma del IPN | HMAC-SHA256 en hexadecimal de `kr-answer`, con la **contraseña** | Igual, sobre la cadena exacta recibida y en tiempo constante |
| Retorno al navegador | la misma respuesta firmada con la **clave HMAC** | No se usa para confirmar nada. Enviada a la notificación, se rechaza |
| Pagado | `orderStatus = PAID` | Sólo entonces, y si importe, moneda, tienda y pedido coinciden con lo guardado |
| `UNPAID` / `RUNNING` | rechazado / en curso | El intento queda rechazado / sigue esperando. El pedido no cambia |
| Reintentos del IPN | hasta 4, cada 15 minutos | La misma notificación dos veces paga una vez |
| Consulta si el IPN no llega (`Order/Get`) | disponible | **No implementado** (PAY-RECONCILE) |
| Respuesta que espera el IPN | no figura en las páginas leídas | Se responde 200 a lo aceptado y 400/404 a lo rechazado. **Sin verificar** |

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

### 1.3 Configurar «Mi Cuenta Web»

1. En el Back Office: Configuración › Tienda › Claves de API REST. Copiar usuario,
   contraseña, clave pública y clave HMAC-SHA-256 **del mismo entorno** (TEST o
   PRODUCCIÓN).
2. En el entorno del servidor:

   ```
   PAYMENT_PROVIDER=micuentaweb
   MICUENTAWEB_SHOP_ID=<usuario>
   MICUENTAWEB_PASSWORD=<contraseña>
   MICUENTAWEB_PUBLIC_KEY=<usuario>:<clave pública>
   MICUENTAWEB_HMAC_KEY=<clave HMAC-SHA-256>
   MICUENTAWEB_IPN_URL=https://<dominio>/api/payments/micuentaweb/notification/
   ```

3. En el Back Office: Configuración › Reglas de notificaciones › «URL de notificación
   al final del pago», la misma dirección, para TEST y para PRODUCCIÓN.
4. Si el servidor filtra por dirección de origen, permitir `194.50.38.0/24`. La
   plataforma no filtra por origen: se fía de la firma.

### 1.4 Lo que sólo se puede comprobar con credenciales

Todo lo anterior se prueba contra una pasarela falsa que exige el contrato publicado.
Que el entorno real acepte exactamente esa petición lo comprueba una prueba que se
omite sin claves:

```
MICUENTAWEB_SANDBOX_SHOP_ID=<usuario> MICUENTAWEB_SANDBOX_PASSWORD=<contraseña de TEST> \
  python manage.py test store.test_micuentaweb.MiCuentaWebSandboxSmokeTest
```

Lo mismo vale para el otro producto (`IzipaySandboxSmokeTest`, IZIPAY-TOKEN-CONTRACT).
Antes de cobrar de verdad hay que hacer además un pago completo en TEST con la tienda
publicada: es la única forma de ver llegar una notificación real.

## 2. Inventario › Equipos: registrar un equipo

Inventario › Equipos › **+ Registrar equipo**.

El formulario muestra desde el principio lo que identifica al equipo: sucursal,
producto, condición, **número de serie**, **IMEI**, IMEI 2, costo, precio propio y el
motivo o documento de ingreso. «Guardar y añadir otro» conserva sucursal, producto y
motivo para el siguiente.

- **Un equipo es una unidad.** Dos teléfonos del mismo modelo son dos registros, cada
  uno con su serie y su IMEI. No hay una cantidad que los sustituya.
- Si el producto no se controla por serie, la pantalla lo dice y, con permiso y stock
  en cero, ofrece activarlo ahí mismo. No esconde los campos.
- El IMEI se pide sólo si el producto lleva línea celular. Lo que el equipo no tiene se
  deja vacío; «N/A» se rechaza.
- Un error se muestra junto a su campo y no borra lo escrito.

## 3. Cargar muchos equipos desde Excel

Inventario › Equipos › **Cargar desde Excel**.

La carga masiva de inventario escribe cantidades y rechaza los productos con serie.
Para ellos hay una plantilla propia, «Equipos serializados.xlsx»:

| Columna | Qué lleva |
|---|---|
| Código | Código o código de barras del producto |
| Producto | Nombre exacto, si no hay código |
| Sucursal | Nombre de la sucursal donde entra |
| Número de serie | Obligatorio |
| IMEI | Obligatorio si el producto lleva línea celular |
| IMEI 2 | Opcional |
| Condición | Nuevo, Caja abierta, Reacondicionado o Usado. Vacío = Nuevo |
| Costo | Opcional |
| Motivo / referencia | Factura de compra, guía, inventario inicial |

**Una fila = un equipo físico.** No hay columna de cantidad; un archivo que la traiga
se rechaza entero.

1. Descargar la plantilla (trae una fila de ejemplo, que no se carga, y una hoja de
   instrucciones).
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
