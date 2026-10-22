# ADR — Listas completas de proveedor y seminuevos externos (PROPUESTA)

Estado: **DIRECCIÓN APROBADA por el propietario (2026-10-22).** Decisión 1 implementada en INV-SNAPSHOT-01 (rama
`feat/inv-snapshot-01`, DEC-INV-SNAPSHOT-01); Decisión 2 (seminuevos externos) sin implementar. Las decisiones D1–D4 de
abajo son suyas; la implementación sigue sujeta a validar los contratos reales y no toca checkout, pagos,
fiscal ni seminuevos bajo pedido. Fecha: 2026-10-22. Base: `master` con INV-SWITCH-01, INV-EXP-01 e INV-ID-01.

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
6. Filas duplicadas no suman. Cantidad no informada: ver «Disponibilidad no confirmada» abajo — **no**
   habilita venta.

### Decisiones del propietario

- **D1 — Programadas.** La lista completa desactiva sólo las ofertas **vigentes hoy** del proveedor
  elegido que estén ausentes. Las futuras no cambian; incluirlas será una operación administrativa aparte.
- **D2 — Ausentes con ventas pendientes.** Se desactivan para nuevas ventas; compromisos, pedidos pagados y
  trazabilidad quedan intactos. Se muestran en una sección de advertencias. Nunca se cancela, se toca un
  pago, se libera una reserva ni se reembolsa automáticamente.

### Disponibilidad no confirmada (corrección obligatoria)

El modelo tiene `supplier_offer_never_overcommitted`: `committed_quantity = 0` **o**
(`confirmed_quantity` no nulo y `>= committed_quantity`). Por eso `confirmed_quantity = NULL` es imposible
mientras haya compromisos: una lista que no informa cantidad para una oferta con ventas en curso no puede
dejarla nula.

Propuesta: un estado explícito `availability_confirmed` (booleano, por defecto verdadero, migración
aditiva).

- Cantidad no informada ⇒ `availability_confirmed = False` y `confirmed_quantity = committed_quantity`
  (lo que queda por vender es 0, la restricción se cumple, D1 «confirmado = comprometido + archivo» se
  conserva con archivo = 0).
- `valid_offer_q` exige además `availability_confirmed = True`: nada nuevo se vende, aunque la oferta siga
  activa para sus compromisos (D2 y D3 sin cambios: el comprometido se recibe o falla como hoy).
- Una lista posterior con cantidad la vuelve a confirmar. La tienda muestra «No disponible»; el panel,
  «Disponibilidad no confirmada».

### Integridad del snapshot (corrección obligatoria)

No se desactiva nada si el lote: está truncado (más de 300 candidatos o texto recortado), tiene filas con
error sin resolver, tiene líneas no reconocidas sin revisar, o la persona no marcó que ya incorporó todos
los mensajes. Antes de confirmar se muestra cuántas ofertas se desactivarán y cuántas tienen
comprometido; la confirmación exige `products.manage` con toda la empresa en alcance y una marca explícita
«Confirmo desactivar N ofertas ausentes». Transacción con bloqueo de las ofertas del proveedor, vista
previa obsoleta rechazada, idempotencia por huella del lote y auditoría.

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
2. **D3 (propietario).** En público: modelo, variante y condición comercial «seminuevo». Batería, grado
   estético, serie e IMEI informados por el proveedor son **datos pendientes de verificación**: sólo
   internos hasta comprobar el equipo. Nunca IMEI ni identidad del proveedor al público.
3. Venta: una ficha = una unidad; reservar la ficha al pagar (bloqueo de fila) evita vender dos veces el
   mismo equipo.
4. **D4 (propietario).** Al recibir una unidad vinculada a una venta, la coincidencia de identificadores es
   **obligatoria**. Si no coincide: no se sustituye en silencio; se bloquea la confirmación del equipo
   esperado; se registra una incidencia; la revisa un usuario autorizado; se conservan la ficha original y
   lo recibido con sus permisos; una sustitución legítima sigue un procedimiento auditado propio. Ningún
   comprobante se emite ni se modifica retroactivamente por una sustitución sin una decisión fiscal
   específica.
5. El seminuevo genérico bajo pedido sigue restringido hasta aprobar su implementación.

Requiere migración (ficha de oferta) y cambios en checkout/recepción: **no se implementa sin aprobación**.
Impacto fiscal (serie/IMEI en el comprobante): decisión aparte de FASE 8.

## Preguntas al propietario

Respondidas el 2026-10-22 (D1–D4 arriba). Pendiente para la implementación: confirmar con pruebas los
contratos reales (`supplier_offer_never_overcommitted`, `valid_offer_q`, `receive_line`) antes de migrar.
