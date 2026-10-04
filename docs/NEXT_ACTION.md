# NEXT ACTION

Se sobrescribe al cerrar cada fase. No es un changelog.

master: e40e440 (merge de #60, Storefront V4)

open_prs:
- #59 deploy/production-vps — borrador, en conflicto con master; se actualiza al final

current_phase:
Cierre de la aplicación antes del ensayo final de producción.

current_priority:
1. Mergear ramas locales listas: `ci/dependabot` (CI-03) y `feat/storefront-image-cleanup`
   (STOREFRONT-IMAGE-CLEANUP, 23 tests verdes en local, falta CI).
2. Corregir lo que dejó la revisión de #60 (ver known_debt).
3. Cabecera y pie V4, conservando todas las funciones actuales.
4. Avisos de ESLint (25) y entradas duplicadas del menú del panel.
5. Master definitivo → actualizar #59 → ensayo Docker nuevo → copia/restauración → merge de #59.

validated:
- master e40e440: árbol idéntico al HEAD de #60 (c7cd81e), que pasó CI de frontend y de backend
  (4719 tests), Jest 564/564, typecheck, lint 0 errores/25 avisos y Playwright 170/170.

blocked_external:
- Dominio, DNS y certificado público.
- Servidor (VPS).
- Credenciales SMTP.
- Credenciales de producción de Izipay (queda en sandbox).
- Host de las fotos de producto y destino de la copia externa.
- Licencia de las imágenes de la propuesta de Figma (no se versionan).

known_debt:
- P2 Hero oscuro: la sombra en línea pisa el halo de `.v3-cutout-on-slab` y no se ve sobre la losa.
- P3 Regla de sombra definida dos veces (`.v3-cutout` en CSS y `storefrontMediaStyle`).
- P3 Imagen de servicio y de ubicación llevan un fondo detrás del recorte.
- P3 PNG con transparencia por color clave (tRNS) se aplana a RGB.
- P3 La ruta pública sirve imágenes de empresas desactivadas.
- P4 Límite de subida compartido con escritura de productos; nombre accesible del campo de archivo.
- Cabecera y pie con el diseño anterior.
- LOGIN-CSRF-01 (baja), THROTTLE-CACHE-01 (controlado: un proceso), AUDIT-01…06 (propuesta).

next_exact_action:
Abrir PR de `ci/dependabot`; terminar y abrir PR de `feat/storefront-image-cleanup`; después
rama `fix/storefront-image-depth` para los hallazgos P2/P3 de la sombra.
