# Estado actual

Este archivo no existía en el baseline. Se incorpora como entrada resumida a la
documentación real, sin reemplazar su historial.

## 2026-10-02 — Endurecimiento del frontend: FE-AUTH-02, FE-AUTH-04, FE-AUTH-07

Rama `security/frontend-hardening`, desde `master` `3f70ca0`. Código en `113a8dd`.
Estado: **CORREGIDO** en esta rama. Sin backend, sin migraciones. Eran hallazgos
«sin veredicto» de la auditoría F1; se verificaron en el código antes de tocarlos.

- **FE-AUTH-02 — clave del carrito anónimo (`5adcd28`).** Esa clave es lo único
  que elige un carrito de invitado en el servidor. Se fabricaba con la hora y seis
  caracteres de `Math.random()`. Las claves nuevas salen de `crypto.randomUUID`
  (o de `crypto.getRandomValues` donde falta). Quien ya tenía una clave la
  conserva, así que nadie pierde su carrito.
- **FE-AUTH-07 — la sesión sólo viaja a la API propia (`40f9769`).**
  `fetchWithAuth` añadía cookies y token CSRF a cualquier URL que le pasaran.
  Ningún llamador le pasaba una ajena (revisados los 105), pero era una costumbre
  de los llamadores, no una propiedad de la función. Ahora rechaza antes de la red
  cualquier URL que no esté bajo `API_BASE`.
- **FE-AUTH-04 — cabeceras de seguridad (`113a8dd`).** El frontend no enviaba
  ninguna. Todas las rutas envían ahora `X-Frame-Options: DENY`, una política de
  contenido estrecha (`frame-ancestors 'none'; base-uri 'self'`), `nosniff` y
  política de referente. Las tres páginas que reciben un token de un solo uso en
  la URL (verificar correo, restablecer contraseña, aceptar invitación) envían
  `no-referrer`. No hay política de scripts ni de estilos: exigiría un nonce en
  cada script que emite Next y es un cambio aparte (CSP-SCRIPT = PROPUESTA).
  Tampoco `object-src`: el ticket de caja se imprime como PDF en un marco y esa
  directiva puede bloquear el visor.

Revisados y sin cambio: FE-AUTH-08 (la tarjeta de cuentas de demostración sólo se
pinta en desarrollo y el servidor responde 404 fuera de él: aceptado) y FE-AUTH-09
(la configuración de la tienda se pide desde el servidor sin reenviar el host;
con una sola tienda por despliegue resuelve por `DEFAULT_STOREFRONT_COMPANY_SLUG`;
para varias tiendas por dominio es una decisión de arquitectura: PROPUESTA).
FE-AUTH-06 (el proxy sigue redirecciones reenviando cabeceras) queda PENDIENTE.

Pruebas: `cart-session-key.test.ts` (4; 3 fallan sobre `master`),
`fetch-with-auth.test.ts` (6 casos nuevos; 5 fallan sobre `master`) y
`security-headers.test.ts` (6; los 6 fallan sobre `master`). Cabeceras
comprobadas contra un servidor real.

Validación sobre `113a8dd`: frontend 527 pruebas en 55 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 10,2 min. La impresión del ticket de caja
sigue pasando con las cabeceras nuevas.

## 2026-10-02 — DRIFT-02: el ajuste de inventario dice en qué sucursal se aplica

Rama `fix/inventory-adjust-branch`. Código en `068bee1`, con `master` `a6d725b`
incorporado en `1fa05af`. Estado: **IMPLEMENTADO / MERGED** por PR #53 (`3f70ca0`). Sin backend, sin
migraciones.

El servidor acepta `branch` en el ajuste de inventario y, si no llega, lo aplica a
la sucursal por defecto de quien ajusta. La pantalla nunca lo enviaba: en una
empresa con varias sucursales todos los ajustes caían en la sucursal por defecto,
sin decirlo y sin poder elegir otra.

Ahora, con más de una sucursal al alcance, el formulario pregunta, parte de la
sucursal por defecto y envía la elegida. Si no hay sucursal por defecto, pide
escogerla antes de enviar. Con una sola sucursal —el caso del piloto— no pregunta
nada y la petición es la de siempre. El servidor sigue decidiendo si quien ajusta
alcanza esa sucursal.

Pruebas: `inventory-adjust-branch.test.tsx` (5 casos; 3 fallan sobre `master`).
Contrato del servidor comprobado sobre una copia desechable de la base con dos
sucursales: con `branch` el movimiento queda en esa sucursal; sin `branch`, en la
de por defecto; con una sucursal inexistente responde 404.

Validación sobre `1fa05af`: frontend 511 pruebas en 53 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos.

## 2026-10-02 — RBAC-F3 y RBAC-F4: el panel no ofrece lo que el servidor niega

Rama `fix/staff-actions-capability`, desde `master` `64b4d5e`. Código en
`4f5d736`. Estado: **IMPLEMENTADO / MERGED** por PR #52 (`a6d725b`). Sin backend, sin migraciones. La
autoridad sigue en el servidor; cambia sólo qué enseña la interfaz.

- **Personal (RBAC-F4, `58d17de`).** Ver al personal pide `memberships.view`;
  invitar, desactivar el acceso y reenviar o revocar una invitación piden
  `memberships.manage`. La pantalla pintaba todos los botones a quien pudiera
  entrar, y a quien sólo podía ver cada clic le devolvía un 403. Ahora los botones
  siguen la capacidad que comprueba el servidor.
- **Menú del panel (RBAC-F3, `847d3bf`).** Cada página decide con las capacidades
  cuando hay empresa, y con el rol antiguo sólo para el operador sin empresa. El
  menú seguía otra regla: si las capacidades no alcanzaban, caía al rol antiguo y
  listaba módulos cuya página respondía «sin permiso». Ahora usa la misma regla
  que las páginas. Auditoría declara `memberships.view`, que es lo que piden su
  página y el servidor; antes sólo declaraba rol antiguo y quien tenía la
  capacidad no la veía en el menú.
- **Código sin uso (`4f5d736`).** Se elimina `BranchAccessPanel.tsx` (334 líneas):
  ninguna pantalla lo monta y nada lo importa. Conservaba la oferta de «Todas».

Revisados y ya corregidos en `master` por trabajo posterior a la auditoría F1, sin
cambio aquí: RBAC-F6 (detalle de transferencia: las acciones piden
`inventory.adjust`), RBAC-F7 (accesos del personal: `memberships.manage`) y
RBAC-F11 (detalle de pedido: reenviar correo, nota de venta y comprobante piden
su capacidad). RBAC-F5 era el mismo defecto del menú que RBAC-F3.

Pruebas: `staff-screen-authority.test.tsx` (3 casos; el de sólo lectura falla
sobre `master`) e `internal-modules-access.test.ts` (9 casos; 6 fallan sobre
`master`). En navegador, con las cuentas de ventas, inventario y técnico: cada
entrada del menú abre su página, ninguna responde «sin permiso».

Validación sobre `4f5d736`: frontend 506 pruebas en 52 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 9,0 min.

Deuda menor observada: el menú lista dos entradas hacia `/admin/settings`
(«Empresa» y «Configuración») y dos hacia `/admin/inventory/reports`.

## 2026-10-02 — ADMIN-MENU-ARIA: el botón del menú móvil del panel dice qué abre

Rama `fix/admin-menu-trigger-aria`. Código en `e2ce0e2`, con `master` `2d9cc97`
incorporado en `02b8ba4`. Estado: **CORREGIDO** en esta rama. Sin backend, sin
migraciones, sin cambios de permisos.

El cajón de navegación del panel en un teléfono ya era un diálogo con el foco
atrapado. El botón que lo abre no llevaba estado: un lector de pantalla anunciaba
un botón sin más y nada cambiaba al pulsarlo. Ahora lleva
`aria-haspopup="dialog"` y `aria-expanded`, y mientras el cajón está abierto
apunta a él con `aria-controls`.

Pruebas: `__tests__/admin-menu-trigger-a11y.test.tsx` (3 casos, los 3 fallan
sobre `master`). La prueba del selector de empresa buscaba «el botón plegado»;
ahora hay dos y elige el que abre una lista. Comprobado en navegador a 375 px:
plegado, abierto con 32 enlaces, y Escape devuelve el foco al botón.

Validación sobre `02b8ba4`: frontend 494 pruebas en 50 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 9,3 min.

## 2026-10-02 — FE-AUTH-05: el proxy `/api` pone tope al cuerpo que acepta

Rama `fix/api-proxy-body-limit`, desde `master` `1815ac1`. Código en `4fad36b`.
Estado: **IMPLEMENTADO / MERGED** por PR #50 (`2d9cc97`). Sin backend, sin migraciones, sin cambios de
autenticación, permisos ni contratos de la API.

El proxy de Next (`frontend/app/api/[...path]/route.ts`) leía entero en memoria el
cuerpo de cada petición antes de reenviarlo, sin límite. Cualquiera, con sesión o
sin ella, podía enviar un cuerpo de cualquier tamaño a cualquier ruta bajo `/api/`
y el proceso de Next lo retenía completo. Los topes del backend se aplicaban
después.

Ahora el cuerpo se lee a trozos hasta un tope y se rechaza con 413 en cuanto lo
supera, sin llamar al backend. `Content-Length` sólo sirve para rechazar antes;
deciden los bytes leídos, así que un tamaño declarado falso o un envío a trozos no
lo evitan. El tope es 32 MiB: por encima de la subida más grande que acepta el
backend (una foto de evidencia de servicio, 25 MB, más su envoltorio). Se cambia
con `API_PROXY_MAX_BODY_BYTES`.

Alcance: en la topología de producción aprobada Caddy envía `/api/*` directo a
Django, así que este proxy no atiende al navegador allí. Sí lo atiende en
desarrollo y en cualquier despliegue que publique Next sin un proxy delante.

Pruebas: `__tests__/api-proxy-body-limit.test.ts` (7 casos; 4 fallan sobre
`master`). Comprobado además contra un servidor real: 34 MB declarados y 34 MB a
trozos responden 413; 1 MB y un inicio de sesión llegan al backend.

Validación sobre `4fad36b`: frontend 491 pruebas en 49 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 9,0 min.

Dos pasadas anteriores de Playwright sobre el mismo commit no fueron limpias, por
el entorno de pruebas y no por el cambio. En la primera, `fiscal-invoice` no pudo
iniciar sesión: una prueba manual mía acababa de gastar el límite de inicios de
sesión. En la segunda se omitieron 9 casos de `tax-breakdown`: la base de pruebas
se había quedado sin ningún producto con 4 unidades tras varias pasadas seguidas.
Se repuso el stock y la tercera pasada fue completa.

Además, sobre `master` `1815ac1`:

- Playwright completo en local: 164 de 164.
- Barrido del panel en un teléfono: 37 rutas de `/admin` (33 fijas y 4 de
  detalle) en 320, 360, 375, 390, 414 y 768 px, con sesión de administrador.
  Ninguna desborda la página y ningún texto queda cortado fuera de un contenedor
  desplazable. El menú móvil del panel ya existe en `master` (diálogo, foco
  atrapado, Escape, foco devuelto).

## 2026-10-02 — ADMIN-INVENTORY-MOBILE-OVERFLOW

Rama `fix/admin-inventory-mobile-overflow`, desde `master` `03581ea`.
Estado: **CORREGIDO** en esta fase; sin backend, migraciones, auth, RBAC ni contratos API.

Causa: los paneles del dashboard de inventario son ítems de grid. `TableWrap` ya
encapsulaba las tablas de mínimo 640 px con `overflow-x-auto`, pero el `Panel` y
su cuerpo conservaban `min-width:auto`; por el cálculo de min-content de CSS Grid,
la tabla podía imponer su ancho al panel completo y ensanchar `/admin/inventory`
en pantallas móviles.

Corrección: `InventoryUi.Panel` y su cuerpo declaran `min-w-0`. La tabla mantiene
su ancho y se desplaza únicamente dentro de `TableWrap`; no se cambia contenido,
permisos ni comportamiento de inventario.

Cobertura: `inventory-mobile-layout.test.tsx` protege el contrato estructural y
`admin-inventory-mobile.spec.ts` mide que la página no desborde en 320, 360, 375,
390 y 414 px con sesión interna real.

## 2026-10-02 — HERO-MOBILE-CLIP y cierre del frontend V3

Rama `fix/hero-mobile-clip`, sobre `master` `c47c538`. Código en `42ef631`.
Estado: **IMPLEMENTADO / MERGED** por PR #47 (`03581ea`). Sin backend, sin
migraciones, sin cambios en el panel interno.

Qué se corrigió:

- **Hero (`ff564e9`).** Hasta 414 px el titular y el párrafo se cortaban por la
  derecha. La columna de texto no podía encogerse por debajo de su palabra más
  larga y la sección escondía lo que sobraba. Ahora la columna puede encogerse,
  el titular escala con el ancho de la pantalla hasta su tamaño de siempre, y el
  marco y la etiqueta superior ocupan menos en pantallas estrechas. No hay ningún
  valor escrito para un ancho concreto. De 640 px en adelante el hero sale
  idéntico píxel a píxel al de `master`, en tema claro y oscuro (medido en 640,
  768, 1024 y 1440 px). La losa oscura, los temas y el logotipo no cambian.
- **Titular del catálogo y «Productos relacionados» (`e082b26`).** Al barrer el
  resto de la tienda con la misma medición aparecieron dos titulares con el mismo
  defecto: el de `/product` entre 320 y 390 px y el de la ficha a 320 px. Ambos
  escalan ahora con el ancho. A 640 y 1440 px salen idénticos a `master`.

Pruebas nuevas, las dos de navegador:

- `e2e/hero-mobile-clip.spec.ts` mide cada trozo de texto del hero contra el área
  visible en 320, 360, 375, 390 y 414 px, en tema claro y oscuro; comprueba que
  ninguna palabra del titular se parte, que la letra no baja de 20 px, que la losa
  sigue oscura y que en escritorio el tamaño del titular es el de antes. Sobre
  `master` fallan 10 de sus 12 casos.
- `e2e/storefront-text-fit.spec.ts` aplica la misma medición a `/`, `/product`,
  una ficha con relacionados, `/services`, `/about`, `/contact`, `/cart` y `/auth`
  en los cinco anchos. Cada ruta se carga una sola vez y se redimensiona
  (`42ef631`): cargarla una vez por ancho agotaba el límite de peticiones del
  carrito y hacía fallar la prueba siguiente.

La prueba de desbordamiento que ya existía compara el ancho de la página con el
de la pantalla. No ve un texto cortado dentro de una sección con
`overflow-hidden`; por eso el defecto pasó. Las pruebas nuevas miden el texto.

Validación sobre `42ef631`: frontend 483 pruebas en 47 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 163 de 163,
sin fallos, omitidas ni reintentos, 10,1 min. `backend/` es idéntico al de
`master`; vale su medición de 4643 pruebas, 0 fallos, 3 omitidas.

Una pasada anterior, sobre `e082b26`, dio 194 de 195: falló «una línea del
carrito vuelve a la ficha de su producto» con un 429 del límite del carrito,
provocado por la primera versión de la prueba nueva. No era un defecto de la
tienda; se corrigió la prueba y se repitió la pasada completa.

### Qué queda del diseño V3

Se comparó `master`, el trabajo paralelo sin integrar y el manual de marca.

Ya está en `master` y no se rehízo: categorías reales en cabecera, pie y portada;
navegación móvil; pie con datos de la tienda; hero con campaña opcional; carrusel;
`/about` y `/contact`; temas; línea del carrito enlazada; separación entre tienda
y panel; movimiento reducido.

- **Perrito decorativo = IMPLEMENTADO como marca de la tienda.** Es el isotipo que
  cada empresa sube en su configuración (`logo_isotype_on_*_url`). Aparece como
  marca de agua del hero, en el bloque de promoción, en el acceso y en el menú del
  panel. Otra empresa ve el suyo. En móvil la marca de agua del hero no se
  muestra: PROPUESTA, no defecto.
- **STOREFRONT-IMAGES-LICENSE = PENDIENTE.** El trabajo paralelo trae diez
  ilustraciones (`assets/editorial/`: hero, iphone, mac, ipad y accesorios, cada
  una con y sin fondo) y cuatro fotos de producto (`assets/products/`) con su
  manifiesto. Según su propia nota, las ilustraciones salen de una propuesta de
  Figma con el fondo quitado y muestran productos Apple; las fotos son copias de
  imágenes del sitio de Apple. No hay evidencia de origen ni de licencia de
  ninguna. No se integran. Los originales no se borraron. Para integrarlas hace
  falta, por cada imagen: quién la hizo, de dónde salió y un permiso de uso
  comercial por escrito.
- **TENANT-TYPOGRAPHY = PROPUESTA.** Sin cambio.
- **STOREFRONT-PILLARS-CMS = PROPUESTA.** La portada funciona con los pilares
  compilados; no es imprescindible.
- **INTERNAL-UI-V3 = PENDIENTE.** El trabajo paralelo cambia sólo piezas
  compartidas del panel, ninguna página: el armazón (`AdminShell`), el menú
  lateral con diálogo móvil, la barra superior, los selectores de empresa y
  sucursal, la campana, los gráficos y los estilos globales `.admin-workspace`.
  Por eso alcanza a todas las rutas a la vez (`/admin`, productos, inventario,
  ventas, caja, clientes, servicio y configuración) y no se puede portar una ruta
  sin las demás. Seis de los nueve archivos chocan con `master`, que ya cambió
  esas piezas en SVC-FUNC-01 y UX-RECON-SVC-01. No hay un port pequeño y seguro;
  necesita fase propia con sus pruebas.

Deuda nueva:

- **ADMIN-INVENTORY-MOBILE-OVERFLOW.** `/admin/inventory` desborda la página entre
  284 y 378 px en pantallas de hasta 414 px. Ya ocurre en `master`. No se tocó: es
  del panel.
- **HERO-WATERMARK-MOBILE = PROPUESTA.** Mostrar el isotipo del hero también en
  móvil.

## 2026-10-01 — STOREFRONT-V3: la tienda pública converge sobre el master auditado

Rama `reconcile/storefront-v3-after-ux`, sobre `master` `0c83381`. Código en
`7f49168`. Estado: **PARCIAL** — se portó lo aprobado del trabajo paralelo «V3»;
el contenido editorial por tienda, las imágenes de producto y el rediseño del panel
quedan fuera a propósito.

`master` decide comportamiento, autenticación, multiempresa, comercio y temas. Del
trabajo paralelo se tomó, archivo por archivo, sólo presentación. El PR #38 no se
usó.

Qué cambia para quien visita una tienda:

- **Categorías reales.** La cabecera, el pie y la portada tenían cada uno una lista
  de categorías escrita a mano para el piloto. Ahora las tres leen el catálogo de
  la tienda (`useCatalogCategories`). El pie enlazaba con `?cat=`, que el catálogo
  no lee: no filtraba nada. Usa `?category=`.
- **Portada.** Productos en un carrusel (flechas, teclado, avance lento que cede
  al tocar, quieto con «reducir movimiento»). Preguntas frecuentes de la tienda.
  Los bloques de servicio sólo si la tienda publicó servicios. El código ya no
  escribe copy que nombre una marca de equipos: el titular y el texto del bloque
  «Cómo trabajamos» salen de `services_hero_title` y `services_hero_subtitle` de la
  tienda (los mismos de `/services`), con un respaldo neutro. El primer pilar pasó
  de «Productos y equipos Apple» a «Conocemos lo que vendemos y reparamos»: ese
  texto sigue compilado y **no** tiene campo en el CMS.
- **Hero.** Sigue siendo una losa oscura en los dos temas; sus pruebas no se
  tocaron. Si la tienda publica la campaña `home_hero`, aporta texto, botones e
  imagen; sin campaña, el hero es el de antes. Ninguna imagen se elige por el
  identificador de la empresa.
- **Nosotros y Contacto** (`/about`, `/contact`): sólo datos que la tienda
  publicó. Sin datos, lo dicen.
- **Carrito.** La línea enlaza a la ficha del producto y cada campo de cantidad
  dice de qué producto es.
- **Navegación móvil.** El menú declara su estado y lleva a Contacto y Nosotros.
  «Control interno» sigue dependiendo de la respuesta del servidor.

Sin cambios: checkout, pagos, desglose fiscal, pedidos, autenticación, proveedor
de tema y `backend/` (subárbol idéntico a `master`, `736690e`; vale su medición de
4643 pruebas, 0 fallos, 3 omitidas). Sin migraciones.

Panel interno: ningún archivo bajo `frontend/app/admin` cambia. Sí cambia lo que lo
envuelve: `layout.tsx` y `StorefrontChrome.tsx` ponen el contenido dentro de un
`div` con clase `internal-surface` (antes un `div` sin clase). Comprobado en
navegador contra `master`, con la misma cuenta y los mismos datos: `/admin`,
`/admin/sales/pos`, `/admin/service/orders` y `/admin/inventory` salen idénticos
píxel a píxel y sin diferencias de estilo calculado.

CSS: lo añadido a `globals.css` son clases `v3-*`, una variable en `:root`
(`--v3-ease-out`), reglas `@starting-style` y un `@keyframes`. No se añadió ningún
selector de elemento (`table`, `h1`, `input`, `button`) que alcance al panel.

El defecto «identificador repetido en las líneas del carrito» no existe en
`master`: lo corrigió la reconciliación anterior. No se tocó.

Validación sobre `7f49168`: frontend 483 pruebas en 47 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 143 de 143,
sin fallos, omitidas ni reintentos, 8,6 min.

Aceptación visual del piloto (2026-10-02) sobre `743aa5e`: siete rutas (`/`,
`/product`, una ficha, `/cart`, `/services`, `/about`, `/contact`) en 1440 y 390 px,
temas claro y oscuro. Sin desbordamiento, logotipo correcto por contraste, hero
oscuro en ambos temas, categorías reales, carrusel, navegación móvil y movimiento
reducido correctos.

Defecto encontrado, **anterior a esta rama** (idéntico en `master`): en móvil, hasta
414 px, el titular y el párrafo del hero se cortan por la derecha. La columna de
texto mide 367 px fijos y la sección oculta lo que sobra, así que la prueba de
desbordamiento no lo detecta. Queda como HERO-MOBILE-CLIP, sin corregir aquí
(corregido después en `ff564e9`, rama `fix/hero-mobile-clip`).

Queda fuera, registrado:

- STOREFRONT-PILLARS-CMS = PROPUESTA. Los cuatro pilares de la portada están
  compilados; para que una tienda los redacte hace falta un campo propio.

- STOREFRONT-EDITORIAL-CMS = PENDIENTE. Ilustraciones por categoría y material
  editorial deben ser contenido subido por la tienda. Hoy sólo existe la imagen
  de campaña.
- STOREFRONT-FEATURED-CATEGORIES = PROPUESTA. La portada muestra las primeras seis
  categorías en el orden del servidor; no hay forma de destacar u ordenar.
- TENANT-TYPOGRAPHY = PROPUESTA. El manual del piloto pide Montserrat; la
  plataforma usa Inter y Unbounded para todas las tiendas.
- INTERNAL-UI-V3 = PENDIENTE. Menú móvil del panel, gráficos y estilos del
  trabajo paralelo.
- Imágenes de producto con licencia: el comando `populate_storefront_images`, el
  manifiesto y los recortes del trabajo paralelo no se portaron.
- Hero configurable por tienda (variante clara u oscura): PENDIENTE.

La deuda de SVC-FUNC-01 no cambia.

## 2026-10-01 — UX-RECON-SVC-01: la interfaz de #43 sobre el master con servicio y caja

Rama `reconcile/uxui-after-svc`, sobre `master` `d98d70c` (que ya incluye SVC-FUNC-01
por el PR #44). Merge de reconciliación `ee3a8d3`, con padres `d98d70c` y `9c1486b`
(la cabeza del PR #43). El PR #43 no se modifica: se reconcilió contra un `master`
anterior a la navegación del servicio, a las autoridades de asignación y cobro y a
la recepción desde la caja.

Regla aplicada: `master` decide comportamiento, seguridad y flujo; #43 decide la
presentación donde es compatible.

- `/admin/service` sigue siendo una redirección a `/admin/service/orders`. #43 traía
  ahí la consola antigua de una sola pantalla; no vuelve.
- Las seis colas (`intake`, `orders`, `diagnostics`, `repairs`, `quality`, `delivery`)
  conservan su ruta y su entrada propia en la barra lateral. La presentación que #43
  dio a aquella consola (cabecera, barra de filtros, tabla, estilos compartidos y
  búsqueda con pausa) se aplicó a `ServiceQueue`, que es lo que dibujan las seis.
- La caja conserva «Productos / Servicio técnico» y la recepción de servicio con
  técnico obligatorio, con la cabecera y los estilos de #43. El flujo de productos
  no cambia.
- El detalle de la orden conserva `mayAssignTechnician`, `mayCollectPayment`,
  «Quitar» sólo con `service.orders.manage` y «Reversar» sólo con
  `service.payments.manage`; sólo cambia la cabecera.
- Las seis pantallas de F2 y `branch-authority.ts`, `internal-modules.ts`,
  `InternalControlGuard.tsx`, `auth.ts` y `service-console.ts` quedan como en `master`
  en lo que deciden. Ningún archivo de `master` se elimina.

Sin backend ni migraciones: el subárbol `backend/` es idéntico al de `master`
(`736690e`), así que vale su medición: 4643 pruebas, 0 fallos, 3 omitidas.

Validación sobre el árbol del merge (`99dbca6`): frontend 437 pruebas en 43 suites,
OK; typecheck OK; lint 0 errores y 26 advertencias; build OK (50 páginas);
Playwright 121 de 121, sin fallos, omitidas ni reintentos, 7,9 min.

Sigue sin adoptarse lo que ya quedó fuera en la primera reconciliación (portada,
cabecera, pie, servicios, inicio de sesión de la tienda). La deuda de SVC-FUNC-01
se mantiene: POS-CUSTOM-PRODUCT (propuesta), FISCAL-SERVICE y SVC-QUOTE-INSHOP
(pendientes), MIG-ADMIN-LIVE, SVC-POS-DRAFT y SVC-ELIGIBLE-COST.

## 2026-10-01 — SVC-FUNC-01: servicio técnico operativo e integrado con la caja

Rama `feature/service-pos-functional-integration`, sobre `master` `ef9890f`. Código
en `9b59a31`.

Qué cambia para quien usa el sistema:

- **Navegación del servicio técnico (SVC-NAV-01, `937cf82`).** Recepción, Órdenes,
  Diagnóstico, Reparación, Control de calidad y Entrega tienen cada una su ruta
  (`/admin/service/intake`, `orders`, `diagnostics`, `repairs`, `quality`,
  `delivery`) y su cola de trabajo. Antes las seis abrían la misma pantalla y la
  barra lateral las marcaba todas a la vez. `/admin/service` redirige a Órdenes.
  Una cola agrupa estados reales del servidor y sólo filtra.
- **Asignar técnico (SVC-ASSIGN-01, `1928b05`).** Nueva capacidad
  `service.orders.assign`. Asigna quien tiene `assign` o `service.orders.manage`.
  El rol estándar Ventas recibe `assign`, y no `manage`. Sólo se puede asignar a
  una persona activa de la empresa, que puede ver órdenes de servicio y alcanza
  la sucursal de la orden; cualquier otro identificador responde «no encontrado».
  Con `assign` se asigna y se reasigna; dejar una orden sin técnico exige
  `service.orders.manage` (SVC-ASSIGN-UNASSIGN, `4796db0`): con sólo `assign` el
  servidor responde 403 y no cambia nada.
  La regla de quién puede nombrar a un técnico es una sola (SVC-ASSIGN-VIEW-01,
  `9b59a31`): `service.orders.manage`, o `service.orders.assign` junto con
  `service.orders.view`. Vale igual para asignar una orden existente, para pedir
  los candidatos de una sucursal y para recibir un equipo indicando el técnico.
  Recibir un equipo sin técnico exige lo mismo que antes.
- **Cobrar el servicio (SVC-PAY-01, `d62fa30`).** Nueva capacidad
  `service.payments.collect`. Registra un pago quien tiene `collect` o
  `service.payments.manage`; reversar sigue exigiendo `manage`. Los roles estándar
  Servicio Técnico y Supervisor Técnico reciben `collect`. Sigue haciendo falta
  una cotización aprobada para cobrar.
- **Servicio técnico desde la caja (POS-SVC-01, `6caa88c`).** `/admin/sales/pos`
  tiene un conmutador «Productos / Servicio técnico». En servicio se recibe un
  equipo eligiendo cliente, equipo, sucursal, falla y técnico por nombre
  (obligatorio). Una sola petición crea la orden y la asigna; se muestra el número
  y «Abrir orden», y el técnico la ve en «Mis reparaciones».

Lo que este flujo **no** hace: no crea pedido, línea de pedido, movimiento de
stock, comprobante ni comisión, y no cobra nada. Una orden de servicio no es una
venta. El importe de un servicio es una línea de la cotización de la reparación
(tipo «Servicio», con descripción, cantidad y precio, sin producto).

Clasificación funcional, con prueba:

| Función | Estado | Prueba |
|---|---|---|
| Servicio desde la caja | IMPLEMENTADO | `SvcIntakeWithAssignmentTest`, `pos-service-intake.test.tsx`, E2E `service-pos` |
| Línea de servicio personalizada | IMPLEMENTADO (ya existía) | `SvcCustomServiceLineTest` |
| Cobro por el técnico | IMPLEMENTADO | `SvcPaymentCollectTest`, E2E `service-pos` |
| Producto personalizado en la caja (POS-CUSTOM-PRODUCT) | PROPUESTA | — |
| Comprobante fiscal de un pago de servicio (FISCAL-SERVICE) | PENDIENTE | — |
| Aprobar la cotización en tienda (SVC-QUOTE-INSHOP) | PENDIENTE | — |

Migraciones: `0094_service_orders_assign` y `0095_service_payments_collect`. Sólo
amplían roles estándar que la empresa no modificó, comparando contra conjuntos
congelados. No cambian el esquema y no se revierten.

Cambios de comportamiento a tener presentes:

- Una membresía con el rol antiguo `technician` que nunca pasó a roles de empresa
  ya no es asignable: no puede ver órdenes de servicio, así que la orden sería suya
  e invisible para ella. La migración 0057 ya trasladó a esas personas al rol
  estándar.
- La lista de candidatos de una orden se limita a quienes alcanzan su sucursal.
- Con sólo `service.orders.assign` hace falta además `service.orders.view` para
  asignar, ver candidatos o recibir un equipo con técnico. Ningún rol estándar
  tiene `assign` sin `view`; un rol creado por la empresa sí puede tenerlo.

Defecto corregido de paso: el `@transaction.atomic` de `assign_technician` había
quedado sobre `_notify` desde `108a904`. `assign_technician` bloquea la fila de la
orden y, fuera de una transacción, PostgreSQL lo rechaza. Lo cubre
`SvcAssignOutsideATransactionTest`.

Validación sobre `9b59a31`: backend PostgreSQL 4643 pruebas (4640 OK, 3 omitidas,
0 fallos), 1600,8 s; `check` sin problemas; `makemigrations --check` sin cambios.
Playwright 121 de 121, sin fallos, omitidas ni reintentos, 9,0 min. El frontend no cambió desde `4796db0` (árbol `frontend/`
idéntico, `7ea6870`), donde se midió: 428 pruebas en 40 suites, OK; typecheck OK;
lint 0 errores y 33 advertencias (las del baseline); build OK (50 páginas). Los
commits de código anteriores se comprobaron además por separado (pruebas del
área, typecheck y migraciones).

Deuda y límites conocidos: al volver de «Servicio técnico» a «Productos» se pierde
una recepción a medio llenar (la cesta sí se conserva); si el contexto de la caja
no carga, tampoco se llega al modo servicio; una orden recién recibida aparece en
Órdenes › «Mis reparaciones», y en la cola Reparación sólo cuando su cotización
está aprobada; un
superusuario de plataforma con membresía de personal figura como candidato. Detalle:
[docs/AUDIT_MEMORY.md](docs/AUDIT_MEMORY.md).
## 2026-10-01 — Integración en master y reconciliación UX/UI (#39)

ERP y las fases F1/F2 de la auditoría están en `master` desde el PR #42 (merge
`ef9890f`): una sola transición, con el árbol exactamente igual al que pasó todas las
compuertas.

La rama de UX/UI (#39) se diseñó sobre el `master` anterior y chocaba en 75 archivos. Su
reconciliación vive en `reconcile/uxui-after-f2` (merge `1fea6b9`), todavía sin
integrar. Criterio: `master` decide el comportamiento y #39 la presentación cuando es
compatible. **IMPLEMENTADO** en esa rama: el lenguaje visual de #39 en el control
interno (cabeceras de página, botones y campos compartidos, navegación lateral con
foco atrapado en móvil), las páginas de error, carga y no encontrado, y la presentación
de carrito, catálogo, detalle de producto, pedidos y recuperación de contraseña. Las
reglas de F2 no cambian: qué puede hacer cada persona y en qué sucursales se decide
igual que antes. **PARCIAL**: el rediseño de #39 para la portada, la cabecera y el pie
de la tienda, la página de servicios y la pantalla de inicio de sesión no se adoptó,
porque `master` ya los había rehecho con contenido editable, tema claro/oscuro y
logotipo por empresa, que #39 no conoce. Los colores de estado de #39, pensados sólo
para tema oscuro, se expresan con los tokens del tema.

Validación sobre `1fea6b9`: frontend 411 pruebas en 40 suites, OK; typecheck OK; lint 0
errores y 26 advertencias; build de producción OK (44 páginas); Playwright 118 de 118.
Sin archivos de backend ni migraciones en el cambio, por lo que la corrida completa de
backend vigente es la de `c191a84` (4566 OK).

## 2026-09-30 — F2 completada: delegación y alcance por sucursal

La fase F2 de la auditoría queda cerrada sobre `c191a84`. Qué puede hacer una persona
(capacidades) y dónde puede hacerlo (alcance por sucursal) se exigen juntos, en el
servidor y reflejados en la interfaz: nadie concede, retira ni modifica lo que no
alcanza; abrir saldo exige autoridad de inventario; pertenecer a una empresa no
basta para leerla; las modificaciones de nivel empresa exigen alcance sobre toda la
empresa; y ningún identificador de sucursal sirve para sondear si existe.

Últimos dos cierres. E2E-02: la prueba de personal desactivaba una cuenta demo
compartida y la dejaba sin capacidades para la corrida siguiente; ahora usa un
trabajador propio (`seed_demo_users --e2e-fixtures`, `9c3445f`). E2E-01: la prueba
fiscal esperaba «Emitir factura»; el panel dice «Preparar factura» porque ese paso
numera y firma sin hablar con SUNAT, y el envío es otro botón. Era la prueba la que
estaba desfasada, y al corregirla volvieron a ejecutarse cuatro pruebas que no
corrían (`c191a84`).

Validación final: backend PostgreSQL 4569 pruebas (4566 OK, 3 omitidas, 0
fallos), 1544,8 s; `check` sin problemas; 0 migraciones nuevas en toda la fase.
Frontend 402 pruebas en 37 suites, OK; typecheck OK; lint 0 errores y 33
advertencias (las mismas del baseline); build de producción OK (44 páginas).
Playwright 118 de 118, sin omitidas ni reintentos. Nada se ha empujado al remoto.

Deuda que sigue abierta: `BranchAccessPanel.tsx` sin uso; el botón «Añadir
trabajador» no comprueba capacidad (RBAC-F4); el admin de Django permite editar
`Product.inventory` sin Kardex; `C15InitialRaceTest` falla aislado (TEST-ENV-01);
`storefront_content_views` sin auditar bajo WRITE-SCOPE-01; una persona con
sucursales seleccionadas no puede reactivar una sucursal propia inactiva. Detalle:
[docs/AUDIT_MEMORY.md](docs/AUDIT_MEMORY.md).

## 2026-09-30 — F2 · DRIFT-01 y DRIFT-07: la interfaz refleja el contrato

Asignar técnico desde la web vuelve a funcionar: la consola leía `technicians` y el
servidor responde `candidates`, así que el selector salía siempre vacío. El tipo
TypeScript describe ahora la respuesta real (`a4be03b`).

La interfaz de administración tiene en cuenta dónde puede actuar cada persona, no
sólo qué puede hacer. A quien tiene sucursales seleccionadas ya no se le ofrece
«Todas», sucursales que no alcanza, crear sucursales, elegir la sucursal de
despacho, guardar los ajustes de la empresa, editar la serie de empresa ni cambiar
el alcance de la numeración; tampoco archivar promociones que funcionan fuera de sus
sucursales, y un combo nuevo se aplica sólo en las suyas. Sigue viendo la plantilla
completa, los ajustes y las series, y edita su sucursal y la serie de su sucursal.
El alcance sale del contexto que envía el servidor; el backend sigue rechazando todo
lo anterior (`6958ec0`, `f6dc9ca`).

Validación: frontend 400 pruebas en 37 suites, OK; typecheck OK; lint 0 errores y 33
advertencias (sin cambio); build de producción OK (44 páginas). Backend sin cambios:
regresiones de contrato (73 pruebas) OK; la corrida completa vigente es la de
`c076120` (4560 OK). E2E pendiente (E2E-02, E2E-01).

## 2026-09-30 — F2 · WRITE-SCOPE-01: el alcance por sucursal limita también lo que se modifica

`company.manage` dice qué se puede cambiar; el alcance por sucursales dice dónde. Una
persona limitada a sucursales seleccionadas podía, con esa capacidad, crear
sucursales, editar o desactivar una que no alcanza, elegir la sucursal de despacho de
la tienda online, editar la serie de empresa, cambiar el alcance de la numeración y
modificar los ajustes de la empresa (identidad, marca, moneda). Ahora esas
operaciones de nivel empresa exigen, además de la capacidad, alcance sobre toda la
empresa (plataforma, modo «todas» o puente legacy: `tenancy.has_company_wide_scope`).
Editar una sucursal propia y la serie de una sucursal propia siguen permitidos; las
lecturas no cambian. Una sucursal inexistente o ajena sigue respondiendo 404; una de
la propia empresa fuera de alcance, 403 (ya figura en su plantilla). Commits
`fa85d41` (sucursales y despacho) y `c076120` (numeración y ajustes).

Validación: backend PostgreSQL 4563 pruebas (4560 OK, 3 omitidas, 0 fallos),
1519,8 s; `check` sin problemas; 0 migraciones por generar. Frontend sin cambios.

## 2026-09-30 — F2 · RBAC-01 y RBAC-02: lecturas de empresa y sucursal por defecto

Pertenecer a una empresa ya no autoriza a leerla. La ficha de la empresa (lista y
detalle) exige `company.view` o `company.manage`; la plantilla de sucursales, además,
acepta `memberships.view/manage`, porque conceder acceso por sucursal la usa como
selector. Sin esas capacidades las listas salen vacías y los detalles responden 404,
igual que un id inexistente o de otra empresa. La plantilla sigue siendo de nivel
empresa (incluye las inactivas, que se reactivan desde ahí); el maestro de plataforma
conserva su alcance global. Se reutiliza el helper con el que M11 ya protegía áreas y
roles (`5afdb81`).

La sucursal por defecto de una membresía ya no revela si un id existe: inexistente, de
otra empresa o fuera del alcance de quien la asigna responden igual, 404 «Sucursal no
encontrada o sin acceso.», antes de escribir nada. Antes respondía 404, 400 con otro
mensaje o, para una sucursal no alcanzada, la aceptaba (`d18e983`).

Validación: backend PostgreSQL 4545 pruebas (4542 OK, 3 omitidas, 0 fallos), 1517,6 s;
`check` sin problemas; 0 migraciones por generar. Frontend sin cambios. Queda abierta,
pendiente de decisión, la autoridad de `company.manage` con alcance por sucursales
sobre la configuración de nivel empresa (alta y edición de sucursales, serie de empresa).

## 2026-09-30 — F2 · F-CAP-01: el stock inicial exige autoridad de inventario

`products.manage` es autoridad sobre el catálogo; `inventory.adjust`, sobre las
existencias. Crear un producto con `inventory > 0` abre saldo con una línea
`initial_stock` en el Kardex y ahora exige ambas, con la misma puerta que el ajuste
directo (puente legacy incluido) y antes de escribir nada: sin `inventory.adjust`
responde 403 y no quedan producto, stock, movimiento ni auditoría. Con `inventory`
omitido o 0 basta `products.manage`. La sucursal del saldo la sigue eligiendo el
servidor dentro del alcance de quien crea. Es la única ruta pública que abre saldo:
la edición de producto rechaza `inventory`, la importación de productos no crea stock
y la de stock ya exigía `inventory.adjust` y acceso a la sucursal. Los presets
actuales sólo dan `products.manage` al rol «administrador», que también tiene
`inventory.adjust`. Commit `c042fea`.

Validación: backend PostgreSQL 4528 pruebas, OK (3 omitidas), 1511,2 s; `check` sin
problemas; 0 migraciones por generar. Frontend sin cambios.

## 2026-09-30 — Auditoría F2 (eje «dónde»): delegación por sucursal

El eje «dónde» ya tiene regla de delegación, como el eje «qué» la tenía desde G3:
nadie concede, retira ni modifica por escritura una sucursal que no alcanza.
`tenancy.can_delegate_branch_scope` es la autoridad única. Quien opera toda la
empresa (plataforma, modo «todas», puente legacy) sigue concediendo cualquier cosa;
una persona limitada a sucursales seleccionadas sólo nombra un subconjunto de las
suyas, nunca «todas», y no toca a quien llega más lejos que ella. Se aplica al alta y
edición de membresías, a las invitaciones de personal (tenían el mismo hueco) y a las
promociones. Además, la edición de una membresía es ahora una sola transacción, y la
serie interna de una sucursal que la persona no alcanza responde 404 también en el
detalle, igual que ya pasaba en el listado. Cerrados: F-BRANCH-01 (`20d110c`),
F-BRANCH-02 (`cccb4d2`), F-BRANCH-03 (`70286d1`).

Validación: backend PostgreSQL 4520 pruebas, OK (3 omitidas), 1520,6 s (31 nuevas);
`check` sin problemas; 0 migraciones por generar. Frontend sin cambios. Pendiente
en F2: F-CAP-01 (requiere decisión), RBAC-01/02, DRIFT-01, DRIFT-07 (la interfaz sigue
ofreciendo «todas» a quien no puede concederlo; el backend responde 403 con un
mensaje legible), E2E-02 y E2E-01.

## 2026-09-30 — Auditoría F1 (seguridad, tenancy, autorización) cerrada

F1 completada sobre `4a9dd5c`. Único HIGH, FE-AUTH-01, corregido en ese commit: el
proxy `frontend/app/api/[...path]/route.ts` rechaza con 400 los segmentos que tras
decodificar son `.`/`..` o contienen `/` o `\`, y ya no sirve el admin de Django por el
origen público (regresión `frontend/__tests__/api-proxy-scope.test.ts`, 11 casos;
jest 368/368 en 34 suites, typecheck OK, lint 0/33). El backend no cambió. Quedan
confirmados y pendientes, entre otros: F-BRANCH-01/02/03 (el eje «dónde» no tiene
regla de delegación), F-CAP-01, F-TENANT-01, DRIFT-01, E2E-01, E2E-02, SEC-SET-02,
SEC-SET-04-A y THROTTLE-CACHE-01. IDOR-01 refutado. Índice verificado de hallazgos,
invariantes, símbolos y tests: [docs/AUDIT_MEMORY.md](docs/AUDIT_MEMORY.md).
Siguiente fase: F2, delegación y alcance por sucursal.

## 2026-09-29 — ERP-FISCAL-6 cerrado · auditoría integral abierta

ERP-FISCAL-6 queda cerrado en `erp/sales-fiscal-ui` con cuatro commits: `97043d6`
(capa comercial), `c9e64b9` (POS: orden del snapshot y caja BETA), `fabfa38` (tests de
promociones) y `9525b08` (fiscal); la rama está empujada a `origin` con ese HEAD. La
auditoría integral se abre en `audit/full-system-2026-09`, creada desde `9525b08` e
integrando `origin/master` `2dca0a3` (dos commits, sólo `docs/internal-parity-matrix.md`,
merge limpio `65aa8c1`). **Baseline oficial medido sobre `65aa8c1`**, árbol limpio:
backend PostgreSQL 14 · Python 3.14.6 · Django 5.2.17 · DRF 3.17.1 — 4489 pruebas, OK
(3 omitidas), 1477,8 s; `check` sin problemas; migraciones 105 aplicadas / 0 pendientes /
0 por generar. Frontend Node 24.16.0 · Next 16.3.4 · React 19.2.4 · TypeScript 5.9.3 —
357 pruebas en 33 suites OK, typecheck OK, lint 0 errores / 33 advertencias, build OK
(44 páginas). Playwright 118 pruebas: 113 pasan, 1 falla preexistente (E2E-01:
`fiscal-invoice.spec.ts` espera «Emitir factura» y el panel dice «Preparar factura»
desde `b3b1cfc`), 4 no ejecutadas por modo serie tras ese fallo. `npm audit`
(producción): 0 vulnerabilidades. Sin CI propio (CI-01, MEDIUM): no hay
`.github/workflows`, cero check-runs; `dynamic/dependabot/update-graph` es el grafo de
dependencias de GitHub. Este documento y `07_CHANGELOG.md` se actualizan en un commit
posterior al SHA medido, que sólo toca `.md`.

## 2026-09-29 — ERP-FISCAL-6: descuentos declarados

Una venta con descuento ya se emite como factura (01) o boleta (03) en BETA. El
descuento se declara con `cac:AllowanceCharge` donde nació: cupón y manual como
descuento global (Catálogo N.º 53 vigente, código `02`); promoción automática en
cada línea rebajada (código `00`), con la atribución por componente que el motor
comercial congela en la venta (ADR-42) y que las ventas anteriores reconstruyen en
memoria sin reescribir su historial. Los totales siguen las reglas oficiales de
validación (ADR-43): `PayableAmount` es lo cobrado, la base imponible es la del
snapshot y `AllowanceTotalAmount` se omite para no declarar la rebaja dos veces. Un
descuento que el snapshot no explica falla cerrado sin gastar correlativo.

La caja ofrece nota interna, boleta y factura con el nuevo contrato de
`receipt_options`: quien puede emitir ve cada opción con `enabled` y una causa segura
cuando no está disponible (`FISCAL_DISABLED`, `NO_SERIES_FOR_BRANCH`,
`AMBIGUOUS_SERIES`, `UNSUPPORTED_ENVIRONMENT`); quien no puede emitir no recibe
opciones fiscales. Tras cobrar, la pantalla muestra el comprobante electrónico real
(serie-correlativo, ambiente, estado del backend) en vez de una nota interna
disfrazada. `seed_demo_users --fiscal-beta` prepara F001/B001 DEMO en BETA
(DEBUG=True y `FISCAL_ENVIRONMENT=beta`, idempotente, sin tocar correlativos).
`FISCAL_ENABLED` sigue apagado por defecto; se enciende sólo en el `.env` local.

Validación: backend PostgreSQL 4489 pruebas, OK (3 omitidas), 1917,4 s; frontend 357 pruebas en 33 suites,
OK; typecheck y build de producción PASAN; lint 0 errores y 33 advertencias (las
mismas de antes). Playwright: `pos-ticket.spec.ts` PASA; `pos-receipt-options.spec.ts`
PASA (humo real con `dev_admin`: las tres opciones habilitadas para «Tienda
principal»). Migraciones nuevas: 0. Pendiente: verificación directa en
SUNAT BETA de un comprobante con descuento; producción no implementada; el PDF aún
no imprime la línea de descuentos globales (FISCAL-PDF-01).

Detalle: [ADR-42 y ADR-43](docs/adr-fiscal-c22a1.md) ·
[matriz UBL](docs/sunat-factura-ubl21-matriz.md) ·
[Catálogo 53 y reglas](docs/sunat-cpe-requisitos.md).

## 2026-09-28 — Estabilización funcional

La estabilización de POS y recepción técnica está implementada en el árbol de
trabajo. El alcance fiscal continúa limitado a BETA. La entrega de recepción usa
la capacidad existente y la migración selectiva 0093; se preservan permisos
personalizados, empresas, sucursales, historial y auditoría.

Validación final: backend PostgreSQL 4412 pruebas, OK (3 omitidas), 1448,6 s;
frontend 350 pruebas en 33 suites, OK. Lint: 0 errores y 33 advertencias.
**Typecheck y build de producción PASAN**: los dos badges de inventario que se
exportaban desde `page.tsx` viven ahora en el módulo compartido
`app/admin/components/InventoryUi.tsx`, de donde esas páginas ya importaban el
resto de sus primitivas. **Playwright: 17 de 17** en POS y acceso interno.
SQLite local actualizado hasta 0093 con respaldo; sin migraciones pendientes.

Resultados, límites y hallazgos:
[Auditoría de estabilización](docs/estabilizacion-funcional-2026-09-28.md).

Historial técnico completo:
[Estado y auditoría técnica](docs/estado-actual-y-auditoria-tecnica.md).
