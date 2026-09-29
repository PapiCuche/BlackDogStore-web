# Changelog

Este archivo no existía en el baseline. La fuente histórica sigue siendo
[CHANGELOG.md](CHANGELOG.md).

## 2026-09-29 — ERP-FISCAL-6 · Descuentos declarados

Promociones: atribución determinista del descuento a cada componente (proporcional
al valor regular, ROUND_DOWN, mayor residuo, desempate por `product_id`), congelada
en `AppliedPromotion.metadata`; el snapshot congelado es la autoridad y el legacy se
reconstruye en memoria (ADR-42). Fiscal: factura y boleta declaran cupón y manual
como `AllowanceCharge` global `02` y promociones por línea con `00`, con importes
netos derivados del snapshot; `PayableAmount` es lo cobrado y no hay doble descuento
(ADR-43, reglas oficiales de validación 21.04.2025). POS: `receipt_options` explica
por qué una opción no está disponible, el selector no permite elegirla, y la venta
completada muestra el comprobante real; `seed_demo_users --fiscal-beta` prepara las
series DEMO en BETA. Corregido: la venta preparaba el comprobante antes de congelar
la promoción; `resolve_series` decía «factura» cuando faltaba una boleta.

Validación: backend 4489 pruebas, OK (3 omitidas), 1917,4 s; frontend 357 pruebas, OK; typecheck, lint (0
errores, 33 advertencias) y build PASAN; Playwright `pos-ticket` PASA,
`pos-receipt-options` PASA. Migraciones nuevas: 0.

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
