# Changelog

Este archivo no existía en el baseline. La fuente histórica sigue siendo
[CHANGELOG.md](CHANGELOG.md).

## 2026-09-28 — Estabilización funcional

POS: selección de documento autorizada y transaccional, idempotencia y numeración
existente. Servicio: recepción web, cola por capacidad, entrega limitada para
Ventas y timeline corregido. Avisos/portal: respeto de estados ocultos y estado
real de correo. Fiscal UI: boleta BETA, firma previa al envío y permisos de acción.
El envío de boletas corresponde al Resumen Diario, cuya gestión web sigue
pendiente. Las notas de crédito/débito ya no reemplazan al comprobante original
en la consulta histórica de una venta. Migraciones locales aplicadas con respaldo.

Validación: backend 4412 pruebas, OK (3 omitidas); frontend 350 pruebas, OK.
Typecheck y build de producción PASAN: los dos badges de inventario se extrajeron
al módulo compartido de componentes, que es de donde esas páginas ya importaban
sus primitivas. Playwright: 17 de 17 en POS y acceso interno.

Detalle y validación:
[Informe de entrega](docs/estabilizacion-funcional-2026-09-28.md).
