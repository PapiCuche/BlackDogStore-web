# Changelog

Este archivo no existía en el baseline. La fuente histórica sigue siendo
[CHANGELOG.md](CHANGELOG.md).

## 2026-10-07 — CHECKPOINT 2: tienda desplegada con HTTPS (PARCIAL)

- Destinatario de avisos de pedidos escrito en el servidor; preflight
  `SUFICIENTE PARA ARRANCAR`.
- Imágenes construidas en el servidor sobre `c5f8c93`; PostgreSQL vacío, 154
  migraciones y `bootstrap_pilot_store --apply`. Estado inicial aprobado comprobado:
  cero usuarios, productos, stock y movimientos; cinco categorías; campaña conservada.
- Primera copia local hecha; restauración pendiente.
- MASTER creado por el propietario con `createsuperuser`, sin membresía.
- Con `GO DNS`: A `@` y A `www` hacia `54.94.236.23`, DNS only, TTL Auto. Verificados.
- Con orden del propietario: 80/443 abiertos en Lightsail, Caddy en marcha con
  certificado de Let's Encrypt, comprobaciones de §4.5 correctas. Sin catálogo, correo
  ni pagos; no abierta a clientes.
- **Fixed** (#98): la consola de integraciones enviaba la empresa del panel a los
  proveedores de plataforma y el servidor la rechazaba; un MASTER no podía configurar
  correo ni pagos. Servidor actualizado a `54b26ac`, sólo `frontend`.
- Acceso por clave SSH dedicada del usuario `deploy`.

## 2026-10-07 — CHECKPOINT 2: preparación del servidor (PARCIAL)

- Con `GO AWS`, creado Lightsail en São Paulo (4 GB / 2 vCPU / 80 GB,
  US$24/mes) y Static IP adjunta. IPv6 apagado, SSH restringido en AWS;
  puertos web cerrados, sin cambios DNS ni publicación.
- Host con Docker/Compose oficiales, deploy, UFW, swap, hora de Lima y copia
  limpia de `c5f8c93`. Secretos generados sólo en el servidor, permisos 600.
- Compose validado sin expandir valores; preflight bloqueado por destinatario
  de pedidos. Despliegue, MASTER, integraciones y backup/restore pendientes.
- Corregida la continuidad documental: #95 ya está mergeado; dominio y servidor
  ya existen. Sin cambios de código, migraciones ni frontend; #80/#85 intactos.

## 2026-10-07 — FRESH-PRODUCTION-DATA-01

**Added**
- `manage.py bootstrap_pilot_store`: en una base de producción nueva retira los tres
  productos de ejemplo que deja la migración `0002` (y su stock), sólo si están intactos,
  sin referencias y la base no se ha usado; si no, se niega y no cambia nada. Deja a la
  tienda piloto con sus cinco categorías aprobadas. Sin `--apply` sólo informa. Se niega
  también si quedan uno o dos de los tres productos, si `DEBUG` está activo y si tendría
  que escribir en una base que ya se usa.
- El ensayo mide una base recién migrada («PRODUCTION FRESH DATABASE CHECK», paso 3b) y
  comprueba el primer administrador: un usuario, MASTER, sin membresía.

**Changed**
- El ensayo crea su propio producto, con su movimiento de stock inicial: la tienda ya no
  empieza con uno de ejemplo.
- `docs/despliegue-produccion.md` §4 y §5: arranque desde una base vacía, primer
  administrador con `createsuperuser` y orden de un primer arranque.

Sin migraciones ni cambios de modelo, API o frontend.

## 2026-10-07 — CHECKPOINT 1B revalidado

- #93 mergeado con CI backend verde en su HEAD exacto; test de error real y ausencia
  de secretos, sin tocar código productivo.
- Revalidado master `a1d2a29`: frontend 933, backend relevante 445 (2 sandbox sin
  credenciales), tipos/lint/build, imágenes, compose y preflight de ensayo correctos.
- Ensayo completo 139 comprobaciones, navegador, backup y dos restores: OK.
- READY FOR CHECKPOINT 2, sólo tras aprobación para Lightsail; ningún recurso AWS.

## 2026-10-07 — FIX-MEAS-LOG-TEST-01

- La prueba de logs de conversiones deja de depender de un INFO incidental de correo.
- Provoca errores inesperados en la frontera HTTP simulada y verifica el logging real
  de `store.measurement`: sin tokens, secretos de API, identificadores ni PII; sin
  traceback, sin deshacer el pago y con reintento pendiente.
- Sólo test backend; sin cambios productivos ni migraciones. RED → GREEN en PostgreSQL.

## 2026-10-07 — Cierre de ANALYTICS-MARKETING-INTEGRATIONS-01

- La fase está en `master`: PR #91, merge `021ad13`. Sin despliegue.
- **Decidido por el propietario** (DEC-MEAS-08): CSP-01 es condición para activar un
  proveedor en producción y no se implanta con una lista especulativa; la IP y el
  navegador se envían a Meta y TikTok sólo bajo consentimiento de marketing (MEAS-IP-UA,
  aprobado); la coincidencia avanzada automática sigue desactivada; los dos ciclos de
  imports de la fase se corrigen en la microfase INT-IMPORT-CYCLE-01.
- Pendiente nuevo: MEAS-PRIVACY-NOTICE, decir en la política de privacidad y cookies lo
  que se envía a Meta y TikTok, antes de producción.
- `docs/CODEBASE_MAP.md` incorpora la consola de integraciones (#90) y la medición (#91).

## 2026-10-06 — ANALYTICS-MARKETING-INTEGRATIONS-01

**Added**
- **Google Analytics 4, Meta y TikTok** en Panel › Configuración › Integraciones ›
  Analítica y marketing, sobre la consola de #90: identificadores públicos, claves
  cifradas, sin direcciones configurables.
- **Aviso de cookies** con tres respuestas (aceptar todas, rechazar opcionales,
  configurar) y «Preferencias de cookies» en el pie.
- Servicio de analítica de la tienda y tres adaptadores; eventos de catálogo, carrito,
  checkout, registro, inicio de sesión y contacto.
- **La compra como conversión única**: una fila por proveedor escrita con el pago, enviada
  desde el servidor (Measurement Protocol, Conversions API, Events API) con reintentos.
  `manage.py send_pending_conversions`; línea nueva en `deploy/crontab.example`.
- `GET /api/measurement/config/`: lo que la tienda puede usar para medir, sin secretos.
- `ops_status` informa de las conversiones.
- [docs/analytics-marketing.md](docs/analytics-marketing.md).

**Changed**
- El icono de la pestaña es el isotipo oficial (antes: el triángulo por defecto del
  framework y un círculo de prototipo).
- La referencia del pago ya no viaja en la dirección de la página de éxito.
- Desde «Mis reparaciones», el enlace de seguimiento abre un documento nuevo.

**Fixed**
- Una ficha de producto podía contarse dos veces si su efecto se ejecutaba dos veces.
- De la revisión de seguridad independiente (1 P1, 6 P2): un script de medición ya cargado
  seguía presente al navegar dentro de la tienda a una dirección privada (ahora esa
  navegación abre un documento nuevo); los píxeles de marketing estaban en las páginas con
  formulario; el primer envío a los proveedores podía retrasar o cancelar el correo de
  confirmación; los identificadores del navegador se guardaban sin que nadie fuera a
  leerlos; la página de estado daba el evento de compra indefinidamente.

## 2026-10-06 — INTEGRATIONS-CONSOLE-01

**Added**
- **Panel › Configuración › Integraciones**, sólo para el MASTER: correo SMTP, Izipay (sus
  dos productos), WhatsApp Business por empresa, ID de cliente de Google y SUNAT se
  configuran, se prueban y se activan sin tocar el servidor ni reiniciar.
- Almacén de secretos (`store.integrations.secret_store`): Fernet con la clave raíz
  `APP_CONFIG_ENCRYPTION_KEY`; `manage.py reseal_integration_secrets` para rotarla.
- Registro de proveedores y adaptadores (`store.integrations`); modelo `IntegrationConfig`
  (migración `0111`); API `/api/admin/integrations/…`.
- `ops_status` y `healthcheck.sh` informan de las integraciones y dan la alarma si la
  tienda no puede enviar correo o cobrar, o si lo guardado no se puede leer.
- El ensayo recorre la consola en la pila de producción con un segundo servidor de correo.
- [docs/integraciones-y-secretos.md](docs/integraciones-y-secretos.md).

**Changed**
- El checkout, el correo, WhatsApp, Google y SUNAT leen su configuración en cada uso:
  la de la consola si hay una activa; si no, las variables de entorno, que quedan como
  respaldo. Una integración apagada en la consola no recurre al entorno.
- El backend arranca en producción sin correo configurado (se configura en la consola).
- `deploy/.env.production.example`: `APP_CONFIG_ENCRYPTION_KEY` es obligatoria; el correo,
  la pasarela y `WHATSAPP_PROVIDER` vienen sin rellenar, para la consola.
- `deploy/preflight.py`: exige la clave raíz; un archivo sin correo ni pasarela es
  «suficiente para arrancar», y los dos siguen figurando como datos que debe el propietario.

**Fixed**
- La prueba de conexión de la pasarela distinguía «no responde» de «rechaza las claves»
  por el texto del error; ahora por su tipo.
- Con `WHATSAPP_PROVIDER=disabled` de serie, WhatsApp no se podía activar desde la consola.
- Una configuración guardada que el servidor no puede leer rompía la petición que quería
  enviar un correo.
- De la revisión independiente (0 P1, 4 P2, 7 P3): una prueba podía dejar marcado como
  probado lo que se guardó mientras corría; un primer borrador con la prueba fallida
  tumbaba `ops_status`; las claves de producción de la pasarela salían como «Correcto»
  sin que nada las comprobara (ahora «Coherente, sin verificar»); apagar o cambiar la
  pasarela con cobros abiertos no avisaba (ahora pide `INTERRUMPIR`); se guardaban en
  claro los cuatro últimos caracteres de cada secreto.

## 2026-10-05 — EXTERNAL-PRODUCTION-CONFIG-01

**Added**
- `deploy/preflight.py`: dice qué falta del propietario (`BLOCKED/OWNER-DATA`), qué es
  opcional y qué se contradice en `deploy/.env.production`, sin imprimir ningún valor.
  Con `--smtp` entra al servidor de correo; con `--smtp-send-to` entrega un mensaje de
  prueba; con `--dns` pregunta a qué dirección apunta el dominio.
- `EMAIL_TIMEOUT` (10 s por omisión) y `EMAIL_USE_SSL` (TLS desde el primer byte, puerto 465).
- El ensayo envía el correo por SMTP de verdad, a un servidor de correo propio
  (`deploy/rehearsal_smtp_sink.py`): registro con verificación, recuperación de
  contraseña, servidor de correo caído y contraseña rechazada.
- El ensayo restaura la copia en un «servidor nuevo»: otro proyecto de Docker, volúmenes
  vacíos, el mismo archivo de variables.

**Changed**
- El ensayo ya no usa el correo de consola: comprueba que la tienda envía por SMTP y que
  ningún enlace de un solo uso queda en el registro.

**Fixed**
- MAIL-TIMEOUT: sin tiempo de espera, un servidor de correo que dejara de responder
  retenía la petición de cada registro o recuperación de contraseña indefinidamente.
- MAIL-SSL: no se podía configurar un proveedor que sólo ofrece el puerto 465.
- De la revisión de la rama: `preflight.py` podía imprimir un valor del archivo y lo leía
  distinto de como lo leen Compose y Django.

## 2026-10-05 — PRODUCTION-READINESS-01

**Added**
- Tope de tamaño para el cuerpo de toda petición, por ruta: `store/request_limits.py`
  (middleware) y los mismos números en `deploy/Caddyfile`.
- Tiempos de espera en Caddy y lectura previa de los cuerpos pequeños.
- Registro de acceso sin lo que una dirección puede llevar (`backend/log_redaction.py`,
  `backend/gunicorn_logging.py`) y el mismo filtro en el registro de la aplicación.
- `python manage.py ops_status` (sólo lectura): migraciones sin aplicar, notificaciones
  de pago rechazadas, pagos esperando y avisos de WhatsApp fallidos o atrasados.
- `deploy/healthcheck.sh` y `deploy/crontab.example`.
- `backups/LAST_OK`: la hora de la última copia completa.
- Rotación de los registros de cada contenedor (5 archivos de 10 MB).
- Ficha de cliente › «Desvincular cuenta».
- `deploy/rehearsal_flows.py`: el ensayo recorre límites, integridad de la notificación
  de pago, un equipo con serie de la recepción a la venta, documentos, seguimiento,
  WhatsApp y Google apagados, y lo que guardan los registros.

**Changed**
- Producción no arranca sin `EMAIL_BACKEND`.
- gunicorn arranca sin socket de control.
- `/seguimiento/<código>` responde `Referrer-Policy: no-referrer` en su cabecera.
- La comprobación CSRF de la API lee el token sólo de la cabecera `X-CSRFToken`; ya no lo
  acepta como campo de formulario (ninguna pantalla lo enviaba así).
- El ensayo comprueba la portada V3 (ya no pide una variante de hero), la tienda a 390 y
  1440 px sin imágenes rotas ni errores de script, y las pantallas nuevas del panel.
- `.gitignore` y los `.dockerignore`: variantes de `.env`, claves, volcados y la
  configuración real del agente de impresión.

**Fixed**
- DOC-TIMEZONE: la nota de venta, su ticket, la salida térmica y el comprobante de pedido
  imprimían la hora en UTC (una venta de las 21:30 salía fechada al día siguiente).
- BACKUP-FILES-PARTIAL: un archivo de imágenes y evidencias cortado quedaba como copia.
- RESTORE-LATE-CHECK: la restauración miraba el archivo de imágenes después de haber
  reemplazado la base de datos.
- GUNICORN-CONTROL-SOCKET: un `[ERROR]` en cada arranque del backend.
- SLOW-BODY: nueve peticiones a medio enviar, sin sesión, dejaban la API sin responder.
- BODY-LIMIT-502: una petición demasiado grande recibía a veces 502 en vez de 413.
- CSRF-BODY-READ: una cuenta sin permiso podía hacer que el servidor recibiera y guardara
  una subida completa antes de negársela.
- REHEARSAL-HERO-V3: el ensayo fallaba en `master` por una comprobación de la portada
  anterior.

## 2026-10-05 — PAYMENTS-EQUIPMENT-DOCUMENTS

**Added**
- Adaptador de pagos «Mi Cuenta Web» (API REST V4 de Izipay): creación del pago,
  formulario Krypton en el checkout y notificación firmada en
  `/api/payments/micuentaweb/notification/`. Variables `MICUENTAWEB_*`.
- `PAYMENT_PROVIDER` elige el único producto de Izipay de la instalación (`izipay` o
  `micuentaweb`).
- Inventario › Equipos › «+ Registrar equipo»: formulario propio con «Guardar y añadir
  otro» y activación del seguimiento por serie.
- Inventario › Equipos › «Cargar desde Excel»: plantilla «Equipos serializados.xlsx»,
  previsualización y registro (migración `0110`).
- Plantilla de productos con hojas «Instrucciones» y «Ejemplo»; ayuda «¿Cómo preparo
  las imágenes?» en la pantalla de importación.
- Trazado común de documentos (`document_style`, `document_layout`): logotipo, caja de
  documento, tabla con código y unidad, serie e IMEI por línea, importe en letras,
  «Página X de Y».
- Guía `docs/pagos-equipos-documentos.md`.

**Changed**
- La nota de venta A4, su ticket de 80 mm, la salida térmica, el comprobante de pedido y
  el ticket de cotización usan el mismo diseño y llevan sucursal y tipo de pago.
- La nota de venta guarda el logotipo con el que se emitió.
- Un error al registrar un equipo nombra el campo que está mal.
- El archivo de stock que trae un producto con serie indica la plantilla de equipos.

**Fixed**
- IZIPAY-SIG-ASCII, DOC-DELIVERY-CITY, DOC-MARKUP, PANEL-OPTION-AMBIGUA (ver
  `02_ESTADO_ACTUAL.md`).
- De la revisión independiente: las notificaciones de pago aceptaban un cuerpo sin tope
  (WEBHOOK-BODY); un juego de caracteres inventado o un sustituto UTF-16 suelto daban
  500; una carga de equipos se leía sin alcanzar todas sus sucursales; un costo «NaN»
  rompía la previsualización; la lista de productos del formulario sumaba el stock de
  toda la empresa.

**Blocked**
- Prueba real de cualquiera de los dos productos de Izipay: faltan las claves de TEST y
  saber cuál tiene contratado el propietario.

## 2026-10-05 — SERVICE-TRACKING

**Added**
- Identificación de equipos: serie e IMEI según el tipo, dígito de control, segundo
  IMEI, motivo cuando no se puede leer, búsqueda «ya estuvo aquí».
- Enlace de seguimiento por orden (`/seguimiento/<código>`) y «Mis reparaciones»
  (`/repairs`); el cliente aprueba o rechaza la cotización desde el enlace.
- Decisión de cotización anotada por el personal (capacidad
  `service.quotes.record_decision`), reapertura de una cotización aprobada y ticket de
  80 mm de la cotización aprobada.
- Avisos por WhatsApp por la API oficial: proveedor intercambiable, configuración por
  empresa, consentimiento, plantillas, bandeja de salida con reintentos
  (`send_pending_notifications`), webhook firmado, estados entregado/leído, pantalla
  Administración › Mensajería, `configure_whatsapp`.
- Inventario › Equipos: productos con número de serie como parte del stock; KPI
  «Equipos disponibles».
- Productos › Categorías: activa, en la portada y orden, por tienda.
- «Continuar con Google», verificado en el servidor.
- Guía `docs/seguimiento-whatsapp-equipos.md`.

**Changed**
- La orden de servicio emite sus avisos desde los pasos reales, y avisa también al
  recibir el equipo y al empezar la reparación.
- La lista pública de categorías sale en el orden de la tienda y sin las retiradas;
  la portada ilustra sólo las marcadas.
- El carrusel de productos se arrastra con ratón, se asienta sin saltos, no trabaja
  fuera de pantalla y no retiene la rueda vertical.
- El stock de un producto con serie no se mueve por cantidad en ningún camino.
- `PyJWT` se declara en `requirements.txt` (ya se instalaba como dependencia).

**Fixed**
- NOTIFY-REAL-PATHS, TRACKING-REVOKE, TRACKING-BORN, TRACKING-SPELLING.
- CAROUSEL-JUMP: «Inicio»/«Fin» tras una flecha dejaban la fila entre tarjetas; el salto
  se reafirma hasta que la fila se queda donde se pidió. CAROUSEL-RESUME: reanudar
  enseguida de pausar ya no la inmoviliza.
- READ-ONLY-GET: el estado del enlace y el ticket son lecturas puras; la migración
  `0109` da su enlace a las órdenes anteriores.
- De la revisión: el enlace se entregaba a quien sólo podía abrir la orden; tener un
  enlace bastaba para quedarse con un cliente entero; un carácter no ASCII en el
  webhook daba 500; el historial de un equipo nombraba órdenes de otra sucursal; un
  movimiento por cantidad podía cruzarse con el cambio a «con serie»; una petición sin
  respuesta a WhatsApp se reenviaba sola.

**Security**
- Aislamiento por empresa y por sucursal probado en equipos, enlaces, cotizaciones,
  avisos, webhook, unidades con serie y categorías.
- El enlace de seguimiento no es enumerable, tiene una sola escritura y se revela al
  personal sólo con capacidad y con registro.
- Ninguna credencial de WhatsApp en la base de datos, en la API ni en los registros.
- Google: sólo RS256, audiencia, emisor, caducidad, correo verificado, intento ligado
  al navegador y de un solo uso; ningún enlace automático por correo.

## 2026-10-04 — MEDIA-EVIDENCE

**Added**
- Galería de producto subida desde el panel: varias imágenes, principal, orden, texto
  alternativo; `images` en el producto público; miniatura en la lista del panel.
- Carga masiva de productos con imágenes: columnas «Imagen principal» e «Imágenes»,
  archivos sueltos o ZIP, resumen de coincidencias en la previsualización.
- Evidencias de servicio: nota por foto, etapas de repuestos, listo para entrega y
  garantía/reingreso, conteo por etapa; varias fotos a la vez, cámara del teléfono, visor
  y antes/después.
- Guía `docs/imagenes-y-evidencias.md`.

**Changed**
- `Product.image_url` pasa a `CharField` validado y es la dirección de la principal.
- La plantilla de productos lleva las dos columnas de imagen y se reconoce al subirla.
- El cliente lee la nota de la evidencia que se le comparte.
- Límites propios para evidencias (lectura 600/min, escritura 120/min) y para cargas
  masivas (30/min).

**Fixed**
- IMPORT-ERRORS-404, AUTH-REGISTER-CSRF, DEV-PROXY-UPLOAD, EVIDENCE-THROTTLE.
- Guardar el texto alternativo se comía el clic siguiente.
- De la revisión: tope de 100 archivos de Django por debajo del nuestro; un flujo
  comprimido dañado daba 500; guardar el producto podía pisar la imagen de la galería; una
  imagen quitada por otra persona daba 500; archivos huérfanos tras descartar una
  previsualización; consultas por línea en carrito y pedidos; etapa de una foto en cola;
  reemplazo de una imagen con el mismo nombre; zona de soltar deshabilitada.

**Security**
- Aislamiento por empresa probado en galería, trabajos de importación, imágenes en
  espera y evidencias; por sucursal en evidencias.
- ZIP sin extraer: rutas que salen, enlaces simbólicos, cifrado, índice desproporcionado,
  tamaño expandido y entradas que mienten sobre su tamaño.
- Una imagen subida no se coloca escribiendo su dirección, ni en el panel ni en el Excel.

**Database**
- `0100_product_images`: `ProductImage`; `Product.image_url` a `CharField(500)`.
- `0101_evidence_caption_and_stages`: `RepairEvidence.caption`; etapas nuevas.

**Tests**
- Backend 4965 (4 omitidas); Jest 659/659; Playwright 179/179.
- Nuevos: `test_product_images.py`, `test_import_media.py`, `test_evidence_context.py`,
  `product-gallery`, `product-import-media`, `evidence-workflow`, `register-csrf`,
  `api-proxy-expect` (Jest), `e2e/media.spec.ts`.

**Decisions** DEC-MEDIA-01, DEC-IMPORT-MEDIA-01, DEC-EVIDENCE-01.

**Pending** IMPORT-BODY-LIMIT, EVIDENCE-CUSTOMER-WEB, TEST-ORDER-C15; repetir el ensayo de
producción.

## 2026-10-04 — Frontend V3 y cierre

- La V3 es el único diseño de la tienda, las páginas de cuenta y el panel (#82). El
  frontend anterior queda en la etiqueta `frontend-anterior-2026-10-04`.
- Un solo hero, que sigue al tema; se retira la opción «Estilo del hero» del panel.
- CART-CSRF-01: el carrito de un cliente con sesión iniciada vuelve a aceptar cambios.
- Integrados #81 (PAYMENT-FISCAL-PRINT-01), #67 (`reportlab` 5, validado a la vista),
  #64 (`actions/setup-python` 7) y #59 (infraestructura de producción).
- Backend 4865 pruebas; Jest 609/609; Playwright 176/176, 0 omitidas.
- Abierta: #80 (TypeScript 6, DEP-TS6).

## 2026-10-04 — PAYMENT-FISCAL-PRINT-01

- Izipay falso para pruebas (token y notificaciones) y corrección de una repetición
  contradictoria que reescribía un pago autorizado. Sandbox real: BLOCKED/CREDENTIALS.
- Comprobante impreso: etiqueta del documento del adquirente por su tipo, leyenda, unidad
  de medida, precio unitario, importe en letras y forma de pago (Anexos I y II, RS 114-2019).
- Logotipo de la tienda en sus comprobantes, congelado con cada uno y sin sombra.
- Impresión en tienda: cola por sucursal, agente local para térmicas de 80 mm en red,
  ticket automático tras la confirmación de la venta e idempotencia de extremo a extremo.
- Panel: Administración › Impresoras y Configuración › Comprobantes.
- Migraciones `0098` y `0099`.

## 2026-10-04 — Infraestructura de producción: ensayo final

- `deploy/rehearsal.sh`: ensayo completo y repetible. Última pasada: `ENSAYO: OK` (83 + 26).
- Imágenes de la tienda públicas y evidencias privadas en el mismo volumen, sin rutas de archivo.
- Persistencia tras reinicio, reconstrucción y restauración de copia.
- PostgreSQL 16 en producción; limpieza diaria de imágenes sin uso; Dependabot para imágenes base.
## 2026-10-04 — Playwright completo y aislamiento de pruebas

- Tres pruebas E2E dejan de depender de otro servidor, de un detalle de Playwright y del orden.
- Playwright 171/171, 0 omitidas.
- Dependabot: #63 y #66 mergeados; #64 pendiente del propietario; #67 (`reportlab` 5) abierta.

## 2026-10-04 — Limpieza de imágenes de la tienda

- Una imagen reemplazada o nunca colocada se borra cuando nadie la usa; nunca una en uso.
- Comando `cleanup_storefront_images` (con `--dry-run`).
- PNG con transparencia por color clave; imágenes de empresas desactivadas dejan de servirse.
- Backend: 4744 pruebas en PostgreSQL, 0 fallos.

## 2026-10-04 — Cierre previo al despliegue

- Menú del panel: una entrada por pantalla, un identificador por módulo.
- `Permissions-Policy` en todas las páginas.
- AUDIT-01…06, CSP-01, LINT-EFFECT-01 e INTERNAL-UI-KIT clasificados como propuesta; la rama
  `uxui/phase-03-internal-ui`, obsoleta.

## 2026-10-04 — Cabecera y pie V4

- Categorías de la cabecera accesibles con teclado y lector de pantalla.
- Carrito móvil con nombre accesible; Escape cierra el menú; objetivos táctiles de 44 px.
- Pie: una sola marca, banda de cierre V4, navegaciones con nombre.
- Lint: de 25 a 23 avisos. Next 16.3.8, React 19.3.0, Playwright 1.63.0.

## 2026-10-04 — Profundidad de los recortes (revisión de #60)

- La sombra de un recorte se ve sobre superficies oscuras (hero oscuro, promoción, tema
  oscuro): dos variables de CSS, una sola regla.
- Sin azulejo detrás de las imágenes de servicio y ubicación.
- `alt` correcto en la imagen de la portada; nombre accesible del campo de subida (WCAG 2.5.3).

## 2026-10-04 — Actualización de dependencias (CI-03)

- Dependabot semanal para `pip`, `npm` y GitHub Actions, sin merge automático.

## 2026-10-04 — Sistema de agentes

- Se incorpora política persistente de agentes y eficiencia de contexto.

## 2026-10-03 — Portada V4 · Imágenes subidas desde el panel

- La tienda sube sus imágenes desde «Escaparate»: hero, campaña, categorías,
  servicio técnico y ubicación. PNG/WebP conservan transparencia y las imágenes
  gestionadas por el panel reciben una sombra suave de profundidad.
- El hero tiene estilo oscuro o claro, elegido por la tienda (`9c39716`).
- Portada V4: tarjetas de categoría con imagen, franja de marca, servicios de la
  tienda y «Cerca de ti» (`9c39716`).
- Migraciones `0096_storefront_images` (tubería/hero/categorías) y
  `0097_storefront_section_images` (servicio/ubicación).
- Corregido: la importación de productos y stock respondía 415 (`b5de287`).
- `backend/private-media/` pasa a `.gitignore` (`3fa9b42`).
- Frontend 563 pruebas, typecheck, lint 0/25; Playwright 170 de 170, sin fallos, omitidas ni reintentos, 10,5 min.

## 2026-10-02 — F-TENANT-01

- Seguridad multiempresa: un administrador de empresa ya no puede vincular una
  cuenta global arbitraria por id mediante `POST /api/admin/memberships/`.
- El alta directa queda reservada a `User.is_superuser` / administrador de
  plataforma para bootstrap y migración.
- El onboarding normal de personal usa el flujo existente de
  `StaffInvitation` + aceptación.
- GET/PATCH de membresías mantienen su alcance tenant/branch actual.
- Sin migraciones.
- Cobertura nueva: `backend/store/test_membership_consent.py`.
## 2026-10-03 — DEPLOY-PREP · Infraestructura de producción

- Imágenes de producción de backend y frontend, `docker-compose.prod.yml`, Caddy
  con HTTPS automático, ejemplo de variables y guiones de copia y restauración.
- Guía completa en `docs/despliegue-produccion.md`, con la cadena de proxy, el
  proceso único de Django, el almacenamiento y la limpieza diaria de sesiones.
- Ensayo de 31 comprobaciones sobre el master actual: todas pasan.
- Sin cambios en el código de la aplicación ni migraciones. Nada desplegado.

## 2026-10-02 — FE-AUTH-06 · Enlace de salto

- El proxy `/api` devuelve al navegador las redirecciones hacia otro origen y sólo
  sigue las del propio backend bajo `/api/` (`085aa2e`).
- «Saltar al contenido» vuelve a ser el primer tabulador en la tienda y en el
  panel (`cf72ee5`).
- Frontend 538 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  169/169.
- PR #55 (endurecimiento del backend) y PR #56 (AUDIT-07, DEP-05) ya están en
  master; su CI de backend pasó con 4682 y 4686 pruebas.

## 2026-10-02 — AUDIT-07 · DEP-05

- Los cambios de producto y la creación de categorías quedan en la auditoría de su
  empresa (`06cd798`).
- Cinco dependencias del backend al último parche de su línea (`65d34ad`).
- Sin migraciones.

## 2026-10-02 — Endurecimiento del backend

- Límite de 30 por minuto y dirección al renovar la sesión, web y app
  (SEC-SET-04-A, `e066183`).
- Registro de cada intento de inicio de sesión, sin la contraseña
  (AUTH-LOGGING-01, `7d5efc8`).
- Configuración que falla cerrada: `SameSite`, sólo JSON en producción, URL
  públicas obligatorias, registro a stderr (SEC-SET-03/06/09/10, `e2dff73`).
- La importación de Excel aplica su límite antes de leer (SEC-SET-05, `e2dff73`).
- `.env.example` describe las 27 variables que faltaban (ENV-04, `a6beda6`).
- La edición de líneas bloquea la transferencia; `page_size` negativo ya no da 500
  (INV-LEGACY-V1-F2/F3, `1f965f7`).
- El admin de Django no se registra en producción (SEC-SET-02, `79d1077`).
- Sin migraciones.
- PR #49 (CI de backend) y PR #54 (endurecimiento del frontend) ya están en master.

## 2026-10-02 — Backend PostgreSQL CI

- Añadido workflow `.github/workflows/backend-postgres-validation.yml`.
- Ejecuta Django con Python 3.12 y PostgreSQL 16 en pull requests que toquen
  `backend/**` o el propio workflow.
- Gates: `manage.py check`, migraciones sin cambios pendientes y suite backend
  completa en un solo proceso.
- Sin cambios funcionales ni migraciones.
- La primera ejecución completa encontró dos defectos reales: `qrcode` faltaba en
  `requirements.txt` (los PDF fiscales fallaban en una instalación limpia) y una
  prueba de orden por nombre dependía de la colación de la base. Corregidos.

## 2026-10-02 — FE-AUTH-02 · FE-AUTH-04 · FE-AUTH-07 · Endurecimiento del frontend

- La clave del carrito anónimo sale de una fuente segura (`5adcd28`).
- `fetchWithAuth` sólo envía la sesión a la API propia (`40f9769`).
- Cabeceras de seguridad en todas las rutas; `no-referrer` en las páginas con
  token en la URL (`113a8dd`).
- Sin cambios de backend, migraciones ni API.
- Frontend 527 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #53 (DRIFT-02) ya está integrado en master por merge `3f70ca0`.

## 2026-10-02 — DRIFT-02 · Sucursal en el ajuste de inventario

- Con varias sucursales al alcance, el ajuste de inventario pregunta en cuál se
  aplica y la envía; con una sola no cambia nada (`068bee1`).
- Sin cambios de backend, migraciones ni API: el servidor ya aceptaba `branch`.
- Cobertura nueva: Jest `inventory-adjust-branch.test.tsx`.
- Frontend 511 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #52 (RBAC-F3, RBAC-F4) ya está integrado en master por merge `a6d725b`.

## 2026-10-02 — RBAC-F3 · RBAC-F4 · El panel no ofrece lo que el servidor niega

- Personal sólo ofrece invitar, desactivar acceso y reenviar o revocar invitaciones
  a quien tiene `memberships.manage` (`58d17de`).
- El menú del panel usa la misma regla que las páginas: con empresa mandan las
  capacidades; Auditoría declara `memberships.view` (`847d3bf`).
- Eliminado `BranchAccessPanel.tsx`, sin uso (`4f5d736`).
- RBAC-F6, RBAC-F7 y RBAC-F11 ya estaban corregidos en `master`; RBAC-F5 era el
  mismo defecto que RBAC-F3.
- Sin cambios de backend, migraciones ni API.
- Frontend 506 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #51 (botón del menú móvil) ya está integrado en master por merge `64b4d5e`.

## 2026-10-02 — ADMIN-MENU-ARIA · Botón del menú móvil del panel

- El botón que abre la navegación del panel en un teléfono anuncia que abre un
  diálogo y si está abierto (`aria-haspopup`, `aria-expanded`, `aria-controls`)
  (`e2ce0e2`).
- Sin cambios de backend, migraciones, auth, RBAC ni API.
- Cobertura nueva: Jest `admin-menu-trigger-a11y.test.tsx`.
- Frontend 494 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #50 (FE-AUTH-05) ya está integrado en master por merge `2d9cc97`.

## 2026-10-02 — FE-AUTH-05 · Tope al cuerpo en el proxy `/api`

- El proxy de Next ya no guarda en memoria un cuerpo sin límite: lee a trozos
  hasta 32 MiB (`API_PROXY_MAX_BODY_BYTES`) y responde 413 si se supera, sin llamar
  al backend (`4fad36b`).
- Sin cambios de backend, migraciones, auth, RBAC ni API.
- Cobertura nueva: Jest `api-proxy-body-limit.test.ts`.
- Frontend 491 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- Barrido móvil del panel sobre `master` `1815ac1`: 37 rutas, sin desbordes.

## 2026-10-02 — ADMIN-INVENTORY-MOBILE-OVERFLOW

- Corregido el desbordamiento móvil de `/admin/inventory`: los paneles y su cuerpo
  ahora pueden encogerse dentro del grid y las tablas de 640 px permanecen dentro
  de su `overflow-x-auto` local.
- Sin cambios de backend, migraciones, auth, RBAC, tenant scope ni API.
- Cobertura nueva: Jest `inventory-mobile-layout.test.tsx` y Playwright
  `admin-inventory-mobile.spec.ts` para 320/360/375/390/414 px.
- PR #47 (HERO-MOBILE-CLIP) ya está integrado en master por merge `03581ea`.

## 2026-10-02 — HERO-MOBILE-CLIP · El texto de la tienda cabe en un teléfono

Rama `fix/hero-mobile-clip`, desde `master` `c47c538`. El titular y el párrafo del
hero ya no se cortan en pantallas de hasta 414 px (`ff564e9`); lo mismo para el
titular del catálogo y «Productos relacionados» de la ficha (`e082b26`). De 640 px
en adelante todo sale idéntico a `master`. Dos pruebas de navegador nuevas miden el
texto contra la pantalla, no sólo el ancho de la página (`ff564e9`, `e082b26`,
`42ef631`). Sin backend, sin migraciones, sin cambios en el panel. Frontend 483
pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright 163/163.

Revisión del diseño V3 pendiente, sin código: el perrito es el isotipo de cada
tienda y ya está; las imágenes del trabajo paralelo siguen fuera por falta de
licencia (STOREFRONT-IMAGES-LICENSE); INTERNAL-UI-V3 sigue pendiente porque sólo
toca piezas compartidas del panel; TENANT-TYPOGRAPHY y STOREFRONT-PILLARS-CMS
siguen como propuesta. Deuda nueva: ADMIN-INVENTORY-MOBILE-OVERFLOW.

## 2026-10-01 — STOREFRONT-V3 · Tienda pública sobre el master auditado

Rama `reconcile/storefront-v3-after-ux`. Cabecera, pie y portada leen las
categorías del catálogo real y el pie filtra con `?category=` (`a57c72e`). Carrusel
de productos, entradas con movimiento reducido respetado y línea del carrito
enlazada a su ficha (`9ba1a70`). Portada sobre el CMS de la tienda, con campaña
`home_hero` opcional y la losa oscura del hero intacta (`11f69f0`). Páginas
`/about` y `/contact` con datos de la tienda (`5380ca6`). Pruebas de navegador de
la experiencia pública (`7f49168`). Sin backend ni migraciones; ningún archivo del
panel interno cambia (su contenido queda envuelto en `internal-surface`).
Frontend 483 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
143/143.

## 2026-10-01 — UX-RECON-SVC-01 · Interfaz reconciliada con servicio y caja

Rama `reconcile/uxui-after-svc`: la capa UX/UI del PR #43 (`9c1486b`) sobre el
`master` que ya incluye SVC-FUNC-01 (`d98d70c`), en el merge `ee3a8d3`. La raíz del
servicio sigue redirigiendo a Órdenes, las seis colas conservan su ruta y adoptan el
diseño de #43 en `ServiceQueue`, y la caja mantiene «Productos / Servicio técnico».
Asignación y cobro quedan como en `master`. Sin backend ni migraciones. Frontend 437
pruebas, typecheck, lint 0/26 y build OK (50 páginas); Playwright 121/121.

## 2026-10-01 — SVC-FUNC-01 · Servicio técnico e integración con la caja

Rama `feature/service-pos-functional-integration`. Cada módulo del
servicio técnico tiene su ruta y su cola (`937cf82`). Nueva capacidad
`service.orders.assign`: asignar técnico ya no exige `service.orders.manage`, y el
técnico debe poder ver órdenes y alcanzar la sucursal (`1928b05`). Nueva capacidad
`service.payments.collect`: el técnico registra pagos y no puede reversarlos
(`d62fa30`). La caja recibe equipos para servicio y asigna al técnico en la misma
operación, sin crear venta, stock, comprobante ni comisión (`6caa88c`). Prueba de
navegador del flujo caja → técnico → cobro y reparación de prueba sembrada por
`seed_demo_users --e2e-fixtures` (`1d35b7d`). Dejar una orden sin técnico exige
`service.orders.manage`; con `service.orders.assign` sólo se asigna y se reasigna
(`4796db0`). Nombrar a un técnico exige en todos los caminos
`service.orders.manage`, o `service.orders.assign` con `service.orders.view`
(`9b59a31`). Migraciones `0094` y `0095` (sólo roles estándar sin modificar).
Backend 4643 pruebas, 0 fallos, en PostgreSQL; frontend 428 pruebas, typecheck,
lint 0/33 y build OK (50 páginas); Playwright 121 de 121, sin fallos, omitidas ni reintentos, 9,0 min.
## 2026-10-01 — Integración en master y reconciliación UX/UI

ERP + F1/F2 integrados en `master` por el PR #42 (`ef9890f`). Reconciliación de la rama
UX/UI (#39) en `reconcile/uxui-after-f2` (`1fea6b9`): diseño del control interno,
carrito, catálogo y detalle adoptado; portada, cabecera, pie, servicios e inicio de
sesión se quedan como estaban en `master`. Frontend 411 pruebas OK, typecheck, lint
0/26, build OK y Playwright 118/118. Sin backend ni migraciones.

## 2026-09-30 — F2 completada

Cierre de la fase «dónde» de la auditoría (`c191a84`): E2E-02 (`9c3445f`,
trabajador desechable para las pruebas de personal, `seed_demo_users
--e2e-fixtures`) y E2E-01 (`c191a84`, la prueba fiscal afirma «Preparar factura» y
«Enviar a SUNAT» como dos pasos). Backend 4569 pruebas, 0 fallos, en PostgreSQL;
frontend 402 pruebas, typecheck, lint 0/33 y build OK; Playwright 118/118. Sin
migraciones en toda la fase.

## 2026-09-30 — F2 · DRIFT-01 y DRIFT-07

Consola de servicio: el selector de técnicos lee `candidates` (`a4be03b`). Panel de
administración: las acciones se ofrecen sólo si la persona tiene la capacidad y el
alcance por sucursal que el servidor exige (`6958ec0`, `f6dc9ca`). Frontend 400
pruebas OK, typecheck, lint 0/33 y build OK. Backend sin cambios.

## 2026-09-30 — F2 · WRITE-SCOPE-01

Las modificaciones de nivel empresa (crear sucursal, sucursal de despacho, serie de
empresa, alcance de numeración, ajustes de empresa) exigen alcance sobre toda la
empresa además de `company.manage`; quien tiene sucursales seleccionadas sólo edita
las suyas (`fa85d41`, `c076120`). Backend 4563 pruebas, 0 fallos, en PostgreSQL.
Sin migraciones.

## 2026-09-30 — F2 · RBAC-01 y RBAC-02

Empresa y sucursales sólo se leen con capacidad de lectura (`5afdb81`); sin ella,
lista vacía y 404. La sucursal por defecto de una membresía responde un único 404
para ids inexistentes, ajenos o fuera de alcance (`d18e983`). Backend 4545 pruebas,
0 fallos, en PostgreSQL. Sin migraciones.

## 2026-09-30 — F2 · F-CAP-01

Crear un producto con stock inicial exige `inventory.adjust` además de
`products.manage`; sin ella responde 403 sin efectos. Crear sin stock no cambia.
Commit `c042fea`. Backend 4528 pruebas OK en PostgreSQL. Sin migraciones.

## 2026-09-30 — F2 · Delegación por sucursal

Nueva regla `can_delegate_branch_scope`: una persona limitada a sucursales
seleccionadas ya no puede ampliarse su propio acceso, pasar a nadie a «todas»,
conceder o retirar sucursales que no alcanza (membresías e invitaciones), crear o
modificar promociones fuera de su alcance, ni leer o reescribir por id la serie
interna de otra sucursal. La edición de membresías deja de guardar cambios a medias
cuando la lista de sucursales se rechaza. Commits `20d110c`, `cccb4d2`, `70286d1`.
Backend 4520 pruebas OK en PostgreSQL. Sin migraciones.

## 2026-09-30 — Auditoría F1 y memoria central

`4a9dd5c` corrige FE-AUTH-01 (HIGH): el proxy de Next ya no puede salir de `/api/`
con segmentos codificados (`%2f`, `%2e%2e`, `%5c`). Se añade `docs/AUDIT_MEMORY.md`,
índice verificado de la auditoría (baseline, invariantes, hallazgos abiertos y
cerrados, tests y consultas rápidas). Sin cambios de backend ni migraciones.

## 2026-09-29 — Cierre de ERP-FISCAL-6 y apertura de la auditoría integral

Commits de cierre en `erp/sales-fiscal-ui`: `97043d6`, `c9e64b9`, `fabfa38`, `9525b08`
(HEAD empujado). Rama `audit/full-system-2026-09` desde `9525b08` con `origin/master`
`2dca0a3` integrado (`65aa8c1`, merge limpio, sólo documentación). Baseline medido sobre
`65aa8c1`: backend 4489 pruebas OK (3 omitidas) en PostgreSQL; frontend 357 pruebas OK,
typecheck, lint 0/33 y build OK; Playwright 113/118 con un fallo preexistente (E2E-01) y
4 pruebas no ejecutadas por él. Hallazgo CI-01 confirmado: sin integración continua
propia. La remediación de hallazgos no comienza hasta fijar el baseline.

## 2026-09-29 — ERP-FISCAL-6 · Descuentos declarados

Promociones: atribución determinista del descuento a cada componente (proporcional
al valor regular, ROUND_DOWN, mayor residuo, desempate por `product_id`), congelada
en `AppliedPromotion.metadata`; el snapshot congelado es la autoridad y el legacy se
reconstruye en memoria (ADR-42). Fiscal: factura y boleta declaran cupón y manual
como `AllowanceCharge` global `02` y promociones por línea con `00`, con importes
netos derivados del snapshot; `PayableAmount` es lo cobrado y no hay doble descuento
(ADR-43, reglas oficiales de validación 21.04.2025). POS: `receipt_options` explica
por qué una opción no está disponible, el selector no permite elegirla, y la venta
completada muestra el comprobante real; `seed_demo_users --fiscal-beta` prepara las
series DEMO en BETA. Corregido: la venta preparaba el comprobante antes de congelar
la promoción; `resolve_series` decía «factura» cuando faltaba una boleta.

Validación: backend 4489 pruebas, OK (3 omitidas), 1917,4 s; frontend 357 pruebas, OK; typecheck, lint (0
errores, 33 advertencias) y build PASAN; Playwright `pos-ticket` PASA,
`pos-receipt-options` PASA. Migraciones nuevas: 0.

## 2026-09-28 — Estabilización funcional

POS: selección de documento autorizada y transaccional, idempotencia y numeración
existente. Servicio: recepción web, cola por capacidad, entrega limitada para
Ventas y timeline corregido. Avisos/portal: respeto de estados ocultos y estado
real de correo. Fiscal UI: boleta BETA, firma previa al envío y permisos de acción.
El envío de boletas corresponde al Resumen Diario, cuya gestión web sigue
pendiente. Las notas de crédito/débito ya no reemplazan al comprobante original
en la consulta histórica de una venta. Migraciones locales aplicadas con respaldo.

Validación: backend 4412 pruebas, OK (3 omitidas); frontend 350 pruebas, OK.
Typecheck y build de producción PASAN: los dos badges de inventario se extrajeron
al módulo compartido de componentes, que es de donde esas páginas ya importaban
sus primitivas. Playwright: 17 de 17 en POS y acceso interno.

Detalle y validación:
[Informe de entrega](docs/estabilizacion-funcional-2026-09-28.md).
