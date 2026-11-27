# Carga del stock real de equipos (Fase K)

Procedimiento para cargar en el sistema los equipos que la tienda **ya tiene**: nuevos y seminuevos, con su IMEI o su
serie. Lo usan el propietario y el personal de almacén. Explica cómo se llenan las dos plantillas, cómo se previsualiza,
se ensaya, se registra, se comprueba y, si hace falta, se anula una carga entera. Al final está el procedimiento para
hacerlo en producción.

Los nombres entre «comillas» son los que ves en pantalla. La guía general de inventario está en
[guia-personal-inventario.md](guia-personal-inventario.md).

**Si eres el propietario, empieza por §0:** son los pasos completos, en orden. El resto del documento es el detalle.

## 0. Paso a paso para el propietario: cargar el stock real (nuevos y seminuevos) con R15

Esto describe cómo funciona el sistema **desde R15** (`af23e3b`). Con R14 la opción «Ingreso de inventario
existente» no existe y no se carga nada.

Lo marcado «lo confirma el operador» lo hace la persona que opera el servidor (no el propietario): ella corre las
comprobaciones y te envía el resumen del informe para que lo apruebes.

### Paso 1. Comprueba que R15 está en producción

R15 está en producción desde el 2026-11-27 (02:52, Lima). En «Inventario › Más herramientas ›
Carga de equipos», el «Tipo de ingreso» ofrece **tres** opciones: «Compra a proveedor», «Inventario inicial» e
«Ingreso de inventario existente». Si sólo ves dos, R15 no está desplegada: no sigas.

### Paso 2. Descarga las plantillas (después del despliegue)

En «Inventario › Más herramientas › Carga de equipos»:

- **«Stock de equipos nuevos»** para los equipos nuevos.
- **«Stock de seminuevos»** para los seminuevos.

Descárgalas de nuevo aunque tengas otras guardadas: así traen tus sucursales y tu escala de «Estado estético». No uses
la plantilla combinada ni un archivo exportado con «Revisión (IMEI ocultos)».

### Paso 3. Llena una fila por cada equipo físico

Dos teléfonos iguales son dos filas. Escribe desde la fila 3 (la fila 2 es un ejemplo y no se carga). No cambies los
encabezados ni el nombre de la hoja.

| Columna | ¿Obligatoria? | Regla |
|---|---|---|
| Sucursal | Sí | Tal como está en el sistema. |
| Modelo, Capacidad, Color, SIM, Versión (región / país de venta) | Modelo sí; las demás si la variante las tiene | Tienen que nombrar una variante que **ya existe** en «Catálogo › Modelos» y en su línea (nuevo o seminuevo). La carga no crea variantes. |
| IMEI 1 | **Sí, en todo producto marcado «lleva IMEI»** en «Catálogo › Modelos» | 15 dígitos, sin espacios, con su dígito de control. No se repite en ningún equipo de la empresa. Revisa la marca de cada variante en el catálogo antes de llenar: dos variantes de iPhone que no la tenían se marcaron el 2026-11-26. |
| IMEI 2 | No | El segundo IMEI, si el equipo tiene dos. |
| Serie | **Opcional si la fila trae un IMEI válido**; obligatoria si el producto no lleva IMEI | No puede ser el IMEI de otro equipo, ni repetirse en la misma variante. |
| Costo | No | Sólo si tienes «Ver costos y márgenes». Sin ese permiso, una fila con costo es un error. |
| Proveedor | No | Tal como está en «Productos › Proveedores». Para stock que ya tenías puede quedar vacío. |
| Precio de venta (nuevos) | No | Es una **propuesta** para el precio de la variante. Nunca cambia sola (§5.1). |
| Precio de venta (seminuevos) | Recomendado | El precio de **ese** equipo. Sin precio no se vende. |
| Garantía (días) (seminuevos) | No | Vacía = la política de garantía de la empresa; 0 = sin garantía. |
| Estado estético, Salud de batería (%), Estado funcional, Defectos, Accesorios, Detalles para el comprador (seminuevos) | Según el equipo | Ver §2.2. Si «Estado funcional» es «Con fallas», llena «Defectos». |
| Fecha de ingreso, Observaciones (notas internas) | No | Fecha vacía = hoy. |

«Lleva IMEI» (desde R15): no se puede apagar ni cambiar sin «Gestionar productos», y no se enciende mientras un equipo
del producto no tenga IMEI válido. Si una fila no tiene IMEI, no inventes uno: deja ese equipo fuera y avisa.

**Precios y garantías propios de cada equipo** (precio de un seminuevo, «Garantía (días)»): sólo entran si quien
registra tiene «Gestionar productos» (`products.manage`), o si alguien con ese permiso **aprueba** los precios y
garantías de esa carga (§7.1). Sin ese permiso y sin aprobación, el servidor **rechaza** el registro de esa carga,
salvo que quien registra elija «Registrar sin precios ni garantías»: entonces los equipos entran sin precio propio y
con la política de garantía de la empresa, y se completan después en su ficha.

### Paso 4. Separa los archivos por tipo de ingreso

Cada archivo se carga con **un solo** tipo de ingreso. Un **estante** es una sucursal × una variante.

| Tipo de ingreso | Para qué estantes | En el Kardex se lee |
|---|---|---|
| **«Inventario inicial»** | Estantes **sin ningún movimiento** (ni venta, ni compra, ni traslado, ni ajuste). | «Stock inicial» (`initial_stock`) |
| **«Ingreso de inventario existente»** | Estantes **con historial**: ya tuvieron una venta, una compra (también «Caja: llegó el equipo» o un escaneo), un traslado, una reserva, una baja o una entrada/salida manual. | «Ingreso de inventario existente» (aparte de las compras) |

- **Nunca** cargues como «Compra a proveedor» un equipo que ya tenías: no hubo compra.
- **Nunca** uses la caja («Llegó el equipo»), «Registrar equipo por escaneo» ni «Entrada manual» para la carga
  inicial: escriben compra o no imprimen etiquetas.
- Un mismo estante no va en los dos tipos. Si una variante de una sucursal ya tuvo movimientos, todos sus equipos de esa
  sucursal van en el archivo de inventario existente.
- No tienes que adivinar qué estantes tienen historial. La forma más simple: pon todo en archivos de «Inventario
  inicial»; la vista previa (Paso 6) lista los **estantes cerrados** y las filas que rechazaría. Esas filas las
  **mueves** a un archivo aparte de «Ingreso de inventario existente». En la copia de producción del 2026-11-26 21:48
  había 4 estantes de equipos con historial (y 8 de productos sin número de serie). El informe da la lista vigente.
- Nombres de archivo: sólo letras, números, `.`, `_` y `-`, con un número delante que fija el orden. Por ejemplo
  `01-nuevos-tienda.xlsx`, `02-seminuevos-almacen.xlsx` (inventario inicial) y `01-nuevos-tienda-existente.xlsx`
  (inventario existente).

### Paso 5. Entrega los archivos

Los archivos tienen IMEI reales: no los mandes por chat, correo ni issues, y nunca van al repositorio. Se copian sin
abrirlos ni retocarlos al servidor de producción (prod-02), dentro de `/srv/blackdogstore/backups/`:

- archivos de **«Inventario inicial»** → `backups/stock-real-inbox/`
- archivos de **«Ingreso de inventario existente»** → `backups/stock-real-inbox-existente/`

Si no existen, se crean con modo `700` (`mkdir -m 700 …`); el kit avisa si el modo es otro. Ejemplo:
`scp 01-nuevos-tienda.xlsx prod-02:/srv/blackdogstore/backups/stock-real-inbox/`.

### Paso 6. Vista previa en una copia aislada (no toca producción)

Lo confirma el operador. Antes, decide con él el **motivo** de cada tipo de ingreso, exactamente como lo escribirás
en el panel en el Paso 9 (por ejemplo «Stock real diciembre 2026»): el informe y la carga real deben usar el mismo.
El operador corre el kit `backups/stockreal-preview-r15/` (runbook `README-stockreal-preview.md`) una vez por
carpeta, con el usuario **real** que va a cargar en el panel y sus permisos reales:

```sh
STOCK_COMPANY=black-dog-store STOCK_ACTOR=<usuario> STOCK_ENTRY_KIND=opening \
  STOCK_REASON='<motivo exacto del panel>' \
  STOCK_INBOX_ALSO=/srv/blackdogstore/backups/stock-real-inbox-existente \
  nohup sh backups/stockreal-preview-r15/stock_real_driver.sh >/dev/null 2>&1 &
STOCK_COMPANY=black-dog-store STOCK_ACTOR=<usuario> STOCK_ENTRY_KIND=existing \
  STOCK_REASON='<motivo exacto del panel>' \
  STOCK_INBOX=/srv/blackdogstore/backups/stock-real-inbox-existente \
  STOCK_INBOX_ALSO=/srv/blackdogstore/backups/stock-real-inbox \
  nohup sh backups/stockreal-preview-r15/stock_real_driver.sh >/dev/null 2>&1 &
```

La segunda se lanza cuando la primera termina: la **última línea** de
`backups/stockreal-preview-r15/stockreal-driver.log` dice «DRIVER_DONE». El kit toma un backup, lo restaura en una
copia sin salida a la red, previsualiza y registra en la copia como lo haría el panel, comprueba y **deshace todo**.
Sólo acepta producción en R15 exacta.

**Duplicados entre las dos carpetas.** Cada ejecución sólo previsualiza su carpeta, así que no vería un mismo IMEI
escrito en un archivo de cada carpeta. Por eso cada una lee también la otra carpeta con `STOCK_INBOX_ALSO` (sólo sus
IMEI y series, sin previsualizarla ni registrarla) y lista los identificadores que aparecen en las dos. **El kit se
detiene sin hacer nada** si `STOCK_INBOX_ALSO` apunta a una carpeta que no existe o que no tiene ningún `.xlsx`. Si
sólo tienes archivos de una de las dos carpetas, el operador quita esa línea del comando; no hay nada con qué
comparar.

### Paso 7. Lee el informe

Por cada archivo, en este orden:

1. **«ESTANTES CERRADOS PARA INVENTARIO INICIAL»**: los estantes que ya no admiten «Inventario inicial».
2. **«FILAS RECHAZADAS POR ESTANTE CERRADO PARA INVENTARIO INICIAL: n (filas …)»** (vista previa de inventario
   inicial): esas filas pásalas al archivo de inventario existente (Paso 4) y repite la vista previa. Con un solo error,
   ese archivo entero no se registra.
3. **«ERRORES»** (con sus filas): corrige el Excel. Los IMEI salen ocultos (`****1234`).
4. **«SE REGISTRARÍAN por sucursal × variante × línea»**: compáralo con tu **conteo físico**.
5. **«PRECIOS DE VENTA DEL ARCHIVO»** (nuevos): las propuestas de precio de variante. Dime cuáles apruebas.
6. **«PRECIOS/GARANTÍAS PROPIOS DE EQUIPOS»** (seminuevos): si quien carga no tiene «Gestionar productos», hará falta
   tu aprobación en el panel (§7.1).
7. **«DUPLICADOS»** dentro del archivo, entre archivos, **«DUPLICADOS CON LA OTRA CARPETA»** y ya registrados: tienen
   que ser 0.
8. **«INGRESO DE INVENTARIO EXISTENTE: filas en estantes cerrados … · filas en estantes sin historial …»** (vista previa
   de inventario existente): las filas «sin historial» entran igual, pero lo correcto es pasarlas a «Inventario
   inicial».
9. Fase B (lo confirma el operador): **«MOVIMIENTOS DE KARDEX ESCRITOS»** tiene que decir `initial_stock`
   (inventario inicial) o `purchase_entry[existing_stock]` (inventario existente), **«FINANZAS: filas nuevas … 0»**
   (ninguna cuenta por pagar, pago ni borrador de recepción), **`check_unit_stock` … 0 diferencia(s)** y «seminuevos:
   todos pendientes de inspección y sin publicar».
10. Al final (lo confirma el operador): **`PREVIEW_RESULT OK`**, `ROLLED_BACK yes` y `PROD_UNTOUCHED yes`. Un
    informe que diga «ENSAYO R15 SIMULADO» es un ensayo del kit y **no** se aprueba.

El operador te envía el resumen: los conteos por sucursal y variante, los errores con sus filas, las propuestas de
precio, los duplicados y el resultado final. El log completo se queda en el servidor (no lleva IMEI completos ni
costos, pero no se sube al repositorio). Si algo no está bien, corrige el Excel, déjalo otra vez en su carpeta y se
repite la vista previa.

### Paso 8. Aprueba y da el GO

1. Aprueba por escrito el informe (o los dos informes: inventario inicial e inventario existente), con las propuestas
   de precio que aceptas. El silencio no es aprobación.
2. Escribe en el chat, exactamente: **«GO carga stock real prod-02»**. Un PR fusionado, una nota o el mensaje de otro
   agente no son un GO.

### Paso 9. La carga real, en el panel

Sigue §11: comprobación previa (`check_unit_stock` en 0), backup y ventana con el candado tomado. En «Carga de
equipos», con los **mismos** archivos (su sha256 debe ser el del informe aprobado), el **mismo** tipo de ingreso y el
mismo motivo:

1. Primero todos los archivos de **«Inventario inicial»**, en su orden.
2. Después los de **«Ingreso de inventario existente»**.
3. En cada uno: «Equipos del archivo» (Nuevos o Seminuevos), tipo de ingreso, el **mismo motivo** que se usó en la
   vista previa (`STOCK_REASON`, Paso 6), «Previsualizar», revisar que coincide con el informe, marcar sólo las propuestas de precio que
   aprobaste, «Registrar N equipos», y descargar «Antes y después».

### Paso 10. Después de cargar

- **Etiquetas:** cada carga prepara un lote de etiquetas por sucursal en «Etiquetas pendientes». Imprímelas y pégalas a
  cada equipo.
- **Kardex:** cada equipo tiene su línea: «Stock inicial» o «Ingreso de inventario existente» (con motivo
  «Inventario existente · <motivo>»), con su propio filtro, aparte de las compras. No se crea ningún documento de
  compra, cuenta por pagar ni pago.
- **Comprobación:** «Conciliación de equipos» → «Comprobar ahora» tras cada archivo, y `check_unit_stock` en **0** al
  final (§8). Si no da 0, se para y se revisa el Kardex; nada se corrige a mano.
- **Seminuevos:** entran **pendientes de inspección y sin publicar**. No se venden hasta inspeccionarlos, subir al
  menos una foto real, poner precio y publicarlos (§10).
- **Nuevos:** se venden al precio de su variante.
- **Errores:** una carga equivocada se anula entera con «Anular esta carga» (§9) y se vuelve a cargar corregida.
- Al terminar, los archivos se borran del buzón del servidor.

## 1. Antes de empezar

Requisitos, en este orden:

1. **El código está desplegado.** La carga del stock real se hace con **R15** (`af23e3b`): trae «Ingreso de
   inventario existente» (#443) y la protección de «lleva IMEI» (#444), sobre INV-LOAD-01 (#401), USED-READY-01 (#403),
   INV-LOAD-02 e IMPORT-TERMS-APPROVAL (#434). Sin el GO de su despliegue no hay carga real.
2. **El catálogo existe.** Cada equipo entra en una **variante**: un modelo con su capacidad, color, SIM y versión
   (región). La variante debe existir en «Catálogo › Modelos», en su línea (nueva o seminueva), marcada «con número de
   serie» y, si corresponde, «lleva IMEI». La carga **no crea** modelos ni variantes.
3. **Los proveedores existen** en «Productos › Proveedores», si vas a llenar la columna «Proveedor».
4. **Las sucursales existen** y están activas.
5. **Permisos de quien carga** (los decide el servidor, no la pantalla):

| Para | Permiso |
|---|---|
| Descargar las plantillas, previsualizar, registrar y anular una carga | `inventory.adjust`, en todas las sucursales del archivo |
| Llenar «Costo» | `costs.view` (sin él, una fila con costo es un error) |
| Llenar «Proveedor» | `products.view` o `costs.view` (regla D-F-3; sin él, una fila con proveedor es un error) |
| «Precio de venta» y «Garantía (días)» de un seminuevo | `products.manage` (sin él, se ignoran con un aviso en la vista previa). Quien **registra** una vista previa que los trae necesita `products.manage`, **o** la aprobación explícita y aún válida de alguien que lo tenga, **o** registrar «sin precios ni garantías» (§7.1) — #432 y la decisión del 2026-11-25 |
| «Aprobar precios y garantías» de una carga (§7.1) | `products.manage` en la empresa, y alcanzar las sucursales del archivo |
| Confirmar el cambio del precio de una variante desde «Stock de equipos nuevos» (§5.1) | `products.manage` (sin él, la propuesta se ve pero no se aplica; el servidor responde 403) |
| Inspeccionar un seminuevo después | `inventory.units.inspect` |
| Ver la «Conciliación» | `inventory.view` |
| Exportar el stock en el formato de las plantillas, copia de revisión (IMEI ocultos) | `inventory.view` |
| Exportar el stock en el formato de las plantillas con IMEI y series completos | `inventory.view` + `inventory.adjust` |

## 2. Las dos plantillas

Están en «Inventario › Más herramientas › Carga de equipos» (`/admin/inventory/units/import`):

- **«Stock de equipos nuevos»** → `stock-equipos-nuevos.xlsx` (hoja «Equipos nuevos»).
- **«Stock de seminuevos»** → `stock-seminuevos.xlsx` (hoja «Equipos seminuevos»).

«Descargar plantilla» sigue dando la plantilla combinada de siempre, para los archivos que ya existen. Para el stock
real usa las dos nuevas.

Reglas comunes:

- **Una fila es un equipo físico.** Dos teléfonos iguales son dos filas. No hay columna de cantidad.
- **La línea la fija la plantilla.** El nombre de la hoja dice qué carga: no lo cambies. Una fila cuya variante es de
  la otra línea es un error de esa fila. La plantilla de nuevos **rechaza el archivo entero** si le agregas una columna
  de seminuevos («Garantía (días)», «Estado estético», «Salud de batería (%)», etc.). «Precio de venta» está en las
  dos, con un sentido distinto en cada una (§2.1 y §2.2).
- **La fila 2 es un ejemplo.** Su «Serie» empieza por `EJEMPLO`: no se carga aunque la dejes (la vista previa la
  muestra como «omitida»). Escribe tus equipos desde la fila 3.
- **No cambies los encabezados.** Cada uno tiene una nota (pasa el ratón por encima) con lo que va debajo.
- La hoja «Instrucciones» es sólo de lectura: el sistema no la lee.
- «Sucursal» ofrece una lista con **tus** sucursales. «Estado funcional» y «Estado estético» también ofrecen su lista.

### 2.1 Columnas de «Stock de equipos nuevos»

| Columna | Obligatoria | Qué escribir |
|---|---|---|
| Sucursal | Sí (o elígela al subir) | El nombre tal como está en el sistema. |
| Modelo | Sí | El modelo del catálogo, por ejemplo «iPhone 16 Pro». |
| Capacidad | Si el modelo la tiene | «256 GB». |
| Color | Si el modelo lo tiene | El color tal como está en el catálogo. |
| SIM | Si el modelo la distingue | «eSIM», «Física + eSIM»… |
| Versión (región / país de venta) | Si el modelo la distingue | La opción de región de la variante, por ejemplo «USA». |
| IMEI 1 | Si la variante «lleva IMEI» | 15 dígitos, sin espacios. |
| IMEI 2 | No | El segundo IMEI de un equipo con dos líneas. |
| Serie | Ver §3 | El número de serie del fabricante. |
| Proveedor | No | Nombre o RUC, tal como está en Proveedores. |
| Costo | No | Lo que costó ESE equipo, sin IGV ni moneda (1234.50). |
| Precio de venta | No | El precio que esperas para la **variante**, mayor que cero (3499.90). Ver abajo. |
| Fecha de ingreso | No | AAAA-MM-DD o DD/MM/AAAA. Vacía = hoy. Ni futura ni antes de 2000. |
| Observaciones (notas internas) | No | Sólo las ve el personal. Hasta 300 caracteres. |

**«Precio de venta» en equipos nuevos (decisión del propietario, 2026-11-24).** Un equipo nuevo se vende al precio de su
**variante** (Catálogo › Modelos), en caja y en la web. La columna es opcional y **nunca cambia sola** ese precio:

- Vacía: no se propone nada.
- Igual al precio de la variante: no pasa nada.
- Distinta: la fila sale con un aviso (variante, precio actual, precio del archivo y diferencia) y la vista previa
  muestra una propuesta (§5.1). El precio sólo cambia si alguien con `products.manage` la marca y la confirma al
  registrar.
- Dos precios distintos para la misma variante en el archivo: no se propone ninguno.
- Cero, negativo, con más de 2 decimales o ilegible: error de la fila.
- El precio nunca se guarda en el equipo y **el costo nunca se usa como precio**.

### 2.2 Columnas de «Stock de seminuevos»

Las mismas de arriba, más:

| Columna | Qué escribir |
|---|---|
| Precio de venta | (Misma columna que en nuevos, otro sentido.) El precio de ESE equipo, mayor que cero. Sin precio no se vende ni en caja ni en la web. |
| Estado estético | Uno de la escala de la empresa (por ejemplo «Excelente»). |
| Salud de batería (%) | La salud MEDIDA, de 0 a 100. Vacía = «no informado». |
| Estado funcional | «Sin fallas» o «Con fallas» (entonces llena «Defectos»). |
| Defectos | Lo que no funciona o está dañado. Lo lee el comprador. |
| Accesorios | Lo que trae (caja, cargador, cable…). Lo lee el comprador. |
| Garantía (días) | Vacía = la política de garantía de la empresa; 0 = sin garantía. |
| Detalles para el comprador | Rayones, detalles. Hasta 500 caracteres. |

**No hay columnas de inspección, publicación ni fotos.** La carga nunca inspecciona, publica ni adjunta fotos: todo
seminuevo entra **pendiente de inspección y sin publicar** (USED-READY-01). Eso se hace después, en el panel (§10).

## 3. IMEI y número de serie

- El IMEI tiene 15 dígitos y su último dígito es de control: un IMEI mal copiado se rechaza.
- Un IMEI no se repite en **ningún** equipo de la empresa (ni como IMEI 1 ni como IMEI 2).
- Una serie no puede ser el IMEI (1 o 2) de otro equipo de la empresa, ni un IMEI la serie de otro: la caja lee primero
  un código con forma de IMEI como IMEI (SCAN-TO-STOCK-01).
- La serie no se repite dentro de la misma variante. Si ya existe en **otra** variante, la vista previa lo avisa: si es
  el mismo equipo, no lo cargues dos veces.
- En una variante que «lleva IMEI», el IMEI 1 es **obligatorio** y la serie es opcional si la fila trae un IMEI
  válido. En las demás, la serie es obligatoria. #22 «iPhone 15 128Gb Negro» y #23 «iPhone 17 Pro JPN 256Gb Plata»
  llevan IMEI desde el 2026-11-26.
- «Lleva IMEI» (SERIALIZATION-IMEI-GUARD-01, R15): apagarlo o cambiarlo en un producto ya controlado por equipo, también
  quitándole la serialización, exige «Gestionar productos» (403); encenderlo se rechaza mientras algún equipo del
  producto no tenga un IMEI válido (409). La recepción, el alta guiada y la caja nunca lo apagan.
- Un equipo vendido no se vuelve a cargar. Un equipo **anulado** libera su IMEI y su serie. Un equipo **dado de baja**
  NO los libera: sigue ocupando su IMEI (`unique_stock_unit_imei_per_company` sólo excluye «Anulado»). Si un equipo
  dado de baja vuelve a la tienda, no se carga de nuevo con el mismo IMEI: se resuelve en su ficha.
- Formatea las columnas de IMEI y serie como texto (la plantilla ya lo hace): Excel convierte 15 dígitos en
  `3.52819E+14` si la celda es numérica.

### 3.1 Antes de llenar: exportar lo que ya está cargado

Para no escribir dos veces un equipo y para cuadrar el conteo físico, descarga primero lo que el sistema ya tiene. En
«Carga de stock» (`/admin/inventory/import`), bloque «Exportar», elige «Equipos con serie»:

- **«Reporte»**: una fila por equipo (también vendidos y dados de baja, con su estado), con su serie, IMEI e IMEI 2,
  sucursal, variante, precio y notas. Sirve para comparar con el conteo físico y marcar los que ya están.
- **«Para volver a cargar»**: sólo los equipos en estante, con las columnas de la plantilla combinada («Equipos»).
  Subido a la misma tienda no carga nada: cada fila sale como «ya está registrado».

Las dos respetan las sucursales de quien descarga (`inventory.view`). El costo sólo sale con `costs.view`.
**Los IMEI y las series salen completos sólo para quien puede mover inventario (`inventory.adjust`)**; para quien sólo
consulta (`inventory.view`) salen ocultos salvo sus últimos 4 caracteres (`•••••••••••1234`), igual que en el ZIP del
inventario completo y en el reporte integral (STOCK-TEMPLATE-EXPORT, desde 2026-11-24). Un archivo con un IMEI o una
serie oculta **no se puede cargar**. Para el formato de las dos plantillas de stock, ver §3.2. Aunque se te pase un
equipo, la vista previa lo rechaza: el IMEI no se repite en la empresa.

### 3.2 Exportar el stock con el formato de las plantillas (conciliación)

En el mismo bloque «Exportar», debajo de las descargas de siempre, está **«Stock con formato de plantilla»**: lo que hay
en tienda (equipos disponibles o apartados), **una fila por equipo físico**, de tus sucursales, con **las mismas
columnas** que «Stock de equipos nuevos» y «Stock de seminuevos». Cada plantilla ofrece dos archivos:

| Archivo | Quién | Hoja | IMEI y serie | Se puede cargar |
|---|---|---|---|---|
| «Revisión (IMEI ocultos)» → `stock-equipos-nuevos-revision.xlsx` / `stock-seminuevos-revision.xlsx` | `inventory.view` | «Revisión · Equipos nuevos» / «Revisión · Equipos seminuevos» | Ocultos salvo los últimos 4 | **No**: se rechaza entero |
| «IMEI completos» → `stock-equipos-nuevos-completo.xlsx` / `stock-seminuevos-completo.xlsx` | `inventory.view` + `inventory.adjust` | «Equipos nuevos» / «Equipos seminuevos» (la de la plantilla) | Completos | Sí; en la misma tienda no registra nada (cada fila sale «ya está registrado») |

Qué llevan y qué no:

- Sucursal, modelo y opciones de la variante (sólo si al volver a leerlas nombran exactamente esa variante; en la copia
  de revisión, si no, el nombre del producto), IMEI 1, IMEI 2, serie, fecha de ingreso y observaciones; en seminuevos,
  además precio de venta, estado estético, batería, estado funcional, defectos, accesorios, garantía y detalles.
- «Costo», sólo con `costs.view`. «Proveedor», sólo con `products.view` o `costs.view` (la misma regla D-F-3 de la
  plantilla). Sin esos permisos la columna va vacía.
- **Nunca** datos de clientes ni de pedidos, ni el código de etiqueta, la inspección o la publicación. Sólo equipos en
  tienda: ni vendidos, ni dados de baja, ni anulados.
- Cada archivo trae una hoja «Instrucciones» que dice qué es. La copia de revisión lo dice también en el nombre de su
  hoja.

Cada descarga queda en la auditoría (`inventory_exported`): quién, empresa, sucursales, filtros, cuántas filas y si los
identificadores salieron completos (`identifiers: full`) u ocultos (`masked`). Nunca una celda.

**Cómo conciliar el conteo físico** (antes de una carga, después de una tanda o en un conteo periódico):

1. Descarga la **copia de revisión** de la línea que vas a contar (nuevos o seminuevos), de la sucursal que cuentas.
2. Recorre el estante con la lista: cada equipo se reconoce por modelo, opciones y los **últimos 4** de su IMEI o su
   serie. Marca los que están.
3. Lo que está en la lista y no en el estante: búscalo por esos 4 caracteres en «Inventario › Equipos» (quizá está en
   otra sucursal o apartado). Lo que está en el estante y no en la lista: es un equipo sin cargar; va a la plantilla
   vacía, con su IMEI completo leído del equipo.
4. Si hace falta cruzar por IMEI completo, quien tiene `inventory.adjust` descarga la versión **«IMEI completos»**. Es
   un archivo sensible: no se reenvía ni se deja en una carpeta compartida, y su descarga queda auditada.
5. Termina con «Conciliación de equipos» → «Comprobar ahora» (§8): cuadra el stock con los equipos disponibles.

La copia de revisión **nunca** entra en una carga: la carga de equipos rechaza su hoja por el nombre y, aunque se copien
sus filas a una plantilla, rechaza todo el archivo si una celda de IMEI o serie trae «•». El asistente «¿Qué archivo
tengo?» la reconoce como una copia de revisión.

## 4. Inventario inicial, inventario existente o compra

Al subir el archivo eliges el «Tipo de ingreso»:

- **«Inventario inicial»**: lo que la tienda ya tenía, en un estante sin movimientos. Es el caso del stock real.
  Queda en el Kardex como inventario inicial, sin compra ficticia. Se puede cargar **por tandas** (varios archivos)
  mientras el producto, en esa sucursal, sólo tenga líneas de inventario inicial. Una venta, una compra o un traslado
  lo cierran: desde ahí, el stock que ya tenías entra como «Ingreso de inventario existente» (§4.1) y lo que llegue
  comprado, como compra. Anular un ingreso de inventario inicial (un equipo o una carga entera) **no** lo cierra: el
  archivo corregido se vuelve a cargar como inventario inicial.
- **«Compra a proveedor»**: mercadería que llega comprada.
- **«Ingreso de inventario existente»** (STOCK-EXISTING-01): equipos que ya tenías; **no es una compra**. Se acepta en
  cualquier estante y, como una compra, cierra su inventario inicial. Pensado para los estantes cuyo inventario inicial
  ya está cerrado (§4.1); en un estante **sin movimientos** usa «Inventario inicial».

Escribe también el «Motivo para las filas sin motivo», por ejemplo «Inventario inicial diciembre 2026». Queda en el
Kardex de cada equipo.

### 4.1 Estantes con historial: «Ingreso de inventario existente»

El inventario inicial de un estante (sucursal × producto) se cierra en cuanto el estante tiene **cualquier** otra
línea de Kardex: una venta, una compra (también «Caja: llegó el equipo»), un traslado, una reserva o una baja. Desde
ahí, «Inventario inicial» se rechaza con «ya tiene movimientos… no es inventario inicial». Antes, el stock real que
faltaba sólo podía entrar como «Entrada por compra», que se leía como una compra que no existió.

Para esos estantes elige **«Ingreso de inventario existente»**. Está en las tres puertas que ya preguntan «compra o
inventario inicial»: la carga por Excel de equipos, «Registrar equipos» y «Recibir mercadería». El rechazo de
«Inventario inicial» ya lo nombra («Regístralo como «Ingreso de inventario existente»»).

- Se acepta en **cualquier** estante, tenga historial o no (decisión del propietario: así un Excel que mezcla
  estantes nuevos y con historial no hay que partirlo). Como una compra, **cierra** el inventario inicial de ese
  estante: en un estante sin movimientos se recomienda «Inventario inicial». Las reglas del equipo no cambian: IMEI válido y no repetido, una serie
  nunca es el IMEI de otro equipo vivo (R1), costo sólo con «Ver costos y márgenes».
- En el Kardex se lee «Ingreso de inventario existente», con su propio filtro (también en la API v1,
  `movement_type=existing_stock`); el filtro «Entrada por compra» ya no lo lista. El motivo empieza con «Inventario
  existente» (Excel: «Inventario existente · <motivo>»). El gráfico de movimientos por tipo y la vista previa del Excel lo cuentan **aparte** de las compras
  («N de inventario existente»).
- Por dentro sigue siendo una entrada (`purchase_entry`) marcada con `metadata.entry_kind = 'existing_stock'`: sin
  migración, y el costo, las etiquetas, la anulación por equipo o por carga y la corrección funcionan como en una
  compra. **No** crea documento de compra, cuenta por pagar ni asiento: tampoco lo hacía la «Entrada por compra».
- Los permisos son los de una compra («Mover inventario»). Precio de venta y garantía propios de cada equipo siguen
  pidiendo «Gestionar productos» (o su aprobación, en el Excel), como en cualquier ingreso.
- No uses «Entrada manual» (Movimientos) para esto: una entrada manual no imprime etiquetas.
- Después de cargar, `check_unit_stock` debe salir con 0 como siempre (§8).

Las líneas «Entrada por compra» escritas antes de esta opción no cambian: no se reescribe historial.

## 5. Previsualizar y leer el resultado

1. Elige el archivo y, **siempre**, «Equipos del archivo»: «Nuevos», «Seminuevos» o «Ambos» (sólo para la plantilla
   combinada, cuya columna «Línea» lo dice en cada fila). Si el archivo es una de las dos plantillas, debe coincidir
   con ella. Elige también el tipo de ingreso y el motivo, y la sucursal si el archivo no la dice.
   Una fila con datos de seminuevo (precio, garantía, estado…) en un archivo que no dice su línea es un **error**:
   nunca entra como equipo nuevo por defecto.
2. Pulsa «Previsualizar». **No registra nada.**
3. Lee «Lo que pasará con cada fila». Arriba sale la plantilla detectada («plantilla de equipos nuevos» o «de
   seminuevos»). Primero salen las filas con error, después las que tienen un aviso.
4. **Errores** (en rojo): mientras haya uno, no se registra ningún equipo. Pulsa «Descargar errores (CSV)», corrige el
   Excel y vuelve a subirlo. Los más comunes:
   - «no tiene esa variante»: falta la variante en el catálogo, o la fila es de la otra línea;
   - «No existe la sucursal…»;
   - IMEI inválido o repetido (en el archivo o en el sistema);
   - costo o proveedor sin el permiso (§1).
5. **Avisos** (en gris): se registra igual. Léelos: «Fila de ejemplo de la plantilla», serie repetida en otra variante,
   IMEI en un producto sin «lleva IMEI».
6. **«Resultado si registras el archivo»**: por sucursal y producto, los equipos disponibles y el stock antes → después,
   y cuántos movimientos de inventario inicial, de compra y de inventario existente escribirá. Compáralo con tu conteo
   físico.

Una vista previa caduca a los 60 minutos: si caducó, súbela otra vez.

### 5.1 «Precios de venta del archivo» (sólo «Stock de equipos nuevos»)

Si alguna fila trae un «Precio de venta» distinto del de su variante, la vista previa añade una tabla, una fila por
variante:

- precio actual («Sin precio» si la variante aún no lo tiene) y precio(s) del archivo;
- diferencia;
- equipos de esa variante que entran con el archivo y los que ya hay disponibles en las sucursales del archivo (el precio cambia también en las demás);
- si la variante se vende en la tienda, si no está publicada o si es un **borrador sin habilitar**;
- la propuesta: «Actualizar el precio de la variante a S/ X».

Sólo quien tiene `products.manage` ve la casilla. Sin ese permiso la propuesta se lee pero no se aplica. Con dos
precios distintos para la misma variante no hay propuesta: corrige el archivo o cambia el precio en Catálogo › Modelos.

Cambiar el precio de una variante **no la habilita para la venta ni la publica**: un borrador sigue siendo borrador.

## 6. Ensayo con datos sintéticos

Antes de la carga real se ensaya el recorrido completo con datos **inventados** (IMEI sintéticos con dígito de
control válido, nunca de un equipo real).

**Prueba automática.** `store.test_stock_load.SyntheticStockLoadTest` descarga las dos plantillas, las llena con
equipos sintéticos en dos sucursales, previsualiza, registra como inventario inicial, corre `check_unit_stock`, anula
por lote una compra equivocada, vende un equipo nuevo en caja y dos seminuevos: uno en caja (sólo después de aprobar
su inspección) y otro en la web (después de aprobarlo, fotografiarlo y publicarlo). Se corre con:

```sh
cd backend
DATABASE_URL=postgres://postgres:postgres@localhost:5432/blackdog_ensayo python3 manage.py test store.test_stock_load --noinput
```

**Simulación completa (STOCK-REAL-READY + STOCK-REAL-SIM-2).** `store.test_stock_real_simulation` recorre en 19
escenarios, con las vistas reales y permisos reales:

1. la primera carga de nuevos y seminuevos;
2. una segunda tanda del mismo modelo;
3. el mismo archivo otra vez, también con dos vistas previas antes de registrar;
4. un IMEI repetido en el archivo, uno ya registrado y uno con dígito de control inválido;
5. una variante que la paleta admite pero no existe (error que dice «Créala en Catálogo › Modelos»; creada, el
   archivo entra);
6. una variante fuera de la paleta;
7. una recepción de proveedor;
8. la venta en caja de ese equipo exacto;
9. un seminuevo pendiente, que no sale en la web, no entra al carrito y la caja rechaza (409);
10. inspección, foto subida y confirmada y publicación, con lo que sale en la web (y no en la de otra empresa);
11. la venta web del equipo exacto. La vista del checkout no se usa, porque abre el pago con el proveedor: sus
    servicios crean el pedido y el pago se confirma con el `_confirm` real de una notificación ya verificada;
12. el Kardex neto igual al stock de cada estante;
13. el aislamiento entre sucursales y entre empresas;
14. inventario inicial en cuatro tandas en dos sucursales (nuevos y seminuevos) y la copia de revisión (IMEI ocultos)
    como conciliación: sus filas por sucursal cuadran con lo cargado; esa copia vuelta a subir, y sus filas pegadas en
    una plantilla, se rechazan sin preparar ninguna carga;
15. «Precio de venta» en la plantilla de nuevos (§5.1): precio igual, sin propuesta; distinto, con propuesta; registrar
    sin confirmar deja la variante como estaba; confirmar sin `products.manage` es 403 y no escribe nada; con
    `products.manage` y confirmación el precio cambia y queda en la auditoría; una vista previa vencida porque otro
    cambió el precio es 409 y no escribe nada; el costo nunca se vuelve precio;
16. seminuevos con y sin precio en la misma variante: se habilita con uno con precio, la caja vende el que tiene precio
    e inspección y rechaza el que no tiene precio, la web ofrece sólo el que está listo;
17. «Registrar equipo», la venta inmediata en caja con nota de venta y su reimpresión (ticket 80 mm y A4). La
    reimpresión del panel, como en producción, marca `pdf_generated_at` y deja una línea `sales_note_pdf_downloaded`
    por descarga; la simulación lo comprueba y lo deshace en una transacción anidada, así que no deja nada. Sólo el
    dibujo puro (`render_sales_note_pdf`, `ticket_pdf_from_ctx`) no escribe nada;
18. el archivo completo (`inventory.adjust`) vuelto a subir a la misma tienda: cada fila «ya está registrado», nada
    creado;
19. la conciliación final: `check_unit_stock` con 0 diferencias y, en cada estante, stock = equipos disponibles =
    Kardex neto.

Cada escenario compara el stock y el Kardex antes y después; del 14 al 19, también la auditoría de la empresa, por
acción. Con `-v 2` imprime la bitácora:

```sh
DATABASE_URL=postgres://postgres:postgres@localhost:5432/blackdog_ensayo python3 manage.py test store.test_stock_real_simulation --noinput -v 2
```

El motor de esa prueba (entre las marcas `PORTABLE DRIVER`) sólo usa código de producción. Copiado en
`sim_stock_real.py` (kit de preparación, fuera del repositorio), corre con `manage.py shell < sim_stock_real.py`
dentro de la imagen sobre una **copia aislada**:

- se niega si `SIM_ISOLATED_DB` no nombra la base conectada;
- crea una empresa sintética única;
- trae sus propios ajustes (`run_all`), así que corre igual dentro y fuera de las pruebas:
  - guarda las fotos en una carpeta temporal que borra al terminar, nunca en el volumen de evidencias (deshacer la
    base no borra un archivo);
  - admite los dominios sintéticos;
  - apaga SUNAT, el correo y WhatsApp;
- deshace la base al terminar y comprueba que la carpeta temporal ya no existe.

**En navegador (Playwright).** `frontend/e2e/stock-real-new-used.spec.ts` recorre lo mismo con la interfaz, en
escritorio (1280×800) y en un móvil de 390×844: plantilla de nuevos con un precio distinto → propuesta → registrar sin
actualizarlo → venta en caja por escaneo → ticket; plantilla de seminuevos → el equipo aparece pendiente en
«Seminuevos» (tarjetas en el móvil) → inspección, foto, confirmación de la foto, precio y publicación. Se corre en el
entorno E2E de `AGENTS.md` §6.

**Ensayo sobre una copia aislada** (opcional, con las plantillas ya llenas con datos reales o sintéticos). Nunca sobre
producción:

```sh
createdb -h localhost -U postgres blackdog_ensayo_carga
gunzip -c backups/db-AAAAMMDD-HHMMSS.sql.gz | psql -h localhost -U postgres blackdog_ensayo_carga
cd backend
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/blackdog_ensayo_carga DEBUG=True
python3 manage.py migrate
python3 manage.py check_unit_stock --company <slug>          # debe salir con 0 ANTES de cargar
python3 manage.py runserver 127.0.0.1:8100
# en otra terminal: frontend con NEXT_PUBLIC_API_URL=http://127.0.0.1:8100/api en :3002
```

En el panel de la copia: previsualizar, registrar, `check_unit_stock`, «Antes y después», «Anular esta carga» y volver a
comprobar. La copia tiene datos personales de clientes: queda en el equipo de quien ensaya y se borra al terminar
(`dropdb blackdog_ensayo_carga`). Nunca se sube al repositorio.

## 7. Registrar

Con 0 errores y el resultado revisado, pulsa «Registrar N equipos». Entra **todo el archivo o nada**: si algo cambió
desde la vista previa (otro registró el mismo IMEI, se desactivó una sucursal), no se carga ninguno y el mensaje dice la
fila. Si la respuesta es «Otra operación estaba usando este stock. Vuelve a intentarlo», nada se escribió: reintenta.

Si marcaste alguna propuesta de precio (§5.1), antes de registrar la pantalla pide confirmarla: lista cada variante con
su precio actual y el nuevo. Al confirmar, los equipos y los precios se registran juntos, todo o nada. El servidor
vuelve a comprobarlo:

- sin `products.manage`: 403, no se registra nada;
- un precio que no es el de la propuesta de esa vista previa: 400, no se registra nada;
- si el precio de la variante cambió desde la vista previa: 409 («cambió desde la vista previa»), no se registra nada;
  sube el archivo otra vez;
- una carga ya registrada no vuelve a aplicar su propuesta.

Cada precio cambiado queda en la auditoría como `product_updated`, con el precio anterior y el nuevo, `via:
stock_import` y el número de la carga. La línea de la carga (`stock_units_import_applied`) dice cuántos precios cambió.

Después de registrar aparecen:

- «Antes y después (CSV)» y «Antes y después (Excel)» (también, más tarde, en «Cargas registradas»): por sucursal y producto, el stock antes, lo que entró, lo que
  la vista previa esperaba, lo que quedó al registrar, si **cuadra** («Sí»/«No») y el stock de hoy. Guárdalo con el
  archivo cargado.
- «Anular esta carga» (§9).

Se preparan sus etiquetas como en cualquier recepción (un lote por sucursal).

### 7.1 Precios y garantías de seminuevos: aprobación o ingreso sin ellos

Decisión del propietario (2026-11-25): los equipos pueden entrar siempre, pero los precios y garantías propios del
archivo nunca se aplican sin la autoridad de `products.manage`. Si la vista previa los trae (sólo pasa cuando la
preparó alguien con ese permiso; sin él se ignoran en la vista previa, §1), la pantalla muestra «Precios y garantías del
archivo» con quién preparó la carga y el enlace que la vuelve a abrir (`…/units/import?job=<n>`):

- **Quien administra productos** puede registrarla directamente, o pulsar «Aprobar precios y garantías» para que la
  registre otra persona sin ese permiso. Puede aprobar su propia carga (queda dicho en la auditoría).
- **Quien no administra productos**, sin aprobación válida, no ve «Registrar N equipos». Tiene dos caminos:
  «Registrar sin precios ni garantías» (los equipos entran sin precio propio y con la política de garantía de la empresa; se
  completan después en la ficha) o compartir el enlace con quien administra productos y volver a abrirlo cuando apruebe.
  Con la aprobación válida registra la carga con sus precios y garantías.

El servidor vuelve a comprobarlo al registrar:

- sin aprobación: 403 `prices_forbidden`, no se registra nada;
- si los precios o garantías cambiaron desde la aprobación: 409 `terms_approval_stale` (la aprobación está atada a
  una huella sha256 de fila, variante, serie, IMEI, precio y garantía de cada equipo);
- si quien aprobó ya no tiene `products.manage` en la empresa, o ya no alcanza todas las sucursales de la carga: 403,
  hace falta otra aprobación;
- la aprobación cubre sólo los precios y garantías **de cada equipo**: cambiar el precio de una variante (§5.1) sigue
  exigiendo `products.manage` a quien registra.

Quien no administra productos ve qué filas traen precio o garantía y la aprobación, pero no los valores: ni al reabrir
la carga, ni en el historial, ni en la respuesta del registro o de la anulación, ni en el CSV de errores (los avisos que
nombran el precio del archivo se le muestran sin el importe).

Auditoría: `unit_import_terms_approved` (carga, filas con precio o garantía, inicio de la huella, quién preparó, si fue
su misma persona) y, en `stock_units_import_applied`, `prepared_by_id`, `terms_approved_by_id` y `terms_digest` (si
una aprobación válida los cubre, registre quien registre), `executed_by_id`,
`commercial_terms` (`applied`, `dropped` o `none`) y `terms_authority` (`applier` o `approval`).

## 8. Comprobar: `check_unit_stock` y «Conciliación»

La regla: en cada sucursal, el stock de un producto con serie es igual a sus equipos disponibles.

- **En el panel:** «Conciliación de equipos» → «Comprobar ahora», en la misma pantalla de la carga. Muestra sólo los
  estantes que no cuadran, dentro de tus sucursales. Sólo lee: no corrige nada.
- **En el servidor:** `python manage.py check_unit_stock [--company <slug>]`. Sólo lee. Sale con **0** si todo cuadra
  y con **2** si hay diferencias, y las nombra (empresa, sucursal, producto, stock, disponibles, diferencia). No
  imprime IMEI, series ni costos.

Una diferencia no se «arregla» a mano: primero se busca la causa en el Kardex del producto.

## 9. Anular una carga entera

Para una carga equivocada (archivo duplicado, sucursal equivocada, tipo de ingreso equivocado):

1. En el resultado de la carga, pulsa «Anular esta carga». Si ya saliste de esa pantalla: en la misma página,
   «Cargas registradas» → «Ver cargas registradas», y en la fila de la carga, «Anular esta carga».
2. El sistema dice antes de nada cuántos equipos anularía y cuáles lo impiden. Si alguno ya se **inspeccionó, publicó
   o tiene fotos**, lo dice con sus cantidades y pide marcar «Entiendo que se anulan también…»: ese trabajo se pierde.
3. Escribe el «Motivo de la anulación» (obligatorio, hasta unos 235 caracteres) y pulsa «Anular N equipos».

Qué hace:

- Cada equipo de **esa** carga sale del stock con una línea de corrección en el Kardex («Anulación de ingreso (Carga
  por archivo anulada): Carga #N: …»). **No se borra nada**: el ingreso y la corrección quedan los dos.
- Los equipos quedan «Anulado» y su IMEI y su serie quedan libres para cargarlos bien.
- **Todo o nada:** si un solo equipo ya se vendió, se apartó, está en un carrito, se trasladó, está en una
  transferencia en borrador, cambió de variante o se dio de baja, no se anula ninguno, y la pantalla lista esos equipos por su código de etiqueta. Resuélvelos uno por
  uno («Anular ingreso» en su ficha) o deja la carga.
- Repetir la anulación no hace nada más. Queda en la auditoría con la empresa.
- Una carga registrada antes de INV-LOAD-02 no guarda la lista de sus equipos: el sistema no la adivina y pide anular
  equipo por equipo.

Después, `check_unit_stock` debe seguir saliendo con 0.

## 10. Después de cargar seminuevos: inspeccionar, fotografiar y publicar

Todo seminuevo cargado queda **pendiente de inspección y sin publicar**. En «Inventario › Seminuevos», en la ficha de
cada equipo:

1. **Inspección** (permiso `inventory.units.inspect`): aprobar o rechazar (rechazar pide una nota). Sin inspección
   aprobada **no se vende en caja ni en la web**.
2. **Fotos:** subir al menos una foto real del equipo. La web no ofrece un seminuevo sin foto.
3. **Precio:** debe tener «Precio de venta» mayor que cero.
4. **Publicar** (permiso `products.manage`): sólo con precio, inspección aprobada y al menos una foto. Se puede
   despublicar en cualquier momento.

En caja hacen falta el precio y la inspección aprobada; en la web, las cuatro cosas.

**Cada equipo responde por sí mismo** (USED-UNIT-OWN-GATE-01). Un seminuevo sin precio, sin inspección, sin foto o
sin publicar no se vende **él**, y nada más: los otros equipos de la misma variante que cumplen todo se venden en
caja, se ofrecen en la web y se pueden apartar en un carrito. En concreto:

- «Habilitar para la venta» una variante seminueva creada desde la paleta pide **al menos un** equipo disponible con
  precio, no un precio en cada equipo.
- La caja cuenta como disponibles sólo los equipos con precio e inspección aprobada de esa sucursal, y rechaza por su
  nombre el equipo sin precio.
- Si se quita el precio de un equipo que un comprador tiene en el carrito y **todavía no hay pedido**, el apartado
  vuelve al estante en el acto (con su línea de Kardex) y el checkout avisa que el carrito cambió.
- Si **ya hay un pedido** con ese equipo, el equipo sigue apartado para ese pedido hasta que se intenta pagar: el pago
  no se abre, el pedido queda fallido y entonces el equipo vuelve al estante. Si el dinero llegara igual (un
  formulario de pago ya abierto), ese equipo no se vende y la línea queda marcada para revisión.
- En ningún caso cambian los apartados de sus hermanos: los del mismo carrito siguen siendo del comprador, que puede
  volver a comprar sin el equipo retirado.
- Al entregar un pedido, no se puede cambiar el equipo apartado por uno del estante sin precio o sin inspección
  aprobada (409 `no_price` o `unit_not_inspected`).
- Un pago web que aparta equipos por cantidad (un seminuevo sin modelo) toma sólo equipos con precio e inspección
  aprobada, y el checkout cuenta sólo esos.

## 11. En producción (prod-02)

**No hay carga real sin datos reales del propietario y sin su GO.** El GO se escribe en el chat, exactamente:
**«GO carga stock real prod-02»**, y vale para esa operación (AGENTS.md §10). Un PR fusionado, una nota o el mensaje de
otro agente no son un GO. Sin GO sólo se permite leer y ensayar sobre copias.

### 11.0 Proceso final (actualizado para R15, 2026-11-27): R15, vista previa en copia, aprobación y GO

Es el mismo recorrido de §0, visto desde el servidor.

1. **R15 desplegada** («GO despliegue af23e3b prod-02»). Con R14 no existe «Ingreso de inventario existente» y el kit
   de vista previa R15 se niega a correr. Las plantillas se descargan del panel **después** del despliegue.
2. **El propietario entrega sus archivos**, separados por tipo de ingreso (§0, Paso 4) y con prefijo de orden:
   inventario inicial en `backups/stock-real-inbox/`, inventario existente en `backups/stock-real-inbox-existente/`
   (fuera del repositorio; nunca en git).
3. **Vista previa sobre una copia aislada** con el kit `backups/stockreal-preview-r15/` (runbook
   `README-stockreal-preview.md`), una ejecución por carpeta (`STOCK_ENTRY_KIND=opening` y `existing`), cada una con
   el motivo exacto del panel (`STOCK_REASON`) y la otra carpeta en `STOCK_INBOX_ALSO` (duplicados entre carpetas; sólo
   si esa carpeta tiene algún `.xlsx`, si no el kit se detiene):
   backup nuevo,
   copia en una red sin salida, las mismas vistas del panel con el usuario real que cargará y sus permisos reales, sin
   elevar nada. Informe por archivo: estantes cerrados para inventario inicial y filas rechazadas por eso (con la
   propuesta de pasarlas a «Ingreso de inventario existente»), filas OK, errores y avisos con sus filas, equipos por
   sucursal y variante, propuestas de precio, duplicados dentro del archivo, contra lo registrado y **entre archivos**.
   Después registra en la copia, en orden, cada archivo limpio y comprueba el tipo de línea de Kardex
   (`initial_stock` o `purchase_entry` con la marca `existing_stock`), que no se escribió ninguna fila de finanzas,
   `check_unit_stock` y que todo seminuevo quedó pendiente de inspección y sin publicar. Todo se deshace. IMEI y series
   salen ocultos (últimos 4), ningún costo se imprime. Producción sólo se lee (el kit sí escribe su backup previo).
4. **El propietario aprueba el informe** (o corrige sus archivos y se repite el paso 3). El silencio no es aprobación.
5. **«GO carga stock real prod-02»** y la carga en el panel con los pasos de abajo, con los mismos archivos (su sha256
   debe coincidir con el del informe aprobado).

No se registra inventario ficticio en producción. Las 19 situaciones de `store.test_stock_real_simulation` se conservan
como prueba automática. Los seminuevos conservan su identificación individual y siguen §10: inspección, fotos reales,
precio y publicación controlada.

Pasos, en `/srv/blackdogstore`:

1. **Estado real.** `git rev-parse HEAD`, `showmigrations store` y las imágenes en marcha: el código desplegado debe
   ser R15 `af23e3b` (imagen `release-af23e3b`). `sh backups/ops/prod_lock.sh --status`: el candado debe estar libre.
2. **Comprobación previa** (sólo lectura; `$C` es el `docker compose …` de
   [despliegue-produccion.md](despliegue-produccion.md)):
   `$C exec -T backend python manage.py check_unit_stock --company <slug>` → debe salir con **0**. Si sale con 2, se
   para y se investiga antes de cargar nada.
3. **Copia de seguridad bajo el candado:**
   `sh backups/ops/prod_lock.sh "<quién> · backup carga stock real · <sha>" sh deploy/backup.sh`.
   Anotar el nombre del `db-*.sql.gz`.
4. **Ventana de carga con el candado tomado** (impide un despliegue, una migración o un backup mientras se carga; no
   bloquea el panel):
   `sh backups/ops/prod_lock.sh "<quién> · carga stock real · <sha>" sh -c 'echo "Carga en curso. Pulsa Enter al terminar."; read _'`
5. **Cargar en el panel**, por tandas pequeñas (por ejemplo, una sucursal y una línea por archivo): primero los
   archivos de «Inventario inicial», después los de «Ingreso de inventario existente», cada uno con el tipo de ingreso
   y el motivo de su informe aprobado: previsualizar, revisar, registrar, descargar «Antes y después». Tras cada tanda,
   «Conciliación» → «Comprobar ahora».
6. **Comprobación final:** `check_unit_stock --company <slug>` → **0**. Soltar el candado (Enter).
7. **Si una tanda salió mal:** «Anular esta carga» (§9) y volver a cargar el archivo corregido. Restaurar la copia
   (`deploy/restore.sh`) es el último recurso y necesita su propio GO: borraría también las ventas hechas después.
8. **Constancia:** quién, cuándo, el GO, la copia previa, cuántos equipos por tanda y el resultado de
   `check_unit_stock`, en `02_ESTADO_ACTUAL.md` (historial) y en `docs/NEXT_ACTION.md`.
9. **Seminuevos:** inspeccionar, fotografiar y publicar (§10). Hasta entonces no se venden.

## 12. Si algo sale mal

| Qué ves | Qué hacer |
|---|---|
| «La plantilla de equipos nuevos no lleva la columna…» | Quita esa columna o usa la plantilla de seminuevos. |
| Aviso «Precio del archivo … distinto del de la variante» | No es un error: el precio no cambia. Si quieres cambiarlo, quien administra productos marca la propuesta y la confirma. |
| «El precio de … cambió desde la vista previa» | Otro cambió el precio de la variante: nada se registró. Sube el archivo otra vez. |
| «Este archivo es la plantilla de … y elegiste cargar …» | Sube la plantilla de la línea correcta. |
| «… no tiene esa variante» | Crea la variante en Catálogo › Modelos (o corrige capacidad, color, SIM o versión). |
| «Inventario inicial» rechazado por movimientos | Ese producto ya tuvo una venta, compra o traslado en esa sucursal: pasa esas filas a un archivo de «Ingreso de inventario existente» (§0, Paso 4). No como compra. |
| «"…" lleva IMEI: indícalo para cada equipo» | El producto lleva IMEI: escribe el IMEI 1 del equipo (15 dígitos). La serie sola no basta. |
| «Otra operación estaba usando este stock» | Nada se escribió: vuelve a intentarlo. |
| La conciliación muestra diferencias | No corrijas a mano: revisa el Kardex del producto y avisa al responsable. |
| «No se anuló ningún equipo» | Algún equipo de la carga ya se movió: la lista dice cuál. |

## 13. Un equipo suelto: «Registrar equipo por escaneo»

Para un equipo que llega solo (no un archivo), **Inventario › Centro de operaciones › «Registrar equipo por escaneo»**
(`/admin/inventory/scan`, permiso «Ajustar inventario»). Diseño y reglas: `docs/adr-scan-to-stock.md`.

1. Elige la **sucursal** y si es **nuevo** o **seminuevo**.
2. Elige el **modelo** y la **variante**, o lee el **código comercial** (UPC/EAN) de la caja: elige la variante, nunca
   es un equipo.
3. Lee con el lector (o escribe y pulsa Enter) el **IMEI 1**, el **IMEI 2** y la **serie**. La pantalla dice qué espera.
   Un EID (32 dígitos) o un MEID nunca se toman por IMEI; un código dudoso se pregunta («¿Es la serie?»), también 15
   dígitos que parecen un IMEI cuando se espera la serie; una lectura repetida se ignora. El lector puede terminar con
   Enter (siempre) o Tab (si se elige en la pantalla; sólo con un código entero escrito). Lo escrito a mano se lee con
   Enter o «Leer»; Esc vacía el campo.
4. Proveedor y costo (con permiso); en un seminuevo, batería, estado, defectos y accesorios; precio y garantía sólo con
   «Administrar productos».
5. Si un **pedido pagado** espera esa variante en la sucursal, primero se elige: «Es del pedido #N» o «Ingresar como
   stock» con un motivo.
6. **«Confirmar ingreso».** Ninguna lectura registra nada sola. El Kardex dice «Inventario: registro por escaneo»; la
   etiqueta interna del equipo queda lista en «Etiquetas pendientes».

Las reglas de §3 valen igual: un IMEI no se repite en la empresa; un equipo vendido o dado de baja sigue ocupando su IMEI;
uno anulado lo libera. Una serie no puede ser el IMEI de otro equipo de la empresa, ni un IMEI la serie de otro (en todas
las puertas, también la carga masiva). Un seminuevo entra **pendiente de inspección y sin publicar** (§10).
