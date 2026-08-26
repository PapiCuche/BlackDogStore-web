# Inventario avanzado y notas de venta internas

> **Fase 6.0** · Kardex, reportes operativos y documentos internos de venta.

---

## 1. Sistema de inventario

Antes de la Fase 6.0 el stock era un único entero (`Product.inventory`) que se
modificaba desde dos sitios: el webhook de Stripe y el endpoint de ajuste manual.
No quedaba rastro de *quién* lo movió, *cuándo* ni *por qué*.

Ahora **todo cambio de stock pasa por `store/inventory_services.py`** y produce
una línea inmutable de Kardex (`StockMovement`). Stock y Kardex se escriben en la
misma transacción, así que nunca pueden divergir.

```
Product.inventory  ←──┐
                      ├── store/inventory_services.create_stock_movement()
StockMovement      ←──┘        (transaction.atomic + select_for_update)
```

Garantías del servicio:

| Regla | Implementación |
|---|---|
| El stock nunca queda negativo | `stock_after < 0` → `InsufficientStockError`, rollback |
| No se confía en el stock del cliente | Se relee de la DB con `select_for_update()` |
| `quantity` siempre positivo | El signo lo decide `movement_type` |
| Movimiento manual con responsable y motivo | `apply_manual_stock_movement` exige `actor` + `reason` |
| Salidas por venta idempotentes | Una sola `sale_exit` por `(orden, producto)` |

---

## 2. Tipos de movimiento

| Tipo | Signo | Origen |
|---|---|---|
| `initial_stock` | + | Carga inicial |
| `purchase_entry` | + | Compra a proveedor |
| `manual_entry` | + | Ajuste manual de operador |
| `return_entry` | + | Devolución de cliente |
| `correction_positive` | + | Corrección de conteo |
| `manual_exit` | − | Salida manual de operador |
| `sale_exit` | − | **Solo el pipeline de pago** |
| `correction_negative` | − | Corrección de conteo |
| `damaged_exit` | − | Merma / equipo dañado |
| `service_exit` | − | Reservado para servicio técnico (aún sin UI) |

`sale_exit` está deliberadamente excluido de los tipos que un operador puede
registrar a mano: el endpoint lo rechaza con `400`. Las salidas por venta solo
las crea el webhook de Stripe.

---

## 3. Kardex

Cada `StockMovement` guarda un fotograma completo del momento:

```
product · movement_type · quantity · stock_before · stock_after
reason · reference_type · reference_id · order · actor · created_at · metadata
```

`stock_before` y `stock_after` se toman bajo bloqueo de fila, así que la
secuencia es auditable línea a línea aunque haya movimientos concurrentes.

El Kardex por producto está en `GET /api/admin/products/{id}/stock-card/` y en
la UI en `/admin/products/{id}/stock-card`.

`StockMovement` está registrado en el Django Admin en **solo lectura**: editarlo
a mano desincronizaría `Product.inventory`.

---

## 4. Idempotencia de las ventas

Cuando Stripe confirma un pago, el webhook llama:

```python
record_sale_stock_movements(order)
```

que descuenta stock y escribe el Kardex dentro de la transacción que ya tenía la
orden bloqueada. Antes de crear cada movimiento consulta qué productos de esa
orden ya tienen una `sale_exit`, y los omite. Consecuencia:

- Un webhook reintentado por Stripe **no vuelve a descontar stock**.
- Tres entregas del mismo evento producen exactamente **una** `sale_exit` por ítem.

Si el stock se agotó entre el checkout y la confirmación, el dinero ya está
cobrado: el servicio **no revierte el pago**. Registra la discrepancia en
`order.payment_error` para revisión del operador — el mismo comportamiento que
antes de la Fase 6.0.

---

## 5. Roles y permisos

| Rol | Inventario | Movimientos | Reportes de venta | Notas de venta |
|---|---|---|---|---|
| `customer` | ✗ | ✗ | ✗ | ✗ |
| `sales` | ✗ | ✗ | ✓ leer | ✓ emitir y descargar |
| `inventory` | ✓ leer | ✓ crear | ✓ leer | ✗ |
| `technician` | ✗ | ✗ | ✗ | ✗ |
| `admin` | ✓ | ✓ | ✓ | ✓ |
| `superadmin` | ✓ | ✓ | ✓ | ✓ |

Separación deliberada: **`inventory` mueve stock pero no toca pagos ni emite
documentos de venta**; **`sales` emite documentos pero no puede alterar stock**.

Clases de permiso en `store/permissions.py`: `CanViewInventoryReports`,
`CanManageStockMovements`, `CanViewSalesReports`, `CanManageSalesNotes`.

`technician` queda sin acceso en esta fase. El tipo `service_exit` ya existe en
el modelo para cuando se le habilite retirar equipos a taller.

---

## 6. Endpoints

| Método | Ruta | Permiso |
|---|---|---|
| `GET` | `/api/admin/inventory/summary/` | `CanViewInventoryReports` |
| `GET` | `/api/admin/inventory/movements/` | `CanViewInventoryReports` |
| `POST` | `/api/admin/inventory/movements/` | `CanManageStockMovements` |
| `GET` | `/api/admin/inventory/low-stock/?threshold=5` | `CanViewInventoryReports` |
| `GET` | `/api/admin/inventory/high-stock/` | `CanViewInventoryReports` |
| `GET` | `/api/admin/inventory/best-selling/?date_from&date_to&limit` | `CanViewSalesReports` |
| `GET` | `/api/admin/inventory/no-movement/?days=60` | `CanViewInventoryReports` |
| `GET` | `/api/admin/products/{id}/stock-card/` | `CanViewInventoryReports` |
| `GET`/`POST` | `/api/admin/orders/{id}/sales-note/` | `CanManageSalesNotes` |
| `GET` | `/api/admin/orders/{id}/sales-note/pdf/` | `CanManageSalesNotes` |

Filtros de `movements/`: `product`, `movement_type`, `date_from`, `date_to`,
`actor`, `order`, `search`, `page`, `page_size` (máx. 100).

---

## 7. Reportes disponibles

1. **Resumen** — productos totales/activos, unidades, agotados, bajo stock, valor del inventario, producto más vendido.
2. **Bajo stock** — al o por debajo de un umbral configurable.
3. **Alto stock** — mayor cantidad inmovilizada.
4. **Agotados** — stock en cero.
5. **Más vendidos** — unidades e ingresos, derivados **solo de órdenes pagadas**.
6. **Últimos movimientos** — Kardex reciente.
7. **Sin movimiento en X días** — productos dormidos.

`inventory_value` = Σ(`inventory` × `price`) de productos activos con stock. Es
**valor a precio de venta**, no costo: no hay costeo promedio en esta fase.

UI: `/admin/inventory`, `/admin/inventory/movements`, `/admin/inventory/reports`.

---

## 8. Notas de venta internas

Una `SalesNote` es un documento **interno** para una orden pagada.

```
NV-000001    NV-000002    NV-000003
```

Reglas:

- Solo para órdenes con `status = paid`. `pending_payment`, `failed`, `expired`
  y `cancelled` reciben `400`.
- Una nota por orden (`OneToOneField`). Emitirla dos veces devuelve la misma
  (`201` la primera vez, `200` después).
- El correlativo es **interno**, asignado bajo la transacción que bloquea la orden.
- Emitir o descargar una nota **no modifica el pago ni el inventario**.

### Lo que la nota NO es

> ⚠️ **Documento interno de venta. No válido como comprobante electrónico SUNAT.**

- **No** es un comprobante electrónico fiscal.
- **No** es numeración fiscal ni una serie registrada ante SUNAT.
- **No** tiene validez tributaria.
- **No** genera XML ni se firma digitalmente.

El disclaimer aparece dos veces en cada PDF: en un recuadro destacado bajo el
título y en el pie de página.

### Contenido del PDF

Black Dog Store · CMAU CORP E.I.R.L. · RUC 20610159886 ·
Octavio Muñoz Najar 238, Tienda 104 · WhatsApp +51 936 449 536 · número interno ·
fecha · cliente · teléfono · documento · comprobante solicitado · método de
entrega · productos (nombre, cantidad, precio unitario, subtotal) · descuento ·
total · notas · disclaimer.

**Nunca incluye** `stripe_session_id`, `stripe_payment_intent_id`,
`payment_error`, tokens, cookies ni secretos. Hay tests que verifican que esas
cadenas no aparecen ni en el contexto ni en los bytes del PDF.

---

## 9. Auditoría

| Acción | Cuándo |
|---|---|
| `stock_entry_created` | Entrada manual registrada |
| `stock_exit_created` | Salida manual registrada |
| `sales_note_created` | Nota de venta emitida (solo la primera vez) |
| `sales_note_pdf_downloaded` | PDF de nota descargado |
| `product_inventory_adjusted` | Endpoint heredado de ajuste (Fase 3.2) |

Metadata permitida: `product_id`, `product_name`, `movement_type`, `quantity`,
`stock_before`, `stock_after`, `reason`, `order_id`, `sales_note_id`,
`sales_note_number`.

**Nunca** se guarda: identificadores de Stripe, `payment_error`, tokens, cookies
ni secretos.

---

## 10. Rate limits

```
admin_inventory_reports   120/min
admin_stock_movements      60/min
admin_sales_notes          60/min
```

No afectan al webhook, al checkout, al carrito, a la autenticación ni a los emails.

---

## 11. Lo que esta fase todavía NO hace

- Integración real con **SUNAT**.
- Generación de **XML** (UBL 2.1).
- **Firma digital** de comprobantes.
- **Numeración fiscal** o series registradas.
- Compras y proveedores.
- Costeo promedio ponderado / Kardex valorizado.
- Lote, número de serie o IMEI.
- Múltiples almacenes.

---

## 12. Próximas fases sugeridas

**Compras e inventario valorizado**
1. Proveedores.
2. Órdenes de compra.
3. Costeo promedio ponderado.
4. Kardex valorizado (costo, no solo unidades).
5. Control de merma formalizado.
6. Control de devoluciones.

**Trazabilidad por equipo (clave para un especialista Apple)**
7. IMEI / número de serie por unidad.
8. Estado del equipo: nuevo · seminuevo · reacondicionado.
9. Condición de batería.
10. Garantía individual por equipo.

**Almacenes y reservas**
11. Transferencias entre almacenes.
12. Almacenes múltiples: tienda · depósito · técnico.
13. Reservas de stock.
14. Stock comprometido en órdenes pendientes.

**Reportes y finanzas**
15. Reporte de rotación.
16. Reporte de productos dormidos (ya existe una versión básica).
17. Margen de ganancia por producto.
18. Dashboard financiero.
19. Control de caja.
20. Exportar inventario y ventas a Excel.
21. Exportar movimientos a PDF.

**Operación**
22. Alertas de bajo stock por WhatsApp o email.
23. `service_exit` habilitado para el rol `technician`.

**Fiscal (fase separada)**
24. Integración SUNAT real: series fiscales, XML UBL 2.1, firma digital y
    validación tributaria. Esta fase es independiente y no debe mezclarse con el
    correlativo interno descrito arriba.
