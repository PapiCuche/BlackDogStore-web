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

**EL TIPO DESCRIBE EL PRESENTE, NO LA HISTORIA.** Una fila que era B y ya se
integró en Mobile es A, y hay que cambiarla el día del merge. Dejarla en B
porque «nació» B convierte la tabla en un diario en vez de un inventario, y la
siguiente ola planifica contra una foto vieja. Un test lo verifica: ver
`Ip1ParityManifestTest`.

**LAS REFERENCIAS SON SÍMBOLOS, NO LÍNEAS.** Este documento citaba
`archivo.py:123`. Cinco de esas citas apuntaban a una línea en blanco, al
docstring de otra clase, o más allá del final del archivo — no porque nadie
mirara, sino porque un número de línea envejece con cada edición que ocurre por
encima de él. Un símbolo sobrevive al refactor y además se puede verificar
automáticamente, y eso es lo que hace el guard.

---

## VENTAS

| Función | Backend | Web | V1 | Mobile | Capability | TIPO |
|---|---|---|---|---|---|---|
| POS — contexto | `pos_views.AdminPosContextView` | `/admin/sales/pos` | `sales/pos/context/` | ✅ (IP1A) | `sales.pos.use` | **A** |
| POS — buscar producto | `pos_views.AdminPosSearchView` | ✅ | `sales/pos/products/search/` | ✅ (IP1A) | `sales.pos.use` | **A** |
| POS — leer código | `pos_views.AdminPosLookupView` | ✅ | `sales/pos/products/lookup/` | ✅ (IP1A) | `sales.pos.use` | **A** |
| POS — previsualizar | `pos_services.build_pos_sale` | ✅ | `sales/pos/preview/` | PARCIAL — el endpoint está integrado; la pantalla no lo llama antes de cobrar | `sales.pos.use` | **B** |
| **POS — registrar venta** | `pos_services.create_pos_sale` | ✅ | `sales/pos/sales/` | ✅ (IP1A) | `sales.pos.use` | **A** |
| POS — asignar vendedor | `pos_services.resolve_pos_seller` | ✅ | ✅ campo `seller` en preview y venta | PENDIENTE — el transporte lo soporta; no hay control en pantalla | `sales.pos.assign_seller` | **B** |
| POS — descuento manual | `pos_services.resolve_discount` | ✅ | ✅ campos `manual_discount_type` · `manual_discount_value` · `discount_reason` | PENDIENTE — ídem | `sales.discounts.apply` | **B** |
| POS — cupón | `pos_services.resolve_discount` | ✅ | ✅ campo `coupon_code` | PENDIENTE — ídem | — (ninguna, por diseño) | **B** |
| POS — promoción automática | `promotion_services.evaluate` | ✅ | ✅ la calcula el servidor y la devuelve en `promotions[]` | PENDIENTE — no se muestran | — (automática) | **B** |
| POS — comisión | `pos_services.calculate_commission` | ✅ | ✅ campo `commission`, nulo sin capability | PENDIENTE — no se muestra | `sales.commissions.view` para verla | **B** |
| POS — combos sugeridos | `promotion_services.combo_availability` | `promotion_views.AdminPosCombosView` | PENDIENTE | PENDIENTE | `sales.pos.use` | **C** |
| Pedidos — listar / abrir | `v1_internal_views.V1InternalOrderListView` | ✅ | ✅ | ✅ | `sales.orders.view` | **A** |
| Pedidos — fulfillment | `order_fulfillment_services.change_fulfillment_status` | ✅ | ✅ | ✅ | `sales.orders.manage` | **A** |
| Notas de venta | `sales_note_services.get_or_create_sales_note` | ✅ | PENDIENTE | PENDIENTE | `sales.notes.manage` | **C** |
| Comisiones — informe | `sales_analytics_views.AdminCommissionsView` | ✅ | PENDIENTE | PENDIENTE | `sales.commissions.view` | **C** |
| Comisiones — tarifas | `pos_views.AdminCommissionSettingsView` | ✅ | PENDIENTE | PENDIENTE | `sales.commissions.manage` | **C** |
| Analytics de ventas | `sales_analytics_views.AdminSalesDashboardView` | ✅ | PENDIENTE | PENDIENTE | `sales.analytics.view` | **C** |
| Promociones — CRUD | `promotion_views.AdminPromotionListView` | ✅ (update solo archiva) | PENDIENTE | PENDIENTE | `sales.promotions.*` | **C** |
| Cupones | `promotion_views.AdminCouponView` | ✅ | PENDIENTE | PENDIENTE | `sales.promotions.*` | **C** |
| Códigos de barras — gestionar | `pos_views.AdminProductBarcodeView` | **ninguna página lo llama** | PENDIENTE | PENDIENTE | `products.manage` | **D** |
| Anulación / devolución POS | **no existe** | — | — | — | — | **E** |
| Arqueo / sesión de caja | **no existe** | — | — | — | — | **E** |

## INVENTARIO

| Función | Backend | Web | V1 | Mobile | Capability | TIPO |
|---|---|---|---|---|---|---|
| Resumen | `inventory_services.get_inventory_summary` | ✅ | ✅ | ✅ | `inventory.view` | **A** |
| Stock por sucursal | `inventory_services.branch_stock_queryset` | ✅ | ✅ | ✅ | `inventory.view` | **A** |
| Kardex / movimientos | `inventory_services.get_stock_card` | ✅ | ✅ | ✅ | `inventory.view` | **A** |
| Entrada / salida manual | `inventory_services.apply_manual_stock_movement` | ✅ | ✅ `inventory/adjustments/` | ✅ | `inventory.adjust` | **A** |
| Transferencia — crear | `inventory_services.create_stock_transfer` | `/admin/inventory/transfers` | `inventory/transfers/` | ✅ (IP1B) | `inventory.adjust` | **A** |
| Transferencia — líneas | `inventory_services.set_transfer_item` | ✅ | `inventory/transfers/<id>/items/` | ✅ (IP1B) | `inventory.adjust` | **A** |
| Transferencia — despachar | `inventory_services.dispatch_transfer` | ✅ | `inventory/transfers/<id>/dispatch/` | ✅ (IP1B) | `inventory.adjust` | **A** |
| Transferencia — recibir | `inventory_services.receive_transfer` | ✅ | `inventory/transfers/<id>/receive/` | ✅ (IP1B) | `inventory.adjust` | **A** |
| Transferencia — cancelar | `inventory_services.cancel_transfer` | ✅ | `inventory/transfers/<id>/cancel/` | ✅ (IP1B) | `inventory.adjust` | **A** |
| Recuento — crear | `inventory_services.create_inventory_count` | `/admin/inventory/counts` | PENDIENTE | PENDIENTE | `inventory.adjust` | **C** |
| Recuento — contar | `inventory_services.set_count_item` | ✅ | PENDIENTE | PENDIENTE | `inventory.adjust` | **C** |
| Recuento — aprobar | `inventory_services.approve_inventory_count` | ✅ | PENDIENTE | PENDIENTE | `inventory.adjust` | **C** |
| Recuento — cancelar | `inventory_services.cancel_inventory_count` | ✅ | PENDIENTE | PENDIENTE | `inventory.adjust` | **C** |
| Reposición | `inventory_services.get_replenishment_rows` | ✅ | PENDIENTE | PENDIENTE | `inventory.reports` | **C** |
| Reportes (8 funciones) | `inventory_services.get_low_stock_rows` y siete más | ✅ | PENDIENTE | PENDIENTE | `inventory.reports` | **C** |
| Importación Excel | `stock_import_services.apply_stock` | ✅ | PENDIENTE | PENDIENTE | `inventory.adjust` | **C** |
| Exportación | `inventory_views.AdminProductStockCardView` y export | ✅ | PENDIENTE | PENDIENTE | `inventory.reports` | **C** |
| Serial / IMEI | **no existe** | — | — | — | — | **E** |

## PRODUCTOS · CLIENTES

| Función | Backend | Web | V1 | Mobile | Capability | TIPO |
|---|---|---|---|---|---|---|
| Catálogo — búsqueda para inventario | **no existe** — ver la nota de abajo | — | — | — | — | **E** |
| Producto — CRUD | `admin_views.AdminProductListView` | `/admin/products` | PENDIENTE | PENDIENTE | `products.manage` | **C** |
| Producto — importación | `import_services.apply_products` | ✅ | PENDIENTE | PENDIENTE | `products.manage` | **C** |
| Categorías | `admin_views.AdminCategoryListView` | ✅ | PENDIENTE | PENDIENTE | `products.manage` | **C** |
| Clientes — CRUD interno | `customer_services.resolve_customer` | `/admin/customers` | PENDIENTE | PENDIENTE | `service.customers.*` | **C** |

### La fila que decía dos cosas a la vez

Hasta H4 había aquí una fila «Producto — búsqueda interna» que citaba el MISMO
símbolo backend y la MISMA ruta v1 que «POS — buscar producto», y decía Mobile
*pendiente* mientras la otra decía ✅. Era la misma función descrita dos veces
con dos respuestas distintas, y la contradicción llevaba una ola entera ahí.

Se auditó: **es la misma función.** `sales/pos/products/search/` está detrás de
`sales.pos.use`, la capability de la caja.

Lo que se borró al deduplicar era, sin embargo, una pregunta legítima, así que
ahora está dicha en voz alta como lo que es: **no existe una búsqueda de
catálogo para quien no vende.** Un miembro con `inventory.view` y sin
`sales.pos.use` no tiene ninguna ruta v1 para buscar un producto por nombre.
Recorre el stock de su sucursal —`inventory/stock/?search=`— y eso alcanza para
inventariar, porque un artículo sin stock en la sucursal no se cuenta ni se
transfiere; pero no es una búsqueda de catálogo, y llamarla así sería maquillar.
El día que haga falta una de verdad, se construye; hoy es **E**, no C.

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


---

## H4 — LO QUE ESTABA MAL

Auditoría de este documento contra el código, antes de escribir una línea de
IP2. Cuatro clases de defecto, todas encontradas leyendo, ninguna cosmética.

### 1. Nueve filas decían B con Mobile ya integrado

IP1 cerró cuatro operaciones de POS y las cinco de transferencias, y las nueve
se quedaron marcadas **B** — «falta Mobile» — con un ✅ en la columna Mobile de
la misma fila. La tabla se contradecía a sí misma línea por línea.

No es una errata. La siguiente ola planifica contra esta tabla: una B rancia le
pide a alguien que construya lo que ya existe, y quien la lea de buena fe
gastará el día averiguando por qué ya funciona. Ahora son **A**, y un test lo
comprueba en cada ejecución.

### 2. Cinco citas apuntaban a ninguna parte

Las referencias tenían forma `archivo.py:123`. Verificadas una por una contra el
código:

| Cita | Qué hay realmente en esa línea | Dónde está de verdad |
|---|---|---|
| `pos_views.py:210` | línea en blanco | `AdminPosLookupView` (168) |
| `pos_views.py:253` | docstring de `AdminPosSaleView` | `AdminPosSearchView` (211) |
| `pos_views.py:465` | línea en blanco | `AdminProductBarcodeView` (358) |
| `pos_views.py:711` | **más allá del final del archivo** | `AdminCommissionSettingsView` (583) |
| `pos_views.py:135` | dentro de un método, no la clase | `AdminPosContextView` (116) |

Nadie mintió: un número de línea envejece con cada edición que ocurre por encima
de él, y `pos_views.py` creció. Por eso las citas ahora son **símbolos**, que
sobreviven al refactor y —lo que importa más— se pueden verificar solos.

### 3. Una fila describía la misma función dos veces, con dos respuestas

«Producto — búsqueda interna» y «POS — buscar producto» citaban el mismo símbolo
y la misma ruta v1, y decían cosas opuestas sobre Mobile. Ver la nota en
PRODUCTOS · CLIENTES: es la misma función, y lo que quedaba sin decir —que no
hay búsqueda de catálogo para quien no vende— ahora está dicho como **E**.

### 4. La capability de los recuentos decía «ídem»

Las cuatro filas de recuento heredaban la capability de la fila de arriba con un
«ídem». Auditado en `inventory_views`: leer un recuento exige `inventory.view` y
todo lo demás `inventory.adjust`. Ahora está escrito.

### El guard

`Ip1ParityManifestTest` pasó de 5 tests a 8. Los tres nuevos comprueban que cada
símbolo citado existe, que no vuelven los números de línea, y que la columna TIPO
describe el presente. Cada uno se probó plantando el defecto que existe para
atrapar y viéndolo fallar.
