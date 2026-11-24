# Plan maestro comercial 2026: auditoría real y orden de PR

Este documento responde a la orden del propietario «PLAN MAESTRO COMERCIAL 2026» del 2026-11-23. Su objetivo es convertir los módulos comerciales existentes en un sistema integral, sin reemplazar lo que ya funciona.

La auditoría (Fase A) se hizo en sólo lectura sobre `master` `e5e1e70`. Las rutas citadas son relativas a `backend/store/` (backend) o a `frontend/app/` (frontend).

Producción (prod-02) en esa fecha: `16189e4`, esquema `0186_label_printing`. La próxima migración libre es la `0187`.

Leyenda de estados:
- **IMPL**: implementado.
- **PARC**: parcial.
- **PEND**: pendiente.
- **PROP**: propuesta.
- **OBS**: obsoleto.

## 0. Coordinación de producción

- **LABEL-04: DESPLEGADO, AUTORIZACIÓN NO ACREDITADA.** La evidencia está en `docs/NEXT_ACTION.md`. El propietario debe confirmar si dio ese GO.
- **Un solo ejecutor.** Se aplican el candado `backups/ops/prod_lock.sh` y las reglas de `AGENTS.md` §10.
- **Nada de este plan se despliega sin un GO propio.**

## 1. Matriz por módulo

### B. Catálogo y variantes

| Capacidad | Estado | Evidencia y nota |
|---|---|---|
| Modelo → variante → unidad | IMPL | `Product.product_model` + `variant_key`. La restricción `unique_variant_per_model_line` impide duplicados. `StockUnit.product` es PROTECT. |
| Marca | PARC | No hay entidad «Marca»: la categoría hace de marca. **Decisión del propietario:** ¿hace falta una entidad propia? |
| Colores y capacidades propios de cada modelo | IMPL | `ModelOption`, importación de la matriz de referencia y `docs/catalog-reference-matrix.csv`. |
| Sin combinaciones imposibles | PARC | `check_variant` las impide sólo si el modelo tiene paleta. Las exclusiones no tienen pantalla. |
| Ver configuraciones válidas, con o sin variante, e incompletas | IMPL | `model_palette.payload` y `ModelPalettePanel`. |
| Crear variante desde la matriz; editar precio, publicación y atributos con reglas de historial | IMPL | `device_catalog.py` (`create_variant_product`, `set_variant`, `edit_rules`). |
| Buscar modelo en el panel | PEND | `CatalogModelsManager.tsx` no tiene búsqueda. |
| Desactivar desde el editor del modelo | PARC | Sólo se puede desde `ProductForm`. |
| Archivar | PEND | No existe el estado. |
| Borrado seguro con explicación de bloqueos | PEND | No hay endpoint. **Riesgo alto:** siete relaciones CASCADE hacia `Product` (imágenes, carrito, reseñas, códigos de barras, opciones, compatibilidades y referencias de proveedor). |
| Normalización 1.1 | IMPL en el repo | `catalog_normalize.py` y el comando `normalize_device_catalog`. En producción espera su GO. |

### C. Disponibilidad semanal de proveedores

| Paso o regla | Estado | Nota |
|---|---|---|
| Proveedor, fecha, texto pegado, Excel, reconocimiento de modelo, capacidad, color, SIM y región, emparejamiento, conflictos, aplicar todo o nada | IMPL | `supplier_offer_import.py`, `configuration_resolver.py` y el asistente B1-1. |
| Stock propio ≠ disponibilidad del proveedor; la baja de un proveedor sólo afecta a ese proveedor; nunca se borran ventas comprometidas; sin duplicados; vencimiento | IMPL | `supply_services.py`, restricciones de `SupplierOffer` y el cron `expire_supplier_offers`. |
| CSV o .xls | PEND | Sólo .xlsx. |
| Precio sugerido | PEND | Requiere una regla de margen. **Decisión del propietario.** |
| «Configuración válida sin variante» en Excel | PARC | Hoy da un error genérico. |
| Panel semanal con modelo, capacidad y color, y cambios contra la semana anterior | PARC/PEND | Hay lista de ofertas y vista previa, pero no vista semanal ni diferencias. |
| Estado de la oferta con proveedor inactivo | Defecto medio | `offer_state` la muestra «Vigente» aunque no venda. |
| Precio por debajo del costo | Defecto medio | No hay control. |

### D. Venta rápida de equipo nuevo (caja)

| Comportamiento | Estado | Nota |
|---|---|---|
| Elegir modelo, capacidad, color, SIM y región por ids; crear la combinación exacta; proveedor; precio con permiso | IMPL | `pos_device_services.py`. |
| IMEI 1, IMEI 2 y serie con control; nunca dos veces; recepción idempotente con Kardex; venta y comprobante sin duplicar; lector, teclado y móvil | IMPL | Restricciones únicas, candados por identificador y `create_pos_sale`. |
| Bajo pedido de una variante existente | IMPL | `supply_services.py`. |
| Recibir en caja el equipo de un pedido pendiente | PARC | La API existe, pero la caja no la usa. **Riesgo alto:** «Llegó el equipo» ignora los pedidos pendientes de esa variante. |
| Bajo pedido de una combinación sin variante | PEND | La regla D-F-1 lo prohíbe. **Decisión del propietario.** |
| Cobro al llegar o adelanto | PEND | Requiere un ADR. |
| Orden de candados entre caja y pago web | Riesgo medio | #249 sigue abierto. |

### E/K. Carga del stock real

| Punto | Estado | Nota |
|---|---|---|
| Importación de equipos: vista previa, Luhn, variante, CSV de errores, todo o nada | IMPL | `unit_import_services.py`. |
| Exportar los equipos actuales para no volver a registrarlos | IMPL | `inventory_exports.py`. |
| Distinguir inventario inicial, compra y corrección | IMPL | `StockMovement`. |
| Inventario inicial por tandas | Defecto alto | `opening_problem` rechaza la carga inicial si el producto ya tiene un movimiento en la sucursal. Obligaría a registrar compras ficticias. |
| Proveedor, fecha real de ingreso y notas en la carga | PEND | `received_at` siempre es la hora de la carga. |
| Simulación de Kardex y comparación antes y después | PEND | |
| Comprobación de la regla «stock = equipos disponibles» y anulación por lote | PEND | |
| Plantillas separadas para nuevos y seminuevos | PARC | Hoy es una sola, con columna «Línea». |
| Seminuevo como unidad propia: batería, grado, precio, fotos | IMPL | `StockUnit` y `UnitPhoto`. |
| Inspección, publicación por unidad, garantía, estado funcional, defectos y accesorios | PEND | **Riesgo alto:** un seminuevo con precio se publica sin fotos ni inspección. |
| Pendiente de inspección y recibido como parte de pago | PEND/PROP | |
| PR #162 (seminuevos en el proveedor) | OBS | Está en conflicto y su migración `0146` ya está ocupada. Se cierra y se conserva como referencia. |

### F. Catálogo visual e imágenes

| Punto | Estado | Nota |
|---|---|---|
| Configurador: foto exacta del acabado, conserva la configuración compatible, precio y disponibilidad, carrito y checkout con la foto del color, caja sólo con fotos verificadas | IMPL | Fase 1.2 y #396. |
| `/product/<variante>` y el historial de pedidos con la regla de acabados | PARC | |
| Fotos reales de seminuevos con procedencia, principal y orden | PARC | **Riesgo alto:** son públicas sin procedencia y se rotulan «Fotos de este equipo». |
| Imágenes propias de la variante con licencia | Defecto alto | Son públicas sin verificar y tienen prioridad sobre la foto del acabado. |
| Quitar fondo | PEND | Requiere un ADR y aprobación del propietario: modelo, licencia y ejecución local. |
| Detectar fotos rotas o equivocadas; cobertura de seminuevos | PEND/PARC | |

### G. Cotizaciones comerciales

| Punto | Estado | Nota |
|---|---|---|
| Módulo propio | PEND | `checkout_quote_views.py` sólo da el desglose de IGV del carrito web. `quote_ticket.py` es de reparaciones. El menú la marca «pending». |
| Piezas reutilizables | — | `document_layout.render_a4`, ticket de 80 mm, `escpos`, `sequences.allocate`, el patrón de revisiones de `RepairQuote`, `resolve_discount`, `PosPriceChanged` y la idempotencia de `create_pos_sale`. |

### H. Parte de pago

| Punto | Estado | Nota |
|---|---|---|
| Todo el dominio | PROP | `docs/adr-trade-in.md`. Las decisiones D1–D12 están todas pendientes. |
| Permitido ya | — | Sólo una valoración referencial no vinculante dentro de la cotización. Sin IMEI completo, DNI ni fotos; sin crédito, stock ni `Order`. |
| Riesgo medio | — | «Llegó el equipo» o la recepción pueden meter un usado como disponible sin inspección. |

### I. Menú del panel

Ya existen los destinos de Productos y de Inventario. Faltan dos cosas:
- **Ventas:** es una lista plana. La venta rápida no tiene entrada propia, Documentos no tiene entrada y Cotizaciones está «pending».
- **Disponibilidad de proveedores:** está repartida entre varias pantallas.

## 2. Orden de PR (ajustado por dependencias)

Las migraciones van en serie: un solo PR con migración en curso a la vez, en el orden 0187, 0188… Los PR sin migración pueden avanzar en paralelo.

**Ola 1: seguridad del dato y carga real (prioridad del propietario) — FUSIONADA el 2026-11-24 (#400 `1ce08caa`, #401 `ca719bde`, #402 `b47c2da1`, #403 `40be4fe7`), sin desplegar**
1. **INV-LOAD-01**: inventario inicial por tandas sin compras ficticias; proveedor, fecha de ingreso y notas en la carga y en «Registrar equipo»; aviso de serie en otra variante; simulación de stock antes y después en la vista previa. Sin migración.
2. **POS-ORDER-ARRIVAL-01**: «Llegó el equipo» ofrece primero la línea de pedido pendiente y responde 409 si hay pedidos sin atender. Sin migración.
3. **SUPPLY-GUARDS-01**: estado real de las ofertas de proveedores inactivos y aviso de precio menor que el costo. Sin migración.
4. **USED-READY-01** (migración 0187): inspección y publicación por unidad, garantía, estado funcional, defectos y accesorios. Un seminuevo sólo se ofrece en la web si está inspeccionado, publicado, tiene precio y al menos una foto real.

**Ola 2 — FUSIONADA (#404, #405, #407, #409, #411, #413), sin desplegar**
5. **INV-LOAD-02**: comprobación `check_unit_stock`, anulación de un lote y comparación antes y después. Sin migración.
6. **VARIANT-SAFE-01** (migración): archivar; borrado con verificación de dependencias bajo bloqueo y un test guardián sobre todas las relaciones; las FK pasan a PROTECT.
7. **CATALOG-ADMIN-UX-B**: búsqueda, filtros, acciones por fila y rendimiento del editor.
8. **SUPPLY-WEEKLY-01**: panel semanal con diferencias y «no mencionadas»; «configuración válida sin variante» también en Excel.
9. **NAV-SALES-01**: destinos de Ventas y de Disponibilidad de proveedores, sin duplicar entradas.

**Ola 3 — FUSIONADA (#408, #410, #414, #415), sin desplegar; fotos autorizadas pendientes**
10. **SALES-QUOTE-01** (migración): modelo, servicios, documentos A4, 80 mm y ESC/POS, panel, y conversión idempotente en venta con revalidación de precio y stock.
11. **TRADE-IN-QUOTE-00**: bloque «equipo ofrecido (referencial)» en la cotización, no vinculante, con un test guardián que impide el crédito en `Order` y el uso de `manual_discount`.
12. **UNIT-PHOTO-02** (migración): procedencia y atestación de foto real, principal y orden; el carrito y el pedido muestran la foto de la unidad.
13. **IMG-GATE-01** e **IMG-HEALTH-01**: licencia en las imágenes de variante; fotos rotas, huella y cobertura.

**Bloqueados por decisión del propietario o un ADR:** quitar fondo, bajo pedido de una combinación nueva (D-F-1), cobro al llegar, estado «declarado por el proveedor» (C1), precio sugerido, liquidación de parte de pago (D1–D12) y entidad Marca.

## 3. Decisiones pendientes del propietario

1. ¿Una fila de auditoría impide borrar una variante, o sólo se conserva? ¿Quién puede borrar?
2. ¿Entidad Marca propia, o basta la categoría?
3. ¿Mínimo de fotos para publicar un seminuevo? Por defecto se propone 1.
4. ¿Garantía por defecto de los seminuevos (días)?
5. ¿Regla de precio sugerido para las listas de proveedor?
6. D-F-1: ¿se puede crear una variante para vender bajo pedido sin el equipo?
7. Quitar fondo: ¿se aprueba un modelo local y cuál?
8. Parte de pago: D1–D12, con el contador donde corresponda.
9. Si se confirma el GO de LABEL-04.
