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

Panel › Productos › **Carga masiva**. El proceso no cambia: archivo → hoja →
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

**La plantilla (`plantilla-productos.xlsx`) tiene tres hojas, y sólo la primera es
datos:**

| Hoja | Para qué | ¿Se importa? |
|---|---|---|
| `Productos` | Las columnas reales. Fila 2: ayuda por columna. Fila 3: un ejemplo en gris | Sólo las filas que escribas tú |
| `Instrucciones` | Diez puntos: las imágenes no se pegan en Excel, Excel guarda nombres, cómo separar varias, qué se adjunta después, formatos y límites | No |
| `Ejemplo` | Tres productos llenos y la lista de archivos que habría que adjuntar | No |

- Subir la plantilla tal cual se descarga no crea nada: la fila de ayuda y la de
  ejemplo se saltan (lo dice la previsualización), y las hojas `Instrucciones` y
  `Ejemplo` aparecen marcadas como hojas de ayuda y no se pueden elegir.
- Sólo se salta la fila de ejemplo EXACTA (mismo nombre y mismo código). Una tienda
  que vende ese producto con su propio código lo importa con normalidad.
- Los formatos y los límites que dice la hoja `Instrucciones` se escriben, al
  descargarla, con los valores que aplica el servidor.
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
con el de productos. Tienen su plantilla, «Equipos serializados.xlsx», una fila por
equipo: `docs/pagos-equipos-documentos.md` §3.

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

### 5.3 Antes de publicar una versión con estos cambios

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
