# SERIAL-OPTIONAL-01 · Serie opcional cuando el equipo tiene IMEI válido

**Estado:** implementado en la rama `feat/serial-optional-imei`, pendiente de revisión. Migración `0174_stock_unit_serial_optional`.

## Regla

Un equipo puede entrar sin número de serie sólo si cumple las dos condiciones:

- su artículo está marcado «lleva IMEI»;
- trae un IMEI válido: 15 dígitos que superan el dígito de control.

El IMEI es único en toda la empresa (`unique_stock_unit_imei_per_company`), así que el equipo sigue siendo una sola fila.

| Artículo «lleva IMEI» | IMEI válido | Serie | Resultado |
|---|---|---|---|
| sí | sí | vacía | entra con `serial_number = NULL` |
| sí | no | cualquiera | se rechaza: falta el IMEI |
| no | cualquiera | vacía | se rechaza: falta el número de serie |

- La serie ausente se guarda como `NULL`, nunca como un marcador («N/A», «SIN SERIE»…).
- Dos equipos sin serie del mismo artículo son dos equipos distintos, no una «serie repetida». La comprobación de duplicados, el bloqueo por identificador y las repeticiones dentro de una carga sólo comparan series que existen.
- Si se escribe una serie, se valida y se controla igual que antes.

## Dónde se aplica

Se aplica en todas las vías de ingreso, porque todas pasan por `stock_unit_services._clean_row`:

- «Recibir mercadería» (`receipt_services`);
- la carga Excel de equipos (`unit_import_services`);
- el registro de artículos;
- la recepción de pedidos a proveedor;
- el formulario de equipos.

En el panel:

- Las validaciones del navegador siguen la misma regla. Sólo piden la serie si el artículo no lleva IMEI.
- La etiqueta del campo dice «Número de serie (opcional con IMEI)».
- Un equipo sin serie se nombra por su IMEI, con `unitIdentifier` en `frontend/app/lib/unit-identifier.ts` y `StockUnit.identifier` en el backend. Así aparece en listados, detalle, costos, etiquetas y mensajes de error.
- La API devuelve `serial_number: ""` (nunca `null`) para no romper a sus lectores.

## Migración y vuelta atrás

- `0174` hace `DROP NOT NULL` en `store_stockunit.serial_number`. Es un cambio de catálogo: no reescribe la tabla ni la recorre.
- La restricción `stock_unit_identifiers_null_not_blank` ya admitía `NULL`.
- La restricción única (empresa, producto, serie) trata los `NULL` como distintos.
- **Vuelta atrás del esquema:** `migrate store 0173` hace `SET NOT NULL`, y falla si ya existe un equipo sin serie. Lo seguro es volver al código anterior **conservando** el esquema 0174.
- **Código anterior sobre equipos sin serie:** el código anterior nunca escribe `NULL` y lee esas filas. Hay dos diferencias visibles:
  - algunos mensajes dirían «None» en lugar del IMEI;
  - la lista del panel recibiría `null` en la serie.

  No se pierde ningún dato.
- Antes de deshacer la migración, cada equipo sin serie necesita su serie. Para encontrarlos: `StockUnit.objects.filter(serial_number__isnull=True)`.
