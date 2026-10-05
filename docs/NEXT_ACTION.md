# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió al cerrar PRODUCTION-READINESS-01)

open_prs:
- #80 (TypeScript 6, DEP-TS6) y #85 (@types/node 24): versiones mayores que el lanzamiento
  no necesita; se deciden aparte. Antes de mergear: actualizarlas sobre `master` y repetir
  su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
READY WITH CONDITIONS. El código está ensayado sobre el estado actual (`ENSAYO: OK`) y
endurecido para publicarse: tope de tamaño por ruta, registros sin tokens, copias que sólo
se guardan enteras, comprobación de estado y tareas programadas en un archivo. Publicar
depende de infraestructura y de datos del propietario. No hay nada publicado en Internet.

current_priority:
1. Esperar los datos del propietario (blocked_external).
2. Con ellos: `docs/despliegue-produccion.md` §10 (lista de publicación) y §4, en orden.
   Para Izipay, `docs/pagos-equipos-documentos.md` §1.3 y §1.4; para WhatsApp,
   `docs/seguimiento-whatsapp-equipos.md` §4.1.
3. Repetir `sh deploy/rehearsal.sh` sobre el commit que se vaya a publicar.
4. Primera fase tras publicar: SERIAL-PICK (elegir o leer el equipo al vender).

validated:
- Backend: 5332 pruebas, 0 fallos, 5 omitidas (suite completa local, PostgreSQL).
- Frontend: Jest 795/795, typecheck, lint 0 errores / 22 avisos, build.
- Playwright completo: 184/184, 0 omitidas.
- Ensayo de producción: `ENSAYO: OK` (107 + 49) sobre `81421d9`.
- Revisión independiente de la rama: 1 P1, 3 P2 y 7 P3; corregidos.

blocked_external:
- Dominio, DNS y certificado público (BLOCKED/INFRA).
- Servidor (VPS) (BLOCKED/INFRA).
- Vigilante externo y correo para los avisos de `healthcheck.sh` (BLOCKED/INFRA).
- Credenciales SMTP (BLOCKED/CREDENTIALS). Sin ellas el backend no arranca.
- Izipay: saber cuál de sus dos productos tiene el propietario («SDK web / Checkout» o
  «Mi Cuenta Web»), sus claves de TEST para la prueba real (`IzipaySandboxSmokeTest` /
  `MiCuentaWebSandboxSmokeTest`) y las de producción (IZIPAY-PRODUCT, IZIPAY-TOKEN-CONTRACT).
- Destino de la copia externa, y dónde guardar la copia de `deploy/.env.production`.
- Opcionales: WhatsApp Business (WHATSAPP-CREDENTIALS), ID de cliente OAuth de Google
  (GOOGLE-CLIENT-ID), certificado y credenciales SOL de SUNAT.
- Existencias, fotos y precios reales del catálogo.
- Licencia de las imágenes de la propuesta (Apple/Figma): no se versionan; se suben desde el panel.
- Texto de marca de la V3 que nombra a Apple: lo decide el propietario y lo escribe en su panel.
- Configuración del repositorio: alertas de vulnerabilidad, escaneo de secretos y protección
  de push (sólo el propietario).
- Confirmar que el código de comercio `4001061` de las pruebas no es el del propietario
  (es un dato público, no un secreto).

known_debt (detalle en `docs/AUDIT_MEMORY.md`):
- SERIAL-PICK (lo primero tras publicar): la caja vende el equipo más antiguo; hay que
  entregar el que nombra la nota. SERIAL-TRANSFER, SERIAL-COUNT.
- PAY-RECONCILE: si la notificación de la pasarela no llega, un pedido cobrado queda
  esperando; `healthcheck.sh` lo avisa, no lo resuelve.
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
Pedir al propietario los datos de `blocked_external`. Con dominio, servidor y SMTP:
`docs/despliegue-produccion.md` §4.
