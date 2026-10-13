# Estado actual

Este archivo no existía en el baseline. Se incorpora como entrada resumida a la
documentación real, sin reemplazar su historial.

## 2026-10-13 — INVENTORY-UNIFIED-01: correcciones tras la revisión independiente

Misma rama (`feat/inventory-unified`), sobre `ac6a061`. **Sólo en la rama: sin push, sin PR, sin
desplegar.** La revisión no encontró nada P0/P1; estos son sus P2/P3. Sin migraciones nuevas (sigue
habiendo una, `0122`), sin capacidades, sin cambios en `deploy/` ni en `request_limits.py`. Una
variable nueva en la configuración de DRF: el cupo `admin_export`.

| | Hallazgo | Estado |
|---|---|---|
| N1, O1 | La descarga anterior del inventario escribía nombres y códigos que empiezan por `=`, `+`, `-`, `@` como fórmulas vivas; `errors.csv` repetía lo que traía el archivo | CORREGIDO: los dos pasan por `guard_text`; la descarga anterior sigue recargándose igual |
| N6 | «Todo lo que coincide» contaba y luego leía los ids en otra consulta, sin compararlos: `expected_count=1` cambió 2 | CORREGIDO: una sola lectura, y es ésa la que se compara |
| N9 | Un NUL en `search` daba 500 en la lista de productos y en la acción en bloque | CORREGIDO: 400 en la acción; la lista no encuentra nada |
| N7 | La carga de productos bloqueaba en el orden del archivo; contra la acción en bloque daba abrazo mortal (500) | CORREGIDO: bloquea todo lo que edita, de menor a mayor id, antes de escribir |
| N2 | El archivo del catálogo «para volver a cargar» incluía productos inactivos, que al recargar podían nacer activos y publicados | CORREGIDO: sólo activos (el reporte los lleva todos) |
| N3 | La carga de productos publica por «Destino» lo que la acción en bloque dejaría fuera, y un archivo viejo vuelve a publicar lo ocultado después | SE DICE ANTES (no se rechaza): aviso por fila con los motivos y conteo de cambios de destino en la vista previa |
| N15, N16 | Textos: recargar stock lo deja como dice el archivo; los archivos cubren sucursales activas y «Stock total» es de toda la empresa | CORREGIDO (sólo palabras: nota del ZIP y panel) |
| N4 | Las descargas compartían el cupo de las cargas (30/min) | CORREGIDO: cupo propio `admin_export`, 6/min |
| N10 | Un carácter de control en el nombre de una sucursal daba 500 al descargar stock | CORREGIDO: las cabeceras se limpian como las celdas |
| N11 | Aplicar imágenes no notaba un cambio de principal o de orden desde la vista previa | CORREGIDO: compara imágenes, orden y principal |
| N13 | Cada vista previa guardaba otra copia de lo que ya estaba en la galería | CORREGIDO: sólo queda en espera lo que se va a colocar |
| F1 | El resultado de la carga de imágenes nunca listaba las galerías (la respuesta de aplicar no trae filas) | CORREGIDO, y la prueba responde lo que responde el servidor |
| O2 | «Mis pedidos» mostraba entero el producto actual de cada línea, también de uno oculto o inactivo: precio de hoy, descripción, dirección, stock y galería (anterior a la fase) | CORREGIDO: esa línea lleva el producto cerrado a id, nombre e imagen; la línea no cambia |
| N5 | La empresa de la acción en bloque se leía del navegador al enviar: con dos pestañas podía caer en otra empresa | CORREGIDO: la lista y la acción mandan explícita la empresa de la pantalla |
| 9 a–i | Panel: respuesta lenta que pisa a la nueva; selección que sobrevive a desactivar una fila; confirmación con selección vacía; última página vacía; vista previa de imágenes que no se invalida; ZIP filtrado sin decirlo; pago que deja enviar con una línea no disponible y no dice qué producto falló; contador del carrito; enlace a una pantalla que niega al operador antiguo | TODOS REALES, CORREGIDOS |
| 9 j | Pruebas que leían el código de una página como texto | SUSTITUIDAS por pruebas que montan la página |
| M03, M04, M06, M53, M54 | Mutantes que sobrevivían en la revisión | FIJADOS con pruebas |
| N8, N12, N14 | Código que empieza por apóstrofo; `<código>-12.png`; apóstrofo visible en reportes | DEUDA, a propósito (`docs/AUDIT_MEMORY.md` §10) |

**Cambió, además de lo pedido.** `Stager.keep_only` (lo usa también `discard`) en `import_media`;
el ZIP sigue aplicando los filtros pero su tarjeta avisa cuando los hay (se eligió decirlo, no
ignorarlos); «Añadir imágenes en lote» sólo se ofrece con empresa resuelta. Cuatro pruebas de las
partes anteriores cambiaron a propósito: las del catálogo «para volver a cargar» (ya sin el
producto inactivo) y la del límite de descargas (ahora su propio cupo).

**Pruebas (sobre `179d75c`, PostgreSQL, base `blackdog_focal`).** `test_bulk_visibility` 69,
`test_inventory_exports` 59, `test_import_assistant` 42, `test_image_import` 71,
`test_product_destination_import` 53. Pasadas focales: 527 OK (esos módulos más
`test_import_media`, `test_import_template`, `test_unit_import`, `test_request_limits`,
`test_storefront_image_cleanup`, `test_storefront_media`, `test_product_visibility`,
`test_stock_units`) y 327 OK (clases de importación, producto, pedidos, carrito y checkout de
`store/tests.py`). Jest completo 1331/1331 en 120 suites; `tsc --noEmit` limpio; lint 0 errores y
22 avisos; `makemigrations --check --dry-run` sin cambios. **La suite completa del backend,
Playwright y el ensayo NO se corrieron aquí.** Mutaciones de estas correcciones: 64, todas
detectadas (una sobrevivió a la primera pasada y se cerró con una prueba).

**Validación final de la rama (2026-10-13).** Sobre `0ed1b33`: suite completa del backend en
PostgreSQL, 6996 pruebas, 0 fallos, 6 omitidas (6716 en `master` `14e658c`); `sh deploy/rehearsal.sh` `ENSAYO: OK` (149);
Playwright 97/97 (`product-destination`, `import-split`, `storefront`, `tax-breakdown`,
`krypton-smartform`) contra servidores locales de la rama; y un recorrido en navegador real, sin
errores de consola: la acción en bloque viaja con `?company=` explícito y deja la selección vacía;
un producto ocultado con el carrito ya armado sale en el carrito como «Ya no está disponible»,
el pago no deja enviar y enlaza al carrito, y al quitar la línea el pago vuelve a abrirse; una
imagen en lote por nombre de archivo se aplica y su resultado enlaza a la galería; la descarga
del catálogo llega como `productos-para-cargar.xlsx`.

**Segunda revisión independiente (`ac6a061..0ed1b33`).** Sin P0, sin P1, sin regresiones. Cada
corrección cierra su hallazgo contra la prueba original; 24 mutantes sobre el código de las
correcciones, 24 detectados. De lo que dejó anotado se corrigió uno (`af0b374`): las tarjetas del
catálogo y del ZIP avisan cuando se pidió «Inactivos», porque un archivo para volver a cargar
lleva sólo activos. El resto queda como deuda en `docs/AUDIT_MEMORY.md` §10:
IMPORT-RELOAD-RENAMED-NO-CODE, ORDERS-V1-HIDDEN-PRODUCT, ORDER-LINE-CLOSED-WASTE e
IMG-IMPORT-OLD-JOB-WORDING. Sobre `af0b374` (sólo cambia el frontend): Jest 1332/1332 en 120
suites, `tsc --noEmit` limpio, lint 0 errores y 22 avisos, `ENSAYO: OK` (149).

## 2026-10-13 — INVENTORY-UNIFIED-01 (partes 3 y 4): centro de gestión masiva e imágenes en lote

Misma rama (`feat/inventory-unified`), sobre `74c6112`. **Sólo en la rama: sin push, sin PR, sin
desplegar.** Una migración, `0122` (sólo opciones de un campo: el tipo de trabajo
`product_images`). Sin capacidades nuevas, sin rutas bajo `api/v1/internal/`, sin cambios en
`deploy/` ni en `request_limits.py`, y sin ninguna vista multipart nueva. Decisión:
DEC-INVENTORY-UNIFIED-01 (ampliada).

| | Qué | Estado |
|---|---|---|
| 3.1 | Pantalla `/admin/inventory/bulk` y UNA entrada de menú «Gestión masiva de inventario», visible con cualquiera de `products.manage`, `inventory.adjust`, `products.view`, `inventory.view`. Las tres cargas conservan entrada, ruta y asistente | EN LA RAMA, probado |
| 3.2 | Secciones: «Importar» (tres tarjetas con qué hace y qué no cada carga, del mismo texto que sus avisos), «Exportar» (el panel de la parte 2, que sigue además en Carga de stock), «Imágenes» e «Historial» (todas las cargas que la persona puede ver, con «Descargar errores») | EN LA RAMA, probado |
| 3.3 | «¿Qué archivo tengo?»: `POST /api/admin/imports/inspect/` (misma ruta y vista) responde `suggested_import_type` (`products`, `stock`, `units`, `images`, `report`, `unknown`; `forbidden` si no puede hacer esa carga) con un código de motivo y su frase, sólo por hojas y cabeceras. No lee filas ni escribe nada | EN LA RAMA, probado |
| 3.4 | Pruebas: cada plantilla, cada descarga en sus dos modos, los libros históricos (plantillas de varias hojas, exportaciones del POS con su rótulo y su columna de stock, libro del propietario), ambiguos, permisos y empresa | EN LA RAMA |
| 4.1 | Carga sólo de imágenes: muchos archivos y/o un ZIP; cada archivo va a su producto por el nombre (`<código>.<ext>`, `_N` / `-N` / ` (N)` para el orden) o por un libro «Imágenes» opcional | EN LA RAMA, probado |
| 4.2 | Vista previa sin efecto (trabajo `product_images`): por producto, qué se añade, cuál será la principal, qué ya estaba, qué pasa de 12; archivos sin producto o ambiguos; modos `add` y `replace` (éste, con palabra de confirmación) | EN LA RAMA, probado |
| 4.3 | Aplicar: todo o nada, una vez, trabajo y productos bloqueados en orden, por `product_media`; se detiene si el catálogo o una galería cambiaron; una línea de auditoría por carga | EN LA RAMA, probado |
| 4.4 | Sólo se escriben filas de galería e `image_url` (fijado con una prueba) | EN LA RAMA, probado |
| 4.5 | Todas las protecciones de `import_media` por este camino; un código de otra empresa nunca casa; `products.manage`; lo guardado de una vista previa fallida o abandonada se retira | EN LA RAMA, probado |
| 4.6 | Sección «Imágenes» del centro y enlace «Añadir imágenes en lote» desde Productos | EN LA RAMA, probado |
| 4.7 | Auditoría de almacenamiento, sobre código y archivos de despliegue: `docs/imagenes-y-evidencias.md` §3.7, §5.3 y §5.4 | DOCUMENTADO (sin acceso al servidor) |

**La restricción de la ruta se cumplió.** El lote de imágenes entra por
`POST /api/admin/products/import/preview/` con `kind=images` —la misma ruta y la misma vista, la
única con nivel `product_import`—. `store/test_request_limits.py` pasa sin cambios.

**Decidido aquí, para confirmar con el propietario.** Un archivo sin producto o con nombre
ambiguo se lista y no se carga, pero no impide aplicar el resto; el código se compara exacto,
mayúsculas incluidas (deuda IMG-IMPORT-CASE); un catálogo con una columna de stock sigue yendo a la
carga de productos (como ya lo trataba esa carga) y la respuesta lo dice.

**Pruebas (sobre `406107e`, PostgreSQL, base `blackdog_focal`).** Nuevas:
`store/test_import_assistant.py` (42) y `store/test_image_import.py` (68, dos con dos conexiones
reales; la última se añadió en `8786d56`, tras las pasadas de abajo, y el módulo se volvió a correr
entero: 68 OK); Jest `bulk-inventory-centre` (29) y `bulk-image-import` (27). Pasadas focales: 560 OK
(los cuatro módulos nuevos de la fase, `test_import_media`, `test_import_template`,
`test_product_destination_import`, `test_unit_import`, `test_request_limits`,
`test_storefront_image_cleanup`, `test_storefront_media`, `test_stock_units`, `test_serial_pick`,
`test_product_visibility`) y 380 OK (clases C14/C15 de importación, las de producto, carrito y
checkout de `store/tests.py`, y la matriz de paridad). Jest completo 1303/1303 en 115 suites;
`tsc --noEmit` limpio; lint 0 errores y 22 avisos (ninguno nuevo); `makemigrations --check
--dry-run` sin cambios. **La suite completa del backend, Playwright y el ensayo NO se corrieron.**
Mutaciones de estas dos partes: 138; 9 sobrevivieron a la primera pasada: 6 se cerraron con
pruebas, 2 quitando una comprobación duplicada, y 1 queda como equivalente (el filtro de empresa
al bloquear productos: la comprobación del catálogo ya rechaza ese caso).

**Deuda registrada** (`docs/AUDIT_MEMORY.md` §10): IMG-IMPORT-CASE, IMG-IMPORT-CODE-CHARS,
IMG-IMPORT-REUPLOAD, IMG-IMPORT-REPLACE-REENCODE, IMG-IMPORT-ALT-TEXT, IMG-STORAGE-S3,
ASSISTANT-HEADERS-ONLY, BULK-CENTRE-LEGACY-BRIDGE, DOCS-CADDY-LIMIT-NOTE.

## 2026-10-13 — INVENTORY-UNIFIED-01 (partes 1 y 2): visibilidad en bloque y exportaciones

Rama `feat/inventory-unified`, desde `master` `14e658c`. **Sólo en la rama: sin push, sin PR, sin
desplegar.** Sin migraciones, sin capacidades nuevas, sin rutas bajo `api/v1/internal/`, sin cambios
en `deploy/` ni en `request_limits.py`. Decisión: DEC-INVENTORY-UNIFIED-01.

| | Qué | Estado |
|---|---|---|
| 1.1 | Una definición de «puede publicarse en bloque» (`store/product_publication.py`): activo, con precio, con imagen, con categoría. La ficha individual guarda como siempre | EN LA RAMA, probado |
| 1.2 | `POST /api/admin/products/bulk-visibility/` (`products.manage`): por ids o por el filtro de la lista + total esperado; 409 `selection_changed`; tope de 2000; una transacción con filas bloqueadas en orden; sólo escribe `is_published_online` y `updated_at`; resultado parcial con motivos; una línea de auditoría por lote | EN LA RAMA, probado |
| 1.3 | La lista `GET /api/admin/products/` filtra por `is_serialized` y cada fila trae `publication_problems`, sin una consulta por fila; el filtro es una función compartida con la acción en bloque | EN LA RAMA, probado |
| 1.4 | Un producto oculto o desactivado DESPUÉS de entrar a un carrito: la lectura del carrito lo devuelve `available: false` con id, nombre e imagen (sin precio ni stock), el cambio de cantidad se rechaza y quitarlo sigue funcionando. Matriz de exposición pública para oculto e inactivo | EN LA RAMA, probado |
| 1.5 | Panel › Productos: columna «E-commerce», filtros rápidos Todos / Publicados / Internos, filtro de tipo, casilla por fila y por página, «Seleccionar los N productos que coinciden», acciones con confirmación y resultado, 409 explicado | EN LA RAMA, probado |
| 2.1 | `GET /api/admin/inventory/exports/{products,stock,units,full}/` con `mode=reimport\|report` y filtros de sucursal, categoría, tipo, existencia, condición, visibilidad y estado | EN LA RAMA, probado |
| 2.2 | `products.view` para el catálogo; `inventory.view` y sucursales visibles para stock y equipos; las dos para el ZIP; «Costo» sólo con `inventory.adjust` | EN LA RAMA, probado |
| 2.3 | Celdas que no se ejecutan como fórmula (y se leen igual al recargar), códigos / series / IMEI como texto, libros en modo write-only, tope de 20 000 filas y de 25 MB para el ZIP, nombres de archivo fijos, `no-store`, límite de peticiones y una línea de auditoría por descarga | EN LA RAMA, probado |
| 2.4 | `GET /api/admin/inventory/export/` responde igual que antes (fijado con pruebas) | EN LA RAMA, probado |
| 2.5 | Panel «Exportar» (`InventoryExportPanel` / `InventoryExports`), por ahora en Inventario › Carga de stock junto a «Descargar inventario» | EN LA RAMA, probado |

**Lo que cambió de comportamiento existente, a propósito.**
- La lectura del carrito añade `available` a cada línea y recorta el producto de una línea que ya
  no se vende. `test_the_cart_that_holds_it_still_opens` sigue igual y sigue pasando: el carrito abre.
- Las tres cargas masivas rechazan una hoja llamada «Reporte de productos», «Reporte de stock» o
  «Reporte de equipos», y quitan UN apóstrofo inicial cuando va delante de `=`, `+`, `-`, `@`,
  tabulador o retorno (deuda IMPORT-UNGUARD).
- En la lista de productos, «Solo stock interno» pasa de la columna Estado a la columna E-commerce.

**No hay campo de marca** en el catálogo: no se añadió filtro ni columna de marca.

**Pruebas (sobre `0a229fb`, PostgreSQL, base `blackdog_focal`).** Nuevas: `store/test_bulk_visibility.py`
(58, dos con dos conexiones reales) y `store/test_inventory_exports.py` (53); Jest
`product-bulk-visibility` (34), `cart-unavailable` (10) e `inventory-export-panel` (33). Pasadas
focales: 550 pruebas OK (módulos nuevos, `test_import_template`, `test_product_destination_import`,
`test_unit_import`, `test_import_media`, `test_stock_units`, `test_serial_pick`,
`test_product_visibility`, `test_request_limits`, clases C14/C15 de importación y la matriz de
paridad) y 392 OK (carrito, `Phase40CheckoutValidationTest`, `M5Checkout*` y las clases que usan
`/api/admin/products/`). Jest completo 1247/1247 en 113 suites; `tsc --noEmit` limpio; lint 0
errores y 22 avisos (ninguno nuevo). `makemigrations --check --dry-run`: sin cambios. **La suite
completa del backend y Playwright NO se corrieron en esta fase.** Mutaciones: 164, las 164
detectadas (4 sobrevivieron a la primera pasada y se cerraron: tres pruebas que no podían fallar y
una comprobación duplicada).

**Encontrado, anterior a la fase.** `store.tests.P0ECartConcurrencyTest.test_many_at_once_still_add_up`
falla en su `setUp` cuando esa clase es la primera `TransactionTestCase` del proceso (reproducido
igual sobre `14e658c`); deuda P0E-CART-RACE-ORDER.

**Deuda registrada** (`docs/AUDIT_MEMORY.md` §10): INV-EXPORT-LEGACY-GUARD, INV-EXPORT-RESTORE-GAPS,
INV-EXPORT-IMPORT-LIMITS, INV-EXPORT-NAME-MATCH, INV-EXPORT-SYNC, INV-EXPORT-THROTTLE-SHARED,
INV-EXPORT-LEGACY-BRIDGE-UI, INV-REPORT-RENAMED-SHEET, IMPORT-UNGUARD, BULK-VIS-CAP,
BULK-VIS-FORM-HINT, CART-UNAVAILABLE-COUNT, P0E-CART-RACE-ORDER.

## 2026-10-13 — CLIENT-SEC-01: ajustes tras la revisión previa al despliegue

Misma rama (`feat/client-sec-delivery`, PR #111, detenido hasta estos ajustes). La revisión de
`8ea3fe4..ab86212` encontró una regresión alta frente a producción y varios menores. Sin
migraciones, rutas ni capacidades nuevas.

| | Hallazgo | Estado |
|---|---|---|
| F1 | El pedido de un invitado cuyo documento está en la ficha de una CUENTA no se archivaba nunca: sin ficha no hay aviso, y el comprador no recibía «listo para recoger» ni «enviado». Caso honesto: quien compró una vez con sesión y ahora compra sin ella | CORREGIDO: al pagarse recibe una ficha propia SIN documento; la ficha de la cuenta no se toca y la cuenta no ve ese pedido |
| F2 | «La misma persona reintentando» se decidía sólo por el correo, que cualquiera puede escribir | CORREGIDO: nombre, teléfono y correo, los tres presentes e iguales |
| F4 | Un pedido pagado que perdía una carrera al archivarse quedaba sin ficha | CORREGIDO: se reintenta una vez |
| F5 | Si el archivado fallaba después de asignar la ficha, el pedido la conservaba en memoria y la confirmación del pago moría al confirmar | CORREGIDO: el pedido queda como estaba |
| F6 | Una ficha provisional inválida por otro motivo bloqueaba al personal con un 400 engañoso | CORREGIDO: al personal, el 409 con esa ficha; al pedido pagado, una ficha sin documento |
| F9 | `ops_status` iba a decir «N pedidos sin evaluar» durante 24 h tras cualquier despliegue | CORREGIDO: sólo cuenta los posteriores a la primera evaluación |
| F11 | Prueba que fallaba por azar (`'15.50'` dentro de una marca de tiempo) | CORREGIDA: compara claves y valores |
| F12 | Dos personas archivando el mismo documento a la vez: la segunda recibía un 500 | CORREGIDO: el mismo 409 |
| — | `test_tracking…test_every_order_is_born_with_a_link_nobody_can_guess` fallaba 1 de cada 100 veces | CORREGIDA: comprueba que el enlace no sale de la orden (identificador aleatorio; rotarlo da otro) |

**Queda como deuda, a propósito** (`docs/AUDIT_MEMORY.md`): CLIENT-SEC-PAID-SEED (F3),
CLIENT-SEC-CREATED-BY-SETNULL (F7), CLIENT-SEC-COUNTER-TYPO (F8), CLIENT-SEC-APP-CE (F10),
CLIENT-SEC-ORDER-NO-EMAIL (F13). Se cerró CLIENT-SEC-UNLINKED-GUEST.

**Pruebas.** `store/test_client_sec.py` pasa de 322 a 352 pruebas (cinco con dos conexiones reales: personal y pago a la vez, dos pagos, dos personas archivando, y los dos bloqueos de fila), `test_ops_status` gana una. Cambió una prueba de la ronda anterior, la que esperaba que el pedido de un invitado con el documento en la ficha de una cuenta quedara sin ficha tras pagarse. Mutaciones de esta ronda: 33; 5 sobrevivieron a la primera pasada — 4 se cerraron con pruebas (datos vacíos en los dos lados; un solo nombre; los dos bloqueos, que las pruebas de carrera no necesitaban porque el reintento las salvaba) y 1 es equivalente (si la ficha provisional no puede soltar el documento, el personal recibe el mismo 409 por la regla de la base). Corridas sobre el código de `a7e38d0` (PostgreSQL): `test_client_sec`, `test_ops_status` y `test_tracking`, 425; `test_delivery_base`, `test_mail_notifications`, `test_deploy_scripts` y los diez módulos de pagos, 504 y 3 omitidas; las clases de clientes, checkout, avisos y despacho de `store/tests.py`, 902. Cero fallos. `makemigrations --check` sin cambios. `store.tests.M12BConcurrencyTest` falla si se ejecuta SOLA (deuda M12B-CONCURRENCY-ISOLATION, anterior a esta rama); dentro de la selección pasa. **No se corrió** la suite completa del backend, el frontend (no cambió), Playwright ni el ensayo de despliegue.

## 2026-10-13 — CLIENT-SEC-01 y DELIVERY-01: correcciones tras la tercera revisión independiente

Misma rama (`feat/client-sec-delivery`). **Sólo en la rama: sin push, sin PR, sin desplegar.**
La tercera revisión (`8ea3fe4`) confirmó las siete correcciones de la ronda anterior y no
encontró nada crítico, pero dejó abierto un camino de gravedad alta **anterior a la fase**, y
la regla del propietario es no abrir un PR con uno abierto. Se corrige aquí, con varios
menores. Sin migraciones nuevas.

| | Hallazgo | Estado |
|---|---|---|
| F1 | Un checkout anónimo y SIN PAGAR con el DNI de otra persona y el contacto propio dejaba una ficha con ese documento. El pedido posterior de la persona real se enlazaba a ella y sus avisos iban al correo de la ficha; en el mostrador el personal recibía «ya existe» hacia esa ficha, una reparación abierta ahí avisaba a quien la sembró, y con el enlace y el DNI se quedaba con la ficha | CORREGIDO en tres partes: (a) el aviso de un pedido va al correo escrito en ese pedido; (b) un invitado sólo se enlaza por documento con una ficha ESTABLECIDA (creada por el personal, o con un pedido pagado o una reparación), y su pedido se archiva al pagarse; (c) en el mostrador una ficha no establecida suelta el documento, en una transacción y con auditoría, y una establecida conserva el 409 |
| F2 | Un checkout con sesión y correo probado seguía escribiendo un documento libre en la ficha, por encima del cupo diario y sin auditoría, y el perfil servía de oráculo | CORREGIDO: un checkout con sesión nunca escribe el documento en la ficha de la cuenta; se añade en «Mi cuenta» |
| F3 | Una cuenta con el correo probado podía guardar en «Mi cuenta» el DNI libre de un extraño, y el personal recibía el 409 hacia esa ficha | CORREGIDO por F1(c): la ficha que una cuenta declaró para sí, sin pedido pagado ni reparación, no es establecida; suelta el documento, conserva la ficha y no se le dice nada |
| F4 | Consecuencia de F2: quien compró con cuenta y pagó no podía añadir nunca su documento (ficha congelada) | CORREGIDO: un documento VACÍO no está congelado; su titular lo completa una vez con el correo probado, el cupo y la comprobación de conflicto. Cambiar uno existente sigue siendo de la tienda |
| F5 | Al desvincular una cuenta de una ficha, la siguiente cuenta enlazada leía las direcciones de la anterior | CORREGIDO: desvincular apaga las direcciones de la ficha |
| F6 | Enviar otra guía junto con el enlace derivado anterior lo dejaba como «escrito» y caducado | CORREGIDO: un enlace devuelto tal como estaba no es un enlace escrito; `tracking_url_typed` viaja en la respuesta del personal |
| F7 | CE de la app sin regla; medio carácter en los dos enlaces (500); 200 filas quitadas bloqueaban la libreta; teléfono pegado con espacios duros; la frase de la retención omitía «listo para retiro» | CORREGIDOS los cinco |
| Auditoría B | `ops_status` no decía nada de la revisión; una persona en tres dispositivos contaba como tres compradores | HECHO: dos notas (nunca alarmas) —pedidos pagados que esperan revisión con la antigüedad del más viejo, y pedidos en línea del último día sin evaluación—; mismo teléfono, correo y documento es un solo comprador |
| Auditoría C | El mutante «`settle` sin propósito» sobrevivía | FIJADO con una prueba |

**Una decisión tomada aquí, para que el propietario la conozca.** La regla (b) tal como se
pidió dejaba sin ficha para siempre a quien reintenta un pago: su primer intento (sin pagar)
crea la ficha provisional y el segundo ya no se enlazaba a ella. Por eso el pedido de un
invitado se archiva **al pagarse**: si la ficha provisional lleva el mismo correo que el
pedido, es la misma persona y se enlaza; si no, esa ficha suelta el documento (auditado) y el
pedido recibe la suya. Sin esto, además, la persona real del caso F1 no recibiría ningún
aviso de su pedido.

**No se tocó, por ser decisión del propietario:** los pesos y umbrales de la evaluación, y si
el monto por sí solo abre una revisión (deuda CLIENT-SEC-RISK-TUNING, con lo que midió la
revisión); el caso «mismo número con otro tipo de documento»; y la IP en las filas de
auditoría del comprador. Queda anotado también CLIENT-SEC-PAID-SEED: quien PAGA un pedido con
el documento de otra persona sí deja una ficha establecida con su contacto.

**Rutas, capacidades, límites y migraciones:** ninguno nuevo. Una acción de auditoría nueva,
`customer_document_released`. La superficie interna de v1 sigue en 89.

**Pruebas.** `store/test_client_sec.py` pasa de 285 a 322 pruebas, `store/test_delivery_base.py` de 117 a 129 y `store/test_ops_status.py` gana 5. Las de F1 y F3 se vieron fallar antes de su código; las de F2, F4–F7 y las dos auditorías se escribieron antes que el código pero se ejecutaron por primera vez junto con él, y cada regla se rompió después a propósito. Cambiaron pruebas existentes, todas por una regla que cambió: `Phase4OrderLinkTest.test_an_authenticated_buyer_without_a_record_gets_their_own` (la ficha nace sin documento); las dos de `Phase4ApiTest` sobre el 409 marcan su ficha como creada por el personal; en `test_mail_notifications`, el pedido de las pruebas lleva el correo de su comprador, el aviso sobre un pedido de OTRA empresa ya no envía ningún correo (antes: un correo sin botón) y el de la otra empresa usa un pedido real; en `test_client_sec`, las fichas de mostrador de las pruebas de la primera revisión llevan `created_by`, «el siguiente pedido encuentra la ficha» vale tras pagar el primero, y las pruebas de la segunda revisión sobre el documento del checkout con correo probado se sustituyeron; en `test_delivery_base`, la respuesta del personal gana `tracking_url_typed` y la prueba del tope espera reutilización en vez de rechazo. Mutaciones de esta ronda: 68; 6 sobrevivieron a la primera pasada — 4 se cerraron con pruebas (la ficha establecida de una cuenta se enlazaba a un invitado en el checkout; la prueba de «todo o nada» rompía la propia liberación; se podía reutilizar la fila de otro comprador; un pedido con menos datos de contacto contaba como el mismo comprador) y 2 eran código muerto que se retiró (un parámetro `exclude_pk` inalcanzable y una comprobación ASCII que el patrón ya hace). Corridas finales sobre el código de `09a80c9` (PostgreSQL): los dos módulos de la rama, 451; pagos (diez módulos), verificación, Google, invitaciones, registro, equipos con serie, seguimiento, correo y avisos por correo, impresión y documentos, 781 y 3 omitidas; las clases de checkout, clientes, pedidos, avisos, despacho, v1 y documentos de `store/tests.py`, 2184; y `test_ops_status`, `test_whatsapp`, `test_service_notifications` y `test_deploy_scripts`, 105. En la primera pasada el segundo grupo tuvo UN fallo, de una prueba anterior que falla por azar (`test_tracking…test_every_order_is_born_with_a_link_nobody_can_guess`: el número de la orden apareció dentro de un token aleatorio; deuda TRACKING-TOKEN-TEST-FLAKE); repetido el grupo entero sobre el mismo commit, 781 sin fallos. Lo demás, cero fallos. `makemigrations --check` sin cambios (esta ronda no toca modelos ni migraciones). Frontend, sobre `a1ae970` (el árbol de frontend es el mismo): Jest 1170 en 110 suites, `tsc` y lint sin errores (22 avisos anteriores). **No se corrió** la suite completa del backend, Playwright, `npm run build` ni el ensayo de despliegue, y nada se miró en un navegador.

## 2026-10-12 — CLIENT-SEC-01 y DELIVERY-01: correcciones tras la segunda revisión independiente

Misma rama (`feat/client-sec-delivery`). **Sólo en la rama: sin push, sin PR, sin desplegar.**
Una segunda revisión independiente (`06df091..ba20986`) no dejó ningún hallazgo crítico, pero
sí dos altos y cuatro medios que impedían fusionar, y una lista de menores. Todos corregidos,
cada uno con su prueba vista fallar antes. Sin migraciones nuevas: `0118` y `0119` —que no
están publicadas— cambian (el tercer propósito de `AccountToken`; `OrderDelivery` con
`tracking_url_typed` y sin `source_address`) y deshacer `0121` ya no borra nada.

| | Hallazgo | Estado |
|---|---|---|
| P1-1 | Un checkout con sesión, sin pagar y repetible, dejaba en la ficha de la cuenta el documento de un extraño: sin correo probado y sin cupo | CORREGIDO: la ficha de una cuenta toma el documento del checkout sólo si la cuenta probó su correo y nadie en la tienda lo tiene; si no, nace sin documento y el pedido conserva lo escrito. La ficha de un invitado, como siempre |
| P1-2 | Una cuenta apagada después de pedir el código de «Verificar mi correo» se reactivaba sola por la recuperación, el enlace o el reenvío | CORREGIDO: ese código tiene su propio propósito de token (`own_email_proof`), que ninguno de esos caminos mira, y su correo no lleva enlace |
| P2-1 | Una fecha de envío del año 9999 se guardaba y dejaba el pedido ilegible (500 en todas sus lecturas); un flete «1e30» y un texto con medio carácter respondían 500 | CORREGIDO: fecha entre 2000-01-01 y hoy + 1 año; la escritura se relee antes de confirmarse; el flete se acota antes de redondearse; el medio carácter se rechaza donde los caracteres de control |
| P2-2 | El enlace de seguimiento se quedaba con la página del transportista anterior, o se borraba al corregir la guía | CORREGIDO: `tracking_url_typed` dice si el enlace es escrito o derivado; el derivado sigue a transportista y guía, el escrito sólo cambia al escribirlo |
| P2-3 | El cupo de documento vivía en la memoria del proceso; un doble toque gastaba dos | CORREGIDO: se cuenta en la auditoría (`customer_document_change_attempted`, sólo el resultado) con la fila de la cuenta bloqueada; el doble toque gasta uno. `CACHES` no se tocó: los límites de DRF siguen en memoria (deuda CLIENT-SEC-THROTTLE-MEMORY, junto a THROTTLE-CACHE-01) |
| P2-4 | La velocidad ya no veía lo que busca: el séptimo pedido de una sesión tras seis pagados salía `low`; cuatro invitados de una dirección que piden y luego pagan no dejaban señal | CORREGIDO: cuenta también pedidos pagados (el sexto, por origen en una hora o por contacto en un día) y se vuelve a preguntar cuando el pedido se paga. Los pedidos sin pagar siguen sin contar: reintentar UNA compra es una compra |
| P3 | Dígitos de otros alfabetos en los dos checkout; una ficha archivada se borraba al reclamar una reparación y admitía direcciones; el código no estaba ligado a la dirección; una entrada de agencia con el transportista apagado no se podía corregir; la tarjeta de entrega mostraba el usuario; deshacer `0121` borraba todo; las direcciones quitadas crecían sin tope; `source_address` no la escribía nadie | CORREGIDOS los nueve: 0-9 en documento y teléfono del checkout web y v1; una ficha archivada nunca es «vacía» y su libreta no admite direcciones nuevas (respuesta neutra); la dirección entra en el HMAC del código; el transportista sólo se juzga cuando cambia; «Personal de la tienda»; marcha atrás sin efecto; tope de 200 filas por comprador; columna retirada |

**Reglas que ninguna prueba fijaba, ahora fijadas:** que una ficha hecha por el personal o
con razón social no es «vacía»; el efecto de los límites de los dos pasos de verificación en
v1 (dejan de responder, no sólo figuran en la clase); y la línea del registro de seguridad
cuando un código se agota.

**Lo que se decidió aquí y conviene que el propietario sepa.** (1) Cuando el documento del
checkout ya está en otra ficha, la ficha de la cuenta nace sin documento y la del titular no
se toca; nadie recibe un aviso. (2) Los umbrales de «pedidos pagados» son seis y seis. (3) El
tope de la libreta es de 200 filas y, al llegar, no hay salida sin que la tienda decida borrar
las quitadas. (4) En una ficha archivada se puede seguir quitando una entrada o cambiando la
predeterminada; lo que no se puede es añadir o reescribir una dirección. Las cuatro están en
`docs/AUDIT_MEMORY.md`.

**Rutas, capacidades y límites:** ninguno nuevo. La superficie interna de v1 sigue en 89.

**Pruebas.** Cada corrección empezó por una prueba vista fallar; las reglas que sólo se fijaron se comprobaron rompiéndolas. `store/test_client_sec.py` pasa de 228 a 285 pruebas (una más con dos conexiones reales: dos intentos de documento a la vez en dos tiendas gastan dos) y `store/test_delivery_base.py` de 88 a 117. Cambiaron pruebas existentes: `Phase4OrderLinkTest.test_an_authenticated_buyer_without_a_record_gets_their_own` prueba antes el correo de la cuenta (la ficha ya no toma el documento sin eso); la prueba del enlace del correo de verificación propia se sustituyó por la que exige que NO lleve enlace; la del nombre de quien escribió la entrega espera «Personal de la tienda» y no el usuario; la de la marcha atrás de `0121` espera que no borre; y las que nombraban `source_address` dejaron de hacerlo. Mutaciones de esta ronda: 93 (57 de cuentas, clientes y evaluación; 36 de entrega); 3 sobrevivieron a la primera pasada y las tres se cerraron con pruebas (el titular de un documento se buscaba en cualquier empresa; `confirm_own` podía leer un token de registro; los teléfonos de la prueba del checkout no llegaban al patrón). Corridas finales sobre el código de `36970eb` (PostgreSQL): los dos módulos de la rama, 402; pagos (diez módulos), verificación, Google, invitaciones, registro, equipos con serie, seguimiento, correo, impresión y documentos, 781 y 3 omitidas (las de credenciales reales y el `Caddyfile`); y las clases de checkout, clientes, pedidos, avisos, despacho, v1 y documentos de `store/tests.py`, 2184. Cero fallos. Las migraciones `0118`–`0121` se deshicieron y se volvieron a aplicar en una base de PostgreSQL con un registro de entrega escrito por el personal: deshacer `0121` lo conservó, deshacer hasta `0117` quitó las cuatro tablas, y al volver `OrderDelivery` tiene `tracking_url_typed` y no tiene `source_address`; `makemigrations --check` sin cambios. Frontend (sólo cambió un comentario): Jest 1169 en 110 suites, `tsc` y lint sin errores (22 avisos anteriores). **No se corrió** la suite completa del backend, Playwright, `npm run build` ni el ensayo de despliegue, y nada se miró en un navegador.

## 2026-10-12 — DELIVERY-01 (base): transportistas, libreta de direcciones y registro de entrega

Rama `feat/client-sec-delivery`, sobre CLIENT-SEC-01. **Sólo en la rama: sin push, sin PR, sin
desplegar.** Migraciones `0119_delivery_base` (tablas) y `0121_delivery_records_backfill`
(datos, por lotes, no atómica). Decisión: DEC-DELIVERY-01. **Aditivo: no cambia ningún
comportamiento anterior.** `Order.delivery_method` y sus opciones, el checkout web y v1 (su
huella y sus campos prohibidos incluidos), los precios, los documentos y los correos son los de
antes; el flete es un dato y no entra a ningún total.

| | Punto | Estado |
|---|---|---|
| 1 | `Carrier` por empresa (agencia o courier, plantilla de seguimiento https con `{guia}`). `GET/POST /api/admin/carriers/`, `PATCH /api/admin/carriers/<id>/`: leer con `sales.orders.view`, cambiar con `settings.manage` sobre toda la empresa; se desactivan, no se borran; nada sembrado. Sección «Transportistas» en Configuración, con dos sugerencias que sólo rellenan el nombre | IMPLEMENTADO en la rama |
| 2 | `CustomerAddress`: la libreta del comprador en cada tienda (puerta o agencia, destinatario distinto, una predeterminada, tope de diez). `/api/account/addresses/…` y `/api/v1/customer/<slug>/addresses/…`. Lo ajeno responde 404 en todos los verbos. Límite propio de escrituras; auditoría con nombres de campo | IMPLEMENTADO en la rama |
| 3 | `OrderDelivery`: el registro de entrega de cada pedido en línea, creado con el pedido desde las columnas del pedido, sin poder hacer fallar un checkout; nunca para una venta de caja. La migración 0121 lo escribe para los pedidos en línea que ya existían | IMPLEMENTADO en la rama |
| 4 | El personal escribe la entrega: `GET/PATCH /api/admin/orders/<id>/delivery/` y `GET/PATCH /api/v1/internal/<slug>/orders/<id>/delivery/` (`sales.orders.view` / `sales.orders.manage`, por `visible_orders`). Tarjeta «Entrega» en el detalle del pedido | IMPLEMENTADO en la rama |
| 5 | El comprador ve transportista, agencia, guía y enlace de seguimiento (sólo https) en sus pedidos, en la web y en `V1CustomerOrderSerializer`, como un bloque añadido `delivery` | IMPLEMENTADO en la rama |
| 6 | «Mis direcciones» en `/account`; en el checkout, «Usar una dirección guardada», que sólo rellena el formulario en el navegador | IMPLEMENTADO en la rama |

**Rutas nuevas:** 10 (dos de transportistas, tres de libreta en la web, tres en v1, una de
entrega en el panel y una en v1). La superficie interna de v1 pasa de 88 a 89. **Capacidades:**
ninguna nueva. **Un límite nuevo:** `account_address_write`.

**Lo que no se hizo, y por qué.** Ofrecer «recojo en agencia» en el checkout, cobrar el flete
e imprimir los datos nuevos en documentos o correos son decisiones del propietario para otra
fase. El checkout no envía qué dirección de la libreta se usó (y el registro de entrega no
apunta a la libreta). El comprador no ve la clave de recojo ni el destinatario del pedido. La app no dibuja
nada de esto todavía (las rutas v1 existen y están probadas).

**Pruebas.** Cada prueba se vio fallar antes de su código. `store/test_delivery_base.py`: 88 pruebas, con dos de concurrencia real (dos conexiones) para la dirección predeterminada. Cambiaron pruebas existentes: las tres de pagos que borran pedidos borran antes su registro de entrega; el contador de rutas internas pasa a 89; y las dos pruebas que fijan lo que el comprador lee de un pedido ganan la clave `delivery`. Mutaciones: 94 de backend, 5 sobrevivieron a la primera pasada — 3 se cerraron con pruebas y 2 son equivalentes (la base de datos repite la regla del nombre único, y `ignore_conflicts` la del relleno) —; 65 de frontend con las de la revisión, 1 sobrevivió y se cerró. Corridas finales sobre el código de `b6fa027` (PostgreSQL): los dos módulos de la rama, 316; pagos (diez módulos), verificación, Google, invitaciones, registro, equipos con serie, seguimiento, correo, impresión y documentos, 781 y 3 omitidas (las de credenciales reales y el `Caddyfile`); y las clases de checkout, clientes, pedidos, avisos, despacho, v1 y documentos de `store/tests.py`, 2184. Cero fallos. Las migraciones `0118`–`0121` se deshicieron y se volvieron a aplicar; `makemigrations --check` sin cambios. Frontend: Jest 1169 en 110 suites, `tsc` y lint sin errores (22 avisos anteriores). **No se corrió** la suite completa del backend, Playwright, `npm run build` ni el ensayo de despliegue, y nada se miró en un navegador.

## 2026-10-12 — CLIENT-SEC-01: correcciones tras la revisión independiente

Misma rama. Una revisión independiente de `06df091` dejó un hallazgo grave (de raíz anterior a
la fase), cuatro medios y varios menores. Todos corregidos, cada uno con su prueba vista fallar
antes. Migración nueva `0120_client_sec_verified_backfill`; `0118` queda sólo con esquema.

| | Hallazgo | Estado |
|---|---|---|
| F1 | Una cuenta podía dejar en su ficha el documento de un extraño y recibir después el pedido de invitado de ese extraño, con sus avisos | CORREGIDO EN LA RAÍZ: un invitado sólo enlaza por documento con una ficha sin cuenta; si no, el pedido queda sin enlazar. Enmienda a DEC-CUSTOMER-01 |
| F2 | Guardar un documento servía para averiguar si era de otro cliente, 20 veces por hora, y lo tomaba | CORREGIDO: sólo una cuenta con el correo verificado, y tres cambios de documento al día por cuenta |
| F3 | La cola de revisión y la campana se llenaban con pedidos sin pagar; reintentar un pago disparaba `velocity_ip`; dos pedidos sin pagar marcaban el siguiente pedido de un tercero | CORREGIDO: la revisión empieza al pagar; la velocidad cuenta compradores, y a los demás sólo si pagaron |
| F4 | Guardar «Mi cuenta» antes de reclamar una reparación bloqueaba el reclamo para siempre | CORREGIDO: una ficha vacía cede el paso al reclamo probado |
| F5 | El titular podía borrar o cambiar el documento de una ficha archivada | CORREGIDO: una ficha archivada no cambia; el documento queda fijo si la ficha la creó el personal o tiene un pedido pagado; no se puede dejar en blanco |
| F6 | Con la retención encendida, «listo para retiro» salía y avisaba al comprador | CORREGIDO: la retención lo incluye |
| F7 | Una cuenta activa no tenía cómo verificar su correo | CORREGIDO: código de 6 dígitos a su propio correo (`account/verify-email/send/` y `confirm/`, web y v1); todo enlace de recuperación consumido deja la prueba |
| F8 | Dígitos de otros alfabetos pasaban por DNI, RUC o teléfono | CORREGIDO: sólo 0-9 |
| F9 | Un carácter nulo en un nombre respondía 500 | CORREGIDO: se rechazan los caracteres de control |
| F10 | `velocity_ip` usaba la dirección IPv6 completa; `first_order` hacía un OR a través de un JOIN | CORREGIDO: /64, y dos consultas |
| F11 | La migración 0118 rellenaba datos dentro de la transacción del `ALTER TABLE` | CORREGIDO: 0118 y 0119 sólo esquema; 0120 y 0121 sólo datos |
| F12 | Quien revisa aparecía por su nombre de usuario; `ip_hash` no caduca; IP del comprador en la auditoría del perfil | El nombre: CORREGIDO («Personal de la tienda»). Lo demás: deuda CLIENT-SEC-IPHASH-RETENTION, y la auditoría sigue el precedente de toda escritura auditada |

**Rutas nuevas:** 4 (enviar y confirmar el código, en la web y en v1). **Dos límites nuevos:**
`account_verify_send` y `account_verify_confirm`. Sin capacidades nuevas.

**Pruebas.** Cada corrección empezó por una prueba vista fallar. `store/test_client_sec.py` pasa de 155 a 228 pruebas, tres de ellas con dos conexiones reales (el bloqueo de una revisión y el de un guardado de perfil, que ninguna prueba fijaba). Mutaciones de las correcciones: 78, 3 sobrevivieron a la primera pasada y las tres se cerraron con pruebas. Corridas finales sobre el código de `b6fa027` (PostgreSQL): los dos módulos de la rama, 316; pagos (diez módulos), verificación, Google, invitaciones, registro, equipos con serie, seguimiento, correo, impresión y documentos, 781 y 3 omitidas (las de credenciales reales y el `Caddyfile`); y las clases de checkout, clientes, pedidos, avisos, despacho, v1 y documentos de `store/tests.py`, 2184. Cero fallos. Las migraciones `0118`–`0121` se deshicieron y se volvieron a aplicar; `makemigrations --check` sin cambios. Frontend: Jest 1169 en 110 suites, `tsc` y lint sin errores (22 avisos anteriores). **No se corrió** la suite completa del backend, Playwright, `npm run build` ni el ensayo de despliegue, y nada se miró en un navegador.

## 2026-10-12 — CLIENT-SEC-01 (fase 1): perfil del comprador, correo verificado, evaluación y revisión de pedidos

Rama `feat/client-sec-delivery` (desde `master` `dfc817a`). **Sólo en la rama: sin push, sin
PR, sin desplegar.** **Migración `0118_client_sec`** (aditiva y reversible; sus datos, en `0120`). Decisión:
DEC-CLIENT-SEC-01. **Todo es consultivo por defecto:** con los ajustes como nacen, ningún
checkout, pago o despacho se comporta distinto que antes.

| | Punto | Estado |
|---|---|---|
| 1 | El correo verificado es un hecho propio (`UserProfile.email_verified_at`), escrito por los caminos que lo prueban: enlace, código, recuperación que termina una cuenta, Google con esa misma dirección, invitación de personal. La migración copia las pruebas que ya existían; los demás quedan en «desconocido». Campo añadido en `auth/me/` (web) y `v1/auth/me/` | IMPLEMENTADO en la rama |
| 2 | Perfil propio del comprador: `GET/PATCH /api/account/profile/` (tienda por Host) y `GET/PATCH /api/v1/customer/<slug>/profile/`. Un solo validador (`identity_validation`): celular peruano, DNI de 8 dígitos, RUC de 11 con prefijo y dígito de control, CE de 6 a 12. Un documento que ya es de otro cliente de la tienda se rechaza con UNA frase genérica. Límite propio de guardados (20 por hora y cuenta). Auditoría con nombres de campo. Página «Mi cuenta» | IMPLEMENTADO en la rama (ver las correcciones de arriba: F2, F5, F7) |
| 3 | Evaluación de cada pedido de la web y de la app al crearse (`OrderRiskAssessment`, `order_risk.assess`): ocho señales con peso, nivel por umbrales, revisión pendiente —desde que el pedido se paga— con nivel alto o cliente archivado. Guarda códigos, no datos; el origen, como HMAC (`ip_hash`). Nunca hace fallar un checkout. Las ventas de caja no se evalúan | IMPLEMENTADO en la rama |
| 4 | Revisión manual: `POST /api/admin/orders/<id>/risk-review/` y `POST /api/v1/internal/<slug>/orders/<id>/risk-review/` (`sales.orders.manage`, alcance de `visible_orders`, una decisión por pedido, 409 después). La evaluación aparece en el detalle del pedido (`sales.orders.view`); las listas llevan `risk_level` y `review_status` y aceptan `?review=`. El comprador no ve nada | IMPLEMENTADO en la rama |
| 5 | Aviso interno `commerce.order.review_needed` (advertencia, sólo en el panel) a quien tiene `sales.orders.manage` y puede ver el pedido: número y nivel, sin motivos ni datos del comprador | IMPLEMENTADO en la rama |
| 6 | Retención opcional, APAGADA: `CompanySettings.hold_orders_pending_review`. Sólo encendida, «listo para retiro», «enviado» y «entregado» responden 409 (`review_pending`) mientras la revisión esté pendiente; la levanta aprobar o rechazar. Las dos opciones (con `risk_review_amount`) están en Configuración › Revisión de pedidos | IMPLEMENTADO en la rama |
| 7 | Panel: etiqueta en la lista de pedidos, filtro «Por revisar», tarjeta «Revisión del pedido» con Aprobar / Rechazar | IMPLEMENTADO en la rama |

**Rutas nuevas:** 4 (dos del comprador, dos de revisión). La superficie interna de v1 pasa de
87 a 88 rutas. **Capacidades:** ninguna nueva; cambiar las dos opciones exige `company.manage`
(la de la pantalla) **y además** `settings.manage`. **Un límite nuevo:** `account_profile_write`.
**Un evento nuevo** en el catálogo de avisos.

**Lo que no se hizo, y por qué.**
- **Los tres validadores anteriores** (checkout web, checkout v1, ficha del panel) no se
  tocaron: siguen juzgando teléfono y documento cada uno a su manera (CLIENT-SEC-VALIDATORS).
- **El nombre del evento** es `commerce.order.review_needed`, con la forma del catálogo, no
  `order_review_needed`.
- **En `v1/auth/me/` el campo va junto a `user`, no dentro:** el objeto `user` es el mismo que
  devuelve el login, clave por clave, y hay una prueba de contrato que los compara.
- **Las dos opciones de empresa** van por la API de configuración que ya existía, que pide
  `company.manage`; `settings.manage` se exige ADEMÁS para cambiarlas (no en su lugar).
- **En la app el perfil exige ser ya cliente de la empresa** (el portón de toda la superficie
  `customer/`); en la web basta la sesión, en la tienda que se está mirando.
- **Google prueba el correo sólo si la dirección que nombra es la de la cuenta**; la migración
  aplica la misma condición a las identidades ya vinculadas.

**Queda como deuda** (`docs/AUDIT_MEMORY.md`): CLIENT-SEC-VALIDATORS, CLIENT-SEC-RUC-CHECK,
CLIENT-SEC-IPHASH-RETENTION, CLIENT-SEC-V1-FIRST-PROFILE, CLIENT-SEC-MEASUREMENT-IP,
CLIENT-SEC-ORDER-PROTECT, CLIENT-SEC-APP-UI, CLIENT-SEC-NOTICE-LINK. (CLIENT-SEC-DOC-CLAIM,
CLIENT-SEC-VERIFY-ACTIVE, CLIENT-SEC-RESET-PROOF y CLIENT-SEC-DOC-ORACLE se cerraron con las
correcciones de arriba.)

**Pruebas** (al cerrar la fase 1, antes de la revisión; los números finales están en las dos
entradas de arriba)**.** Cada prueba se vio fallar antes de su código. Backend: `store/test_client_sec.py`,
155 pruebas; con los módulos de pagos, verificación, Google, invitaciones, registro, equipos
con serie, límites y correo: 862, 0 fallos, 3 omitidas (las de siempre: credenciales reales
y el `Caddyfile`). Cambiaron cuatro pruebas existentes: tres de pagos borran la evaluación
antes que el pedido (la protege, como ya hacían con los intentos de pago) y el contador de
rutas internas pasa de 87 a 88. Mutaciones de backend: 180, 11 sobrevivieron a la primera
pasada; 7 se cerraron con pruebas, 1 estaba mal planteada y se sustituyó, y 3 son
equivalentes (una condición de la migración que el mínimo ya hace, y la comprobación previa
de documento, que repite la regla de la base). Frontend: Jest 1123 en 107 suites, `tsc` y
lint sin errores (22 avisos anteriores); 41 mutaciones, 4 sobrevivieron y se cerraron.
La migración se deshizo y se volvió a aplicar en PostgreSQL. **No se corrió** la suite
completa del backend, Playwright, `npm run build` ni el ensayo de despliegue; el ancho de
la cabecera con el enlace nuevo no se midió en un navegador (por eso sólo aparece desde `xl`).

## 2026-10-11 — PAY-GUARD-03: la tercera auditoría — seis ajustes

Rama `feat/izipay-smartform-v4` (PR #110). **Sin migración, sin rutas, sin ajustes
nuevos.** La auditoría dio por cumplidos los puntos de aceptación; quedaban seis defectos
pequeños. No desplegado. Nada se probó contra la pasarela real.

| | Defecto | Estado |
|---|---|---|
| 1 | El checkout de la web no tenía límite para una cuenta con sesión | CORREGIDO: el mismo límite por usuario que la app |
| 2 | «SDK web» con `IZIPAY_ENV` en mayúsculas: el guardia buscaba cobros del entorno equivocado | CORREGIDO |
| 3 | Una pausa volvía sola con una fila apagada por un cambio de producto | CORREGIDO |
| 4 | El preflight no avisaba de `PAYMENTS_PUBLIC_TEST=2` (y la aplicación lo lee encendido) | CORREGIDO: avisa de todo lo que no sea vacío, `0`, `false`, `no` u `off` |
| 5 | El servidor en marcha no decía que el interruptor estaba encendido | CORREGIDO: ATENCIÓN en la comprobación de estado |
| 6 | El rechazo de un cambio ofrecía pausar cuando la pasarela cobra por variables del servidor y no hay nada que pausar | CORREGIDO |

**Queda como deuda** (`docs/AUDIT_MEMORY.md`): PAY-CAP-ATOMIC, PAY-WEB-ORPHAN-ORDER,
PAY-FOREIGN-GOLIVE, PAY-RECONCILE-CAPACITY, PAY-DEFERRED-WINDOW, PAY-NOGATEWAY-500.

**Pruebas.** Cada prueba se vio fallar antes de su corrección; 12 mutaciones, todas
detectadas. 13 pruebas nuevas (`test_payment_audit3`, `test_preflight`). Módulos de
pagos, checkout, consola, medición, preflight, estado y despliegue: 1017, 0 fallos, 3
omitidas. Las pruebas de la comprobación de estado miran una tienda con el interruptor
apagado (las pruebas corren con él encendido); una de ellas fallaba cuando el pedido
era el número 24 y se corrigió. No se corrió la suite completa, Playwright ni el ensayo.

## 2026-10-11 — PAY-GUARD-02: la segunda auditoría — TEST por entorno, pausar pagos, «no se pudo preguntar»

Rama `feat/izipay-smartform-v4`. **Migración `0117_payment_attempt_origin` ampliada**
(no estaba en ningún historial): añade `IntegrationConfig.paused`. **Sin rutas nuevas**
(pausar y reanudar son acciones de la ruta de acciones que ya existía). **Un ajuste
nuevo, `PAYMENTS_PUBLIC_TEST`, apagado por defecto.** Decisión: DEC-PAY-07. No
desplegado. **Nada se probó contra la pasarela real.**

| | Hallazgo | Estado |
|---|---|---|
| 1 | La puerta de TEST ignoraba las claves de las variables del servidor, y revocar la consola volvía a ellas | CORREGIDO: depende del entorno de lo que cobra; `PAYMENTS_PUBLIC_TEST` es la única excepción; revocar no cede la tienda al entorno |
| 2 | Con la pasarela caída o rechazando, la palabra sólo se ofrecía con un único intento abierto | CORREGIDO |
| 3 | Un comprador podía mantener bloqueado para siempre apagar, revocar o cambiar | CORREGIDO: «Pausar pagos nuevos» |
| 4 | Si `Order/Get` contesta con error a una referencia sin usar, cada compra abandonada bloqueaba dos horas | CORREGIDO: es «no se pudo preguntar» (palabra y auditoría) |
| 5 | La app podía multiplicar sin límite las consultas a la pasarela | CORREGIDO: límite por usuario, tope de formularios, parada al primer fallo |
| 6 | Se preguntaba a una tienda por intentos abiertos con otra, y se cerraban | CORREGIDO |
| 7 | Un intento se cerraba al primer «no hay nada» | CORREGIDO: al final de su ventana |
| 8 | Veinte intentos nuevos por pasada dejaban sin turno a los ya consultados | CORREGIDO: una cuarta parte de la pasada es suya |
| 9 | Un rechazo con el formulario vivo no dejaba rastro | CORREGIDO |
| 10 | La tienda se guardaba cortada y se comparaba entera | CORREGIDO |
| 11 | Las credenciales de «SDK web» se imprimían con sus claves | CORREGIDO |
| 12 | A la app no se le decía por qué no recibía formulario; un cambio de pasarela fallaba el pedido | CORREGIDO (`code`, campo añadido) |
| 13 | La consulta previa a un cambio podía durar minutos | CORREGIDO: 20 segundos |
| 14 | El preflight aceptaba una dirección de API que la aplicación rechaza | CORREGIDO |
| 15 | Textos para quien opera que no decían lo que había pasado | CORREGIDO |

**Pruebas que cambiaron a propósito:** `KeysFromTheEnvironmentTest` fijaba el hueco del
hallazgo 1 (las claves de TEST del entorno cobraban a cualquiera) y ahora fija lo
contrario; tres pruebas del preflight llamaban «completo» a un archivo con claves de
TEST; las que esperaban el cierre a los 45 minutos lo esperan al final de la ventana; y
la consulta con credenciales de otra tienda ya no llega a preguntar.

**Para el propietario.** Cómo pausar los pagos y por qué: `docs/pagos-equipos-documentos.md`
§1.6. Las claves de TEST son sólo para MASTER vengan de donde vengan (§1.3).

**Para quien despliega.** `deploy/rehearsal.sh` no cambia. `deploy/preflight.py` sí:
`PAYMENTS_PUBLIC_TEST` encendido es ATENCIÓN, las claves de TEST del archivo ya no son
«OK» y `MICUENTAWEB_API_URL` tiene que ser la oficial.

**Pruebas.** Esta vez, cada prueba se escribió y se vio fallar antes de su corrección.
Mutaciones, una a una: 60, todas detectadas (una sobrevivió a la primera pasada y obligó
a añadir su prueba). Pruebas nuevas: 74 en `test_payment_audit2`, 5 en `test_preflight`
y 4 de Jest. Módulos de pagos, checkout, consola, medición, preflight y despliegue: 996,
0 fallos, 3 omitidas (las pruebas contra la pasarela real). Jest 1072 de 1072, `tsc` y
`lint` sin errores. **No se corrió en esta sesión** la suite completa de backend,
Playwright ni el ensayo Docker.

## 2026-10-11 — PAY-GUARD-01: cambios de pasarela, claves de TEST y lo que encontró la auditoría

Rama `feat/izipay-smartform-v4`. **Con migración (`0117_payment_attempt_origin`). Sin
rutas nuevas. Sin dependencias nuevas.** Decisiones: DEC-PAY-04, DEC-PAY-05, DEC-PAY-06.
No desplegado. **Nada se probó contra la pasarela real: no hay credenciales.**

**1. Un cambio de pasarela que dejaría un cobro sin confirmar no se hace** (DEC-PAY-04).
Activar el otro producto, apagar o revocar el que cobra, cambiar de tienda o de entorno:
con «Mi Cuenta Web» cobrando, el servidor pregunta antes a la pasarela por los cobros
abiertos y rechaza el cambio (409, no cambia nada) mientras alguno pueda cobrarse o siga
sin resolver. `INTERRUMPIR` ya no lo salta; queda para «SDK web / Checkout», que no se
puede consultar, y para una pasarela que no responde, y deja en la auditoría los
intentos interrumpidos. Renovar las claves de la misma tienda y entorno sigue permitido.

**2. Las claves de TEST de la consola no son para compradores** (DEC-PAY-05). Sólo un
MASTER con sesión abre un pago; a los demás la tienda les dice que el pago en línea
todavía no está disponible. Un pedido pagado en TEST queda señalado en su intento y en
la auditoría.

**3. La auditoría independiente de la conciliación** (DEC-PAY-06): doce hallazgos.

| | Hallazgo | Estado |
|---|---|---|
| 1 | Tarjeta rechazada y luego aceptada en el mismo formulario, con la notificación del pago perdida: no se conciliaba nunca | CORREGIDO |
| 2 | Dos cobros de un pedido archivados como un pago normal; segundo formulario sin preguntar por el primero | CORREGIDO |
| 3 | Veinte compras abandonadas dejaban sin turno a un pago perdido; no se podía consultar una referencia | CORREGIDO |
| 4 | Una respuesta sin decidir sólo dejaba un aviso en el registro de la tarea | CORREGIDO |
| 5 | Varias pasadas a la vez escribían varios `payment_reconciled` | CORREGIDO |
| 6 | Una pasarela que rechaza a quien llama se consultaba por cada intento | CORREGIDO |
| 7 | Una respuesta cortada escapaba como excepción; lectura sin tope | CORREGIDO |
| 8 | El intento no guardaba tienda ni entorno | CORREGIDO (intentos anteriores a `0117`: como antes) |
| 9 | Un pago repetido volvía a pagar un pedido que había dejado de estarlo | CORREGIDO |
| 10 | Una redirección reenviaba la contraseña | CORREGIDO; servidor de la API fijado al oficial |
| 11 | Las credenciales se imprimían con su contraseña | CORREGIDO («Mi Cuenta Web»; las de «SDK web»: PAY-SDK-SECRETS-REPR) |
| 12 | `--hours 0`, firma por defecto, textos y registro de los fallos de integridad | CORREGIDO; los medios de pago de más de 2 h quedan como límite documentado |

**Pruebas que cambiaron, y por qué** (ninguna se relajó): las de la consola y las de
medición pagaban como comprador anónimo con claves de TEST de la consola; ahora pagan
como MASTER o con claves de producción simuladas. Un rechazo firmado deja el intento
esperando en lugar de rechazado (hallazgo 1). El orden de consulta es «quien más lleva
sin respuesta» en lugar de «el más reciente» (hallazgo 3).

**Para el propietario.** Los pasos de TEST cambiaron: `docs/pagos-equipos-documentos.md`
§1.3 (el pago de prueba se hace con la sesión de MASTER iniciada). Un cliente que dice
que pagó: `reconcile_payments --reference <referencia>` (§1.5). Qué hace falta saber
con claves de TEST: §1.5, al final.

**Deuda que queda registrada** (`docs/AUDIT_MEMORY.md`): PAY-GET-UNKNOWNS,
PAY-DEFERRED-WINDOW, PAY-TEST-ENV, PAY-TEST-BADGE, PAY-SDK-SECRETS-REPR, KRYPTON-GLUE,
PAY-RECONCILE-REHEARSAL.

**Pruebas.** RED antes que GREEN en lo nuevo de los cambios de pasarela y de las claves
de TEST; los hallazgos de la auditoría venían ya demostrados por sus propias sondas.
Mutaciones, una a una: 41 de las dos primeras partes y 59 de la tercera, todas
detectadas (cinco sobrevivieron a la primera pasada y obligaron a añadir su prueba).
Pruebas nuevas: 101 de backend (`test_payment_change_guard`, `test_payment_test_access`,
`test_payment_hardening`) y 3 de Jest. Módulos de pagos, checkout, consola, medición y
despliegue: 871, 0 fallos, 3 omitidas (las pruebas contra la pasarela real). Jest 1068
de 1068, `tsc` y `lint` sin errores. **No se corrió en esta sesión** la suite completa
de backend, Playwright ni el ensayo Docker.

## 2026-10-11 — IZIPAY-MCW-AUDIT-01: «Mi Cuenta Web» contra su documentación, y la consulta que faltaba

Rama `feat/izipay-smartform-v4`. **Sin migración. Sin rutas nuevas. Sin dependencias
nuevas.** Decisión: DEC-PAY-03. No desplegado. **Nada se probó contra la pasarela real:
no hay credenciales.**

Se leyó la documentación oficial de la API REST V4 (inicio rápido del SmartForm, claves,
`Charge/CreatePayment`, IPN, retorno al navegador, estados, `Order/Get`, reglas de
notificación del Back Office, tarjetas de prueba, CSP) y se comparó con el código. La
tabla completa, con la página de cada punto, está en
`docs/pagos-equipos-documentos.md` §1.1.

**Lo que ya cumplía y no se tocó:** crear el pago (dirección, `Basic`, importe entero
en céntimos, `PEN`, `orderId` por intento), el guion y el tema oficiales como constantes
del código, la firma del IPN con la contraseña, el rechazo de la copia del navegador,
`PAID` como único pagado, la comparación de importe, moneda, tienda y referencia, el
pago único ante notificaciones repetidas, TEST y PRODUCCIÓN decididos por las claves, y
la consola (sólo MASTER, secretos cifrados y nunca devueltos).

**Diferencias encontradas y qué se hizo.**

| | Documentación | Antes | Ahora |
|---|---|---|---|
| Consulta si el IPN no llega | `Order/Get` devuelve las transacciones de una referencia | No existía (PAY-RECONCILE) | `reconcile_payments`: pregunta y paga por el mismo camino que una notificación |
| Firma del IPN | el ejemplo oficial calcula el hash tras cambiar `\/` por `/` | sólo la cadena recibida: una notificación con barras escapadas se rechazaba | se acepta también esa lectura, y se interpreta lo que cubrió la firma |
| Qué respuesta espera el IPN | falla si no es `200`–`206` o `301`/`302`/`303`/`307`/`308` | «sin verificar» | documentado; el código ya respondía así. Sin cambio |
| React | «requiere» `embedded-form-glue` | guion oficial insertado directamente | sin cambio: no se añaden dependencias (KRYPTON-GLUE) |
| CSP | abrir `static.micuentaweb.pe` si el sitio usa CSP | la política no restringe guiones ni conexiones | sin cambio: ya estaba fijada por una prueba |

**La consulta, en una frase:** para cada intento de «Mi Cuenta Web» que sigue esperando
(entre 3 minutos y 2 horas, los más recientes primero, hasta 20 por pasada), el servidor
pregunta `Order/Get`, sólo cree un cobro pagado y completo de esta tienda, esta
referencia y este entorno, y lo demás lo deja esperando. Detalle en
`docs/pagos-equipos-documentos.md` §1.5.

**Para el propietario.** Las claves de TEST se escriben en Configuración ›
Integraciones › «Izipay · Mi Cuenta Web»: los pasos están en
`docs/pagos-equipos-documentos.md` §1.3. En el servidor hay que añadir la línea de
`reconcile_payments` de `deploy/crontab.example`.

**Estado por punto del encargo.**

| Punto | Estado |
|---|---|
| SmartForm V4: crear el pago, `Basic`, `formToken`, importes, guion, CSP, errores | IMPLEMENTADO con pasarela falsa · BLOCKED/CREDENTIALS para la prueba real |
| IPN: firma, importe, moneda, pedido, tienda, estados, repetición, reintentos | IMPLEMENTADO con pasarela falsa · BLOCKED/CREDENTIALS |
| Configuración desde Panel › Integraciones | IMPLEMENTADO (INTEGRATIONS-CONSOLE-01, sin cambios) |
| Conciliación por `Order/Get` | IMPLEMENTADO con pasarela falsa · BLOCKED/CREDENTIALS |
| Pruebas y flujo en TEST | IMPLEMENTADO con pasarela falsa; las dos pruebas reales se omiten sin claves |
| Cambiar de proveedor o de claves sin romper cobros abiertos | PARCIAL en esta entrada; resuelto en PAY-GUARD-01 (arriba) |

**Deuda que queda registrada** (`docs/AUDIT_MEMORY.md`): PAY-RECONCILE para el otro
producto, PAY-TEST-PUBLIC, KRYPTON-GLUE, PAY-RECONCILE-REHEARSAL, PAY-SWITCH-PENDING.

**Pruebas.** RED antes que GREEN en cada regla nueva; después, 34 mutaciones, una a
una, todas detectadas (tres sobrevivieron a la primera pasada y obligaron a endurecer
sus pruebas). Backend completo: 6057 pruebas, 0 fallos, 6 omitidas (tres son las pruebas
contra la pasarela real, que piden claves). Módulos de pagos, consola y despliegue: 232,
0 fallos, 3 omitidas. Frontend sin cambios: Jest 1059 de 1059, `tsc` y `lint` sin
errores. La suite completa corrió antes de añadir dos pruebas de regresión, que sólo
están en la pasada de módulos. Sin Playwright ni ensayo Docker en esta fase.

## 2026-10-11 — #102–#108 en `master` y desplegados

Los siete PR apilados se fusionaron en orden, con commit de merge y CI verde en cada
cabeza: #102 → #103 → #104 → #105 → #106 → #107 → #108. `master` quedó en `3057dd2`,
con el mismo árbol que la cabeza de #108 (`7296372`), la que pasó la suite completa y
el ensayo.

**Desplegado el 2026-10-11, 16:32 (Lima).** El servidor pasó de `36c2fa4` a `3057dd2`
siguiendo `docs/despliegue-produccion.md` §7: copia previa `db-20261011-162832.sql.gz`
y `evidence-20261011-162832.tar.gz`, imágenes construidas con la web en línea,
`migrate --plan` con cuatro operaciones (`0113`–`0116`), `migrate` sin incidencias,
contenedores reemplazados y sanos, `deploy/healthcheck.sh` en verde, `manage.py check`
sin incidencias y sin errores en el registro. El `deploy/Caddyfile` local del servidor
no cambió. No se tocó la pasarela de pago ni ninguna variable.

**Antes de migrar** se leyó producción, sólo lectura: 1 empresa, 6 usuarios, 1 producto,
0 pedidos, 0 equipos, 0 cobros y ningún correo con más de una cuenta. No había pedidos
pagados sin entregar, así que ninguno quedó pidiendo identificar su equipo.

**Copia y vuelta atrás, comprobadas.** La copia previa se restauró en un PostgreSQL
desechable sin red (98 tablas, los mismos recuentos). Sobre esa copia, con la imagen
nueva, se aplicaron `0113`–`0116`, se deshicieron y se aplicaron otra vez: tras
deshacerlas el esquema es idéntico al de `0112`. La vuelta atrás es, en este orden:
`migrate store 0112` con la imagen nueva, `git checkout 36c2fa4`, levantar las imágenes
guardadas como `rollback-36c2fa4` (o `build`) y `up -d`. Volver al commit anterior no
deshace el esquema por sí solo. La otra vía es `deploy/restore.sh` con la copia previa.

**Comprobado en producción después**, sin crear cuentas, pedidos ni pagos: tienda,
producto, carrito, acceso, recuperación, verificación, checkout, servicios y panel
responden 200; catálogo y categorías 200; las rutas nuevas de plantillas y de
identificación de equipos responden 401 sin sesión; las rutas de notificación de pago
responden 405 a una lectura (no se envió ninguna notificación). En la base: índice
único del correo creado, tabla de equipos identificados vacía, columna `price_override`
conservada (deuda SERIAL-PRICE-DROP) y fuera del modelo, el producto existente sigue
publicado. Las dos plantillas salen con una hoja y nueve cabeceras.

**Falta, y sólo puede hacerlo el propietario con su sesión:** entrar, registrar una
cuenta y verificarla con el código, crear un producto «sólo stock interno», descargar y
subir las dos plantillas, vender un equipo en caja identificándolo, y entregar un
pedido web identificando su equipo. La tienda sigue sin poder cobrar en línea: la
pasarela no tiene credenciales (fase aparte).

## 2026-10-11 — IMPORT-SPLIT-01: dos cargas diferenciadas, cada una con su plantilla de una hoja

Rama `feat/bulk-import-split`, apilada sobre `feat/serial-pick`. **Sin migración.**
Decisión: DEC-IMPORT-SPLIT-01. En `master` y desplegado el 2026-10-11 (`3057dd2`).

Reutiliza lo que ya había: el importador de productos con «Destino» (#105), el de
equipos (`unit_import_services`) y el Kardex (`receive_units`). No añade un importador
ni otro camino al stock.

**Qué cambia.**
- **Plantillas de una hoja.** `carga-masiva-productos.xlsx` (hoja «Productos») y
  `carga-masiva-equipos.xlsx` (hoja «Equipos»): cabeceras y nada debajo. La ayuda pasa a
  una nota en cada cabecera y a la pantalla.
- **Productos y accesorios.** Categorías de la empresa en una lista, leída al descargar;
  imágenes opcionales, editables después en la ficha; «Destino» con sus dos valores,
  aparte de «activo». No mueve stock: una columna de stock, serie o IMEI se dice a la
  vista que no entra por ahí.
- **Equipos.** Una fila por unidad, serie obligatoria; serie sola, serie + IMEI o serie
  + dos IMEI según lo que lleve el producto. Listas de sucursal (las de quien carga) y
  de condición. Una columna de cantidad o de stock rechaza el archivo.
- **Listas que ofrecen y no bloquean**, como en los archivos del propietario. Si los
  nombres de categorías o sucursales no caben en una lista de celda, la plantilla sale
  sin ella y el dato se escribe.
- **Tres entradas en el menú con nombre propio:** «Carga de productos», «Carga de
  equipos» (nueva) y «Carga de stock». Antes había dos «Carga masiva».
- **Compatibilidad.** Los archivos hechos con las plantillas anteriores se importan
  igual.

**Sin cambio:** previsualización, errores por fila, aplicación todo o nada, auditoría
(`product_import_applied`, `stock_units_import_applied`), permisos (`products.manage`,
`inventory.adjust`) y aislamiento por empresa y sucursal.

**Decisiones del propietario (2026-10-11).**
- Un IMEI en una fila cuyo producto no está marcado «lleva IMEI» se guarda y se avisa;
  no se rechaza. La regla de «Registrar equipo» no cambia. Queda trazado en la fila, en
  el resumen del trabajo y en la auditoría de la carga, con la cuenta y sin los IMEI.
- El IMEI 2 es opcional. No se añade una capacidad «doble IMEI» por producto ni una
  migración. Cuando viene, se valida su formato, su dígito de control y su unicidad.
- Menú: «Carga de productos», «Carga de equipos» y «Carga de stock».

## 2026-10-11 — SERIAL-PICK-01: el equipo que sale es el que alguien identificó

Rama `feat/serial-pick`, apilada sobre `fix/register-race`. **Con migración**
(`0116_serial_pick`). Decisión: DEC-SERIAL-02. En `master` y desplegado el 2026-10-11 (`3057dd2`).

**Qué pasaba.** Un producto con serie se vendía como «uno de ellos»: el sistema tomaba
el equipo más antiguo de la sucursal y la nota imprimía ese IMEI, fuera cual fuera el
que se entregaba. Si se entregaba otro, quedaba vendido un equipo que seguía en la
tienda y la garantía a nombre de la serie equivocada.

**Qué hay ahora.**
- **Caja.** Nada con serie se vende sin decir qué equipo. Se escanea su IMEI o su serie,
  o se escribe a mano si el lector no lee. A mano: el IMEI se valida (15 dígitos y su
  dígito de control), el sistema muestra qué equipo es —modelo, estado, IMEI y serie— y
  la persona lo confirma. Queda registrado que fue a mano y quién lo hizo. Hasta que cada
  línea con serie tiene sus equipos, «Cobrar» está apagado y el servidor rechaza la venta.
- **Pedido web.** El pago aparta un equipo; cuál sale lo dice quien lo entrega, en la
  ficha del pedido («Equipos que se entregan»), con el lector o a mano y la misma
  validación. Puede ser otro equipo del mismo modelo: el apartado vuelve al estante y el
  cambio queda en el Kardex. Sin identificar, el pedido no pasa a «enviado» ni a
  «entregado». Lo entregado ya no se cambia.
- **Documentos.** La nota de venta y el ticket imprimen el equipo identificado, y nada
  antes de que alguien lo identifique.
- **Precio.** Se retira el «precio propio» del equipo: se cobra el del producto.
- **Dos pedidos del mismo modelo.** Mientras nadie los identifique, cualquiera de las
  cajas apartadas sirve para cualquiera de los dos pedidos.
- **Devoluciones.** Un equipo devuelto antes de entregarse deja de ser el del pedido, que
  vuelve a pedir uno. Devuelto después, la venta lo sigue nombrando.
- **Lo que salió no se cambia**, aunque el pedido se devuelva a un estado anterior.
- **La app (API v1)** identifica y quita equipos de un pedido igual que el panel.
- **Lo anterior.** Las ventas ya entregadas quedan registradas como «asignado por el
  sistema (regla anterior)» y sus documentos se reimprimen igual. Un pedido web pagado
  antes del cambio y aún sin entregar pedirá identificar su equipo.

**Lecturas que el propietario debe conocer.**
- «Igual que en caja al entregar» se aplicó como obligatorio: no hay «enviado» ni
  «entregado» sin identificar.
- El producto no tiene campos de modelo ni de color: se muestra su nombre, que los
  lleva, además del estado, el IMEI y la serie.
- Si fue lector o a mano lo declara el panel (por el ritmo de las teclas o el botón «a
  mano»). Un lector lento se trata como escrito a mano: pide confirmar, no deja pasar.

**Deuda.**
- SERIAL-PRICE-DROP: la columna `price_override` sigue en la base, sin uso. Se borra en
  la versión siguiente, cuando ninguna versión en línea la lea.
- La caja registra en auditoría quién identificó cada equipo, sin la dirección de red
  (las ventas de caja no la llevan en ninguna de sus líneas de auditoría).
- SERIAL-TRANSFER y SERIAL-COUNT siguen pendientes.

## 2026-10-08 — REGISTER-RACE-01: un correo, una cuenta, también en la base

Rama `fix/register-race`, apilada sobre `feat/product-visibility-import`. **Con
migración** (`0115_user_email_unique_ci`). Decisión: DEC-REGISTER-01. En `master` y desplegado el 2026-10-11 (`3057dd2`).

**Qué pasaba.** El registro preguntaba «¿este correo ya está?» y después creaba la
cuenta. Dos peticiones en el mismo instante oían «no» las dos y creaban una cada una.
Desde ahí ese correo no podía verificarse, ni pedir otra verificación, ni recuperar la
contraseña: todo eso se niega a elegir entre dos cuentas. Una ráfaga dejaba un correo
bloqueado para siempre. Reproducido: cinco registros simultáneos, cinco cuentas.

**Qué hay ahora.**
- La base rechaza una segunda cuenta con el mismo correo, sin distinguir mayúsculas
  (índice único sobre `UPPER(email)`, la misma expresión con que el código compara un
  correo). Las cuentas sin correo no cuentan.
- Un correo de cuenta se escribe con letras sin tildes ni caracteres especiales: una
  dirección como «ınfo@tienda.com» (con «ı» sin punto) pasa por otra y la base las
  considera la misma.
- Quien pierde la carrera recibe la misma respuesta que quien llega tarde: «Este correo
  ya está registrado». Sin error 500 y sin nada a medio escribir. Lo mismo con un nombre
  de usuario, y con la entrada con Google.
- La migración **se detiene** si ya hay duplicados: dice cuántos, no cuáles, y no fusiona
  ni borra nada.
- `python manage.py list_duplicate_emails` los lista para que una persona decida (sólo
  lectura; el correo sale enmascarado).

**Producción, consultado el 2026-10-08 (sólo lectura):** 6 cuentas, **0 correos
duplicados**, ninguna sin correo y ninguna con caracteres no ASCII. La migración no
encontraría nada que la detenga. La base usa `en_US.utf8`.

**Revisión independiente antes del PR:** la primera versión ponía el índice sobre
`LOWER(email)`. El código compara con `UPPER`, y en una base UTF-8 —la de producción lo
es— «ınfo@» e «info@» son la misma dirección para `UPPER` y distintas para `LOWER`: dos
cuentas seguían cabiendo. Corregido, con dos pruebas antiguas que la regla rompía y
que la corrida parcial no había ejecutado.

**Deuda:** `createsuperuser` con un correo ya usado termina en un error de la base que
cita el correo (sólo en la terminal de quien administra).

## 2026-10-08 — PRODUCT-DESTINATION-01: «sólo stock interno» y la plantilla de carga masiva

Rama `feat/product-visibility-import`, apilada sobre `feat/mail-verification-code`. **Con migración**
(`0114_product_is_published_online`). Decisión: DEC-PRODUCT-DEST-01. En `master` y desplegado el 2026-10-11 (`3057dd2`).

**Qué hay.** Un producto tiene un dato nuevo, independiente de «activo»:
`Product.is_published_online`. En `false` es **«sólo stock interno»**: sigue en
inventario y se vende en caja y en servicio técnico según los permisos de cada persona,
y no existe para la tienda en línea: ni catálogo, ni búsqueda, ni su dirección, ni
carrito, ni compra, ni campañas, ni reseñas. Todos los productos existentes quedan
publicados, como estaban.

**Dónde se decide.** En la ficha del producto («Destino») y en la carga masiva (columna
«Destino»). Lo cambia quien puede gestionar productos; queda en el registro de auditoría.

**Carga masiva.**
- La columna «Destino» —y «Destino (pendiente web)», como venía en la plantilla del
  propietario— se reconoce sola, se valida y se ve en la vista previa. Vacía: lo nuevo se
  publica, lo existente no cambia. Un archivo de antes de esta columna se importa igual.
- La plantilla descargable trae las categorías **de esa empresa** en una hoja
  «Categorías» y las ofrece como lista en la columna «Categoría»; «Destino» también es
  una lista. Los códigos van como texto. Las imágenes siguen siendo opcionales y se
  añaden o cambian después desde la ficha (la galería que ya existía).
- En la pantalla se puede elegir una categoría para las filas que no traen una. Se
  comprueba que es de esa empresa.
- Ninguna categoría está escrita en el código.

**Lo que no cambia.** Caja, inventario, traslados, recuentos y servicio técnico ven y
usan estos productos como cualquier otro. «Activo» significa lo mismo que antes.

**Deuda y límites conocidos**
- Un producto que deja la web estando en un carrito: el carrito sigue abriendo y la
  compra se rechaza, igual que con un producto desactivado.
- La imagen de un producto que deja la web sigue respondiendo por su dirección de 32
  caracteres, igual que la de un producto desactivado (PRODUCT-IMAGE-HIDDEN).
- Las categorías públicas no cuentan productos: una categoría cuyos productos son todos
  internos se sigue listando, vacía.
- **Orden de unión (decisión del propietario, 2026-10-08):** primero el código de
  verificación (MAIL-TEMPLATE-01 3A, migración `0113`); esta rama va apilada sobre ella
  y su migración es la `0114`.
- Un pedido creado mientras el producto estaba publicado se puede terminar de pagar
  después de que deje la web (igual que con un producto desactivado).
- Una campaña enlazada a un producto interno se sigue pudiendo publicar; sale sin el
  enlace al producto.
- No se probó en navegador real (sólo backend y Jest).

**Revisión independiente antes de commitear:** dos fugas reales —un perfil de columnas
recordado hacía ignorar «Destino» y publicaba lo que debía quedar interno; el checkout
de la app confirmaba que un producto interno existe y daba su nombre— y un defecto más,
todos corregidos con su prueba.
## 2026-10-08 — MAIL-TEMPLATE-01 (fase 1 de 3): los correos llevan la plantilla de la tienda

Rama `feat/mail-template`, desde `master` `6266402` (#101). Sin migraciones y sin
variables de entorno nuevas. Decisión: DEC-MAIL-01. En `master` y desplegado el 2026-10-11 (`3057dd2`).

**Qué hay.** Un motor de correo (`backend/store/mail/`) que rellena una sola plantilla
HTML —la que entregó el propietario, sin editar— con un diccionario de datos, y produce
con los mismos datos la versión de texto plano. Cinco correos lo usan ya: verificar
correo, restablecer contraseña, invitación de personal, compra confirmada (con su PDF) y
aviso interno de pedido. Guía: `docs/correos.md`.

**De quién es la plantilla.** Su pie lleva el nombre, la dirección, el teléfono y las
redes de una tienda; `store/mail/plantilla/marca.json` dice de cuál. La usan los correos
de esa empresa, y los de plataforma sólo si `DEFAULT_STOREFRONT_COMPANY_SLUG` es esa
empresa (en producción lo es) y la persona no lo pidió desde la dirección de otra.
Cualquier otra empresa conserva el correo neutro de antes.

**Un correo no se pierde por cómo se ve.** Si la plantilla no se puede rellenar —falta
la razón social o el RUC, un dato no cumple el contrato, falla el motor— sale el correo
neutro y queda un error en el registro, sin el cuerpo. Enviar se intenta una sola vez,
con o sin plantilla.

**Revisión independiente antes del PR:** sin hallazgos graves; dos defectos (un correo
podía salir dos veces si el servidor fallaba al confirmar; la plantilla en correos de
cuenta pedidos desde la dirección de otra empresa) y cinco menores, todos corregidos con
su prueba. El ensayo de producción detectó por su cuenta el primero (el doble de espera
con el servidor de correo mudo).

**Vista previa**, sólo con `DEBUG`: `/api/dev/mail-preview/`. En producción responde 404.

**Dependencia nueva:** `chevron==0.14.0` (Mustache en Python puro, MIT).

**Fase 2** (rama `feat/mail-template-notifications`, sobre la fase 1): los avisos de
`notification_services` y la prueba del servidor de correo salen con la plantilla. De
los 15 eventos, hoy van por correo cuatro (`EMAIL_WORTHY_EVENTS`); esa decisión no se
toca. Un aviso lleva un botón a donde está el detalle, nunca el detalle; y sólo cuando
esa página se lo va a mostrar a quien lo recibe.

**MAIL-TRACKING-LINK (decidido por el propietario el 2026-10-08):** el aviso de una
reparación lleva al cliente su enlace de seguimiento, tenga cuenta o no. Sólo al cliente
de esa reparación; un enlace revocado no se recrea; la copia del personal no lo lleva.

**Fase 3**, aprobada por el propietario el 2026-10-08, en tres PR:

- **3A · código de verificación** (rama `feat/mail-verification-code`, sobre la fase 2):
  el correo de verificación lleva enlace y código de 6 dígitos. Migración
  `0113_account_token_code`. Decisión DEC-MAIL-03.
- **3B · garantía y posventa:** pendiente. Va después de SERIAL-PICK-01, que deja
  registrado qué equipo se entregó: el correo de garantía sólo saldrá con un equipo
  identificado por una persona.
- **3C · promociones con consentimiento y baja:** pendiente.

## 2026-10-07 — STAFF-ONBOARDING-01: una persona invitada siempre puede terminar

Rama `fix/staff-invitation-onboarding`, desde `master` `54b26ac` (#98). Sin migraciones.
Decisión: DEC-ONBOARD-01. En `master` desde el 2026-10-08: PR #100, merge `36c2fa4`.

**Desplegado el 2026-10-08, 10:09 (Lima).** El servidor pasó de `9fd9536` a `36c2fa4`
siguiendo `docs/despliegue-produccion.md` §7: copia previa `db-20261008-100724.sql.gz`
y `evidence-20261008-100724.tar.gz`, imágenes construidas con la web en línea,
`migrate --plan` sin operaciones, `check --deploy` con el único aviso esperado
(`security.W008`), contenedores reemplazados y sanos, sin errores en el registro.

Comprobado en producción después: tienda, acceso, registro, invitación, recuperación y
privacidad responden 200; `/api/dev/demo-accounts` 404; una invitación inexistente
responde 404 con un solo mensaje; la pantalla de invitación es la nueva. Recuentos
iguales antes y después: 4 usuarios, 2 de ellos inactivos, 1 superusuario, 0 membresías,
2 invitaciones pendientes; la verificación de correo sigue activa.

**El caso real, confirmado con los datos del servidor** (sólo recuentos): las dos
invitaciones pendientes siguen vigentes y la cuenta de cada una estaba registrada y sin
verificar. Es la primera de las variantes descritas abajo. Con el código nuevo cada
persona abre su invitación, pide «Establecer mi contraseña», elige una y acepta; no hubo
que tocar ninguna cuenta a mano.

**Qué pasaba en producción.** Una persona abría su invitación, veía «Crear cuenta» y al
registrarse se le decía que la cuenta ya existía. No conocía ninguna contraseña que
sirviera y no podía terminar.

**Causa.** La invitación y el registro coinciden en quién es alguien (comparan el correo
sin mayúsculas ni espacios), así que no era un desacuerdo. Era el recorrido:

1. la invitación mandaba al registro ordinario, que además se abría en la pantalla de
   iniciar sesión y sin el correo;
2. con la verificación de correo activa (producción), registrarse crea una cuenta
   INACTIVA y envía un segundo correo;
3. desde ahí el correo «ya está registrado», la cuenta no puede iniciar sesión y la
   recuperación de contraseña la ignoraba por no estar activa.

Reproducido con los ajustes de producción antes de tocar nada. Ninguna prueba recorría el
camino entero con la verificación activa. Lo que no se pudo saber desde el código: cuál
de las variantes sufrió la persona real (cuenta a medio crear, cuenta abandonada de
antes, o un nombre de usuario ya ocupado).

**Qué cambia.**

| Caso | Antes | Ahora |
|---|---|---|
| El correo no tiene cuenta | Registro ordinario: cuenta inactiva, segundo correo, sin vuelta | «Crear mi cuenta» abre el registro con el correo de la invitación, que no se puede cambiar. La cuenta nace activa: el enlace ya prueba el buzón. Queda dentro y de vuelta en la invitación |
| Tiene cuenta y sabe la contraseña | Iniciar sesión y aceptar | Igual |
| Tiene cuenta y no la sabe, o nunca tuvo una (Google) | Sin enlace desde la invitación y sin vuelta | «No conozco mi contraseña» pide la recuperación de siempre para ese correo; el enlace vuelve a la invitación y dice con qué usuario se entra |
| Cuenta registrada y nunca verificada | No entraba, no se registraba, no recuperaba | La invitación lo dice («a medio terminar») y ofrece establecer la contraseña; al hacerlo la cuenta queda activa |
| Sesión de otra cuenta | No se podía aceptar | Igual, y se puede cerrar esa sesión ahí mismo |

- La contraseña la elige siempre la persona. Nadie más la escribe ni la ve, y no se
  envía ninguna por correo.
- Aceptar sigue exigiendo una sesión del correo invitado: lo comprueba el servidor.
- La recuperación también sirve a una cuenta registrada y nunca verificada, con o sin
  invitación. Una cuenta apagada después de haber sido verificada no se reactiva.
- El enlace de recuperación sólo puede volver a una invitación; cualquier otra
  dirección se descarta, en el navegador y en el servidor.
- `GET /api/staff/invitations/accept/` añade `account_state` (`none`, `active`,
  `unverified`); `POST /api/auth/password-reset/confirm/` añade `username`. Lo demás no
  cambia, y las invitaciones ya enviadas siguen sirviendo.

**Lo que encontró el navegador real**, que las pruebas unitarias no:
- La lectura de una invitación tiene un cupo por red (20 por minuto). La pantalla
  preguntaba de más y, con el cupo agotado, decía «Invitación no válida». Ahora pregunta
  una vez, conserva lo que sabía y distingue «no pudimos comprobarla».
- Si la invitación no se puede leer, el registro no sigue sin ella.
- Dos cuentas con el mismo correo hacían que pedir la recuperación respondiera 500.

**Lo que encontró la revisión de seguridad independiente**, antes de abrir el PR:

| | Qué | Corrección |
|---|---|---|
| P1 | El registro guardaba la cuenta activa y después miraba la invitación. Un token malformado hacía fallar esa consulta: respuesta 500 y una cuenta activa, sin verificar, en cualquier correo | La decisión se toma antes de crear la cuenta, que se escribe una vez y en su estado final junto con su enlace de verificación. Lo que no tiene forma de token no se consulta; una consulta que falla es un «no» |
| P2 | Una cuenta terminada por recuperación seguía pareciendo «nunca verificada»: apagada después, una recuperación la habría reactivado | Al terminarla se gastan sus enlaces de verificación |
| P3 | La vuelta a la invitación en el correo de recuperación sólo se comprobaba por su forma | Se conserva sólo si es una invitación vigente de ese mismo correo |
| P3 | Reenviar la verificación de un correo con dos cuentas respondía 500 | Misma respuesta discreta que un correo desconocido |

Aceptado y dicho, no corregido: **para un correo sin cuenta, el enlace de invitación
basta por sí solo** para crear la cuenta y aceptar. Antes hacía falta leer además un
segundo correo. Es la consecuencia de que la invitación pruebe el buzón (DEC-ONBOARD-01)
y es lo que desbloquea a quien recibe una; lo acotan su caducidad de siete días, el uso
único, la revocación y el reenvío, que anula el enlace anterior. Decide el propietario.

**Verificación**

| Medida | Antes (`master` `54b26ac`) | Después |
|---|---|---|
| Backend, suite completa en PostgreSQL | 5679, 0 fallos, 5 omitidas | 5714, 0 fallos, 5 omitidas (35 nuevas), sobre `6264176` |
| Jest | 936/936 | 977/977: 37 nuevas, más las 4 que trajo #99 al unir `master` |
| Tipos · lint | limpio · 0 errores, 22 avisos | limpio · 0 errores, los mismos 22 avisos |
| Navegador, specs de personal y de sesión | 20/20 | 22/22, 0 omitidas |
| Navegador, con verificación de correo obligatoria | no se probaba | los dos recorridos nuevos, 2/2 |
| `manage.py check` · `makemigrations --check` | — | sin incidencias · sin cambios: 0 migraciones |
| Ensayo de producción | — | `ENSAYO: OK` sobre `25b6666`: 149 comprobaciones y 49 pasos de navegador; no deja nada en el equipo |

- Pruebas nuevas vistas primero en rojo: 15 de las 28 iniciales de backend fallaban contra
  el código anterior; las 7 de la revisión, también.
- Mutaciones: 17 reglas del backend y 12 del frontend rotas a propósito; todas detectadas
  menos una, que es la segunda capa de una defensa doble (la forma del token) y cuya
  ausencia sigue cubierta por la primera.
- Revisión de seguridad independiente: 1 P1, 1 P2 y 2 P3 corregidos con prueba; un P2 de
  diseño aceptado y dicho arriba.
- Corridas del ensayo que NO se cuentan, y por qué: una con el equipo en reposo (falló
  el inicio de sesión del navegador); dos en las que `pip install` falló al construir la
  imagen sin caché (intermitente: la misma imagen construye bien por separado, y el
  resto de esas corridas pasó); y una que destapó un fallo que ya estaba en `master`:
  desde #99 el aviso de cookies nombra a Google Analytics y la comprobación «sin
  Continuar con Google» buscaba esa palabra en la página de acceso. Ahora busca el botón.

**Deuda que deja**
- LOGIN-BY-EMAIL: se inicia sesión con un nombre de usuario, que la persona tiene que
  elegir y recordar. La recuperación ya se lo dice; entrar con el correo sería mejor.
- INVITE-REGISTER-USERNAME: el registro desde una invitación pide un nombre de usuario;
  si está ocupado, se dice y hay que elegir otro.
- VERIFY-RESEND-UI: reenviar la verificación existe en la API y ninguna pantalla lo
  ofrece. Ya no bloquea a nadie: la recuperación termina esas cuentas.
- INVITE-PENDING-LIST: una persona con sesión no ve sus invitaciones pendientes si
  pierde el correo; la empresa puede reenviarla.

## 2026-10-07 — Aviso de medición y renovación del consentimiento

**PARCIAL · pendiente de publicación y validación del responsable.** Se prepara `/privacy`,
con identidad y contacto del tenant, datos de navegación y compra, envío al confirmar
el pago, proveedores, conservación técnica y cambio de preferencias. Enlaces desde
el banner y pie; consentimiento v2: las respuestas del aviso anterior no habilitan
medición. No se cambian conversiones, credenciales ni ajustes de producción.

El aviso describe medición, no sustituye la política completa de cuentas, pedidos,
comprobantes y servicio técnico. No se declara cumplimiento legal, ni se acepta en
nombre del titular la declaración de Google. Antes de publicar: validar identidad y
contacto configurados, conservación de Analytics y política para las demás finalidades.


## 2026-10-07 — CHECKPOINT 2: servidor preparado, despliegue pendiente

**PARCIAL · 25% · BLOCKED/OWNER-DATA.** El propietario autorizó `GO AWS` para
crear y configurar el servidor; `GO DNS` y la apertura pública son autorizaciones
independientes y siguen pendientes. Baseline verificado por fetch: `c5f8c935a8b16072a6424184fc814efbe1babaf1`
(#95). FRESH-PRODUCTION-DATA-01 está IMPLEMENTADO / MERGED.

- AWS: cuenta BlackDogStore terminada en 7704; Lightsail `blackdogstore-prod-01`,
  `sa-east-1a`, Ubuntu 24.04 LTS, 4 GB RAM, 2 vCPU, 80 GB SSD, US$24/mes.
- Static IP `blackdogstore-prod-ip`: `54.94.236.23`, adjunta; IPv6 desactivado.
  Firewall AWS: sólo SSH desde la IP administrativa y Lightsail browser SSH.
  HTTP/HTTPS cerrados; sin balanceador, CDN, snapshots automáticos ni otros servicios.
- Docker 29.8.2 / Compose 5.6.0, usuario `deploy` en grupo docker, UFW activo,
  swap 2 GiB, hora America/Lima sincronizada. Repositorio limpio en
  `/srv/blackdogstore`, HEAD exacto del baseline. Directorio 750, backups 700.
- Los tres secretos de arranque se generaron directamente en el servidor; archivo
  `deploy/.env.production` propiedad de deploy, 600; sin imprimir ni copiar valores.
  `compose config --quiet`: OK. Preflight: CONFIGURACIÓN INCOMPLETA,
  exclusivamente por `ORDER_NOTIFICATION_EMAIL`; SMTP e Izipay quedan para el panel.
- Cloudflare autenticado: zona `blackdogstoreperu.com` con cero registros. Propuesta
  pendiente de `GO DNS`: A `@` y A `www` hacia la Static IP, DNS only, TTL Auto.
  No se modificó DNS, nameservers ni proxy.
- El launch script de Lightsail falló antes de instalar por el envoltorio sh
  (`pipefail`); se ejecutó explícitamente con Bash y se comprobó su finalización.
  Actualizaciones oficiales aplicadas y reinicio verificado: Docker, UFW, swap
  y Compose config correctos después de arrancar con el kernel actualizado.
- Sin contenedores, base, migraciones, bootstrap, MASTER, cron ni tráfico público.
  Build, healthcheck y backup/restore reales pendientes. No se tocó código productivo
  ni el árbol principal del propietario. Analítica y pagos permanecen sin configurar.

25% = plan/servidor 10% + host 15%; los otros bloques no se dan por terminados.
No es READY FOR PRODUCTION. Siguiente dato: destinatario real de avisos de pedidos;
después preflight, imágenes, PostgreSQL vacío, migrate y bootstrap, sin Caddy hasta DNS.

## 2026-10-07 — FRESH-PRODUCTION-DATA-01: producción nace de una base vacía

Rama `feat/fresh-production-data-01`, desde `master` `52a4ba3` (documentación de #94 sobre
`a1d2a29`, merge de #93). Sin migraciones, sin cambios de modelo, de API ni de frontend.
Decisión: DEC-FRESH-DATA-01. Operación: `docs/despliegue-produccion.md` §4.3, §4.4 y §5.
Nada publicado y nada creado en AWS.

**Qué se midió.** PostgreSQL vacío, las 154 migraciones, `DEBUG=False`, ninguna semilla:

| | Filas | Clase |
|---|---|---|
| Usuarios, membresías, invitaciones | 0 | — |
| Clientes, equipos, órdenes de servicio, pedidos, pagos, carritos, reseñas | 0 | — |
| Movimientos de stock, documentos fiscales, integraciones, medición | 0 | — |
| Roles · áreas · estados de reparación · series internas · lista de calidad | 5 · 7 · 12 · 2 · 1 | estructura |
| Empresa piloto, sucursal y configuración (`0015`, `0028`) | 1 · 1 · 1 | tenant piloto |
| Portada: 5 preguntas, 8 servicios, campaña «iPhone 18 Pro Max» publicada (`0075`, `0078`) | — | contenido piloto, aprobado |
| Categorías iPhone y Accesorios (`0002`) | 2 | tenant piloto, aprobadas |
| **iPhone 15 Pro, Apple Watch Series 9, AirPods Pro, con 10, 15 y 20 unidades** (`0002`, `0025`) | 3 | **dato de ejemplo** |

Los tres productos salían por `/api/products/` y se podían comprar: 45 unidades que no
existen, sin una línea de Kardex.

**Qué cambia.**
- `manage.py bootstrap_pilot_store`: retira esos tres productos y su stock sólo si están
  exactamente como los dejó la migración, nada los referencia y la base no tiene
  actividad; si no, se niega y no cambia nada. Deja a la tienda piloto con sus cinco
  categorías aprobadas (iPhone, Mac, iPad, Apple Watch, Accesorios). Sin `--apply` sólo
  informa. Repetirlo no hace nada.
- Tres guardas añadidas tras la revisión del propietario en el PR: se niega si quedan uno
  o dos de los tres productos; no se ejecuta con `DEBUG` activo; y con la base ya en uso
  no escribe nada, ni para reponer una categoría que la tienda quitó.
- No es una migración: correría también en cada base de desarrollo y de pruebas.
- Es de la tienda piloto: una empresa nueva no hereda categorías, campaña ni productos.
- El ensayo mide la base recién migrada (paso 3b) y al primer administrador.

**MASTER.** Es `is_superuser`, se crea con `createsuperuser` y no hay contraseña en el
repositorio, en las migraciones ni en los archivos de variables. Medido: sin membresía
tiene las 44 capacidades en la empresa que nombre, ve la consola de integraciones y usa
el panel entero; no figura como personal ni como vendedor de caja. No se le da una
membresía por ser superusuario.

**Login.** La tarjeta «Accesos de desarrollo» no entra en los archivos que sirve el build
de producción y `/api/dev/demo-accounts/` responde 404 con `DEBUG=False`.

**Verificación**

| Medida | Resultado |
|---|---|
| Línea base, `master` `52a4ba3` (código de `a1d2a29`) | CI backend 5654, 0 fallos, 5 omitidas · ensayo 139 + 49 (CHECKPOINT 1B) |
| Pruebas nuevas (`test_pilot_bootstrap`) | 25/25. Primero en rojo: las 17 iniciales porque el comando no existía; las 8 de las tres guardas, 7 fallaban contra el comando anterior |
| Mutaciones | 11 reglas del comando rotas a propósito sobre la versión final (`DEBUG`, catálogo parcial, actividad sin productos, comprobación tras escribir, referencias, actividad, edición, stock, empresa, simulación, orden): las 11 detectadas |
| Backend, suite completa en PostgreSQL, sobre `3b63400` | 5679 pruebas, 0 fallos, 5 omitidas |
| `manage.py check` · `makemigrations --check` | sin incidencias · sin cambios: 0 migraciones nuevas |
| Ensayo de producción (`sh deploy/rehearsal.sh`), sobre `3b63400` | `ENSAYO: OK`, 149 comprobaciones (10 nuevas) y 49 pasos de navegador; no deja nada en el equipo |
| Base nueva, tras `bootstrap_pilot_store --apply` (ensayo, paso 3b) | `Product=0 \| BranchStock=0 \| StockMovement=0`; categorías: iPhone, Mac, iPad, Apple Watch, Accesorios; campaña publicada: 1; usuarios: 0 |
| Segunda empresa (prueba) | 0 categorías, 0 campañas y 0 productos heredados; una con un producto del mismo slug lo conserva |
| Primer administrador (ensayo, paso 6) | tras `createsuperuser`: 1 usuario, MASTER, 0 membresías, 0 cuentas de demostración |

Frontend sin cambios: Jest, tipos, lint, build y Playwright no se repitieron.

**Deuda que deja**
- FRESH-CATEGORY-ORDER: el comando da posición a una categoría aprobada sólo si su orden
  es 0, el valor por defecto. Una tienda que hubiera puesto una a propósito en 0 la vería
  moverse.
- FRESH-SAMPLE-DEV: las bases de desarrollo y de pruebas siguen naciendo con los tres
  productos de ejemplo. Es deliberado: las pruebas los usan.
- La campaña «iPhone 18 Pro Max» queda publicada sin producto que la respalde hasta que
  se cargue el catálogo real.

## 2026-10-07 — CHECKPOINT 1B: revalidación del master actual

**READY FOR CHECKPOINT 2**, pendiente de aprobación del propietario para crear
Lightsail. Nada creado en AWS, nada publicado y ningún cambio en el árbol principal.
Dominio comprado por el propietario: `blackdogstoreperu.com`.

SHA ensayado: `a1d2a29a11f324071b849f0100ccc85d4a756bb1`, merge de #93.
FIX-MEAS-LOG-TEST-01 queda IMPLEMENTADO: defecto exclusivo del test, sin cambio
productivo. CI backend completo sobre `dff943f7a240c7c101742b584e5baaac7dc0f0d1`:
5654 pruebas, OK, 5 omitidas (3 de catálogo sin capacidades reservadas y 2 de
pasarelas reales sin credenciales). El árbol de código de ese HEAD y el master
ensayado es idéntico. CI: https://github.com/PapiCuche/BlackDogStore-web/actions/runs/37580138775

Revalidación local fresca en copia limpia de master: frontend 933/933 (97 suites),
typecheck limpio, lint 0 errores / 22 avisos existentes, build correcto; backend
relevante 445 pruebas, OK, 2 omitidas exclusivamente por credenciales de pasarela;
redacción/logging/integraciones en la corrección 333/333 sin omitidas.
`makemigrations --check --dry-run`: sin cambios. Imágenes productivas reconstruidas
sin caché, `compose config --quiet` correcto. Preflight con dominio comprado y
secretos/destinatario sintéticos: SUFICIENTE PARA ARRANCAR, no prueba datos reales.
Ensayo COMPLETO: `ENSAYO: OK`, 139 comprobaciones, navegador incluido, backup,
restore tras daño y restore en otro proyecto con volúmenes vacíos. Limpieza:
0 contenedores, volúmenes, imágenes y env del ensayo. Escaneo del diff añadido limpio.

Desde `643ba90`: analítica de #91, documentación de #92 y prueba de #93. Topología,
compose, Caddy, Dockerfiles y healthcheck sin cambios. Una migración nueva:
`0112_measurement_conversions`; 124 migraciones de store aplicadas en el ensayo.
Variable opcional nueva: `MEASUREMENT_SEND_INLINE=1`. IDs y secretos de proveedores
se guardan en la consola cifrada; no hay secretos nuevos obligatorios para arrancar.
Sin IDs/integraciones activas no se carga ni envía analítica; consentimiento denegado
por defecto. Se excluyen las rutas privadas/tokenizadas documentadas; Meta/TikTok
además no se cargan en formularios `/auth` y `/checkout` (excepto success).
Dominios externos y límites de la exclusión: `docs/analytics-marketing.md` §6–7.

Sin bloqueadores técnicos para CHECKPOINT 2. Antes de abrir producción: SMTP,
pasarela TEST/producción, destinatario real de pedidos, servidor/DNS y secretos
reales; analítica desactivada hasta cerrar CSP-01 y MEAS-PRIVACY-NOTICE y validar
proveedores reales. Backup externo queda en su fase autorizable separada.

## 2026-10-07 — FIX-MEAS-LOG-TEST-01: comprobar el error de conversión, no un aviso de correo

Rama `fix/measurement-log-test`, desde `master` `cc6a785`. Sólo cambia una prueba;
sin código productivo, migraciones ni configuración de despliegue.

El test de ausencia de secretos pasaba en CI por un INFO de `store.email_services`
cuando faltaba el destinatario interno de pedidos. Con `ORDER_NOTIFICATION_EMAIL`
configurado desaparecía ese aviso y `assertLogs('store')` fallaba: los errores
HTTP/red de Meta y TikTok se convierten en respuestas seguras sin logging.
Reproducido dos veces aislado, con la clase y con el módulo; con el entorno de CI,
tres aisladas, clase y módulo pasaban. Es un defecto del test, no una fuga demostrada.

Ahora la frontera HTTP simulada lanza una excepción inesperada con tokens,
identificadores del navegador y PII ficticia en su mensaje. El código real registra
tres ERROR en `store.measurement`, sólo con el tipo de excepción y sin traceback.
La prueba exige esos registros, comprueba que ningún dato sensible aparece y que
el pago sigue confirmado con las conversiones pendientes de reintento.

RED → GREEN en PostgreSQL 16 / Python 3.12: aislado con y sin destinatario, clase
10/10 y módulo 35/35; redacción, logging e integraciones 333/333, sin omitidas;
`makemigrations --check --dry-run`, sin cambios por generar. El merge exige CI backend completo sobre el HEAD exacto;
CHECKPOINT 1B se repite después sobre el nuevo master. Sin recursos AWS.

## 2026-10-06 — ANALYTICS-MARKETING-INTEGRATIONS-01: medir, con permiso y una sola vez

En `master` desde el 2026-10-07: PR #91, merge `021ad13` (rama `feat/analytics-marketing`,
desde `643ba90`, merge de #90). Una migración
(`0112_measurement_conversions`). Operación:
[docs/analytics-marketing.md](docs/analytics-marketing.md). Decisiones: DEC-MEAS-01 a 07 y
DEC-BRAND-ICON-01. Nada está publicado en Internet.

**Qué cambia.** Google Analytics 4, Meta y TikTok se configuran en la consola de #90
(Panel › Configuración › Integraciones › Analítica y marketing). Quien visita la tienda
decide con un aviso de cookies; sin su permiso no se carga nada de terceros y el servidor
no envía nada. La compra se mide una vez, desde la confirmación firmada del pago. El icono
de la pestaña es el isotipo oficial.

**Estado: NOT READY, por datos del propietario**, como antes. Las tres integraciones están
implementadas y probadas con respuestas simuladas; ninguna contra su servicio real.

| | Estado |
|---|---|
| FAVICON | IMPLEMENTADO |
| CONSENT | IMPLEMENTADO |
| GA4 | IMPLEMENTADO · validación real BLOCKED/CREDENTIALS |
| META PIXEL | IMPLEMENTADO · validación real BLOCKED/CREDENTIALS |
| META CAPI | IMPLEMENTADO (compra) · validación real BLOCKED/CREDENTIALS |
| TIKTOK PIXEL | IMPLEMENTADO · validación real BLOCKED/CREDENTIALS |
| TIKTOK EVENTS API | IMPLEMENTADO (compra) · validación real BLOCKED/CREDENTIALS |
| ECOMMERCE TRACKING | IMPLEMENTADO; Meta y TikTok sin `AddPaymentInfo` ni `CompleteRegistration` por decisión de privacidad |
| PURCHASE DEDUP | IMPLEMENTADO |
| CSP | PENDIENTE (CSP-01): auditada, no implantada; lista de dominios en `docs/analytics-marketing.md` §7 |

**Decisiones del propietario al cerrar (2026-10-07)**, en DEC-MEAS-08:

| | Clasificación |
|---|---|
| ANALYTICS-MARKETING-INTEGRATIONS-01 | IMPLEMENTADO |
| Validación real de GA4 | BLOCKED/CREDENTIALS |
| Validación real de Meta | BLOCKED/CREDENTIALS |
| Validación real de TikTok | BLOCKED/CREDENTIALS |
| CSP-01 | PENDIENTE / BLOQUEANTE PARA ACTIVACIÓN EN PRODUCCIÓN. Sin lista especulativa: se cierra validando los dominios reales de la pasarela y de los SDK |
| INT-IMPORT-CYCLE | PENDIENTE / DEUDA TÉCNICA. Se corrige en una microfase propia, INT-IMPORT-CYCLE-01, no en esta fase |
| MEAS-IP-UA | APROBADO BAJO CONSENTIMIENTO MARKETING. Falta decirlo en la política de privacidad y cookies antes de producción (MEAS-PRIVACY-NOTICE) |
| Coincidencia avanzada automática | DESACTIVADA, en el código y en los paneles de Meta y TikTok |
| Producción | NO DESPLEGADA |

**Línea base, sobre `master` `643ba90`** (mismo árbol de código que `2a5632d`): backend
5581 pruebas, 0 fallos, 5 omitidas (también en CI) · Jest 830 · Playwright 187 · lint 0
errores, 22 avisos · `ENSAYO: OK` (138 + 49).

**Grafo de dependencias.** Al empezar la fase «Graphify» no estaba instalado y no se
instaló: el grafo inicial se construyó con análisis estático propio (imports de Python con
`ast`, imports de TypeScript). El propietario lo instaló durante la fase y el grafo final
es de Graphify 0.9.79, sobre `62ed3b8`: todo el código por AST (702 archivos) y, por
extracción semántica, sólo `docs/analytics-marketing.md` y
`docs/integraciones-y-secretos.md`. Quedaron fuera los otros 49 documentos y las 23
imágenes. 18 591 nodos, 46 883 aristas, 616 comunidades. El grafo no se versiona.

| | Antes (análisis propio) | Después (Graphify) |
|---|---|---|
| Ciclos de imports | 1, contado por paquete: `integrations` y sus proveedores | 12, contados por archivo, todos a través de `integrations/__init__.py`. 10 ya estaban en `master`; 2 son de esta fase y repiten el patrón de #90 (ver abajo) |
| Proveedores sin consumidor en ejecución | 0 | 0: `store/measurement/conversions.py` y `store/measurement_views.py` consumen los tres nuevos |
| Llamadas `gtag` / `fbq` / `ttq` | 0 | los tres adaptadores sólo los importa `frontend/app/lib/analytics/service.ts` |
| Código que habla con los tres desde el servidor | — | `store/measurement/adapters.py`, importado sólo por el módulo de proveedores y por `conversions.py` |
| Quién dispara la compra en el servidor | — | `store/views.py` (confirmación del pago) y el comando `send_pending_conversions`; nadie más importa `conversions.py` |
| Consentimiento | — | `lib/consent.ts` lo leen el aviso, el pie, el servicio y los adaptadores; ninguna página lo escribe por su cuenta |

**Corrección.** El análisis propio decía «ningún ciclo nuevo». Contando por archivo,
Graphify encuentra dos: el módulo `providers/measurement.py` con el paquete que lo
registra (igual que los otros cinco proveedores de #90), y ese mismo módulo con
`store/measurement/__init__.py`, que importa `integrations` dentro de una función (igual
que `payments/izipay.py`, `messaging` y `google_identity.py`). Ninguno es un ciclo al
cargar: el paquete importa sus proveedores al final y el otro import es diferido. No se
cambió el código al cierre; queda anotado como INT-IMPORT-CYCLE.

**Límites del grafo.** Graphify no resuelve las llamadas escritas como
`conversions.record_purchase(...)` a su función: la relación entre `views.py` y los
ganchos de compra está a nivel de archivo. No hay arista entre el frontend y el backend
(se hablan por HTTP); los unen los nodos de la documentación. El informe marca 1513
aristas hacia módulos de fuera del repositorio (biblioteca estándar, Django, React), 18
autoreferencias y 1037 aristas repetidas que se funden, casi todas imports repetidos de
`store/tests.py`. Las aristas `indirect_call` de las pruebas de navegador hacia
`service.ts` son coincidencias de nombre, no llamadas.

**Defectos encontrados en la fase**

| ID | Qué pasaba |
|---|---|
| FAVICON-DEFAULT | El icono de la pestaña era el triángulo por defecto del framework, y el que declaraba la página, un círculo de un prototipo |
| PAY-REFERENCE-URL | La referencia del pago viajaba en la dirección de la página de éxito |
| VIEW-ITEM-TWICE | Una ficha de producto se contaba dos veces si su efecto se ejecutaba dos veces (hallado en navegador real) |
| SPA-PRIVATE (revisión, P1) | Con un script de medición ya cargado, navegar dentro de la tienda a una dirección privada lo dejaba presente: lee la dirección por su cuenta. Ahora esa navegación abre un documento nuevo, sin él |
| FORM-PIXELS (revisión, P2) | Los píxeles de marketing estaban en el checkout y el acceso, donde su panel puede hacerles leer los campos |
| MAIL-AFTER-PROVIDERS (revisión, P2) | El primer envío a los proveedores se programaba antes del correo de confirmación y podía retrasarlo o cancelarlo |
| CONTEXT-KEPT (revisión, P2) | Los identificadores del navegador se guardaban en cada checkout aunque nadie fuera a leerlos, y los de pedidos abandonados dependían de una tarea para borrarse |
| STATUS-REPLAY (revisión, P2) | La página de estado daba el evento de compra indefinidamente, con el cupón |
| TIKTOK-CONSENT (revisión, P2) | Retirar el permiso sólo apagaba la cookie de TikTok, no su estado de consentimiento |
| Propio | Durante la fase se sobrescribió `store/tracking_views.py` (seguimiento de reparaciones) al crear un archivo con ese nombre; se restauró desde git en el acto y lo nuevo se llama `measurement` |

**Verificación**

| Medida | Resultado |
|---|---|
| Backend, suite completa en PostgreSQL, sobre `62ed3b8` | 5654 pruebas, 0 fallos, 5 omitidas (73 nuevas) |
| Jest | 933/933 (97 suites; 103 nuevas) |
| Tipos · lint · build | limpio · 0 errores, 22 avisos (los de antes) · correcto |
| `manage.py check` · `makemigrations --check` | sin incidencias · sin cambios |
| Playwright completo, sobre `62ed3b8` | 194/194, 0 omitidas (7 nuevas) |
| Ensayo de producción, sobre `62ed3b8` | `ENSAYO: OK`, 139 comprobaciones y 49 pasos de navegador; no deja nada en el equipo |
| Mutaciones | cada regla nueva de consentimiento, privacidad y compra única se rompió a propósito y una prueba lo detectó |
| Revisión de seguridad independiente | 1 P1 y 6 P2 corregidos con prueba; 1 P2 no corregido: CSP-01, que sigue PENDIENTE y es condición para activar un proveedor en producción; P3 revisados uno a uno |
| Llamadas reales a Google, Meta o TikTok | ninguna: respuestas simuladas en las pruebas, dominios interceptados en el navegador |

El equipo de trabajo no entró en reposo durante las tres corridas largas.

## 2026-10-06 — INTEGRATIONS-CONSOLE-01: los servicios externos se configuran en el panel

Rama `feat/integrations-console`, desde `master` `5da4e99` (merge de #89). Una migración
(`0111_integration_config`). Operación:
[docs/integraciones-y-secretos.md](docs/integraciones-y-secretos.md). Decisiones:
DEC-INT-01 a 07. Nada está publicado en Internet.

**Qué cambia.** Un usuario MASTER configura, prueba y activa el correo, la pasarela de
pago, WhatsApp (por empresa), el ID de cliente de Google y SUNAT desde **Panel ›
Configuración › Integraciones**, sin editar `.env`, sin recompilar el frontend, sin
reconstruir imágenes y sin reiniciar. Las credenciales se guardan cifradas y no vuelven a
mostrarse. Las variables de entorno quedan como respaldo de una instalación que no haya
usado el panel.

**Estado: NOT READY, por datos del propietario.** Igual que al cerrar la fase anterior: no
hay servidor, dominio, SMTP ni claves de Izipay. Lo que cambia es dónde se escribirán.

**Línea base, medida sobre `master` `5da4e99`:** backend 5375 pruebas, 0 fallos, 5
omitidas · Jest 795/795 · Playwright 184/184 · tipos limpios · lint 0 errores, 22 avisos ·
build correcto · `ENSAYO: OK` (131 comprobaciones y 49 pasos de navegador).

**Cada integración, y cómo se demostró que el panel manda**

| Integración | Alcance | Lo que el sistema usa tras activarla en el panel | Prueba |
|---|---|---|---|
| Correo SMTP | instalación | El registro de un usuario sale por el servidor, el usuario y el remitente del panel; cambiarlo cambia el siguiente envío | `test_integrations_smtp` (servidor SMTP real en la prueba) y el ensayo, paso 11c, en la pila de producción |
| Izipay (2 productos) | instalación | El checkout abre el cobro con el producto y las claves del panel; la notificación se verifica con su clave hash; cambiar de producto cambia la ruta de notificación | `test_integrations_payments` (pasarelas falsas de contrato) |
| WhatsApp Business | empresa | El aviso sale con el número y el token de esa empresa; su webhook sólo cree su secreto de aplicación; otra empresa no ve nada | `test_integrations_whatsapp` |
| Google | instalación | El botón recibe ese ID de cliente y un token sólo se acepta para esa audiencia | `test_integrations_google` |
| SUNAT (sólo BETA) | instalación | La emisión recibe esas credenciales SOL y firma con el certificado subido | `test_integrations_sunat` |

En las cinco: desactivada en el panel no recurre al entorno; lo que el servidor no puede
leer no rompe ninguna petición; ningún secreto vuelve por la API, la auditoría, los
registros ni `ops_status`.

**Lo que NO se cerró.** IZIPAY-PRODUCT e IZIPAY-TOKEN-CONTRACT siguen abiertas: «Probar
conexión» con claves de TEST existe y está probado contra las pasarelas falsas, pero no se
ha ejecutado contra Izipay porque no hay claves. Ninguna integración se probó contra su
servicio real: no hay credenciales de ninguno.

**Defectos encontrados en la fase**

| ID | Qué pasaba |
|---|---|
| PAY-TEST-BY-TEXT | La validación de la pasarela distinguía «no responde» de «rechaza las claves» por el texto del error del adaptador (y «red» está dentro de «credenciales»). Ahora por su tipo |
| MAIL-OFF-BYPASS | Un correo apagado en el panel seguía saliendo si el entorno nombraba un backend de desarrollo |
| WHATSAPP-DEFAULT-OFF | El archivo de ejemplo traía `WHATSAPP_PROVIDER=disabled`: WhatsApp no se podía activar desde el panel sin editar el servidor |
| MAIL-UNREADABLE-CRASH | Con la clave raíz cambiada, enviar un correo lanzaba un error dentro de la petición que lo pedía |
| TEST-RACE (revisión, P2) | Una prueba tarda segundos y al terminar marcaba como probado lo que hubiera guardado: un borrador guardado entretanto quedaba validado sin haberse probado |
| OPS-DRAFT-CRASH (revisión, P2) | Un primer borrador con la prueba fallida tumbaba `ops_status` y `healthcheck.sh`, y la consola lo llamaba «activa» |
| PROD-KEYS-CORRECT (revisión, P2) | Las claves de producción de la pasarela salían como «Correcto» sin que nada las comprobara. Ahora «Coherente, sin verificar», y la prueba de TEST dice que la clave hash sólo la prueba una notificación |
| PAY-IN-FLIGHT (revisión, P2) | Apagar, revocar o cambiar la pasarela con un comprador en el formulario de la tarjeta no avisaba. Ahora cuenta los cobros abiertos y pide escribir `INTERRUMPIR` |
| LAST-FOUR (revisión, P3) | Se guardaban en claro los cuatro últimos caracteres de cada secreto |
| IMPORT-PLAIN-SMTP (regla, no defecto) | Hallado en la pila de producción: el panel se niega a copiar del entorno un correo sin cifrar hacia otro equipo. Se mantiene; el ensayo usa un servidor de correo en la máquina del backend |

**Verificación**

| Medida | Resultado |
|---|---|
| Pruebas nuevas de backend (`test_integrations_*`: núcleo, correo, pagos, WhatsApp, Google, SUNAT) | 178, verdes. Cada módulo se comprobó con mutaciones: quitar la lectura del panel, el filtro por empresa, la confirmación o el «apagado es apagado» hace fallar pruebas |
| `test_ops_status` · `test_preflight` · `test_production_settings` | 23 · 46 · 22, verdes |
| Suites existentes de pagos, checkout, fiscal, WhatsApp y Google | verdes (357 de pagos y checkout; 482 con las fiscales) |
| Jest | 830/830 (35 nuevas) |
| Tipos · lint | limpio · 0 errores, 22 avisos (los de siempre) |
| Playwright, spec nueva (`integrations-console`) | 3/3 |
| Revisión independiente de la rama | 0 P1, 4 P2 y 7 P3. Corregidos con prueba los cuatro P2 y seis P3; el séptimo (IZIPAY-MODE-DECLARED) queda anotado como deuda: es una sospecha sin claves reales con que confirmarla |
| Backend, suite completa (PostgreSQL local) | 5581 pruebas, 0 fallos, 5 omitidas (línea base 5375), sobre `2a5632d` |
| Playwright completo, base limpia | 187/187, 0 omitidas, sobre `2a5632d` |
| Build · `manage.py check` · `makemigrations --check` | correcto · sin incidencias · sin cambios pendientes |
| Ensayo de producción (`sh deploy/rehearsal.sh`) | `ENSAYO: OK`, 138 comprobaciones y 49 pasos de navegador, sobre `2a5632d`. Incluye la consola en la pila de producción (28 comprobaciones). Los commits posteriores sólo tocan documentación |

## 2026-10-05 — EXTERNAL-PRODUCTION-CONFIG-01: configuración externa, sin publicar

Rama `chore/external-production-config-01`, desde `master` `7829685` (merge de #88). Sin
migraciones. Operación: [docs/despliegue-produccion.md](docs/despliegue-produccion.md)
(§3 entrega de datos y `preflight`, §9 ensayo, §11 qué tiene que estar comprobado antes
de abrir). Nada está publicado en Internet.

**Estado: NOT READY.** No por el código, que está verde y ensayado, sino porque **no se
recibió ningún dato del propietario**: no hay servidor, dominio, SMTP, claves de Izipay ni
destino de la copia externa. Sin Izipay validado en TEST y sin correo real probado, la
tienda no se puede abrir si va a cobrar en línea.

**Línea base, medida sobre `master` `7829685`:**

| Medida | Resultado |
|---|---|
| Jest · tipos · lint · build | 795/795 · limpio · 0 errores, 22 avisos · correcto |
| `manage.py check` · `makemigrations --check` | sin incidencias · sin cambios pendientes |
| Ensayo de producción | `ENSAYO: OK`, 107 comprobaciones y 49 pasos de navegador |
| Backend, suite completa en local | 5332 pruebas, 1 fallo, 5 omitidas. El fallo es una prueba de límite por minuto (`P0BThrottleSpoofingTest`) cuyas peticiones se repartieron en más de un minuto porque el equipo entró en reposo a mitad; sola pasa (6 de 6). En CI, sobre el mismo árbol: 5332, 0 fallos |
| Playwright completo | 184/184, 0 omitidas, con el equipo ya despierto. Antes hubo cuatro intentos no válidos (entre 158 y 172 pasadas, fallos distintos cada vez y todos por tiempo agotado) mientras el equipo estaba en reposo |

**El equipo de trabajo estuvo en reposo buena parte de la fase** (tapa cerrada, a
batería). Lo que necesita media hora seguida despierto —Playwright y el ensayo— se midió
al final, con el equipo abierto. No se cambió ningún ajuste de energía.

| Asunto | Estado | Por qué |
|---|---|---|
| Producto de Izipay contratado (IZIPAY-PRODUCT) | BLOCKED/OWNER-DATA | No se ha dicho cuál de los dos. No se asume: los dos adaptadores siguen detrás de `PAYMENT_PROVIDER` |
| Prueba contra la pasarela en TEST (IZIPAY-TOKEN-CONTRACT) | BLOCKED/OWNER-DATA | Sin claves de TEST, `IzipaySandboxSmokeTest` y `MiCuentaWebSandboxSmokeTest` se omiten. Ninguna de las dos incidencias se cierra |
| Servidor, dominio, DNS | BLOCKED/OWNER-DATA | No hay servidor al que entrar ni dominio que configurar. No se tocó ningún DNS |
| SMTP real | BLOCKED/OWNER-DATA | Sin credenciales. Lo que sí se comprobó: la tienda envía por SMTP de verdad a un servidor de correo del ensayo |
| Copia externa | BLOCKED/OWNER-DATA | No hay destino. La restauración sí quedó probada en un «servidor nuevo» |
| Tareas programadas y comprobación de estado en el servidor | BLOCKED/OWNER-DATA | No hay servidor. Los cinco comandos corren en el ensayo |
| «Continuar con Google» | BLOCKED/OPTIONAL | Sin ID de cliente. La tienda funciona sin él (ensayado) |
| WhatsApp | BLOCKED/OPTIONAL | Sin credenciales. Las órdenes de servicio funcionan sin él (ensayado) |
| SUNAT | BLOCKED/OPTIONAL | La emisión real no entra en la primera publicación (`FISCAL_ENABLED=0`) |
| Ensayo con configuración equivalente a producción | `ENSAYO: OK`, 131 comprobaciones y 49 pasos de navegador, sobre `4c291a6` | Con correo por SMTP —servidor caído, mudo o rechazando la contraseña incluidos— y restauración en servidor nuevo. Los commits posteriores sólo tocan documentación |

**Hecho en la fase, sin datos externos**

| Qué | Para qué |
|---|---|
| `deploy/preflight.py` | Dice qué falta, qué es opcional y qué se contradice en el archivo de variables, **sin imprimir ningún valor**. Sabe a cuál de los dos productos de Izipay pertenecen las claves y rechaza un juego mezclado. Es la forma de entregar los datos sin pasarlos por un chat |
| Correo por SMTP en el ensayo | Registro con verificación, recuperación de contraseña, servidor de correo caído y contraseña rechazada, contra un servidor de correo propio. Ningún enlace de un solo uso queda en el registro |
| Restauración en un «servidor nuevo» | El procedimiento de la guía (§6.4), hecho: otro proyecto de Docker, volúmenes vacíos, el mismo archivo de variables. Datos, archivos y enlace de seguimiento, iguales |
| Guía: entrega de datos, criterios de salida y tabla exacta de lo que falta | §3 y §11 |

**Corregido en la fase**

| ID | Qué pasaba |
|---|---|
| MAIL-TIMEOUT (P2) | Django espera a un servidor de correo sin límite, y un registro o una recuperación de contraseña envían su mensaje dentro de la petición. Con ocho hilos, un proveedor que dejara de responder tumbaría la API registro a registro. Ahora espera 10 segundos (`EMAIL_TIMEOUT`) |
| MAIL-SSL (P3) | Sólo se podía configurar STARTTLS (puerto 587). Hay proveedores que sólo ofrecen el 465. Se lee `EMAIL_USE_SSL`, y pedir los dos cifrados a la vez se rechaza al arrancar |

**Revisión independiente de la rama:** 2 P1, 5 P2 y 7 P3, todos en la herramienta nueva o
en el ensayo; corregidos con prueba (`5cd9a3d`, `072509f`).

| ID | Qué pasaba |
|---|---|
| PREFLIGHT-ECHO (P1) | `preflight.py` rompía su propia regla: escribía tal cual el servidor y el puerto del correo (una dirección con la contraseña dentro salía entera), aceptaba como dominio cualquier cosa con un punto, y un número mal escrito terminaba en una traza que lo citaba. Ahora sólo imprime un dominio o un servidor si tienen forma de serlo, y ningún error se muestra con el texto que lo provocó |
| PREFLIGHT-PARSE (P2) | Leía el archivo a su manera, no como Compose y Django: una línea `EMAIL_USE_TLS=` vacía le parecía STARTTLS (Django la lee como «no»: la contraseña del correo habría viajado en claro), no entendía un comentario tras un valor ni las líneas con `export`, y daba por apagado un WhatsApp que la aplicación da por encendido |
| PREFLIGHT-PLAIN-AUTH (P2) | Con `--smtp` entraba al servidor de correo con la contraseña real justo después de avisar de que la conexión iba sin cifrar |
| REHEARSAL-TIMEOUT-UNTESTED (P2) | Los dos casos de «servidor de correo caído» fallaban al instante y no ejercitaban `EMAIL_TIMEOUT`. Se añadió un servidor que acepta la conexión y calla: la tienda espera lo configurado (medido: 10,1 s con el valor por omisión de 10) y contesta |
| REHEARSAL-CLEANUP (P3) | Un fallo en mitad del paso de «servidor nuevo» dejaba atrás los volúmenes del primer proyecto |

**Sin cambios, a propósito:** SERIAL-PICK, SERIAL-TRANSFER, SERIAL-COUNT, PAY-RECONCILE,
TypeScript 6 (#80), @types/node (#85) y el resto de la deuda P4 siguen para después de
publicar.

**Medido al cerrar, sobre `4c291a6`:** backend 5375 pruebas, 0 fallos, 5 omitidas (local y CI); Jest 795/795; Playwright 184/184, 0
omitidas; tipos limpio; lint 0 errores / 22 avisos; build correcto; `manage.py check` y
`makemigrations --check` sin incidencias; ensayo `ENSAYO: OK` (131 + 49).

## 2026-10-05 — PRODUCTION-READINESS-01: ensayo, endurecimiento y lista de publicación

Rama `chore/production-readiness-01`, desde `master` `78ad79c` (merge de #87). Sin
migraciones. Decisiones: DEC-LIMIT-01, DEC-LOG-01. Operación:
[docs/despliegue-produccion.md](docs/despliegue-produccion.md) (§1 límites, §6 copias y
estado, §9 ensayo, §10 lista de publicación). Nada está publicado en Internet.

**Clasificación: READY WITH CONDITIONS.** El código está listo y ensayado; publicar
depende de infraestructura y datos que sólo puede dar el propietario (lista completa en
la guía, §10).

**Línea base, medida sobre `master` `78ad79c`:** backend 5255 pruebas, 0 fallos, 5
omitidas (local y CI); Jest 787/787; Playwright 184/184; tipos, lint y build limpios. El
ensayo de producción dio `ENSAYO: 7 FALLO(S)`: una sola comprobación del guion, que
pedía a la portada una variante de hero que la V3 no tiene (REHEARSAL-HERO-V3).

| Asunto | Antes | Ahora |
|---|---|---|
| Ensayo de producción sobre el código actual | DESACTUALIZADO (anterior a cinco fases) y en rojo sobre `master` | `ENSAYO: OK`, 107 comprobaciones y 49 pasos de navegador |
| Lo que recorre el ensayo | Rutas, cabeceras, imágenes, evidencias, límites de acceso, copia y restauración | Además: tamaño de petición, notificación de pago, equipo con serie de punta a punta, documentos, seguimiento, WhatsApp y Google apagados, registros, estado |
| Tope de tamaño de una petición (IMPORT-BODY-LIMIT) | PENDIENTE: ni Caddy ni Django limitaban nada | IMPLEMENTADO: por ruta, en Caddy, en Django y en la pantalla |
| Peticiones a medio enviar (SLOW-BODY) | Nueve bastaban para dejar la API sin responder | Caddy lee el cuerpo antes; tiempos de espera |
| Registros | Guardaban enlaces de seguimiento, tokens de invitación e IMEI buscados; crecían sin límite | Sin lo que una dirección puede llevar; rotan |
| Copia de seguridad | Un archivo cortado quedaba como copia | Sólo se guarda entera; deja la hora de la última completa |
| Restauración | Comprobaba un archivo después de reemplazar la base | Comprueba todo antes de tocar nada; prueba de recuperación en el ensayo |
| Saber que algo va mal | PENDIENTE | `deploy/healthcheck.sh` y `manage.py ops_status` |
| Tareas programadas | Cuatro líneas sueltas en la guía | `deploy/crontab.example` |
| Deshacer una vinculación cuenta–cliente (CUSTOMER-UNLINK-UI) | Sólo por la API | En la ficha del cliente |
| Izipay contra la pasarela real (IZIPAY-TOKEN-CONTRACT) | BLOCKED/CREDENTIALS | BLOCKED/CREDENTIALS |
| WhatsApp real · Google real | BLOCKED/CREDENTIALS | BLOCKED/CREDENTIALS. Sin credenciales no rompen nada (ensayado) |
| Elegir el equipo al vender, transferirlo, recuento por series | PENDIENTE | PENDIENTE POST-LAUNCH (abajo) |

**Corregido en la fase**

| ID | Qué pasaba |
|---|---|
| SLOW-BODY (P2) | Django atiende con ocho hilos y Caddy le pasaba cada cuerpo según llegaba. Nueve peticiones sin sesión que anunciaban un cuerpo y no lo terminaban dejaban la API sin responder. Reproducido en la pila de producción: sin respuesta en 8 s; corregido: 200 en 0,1 s |
| IMPORT-BODY-LIMIT (P2) | Ningún tope de tamaño. Cualquier ruta, el inicio de sesión incluido, aceptaba un formulario con archivos de cualquier tamaño y lo escribía en disco |
| LOG-REDACT (P2) | El registro de acceso guardaba el enlace de seguimiento de cada reparación (quien lo tiene responde la cotización como el cliente), el token de las invitaciones, el token de verificación de WhatsApp y los IMEI, documentos y teléfonos escritos en un buscador |
| DOC-TIMEZONE (P2) | La nota de venta, su ticket, la salida térmica y el comprobante de pedido imprimían la hora en UTC: una venta de las 21:30 salía fechada a las 02:30 del día siguiente. También la fecha de caducidad en el correo de invitación |
| RESTORE-LATE-CHECK (P2) | `restore.sh` buscaba el archivo de imágenes después de borrar y reemplazar la base. Con el nombre mal escrito dejaba la tienda detenida y los datos anteriores borrados |
| BACKUP-FILES-PARTIAL (P3) | Un archivo de imágenes y evidencias cortado, o que no era un archivo, quedaba en la carpeta como una copia buena y el guion terminaba bien |
| MAIL-CONSOLE-DEFAULT (P3) | Sin la línea `EMAIL_BACKEND`, producción arrancaba escribiendo cada correo —con sus enlaces de un solo uso— en el registro |
| LOG-ROTATION (P3) | Docker guardaba el registro de cada contenedor en un archivo sin límite |
| PROXY-TIMEOUTS (P3) | Caddy no tenía tiempos de espera |
| TRACKING-REFERRER-HEADER (P3) | La página de seguimiento pedía «sin referente» sólo con una etiqueta del documento, no en la cabecera |
| GUNICORN-CONTROL-SOCKET (P3) | Un `[ERROR]` en cada arranque del backend: gunicorn 26 intentaba crear su socket de control donde no puede escribir |
| IGNORE-GAPS (P3) | Variantes de `.env`, claves `.key` y volcados SQL se podían confirmar en Git; la imagen del backend copiaba certificados, copias o la configuración del agente de impresión si estaban junto al código |
| REHEARSAL-HERO-V3 | El ensayo fallaba sobre `master` por una comprobación de la portada anterior |

**Revisión independiente de la rama:** 1 P1, 3 P2 y 7 P3; corregidos con prueba los que
tenían corrección en el código (`f500342`, `e033aee`, `d14a1cd`).

| ID | Qué pasaba |
|---|---|
| SLOW-BODY-WEBHOOKS (P1) | La primera corrección de SLOW-BODY cubría la ruta general y dejaba fuera las dos que reciben llamadas de fuera. Nueve peticiones a medio enviar al webhook de WhatsApp dejaban la API sin responder (reproducido: sin respuesta en 8 s; corregido: 200 en 0,1 s) |
| CSRF-BODY-READ (P2) | La comprobación CSRF buscaba el token en el formulario antes que en la cabecera, y para eso leía el cuerpo entero: cualquier cuenta con sesión, la de un cliente incluida, podía hacer que el servidor recibiera y guardara hasta 111 MiB en una ruta que después le negaba. El token se lee ahora sólo de la cabecera |
| OPS-FALSE-ALARM (P2) | `ops_status` daba la alarma durante un día por cada compra abandonada (cada una deja un pago esperando). Ahora es una nota; un mensaje de WhatsApp sin entregar también, y tres sin ninguno entregado es la alarma |
| OPS-PENDING-BLIND (P2) | Un aviso de WhatsApp que nadie intentó enviar no tiene reintento programado, y la consulta que buscaba atrasados por ese campo no lo veía |
| PROXY-ERROR-LOG · GUNICORN-ERROR-LOG (P3) | Caddy, cuando no alcanza a una aplicación, y gunicorn, cuando falla al atender una petición, anotaban su dirección entera |
| BODY-LIMIT-502 (P2, hallado por el ensayo) | Django rechaza una petición demasiado grande y cierra sin leerla; Caddy, que aún le estaba enviando el cuerpo, respondía 502 en vez de 413 una de cada diez veces (8 de 80 en la pila de producción; hizo fallar el ensayo dos veces). Caddy compara ahora él mismo lo que la petición anuncia y responde 413 sin llamar a ninguna aplicación: 200 de 200 |
| CRON-NO-DIR · HEALTH-QUOTED-DOMAIN · BACKUP-TRAILER (P3) | Las tareas programadas fallaban hasta que existiera `backups/`; un `SITE_DOMAIN` entre comillas rompía la comprobación de estado; la marca de volcado completo se buscaba justo en la última línea donde cabía |

**Auditado sin cambios**

- **Ajustes de producción** (leídos del contenedor en el ensayo): `DEBUG=False`; cookies
  `Secure`, `HttpOnly`, `SameSite=Lax`; HSTS; `X-Frame-Options: DENY`; CORS y CSRF sólo
  para el dominio propio; sólo JSON; sin admin de Django; un proxy de confianza.
- **Izipay**: el entorno es explícito (`IZIPAY_ENV`, o el prefijo de las claves en «Mi
  Cuenta Web») y no se deduce de `DEBUG`; lo que tiene el navegador no paga un pedido; la
  notificación repetida paga una vez. Cubierto por `test_izipay_contract` y
  `test_micuentaweb`, y por el ensayo con claves propias.
- **Secretos**: ningún secreto real en los archivos versionados ni en el historial; ningún
  secreto del servidor en los archivos del frontend (comprobado en la imagen). El
  historial conserva `backend/db.sqlite3` de 2026 sin usuarios, pedidos ni sesiones.
- **Almacén**: fotos de producto, imágenes de la tienda y evidencias sobreviven a apagar,
  reconstruir, recrear y restaurar.
- **Tareas**: `flushexpiredtokens`, `cleanup_storefront_images` y
  `send_pending_notifications` corren en el contenedor. No hay tareas fiscales
  periódicas. No hace falta ningún servicio más.
- **Documentos**: nota de venta A4 (con serie, con descuento, con logotipo, sin él),
  ticket de 80 mm y ticket de cotización, revisados a la vista.

**PENDIENTE POST-LAUNCH** (el flujo actual opera sin ello)

| ID | Qué falta | Qué hacer mientras |
|---|---|---|
| SERIAL-PICK | Resuelto en SERIAL-PICK-01 (2026-10-11): el equipo se identifica al vender o al entregar | — |
| SERIAL-TRANSFER | Un equipo con serie no se transfiere entre sucursales | No afecta a una empresa con una sucursal |
| SERIAL-COUNT | No hay recuento por lectura de series | La lista de Inventario › Equipos sirve de hoja de comprobación |
| PAY-RECONCILE | Si la notificación de la pasarela no llega, el pedido cobrado queda esperando | La comprobación de estado anota cuántos pagos siguen sin respuesta (sin alarma: no se distingue de una compra abandonada); si un cliente dice que pagó, se comprueba en el panel de la pasarela |
| PAY-UNCONFIGURED-500 | Sin credenciales de Izipay, la notificación y el inicio de un pago responden 500 con «la pasarela no está configurada» | Es el estado «sin Izipay»: el catálogo, el carrito y la caja funcionan |
| LOGIN-IDENTIFIER-LOG | Un inicio de sesión fallido deja en el registro lo escrito como usuario | — |

**Medido:** backend 5332 pruebas, 0 fallos, 5 omitidas; Jest 795/795; Playwright 184/184, 0 omitidas; tipos limpio; lint 0
errores / 22 avisos; build correcto; `manage.py check` y `makemigrations --check` sin
incidencias; ensayo `ENSAYO: OK`.

**Bloqueado por datos del propietario:** dominio, DNS y servidor; SMTP; cuál de los dos
productos de Izipay tiene contratado y sus claves; destino de la copia externa; y,
opcionales, WhatsApp Business, ID de cliente de Google y credenciales de SUNAT.

## 2026-10-05 — PAYMENTS-EQUIPMENT-DOCUMENTS: Izipay, registro de equipos, plantillas y documentos

Rama `feat/payments-equipment-documents`. Migración `0110` (sólo opciones de un campo).
Decisiones: DEC-PAY-01 (actualizada), DEC-PAY-02, DEC-UNIT-IMPORT-01, DEC-DOC-01. Guía:
[docs/pagos-equipos-documentos.md](docs/pagos-equipos-documentos.md). Nada está
publicado en Internet.

| Funcionalidad | Antes | Ahora |
|---|---|---|
| Izipay «SDK web / Checkout» (developers.izipay.pe) | IMPLEMENTADO con pasarela falsa · BLOCKED/CREDENTIALS | Sin cambios de contrato; corregido un 500 sin autenticar |
| Izipay «Mi Cuenta Web», API REST V4 (la página oficial indicada) | PENDIENTE (no existía) | IMPLEMENTADO con pasarela falsa · BLOCKED/CREDENTIALS para la prueba real |
| Un proveedor de pago por instalación (`PAYMENT_PROVIDER`) | PARCIAL (el ajuste existía y no decidía nada) | IMPLEMENTADO |
| Consultar a la pasarela si la notificación no llega (PAY-RECONCILE) | PENDIENTE | PENDIENTE |
| «+ Registrar equipo» con serie e IMEI a la vista | PARCIAL (la recepción iba incrustada en el panel y callaba sobre productos sin serie) | IMPLEMENTADO |
| Plantilla de productos con instrucciones y ejemplo de imágenes | PARCIAL (una hoja, sin explicación) | IMPLEMENTADO |
| Carga masiva de equipos con serie | PENDIENTE (el archivo de stock los rechaza) | IMPLEMENTADO («Equipos serializados.xlsx») |
| Nota de venta A4 con logotipo, sucursal, tipo de pago, código por línea, serie e IMEI, importe en letras | PARCIAL (cuatro columnas, sin logotipo) | IMPLEMENTADO |
| Ticket de 80 mm y salida térmica con los mismos datos | PARCIAL | IMPLEMENTADO |
| Comprobante de pedido y ticket de cotización con el mismo diseño | PARCIAL (cada uno el suyo) | IMPLEMENTADO |
| Descuento por línea en la nota de venta (NOTE-LINE-DISCOUNT) | — | PENDIENTE: la venta guarda el descuento del pedido entero |
| Representaciones impresas de boleta y factura | IMPLEMENTADO | Sin cambios (formato regulado) |

**Izipay.** El código implementa un producto oficial de Izipay y la página indicada
documenta otro. Veredicto de la auditoría: B, integración oficial válida pero distinta
de la página. Se añadió el segundo como adaptador aparte; `PAYMENT_PROVIDER` elige uno
y la notificación del otro responde 404. La comparación punto por punto está en la guía,
§1.1. Falta saber de cuál de los dos tiene credenciales el propietario.

**Equipos.** Registrar un equipo es un formulario con su serie y su IMEI desde el primer
momento; un producto que no lleva serie se explica y se puede activar ahí. Los errores
del servidor nombran su campo.

**Carga masiva.** La plantilla de productos explica cómo viajan las imágenes y sus hojas
de ayuda no se importan. Los equipos con serie tienen plantilla propia: una fila es un
equipo, se previsualiza y entra todo o nada, por el mismo escritor que el formulario.

**Documentos.** Un solo trazado A4 y una sola cabecera de ticket. Serie e IMEI se leen
de los movimientos de Kardex de la venta. El logotipo es el de la empresa y se guarda
con cada nota.

**Corregido en la fase**

| ID | Qué pasaba |
|---|---|
| IZIPAY-SIG-ASCII | Una firma con un carácter no ASCII hacía responder 500 a la notificación de pagos, sin autenticar. No cambiaba ningún estado |
| DOC-DELIVERY-CITY | «Delivery Arequipa» se imprimía en los documentos de cualquier empresa; ahora nombra la ciudad de cada una |
| DOC-MARKUP | El nombre de un producto con «&» o «<» se interpretaba como formato del PDF |
| PANEL-OPTION-AMBIGUA | Una prueba del panel de equipos pedía «una opción iPhone 16» sin decir en qué lista; pasaba por el orden de carga |
| WEBHOOK-BODY (P2, revisión) | La notificación de «Mi Cuenta Web» leía un cuerpo de cualquier tamaño, sin sesión ni límite de peticiones. Las dos notificaciones de pago rechazan ahora más de 128 KB por la longitud declarada, antes de leer |
| WEBHOOK-CHARSET, IZIPAY-SURROGATE (P3) | Un juego de caracteres inventado en la cabecera, o un sustituto UTF-16 suelto en la firma, daban 500 en la notificación; ahora se rechazan |
| UNIT-IMPORT-REACH (P3) | Una carga de equipos se podía leer sin alcanzar una sucursal cuyas únicas filas eran errores; esas filas también llevan serie e IMEI |
| UNIT-COST-NAN (P3) | Un costo «NaN» hacía fallar la previsualización y el formulario de un equipo |
| TEST-TRACKING-RANDOM | Una prueba de navegador buscaba el id de la orden («24») dentro de un código aleatorio de 43 caracteres; fallaba sola una de cada cien veces |
| UNIT-PRODUCTS-SCOPE (P3) | La lista de productos del formulario sumaba el stock de toda la empresa para quien sólo opera en algunas sucursales |

**Revisión independiente de seguridad de la rama:** sin P1; 1 P2 y 5 P3, corregidos con
prueba (`2d76298`).

**Bloqueado por datos del propietario:** qué producto de Izipay tiene contratado y sus
claves de TEST (`MiCuentaWebSandboxSmokeTest` o `IzipaySandboxSmokeTest`).

**Medido:** backend 5255 pruebas, 0 fallos, 5 omitidas; Jest 787/787; Playwright 184/184, 0 omitidas; tipos y build
limpios; lint 0 errores y 22 avisos (los de siempre).

## 2026-10-05 — SERVICE-TRACKING: seguimiento, cotizaciones, WhatsApp, equipos con serie, portada y Google

Rama `feat/service-tracking-equipment`. Migraciones `0102`–`0109`. Decisiones:
DEC-DEVICE-01, DEC-TRACKING-01, DEC-QUOTE-01, DEC-WHATSAPP-01, DEC-SERIAL-01,
DEC-CUSTOMER-01, DEC-STOREFRONT-CAT-01, DEC-GOOGLE-01. Guía de uso y operación:
[docs/seguimiento-whatsapp-equipos.md](docs/seguimiento-whatsapp-equipos.md).
Nada está publicado en Internet.

| Funcionalidad | Antes | Ahora |
|---|---|---|
| Serie e IMEI según el tipo de equipo, con dígito de control; segundo IMEI; «este equipo ya estuvo aquí» | PARCIAL (texto libre) | IMPLEMENTADO |
| Seguimiento del cliente por enlace seguro, sin cuenta | PENDIENTE | IMPLEMENTADO |
| «Mis reparaciones» en la web, con cuenta | PENDIENTE (EVIDENCE-CUSTOMER-WEB) | IMPLEMENTADO |
| Fotos compartidas visibles para el cliente en la web | PENDIENTE | IMPLEMENTADO (en el enlace de seguimiento) |
| Aprobación de cotización por el cliente | IMPLEMENTADO (sólo app) | IMPLEMENTADO (app y enlace) |
| Aprobación anotada por el personal, con canal y autor | PENDIENTE | IMPLEMENTADO |
| Reabrir una cotización aprobada (anula la aprobación) | PENDIENTE | IMPLEMENTADO |
| Ticket de 80 mm de la cotización aprobada | PENDIENTE | IMPLEMENTADO |
| Avisos al cliente en los pasos reales del servicio | PARCIAL (defecto: no se emitían) | IMPLEMENTADO |
| Avisos por WhatsApp (API oficial): consentimiento, plantillas, reintentos, webhook, estados | PENDIENTE | IMPLEMENTADO con proveedor falso · BLOCKED/CREDENTIALS para el envío real |
| Inventario › Equipos (productos con número de serie) | PENDIENTE | IMPLEMENTADO |
| KPI «Equipos disponibles» | PENDIENTE | IMPLEMENTADO |
| Categorías de la portada configurables por tienda; pantalla de categorías | PARCIAL (lista completa por nombre; sin pantalla) | IMPLEMENTADO |
| Carrusel de productos: arrastre, reposo fuera de pantalla, sin retener la rueda | PARCIAL | IMPLEMENTADO |
| «Continuar con Google» | PENDIENTE | IMPLEMENTADO · BLOCKED/CREDENTIALS (falta el ID de cliente) |
| Transferir equipos con serie entre sucursales; recuento por series; elegir el equipo al vender | — | PENDIENTE |
| Pantalla para deshacer la vinculación cuenta–cliente (CUSTOMER-UNLINK-UI) | — | PENDIENTE (la API existe) |
| `hero_variant` | OBSOLETO | OBSOLETO (sin cambios) |

**Equipo.** Qué identificadores pide cada tipo lo decide el servidor. El IMEI se valida
con su dígito de control; lo ausente es vacío, nunca un marcador. La recepción ve si
el equipo ya estuvo en el taller.

**Seguimiento.** Cada orden nace con un enlace opaco (`/seguimiento/<código>`): estado,
avance, cotización, pagos, fotos compartidas y garantía, con serie e IMEI
enmascarados. Quien tiene cuenta lista sus reparaciones en `/repairs`.

**Cotizaciones.** El registro dice quién decidió y por dónde. El personal anota lo que
el cliente le dijo con una capacidad propia. Reabrir anula la aprobación. El ticket
sólo existe para una aprobación vigente.

**WhatsApp.** Proveedor oficial detrás de una interfaz; configuración por empresa sin
secretos en la base; consentimiento; bandeja de salida con reintentos; webhook
firmado. Un fallo no deshace el trabajo.

**Equipos con serie.** Son parte del stock: la cantidad del producto es la de equipos
disponibles, impuesto en el escritor único del Kardex.

**Corregido en la fase**

| ID | Qué pasaba |
|---|---|
| NOTIFY-REAL-PATHS | «Listo para recoger», «entregado» y «esperando repuesto» nunca se avisaban: los pasos reales no pasaban por el único punto que avisaba |
| TRACKING-REVOKE | Un enlace revocado se recreaba al listar las reparaciones |
| TRACKING-BORN | Una orden recién creada no mostraba su enlace (hallado por la prueba de navegador) |
| TRACKING-SPELLING | Tres variantes del último carácter del enlace abrían la misma orden |
| SERVICE-REVIEW | 2 P2 y 5 P3 de una revisión independiente (ver `07_CHANGELOG.md`) |
| CAROUSEL-JUMP | «Inicio» o «Fin» justo después de una flecha dejaban la fila entre dos tarjetas: en Chromium un salto no cancela un desplazamiento suave en marcha |
| CAROUSEL-RESUME | Reanudar el carrusel enseguida de pausarlo lo dejaba inmóvil |
| READ-ONLY-GET | Consultar el estado del enlace o imprimir el ticket escribían en la base; en la superficie interna una lectura no escribe |

| Medida | Resultado |
|---|---|
| Backend | 5172 pruebas en PostgreSQL, 0 fallos, 4 omitidas (suite completa local) |
| Jest | 765/765 |
| Tipos · lint · build | limpio · 0 errores, 22 avisos · correcto |
| Playwright | 183/183, 0 omitidas |

## 2026-10-04 — MEDIA-EVIDENCE: imágenes de producto, carga masiva con imágenes y evidencias de servicio

Rama `feat/media-evidence`. Migraciones `0100` (galería de producto) y `0101` (nota y
etapas de evidencia). Decisiones: DEC-MEDIA-01, DEC-IMPORT-MEDIA-01, DEC-EVIDENCE-01.
Guía de uso y de operación: [docs/imagenes-y-evidencias.md](docs/imagenes-y-evidencias.md).
Nada está publicado en Internet.

| Funcionalidad | Antes | Ahora |
|---|---|---|
| Imagen de producto subida desde el panel | PENDIENTE (sólo una URL absoluta) | IMPLEMENTADO |
| Galería por producto: varias, principal, orden, texto alternativo | PENDIENTE | IMPLEMENTADO |
| Carga masiva de productos (inspeccionar → previsualizar → aplicar) | IMPLEMENTADO | IMPLEMENTADO, sin cambios de contrato |
| Carga masiva con imágenes (archivos sueltos o ZIP) | PENDIENTE | IMPLEMENTADO |
| Evidencias de servicio: privadas, por etapa, anulables | IMPLEMENTADO | IMPLEMENTADO |
| Nota por foto; etapas de repuestos, listo para entrega y garantía | PENDIENTE | IMPLEMENTADO |
| Varias fotos a la vez, cámara del teléfono, visor, antes/después | PARCIAL (una foto por vez) | IMPLEMENTADO |
| Galería de evidencias para el cliente en la web | PENDIENTE | PENDIENTE (el contrato existe; no hay pantalla) |
| Requerir evidencia por etapa, plantillas de evidencia, ZIP de evidencias, miniaturas en el PDF de recepción | — | PROPUESTA |
| `hero_variant` | en uso hasta la V3 | OBSOLETO (la V3 no lo lee; sin migración en esta fase) |

**Imágenes de producto.** Una tubería, no dos: los píxeles pasan por la de las imágenes
de la tienda (recodifica, aísla por empresa, limpia lo que nadie muestra). `ProductImage`
sólo dice qué producto muestra qué imagen. `Product.image_url` sigue siendo lo que leen el
catálogo, el carrito, los pedidos y la caja: ahora es la dirección de la principal. El
producto público gana `images`.

**Carga masiva con imágenes.** Dos columnas nuevas con nombres de archivo («Imagen
principal», «Imágenes» separadas por `|`). Los archivos viajan con el Excel, sueltos o
en un ZIP. La previsualización dice, por fila y con el archivo nombrado, qué falta o qué
está mal; con un error no se aplica. Entre previsualizar y aplicar las imágenes esperan
como imágenes sin colocar de la empresa (24 h). Aplicar es todo o nada.

**Evidencias de servicio.** Lo que había se extiende: nota por foto (corregible, con el
texto anterior en la auditoría), etapas de repuestos, listo para entrega y
garantía/reingreso, conteo por etapa. En el panel: varias fotos a la vez, cámara del
teléfono, visor a pantalla completa y antes/después. Una foto no se reemplaza ni se
borra; no mueve el estado de la orden.

**Corregido en la fase**

| ID | Qué pasaba |
|---|---|
| IMPORT-ERRORS-404 | El reporte de errores de una importación respondía 404 desde el navegador |
| AUTH-REGISTER-CSRF | Registrarse con una sesión abierta en otra pestaña respondía «CSRF Failed» |
| DEV-PROXY-UPLOAD | El proxy de desarrollo reenviaba `Expect` y convertía una subida en 502 (sólo con curl; el panel no lo sufría) |
| EVIDENCE-THROTTLE | La galería de evidencias gastaba el cupo de «cambios de estado»: con muchas fotos no se podía subir |
| IMPORT-TEMPLATE | La plantilla de la plataforma no se reconocía y su fila de ayuda se importaba como producto |
| MEDIA-REVIEW | 12 hallazgos de una revisión independiente (ver `07_CHANGELOG.md`) |

| Medida | Resultado |
|---|---|
| Backend | 4965 pruebas en PostgreSQL 16, 0 fallos, 4 omitidas (suite completa local) |
| Jest | 659/659 |
| Tipos · lint · build | limpio · 0 errores, 22 avisos · correcto |
| Playwright | 179/179, 0 omitidas |
| Ensayo de producción | NO repetido: el último (`9043c89`) es anterior a #81, #82 y a esta fase |

**Pendiente real**

| ID | Estado | Qué falta |
|---|---|---|
| IMPORT-BODY-LIMIT | PENDIENTE | Caddy no pone tope al cuerpo de una petición; el de 100 MB se comprueba cuando el servidor ya la recibió |
| EVIDENCE-CUSTOMER-WEB | PENDIENTE | El cliente no tiene pantalla web para ver las evidencias que se le comparten |
| TEST-ORDER-C15 | PENDIENTE | `C15InitialRaceTest` falla si es la primera clase en ejecutarse (ya ocurría en `master`) |
| HERO-VARIANT-LEGACY | OBSOLETO | Retirar `hero_variant` del modelo y de la API |
| DEP-TS6 | PROPUESTA | TypeScript 6 (#80) |

## 2026-10-04 — Cierre: frontend V3 como único diseño

`master` en `23d2603`. PR #82 (V3), #81 (PAYMENT-FISCAL-PRINT-01), #67 (`reportlab` 5),
#64 (`actions/setup-python` 7) y #59 (infraestructura de producción) están integrados.
Nada está publicado en Internet.

**Frontend V3.** La presentación V3 del propietario es el único diseño de la tienda, las
páginas de cuenta y el panel, sobre la lógica que ya tenía `master`. Cada archivo se
comparó a tres bandas; no se copió ninguna carpeta encima.

- Portada en la composición V3: hero partido, firma de marca con el isotipo como marca de
  agua al 13 % en ambos temas, categorías ilustradas, proceso de servicio, carrusel,
  soluciones, cercanía, preguntas y campaña.
- Todo sale del backend. Los rótulos del hero son las categorías de la tienda y el taller
  sólo se nombra si hay servicios publicados. Ninguna imagen está compilada: son las que
  la tienda sube desde su panel.
- Un solo hero, que sigue al tema. Desaparecen la losa oscura y la opción «Estilo del
  hero» del panel. La API sigue enviando `hero_variant`; el frontend lo ignora
  (HERO-VARIANT-LEGACY).
- El carrusel avanza despacio y de forma continua, cede el control al tocar, enfocar o
  desplazar, y se reanuda con su botón. Con «reducir movimiento» no avanza.
- El frontend anterior queda en la etiqueta `frontend-anterior-2026-10-04`.

**Corregido al verificar — CART-CSRF-01.** Con la sesión iniciada, agregar al carrito,
cambiar una cantidad, quitar una línea o validar un cupón respondían «CSRF token missing».
Esas peticiones salían sin el token y la cookie de acceso hace que el servidor lo exija.
Un visitante anónimo no lo sufría. Ahora salen por `fetchWithAuth`.

**Dependencias.** #67 se integró tras comparar a la vista los PDF con `reportlab` 4.4.10 y
5.0.1: nota de venta A4, ticket y recibo idénticos; factura A4 y ticket fiscal sólo
difieren en hora, resumen y QR. #64 se integró. #80 (TypeScript 6) queda abierta como
DEP-TS6: su CI pasa, no hace falta para el lanzamiento y se decide con calma.

| Medida | Resultado |
|---|---|
| Backend | 4865 pruebas en PostgreSQL 16, 0 fallos, 4 omitidas (CI de `1b62f31`) |
| Jest | 609/609 |
| Tipos · lint · build | limpio · 0 errores, 23 avisos · correcto |
| Playwright | 176/176, 0 omitidas |
| Ensayo de producción | `ENSAYO: OK` (83 + 26) sobre `9043c89`; hay que repetirlo sobre el commit que se despliegue |

**Pendiente real**

| ID | Estado | Qué falta |
|---|---|---|
| PRODUCT-PHOTO-UPLOAD | PENDIENTE | La foto de un producto sólo admite una dirección absoluta; no se puede subir desde el panel como las del hero y las categorías |
| DEV-PROXY-UPLOAD | PENDIENTE | En desarrollo, el proxy de Next rechaza subidas de más de ~1 MB. Producción no pasa por ese proxy |
| HERO-VARIANT-LEGACY | PROPUESTA | Retirar `hero_variant` del modelo y de la API |
| DEP-TS6 | PROPUESTA | TypeScript 6 (#80) |
| Texto de marca | DECISIÓN DEL PROPIETARIO | La V3 nombra a Apple en sus textos; no se compiló. El titular es el que la tienda escribe en su panel |

## 2026-10-04 — PAYMENT-FISCAL-PRINT-01: pago, comprobante impreso e impresión en tienda

Rama `feat/payment-fiscal-print`. Migraciones `0098` (logotipo) y `0099` (impresión).
Decisiones: DEC-PAY-01, DEC-FISC-PRINT-01, DEC-FISC-LOGO-01 y DEC-PRINT-01.

**Izipay.** Auditoría de la integración y un Izipay falso para pruebas
(`payments/fake_izipay.py`): el checkout corre hasta el socket y el falso comprueba el
contrato del token; firma, manipula y repite notificaciones. Ninguna prueba necesita
credenciales. Encontrado y corregido: una notificación contradictoria, bien firmada,
que llegaba después de autorizar marcaba el intento como «fallo de integridad» con el
pedido ya pagado (IZIPAY-REPLAY-INTEGRITY).

- **BLOCKED/CREDENTIALS**: la prueba contra el sandbox real (`IzipaySandboxSmokeTest`).
  La forma exacta del cuerpo de `Token/Generate` no se pudo leer en la referencia
  pública de Izipay; el arnés no la exige y sólo el sandbox la confirma
  (IZIPAY-TOKEN-CONTRACT).

**Representación impresa.** Auditada contra los Anexos I y II de la RS 114-2019/SUNAT.
No se encontró un «Anexo B» vigente sobre el papel.

- La etiqueta del documento del adquirente sigue su tipo (RUC, DNI, carné de
  extranjería, pasaporte). Antes decía «RUC» también en una boleta a un DNI.
- La leyenda nombra el comprobante; cada ítem lleva su unidad de medida; el ticket de
  80 mm imprime el precio de venta unitario y el valor resumen; el importe en letras y
  la forma de pago llegan al papel.
- Pendiente: totales exonerado/inafecto y descuentos por separado (FISCAL-PRINT-EXO).

**Logotipo.** La tienda elige el de sus comprobantes en Configuración › Comprobantes
(una imagen subida por ella). Cada comprobante congela el logotipo con el que se emitió:
una reimpresión lo conserva aunque la tienda lo cambie o lo borre. Se dibuja tal cual,
sin sombra, en el papel y en la vista previa.

**Impresión en tienda.** Cola por sucursal (`Printer`, `PrintAgent`, `PrintJob`) y un
agente local de un solo archivo (`backend/print_agent/`) para térmicas de 80 mm en red.

- El ticket se encola solo cuando un comprobante queda firmado o una nota de venta se
  crea, sobre un pedido pagado, en un local con impresora automática.
- Un trabajo por documento; cada entrega con su token; el agente no imprime dos veces
  lo que ya imprimió. Tras cinco intentos, una persona lo reenvía.
- Un agente sólo ve los trabajos de su sucursal. Las direcciones de impresora tienen
  que ser de una red local.
- La venta en caja responde con el trabajo que encoló: cobrar desde un teléfono no abre
  ningún diálogo de impresión. Pantalla nueva: Administración › Impresoras.

**De la tienda en línea al papel**, con las piezas de verdad y sin credenciales
(`test_payment_to_print.py`): carrito → checkout → Izipay falso firma → pagado →
comprobante firmado → PDF A4 y ticket → trabajo → el agente lo entrega a una impresora
simulada. Un pago rechazado, manipulado o repetido no deja ni un comprobante ni un
ticket de más.

**Revisión independiente del código** antes de subir: sin P0 ni P1; 12 hallazgos P2–P4,
todos corregidos con su prueba. Los tres P2: un `ESC` en el nombre del comprador llegaba
a la impresora como orden; no había espera entre reintentos; y la caja decía «enviado»
sin ningún agente escuchando. El detalle está en DEC-PRINT-01.

Validación local: 121 pruebas nuevas de backend (pago 21, papel 10, logotipo 10, cola 32,
API 29, agente 16, extremo a extremo 3), en SQLite y en PostgreSQL, más 796 de las clases
de pago, fiscal y caja existentes; Jest 602/602 (64 suites), typecheck, lint 0 errores /
23 avisos, build. La suite completa corre en la CI del PR.

## 2026-10-04 — Infraestructura de producción: ensayo final

Rama `deploy/production-vps` con `master` `1b748b1` incorporado. No se ha publicado nada en
Internet.

**El ensayo es un guion.** `sh deploy/rehearsal.sh` construye la pila de producción sin
caché, arranca PostgreSQL vacío, la recorre a través de Caddy como un cliente y la desmonta.
Proyecto, puertos y dominio reservados; certificado interno; sin correo ni cobros. Resultado
sobre `9043c89` (con gunicorn 26.2.0 y DRF 3.18.1): `ENSAYO: OK` — 83 comprobaciones del guion y 26 pasos de navegador, 0 fallos.
La tabla completa está en `docs/despliegue-produccion.md` §9.

**Imágenes públicas y evidencias privadas.** Comparten volumen y almacén, no autorización:

- cinco PNG sin fondo subidos por la API (hero, categoría, servicio, ubicación, campaña) se
  sirven a cualquiera con su transparencia intacta y caché inmutable, y se ven en la portada
  a 320, 390, 768 y 1440 px en claro y en oscuro, con sombra por silueta y sin fondo detrás;
- una evidencia del servicio técnico, en el mismo volumen, no se entrega sin sesión ni a
  quien no trabaja en la empresa;
- las claves reales del almacén no se alcanzan por ninguna ruta de archivos: Caddy no tiene
  `file_server`;
- SVG, GIF, HTML con extensión de imagen, PNG truncado y archivos de más de 8 MB se rechazan.

**Persistencia.** Datos y archivos sobreviven a apagar y encender, a reconstruir las imágenes
y recrear los contenedores, y a borrar todos los archivos y restaurar la copia.

**Cambios en la rama.** PostgreSQL 16 en producción, el mismo que valida la CI (no hay datos
de producción que migrar). Limpieza diaria de imágenes sin uso en la guía (§6.1.2).
Dependabot vigila también las imágenes base de los `Dockerfile.prod`.

**Lo que el ensayo enseñó del propio ensayo.** `seed_demo_users` se niega en producción, así
que el guion crea su orden de servicio. El inicio de sesión se limita a 5 por minuto: un
arnés que inicia más sesiones que eso se falla a sí mismo.

Estado: **READY FOR EXTERNAL PRODUCTION CONFIGURATION**. Falta lo que sólo el propietario
puede dar: dominio y DNS, servidor, credenciales SMTP, credenciales de producción de Izipay,
host de las fotos de producto y destino de la copia externa.
## 2026-10-04 — Playwright completo y aislamiento de tres pruebas

La pasada completa tras subir Playwright a 1.63 y reconstruir el entorno local encontró tres
pruebas que dependían de algo ajeno a la propia pasada. Ninguna era un defecto del producto.

- `demo-accounts`: comparaba la tarjeta de accesos con un backend en una dirección escrita a
  mano (`127.0.0.1:8000`). Pasaba sólo mientras ese otro servidor estuviera encendido y
  sembrado igual. Ahora pregunta al backend de la pasada (`E2E_API_BASE`).
- `pos-ticket`: leía el cuerpo del PDF de la respuesta que la página consume como blob. Desde
  Playwright 1.63 ese cuerpo llega vacío al arnés aunque el navegador lo recibe entero
  (comprobado: 2628 bytes, `%PDF`). El PDF se pide otra vez con la misma sesión, y la respuesta
  de la página se comprueba por estado, tipo y tamaño.
- `skip-link`: iniciaba sesión sin respetar el límite de 5 por minuto y fallaba cuando las
  pruebas anteriores habían gastado la ventana. Espera y reintenta, como el resto.

La base de E2E se pierde con `/tmp`; `AGENTS.md` §6 dice ahora cómo se reconstruye. Una base
sembrada desde cero omite las 9 pruebas de `fiscal-invoice` (no tiene un pedido pagado con
factura), y la copia de la base de desarrollo apunta sus fotos a un host externo que
`next/image` rechaza. Una prueba omitida no cuenta como verde.

Validación sobre `master` `a46b5d3` más estas tres correcciones: **Playwright 171/171, 0
omitidas, 10,2 min.** Jest 590/590, typecheck y lint sin cambios.

Dependabot: mergeados #63 (`actions/checkout` 7) y #66 (DRF 3.18.1, gunicorn 26.2.0,
pillow-heif 1.8.0, urllib3 2.8.0; CI 4744 pruebas). #64 (`actions/setup-python` 7) tiene la CI
verde pero GitHub rechaza el merge con el token de esta sesión («sin permiso `workflow`»):
lo mergea el propietario. #67 (`reportlab` 5) queda abierta como DEP-REPORTLAB5: es la
librería de los PDF y pide comparar un ticket y un A4 a la vista.

## 2026-10-04 — Limpieza de imágenes de la tienda (STOREFRONT-IMAGE-CLEANUP)

Una imagen subida se quedaba para siempre en el almacén: reemplazar la del hero dejaba la
anterior, y también una subida que nunca se guardó.

- Al reemplazar o vaciar una imagen (portada, servicio, ubicación, categoría, campaña), la
  anterior se borra **sólo si ningún campo de ninguna empresa la sigue usando**. Las
  referencias son direcciones en campos de texto, así que se cuentan en todos los campos que
  pueden llevar una dirección (`storefront_media.reference_fields()` los calcula; hoy son 23).
- El recuento se hace con la fila de la imagen bloqueada, y colocar una imagen toma el mismo
  bloqueo: un borrado y una colocación no pueden cruzarse. El archivo se borra al confirmarse
  la transacción.
- Colocar una imagen que ya no existe, o que es de otra empresa, responde 400 con el mismo
  mensaje: no se confirma que exista la de otra tienda.
- `python manage.py cleanup_storefront_images [--older-than-hours 24] [--dry-run]` borra las
  subidas que nunca se colocaron. Cada borrado se audita bajo su empresa
  (`storefront_image_deleted`).
- De la revisión de #60: un PNG con transparencia por color clave (tRNS en RGB o en escala de
  grises) conserva su transparencia, y la ruta pública responde 404 para una empresa
  desactivada.

Sin migraciones nuevas. Validación (CI de #73, PostgreSQL 16): 4744 pruebas, 0 fallos,
3 omitidas, 2516 s.

Trabajo local: un reinicio del equipo vació `/tmp` con los worktrees dentro. No se perdió nada
confirmado. Los worktrees pasan a `~/Library/Caches/blackdog-worktrees/` (`AGENTS.md` §5).

## 2026-10-04 — Cierre previo al despliegue: menú, cabeceras y clasificación

**Menú del panel.** Dos pantallas tenían dos entradas cada una (`/admin/inventory/reports`
bajo Inventario y bajo Reportes; `/admin/settings` como «Empresa» y como «Configuración»,
con la misma capacidad), y `admin.areas` estaba definido dos veces, la segunda diciendo
«pantalla pendiente» de una pantalla que existe. Queda una entrada por pantalla y un
identificador por módulo, con prueba que lo exige.

**Cabeceras.** Auditoría de la política de contenido: no usa `unsafe-inline`,
`unsafe-eval` ni comodines; sólo limita quién puede enmarcar la página y a dónde apunta
`<base>`. Se añade `Permissions-Policy: camera=(), microphone=(), geolocation=()` (la
aplicación no usa ninguna de las tres). Una política de scripts queda como CSP-01.

**Clasificación.**

| ID | Estado | Motivo |
|---|---|---|
| AUDIT-01…06 | PROPUESTA | Ediciones de borradores sin fila de auditoría; todos los cierres se auditan. Detalle por función en `docs/AUDIT_MEMORY.md` |
| CSP-01 | PROPUESTA | Exige nonce por petición y la captura de red del SDK de Izipay |
| LINT-EFFECT-01 | PROPUESTA | 23 avisos; cambia el patrón de carga de 20 pantallas |
| INTERNAL-UI-KIT | PROPUESTA | Componentes del panel definidos en tres sitios |
| `uxui/phase-03-internal-ui` | OBSOLETO | 578 commits detrás; mergearla revertiría el panel y la portada |
| DEP-TS7, DEP-ESLINT10 | PROPUESTA | Las versiones mayores rompen la CI |

Dependabot: mergeados #65 (`setup-node`), #68 (menores y parches del frontend) y #70
(`jest-dom`); cerrados #69, #71 y #72 con su motivo.

Validación: Jest 590/590 (62 suites), typecheck OK, lint 0 errores/23 avisos.

## 2026-10-04 — Cabecera y pie V4

La cabecera y el pie ya salían enteros de la configuración de la tienda; no cambia qué
muestran. Se corrigen los defectos reales del armazón:

- **Categorías sin ratón.** Sólo se abrían al pasar el puntero. Ahora «Catálogo» es un
  enlace y a su lado hay un botón («Categorías del catálogo») con `aria-expanded` y
  `aria-controls`. Se abre con teclado, Escape lo cierra y devuelve el foco, y se cierra
  cuando el foco sale. Una tienda sin categorías no muestra el botón.
- **Móvil.** El carrito era un icono sin nombre accesible; ahora dice «Carrito» y cuántos
  artículos lleva. Escape cierra el menú y devuelve el foco. Carrito y menú miden 44 px.
- **Pie.** La banda de cierre ya no repite el logotipo que el pie pinta debajo; usa la
  tipografía de V4. Las columnas «Tienda» y «Servicios» son navegaciones con nombre. Las
  redes miden 44 px y sólo aparecen si la tienda las publicó.
- **Cierre de sesión.** Sigue siendo una carga completa (tira lo que la sesión tenía en
  memoria) y ya no deja la página anterior en el historial.

Dependencias: Next 16.3.8, React 19.3.0, Playwright 1.63.0 (#68, Dependabot).

Validación: Jest 587/587 (62 suites), typecheck OK, lint 0 errores/23 avisos (los 23 son
`react-hooks/set-state-in-effect`; ver LINT-EFFECT-01). Playwright de tienda y sesión:
112/112, con una prueba nueva que abre y cierra las categorías con el teclado.

**LINT-EFFECT-01 (P4, propuesta).** Los 23 avisos restantes señalan pantallas que cargan
datos con `setState` dentro de un efecto. No hay un defecto asociado; quitarlos exige
cambiar el patrón de carga de 20 pantallas del panel. No se silencian.

## 2026-10-04 — Profundidad de los recortes (revisión de #60)

La revisión de Storefront V4 no dejó hallazgos P0 ni P1. Corregido en el frontend:

- **Sombra sobre superficies oscuras (P2).** En el hero oscuro el estilo en línea
  pisaba el halo claro y el recorte quedaba sin profundidad; lo mismo en la promoción
  inferior y en todo el tema oscuro. El valor vive ahora en dos variables de CSS
  (`--cutout-shadow`, `--cutout-shadow-on-slab`); el tema oscuro apunta la primera a la
  segunda. `storefrontMediaStyle(src, "page" | "slab")` sólo dice sobre qué superficie
  está la imagen. La regla duplicada `.v3-cutout` desaparece.
- Las imágenes de servicio y de ubicación ya no pintan un azulejo detrás del recorte.
- La imagen propia de la portada es decorativa: no toma el título de una campaña sin imagen.
- El nombre accesible del campo de subida empieza por el texto visible del botón
  («Subir imagen: …», «Cambiar imagen: …») — WCAG 2.5.3.
- Los títulos de sección parten una palabra larga antes que desbordar.

Validación: Jest 575/575 (61 suites), typecheck OK, lint 0 errores/25 avisos. Playwright
de la tienda (imágenes V4, contraste, hero, ajuste de texto, tienda V3, enlace de salto):
116 pruebas OK; una falló en la pasada conjunta por el límite del carrito y pasó sola.

Las dos correcciones de backend de la misma revisión (transparencia por color clave,
imágenes de una empresa desactivada) van con la limpieza de imágenes en el PR #73.

## 2026-10-04 — Actualización de dependencias (CI-03)

`.github/dependabot.yml`: una vez por semana (lunes 06:00, Lima) Dependabot abre pull
requests para `pip` (`/backend`), `npm` (`/frontend`) y GitHub Actions. Las versiones
menores y de parche llegan agrupadas por ecosistema; las mayores, por separado. Django se
queda en su línea LTS 5.2 y Next/React en su versión mayor: moverlos es una migración
planificada. Nada se mergea solo: cada PR pasa la misma CI que cualquier cambio.

Pendiente del propietario en la configuración del repositorio: activar las alertas de
vulnerabilidad, el escaneo de secretos y la protección de push (hoy apagados).

## 2026-10-04 — Sistema de agentes

Se incorpora política persistente de agentes y eficiencia de contexto: `AGENTS.md`,
`docs/NEXT_ACTION.md`, `docs/AGENT_TOKEN_POLICY.md`, `docs/AGENT_BOOTSTRAP.md` y
`docs/CODEBASE_MAP.md`. Sin cambios de código. PR #60 (Storefront V4) ya está en
`master` por merge `e40e440`.

## 2026-10-03 — Portada V4: imágenes que la tienda sube desde su panel

Rama `feat/storefront-v4-images`, desde `master` `833fdec`. Código en `3fa9b42`.
Estado: **IMPLEMENTADO en esta rama**, pendiente de revisión y merge.

El propietario pidió la portada del diseño V4 con los huecos de imagen listos
para llenarlos desde el panel, y que un PNG sin fondo se vea sin fondo.

Qué hay ahora:

- **Subida de imágenes (`0e9db9d`).** `POST /api/admin/storefront/images/`
  (`company.manage`). Acepta PNG, JPEG y WebP; el tipo lo decide el
  decodificador, no el nombre del archivo. La imagen se vuelve a codificar desde
  sus píxeles, así que no sale ningún metadato, y se reduce si pasa de 2400 px.
  **PNG y WebP conservan la transparencia.** La dirección pública es
  `/api/storefront/images/<id>`, con un identificador aleatorio.
- **Huecos de imagen tenant-aware.** Hero, una imagen por categoría, una imagen
  opcional del bloque de servicio y una de «Cerca de ti». `0096` introduce la
  tubería de imágenes y `0097` añade los dos huecos editoriales sin reescribir la
  migración ya aplicada en entornos locales.
- **Panel (`57ed321` + seguimiento V4).** En «Escaparate» hay huecos de imagen
  para hero, campaña, categorías, servicio técnico y ubicación. La vista previa
  usa cuadrícula para comprobar transparencia. Toda imagen servida por la tubería
  propia del escaparate recibe una sombra `drop-shadow` suave; en PNG/WebP
  transparentes sigue el contorno del recorte en vez de dibujar una caja.
- **Portada V4 (`9c39716`).** El hero tiene dos estilos y lo elige la tienda; el
  oscuro sigue siendo el de quien no eligió. Las categorías son tarjetas con su
  imagen; una sin imagen muestra un hueco tranquilo, no una imagen rota. Con el
  hero claro sigue una franja de marca oscura con el isotipo. El bloque de
  servicio lista los servicios de la tienda. «Cerca de ti» sólo aparece con una
  dirección publicada.

Lo que no cambia: ninguna imagen, categoría ni texto del piloto está en el
código. Las imágenes de la propuesta de Figma siguen fuera del repositorio; la
tienda sube las suyas (STOREFRONT-IMAGES-LICENSE sigue BLOQUEADO para subirlas
al repositorio, y ya no hace falta que estén en él).

Decisiones que esto cambia:

- STOREFRONT-HERO-VARIANT = IMPLEMENTADO. Sustituye a «el hero es una losa
  oscura para todas las tiendas»: la losa es el valor por defecto, no la única.
  Las pruebas que protegen la losa siguen midiéndola tal cual.
- STOREFRONT-EDITORIAL-CMS = PARCIAL. Hay imagen por categoría e imagen de hero;
  no hay más material editorial.

Dónde se guardan: en el mismo almacenamiento que las evidencias, bajo
`companies/<id>/storefront/`, y se sirven por la API. En el despliegue preparado
eso es el volumen `evidence`, que ya entra en la copia de seguridad.

Encontrado y corregido por el camino:

- **IMPORT-UPLOAD-415 (`b5de287`).** Importar productos o stock desde el panel
  respondía 415: el archivo salía con una cabecera `Content-Type` vacía. Ya estaba
  roto en `master`. Confirmado en navegador antes y después.
- **`backend/private-media/` no estaba en `.gitignore` (`3fa9b42`).** Ahí viven
  las evidencias de servicio. Un `git add -A` en una copia usada podía publicarlas.
  Nunca llegó a ocurrir: no hay ningún archivo de esa carpeta en el historial.

Pruebas nuevas: `test_storefront_media` (26, backend), `image-upload-field` (7),
`category-images-editor` (3), `storefront-v4-home` (12), tres casos en
`fetch-with-auth`, y `e2e/storefront-v4-images.spec.ts`, que sube un recorte en el
panel con un navegador real y comprueba en la portada pública que sus esquinas
siguen transparentes y que nada pinta un fondo detrás.

Validación sobre `3fa9b42`: backend, 26 pruebas propias y 161 vecinas en
PostgreSQL, `makemigrations --check` sin cambios; la suite completa la da el CI.
Frontend 563 pruebas en 60 suites, OK; typecheck OK; lint 0 errores y 25
advertencias; build OK (52 páginas); Playwright 170 de 170, sin fallos, omitidas ni reintentos, 10,5 min.

Una pasada anterior de Playwright no cuenta: el equipo se suspendió veinte
minutos a mitad de la ejecución y dos pruebas agotaron su tiempo. Se repitió
entera.

Deuda nueva:

- STOREFRONT-IMAGE-CLEANUP. Una imagen subida y luego sustituida queda guardada.
  No hay listado ni borrado.
- La cabecera y el pie siguen con el diseño anterior; el diseño V4 los simplifica.
## 2026-10-03 — DEPLOY-PREP: la infraestructura de producción, sobre el master actual

Rama `deploy/production-vps`, con `master` `833fdec` incorporado. Estado:
**PARCIAL** hasta que el PR esté integrado; nada desplegado.

Qué añade: `backend/Dockerfile.prod` y `frontend/Dockerfile.prod`,
`docker-compose.prod.yml`, `deploy/Caddyfile`, `deploy/.env.production.example`,
los guiones de copia y restauración, y la guía
[docs/despliegue-produccion.md](docs/despliegue-produccion.md). No cambia código de
la aplicación ni migraciones.

Topología: internet → Caddy (único que publica puertos, HTTPS automático) →
`/api/*` a Django con gunicorn, lo demás a Next → PostgreSQL en la red privada.
Un solo proxy delante de Django: Caddy sobrescribe las cabeceras de dirección del
cliente y Django la lee con `TRUSTED_PROXY_COUNT=1`.

Decisiones que quedan fijadas:

- **Un proceso de Django, ocho hilos.** Los límites de peticiones se cuentan en la
  memoria del proceso y así son exactos. THROTTLE-CACHE-01 queda controlado, no
  cerrado: subir procesos exige antes una caché compartida, y la guía lo dice.
- **TOKEN-HYGIENE-01 resuelto en la guía.** `flushexpiredtokens` una vez al día
  desde el `crontab` del servidor, en el contenedor `backend`, con cómo comprobarlo.
- **Las migraciones no se aplican al arrancar.** Son un paso explícito de cada
  despliegue.
- **Almacenamiento.** Fotos de producto, imagen de campaña y logotipos son URL
  públicas que la tienda carga donde elija. Las evidencias del servicio técnico
  son lo único que guarda la aplicación y nunca tienen URL pública.

Ajustes hechos al traer `master`: Caddy ya no pisa la política de referente que
envía la aplicación; el frontend tiene comprobación de salud y Caddy espera a que
las dos aplicaciones respondan; los guiones de copia se pueden ensayar con otro
nombre de proyecto.

Ensayo completo sobre `f0c29ce` (2026-10-03): imágenes sin caché, PostgreSQL
vacío, 31 comprobaciones, con navegador real a través de Caddy. Todas pasan; el
detalle está en la sección 9 de la guía. Un primer ensayo dio tres fallos en
comprobaciones hechas con `curl`; eran comillas mal anidadas en el guion de
ensayo, no la aplicación. Se corrigió el guion y se repitió entero.

Escaneo de los commits a publicar: sin secretos, sin datos, sin copias de
seguridad. El archivo de ejemplo lleva todos los secretos vacíos.

El respaldo de los datos locales sigue fuera del repositorio y sin tocar
(integridad correcta, sin relaciones rotas; sus ocho usuarios son de
demostración y no pasan a producción).

Pendiente de datos del propietario, sin los cuales no se publica:

| Dato | Estado |
|---|---|
| Dominio y DNS, certificado público | PENDIENTE DOMINIO/DNS |
| Servidor (VPS) | PENDIENTE CONTRATACIÓN |
| SMTP | PENDIENTE CREDENCIALES |
| Izipay producción | PENDIENTE CREDENCIALES (queda en `sandbox`) |
| Host de las fotos de producto | PENDIENTE DECISIÓN |
| Destino de la copia externa | PENDIENTE DECISIÓN |
| Imágenes del trabajo paralelo | BLOQUEADO: LICENCIA |

Deuda nueva: LOGIN-CSRF-01 (baja). El inicio de sesión no rechaza por origen;
las operaciones con sesión sí.

## 2026-10-02 — FE-AUTH-06 y enlace «Saltar al contenido»

Rama `fix/frontend-proxy-redirects-skip-link`. Código en `085aa2e` y `cf72ee5`,
juntos en `abfda62`. Estado: **IMPLEMENTADO / MERGED** por PR #57 (`833fdec`). Sin backend, sin
migraciones.

- **FE-AUTH-06 — el proxy `/api` sólo sigue las redirecciones del propio backend
  (`085aa2e`).** Seguía en el servidor cualquier redirección reenviando las
  cabeceras del visitante. Hay una que sale del sitio: con las evidencias en un
  almacenamiento externo, el backend responde 302 hacia una URL firmada de ese
  proveedor, y el proxy le entregaba el `X-CSRFToken` del visitante. Ahora una
  redirección hacia otro origen vuelve al navegador, que la sigue solo. Dentro
  del backend se sigue, con la sesión, sólo bajo `/api/` y como mucho tres saltos.
- **Enlace «Saltar al contenido» (`cf72ee5`).** Quien navega con teclado o lector
  de pantalla recorría la cabecera entera en cada página. El enlace existió y se
  perdió al reconciliar la interfaz; el panel conservaba el destino sin nadie que
  apuntara a él. Es el primer tabulador, invisible hasta recibir el foco, y mueve
  el foco al contenido, en la tienda y en el panel.

Pruebas: `api-proxy-redirects.test.ts` (8; 6 fallan sobre `master`),
`skip-link.test.tsx` (3; 2 fallan sobre `master`) y `e2e/skip-link.spec.ts` (5,
con teclado real).

Validación sobre `abfda62`: frontend 538 pruebas en 57 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 169 de 169,
sin fallos, omitidas ni reintentos, 9,1 min, sin suspensiones del equipo. El
árbol de `frontend/` de esta rama no cambió al incorporar `master`.

## 2026-10-02 — AUDIT-07 y DEP-05: auditoría del catálogo y versiones de parche

Rama `fix/audit-log-company`, sobre `security/backend-hardening`. Estado:
**IMPLEMENTADO / MERGED** por PR #56 (`f7fd6e6`). CI de backend sobre una
instalación limpia con las versiones nuevas: 4686 pruebas, 0 fallos, 3 omitidas.
Sin migraciones.

- **AUDIT-07 (`06cd798`).** El registro de auditoría de una empresa sólo enseña
  las filas que llevan esa empresa. Cambiar un producto (precio, nombre,
  publicación) y crear una categoría escribían la fila sin empresa: existía, pero
  ningún administrador del tenant podía verla. Ahora la llevan. El cambio de rol
  global sigue sin empresa a propósito: es una operación de plataforma. Las filas
  antiguas de catálogo sin empresa no se rellenan; en una base nueva no existen.
- **DEP-05 (`65d34ad`).** Cinco dependencias del backend pasan al último parche
  de su línea: djangorestframework 3.17.2, psycopg2-binary 2.9.13, boto3
  1.43.107, reportlab 4.4.10 y cryptography 50.0.2.

Pruebas: `test_audit_company` (4; 3 fallan antes del cambio). La suite completa
sobre una instalación limpia con las versiones nuevas la da el CI de backend de
este PR.

AUDIT-01…06 = PROPUESTA. Son ediciones de borrador sin fila de auditoría: líneas
de presupuesto, líneas de transferencia, cantidades de un recuento y audiencia de
un comunicado. Sus cierres (publicar, despachar, aprobar) sí se auditan y el
resultado queda en el Kardex o en el documento. Falta decidir si cada edición de
un borrador merece su propia fila.

## 2026-10-02 — Endurecimiento del backend: sesión, configuración, transferencias y admin

Rama `security/backend-hardening`, desde `master` `dbe30b2`. Estado:
**IMPLEMENTADO / MERGED** por PR #55 (`bd6dbc2`). CI de backend: 4682 pruebas, 0
fallos, 3 omitidas. Sin migraciones. Sin cambios en contratos de la API salvo los que
se dicen.

- **SEC-SET-04-A — renovar la sesión tiene límite (`e066183`).** Las dos rutas de
  renovación (cookie para la web, cuerpo para la app) no tenían limitador, y cada
  renovación válida escribe una fila de token. Ahora comparten un cupo de 30 por
  minuto por dirección. Sólo cuentan las peticiones que traen un refresh: la web
  intenta renovar en cada 401, también para visitantes sin sesión, y esas
  peticiones no gastan el cupo de quien sí la tiene.
- **AUTH-LOGGING-01 — cada intento de inicio de sesión deja registro
  (`7d5efc8`).** Ningún canal lo hacía. Fallos a nivel de aviso con canal,
  dirección y el nombre escrito; aciertos con el identificador del usuario. La
  contraseña no se registra nunca. Son líneas de registro, no filas: un anónimo no
  puede llenar una tabla.
- **Configuración que falla cerrada (`e2dff73`).** `JWT_COOKIE_SAMESITE` sólo
  admite `Lax` o `Strict` (SEC-SET-03). Producción responde sólo JSON; la API
  navegable queda en desarrollo (SEC-SET-09). Producción no arranca con
  `FRONTEND_URL` o `CHECKOUT_RETURN_URL` ausentes o en `localhost` (SEC-SET-10,
  ENV-02). Hay configuración de registro: antes no había ninguna y los eventos de
  seguridad de Django se descartaban en producción (SEC-SET-06).
- **SEC-SET-05 — la importación de Excel aplica su límite antes de leer
  (`e2dff73`).** Leía el archivo entero y después comparaba con 10 MB.
- **ENV-04 — `.env.example` describe todas las variables que lee el backend
  (`a6beda6`).** Faltaban 27.
- **INV-LEGACY-V1-F2 — una línea no se edita sobre una transferencia ya despachada
  (`1f965f7`).** Se decidía si era borrador mirando el objeto en la mano, sin
  bloquear la fila; un despacho simultáneo dejaba la línea escrita sobre un
  documento cuyas unidades ya habían salido. Ahora se bloquea y se relee, con el
  mismo bloqueo que toma el despacho.
- **INV-LEGACY-V1-F3 — `page_size` negativo ya no responde 500 (`1f965f7`).**
- **SEC-SET-02 — el admin de Django no se registra en producción (`79d1077`,
  `b0d2434`).** Trabajo de la otra sesión, incorporado aquí. En producción no
  existe la ruta `/admin/` del backend; en desarrollo sigue disponible.

Cambios que ve quien despliega: producción exige `FRONTEND_URL` y
`CHECKOUT_RETURN_URL` reales, y el admin de Django deja de existir allí. El primer
administrador se crea con `createsuperuser`, como ya decía la guía de despliegue.

Pruebas nuevas: `test_refresh_throttle` (8), `test_security_log` (6),
`test_production_settings` (14; cada ajuste se comprueba arrancando un proceso
nuevo con ese entorno), `test_transfer_line_lock` (8) y
`test_production_url_surface` (3). Todas se vieron fallar antes del cambio.

Validación local: las 55 pruebas del área, OK en PostgreSQL; `check` y
`makemigrations --check` sin cambios. La suite completa se midió por separado
sobre el límite de renovación (`e066183`: 4651 pruebas, 0 fallos, 3 omitidas) y
sobre el cambio del admin (`7f0f459`: 4646, 0 fallos, 3 omitidas); la del conjunto
la da el CI de backend de este PR. Con el backend del límite de renovación,
Playwright completo: 164 de 164.

Queda abierto, con el motivo:

- **F-TENANT-01 = CORREGIDO en rama, pendiente de CI/merge.** El alta directa
  por `POST /api/admin/memberships/` queda reservada al administrador de
  plataforma. Un administrador de empresa incorpora personal mediante
  `StaffInvitation` + aceptación, que ya es el flujo de la interfaz. Así no
  puede vincular por id a una cuenta global que nunca aceptó entrar al tenant.
- **THROTTLE-CACHE-01 = PENDIENTE.** Los límites se cuentan en la memoria de cada
  proceso. Con un proceso (lo que usa el despliegue preparado) son exactos; con
  varios, cada uno cuenta por su lado. Compartirlos necesita una caché común.
- **TOKEN-HYGIENE-01 = PENDIENTE.** Las filas de token caducadas no se purgan
  solas; falta programar `flushexpiredtokens` en el servidor.

## 2026-10-02 — F-TENANT-01: alta de personal con consentimiento

Rama `security/f-tenant-01-consent-based-membership`, desde `master`
`833fdec`. Estado: **PARCIAL / pendiente de CI y merge**.

El endpoint de bajo nivel `POST /api/admin/memberships/` deja de aceptar altas
directas hechas por administradores de empresa. Se conserva para el administrador
de plataforma como herramienta de bootstrap/migración. El flujo normal de empresa
es la invitación existente: la persona recibe la invitación y su aceptación crea
la membresía.

No hay migraciones. GET/PATCH de membresías siguen tenant-scoped; no se cambia
RBAC, branch scope ni la interfaz de Personal.

Cobertura nueva: `test_membership_consent.py` comprueba que un admin de empresa
recibe 403 sin crear la membresía ni obtener el username y que un platform admin
conserva el alta directa.

## 2026-10-02 — Backend CI sobre PostgreSQL

Rama `ci/backend-postgres-validation`, desde `master` `1815ac1`.
Estado: **IMPLEMENTADO / MERGED** por PR #49 (`dbe30b2`). La tercera ejecución, sobre
`d195632`, terminó en verde. La primera ejecución se canceló al
llegar a su límite de 35 minutos con la suite aún corriendo, sin ninguna prueba
fallida; el límite pasa a 120 minutos (`0a17afd`).

La segunda ejecución terminó en 53 minutos: 4643 pruebas, 1 fallo y 16 errores.
Los dos eran defectos reales que las máquinas de desarrollo escondían:

- **`qrcode` no estaba en `requirements.txt`.** Lo importa `store/fiscal/qr.py`
  para el código QR del comprobante electrónico. Estaba instalado en las máquinas
  de desarrollo, así que las pruebas pasaban allí; en una instalación limpia —el
  runner de CI y la imagen de producción— todo PDF fiscal fallaba con
  `ModuleNotFoundError` (16 errores). Se declara `qrcode==8.2`. Se revisaron los
  demás paquetes que importa el backend: no falta ninguno más.
- **Una prueba dependía de la colación de la base.** `test_ordering_name_asc`
  esperaba «Mac Studio» antes de «MacBook Air», que es el orden por bytes de la
  base local (colación `C`). La imagen de PostgreSQL usa una colación lingüística
  (`en_US`), que ignora el espacio y pone «MacBook» primero. Los dos órdenes son
  alfabéticos. La prueba compara ahora contra el orden por nombre de la propia
  base y fija el par en el que todas las colaciones coinciden. El catálogo en
  producción ordena por nombre con la colación de su base.

Se añade una compuerta reproducible para cualquier cambio de backend:
PostgreSQL 16, Python 3.12, instalación desde `backend/requirements.txt`,
`manage.py check`, `makemigrations --check --dry-run` y la suite completa
`manage.py test`. La suite corre en un solo proceso, sobre PostgreSQL, para que
los cambios de seguridad y persistencia no dependan de una base SQLite local.

No cambia código de aplicación, modelos, migraciones, auth, RBAC ni contratos API.

## 2026-10-02 — Endurecimiento del frontend: FE-AUTH-02, FE-AUTH-04, FE-AUTH-07

Rama `security/frontend-hardening`, desde `master` `3f70ca0`. Código en `113a8dd`.
Estado: **CORREGIDO** en esta rama. Sin backend, sin migraciones. Eran hallazgos
«sin veredicto» de la auditoría F1; se verificaron en el código antes de tocarlos.

- **FE-AUTH-02 — clave del carrito anónimo (`5adcd28`).** Esa clave es lo único
  que elige un carrito de invitado en el servidor. Se fabricaba con la hora y seis
  caracteres de `Math.random()`. Las claves nuevas salen de `crypto.randomUUID`
  (o de `crypto.getRandomValues` donde falta). Quien ya tenía una clave la
  conserva, así que nadie pierde su carrito.
- **FE-AUTH-07 — la sesión sólo viaja a la API propia (`40f9769`).**
  `fetchWithAuth` añadía cookies y token CSRF a cualquier URL que le pasaran.
  Ningún llamador le pasaba una ajena (revisados los 105), pero era una costumbre
  de los llamadores, no una propiedad de la función. Ahora rechaza antes de la red
  cualquier URL que no esté bajo `API_BASE`.
- **FE-AUTH-04 — cabeceras de seguridad (`113a8dd`).** El frontend no enviaba
  ninguna. Todas las rutas envían ahora `X-Frame-Options: DENY`, una política de
  contenido estrecha (`frame-ancestors 'none'; base-uri 'self'`), `nosniff` y
  política de referente. Las tres páginas que reciben un token de un solo uso en
  la URL (verificar correo, restablecer contraseña, aceptar invitación) envían
  `no-referrer`. No hay política de scripts ni de estilos: exigiría un nonce en
  cada script que emite Next y es un cambio aparte (CSP-SCRIPT = PROPUESTA).
  Tampoco `object-src`: el ticket de caja se imprime como PDF en un marco y esa
  directiva puede bloquear el visor.

Revisados y sin cambio: FE-AUTH-08 (la tarjeta de cuentas de demostración sólo se
pinta en desarrollo y el servidor responde 404 fuera de él: aceptado) y FE-AUTH-09
(la configuración de la tienda se pide desde el servidor sin reenviar el host;
con una sola tienda por despliegue resuelve por `DEFAULT_STOREFRONT_COMPANY_SLUG`;
para varias tiendas por dominio es una decisión de arquitectura: PROPUESTA).
FE-AUTH-06 (el proxy sigue redirecciones reenviando cabeceras) queda PENDIENTE.

Pruebas: `cart-session-key.test.ts` (4; 3 fallan sobre `master`),
`fetch-with-auth.test.ts` (6 casos nuevos; 5 fallan sobre `master`) y
`security-headers.test.ts` (6; los 6 fallan sobre `master`). Cabeceras
comprobadas contra un servidor real.

Validación sobre `113a8dd`: frontend 527 pruebas en 55 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 10,2 min. La impresión del ticket de caja
sigue pasando con las cabeceras nuevas.

## 2026-10-02 — DRIFT-02: el ajuste de inventario dice en qué sucursal se aplica

Rama `fix/inventory-adjust-branch`. Código en `068bee1`, con `master` `a6d725b`
incorporado en `1fa05af`. Estado: **IMPLEMENTADO / MERGED** por PR #53 (`3f70ca0`). Sin backend, sin
migraciones.

El servidor acepta `branch` en el ajuste de inventario y, si no llega, lo aplica a
la sucursal por defecto de quien ajusta. La pantalla nunca lo enviaba: en una
empresa con varias sucursales todos los ajustes caían en la sucursal por defecto,
sin decirlo y sin poder elegir otra.

Ahora, con más de una sucursal al alcance, el formulario pregunta, parte de la
sucursal por defecto y envía la elegida. Si no hay sucursal por defecto, pide
escogerla antes de enviar. Con una sola sucursal —el caso del piloto— no pregunta
nada y la petición es la de siempre. El servidor sigue decidiendo si quien ajusta
alcanza esa sucursal.

Pruebas: `inventory-adjust-branch.test.tsx` (5 casos; 3 fallan sobre `master`).
Contrato del servidor comprobado sobre una copia desechable de la base con dos
sucursales: con `branch` el movimiento queda en esa sucursal; sin `branch`, en la
de por defecto; con una sucursal inexistente responde 404.

Validación sobre `1fa05af`: frontend 511 pruebas en 53 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos.

## 2026-10-02 — RBAC-F3 y RBAC-F4: el panel no ofrece lo que el servidor niega

Rama `fix/staff-actions-capability`, desde `master` `64b4d5e`. Código en
`4f5d736`. Estado: **IMPLEMENTADO / MERGED** por PR #52 (`a6d725b`). Sin backend, sin migraciones. La
autoridad sigue en el servidor; cambia sólo qué enseña la interfaz.

- **Personal (RBAC-F4, `58d17de`).** Ver al personal pide `memberships.view`;
  invitar, desactivar el acceso y reenviar o revocar una invitación piden
  `memberships.manage`. La pantalla pintaba todos los botones a quien pudiera
  entrar, y a quien sólo podía ver cada clic le devolvía un 403. Ahora los botones
  siguen la capacidad que comprueba el servidor.
- **Menú del panel (RBAC-F3, `847d3bf`).** Cada página decide con las capacidades
  cuando hay empresa, y con el rol antiguo sólo para el operador sin empresa. El
  menú seguía otra regla: si las capacidades no alcanzaban, caía al rol antiguo y
  listaba módulos cuya página respondía «sin permiso». Ahora usa la misma regla
  que las páginas. Auditoría declara `memberships.view`, que es lo que piden su
  página y el servidor; antes sólo declaraba rol antiguo y quien tenía la
  capacidad no la veía en el menú.
- **Código sin uso (`4f5d736`).** Se elimina `BranchAccessPanel.tsx` (334 líneas):
  ninguna pantalla lo monta y nada lo importa. Conservaba la oferta de «Todas».

Revisados y ya corregidos en `master` por trabajo posterior a la auditoría F1, sin
cambio aquí: RBAC-F6 (detalle de transferencia: las acciones piden
`inventory.adjust`), RBAC-F7 (accesos del personal: `memberships.manage`) y
RBAC-F11 (detalle de pedido: reenviar correo, nota de venta y comprobante piden
su capacidad). RBAC-F5 era el mismo defecto del menú que RBAC-F3.

Pruebas: `staff-screen-authority.test.tsx` (3 casos; el de sólo lectura falla
sobre `master`) e `internal-modules-access.test.ts` (9 casos; 6 fallan sobre
`master`). En navegador, con las cuentas de ventas, inventario y técnico: cada
entrada del menú abre su página, ninguna responde «sin permiso».

Validación sobre `4f5d736`: frontend 506 pruebas en 52 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 9,0 min.

Deuda menor observada: el menú lista dos entradas hacia `/admin/settings`
(«Empresa» y «Configuración») y dos hacia `/admin/inventory/reports`.

## 2026-10-02 — ADMIN-MENU-ARIA: el botón del menú móvil del panel dice qué abre

Rama `fix/admin-menu-trigger-aria`. Código en `e2ce0e2`, con `master` `2d9cc97`
incorporado en `02b8ba4`. Estado: **IMPLEMENTADO / MERGED** por PR #51 (`64b4d5e`). Sin backend, sin
migraciones, sin cambios de permisos.

El cajón de navegación del panel en un teléfono ya era un diálogo con el foco
atrapado. El botón que lo abre no llevaba estado: un lector de pantalla anunciaba
un botón sin más y nada cambiaba al pulsarlo. Ahora lleva
`aria-haspopup="dialog"` y `aria-expanded`, y mientras el cajón está abierto
apunta a él con `aria-controls`.

Pruebas: `__tests__/admin-menu-trigger-a11y.test.tsx` (3 casos, los 3 fallan
sobre `master`). La prueba del selector de empresa buscaba «el botón plegado»;
ahora hay dos y elige el que abre una lista. Comprobado en navegador a 375 px:
plegado, abierto con 32 enlaces, y Escape devuelve el foco al botón.

Validación sobre `02b8ba4`: frontend 494 pruebas en 50 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 9,3 min.

## 2026-10-02 — FE-AUTH-05: el proxy `/api` pone tope al cuerpo que acepta

Rama `fix/api-proxy-body-limit`, desde `master` `1815ac1`. Código en `4fad36b`.
Estado: **IMPLEMENTADO / MERGED** por PR #50 (`2d9cc97`). Sin backend, sin migraciones, sin cambios de
autenticación, permisos ni contratos de la API.

El proxy de Next (`frontend/app/api/[...path]/route.ts`) leía entero en memoria el
cuerpo de cada petición antes de reenviarlo, sin límite. Cualquiera, con sesión o
sin ella, podía enviar un cuerpo de cualquier tamaño a cualquier ruta bajo `/api/`
y el proceso de Next lo retenía completo. Los topes del backend se aplicaban
después.

Ahora el cuerpo se lee a trozos hasta un tope y se rechaza con 413 en cuanto lo
supera, sin llamar al backend. `Content-Length` sólo sirve para rechazar antes;
deciden los bytes leídos, así que un tamaño declarado falso o un envío a trozos no
lo evitan. El tope es 32 MiB: por encima de la subida más grande que acepta el
backend (una foto de evidencia de servicio, 25 MB, más su envoltorio). Se cambia
con `API_PROXY_MAX_BODY_BYTES`.

Alcance: en la topología de producción aprobada Caddy envía `/api/*` directo a
Django, así que este proxy no atiende al navegador allí. Sí lo atiende en
desarrollo y en cualquier despliegue que publique Next sin un proxy delante.

Pruebas: `__tests__/api-proxy-body-limit.test.ts` (7 casos; 4 fallan sobre
`master`). Comprobado además contra un servidor real: 34 MB declarados y 34 MB a
trozos responden 413; 1 MB y un inicio de sesión llegan al backend.

Validación sobre `4fad36b`: frontend 491 pruebas en 49 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 164 de 164,
sin fallos, omitidas ni reintentos, 9,0 min.

Dos pasadas anteriores de Playwright sobre el mismo commit no fueron limpias, por
el entorno de pruebas y no por el cambio. En la primera, `fiscal-invoice` no pudo
iniciar sesión: una prueba manual mía acababa de gastar el límite de inicios de
sesión. En la segunda se omitieron 9 casos de `tax-breakdown`: la base de pruebas
se había quedado sin ningún producto con 4 unidades tras varias pasadas seguidas.
Se repuso el stock y la tercera pasada fue completa.

Además, sobre `master` `1815ac1`:

- Playwright completo en local: 164 de 164.
- Barrido del panel en un teléfono: 37 rutas de `/admin` (33 fijas y 4 de
  detalle) en 320, 360, 375, 390, 414 y 768 px, con sesión de administrador.
  Ninguna desborda la página y ningún texto queda cortado fuera de un contenedor
  desplazable. El menú móvil del panel ya existe en `master` (diálogo, foco
  atrapado, Escape, foco devuelto).

## 2026-10-02 — ADMIN-INVENTORY-MOBILE-OVERFLOW

Rama `fix/admin-inventory-mobile-overflow`, desde `master` `03581ea`.
Estado: **CORREGIDO** en esta fase; sin backend, migraciones, auth, RBAC ni contratos API.

Causa: los paneles del dashboard de inventario son ítems de grid. `TableWrap` ya
encapsulaba las tablas de mínimo 640 px con `overflow-x-auto`, pero el `Panel` y
su cuerpo conservaban `min-width:auto`; por el cálculo de min-content de CSS Grid,
la tabla podía imponer su ancho al panel completo y ensanchar `/admin/inventory`
en pantallas móviles.

Corrección: `InventoryUi.Panel` y su cuerpo declaran `min-w-0`. La tabla mantiene
su ancho y se desplaza únicamente dentro de `TableWrap`; no se cambia contenido,
permisos ni comportamiento de inventario.

Cobertura: `inventory-mobile-layout.test.tsx` protege el contrato estructural y
`admin-inventory-mobile.spec.ts` mide que la página no desborde en 320, 360, 375,
390 y 414 px con sesión interna real.

## 2026-10-02 — HERO-MOBILE-CLIP y cierre del frontend V3

Rama `fix/hero-mobile-clip`, sobre `master` `c47c538`. Código en `42ef631`.
Estado: **IMPLEMENTADO / MERGED** por PR #47 (`03581ea`). Sin backend, sin
migraciones, sin cambios en el panel interno.

Qué se corrigió:

- **Hero (`ff564e9`).** Hasta 414 px el titular y el párrafo se cortaban por la
  derecha. La columna de texto no podía encogerse por debajo de su palabra más
  larga y la sección escondía lo que sobraba. Ahora la columna puede encogerse,
  el titular escala con el ancho de la pantalla hasta su tamaño de siempre, y el
  marco y la etiqueta superior ocupan menos en pantallas estrechas. No hay ningún
  valor escrito para un ancho concreto. De 640 px en adelante el hero sale
  idéntico píxel a píxel al de `master`, en tema claro y oscuro (medido en 640,
  768, 1024 y 1440 px). La losa oscura, los temas y el logotipo no cambian.
- **Titular del catálogo y «Productos relacionados» (`e082b26`).** Al barrer el
  resto de la tienda con la misma medición aparecieron dos titulares con el mismo
  defecto: el de `/product` entre 320 y 390 px y el de la ficha a 320 px. Ambos
  escalan ahora con el ancho. A 640 y 1440 px salen idénticos a `master`.

Pruebas nuevas, las dos de navegador:

- `e2e/hero-mobile-clip.spec.ts` mide cada trozo de texto del hero contra el área
  visible en 320, 360, 375, 390 y 414 px, en tema claro y oscuro; comprueba que
  ninguna palabra del titular se parte, que la letra no baja de 20 px, que la losa
  sigue oscura y que en escritorio el tamaño del titular es el de antes. Sobre
  `master` fallan 10 de sus 12 casos.
- `e2e/storefront-text-fit.spec.ts` aplica la misma medición a `/`, `/product`,
  una ficha con relacionados, `/services`, `/about`, `/contact`, `/cart` y `/auth`
  en los cinco anchos. Cada ruta se carga una sola vez y se redimensiona
  (`42ef631`): cargarla una vez por ancho agotaba el límite de peticiones del
  carrito y hacía fallar la prueba siguiente.

La prueba de desbordamiento que ya existía compara el ancho de la página con el
de la pantalla. No ve un texto cortado dentro de una sección con
`overflow-hidden`; por eso el defecto pasó. Las pruebas nuevas miden el texto.

Validación sobre `42ef631`: frontend 483 pruebas en 47 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 163 de 163,
sin fallos, omitidas ni reintentos, 10,1 min. `backend/` es idéntico al de
`master`; vale su medición de 4643 pruebas, 0 fallos, 3 omitidas.

Una pasada anterior, sobre `e082b26`, dio 194 de 195: falló «una línea del
carrito vuelve a la ficha de su producto» con un 429 del límite del carrito,
provocado por la primera versión de la prueba nueva. No era un defecto de la
tienda; se corrigió la prueba y se repitió la pasada completa.

### Qué queda del diseño V3

Se comparó `master`, el trabajo paralelo sin integrar y el manual de marca.

Ya está en `master` y no se rehízo: categorías reales en cabecera, pie y portada;
navegación móvil; pie con datos de la tienda; hero con campaña opcional; carrusel;
`/about` y `/contact`; temas; línea del carrito enlazada; separación entre tienda
y panel; movimiento reducido.

- **Perrito decorativo = IMPLEMENTADO como marca de la tienda.** Es el isotipo que
  cada empresa sube en su configuración (`logo_isotype_on_*_url`). Aparece como
  marca de agua del hero, en el bloque de promoción, en el acceso y en el menú del
  panel. Otra empresa ve el suyo. En móvil la marca de agua del hero no se
  muestra: PROPUESTA, no defecto.
- **STOREFRONT-IMAGES-LICENSE = PENDIENTE.** El trabajo paralelo trae diez
  ilustraciones (`assets/editorial/`: hero, iphone, mac, ipad y accesorios, cada
  una con y sin fondo) y cuatro fotos de producto (`assets/products/`) con su
  manifiesto. Según su propia nota, las ilustraciones salen de una propuesta de
  Figma con el fondo quitado y muestran productos Apple; las fotos son copias de
  imágenes del sitio de Apple. No hay evidencia de origen ni de licencia de
  ninguna. No se integran. Los originales no se borraron. Para integrarlas hace
  falta, por cada imagen: quién la hizo, de dónde salió y un permiso de uso
  comercial por escrito.
- **TENANT-TYPOGRAPHY = PROPUESTA.** Sin cambio.
- **STOREFRONT-PILLARS-CMS = PROPUESTA.** La portada funciona con los pilares
  compilados; no es imprescindible.
- **INTERNAL-UI-V3 = PENDIENTE.** El trabajo paralelo cambia sólo piezas
  compartidas del panel, ninguna página: el armazón (`AdminShell`), el menú
  lateral con diálogo móvil, la barra superior, los selectores de empresa y
  sucursal, la campana, los gráficos y los estilos globales `.admin-workspace`.
  Por eso alcanza a todas las rutas a la vez (`/admin`, productos, inventario,
  ventas, caja, clientes, servicio y configuración) y no se puede portar una ruta
  sin las demás. Seis de los nueve archivos chocan con `master`, que ya cambió
  esas piezas en SVC-FUNC-01 y UX-RECON-SVC-01. No hay un port pequeño y seguro;
  necesita fase propia con sus pruebas.

Deuda nueva:

- **ADMIN-INVENTORY-MOBILE-OVERFLOW.** `/admin/inventory` desborda la página entre
  284 y 378 px en pantallas de hasta 414 px. Ya ocurre en `master`. No se tocó: es
  del panel.
- **HERO-WATERMARK-MOBILE = PROPUESTA.** Mostrar el isotipo del hero también en
  móvil.

## 2026-10-01 — STOREFRONT-V3: la tienda pública converge sobre el master auditado

Rama `reconcile/storefront-v3-after-ux`, sobre `master` `0c83381`. Código en
`7f49168`. Estado: **PARCIAL** — se portó lo aprobado del trabajo paralelo «V3»;
el contenido editorial por tienda, las imágenes de producto y el rediseño del panel
quedan fuera a propósito.

`master` decide comportamiento, autenticación, multiempresa, comercio y temas. Del
trabajo paralelo se tomó, archivo por archivo, sólo presentación. El PR #38 no se
usó.

Qué cambia para quien visita una tienda:

- **Categorías reales.** La cabecera, el pie y la portada tenían cada uno una lista
  de categorías escrita a mano para el piloto. Ahora las tres leen el catálogo de
  la tienda (`useCatalogCategories`). El pie enlazaba con `?cat=`, que el catálogo
  no lee: no filtraba nada. Usa `?category=`.
- **Portada.** Productos en un carrusel (flechas, teclado, avance lento que cede
  al tocar, quieto con «reducir movimiento»). Preguntas frecuentes de la tienda.
  Los bloques de servicio sólo si la tienda publicó servicios. El código ya no
  escribe copy que nombre una marca de equipos: el titular y el texto del bloque
  «Cómo trabajamos» salen de `services_hero_title` y `services_hero_subtitle` de la
  tienda (los mismos de `/services`), con un respaldo neutro. El primer pilar pasó
  de «Productos y equipos Apple» a «Conocemos lo que vendemos y reparamos»: ese
  texto sigue compilado y **no** tiene campo en el CMS.
- **Hero.** Sigue siendo una losa oscura en los dos temas; sus pruebas no se
  tocaron. Si la tienda publica la campaña `home_hero`, aporta texto, botones e
  imagen; sin campaña, el hero es el de antes. Ninguna imagen se elige por el
  identificador de la empresa.
- **Nosotros y Contacto** (`/about`, `/contact`): sólo datos que la tienda
  publicó. Sin datos, lo dicen.
- **Carrito.** La línea enlaza a la ficha del producto y cada campo de cantidad
  dice de qué producto es.
- **Navegación móvil.** El menú declara su estado y lleva a Contacto y Nosotros.
  «Control interno» sigue dependiendo de la respuesta del servidor.

Sin cambios: checkout, pagos, desglose fiscal, pedidos, autenticación, proveedor
de tema y `backend/` (subárbol idéntico a `master`, `736690e`; vale su medición de
4643 pruebas, 0 fallos, 3 omitidas). Sin migraciones.

Panel interno: ningún archivo bajo `frontend/app/admin` cambia. Sí cambia lo que lo
envuelve: `layout.tsx` y `StorefrontChrome.tsx` ponen el contenido dentro de un
`div` con clase `internal-surface` (antes un `div` sin clase). Comprobado en
navegador contra `master`, con la misma cuenta y los mismos datos: `/admin`,
`/admin/sales/pos`, `/admin/service/orders` y `/admin/inventory` salen idénticos
píxel a píxel y sin diferencias de estilo calculado.

CSS: lo añadido a `globals.css` son clases `v3-*`, una variable en `:root`
(`--v3-ease-out`), reglas `@starting-style` y un `@keyframes`. No se añadió ningún
selector de elemento (`table`, `h1`, `input`, `button`) que alcance al panel.

El defecto «identificador repetido en las líneas del carrito» no existe en
`master`: lo corrigió la reconciliación anterior. No se tocó.

Validación sobre `7f49168`: frontend 483 pruebas en 47 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 143 de 143,
sin fallos, omitidas ni reintentos, 8,6 min.

Aceptación visual del piloto (2026-10-02) sobre `743aa5e`: siete rutas (`/`,
`/product`, una ficha, `/cart`, `/services`, `/about`, `/contact`) en 1440 y 390 px,
temas claro y oscuro. Sin desbordamiento, logotipo correcto por contraste, hero
oscuro en ambos temas, categorías reales, carrusel, navegación móvil y movimiento
reducido correctos.

Defecto encontrado, **anterior a esta rama** (idéntico en `master`): en móvil, hasta
414 px, el titular y el párrafo del hero se cortan por la derecha. La columna de
texto mide 367 px fijos y la sección oculta lo que sobra, así que la prueba de
desbordamiento no lo detecta. Queda como HERO-MOBILE-CLIP, sin corregir aquí
(corregido después en `ff564e9`, rama `fix/hero-mobile-clip`).

Queda fuera, registrado:

- STOREFRONT-PILLARS-CMS = PROPUESTA. Los cuatro pilares de la portada están
  compilados; para que una tienda los redacte hace falta un campo propio.

- STOREFRONT-EDITORIAL-CMS = PENDIENTE. Ilustraciones por categoría y material
  editorial deben ser contenido subido por la tienda. Hoy sólo existe la imagen
  de campaña.
- STOREFRONT-FEATURED-CATEGORIES = PROPUESTA. La portada muestra las primeras seis
  categorías en el orden del servidor; no hay forma de destacar u ordenar.
- TENANT-TYPOGRAPHY = PROPUESTA. El manual del piloto pide Montserrat; la
  plataforma usa Inter y Unbounded para todas las tiendas.
- INTERNAL-UI-V3 = PENDIENTE. Menú móvil del panel, gráficos y estilos del
  trabajo paralelo.
- Imágenes de producto con licencia: el comando `populate_storefront_images`, el
  manifiesto y los recortes del trabajo paralelo no se portaron.
- Hero configurable por tienda (variante clara u oscura): PENDIENTE.

La deuda de SVC-FUNC-01 no cambia.

## 2026-10-01 — UX-RECON-SVC-01: la interfaz de #43 sobre el master con servicio y caja

Rama `reconcile/uxui-after-svc`, sobre `master` `d98d70c` (que ya incluye SVC-FUNC-01
por el PR #44). Merge de reconciliación `ee3a8d3`, con padres `d98d70c` y `9c1486b`
(la cabeza del PR #43). El PR #43 no se modifica: se reconcilió contra un `master`
anterior a la navegación del servicio, a las autoridades de asignación y cobro y a
la recepción desde la caja.

Regla aplicada: `master` decide comportamiento, seguridad y flujo; #43 decide la
presentación donde es compatible.

- `/admin/service` sigue siendo una redirección a `/admin/service/orders`. #43 traía
  ahí la consola antigua de una sola pantalla; no vuelve.
- Las seis colas (`intake`, `orders`, `diagnostics`, `repairs`, `quality`, `delivery`)
  conservan su ruta y su entrada propia en la barra lateral. La presentación que #43
  dio a aquella consola (cabecera, barra de filtros, tabla, estilos compartidos y
  búsqueda con pausa) se aplicó a `ServiceQueue`, que es lo que dibujan las seis.
- La caja conserva «Productos / Servicio técnico» y la recepción de servicio con
  técnico obligatorio, con la cabecera y los estilos de #43. El flujo de productos
  no cambia.
- El detalle de la orden conserva `mayAssignTechnician`, `mayCollectPayment`,
  «Quitar» sólo con `service.orders.manage` y «Reversar» sólo con
  `service.payments.manage`; sólo cambia la cabecera.
- Las seis pantallas de F2 y `branch-authority.ts`, `internal-modules.ts`,
  `InternalControlGuard.tsx`, `auth.ts` y `service-console.ts` quedan como en `master`
  en lo que deciden. Ningún archivo de `master` se elimina.

Sin backend ni migraciones: el subárbol `backend/` es idéntico al de `master`
(`736690e`), así que vale su medición: 4643 pruebas, 0 fallos, 3 omitidas.

Validación sobre el árbol del merge (`99dbca6`): frontend 437 pruebas en 43 suites,
OK; typecheck OK; lint 0 errores y 26 advertencias; build OK (50 páginas);
Playwright 121 de 121, sin fallos, omitidas ni reintentos, 7,9 min.

Sigue sin adoptarse lo que ya quedó fuera en la primera reconciliación (portada,
cabecera, pie, servicios, inicio de sesión de la tienda). La deuda de SVC-FUNC-01
se mantiene: POS-CUSTOM-PRODUCT (propuesta), FISCAL-SERVICE y SVC-QUOTE-INSHOP
(pendientes), MIG-ADMIN-LIVE, SVC-POS-DRAFT y SVC-ELIGIBLE-COST.

## 2026-10-01 — SVC-FUNC-01: servicio técnico operativo e integrado con la caja

Rama `feature/service-pos-functional-integration`, sobre `master` `ef9890f`. Código
en `9b59a31`.

Qué cambia para quien usa el sistema:

- **Navegación del servicio técnico (SVC-NAV-01, `937cf82`).** Recepción, Órdenes,
  Diagnóstico, Reparación, Control de calidad y Entrega tienen cada una su ruta
  (`/admin/service/intake`, `orders`, `diagnostics`, `repairs`, `quality`,
  `delivery`) y su cola de trabajo. Antes las seis abrían la misma pantalla y la
  barra lateral las marcaba todas a la vez. `/admin/service` redirige a Órdenes.
  Una cola agrupa estados reales del servidor y sólo filtra.
- **Asignar técnico (SVC-ASSIGN-01, `1928b05`).** Nueva capacidad
  `service.orders.assign`. Asigna quien tiene `assign` o `service.orders.manage`.
  El rol estándar Ventas recibe `assign`, y no `manage`. Sólo se puede asignar a
  una persona activa de la empresa, que puede ver órdenes de servicio y alcanza
  la sucursal de la orden; cualquier otro identificador responde «no encontrado».
  Con `assign` se asigna y se reasigna; dejar una orden sin técnico exige
  `service.orders.manage` (SVC-ASSIGN-UNASSIGN, `4796db0`): con sólo `assign` el
  servidor responde 403 y no cambia nada.
  La regla de quién puede nombrar a un técnico es una sola (SVC-ASSIGN-VIEW-01,
  `9b59a31`): `service.orders.manage`, o `service.orders.assign` junto con
  `service.orders.view`. Vale igual para asignar una orden existente, para pedir
  los candidatos de una sucursal y para recibir un equipo indicando el técnico.
  Recibir un equipo sin técnico exige lo mismo que antes.
- **Cobrar el servicio (SVC-PAY-01, `d62fa30`).** Nueva capacidad
  `service.payments.collect`. Registra un pago quien tiene `collect` o
  `service.payments.manage`; reversar sigue exigiendo `manage`. Los roles estándar
  Servicio Técnico y Supervisor Técnico reciben `collect`. Sigue haciendo falta
  una cotización aprobada para cobrar.
- **Servicio técnico desde la caja (POS-SVC-01, `6caa88c`).** `/admin/sales/pos`
  tiene un conmutador «Productos / Servicio técnico». En servicio se recibe un
  equipo eligiendo cliente, equipo, sucursal, falla y técnico por nombre
  (obligatorio). Una sola petición crea la orden y la asigna; se muestra el número
  y «Abrir orden», y el técnico la ve en «Mis reparaciones».

Lo que este flujo **no** hace: no crea pedido, línea de pedido, movimiento de
stock, comprobante ni comisión, y no cobra nada. Una orden de servicio no es una
venta. El importe de un servicio es una línea de la cotización de la reparación
(tipo «Servicio», con descripción, cantidad y precio, sin producto).

Clasificación funcional, con prueba:

| Función | Estado | Prueba |
|---|---|---|
| Servicio desde la caja | IMPLEMENTADO | `SvcIntakeWithAssignmentTest`, `pos-service-intake.test.tsx`, E2E `service-pos` |
| Línea de servicio personalizada | IMPLEMENTADO (ya existía) | `SvcCustomServiceLineTest` |
| Cobro por el técnico | IMPLEMENTADO | `SvcPaymentCollectTest`, E2E `service-pos` |
| Producto personalizado en la caja (POS-CUSTOM-PRODUCT) | PROPUESTA | — |
| Comprobante fiscal de un pago de servicio (FISCAL-SERVICE) | PENDIENTE | — |
| Aprobar la cotización en tienda (SVC-QUOTE-INSHOP) | PENDIENTE | — |

Migraciones: `0094_service_orders_assign` y `0095_service_payments_collect`. Sólo
amplían roles estándar que la empresa no modificó, comparando contra conjuntos
congelados. No cambian el esquema y no se revierten.

Cambios de comportamiento a tener presentes:

- Una membresía con el rol antiguo `technician` que nunca pasó a roles de empresa
  ya no es asignable: no puede ver órdenes de servicio, así que la orden sería suya
  e invisible para ella. La migración 0057 ya trasladó a esas personas al rol
  estándar.
- La lista de candidatos de una orden se limita a quienes alcanzan su sucursal.
- Con sólo `service.orders.assign` hace falta además `service.orders.view` para
  asignar, ver candidatos o recibir un equipo con técnico. Ningún rol estándar
  tiene `assign` sin `view`; un rol creado por la empresa sí puede tenerlo.

Defecto corregido de paso: el `@transaction.atomic` de `assign_technician` había
quedado sobre `_notify` desde `108a904`. `assign_technician` bloquea la fila de la
orden y, fuera de una transacción, PostgreSQL lo rechaza. Lo cubre
`SvcAssignOutsideATransactionTest`.

Validación sobre `9b59a31`: backend PostgreSQL 4643 pruebas (4640 OK, 3 omitidas,
0 fallos), 1600,8 s; `check` sin problemas; `makemigrations --check` sin cambios.
Playwright 121 de 121, sin fallos, omitidas ni reintentos, 9,0 min. El frontend no cambió desde `4796db0` (árbol `frontend/`
idéntico, `7ea6870`), donde se midió: 428 pruebas en 40 suites, OK; typecheck OK;
lint 0 errores y 33 advertencias (las del baseline); build OK (50 páginas). Los
commits de código anteriores se comprobaron además por separado (pruebas del
área, typecheck y migraciones).

Deuda y límites conocidos: al volver de «Servicio técnico» a «Productos» se pierde
una recepción a medio llenar (la cesta sí se conserva); si el contexto de la caja
no carga, tampoco se llega al modo servicio; una orden recién recibida aparece en
Órdenes › «Mis reparaciones», y en la cola Reparación sólo cuando su cotización
está aprobada; un
superusuario de plataforma con membresía de personal figura como candidato. Detalle:
[docs/AUDIT_MEMORY.md](docs/AUDIT_MEMORY.md).
## 2026-10-01 — Integración en master y reconciliación UX/UI (#39)

ERP y las fases F1/F2 de la auditoría están en `master` desde el PR #42 (merge
`ef9890f`): una sola transición, con el árbol exactamente igual al que pasó todas las
compuertas.

La rama de UX/UI (#39) se diseñó sobre el `master` anterior y chocaba en 75 archivos. Su
reconciliación vive en `reconcile/uxui-after-f2` (merge `1fea6b9`), todavía sin
integrar. Criterio: `master` decide el comportamiento y #39 la presentación cuando es
compatible. **IMPLEMENTADO** en esa rama: el lenguaje visual de #39 en el control
interno (cabeceras de página, botones y campos compartidos, navegación lateral con
foco atrapado en móvil), las páginas de error, carga y no encontrado, y la presentación
de carrito, catálogo, detalle de producto, pedidos y recuperación de contraseña. Las
reglas de F2 no cambian: qué puede hacer cada persona y en qué sucursales se decide
igual que antes. **PARCIAL**: el rediseño de #39 para la portada, la cabecera y el pie
de la tienda, la página de servicios y la pantalla de inicio de sesión no se adoptó,
porque `master` ya los había rehecho con contenido editable, tema claro/oscuro y
logotipo por empresa, que #39 no conoce. Los colores de estado de #39, pensados sólo
para tema oscuro, se expresan con los tokens del tema.

Validación sobre `1fea6b9`: frontend 411 pruebas en 40 suites, OK; typecheck OK; lint 0
errores y 26 advertencias; build de producción OK (44 páginas); Playwright 118 de 118.
Sin archivos de backend ni migraciones en el cambio, por lo que la corrida completa de
backend vigente es la de `c191a84` (4566 OK).

## 2026-09-30 — F2 completada: delegación y alcance por sucursal

La fase F2 de la auditoría queda cerrada sobre `c191a84`. Qué puede hacer una persona
(capacidades) y dónde puede hacerlo (alcance por sucursal) se exigen juntos, en el
servidor y reflejados en la interfaz: nadie concede, retira ni modifica lo que no
alcanza; abrir saldo exige autoridad de inventario; pertenecer a una empresa no
basta para leerla; las modificaciones de nivel empresa exigen alcance sobre toda la
empresa; y ningún identificador de sucursal sirve para sondear si existe.

Últimos dos cierres. E2E-02: la prueba de personal desactivaba una cuenta demo
compartida y la dejaba sin capacidades para la corrida siguiente; ahora usa un
trabajador propio (`seed_demo_users --e2e-fixtures`, `9c3445f`). E2E-01: la prueba
fiscal esperaba «Emitir factura»; el panel dice «Preparar factura» porque ese paso
numera y firma sin hablar con SUNAT, y el envío es otro botón. Era la prueba la que
estaba desfasada, y al corregirla volvieron a ejecutarse cuatro pruebas que no
corrían (`c191a84`).

Validación final: backend PostgreSQL 4569 pruebas (4566 OK, 3 omitidas, 0
fallos), 1544,8 s; `check` sin problemas; 0 migraciones nuevas en toda la fase.
Frontend 402 pruebas en 37 suites, OK; typecheck OK; lint 0 errores y 33
advertencias (las mismas del baseline); build de producción OK (44 páginas).
Playwright 118 de 118, sin omitidas ni reintentos. Nada se ha empujado al remoto.

Deuda que sigue abierta: `BranchAccessPanel.tsx` sin uso; el botón «Añadir
trabajador» no comprueba capacidad (RBAC-F4); el admin de Django permite editar
`Product.inventory` sin Kardex; `C15InitialRaceTest` falla aislado (TEST-ENV-01);
`storefront_content_views` sin auditar bajo WRITE-SCOPE-01; una persona con
sucursales seleccionadas no puede reactivar una sucursal propia inactiva. Detalle:
[docs/AUDIT_MEMORY.md](docs/AUDIT_MEMORY.md).

## 2026-09-30 — F2 · DRIFT-01 y DRIFT-07: la interfaz refleja el contrato

Asignar técnico desde la web vuelve a funcionar: la consola leía `technicians` y el
servidor responde `candidates`, así que el selector salía siempre vacío. El tipo
TypeScript describe ahora la respuesta real (`a4be03b`).

La interfaz de administración tiene en cuenta dónde puede actuar cada persona, no
sólo qué puede hacer. A quien tiene sucursales seleccionadas ya no se le ofrece
«Todas», sucursales que no alcanza, crear sucursales, elegir la sucursal de
despacho, guardar los ajustes de la empresa, editar la serie de empresa ni cambiar
el alcance de la numeración; tampoco archivar promociones que funcionan fuera de sus
sucursales, y un combo nuevo se aplica sólo en las suyas. Sigue viendo la plantilla
completa, los ajustes y las series, y edita su sucursal y la serie de su sucursal.
El alcance sale del contexto que envía el servidor; el backend sigue rechazando todo
lo anterior (`6958ec0`, `f6dc9ca`).

Validación: frontend 400 pruebas en 37 suites, OK; typecheck OK; lint 0 errores y 33
advertencias (sin cambio); build de producción OK (44 páginas). Backend sin cambios:
regresiones de contrato (73 pruebas) OK; la corrida completa vigente es la de
`c076120` (4560 OK). E2E pendiente (E2E-02, E2E-01).

## 2026-09-30 — F2 · WRITE-SCOPE-01: el alcance por sucursal limita también lo que se modifica

`company.manage` dice qué se puede cambiar; el alcance por sucursales dice dónde. Una
persona limitada a sucursales seleccionadas podía, con esa capacidad, crear
sucursales, editar o desactivar una que no alcanza, elegir la sucursal de despacho de
la tienda online, editar la serie de empresa, cambiar el alcance de la numeración y
modificar los ajustes de la empresa (identidad, marca, moneda). Ahora esas
operaciones de nivel empresa exigen, además de la capacidad, alcance sobre toda la
empresa (plataforma, modo «todas» o puente legacy: `tenancy.has_company_wide_scope`).
Editar una sucursal propia y la serie de una sucursal propia siguen permitidos; las
lecturas no cambian. Una sucursal inexistente o ajena sigue respondiendo 404; una de
la propia empresa fuera de alcance, 403 (ya figura en su plantilla). Commits
`fa85d41` (sucursales y despacho) y `c076120` (numeración y ajustes).

Validación: backend PostgreSQL 4563 pruebas (4560 OK, 3 omitidas, 0 fallos),
1519,8 s; `check` sin problemas; 0 migraciones por generar. Frontend sin cambios.

## 2026-09-30 — F2 · RBAC-01 y RBAC-02: lecturas de empresa y sucursal por defecto

Pertenecer a una empresa ya no autoriza a leerla. La ficha de la empresa (lista y
detalle) exige `company.view` o `company.manage`; la plantilla de sucursales, además,
acepta `memberships.view/manage`, porque conceder acceso por sucursal la usa como
selector. Sin esas capacidades las listas salen vacías y los detalles responden 404,
igual que un id inexistente o de otra empresa. La plantilla sigue siendo de nivel
empresa (incluye las inactivas, que se reactivan desde ahí); el maestro de plataforma
conserva su alcance global. Se reutiliza el helper con el que M11 ya protegía áreas y
roles (`5afdb81`).

La sucursal por defecto de una membresía ya no revela si un id existe: inexistente, de
otra empresa o fuera del alcance de quien la asigna responden igual, 404 «Sucursal no
encontrada o sin acceso.», antes de escribir nada. Antes respondía 404, 400 con otro
mensaje o, para una sucursal no alcanzada, la aceptaba (`d18e983`).

Validación: backend PostgreSQL 4545 pruebas (4542 OK, 3 omitidas, 0 fallos), 1517,6 s;
`check` sin problemas; 0 migraciones por generar. Frontend sin cambios. Queda abierta,
pendiente de decisión, la autoridad de `company.manage` con alcance por sucursales
sobre la configuración de nivel empresa (alta y edición de sucursales, serie de empresa).

## 2026-09-30 — F2 · F-CAP-01: el stock inicial exige autoridad de inventario

`products.manage` es autoridad sobre el catálogo; `inventory.adjust`, sobre las
existencias. Crear un producto con `inventory > 0` abre saldo con una línea
`initial_stock` en el Kardex y ahora exige ambas, con la misma puerta que el ajuste
directo (puente legacy incluido) y antes de escribir nada: sin `inventory.adjust`
responde 403 y no quedan producto, stock, movimiento ni auditoría. Con `inventory`
omitido o 0 basta `products.manage`. La sucursal del saldo la sigue eligiendo el
servidor dentro del alcance de quien crea. Es la única ruta pública que abre saldo:
la edición de producto rechaza `inventory`, la importación de productos no crea stock
y la de stock ya exigía `inventory.adjust` y acceso a la sucursal. Los presets
actuales sólo dan `products.manage` al rol «administrador», que también tiene
`inventory.adjust`. Commit `c042fea`.

Validación: backend PostgreSQL 4528 pruebas, OK (3 omitidas), 1511,2 s; `check` sin
problemas; 0 migraciones por generar. Frontend sin cambios.

## 2026-09-30 — Auditoría F2 (eje «dónde»): delegación por sucursal

El eje «dónde» ya tiene regla de delegación, como el eje «qué» la tenía desde G3:
nadie concede, retira ni modifica por escritura una sucursal que no alcanza.
`tenancy.can_delegate_branch_scope` es la autoridad única. Quien opera toda la
empresa (plataforma, modo «todas», puente legacy) sigue concediendo cualquier cosa;
una persona limitada a sucursales seleccionadas sólo nombra un subconjunto de las
suyas, nunca «todas», y no toca a quien llega más lejos que ella. Se aplica al alta y
edición de membresías, a las invitaciones de personal (tenían el mismo hueco) y a las
promociones. Además, la edición de una membresía es ahora una sola transacción, y la
serie interna de una sucursal que la persona no alcanza responde 404 también en el
detalle, igual que ya pasaba en el listado. Cerrados: F-BRANCH-01 (`20d110c`),
F-BRANCH-02 (`cccb4d2`), F-BRANCH-03 (`70286d1`).

Validación: backend PostgreSQL 4520 pruebas, OK (3 omitidas), 1520,6 s (31 nuevas);
`check` sin problemas; 0 migraciones por generar. Frontend sin cambios. Pendiente
en F2: F-CAP-01 (requiere decisión), RBAC-01/02, DRIFT-01, DRIFT-07 (la interfaz sigue
ofreciendo «todas» a quien no puede concederlo; el backend responde 403 con un
mensaje legible), E2E-02 y E2E-01.

## 2026-09-30 — Auditoría F1 (seguridad, tenancy, autorización) cerrada

F1 completada sobre `4a9dd5c`. Único HIGH, FE-AUTH-01, corregido en ese commit: el
proxy `frontend/app/api/[...path]/route.ts` rechaza con 400 los segmentos que tras
decodificar son `.`/`..` o contienen `/` o `\`, y ya no sirve el admin de Django por el
origen público (regresión `frontend/__tests__/api-proxy-scope.test.ts`, 11 casos;
jest 368/368 en 34 suites, typecheck OK, lint 0/33). El backend no cambió. Quedan
confirmados y pendientes, entre otros: F-BRANCH-01/02/03 (el eje «dónde» no tiene
regla de delegación), F-CAP-01, F-TENANT-01, DRIFT-01, E2E-01, E2E-02, SEC-SET-02,
SEC-SET-04-A y THROTTLE-CACHE-01. IDOR-01 refutado. Índice verificado de hallazgos,
invariantes, símbolos y tests: [docs/AUDIT_MEMORY.md](docs/AUDIT_MEMORY.md).
Siguiente fase: F2, delegación y alcance por sucursal.

## 2026-09-29 — ERP-FISCAL-6 cerrado · auditoría integral abierta

ERP-FISCAL-6 queda cerrado en `erp/sales-fiscal-ui` con cuatro commits: `97043d6`
(capa comercial), `c9e64b9` (POS: orden del snapshot y caja BETA), `fabfa38` (tests de
promociones) y `9525b08` (fiscal); la rama está empujada a `origin` con ese HEAD. La
auditoría integral se abre en `audit/full-system-2026-09`, creada desde `9525b08` e
integrando `origin/master` `2dca0a3` (dos commits, sólo `docs/internal-parity-matrix.md`,
merge limpio `65aa8c1`). **Baseline oficial medido sobre `65aa8c1`**, árbol limpio:
backend PostgreSQL 14 · Python 3.14.6 · Django 5.2.17 · DRF 3.17.1 — 4489 pruebas, OK
(3 omitidas), 1477,8 s; `check` sin problemas; migraciones 105 aplicadas / 0 pendientes /
0 por generar. Frontend Node 24.16.0 · Next 16.3.4 · React 19.2.4 · TypeScript 5.9.3 —
357 pruebas en 33 suites OK, typecheck OK, lint 0 errores / 33 advertencias, build OK
(44 páginas). Playwright 118 pruebas: 113 pasan, 1 falla preexistente (E2E-01:
`fiscal-invoice.spec.ts` espera «Emitir factura» y el panel dice «Preparar factura»
desde `b3b1cfc`), 4 no ejecutadas por modo serie tras ese fallo. `npm audit`
(producción): 0 vulnerabilidades. Sin CI propio (CI-01, MEDIUM): no hay
`.github/workflows`, cero check-runs; `dynamic/dependabot/update-graph` es el grafo de
dependencias de GitHub. Este documento y `07_CHANGELOG.md` se actualizan en un commit
posterior al SHA medido, que sólo toca `.md`.

## 2026-09-29 — ERP-FISCAL-6: descuentos declarados

Una venta con descuento ya se emite como factura (01) o boleta (03) en BETA. El
descuento se declara con `cac:AllowanceCharge` donde nació: cupón y manual como
descuento global (Catálogo N.º 53 vigente, código `02`); promoción automática en
cada línea rebajada (código `00`), con la atribución por componente que el motor
comercial congela en la venta (ADR-42) y que las ventas anteriores reconstruyen en
memoria sin reescribir su historial. Los totales siguen las reglas oficiales de
validación (ADR-43): `PayableAmount` es lo cobrado, la base imponible es la del
snapshot y `AllowanceTotalAmount` se omite para no declarar la rebaja dos veces. Un
descuento que el snapshot no explica falla cerrado sin gastar correlativo.

La caja ofrece nota interna, boleta y factura con el nuevo contrato de
`receipt_options`: quien puede emitir ve cada opción con `enabled` y una causa segura
cuando no está disponible (`FISCAL_DISABLED`, `NO_SERIES_FOR_BRANCH`,
`AMBIGUOUS_SERIES`, `UNSUPPORTED_ENVIRONMENT`); quien no puede emitir no recibe
opciones fiscales. Tras cobrar, la pantalla muestra el comprobante electrónico real
(serie-correlativo, ambiente, estado del backend) en vez de una nota interna
disfrazada. `seed_demo_users --fiscal-beta` prepara F001/B001 DEMO en BETA
(DEBUG=True y `FISCAL_ENVIRONMENT=beta`, idempotente, sin tocar correlativos).
`FISCAL_ENABLED` sigue apagado por defecto; se enciende sólo en el `.env` local.

Validación: backend PostgreSQL 4489 pruebas, OK (3 omitidas), 1917,4 s; frontend 357 pruebas en 33 suites,
OK; typecheck y build de producción PASAN; lint 0 errores y 33 advertencias (las
mismas de antes). Playwright: `pos-ticket.spec.ts` PASA; `pos-receipt-options.spec.ts`
PASA (humo real con `dev_admin`: las tres opciones habilitadas para «Tienda
principal»). Migraciones nuevas: 0. Pendiente: verificación directa en
SUNAT BETA de un comprobante con descuento; producción no implementada; el PDF aún
no imprime la línea de descuentos globales (FISCAL-PDF-01).

Detalle: [ADR-42 y ADR-43](docs/adr-fiscal-c22a1.md) ·
[matriz UBL](docs/sunat-factura-ubl21-matriz.md) ·
[Catálogo 53 y reglas](docs/sunat-cpe-requisitos.md).

## 2026-09-28 — Estabilización funcional

La estabilización de POS y recepción técnica está implementada en el árbol de
trabajo. El alcance fiscal continúa limitado a BETA. La entrega de recepción usa
la capacidad existente y la migración selectiva 0093; se preservan permisos
personalizados, empresas, sucursales, historial y auditoría.

Validación final: backend PostgreSQL 4412 pruebas, OK (3 omitidas), 1448,6 s;
frontend 350 pruebas en 33 suites, OK. Lint: 0 errores y 33 advertencias.
**Typecheck y build de producción PASAN**: los dos badges de inventario que se
exportaban desde `page.tsx` viven ahora en el módulo compartido
`app/admin/components/InventoryUi.tsx`, de donde esas páginas ya importaban el
resto de sus primitivas. **Playwright: 17 de 17** en POS y acceso interno.
SQLite local actualizado hasta 0093 con respaldo; sin migraciones pendientes.

Resultados, límites y hallazgos:
[Auditoría de estabilización](docs/estabilizacion-funcional-2026-09-28.md).

Historial técnico completo:
[Estado y auditoría técnica](docs/estado-actual-y-auditoria-tecnica.md).
