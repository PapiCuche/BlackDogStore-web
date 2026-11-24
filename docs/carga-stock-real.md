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
| Confirmar el cambio del precio de una variante desde «Stock de equipos nuevos» (§5.1) | `products.manage` (sin él, la propuesta se ve pero no se aplica; el servidor responde 403) |
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
- Un equipo vendido no se vuelve a cargar. Un equipo **anulado** libera su IMEI y su serie. Un equipo **dado de baja**
  NO los libera: sigue ocupando su IMEI (`unique_stock_unit_imei_per_company` sólo excluye «Anulado»). Si un equipo
  dado de baja vuelve a la tienda, no se carga de nuevo con el mismo IMEI: se resuelve en su ficha.
- Formatea las columnas de IMEI y serie como texto (la plantilla ya lo hace): Excel convierte 15 dígitos en
  `3.52819E+14` si la celda es numérica.

### 3.1 Antes de llenar: exportar lo que ya está cargado

Para no escribir dos veces un equipo y para cuadrar el conteo físico, descarga primero lo que el sistema ya tiene. En
«Carga de stock» (`/admin/inventory/import`), bloque «Exportar», elige «Equipos con serie»:

- **«Reporte»**: una fila por equipo (también vendidos y dados de baja, con su estado), con su serie, **IMEI e IMEI 2
  completos**, sucursal, variante, precio y notas. Sirve para comparar con el conteo físico y marcar los que ya están.
- **«Para volver a cargar»**: sólo los equipos en estante, con las columnas de la plantilla combinada («Equipos»).
  Subido a la misma tienda no carga nada: cada fila sale como «ya está registrado».

Las dos respetan las sucursales de quien descarga (`inventory.view`). El costo sólo sale con `costs.view`. El IMEI no
sale enmascarado: el archivo es del personal que identifica los equipos por ese número y no se reenvía. No existe una
exportación con el formato de las dos plantillas de stock: la de equipos usa la plantilla combinada. Aunque se te pase
un equipo, la vista previa lo rechaza: el IMEI no se repite en la empresa.

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
   y cuántos movimientos de inventario inicial y de compra escribirá. Compáralo con tu conteo físico.

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

**Simulación completa (STOCK-REAL-READY).** `store.test_stock_real_simulation` recorre en 13 escenarios, con las
vistas reales y permisos reales:

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
13. el aislamiento entre sucursales y entre empresas.

Cada escenario compara el stock y el Kardex antes y después. Con `-v 2` imprime la bitácora:

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
| Aviso «Precio del archivo … distinto del de la variante» | No es un error: el precio no cambia. Si quieres cambiarlo, quien administra productos marca la propuesta y la confirma. |
| «El precio de … cambió desde la vista previa» | Otro cambió el precio de la variante: nada se registró. Sube el archivo otra vez. |
| «Este archivo es la plantilla de … y elegiste cargar …» | Sube la plantilla de la línea correcta. |
| «… no tiene esa variante» | Crea la variante en Catálogo › Modelos (o corrige capacidad, color, SIM o versión). |
| «Inventario inicial» rechazado por movimientos | Ese producto ya tuvo una venta, compra o traslado en esa sucursal: cárgalo como compra. |
| «Otra operación estaba usando este stock» | Nada se escribió: vuelve a intentarlo. |
| La conciliación muestra diferencias | No corrijas a mano: revisa el Kardex del producto y avisa al responsable. |
| «No se anuló ningún equipo» | Algún equipo de la carga ya se movió: la lista dice cuál. |
