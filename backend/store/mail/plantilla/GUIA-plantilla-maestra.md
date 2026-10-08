# Plantilla maestra

Una sola plantilla HTML sirve para todos los correos que envía la web de Black Dog Store: confirmación de correo, restablecer contraseña, compras, cotizaciones, servicio técnico, envíos, invitaciones, promociones, garantías y comunicados. La web le pasa un objeto de datos y la plantilla muestra solo los bloques que reciben información. El archivo es `assets/Plantillas/plantilla-maestra.html`.

## Dos tipos de variables

- `[Corchetes]` se reemplazan **una sola vez** al instalar la plantilla: `[URL pública del logo]`, `[URL del sitio web]`, `[URL del canal de YouTube]`, `[Razón social]` y `[RUC]`.
- `{{llaves}}` las llena la web **en cada envío**, con sintaxis Mustache. Funciona igual con Handlebars (Node), mustache.php, Mustache.java, chevron o pystache (Python). `{{#bloque}}…{{/bloque}}` solo se muestra si el dato existe, y `{{^bloque}}` solo si no existe.

## Bloques (en este orden)

| Bloque | Variable | Úsalo para |
| --- | --- | --- |
| Header | fijo | Siempre. `momento_oro: true` cambia el filete a oro. |
| Imagen | `imagen {url, alt, enlace}` | Promociones, lanzamientos e invitaciones. Imagen de 1040px de ancho en una URL pública; `alt` siempre. |
| Principal | `etiqueta`, `titulo`, `saludo`, `parrafos[]` | Siempre. El título es obligatorio y los párrafos van de 1 a 3. |
| Código | `codigo {etiqueta, valor, nota}` | Confirmar correo, iniciar sesión, restablecer contraseña o un cupón. |
| Progreso | `progreso {titulo, pasos[{texto, fecha, hecho / actual / pendiente}]}` | Servicio técnico, envío o estado de un pedido. Solo un paso con `actual: true`, que se marca con la palabra "En curso" además del color. |
| Detalle | `detalle {titulo, items[{nombre, nota, valor}], resumen[{nombre, valor}], total {nombre, valor}}` | Ventas, cotizaciones, pedidos y recibos. Los montos van como texto ya formateado: "S/ 4,299.00". |
| Datos | `datos {titulo, campos[{nombre, valor}], destacados[{nombre, valor}]}` | Tarjeta negra: ficha técnica, garantía, datos de envío o de un evento. Los `campos` van en dos columnas; `destacados` va a todo el ancho, para IMEI, serie o número de seguimiento. |
| Aviso | `aviso {etiqueta, texto}` | Una sola indicación: vigencia, qué traer, comprobante adjunto. |
| Botón | `boton {texto, url, mostrar_enlace}` | Una acción por correo. Con `mostrar_enlace: true` se imprime la URL debajo; úsalo en confirmaciones y contraseñas. El botón es oro solo con `momento_oro`. |
| Secundario | `secundario {texto, enlace, url}` | Un enlace de texto, normalmente a WhatsApp. |
| Firma | `firma {cierre, nombre}` | Sin `firma` cierra solo con "Equipo Black Dog Store", útil para correos automáticos. |
| Footer | `motivo`, `baja_url`, `anio` | Siempre. `motivo` explica por qué llega el correo; `baja_url` es obligatorio en promociones e invitaciones. |
| Generales | `asunto`, `preheader`, `momento_oro` | `preheader` es la línea que se ve en la bandeja junto al asunto (40 a 90 caracteres). |

## Qué bloques usa cada correo

| Correo | Bloques |
| --- | --- |
| Confirmar correo / contraseña | principal · código · botón con enlace |
| Confirmación de compra | principal · detalle · datos (ficha) · aviso (comprobante) · botón |
| Cotización | principal · detalle · aviso (vigencia) · botón a WhatsApp |
| Servicio técnico | principal · progreso · datos (equipo) · secundario |
| Envío / delivery | principal · progreso · datos (envío) · botón de seguimiento |
| Garantía | principal · datos (ficha + IMEI) · aviso (condiciones) · botón |
| Invitación / promoción | `momento_oro` · imagen · principal · datos (evento) · botón oro · `baja_url` |
| Agradecimiento post-venta | principal · botón (reseña) · secundario (redes) |
| Comunicado interno / RR. HH. | principal · aviso · firma con nombre |

Cada caso tiene su JSON de ejemplo en `assets/Plantillas/ejemplos/`. Puedes usarlo como contrato con quien programe la web.

## Reglas al integrarla

- Escapa siempre los datos (`{{ }}`, no `{{{ }}}`): nombres y textos de clientes nunca se insertan como HTML.
- Formatea montos, fechas (DD/MM/AAAA) e IMEI antes de pasarlos; la plantilla no calcula nada.
- Si un dato no existe, no lo envíes vacío: omite la clave para que el bloque desaparezca.
- Prueba cada correo nuevo en Gmail (web y app), Outlook de escritorio y Apple Mail antes de activarlo.
