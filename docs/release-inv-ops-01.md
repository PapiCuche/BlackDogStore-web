# INV-OPS-01 — IMPLEMENTADO — DESPLEGADO — ACEPTADO

**Actualización de producción — 30/10/2026:** el release posterior INV-RECEIVE-UX-02 + NAV-UX-01 + NAV-UX-02 sirve `d52e9b8794151c2abca1061ee7b3c483886e8440`; backend/frontend `release-d52e9b8`, IDs `37cb59d55205` / `6f67fc548f35`. Despliegue, respaldo externo, restauración aislada y humo HTTP/API PASS; esquema `0159`, sin migrar; SUNAT desactivado; rollback vigente `rollback-b21c7d7`. Humo visual autenticado PASS; aceptación funcional expresa del propietario de este release pendiente. Véase `../02_ESTADO_ACTUAL.md`. El cierre de INV-OPS-01 que sigue es histórico y conserva su aceptación 8/8; no atribuirla al nuevo release.

Producción sirve `b21c7d7efbe7e26b4678eb478def56419d4ae9d5`, desplegado el **29 de octubre de 2026, 23:12 (Lima)**. El propietario confirma **8/8 PASS** de aceptación funcional. El cierre documental parte de master `6747fa9`, con sólo documentación posterior al código desplegado; los siguientes commits documentales no implican otro despliegue.

## 0. Cierre del despliegue

Fuente técnica: informes existentes de Codex `deployment.json` y `verification.json`, conservados localmente fuera del repositorio público. No se adjuntan volcados, archivos privados, credenciales ni identificadores de clientes. Este cierre resume esas evidencias; no contiene nuevas verificaciones del servidor. Fuente de aceptación: confirmación expresa del propietario en este cierre (8/8), posterior al informe que todavía la dejaba pendiente.

| Comprobación registrada | Resultado |
|---|---|
| SHA desplegado | `b21c7d7efbe7e26b4678eb478def56419d4ae9d5` |
| Backend | `blackdog-backend:ops-b21c7d7`, `sha256:f01b8b68e239b396f82c2e42ce13cbc32caa308da56051ebeeafb65ec73f7d7b`, healthy |
| Frontend | `blackdog-frontend:ops-b21c7d7`, `sha256:2461a31ee95c7f187bed941f1fb4e735fd02bdc35fdf4b813cead0a3b3925dc6`, healthy |
| CI y build | SHA exacto 7/7 PASS; frontend idéntico a `f4240cd` con CI verde; builds PASS |
| Comprobaciones Django | `check` sin incidencias; `migrate --plan`: «No planned migration operations» |
| Esquema | `0159_label_batches`; ninguna migración ejecutada |
| Healthcheck y humo | PASS, exit 0; web, acceso, rutas nuevas y API pública 200; APIs protegidas 401 sin sesión; preview 200 con permiso y 403 sin `inventory.adjust`, con conexión de BD forzada a sólo lectura |
| Navegador | Tienda y acceso sin desborde a 390 y 1280 px; asistente protegido sin sesión; aceptación funcional del propietario 8/8 PASS |
| Integridad del negocio durante el humo Codex | Conteos de movimientos, obligaciones, lotes, auditoría y unidades sin cambio; ninguna recepción ficticia, venta ni comprobante registrados por el humo |
| Respaldo nuevo | 29/10/2026 21:36 (Lima), BD 147 KB y archivos 12 MB; gzip válido, SHA-256 idénticos tras descarga |
| Restauración de prueba | PostgreSQL 16.15 local, aislado, sin red ni puertos; 132 tablas / 2.044 filas coincidentes por cantidad y hash, 131 secuencias, 14 archivos completos, sin restricciones ni índices inválidos; contenedor eliminado |
| SUNAT BETA | Desactivado según preflight y comprobación final del despliegue; sin modificación |
| Infraestructura | PostgreSQL y Caddy sin recrear; Caddyfile MD5 `9f0aab332b6b2b29931afb6d72d9d6e7` conservado; sin cambios de firewall, certificados, credenciales ni permisos |
| Rollback | R3 `blackdog-{backend,frontend}:r3-a1a94a7` conservado, IDs `92f1275c4f9f` / `2da21e607288`; etiquetas `rollback-42eccc8` también presentes; no ejecutado |

La restauración se hizo **fuera del servidor de producción**, a diferencia de la base desechable interna prevista originalmente. La BD tiene una instantánea consistente; los archivos se copiaron con la aplicación activa, sin garantizar simultaneidad exacta con la BD. Se recrearon únicamente backend y frontend usando `up -d --no-build --no-deps`; no se migró ni se restauró la BD productiva. Durante el despliegue no se detectaron errores en los registros examinados.

### Deudas y siguientes entregas

La aceptación funcional no implementa ni elimina estas tareas. No se corrigen en este cierre:

- **P3 — navegación tras recarga:** reconciliar el historial del asistente con el borrador reiniciado. La recarga descarta el borrador en memoria; pueden quedar entradas anteriores de los pasos y recorridos «Atrás/Adelante» poco claros. Mantener la prohibición de persistir series, IMEI y costos en el navegador. Deuda indicada para este cierre; no reproducida de nuevo en producción.
- **P3 — foco:** tras «Corregir» y luego «Atrás», el foco puede volver al primer campo de la línea en vez del encabezado del paso 2; observación previa de E2E, no un fallo nuevo del servidor.
- **P3 — texto:** una línea con errores aún puede mostrar «Entran 0 equipos identificados»; mejorar el mensaje sin cambiar la validación.
- **P3 — selector de empresa:** las páginas de inventario bajo `AccessGuard` no entregan `onSelectCompany` a `AdminShell`; completar esa conexión sin perder el aviso de salida del borrador.
- **PENDIENTE — LABEL-B:** API y bandeja de lotes pendientes, recuperación de movimientos sin lote, lectura/impresión y estado de impresión real. «Etiquetas pendientes» sigue enlazando a la pantalla existente.
- **PENDIENTE — LABEL-C:** plantillas por empresa y logotipo.
- **PENDIENTE — INV-OPS-02:** simplificación del recuento.
- **BLOCKED — LABEL-D:** impresora y validación física, dependientes del hardware. Las demás deudas del proyecto permanecen en [AUDIT_MEMORY](AUDIT_MEMORY.md#10-deuda-técnica-activa).

**Historial del runbook:** las secciones 1–7 conservan el alcance y el procedimiento preparado antes del GO. Las condiciones «con GO», el R3 de partida y las tareas pendientes de aquella ventana no describen el estado vigente. Reutilizar comandos requiere autorización para una nueva operación de producción.

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
- Cobertura histórica de punta a punta: el recorrido «Atrás» se probó en navegador real automatizado (local). La pasada del propietario que entonces faltaba queda cerrada por su aceptación funcional 8/8; la deuda P3 tras recarga permanece registrada arriba.
