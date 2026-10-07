# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: baseline del servidor `c5f8c935a8b16072a6424184fc814efbe1babaf1` (#95), 2026-10-07.
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
MEAS-PRIVACY-NOTICE bloquean activar analítica. No iniciar SERIAL-PICK ni
INT-IMPORT-CYCLE-01; no tocar #80/#85.
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
3. Un pago completo en TEST con la tienda publicada: cierra IZIPAY-PRODUCT e
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
6. Primera fase tras publicar: SERIAL-PICK, cuando el propietario lo ordene.

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
- SERIAL-PICK (lo primero tras publicar): la caja vende el equipo más antiguo; hay que
  entregar el que nombra la nota. SERIAL-TRANSFER, SERIAL-COUNT.
- PAY-RECONCILE: si la notificación de la pasarela no llega, un pedido cobrado queda
  esperando; `healthcheck.sh` lo anota (sin alarma), no lo resuelve.
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
