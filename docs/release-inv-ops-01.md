# Despliegue de INV-OPS-01 (código `b21c7d7`) — plan y runbook, SIN GO

Estado: **plan preparado, nada ejecutado.** Producción sirve R3 `a1a94a7` (esquema `0159`). El candidato `b21c7d7` añade, sobre R3, #203 (API de recepción), #204 (centro de operaciones y asistente) y #206 (la repetición idempotente revalida el acceso a la sucursal). Master actual: `a95f40f` (solo documentación encima del código).

## 1. Qué cambia
- **Sin migraciones.** `git diff --name-only a1a94a7 b21c7d7` no toca migraciones, `deploy/`, `docker-compose*`, `Dockerfile*`, dependencias, `Caddyfile` ni variables de entorno.
- Backend: `receipt_services.py`, `receipt_views.py` y dos rutas nuevas (`/api/admin/inventory/receipts/preview|confirm/`); `inventory_services.apply_manual_stock_movement` (parámetro opcional `unit_cost`); `stock_unit_services.receive_units` (bloqueo asesor por identificador antes de comprobar duplicados, también para «Registrar equipos», la importación masiva y la recepción de pedidos bajo pedido) y `find_conflict`.
- Frontend: centro de operaciones y asistente de recepción, entradas nuevas del menú de Inventario, `NextStepBar`, `ArticleRegistrationWizard` (retorno opcional) y `InternalControlGuard` (aviso de salida al cambiar de empresa). La tienda pública, la caja, el servicio técnico y las finanzas no cambian.
- **Permisos:** las rutas nuevas exigen `inventory.adjust`; la pantalla también `products.view`. Costos solo con `costs.view`. Ningún rol gana ni pierde capacidades.

## 2. Preflight (solo lectura; repetir justo antes de la ventana)
1. Servido `a1a94a7`, contenedores sanos, `showmigrations store` termina en `0159`, `sh deploy/healthcheck.sh` en OK, disco y memoria suficientes, `Caddyfile` md5 `9f0aab33…`.
2. Imágenes de recuperación presentes: `blackdog-{backend,frontend}:r3-a1a94a7` (IDs `92f1275c4f9f` y `2da21e607288`; el ID del frontend puede variar según el almacén de imágenes: comprobar por etiqueta y fecha).
3. Código: CI de `master` verde sobre `b21c7d7` y ningún cambio de código posterior.
4. Estado del negocio sin cambios de riesgo: `predeploy_checks.sql` de `docs/release-r3-rehearsal/` (productos activos a precio 0 = 0, recuentos, claves POS) y recepciones en curso: ninguna operación de caja abierta.
5. `FISCAL_ENABLED=0`; la integración `sunat` de la consola se **observa, no se toca** (el 2026-10-29 estaba activa en BETA, sin documentos emitidos).

## 3. Respaldo (con GO)
`sh deploy/backup.sh`, `gzip -t` de la base y de la evidencia, y restauración de prueba en una base desechable dentro del contenedor de PostgreSQL (se borra al terminar); no ejecutar nada más mientras corre el respaldo.

## 4. Lanzamiento (con GO, sin migrar)
`C="docker compose -f docker-compose.prod.yml --env-file deploy/.env.production"`
1. `git fetch origin b21c7d7efbe7e26b4678eb478def56419d4ae9d5`; exportar el código a una carpeta aparte con `git archive` (por ejemplo `~/ops-build`); construir `blackdog-backend:ops-b21c7d7` y `blackdog-frontend:ops-b21c7d7` con `docker build -f backend/Dockerfile.prod …` y `docker build -f frontend/Dockerfile.prod --build-arg NEXT_PUBLIC_API_URL=http://backend:8000/api --build-arg NEXT_PUBLIC_IMAGE_HOSTS= …` mientras R3 sigue sirviendo; no tocar `:latest`.
2. Comprobación de solo lectura con la imagen nueva: `$C -f ~/ops-override.yml run --rm --no-deps -T backend python manage.py check` y `migrate --plan` debe decir «No planned migration operations».
3. `docker tag blackdog-backend:ops-b21c7d7 blackdog-backend:latest` (y el frontend), `git checkout b21c7d7…` (el `Caddyfile` modificado a mano se conserva) y `$C up -d --no-build backend frontend`.
4. Healthchecks, `manage.py check`, registros sin errores en backend, frontend, proxy y PostgreSQL.

## 5. Humo (sin ventas, cobros ni documentos fiscales reales)
- Externo: tienda pública, `/auth`, `/admin` (`no-store`), APIs protegidas 401 sin sesión, 390 y 1280 px sin desborde.
- Con la cuenta del propietario: inicio de sesión; `/admin/inventory/operations` (cinco tarjetas según permisos); `/admin/inventory/receive`: recorrer los cuatro pasos y **detenerse en «Revisar y confirmar»** (la vista previa no escribe nada); no pulsar «Confirmar» salvo que el propietario decida registrar una recepción real; «Atrás» en los pasos 2 y 3 y la salida con y sin líneas; los demás módulos de Inventario siguen abiertos.
- Servidor (solo lectura, `force_authenticate`, sin crear sesiones): `receipts/preview` con 200 para el administrador y 403 para un rol sin `inventory.adjust`; sin datos privados en las respuestas.
- Verificar que no se escribió nada: conteos de movimientos, obligaciones y lotes sin cambio.

## 6. Cancelación y vuelta atrás
Detener y volver si falla un healthcheck, hay 5xx repetidos en el panel o la caja, o la recepción deja el inventario en duda. **Vuelta atrás de código:** `docker tag blackdog-backend:r3-a1a94a7 blackdog-backend:latest` (y el frontend), `git checkout a1a94a7…` y `$C up -d --no-build backend frontend`. No hay migraciones que deshacer. Las recepciones ya confirmadas con la API nueva quedan como movimientos, unidades y lotes normales (el código R3 los ignora o los lee como cualquier entrada de compra): no se borra nada.

## 7. Riesgos
- Si el ID de imagen difiere del anotado, verificar el contenido (por ejemplo, textos «Centro de operaciones» o «Recibir mercadería» en el `.next` del frontend nuevo).
- Las recepciones por el asistente usan bloqueos asesores por identificador: dos recepciones simultáneas del mismo identificador hacen cola; un interbloqueo responde 409 `busy` con la clave libre.
- Cobertura de punta a punta: el recorrido «Atrás» se probó en navegador real automatizado (local); queda la pasada del propietario en su navegador y móvil.
