# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master`; CHECKPOINT 1B ensayado sobre `a1d2a29` (#93), 2026-10-07.

open_prs:
- #80 (TypeScript 6, DEP-TS6) y #85 (@types/node 24): versiones mayores que el lanzamiento
  no necesita; se deciden aparte. Antes de mergear: actualizarlas sobre `master` y repetir
  su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
CHECKPOINT 1B cerrado: **READY FOR CHECKPOINT 2** sobre `a1d2a29` (#93).
FIX-MEAS-LOG-TEST-01 IMPLEMENTADO, CI completo verde sobre su HEAD exacto.
Esperar aprobación explícita del plan Lightsail antes de crear cualquier recurso AWS.
Dominio comprado: `blackdogstoreperu.com`. Ningún servidor creado ni tienda publicada.
La preparación técnica está validada; no equivale a READY FOR PRODUCTION.

Producción: NOT READY, por datos externos y no por el código. ANALYTICS-MARKETING-INTEGRATIONS-01
cerrada: Google Analytics 4, Meta y TikTok se configuran en la consola (sólo MASTER), la
tienda pide permiso antes de cargar nada de terceros y la compra se mide una vez, desde
la confirmación del pago, en `master` desde #91. Ninguna integración se ha probado contra
su servicio real. No iniciar SERIAL-PICK hasta que el propietario lo ordene.
Sigue sin haber servidor, SMTP ni claves de Izipay. El dominio ya está comprado; DNS pendiente. No hay nada publicado.

current_priority:
0. INT-IMPORT-CYCLE-01 (microfase, espera el GO del propietario): quitar los dos ciclos
   de imports que introdujo #91, sin cambiar comportamiento, API, esquema ni RBAC.
1. Con aprobación del propietario, crear el servidor del CHECKPOINT 2. El dominio ya está comprado. En el servidor:
   `cp deploy/.env.production.example deploy/.env.production`, rellenar lo de arranque
   (dominio, `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_CONFIG_ENCRYPTION_KEY`, dirección de
   los pedidos) y `python3 deploy/preflight.py` hasta `SUFICIENTE PARA ARRANCAR`.
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
- Escaneo del diff limpio; rama local del propietario preservada. Sin recursos AWS.
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
- Servidor: proveedor, IP, usuario SSH y acceso.
- Dominio y quién gestiona el DNS.
- Destino de la copia externa, y dónde guardar la copia de `deploy/.env.production`.
- Dirección que recibe el aviso de cada pedido.
- Existencias, fotos y precios reales del catálogo.

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
Propietario: decir cuál de los dos productos de Izipay tiene contratado y contratar el
servidor. Con el servidor: `cp deploy/.env.production.example deploy/.env.production`,
rellenar lo de arranque allí, `python3 deploy/preflight.py`, arrancar, y configurar el
correo y la pasarela en Panel › Configuración › Integraciones.
