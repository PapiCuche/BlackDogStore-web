# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió con la V3 en #82, `23d2603`)

open_prs:
- #80 (TypeScript 6, DEP-TS6): CI verde. Versión mayor que el lanzamiento no necesita; se
  decide aparte. Antes de mergear: actualizarla sobre `master` y repetir su CI.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
READY FOR EXTERNAL PRODUCTION CONFIGURATION. La aplicación, la V3 y la infraestructura
están cerradas; no hay nada publicado en Internet.

current_priority:
1. Esperar los datos del propietario (blocked_external).
2. Con ellos: `docs/despliegue-produccion.md` §4, en orden.
3. Mientras tanto, sólo mantenimiento: Dependabot y deuda P4.

validated:
- Backend: 4865 pruebas en PostgreSQL 16, 0 fallos, 4 omitidas (CI de `1b62f31`).
- Frontend: Jest 609/609, typecheck, lint 0 errores / 23 avisos, build.
- Playwright completo: 176/176, 0 omitidas.
- Ensayo de producción: `sh deploy/rehearsal.sh` → `ENSAYO: OK` (83 comprobaciones + 26 pasos
  de navegador) sobre `9043c89`. Es anterior a #81 y #82: hay que repetirlo.

blocked_external:
- Dominio, DNS y certificado público.
- Servidor (VPS).
- Credenciales SMTP.
- Credenciales de Izipay: producción, y sandbox para `IzipaySandboxSmokeTest`
  (IZIPAY-TOKEN-CONTRACT).
- Host de las fotos de producto y destino de la copia externa.
- Licencia de las imágenes de la propuesta (Apple/Figma): no se versionan; se suben desde el panel.
- Texto de marca de la V3 que nombra a Apple: lo decide el propietario y lo escribe en su panel.
- Configuración del repositorio: alertas de vulnerabilidad, escaneo de secretos y protección
  de push (sólo el propietario).

known_debt (todo P4 o propuesta; detalle en `docs/AUDIT_MEMORY.md`):
- PRODUCT-PHOTO-UPLOAD: la foto de producto no se sube desde el panel (sólo dirección absoluta).
- DEV-PROXY-UPLOAD: el proxy de `next dev` rechaza subidas de más de ~1 MB.
- HERO-VARIANT-LEGACY: `hero_variant` sigue en el modelo y la API; la V3 no lo lee.
- AUDIT-01…06: ediciones de borrador sin fila de auditoría.
- CSP-01: política de scripts (nonce + captura del SDK de Izipay).
- LINT-EFFECT-01: 23 avisos `set-state-in-effect`.
- INTERNAL-UI-KIT: componentes del panel definidos en tres sitios.
- DEP-TS6, DEP-TS7, DEP-ESLINT10: versiones mayores.
- FISCAL-PRINT-EXO, PRINT-USB: ver `docs/AUDIT_MEMORY.md`.
- LOGIN-CSRF-01 (baja); THROTTLE-CACHE-01 (controlado: un proceso de gunicorn).
- Los huecos `home_featured` y `home_promo` existen en el modelo y la portada no los pinta.

next_exact_action:
Repetir `sh deploy/rehearsal.sh` sobre `master` (incluye #81 y #82). Con dominio y servidor:
`docs/despliegue-produccion.md` §4.1.
