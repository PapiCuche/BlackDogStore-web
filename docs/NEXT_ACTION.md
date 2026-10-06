# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió al cerrar EXTERNAL-PRODUCTION-CONFIG-01)

open_prs:
- #80 (TypeScript 6, DEP-TS6) y #85 (@types/node 24): versiones mayores que el lanzamiento
  no necesita; se deciden aparte. Antes de mergear: actualizarlas sobre `master` y repetir
  su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
NOT READY, por datos externos y no por el código. El código está verde y ensayado
(`ENSAYO: OK` con correo por SMTP y restauración en servidor nuevo). No se recibió ningún
dato del propietario: no hay servidor, dominio, SMTP, claves de Izipay ni destino de copia.
No hay nada publicado en Internet y no se tocó ningún DNS.

current_priority:
0. Con el equipo despierto: repetir `sh deploy/rehearsal.sh` y Playwright (not_validated).
1. El propietario entrega los datos de `blocked_external` como dice
   `docs/despliegue-produccion.md` §3: los secretos, directos a `deploy/.env.production`
   del servidor; nunca por chat. `python3 deploy/preflight.py` dice qué falta.
2. Con servidor y `deploy/.env.production`: `docs/despliegue-produccion.md` §4 en orden,
   `preflight.py --smtp-send-to <dirección>`, y la prueba de Izipay en TEST
   (`docs/pagos-equipos-documentos.md` §1.4).
3. Repetir `sh deploy/rehearsal.sh` sobre el commit que se vaya a publicar.
4. Recorrer `docs/despliegue-produccion.md` §11. Abrir el tráfico es orden del propietario.
5. Primera fase tras publicar: SERIAL-PICK.

validated (sobre `072509f`):
- Backend en CI (PostgreSQL 16): 5375 pruebas, 0 fallos, 5 omitidas.
- Frontend: Jest 795/795, typecheck, lint 0 errores / 22 avisos, build (sin cambios en la rama).
- Línea base de `master` `7829685`: ensayo `ENSAYO: OK` (107 + 49).

not_validated (el equipo de trabajo estuvo en reposo: tapa cerrada, a batería):
- Ensayo final sobre la rama: la última pasada da 128 + 49 bien y `ENSAYO: 3 FALLO(S)` por
  una sola causa (`npm ci` cortado al construir la imagen del frontend, sin red). Repetir:
  `sh deploy/rehearsal.sh`, con el equipo abierto y enchufado.
- Playwright completo: cuatro intentos, 158–172 de 184, fallos distintos cada vez y todos
  por tiempo agotado. Repetir con el equipo despierto.
- Suite backend completa en local.

blocked_external` como dice
   `docs/despliegue-produccion.md` §3: los secretos, directos a `deploy/.env.production`
   del servidor; nunca por chat. `python3 deploy/preflight.py` dice qué falta.
2. Con servidor y `deploy/.env.production`: `docs/despliegue-produccion.md` §4 en orden,
   `preflight.py --smtp-send-to <dirección>`, y la prueba de Izipay en TEST
   (`docs/pagos-equipos-documentos.md` §1.4).
3. Repetir `sh deploy/rehearsal.sh` sobre el commit que se vaya a publicar.
4. Recorrer `docs/despliegue-produccion.md` §11. Abrir el tráfico es orden del propietario.
5. Primera fase tras publicar: SERIAL-PICK.

validated:
- Backend: __BACKEND__ (suite completa local, PostgreSQL).
- Frontend: Jest 795/795, typecheck, lint 0 errores / 22 avisos, build.
- Playwright completo: __PW__.
- Ensayo de producción: `ENSAYO: OK` (__CHECKS__ + __STEPS__) sobre `__HEAD__`.

blocked_external (BLOCKED/OWNER-DATA: sin esto no se abre):
- Izipay: cuál de sus dos productos tiene contratado («SDK web / Checkout» o «Mi Cuenta
  Web») y sus claves de TEST; después, las de producción (IZIPAY-PRODUCT,
  IZIPAY-TOKEN-CONTRACT, las dos abiertas).
- SMTP: host, puerto, usuario, contraseña, remitente y tipo de cifrado.
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
rellenarlo allí y ejecutar `python3 deploy/preflight.py`.
