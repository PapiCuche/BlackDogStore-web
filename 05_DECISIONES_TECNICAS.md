# Decisiones técnicas

Este archivo no existía en el baseline. Es la entrada resumida a las decisiones
de arquitectura del proyecto; el registro completo y su historia viven en los
ADR por dominio, que no se reescriben.

## Registro por dominio

| Dominio | Registro completo |
|---|---|
| Fiscal peruano (SUNAT, UBL 2.1) | [docs/adr-fiscal-c22a1.md](docs/adr-fiscal-c22a1.md) — ADR-1 … ADR-43 |
| Matriz de conformidad UBL 2.1 | [docs/sunat-factura-ubl21-matriz.md](docs/sunat-factura-ubl21-matriz.md) |
| Requisitos y catálogos SUNAT | [docs/sunat-cpe-requisitos.md](docs/sunat-cpe-requisitos.md) |

## Decisiones registradas en esta entrada

### DEC-42 · Atribución determinista del descuento de promociones a componentes

Regla del motor SaaS, no del tenant piloto. Una promoción reparte su descuento YA
DECIDIDO entre los componentes consumidos en proporción a su valor regular
(`unit_price × quantity_used`), con ROUND_DOWN a céntimos, céntimos restantes a los
mayores residuos y desempate por `product_id` ascendente. Cierra exacto o falla. Se
congela en `AppliedPromotion.metadata["components"]`; el snapshot congelado es la
autoridad histórica (se valida y se usa tal cual), un snapshot anterior se
reconstruye en memoria sin escribir, y un snapshot a medias falla cerrado. No cambia
precio, stacking, prioridades, unidades consumidas ni totales. Detalle, motivos y
descartes: [ADR-42](docs/adr-fiscal-c22a1.md#adr-42--atribución-determinista-del-descuento-de-una-promoción-a-sus-componentes).

### DEC-43 · Descuentos declarados en UBL sin doble descuento

Cupón y manual: un `cac:AllowanceCharge` global con código `02` del Catálogo N.º 53
vigente; promoción: uno por línea rebajada con código `00`. Importes en valor de
venta derivados del snapshot (`subtotal_amount`, `taxable_amount`, `tax_rate`).
`LegalMonetaryTotal/LineExtensionAmount` es la base imponible (regla 3278),
`PayableAmount` es `Order.total`, y `AllowanceTotalAmount` se omite porque SUNAT lo
reserva a descuentos que no afectan la base y lo resta del importe a pagar (reglas
3300/3280). Fuente: archivo oficial de reglas de validación (21.04.2025). Detalle:
[ADR-43](docs/adr-fiscal-c22a1.md#adr-43--un-descuento-se-declara-donde-nació-con-el-código-de-su-nivel-y-no-se-resta-dos-veces).
