# Listas semanales de prueba (R1)

Datos inventados. Sólo para «Proveedor Prueba R1» y el modelo «Galaxy Prueba R1».

## Cómo se arma cada archivo

1. Parte siempre de «Descargar plantilla» en Productos › Ofertas de proveedores, panel «Actualizar ofertas desde Excel». El archivo descargado es `ofertas-de-proveedor.xlsx`, con la hoja «Ofertas de proveedor».
2. Copia las filas de la tabla del archivo que estés armando. Deja vacía cualquier columna que no figure en la tabla.
3. Usa las **mismas fechas en los cuatro archivos**:
   - «Vigente desde» = el día de la prueba (`DD/MM/AAAA`);
   - «Vigente hasta» = siete días después.

   Si una vigencia empieza en el futuro y se cruza con la oferta que se vende hoy, el importador la rechaza con un aviso.
4. Guarda cada archivo con su propio nombre:
   - `prueba-r1-parcial-1.xlsx`
   - `prueba-r1-parcial-2.xlsx`
   - `prueba-r1-completa.xlsx`
   - `prueba-r1-completa-vacia.xlsx`

Columnas de la plantilla, en orden: «Proveedor», «Código/SKU del producto», «Modelo», «Capacidad», «Color», «Línea», «Costo pactado», «Precio comercial», «Cantidad confirmada», «Vigente desde», «Vigente hasta», «Plazo (días)», «Activa (Sí/No)», «Bajo pedido (Sí/No)», «Código del proveedor».

Cómo se lee «Cantidad confirmada»:
- vacío = sin confirmar: no se vende;
- `0` = agotada.

## Archivo 1 — `prueba-r1-parcial-1.xlsx` (lista parcial, dos ofertas nuevas)

| Proveedor | Modelo | Capacidad | Color | Línea | Costo pactado | Precio comercial | Cantidad confirmada | Plazo (días) | Activa (Sí/No) | Bajo pedido (Sí/No) |
|---|---|---|---|---|---|---|---|---|---|---|
| Proveedor Prueba R1 | Galaxy Prueba R1 | 128 GB | Negro | Nuevo | 999 | 1199 | 2 | 3 | Sí | Sí |
| Proveedor Prueba R1 | Galaxy Prueba R1 | 256 GB | Negro | Nuevo | 1099 | 1299 | *(vacía)* | 3 | Sí | Sí |

## Archivo 2 — `prueba-r1-parcial-2.xlsx` (lista parcial: cambia un precio y agota la otra)

| Proveedor | Modelo | Capacidad | Color | Línea | Costo pactado | Precio comercial | Cantidad confirmada | Plazo (días) |
|---|---|---|---|---|---|---|---|---|
| Proveedor Prueba R1 | Galaxy Prueba R1 | 128 GB | Negro | Nuevo | 999 | 1249 | 2 | 3 |
| Proveedor Prueba R1 | Galaxy Prueba R1 | 256 GB | Negro | Nuevo | 1099 | 1299 | 0 | 3 |

## Archivo 3 — `prueba-r1-completa.xlsx` (lista completa: sólo la de 256 GB)

| Proveedor | Modelo | Capacidad | Color | Línea | Costo pactado | Precio comercial | Cantidad confirmada | Plazo (días) | Activa (Sí/No) | Bajo pedido (Sí/No) |
|---|---|---|---|---|---|---|---|---|---|---|
| Proveedor Prueba R1 | Galaxy Prueba R1 | 256 GB | Negro | Nuevo | 1099 | 1299 | 1 | 3 | Sí | Sí |

Subido como lista completa, deja fuera la oferta de 128 GB, que se desactiva.

## Archivo 4 — `prueba-r1-completa-vacia.xlsx` (lista completa sin filas)

Sólo los encabezados de la plantilla, sin ninguna fila. Subido como lista completa, debe rechazarse con «La lista completa está vacía: no se desactiva nada con un archivo sin filas.».

## Lista de WhatsApp de prueba

Está en `whatsapp-proveedor-prueba.txt`, en esta misma carpeta. Trae:
- dos equipos nuevos del modelo de prueba, con un solo importe cada uno, que es su costo;
- un teléfono ficticio con forma de celular peruano;
- un accesorio que **no** existe en el catálogo.

IMEI de prueba, ficticios:

| Uso | Valor |
|---|---|
| Válido (pasa Luhn) | `358421073165298` |
| Válido, segundo equipo | `352906118047135` |
| 14 dígitos (debe ofrecer completar el último) | `35842107316529` |
| No válido (falla Luhn) | `358421073165290` |
