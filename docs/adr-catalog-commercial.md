# ADR — Catálogo comercial, ofertas de proveedor y ficha de compra (CATALOG-COMMERCIAL-01)

Estado: **PROPUESTA. No implementado.** Las decisiones marcadas **DECIDIR** esperan al propietario. Ningún cambio de checkout, pagos, reservas ni de D-A/D-B se hace sin su autorización expresa.

Fecha: 2026-11-10. Base: master `fc72201` (= producción R4.4). PR abiertos que tocan zonas vecinas: #254 (imágenes por variante), #255 (cargas masivas), #162 (borrador, seminuevos externos), #249 (borrador, orden de bloqueos oferta/estante).

## 1. Requisito del propietario

Una ficha de compra tan clara como la de Apple, adaptada a la operación real:

- nuevos con stock propio;
- nuevos ofrecidos por proveedores, muchas veces **sin cantidad declarada**;
- seminuevos únicos con IMEI, fotos, batería, estado y precio propio;
- modelos con capacidades, colores, SIM y regiones distintas;
- listas de proveedor que llegan cada semana por WhatsApp.

Sin duplicar productos, sin inventar stock y sin arquitectura propia de una sola tienda.

## 2. Lo que el código hace hoy (auditoría)

| Pieza | Estado | Dónde |
|---|---|---|
| Modelo (`ProductModel`) y línea Nuevo/Seminuevo (`Product.condition_line`) | IMPLEMENTADO | models.py:10798, :245 |
| Variante = `Product` con opciones (`ProductVariantOption`), combinación única por (modelo, línea, `variant_key`) | IMPLEMENTADO | models.py:279, :10887 |
| Atributos y opciones (`VariantAttribute`, `VariantOption` con `swatch_hex`) | **PARCIAL**: son de la empresa, no del modelo. `set_variant` acepta cualquier opción de la empresa; los atributos de las variantes de un modelo pueden no coincidir | device_catalog.py:367 |
| Capacidad y color | PARCIAL: atributos genéricos; las exportaciones suponen exactamente dos (color = «Color», texto = «Capacidad») | inventory_exports.py:355 |
| SIM y región | **NO EXISTEN**. El reconocedor de texto los lee y el emparejador los descarta | text_preview_match.py:20 |
| Combinaciones imposibles | PARCIAL: la tienda sólo muestra combinaciones de variantes existentes, pero nada impide crear una imposible | device_catalog.py:743 |
| Seminuevos: unidad con IMEI, batería, grado, fotos y precio propio; compra del equipo exacto; protección contra doble venta | **IMPLEMENTADO** | `StockUnit`, `UnitPhoto`, `unit_hold_services.hold`, `OrderUnit` |
| Stock propio en la tienda | IMPLEMENTADO (sólo la sucursal de despacho; D-A sin implementar) | `public_model` |
| Oferta de proveedor con cantidad confirmada → «Bajo pedido» comprable | IMPLEMENTADO: `valid_offer_q`, pago que compromete la oferta bajo bloqueo; si la oferta venció, la línea queda «por confirmar» | supply_services.py:415, :582 |
| Oferta **sin cantidad** | PARCIAL: existe (`confirmed_quantity` NULL, `availability_confirmed=False`) y nunca se vende, pero **la tienda la muestra como «Agotado»**: no hay estado «consultar» | models.py:11075 |
| Vigencia de la oferta | IMPLEMENTADO (`valid_from`/`valid_until`, cron `expire_supplier_offers`) | |
| Costo del proveedor con permiso | IMPLEMENTADO (`agreed_cost`, `costs.view`) | supply_services.py:264 |
| Precio de venta aprobado | PARCIAL: `Product.price` (stock propio) y `SupplierOffer.commercial_price` (bajo pedido); la única «aprobación» es aceptar/mantener precios en la carga semanal | supplier_offer_import.py:1058 |
| Fecha de última confirmación | **NO EXISTE** (sólo `updated_at` y la auditoría) | |
| Carga semanal: vista previa, comparación, idempotencia, concurrencia y vista previa desfasada | IMPLEMENTADO (Excel; el texto genera ese Excel en memoria) | supplier_offer_import.py |
| «No mencionadas» | PARCIAL: sólo en el modo «lista completa», que las apaga con doble confirmación | :957 |
| Origen de cada oferta | PARCIAL: sólo en la auditoría; la oferta no guarda la carga que la confirmó | |
| IA de inventario | Inerte: sin proveedor configurado, sin llamador en producción; nunca escribe | inventory_ai.py:61 |

**Ficha pública hoy** (`ModelDetail`, `VariantPicker`):
- selección jerárquica, galería por variante y precio con `aria-live`;
- botones «Agregar al carrito», «Pedir bajo pedido» y «Agotado»;
- seminuevos como tarjetas con «Comprar este equipo».

Le faltan:
- distinguir «no existe» de «agotado»: hoy los dos se ven tachados;
- una acción de consulta;
- una barra de compra fija en el celular;
- que el nombre del botón de cada seminuevo diga batería y precio.

## 3. Por qué las listas de WhatsApp se reconocen mal

Se ejecutó el reconocedor real (`previewSupplierText`) con la lista de entrenamiento: **1 de 14 líneas** sale correcta.

| Causa | Efecto en la lista |
|---|---|
| Las palabras desconocidas se pegan al nombre del modelo (`inventory-text-stages.ts:247`) | «iPhone 18 Pro Max borgoña y», «iPhone 17 Pro japan». El servidor busca el modelo por nombre exacto: «No existe el modelo» |
| Los colores salen de una **lista fija en el código**, la misma para todos los modelos y empresas | `borgoña`, `glaciar` y `naranja` no existen; «glaciar blanco» se parte en modelo + `blanco` |
| Varios colores en una línea = «Elige uno», nunca una configuración por color; `y` no separa | «blanco azul naranja» debía dar 3 ofertas; en la línea 4 se pierde un color en silencio |
| La región es una lista corta de siglas | `japan` no se reconoce y termina en el modelo |
| El emparejador descarta SIM y región, y busca etiquetas en toda la empresa | eSIM y chip del mismo modelo no se distinguen |
| El reconocedor está pensado para **un bloque por equipo** (IMEI, batería, serie) | Una lista de proveedor es **una línea por oferta**: necesita su propia gramática |
| El importe solo se toma como costo | Correcto que no se invente un precio, pero la persona debe poder decir una vez para toda la lista qué son esos importes |

`IPH` y la generación 18 **sí** se reconocen. La IA existente no puede ayudar: nunca ve el texto original, por diseño.

## 4. Contrato de catálogo propuesto

Sin arquitectura paralela: se extienden `VariantAttribute`, `VariantOption`, `ProductModel` y `Product`.

1. **Modelo principal:** `ProductModel` (sin cambios).
2. **Línea:** `Product.condition_line` Nuevo/Seminuevo (sin cambios).
3. **Capacidad**, 4. **Color**, 5. **Tipo de SIM**, 6. **Región o procedencia comercial:**
   - siguen siendo atributos **de la empresa**;
   - nuevo campo **`VariantAttribute.role`**: `capacity | color | sim | region | other`;
   - el rol le dice al reconocedor, a las exportaciones y a la ficha qué es cada atributo, sin escribir «iPhone» ni colores en el código;
   - los valores (Borgoña, eSIM, LL/A, J/A…) son datos de cada empresa.
7. **Variante válida:**
   - **nueva tabla `ModelOption` (modelo ↔ opción permitida)**: la «paleta» de cada modelo, es decir, qué capacidades, colores, SIM y regiones existen para él;
   - se llena al migrar con las opciones que ya usan sus variantes;
   - `set_variant` rechaza una opción fuera de la paleta del modelo, **cuando el modelo tiene paleta**: los modelos sin paleta siguen como hoy;
   - todas las variantes de un modelo con paleta llevan los mismos atributos.
8. **Disponibilidad propia:** `BranchStock` de la sucursal de despacho / unidades AVAILABLE (sin cambios).
9. **Disponibilidad declarada por proveedor**, 10. **Cantidad confirmada o desconocida:** ver §5.
11. **Precio de venta aprobado:**
    - stock propio = `Product.price`;
    - bajo pedido = `SupplierOffer.commercial_price` (sin cambios);
    - se añade **quién y cuándo aprobó** el precio de la oferta (`commercial_price_approved_by/at`), escrito por la decisión de precios que ya existe en la carga.
12. **Costo del proveedor:** `SupplierOffer.agreed_cost`, sólo con `costs.view` (sin cambios).
13. **Vigencia:** `valid_from` / `valid_until` (sin cambios).
14. **Última confirmación:**
    - nuevo `SupplierOffer.last_confirmed_at` y `last_confirmed_job` (FK a la carga que la confirmó);
    - se marcan también cuando la fila no cambió.

**Alias por empresa:**
- nueva tabla `OptionAlias(company, option, alias)`: «borgoña» → la opción Borgoña, «japan» → la opción J/A;
- se consultan **sólo en el servidor**, sólo apuntan a opciones que la empresa ya tiene y nunca viajan a la IA (compatible con ADR inv-ai §6, que prohíbe vocabulario de empresa en la lista permitida de la IA).

**Ejemplo con la lista** (la paleta la define la empresa, no el código):
- «iPhone 18 Pro Max»:
  - capacidad 256 GB / 512 GB;
  - color Borgoña / Glaciar negro / Azul / Negro;
  - SIM eSIM / Chip;
  - región LL/A / J/A.
- «iPhone 15»: capacidad 128 GB… y color Negro…

Si «iPhone 15» no tiene «Borgoña» en su paleta, una oferta «15 borgoña» queda **en revisión**: nunca se crea esa variante.

## 5. Contrato de disponibilidad sin cantidad

Estados públicos por variante, en este orden de prioridad (D-A respetada: «otra sucursal» va antes que «bajo pedido»):

| Estado | Cuándo | Qué ve el cliente | ¿Compra? |
|---|---|---|---|
| `own_stock` | stock en la sucursal de despacho | «En stock» | Sí (sin cambios) |
| `other_branch` | (D-A, aprobado y sin implementar) stock en otra sucursal | «Disponible en otra sucursal · Consúltanos» | No |
| `supplier` | oferta válida con **cantidad confirmada** | «Bajo pedido · plazo estimado del proveedor N días» | Sí: flujo actual bajo pedido, **sin cambios** |
| **`supplier_declared`** (nuevo) | oferta activa y vigente, **cantidad desconocida**, marcada «el proveedor declara disponible», confirmada hace no más de N días | «Disponible con proveedor · Consultar disponibilidad» | **No.** Sólo consulta (WhatsApp o formulario con la variante), sin carrito, sin reserva y sin cobro |
| `none` | nada de lo anterior | «Agotado» | No |

Reglas:
- `SupplierOffer.declared_available` (nuevo, booleano). Sólo puede ser verdadero si `confirmed_quantity` es NULL (CHECK en la base).
- **Nunca** se convierte una cantidad desconocida en 1, 10 ni 999: `valid_offer_q`, el carrito y el checkout no cambian, así que una oferta declarada no se puede comprar ni reservar.
- La declaración **caduca sola**: si `last_confirmed_at` es más antigua que la ventana de la empresa (propuesta: 7 días), la oferta deja de mostrarse. Nada se desactiva.
- El dato público no lleva proveedor, costo ni cantidad. Se actualiza `PublicExposureMatrixTest`.
- Entrega: sólo se muestra el plazo de una oferta **confirmada**, como «plazo estimado del proveedor» (no incluye el envío). Para stock propio no se promete plazo mientras la empresa no configure sus tiempos de entrega.

## 6. Asistente de ofertas de proveedor

Un flujo distinto del ingreso de equipos por IMEI: **Pegar → Reconocer → Resolver dudas → Comparar → Confirmar**.

1. **Pegar:**
   - eliges el proveedor y la fecha de la lista;
   - dices **una vez** qué son los importes: «costo del proveedor», «precio de venta» o «no sé»;
   - fijas la vigencia y el plazo por defecto, y si el proveedor declara disponibilidad sin cantidad.
2. **Reconocer** (en el navegador, determinista):
   - gramática de **línea por oferta**: familia + generación + gama, y después capacidad / SIM / región / importe / colores;
   - una línea con N colores da N configuraciones;
   - colores de varias palabras primero («glaciar negro»); `y` y la coma separan;
   - las palabras que no entiende quedan como dudas, **nunca dentro del modelo**;
   - reutiliza el limpiador de líneas, los importes y la detección de identificadores del reconocedor actual;
   - se quitan IMEI y contactos antes de nada.
3. **Resolver dudas** (servidor):
   - el modelo se busca por nombre exacto; los colores, capacidades, SIM y regiones, **sólo en la paleta del modelo** y en los alias de la empresa;
   - cada configuración queda como: lista / variante existente / combinación válida sin variante (proponer crearla, con permiso) / fuera de la paleta (revisión) / campo faltante («falta la capacidad»);
   - la persona puede guardar un alias («borgoña» = Borgoña) con permiso.
4. **Comparar** (vista previa oficial de la carga, sin escribir):
   - **nuevas**, **precio cambiado**, **sin cambio** (sólo renueva la confirmación), **no mencionadas** (informativo: no se apaga nada) y **en revisión**.
5. **Confirmar:**
   - con el `apply` existente, que ya tiene bloqueos, huella de vista previa, decisión de precios y aplicación única;
   - marca `last_confirmed_at`/`last_confirmed_job`;
   - el modo «lista completa» sigue siendo el único que apaga ofertas, con su doble confirmación.

Correcciones incluidas:
- **Riesgo detectado:** la protección «ya aplicado» compara la huella del **archivo**. Un Excel generado otra vez desde el mismo texto probablemente tiene otra huella, y volver a aplicar la misma lista podría reponer disponibilidad ya vendida (no probado aún; se cubre con una prueba). Propuesta: huella de las **filas normalizadas**.
- La IA sigue siendo opcional y nunca escribe. Este diseño no la necesita.

## 7. Ficha de compra

Referencia de usabilidad, no de diseño ni recursos.

- **Galería** de la variante elegida (ya existe con #254). Arranca en la imagen principal.
- **Nuevo / Seminuevo** como pestañas.
- **Opciones en el orden de sus roles:** capacidad → color (muestras de color, ya existen) → SIM → región. Sólo se muestran SIM y región si el modelo las tiene en su paleta.
- **Sólo configuraciones existentes:**
  - «no existe» se oculta o se atenúa sin tachar;
  - «agotado» se distingue con texto, no sólo con un tachado.
- **Precio de la selección** y **estado** según §5, con `aria-live`.
- **Un botón según el estado:** Agregar al carrito / Pedir bajo pedido / Consultar disponibilidad (WhatsApp con el texto de la variante) / Consultar en otra sucursal (D-A) / Agotado.
- **En el celular:** barra fija con precio y botón; destinos táctiles de 44 px; pestañas que no desbordan.
- **Seminuevos:**
  - tarjetas con batería, grado, fotos y precio;
  - el botón nombra batería y precio;
  - enlace propio `?unit=<id>`;
  - la compra del equipo exacto y la protección contra doble venta **ya existen y no cambian**.
- **Sin cambios** en carrito, checkout, reservas ni pagos.

## 8. Plan de PR pequeños

| PR | Etapa | Contenido | Migración | Depende de |
|---|---|---|---|---|
| A1 | Catálogo | `VariantAttribute.role`, tabla `ModelOption` con relleno desde las variantes existentes, validación en `set_variant` (sólo modelos con paleta), pantalla «Opciones de este modelo», columnas SIM y Región en importar/exportar por rol | Sí (aditiva) | #254 y #255 fusionados (tocan el editor y las cargas) |
| A2 | Catálogo | `OptionAlias`; `resolve_variant_spec` por modelo, con SIM, región y alias | Sí (aditiva) | A1 |
| B1 | Listas | Gramática de línea por oferta (biblioteca + pruebas con la lista de entrenamiento); emparejador extendido | No | A2 |
| B2 | Listas | Asistente «Actualizar lista de proveedor»; significado de los importes; «no mencionadas» informativo; huella por filas | No | B1, C1 (para marcar la confirmación) |
| C1 | Disponibilidad | `declared_available`, `last_confirmed_at/job`, `commercial_price_approved_by/at`; estado público `supplier_declared` sin compra | Sí (aditiva) | **Decisiones 3 y 4** |
| C2 | Disponibilidad | D-A «otra sucursal» (ya aprobado) | No | Encargo propio |
| D1 | Ficha | Configurador: estados, botones, barra fija, accesibilidad, seminuevos | No | C1 para «Consultar»; el resto no |
| E | Documentación | Guías y capacitación | No | Cada etapa |

## 9. Riesgos

- **Checkout, reservas y pagos:** ningún PR los cambia. El riesgo que ya existe —sin reserva para productos por cantidad, dos carritos pueden pagar la última unidad— no se toca (D-B no autorizado).
- **SaaS:** paletas, alias, roles y ventanas son datos de cada empresa. En el código sólo queda la gramática de familias de varios fabricantes, la excepción ya aprobada en AGENTS §2. Una empresa sin paleta sigue funcionando como hoy.
- **Migraciones en tablas grandes** (`Product`, `SupplierOffer`): sólo columnas con `db_default` y tablas nuevas, con `lock_timeout`, como en 0147/0151.
- **Rellenar la paleta:** si un modelo ya tiene variantes con atributos distintos, no se le activa la paleta y se lista para revisión.
- **`matchVariant` exige todos los atributos:** añadir SIM o región sólo a algunas variantes las dejaría inalcanzables. Por eso la paleta exige el mismo conjunto en todo el modelo.
- **Exportaciones e importaciones fijadas en Capacidad/Color:** se pasan a columnas por rol. Hay unos 11 módulos de pruebas con esa forma.
- **Datos públicos:** cada campo nuevo pasa por `PublicExposureMatrixTest`.
- **PR vecinos:** #255 (`import_services`), #254 (`CatalogModelEditor`, `ModelDetail`), #162 (ofertas de seminuevos externos) y #249 (orden de bloqueos de ofertas). Conviene fusionar #254 y #255 antes de A1.

## 10. Decisiones del propietario

1. **DECIDIR — Paleta por modelo:** tabla `ModelOption` (recomendado) o deducir lo válido sólo de las variantes existentes (sin tabla, pero entonces no se distingue «combinación nueva válida» de «error de tipeo»).
2. **DECIDIR — SIM y región en la variante:** ¿un 18 Pro Max 256 GB eSIM LL/A y uno Chip son **productos distintos** con su propio precio y stock (recomendado, porque el precio difiere), o sólo una nota de la oferta?
3. **DECIDIR — Importes de las listas:** ¿qué envía cada proveedor (costo o precio)? ¿Cómo sale el precio de venta: lo escribe la persona (recomendado en la v1), se mantiene el actual o se aplica una regla de margen?
4. **DECIDIR — Estado «Consultar disponibilidad»:** ¿mostrar públicamente las ofertas sin cantidad como «Disponible con proveedor · Consultar», sin compra? ¿Con qué ventana de frescura (propuesto: 7 días)?
5. **DECIDIR — No mencionadas en la lista semanal:** sólo informativas (recomendado); la declaración caduca sola con la ventana del punto 4.
6. **DECIDIR — Alias por empresa:** aprobar `OptionAlias`, sólo en el servidor y fuera de la IA.
7. **DECIDIR — D-A:** ¿se implementa ahora «Disponible en otra sucursal» (ya aprobado) como C2?
8. **DECIDIR — Entrega:** mostrar sólo «plazo estimado del proveedor» en bajo pedido y nada para stock propio, hasta configurar tiempos por empresa.
