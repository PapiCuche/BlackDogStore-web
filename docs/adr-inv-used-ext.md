# ADR — Seminuevos externos identificados (INV-USED-EXT-01)

| | |
|---|---|
| **Estado** | Plan aceptado para implementar lo no bloqueado. Fuera de R1. |
| **Fecha** | 2026-10-23 |
| **Base** | `master` `b593148` (migraciones hasta `0145`) |
| **Desarrolla** | [adr-inv-snapshot-external-used.md](adr-inv-snapshot-external-used.md), Decisión 2, con D3 y D4 del propietario |

## 1. Lo que el código hace hoy (verificado)

- **Un seminuevo bajo pedido está prohibido** en tres puntos:
  - `supply_services.set_sale_mode`: «Un seminuevo se vende por equipo, nunca bajo pedido»;
  - `supply_services._offer_values`: no hay oferta de un seminuevo;
  - `supply_services.sells_on_order` / `best_offer`, por medio de `sells_on_order`.

  `supplier_offer_import.preview` rechaza además la fila de un seminuevo.
- **Una oferta por proveedor y variante a la vez:** lo imponen `unique_supplier_offer_window` y
  `guard_offer_overlap`. No se puede tener «una oferta por equipo».
- **Venta bajo pedido:**
  - en el checkout la línea es `supply_origin='supplier'` con `supplier_offer`;
  - el pago llama a `commit_order_lines`, que bloquea la fila de la oferta, suma `committed_quantity` y no
    mueve stock;
  - si la oferta ya no cubre la línea, queda `to_confirm`;
  - en el carrito, `views.py` ya admite una línea bajo pedido de un producto `sells_by_unit` sin
    `stock_unit`.
- **Recepción (#138–#140):**
  - `supply_services.receive_line` bloquea el pedido y la línea, llama a
    `stock_unit_services.receive_units` (la única puerta de equipos: serie, IMEI y unicidad), escribe la
    salida de venta y registra `OrderUnit` (SERIAL-PICK);
  - es idempotente: `already_received` (409);
  - `fail_line` es la falla D3: no devuelve disponibilidad.
- **Público:**
  - `device_catalog.public_model` muestra los seminuevos **sólo** como `StockUnit` físicos
    (`public_unit`: batería, grado e `imei_hint` enmascarado);
  - una variante nueva bajo pedido muestra sólo plazo y precio (`on_order_payload`).
- **No existe** ninguna ficha de equipo externo, ni incidencia de recepción, ni una comparación de
  identificadores entre lo esperado y lo recibido.

## 2. Diseño

### D2. La ficha no es un `StockUnit` y reutiliza la oferta

- **Oferta:** cada proveedor tiene **una** `SupplierOffer` por variante seminueva, como hoy.
  - Lleva el precio comercial, el costo pactado, la vigencia y el plazo.
  - Su `confirmed_quantity` es el número de fichas vigentes. Lo mantiene sólo el servicio de fichas, bajo
    el bloqueo de la oferta.
- **Ficha:** cada equipo informado es una **`ExternalUsedCard`** que cuelga de esa oferta.
  - Guarda lo que informó el proveedor: serie, IMEI, IMEI 2, batería y grado. Son datos **pendientes de
    verificación** y sólo internos.
  - Su estado es `available` → `reserved` (con la línea vendida) → `received`, o `withdrawn`.
- **El seminuevo genérico sigue prohibido:** una oferta de seminuevo sin fichas no vende, y la lista
  semanal sigue rechazando seminuevos.
  - `sells_on_order`, `valid_offers` y `_offer_is_valid` admiten un seminuevo sólo si hay fichas
    disponibles.
  - `set_sale_mode` admite «Bajo pedido» en una variante seminueva.
- **Vender:**
  - es el camino de siempre: `best_offer` → checkout → `commit_order_lines`;
  - dentro del **mismo bloqueo de la oferta**, `commit_order_lines` reserva `quantity` fichas
    disponibles para la línea;
  - si no hay fichas suficientes, la línea queda `to_confirm`, como una oferta agotada;
  - no hay un segundo escritor: el pago sigue siendo el único que compromete.
- **No hay `StockUnit` antes de recibir**, y nunca se crea un `StockUnit` desde una ficha.

### D3. Lo público

- **Qué se muestra:** en la página del modelo, una fila por variante seminueva con fichas disponibles.
  Lleva las opciones, el precio comercial, el plazo y la condición comercial (la etiqueta de la línea
  seminuevo de la tienda).
- **Qué no se muestra:** IMEI, batería, grado, serie, proveedor ni costo.
- **Cómo se prueba:** las claves de la respuesta pública son una lista cerrada.

### D4. Recepción con coincidencia obligatoria

- **Coincidencia:** `receive_line` de una línea con fichas reservadas compara **antes de escribir nada**.
  Cada equipo recibido debe coincidir con una ficha reservada de esa línea, por IMEI si la ficha lo trae y
  si no por serie.
- **Si no coincide:** no se escribe nada.
  - Se abre una **`SupplyReceptionIncident`** (una abierta por línea) en su propia transacción, con lo
    esperado, lo recibido, quién y cuándo, y `AdminAuditLog` con `company`.
  - La respuesta es 409 `identity_mismatch`.
- **Mientras haya una incidencia abierta, la línea no se recibe**, aunque se presente el equipo correcto.
- **Resolución:** sólo un superior (`cancelling.may_cancel_as_superior`, la misma regla que cierra una
  devolución), con motivo.
  - Única resolución en esta fase: **`rejected`**. El equipo recibido no entra, la ficha sigue reservada a
    la línea, y la línea puede recibir el equipo correcto o fallar por el `fail_line` existente (flujo de
    reembolso).
  - Nunca se sustituye un IMEI.
- **Fiscal:** nada se emite ni se modifica. Una prueba fija que los recuentos fiscales no cambian.
- **Identificadores en las respuestas:** sólo aparecen enmascarados (`mask_imei`). Quien recibe no ve el
  IMEI esperado completo: la comparación es ciega y la hace el servidor.

### Permisos y multiempresa

- **Fichas:**
  - ver: `products.view`;
  - crear y retirar: `products.manage`;
  - costo: `costs.view`.
- **Recibir:** lo de hoy, `sales.orders.manage` + `inventory.adjust`.
- **Resolver:** superior.
- **Multiempresa:** todo se resuelve en el servidor, y un id ajeno responde 404.

## 3. Migraciones

Hay una sola, `0146_external_used_card_and_incident`, con dos tablas nuevas, sin tocar columnas
existentes ni rellenar datos. Se ensaya en los dos sentidos (`migrate store 0145` ↔ `0146`).

## 4. BLOCKED (decisión del propietario o fiscal)

- **B1 · Sustitución legítima** de un equipo distinto: consentimiento del comprador, diferencia de precio,
  qué pasa con el proveedor. D4 pide «un procedimiento auditado propio» que no está definido. Hoy sólo
  existe «rechazar».
- **B2 · Fiscal:** serie o IMEI de un equipo externo en el comprobante, y cualquier efecto de una
  sustitución en documentos emitidos. Es una decisión aparte de FASE 8. No se toca.
- **B3 · Precio por variante o por equipo:** propuesta, por confirmar. Con la oferta reutilizada, todas las
  fichas de un proveedor y una variante tienen el mismo precio público, coherente con D3: el público no
  distingue los equipos. Un precio por ficha exigiría una oferta por equipo y rompería
  `unique_supplier_offer_window`.

## 5. Fuera de esta fase

Interfaz de alta masiva de fichas (texto de WhatsApp → fichas), publicación de datos verificados tras
recibir (el equipo ya está vendido), y cualquier cambio de pagos o Izipay.
