# Devolución posterior a la entrega — PROPUESTA (sin implementar)

Estado: **APROBADA** por el propietario el 2026-10-19 (D1–D8, todas según la recomendación).
Fase 1 implementada (DEC-ORDER-RETURN-01): devolución del pedido completo. Origen: DEC-ORDER-FULFILLMENT-SAFETY-01
dejó PENDIENTE este flujo.

**Precisión de la fase 1 sobre D3.** Un equipo con serie: «vendible» vuelve disponible; «a
revisión» entra apartado (el estado «Apartado» que ya existe, con el motivo) hasta que alguien lo
libere; «dañado» entra y se da de baja (Kardex: entrada y baja). Un producto por cantidad: sólo
«vendible» o «dañado» (no hay dónde apartar cantidades); «a revisión» para cantidades queda para
la fase 2.

## El problema

Un pedido web que ya salió de la tienda (enviado o entregado) no se puede cancelar: la
cancelación lo rechaza con «requiere devolución». Hoy no existe esa devolución para pedidos web.
La caja sí tiene «Anular venta» con «la mercadería volvió al local».

Sin este flujo, cuando un cliente devuelve un equipo:
- el stock se corrige a mano;
- la venta sigue contando como ingreso;
- el reembolso no queda ligado al pedido.

## Qué se reutiliza (ya existe y está probado)

- **Stock:** la devolución al Kardex al costo de la venta (`order_cancel_return`), una sola vez
  por salida. El equipo exacto que salió es el que vuelve.
- **Reembolso:** pendiente → confirmado, con referencia, evidencia e importe exacto
  (`company.manage`). Nunca se confirma solo.
- **Comisión:** si no está liquidada, se anula; si ya se liquidó, ajuste compensatorio único.
- **Fiscal:** la boleta o factura queda marcada «nota de crédito pendiente». No se emite nada
  desde aquí.
- **Nota de venta interna:** se anula y se conserva.
- **Tableros:** una venta anulada ya sale de ventas y margen, y su dinero aparece aparte en
  «Cobros».

## Flujo propuesto (en dos pasos, por personas distintas si se quiere)

1. **Registrar la devolución.** Lo hace quien gestiona pedidos (`sales.orders.manage`) e indica:
   - el motivo y el tipo: desistimiento, producto defectuoso, error de envío u otro;
   - qué vuelve: todo el pedido o algunas líneas (D1).

   El pedido muestra «Devolución en curso». Nada se mueve todavía.
2. **Recibir la mercadería.** Lo hace quien maneja inventario en la sucursal que recibe:
   - escanea el IMEI o la serie de cada equipo, que debe ser el mismo que salió con ese pedido;
   - indica el estado en que llega: vendible, a revisión o dañado (D3).
   - **Entonces sí:**
     - el stock entra al Kardex al costo de la venta;
     - el despacho pasa a «Devuelto» en el historial.
3. **Cerrar la devolución.** Lo hace un superior: confirma el importe a devolver (D4) y deja el
   reembolso **pendiente**. Además:
   - la venta (o la parte devuelta) deja de contar;
   - la comisión se anula o se compensa;
   - la nota de crédito queda pendiente.

   El reembolso se confirma después, como hoy, con evidencia del dinero devuelto.

Cada paso:
- queda en la auditoría con su usuario y su fecha;
- se ve en el historial de despacho;
- no se puede repetir: es idempotente y trabaja con el pedido bloqueado.

## Decisiones del propietario

| # | Pregunta | Recomendación |
|---|---|---|
| D1 | ¿Devolución sólo del pedido completo, o también de algunas líneas? | Fase 1: pedido completo; las parciales en una fase 2 (cambian margen, comisión y nota de crédito por línea). |
| D2 | ¿Qué pasa con el pedido? | Pedido completo: queda «Cancelado» con la marca «por devolución», igual que una anulación. Así los tableros ya lo tratan bien. |
| D3 | Estado del equipo devuelto | «Vendible» vuelve a la venta. «A revisión» y «Dañado» entran al stock pero no se venden hasta que alguien los revise. Un seminuevo puede cambiar de grado o batería al revisarlo. |
| D4 | ¿Cuánto se devuelve? | El total cobrado, menos un descuento opcional (por ejemplo, el flete de vuelta) con motivo. Nunca más de lo cobrado. |
| D5 | ¿Plazo? | Libre dentro de un plazo configurable por empresa (por ejemplo, 7 días desde la entrega). Pasado el plazo, sólo un superior. |
| D6 | ¿Quién autoriza? | Registrar: `sales.orders.manage`. Recibir: inventario. Cerrar: superior (`company.manage`). |
| D7 | Garantía | Una falla en garantía va a Servicio técnico, no a devolución. Si el taller decide cambiar el equipo o devolver el dinero, entonces se abre una devolución. |
| D8 | ¿También para ventas de caja? | Sí: la misma devolución sirve para una venta de caja entregada hace días. «Anular venta» queda para el error inmediato en el mostrador. |

## Lo que no hará

- No llama a la pasarela de pago ni devuelve dinero por sí mismo.
- No emite notas de crédito ante SUNAT. Eso es del flujo fiscal.
- No cambia el costo de la venta ni reescribe sus instantáneas.

## Impacto técnico estimado

- Un modelo nuevo de devolución (cabecera, y líneas en la fase 2) con migración aditiva.
- Un tipo de movimiento de Kardex «Devolución de cliente».
- La pantalla «Devolución» en el detalle del pedido.
- Pruebas de stock, comisión, reembolso, tableros, permisos y empresas.

Sin cambios en pagos.
