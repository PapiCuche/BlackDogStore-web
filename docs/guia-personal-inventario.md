# Guía de inventario para el personal de tienda

Esta guía explica cómo registrar mercadería en el panel de Black Dog Store, cómo corregir un ingreso y cómo
trasladar equipos entre sucursales. Está escrita para quien atiende la tienda o el almacén.

Los nombres entre «comillas» son los que ves en pantalla: botones, menús y campos.

Para cargar de una vez el **stock real** de la tienda (equipos nuevos y seminuevos que ya tienes), sigue
[carga-stock-real.md](carga-stock-real.md): las dos plantillas, el ensayo, la comprobación y cómo anular una carga.

Todo lo de inventario está en el menú **Inventario**:

- «Centro de operaciones»
- «Recibir mercadería»
- «Stock y equipos» → «Equipos nuevos», «Equipos seminuevos»
- «Movimientos» → «Kardex», «Transferencias»
- «Más herramientas» → «Gestión masiva», «Carga de equipos», «Carga de stock», «Costos y precios»

## 1. Antes de empezar

1. **Mira la sucursal.** En las pantallas de consulta (por ejemplo «Equipos», «Equipos seminuevos» o «Costos y
   precios») hay un selector de sucursal arriba a la derecha. Muestra el nombre de la sucursal o «Todas las
   sucursales». Lo que ves es el stock de esa selección.
2. **Al recibir, la sucursal se elige otra vez.** En «Recibir mercadería», el campo «Sucursal que recibe» decide
   dónde entra la mercadería. Revísalo siempre.
3. **Permisos.** Para recibir necesitas «Mover inventario» y «Ver productos». Para poner precio o crear un artículo nuevo al recibir, también «Administrar productos». Si una pantalla te dice que no
   tienes permiso, pídelo a quien administra el sistema. No uses el usuario de otra persona.
4. **Costos.** Sólo quien tiene el permiso «Ver costos y márgenes» ve y escribe costos. Si no lo tienes, los
   campos de costo no aparecen y la pantalla dice que el costo lo registra otra persona. Es normal.
5. **Empieza con poco.** La primera vez que uses una función nueva, prueba con un lote real y pequeño (uno o dos
   equipos de verdad), con autorización del responsable de tienda.
6. **Nunca registres productos ni equipos de prueba en el sistema.** Todo lo que entra queda en el Kardex y no
   se borra. No inventes series ni IMEI «para probar».

## 2. Registrar equipos nuevos (con serie o IMEI)

1. Entra a «Recibir mercadería». El asistente tiene cuatro pasos: «Procedencia», «Artículos», «Revisar y
   confirmar» y «Terminado».
2. En «Procedencia»:
   - en «Tipo de ingreso», elige «Compra a proveedor» (lo normal; ver la sección 6);
   - elige la «Sucursal que recibe»;
   - si quieres, completa «Proveedor (opcional)», «Referencia del documento (opcional)» (factura o guía) y
     «Observaciones (opcional)».
3. Pulsa «Siguiente: artículos».
4. En «Buscar el artículo que llegó», escribe el nombre o escanea el código de barras de la caja y pulsa Enter.
   Si el artículo ya existe, se usa el mismo: no se crea otro.
5. Para un equipo, deja marcado «Contar por equipo (serie o IMEI)». Cada equipo tiene su propia fila.
6. En cada fila, completa:
   - «IMEI *»: el IMEI de 15 dígitos de la caja o del equipo;
   - «Número de serie (opcional con IMEI)»: si el artículo pide IMEI y el IMEI es válido, la serie puede
     quedar vacía; si el campo dice «Número de serie *», la serie es obligatoria;
   - «IMEI 2 (opcional)»: sólo si el equipo tiene dos;
   - «Estado del equipo»: «Nuevo».
7. Pulsa «Agregar otro equipo» para la siguiente fila.
8. Con el lector: escanea la serie, pulsa Enter, escanea el IMEI y pulsa Enter. Aparece la fila del equipo
   siguiente.
9. Pulsa «Siguiente: revisar». Lee el resumen en «Revisa antes de registrar»: sucursal, tipo de ingreso y la
   cantidad de equipos.
10. Si todo está bien, pulsa «Confirmar recepción».
11. En «Recepción registrada», pulsa «Imprimir o ver etiquetas» y pega cada etiqueta en su equipo.

Para un solo equipo suelto también puedes usar «+ Registrar equipo» en la pantalla «Equipos».

## 3. Registrar seminuevos

Cada seminuevo es distinto: tiene su propio estado, batería, grado y precio. Por eso **un seminuevo siempre se
registra uno por uno**. El sistema no deja recibir una variante seminueva por cantidad.

1. En «Recibir mercadería», busca la variante **seminueva** del modelo, no la nueva.
2. «Contar por equipo (serie o IMEI)» queda marcado y no se puede quitar.
3. En cada equipo completa:
   - «IMEI *» y, si corresponde, «Número de serie (opcional con IMEI)»;
   - «Estado del equipo» (por ejemplo «Usado» o «Reacondicionado»);
   - «Batería medida (%)», medida en el propio equipo;
   - «Estado estético» (el grado);
   - «Detalles para el comprador (opcional)»: lo que verá el cliente;
   - «Observaciones internas (opcional)»: lo que sólo ve el personal.
4. «Precio de venta del equipo (S/, opcional)» sólo aparece si tienes «Administrar productos». Un seminuevo sin precio
   queda «pendiente»: no se vende en caja ni en la web hasta que alguien le ponga precio.
5. Confirma la recepción. Al final verás «Completar … precios pendientes» si faltan precios. También se
   completan en «Equipos seminuevos».

No pongas «Estado estético» a un equipo de una variante nueva: el sistema lo rechaza. Si el equipo tiene grado, es
seminuevo y va en la variante seminueva.

## 4. Registrar accesorios (por cantidad)

1. En «Recibir mercadería», busca el accesorio (cargador, funda, cable…).
2. No marques «Contar por equipo». La línea dice «Por cantidad».
3. En «Cantidad que entra», usa los botones − y + o escribe el número.
4. Si tienes permiso de costos, completa «Costo por unidad (opcional)».
5. Puedes mezclar accesorios y equipos en la misma recepción.

Si el sistema pregunta si quieres recibir un equipo «por cantidad», responde «Mantener por equipo». Un artículo
que se recibe por cantidad ya no se puede pasar a control por equipo mientras tenga stock.

## 5. Muchos equipos a la vez

### Pegar varios IMEI en una línea

Sirve cuando tienes una lista de IMEI o series de un mismo artículo.

1. Dentro de la línea del artículo, pulsa «Pegar varios».
2. Pega en «Series o IMEI, uno por línea». Un valor de 15 dígitos válido va al IMEI; cualquier otro, a la serie.
   «Serie IMEI» en la misma línea es un solo equipo.
3. Pulsa «Revisar lo pegado». La vista previa te dice cuántos equipos se agregarán y cuáles no:
   - «Repetidos en lo pegado (se agregan una sola vez)»;
   - «Ya están en esta línea (no se agregan otra vez)»;
   - «No se reconocen (no se agregan)».
4. Si está bien, pulsa el botón «Agregar … equipos». Si no, pulsa «Cancelar».
5. Para poner el mismo dato a todos (costo, grado, batería o precio), pulsa «Aplicar a todos». Completa lo que
   quieras y pulsa «Aplicar a los … equipos». Sin marcar «Reemplazar también lo que ya está escrito», sólo se
   llenan los campos vacíos.

### Pegar una lista con varios modelos

En el paso «Artículos», el botón «Pegar una lista» lee un mensaje con varios equipos y busca el catálogo por ti.
Pega el texto, pulsa «Analizar y preparar» y revisa cada fila antes de agregarla a la recepción.

### Cargar desde Excel

Entra a «Más herramientas» → «Gestión masiva». Hay tres cargas y cada una hace una sola cosa:

- «Carga de equipos»: una fila es un equipo físico (con serie o IMEI).
- «Carga de stock»: cantidades de accesorios; no sirve para equipos con serie.
- «Carga de productos»: crea y edita el catálogo; no mueve stock.

Para la «Carga de equipos»:

1. Descarga la plantilla y llénala. Usa siempre una plantilla del sistema: «Stock de equipos nuevos» o «Stock de
   seminuevos» (cada una carga sólo su línea; ver [carga-stock-real.md](carga-stock-real.md)) o «Descargar
   plantilla», la combinada de siempre.
2. Elige el archivo en «Archivo de equipos (.xlsx)».
3. Si las filas no dicen la sucursal, elígela en «Sucursal para las filas sin sucursal».
4. Elige el «Tipo de ingreso» y escribe el «Motivo para las filas sin motivo» (por ejemplo, el número de guía).
5. Pulsa «Previsualizar». Previsualizar no registra nada.
6. Lee «Lo que pasará con cada fila». Si hay errores, pulsa «Descargar errores (CSV)», corrige el Excel y súbelo
   otra vez.
7. Cuando todo esté bien, pulsa «Registrar … equipos».
8. Descarga «Antes y después» y guárdalo con el archivo. Si la carga fue un error, «Anular esta carga» la deshace
   entera mientras ningún equipo se haya movido ([carga-stock-real.md](carga-stock-real.md) §9).
9. «Conciliación de equipos» → «Comprobar ahora» confirma que el stock de cada producto es igual a sus equipos
   disponibles.

## 6. Inventario inicial o compra

En «Tipo de ingreso» hay dos opciones:

- «Compra a proveedor»: mercadería que llega comprada. Es la opción normal.
- «Inventario inicial»: el stock que la tienda ya tenía antes de usar el sistema.

«Inventario inicial» sólo se acepta en un artículo **sin movimientos en esa sucursal**. Si el artículo ya tuvo
cualquier movimiento ahí, el sistema lo rechaza y te pide registrarlo como compra. Si dudas, pregunta al
responsable antes de usar «Inventario inicial».

## 7. Costos

- **Equipos con serie:** cada equipo tiene su propio costo, lo que la tienda pagó por ese equipo. Se escribe en
  «Costo del equipo: lo que pagó la tienda (opcional)».
- **Accesorios:** el sistema calcula un **costo promedio** por sucursal con el «Costo por unidad» de cada
  ingreso.
- **Costo pendiente:** si algo entra sin costo, queda como «Costo pendiente». Nunca se cuenta como si costara
  cero. En un equipo, quien tiene «Ver costos y márgenes» lo completa con «Corregir costo». En un accesorio, el costo
  pendiente todavía no se completa desde el panel: avisa al responsable.
- **Quién lo ve:** sólo quien tiene «Ver costos y márgenes». Los demás no ven ningún costo.

Para corregir el costo de un equipo (con permiso de costos y de mover inventario):

1. Entra a «Más herramientas» → «Costos y precios».
2. En «Costo de cada equipo», busca el equipo y pulsa «Corregir costo».
3. Escribe el «Nuevo costo» y el «Motivo». El motivo es obligatorio. No escribas montos en el motivo: lo pueden
   ver personas sin permiso de costos.
4. Pulsa «Guardar corrección».

No se puede corregir el costo de un equipo **en tránsito**: corrígelo antes de despachar o después de recibirlo.
Tampoco se cambia el costo de un equipo vendido, dado de baja o anulado.

## 8. Sucursales

- El stock es **de cada sucursal**. Un equipo sólo se vende en la sucursal donde está.
- Para cambiar un equipo o un accesorio de sucursal se usa una **transferencia** (sección 11). No lo des de baja
  en una sucursal para volver a ingresarlo en otra.
- Si registraste en la sucursal equivocada y el equipo no tiene otro movimiento, puedes anular el ingreso con el
  motivo «Sucursal equivocada» y registrarlo bien (sección 10).

## 9. Prevención de duplicados

El IMEI no se repite en toda la empresa, y la serie no se repite en el mismo producto.

- Si escaneas dos veces el mismo equipo, el sistema lo marca en «Revisa antes de registrar» como
  «Repetidos en esta recepción».
- Si el equipo ya existe, verás «Ya registrados en el sistema» o el mensaje «Ese IMEI ya está registrado en
  otro equipo de la empresa».
- Los campos con problema se marcan en rojo: «Revisa los campos marcados en rojo: hay datos que faltan o están
  repetidos».

Qué hacer:

1. Compara el IMEI de la pantalla con el de la caja y el del equipo.
2. Si fue un error de lectura, borra la fila y vuelve a escanear.
3. Si el equipo de verdad ya figura en el sistema, **no lo registres otra vez**. Búscalo en «Equipos» y avisa al
   responsable.

## 10. Corregir un ingreso mal hecho

Las correcciones no borran ni editan la historia. Dejan una línea nueva en el Kardex con tu nombre y tu motivo.
Sólo se puede si el ingreso es el **único movimiento** del equipo: que no se haya vendido, apartado ni
transferido. Si no se puede, la pantalla dice «Este equipo no se puede anular» y el motivo. En ese caso avisa al
responsable.

### Anular ingreso

Para un equipo que no debió entrar.

1. Entra a «Stock y equipos» → «Equipos nuevos». Para un seminuevo, cambia «Línea» a «Seminuevos» y pulsa
   «Filtrar».
2. Busca el equipo por su serie o por los últimos dígitos de su IMEI.
3. En su fila, pulsa «Anular ingreso».
4. En «Tipo de error», elige uno:
   - «Error de digitación (IMEI o serie)»;
   - «Variante equivocada»;
   - «Sucursal equivocada»;
   - «Ingreso duplicado»;
   - «El equipo no llegó».
5. Escribe el «Motivo de la operación» y pulsa «Confirmar».
6. El equipo queda anulado y su IMEI y su serie quedan libres. Si corresponde, registra el equipo correcto con
   «Recibir mercadería».

### Cambiar variante

Para un equipo correcto que entró con la capacidad o el color equivocado.

1. En la fila del equipo, pulsa «Cambiar variante».
2. En «Variante correcta», elige la variante. Sólo aparecen variantes del mismo modelo y de la misma línea (nueva
   o seminueva).
3. Escribe el «Motivo de la operación» y pulsa «Confirmar».
4. Pulsa «Imprimir la etiqueta nueva» y cambia la etiqueta del equipo.

Si el equipo es de otro modelo, no uses «Cambiar variante»: anula el ingreso y regístralo de nuevo.

## 11. Transferir equipos entre sucursales

Necesitas permiso en las dos sucursales, la de origen y la de destino.

**En la sucursal de origen:**

1. Entra a «Movimientos» → «Transferencias».
2. En «Nueva transferencia», elige «Origen», «Destino» y escribe el «Motivo». Pulsa «Crear borrador».
3. En el borrador, sección «Equipos con serie»: escanea cada equipo en «Escanear equipo (IMEI, serie o código
   interno)». Si lo escribes a mano, marca la casilla «Lo escribí a mano…». Para sacar uno, pulsa «Quitar».
4. Para accesorios, en «Productos» escribe la cantidad de cada uno y pulsa «Guardar líneas».
5. Mientras es borrador, los equipos siguen a la venta. Si alguien vende uno antes del despacho, el sistema lo
   avisará al despachar.
6. Revisa la lista con los equipos en la mano y pulsa «Despachar». Una vez despachada, la transferencia no se
   puede editar ni anular.

**Mientras viaja:** la transferencia está «En tránsito». El stock ya salió del origen y todavía no entró al
destino. **Los equipos en tránsito no se pueden vender** en ninguna sucursal.

**En la sucursal de destino:**

1. Abre la transferencia desde «Transferencias».
2. Escanea cada equipo que llegó en «Verificar llegada (IMEI, serie o código interno)». Cada uno pasa de «Por
   verificar» a «Verificado».
3. Mira el contador «… de … equipos verificados».
4. El botón «Recibir» se activa sólo cuando todos los equipos están verificados. Púlsalo para sumar el stock al
   destino.

**Si falta un equipo o llegó uno distinto:** no recibas la transferencia. Déjala en tránsito y avisa de inmediato
al responsable de las dos sucursales. Una transferencia despachada no se anula.

«Anular» sólo sirve para un borrador que todavía no se despachó.

## 12. Si algo sale mal

1. **Lee el mensaje de la pantalla.** Casi siempre dice qué falta o qué está repetido.
2. **No fuerces.** No inventes una serie, no cambies de sucursal ni de artículo «para que pase», no des de baja
   un equipo para limpiar un error.
3. **No recargues la página** si el sistema dice que lo escrito no se guardó.
4. Si el mensaje dice «Puede que la recepción ya se haya registrado», quédate en la pantalla y pulsa otra vez
   el mismo botón: no se duplica. No la registres de nuevo desde cero. Si dudas, revisa el Kardex.
5. **Avisa al responsable** con el nombre de la pantalla, lo que intentabas hacer y el mensaje exacto. No mandes
   por chat fotos con IMEI completos ni costos.
