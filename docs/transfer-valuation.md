# TRANSFER-VALUATION-01 — valorización de transferencias

Una transferencia mueve valor entre sucursales de una misma empresa. No crea ni destruye valor.

## Origen de la diferencia (auditoría 2026-11-18)

Se encontraron dos casos.

1. **Equipos con serie, línea mixta.** El `TRANSFER_OUT` valora una línea con equipos con costo y sin costo así:
   el equipo sin costo usa el costo de referencia del catálogo, y la línea es la media, con fuente `reference`.
   A la llegada, `cost_services.resolve` rellenaba el equipo sin costo con esa media ya calculada y volvía a
   promediar. Ejemplo: 2800 y sin costo, referencia 2000. La salida es 2400 y la llegada era 2600. El valor
   de entrada difería del de salida.
2. **Accesorios con unidades sin costo y con costo.** Las unidades sin costo salen primero (regla DC1). Una
   salida que tomaba unidades sin costo y con costo se valoraba entera al costo de referencia, o como
   desconocida. En destino toda la línea quedaba como «costo pendiente». Ejemplo: 2 unidades sin costo y
   3 a 10. Al enviar 4, el origen perdía 20 de valor conocido y el destino no lo recibía.

## Corrección

- **Equipos:** el `TRANSFER_IN` lleva el costo y la fuente de su `TRANSFER_OUT` tal cual
  (`resolve(..., mirrors_line=True)`). El costo propio de cada equipo (`StockUnit.cost`) no cambia y nunca se
  inventa: un equipo sin costo sigue sin costo.
- **Costo en tránsito:** el costo de un equipo en tránsito no se puede corregir (409). Se corrige antes de
  despachar o después de recibir.
- **Accesorios:** al despachar, una línea que toma unidades sin costo y con costo sale en dos `TRANSFER_OUT`:
  primero la parte pendiente (referencia o desconocido) y después la parte con costo (promedio de la sucursal).
  La división se calcula con la fila de stock bloqueada, en el momento del despacho. Al recibir, cada
  `TRANSFER_OUT` tiene su `TRANSFER_IN` con la misma cantidad, costo y fuente. La parte con costo alimenta el
  promedio del destino y la parte pendiente sigue pendiente.
- **Rechazo explícito:** la recepción se rechaza si lo que salió no suma la cantidad de la línea, o si una línea
  de equipos tiene más de un `TRANSFER_OUT`. En ese caso hay que revisar el Kardex del origen.

## Qué no cambia

- Los costos históricos no se recalculan.
- Ninguna línea ya escrita se modifica.
- Una transferencia despachada antes de este cambio tiene una salida por línea y llega igual que antes.
- `costs.view`: el Kardex y la transferencia no muestran costos (`unit_cost` no está en sus respuestas). La
  `metadata` del Kardex no lleva importes.
- Las transferencias siguen siendo dentro de una misma empresa.
- No hay migración.

## Para el despliegue y la vuelta atrás

- Antes de desplegar, conviene una consulta de sólo lectura: transferencias `in_transit` cuyo `TRANSFER_OUT`
  por producto no sume la cantidad de su línea. Debe dar cero; si no, esa transferencia no se podrá recibir.
- Si se vuelve a la versión anterior con una transferencia dividida en tránsito, la versión anterior la recibe
  entera con el costo de la primera línea (la parte pendiente). La cantidad queda bien, pero el valor conocido
  vuelve a quedar pendiente. Es el defecto original. Antes de volver atrás, conviene recibir esas transferencias.
- Quien ve el Kardex sin `costs.view` ve dos salidas (por ejemplo 2 + 2). La primera cantidad dice cuántas
  unidades no tenían costo al despachar. No muestra ningún importe.

## Pruebas

`backend/store/test_transfer_valuation.py`:
- equipos con costo, mixtos con y sin referencia, referencia cambiada en tránsito;
- costo bloqueado en tránsito, otros datos editables;
- accesorios homogéneos, mixtos con y sin referencia, sólo pendientes;
- división según el stock del despacho;
- idempotencia de despacho y recepción;
- equipos y accesorios juntos;
- sin `costs.view`;
- otra empresa;
- recepción rechazada si las salidas no cuadran (accesorios y equipos);
- transferencia despachada por la versión anterior (una sola salida);
- 409 del panel al corregir un costo en tránsito;
- pendientes igual a la cantidad.

La concurrencia se apoya en los bloqueos existentes: la transferencia y las filas de stock se bloquean en el
orden del módulo antes de calcular la división.
