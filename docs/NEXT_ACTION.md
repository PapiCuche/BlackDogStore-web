# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió al cerrar MEDIA-EVIDENCE)

open_prs:
- #80 (TypeScript 6, DEP-TS6): CI verde. Versión mayor que el lanzamiento no necesita; se
  decide aparte. Antes de mergear: actualizarla sobre `master` y repetir su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
READY FOR EXTERNAL PRODUCTION CONFIGURATION, con una condición: repetir el ensayo de
producción. La aplicación incluye ya la V3, la impresión en tienda, las imágenes de
producto, la carga masiva con imágenes y las evidencias de servicio completas. No hay nada
publicado en Internet.

current_priority:
1. Repetir `sh deploy/rehearsal.sh` sobre `master`: el último es anterior a #81, #82 y a
   MEDIA-EVIDENCE (migraciones `0098`–`0101`, imágenes en el volumen de archivos).
2. Esperar los datos del propietario (blocked_external).
3. Con ellos: `docs/despliegue-produccion.md` §4, en orden.

validated:
- Backend: 4965 pruebas en PostgreSQL 16, 0 fallos, 4 omitidas.
- Frontend: Jest 659/659, typecheck, lint 0 errores / 22 avisos, build.
- Playwright completo: 179/179, 0 omitidas.
- Ensayo de producción: `ENSAYO: OK` (83 + 26) sobre `9043c89`. DESACTUALIZADO.

blocked_external:
- Dominio, DNS y certificado público.
- Servidor (VPS).
- Credenciales SMTP.
- Credenciales de Izipay: producción, y sandbox para `IzipaySandboxSmokeTest`
  (IZIPAY-TOKEN-CONTRACT).
- Destino de la copia externa (ahora incluye las imágenes de producto y las evidencias).
- Licencia de las imágenes de la propuesta (Apple/Figma): no se versionan; se suben desde el panel.
- Texto de marca de la V3 que nombra a Apple: lo decide el propietario y lo escribe en su panel.
- Configuración del repositorio: alertas de vulnerabilidad, escaneo de secretos y protección
  de push (sólo el propietario).

known_debt (todo P4 o propuesta; detalle en `docs/AUDIT_MEMORY.md`):
- IMPORT-BODY-LIMIT: Caddy no pone tope al cuerpo de una petición.
- EVIDENCE-CUSTOMER-WEB: el cliente no tiene pantalla web para sus evidencias compartidas.
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
