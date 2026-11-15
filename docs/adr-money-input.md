# ADR · Importes escritos por personas y archivos (DEC-INV-MONEY-01)

Estado: **B1-0 y B1-0b-1 FUSIONADOS** (sin desplegar). **B1-0b-2 con política, fuente de moneda (D-B1-0b-2c), precisión (D-B1-0b-2d), seguridad (D-B1-0b-2e), decimal explícito (D-B1-0b-2f), precisión por columna (D-B1-0b-2g) y texto con 2 decimales como máximo (D-B1-0b-2h) APROBADAS** (2026-11-15); **sin GO de implementación**: espera 2 o 3 archivos representativos del propietario. **B1-0b-3** y los flujos restringidos esperan su propio GO.

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
| B1-0b-2 | `import_services.clean_price`: precio al cliente, costo de referencia, costo histórico y costo de la carga de stock | política completa APROBADA (D-B1-0b-2a a 2g, 2026-11-15); sin implementar; espera archivos representativos y GO |
| B1-0b-3 | reembolsos, devoluciones, finanzas (y la validación de reglas en `RulesPanel`) | espera GO propio |
| Restringidos | promociones, POS, nota de débito; pagos, checkout y reservas no se tocan | sólo con autorización expresa |

## B1-0b-2 · Política de moneda aprobada (propietario, 2026-11-15; cerrada el mismo día)

- **«S/», «s/» y «S/.»** se aceptan **sólo** cuando la moneda guardada de la empresa es PEN (D-B1-0b-2c). El prefijo va al principio, con o sin espacio, y el resto del texto sigue la política común.
- **Las palabras «PEN» y «soles» se rechazan por ahora**, aunque la empresa facture en PEN.
- «$», «US$», «USD» y cualquier otra moneda escrita («€», «EUR», «dólares») se rechazan en las cargas mientras no exista un contrato explícito de conversión. El mensaje nombra la moneda.
- Ningún importe se convierte de forma automática.
- Se rechazan los formatos ambiguos, varios importes en una celda y el redondeo silencioso del texto (`5.5555` deja de leerse como 5,56).
- Los `Decimal` internos ya validados no se reinterpretan. Cada campo conserva su precisión: precio y costo de referencia con 2 decimales, costo de stock hasta 4 decimales en su columna cuando llega como número (celda numérica o valor ya validado); escrito como texto, 2 (D-B1-0b-2h).
- Los importes históricos no cambian.

### Seguridad de las cargas (D-B1-0b-2e) — APROBADA (propietario, 2026-11-15)

- Se mantienen `costs.view`, COST-WRITE-AUTHORITY (al previsualizar y al aplicar) y el aislamiento por empresa.
- **El tope de cada columna se valida en la vista previa:** precio < 1e8, costo de referencia < 1e10, costo de stock < 1e10. Un importe fuera de rango es un error de fila, nunca un error 500 al aplicar. Hoy esos importes pasan la vista previa y `apply_products` / `apply_stock` responden 500, sin guardar nada a medias.
- **La aplicación sigue siendo atómica** (`@transaction.atomic`): o se aplica el archivo entero o nada.
- **Una vista previa antigua no puede saltarse las validaciones nuevas.** El diseño propuesto, sin migración:
  - el trabajo de carga guarda en su `summary` (JSON) la versión de la política de importes con la que se previsualizó;
  - al aplicar, un trabajo sin esa versión, o con una anterior, se rechaza con el mismo aviso de vista previa desactualizada (`preview_stale`), y la pantalla ofrece subir el archivo otra vez;
  - además, al aplicar se vuelve a comprobar el tope de cada importe guardado, como defensa.
  - Con esto no hace falta esperar a que caduquen las vistas previas (una hora) después del despliegue.
- Ningún registro histórico se modifica.

### Fuente de la moneda de la empresa (D-B1-0b-2c) — APROBADA (propietario, 2026-11-15)

- **Hallazgo:** `CompanySettings.currency` (`models.py`) existe para cada empresa. Es de sólo lectura en la API del panel y sólo se cambia desde el admin de Django (plataforma).
  - No existe una marca de «moneda verificada». El campo vale `'PEN'` por defecto en el modelo, y `settings_views` crea la fila con `'PEN'` cuando falta.
  - `service_services.company_currency` devuelve el valor por defecto cuando el campo está vacío.
- **Decisión:** `CompanySettings.currency` es la **fuente de autoridad operativa provisional** de las cargas.
  - Sólo un `PEN` **efectivamente guardado** permite aceptar «S/», «s/» y «S/.».
  - Si la fila no existe o el campo está vacío, esos prefijos se rechazan y se pide el importe sin símbolo. Las cargas **no** usan `company_currency` ni ningún otro valor por defecto inferido.
  - El valor guardado **no** cuenta como confirmación formal de la empresa. No se crea ninguna migración para verificar monedas.
  - Nunca se convierte. «$», «US$», «USD» y cualquier otra moneda no autorizada se rechazan en el importador actual.
- Un importe sin símbolo se sigue leyendo en la moneda de la empresa, como hoy.

### Precisión por campo (propietario, 2026-11-15)

| Campo | Precisión | Nota |
|---|---|---|
| Precio público (`Product.price`) | 2 decimales | `max_digits=10` |
| Costo de referencia (`Product.reference_cost`) | 2 decimales | `max_digits=12` |
| Costo de stock (`StockMovement.unit_cost`, `BranchStock.average_cost`) | hasta 4 decimales en una celda numérica o en un valor estructurado ya validado; como texto, 2 (D-B1-0b-2h) | `max_digits=14, decimal_places=4` |
| `Decimal` interno ya validado | se conserva su precisión; nunca se vuelve a convertir en texto | — |

- Una entrada de texto nunca se redondea en silencio: un decimal de más es un error de fila.
- Los importes históricos nunca se reinterpretan.

### Decimal explícito (D-B1-0b-2f) — APROBADA (propietario, 2026-11-15)

- «.50» y «,50» se rechazan: se exige «0.50» o «0,50». Por la misma regla, «1500.» y «1500,» también se rechazan.
- Sólo se admiten las cifras 0–9. Se rechazan los dígitos de otros alfabetos («１５００», «١٥٠٠»).
- Se rechaza un símbolo de moneda en cualquier otra posición que no sea el prefijo «S/» permitido («15S/00», «1$500», «1500$»).
- Se rechazan los espacios entre cifras («1 5 0 0», «5 500», también con espacio no separable o fino) y los formatos ambiguos.
- El texto nunca se modifica para volverlo válido: no se quitan símbolos ni espacios y no se completan cifras. Se lee tal cual o la fila da error.

### Precisión por columna (D-B1-0b-2g) — APROBADA (propietario, 2026-11-15); su parte de texto la sustituye D-B1-0b-2h

| Columna | Celda numérica de Excel o valor estructurado ya validado | Texto escrito |
|---|---|---|
| Precio público | 2 decimales | 2 decimales |
| Costo de referencia | 2 decimales | 2 decimales |
| Costo de stock | hasta 4 decimales | **2 decimales** (D-B1-0b-2h) |
| Costo histórico (sólo informe) | 2 decimales, como hasta ahora | 2 decimales |

- Una celda numérica con más decimales que su columna es un error de fila. El mensaje nombra el valor guardado, porque la plantilla oficial muestra 2 decimales (`0.00`) aunque la celda guarde más. Una celda numérica nunca se llama «ambigua».
- El ruido de coma flotante menor que `1e-9` sigue aceptándose (§2).
- Nada se redondea en silencio.
- Los `Decimal` internos ya validados conservan su precisión y nunca se vuelven a leer como texto. El límite interno del costo de stock sigue en 4 decimales.

### Texto con 2 decimales como máximo (D-B1-0b-2h) — APROBADA (propietario, 2026-11-15; opción B)

- **Regla:** todo importe escrito como texto admite como máximo 2 decimales, en todas las columnas. Sustituye la parte de D-B1-0b-2g que admitía 4 decimales en el texto del costo de stock.
- **Por qué:** «13,1234» (un costo de 4 decimales) y «1800,2000» (dos importes pegados, rechazado desde B1-0) tienen la misma forma; con 4 decimales en texto, el segundo se leería en silencio como 1 800,20.
- Un costo de stock con 3 o 4 decimales sólo puede venir de una celda numérica real de Excel o de un valor estructurado ya validado cuyo contrato lo admita.
- Casos obligatorios:

| Entrada | Resultado |
|---|---|
| texto «13,1234» | rechazado: «escribe el costo con 2 decimales (13,12) o usa una celda numérica para 4 decimales» |
| texto «1800,2000» | rechazado: más de 2 decimales o dos importes; escribe un solo importe |
| texto «0,50» | 0,50 |
| texto «.50» | rechazado: «escribe 0.50 o 0,50» (D-B1-0b-2f) |
| celda numérica 13.1234 en el costo de stock | 13,1234 |
| celda numérica 13.1234 en el precio público | rechazado: «la celda guarda 13.1234; el precio lleva a lo sumo 2 decimales» |

- Cada mensaje de error dice cómo corregir la entrada.

## Consecuencias

- Un importe que antes se guardaba mal ahora se rechaza con un mensaje que explica cómo escribirlo. Algunas filas que antes pasaban darán error.
- Las cargas de productos y de stock (B1-0b-2) tocan el **precio al cliente**: riesgo ALTO, PR y GO propios, y pruebas con archivos representativos antes de implementar.
- Los datos ya guardados con una lectura errónea sólo se pueden encontrar con una consulta de producción de sólo lectura, que necesita autorización expresa. Nunca se corrigen de forma automática.
