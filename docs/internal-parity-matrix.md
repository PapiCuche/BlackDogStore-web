# Matriz de paridad funcional interna

**Contrato base:** `origin/master` — la columna «SHA» de cada operación dice en
qué commit apareció su ruta v1.

> **ESTE DOCUMENTO NO CONCEDE PERMISOS.** Es un inventario, no autoridad. Toda
> capability se resuelve en el servidor, en cada petición, contra
> `resolve_capabilities()`. Una fila que diga `sales.pos.use` describe lo que el
> backend exigirá; no hace que nadie lo tenga.

## Para qué existe

Backend manda. Web y Mobile representan.

Una operación solo puede integrarse en un cliente si **ya existe** en el dominio
del backend y es alcanzable. Esta tabla es el registro de qué existe dónde, y es
lo que decide si una función se construye o se clasifica PENDIENTE.

## Cómo se lee

**TIPO**

| | significado | qué hacer |
|---|---|---|
| **A** | Backend + Web + V1 + Mobile | nada: paridad cerrada |
| **B** | Backend + Web + V1, falta Mobile | integrar en Mobile |
| **C** | Backend + Web, **sin V1** | crear adapter V1 → merge → smoke → Mobile |
| **D** | Backend existe, **ninguna pantalla Web lo alcanza** | evaluar paridad Web **antes** que Mobile |
| **E** | El dominio **no existe** | PENDIENTE. Mobile prohibido |

**Estado:** `IMPLEMENTADO` · `PARCIAL` · `PENDIENTE` · `PROPUESTA` · `OBSOLETO`.

---

## VENTAS

| Función | Backend | Web | V1 | Mobile | Capability | TIPO |
|---|---|---|---|---|---|---|
| POS — contexto | `pos_views.py:135` | `/admin/sales/pos` | **`sales/pos/context/`** | ✅ (IP1A) | `sales.pos.use` | **B** |
| POS — buscar producto | `pos_views.py:253` | ✅ | **`sales/pos/products/search/`** | ✅ (IP1A) | `sales.pos.use` | **B** |
| POS — leer código | `pos_views.py:210` | ✅ | **`sales/pos/products/lookup/`** | ✅ (IP1A) | `sales.pos.use` | **B** |
| POS — previsualizar | `pos_services.build_pos_sale:658` | ✅ | **`sales/pos/preview/`** | PARCIAL (IP1A) | `sales.pos.use` | **B** |
| **POS — registrar venta** | **`pos_services.create_pos_sale:768`** | ✅ | **`sales/pos/sales/`** | ✅ (IP1A) | `sales.pos.use` | **B** |
| POS — asignar vendedor | `pos_services.resolve_pos_seller:554` | ✅ | ✅ (dentro de la venta) | pendiente | `sales.pos.assign_seller` | **B** |
| POS — descuento manual | `pos_services.resolve_discount:292` | ✅ | ✅ (dentro de la venta) | pendiente | `sales.discounts.apply` | **B** |
| POS — cupón | `pos_services.resolve_discount:329` | ✅ | ✅ (dentro de la venta) | pendiente | — (ninguna, por diseño) | **B** |
| POS — promoción automática | `promotion_services.py:119` | ✅ | ✅ (la calcula el servidor) | pendiente | — (automática) | **B** |
| POS — comisión | `pos_services.calculate_commission:419` | ✅ | ✅ (la devuelve la venta) | pendiente | `sales.commissions.view` para verla | **B** |
| POS — combos sugeridos | `promotion_services.py:182` | ✅ | PENDIENTE | PENDIENTE | `sales.pos.use` | **C** |
| Pedidos — listar / abrir | `v1_internal_views.py:143` | ✅ | ✅ | ✅ | `sales.orders.view` | **A** |
| Pedidos — fulfillment | `order_fulfillment_services.py:68` | ✅ | ✅ | ✅ | `sales.orders.manage` | **A** |
| Notas de venta | `sales_note_services.py:55` | ✅ | PENDIENTE | PENDIENTE | `sales.notes.manage` | **C** |
| Comisiones — informe | `sales_analytics_views.py:512` | ✅ | PENDIENTE | PENDIENTE | `sales.commissions.view` | **C** |
| Comisiones — tarifas | `pos_views.py:711` | ✅ | PENDIENTE | PENDIENTE | `sales.commissions.manage` | **C** |
| Analytics de ventas | `sales_analytics_views.py:148` | ✅ | PENDIENTE | PENDIENTE | `sales.analytics.view` | **C** |
| Promociones — CRUD | `promotion_views.py:91` | ✅ (update solo archiva) | PENDIENTE | PENDIENTE | `sales.promotions.*` | **C** |
| Cupones | `promotion_views.py:417` | ✅ | PENDIENTE | PENDIENTE | `sales.promotions.*` | **C** |
| Códigos de barras — gestionar | `pos_views.py:465` | **ninguna página lo llama** | PENDIENTE | PENDIENTE | `products.manage` | **D** |
| Anulación / devolución POS | **no existe** | — | — | — | — | **E** |
| Arqueo / sesión de caja | **no existe** | — | — | — | — | **E** |

## INVENTARIO

| Función | Backend | Web | V1 | Mobile | Capability | TIPO |
|---|---|---|---|---|---|---|
| Resumen | `inventory_services.py:1262` | ✅ | ✅ | ✅ | `inventory.view` | **A** |
| Stock por sucursal | `inventory_services.py:1108` | ✅ | ✅ | ✅ | `inventory.view` | **A** |
| Kardex / movimientos | `inventory_services.py:1318` | ✅ | ✅ | ✅ | `inventory.view` | **A** |
| Entrada / salida manual | `inventory_services.apply_manual_stock_movement:312` | ✅ | ✅ `inventory/adjustments/` | ✅ | `inventory.adjust` | **A** |
| Transferencia — crear | `inventory_services.create_stock_transfer:612` | `/admin/inventory/transfers` | **`inventory/transfers/`** | ✅ (IP1B) | `inventory.adjust` | **B** |
| Transferencia — líneas | `inventory_services.set_transfer_item:638` | ✅ | **`inventory/transfers/<id>/items/`** | ✅ (IP1B) | `inventory.adjust` | **B** |
| Transferencia — despachar | `inventory_services.dispatch_transfer:673` | ✅ | **`inventory/transfers/<id>/dispatch/`** | ✅ (IP1B) | `inventory.adjust` | **B** |
| Transferencia — recibir | `inventory_services.receive_transfer:761` | ✅ | **`inventory/transfers/<id>/receive/`** | ✅ (IP1B) | `inventory.adjust` | **B** |
| Transferencia — cancelar | `inventory_services.cancel_transfer:833` | ✅ | **`inventory/transfers/<id>/cancel/`** | ✅ (IP1B) | `inventory.adjust` | **B** |
| Recuento — crear | `inventory_services.create_inventory_count:884` | `/admin/inventory/counts` | PENDIENTE | PENDIENTE | ídem | **C** |
| Recuento — contar | `inventory_services.set_count_item:901` | ✅ | PENDIENTE | PENDIENTE | ídem | **C** |
| Recuento — aprobar | `inventory_services.approve_inventory_count:947` | ✅ | PENDIENTE | PENDIENTE | ídem | **C** |
| Recuento — cancelar | `inventory_services.cancel_inventory_count:1050` | ✅ | PENDIENTE | PENDIENTE | ídem | **C** |
| Reposición | `inventory_services.py:1153` | ✅ | PENDIENTE | PENDIENTE | `inventory.reports` | **C** |
| Reportes (8 funciones) | `inventory_services.py:1135-1424` | ✅ | PENDIENTE | PENDIENTE | `inventory.reports` | **C** |
| Importación Excel | `stock_import_services.py` | ✅ | PENDIENTE | PENDIENTE | `inventory.adjust` | **C** |
| Exportación | `inventory_views.py` | ✅ | PENDIENTE | PENDIENTE | `inventory.reports` | **C** |
| Serial / IMEI | **no existe** | — | — | — | — | **E** |

## PRODUCTOS · CLIENTES

| Función | Backend | Web | V1 | Mobile | Capability | TIPO |
|---|---|---|---|---|---|---|
| Producto — búsqueda interna | `pos_views.py:253` | ✅ | **`sales/pos/products/search/`** | pendiente | `sales.pos.use` | **B** |
| Producto — CRUD | `admin_views.AdminProduct*` | `/admin/products` | PENDIENTE | PENDIENTE | `products.manage` | **C** |
| Producto — importación | `import_services.py` | ✅ | PENDIENTE | PENDIENTE | `products.manage` | **C** |
| Categorías | `admin_views` | ✅ | PENDIENTE | PENDIENTE | `products.manage` | **C** |
| Clientes — CRUD interno | `customer_services.py` | `/admin/customers` | PENDIENTE | PENDIENTE | `service.customers.*` | **C** |

## SERVICIO TÉCNICO (M8–M12B — regresión)

Toda la cadena está en **TIPO A**: recepción, diagnóstico, cotización,
aprobación del cliente, ejecución, repuestos, control de calidad, entrega y
cobro. 34 rutas v1, 32 consumidas por Mobile.

---

## Matriz de capabilities — **medida**, no leída

Ejecutada contra `resolve_capabilities()` sobre una empresa aprovisionada.

| rol | pos.use | assign_seller | discounts | ord.view | ord.manage | notes | comm.view | analytics | promo.* | inv.view | inv.adjust | inv.reports | prod.view | prod.manage |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Administrador** | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí |
| **Ventas** | **sí** | **no** | **no** | sí | sí | sí | no | no | no | **no** | no | no | sí | no |
| **Inventario** | **no** | no | no | no | no | no | no | no | no | sí | sí | sí | sí | no |
| Servicio Técnico | no | no | no | no | no | no | no | no | no | no | no | no | no | no |
| Supervisor Técnico | no | no | no | no | no | no | no | no | no | no | no | no | no | no |
| **Platform master** (tenant explícito) | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí | sí |
| Rol custom «solo caja» | **sí** | no | no | no | no | no | no | no | no | no | no | no | no | no |
| **Admin de OTRA empresa** | **no** | no | no | no | no | no | no | no | no | no | no | no | no | no |
| Cliente | no | no | no | no | no | no | no | no | no | no | no | no | no | no |

Tres cosas que esto fija y que no se pueden asumir:

- **`Ventas` ≠ todo Sales.** Tiene la caja; no tiene asignación de vendedor, ni
  descuento manual, ni comisiones, ni analytics, ni promociones, ni inventario.
- **`Inventario` no tiene POS.** Trabajar en la tienda no es cobrar en ella.
- **El admin de otra empresa resuelve a cero.** El aislamiento es del resolver,
  no de una comprobación en una vista.

### Presets que los documentos nombran y `company_provisioning` NO crea

`Recepción` y `Control de Calidad` aparecen en la documentación histórica y **no
existen**. Se clasifican **PROPUESTA**. Crearlos exige decidir qué autoridad
recibe cada uno, y esa es una decisión de producto que no se toma de pasada.

---

## Manifiesto de contrato — mutaciones internas de Mobile

Toda mutación interna de Mobile debe tener una fila aquí, con la ruta y la
capability que el backend exige, y el SHA en el que esa ruta se mergeó.

| operación | endpoint v1 | capability | SHA que la introdujo |
|---|---|---|---|
| `sales.orders.fulfillment` | `internal/<slug>/orders/<id>/fulfillment/` | `sales.orders.manage` | histórico |
| `inventory.adjust` | `internal/<slug>/inventory/adjustments/` | `inventory.adjust` | histórico |
| `service.orders.create` | `internal/<slug>/service/orders/` | `service.orders.create` | M8 |
| `service.orders.transition` | `internal/<slug>/service/orders/<id>/transition/` | `service.orders.manage` | M8 |
| `service.orders.assignment` | `internal/<slug>/service/orders/<id>/assignment/` | `service.orders.manage` | M8 |
| `service.devices.create` | `internal/<slug>/service/devices/` | `service.devices.manage` | M8 |
| `service.diagnostic.*` | `.../diagnostics/`, `.../diagnostics/<id>/` | `service.diagnostic.manage` | M9 |
| `service.quote.*` | `.../quotes/…` (5 rutas) | `service.diagnostic.manage` | M9 |
| `service.quote.decision` | `customer/<slug>/repairs/<id>/quotes/<id>/decision/` | — (dueño) | M9 |
| `service.execution.*` | `.../execution/…` (5 rutas) | `service.repair.manage` | M10 |
| `service.parts.*` | `.../parts/`, `.../parts/<id>/reverse/` | `service.repair.manage` | M10 |
| `service.quality.*` | `.../quality/…` (4 rutas) | `service.quality.manage` | M11 |
| `service.delivery.create` | `.../delivery/` | `service.delivery.manage` | M12 |
| `service.payments.create` | `.../payments/` | `service.payments.manage` | M12B |
| `service.payments.reverse` | `.../payments/<id>/reverse/` | `service.payments.manage` | M12B |
| `sales.pos.sale` | `internal/<slug>/sales/pos/sales/` | `sales.pos.use` | **IP1A** |
| `sales.pos.preview` | `internal/<slug>/sales/pos/preview/` | `sales.pos.use` | **IP1A** |
| `inventory.transfer.create` | `internal/<slug>/inventory/transfers/` | `inventory.adjust` | **IP1B** |
| `inventory.transfer.items` | `internal/<slug>/inventory/transfers/<id>/items/` | `inventory.adjust` | **IP1B** |
| `inventory.transfer.dispatch` | `internal/<slug>/inventory/transfers/<id>/dispatch/` | `inventory.adjust` | **IP1B** |
| `inventory.transfer.receive` | `internal/<slug>/inventory/transfers/<id>/receive/` | `inventory.adjust` | **IP1B** |
| `inventory.transfer.cancel` | `internal/<slug>/inventory/transfers/<id>/cancel/` | `inventory.adjust` | **IP1B** |

---

## Reglas que esta matriz hace cumplir

1. Ninguna mutación de Mobile sin una ruta v1 **mergeada** en `origin/master`.
2. Ninguna capability que no exista en `capabilities.py`.
3. Ninguna operación TIPO **D** o **E** se construye en Mobile.
4. `INTEGRADO` solo cuando lo están **todas** las superficies que la fila nombra;
   si falta una, se dice cuál.


## Transferencias — las dos preguntas de sucursal

**Ver** una transferencia necesita acceso a **cualquiera** de los dos extremos:
quien dirige la tienda de destino tiene que ver lo que le llega aunque el origen
sea una tienda en la que nunca entra.

**Operarla** necesita **ambos**. Despachar saca unidades de un estante y recibir
las pone en otro; quien alcanza solo uno de los dos no está en posición de
afirmar que ocurrió todo.

Las dos reglas son las de la superficie Web —`visible_branches` y
`assert_branch_access`— reutilizadas, no reescritas.

**El stock se mueve en dos transiciones, no en cuatro.** Entre despachar y
recibir las unidades no están en ningún estante: una tienda que envió algo está
corta antes de que la otra esté larga, y fingir que el movimiento es instantáneo
dejaría uno de los dos recuentos mal mientras la furgoneta está en la carretera.

**Despachar y recibir son idempotentes** en el dominio: repetir la llamada
devuelve el mismo estado y no vuelve a mover nada. Mejor que un error — un
doble toque en un teléfono, o un reintento tras una respuesta perdida, no es un
fallo del operador y no debe parecerlo.


---

## CIERRE DE IP1

Las cuatro entregas que la tabla decidió, más una quinta que la tabla no podía
prever porque solo aparece al construir.

| # | PR | Repo | Merge |
|---|---|---|---|
| 1 | #21 · v1 POS interno | web | `d484e3e` |
| 2 | #20 · Mobile POS | mobile | `670b666` |
| 3 | #22 · v1 transferencias | web | `b38ec26` |
| 4 | #23 · `product_slug` en las líneas | web | `8a1e581` |
| 5 | #21 · Mobile transferencias | mobile | `7d43b17` |

### El hueco que solo se ve construyendo

El PR #22 expuso `PUT .../transfers/<id>/items/` pidiendo un **pk** de producto,
copiando la consola Web. Ningún cliente nativo puede conseguir uno honestamente:

| Ruta | Cómo nombra un artículo |
|---|---|
| `GET .../inventory/stock/` | `product_slug` — **ningún id** |
| `POST .../inventory/adjustments/` | `product_slug` |
| `PUT .../transfers/<id>/items/` (antes) | `product` (pk) |

La ruta quedaba inalcanzable **desde la propia lista con la que se usa**, y
alcanzable solo por un cliente que hubiera pasado por `/api/admin/` — que es
justo lo que un cliente nativo no debe hacer. El PR #23 acepta `product_slug`
sin quitar el pk.

**La lección no es el campo.** Una ruta puede pasar sus tests, su smoke y su
revisión y aun así no ser utilizable por el cliente para el que se escribió,
porque los tests la llaman con datos que el cliente no tiene. La prueba de que
una superficie está completa no es que responda, es que **se pueda recorrer
entera con lo que ella misma devuelve**.

### Lo que NO se construyó, y por qué

| Función | Motivo |
|---|---|
| Recuentos de inventario | Existe en Backend y Web, **sin ruta v1** — TIPO C, va a IP2 |
| Recepción parcial de una transferencia | **No existe el dominio** — TIPO E |
| Reversar una transferencia despachada | **No existe** — anular es solo para borradores |
| Anulación / devolución de venta | **No existe en el backend** — prohibido en Mobile |
| Arqueo / sesión de caja | **No existe** — TIPO E |
| Presets `Recepción` y `Control de Calidad` | Solo PROPUESTA, por decisión explícita |

Ninguna se simuló. Una operación que no existe no recibe un mock: recibe la
palabra PENDIENTE.
