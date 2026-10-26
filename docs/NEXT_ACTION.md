# NEXT ACTION — 2026-10-26

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

## Próxima entrega: release financiero independiente

Cuando los cambios financieros estén fusionados y listos, congelar SHA y alcance nuevo. Verificar hoja de migraciones real (mínimo `0156`–`0158`, `0159` solo si existe al preparar el release), seguridad de `finance.*`, aislamiento por empresa/sucursal, cron de generación, paridad de saldos de reparaciones, backfill en seco, concurrencia, rollback y compatibilidad con R2 sirviendo durante las migraciones.

**No ejecutar migraciones financieras en producción ni programar mensajes externos sin un nuevo GO explícito del propietario.**

## Política de continuidad

Trabajar en worktrees, nunca directamente en `master`. Usar `AGENTS.md` y `docs/AGENT_TOKEN_POLICY.md`. Actualizar `02_ESTADO_ACTUAL.md`, `07_CHANGELOG.md` y este archivo al cerrar cada fase. No crear checkpoints paralelos. Clasificar IMPLEMENTADO / PARCIAL / PENDIENTE / PROPUESTA / OBSOLETO / BLOCKED.
