# NEXT ACTION — 2026-10-26

## INV-OPS-01 — asistente de recepción unificado (backend en rama `feat/inv-ops-01-api`, pendiente de revisión, CI y PR)

API lista en rama (`receipt_services.py`, `receipt_views.py`, `store.test_inventory_receipts`), sin migraciones ni despliegue; el frontend se construye aparte contra el contrato de `02_ESTADO_ACTUAL.md`. **Siguiente:** revisión independiente del backend, PR (backend ⇒ ~70 min de CI), integrar con el frontend y sólo entonces proponer despliegue. Producción: R3 `a1a94a7`.

## R3 DESPLEGADO (`a1a94a7`, 2026-10-29) — PENDIENTE DE ACEPTACIÓN DEL PROPIETARIO

Producción sirve `a1a94a7d901f3dd9fc9ad191707d2246033fc90f` con migraciones hasta `0159` (registro en `02_ESTADO_ACTUAL.md`). **Siguiente:** el propietario valida la web (inicio de sesión, panel, recibir mercadería, etiquetas, Centro de Publicación, tienda) y decide: la integración `sunat` activa en la consola (BETA, 0 documentos), cargar productos y publicarlos con «Publicar listos», la serie obligatoria por equipo, el tope de la descripción y el cron de finanzas. **Vuelta atrás (solo si hay un fallo crítico):** de código, `docker tag blackdog-backend:rollback-42eccc8 blackdog-backend:latest` (y frontend) y `docker compose -f docker-compose.prod.yml --env-file deploy/.env.production up -d --no-build backend frontend`; antes `cancel_migration_counts.py`; `strip_r3_capabilities.sql` solo si se van a editar roles con R2. Nunca restaurar la copia sobre transacciones posteriores ni migrar el esquema hacia atrás sin autorización.

## R3 PREDEPLOY — candidato `a1a94a7`, esperando la decisión del propietario (NO desplegado)

Código candidato `a1a94a7d901f3dd9fc9ad191707d2246033fc90f`; migraciones `0156`–`0159`; procedimiento en `docs/release-r3-rehearsal.md` §7 (los scripts se pasan por la entrada estándar de `ssh`). **Preparado (2026-10-29, sin GO):** imágenes `blackdog-{backend,frontend}:r3-a1a94a7` construidas en el servidor, imágenes de R2 etiquetadas `rollback-42eccc8`, runbook exacto en `docs/release-r3-rehearsal.md` §7b; ninguna migración aplicada ni contenedor cambiado. **Siguiente:** el propietario responde `GO DESPLIEGUE R3 — SHA <sha exacto>` o pide cambios. Antes de la ventana: repetir `predeploy_checks.sql` (productos activos a precio 0 = 0) y confirmar la CI de master en el SHA. Decisiones abiertas: serie obligatoria por equipo, tope de la descripción, últimos 4 dígitos del IMEI público, línea de cron de `generate_finance_obligations` (no añadirla todavía).

## Estado operativo — R2 DEPLOYED / ACCEPTED

- **Producción:** `42eccc807b07170f81c3ab55a2fcf5c79851b823`, migraciones store hasta `0155_pos_sale_key_reservation`; R1 era `6834bc2`.
- **Aceptación del propietario:** confirmada el 2026-10-26 tras seis comprobaciones manuales (login web, caja sin venta, servicio técnico, inventario/etiquetas, contabilidad y tienda pública). La expiración real por tiempo y el login móvil con credenciales válidas **no** se probaron en producción.
- **Respaldo de R2:** `backups/db-20261026-080641.sql.gz` y `evidence-20261026-080641.tar.gz`; restauración en base desechable reportada correcta. Imágenes `rollback-6834bc2` identificadas. Fuente de evidencia y pasos: `docs/release-r2-rehearsal.md` (#187).
- **Rollback ante fallo crítico:** únicamente el procedimiento condicionado de vuelta atrás por código de `docs/release-r2-rehearsal.md` §7; verificar recuentos abiertos antes de cancelar y no retirar capacidades salvo que se vayan a editar roles con R1. Nunca sobrescribir transacciones posteriores con backups antiguos.
- **Operaciones no autorizadas por la aceptación:** `assign_unit_codes --apply`, cambios de inventario, depuración/borrado de productos o pedidos de prueba, habilitación de SUNAT/Izipay productivos y cualquier próximo despliegue.

## Estado de integración y alcance

- **`master` (desarrollo, NO producción):** integra #186 (correcciones cobros), #188 (FIN-B1; migraciones `0156`, `0157`) y #190 (FIN-E; `0158`). Estas tres migraciones financieras **no** existen en el esquema productivo R2.
- **#187 — documentación R2:** incorporar esta aceptación, verificar scripts y cerrar mediante PR con diff revisado; no desplegar nada.
- **#192 — FIN-E correcciones:** abierto en borrador; antes de fusionar, exigir revisión independiente, CI verde sobre el HEAD final, 0 commits detrás de master y ausencia de P0/P1/P2. Sin migraciones en su versión revisada al 2026-10-26.
- **FIN-F (mensajes manuales):** PARCIAL/en rama según checkpoint del agente; confirmar estado real, completar pruebas y revisión sin envíos externos. FIN-G mensajes automáticos: **BLOCKED**.
- **FIN-B2 (pantalla de pagos fijos):** PENDIENTE. **FIN-D (proveedores):** PENDIENTE. Orden sugerido: #192 → FIN-F → FIN-B2 → FIN-D; PR pequeños y merges secuenciales.
- **#185 — ADR finanzas:** borrador documental; corregir enunciados obsoletos para reflejar las reglas de pausas de FIN-B1 y lo ya implementado en `master`, sin confundirlo con lo desplegado.

## LABEL-AUTO-01 — etiquetas automáticas (en curso)

**LABEL-A listo en la rama `feat/label-auto-a` (migración `0159_label_batches`), pendiente de revisión independiente, CI y PR; sin fusionar ni desplegar.** Siguiente: LABEL-B (API de lectura/impresión y pestañas; capacidades `inventory.labels.print`/`.sensitive`). Revisión de #194 incorporada: `manual_entry` no etiqueta, los fallos se señalan con `label_error` y las lecturas de preparación van dentro del punto de guardado. Para LABEL-B: la lista derivada «pendientes sin lote» (entradas `purchase_entry`/`initial_stock` sin lote) es la vía de recuperación de un lote que falló o de una caída entre el commit del servicio y el gancho. Pregunta abierta: un lote por sucursal en cargas masivas de varias sucursales.

Prioridad del siguiente ciclo, en PR pequeños e independientes de finanzas: **LABEL-A** (backend de lotes y ganchos en las entradas físicas, migración propia, la siguiente libre tras `0158`) → **LABEL-B** (API y pestañas) → **LABEL-C** (plantillas y logotipo) → **LABEL-D** (impresora, BLOCKED: HARDWARE). La numeración de la migración se renumera al fusionar si FIN-F (`0159_finance_message_templates`) entra antes. Sin despliegue, sin migraciones en producción ni impresiones físicas sin GO específico.

## INVENTORY-FIRST-01 — INV-UX-A1 (rama `feat/inv-ux-a1`, pendiente de PR)

Selector de producto (nombre/SKU/escáner), «Recibir mercadería» y «Siguiente paso» listos en rama (PR #195), sin migraciones. Revisión independiente: GO con 2 P2 y P3 corregidos (código principal también en detalle/alta/edición, stock del producto elegido al día tras guardar, coincidencia exacta sin distinguir mayúsculas, desempate por id, lista estable al buscar). Siguiente: revisión y PR (toca `backend/**`: CI largo), luego INV-UX-A2 (mejoras del asistente de alta). Los ganchos de `label_batch` de LABEL-A deben comprobarse contra la forma real de su respuesta al integrar (`extractLabelBatch`).

## ECOM-READY-D1 — completar ficha y publicar (rama `feat/ecom-ready-d1`, pendiente de revisión, CI y PR)

Panel «Completar ficha», vista previa del cliente y «Publicar listos» (elección explícita por producto, lotes de 50) listos en rama con los P2/P3 de la revisión #197 corregidos; sin migración, sin PR abierto, sin desplegar. Siguiente (D2): fotos por unidad de seminuevos, edición por lotes y tema. Preguntas del propietario en el informe de la rama.

## Próxima entrega: release financiero independiente

Cuando los cambios financieros estén fusionados y listos, congelar SHA y alcance nuevo. Verificar hoja de migraciones real (mínimo `0156`–`0158`, `0159` solo si existe al preparar el release), seguridad de `finance.*`, aislamiento por empresa/sucursal, cron de generación, paridad de saldos de reparaciones, backfill en seco, concurrencia, rollback y compatibilidad con R2 sirviendo durante las migraciones.

**No ejecutar migraciones financieras en producción ni programar mensajes externos sin un nuevo GO explícito del propietario.**

## Política de continuidad

Trabajar en worktrees, nunca directamente en `master`. Usar `AGENTS.md` y `docs/AGENT_TOKEN_POLICY.md`. Actualizar `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md` y este archivo al cerrar cada fase. No crear checkpoints paralelos. Clasificar IMPLEMENTADO / PARCIAL / PENDIENTE / PROPUESTA / OBSOLETO / BLOCKED.

## INV-FAST-B — lista pegada → catálogo → equipos (rama `feat/inv-fast-b1`, 2026-10-28)

- **Estado:** B1 (catálogo asistido) y B2 (registro directo por el importador oficial) listos en la rama, sin migraciones. Pendiente: revisión independiente, CI verde sobre el HEAD y fusión; no se despliega sin GO.
- **Decisiones del propietario (2026-10-28, valores conservadores, se mantienen):** variante sin precio sugerido = `0.00`, oculta y no publicable (`no_price`); seminuevo de WhatsApp = «Usado»; la serie sigue obligatoria por equipo; eSIM / región / RAM siguen siendo avisos, no opciones de catálogo.
- **Integración:** master `a17042f` (#194 + #195) fusionado; «Revisar y registrar» usa el `NextStepBar` de INV-UX-A1 con los `label_batches` / `label_error` del aplicar oficial y preselecciona la última sucursal (`last-branch`).

## INV-QA-E — validación integral del flujo inventario primero (rama `test/inv-qa-e`, 2026-10-28)

- **Estado:** `frontend/e2e/inventory-first.spec.ts` verde (8/8, 5 corridas seguidas) contra un backend local con base propia; storefront, service-pos y serial-pick siguen verdes. Sin migraciones ni despliegue. Pendiente: CI y fusión tras #196 (`git merge origin/master` cuando entre).
- **Cómo correrla:** servidor `runserver` y `next dev -p 3002 --NEXT_PUBLIC_API_URL=…` como en `AGENTS.md` §6; `E2E_BACKEND_DIR=<backend> E2E_BASE_URL=http://localhost:3002 E2E_API_BASE=http://127.0.0.1:<puerto>/api npx playwright test e2e/inventory-first.spec.ts`. Requiere `dev_admin` sembrado (`seed_demo_users`) y DEBUG; el entorno del proceso debe llevar el mismo `DATABASE_URL` que el servidor (la siembra y los conteos usan `manage.py shell`).
- **Trampas:** el spec es serial (un escenario deja lo que el siguiente necesita: no correr uno suelto con `-g`); el límite de 5 inicios de sesión por minuto cuenta (1 por interfaz + 1 por API); la marca de la corrida debe ser sólo letras.
- **Revisión de #198:** 2 P2 y los P3 corregidos en la rama (guarda del ayudante, aislamiento no vacuo). El spec ya no corre contra una base cuyo nombre no contenga `qae`/`e2e`/`test` sin `E2E_DB_OK=<nombre exacto>`; sin la tarjeta de desarrollo falla (omitir a propósito: `E2E_ALLOW_SKIP=1`).
- **Ensayo R3 / entorno del E2E:** `runserver` con `DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store` (el escenario 4 crea una segunda empresa activa y, con dos, la tienda pública queda vacía en DEBUG por diseño); la base debe llevar la palabra `qae`, `e2e` o `test` como palabra entera en su nombre (`latest_dev` no vale) o aceptarse con `E2E_DB_OK=<nombre exacto>`; los `getByText` de página ignoran el `<title>`.
