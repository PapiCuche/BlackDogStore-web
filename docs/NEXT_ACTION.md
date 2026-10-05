# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió al cerrar SERVICE-TRACKING)

open_prs:
- #80 (TypeScript 6, DEP-TS6): CI verde. Versión mayor que el lanzamiento no necesita; se
  decide aparte. Antes de mergear: actualizarla sobre `master` y repetir su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
READY FOR EXTERNAL PRODUCTION CONFIGURATION, con una condición: repetir el ensayo de
producción. La aplicación incluye ya el seguimiento del cliente por enlace, las decisiones
de cotización con su ticket, los avisos por WhatsApp (con proveedor falso), los equipos con
número de serie, las categorías de portada configurables y «Continuar con Google». No hay
nada publicado en Internet.

current_priority:
1. Repetir `sh deploy/rehearsal.sh` sobre `master`: el último es anterior a #81, #82,
   MEDIA-EVIDENCE y SERVICE-TRACKING (migraciones `0098`–`0109`).
2. Esperar los datos del propietario (blocked_external).
3. Con ellos: `docs/despliegue-produccion.md` §4, en orden; para WhatsApp,
   `docs/seguimiento-whatsapp-equipos.md` §4.1 y la tarea de `docs/despliegue-produccion.md` §6.1.3.

validated:
- Backend: 5172 pruebas en PostgreSQL, 0 fallos, 4 omitidas (suite completa local).
- Frontend: Jest 765/765, typecheck, lint 0 errores / 22 avisos, build.
- Playwright completo: 183/183, 0 omitidas.
- Revisión independiente de seguridad de la rama: sin P1; 2 P2 y 5 P3, corregidos.
- Ensayo de producción: `ENSAYO: OK` (83 + 26) sobre `9043c89`. DESACTUALIZADO.

blocked_external:
- Dominio, DNS y certificado público.
- Servidor (VPS).
- Credenciales SMTP.
- Credenciales de Izipay: producción, y sandbox para `IzipaySandboxSmokeTest`
  (IZIPAY-TOKEN-CONTRACT).
- Destino de la copia externa (ahora incluye las imágenes de producto y las evidencias).
- WhatsApp Business: número, plantillas aprobadas por Meta, token de acceso, secreto de la
  aplicación y token de verificación del webhook (WHATSAPP-CREDENTIALS).
- ID de cliente OAuth de Google con el dominio como origen autorizado (GOOGLE-CLIENT-ID).
- Licencia de las imágenes de la propuesta (Apple/Figma): no se versionan; se suben desde el panel.
- Texto de marca de la V3 que nombra a Apple: lo decide el propietario y lo escribe en su panel.
- Configuración del repositorio: alertas de vulnerabilidad, escaneo de secretos y protección
  de push (sólo el propietario).

known_debt (todo P4 o propuesta; detalle en `docs/AUDIT_MEMORY.md`):
- IMPORT-BODY-LIMIT: Caddy no pone tope al cuerpo de una petición.
- SERIAL-TRANSFER, SERIAL-COUNT, SERIAL-PICK: transferir equipos con serie, recuento por
  series y elegir el equipo al vender.
- CUSTOMER-UNLINK-UI: deshacer una vinculación cuenta–cliente sólo existe en la API; no hay
  fusión de dos registros del mismo cliente.
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
`sh deploy/rehearsal.sh` sobre `master`. Si da `ENSAYO: OK`, actualizar este archivo y
`docs/despliegue-produccion.md` §9 con el commit ensayado.
