# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió al cerrar INTEGRATIONS-CONSOLE-01)

open_prs:
- #80 (TypeScript 6, DEP-TS6) y #85 (@types/node 24): versiones mayores que el lanzamiento
  no necesita; se deciden aparte. Antes de mergear: actualizarlas sobre `master` y repetir
  su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
NOT READY, por datos externos y no por el código. INTEGRATIONS-CONSOLE-01 cerrada: el
correo, la pasarela, WhatsApp, Google y SUNAT se configuran, se prueban y se activan en
Panel › Configuración › Integraciones (sólo MASTER), sin tocar el servidor ni reiniciar.
Sigue sin haber servidor, dominio, SMTP ni claves de Izipay. No hay nada publicado.

current_priority:
1. El propietario contrata el servidor y el dominio. En el servidor:
   `cp deploy/.env.production.example deploy/.env.production`, rellenar lo de arranque
   (dominio, `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_CONFIG_ENCRYPTION_KEY`, dirección de
   los pedidos) y `python3 deploy/preflight.py` hasta `SUFICIENTE PARA ARRANCAR`.
2. `docs/despliegue-produccion.md` §4 en orden. Con la cuenta MASTER, en
   Configuración › Integraciones: correo (probar con mensaje de prueba, activar) y la
   pasarela (elegir el producto de Izipay, claves de TEST, probar, activar).
3. Un pago completo en TEST con la tienda publicada. Eso —y sólo eso— cierra
   IZIPAY-PRODUCT e IZIPAY-TOKEN-CONTRACT.
4. Repetir `sh deploy/rehearsal.sh` sobre el commit que se vaya a publicar y recorrer
   `docs/despliegue-produccion.md` §11. Abrir el tráfico es orden del propietario.
5. Primera fase tras publicar: SERIAL-PICK.

validated:
- Pruebas de la fase: 170 de backend en `test_integrations_*`, más `test_ops_status` (23) y
  `test_preflight` (46); Jest 823/823; spec de Playwright `integrations-console` 3/3;
  recorrido `console` del ensayo en la pila de producción: TODO OK.
- Regresión completa: PENDIENTE al escribir esto: el equipo de trabajo estaba a batería. Se mide antes de abrir el PR
- Línea base (`master` `5da4e99`): backend 5375, Jest 795, Playwright 184, `ENSAYO: OK` (131 + 49).

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
