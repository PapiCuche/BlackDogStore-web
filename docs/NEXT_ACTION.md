# NEXT ACTION

## Decisiones pendientes del propietario

- Devolución posterior a la entrega: D1–D8 en `docs/diseno-devolucion-post-entrega.md`.

## Desplegado: ORDER-FULFILLMENT-SAFETY-01 (#128, `7903a49`) el 2026-10-17 — pruebas del propietario (PENDIENTES)

Con su sesión, sobre pedidos de prueba (no sobre pedidos reales):
1. Pedido de recojo: avanzar Pendiente → En preparación → Listo para retiro → Entregado; ver
   que «Enviado» nunca aparece y que «Entregado» pide confirmación.
2. Pedido con envío nacional: «Enviado» se bloquea sin transportista y guía; con ellos, pasa.
3. Volver un paso exige motivo; después de «Enviado» no hay vuelta atrás.
4. Historial: cada cambio con fecha, responsable y motivo.
5. **HECHA por el propietario el 2026-10-19** (corregida y cancelada, reembolso pendiente
   de un pago TEST). Texto original: Orden #4 (elegible, sin modificar): entrar con la cuenta del rol `administrador`, abrir
   Pedidos › #4 › «Estado de despacho» › «Corregir marcado de salida» › «Corregir este
   registro», escribir el motivo, marcar «Confirmo que el producto nunca salió de la tienda» y
   «Corregir registro»; revisar
   que el registro original sigue y sale «Corregido»; decidir después si se cancela (la
   cancelación no ocurre sola; el pago fue TEST, así que no hay dinero que devolver).
6. Con una cuenta sin `company.manage`: la corrección no aparece.

## Desplegado: COMMERCE-ADMIN-UX-01 y cierre financiero (#125, #126, `68137e5`) el 2026-10-16

Pruebas que el propietario debe hacer con su sesión (no hechas por el agente). Usar un
producto y pedidos de prueba, y pagar sólo con Izipay TEST:
1. Con la cuenta `administrador`: Inventario › Costos y precios muestra margen y valor al
   costo. Con una cuenta de ventas, esa entrada no aparece.
2. Ventas › Consultar disponibilidad con una cuenta de ventas: stock por sucursal, sin costos,
   IMEI enmascarado.
3. Registrar un artículo con el asistente único y publicarlo en el centro de publicación; verlo
   en la tienda.
4. Cancelar un pedido web impago: los equipos apartados se liberan y no aparece ingreso.
5. Pagar un pedido web con Izipay TEST y cancelarlo marcando reembolso: queda «pendiente».
   Confirmarlo con referencia, evidencia e importe exacto (sólo con `company.manage`).
6. Venta de caja reciente: anularla con «la mercadería volvió al local» (simple). Configuración
   › plazo de anulación a 0 y repetir con otra venta: debe pedir un superior.
7. Revisar el tablero: la venta anulada sale del total y el dinero cobrado aparece aparte en
   «Cobros»; el informe de comisiones la muestra como anulada.
8. Kardex: la devolución de stock de la anulación aparece al costo de la venta.
9. Devolver el plazo de anulación a 7 y desactivar los datos de prueba.

Decisiones abiertas: confirmar que «compleja» = stock de varias sucursales; pantalla para
liquidar comisiones (hoy no existe, así que anular sólo anula). PR abiertos: #120 (necesita
una migración de fusión) y #122.

## Desplegado: DEVICE-COMMERCE-01 (#123, `04640cf`) el 2026-10-15

Pruebas que el propietario debe hacer con su sesión (no hechas por el agente):
1. Panel › Costos: ver «Margen comercial» y «Valor al costo» con una cuenta con `costs.view`
   y confirmar que una cuenta de ventas/almacén NO los ve.
2. Crear un modelo con capacidad y color y un producto de prueba; publicar; ver la página
   del modelo en la tienda (variantes, imágenes, precio por variante).
3. Registrar un seminuevo de prueba (batería, estado, foto) y comprobar en la tienda que
   el IMEI sale enmascarado.
4. Reservar ese equipo en el carrito, pagar con tarjeta de prueba de Izipay TEST y ver la
   venta de esa unidad exacta; dejar otra reserva sin pagar y ver que vence.
5. Caja: venta con stock y venta «Bajo pedido» con una oferta de proveedor válida; una
   oferta vencida al pagar debe quedar «abastecimiento por confirmar».
6. Cargar ofertas por Excel (vista previa y aplicar) y «Recibir del proveedor» un pedido.
7. Borrar o desactivar después los datos de prueba.

PR abiertos sin fusionar: #119, #120, #121 (sobre #119), #122.

## Hecho: INVENTORY-UNIFIED-01 en `master` (#117, `041d0fd`) y desplegado el 2026-10-14

Siguiente fase ordenada por el propietario: ORDER-LIFECYCLE-01 y después PAYABLES-01,
desde `master` y en ramas propias.

## Historia de la fase (para consulta)

Las cuatro partes están en la rama, con commits locales: visibilidad en bloque y
exportaciones (hasta `74c6112`), centro de gestión masiva con asistente e imágenes en
lote (hasta `ac6a061`), las correcciones de la revisión independiente (`8b9dda1`…
`af0b374`) y las siete decisiones del propietario del 2026-10-13 (`e2c9c84`…`4a46012`,
más `a94c8fa`, `0dbf3b3` y `1f21cfe` tras la tercera revisión, y la documentación). **Publicada con push y PR a `master`; sin fusionar ni desplegar.**
Detalle en `02_ESTADO_ACTUAL.md` (las cuatro entradas de arriba) y DEC-INVENTORY-UNIFIED-01.

Hecho antes de abrir el PR:
1. Sobre `6e556c9`: suite completa del backend (7056, 0 fallos), Playwright 97/97,
   recorrido en navegador y `sh deploy/rehearsal.sh` en verde; Jest, tipos y lint sobre
   `4a46012` (el frontend no cambia después). Cifras en `02_ESTADO_ACTUAL.md`.
2. La rama añade las migraciones `0122` (sólo opciones) y `0123` (una columna con valor por
   defecto en la base) y cambia el carrito público, «Mis pedidos» (web y app), la lista de
   productos, la configuración de la empresa, las descargas y las tres cargas.
3. Tres revisiones independientes: HECHAS (sin P0/P1), sus P2/P3 corregidos y las dos últimas sin
   regresiones; `af0b374` cierra el último aviso. El propietario decidió los puntos
   abiertos el 2026-10-13 (D1–D7 en DEC-INVENTORY-UNIFIED-01): implementados en la rama.
   Las imágenes en lote también se decidieron (2026-10-14): el código se compara sin
   mayúsculas ni espacios, lo ambiguo no se asigna y lo que no tiene producto no
   detiene el resto.
4. Con autorización expresa: merge y despliegue (migrar `0122` y `0123`;
   vuelta atrás en `docs/despliegue-produccion.md` §7). El Caddyfile del servidor NO
   cambia. Variable nueva y opcional: `ADMIN_EXPORT_RATE` (6/min si no se pone).

Encontrado y sin tocar: `P0ECartConcurrencyTest.test_many_at_once_still_add_up` falla si
es la primera `TransactionTestCase` del proceso (deuda P0E-CART-RACE-ORDER, anterior).


## Prioridad del pedido actual: privacidad de medición

PARCIAL: aviso `/privacy` y consentimiento v2 preparados. Revisar y publicar tras
autorización expresa de despliegue; configurar identidad legal y correo del titular
con los datos confirmados en la conversación (no hardcodearlos en el producto).
Validar la retención real de Analytics y completar la política para otras finalidades
antes de declarar cumplimiento. No aceptar automáticamente la declaración de Google
ni activar el borrador del secreto API. La configuración de producción queda intacta.

## Contexto previo del lanzamiento

Se sobrescribe al cerrar cada fase. No es un changelog.

master: el servidor sirve `14e658cb3fc4aef2271d83417e25e83d1fc20a7d` (#111, CLIENT-SEC-01 y base de DELIVERY-01; incluye #102–#110), desplegado el 2026-10-13.
Volver a hacer fetch antes de asumir el HEAD remoto; cambios posteriores de docs no
actualizan automáticamente la copia desplegable del servidor.

open_prs:
- #80 (TypeScript 6, DEP-TS6) y #85 (@types/node 24): versiones mayores que el lanzamiento
  no necesita; se deciden aparte. Antes de mergear: actualizarlas sobre `master` y repetir
  su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
CHECKPOINT 2 **PARCIAL · 25% · BLOCKED/OWNER-DATA**. `GO AWS` recibido; servidor
Lightsail `blackdogstore-prod-01` creado en `sa-east-1a` (4 GB, 2 vCPU, 80 GB,
US$24/mes), Static IP `blackdogstore-prod-ip` `54.94.236.23` adjunta.
Docker/Compose, deploy, UFW, swap y copia limpia del baseline preparados.
Secretos de arranque generados sólo en el servidor, `.env.production` 600.
Compose config OK; preflight INCOMPLETO por `ORDER_NOTIFICATION_EMAIL`.
No hay contenedores, MASTER ni tienda publicada. HTTP/HTTPS cerrados en AWS.
`GO DNS` pendiente: Cloudflare autenticado, zona sin registros; propuesta A @ y
A www hacia Static IP, DNS only, TTL Auto; nameservers sin cambios.
Producción NOT READY. Analítica/marketing e Izipay sin configurar; CSP-01 y
MEAS-PRIVACY-NOTICE bloquean activar analítica. SERIAL-PICK-01 e IMPORT-SPLIT-01 en
`master` y desplegados el 2026-10-11. No iniciar INT-IMPORT-CYCLE-01; no tocar #80/#85.
CHECKPOINT 1B cerrado sobre `a1d2a29` (#93); FIX-MEAS-LOG-TEST-01 IMPLEMENTADO.
FRESH-PRODUCTION-DATA-01 IMPLEMENTADO / MERGED con #95 en `c5f8c93`.

current_priority:
0a. FRESH-PRODUCTION-DATA-01 IMPLEMENTADO / MERGED (#95): al desplegar,
   PostgreSQL vacío → migrate → bootstrap_pilot_store --apply → comprobar cero
   productos/stock/movimientos, cinco categorías y campaña conservada. Primer
   usuario MASTER con createsuperuser, sin membership implícita (§5 de la guía).
0. INT-IMPORT-CYCLE-01 (microfase, espera el GO del propietario): quitar los dos ciclos
   de imports que introdujo #91, sin cambiar comportamiento, API, esquema ni RBAC.
1. Continuar CHECKPOINT 2 en el servidor ya creado, sin apertura pública. En el servidor:
   `.env.production` ya existe y contiene los secretos generados: no volver a copiar
   el ejemplo ni regenerarlos. Completar sólo el destinatario real de pedidos y
   ejecutar `python3 deploy/preflight.py` hasta `SUFICIENTE PARA ARRANCAR`.
2. `docs/despliegue-produccion.md` §4 en orden. Con la cuenta MASTER, en
   Configuración › Integraciones: correo y pasarela (claves de TEST, probar, activar).
3. Un pago completo en TEST con la tienda publicada y la sesión de MASTER iniciada (a
   los demás compradores la tienda no les abre el pago mientras las claves sean de TEST): cierra IZIPAY-PRODUCT e
   IZIPAY-TOKEN-CONTRACT.
4. Analítica y marketing (opcional; `docs/analytics-marketing.md` §9). Condiciones para
   activar un proveedor en producción: CSP-01 implantada y validada con los dominios
   reales de la pasarela y de los SDK (sin lista especulativa), y la política de
   privacidad y cookies al día (MEAS-PRIVACY-NOTICE). Después: IDs y tokens en la consola,
   con código de evento de prueba; una compra de prueba que aparezca UNA vez; quitar los
   códigos de prueba. En los paneles de Meta y TikTok, coincidencia avanzada automática
   apagada.
5. Repetir `sh deploy/rehearsal.sh` sobre el commit que se vaya a publicar y recorrer
   `docs/despliegue-produccion.md` §11. Abrir el tráfico es orden del propietario.
6. SERIAL-PICK-01 está hecho en `feat/serial-pick`, a la espera de fusión. Siguen la
   garantía y posventa (3B) y el plan de códigos promocionales.

validated:
- CHECKPOINT 1B en `a1d2a29`: backend relevante 445 (2 sandbox BLOCKED/CREDENTIALS),
  frontend 933/933, typecheck/build correctos, lint 0 errores / 22 avisos; imágenes,
  compose, preflight con datos sintéticos y ENSAYO COMPLETO 139, navegador, backup y
  dos restores correctos; limpieza completa. CI #93: 5654, OK, 5 omitidas conocidas.
- Escaneo del diff del CHECKPOINT 1B limpio; rama local del propietario preservada.
  En ese checkpoint no se crearon recursos; el servidor se creó después con GO AWS.
- Sobre `62ed3b8` (el código de la fase, en `master` con #91; lo posterior es documentación): backend 5654
  pruebas, 0 fallos, 5 omitidas · Jest 933/933 · tipos limpio · lint 0 errores, 22 avisos ·
  build correcto · Playwright 194/194, 0 omitidas · `ENSAYO: OK` (139 + 49).
- Revisión de seguridad independiente: 1 P1 y 6 P2 corregidos; CSP-01 sigue PENDIENTE.
- Grafo final con Graphify 0.9.79: sin proveedores sin consumidor, `gtag`/`fbq`/`ttq`
  sólo en los adaptadores, 2 ciclos de imports nuevos del mismo patrón que #90
  (INT-IMPORT-CYCLE), ninguno al cargar.
- Ninguna integración probada contra su servicio real (BLOCKED/CREDENTIALS).

blocked_external (BLOCKED/OWNER-DATA: sin esto no se abre):
- Izipay: cuál de sus dos productos tiene contratado («SDK web / Checkout» o «Mi Cuenta
  Web») y sus claves de TEST; después, las de producción (IZIPAY-PRODUCT,
  IZIPAY-TOKEN-CONTRACT, las dos abiertas). Se escriben en la consola.
- SMTP: host, puerto, usuario, contraseña, remitente y tipo de cifrado. En la consola.
- DNS: GO DNS independiente para A @ y A www hacia 54.94.236.23, DNS only.
  Servidor y acceso AWS ya disponibles; dominio gestionado por Cloudflare.
- Destino de la copia externa, y dónde guardar la copia de `deploy/.env.production`.
- Dirección que recibe el aviso de cada pedido.
- Catálogo, existencias, fotos y precios reales: tras `bootstrap_pilot_store --apply` la
  tienda nace sin productos.

blocked_optional (BLOCKED/OPTIONAL: la tienda abre sin esto):
- Google Analytics 4: ID de medición y, opcional, secreto de API (BLOCKED/CREDENTIALS).
- Meta: ID del píxel y, opcional, token de la API de conversiones (BLOCKED/CREDENTIALS).
- TikTok: código del píxel y, opcional, token de Events API (BLOCKED/CREDENTIALS).
- ID de cliente OAuth de Google (GOOGLE-CLIENT-ID).
- WhatsApp Business: número, plantillas aprobadas y credenciales (WHATSAPP-CREDENTIALS).
- SUNAT: certificado y credenciales SOL.
- Vigilante externo y correo para los avisos de `healthcheck.sh`.

otros pendientes del propietario:
- Licencia de las imágenes de la propuesta (Apple/Figma): no se versionan; se suben desde el panel.
- Texto de marca de la V3 que nombra a Apple.
- Configuración del repositorio: alertas de vulnerabilidad, escaneo de secretos y protección de push.
- Confirmar que el código de comercio `4001061` de las pruebas no es el suyo (es público, no un secreto).

known_debt (detalle en `docs/AUDIT_MEMORY.md`):
- De analítica y marketing (`docs/analytics-marketing.md` §9): CSP-01 (bloqueante para
  activar un proveedor en producción), MEAS-PRIVACY-NOTICE (antes de producción),
  INT-IMPORT-CYCLE (microfase INT-IMPORT-CYCLE-01), MEAS-FORM-PAGES, MEAS-SERVER-EVENTS,
  MEAS-GA-HISTORY, MEAS-PIXEL-URL, MEAS-HARD-NAV, MEAS-CONSENT-LOG, MEAS-TENANT-SCOPE.
  Decidido: MEAS-IP-UA aprobado bajo consentimiento de marketing; coincidencia avanzada
  automática desactivada (DEC-MEAS-08).
- De la consola (`docs/integraciones-y-secretos.md` §10): PAY-TENANT-SCOPE y
  FISCAL-TENANT-SCOPE (pasarela y SUNAT son de la instalación), PAY-SWITCH-PENDING
  (cambiar de producto con cobros abiertos), SUNAT-SOL-CHECK, ENV-FALLBACK-RETIRE,
  INTEGRATION-READ-COST, MAIL-TENANT-SCOPE.
- SERIAL-TRANSFER, SERIAL-COUNT. SERIAL-PRICE-DROP: borrar la columna
  `price_override` en la versión siguiente a la que publique SERIAL-PICK-01.
- PAY-RECONCILE: con «Mi Cuenta Web», `reconcile_payments` pregunta a la pasarela
  (`Order/Get`) y paga lo cobrado (DEC-PAY-03); hay que añadir su línea de
  `deploy/crontab.example` al servidor. Con el otro producto sigue como estaba.
  PAY-TEST-BADGE: el panel no señala un pedido pagado en TEST. PAY-PAUSE-ENV: pausar
  pagos necesita la configuración en la consola. PAY-GET-UNKNOWNS: lo que la prueba con
  claves de TEST tiene que contestar (`docs/pagos-equipos-documentos.md` §1.5).
- PAY-UNCONFIGURED-500: sin credenciales, pagos responde 500 «no está configurada».
- NOTE-LINE-DISCOUNT: la nota de venta muestra el descuento del pedido, no por línea.
- LOGIN-IDENTIFIER-LOG: un inicio de sesión fallido registra lo escrito como usuario.
- CUSTOMER-MERGE: no hay fusión de dos registros del mismo cliente.
- GOOGLE-NONCE-CACHE, WHATSAPP-INLINE-SEND: ver `docs/AUDIT_MEMORY.md` §10.
- TEST-ORDER-C15: una clase de pruebas depende del orden de ejecución.
- HERO-VARIANT-LEGACY (obsoleto): `hero_variant` sigue en el modelo y la API.
- Propuestas de evidencias: exigir fotos por etapa, plantillas de recepción, ZIP de
  evidencias, miniaturas en el comprobante de recepción.
- AUDIT-01…06: ediciones de borrador sin fila de auditoría.
- CSP-01: política de scripts (nonce + captura del SDK de Izipay).
- LINT-EFFECT-01: 22 avisos `set-state-in-effect`.
- INTERNAL-UI-KIT: componentes del panel definidos en tres sitios.
- DEP-TS6, DEP-TS7, DEP-ESLINT10: versiones mayores.
- FISCAL-PRINT-EXO, PRINT-USB: ver `docs/AUDIT_MEMORY.md`.
- LOGIN-CSRF-01 (baja); THROTTLE-CACHE-01 (controlado: un proceso de gunicorn).
- Los huecos `home_featured` y `home_promo` existen en el modelo y la portada no los pinta.

next_exact_action:
Propietario: indicar únicamente el correo real que recibe los avisos de pedidos
(ORDER_NOTIFICATION_EMAIL), sin contraseña. Después: completar preflight en el
servidor, reconstruir imágenes y continuar el arranque limpio documentado. DNS se
modifica sólo después de GO DNS sobre el antes/después; no abrir tráfico público.
SMTP e Izipay TEST se escriben directamente en el panel MASTER cuando esté disponible.
