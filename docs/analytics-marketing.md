# Analítica y marketing

Cómo mide la tienda sus visitas y sus ventas con Google Analytics 4, Meta y TikTok, qué
decide quien la visita, y cómo lo configura un usuario MASTER desde **Panel ›
Configuración › Integraciones › Analítica y marketing**.

Fase ANALYTICS-MARKETING-INTEGRATIONS-01. Extiende la consola de integraciones
([integraciones-y-secretos.md](integraciones-y-secretos.md)); decisiones en
`05_DECISIONES_TECNICAS.md` (DEC-MEAS-01 a 07).

## 1. La idea en seis líneas

- **Quien visita decide.** Sin respuesta, o con un «no», no se carga ningún script de
  terceros y el servidor no envía nada. Analítica es Google; marketing es Meta y TikTok.
- **El MASTER configura, sin código.** Los identificadores y las claves se escriben en la
  consola y valen en la siguiente visita: no hay que recompilar ni reiniciar.
- **La tienda habla su propio idioma.** Las páginas dicen «se añadió al carrito»; tres
  adaptadores lo traducen. Fuera de ellos no existe `gtag`, `fbq` ni `ttq`.
- **Una venta se mide una vez**, y nace donde el pedido queda pagado: la notificación
  firmada de la pasarela. Abrir la página de «gracias» no es comprar.
- **Medir nunca estorba.** Un proveedor caído, lento o que rechaza no toca ni el checkout
  ni el pago.
- **De la persona, nada.** Se envía el pedido —importes y productos—, nunca nombre,
  correo, teléfono, documento, dirección, IMEI, serie ni un token.

## 2. Lo que ve quien visita

La primera vez aparece un aviso con tres respuestas del mismo peso:

| Botón | Qué hace |
|---|---|
| **Aceptar todas** | analítica y marketing |
| **Rechazar opcionales** | ninguna de las dos; un clic, igual que aceptar |
| **Configurar** | elegir: Necesarias (siempre), Analítica, Marketing |

Cerrar la página sin responder es no haber aceptado. La respuesta se guarda en ese
navegador (no en una cuenta) y se cambia cuando se quiera desde **«Preferencias de
cookies»**, en el pie de la tienda; el cambio vale al momento. El panel no pregunta: al
personal no se le mide.

| Categoría | Proveedores | Qué permite |
|---|---|---|
| Necesarias | — | la sesión, el carrito y el tema. No se preguntan |
| Analítica | Google Analytics 4 | saber qué se visita y cómo avanza una compra |
| Marketing | Meta, TikTok | saber si un anuncio trajo una visita o una venta |

**Lo que se rechaza en el navegador se rechaza en el servidor.** La respuesta viaja con el
pedido al empezar a pagar, y es lo único que permite al servidor enviar esa compra a un
proveedor.

## 3. Qué configura el MASTER, y de dónde saca cada dato

Los tres son de la instalación (no de cada empresa). Flujo de siempre: **guardar → probar →
activar**. Los secretos se escriben y no vuelven a mostrarse.

### 3.1 Google Analytics 4

| Campo | Qué es | Dónde está |
|---|---|---|
| **ID de medición** | `G-XXXXXXXXXX`. Público | Google Analytics › Administrar › Flujos de datos › el flujo web |
| **Secreto de API de Measurement Protocol** (opcional, secreto) | Permite que la compra la envíe el servidor | El mismo flujo › «Secretos de API de Measurement Protocol» › Crear |

- **Sin el secreto**, la compra la envía el navegador cuando el servidor confirma que el
  pedido está pagado. Si el comprador cierra la pestaña antes, esa compra no se mide.
- **Con el secreto**, la compra la envía el servidor en cuanto el pago se confirma, y el
  navegador ya no la envía (para no contarla dos veces).
- **«Probar conexión»** no puede verificar el ID: Google no ofrece cómo. Con secreto,
  comprueba el formato del evento contra el servidor de validación de Google, que no
  registra nada y —según su propia documentación— tampoco valida el ID ni el secreto. El
  resultado es «Coherente, sin verificar». Para verificarlo de verdad: Informes › Tiempo
  real, y visitar la tienda aceptando la analítica.
- **En Google Analytics**, en el flujo web › Medición mejorada, **desactiva «Cambios de
  página basados en eventos del historial del navegador»**: la tienda envía sus propias
  vistas de página y, con esa opción activa, Google contaría cada una dos veces. No es
  una cuestión de privacidad: a una dirección privada la tienda no llega nunca dentro
  de un documento donde esté Google (§6).

### 3.2 Meta

| Campo | Qué es | Dónde está |
|---|---|---|
| **Modo** | «Sólo píxel» o «Píxel y API de conversiones» | — |
| **ID del píxel** | Sólo dígitos. Público | Meta Events Manager › Orígenes de datos › el píxel |
| **Token de acceso** (secreto; sólo con API de conversiones) | Autoriza al servidor a enviar eventos | Configuración del píxel › API de conversiones › Generar token |
| **Código de evento de prueba** (opcional, temporal) | Lo que envíe el servidor aparece sólo en «Eventos de prueba» | Events Manager › Eventos de prueba |

- **Sólo píxel:** todo sale del navegador.
- **Píxel y API de conversiones:** la compra sale del navegador **y** del servidor con el
  mismo identificador de evento; Meta se queda con una.
- **«Probar conexión»**: con un código de evento de prueba, el servidor envía UN evento de
  prueba y el resultado es «Correcto» si Meta lo recibe. Sin código no se envía nada —
  probar el token crearía un evento real— y el resultado es «Coherente, sin verificar».
- **Quita el código de prueba antes de abrir la tienda.**
- **Deja apagada la «coincidencia avanzada automática»** en Events Manager (y su
  equivalente en TikTok). Con ella, el propio script del proveedor lee el correo y el
  teléfono que alguien escribe en un formulario. La tienda no carga esos scripts en las
  páginas que tienen uno (§6), pero la opción no es suya: es del panel del proveedor.

### 3.3 TikTok

| Campo | Qué es | Dónde está |
|---|---|---|
| **Modo** | «Sólo píxel» o «Píxel y Events API» | — |
| **Código del píxel** | Letras mayúsculas y números. Público | TikTok Ads Manager › Herramientas › Eventos › el píxel web |
| **Token de acceso** (secreto; sólo con Events API) | Autoriza al servidor | Configuración del píxel › Events API › Generar token |
| **Código de evento de prueba** (opcional, temporal) | Lo enviado aparece sólo en «Test Events» | Events Manager › Test Events |

Se comporta como Meta. El evento de compra se llama `Purchase`: la lista vigente de
eventos estándar de TikTok no tiene `CompletePayment`.

### 3.4 Lo que NO se configura

- **Ninguna dirección.** De dónde se carga cada script y adónde se envían los eventos son
  constantes del código, tomadas de la documentación de cada proveedor.
- **Coincidencia avanzada** (enviar correo o teléfono, aunque sea cifrado): no está
  implementada. PROPUESTA; exigiría su propio consentimiento.
- Publicación de vídeos, gestión de campañas, bandeja de mensajes o inicio de sesión con
  TikTok: NO APLICA.

## 4. Qué se mide

| En la tienda | Google Analytics | Meta | TikTok |
|---|---|---|---|
| Se abre una página | `page_view` | `PageView` | `PageView` |
| Se muestra el catálogo | `view_item_list` | — | — |
| Se elige un producto | `select_item` | — | — |
| Se ve un producto | `view_item` | `ViewContent` | `ViewContent` |
| Se busca | `search` | `Search` | `Search` |
| Se añade al carrito | `add_to_cart` | `AddToCart` | `AddToCart` |
| Se quita del carrito | `remove_from_cart` | — | — |
| Se abre el carrito | `view_cart` | — | — |
| Se pulsa «Continuar al checkout» en el carrito | `begin_checkout` | `InitiateCheckout` | `InitiateCheckout` |
| Se envía el formulario (forma de entrega) | `add_shipping_info` | — | — |
| Se abre el formulario de la tarjeta | `add_payment_info` | no¹ | no¹ |
| **El pago se confirma** | `purchase` | `Purchase` | `Purchase` |
| Registro | `sign_up` | no¹ | no¹ |
| Inicio de sesión | `login` | — | — |
| Clic en WhatsApp, correo o teléfono | `generate_lead` | `Contact` | `Contact` |

Un «—» es que ese proveedor no tiene un evento estándar para eso: no se inventa uno.

¹ Meta y TikTok tienen ese evento (`AddPaymentInfo`, `CompleteRegistration`) y los
adaptadores lo traducen, pero ocurre en una página con un formulario de datos personales
—el checkout, el acceso—, donde sus scripts no se cargan (§6). Es una decisión de
privacidad, no un olvido: por eso el inicio del checkout se dice desde el botón del
carrito, que es el último sitio donde pueden oírlo.
`LEAD` existe en el vocabulario de la tienda y en los adaptadores, pero hoy nada lo emite:
la tienda no tiene un formulario de captación.

De cada producto viajan: id, nombre, categoría, precio y cantidad. De un pedido: su
número, el total, la moneda, el impuesto y el cupón.

**Servicio técnico:** no se mide. Abrir un seguimiento, ver una cotización o aprobarla
serían eventos sobre el equipo de una persona, y sus páginas son justo las que no llevan
ningún script (§6). PROPUESTA.

## 5. La compra: una vez, y sólo si es una venta

```
el comprador pulsa «pagar»   → se guarda con el pedido lo que aceptó
la pasarela notifica, firmado → el pedido queda pagado
                                + una fila por proveedor, en esa misma transacción
tras confirmar la transacción → el servidor envía cada fila
si un proveedor falla         → se reintenta (1, 5, 30, 120 y 720 minutos), con el mismo id
```

- **Dónde nace:** en el único sitio que puede marcar un pedido como pagado. Ni la página
  de éxito, ni una notificación con la firma mal, ni una petición a la API producen una
  conversión.
- **Por qué no se duplica:** una fila por pedido, proveedor y evento, garantizado por la
  base de datos. La notificación repetida de la pasarela no hace nada. El navegador y el
  servidor usan el mismo identificador (`purchase.<pedido>`), que es lo que Meta y TikTok
  usan para quedarse con uno. En el navegador, además, se emite una vez por pedido aunque
  se recargue la página.
- **Quién la envía**, según la configuración:

  | | Navegador | Servidor |
  |---|---|---|
  | GA4 sin secreto · Meta/TikTok «sólo píxel» | sí | no |
  | GA4 con secreto | no | sí |
  | Meta/TikTok con API de servidor | sí | sí, mismo id |

- **Un proveedor caído no toca el pago.** La fila queda pendiente y la reenvía
  `python manage.py send_pending_conversions` (en el `crontab`, cada cinco minutos). Un
  rechazo —token inválido— es definitivo y queda anotado con su tipo.
- **Lo que guarda el servidor del navegador:** sólo si algún proveedor activo envía desde
  el servidor y el comprador aceptó su categoría; con píxeles solos no se guarda nada. Son
  el consentimiento y los identificadores que los propios scripts dejaron (`_ga`, `_fbp`,
  `_fbc`, `_ttp`, `ttclid`); la dirección IP y el navegador, sólo con consentimiento de
  marketing, porque es a Meta y a TikTok a quienes se envían. Se borran en cuanto las
  conversiones del pedido terminan, y a los siete días en cualquier caso (cada checkout
  nuevo limpia los caducados: no depende de una tarea programada).
- **La copia del navegador se le da durante una hora.** Pasada una hora del pago, la
  referencia sigue abriendo el estado del pedido, pero ya no el evento de compra: si no,
  cada navegador nuevo que la usara lo emitiría otra vez.
- **El correo de confirmación va primero.** El envío a los proveedores se programa después
  y espera poco (3 s); quien no responde se queda para la tarea programada.
- Las ventas del punto de venta no son conversiones web y no se envían.

`ops_status` (y `healthcheck.sh`) añade una línea `conversiones`: nota si alguna no se
pudo enviar, alarma si llevan mucho esperando o si fallan todas. Nunca nombra un pedido.

## 6. Privacidad

**Lo que nunca se envía:** nombre, correo, teléfono, documento, dirección, notas,
diagnóstico, fotos, IMEI, número de serie, contraseñas, tokens de sesión, de
restablecimiento, de invitación o de seguimiento, ni la referencia del pago.

Cómo se sostiene:

- **El vocabulario de eventos no tiene dónde ponerlo.** Un producto es id, nombre,
  categoría, precio y cantidad; no hay un campo para una persona ni para un equipo.
- **Un script de medición y una dirección privada nunca están en el mismo documento.** El
  script de un proveedor lee la dirección de la página por su cuenta —el de Google incluso
  cuenta vistas cuando cambia—, y nada de lo que se le pase lo impide. Por eso:
  - en estas rutas no se carga ninguno, con el permiso dado y todo;
  - y cuando uno YA está cargado, la tienda no cambia a una de estas rutas dentro del
    mismo documento: avisa al script de que pare y la abre con una carga completa, en un
    documento nuevo que no lo tiene. El botón Atrás hacia una de ellas recarga al momento.

  Las rutas:

  | Ruta | Por qué |
  |---|---|
  | `/seguimiento/…` | el enlace abre una orden de reparación |
  | `/auth/reset-password`, `/auth/verify-email`, `/invitacion` | llevan un token de un solo uso |
  | `/orders`, `/repairs` | los pedidos y reparaciones de una persona |
  | `/admin/…` | el panel |
  | cualquier dirección cuyos parámetros lleven un correo, un número largo o un token | p. ej. buscar un IMEI en el catálogo |

- **Meta y TikTok tampoco están en las páginas con un formulario de datos personales**
  (`/checkout`, `/auth`): sus paneles pueden hacer que sus scripts lean lo que se escribe.
  Google sí está: no lee campos.
- **La referencia del pago salió de la dirección.** Antes la página de éxito era
  `/checkout/success?reference=…`; ahora la referencia se guarda en la pestaña.
- **Desde «Mis reparaciones», el enlace de seguimiento es una navegación completa**: abre
  un documento nuevo, sin scripts de medición.
- **A Google se le dice una ruta, nunca los parámetros.** Una búsqueda que parece un
  correo, un IMEI o una serie se sustituye por `[REDACTED]`.
- **Meta se inicia sin datos de la persona y sin `autoConfig`**, que es lo que le haría
  leer botones y formularios por su cuenta.
- **El servidor** dice como dirección del evento la de la página de éxito, fija, sin
  parámetros.

**Lo que sí se envía de quien compra, con su permiso de marketing:** la dirección IP y el
navegador, a Meta y a TikTok, junto a la compra. Sus API de servidor los usan para
atribuirla y no son «coincidencia avanzada» (no hay correo ni teléfono). El propietario
lo aprobó el 2026-10-07, sólo bajo consentimiento de marketing (DEC-MEAS-08). Antes de
producción, la política de privacidad y cookies de la tienda tiene que decirlo
(MEAS-PRIVACY-NOTICE).

**Límite conocido.** En una ruta normal, los píxeles de Meta y TikTok envían la dirección
completa de la página. Los parámetros de una campaña (`utm_…`) viajan, como es habitual.
Lo que la tienda evita es que haya algo privado en esa dirección.

## 7. Scripts de terceros y CSP

Dominios que carga el navegador, y sólo con el consentimiento que corresponde:

| Proveedor | Script | También habla con |
|---|---|---|
| Google Analytics | `www.googletagmanager.com` | `www.google-analytics.com`, `region1.google-analytics.com` |
| Meta | `connect.facebook.net` | `www.facebook.com` |
| TikTok | `analytics.tiktok.com` | `analytics.tiktok.com` |

El servidor habla con `www.google-analytics.com`, `graph.facebook.com` y
`business-api.tiktok.com`.

La tienda **no tiene hoy una política `script-src`** (CSP-01, deuda anterior a esta
fase): la cabecera actual sólo limita quién puede enmarcarla. Esta fase no la abre ni la
cierra. Cuando se implante, la tabla de arriba es la lista que debe permitir, junto con
la pasarela y Google Identity; no hará falta `*` ni `unsafe-eval`.

**Qué significa mientras tanto.** Un script de terceros tiene acceso completo a la página
donde se carga y puede traer más código. Lo que esta fase hace para acotarlo sin CSP: no
se carga ninguno sin consentimiento, ninguno en rutas privadas, y los de marketing tampoco
en las páginas con formulario. Las cookies de sesión son `HttpOnly` y no las ven. **La
revisión de seguridad recomienda implantar la política antes de activar un proveedor en
producción**; no se hizo aquí porque exige probarla con el SDK de la pasarela real, que
carga sus propios scripts antifraude, y no hay con qué.

**Decisión del propietario (2026-10-07): CSP-01 es obligatoria antes de activar un
proveedor en producción.** No se implanta una lista especulativa: se cierra cuando puedan
validarse los dominios reales de producción, los de la pasarela y los de los SDK.

## 8. Rendimiento

Ningún script de terceros está en el HTML: se añaden después, asíncronos, y sólo tras el
consentimiento. Cada uno se carga una vez por documento. Una vista de página se cuenta
una vez por ruta, aunque el componente se vuelva a pintar.

## 9. Estado y lo que falta

| | Estado |
|---|---|
| Consentimiento | IMPLEMENTADO |
| GA4 (navegador y Measurement Protocol para la compra) | IMPLEMENTADO · validación real BLOCKED/CREDENTIALS |
| Meta Pixel · Conversions API (compra) | IMPLEMENTADO · validación real BLOCKED/CREDENTIALS |
| TikTok Pixel · Events API (compra) | IMPLEMENTADO · validación real BLOCKED/CREDENTIALS |
| Eventos de comercio | IMPLEMENTADO |
| Compra única e idempotente | IMPLEMENTADO |
| CSP (`script-src`) | PENDIENTE (CSP-01) · bloqueante para activar un proveedor en producción |
| IP y navegador a Meta y TikTok | APROBADO, sólo bajo consentimiento de marketing |
| Coincidencia avanzada automática | DESACTIVADA |
| Eventos de servicio técnico | PROPUESTA |

Ninguno de los tres se ha probado contra su servicio real: no hay credenciales. Los
contratos están tomados de la documentación oficial y probados con respuestas simuladas.
Cuando el MASTER los cargue:

0. Antes, en producción: CSP-01 implantada y la política de privacidad y cookies al día
   (MEAS-PRIVACY-NOTICE). En los paneles de Meta y TikTok, coincidencia avanzada
   automática apagada.
1. Guardar, probar (con código de evento de prueba en Meta y TikTok) y activar.
2. Visitar la tienda aceptando las cookies y comprobar en la pantalla de tiempo real o de
   eventos de prueba de cada proveedor que llegan las vistas.
3. Hacer una compra de prueba y comprobar que aparece **una** vez.
4. Quitar los códigos de prueba.

Deuda:

| ID | Qué |
|---|---|
| CSP-01 | sin política de scripts; bloqueante para activar un proveedor en producción; la lista de dominios de partida está en §7 |
| MEAS-SERVER-EVENTS | sólo la compra se envía desde el servidor; el resto, sólo desde el navegador |
| MEAS-GA-HISTORY | Google cuenta cada vista dos veces si «eventos del historial» sigue activo en su panel (§3.1) |
| MEAS-PIXEL-URL | los píxeles envían la dirección completa de las rutas no privadas (§6) |
| MEAS-FORM-PAGES | Meta y TikTok no reciben `AddPaymentInfo` ni `CompleteRegistration`: no están en las páginas con formulario |
| MEAS-IP-UA | la IP y el navegador se envían a Meta y TikTok con la compra, con consentimiento de marketing: aprobado por el propietario el 2026-10-07 |
| MEAS-PRIVACY-NOTICE | la política de privacidad y cookies de la tienda (su dirección la configura cada tienda) aún no dice qué se envía a Meta y TikTok; pendiente antes de producción |
| MEAS-HARD-NAV | entrar en una ruta privada o en el checkout con scripts cargados recarga la página: es el precio de que no la vean |
| MEAS-CONSENT-LOG | no se guarda un registro de consentimientos en el servidor, sólo el del pedido |
| MEAS-TENANT-SCOPE | una configuración para toda la instalación, no por empresa |
| INT-IMPORT-CYCLE | `providers/measurement.py` y `store/measurement/__init__.py` se importan entre sí a través del paquete `integrations`, con un import diferido: el mismo patrón que los proveedores de #90. No falla al cargar. Se corrige en la microfase INT-IMPORT-CYCLE-01 |

## 10. Para quien toque el código

| Qué | Dónde |
|---|---|
| Proveedores (campos, validación, prueba, qué es público) | `backend/store/integrations/providers/measurement.py` |
| Lo único que habla con los tres desde el servidor | `backend/store/measurement/adapters.py` |
| Consentimiento del pedido, salida de conversiones, envío | `backend/store/measurement/conversions.py` |
| Configuración pública (`GET /api/measurement/config/`) | `backend/store/measurement_views.py` |
| Vocabulario de eventos | `frontend/app/lib/analytics/events.ts` |
| Servicio central | `frontend/app/lib/analytics/service.ts` |
| Reglas de privacidad | `frontend/app/lib/analytics/privacy.ts` |
| Adaptadores (`gtag`, `fbq`, `ttq`) | `frontend/app/lib/analytics/adapters/` |
| Consentimiento | `frontend/app/lib/consent.ts`, `components/ConsentBanner.tsx` |

En este código «tracking» es el seguimiento de una reparación; lo de esta fase se llama
«measurement» en el backend y «analytics» en el frontend.

Para medir algo nuevo: añadirlo al vocabulario, decidir en cada adaptador a qué evento del
proveedor corresponde (o a ninguno) y llamar a `track(...)` desde el flujo. Una prueba
falla si aparece `gtag`, `fbq` o `ttq` fuera de los adaptadores, o si un adaptador no
decide sobre un evento.
