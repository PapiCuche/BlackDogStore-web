# ADR · Importes escritos por personas y archivos (DEC-INV-MONEY-01)

Estado: **B1-0 y B1-0b-1 FUSIONADOS** (sin desplegar). **B1-0b-2 con política APROBADA** (2026-11-15), sin implementar: espera 2 o 3 archivos representativos del propietario. **B1-0b-3** y los flujos restringidos esperan su propio GO.

## Contexto

Un importe escrito «5.500» puede ser cinco mil quinientos o cinco con cinco. Antes de B1-0, varios analizadores lo leían como 5,50 sin avisar. La auditoría B1-0b (2026-11-14) encontró el mismo defecto en los costos de inventario, en las cargas de productos y de stock, y en flujos restringidos (finanzas, reembolsos, POS, promociones y nota de débito).

## Decisión

### 1. Una sola política para el TEXTO (`backend/store/money_input.py`)

`money_input.amount_text(text, label)` devuelve un `Decimal` o lanza `AmountTextError(detail, code)`:

| Texto | Resultado | Código |
|---|---|---|
| `5500`, `5500,00`, `5500.00` | 5500 | — |
| `1.999,90`, `1,999.90`, `1.234.567` | importe válido (miles en grupos exactos de 3) | — |
| `5.500`, `5,500` (un separador seguido de exactamente 3 cifras) | rechazado; el mensaje nombra las dos lecturas | `ambiguous_amount` |
| `1800 2000`, dos importes en una entrada | rechazado | `several_amounts` |
| `5.500,1234`, `1800,2000`, `5.5555` (más de 2 decimales tras un separador) | rechazado | `invalid` |
| `1.2,3`, `5 500`, moneda, letras, exponente, NaN, signo | rechazado | `invalid` |

`ACTIONABLE_CODES = {'ambiguous_amount', 'several_amounts'}`: son los únicos códigos que la pantalla recibe para actuar sobre ellos. El resto llega como un error de validación sin código.

### 2. Los valores tipados no pasan por la política del texto

- Un `Decimal` o un `int` ya validados se usan tal cual. Los costos internos de 4 decimales (`StockMovement.unit_cost`, `BranchStock.average_cost`, `OrderItem.unit_cost`) nunca se convierten en texto para volver a leerlos.
- Un `float` sólo se acepta con 2 decimales como máximo. El costo inicial del registro guiado no acepta un `float`.
- Una celda NUMÉRICA de Excel es un número, no un texto. Se acepta el ruido de coma flotante (`0.30000000000000004`) sólo cuando la diferencia con 2 decimales es menor que `1e-9`.

### 3. Lo ya guardado no se vuelve a leer

Sólo cambia la lectura de las entradas nuevas: ningún costo ni precio histórico se reescribe. Una vista previa de carga caduca a la hora (`IMPORT_PREVIEW_TTL_SECONDS`, para todos los tipos de carga), así que la lectura antigua sólo puede aplicarse durante la primera hora después de un despliegue.

### 4. Permisos y aislamiento

- Sin `costs.view`, un error de costo nunca muestra el importe (`import_exports.cost_free_message`, con los prefijos conocidos). El costo de una carga de equipos lleva el prefijo «Costo del equipo: ».
- COST-WRITE-AUTHORITY se mantiene: quien no tiene `costs.view` no escribe costos, tampoco al aplicar una vista previa de otra persona.
- Cada lectura usa sólo la empresa de la sesión.

## Fases

| Fase | Alcance | Estado |
|---|---|---|
| B1-0 | ofertas, carga de ofertas, registro guiado (precio), recepción bajo pedido (`clean_money`) | FUSIONADO `9f5410a` (#281), sin desplegar |
| B1-0b-1 | costo del equipo (`stock_unit_services._money`), costo de la línea bajo pedido (como `Decimal`), costo inicial del registro guiado, costo de referencia (`admin_views._clean_reference_cost`), celdas numéricas de la carga de equipos | FUSIONADO `9ab8430` (#285), sin desplegar |
| B1-0b-2 | `import_services.clean_price`: precio al cliente, costo de referencia, costo histórico y costo de la carga de stock | política APROBADA (2026-11-15); sin implementar; espera archivos representativos |
| B1-0b-3 | reembolsos, devoluciones, finanzas (y la validación de reglas en `RulesPanel`) | espera GO propio |
| Restringidos | promociones, POS, nota de débito; pagos, checkout y reservas no se tocan | sólo con autorización expresa |

## B1-0b-2 · Política de moneda aprobada (propietario, 2026-11-15)

- «S/» se acepta **sólo** cuando la moneda configurada de la empresa es PEN.
- «$», «US$», «USD» y cualquier otra moneda escrita («€», «EUR», «soles», «dólares») se rechazan en las cargas mientras no exista un contrato explícito de conversión. El mensaje nombra la moneda.
- Ningún importe se convierte de forma automática.
- Se rechazan los formatos ambiguos, varios importes en una celda y el redondeo silencioso del texto (`5.5555` deja de leerse como 5,56).
- Los `Decimal` internos ya validados no se reinterpretan. Cada campo conserva su precisión: precio y costo de referencia con 2 decimales, costo de stock hasta 4 decimales en su columna.
- Los importes históricos no cambian.

### Fuente de la moneda de la empresa: hallazgo y propuesta

- `CompanySettings.currency` (`models.py`) existe para cada empresa. Es de sólo lectura en la API del panel; sólo se cambia desde el admin de Django (plataforma).
- **No existe una marca de «moneda verificada».** El campo vale `'PEN'` por defecto en el modelo, `settings_views` crea la fila con `'PEN'` cuando falta, y `service_services.company_currency` devuelve el valor por defecto cuando el campo está vacío. Por eso hoy no se distingue un PEN elegido de un PEN por defecto.
- **Propuesta (no inventar un valor por defecto):** B1-0b-2 lee el valor guardado de forma estricta. Si la fila existe y su moneda es `PEN`, «S/» se acepta. Si la fila no existe o el campo está vacío, «S/» se rechaza y se pide el importe sin símbolo. No se usa `company_currency`, porque tiene la vuelta al valor por defecto.
- **Decisión pendiente del propietario:** aceptar un `PEN` guardado como moneda verificada (sólo la plataforma puede cambiarlo), o añadir antes una confirmación explícita por empresa, con migración y GO propios.
- Un importe sin símbolo se sigue leyendo en la moneda de la empresa, como hoy.

## Consecuencias

- Un importe que antes se guardaba mal ahora se rechaza con un mensaje que explica cómo escribirlo. Algunas filas que antes pasaban darán error.
- Las cargas de productos y de stock (B1-0b-2) tocan el **precio al cliente**: riesgo ALTO, PR y GO propios, y pruebas con archivos representativos antes de implementar.
- Los datos ya guardados con una lectura errónea sólo se pueden encontrar con una consulta de producción de sólo lectura, que necesita autorización expresa. Nunca se corrigen de forma automática.
