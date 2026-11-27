# GD-001 — Propiedad permanente de datos
**APROBADO por propietario (2026-11-24), reafirmado en review #424.** BlackDogStore Web es y seguirá siendo el único maestro de inventario. Good Doggy CRM es lector permanente; no existe fase posterior de escritura de inventario para esta integración.
| Dominio | Autoridad | Good Doggy |
|---|---|---|
| Stock por sucursal | BranchStock y servicios del maestro | lectura con scope |
| Equipos, Kardex, reservas | StockUnit, StockMovement, servicios del maestro | proyección mínima de disponibilidad; sin identificadores físicos |
| Catálogo, precios, promociones | Web y reglas comerciales existentes | lectura por scopes independientes |
| Pedidos, reparaciones | Web | deshabilitados hasta mecanismo de autorización de cliente aprobado |
| Relaciones CRM | CRM | sus propios datos, sin autoridad de mutación sobre Web |
Prohibido modificar inventario directa o indirectamente: entradas/salidas, ajustes, movimientos, reservas, liberaciones, StockUnit, BranchStock, StockMovement, checkout, POS, pedidos, cotizaciones, carrito, pagos o funciones que produzcan efectos comerciales. Product.inventory es derivado y nunca maestro. GET no garantiza pureza: prohibidos release_expired y get_or_create de inventario, incluso mediante middleware, serializer, señal o webhook. Solo procesos autónomos del maestro liberan reservas.
No fallback público/administrativo/móvil para inventario; no asumir identidad staff ni cookies. Scopes por operación, campo, filtro, tenant y sucursal, también para feed, caché, conteos, ETag, eventos y suscripciones. Filas de oferta consolidadas exigen catalog.read + prices.read + inventory.read para evitar inferencia; beneficio de promoción requiere promotions.read + prices.read. No se concede permiso por conocer un ID.
Los webhooks son avisos; acuse y reintento solo cambian estado técnico de entrega. No comandos entrantes. No sincronizar clientes ni PII sin acuerdo independiente. La propiedad del inventario no es pregunta abierta.
