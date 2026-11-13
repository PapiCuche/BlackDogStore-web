# ADR — Catálogo comercial, ofertas de proveedor y ficha de compra (CATALOG-COMMERCIAL-01)

Estado: **PROPUESTA REVISADA con las decisiones del propietario del 2026-11-10 (§10). No implementado.**
- **No se empieza A1** hasta que el propietario apruebe esta revisión.
- **No cambian** checkout, pagos ni reservas. **D-B sigue NO AUTORIZADO.**

Fecha: 2026-11-10. Base: master `fc72201` (= producción R4.4).

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
| Atributos y opciones (`VariantAttribute`, `VariantOption` con `swatch_hex`) | **PARCIAL**: son de la empresa, no del modelo; `set_variant` acepta cualquier opción de la empresa | device_catalog.py:367 |
| Capacidad y color | PARCIAL: atributos genéricos; las exportaciones suponen exactamente dos | inventory_exports.py:355 |
| SIM y región | **NO EXISTEN**; el emparejador de texto los descarta | text_preview_match.py:20 |
| Combinaciones imposibles | PARCIAL: la tienda sólo muestra las de variantes existentes, pero nada impide crear una imposible | device_catalog.py:743 |
| Seminuevos: unidad con IMEI, batería, grado, fotos y precio; compra del equipo exacto; protección contra doble venta | **IMPLEMENTADO** | `StockUnit`, `unit_hold_services.hold`, `OrderUnit` |
| Stock propio en la tienda | IMPLEMENTADO (sólo la sucursal de despacho; D-A sin implementar) | `public_model` |
| Oferta con cantidad confirmada → «Bajo pedido» comprable | IMPLEMENTADO | supply_services.py:415, :582 |
| Oferta **sin cantidad** | PARCIAL: nunca se vende, pero la tienda la muestra como «Agotado» | models.py:11075 |
| Vigencia, costo con permiso, carga semanal con vista previa y concurrencia segura | IMPLEMENTADO | supplier_offer_import.py |
| Fecha de última confirmación, origen de la oferta | **NO EXISTEN** (sólo auditoría) | |
| Reimportar la misma lista | **RIESGO CONFIRMADO**, ver §7 | |
| IA de inventario | Inerte; nunca escribe | inventory_ai.py:61 |

**Ficha pública hoy:**
- tiene selección jerárquica, galería por variante, precio con `aria-live` y los botones «Agregar al carrito» / «Pedir bajo pedido» / «Agotado», además de tarjetas de seminuevos;
- le falta distinguir «no existe» de «agotado» (los dos se ven tachados);
- le falta una acción de consulta y una barra fija en el celular.

## 3. Por qué las listas de WhatsApp se reconocen mal

Con el reconocedor real (`previewSupplierText`) y la lista de entrenamiento, **1 de 14 líneas** sale correcta.

| Causa | Efecto |
|---|---|
| Las palabras desconocidas se pegan al nombre del modelo (`inventory-text-stages.ts:247`) | «iPhone 18 Pro Max borgoña y», «iPhone 17 Pro japan»: el servidor no encuentra el modelo |
| Los colores salen de una **lista fija en el código** | `borgoña`, `glaciar` y `naranja` no existen; «glaciar blanco» se parte |
| Varios colores = «Elige uno»; `y` no separa | «blanco azul naranja» debía dar 3 ofertas; en la línea 4 se pierde un color en silencio |
| La región es una lista corta de siglas | `japan` termina en el modelo |
| SIM y región se descartan al emparejar; las etiquetas se buscan en toda la empresa | eSIM y chip del mismo modelo no se distinguen |
| Pensado para **un bloque por equipo** (IMEI, batería) | Una lista de proveedor es **una línea por oferta** |

`IPH` y la generación 18 sí se reconocen. La IA existente no puede ayudar: no ve el texto original, por diseño.

## 4. Impacto en los datos actuales

Se restauró el respaldo de producción del 2026-11-09 en un contenedor aislado y se consultó sólo con `SELECT` en una transacción de sólo lectura. Producción no se tocó.

**Catálogo hoy** (empresa `black-dog-store`):
- 12 modelos;
- 10 productos (1 enlazado a un modelo, como variante sin opciones);
- **0** atributos, **0** opciones, **0** ofertas de proveedor y **0** códigos de proveedor;
- 2 equipos físicos.

**Consecuencias:**
- **La migración no convierte nada:**
  - la paleta se rellena con 0 filas;
  - `variant_key` no cambia;
  - las columnas nuevas de `SupplierOffer` no afectan a ninguna fila.
- **No hay duplicados** de (modelo, línea, clave) ni de nombres normalizados. Ningún nombre de producto o modelo trae SIM o región.
- **La unicidad con SIM y región no tiene bloqueos** en los datos actuales.

**Requieren una revisión humana antes de exigir paletas** (son tareas de catálogo, no de código):
1. «Seminuevos» está publicado como modelo, con 0 variantes. En realidad es una colección o una línea.
2. «Adaptadores y Cables» es un modelo usado como categoría: sus 6 accesorios no están enlazados a él.
3. 4 modelos ocultos no tienen categoría: iPhone 15 Pro, 15 Pro Max, 16 Pro y 17.
4. Dos teléfonos fuera de cualquier modelo, con historial de pedidos y movimientos (no se pueden borrar ni fusionar):
   - «18 pro max 256GB»;
   - «iPhone 17 Pro Azul 256Gb», que además está en la línea Nuevo y su única unidad figura como usada.
   - Estrategia: archivarlos (inactivos y conservados) o enlazarlos a mano a su modelo con sus opciones.
5. El «iPhone 13 Pro Max» seminuevo necesita sus opciones cuando exista la paleta.
6. **Las cargas de ofertas que se intentaron y no se aplicaron** (2 trabajos de 11 filas, todas con error) escriben SIM y región **dentro del nombre del modelo**: «iPhone 18 Pro Max (eSIM LLA)», «iPhone 18 Pro (eSIM Japón)», «iPhone 17 (chip)». Los alias (§6) deben convertir ese patrón en opciones, no en modelos nuevos.
7. La carga de productos sin aplicar (667 accesorios) tiene 3 nombres repetidos dentro del mismo archivo; se resuelven en el propio archivo.

## 5. Contrato de catálogo

1. **Modelo principal:** `ProductModel`.
2. **Línea:** `Product.condition_line` Nuevo/Seminuevo.
3. a 6. **Capacidad, color, SIM y región:**
   - siguen siendo atributos de la empresa (`VariantAttribute`), con un rol nuevo: `role = capacity | color | sim | region | other`;
   - los valores (Borgoña, eSIM, LL/A, J/A…) son datos de cada empresa, nunca del código.
7. **Variante válida (APROBADO, decisión 1):** tabla **`ModelOption(model, option)`**, la paleta de cada modelo.
   - `set_variant` rechaza una opción fuera de la paleta cuando el modelo tiene paleta.
   - Todas las variantes de un modelo con paleta llevan **los mismos roles**. Si a un modelo le falta un rol, ninguna de sus variantes lo tiene.
8. **Disponibilidad propia:** sin cambios.
9. y 10. **Disponibilidad del proveedor y cantidad:** ver §6.
11. **Precio de venta:**
    - stock propio = `Product.price`; bajo pedido = `SupplierOffer.commercial_price`;
    - **requiere una aprobación independiente** (decisión 3): `commercial_price_approved_by/at`, escrito sólo por quien tiene el permiso de precios, nunca por la carga de una lista.
12. **Costo del proveedor:** `agreed_cost`, sólo con `costs.view`.
13. **Vigencia:** `valid_from`/`valid_until`, que salen de la **fecha del mensaje**, no del día en que se pega (§7).
14. **Última confirmación:** `last_confirmed_at` y `last_confirmed_job`; sólo avanzan con una confirmación verificable (§7).

**Alias por empresa (APROBADO, decisión 6):**
- `OptionAlias(company, option, alias)`: «borgoña» → Borgoña, «japan»/«japón» → J/A, «chip» → SIM física, y también los patrones «(eSIM LLA)»;
- sólo en el servidor, sólo hacia opciones de la propia empresa y nunca enviados a la IA.

### Criterio de unicidad (APROBADO, decisión 2)

- **Identidad de una variante** = empresa (por el modelo) + modelo + línea + el conjunto ordenado de opciones de **todos** sus roles (capacidad, color, SIM, región y otros). Es el `variant_key` actual, con SIM y región como opciones más.
- **Restricciones:**
  - la base ya impide dos variantes con la misma clave (`UNIQUE` parcial, models.py:279);
  - `set_variant` impide variantes con roles distintos dentro de un modelo con paleta;
  - una opción de otra empresa nunca entra (`ProductVariantOption.save`).
- **Modelos:**
  - el nombre normalizado (minúsculas, espacios colapsados, sin SIM ni región) debe ser único por empresa;
  - la importación nunca crea un modelo con palabras sobrantes;
  - un modelo que coincide al quitar SIM o región va a revisión, no se crea otro.
- **Duplicados existentes:** hoy hay 0.
  - Para el futuro, una consulta de detección (mismo nombre normalizado, o misma clave sin SIM/región) genera una **lista para revisar**.
  - **Nunca se fusiona automáticamente.** Un producto con historial se archiva; no se borra.

## 6. Disponibilidad sin cantidad (APROBADO sólo como consulta, decisión 4)

| Estado público | Cuándo | Qué ve el cliente | ¿Compra? |
|---|---|---|---|
| `own_stock` | stock en la sucursal de despacho | «En stock» | Sí (sin cambios) |
| `other_branch` | D-A: stock en otra sucursal (PR propio, decisión 7) | «Disponible en otra sucursal · Consúltanos» | **No** |
| `supplier` | oferta válida con cantidad confirmada | «Bajo pedido · plazo estimado del proveedor N días» | Sí (flujo actual, sin cambios) |
| `supplier_declared` (nuevo) | oferta activa y vigente, **cantidad desconocida**, `declared_available`, confirmada hace **≤ 7 días** | «Disponible con proveedor · Consultar disponibilidad» | **No.** Sólo consulta, sin carrito, reserva, cobro ni stock |
| `none` | nada de lo anterior | «Agotado» | No |

Reglas:
- `declared_available` sólo puede ser verdadero si `confirmed_quantity` es NULL (CHECK en la base).
- `valid_offer_q`, el carrito y el checkout **no cambian**, así que una oferta declarada no se puede comprar.
- **7 días desde la última confirmación verificable** (no desde el pegado). Pasado ese plazo deja de mostrarse sola; no se desactiva nada.
- El dato público no lleva proveedor, costo ni cantidad (`PublicExposureMatrixTest`).
- **Plazos (decisión 8):**
  - sólo el `lead_time_days` documentado en una oferta **confirmada**, como «plazo estimado del proveedor», que no incluye el envío;
  - ningún plazo para el stock propio;
  - ningún plazo para las ofertas sólo declaradas.

## 7. Reimportar una lista: riesgo confirmado y controles

Comprobado con pruebas desechables sobre `fc72201`, con el flujo real: borrador de texto → archivo → vista previa → aplicar → pago real. Producción tiene hoy 0 ofertas, así que **todavía no ha ocurrido**.

| Escenario | Hoy | Riesgo |
|---|---|---|
| Pegar el mismo texto dos veces | Cada archivo generado tiene otra huella (openpyxl guarda la hora), así que «ya aplicado» no lo detecta | La protección no sirve para WhatsApp |
| La misma lista después de vender 2 de 3 | `confirmed = committed + cantidad del archivo` deja **3 disponibles otra vez** | **Se revenden unidades que el proveedor ya no tiene** |
| El mismo archivo exacto con «confirmar de nuevo» | Igual: vuelven a estar disponibles | La prueba actual `ReapplyTest` lo da por bueno |
| Pegar 4 días después | Las fechas por defecto (hoy a hoy+6) mueven y **alargan** la vigencia | Ofertas vivas con datos viejos |
| Oferta vencida + pegado | Se reactiva sola, con un aviso | Una lista vieja revive una oferta |
| Fila sin cambios | No escribe nada | No queda constancia de que el proveedor confirmó |
| Cantidad vacía sobre una oferta confirmada sin ventas | Pasa a «sin confirmar» sin avisar | Deja de venderse sin que nadie lo note |
| Dos aplicaciones a la vez | Una gana y la otra responde `stale` | Seguro hoy |

**Controles (etapa B0):**
1. **Huella de contenido** en `BulkImportJob`:
   - hash de las filas normalizadas (proveedor, variante, precio, cantidad, plazo y **fecha del mensaje**), sin metadatos del archivo ni fechas por defecto;
   - si coincide con una carga ya aplicada: 409 `already_applied_content`, que sólo se puede seguir con una revisión registrada.
2. **Nunca reponer lo consumido:**
   - cada oferta guarda `last_declared_quantity`, `declared_at` y `committed_at_declaration`;
   - si la nueva declaración es la misma y desde entonces se vendió, lo disponible es «declarado − vendido desde entonces», o va a revisión;
   - «confirmar de nuevo» resta lo vendido.
3. **La vigencia nunca se alarga con contenido idéntico:**
   - las fechas salen de la **fecha del mensaje** que indica el operador; sin fecha, la pantalla la pide;
   - una fecha de mensaje anterior o igual a la ya registrada no renueva nada.
4. **`last_confirmed_at`** sólo avanza con una confirmación verificable: una fecha de mensaje posterior, un contenido distinto o una confirmación explícita y auditada del operador. **Pegar dos veces el mismo mensaje no renueva los 7 días.**
5. **Una oferta vencida no se reactiva** sin una fecha de mensaje posterior o la acción explícita de un revisor.
6. **Cantidad vacía sobre una oferta confirmada:** pasa a ser una decisión en la vista previa, no un cambio silencioso.
7. **Ofertas ausentes de la lista (decisión 5):** se informan y no se desactivan. El modo «lista completa» sigue siendo el único que apaga ofertas, con doble confirmación.
8. **Concurrencia:** sin cambios (bloqueos por proveedor y variante, huella de vista previa). La comprobación de contenido se hace bajo el mismo bloqueo.

Pruebas existentes que cambian de expectativa:
- `ReapplyTest.test_the_same_file_after_sales…`;
- `test_an_offer_switched_off_only_because_it_expired_comes_back_on`.

## 8. Asistente de ofertas de proveedor

**Pegar → Reconocer → Resolver dudas → Comparar → Confirmar**, distinto del ingreso de equipos por IMEI.

1. **Pegar:**
   - proveedor, **fecha del mensaje**, vigencia y plazo;
   - **clasificación de los importes** (decisión 3): el operador indica una vez si son *costo* o *precio de referencia del proveedor*. Para clasificarlos como costo necesita `costs.view`, y se pide una confirmación explícita;
   - **un importe nunca se convierte en precio de venta**, que requiere su propia aprobación.
2. **Reconocer** (en el navegador, determinista):
   - gramática de una línea por oferta;
   - N colores dan N configuraciones;
   - frases de color compuestas primero; `y` y la coma separan;
   - las palabras desconocidas quedan como duda, **nunca en el modelo**;
   - se quitan IMEI y contactos;
   - nunca se infiere una cantidad.
3. **Resolver dudas** (servidor):
   - el modelo por nombre exacto; las opciones **sólo en la paleta del modelo** y en los alias;
   - cada configuración queda como: variante existente / combinación válida sin variante (se propone crearla, con permiso) / fuera de la paleta / incompleta / ambigua.
4. **Comparar:** nuevas, precio cambiado, sin cambio, **no mencionadas (sólo informativo)** y en revisión, con los controles de §7.
5. **Confirmar:** con el `apply` existente, más los controles de §7.

## 9. Ficha de compra

- Galería de la variante elegida, que arranca en la imagen principal (#254).
- Nuevo / Seminuevo como pestañas.
- Opciones en el orden de sus roles: capacidad → color (con muestras) → SIM → región. SIM y región sólo aparecen si el modelo los tiene.
- Sólo configuraciones existentes. «Agotado» se distingue con texto de «no existe».
- Precio y estado según §6, con `aria-live`.
- **Un botón según el estado:** comprar / bajo pedido / consultar disponibilidad (WhatsApp con la variante) / consultar en otra sucursal / agotado.
- En el celular: barra fija con precio y botón, y destinos táctiles de 44 px.
- **Seminuevos:**
  - tarjetas con batería, grado, fotos y precio; el botón nombra batería y precio;
  - enlace propio `?unit=<id>`;
  - compra del equipo exacto (ya existe).
- **Sin cambios** en carrito, checkout, reservas ni pagos.

## 10. Decisiones del propietario (2026-11-10)

| # | Decisión | Resolución |
|---|---|---|
| 1 | Paleta de opciones por modelo | **APROBADO** |
| 2 | SIM y región como atributos de variante | **APROBADO**, con la identidad y la unicidad de §5 (verificadas: hoy no hay datos que lo bloqueen) |
| 3 | Importes de WhatsApp | **Nunca se asumen como precio de venta.** El operador los clasifica (costo o precio de referencia) según sus permisos y con confirmación explícita. El precio de venta requiere una aprobación independiente |
| 4 | Ofertas sin cantidad | **APROBADO sólo como consulta**, sin carrito, reservas, cobros ni stock ficticio. Vigencia de **7 días** desde la última confirmación verificable |
| 5 | Ofertas ausentes de una lista | **Informar, no desactivar** automáticamente |
| 6 | Alias por empresa | **APROBADO** |
| 7 | D-A | **Sólo consulta** de disponibilidad en otras sucursales, en un PR independiente. **D-B sigue NO AUTORIZADO** |
| 8 | Plazos | Sólo los plazos estimados documentados del proveedor; **ningún plazo inventado** para el stock propio |

### Decisiones nuevas que surgen de esta revisión

- **R-1, controles de reimportación (§7):** son un riesgo actual del código de master; se proponen como la etapa B0, la primera.
- **R-2, sobreventa de productos por cantidad (§11):** alertar al personal y bloquear la entrega de un pedido con faltante. **DECIDIR.**
- **R-3, limpieza del catálogo (§4):** «Seminuevos» y «Adaptadores y Cables» como modelos, y los 2 teléfonos sueltos. **DECIDIR:** lo hace el propietario desde el panel, o se prepara una lista guiada.
- **R-4, las 22 combinaciones:** con colores compuestos («glaciar negro» = un color) la lista da **19 configuraciones**; si «glaciar X» fueran dos colores, **23**. Ninguna lectura da 22. Las pruebas cubren las dos (§12). **CONFIRMAR** la lectura esperada. *(2026-11-13: sigue abierta. Con R-COL la paleta decide: 19 con los compuestos; 19 con 4 `ambigua` sin ellos. El «22» sale de dos lecturas posibles, ninguna adoptada. P1 y P2 siguen pendientes del propietario; P4 aprobada: una línea sin color es `incompleta`.)*


### 10.1 Decisiones de A1b-2 (2026-11-12)

| # | Decisión | Resolución |
|---|---|---|
| S1 | Alcance del control de publicación | Sólo las variantes creadas por el flujo A1b-2 (`draft_origin='palette'`); las reglas históricas no cambian |
| S2 | «Precio aprobado» | Precio comercial > 0, auditado; sin aprobación explícita en esta fase |
| S3 | Borradores | Visibles en panel y exportación con estado identificable; nunca crean stock, nunca públicos, nunca vendibles |
| S4 | Nombre | Generado desde el modelo y sus opciones, con vista previa editable; sin `name_reviewed` |
| S5 | Serialización | No obligatoria de forma universal; rigen las reglas de cada línea e inventario |
| S6 | Caja a 0,00 | Ya bloqueada en master (#196); sin cambio global |
| S7 | Configuraciones parciales | No se persisten (sólo estado del formulario) |
| S8 | `catalog_proposal.apply` | Contrato intacto; alineación futura documentada |
| T8 | Transición | `draft_origin` = procedencia inmutable; `sale_enabled_at/by` = autorización explícita y auditada; sin «deshabilitar»; defensa central en las definiciones públicas y vendibles; `publish=True` opcional (por defecto `False`); columna «Origen» en reimportación pendiente de prueba real |

Detalle y texto normativo: DEC-CATALOG-VARIANT-DRAFT-01 en `05_DECISIONES_TECNICAS.md`.

## 11. Sobreventa de productos por cantidad (investigación, sin cambios)

Reproducido con pruebas desechables sobre `fc72201`:
- 7 casos;
- pagos seguidos y simultáneos;
- web contra caja.

**Lo que ocurre:**
- **El stock nunca queda negativo:** lo impiden el bloqueo de fila, `InsufficientStockError` y un CHECK en la base.
- **El segundo pedido queda pagado y con faltante:**
  - el cobro queda capturado;
  - el cliente recibe la confirmación normal;
  - el faltante sólo queda en `payment_error`.
- **El personal no recibe ningún aviso.** Sólo hay una insignia y un filtro en la lista de pedidos.
- **Un pedido con faltante se puede llevar hasta «entregado»** sin ninguna salida de stock.
- La caja puede vender la última unidad mientras un pago web espera: es el caso **más probable** en una sola tienda.
- Hoy se arregla a mano: reponer y reprocesar, o anular con devolución en el panel de la pasarela.

**Opciones, de menor a mayor impacto:**
1. **Avisar al personal y bloquear la entrega mientras falte stock.** Toca la preparación del pedido y las notificaciones; **no** toca checkout, pagos ni reservas. Recomendado como PR independiente (F1), **pendiente de decisión R-2**.
2. Volver a comprobar el stock al abrir el intento de pago. Toca pagos: **requiere D-B** o una autorización más acotada.
3. Una reserva corta en el checkout. **Es D-B completa.**
4. Devolución automática por la pasarela. Requiere D-B y una decisión sobre devoluciones.

**Relación con este ADR:**
- con SIM y región como variantes, hay más productos con «la última unidad», así que la carrera ocurre más a menudo en los productos por cantidad;
- los equipos de un modelo se venden por unidad con apartado y no se ven afectados;
- la consulta del proveedor y la de otra sucursal no interactúan.

## 12. Pruebas de la lista de entrenamiento

Los 14 mensajes y sus configuraciones candidatas quedan registrados en `docs/catalog-commercial/training-lines.json`, con **una paleta de prueba** que no asume que todas sean válidas:
- variantes existentes;
- combinaciones válidas sin variante;
- opciones fuera de la paleta;
- campos que faltan;
- frases de color ambiguas;
- una segunda empresa con la misma paleta en nombre pero opciones propias (aislamiento).

B1 las convierte en pruebas de Jest (reconocedor) y del backend (resolución). Además se prueban:
- importes sin clasificar;
- la lista pegada dos veces (no renueva nada);
- la lista después de ventas (no repone);
- una oferta vencida (no revive);
- un usuario sin `costs.view` (no puede clasificar como costo).

## 13. Plan de implementación ordenado

| Orden | PR | Contenido | Migración | Depende de |
|---|---|---|---|---|
| 1 | **B0** | Controles de reimportación (§7): huella de contenido, lo consumido no se repone, la vigencia sale de la fecha del mensaje, sin reactivar lo vencido, `last_confirmed_at/job` | Sí (aditiva) | Aprobación de este ADR |
| 2 | **A1** · **IMPLEMENTADO en master** (A1 #269 `27e3503`, A1b-1 #271 `402ce17`, A1b-2a #274 `e796795`, A1b-2b #275 `fc0a513`; **sin desplegar**, `0165`–`0167` pendientes) | `VariantAttribute.role`, `ModelOption` y validación en `set_variant`; pantalla «Opciones de este modelo»; columnas por rol en importar y exportar; normalización del nombre de modelo. Añadido en A1b-2: borradores desde la paleta con procedencia inmutable y habilitación comercial explícita (DEC-CATALOG-VARIANT-DRAFT-01). Pendiente: columna «Origen» en la reimportación (T8-4) | Sí (aditiva: `0165`, `0166`, `0167`) | #254 y #255 fusionados |
| 3 | **A2** · A2-1 **IMPLEMENTADO en master** (#277 `9841966`, backend; **sin desplegar**, `0168` pendiente); A2-2 frontend **EN REVISIÓN** (PR #279) | `CatalogAlias` (palabra de modelo, modelo y opción; el patrón «(eSIM LLA)» se lee como opciones dentro del nombre y va a revisión); resolutor de sólo lectura con R-COL; `resolve_variant_spec` con alias, SIM y región (DEC-CATALOG-ALIAS-01) | Sí (aditiva: `0168`) | A1 |
| 4 | **C1** | `declared_available` con CHECK, `commercial_price_approved_by/at`, estado público `supplier_declared` (7 días) sin compra | Sí (aditiva) | B0 |
| 5 | **B1** | Reconocedor de una línea por oferta y emparejador extendido; pruebas de §12 | No | A2 |
| 6 | **B2** | Asistente «Actualizar lista de proveedor» (fecha del mensaje, clasificación de importes, no mencionadas informativas) | No | B0, B1, C1 |
| 7 | **C2** | D-A: consulta de otra sucursal | No | Independiente |
| 8 | **D1** | Ficha de compra | No | C1 (consultar) y C2 (otra sucursal) |
| 9 | **E** | Guías y capacitación | No | Cada etapa |
| — | **F1** | Aviso al personal y bloqueo de entrega con faltante (§11) | Por definir | **Decisión R-2** |

## 14. Matriz de dependencias con los PR abiertos

| PR | Estado (2026-11-10) | Relación con este plan |
|---|---|---|
| #254 imágenes por variante | Abierto, sin conflictos | **Antes de A1**: toca `CatalogModelEditor`, `ModelDetail`, `device_catalog.py` |
| #255 cargas masivas | Abierto, sin conflictos | **Antes de A1**: toca `import_services` (columnas por rol). Comparte `internal-api.ts` con #254; se combinan solos (verificado) |
| #256 pagos fijos | Abierto, CI verde | Independiente |
| #257 tramos de subida | Abierto | Independiente; requiere ajustar el Caddyfile de producción al desplegar |
| #258 docs de #254–#257 | Abierto | Se fusiona después de #254–#257. **Conflicto** con #259 en `02_ESTADO_ACTUAL.md` y `07_CHANGELOG.md`: el segundo en fusionarse se ajusta conservando las dos entradas |
| #259 docs R4.4 | Abierto | Igual que #258 |
| #260 este ADR | Borrador | Se fusiona cuando el propietario lo apruebe |
| #162 seminuevos externos (borrador) | Sin fusionar | Toca `supplier_offer_import.py` y `SupplierOffer`: hay que coordinarlo con B0 y C1 |
| #249 orden de bloqueos (borrador) | Sin fusionar | Afecta a `commit_order_lines` y a la caja; F1 no debe chocar con él |
