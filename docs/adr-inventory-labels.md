# ADR — Códigos de barras y etiquetas térmicas del inventario (INVENTORY-LABELS-01)

Estado: **PROPUESTA**, fase LABEL-00/01: auditoría y decisiones. Incorpora las decisiones del propietario del
2026-10-23 (§1.1). Nada de esto está implementado. Cada decisión queda marcada como `PROPUESTA`, o como
`BLOCKED: HARDWARE` / `BLOCKED: ARCHIVO REAL` cuando depende de la impresora o del archivo histórico.

Base auditada: `master` @ `eda8b62`. Producción sirve R1 (`6834bc2`), con migraciones hasta `0145`.
Orden de autoridad: código > migraciones > tests > docs. Las referencias tienen la forma `archivo::símbolo`.

## 1. Objetivo

Que cualquier empresa y sucursal genere códigos y etiquetas adhesivas térmicas, para **accesorios por
cantidad** y para **equipos serializados**, integrados con el catálogo, la recepción, las cargas masivas, las
imágenes y la caja que ya existen. **No habrá un segundo inventario, ni un segundo sistema de
identificación, ni una copia de `ProductBarcode`.**

### 1.1 Decisiones del propietario (2026-10-23), vinculantes para este ADR

1. **Los códigos existentes se conservan.** Se genera uno sólo si el producto no trae un código ni tiene uno
   asociado. Se conservan el código escrito por el usuario y el del archivo de carga; el producto que ya tiene
   código lo reutiliza; si tiene varios, se conservan todos. Además:
   - nunca se sobrescribe un código;
   - nunca se regenera al editar ni al imprimir;
   - nunca se reutiliza el código de un producto desactivado;
   - un código que ya es de **otro** producto se rechaza con un mensaje claro; nunca se genera otro para
     esquivar el error;
   - la misma regla vale en todos los caminos de alta, con restricciones de base de datos, transacciones y
     concurrencia segura;
   - el frontend nunca genera códigos.
2. **Fase nueva LABEL-MIGRATION-01:** importar los códigos históricos de un sistema anterior.
   - Se reutiliza la carga masiva.
   - Hay una vista previa que no escribe nada, y se aplica sólo con aprobación.
   - Los códigos son texto: se conservan los ceros, las mayúsculas y la longitud; nunca se reconstruye uno, y
     un código interno nunca se presenta como GTIN.
   - Es idempotente y nunca se ejecuta sola en producción.
   - Va **antes** de rellenar en bloque los productos sin código. Las altas nuevas pueden avanzar en paralelo.
3. **Equipos: el código de barras de la etiqueta es el IMEI 1** cuando el equipo tiene un IMEI válido.
   - Sin IMEI: la serie, si sirve y no es ambigua; si no, un identificador interno estable.
   - El IMEI nunca es un código de producto compartido y nunca sale en un endpoint público.
   - Imprimirlo es la elección de **este** propietario, no una regla global: necesita controles,
     trazabilidad y un perfil por empresa.
4. **Diseños confirmados.**
   - **Accesorio, 30×20 mm:** logo, descripción corta, variante si cabe, precio, código y su texto. Una
     etiqueta por unidad recibida.
   - **Teléfono, 50×25 mm:** logo, modelo, capacidad, color, SIM si se conoce, condición, precio, Code 128 del
     IMEI 1 y su texto, y **batería sólo en equipos usados con medición registrada**.
   - Nunca se encoge un código por debajo de lo legible. Los datos técnicos que no caben van en una etiqueta
     técnica aparte o en una ficha autenticada.
5. **Impresora real: Advance ADV-9010N.** Rollos de 30×20 mm (accesorios) y 50×25 mm (teléfonos). No se
   supone conexión ni lenguaje hasta verificarlos. Los diseños y los PDF a escala se preparan ya. La
   automatización física depende de las pruebas con la impresora y los rollos. El agente de tickets no se
   toca sin un análisis de compatibilidad.
6. **Se imprime sólo tras una entrada física confirmada.** Nunca por listas semanales, ofertas sin stock,
   vistas previas, cargas rechazadas ni cambios de costo. Un cambio de precio comercial lista las etiquetas a
   reimprimir y no regenera códigos. La cola es idempotente, transaccional y está atada a la confirmación de
   la recepción.

### 1.2 Diez conceptos separados: qué existe y qué es nuevo

| # | Concepto | Existe hoy | Nuevo (fase) |
|---|---|---|---|
| 1 | Código **previo** de un producto (EAN/UPC del fabricante, interno de la tienda, escrito a mano) | `ProductBarcode`: único por empresa, varios por producto, se desactiva y no se borra | Rechazar el código que choca con un IMEI o una serie de unidad (LABEL-01) |
| 2 | Código **generado** para un producto sin código | **No existe** | `product_codes.ensure_code` sobre `InternalSequence` (LABEL-01) |
| 3 | Código **original importado** de la base histórica | La carga de productos ya guarda `Código` (`internal`) y `Código de barras` como texto y avisa de ceros perdidos | Plantilla, mapeo, clasificación de la vista previa, SKU antiguo e id externo (LABEL-MIGRATION-01) |
| 4 | **Identificador físico** de la unidad | `StockUnit` (`imei`, `imei2`, `serial_number`, `public_id`); único escritor `receive_units` | `StockUnit.label_code`, el id interno estable (LABEL-02) |
| 5 | **IMEI como símbolo** escaneable | La caja ya resuelve un IMEI con `find_unit` | Regla de qué símbolo lleva la etiqueta, perfil por empresa y permiso (LABEL-02) |
| 6 | **Diseño comercial** | Nada | `LabelTemplate` + dibujo (LABEL-03) |
| 7 | **Diseño técnico** | Nada | Plantilla `device_technical` con permiso propio (LABEL-03) |
| 8 | **Impresión manual** | Sólo tickets ESC/POS de 80 mm por red | Lote manual + PDF a escala (LABEL-03); térmica (LABEL-04) |
| 9 | **Impresión automática** | Tickets tras un pago confirmado (`_queue_ticket`) | Gancho tras una entrada física confirmada, apagado por defecto (LABEL-05) |
| 10 | **Compatibilidad con la impresora real** | Agente ESC/POS sólo por TCP 9100; #120 (USB por CUPS) sin fusionar | Adaptador ADV-9010N tras verificar el equipo (LABEL-04, `BLOCKED: HARDWARE`) |

## 2. Auditoría: qué existe

### 2.1 Códigos de producto — `models.py::ProductBarcode`

- Tabla aparte, porque un producto tiene varios códigos. Guarda la `company` duplicada para que la búsqueda
  sea una sola consulta indexada.
- **Unicidad por empresa:** `UniqueConstraint(company, code)` (`unique_barcode_per_company`), que también
  cubre los **inactivos**. El código de un producto o de un código desactivado nunca se reutiliza en esa
  empresa; entre empresas puede repetirse. Hay un primario por producto, con una restricción condicional.
- **Validación:**
  - `normalize_barcode` sólo recorta los bordes: no fuerza mayúsculas ni convierte a número;
  - `validate_barcode` exige ASCII imprimible de 4 a 64 caracteres;
  - **se distinguen mayúsculas**: `abc1` y `ABC1` son dos códigos;
  - `symbology` es informativa y la búsqueda nunca depende de ella;
  - `import_services::detect_symbology` sólo nombra EAN-13, EAN-8 o UPC-A si el dígito de control cuadra,
    nunca por la longitud.
- **Productos y códigos no se borran por la API:** no hay un `delete` de producto en `admin_views.py`, y
  `AdminProductBarcodeView.patch` sólo activa, desactiva o promueve a primario.

| Quién escribe códigos hoy | Archivo | Comportamiento |
|---|---|---|
| Alta guiada | `registration_services::_barcode` | `sku` → primario con simbología `unknown`; si es de otro producto, 409 con su nombre. **Sin `sku`, el producto queda sin código** |
| Alta simple del panel | `admin_views.py` (`ser.save(company=…, inventory=0)`) | **no crea ningún código** |
| Carga de productos | `import_services::_barcodes_for`, `apply_products` | `Código de barras` → simbología detectada; `Código` → `internal`. Un código ya tenido no se reescribe ni se reactiva. Un producto encontrado gana los códigos nuevos de la fila sin perder los que tenía |
| API de códigos | `pos_views::AdminProductBarcodeView` (`/api/admin/products/<pk>/barcodes/`) | GET, POST y PATCH con `products.manage`; 409 por choque; deja `AdminAuditLog`. **El panel no tiene pantalla que la use** |
| Exportaciones | `import_exports::product_codes` | `internal` → «Código»; el resto → «Código de barras» |

**Generación automática: NO existe.** El patrón `C000001` sólo aparece como dato importado del archivo del
piloto (696 filas, 692 con forma `C000001`, y otros 692 códigos de 12 dígitos que no son UPC) y en los tests.

La maquinaria de correlativos que sí existe es `InternalSequence` + `sequences.allocate`, hoy para las notas
`NV-` y las órdenes `SRV-`:
- una fila por empresa (o por sucursal), bloqueada con `select_for_update`;
- un entero monotónico que nunca se recicla;
- prefijo y relleno configurables por la empresa;
- `ensure_company_sequence` crea la fila si falta.

### 2.2 Unidades — `models.py::StockUnit`, `stock_unit_services.py`

- `BranchStock.quantity` sigue siendo el único número vendible.
- **Único escritor:** `stock_unit_services::receive_units`. Lo llaman `registration_services`,
  `unit_import_services::apply_units`, `supply_services::receive_line` y `stock_unit_views`.
- Identificadores:
  - `serial_number` es único **por producto**, no por empresa;
  - `imei` e `imei2` son únicos **por empresa** (restricción condicional) y se validan con Luhn
    (`device_identity.clean_imei`);
  - un dato ausente es nulo, nunca `N/A`;
  - `public_id` es un UUID para la web.
- **Público:** la web sólo ve `public_id` y un IMEI enmascarado (`device_catalog.mask_imei`, `imei_hint`).
  El IMEI completo sólo sale a `inventory.view` y a la caja (`unit_identification.describe`).
- `battery_health` nulo significa «no informado»; hay un check ≤ 100.
- `condition` ∈ {`new`, `open_box`, `refurbished`, `used`}, más `Product.condition_line` (nuevo o seminuevo),
  `cosmetic_grade` y `sale_price` (sólo seminuevos).
- **Capacidad, color, SIM y región no son campos**: son `VariantAttribute` y `VariantOption` de cada empresa.
  Si una empresa no definió «SIM», no hay SIM que imprimir.

### 2.3 Caja: cómo se resuelve lo escaneado

`pos/page.tsx::handleScan` sigue este orden:
1. `posLookup` → `pos_views::AdminPosLookupView`: busca un `ProductBarcode` exacto de la empresa, activo y de
   un producto activo. Otra empresa responde 404.
2. Si no es un producto, `posUnitLookup` → `unit_identification::sellable_unit` → `find_unit`: 15 dígitos se
   tratan como IMEI (Luhn, `imei` o `imei2`); si no, como serie; una serie que existe en dos productos se
   rechaza como ambigua.
3. Escrito a mano, un equipo con IMEI **sólo** se acepta por su IMEI (`_typed_by_hand_is_an_imei`).

La entrega de pedidos usa el mismo `find_unit` (`candidate_for_order`); las rutas v1 están en `v1_pos_views`.
**Un IMEI 1 escaneado ya resuelve hoy a la unidad correcta**, siempre que ningún `ProductBarcode` tenga ese
mismo texto, porque la caja busca primero productos.

### 2.4 Impresión — `printing/`, `print_agent/`, `Printer`, `PrintAgent`, `PrintJob`

- **`Printer`:** por sucursal, sólo `connection=network` (puerto 9100), `paper_width_mm=80`, `encoding` y
  `auto_print`, con **una automática por sucursal**. No tiene rol (ticket o etiqueta), lenguaje, dpi ni medio.
- **`PrintAgent`:** un token por sucursal (`bdpa_…`; en la base sólo su huella).
- **`PrintJob`:**
  - `kind` ∈ {`fiscal_ticket`, `sales_note_ticket`}, y **`order` es obligatorio** (FK `PROTECT` no nula);
  - `idempotency_key` única por empresa (`auto:fiscal:<id>`, `auto:note:<id>`, `manual:<clave>`);
  - un `uid` estable entre bases;
  - estado `pending` → `printing` → `printed` | `failed` | `cancelled`;
  - lease de 90 s, 5 intentos con esperas de 10, 30, 60 y 120 s, y caducidad a las 12 h.
- **Protocolo:**
  - el agente **tira** por HTTPS (`/api/v1/print-agent/jobs/claim/`, `…/<id>/result/`);
  - el servidor genera los bytes **ESC/POS** al entregar (`printing.render`);
  - el agente los envía por TCP sólo a una dirección **privada** y lleva un diario para no imprimir dos veces;
  - confirma con el `claim_token` vigente.
- **Lo que de verdad confirma:** `printed` significa «se escribieron los bytes en el socket». No hay
  confirmación física. No sabe ZPL, TSPL, EPL ni detectar el hueco entre etiquetas.
- **Encolado:** `sales_note_services::_queue_ticket` y `fiscal_services` crean el trabajo **dentro de la misma
  transacción, en un punto de guardado propio**. No usan `on_commit`.
- **PR #120 (PRINT-THERMAL-01), abierto y sin fusionar**, con una migración `0124` que ya no encaja:
  - añade el transporte `cups_raw` (USB por CUPS);
  - bloquea PDF y PostScript hacia la térmica;
  - añade el resultado `queued_not_printed`;
  - registra que la térmica de tickets del propietario es USB en macOS.
- **Panel:** `admin/settings/printing/PrintingSettings.tsx`. Configurar exige `company.manage`; operar,
  `sales.pos.use` o `sales.orders.view`.

### 2.5 Eventos autoritativos y sus guardas

| Evento | Dónde | Guarda contra repetición |
|---|---|---|
| Alta guiada (producto + equipos o stock) | `registration_views` → `registration_services::register` | `Idempotency-Key` + huella; repetirla devuelve el registro anterior. `dry_run` deshace todo |
| Carga de equipos aplicada | `unit_import_services::apply_units` | `select_for_update` del trabajo; si ya está `applied`, devuelve `(job, False)` |
| Carga de stock aplicada | `stock_import_services::apply_stock` | igual. Es una **conciliación**, no una recepción |
| Stock inicial, compra, entrada o corrección positiva | `inventory_services::apply_initial_stock`, `apply_manual_stock_movement` | un movimiento de Kardex único |
| Recepción al pase de un proveedor | `supply_services::receive_line` | entra y **sale con el pedido** en la misma transacción |
| Lista semanal y ofertas del proveedor | `supplier_offer_import`, `supply_services` | no mueven stock ni `Product.price` (verificado) |

Todo pasa por `inventory_services::create_stock_movement`. En el proyecto, `on_commit` sólo se usa para
efectos **externos** (correo, WhatsApp, conversiones).

### 2.6 Imágenes

`image_import_services::ImageCodes` casa cada imagen con **cualquier** `ProductBarcode` de la empresa, activo
o no, comparando **sin distinguir mayúsculas**; si dos productos comparten la clave, la imagen no se asigna.
Las fotos públicas (`ProductImage`, `UnitPhoto`) y las evidencias privadas comparten almacén, nunca autorización.

### 2.7 Logotipo

`CompanySettings.document_logo_url` es una imagen que la empresa sube desde el panel.
`fiscal_logo::current_png(company)` la devuelve aplanada sobre blanco, o `None` sin detener la impresión, y
`escpos` ya la imprime a 1 bit. **Ningún logotipo está en el código.** El `marca.json` es sólo de los correos.

### 2.8 Librerías ya presentes (`backend/requirements.txt`)

- `reportlab==5.0.1`, ya usado con páginas en mm (`ticket_services`).
- `reportlab.graphics.barcode` dibuja Code 128, EAN-13, UPC-A y QR en PDF y SVG. **No** dibuja PNG: `renderPM`
  necesita `rlPyCairo`, que no está (probado en local).
- `Code128.decompose()` da los anchos de barra exactos para pintarlos con `Pillow` (presente) si hiciera falta.
- **No hace falta ninguna dependencia nueva.**

### 2.9 Permisos

Hay `products.manage`, `inventory.view`, `inventory.adjust` y `costs.view`, este último aparte y no implícito.
No hay ninguno de etiquetas ni de datos sensibles impresos. Las capacidades nuevas llegan a los preajustes con
una migración de datos (patrón `0126_costs_view_capability`).

### 2.10 La carga actual, vista para LABEL-MIGRATION-01

- **Formato:** **sólo `.xlsx`** (`xlsx_reader`: se rechazan `.xlsm`, `.xls` y CSV). Un CSV no está validado hoy.
- **Mapeo:**
  - perfiles por **firma de cabeceras** (`import_formats::PRESETS`), sin depender de qué empresa sube el
    archivo; ya hay un perfil para el export del POS anterior (`ID / CODIGO / CODIGO EAN / NOMBRE`);
  - mapeo editable y recordado;
  - campos de producto: `name`, `code`, `barcode`, `price`, `description`, `category`, `identifier`,
    `model`, `capacity`, `colour`, `line`, `destination`, `reference_cost` e imágenes;
  - **no hay** marca, SKU antiguo ni id externo en productos; `external_id` existe sólo en la carga de
    stock, que lo muestra y no lo guarda.
- **Ceros y texto:** el lector informa qué celdas eran numéricas (`numeric_columns`).
  `barcode_warnings` avisa, **sin reponer nunca un dígito**, cuando una celda numérica de 7, 11 o 12 dígitos
  puede haber perdido un cero.
- **Identidad:** `CatalogueIndex.match` reconoce la fila por el identificador, después por sus códigos
  (activos o no, sin reactivar los inactivos) y por último por un nombre único. Si el identificador y los
  códigos apuntan a dos productos, la fila es un error. Un nombre repetido pide código.
  - **Hueco:** una fila reconocida por código **cambia el nombre** del producto sin marcarlo para revisión.
- **Vista previa y aplicación:**
  - la vista previa clasifica `create`, `update`, `no_change`, `skip` y `error`, con avisos;
  - detecta duplicados dentro del archivo (`seen_barcodes`) y la forma inválida de un código
    (`_barcode_shape_errors`);
  - `apply_products` es todo o nada e idempotente: un trabajo `applied` no se reaplica;
  - **la carga de productos nunca mueve stock** (IMPORT-SPLIT-01).

### 2.11 La impresora Advance ADV-9010N

Sólo se encontraron fichas de tiendas peruanas; **no hay documentación del fabricante ni manual de
programación públicos**. Lo que dicen las tiendas, sin verificar:
- térmica directa, hasta 127 mm/s;
- papel de 58 y 80 mm;
- USB y Bluetooth, para Windows (una ficha añade Linux);
- códigos 1D/2D y QR.

Se vende a la vez como «ticketera» y como «de etiquetas». **No consta**:
- la resolución (dpi);
- el lenguaje de órdenes (TSPL, ZPL, EPL, ESC/POS en modo etiqueta);
- el sensor de hueco o de marca negra;
- un controlador para macOS;
- una interfaz de red.

Consecuencia: **no se conecta por la red local**, que es el único transporte del agente actual.

## 3. Mapa de reutilización

| Necesidad | Se reutiliza | Se añade |
|---|---|---|
| Código de producto | `ProductBarcode` (canónico, sin copia) | política única de alta y guardas cruzadas |
| Asignador | `InternalSequence` + `allocate` + `ensure_company_sequence` | `document_type='product_code'` y `allocate_many` |
| Importación histórica | carga de productos (perfiles, mapeo, vista previa, `apply_products`, avisos de ceros) | campos de SKU antiguo, id externo y marca, más una clasificación de revisión |
| Unidad | `StockUnit` + `receive_units` (escritor único) | `label_code` |
| Símbolo de la etiqueta del equipo | IMEI 1 ya validado y único; `find_unit` ya lo resuelve | regla de elección y perfil por empresa |
| Imágenes por código | `ImageCodes` | nada |
| Logotipo | `fiscal_logo.current_png` | nada |
| PDF y códigos | `reportlab` | módulo de dibujo en mm |
| Cola | `PrintAgent`, lease, `claim_token`, diario, idempotencia, red privada | tipos de trabajo de etiqueta, rol, lenguaje y transporte USB |
| Encolado | punto de guardado en la transacción (`_queue_ticket`) | lo mismo para etiquetas |
| Auditoría | `AdminAuditLog.log(…, company=…)` | acciones nuevas |

## 4. Brechas

1. El alta simple, el alta guiada sin `sku` y la carga sin códigos dejan productos sin código.
2. Nada impide guardar un IMEI o una serie de unidad como código de producto, y ese código le quitaría el
   escaneo al equipo.
3. El panel no muestra, ni copia, ni descarga los códigos.
4. La carga no tiene SKU antiguo, id externo ni marca, y no marca para revisión el cambio de nombre al
   reconocer una fila por código.
5. No hay un identificador interno de unidad corto y estable para los equipos sin IMEI ni serie útil.
6. No hay plantillas, ni registro de lo impreso, ni precio impreso, ni lista de etiquetas obsoletas.
7. `PrintJob` exige un pedido y sólo conoce tickets. `Printer` sólo va por red, no distingue rol y admite una
   sola impresora automática por sucursal.
8. `printed` no significa impreso.
9. No hay política de impresión automática, ni capacidades de etiquetas, ni adaptador para la ADV-9010N.

## 5. Decisiones

### D1 · Política única de códigos de producto — PROPUESTA (LABEL-01)

**Una sola función**, `product_codes.ensure_code(product, *, supplied=(), actor, request)`, llamada dentro
de la transacción de **cada** alta, sigue este orden:
1. Normaliza cada código **dado** (`normalize_barcode`, sin tocar mayúsculas ni ceros) y lo valida
   (`validate_barcode`).
2. Si un código dado ya es de **otro** producto de la empresa, activo o no, **rechaza el alta** con un 409 que
   nombra a ese producto (`code='code_conflict'`). Nunca genera otro código para salir del paso. Si ya es de
   este mismo producto, lo reutiliza sin cambios.
3. Si un código dado coincide con el `imei`, `imei2`, `serial_number` o `label_code` de una unidad de la
   empresa, lo rechaza: el IMEI identifica un equipo, nunca un modelo (decisión 3 del propietario).
4. Guarda los códigos dados que falten. Nunca sobrescribe ni desactiva uno existente, y el primario sólo se
   asigna si el producto no tenía ninguno.
5. **Sólo si no se dio ningún código y el producto no tiene ninguno asociado** (activo o no), genera uno. Si el
   producto sólo tiene códigos inactivos no se genera nada: su identidad es de quien la desactivó.

No se llama al editar, al imprimir ni al cambiar el precio: **un producto recibe un código generado una sola
vez en su vida**.

**Formato del generado:** `<prefijo><número con relleno>`, p. ej. `P000123`, y sólo con `A–Z0–9`:
- sin minúsculas, porque las imágenes se comparan sin mayúsculas y Bloq Mayús las invierte en el lector;
- sin `-`, `_` ni `/`, porque un lector configurado con otra distribución de teclado cambia esos signos.

El prefijo es de la empresa y lleva **al menos una letra**, así que un código generado nunca es un GTIN
válido y nunca se le llama EAN. Relleno de 6 por defecto. La semilla es neutra y se escribe en la fila de la
empresa al crearla (`ensure_company_sequence`); el piloto no aparece en el código.

**Asignador:** `InternalSequence` con `document_type='product_code'`, siempre a nivel de empresa, y `allocate`
bajo `select_for_update` de esa única fila.
- Cada número tomado se compara sin mayúsculas contra **todos** los `ProductBarcode` de la empresa y contra
  los identificadores de unidad. Uno ocupado, por ejemplo un `C000700` importado, se gasta y se pasa al
  siguiente, hasta un tope de 1 000 que da un error claro.
- `unique_barcode_per_company` es la última barrera.
- `allocate_many` reserva un bloque con un solo bloqueo para las cargas.
- Un número consumido no vuelve: un hueco es más barato que un duplicado.
- **Concurrencia:** se bloquea la fila del producto antes que la de la secuencia, siempre en ese orden.

**Dígito de control:** ninguno. Code 128 lleva el suyo obligatorio y se conserva la forma `C000001` ya impresa.

**Reintentos:**
- repetir el alta guiada devuelve el registro anterior y no vuelve a correr;
- `dry_run` deshace todo y no consume el número;
- una carga `applied` no se reaplica.

**Alternativas descartadas:**
- `MAX(code)+1`: es el defecto que `sequences.py` ya corrigió;
- una secuencia de PostgreSQL: no admite prefijo por empresa;
- un código aleatorio: lo escribe y lo lee una persona;
- EAN-13 con prefijo 20–29: choca con las balanzas y no es Code 128.

**Caminos que la llaman:**
- `admin_views` (alta simple) y `registration_services` (en lugar de `_barcode`), en LABEL-01;
- `import_services` (filas nuevas), en LABEL-MIGRATION-01, que es la fase dueña de ese archivo.

**Herramientas:**
- `POST /api/admin/products/<pk>/barcodes/generate/` (`products.manage`, `Idempotency-Key`): genera sólo si
  falta;
- `assign_internal_codes --company <slug> [--apply]`: rellena en bloque. **No se ejecuta en el piloto hasta
  cerrar LABEL-MIGRATION-01.**

### D2 · Códigos del fabricante y choques — PROPUESTA (LABEL-01)

- Un EAN, un UPC o cualquier código previo se guarda tal cual. Su simbología sólo se nombra si el dígito de
  control cuadra; si no, queda `unknown`. Un código interno nunca se presenta como GTIN.
- Un producto **con** código previo no recibe uno generado (D1.5), salvo que el propietario decida otra cosa
  (P4).
- En las altas manuales (`AdminProductBarcodeView.post`, alta guiada) se aplican las mismas reglas 2 y 3 de D1.
- **Mayúsculas:** se conservan. Un código que sólo difiere en mayúsculas de otro de la empresa se acepta, porque
  la base lo distingue, pero se **marca para revisión**: las imágenes y algunos lectores no lo distinguen.

### D3 · Productos desactivados — PROPUESTA

Nada cambia: sus códigos se quedan en la tabla, la unicidad los cubre y la caja no los encuentra. El
asignador es monotónico, así que nunca vuelve a emitir un número. No hace falta un libro de códigos emitidos:
ningún camino de la API borra productos ni códigos.

### D4 · Identidad física y símbolo de la etiqueta del equipo — PROPUESTA (LABEL-02; el ADR que se exige antes de cualquier modelo nuevo)

Tres cosas que no se mezclan:
- el **código comercial**, que es el `ProductBarcode` del modelo o variante;
- la **identidad de la unidad**, que es la fila de `StockUnit`;
- el **valor escaneable impreso**, que es uno de los identificadores de esa fila.

**Identificador interno estable:** un campo **`StockUnit.label_code`**, no un modelo nuevo.
- `CharField(24)` nulo, con `UniqueConstraint(company, label_code)` condicional.
- Lo asigna `receive_units` en la misma transacción, a **todas** las unidades.
- Formato `<prefijo><9 dígitos aleatorios><dígito Luhn>`, p. ej. `U4827193056`. Aleatorio, para no revelar
  cuántos equipos entran, y no se deriva del IMEI ni de la serie.
- Si dos chocan, se reintenta en un punto de guardado, hasta 5 veces. No hay ninguna fila compartida que
  bloquear.
- El prefijo es de la empresa, **distinto** del de productos, en `CompanyLabelSettings`.
- Las unidades que ya existen se rellenan con `assign_unit_codes` (en modo de prueba por defecto), después
  del despliegue.
- Un equipo que vuelve conserva su fila y su código.

Alternativas descartadas:
- usar `public_id`: tiene 36 caracteres (unos 70 mm en Code 128) y ya es público en la web;
- un modelo `UnitIdentifier`: nadie pidió historial de pegatinas, y lo impreso queda registrado en D8.

**Qué símbolo lleva la etiqueta** (`labels.unit_symbol(unit, profile)`):
1. Con el perfil `imei1` (el de este propietario) y un `imei` válido: **el IMEI 1**. Si hay dos, se imprime el
   primero; ambos siguen en la fila.
2. Si no, la **serie**, siempre que sea apta: sólo `A–Z0–9`, única en **toda** la empresa (no sólo en su
   producto), sin coincidir con ningún `ProductBarcode` y cabiendo en el ancho a módulo legible.
3. Si no, el **`label_code`**.

Con el perfil `internal`, el que trae cualquier otra empresa por defecto, la etiqueta comercial lleva siempre
el `label_code` y ningún IMEI.

**En caja:** `find_unit` gana una primera rama para la forma de un `label_code` con Luhn válido, y después
sigue como hoy, con el IMEI y la serie. El IMEI 1 ya funciona sin cambios. Para que la búsqueda de producto
no le robe el escaneo:
- D1.3 impide que un IMEI o una serie se guarden como código de producto;
- `receive_units` **rechaza** un IMEI o una serie que ya sea un `ProductBarcode` de la empresa;
- un comando de sólo lectura lista los choques que ya existan en los datos.

Lo que se escribe a mano no cambia: un equipo con IMEI se identifica por su IMEI (P6).

**El IMEI nunca sale en un endpoint público**: sigue sólo `imei_hint` enmascarado.

### D5 · Plantillas y diseños — PROPUESTA (LABEL-03)

**`LabelTemplate`:**
- `company` y `branch` (nulo = toda la empresa);
- `purpose` ∈ {`accessory`, `device_commercial`, `device_technical`} y `name`;
- `width_mm`, `height_mm` y márgenes, en décimas de mm;
- `orientation`, `dpi`, `density` y `min_module_dots`;
- `fields`: lista ordenada de `{key, size, label}`;
- `is_default` (único por empresa, sucursal y propósito), `version` e `is_active`.

**Los campos salen de un registro cerrado en código**, distinto por propósito. Costo, margen, proveedor y
cliente **no existen** en él. Las claves técnicas (`imei2`, `serial`, `grade`, `region`) sólo se admiten en
`device_technical`. El IMEI 1 como símbolo se admite en la comercial sólo con el perfil `imei1`.

**Diseños confirmados por el propietario**, que son filas de **su** empresa (no semillas globales):

| Propósito | Medida | Campos, en orden |
|---|---|---|
| `accessory` | 30×20 | `logo`, `short_name`, `variant` (sólo si aplica y cabe), `price`, `barcode`, `code_text` |
| `device_commercial` | 50×25 | `logo`, `model`, `attribute:Capacidad`, `attribute:Color`, `attribute:SIM` (sólo si se conoce), `condition`, `price`, `unit_barcode`, `unit_code_text`, `battery` |
| `device_technical` | por definir (P10) | IMEI 2, serie, región, grado y batería; o un QR o código hacia la **ficha autenticada** del equipo por su `label_code` |

Los demás tamaños (40×30, 50×30, 60×40, 70×50) son sugerencias editables del panel; ninguna medida está en el
motor.

**Reglas de contenido:**
- **Nunca se inventa.** Un dato vacío no se imprime ni se rellena, y un atributo que la empresa no tiene no
  existe.
- **Batería** sólo si el equipo es **usado** (`Product.condition_line = used`; para `refurbished` y
  `open_box`, ver P7) **y** `battery_health` no es nulo. Un usado sin medición muestra «Batería no informada»
  si cabe; si no, la etiqueta queda **marcada para revisión** y no sale sola. Un equipo nuevo no lleva batería.
  Nunca se supone el 100 %.
- **Precio:** el comercial vigente (`Product.price`, o `StockUnit.sale_price` en un seminuevo con precio
  propio). Un seminuevo sin precio queda marcado para revisión (P7). Ningún costo.
- **Nunca se encoge un código por debajo de lo legible ni se recorta un texto en silencio.** El dibujo calcula
  el ancho a `min_module_dots` del dpi y se **niega** si no cabe («No cabe en 30×20: …»).

**Ancho de Code 128 con sus zonas silenciosas** (0,25 mm por módulo, 2 puntos a 203 dpi; a 300 dpi con
3 puntos es casi lo mismo):

| Código | Ancho | ¿Cabe? |
|---|---|---|
| `C000701` | 27,5 mm | entra en 30 mm sólo con márgenes ≤ 1,2 mm |
| 12 dígitos (los internos del piloto) | 30,2 mm | **no** entra en 30 mm |
| EAN-13 dibujado como Code 128 | 35,8 mm | no entra |
| EAN-13 en su propia simbología | ≈ 28,3 mm | entra justo |
| IMEI de 15 dígitos | 38,5 mm | **entra en 50 mm** |
| Serie alfanumérica de 12 | ≈ 47 mm | no entra en 50 mm con 2 mm de margen: se usa `label_code` (33 mm) |

**Qué código lleva el accesorio:** el primario si cabe; si no, el **más corto** de sus códigos activos que
quepa (EAN-13 y UPC-A válidos en su simbología; el resto en Code 128). Si ninguno cabe, se marca para revisión.
Nunca se genera un código nuevo para que quepa (decisión 1.8).

### D6 · Configuración, perfiles y permisos — PROPUESTA

**`CompanyLabelSettings`** (LABEL-02), uno a uno con la empresa:
- `unit_code_prefix`;
- `device_symbol` ∈ {`internal`, `imei1`}, `internal` por defecto (el piloto elige `imei1`);
- `allow_imei_on_commercial`, que va con `imei1`.

**`BranchLabelPolicy`** (LABEL-05), uno a uno con la sucursal:
- `auto_units`, `auto_accessories` y `auto_positive_adjustments`, **todos apagados**;
- impresora y plantillas por defecto.

**Capacidades nuevas:**
- `inventory.labels.print`: etiquetas comerciales sin IMEI, en las sucursales que alcanza.
- `inventory.labels.sensitive`: cualquier etiqueta que **muestre un IMEI o una serie**, incluida la comercial
  con perfil `imei1`, y la técnica. No la implica ninguna otra capacidad. Cada lote deja
  `AdminAuditLog('label_sensitive_printed')` con los ids de las unidades, nunca los IMEI, y su PDF va con
  `no-store`.

**Capacidades que ya existen:**
- generar códigos de producto: `products.manage`;
- `label_code`: lo asigna `receive_units`, que ya exige `inventory.adjust`;
- plantillas, impresoras, perfil y política: `company.manage`.

Los preajustes se reparten con una migración de datos; qué roles reciben qué lo decide el propietario (P11).

### D7 · Impresora y cola: `PrintJob` ampliado — PROPUESTA (LABEL-04; el transporte y el lenguaje están `BLOCKED: HARDWARE`)

- **`PrintJob`:**
  - `order` pasa a ser nulo (en PostgreSQL sólo cambia el catálogo);
  - se añade `label_batch` (FK a D8);
  - `kind` gana `label_accessory`, `label_device_commercial` y `label_device_technical`;
  - un `CheckConstraint` exige que un ticket tenga pedido y una etiqueta tenga lote.
- **`Printer`:**
  - `role` ∈ {`ticket`, `label`}; las filas que ya existen quedan `ticket`;
  - `language` y `transport` ∈ {`tcp`, `usb_raw`, `os_driver`, …}, que sólo se habilitan **los que se
    verifiquen**;
  - `dpi` y medidas del medio;
  - la restricción de una impresora automática pasa a ser por **(sucursal, rol)**;
  - una de rol `ticket` nunca recibe una etiqueta, y al revés; se comprueba al encolar y al entregar.
- **Confirmación:** campo `confirmation` ∈ {`none`, `sent`, `spooled`, `spooler_done`, `device_ok`,
  `operator_ok`, `operator_missing`, `unknown`}.
  - El panel dice «Impreso» sólo con `device_ok` u `operator_ok`; con cualquier otro, «Enviado».
  - Una descarga en PDF no confirma nada.
  - El nivel que da la ADV-9010N se mide con el equipo. Se alinea con `queued_not_printed` de #120.
- Las etiquetas caducan a las 12 h, como los tickets, pero quedan visibles en «Pendientes» para reimprimirlas.
- **Agente:**
  - la ADV-9010N es USB o Bluetooth, así que el TCP 9100 actual no le sirve;
  - **antes de tocar `print_agent/` va un análisis de compatibilidad**: qué cambia en el camino de tickets,
    si #120 se fusiona, se rehace o se cierra, y si conviene una segunda instancia del mismo agente con su
    propio token y configuración, sin cambiar el comportamiento de los tickets;
  - los tests de tickets son la guardia;
  - nunca un puerto abierto a internet ni el servidor conectándose a una impresora local.
- **Alternativa descartada:** un `LabelJob` con su propio reclamo, que duplica el lease, el diario y la
  idempotencia.

### D8 · Registro de lo impreso — PROPUESTA (LABEL-03)

- **`LabelBatch`:**
  - empresa, sucursal y propósito;
  - plantilla y su copia congelada;
  - `reason` ∈ {`auto`, `manual`, `reprint`} y `channel` ∈ {`pdf`, `printer`};
  - `source_key`, único por empresa;
  - quién lo pidió.
- **`LabelBatchLine`:** producto o unidad, `copies`, `code_printed`, `symbology_printed` y `price_printed`.

**Reimprimir** usa los códigos vigentes y **nunca genera ni cambia un código**: si `code_printed` ya no
existe, la línea se marca para revisión.

**Obsoletas:** la última línea con existencias en la sucursal cuyo `price_printed` difiere del precio
comercial vigente. Se listan y se ofrece reimprimir; nunca se reimprime solo. Los cambios de costo
(`SupplierOffer`, `reference_cost`, listas semanales) no tocan `Product.price`, así que nunca aparecen.

**Sin existencias propias en la sucursal no hay etiqueta.** Eso cubre la oferta al pase y `receive_line`, cuya
mercadería sale con el pedido.

### D9 · Impresión automática — PROPUESTA (LABEL-05)

- **Cuándo:** sólo tras una **entrada física confirmada** (decisión 6):
  - **equipos:** una etiqueta por `StockUnit` recibida (`register` con equipos, `apply_units` sólo en su
    primera aplicación);
  - **accesorios:** `copies` = cantidad recibida (`register` con stock, `apply_initial_stock` y
    `PURCHASE_ENTRY`; `MANUAL_ENTRY` y `CORRECTION_POSITIVE` sólo si la política de la sucursal lo permite).
- **Nunca:**
  - `dry_run`, vistas previas, cargas rechazadas;
  - `apply_stock` (es conciliación), traslados, devoluciones, `receive_line`;
  - listas semanales, ofertas, cambios de costo o de precio;
  - reintentos de algo ya aplicado.
- **Cómo:** `LabelBatch` + `PrintJob` **dentro de la transacción del evento, en un punto de guardado propio**,
  como `_queue_ticket`. Así una unidad deshecha nunca tiene trabajo. `on_commit` añadiría una ventana para
  perder el trabajo después del commit, y aquí no hay ningún efecto externo que lo justifique.
- **Idempotencia:** clave `auto:label:movement:<StockMovement.pk>`, porque cada recepción escribe un movimiento
  de Kardex único. `source_key` e `idempotency_key` son únicos por empresa, así que no hay dos trabajos
  automáticos para la misma recepción.
- **Llamadas explícitas** en cada servicio, con un test que fija la lista permitida, y no dentro de
  `create_stock_movement`, que dispararía también en caminos futuros sin que nadie lo decida.
- **Sin impresora o con la impresora apagada:** el stock sigue confirmado y el lote queda pendiente o en error.
  Se reintenta sin duplicar, y «Pendientes de etiqueta» es una lista **derivada** de unidades y entradas sin
  lote, para que quien opera vea qué falta.

### D10 · PDF y Code 128 — PROPUESTA (LABEL-03)

- **PDF a escala real** con `reportlab`, con la página del tamaño de la etiqueta, y códigos vectoriales de
  `reportlab.graphics.barcode` (Code 128, EAN-13, UPC-A). Sin dependencias nuevas.
- **Se descarga; nunca se imprime solo.** Es la vía de respaldo y la de LABEL-03, antes de tener la térmica.
- **SVG** para ver, copiar y descargar un código; PNG no, porque `renderPM` no está.
- **Térmica:** primero las órdenes nativas del lenguaje que tenga la impresora. Si hiciera falta un mapa de
  bits, las barras salen de `Code128.decompose()` a puntos enteros con Pillow, y el logotipo a 1 bit como hoy.
- **El dibujo vive en un paquete puro `backend/store/labels/`** que recibe un contrato `LabelData` y no lee
  modelos, así se prueba sin base de datos.

### D11 · Adaptador de la ADV-9010N — BLOCKED: HARDWARE (LABEL-04)

Antes de escribir código, con el equipo delante:
- dpi, lenguaje de órdenes (por ejemplo la página de autoprueba o la configuración del controlador) y sensor
  de hueco;
- calibración con los rollos de 30×20 y 50×25;
- cuántas etiquetas por fila trae el rollo;
- conexión (USB o Bluetooth) y sistema del equipo anfitrión;
- si hay controlador para macOS;
- qué confirmación devuelve.

Con eso se elige el adaptador: lenguaje nativo, cola cruda o controlador del sistema (nunca «Generic
PostScript», lección de #120). Mientras tanto, los PDF a escala real permiten imprimir con el controlador del
fabricante y validar los diseños.

### D12 · Importación histórica (LABEL-MIGRATION-01) — PROPUESTA; la aplicación con datos reales está `BLOCKED: ARCHIVO REAL`

**Reutiliza la carga de productos** (`import_services`, `import_formats`, `import_views`, `ImportWizard`):
- **Formato:** **`.xlsx`**, que es lo que el sistema valida hoy. Un CSV no se acepta: tendría que tener su
  propio lector y no cambia el riesgo de los ceros.
- **Perfil** «Base histórica» por firma de cabeceras, y siempre un paso de **revisión de columnas**: no se
  supone que todas existan.

| Columna propuesta | Va a |
|---|---|
| Código de barras original | `barcode`: `ProductBarcode` con simbología detectada o `unknown` |
| Código interno original | `code`: `ProductBarcode` `internal` |
| SKU antiguo | `legacy_code` (nuevo): otro `ProductBarcode` `unknown`, si es válido; si no, va al informe (P12) |
| Nombre, categoría, modelo, variante, precio comercial | campos que ya existen |
| Marca | **no hay campo**: la columna se marca «ignorada» (P12) |
| Id externo | `external_id` (nuevo en productos): se lleva en el trabajo y en el informe de conciliación; **no** se guarda en `Product` sin decisión (P12) |
| Stock | **no**: la carga de productos no mueve stock. Si se decide, va por la carga de stock aparte |

- **Conservación:**
  - todo es texto: ceros, mayúsculas, longitud y caracteres alfanuméricos se respetan;
  - la simbología sólo se nombra con su validador;
  - **cualquier** celda numérica que llegue a un código se marca «revisar», no sólo las de 7, 11 o 12 dígitos;
  - una celda numérica de más de 15 dígitos, o en notación científica, se **rechaza**: Excel ya perdió dígitos;
  - nunca se reconstruye un código.
- **Vista previa** (no escribe nada), que extiende la actual con estas categorías:
  - coincide con el catálogo / producto nuevo;
  - códigos que se conservan / que ya existían en el mismo producto;
  - duplicados dentro del archivo;
  - **conflictos** (el código es de otro producto, o el identificador y el código no apuntan al mismo);
  - **ambiguas** (nombre repetido);
  - **rechazadas** (forma inválida, más de 15 dígitos);
  - **a revisar** (celda numérica, código que difiere sólo en mayúsculas de otro, o fila reconocida por código
    cuyo **nombre difiere**, que hoy se renombra sin avisar).
- **Aplicación:** la aprueba el usuario. Si hay filas a revisar, exige una confirmación escrita con su número
  (patrón `REEMPLAZAR`). Es todo o nada (`apply_products`).
- **Idempotencia:** volver a subir el mismo archivo da `no_change` y no duplica productos ni códigos.
- **Seguridad:** nada automático en producción, nada de SQL directo, y ningún archivo, volcado ni dato real en
  git. Las pruebas usan datos ficticios.
- **Orden:** se hace antes de `assign_internal_codes` en el piloto.

## 6. Migraciones por fase

La numeración es provisional: #162 trae `0146` y #120 una `0124` que ya no encaja. Las fases se fusionan de
una en una y se renumeran.

| Fase | Migraciones | Despliegue |
|---|---|---|
| LABEL-01 | `InternalSequence.document_type` gana `product_code` (sólo cambia las opciones, sin SQL); la fila se crea al primer uso con `ensure_company_sequence` | aditiva; la vuelta atrás es sólo de código |
| LABEL-MIGRATION-01 | **ninguna** (los campos nuevos son del mapeo, no del modelo) salvo que el propietario pida guardar el id externo (P12) | — |
| LABEL-02 | `StockUnit.label_code` + unicidad condicional · `CompanyLabelSettings` · capacidades `inventory.labels.print` y `.sensitive` en los preajustes | `lock_timeout`; `assign_unit_codes` **después** del despliegue |
| LABEL-03 | `LabelTemplate` · `LabelBatch` + `LabelBatchLine` | tablas nuevas |
| LABEL-04 | `Printer`: rol, lenguaje, transporte, dpi, medio y automática por rol · `PrintJob`: `order` nulo, `label_batch`, tipos, `confirmation` y check | en dos pasos, como `0143`/`0144` |
| LABEL-05 | `BranchLabelPolicy` | apagada por defecto |

## 7. Fases, alcance de archivos y dependencias

| Fase | ¿Necesita la impresora? | ¿Necesita el archivo real? | Depende de |
|---|---|---|---|
| LABEL-01 · códigos sólo donde faltan | no | no | — |
| LABEL-MIGRATION-01 · importación histórica | no | **sólo para aplicar con datos reales** | — (en paralelo con LABEL-01) |
| LABEL-02 · identidad del equipo y reglas del IMEI | no | no | migración después de la de LABEL-01 |
| LABEL-03 · plantillas, vista previa, PDF, lotes | no (validar la escala impresa sí la pide) | no | el dibujo puro, ya; los modelos, tras LABEL-02 |
| LABEL-04 · adaptador ADV-9010N | **sí** | no | LABEL-03 y la decisión sobre #120 |
| LABEL-05 · cola automática | la salida física, sí | no | LABEL-01, 03 y 04 |
| LABEL-06 / 07 · obsoletas, masivo, aceptación | 07, sí | no | — |

**Archivos por fase**, para trabajar en worktrees paralelos sin tocar los mismos:

- **LABEL-01:**
  - `store/product_codes.py` (nuevo), `sequences.py`;
  - `admin_views.py` (alta) y `registration_services.py` (`_barcode` → `ensure_code`);
  - `pos_views.py` (`AdminProductBarcodeView`, `generate/`) y una ruta en `urls.py`;
  - `models.py` (sólo las opciones de `InternalSequence`) y su migración;
  - `management/commands/assign_internal_codes.py`, `test_product_codes.py`;
  - frontend: `admin/components/ProductCodesPanel.tsx` (nuevo) y `admin/lib/product-codes-api.ts` (nuevo).
- **LABEL-MIGRATION-01:**
  - `import_services.py`, que incluye la llamada a `ensure_code` en las filas nuevas;
  - `import_formats.py`, `import_exports.py` (plantilla), `import_views.py` y, si hace falta, `xlsx_reader.py`;
  - `test_label_migration_import.py` (datos ficticios);
  - frontend: `admin/products/import/page.tsx` e `ImportWizard.tsx`.
  - Sin modelos ni migraciones.
- **LABEL-02:**
  - `stock_unit_services.py` (`receive_units`), `unit_identification.py` (`find_unit`) y
    `store/unit_codes.py` (nuevo);
  - `models.py` (`StockUnit`, `CompanyLabelSettings`) y sus migraciones;
  - `management/commands/assign_unit_codes.py` y `audit_code_collisions.py`, `test_unit_codes.py`.
  - Hay **un único punto de contacto** con LABEL-01: `product_codes.py` llamará a `unit_codes.is_unit_identifier`.
    Se resuelve fusionando LABEL-01 primero; LABEL-01 ya compara con `imei` e `imei2`, que existen.
- **LABEL-03:**
  - `store/labels/` (nuevo: `fields.py`, `layout.py`, `code128.py`, `pdf.py`, `svg.py`, `data.py`,
    `symbols.py`), que puede empezar **ya**, sin modelos;
  - `store/label_views.py` (nuevo), rutas en `urls.py` en otro tramo, `models.py` (`LabelTemplate`,
    `LabelBatch`) después de LABEL-02;
  - `test_labels_render.py`, `test_label_batches.py`;
  - frontend: `admin/inventory/labels/**`, `admin/lib/labels-api.ts` y la entrada en `internal-modules.ts`.
- **LABEL-04:** `print_agent/**` (sólo tras el análisis de compatibilidad), `printing/services.py`,
  `printing/<adaptador>.py` (nuevo), `print_views.py`, `models.py` (`Printer`, `PrintJob`),
  `PrintingSettings.tsx`, `printing-api.ts`. Choca con los archivos de #120.
- **LABEL-05:** las llamadas en `registration_services.py`, `unit_import_services.py` e
  `inventory_services.py`, y `BranchLabelPolicy`. Va después de LABEL-01.

**Archivos compartidos:**
- `models.py` y la numeración de migraciones se fusionan en orden: LABEL-01 → LABEL-02 → LABEL-03 → LABEL-04.
- `urls.py`, `02_` y `07_` se tocan en tramos distintos y se fusionan de uno en uno.

## 8. Pruebas previstas

| Prueba | Fase |
|---|---|
| Un código escrito a mano se conserva tal cual (alta simple, alta guiada, API de códigos) y no se genera otro | 01 |
| Un código importado se conserva; un producto ya con código no recibe otro; varios códigos siguen todos asociados | 01 / MIG |
| Ceros a la izquierda: `0750…` como texto se guarda igual; una celda numérica se marca «revisar»; nunca se repone un dígito; más de 15 dígitos se rechaza | MIG |
| Mayúsculas: `abC1` se guarda `abC1`; uno que difiere sólo en mayúsculas se marca; el generado es siempre `A–Z0–9` | 01 / MIG |
| Volver a importar el mismo archivo: `no_change`, sin productos ni códigos duplicados; una segunda aplicación no hace nada | MIG |
| Choque entre productos: un código de otro producto da 409 con su nombre, nada se escribe y **no** se genera un código alternativo | 01 / MIG |
| Concurrencia: dos altas simultáneas sin código reciben códigos distintos y consecutivos; un reintento del mismo alta no consume un segundo número (`TransactionTestCase` con hilos, PostgreSQL) | 01 |
| `dry_run` no consume números; editar o cambiar el precio no genera código; un producto desactivado conserva el suyo y ningún otro lo recibe | 01 |
| IMEI frente a código de producto: un IMEI o una serie no se aceptan como `ProductBarcode`; `receive_units` rechaza un IMEI que ya es código de producto | 01 / 02 |
| Caja: el EAN resuelve al producto, el IMEI 1 y el IMEI 2 a su unidad, el `label_code` a su unidad, una serie ambigua pide IMEI; lo escrito a mano sigue la regla actual | 02 |
| Multiempresa: el mismo código, IMEI o `label_code` en otra empresa responde 404; los asignadores de dos empresas no se bloquean entre sí | 01 / 02 |
| Elección del símbolo: perfil `imei1` → IMEI 1; sin IMEI → serie apta; serie no apta → `label_code`; perfil `internal` → nunca un IMEI | 02 |
| Reimprimir no regenera: misma línea, mismo código, precio vigente; un código desaparecido marca la línea para revisión | 03 |
| Batería: usado con medición → «Batería N %»; usado sin medición → «Batería no informada» o marcado; nuevo → nunca batería; nunca 100 % supuesto | 03 |
| Ancho: `C000701` en 30×20 cabe a 2 puntos; un código de 12 dígitos en 30×20 se niega o usa otro código que quepa; IMEI en 50×25 cabe; nunca baja de `min_module_dots` | 03 |
| Etiqueta comercial sin costo, margen, proveedor ni cliente (el registro de campos lo impide); IMEI sólo con `inventory.labels.sensitive` y con auditoría | 03 |
| Cola automática: una recepción, un trabajo; repetir no duplica; un rollback no deja trabajo; la lista semanal, `apply_stock` y el `dry_run` no encolan; con la impresora apagada el stock queda confirmado | 05 |
| Una impresora de tickets nunca recibe una etiqueta, y al revés | 04 |

## 9. Riesgos

1. **30×20 es muy justo:** los códigos de 12 dígitos del piloto no caben en Code 128 a módulo legible. Hay que
   elegir otro código del producto o marcar la etiqueta, y probarlo con el lector real.
2. **La ADV-9010N no tiene documentación técnica pública:** lenguaje, dpi y sensor desconocidos; sólo USB o
   Bluetooth; controlador «para Windows». Puede no tener controlador para macOS.
3. **El agente de tickets:** un transporte USB lo toca; el análisis de compatibilidad y los tests de tickets
   son la guardia.
4. **Datos ya existentes:** un IMEI o una serie que ya figure como código de producto le quita el escaneo al
   equipo. Hay que revisarlo con `audit_code_collisions` antes de LABEL-02.
5. **Renombrado al importar:** una fila reconocida por código cambia el nombre sin revisión hasta
   LABEL-MIGRATION-01.
6. **Mayúsculas:** los códigos que difieren sólo en mayúsculas son distintos para la base e iguales para las
   imágenes y algunos lectores.
7. **«Impreso» que no se imprimió:** `confirmation` va separado del estado.
8. **El IMEI en papel:** perfil por empresa, permiso propio y auditoría; las demás empresas lo tienen apagado.
9. **Bloqueo de la fila de secuencia en cargas grandes:** `allocate_many` y transacciones cortas.
10. **#120 y #162** cambian la numeración de las migraciones.
11. **Térmica directa:** la etiqueta se borra con el calor o el sol si pasa meses en una caja expuesta (P2).

## 10. Preguntas al propietario

1. **ADV-9010N:** resolución (dpi), lenguaje (lo dice la página de autoprueba o el controlador), sensor de hueco
   o marca negra, cómo la conecta (USB o Bluetooth) y en qué equipo (¿Windows o el Mac de caja?). ¿El CD trae
   un controlador o un programa de etiquetas?
2. **Rollos:** ancho total del rollo, etiquetas por fila, hueco o marca negra, y si son térmicas directas.
3. **Prefijo de los códigos nuevos:** ¿se continúa la serie `C` del sistema anterior (a partir del último), con
   relleno de 6, o se usa otro prefijo?
4. ¿Se genera también un código interno a los productos que ya tienen un EAN/UPC? (Por defecto, no.)
5. **Prefijo del identificador interno de unidad**, por ejemplo `U`.
6. ¿Se acepta en caja escribir a mano un `label_code`, o un equipo con IMEI se sigue identificando a mano sólo
   por su IMEI?
7. ¿«Usado» incluye «reacondicionado» y «caja abierta» para mostrar la batería? Un seminuevo sin precio
   propio, ¿se imprime sin precio o queda en revisión?
8. **Etiqueta técnica:** ¿una segunda pegatina más grande, o un QR o código hacia la ficha autenticada del
   equipo?
9. ¿Qué eventos imprimen solos y en qué sucursales? ¿También los traslados recibidos?
10. Medidas de la etiqueta técnica, si se usa.
11. ¿Qué roles reciben `inventory.labels.print` e `inventory.labels.sensitive`?
12. **Base histórica:** ¿de qué sistema viene y en qué formato? ¿El SKU antiguo tiene que escanearse? ¿La marca
    y el id externo se guardan en el producto (eso exige un campo y su ADR) o basta el informe de conciliación?
13. ¿Hay un logotipo en negro sobre blanco, apto para 1 bit, en «Logotipo de documentos»?
14. ¿#120 (USB por CUPS) se fusiona, se rehace o se cierra?

---
Fuentes sobre la ADV-9010N (fichas de tiendas, sin documentación del fabricante):
[MercadoLibre](https://www.mercadolibre.com.pe/impresora-termica-de-etiquetas-advance-adv9010/up/MPEU2886280310),
[Gaming Power](https://gamingpower.pe/products/impresora-termica-etiquetas-advance-adv-9010-bluetooth-usb-58mm-80mm),
[Computer Movil](https://computermovil.com/electro-shop/impresoras/punto-de-venta-pos/impresora-termica-de-etiquetas-advance-adv-9010n/),
[GP Perú](https://gpperu.com/producto/pradv9010n/impresora-termica-de-etiquetas-advance-adv-9010-velocidad-127-mm-seg-usb-y-bt).
