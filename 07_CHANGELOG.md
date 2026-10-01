# Changelog

Este archivo no existía en el baseline. La fuente histórica sigue siendo
[CHANGELOG.md](CHANGELOG.md).

## 2026-10-01 — SVC-FUNC-01 · Servicio técnico e integración con la caja

Rama `feature/service-pos-functional-integration`. Cada módulo del
servicio técnico tiene su ruta y su cola (`937cf82`). Nueva capacidad
`service.orders.assign`: asignar técnico ya no exige `service.orders.manage`, y el
técnico debe poder ver órdenes y alcanzar la sucursal (`1928b05`). Nueva capacidad
`service.payments.collect`: el técnico registra pagos y no puede reversarlos
(`d62fa30`). La caja recibe equipos para servicio y asigna al técnico en la misma
operación, sin crear venta, stock, comprobante ni comisión (`6caa88c`). Prueba de
navegador del flujo caja → técnico → cobro y reparación de prueba sembrada por
`seed_demo_users --e2e-fixtures` (`1d35b7d`). Dejar una orden sin técnico exige
`service.orders.manage`; con `service.orders.assign` sólo se asigna y se reasigna
(`4796db0`). Nombrar a un técnico exige en todos los caminos
`service.orders.manage`, o `service.orders.assign` con `service.orders.view`
(`9b59a31`). Migraciones `0094` y `0095` (sólo roles estándar sin modificar).
Backend 4643 pruebas, 0 fallos, en PostgreSQL; frontend 428 pruebas, typecheck,
lint 0/33 y build OK (50 páginas); Playwright 121 de 121, sin fallos, omitidas ni reintentos, 9,0 min.

## 2026-09-30 — F2 completada

Cierre de la fase «dónde» de la auditoría (`c191a84`): E2E-02 (`9c3445f`,
trabajador desechable para las pruebas de personal, `seed_demo_users
--e2e-fixtures`) y E2E-01 (`c191a84`, la prueba fiscal afirma «Preparar factura» y
«Enviar a SUNAT» como dos pasos). Backend 4569 pruebas, 0 fallos, en PostgreSQL;
frontend 402 pruebas, typecheck, lint 0/33 y build OK; Playwright 118/118. Sin
migraciones en toda la fase.

## 2026-09-30 — F2 · DRIFT-01 y DRIFT-07

Consola de servicio: el selector de técnicos lee `candidates` (`a4be03b`). Panel de
administración: las acciones se ofrecen sólo si la persona tiene la capacidad y el
alcance por sucursal que el servidor exige (`6958ec0`, `f6dc9ca`). Frontend 400
pruebas OK, typecheck, lint 0/33 y build OK. Backend sin cambios.

## 2026-09-30 — F2 · WRITE-SCOPE-01

Las modificaciones de nivel empresa (crear sucursal, sucursal de despacho, serie de
empresa, alcance de numeración, ajustes de empresa) exigen alcance sobre toda la
empresa además de `company.manage`; quien tiene sucursales seleccionadas sólo edita
las suyas (`fa85d41`, `c076120`). Backend 4563 pruebas, 0 fallos, en PostgreSQL.
Sin migraciones.

## 2026-09-30 — F2 · RBAC-01 y RBAC-02

Empresa y sucursales sólo se leen con capacidad de lectura (`5afdb81`); sin ella,
lista vacía y 404. La sucursal por defecto de una membresía responde un único 404
para ids inexistentes, ajenos o fuera de alcance (`d18e983`). Backend 4545 pruebas,
0 fallos, en PostgreSQL. Sin migraciones.

## 2026-09-30 — F2 · F-CAP-01

Crear un producto con stock inicial exige `inventory.adjust` además de
`products.manage`; sin ella responde 403 sin efectos. Crear sin stock no cambia.
Commit `c042fea`. Backend 4528 pruebas OK en PostgreSQL. Sin migraciones.

## 2026-09-30 — F2 · Delegación por sucursal

Nueva regla `can_delegate_branch_scope`: una persona limitada a sucursales
seleccionadas ya no puede ampliarse su propio acceso, pasar a nadie a «todas»,
conceder o retirar sucursales que no alcanza (membresías e invitaciones), crear o
modificar promociones fuera de su alcance, ni leer o reescribir por id la serie
interna de otra sucursal. La edición de membresías deja de guardar cambios a medias
cuando la lista de sucursales se rechaza. Commits `20d110c`, `cccb4d2`, `70286d1`.
Backend 4520 pruebas OK en PostgreSQL. Sin migraciones.

## 2026-09-30 — Auditoría F1 y memoria central

`4a9dd5c` corrige FE-AUTH-01 (HIGH): el proxy de Next ya no puede salir de `/api/`
con segmentos codificados (`%2f`, `%2e%2e`, `%5c`). Se añade `docs/AUDIT_MEMORY.md`,
índice verificado de la auditoría (baseline, invariantes, hallazgos abiertos y
cerrados, tests y consultas rápidas). Sin cambios de backend ni migraciones.

## 2026-09-29 — Cierre de ERP-FISCAL-6 y apertura de la auditoría integral

Commits de cierre en `erp/sales-fiscal-ui`: `97043d6`, `c9e64b9`, `fabfa38`, `9525b08`
(HEAD empujado). Rama `audit/full-system-2026-09` desde `9525b08` con `origin/master`
`2dca0a3` integrado (`65aa8c1`, merge limpio, sólo documentación). Baseline medido sobre
`65aa8c1`: backend 4489 pruebas OK (3 omitidas) en PostgreSQL; frontend 357 pruebas OK,
typecheck, lint 0/33 y build OK; Playwright 113/118 con un fallo preexistente (E2E-01) y
4 pruebas no ejecutadas por él. Hallazgo CI-01 confirmado: sin integración continua
propia. La remediación de hallazgos no comienza hasta fijar el baseline.

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
