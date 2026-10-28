# ADR — Borrador de importación desde una lista de WhatsApp revisada (INV-TEXT-03)

| | |
|---|---|
| **Estado** | Aceptada para esta fase (preparación). La escritura queda **fuera** y pide su propio ADR y el GO del propietario. |
| **Fecha** | 2026-10-22 |
| **Base** | `master` `f9f52de` (#142 INV-TEXT-02, #151 INV-SNAPSHOT-01) |
| **Relacionadas** | [adr-inv-snapshot-external-used.md](adr-inv-snapshot-external-used.md) (lista completa, seminuevos externos) |

## 1. Contexto: lo que el código hace hoy

| Pieza | Entrada | ¿Persiste algo en la vista previa? | Valida |
|---|---|---|---|
| Reconocedor local (#142) `inventory-text-preview.ts` | texto pegado | No: todo en la pestaña | líneas, costo, IMEI (Luhn), duplicados; cantidad siempre `null` |
| Conciliación (#142) `POST /api/admin/inventory/text-preview/match/` | modelo, capacidad, color, tipo, línea | No | variante real de la empresa (`device_catalog`) |
| Ofertas de proveedor `supplier_offer_import.preview` | **un .xlsx** con la plantilla oficial | **Sí**: un `BulkImportJob` (`SUPPLIER_OFFERS`, estado de vista previa) y sus `BulkImportRow`. Ninguna oferta, ningún código de proveedor, ningún stock | proveedor, variante (identificador, código, modelo+opciones), seminuevo ≠ bajo pedido, precio, cantidad (vacía / N / 0), vigencia, plazo, solapes, fila repetida, costo sólo con `costs.view`, lista completa (#151: ausentes, `looks_truncated`) |
| Equipos propios `unit_import_services.preview_units` | **un .xlsx** con la plantilla de equipos | **Sí**: `BulkImportJob` (`UNITS`) con **series e IMEI** en `normalized_data` | producto con serie, sucursal alcanzable, serie obligatoria, IMEI, unicidad contra la base |

La vista previa oficial crea un trabajo **aplicable**: con su id, `apply` escribe ofertas o recibe equipos
(con sus confirmaciones). No toca stock, pero deja un registro que aparece en el historial y que alguien
podría aplicar.

## 2. Decisión

**Un borrador = las filas de la plantilla oficial, validadas por el importador oficial sin dejar rastro.**
No hay importador nuevo ni validadores copiados.

### 2.1 Origen «oferta de proveedor»: ensayo en seco del preview oficial (opción a/c)

`POST /api/admin/inventory/text-preview/offer-draft/` (nuevo, sólo lectura):

1. Recibe JSON: proveedor (id), vigencia, plazo, modo (parcial por defecto, o lista completa) y filas
   `{key, product_id, commercial_price, confirmed_quantity, agreed_cost?}`. Campos fuera de esa lista se
   rechazan (400): nunca texto pegado, IMEI ni serie.
2. Resuelve en el servidor la empresa (`_company_context`, `products.manage`, igual que el preview oficial),
   el proveedor y cada `product_id` **dentro de la empresa**; uno ajeno o inexistente responde **404**.
   Lista completa: exige alcance de toda la empresa (403), como el preview oficial.
3. Construye en memoria un .xlsx con las **cabeceras oficiales** (`supplier_offer_import.HEADERS` +
   «Identificador») y llama a **`supplier_offer_import.preview`** tal cual, dentro de
   `transaction.atomic()` que **siempre se revierte**. La respuesta es `job_payload` sin `id` (el trabajo ya
   no existe): conteos, filas con errores y avisos, cambios de precio, ausentes de la lista completa.
4. `costs.view` gobierna el costo como en el preview oficial: sin él la columna no se escribe y la
   respuesta no lo trae.
5. Sin IA: no se llama a `inventory_ai` (el preview oficial sólo informa su decisión en la vista).

¿Por qué no llamar directo al preview oficial? Porque dejaría un trabajo aplicable y una fila en el
historial por cada «Generar borrador». Con la reversión el resultado es el del importador oficial, con
sus mismas reglas, y no queda nada. Coste conocido: cada ensayo consume valores de las secuencias de
`BulkImportJob` y `BulkImportRow` (PostgreSQL no devuelve un `nextval` al revertir), así que quedan huecos en
sus id. No es un dato ni un rastro del borrador: nada identifica a quién ensayó ni qué filas.

¿Por qué no refactorizar `preview()` para aceptar filas? Cambiaría el contrato del importador (#151) en
una fase sin escritura. Generar el .xlsx en memoria usa la misma puerta (`xlsx_reader.check_upload`,
límites, fórmulas) que un archivo subido.

Además, **«Descargar borrador (.xlsx)»** (`…/offer-draft/file/`, mismo cuerpo y permisos): el mismo libro,
para subirlo en **Ofertas de proveedores**, que hace su vista previa y su aplicación con sus confirmaciones.
No es un botón de aplicar: sólo el flujo oficial escribe.

### 2.2 Origen «stock propio»: borrador local (opción b), los identificadores no salen

La regla de #142 es que el IMEI y la serie no salen del navegador salvo al flujo oficial de equipos. Un
ensayo en el servidor sería **otro endpoint** con IMEI: descartado. El borrador de equipos se arma en la
pestaña con las columnas de la plantilla oficial de **Carga de equipos** y se copia (texto separado por
tabuladores, sin fila de encabezados) para pegarlo bajo los encabezados de esa plantilla. La unicidad de series e IMEI contra la base la valida la carga
oficial al subir el archivo. Requisitos locales para entrar en el borrador: variante elegida, serie (la
carga oficial la exige), IMEI válido por Luhn si lo hay, no duplicado, y ser un equipo (un accesorio propio
va por cantidad en «Carga de stock»: se marca para revisar).

### 2.3 Reglas que el borrador hace cumplir

- **Nunca escribe** inventario, ofertas, códigos de proveedor ni trabajos. Pruebas: recuentos de
  `SupplierOffer`, `SupplierProductRef`, `StockUnit`, `StockMovement`, `BulkImportJob`, `BulkImportRow` y
  `AdminAuditLog` iguales antes y después.
- **Un seminuevo de proveedor no es oferta ni stock propio** (ADR de seminuevos externos, Decisión 2 sin
  implementar): queda en «necesita revisión», no viaja. El servidor lo rechaza igualmente si la variante
  elegida es seminueva.
- **Precio de venta nunca automático**: el «Precio comercial» de la oferta lo escribe la persona en cada
  fila; vacío = la fila necesita revisión. Un cambio de precio frente a la oferta vigente sale como
  «cambio de precio» (el flujo oficial pide aceptarlo o mantenerlo al aplicar). En equipos, «Precio de
  venta» va vacío.
- **Cantidad**: vacía = sin confirmar (no se vende), N = confirmada, 0 = agotada. Se cuentan aparte.
- **Duplicados**: marcados, nunca sumados, no viajan; dos filas con la misma variante las rechaza además
  el preview oficial.
- **Parcial por defecto**; la lista completa es una elección explícita y su resumen dice cuántas ofertas
  se desactivarían (sólo al aplicar en el flujo oficial, con su confirmación).
- La empresa de la conciliación y del borrador es la misma (`?company=` en las dos llamadas).

### 2.4 Interfaz

Se extiende `InventoryTextPreview` (sin UI paralela): columnas de precio comercial y cantidad sólo para
ofertas; datos del borrador (proveedor, vigencia, plazo, modo / sucursal y motivo para equipos);
«Generar borrador» → resumen por operación (nuevas, actualizaciones, sin cambios, agotadas, cambios de
precio, posibles desactivaciones, filas que necesitan revisión, duplicados, cantidades desconocidas);
«Seguir corrigiendo» (cierra el resumen y conserva todo) y «Cancelar» (descarta el borrador y la revisión;
el texto pegado sigue). Cualquier corrección invalida el borrador. Sin botón de aplicar.

## 3. Lo que necesitaría la fase de escritura («aplicar»)

Contrato auditado (qué filas, quién, `AdminAuditLog` con `company`), permisos (`products.manage`,
`costs.view`, `inventory.adjust` para equipos), una transacción por borrador con `select_for_update`,
idempotencia (huella del borrador; el preview oficial ya marca «stale» y `previously_applied`),
pruebas de concurrencia (dos aplicaciones, oferta editada a mano entre medias), su ADR y el GO del
propietario. Lo natural es reutilizar `supplier_offer_import.apply` sobre un trabajo creado en ese momento,
no escribir otro aplicador.

## 4. Riesgos

- El ensayo depende de que `preview()` no tenga efectos fuera de la base (hoy no: ni archivos, ni caché,
  ni auditoría). Una prueba fija que no queda ninguna fila; si `preview()` gana un efecto externo, el
  ensayo debe revisarse.
- El IMEI en el portapapeles: lo pegó la propia persona; el borrador no lo manda a ningún servidor.

## Adenda INV-TEXT-04 — del borrador al importador oficial (2026-10-23)

### Qué faltaba

| Tramo | Estado en #154 |
|---|---|
| WhatsApp → reconocimiento → revisión editable → borrador (ensayo) | Hecho |
| Borrador → importador oficial | **Hueco**: descargar el .xlsx, ir a «Ofertas de proveedores», volver a elegir el archivo, volver a marcar «lista completa» y su proveedor |
| Vista previa oficial → confirmaciones → aplicar → resultado | Hecho (`ImportSection` de `SupplierOffersManager`) |

El único hueco es ese salto manual. Al hacerlo a mano se puede perder la elección «lista completa» y su
proveedor, o subir otro archivo.

### Decisión

«Continuar al importador oficial», en el resumen del borrador de ofertas, **monta el mismo componente del
importador oficial** (`ImportSection`, exportado como `SupplierOfferImport`) debajo del borrador. Lo monta con
el libro del borrador ya elegido, es decir el mismo cuerpo que se ensayó, pedido otra vez a `…/offer-draft/file/`.
También lleva el modo lista completa y su proveedor, y lanza **su** «Previsualizar».

A partir de ahí todo es del flujo oficial, sin cambios:
- `POST /api/admin/supplier-offers/import/preview/` crea su trabajo de vista previa.
- Sus confirmaciones son las mismas: precios nuevos (aceptar o mantener), archivo ya aplicado, «todos los
  mensajes» y «confirmo desactivar N», y lista truncada.
- Sus botones «Aplicar…» y «Descartar» siguen igual.
- `POST …/import/<id>/apply/` sigue siendo el único escritor.

No hay importador nuevo, ni aplicador, ni ruta de servidor, ni migración. Tampoco hay IA: la casilla del flujo
oficial empieza desmarcada.

Reglas:
- El archivo vive en la memoria de la pestaña. No va a `localStorage` ni a una URL, y no se navega con él.
- Cualquier corrección del borrador, «Seguir corrigiendo» o «Cancelar» desmonta el importador. Si había una
  vista previa sin aplicar, se pierde sin escribir nada. Así nunca se aplica un archivo que ya no coincide con
  lo revisado.
- Se ofrece sólo con `products.manage`, como el importador oficial. La empresa es la del panel (`?company=`)
  y el servidor la vuelve a resolver.

### Casos del propietario sobre el aplicador oficial

Ya cubiertos por pruebas existentes:
- vista previa obsoleta: `test_supplier_weekly_import`, `test_supplier_snapshot`;
- doble aplicación y reintento idempotente: el mismo trabajo responde lo que hizo, y el mismo archivo pide
  `confirm_reapply`;
- aplicación concurrente: `test_supplier_snapshot_race` y `test_one_applies_and_the_other_is_refused`;
- duplicados y error parcial: el archivo entero o nada;
- lista completa vacía o truncada;
- proveedor distinto en una lista completa;
- comprador limitado a algunas sucursales.

Nuevos en `test_text_preview_handoff.py`, siempre por las rutas oficiales:
- el libro del borrador se aplica con sus confirmaciones;
- quien sólo ve el catálogo no aplica (403);
- un trabajo de otra empresa da 404;
- un lote vacío no escribe nada;
- cancelar antes de aplicar no escribe nada.

## Adenda INV-FAST-B — de la lista al catálogo y a los equipos (2026-10-28)

Cambia lo de §2.2 («los identificadores no salen»): para **equipos propios** el borrador ya puede registrarse sin
Excel. Las series e IMEI siguen sin guardarse en el navegador y no viajan al generar el borrador; viajan **sólo** al
pedir «Revisar y registrar», a `POST …/text-preview/units/preview/`, que los entrega al importador oficial de equipos
de la empresa (`preview_units`) y nada más. Registrar es el aplicar oficial, sin cambios. Con la confirmación
explícita de la persona y las mismas reglas del archivo. «Copiar a Excel» sigue como alternativa.

Antes de eso, las filas sin variante pueden pedir «Proponer lo que falta» (`…/catalog-proposal/`): el servidor propone,
la persona aprueba y sólo lo aprobado se crea, oculto de la web y con precio 0.00 si no hay uno sugerido (no
publicable hasta fijarlo).

### Revisión de #196 (2026-10-28)

- **IMEI por defecto.** Toda variante creada desde la lista exige IMEI (`requires_imei=True`, como el alta guiada). Sólo se crea
  opcional si la persona marca «Sin IMEI» en esa variante (`without_imei` en el apply); una fila sin IMEI no dice nada de su
  variante y ningún dato de IMEI viaja en la propuesta.
- **Precio 0.00 y caja.** Una variante creada sin precio (`0.00`) puede quedar activa y con stock. `build_pos_sale` (vista previa,
  venta web y caja móvil) rechaza una línea cuyo producto no tiene precio («Este producto no tiene precio: configúralo antes de
  venderlo»). Un descuento que deja el total en cero no es un precio faltante. Una venta ya hecha se reproduce por su clave antes de
  fijar precios. La vista previa de equipos avisa «Sin precio» en las filas de esas variantes.
- **Nombres.** Modelos y opciones se comparan compuestos (NFC), con espacios colapsados y sin distinguir mayúsculas: «Teléfono Y»,
  su forma descompuesta y «TELÉFONO y» son un solo modelo. Un color con un IMEI, serie o teléfono no se propone ni se guarda.
- **Vistas previas sin aplicar.** Como una carga oficial, una vista previa de equipos deja un `BulkImportJob` de tipo `UNITS` que
  nadie limpia (no hay limpieza de `BulkImportJob`). Acotado por: `AdminImportThrottle`, tope de `MAX_UNITS` filas por petición (con
  prueba) y 404/400 antes de escribir nada. Aceptado; una limpieza de trabajos sin aplicar es un seguimiento aparte.
- **Ventana propuesta → aplicar.** El apply recalcula el plan tras el candado de la empresa: un modelo creado a mano entre la
  propuesta y el apply se encuentra y no se duplica (prueba). Lo único que queda es un modelo creado a mano *durante* el apply: el
  nombre del modelo no es único (sólo el slug, que `create_model` hace libre), así que en esa ventana de milisegundos podría haber dos
  modelos con el mismo nombre; las filas siguientes encuentran el de menor id y se pueden unir a mano. Aceptado.
