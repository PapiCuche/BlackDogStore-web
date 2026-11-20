# ADR — Catálogo maestro de referencia de fabricantes (CATALOG-REFERENCE-01)

Estado: **APROBADO por el propietario (orden «FASE 1 AMPLIADA», 2026-11-20). En implementación por PR A–G (PR B: §4.1).**
Fecha: 2026-11-20. Base: master `d34df6b`.

## 1. Requisito

El propietario pidió un catálogo completo y administrable de iPhone, con:
- modelos y generaciones;
- capacidades y colores oficiales de cada modelo;
- configuraciones SIM, regiones y números de modelo.

Debe servir de base común a la tienda web, al panel y a la caja. Su objetivo es dejar de crear a mano modelos y variantes para cada venta.

Además:
- cada empresa activa, oculta y personaliza lo que vende;
- el maestro nunca se convierte en stock.

## 2. Excepción a AGENTS.md §2 (SaaS)

AGENTS.md §2 prohíbe «Apple/iPhone» en código global. Con esta orden, el propietario aprueba una excepción acotada, que AGENTS.md recoge desde ahora:

- **Qué entra:** un **catálogo de referencia de fabricantes**, con **datos factuales** verificados en fuentes oficiales: modelos, generaciones, capacidades, colores, SIM, regiones y números de modelo.
- **Dónde vive:** archivos de datos versionados en `backend/catalog_reference/`, fuera de `backend/store/`. Es uno por fabricante y familia, con un esquema común, así que otro fabricante se añade con otro archivo.
- **El motor no nombra nada.** El validador (PR A), el importador y los servicios del catálogo (PR B en adelante) no contienen nombres de fabricante, modelo ni color. Cada PR añade su módulo al test guardián: `NoVocabularyInValidatorTest`, del mismo estilo que `NoVocabularyInCodeTest`.
- **Qué no entra:**
  - precios, stock ni disponibilidad;
  - imágenes;
  - textos comerciales y garantías;
  - nada propio de la tienda piloto.
- **Prueba mínima intacta:** una empresa que no importa el catálogo no ve nada de él. Importarlo crea el modelo, sus opciones y su paleta, todo **oculto**, y nunca crea variantes vendibles, stock ni unidades.

## 3. Decisiones (la orden del propietario resuelve las abiertas en la auditoría)

| ID | Decisión |
|---|---|
| D-REF-1 | Se aprueba la excepción del §2. |
| D-REF-2 | Alcance: **todas** las generaciones documentadas por Apple hasta noviembre de 2026. Primero las comercialmente relevantes y después el histórico. Cada modelo lleva `completeness` (`complete` / `partial` / `pending_review`). Mientras quede alguno sin verificar no se habla de «todos los iPhone». |
| D-REF-3 | Colores: el maestro guarda el nombre oficial en inglés y en español (es-419 si existe; si no, es-ES). La empresa puede cambiar la etiqueta comercial (`VariantOption.label`). Ambas quedan unidas por `reference_key`, así que una reimportación no duplica opciones renombradas. |
| D-B-1 | El importador crea **sólo** modelo, opciones y paleta, nunca variantes (`Product`). Las variantes nacen bajo demanda: «Crear variante» del panel o la llegada de un equipo en caja (PR F). |
| D-C-1 | Imágenes, por orden de prioridad: fotos propias, material autorizado por proveedor o distribuidor, banco con licencia comercial y, por último, recurso demostrativo identificado como tal. **Ninguna imagen entra al repositorio** (AGENTS §9). Se suben desde el panel con procedencia y licencia. No se copian imágenes de apple.com ni se recolorea una foto. Si falta la del acabado, se muestra un marcador neutro «Imagen pendiente», nunca la de otro color. |
| D-D-1 | Variantes enlazables con `?variante=` y canonical al modelo: la variante se puede enlazar sin generar contenido duplicado. |
| D-D-2 | «Bajo pedido» sólo se muestra cuando ya existe una oferta comprable (DC3). La disponibilidad de un proveedor nunca se presenta como stock propio. |
| D-F-3 | La unidad recibida guarda su proveedor (campo nulo y aditivo, PR F). |
| D-F-1/2 | Se definen en PR F con su ADR corto. La variante creada en caja respeta DEC-CATALOG-VARIANT-DRAFT-01: no se publica en la web sin autorización. |

## 4. Modelo de datos (PR B y C; todo aditivo)

**Plataforma** (sin `company`; los tenants sólo los leen; los escribe únicamente el comando de carga, con auditoría):
- `ReferenceModel`: `key` único, fabricante, familia, línea, generación, fecha de anuncio, orden, `completeness`, `sources`, `verified_at`, `dataset_version`.
- `ReferenceOption`: modelo, `role` (el mismo vocabulario que `VariantAttribute.ROLE_*`), `key`, etiquetas y orden.
- `ReferenceModelNumber`: número de modelo, SIM y regiones. Define qué pares SIM × región existen.

**Empresa:**
- `ProductModel.reference_key` y `VariantOption.reference_key`, con `db_default=''` y únicos parciales por empresa;
- `ModelOptionExclusion` para los pares imposibles. Un modelo sin exclusiones se comporta exactamente como hoy.
- `ModelFinishImage`: galería por modelo y color.
- Procedencia y licencia en `StorefrontImage`.

Se descartó una «empresa de referencia» oculta: rompería el aislamiento entre empresas y metería filas `Product` en Kardex, en la tienda y en el barrido de imágenes.

### 4.1 Decisiones de implementación de PR B (2026-11-20)

| ID | Decisión |
|---|---|
| D-PRB-1 | **SIM y región.** El archivo no trae una lista de regiones propia: cada entrada `sim` dice qué configuración existe en qué regiones, con el texto de Apple. Por eso: una `ReferenceOption` de rol `sim` por cada `sim.key` del modelo (etiqueta en español = `label_es` de la entrada, p. ej. «nano-SIM y eSIM», «Solo eSIM»; si el archivo no la trae, la clave en palabras; los textos exactos de configuración en `official.config`); una de rol `region` por cada grupo de regiones, que es el texto de `regions` hasta el primer paréntesis (lo demás cita la fuente; «default (other countries…)» → «default»), con clave derivada (`catalog_reference.region_key`: slug, recortado a 64 con un hash cuando es más largo), etiqueta en español = `region_label_es` de la entrada (p. ej. «Internacional (otros países)», «EE. UU. y Puerto Rico»; si falta, el texto del archivo recortado a 60) y los textos completos en `official.regions`. `label_es` y `region_label_es` son opcionales en el esquema, deben ser texto no vacío si están, y dentro de un modelo una misma SIM o un mismo grupo de regiones se escribe igual; el archivo de iPhone los lleva en todas las entradas (lo prueba un test). Las claves no dependen de esas etiquetas: cambiarlas no rompe enlaces ni reimportaciones, y una opción que la empresa ya renombró no se toca. Cada entrada `sim` es un par SIM × región que **existe**; `ReferenceModel.sim_entries` guarda las entradas tal cual con su `region_key`. |
| D-PRB-2 | **Números de modelo.** `ReferenceModelNumber` guarda número y texto de regiones, sólo como referencia: el dataset v1 no une un número con una entrada SIM, así que los pares los define `sim_entries` (no el número de modelo, como decía el §4). |
| D-PRB-3 | **Exclusiones.** Al importar, cada par SIM × región del modelo que no está en `sim_entries` se guarda como `ModelOptionExclusion` (par ordenado, por empresa y modelo). Capacidad × color: el dataset no trae exclusiones, no se crea ninguna. `check_variant` las rechaza (`combination_excluded`) también en lecturas parciales que nombran las dos opciones; `payload` no ofrece esas combinaciones y añade `excluded_pairs` **sólo** si el modelo tiene alguna. La consulta de la paleta lleva un `EXISTS` y los pares se leen sólo si hay: un modelo sin exclusiones hace las mismas consultas que antes. |
| D-PRB-4 | **Retirada.** La carga nunca borra: lo que salió del archivo (modelo, opción o número del mismo fabricante y familia) queda `in_dataset=False`, se informa como candidato a retirar y el importador deja de ofrecerlo; si vuelve al archivo, vuelve a `True`. |
| D-PRB-5 | **Estados de la vista previa.** `new` / `reused` / `conflict` / `pending_review`. Conflicto (nunca se aplica; `apply` responde 400 `conflict` y no escribe nada): un modelo o una opción con el mismo nombre que ya están unidos a **otra** referencia, un alias de proveedor con ese texto, un atributo con el nombre del rol pero sin el rol, dos referencias que caerían en una misma opción. `pending_review` = `completeness` distinta de `complete`: sólo se aplica con la clave en `approve` (si no, 400 `approval_required`). |
| D-PRB-6 | **Reutilizar y unir.** Modelo: por `reference_key` y, si no, por nombre plegado (`model_name_holder`); entonces sólo se le escribe `reference_key`. Opción: por `reference_key` y, si no, por etiqueta plegada como compara `create_option` (capacidad también «256GB» / «1 TB»); entonces sólo se le escribe `reference_key`. Nombre, etiqueta, muestra, precio, fotos, publicación y variantes de lo existente no se tocan. La paleta de un modelo reutilizado sólo crece (`add_to_palette`, o `set_palette` si no tenía); sus variantes que no encajen se informan, nunca se reescriben. |
| D-PRB-7 | **Atributos.** Por rol; capacidad por nombre y color por tipo como en la lista pegada; si falta, se crea con el nombre del rol («Capacidad», «Color», «SIM», «Región»). Un color nuevo lleva la muestra neutra de `catalog_proposal`. |
| D-PRB-8 | **Permisos y contrato.** Ver y previsualizar = `products.view`; aplicar = `products.manage` (no crea variantes, así que no pide `inventory.adjust`). Huella, `Idempotency-Key` y candado de empresa son los de `catalog_proposal` (helpers compartidos `request_fingerprint`, `replay`). Las tablas de plataforma sólo las escribe el comando, con `AdminAuditLog` sin empresa ni actor y `source: command`. |

## 5. Formato del dataset (PR A)

Hay un archivo por fabricante y familia: `backend/catalog_reference/<fabricante>-<familia>.json`, en JSON UTF-8. El esquema es el validador puro `store/catalog_reference.py`, que no usa la base de datos y cuyas pruebas (`store/test_catalog_reference.py`) corren en CI con cada cambio de los datos.

**Cabecera:** `schema_version` (1), `manufacturer`, `family`, `dataset_version`, `official_source_hosts` y `notes`.

**Cada modelo incluye:**
- `key`, `name`, `line` (lista cerrada: standard, plus, pro, pro_max, mini, se, air, e, original, other), `generation` y `announced`;
- `capacities_gb`;
- `colors`, con `key`, `label_en`, `label_es` y `official`, que guarda las cadenas exactas en/es-419/es-ES;
- `sim`: `key`, `regions` y `config`, con el texto de Apple, y (opcionales, PR B) `label_es` y `region_label_es`, cómo se leen en español la SIM y el grupo de regiones;
- `model_numbers`;
- `sources`, `verified_at`, `completeness`, `missing` y `notes`.

**El validador rechaza:**
- campos desconocidos: un archivo de referencia nunca lleva precio, stock ni imágenes;
- claves, nombres o números de modelo repetidos;
- colores sin `label_en` o sin `label_es`;
- capacidades no positivas, repetidas o desordenadas;
- fuentes que no sean páginas https de los hosts oficiales declarados;
- fechas que no sean ISO;
- un modelo `complete` con algo en `missing` o sin capacidades, colores, SIM o números de modelo;
- un modelo `partial` o `pending_review` que no diga qué falta.

**Datos v1 (2026-11-20):** 55 modelos, desde el iPhone original de 2007 hasta los anunciados el 2026-09-09: iPhone 18 Pro, 18 Pro Max y Duo. Hay 49 `complete` y 6 `partial`: el original, 3G, 3GS, 4, 4s y 5c, cuyas especificaciones Apple publica incompletas. Una revisión independiente los contrastó con las páginas de Apple antes de fusionar; el resultado está en la descripción del PR.

**Reglas de los datos:**
- La SIM de Perú corresponde al grupo «otros países» de Apple (nano-SIM + eSIM), salvo en Air y Duo, que son sólo eSIM.
- Un segundo IMEI nunca se interpreta como una segunda ranura física.

## 6. Invariantes

- Aislamiento por empresa: otra empresa responde 404.
- Nada existente se reescribe, fusiona ni borra al importar.
- El catálogo nunca crea `Product`, `BranchStock`, `StockUnit` ni movimientos.
- `company_storefront_products` y `pending_draft_q()` siguen siendo la única definición de lo vendible.
- El resolutor resuelve exactamente lo mismo que antes.
- `check_model_names` sigue devolviendo 0.
- No se importa ni despliega en producción sin un GO del propietario con SHA y alcance.
