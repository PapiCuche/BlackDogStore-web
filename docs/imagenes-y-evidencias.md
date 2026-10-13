# Imágenes de producto, carga masiva con imágenes y evidencias de servicio

Guía de uso y de operación. Las decisiones de diseño están en
[05_DECISIONES_TECNICAS.md](../05_DECISIONES_TECNICAS.md) (DEC-MEDIA-01,
DEC-IMPORT-MEDIA-01, DEC-EVIDENCE-01).

## 1. Dos clases de imagen, dos reglas

| | Imágenes de producto | Evidencias de servicio |
|---|---|---|
| Quién las ve | Cualquiera: van en el catálogo | Sólo el personal con acceso a la orden; el cliente, las que se le comparten |
| Dónde se sirven | `/api/storefront/images/<id>` (público) | `/api/v1/internal/<empresa>/service/orders/<orden>/evidence/<id>/content/` (se autoriza en cada petición) |
| Se pueden quitar | Sí | No: se anulan, con motivo, y se conservan |
| Formatos | PNG, JPEG, WebP | JPEG, PNG, WebP, HEIC |
| Tope por archivo | 8 MB | 25 MB |

Las dos se recodifican en el servidor a partir de los píxeles: no sobrevive
ningún metadato (tampoco la ubicación GPS), la orientación queda corregida y el
nombre original del archivo nunca llega al almacenamiento.

## 2. Imágenes de un producto

Panel › Productos › *un producto* › **Imágenes**.

- Elige varios archivos o arrástralos. Se suben de uno en uno; el que falla
  dice por qué y se puede reintentar.
- La **principal** es la que muestra el catálogo. La primera que subes lo es;
  puedes elegir otra.
- El **texto alternativo** describe la imagen a quien no la ve. Vacío, se usa el
  nombre del producto.
- El orden se cambia con las flechas. Hasta 12 imágenes por producto.
- Cada cambio se guarda al hacerlo y queda en el registro de auditoría.

Al crear un producto puedes elegir las imágenes antes de guardarlo: se suben en
cuanto el producto existe.

**Productos antiguos.** Uno que tenía una dirección externa escrita a mano la
conserva hasta que le subas una imagen; entonces la galería pasa a mandar. El
campo «Dirección de imagen externa» sólo se ofrece a productos sin galería.

## 3. Carga masiva con imágenes

Panel › Productos › **Carga de productos**. El proceso no cambia: archivo → hoja →
columnas → previsualización → confirmación → resultado. Nada se escribe hasta
confirmar, y se aplica todo o nada.

### 3.1 El Excel

Dos columnas nuevas, opcionales. Llevan **nombres de archivo**, no direcciones:

| Columna | Contenido | Ejemplo |
|---|---|---|
| `Imagen principal` | Un archivo: la que mostrará el catálogo | `iphone15-frontal.webp` |
| `Imágenes` | Varios archivos separados por `\|` | `iphone15-frontal.webp\|iphone15-trasera.webp` |

Ejemplo de filas:

| Código | Nombre | Precio de venta | Categoría | Imagen principal | Imágenes |
|---|---|---|---|---|---|
| IP15-128-BLK | iPhone 15 128 GB | 2999 | Smartphones | iphone15-frontal.webp | iphone15-frontal.webp\|iphone15-trasera.webp |
| CAB-USBC-1M | Cable USB-C 1 m | 49.90 | Accesorios | | cable-usbc.webp |

- La plantilla descargable ya trae las dos columnas y se reconoce sola al
  subirla.

**La plantilla (`carga-masiva-productos.xlsx`) es una sola hoja, «Productos»**
(IMPORT-SPLIT-01): la fila de cabeceras y nada debajo.

- Cada cabecera lleva una **nota** con lo que va debajo. Las de `Imagen principal` e
  `Imágenes` dicen que la imagen no se pega en la celda, cómo separar varias, qué se
  adjunta después, y los formatos y límites, escritos al descargarla con los valores
  que aplica el servidor.
- `Categoría` ofrece en una lista las categorías activas de la empresa, leídas al
  descargar. La lista ayuda y no impide escribir una nueva. Si los nombres no caben en
  una lista de celda (más de 255 caracteres en total, o un nombre con coma, comillas o
  que empieza por `=`, `+`, `-` o `@`), la plantilla sale sin lista y la categoría se
  escribe; se valida al subir.
- `Destino` ofrece sus dos valores en una lista, que tampoco bloquea: un valor distinto
  es un error de fila al subir.
- Subir la plantilla tal cual se descarga no crea nada.
- **Los archivos hechos con la plantilla anterior siguen sirviendo.** Su fila de ayuda
  y su fila de ejemplo se saltan (lo dice la previsualización), y sus hojas
  `Categorías`, `Instrucciones` y `Ejemplo` aparecen marcadas como hojas de ayuda y no
  se pueden elegir. Sólo se salta la fila de ejemplo EXACTA (mismo nombre y mismo
  código).
- **Esta carga no mueve stock.** Una columna `Stock`, `Cantidad`, `Número de serie` o
  `IMEI` en un archivo de productos no se importa, y la previsualización lo dice a la
  vista con a dónde ir: Inventario › Carga de stock o Inventario › Carga de equipos.
- En la pantalla, «¿Cómo preparo las imágenes?» muestra lo mismo en corto, antes de
  elegir el archivo y otra vez al adjuntar las imágenes.
- Un Excel sin estas columnas se importa exactamente como antes.
- La columna `URL de imagen` sigue existiendo para imágenes alojadas fuera. Si
  una fila trae archivos y además una URL, se usan los archivos.

### 3.2 Los archivos

En el paso «Columnas e imágenes», adjunta las imágenes:

- varios archivos a la vez (selección múltiple o arrastrar), o
- un **ZIP** con ellas, o las dos cosas.

Cada fila se casa con sus archivos **por el nombre**, sin mirar mayúsculas ni
la carpeta en la que vengan dentro del ZIP.

### 3.3 Qué muestra la previsualización

Por fila: su acción, sus imágenes (la principal marcada con ★) y sus errores.
En el resumen de imágenes:

| Dato | Significa |
|---|---|
| Adjuntas | Imágenes recibidas |
| Válidas | Citadas por alguna fila y legibles |
| Faltantes | Citadas por una fila y no adjuntas |
| Inválidas | Adjuntas pero ilegibles, demasiado grandes o ambiguas |
| Duplicadas | Mismo contenido con otro nombre: se guarda una vez |
| Sin usar | Adjuntas que ninguna fila cita: no se guardan |

Con un solo error no se puede aplicar. «Volver a columnas e imágenes» conserva
el Excel y las imágenes ya elegidas: añade la que falte y previsualiza otra vez.
El reporte descargable (`errors.csv`) incluye los errores de imágenes.

### 3.4 Errores frecuentes

| Mensaje | Qué hacer |
|---|---|
| Imagen «x»: no está entre los archivos adjuntos | Adjunta el archivo o corrige el nombre en el Excel |
| Imagen «x»: hay más de un archivo con ese nombre y no son iguales | Renombra uno de los dos |
| Imagen «x»: sube la imagen en PNG, JPEG o WebP | Convierte el archivo |
| Imagen «x»: pesa más de 8 MB | Reduce la imagen |
| Un producto admite hasta 12 imágenes | Quita nombres de la fila |
| La imagen «x» ya no está disponible. Vuelve a previsualizar | Pasaron más de 24 h entre previsualizar y aplicar |
| El ZIP contiene rutas que salen de su carpeta | Vuelve a crear el ZIP sólo con las imágenes |

### 3.5 Lo que conviene saber

- **Importar dos veces el mismo archivo no duplica la galería.** Una imagen que
  el producto ya muestra se reconoce y no se vuelve a añadir.
- **En una actualización, las imágenes se añaden** a las que el producto tenía.
  `Imagen principal` cambia cuál es la principal.
- **Entre previsualizar y aplicar hay 24 horas.** Las imágenes esperan guardadas
  como imágenes sin colocar de la empresa; la limpieza diaria retira las de una
  previsualización abandonada.
- **Límites de una carga:** 200 imágenes y 100 MB en total; 400 archivos dentro
  de un ZIP. Si tienes más, divide la carga en varias.

### 3.6 Equipos con número de serie

Los equipos con serie no se cargan con el Excel de inventario (escribe cantidades) ni
con el de productos. Tienen su carga, Inventario › **Carga de equipos**, una fila por
equipo: `docs/pagos-equipos-documentos.md` §3.

### 3.7 Imágenes en lote, sin libro de productos (INVENTORY-UNIFIED-01)

Panel › Inventario › **Gestión masiva de inventario** › *Imágenes* (también desde
Productos › «Añadir imágenes en lote»). Añade imágenes a productos que **ya
existen**; no crea ni edita ninguno, y no toca nombre, precio, categoría, destino,
estado, stock, series ni Kardex. Pide `products.manage`.

**Cómo se nombra un archivo.** El nombre es el código del producto:

| Archivo | Producto | Orden |
|---|---|---|
| `FUNDA.png` | el de código `FUNDA` | 0 (la principal) |
| `FUNDA_2.png`, `FUNDA-3.jpg`, `FUNDA (4).webp` | el mismo | 2, 3, 4 |

- La gramática exacta: `<código>.<ext>`, con un sufijo opcional `_N`, `-N` o ` (N)`
  donde `N` tiene de uno a tres dígitos. Un nombre sin número cuenta como 0; la de
  número más bajo es la principal. Sólo se quita la última extensión y la carpeta
  no forma parte del nombre.
- `<código>` se busca como lo busca la carga de productos: código interno y códigos
  de barras, **exacto** (mayúsculas incluidas). **Nunca por el nombre del producto.**
- Un código puede terminar en `-2`. Por eso se prueba el nombre entero como código
  y, aparte, el nombre sin su número final. Si sale un producto, es ése. Si salen
  **dos productos distintos**, el archivo no se carga y la vista previa lo dice. Si
  no sale ninguno, lo mismo. No se adivina.
- Un código con caracteres que un nombre de archivo no admite (`/`, `:`) no se
  puede escribir así: para ésos está el libro.

**El libro «Imágenes» (opcional).** Una hoja «Imágenes» con `Código`,
`Imagen principal` e `Imágenes` (varios nombres separados por `|`), leídas igual que
en la carga de productos. Con libro, los nombres de los archivos no deciden nada.
También sirve el propio libro de productos: sólo se leen esas columnas.

**Dos modos, elegidos a propósito.**

- **Añadir** (por defecto). Se suman a la galería. Una imagen cuyo contenido ya
  está en esa galería no se añade otra vez. La principal sólo cambia si la galería
  estaba vacía o se marcó «usar como principal».
- **Reemplazar.** La galería de cada producto **de la carga** pasa a ser exactamente
  lo subido, en su orden. La vista previa dice cuántas imágenes pierde cada producto
  y aplicar exige escribir `REEMPLAZAR`. Un producto que no está en la carga no se toca.

**La vista previa** no coloca nada. Por producto: qué se añade (con miniatura), cuál
será la principal, qué ya estaba, cuántas se quitan y qué pasa del límite de 12.
Aparte: archivos sin producto o con nombre ambiguo (se listan y **no** impiden el
resto), archivos que no son imágenes (ignorados) y, como errores que sí impiden
aplicar, una imagen ilegible, dos archivos distintos con el mismo nombre o un
producto que superaría el límite.

**Aplicar** es todo o nada y una sola vez (un segundo clic devuelve el primer
resultado). Se detiene si el catálogo o una galería cambiaron desde la vista previa.
Deja una línea de auditoría por carga (`product_images_import_applied`: modo y
conteos, sin nombres de archivo) además de la de cada imagen en su galería.

**Seguridad.** Es el mismo lote que la carga de productos (`import_media`): rutas
que salen del ZIP, enlaces simbólicos, ZIP cifrado, demasiadas entradas, expansión
desproporcionada, topes de archivos y de peso, y cada imagen recodificada desde sus
píxeles. Viaja por la misma ruta (`POST /api/admin/products/import/preview/` con
`kind=images`), que es la única dimensionada para ese peso; aplicar va por
`POST /api/admin/products/images/import/<id>/apply/`, que no recibe archivos.

## 4. Evidencias fotográficas del servicio técnico

Panel › Servicio técnico › *una orden* › **Evidencias**.

1. Elige la **etapa**.
2. **Tomar foto** abre la cámara del teléfono; **Elegir fotos** abre la galería
   y admite varias.
3. Escribe, si quieres, una **nota** por foto: qué muestra.
4. **Subir N fotos**. La que falla se queda en la cola con su motivo.

### 4.1 Etapas

| Etapa | Para qué | Quién puede |
|---|---|---|
| Ingreso | Estado físico al recibir: pantalla, carcasa, accesorios, serie | Quien abre órdenes |
| Diagnóstico | Síntomas, equipo abierto, daños internos | Quien diagnostica |
| Antes / Durante / Después de reparar | El trabajo realizado | Quien repara |
| Repuestos | Pieza retirada e instalada, serie o lote | Quien repara |
| Control de calidad | Pruebas y condición final | Quien hace control de calidad |
| Listo para entrega | Estado final antes de que llegue el cliente | Quien entrega |
| Entrega | Equipo y accesorios entregados | Quien entrega |
| Garantía / reingreso | Cómo vuelve un equipo y qué se reclama | Quien abre órdenes |
| Otras | Lo que no cabe en las anteriores | Quien administra órdenes |

Subir una foto **no cambia el estado de la orden**.

### 4.2 Lo que queda registrado

Cada foto guarda su etapa, su nota, quién la subió y cuándo. La galería las
agrupa por etapa, cuenta las que están en vigor, pone lado a lado el antes y el
después de la reparación y abre cualquiera en grande (flechas para recorrer,
Escape para cerrar).

- **Una foto no se reemplaza ni se borra.** Si está mal, se **anula** con un
  motivo: deja de estar disponible y se conserva que existió. En una garantía o
  reingreso se añaden fotos nuevas; las originales no se tocan.
- **La nota se puede corregir**; el texto anterior queda en el registro.
- **Nacen internas.** «Compartir con cliente» es un acto aparte; el cliente verá
  la foto y su nota. «Ocultar al cliente» retira el acceso de inmediato.

### 4.3 Privacidad

Una evidencia puede mostrar un número de serie, un IMEI o datos del cliente. Por
eso no hay listado público ni dirección que sirva por sí sola: cada petición
comprueba empresa, sucursal, autoridad sobre la orden, visibilidad y anulación.
Otra empresa, u otra sucursal sin acceso, recibe «no encontrado».

## 5. Operación

### 5.1 Variables (todas opcionales)

| Variable | Por defecto | Qué limita |
|---|---|---|
| `STOREFRONT_IMAGE_MAX_UPLOAD_BYTES` | 8 MB | Una imagen de producto o de la tienda |
| `PRODUCT_IMAGE_MAX_PER_PRODUCT` | 12 | Imágenes por producto |
| `IMPORT_IMAGES_MAX_FILES` | 200 | Imágenes en una carga masiva |
| `IMPORT_IMAGES_MAX_TOTAL_BYTES` | 100 MB | Peso total de las imágenes de una carga |
| `IMPORT_IMAGES_ZIP_MAX_ENTRIES` | 400 | Archivos dentro de un ZIP |
| `IMPORT_IMAGES_ZIP_MAX_RATIO` | 200 | Cuánto puede crecer un archivo del ZIP al expandirse |
| `SERVICE_EVIDENCE_MAX_UPLOAD_BYTES` | 25 MB | Una foto de evidencia |

### 5.2 Limpieza

`python manage.py cleanup_storefront_images` (programada a diario, ver
[despliegue-produccion.md](despliegue-produccion.md) §6.1.2) retira las imágenes
públicas que nadie muestra y llevan más de 24 horas: las de un formulario que no
se guardó y las de una previsualización de carga masiva que no se aplicó. Las
evidencias de servicio no se limpian nunca.

### 5.3 Dónde viven las imágenes y quién las sube (INVENTORY-UNIFIED-01)

Revisado sobre el código y los archivos de despliegue del repositorio, sin acceso al
servidor.

- **Nadie necesita FTP, SSH ni acceso al disco del servidor.** Las imágenes se suben
  desde el panel: la galería de cada producto, la carga de productos (con sus
  imágenes), «Imágenes» en Gestión masiva de inventario, y Configuración › Tienda
  para hero, categorías y campañas.
- **Dónde quedan.** Con `EVIDENCE_STORAGE_BACKEND=filesystem` (el valor por defecto
  y el del despliegue entregado), en el volumen con nombre `evidence`, montado en
  `/app/private-media` del contenedor `backend` (`docker-compose.prod.yml`). Cada
  imagen pública tiene la clave `companies/<id de empresa>/storefront/<identificador>.<ext>`;
  las evidencias de servicio comparten el volumen bajo otras claves.
- **Sobreviven a recrear el contenedor.** Es un volumen con nombre: `docker compose
  up -d --build` y `down` lo conservan. `down -v` lo **borra**, igual que la base.
- **Están en la copia nocturna.** `deploy/backup.sh` empaqueta `/app/private-media`
  entero en `evidence-AAAAMMDD-HHMMSS.tar.gz`, junto al volcado de la base, y
  `deploy/crontab.example` lo programa a diario. Una copia en el mismo servidor no
  es una copia: ver «Copia externa» en [despliegue-produccion.md](despliegue-produccion.md).
- **Cómo se sirven.** Por `GET /api/storefront/images/<identificador>`: Django lee
  el archivo del almacén y lo entrega con caché larga (`immutable`); la dirección es
  un identificador que no se adivina y nunca cambia de contenido. El volumen **no**
  se publica como archivos estáticos (comparte disco con las evidencias, que son
  privadas).
- **Límites.** 8 MB por imagen, 12 por producto, 200 archivos y 100 MB por carga,
  400 entradas por ZIP (§5.1). El tamaño de la petición lo limitan además
  `store/request_limits.py` y `deploy/Caddyfile` (nivel `product_import`).
- **Limpieza.** Una imagen subida y no colocada se retira a las 24 horas (§5.2);
  una colocada en una galería cuenta como en uso y no se toca.

### 5.4 Almacenamiento de objetos (S3 o compatible): qué hay y qué faltaría

**Lo que ya existe en el código.** `EVIDENCE_STORAGE_BACKEND=s3` con
`EVIDENCE_STORAGE_BUCKET`, `…_ENDPOINT_URL`, `…_ACCESS_KEY_ID`,
`…_SECRET_ACCESS_KEY` y `…_REGION` (`store/evidence_storage.py`, con
`django-storages` y `boto3` ya en `requirements.txt`). Las imágenes públicas y las
evidencias usan ese mismo almacén, así que el cambio las mueve a las dos.

**Lo que NO está hecho y haría falta para migrar una instalación en marcha.**

1. Copiar lo que hay en `/app/private-media` al bucket conservando las claves
   (`companies/<id>/storefront/…`): la base guarda la clave de cada archivo.
2. Una copia de seguridad del bucket (versionado o réplica): `deploy/backup.sh` sólo
   empaqueta el volumen local y dejaría de cubrir las imágenes.
3. Comprobar que las direcciones públicas no cambian: siguen siendo
   `/api/storefront/images/<identificador>` servidas por Django, que leería del
   bucket. No habría que reescribir productos ni páginas.
4. Ensayarlo (`deploy/rehearsal.sh` contra un bucket de prueba) y un plan de vuelta
   atrás mientras el volumen local siga intacto.

Nada de esto se ha ejecutado ni probado contra un proveedor real: **necesita el plan
y la autorización del propietario** (credenciales, coste y proveedor).

### 5.5 Antes de publicar una versión con estos cambios

- Aplicar las migraciones `0100` y `0101` (añaden tablas y columnas; no borran
  ni reescriben datos).
- El volumen de archivos (`private-media`) debe ser escribible por el backend y
  estar incluido en la copia de seguridad: ahí viven las imágenes de producto y
  las evidencias.
- El proxy delante de Django no debe cortar una petición de 100 MB ni tardar
  menos que una carga con 200 imágenes. Caddy, tal como se entrega, no pone tope.
- La limpieza diaria debe estar programada: es lo que retira las imágenes de las
  previsualizaciones abandonadas.
- Repetir `sh deploy/rehearsal.sh` sobre el commit que se va a desplegar.
