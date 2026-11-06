# DEC-INV-05 — 403 para permisos insuficientes

> Decisión del propietario: «Adoptar 403 para permisos insuficientes, conservando códigos de negocio
> estables. Auditar dependencias frontend, mobile, API y tests antes de modificar endpoints antiguos.
> Preparar PR independiente si requiere cambios de contrato.»

Estado: rama `feat/inv-403-permissions` (no desplegada). Sin migraciones.

## Qué cambia

Un rechazo por **permiso que falta** responde **403**; el `code` de negocio (`costs_forbidden`,
`prices_forbidden`) y el `detail` no cambian. Antes, las puertas nuevas de COST-WRITE-AUTHORITY (#242)
respondían 400 y las antiguas 403. La regla vive en `cost_services.PERMISSION_CODES` /
`cost_services.refusal_status(code, default)`.

**401 no cambia**: sin sesión, DRF responde 401 antes de llegar a la vista. Lo fija
`test_cost_write_authority.PermissionRefusalStatusTest.test_nobody_signed_in_still_hears_401_on_every_door`.

| Puerta (todas `/api/admin/…`, sólo las usa el panel web) | Rechazo | Antes | Ahora |
|---|---|---|---|
| `inventory/receipts/preview/` y `confirm/` | costo sin `costs.view` | 400 `costs_forbidden` | 403 |
| `inventory/receipts/preview/` y `confirm/` | precio de seminuevo sin `products.manage` | 400 `prices_forbidden` | 403 |
| `POST inventory/units/` («Registrar equipos») | costo sin `costs.view` | 400 `costs_forbidden` | 403 |
| `POST inventory/units/` | precio sin `products.manage` | 403 `prices_forbidden` | 403 (igual) |
| `inventory/units/import/<id>/apply/` | trabajo con costos aplicado sin `costs.view` | 400 `costs_forbidden` | 403 |
| `inventory/import/<id>/apply/` (stock) | ídem | 400 `costs_forbidden` | 403 |
| `products/import/<id>/apply/` | ídem | 400 `costs_forbidden` | 403 |
| `supplier-offers/import/<id>/apply/` | ídem | 400 `costs_forbidden` | 403 |
| `POST supplier-offers/`, `PATCH supplier-offers/<id>/` | costo pactado sin `costs.view` | 400 `costs_forbidden` | 403 |
| `orders/<id>/supply/<línea>/receive/` | costo real o de equipo sin `costs.view` | 400 `costs_forbidden` | 403 |
| `inventory/text-preview/offer-draft/` (y `file/`) | costo en el borrador sin `costs.view` | 400 `costs_forbidden` | 403 |
| `PATCH inventory/units/<id>/` costo / precio | sin `costs.view` / `products.manage` | 403 | 403 (igual) |
| `PATCH products/<id>/` `reference_cost` | sin `costs.view` | 403 | 403 (igual) |
| `products/register/` («Registrar artículo») | costo / stock sin permiso | 403 | 403 (igual) |
| `orders/<id>/supply/<línea>/real-cost/` | sin `costs.view` | 403 | 403 (igual) |

Lo que **sigue en 400** porque no es un permiso: un importe mal escrito de quien sí tiene el permiso,
una carga con filas en error (también la fila «Este archivo trae costos…», que es un error de fila en la
vista previa, no una respuesta HTTP), `restore_stock_not_allowed` (regla de negocio: sólo una venta POS se
anula sin reponer stock), «No puedes desactivar tu propio acceso» / «No puedes cambiar tu propio rol»
(protecciones sobre uno mismo).

## Dependencias auditadas

- **Frontend**: los clientes de estas puertas (`inventory-receipts.ts`, `stock-units.ts`,
  `internal-api.ts` `importApplyError` / `catalogCall` / `receiveOrderSupply`, `supplier-import-api.ts`,
  `inventory-text-draft.ts`) muestran el `detail` del servidor y leen `code`/`field`/`line`, sin ramificar
  por 400. `catalogCall` sólo lee campos sueltos en un 400 **sin** `detail`; estos rechazos siempre lo
  traen. `fetchWithAuth` sólo trata el 401 como sesión caducada; un 403 no cierra la sesión. El único
  `err.status === 403` cercano (`SuppliersManager` › liquidación) es de una lectura que no cambia.
- **API v1 / móvil**: ninguna de estas puertas está en `/api/v1/` (`v1_urls.py`). Sin cambio de contrato.
- **print_agent**: sólo usa `/api/v1/print-agent/…` y distingue 401; no le afecta.
- **Tests**: ver el PR; cada aserción fijada a 400 se cambió a 403 con el mismo `code`.

## Propuesta aparte (no incluida): contrato v1 y casos mixtos

| Endpoint | Hoy | Propuesta | Clientes conocidos |
|---|---|---|---|
| `POST /api/v1/internal/<empresa>/sales/pos/preview/` y `…/sales/` y sus gemelos `/api/admin/pos/…` | 400 «Sucursal no encontrada o sin acceso.» (`PosValidationError`) | 404 `not_found` como el resto de puertas con sucursal fuera de alcance (no 403: no se confirma que exista) | Panel web (caja); app móvil/terceros de v1: ninguno en este repositorio, pero v1 es el contrato público |
| ídem, vendedor atribuido | 400 «Vendedor no encontrado, sin permiso de venta, o sin acceso a esta sucursal.» | Mantener 400: mezcla validación (no existe) con permiso (no vende); separarlo exigiría decir cuál es y filtraría información | Panel web (caja) |

Ambas tocan `/api/v1/` (o comparten servicio con él): si el propietario las quiere, van en un PR propio con nota de
contrato.
