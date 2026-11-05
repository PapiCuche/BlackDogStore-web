# ADR — Disponibilidad multisucursal en la tienda en línea (ECOM-BRANCH-STOCK-01)

Estado:
- **D-A, la visibilidad: DISEÑO APROBADO** por el propietario el 2026-11-05 (§6). **Sin implementar**: el PR D-A queda en fase de diseño aprobado y requiere su propio encargo.
- **D-B, comprar y despachar desde cualquier sucursal: NO AUTORIZADO.** Está prohibido modificar checkout, pagos, reservas o transferencias para habilitarlo.

Fecha: 2026-11-05. Base: master `75a8f2d`, producción R4.1.

## 1. Requisito del propietario

Si un producto existe físicamente en cualquier sucursal activa de la empresa, la tienda no debe mostrarlo como «Agotado» para toda la empresa. A la vez, la web nunca puede cobrar prometiendo un despacho que el sistema no puede cumplir.

## 2. Lo que el código hace hoy

- **Una sola sucursal de despacho.**
  - `tenancy.company_fulfillment_branch` (tenancy.py:1660-1688) devuelve `Company.default_inventory_branch` si está activa, si no la única sucursal activa, y si no **ninguna**.
  - Con dos o más sucursales activas y sin sucursal de despacho configurada, toda la web marca 0 y el checkout rechaza la compra.
- **Stock vendible.**
  - Para productos por cantidad, es `BranchStock.quantity` de la sucursal de despacho (`available_stock`, tenancy.py:727-783). La cantidad exacta ya es pública en `ProductSerializer.inventory` y en `V1ProductSerializer.inventory`.
  - Para seminuevos, son las unidades `AVAILABLE` con precio mayor que cero en la sucursal de despacho (`device_catalog.offered_units`, device_catalog.py:628-643).
  - Un equipo apartado por un carrito pasa a `RESERVED` y ya no cuenta.
- **Todo el recorrido web lee la sucursal de despacho:** catálogo, búsqueda y filtro `in_stock`, página del modelo, carrito, apartado de equipos, cotización, checkout web y móvil, confirmación del pago y despacho (citas en el informe de auditoría).
- **El pedido tiene una sola sucursal.** `Order.fulfillment_branch` es una FK; `OrderItem` no tiene sucursal. Un pedido no puede tomar stock de dos sucursales.
- **Lo que no depende de la sucursal de despacho:**
  - la caja usa la sucursal elegida por el trabajador;
  - las transferencias van de origen a destino;
  - una anulación devuelve el stock a la sucursal de cada movimiento de venta;
  - una devolución entra en la sucursal que elige el trabajador.
- **Riesgo que ya existe hoy, independiente de este cambio.** Los productos por cantidad no tienen reserva: el stock se descuenta al confirmar el pago. Dos carritos pueden pagar la última unidad; el segundo pedido queda marcado con faltante y con el pago capturado. D-B lo resolvería; D-A no lo empeora.

## 3. Definiciones

| Término | Qué es |
|---|---|
| Existencia de la empresa | Suma de `BranchStock.quantity` en las sucursales activas de la empresa. Es interna: nunca se publica. |
| Existencia por sucursal | `BranchStock.quantity` de una sucursal. Es interna. |
| Disponible para compra inmediata | Lo que el checkout puede reservar o descontar hoy: la sucursal de despacho (§2). |
| Disponible en otra sucursal | Hay al menos una unidad en otra sucursal activa de la misma empresa. Para un seminuevo, además, la unidad está `AVAILABLE` y tiene precio válido. No se puede comprar en línea hoy. |
| Bajo pedido | Oferta vigente de un proveedor (DC3). Es otro concepto: **no** se usa para el stock de otra sucursal. |
| Publicado | Visible en la web. Publicado no significa disponible. |

## 4. D-A — visibilidad honesta (propuesta implementable, sin migraciones)

**Backend:**
- **Productos por cantidad:** una anotación `in_other_branch` en `company_storefront_products`. Es un `Exists(BranchStock)` de la misma empresa, en una sucursal activa, con `quantity > 0` y **excluyendo la sucursal de despacho**. Es una subconsulta correlacionada por página, sin consultas N+1.
- **Seminuevos:** el mismo `Exists` sobre `StockUnit` `AVAILABLE` con `sale_price > 0` en otra sucursal activa, con la misma regla que `offered_units`.
- **Serializadores públicos** (`ProductSerializer` y `V1ProductSerializer`): un campo nuevo `availability` con valores `in_stock`, `on_order`, `other_branch` o `sold_out`.
  - Se calcula en el servidor.
  - Vale `null` cuando la consulta no viene anotada, por ejemplo en el carrito.
  - **No cambian** `available`, `sold_out` ni `inventory`: un cliente antiguo sigue viendo «Agotado» y nunca activa la compra por error.
- **Página del modelo:** `availability: 'other_branch'` por variante, y `available_elsewhere` por modelo.
- **Nunca se publican** nombres, ids ni cantidades de otras sucursales, ni los equipos apartados.

**Frontend:**
- Insignia y texto «Disponible en otra sucursal» en la tarjeta, el detalle y la página del modelo.
- La compra queda desactivada. La acción alternativa es WhatsApp o «Visítanos», con el enlace ya configurado de la empresa.
- Archivos: `ProductCard`, `ProductDetail`, `catalog-models.availabilityLabel`, `ModelDetail` y `CatalogModels`.

**Por qué D-A no puede vender lo que no se cumple:**
- El carrito, el apartado, el checkout y la confirmación del pago siguen leyendo la sucursal de despacho.
- Si un frontend activara la compra por error, el carrito respondería 400 o 409.

**Pruebas obligatorias de D-A:**
- stock sólo en la sucursal de despacho, sólo en otra, o en ambas;
- otra sucursal desactivada;
- otra empresa;
- equipo apartado o reservado en otra sucursal;
- seminuevo sin precio en otra sucursal;
- el filtro `in_stock` sin cambios;
- el carrito sigue rechazando un producto con `other_branch`;
- número de consultas plano en el listado;
- API móvil.

## 5. D-B — comprar y despachar desde cualquier sucursal (sólo contrato)

Decisiones que el propietario debe tomar antes de escribir código:

1. **Elección de la sucursal.** El servidor elige y congela la sucursal del pedido en el checkout; nunca la elige el cliente.
   - **Opción a:** una sola sucursal que cubra toda la canasta. Si ninguna la cubre, el pedido no se divide.
   - **Opción b:** dividir en envíos. Necesita una sucursal por línea o por envío y una migración.
2. **Reserva atómica** para productos por cantidad: una cantidad reservada con candado y vencimiento, o un modelo nuevo. Sin ella, una venta en caja o una transferencia gana la carrera y el pago queda sin stock. Necesita migración.
3. **Logística.** O se despacha directo desde la otra sucursal, o primero se transfiere a la de despacho.
   - Hay que decidir quién crea la transferencia, el estado «en tránsito» y la fecha que se promete al cliente.
4. **Caja frente a web.** Qué parte del estante de cada sucursal puede reservar la web: un tope o un porcentaje, como los apartados de equipos.
5. **Pagos.**
   - Los reintentos y las confirmaciones tardías se aplican contra la sucursal o la reserva congelada.
   - Hay que decidir qué pasa si la reserva venció antes de confirmarse el pago.
6. **Anulaciones y devoluciones.**
   - Una anulación devuelve el stock a la sucursal de cada movimiento: esa regla ya existe.
   - Para una devolución después de la entrega, decidir si entra en la misma sucursal o en cualquiera.
7. **Restricciones.** Si un pedido puede abarcar varias sucursales, se necesita una restricción única de `StockMovement` por (pedido, producto, sucursal, salida de venta).
8. **Límites y operación por sucursal.** Topes de apartado, series y documentos de venta, y pantallas de despacho de cada sucursal.

Mientras D-B no esté aprobado, «Disponible en otra sucursal» **no** permite comprar en línea.

## 6. Decisiones del propietario sobre D-A (aprobadas el 2026-11-05)

Aprobadas tal como se propusieron:
- **D-A1.** «Disponible en otra sucursal» tiene prioridad sobre la oferta de proveedor («Bajo pedido») cuando corresponda.
- **D-A2.** Si una empresa con dos o más sucursales activas no tiene sucursal de despacho, se advierte al administrador en el panel.
- **D-A3.** «Con stock» cuenta sólo lo comprable en línea.
- **D-A4.** Se muestra «Disponible en otra sucursal» con la acción «Consúltanos por WhatsApp».

La propuesta original fue:

- **D-A1 · Prioridad cuando hay oferta de proveedor y también stock en otra sucursal.** Propuesta: «Disponible en otra sucursal» antes que «Bajo pedido», porque el producto ya existe físicamente.
- **D-A2 · Empresa con dos o más sucursales activas y sin sucursal de despacho.** Hoy toda la web marca 0. Con D-A todo mostraría «otra sucursal». Propuesta: además, un aviso en el panel de que falta configurar la sucursal de despacho.
- **D-A3 · El filtro «Con stock».** Propuesta: que siga contando sólo lo comprable en línea.
- **D-A4 · Texto y acción.** «Disponible en otra sucursal · consúltanos por WhatsApp» frente a «Disponible en tienda».

## 7. Migraciones

- D-A: ninguna.
- D-B: sí, para la reserva, la sucursal por línea o por envío y las restricciones. Se justifican antes de escribirlas.
