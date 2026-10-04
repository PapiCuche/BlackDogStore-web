# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: ver `git log -1 origin/master` (este archivo se escribió con la infraestructura en #59)

open_prs:
- #64 (`actions/setup-python` 7): CI verde; el merge exige permiso `workflow`, lo hace el propietario.
- #67 (`reportlab` 5, DEP-REPORTLAB5): comparar a la vista un ticket y un A4 antes de mergear.
- Las demás de Dependabot que estén esperando su CI. Menores y parches: mergear con CI verde.
  Mayores: leer el cambio; si rompen la CI, `@dependabot ignore this major version`.

current_phase:
READY FOR EXTERNAL PRODUCTION CONFIGURATION. La aplicación y la infraestructura están
cerradas y ensayadas; no hay nada publicado en Internet.

current_priority:
1. Esperar los datos del propietario (blocked_external).
2. Con ellos: `docs/despliegue-produccion.md` §4, en orden.
3. Mientras tanto, sólo mantenimiento: Dependabot y deuda P4.

validated:
- Backend: 4744 pruebas en PostgreSQL 16, 0 fallos (CI).
- Frontend: Jest 590/590, typecheck, lint 0 errores / 23 avisos, build.
- Ensayo de producción: `sh deploy/rehearsal.sh` → `ENSAYO: OK` (83 comprobaciones + 26 pasos
  de navegador) sobre `9043c89`.
- Playwright completo: 171/171, 0 omitidas.

blocked_external:
- Dominio, DNS y certificado público.
- Servidor (VPS).
- Credenciales SMTP.
- Credenciales de producción de Izipay (queda en sandbox).
- Host de las fotos de producto y destino de la copia externa.
- Licencia de las imágenes de la propuesta de Figma (no se versionan).
- Configuración del repositorio: alertas de vulnerabilidad, escaneo de secretos y protección
  de push (sólo el propietario).

known_debt (todo P4 o propuesta; detalle en `docs/AUDIT_MEMORY.md`):
- AUDIT-01…06: ediciones de borrador sin fila de auditoría.
- CSP-01: política de scripts (nonce + captura del SDK de Izipay).
- LINT-EFFECT-01: 23 avisos `set-state-in-effect`.
- INTERNAL-UI-KIT: componentes del panel definidos en tres sitios.
- DEP-TS7, DEP-ESLINT10: versiones mayores que rompen la CI. DEP-REPORTLAB5: mayor de la librería de PDF.
- LOGIN-CSRF-01 (baja); THROTTLE-CACHE-01 (controlado: un proceso de gunicorn).
- Los huecos `home_featured` y `home_promo` existen en el modelo y la portada no los pinta.

next_exact_action:
Con dominio y servidor: `docs/despliegue-produccion.md` §4.1. Antes de publicar, repetir
`sh deploy/rehearsal.sh` sobre el commit que se va a desplegar.
