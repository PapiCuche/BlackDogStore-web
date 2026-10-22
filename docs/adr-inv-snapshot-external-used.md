# ADR — Listas completas de proveedor y seminuevos externos (PROPUESTA)

Estado: **PROPUESTA, sin implementar.** Requiere aprobación del propietario antes de tocar contratos de
stock, ofertas o venta. Fecha: 2026-10-22. Base: `master` con INV-SWITCH-01, INV-EXP-01 e INV-ID-01.

## Contexto (lo que el código hace hoy)

- La lista semanal (`supplier_offer_import.py`, DEC-SUPPLIER-WEEKLY-01) es **parcial**: lo que no viene en
  el archivo no cambia; `0` = agotada; los cambios de precio comercial exigen aceptar o conservar; la vista
  previa obsoleta se rechaza; hay una sola oferta por proveedor y producto.
- D1: la cantidad semanal es la disponibilidad neta (confirmado = comprometido + archivo). D2: margen
  estimado con costo pactado y realizado con costo real. D3: una falla del proveedor no devuelve
  disponibilidad.
- Un seminuevo genérico **bajo pedido** está restringido por el backend; un seminuevo propio es un
  `StockUnit` con serie, IMEI y batería.
- La vista previa de texto (INV-TEXT-02, #142) reconoce candidatos, sin escribir.

## Decisión 1 — Lista completa por proveedor (snapshot)

Propuesta: un modo **«lista completa»** explícito por importación, nunca inferido del texto.

1. La persona elige proveedor y marca «Esta es la lista completa del proveedor X» (la instrucción del texto
   es sólo una sugerencia: el servidor exige la marca).
2. La vista previa compara la lista con las ofertas **vigentes de ese proveedor**: altas, cambios (costo,
   cantidad, vigencia) y **ausentes**.
3. Los ausentes sólo se desactivan al **confirmar** la aplicación del snapshot, en una transacción con
   bloqueo de las ofertas de ese proveedor (mismo patrón que la vista previa obsoleta: si cambió algo desde
   la vista previa, se rechaza).
4. Desactivar una ausente **no toca** su comprometido: las ventas ya pagadas siguen su curso (D1/D3 sin
   cambios); sólo deja de ofrecerse lo que queda. Un ausente con comprometido pendiente se lista aparte.
5. Otros proveedores y otras empresas no cambian. Auditoría `supplier_snapshot_applied` con altas, cambios
   y ausentes (sin costos en la auditoría pública).
6. Filas duplicadas no suman; cantidad no informada = desconocida (`confirmed_quantity` nulo), que **no**
   habilita venta (`valid_offer_q` ya lo exige).

Contratos que cambian: `supplier_offer_import.preview/apply` (parámetro `complete_snapshot`), nueva
categoría «ausente» en la vista previa. Sin migración si la desactivación usa `is_active` +
`deactivated_reason`.

Pruebas mínimas: ausente desactivado sólo al confirmar; vista previa obsoleta; ausente con comprometido;
otro proveedor intacto; concurrencia de dos aplicaciones; idempotencia (mismo archivo dos veces).

## Decisión 2 — Seminuevos externos

Tres tipos, distintos:

| Tipo | Qué se sabe | Qué es en el sistema |
|---|---|---|
| Seminuevo propio | Unidad física: serie, IMEI, batería | `StockUnit` (sin cambios) |
| Seminuevo genérico de proveedor | Modelo, capacidad, color, costo | Oferta de proveedor de la línea seminuevo |
| Seminuevo externo identificado | Además serie/IMEI/batería informados | Oferta de proveedor **con ficha**, no `StockUnit` |

Propuesta:

1. **Nunca** crear `StockUnit` antes de la recepción física (regla vigente).
2. El seminuevo externo identificado se guarda como **ficha de la oferta** (serie/IMEI/batería informados por
   el proveedor), visible sólo internamente; el público ve el modelo y el estado, nunca el IMEI.
3. Venta: una ficha = una unidad; reservar la ficha al pagar (bloqueo de fila) evita vender dos veces el
   mismo equipo. Al recibir, `receive_line` crea el `StockUnit` con la serie/IMEI **escaneados**; si no
   coinciden con la ficha, la recepción lo marca para revisión (no lo sustituye en silencio).
4. El seminuevo genérico bajo pedido sigue restringido hasta aprobar este ADR.

Requiere migración (ficha de oferta) y cambios en checkout/recepción: **no se implementa sin aprobación**.
Impacto fiscal (serie/IMEI en el comprobante): decisión aparte de FASE 8.

## Preguntas al propietario

1. ¿La «lista completa» desactiva también las ofertas **programadas** (vigencia futura) ausentes, o sólo las
   vigentes hoy?
2. ¿Un ausente con ventas pendientes se desactiva igual (dejando las ventas) o bloquea la aplicación?
3. ¿Un seminuevo externo identificado se publica con su batería y estado, o sólo como «seminuevo»?
4. ¿Se exige que el IMEI escaneado al recibir coincida con el de la ficha para cerrar la venta?
