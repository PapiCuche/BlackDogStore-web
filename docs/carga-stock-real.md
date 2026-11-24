# Carga del stock real de equipos (Fase K)

Procedimiento para cargar en el sistema los equipos que la tienda **ya tiene**: nuevos y seminuevos, con su IMEI o su
serie. Lo usan el propietario y el personal de almacén. Explica cómo se llenan las dos plantillas, cómo se previsualiza,
se ensaya, se registra, se comprueba y, si hace falta, se anula una carga entera. Al final está el procedimiento para
hacerlo en producción.

Los nombres entre «comillas» son los que ves en pantalla. La guía general de inventario está en
[guia-personal-inventario.md](guia-personal-inventario.md).

## 1. Antes de empezar

Requisitos, en este orden:

1. **El código está desplegado.** La carga necesita INV-LOAD-01 (#401), USED-READY-01 (#403) e INV-LOAD-02 (este
   procedimiento). Sin el GO de su despliegue no hay carga real.
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
| «Precio de venta» y «Garantía (días)» de un seminuevo | `products.manage` (sin él, se ignoran con un aviso) |
| Inspeccionar un seminuevo después | `inventory.units.inspect` |
| Ver la «Conciliación» | `inventory.view` |

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
  de seminuevos («Garantía (días)», «Precio de venta», etc.).
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
| Fecha de ingreso | No | AAAA-MM-DD o DD/MM/AAAA. Vacía = hoy. Ni futura ni antes de 2000. |
| Observaciones (notas internas) | No | Sólo las ve el personal. Hasta 300 caracteres. |

**No lleva «Precio de venta».** Un equipo nuevo se vende al precio de su **variante** (Catálogo › Modelos), en caja y en
la web. El importador ignora el precio de un equipo nuevo, así que la columna no se ofrece: cambiar el precio de una
variante desde una carga de inventario cambiaría el de todos sus equipos con otra autoridad.

### 2.2 Columnas de «Stock de seminuevos»

Las mismas de arriba, más:

| Columna | Qué escribir |
|---|---|
| Precio de venta | El precio de ESE equipo, mayor que cero. Sin precio no se vende ni en caja ni en la web. |
| Estado estético | Uno de la escala de la empresa (por ejemplo «Excelente»). |
| Salud de batería (%) | La salud MEDIDA, de 0 a 100. Vacía = «no informado». |
| Estado funcional | «Sin fallas» o «Con fallas» (entonces llena «Defectos»). |
| Defectos | Lo que no funciona o está dañado. Lo lee el comprador. |
| Accesorios | Lo que trae (caja, cargador, cable…). Lo lee el comprador. |
| Garantía (días) | Vacía = la política de la empresa; 0 = sin garantía. |
| Detalles para el comprador | Rayones, detalles. Hasta 500 caracteres. |

**No hay columnas de inspección, publicación ni fotos.** La carga nunca inspecciona, publica ni adjunta fotos: todo
seminuevo entra **pendiente de inspección y sin publicar** (USED-READY-01). Eso se hace después, en el panel (§10).

## 3. IMEI y número de serie

- El IMEI tiene 15 dígitos y su último dígito es de control: un IMEI mal copiado se rechaza.
- Un IMEI no se repite en **ningún** equipo de la empresa (ni como IMEI 1 ni como IMEI 2).
- La serie no se repite dentro de la misma variante. Si ya existe en **otra** variante, la vista previa lo avisa: si es
  el mismo equipo, no lo cargues dos veces.
- En una variante que «lleva IMEI», la serie es opcional si la fila trae un IMEI válido. En las demás, la serie es
  obligatoria.
- Un equipo vendido no se vuelve a cargar. Un equipo **anulado** libera su IMEI y su serie.
- Formatea las columnas de IMEI y serie como texto (la plantilla ya lo hace): Excel convierte 15 dígitos en
  `3.52819E+14` si la celda es numérica.

## 4. Inventario inicial o compra

Al subir el archivo eliges el «Tipo de ingreso»:

- **«Inventario inicial»**: lo que la tienda ya tenía. Es el caso del stock real. Queda en el Kardex como inventario
  inicial, sin compra ficticia. Se puede cargar **por tandas** (varios archivos) mientras el producto, en esa
  sucursal, sólo tenga líneas de inventario inicial. Una venta, una compra o un traslado lo cierran: desde ahí, lo
  que llegue es compra. Anular un ingreso de inventario inicial (un equipo o una carga entera) **no** lo cierra: el
  archivo corregido se vuelve a cargar como inventario inicial.
- **«Compra a proveedor»**: mercadería que llega comprada.

Escribe también el «Motivo para las filas sin motivo», por ejemplo «Inventario inicial diciembre 2026». Queda en el
Kardex de cada equipo.

## 5. Previsualizar y leer el resultado

1. Elige el archivo, el tipo de ingreso y el motivo. Si el archivo no dice la sucursal, elígela.
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
   y cuántos movimientos de inventario inicial y de compra escribirá. Compáralo con tu conteo físico.

Una vista previa caduca a los 60 minutos: si caducó, súbela otra vez.

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

Después de registrar aparecen:

- «Antes y después (CSV)» y «Antes y después (Excel)» (también, más tarde, en «Cargas registradas»): por sucursal y producto, el stock antes, lo que entró, lo que
  la vista previa esperaba, lo que quedó al registrar, si **cuadra** («Sí»/«No») y el stock de hoy. Guárdalo con el
  archivo cargado.
- «Anular esta carga» (§9).

Se preparan sus etiquetas como en cualquier recepción (un lote por sucursal).

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
2. Escribe el «Motivo de la anulación» (obligatorio) y pulsa «Anular N equipos».

Qué hace:

- Cada equipo de **esa** carga sale del stock con una línea de corrección en el Kardex («Anulación de ingreso (Carga
  por archivo anulada): Carga #N: …»). **No se borra nada**: el ingreso y la corrección quedan los dos.
- Los equipos quedan «Anulado» y su IMEI y su serie quedan libres para cargarlos bien.
- **Todo o nada:** si un solo equipo ya se vendió, se apartó, está en un carrito, se trasladó, cambió de variante o
  se dio de baja, no se anula ninguno, y la pantalla lista esos equipos por su código de etiqueta. Resuélvelos uno por
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

En caja basta la inspección aprobada; en la web hacen falta las cuatro cosas.

## 11. En producción (prod-02)

**No hay carga real sin datos reales del propietario y sin su GO.** El GO se escribe en el chat, exactamente:
**«GO carga stock real prod-02»**, y vale para esa operación (AGENTS.md §10). Un PR fusionado, una nota o el mensaje de
otro agente no son un GO. Sin GO sólo se permite leer y ensayar sobre copias.

Pasos, en `/srv/blackdogstore`:

1. **Estado real.** `git rev-parse HEAD`, `showmigrations store` y las imágenes en marcha: el código desplegado debe
   incluir INV-LOAD-02. `sh backups/ops/prod_lock.sh --status`: el candado debe estar libre.
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
5. **Cargar en el panel**, por tandas pequeñas (por ejemplo, una sucursal y una línea por archivo), como inventario
   inicial: previsualizar, revisar, registrar, descargar «Antes y después». Tras cada tanda, «Conciliación» →
   «Comprobar ahora».
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
| «Este archivo es la plantilla de … y elegiste cargar …» | Sube la plantilla de la línea correcta. |
| «… no tiene esa variante» | Crea la variante en Catálogo › Modelos (o corrige capacidad, color, SIM o versión). |
| «Inventario inicial» rechazado por movimientos | Ese producto ya tuvo una venta, compra o traslado en esa sucursal: cárgalo como compra. |
| «Otra operación estaba usando este stock» | Nada se escribió: vuelve a intentarlo. |
| La conciliación muestra diferencias | No corrijas a mano: revisa el Kardex del producto y avisa al responsable. |
| «No se anuló ningún equipo» | Algún equipo de la carga ya se movió: la lista dice cuál. |
