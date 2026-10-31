# Changelog

Este archivo no existía en el baseline. La fuente histórica sigue siendo
[CHANGELOG.md](CHANGELOG.md).

## 2026-10-31 — TRADE-IN-01 (R4): decisiones clasificadas y propuesta operativa (BLOQUEADO POR DECISIÓN)

**Changed**
- `docs/decisiones-propietario-trade-in.md`: separa lo que ya cerró el equipo técnico (reversible hasta implementar) de lo que decide el propietario, el contador y legal, y añade la propuesta operativa recomendada de punta a punta. **Nada se implementó**: ni intake, ni crédito, ni stock, ni cambios fiscales. La caja sólo anuncia la opción, deshabilitada.
## 2026-10-31 — SALES-UX-02: recorrido de la caja en cinco pasos (no desplegado)

**Changed** (sólo frontend; sin migraciones; el servidor sigue siendo la única autoridad de precios, descuentos, impuestos y totales)
- Caja: barra de pasos Productos → Cliente → Forma de pago → Comprobante → Revisar, con el motivo visible de lo que falta; secciones en ese orden en una sola pantalla; «Revisar y cobrar» muestra cada equipo (estado, batería, IMEI/serie enmascarado, precio propio), el accesorio, descuentos, subtotal, impuestos del servidor, total, cliente, vendedor, medio de pago (vuelto) y tipo de documento, antes de «Cobrar». En tableta y móvil una barra fija lleva el total y lleva a la revisión.
- Comprobante: la nota de venta interna se rotula «Interno · no fiscal»; boleta y factura se rotulan «Comprobante electrónico · BETA» y, con la emisión apagada, «no operativas» (o «no disponibles para tu cuenta» si el operador no puede emitirlas).
- «Nuevo cliente» se crea sin salir de la caja (diálogo con foco atrapado, Escape bloqueado mientras guarda, duplicados por teléfono o correo avisados); un documento repetido ofrece usar al cliente existente.
- «Equipo como parte de pago» se anuncia **deshabilitado**: no registra crédito, stock ni pago (TRADE-IN-01 sigue en propuesta).

**Sin cambios**: `charge()`, `expected_total`, la clave de idempotencia, la guarda de vista previa vencida, los endpoints web y v1, el inventario y el e-commerce.
## 2026-10-31 — FIN-REMIND-01: pagos fijos, vencimientos y recordatorios internos (no desplegado)

**Added**
- Recordatorios internos de pagos fijos y obligaciones por pagar. Política por empresa (`FinanceReminderPolicy`: avisos 7/3/1/0 días antes por defecto, avisos de vencido espaciados y con tope, correo opcional y apagado por defecto), «Posponer aviso» por persona (`ObligationReminderSnooze`) y el motor `store/finance/reminders.py` sobre `notification_services.emit`. Migración `0160_finance_reminders` (aditiva: dos tablas).
- Comando `run_finance_schedule` (genera los períodos y envía los avisos, por empresa en su fecha local), idempotente, con candado de PostgreSQL y presupuesto de tiempo; sale con error si hubo fallos. Línea nueva en `deploy/crontab.example` (con `flock`). `ops_status` avisa si pasan más de 1 h sin pasada o si la última falló.
- API: `GET/PATCH /api/admin/finance/reminder-policy/` (`finance.automation.manage` + alcance de toda la empresa), `POST/DELETE /api/admin/finance/obligations/<id>/snooze/` (sólo silencia a quien lo pide).
- Pantalla `Finanzas` (`/admin/finance`): resumen por moneda, pestañas (Próximos, Hoy, Vencidos, Parciales, Completados, Reglas), calendario, filtros, detalle con pagos/historial/avisos, registrar y revertir pago, posponer aviso, reglas y política de recordatorios. La campana abre `/admin/finance?obligation=<id>`.

**Changed**
- `notification_services.emit` acepta `email=`; el aviso de correo de un pago lleva botón y un párrafo por línea.

**Reglas que se mantienen**: un aviso nunca registra pagos, cambia saldos ni estados; no hay WhatsApp; sólo ven el aviso quienes tienen `finance.view` con acceso a la sucursal (un pago general: alcance de toda la empresa); el balance viaja en el texto, no en el payload; no se envía nada de obligaciones por cobrar.

**Antes de desplegar**: instalar la línea de `run_finance_schedule` en el crontab del servidor (el crontab de producción hoy NO programa ningún comando de finanzas) y revisar la política de cada empresa. Producción sin cambios.

## 2026-10-30 — INV-COM-01C: la caja vende cada seminuevo a su precio de ficha (no desplegado)

**Changed**
- En la caja web y en POS v1, un equipo serializado de la línea seminueva se cobra a su `StockUnit.sale_price`; nuevos y accesorios siguen a `Product.price`. Un equipo sin precio válido (nulo, 0 o inválido) no se vende hasta que alguien con `products.manage` lo precie. El costo nunca decide el precio y el cliente no puede imponerlo.
- Una sola autoridad (`pos_services.unit_prices`) para la vista previa y la venta; cada equipo es una línea (`OrderItem.stock_unit`) con su precio, su costo y su reparto de descuento. La salida de Kardex sigue siendo una por (venta, producto) y nombra ambos equipos. Las promociones y combos no se aplican a un equipo con precio propio.
- `expected_total` (opcional): un precio cambiado entre la vista previa y el cobro responde 409 `price_changed` con la vista previa nueva, antes de validar el efectivo; los clientes que no lo envían (POS v1, móvil) se cobran con el precio del servidor, como antes. El 409 no reserva la clave de idempotencia.
- Pantalla de caja: ficha del equipo (estado, batería, grado, precio propio), aviso «Este seminuevo todavía no tiene precio de venta. Solicita que un usuario autorizado lo configure», «Cobrar» bloqueado mientras la vista previa no corresponde a la cesta, el subtotal y los precios de los equipos salen de la vista previa del servidor.

**Added**
- Backend 42 casos (`test_pos_unit_price`, `test_pos_unit_price_api`); frontend 25 casos (`pos-unit-price.test.ts`); E2E real `pos-unit-price.spec.ts` (escritorio y 390 px).
- `docs/decisiones-propietario-trade-in.md`: decisiones D1–D12 de TRADE-IN para el propietario, el contador y legal. `docs/adr-trade-in.md` corregido (intake→venta no único, `pending_approval`, la venta de caja nace pagada).

**Sin migraciones. Sin cambios fiscales ni SUNAT. Producción sin cambios.** Antes de desplegar: todo seminuevo disponible con `sale_price` nulo o 0 dejará de poder venderse en caja; hay que preciarlo antes.

## 2026-10-30 — INV-COM-01B: destino comercial de cada línea de la recepción (no desplegado)

**Added**
- El paso 3 de «Recibir mercadería» muestra, por línea, modelo → línea → variante → unidades, dónde se ofrece en la tienda, qué falta para publicarlo y si la sucursal es la de despacho. El paso 4 dice qué queda por completar en cada seminuevo (precio y fotos). `receipts/preview` añade `commercial` por línea (lectura; sin precios ni identificadores). Sin migraciones.

## 2026-10-30 — INV-COM-01A: precio propio y detalles de un seminuevo en la recepción (no desplegado)

**Added**
- `receipts/preview|confirm` aceptan `sale_price` y `public_notes` por equipo de un artículo seminuevo (`sale_price` exige `products.manage`; 400 `prices_forbidden` si se envía sin ella). Campos de precio y detalles para el comprador en la recepción individual y en la lista pegada.

**Changed**
- Un artículo nuevo o una línea por cantidad no aceptan precio propio. Un seminuevo sin precio se recibe y queda pendiente (aviso en la vista previa). La respuesta y la auditoría no llevan importes. Sin migraciones.

## 2026-10-30 — Despliegue controlado: INV-RECEIVE-UX-02 + NAV-UX-01 + NAV-UX-02

**Released — IMPLEMENTADO — DESPLEGADO**

- Producción pasa de `b21c7d7` a `d52e9b8794151c2abca1061ee7b3c483886e8440` (11:58 Lima). Backend `release-d52e9b8` / `37cb59d55205`; frontend `release-d52e9b8` / `6f67fc548f35`, ambos healthy. CI 8/8 PASS; builds, `check`, plan sin migraciones y healthcheck PASS. Esquema `0159`, ninguna migración ejecutada.
- Respaldo nuevo de BD y archivos, copia externa con SHA-256 coincidentes y restauración local aislada PostgreSQL 16.15 PASS: 132 tablas, 2.102 filas, 131 secuencias y 15 archivos; recursos desechables eliminados.
- Humo HTTP y lecturas autenticadas de caja, catálogo e inventario PASS; preview 200/403 según permiso y reconocimiento de lista 200, sin escrituras comerciales. Nueve conteos de negocio sin cambio; 0 errores y 0 HTTP 5xx en los registros examinados desde el despliegue.
- SUNAT BETA permanece desactivado. Sólo se recrearon backend y frontend; PostgreSQL, Caddy, Caddyfile, variables y Compose intactos. Rollback por código `rollback-b21c7d7` conservado y verificado, sin ejecutarlo ni restaurar la BD productiva.
- Humo visual autenticado PASS tras el inicio de sesión del propietario: menú y herramientas, pestañas, enlace Productos a Recibir, lista vacía y regreso al paso 1, caja; sin operaciones ficticias y sin desborde en los tamaños comprobados. **Pendiente:** aceptación funcional expresa del propietario; el humo técnico no la sustituye. Las fases posteriores y decisiones de producto no se despliegan ni implementan en esta operación.

## 2026-10-30 — NAV-UX-02: pestañas por destino, página «Más herramientas» y recibir desde Productos (#214, no desplegado)

**Added**
- Barra de pestañas por destino dentro de la página; página `/admin/inventory/tools` por tarea; enlace «Recibir mercadería» en la cabecera de Productos; enlaces a las cargas oficiales de Excel en el paso 2 del asistente.

**Changed**
- El «Modo avanzado» del Centro de operaciones va agrupado por tarea. «Publicado» avisa cuando el artículo está inactivo. Sin cambios de rutas, permisos ni backend.

## 2026-10-30 — NAV-UX-01: menú lateral por destinos (#213, no desplegado)

**Changed**
- El menú lateral de Productos e Inventario pasa de 25 enlaces planos a nueve destinos (Catálogo: Productos y variantes, Proveedores; Inventario: Centro de operaciones, Recibir mercadería, Stock y equipos, Recuentos, Etiquetas, Movimientos, Más herramientas). Las demás pantallas son opciones del destino. Sin cambios de rutas, permisos ni backend.

**Added**
- `sidebarSections` / `SIDEBAR_DESTINATIONS` sobre el registro de módulos (que no cambia), 61 pruebas y un E2E real en Chromium (1280, 768, 390 con toque) y WebKit.

## 2026-10-30 — INV-RECEIVE-UX-02: puerta de seguridad final (#212)

**Fixed**
- «Descartar lo que falta» ya no borra la lista entera: pregunta con las cuentas y solo descarta lo que no se puede recibir; lo que no cabe en una recepción (200 equipos / 50 artículos) se puede dejar en espera.
- Las filas en espera ya no entran a la recepción actual aunque el catálogo las reconozca (marca «En espera», «Volver a incluir», «Registrar otra recepción» las libera).
- Una fila nueva hereda el artículo ya elegido de su combinación; «Son nuevos» ya no borra batería ni estado.
- El color se limpia de identificadores antes de consultar el catálogo (cliente y servidor); la clave de «crear lo aprobado» sale del generador seguro y cambia con la aprobación.

## 2026-10-30 — INV-RECEIVE-UX-02: asistente inteligente de ingreso de mercadería (#210, en master `b49bcf2`, no desplegado)

**Added**
- Pestaña «Pegar una lista» en el paso 2 de «Recibir mercadería»: reconocimiento local, búsqueda automática del catálogo (una pregunta por combinación), asociación automática de lo exacto, corrección por grupos, propuesta de variantes faltantes (reutiliza `CatalogProposalPanel`), ficha por equipo (serie, IMEI, estado, batería, estado estético, observaciones, costo con permiso), resumen de avance, «Resolver pendientes», flujo de lector con Enter, filas en espera para otra recepción y revisión con identificadores enmascarados.
- API de coincidencias: `is_serialized`, `requires_imei` e `is_active` en cada opción (sólo lectura).
- Pruebas: lógica pura, pantalla con catálogo falso, flujo del backend de 11 equipos de punta a punta y spec E2E en navegador real.

**Changed**
- `receipts/preview|confirm` reciben `battery_health`, `cosmetic_grade` y `notes` por equipo desde la pantalla (el servidor ya los aceptaba); un equipo nuevo no los envía. El panel de propuesta de catálogo tiene objetivos táctiles de 44 px y no deja un botón inútil tras crear.

**Fixed (durante la revisión)**
- Respuestas tardías del catálogo, segunda pegada, ids repetidos tras volver al paso 1, edición de modelo que reconstruía la tarjeta, batería en equipos nuevos, artículo inactivo sin aviso, costos de accesorios fundidos y foco del lector al saltar de fila.

**Documentation**
- Sin migraciones, sin cambios de producción ni despliegue. Propuesta pendiente de decisión: precio de venta por equipo en la recepción.

## 2026-10-29 — INV-OPS-01 desplegado y aceptado por el propietario

**Released — IMPLEMENTADO — DESPLEGADO — ACEPTADO**

- Código de producción `b21c7d7efbe7e26b4678eb478def56419d4ae9d5`; backend `ops-b21c7d7` / `f01b8b68e239`, frontend `ops-b21c7d7` / `2461a31ee95c`.
- Según el informe Codex: builds, `manage.py check`, healthchecks y humo PASS; esquema `0159`, sin migraciones nuevas ni ejecución de migraciones. Sólo se actualizaron backend y frontend; SUNAT BETA permaneció desactivado y el rollback R3 quedó conservado.
- Respaldo íntegro y restauración aislada verificados: PostgreSQL 16.15, 132 tablas, 2.044 filas, 131 secuencias y 14 archivos; contenedor de prueba eliminado. No se restauró sobre producción.
- Aceptación funcional en producción: **8/8 PASS**, confirmada por el propietario. No se presentan como pruebas automáticas nuevas.

**Documentation**

- Estado vigente y runbook actualizados; fases anteriores se conservan como historial. Este PR no cambia código, migraciones ni producción y no vuelve a consultar el servidor.
- Pendientes: LABEL-B, LABEL-C, INV-OPS-02 y LABEL-D (hardware). Deudas P3 vigentes: navegación tras recarga, foco tras «Corregir» + «Atrás», texto de líneas con error y selector de empresa; detalle en `docs/release-inv-ops-01.md` y `docs/AUDIT_MEMORY.md`.

> **Historial:** los estados «no desplegado» y «sin GO» de las entradas siguientes corresponden a fases previas al despliegue y aceptación de arriba.

## 2026-10-30 — Corrección de autorización en la repetición de recepciones (#206)

**Fixed**
- `InventoryReceiptConfirmView`: al repetir una clave de idempotencia se revalida el acceso actual del operador a la sucursal guardada de la recepción (404 sin datos si se revocó, antes de comparar la huella); un registro dañado se niega, nunca da 500 ni cae a la sucursal predeterminada. Estado: listo para desplegar, sin GO todavía.

## 2026-10-29 — INV-OPS-01: repetición idempotente autorizada (rama `fix/receipt-replay-authz`)

**Fixed**
- Repetir una clave de recepción exige el alcance vigente sobre la sucursal guardada de esa recepción (404 si se perdió, antes de comparar la huella); sin cambios de modelo ni migraciones.
- La sucursal guardada se lee de forma defensiva: un rastro dañado (sin registro, sin id, nulo o no entero) niega con 404 en vez de 500 o de usar la sucursal predeterminada.

## 2026-10-30 — INV-OPS-01 en master

**Added**
- API de recepción unificada (`receipts/preview` y `receipts/confirm`) y, sobre ella, el centro de operaciones de inventario y el asistente de recepción de cuatro pasos. Sin migraciones; no desplegado.

**Fixed**
- `receive_units` serializa la recepción del mismo identificador (IMEI en `imei` o `imei2`, serie por producto) con un bloqueo asesor, lo que protege también «Registrar equipos» y la importación masiva.

## 2026-10-29 — INV-OPS-01 (frontend) en rama

- Nuevo: centro de operaciones del inventario (`/admin/inventory/operations`) y asistente «Recibir mercadería» (`/admin/inventory/receive`), con entradas en el menú de Inventario. Reutiliza `ProductPicker`, `NextStepBar`, `last-branch` y los controles de capacidades existentes.
- `ArticleRegistrationWizard` acepta `returnToReceive` (desde `?from=receive`) y ofrece «Volver a recibir mercadería».
- Pendiente: integración con la API real de recibos (otra rama) y E2E completo.

## 2026-10-29 — INV-OPS-01 E2E de recepción: historial, diálogo de salida y confirmación sin respuesta

- `inventory-receive.spec.ts`: escenario 9b (historial y diálogo de salida a 390/1280 px), aviso fuerte en 5b y salidas del flujo terminado. Sólo pruebas.

## 2026-10-29 — INV-OPS-01 E2E de recepción: aserciones de foco, títulos de duplicados y mensajes sin eco

- `inventory-receive.spec.ts` verifica el foco por paso, la ausencia de «Entrarán» con errores, los nuevos títulos de duplicados y que los mensajes no repitan serie ni IMEI. Sólo pruebas.

## 2026-10-29 — INV-OPS-01 E2E de recepción (rama `feat/inv-ops-01-ui`)

- Nuevo: `frontend/e2e/inventory-receive.spec.ts` y `inventory-receive-support.ts` (9 escenarios sin simulacros: centro de operaciones, seminuevo, equipos nuevos, accesorios con y sin costos, reintento idempotente, errores 403/404/`invalid_lines`, aislamiento entre empresas, conciliación y los cuatro pasos a 390/1280 px). Sólo pruebas; sin cambios de producto.

## 2026-10-29 — INV-OPS-01 backend (rama `feat/inv-ops-01-api`)

**Added**
- `POST /api/admin/inventory/receipts/preview/` y `/confirm/`: recepción de mercadería con varias líneas (cantidades y equipos) en una operación idempotente, todo o nada, con lotes de etiquetas (LABEL-A) y rastro de auditoría; sin migraciones.
**Fixed**
- `receive_units`: un identificador físico (IMEI en `imei` o `imei2`, serie por producto) se serializa con un bloqueo asesor por transacción; dos recepciones simultáneas ya no pueden registrar el mismo IMEI en columnas distintas.
- Recepción: parsers por defecto (sin romper la tabla fijada de `test_request_limits`); JSON anidado en exceso atrapado en la vista.
- Recepción: 409 `busy` ante interbloqueo, mensajes sin serie ni IMEI, alias contradictorios → 400, respuesta leída tras el commit.

- `stock_unit_services.find_conflict` (consulta pública de duplicados) y `unit_cost` opcional en `inventory_services.apply_manual_stock_movement`.

## 2026-10-29 — INV-OPS-01 (frontend): revisión independiente (#204)

- Seis P2 (A, B, C, D, H, I) y los P3 E, F, G, J corregidos con pruebas; ver `02_ESTADO_ACTUAL.md`. Nuevos: `useReceivingNavigation.tsx` (historial por paso y aviso al salir) y `__tests__/receive-page-gate.test.tsx`.
- Delta de la revisión: sin trampa de «atrás» tras terminar, historial coherente al registrar otra recepción, aviso de salida también en el selector de empresa, avisos sin efectos dentro de actualizaciones de estado.

## 2026-10-29 — INV-OPS-01 (frontend): hallazgos de UX del E2E

- `ReceivingWizard`: el foco va al encabezado del paso en cada cambio (y se anuncia «Paso N de 4»); los duplicados se titulan «Repetidos en esta recepción» / «Ya registrados en el sistema» / «Duplicados»; con líneas en error se dice «No se puede confirmar todavía: corrige N línea(s)» en vez de totales en cero; 409 `busy` conserva clave y contenido.

## 2026-10-29 — Release R3 desplegado

**Changed**
- Producción pasa de `42eccc8` a `a1a94a7` (inventario primero: buscador de producto, recibir mercadería, lotes de etiquetas, catálogo asistido, registro directo de equipos, completar ficha y publicar listos; base de finanzas y cuentas por cobrar de reparación). Migraciones `0156`–`0159`. Estado: DEPLOYED / PENDING ACCEPTANCE.

## 2026-10-29 — R3 predeploy

**Added**
- `docs/release-r3-rehearsal.md` y scripts de recuperación y preflight; candidato de código `a1a94a7`.

**Fixed**
- #199: precios y ids malformados del catálogo asistido responden 400 (antes 500), el resumen de etiquetas no produce 500 tras el commit y el prefijo de la serie de finanzas no se puede cambiar.

## 2026-10-29 — Revisión R3 (P2 y P3)

**Fixed**
- Precios (`price`, `prices`) e ids (`category_id`, `branch`, `product_id`) mal formados en la propuesta de catálogo y la vista previa de equipos: 400 en vez de 500.
- La lectura del lote de etiquetas tras el commit ya no puede dar 500: responde el marcador de error con `label_error`.
- El prefijo de la serie `finance_obligation` no se puede cambiar.
## 2026-10-28 — INV-QA-E: recorrido E2E de inventario primero

**Added**
- `frontend/e2e/inventory-first.spec.ts`: 8 pruebas Playwright (lista pegada → catálogo → equipos → etiquetas → ficha → «Publicar listos» → tienda, aislamiento, conciliación, 390 px y almacenamiento).

**Fixed**
- Centro de Publicación a 390 px: las tablas ahora desplazan en un contenedor `relative` y ya no ensanchan la página.
- Revisión de #198: guarda de seguridad del ayudante E2E (DEBUG + base local desechable), aislamiento con controles positivos y 403/404 exactos, `signIn` que falla en voz alta, conciliación con el efecto real del movimiento.
- Ensayo R3: localizadores sin `<title>`, guarda por palabra completa, controles que restauran en `finally`, código propio de la empresa auxiliar.

## 2026-10-28 — INV-FAST-B: correcciones de la revisión de #196

**Changed**
- Las variantes creadas desde la lista exigen IMEI salvo «Sin IMEI» explícito; se retira el indicio `hasImei` de las filas.
- La caja (vista previa, venta web y móvil) rechaza productos sin precio: «Este producto no tiene precio: configúralo antes de venderlo».
- Comparación de nombres NFC + casefold; los colores con identificador no se proponen; la propuesta empieza sin nada marcado; filas `skip` = «Necesita revisión»; reintento de sucursales.

## 2026-10-28 — INV-FAST-B: catálogo asistido y registro directo desde la lista pegada

**Added**
- `catalog_proposal.py` y `POST …/text-preview/catalog-proposal/` + `…/apply/`: propuesta de modelos, opciones y variantes que faltan y creación **sólo de lo aprobado**, atómica e idempotente, con auditoría. Lo creado queda oculto, por unidad, con código y precio sugerido o `0.00` (marcado `no_price`).
- `unit_receive_draft.py` y `POST …/text-preview/units/preview/`: las filas del borrador por `preview_units`, el importador oficial; registrar usa el aplicar oficial.
- Interfaz: `CatalogProposalPanel` («Proponer lo que falta») y `UnitReceivePanel` («Revisar y registrar»).

**Changed**
- El texto de la vista previa ya no dice que «no crea nada»: nada se crea ni se registra hasta que se aprueba cada paso.
- Las series e IMEI viajan al servidor sólo con «Revisar y registrar» (ADR INV-TEXT-03).

## 2026-10-28 — ECOM-READY-D1 (rama `feat/ecom-ready-d1`)

**Added**
- Vista previa de la tienda por producto (`/api/admin/products/<id>/storefront-preview/`), panel «Completar ficha» en Publicación e-commerce, filtro «Listos para publicar» (`ready_to_publish`) y «Publicar listos» como elección explícita por producto (nada marcado por defecto, lotes de 50, por ids).

**Fixed**
- Revisión #197: trampa de foco y retorno al botón de origen, bloqueo de scroll, respuestas de vista previa atrasadas, `company` en la dirección de la subida de fotos y del detalle, tabla estable al recargar.

## 2026-10-28 — INV-UX-A1 (rama `feat/inv-ux-a1`)

**Added**
- Selector de producto por nombre, SKU o código escaneado; «Recibir mercadería» en productos; barra «Siguiente paso» tras registrar, mover o ingresar equipos; la lista de productos del panel también busca por código (`primary_code`, `search_exact` en cada fila).

**Changed**
- «Registrar entrada o salida» y «Registrar equipo» dejan de cargar 100 productos en un `<select>`.

## 2026-10-28 — LABEL-A: lotes de etiquetas automáticos

**Added**
- `LabelBatch`, `LabelBatchItem` y `LabelPrintEvent` (migración `0159_label_batches`) y `store/label_batches.py`: cada entrada física confirmada de tipo compra o stock inicial (alta guiada, registrar equipos, carga masiva de equipos, stock inicial al crear un producto, compra manual) deja un lote `ready` e idempotente; un `manual_entry` no etiqueta. Las respuestas de esas APIs incluyen el resumen del lote y `label_error` (un fallo al etiquetar no deshace la recepción, pero se avisa).

**Changed**
- `stock_unit_services.receive_units` devuelve una lista que además expone el movimiento (`.movement`); los llamadores existentes no cambian.

## 2026-10-26 — LABEL-AUTO-01 registrado

**Added**
- Plan y auditoría de etiquetas automáticas al ingresar stock, logotipo propio por empresa y reimpresión personalizada (sin código todavía; ver `02_ESTADO_ACTUAL.md`). La impresión térmica directa queda **BLOCKED: HARDWARE**.

## 2026-10-26 — Release R2 desplegado y aceptado

**Changed**
- Producción pasa de `6834bc2` a `42eccc8` (AUTH-B/C, etiquetas, recuentos de migración, centro contable, fiscal PROD-02, #186, #189). Migraciones `0146`–`0155`. AUTH-B/C dejan de estar en borrador. Las sesiones web se cerraron una vez.

**Accepted**
- El propietario confirmó el 2026-10-26 las seis comprobaciones funcionales de R2 (sesión real, caja sin venta, servicio técnico, inventario/etiquetas, contabilidad y tienda pública). Estado final **R2 DEPLOYED / ACCEPTED**.
- No se despliegan finanzas `0156`–`0158` ni funcionalidades FIN-F/FIN-B2/FIN-D. `assign_unit_codes --apply` sigue sin autorización.

## 2026-10-26 — FIN-E: cuentas por cobrar (rama local, sin fusionar)

**Added**
- Cuenta por cobrar de reparación derivada al leer (identidad y vencimiento guardados; importes de la cotización y de
  `RepairPayment`), con estados «origen cancelado» y «en revisión de cotización» y saldo a favor aparte.
- Cuentas por cobrar manuales con motivo y `finance.receivables.manual`; no se vinculan a reparaciones ni ventas.
- Comando `backfill_repair_receivables` (idempotente, `--dry-run`, por empresa).
- Migración `0158_finance_receivable_origin` (aditiva).

## 2026-10-25 — FIN-B1: pagos fijos y obligaciones (rama local, sin fusionar)

**Added**
- Pagos fijos con calendario calculado, pausas, fin y generación idempotente (`generate_finance_obligations`, cada hora).
- Obligaciones con saldo derivado, pagos parciales, reversión, ajustes, anulación con motivo y disputa.
- Capacidades `finance.*`; sólo `view`, `manage` y `payments.record` llegan por defecto al Administrador.
- API `/api/admin/finance/…`, con totales por moneda.

## 2026-10-25 — FIN-H1: integridad del cobro de reparaciones (rama, sin fusionar)

**Fixed**
- Reversión de un pago de reparación serializada con la entrega (`RepairOrder` → pagos).
- El panel de la orden ya no pierde un segundo pago o consumo idéntico: una clave de idempotencia por intento.
- Una clave de idempotencia reutilizada en otra reparación responde 409 en vez de devolver el pago o la entrega de otra orden.

## 2026-10-25 — AUTH-C: el navegador avisa antes de cerrar la sesión (rama, sin fusionar)

**Added**
- `SessionWatcher` (panel, checkout y «Mi cuenta»): aviso con cuenta atrás, «Continuar sesión», «Cerrar sesión» y
  salida al login con el motivo. Consulta de la caja por clave de idempotencia (`GET /api/admin/pos/sales/by-key/`).

**Changed**
- `fetchWithAuth` sólo marca actividad tras una pulsación real y no renueva una sesión terminada; el checkout se
  detiene y pregunta si la sesión se cerró; `/admin` va `no-store`.

## 2026-10-25 — AUTH-B: la sesión web caduca a los 30 minutos sin actividad (rama, sin fusionar)

**Added**
- `AuthSession` (migración `0154`) y `store/auth_sessions.py`: sesión con `sid` estable, inactividad de 30 min
  decidida por la base, tope de 8 h (personal) y 7 días (clientes), `GET /api/auth/session/` y
  `POST /api/auth/session/keepalive/`, cabecera `X-Session-Idle-Remaining`.

**Changed**
- Login con contraseña y con Google emiten la sesión por un único punto; la renovación no cuenta como actividad,
  bloquea la fila y no renueva una sesión muerta; el logout termina la sesión aunque el access haya caducado o el
  refresh ya se haya rotado.
- Una cookie de sesión muerta deja de romper la tienda: lecturas como invitado, cookies borradas.

**Security**
- Un refresh o un access web (con `sid`) se rechaza en la API móvil V1, y un token V1 en la cookie web.
- Dos renovaciones simultáneas con el mismo refresh ya no ganan las dos (ADR H-6).

## 2026-10-23 — Release R1 en producción (`6834bc2`)

**Deployed**
- Editor de modelos y variantes a la vista, listas completas de proveedor, disponibilidad no confirmada,
  reconocedor y borrador de listas de WhatsApp con paso al importador oficial, base de la IA (apagada),
  y las correcciones de #143–#149. Migraciones `0142`–`0145`.

## 2026-10-23 — Del borrador de WhatsApp al importador oficial (INV-TEXT-04)

**Added**
- «Continuar al importador oficial»: abre la importación de ofertas con el borrador ya cargado; su vista previa,
  sus confirmaciones y «Aplicar» son los de siempre. Corregir el borrador cierra el importador.

## 2026-10-23 — Pruebas de concurrencia que pasan solas (TEST-FIXTURE-SEQ-01)

**Fixed**
- Cinco clases de pruebas de concurrencia ya no dependen del orden de ejecución.

## 2026-10-23 — Borrador de importación desde WhatsApp (INV-TEXT-03)

**Added**
- Tras revisar una lista de WhatsApp: «Generar borrador». Ofertas de proveedor: ensayo de la importación oficial
  (resumen de altas, cambios y posibles desactivaciones) sin escribir nada, y el archivo con la plantilla oficial.
  Stock propio: filas para la «Carga de equipos», copiadas en el navegador.
- Distingue cantidad desconocida, 0 y N; marca duplicados sin sumarlos; parcial por defecto, lista completa
  sólo si se indica.

## 2026-10-23 — Base de la IA del inventario, apagada (INV-AI-01)

**Added**
- Contrato estructurado para una IA futura: sólo vocabularios cerrados y referencias opacas; nunca texto del
  proveedor, IMEI, series, nombres, contactos, costos, precios ni cantidades. Interruptores, cuotas,
  presupuesto y cortacircuitos. Sin proveedor, sin claves y apagada (`INVENTORY_AI_ENABLED=false`).

## 2026-10-22 — Listas de proveedores reales (INV-TEXT-02)

**Changed**
- La vista previa de texto reconoce el costo en cualquier posición, accesorios, colores, regiones y SIM,
  el formato de WhatsApp y la capacidad sin «GB», y lista las líneas que no reconoce con su motivo.
- Cada registro se puede corregir, ver su texto original y buscar su variante real en el catálogo, sin
  guardar nada.

## 2026-10-21 — INV-TEXT-01: vista previa local de listas (EN RAMA, no desplegado)

**Added**
- Reconocedor determinista solo en navegador para pegar texto de WhatsApp y revisar candidatos sin API ni escritura.
- Alertas de IMEI Luhn, duplicados, cantidad desconocida y conflictos de condición.
- Tests focales de parser y documento de seguridad/roadmap (docs/inventory-ai-safe-rollout.md).

**Preserved**
- Importadores, exportaciones, stock real/Kardex, módulos manuales, precios comerciales, fiscal y checkout.

**Pending**
- Ejecución Jest/typecheck/lint/build/E2E, revisión funcional, aceptación y merge. API Claude y secretos quedan para fase posterior de despliegue.

## 2026-10-22 — Editar modelos y variantes, a la vista (CATALOG-EDITOR-FIX-01)

**Fixed**
- Modelos: cada fila tiene «Ver detalles», «Editar modelo» y «Gestionar variantes».
- «Editar modelo» abre el formulario precargado con «Guardar cambios» y «Cancelar»; cambiar la dirección de
  un modelo publicado avisa que la anterior dejará de funcionar y pide confirmarlo.
- Una variante se guarda de una vez: sus opciones, su precio y su publicación juntos, o nada.
- Sin atributos, el editor explica dónde crearlos para elegir capacidad o color.

## 2026-10-22 — Lista completa de proveedor (INV-SNAPSHOT-01)

Integrado en master (#151, `b25ef1d`); no desplegado.

**Added**
- Carga semanal: «Es la lista completa de un proveedor» muestra qué ofertas vigentes ausentes se
  desactivarán y exige confirmarlo; las que tienen ventas siguen su curso.
- «Disponibilidad no confirmada»: una oferta sin cantidad informada no vende más y no se muestra como 0.

## 2026-10-22 — ADR de listas completas y seminuevos externos

**Docs**
- Decisiones D1–D4 del propietario y el estado «disponibilidad no confirmada» (sin implementar).

## 2026-10-22 — Doble envío de un comprobante

**Fixed**
- Un segundo «Enviar» justo después de la respuesta de SUNAT ya no transmite otra vez el mismo comprobante.

## 2026-10-22 — IMEI con el dígito que falta (INV-ID-01)

**Added**
- Al escribir 14 dígitos de un IMEI, se ofrece completar el decimoquinto mostrando el número completo.

## 2026-10-22 — Exportar por línea (INV-EXP-01)

**Added**
- Exportaciones de inventario: elegir solo nuevos, solo seminuevos, nuevos y seminuevos, o solo accesorios.
- «Reporte integral»: stock propio, equipos y disponibilidad de proveedores en un solo Excel, cada uno en su
  hoja y sin sumarse.

## 2026-10-22 — Interruptor de IA y modos del inventario (INV-SWITCH-01)

**Added**
- Configuración › Inventario · Inteligencia artificial: activar o desactivar la IA de la empresa (apagada).
- «Usar IA en esta importación» al previsualizar la lista semanal; sin IA configurada se lee igual y lo dice.
- Inventario: «Modo rápido» con las tareas del día y «Modo avanzado» con el tablero completo.

## 2026-10-22 — Cancelación que cruza un pago

**Fixed**
- Si un pedido se paga mientras alguien lo cancela como impago, la cancelación se rechaza (409) en vez de
  anular un pedido ya pagado sin decidir el reembolso.

## 2026-10-22 — Despliegue de #137, #138, #139 y #140 en producción

**Deployed**
- Producción pasa de `acf20fc` a `6daf9ae` con las migraciones `0140_supplier_product_ref` y
  `0141_orderitem_received_cost`: editor de variantes, proveedores y lista semanal, D1/D2 y D3.

## 2026-10-21 — Falla del proveedor sin aumentar disponibilidad (D3)

**Fixed**
- Marcar una línea al pase como «No se pudo abastecer» por falla del proveedor ya no vuelve a
  ofrecer esa cantidad; cancelar el pedido sí la devuelve, una vez.

## 2026-10-21 — Cantidad neta semanal y margen realizado con costo real

**Changed**
- La cantidad de la lista semanal es lo que queda por vender (no se descuenta otra vez lo
  comprometido).
- El margen realizado de una venta al pase usa el costo real de la recepción; sin él, «costo real
  pendiente».

**Added**
- «Registrar costo real» de una línea recibida (`costs.view`).

## 2026-10-21 — Proveedores, venta al pase y recepción rápida

**Added**
- Códigos de proveedor (`SupplierProductRef`), diferencias y decisión de precio en la lista semanal,
  alertas operativas (`/api/admin/orders/alerts/`), costo real en la recepción, «Agotado» en tienda.

**Changed**
- La lista semanal actualiza sólo sus filas; cantidad 0 = agotada; una oferta por proveedor y
  producto, sin solapes; la recepción exige lo que exige el servidor y se rechaza durante una
  devolución.

## 2026-10-20 — Editar variantes y estado de los módulos

**Added**
- «Editar variante» en la página del modelo; motivo de superior para variantes con historial.

**Changed**
- Quitar una variante del modelo sigue las mismas reglas; el tablero separa lo operativo de lo
  que está en desarrollo; catálogo de módulos sin duplicados y con estados reales.

## 2026-10-20 — Pruebas de importe de devolución

**Added**
- `test_order_return_amounts`: descuento comercial frente a deducción de devolución, tope
  reembolsable, reembolso duplicado, stock una vez al costo de la venta, pago intacto.

## 2026-10-19 — Devolución posterior a la entrega (fase 1) (#134, desplegado `acf20fc`)

**Added**
- Panel «Devolución» y `GET/POST /api/admin/orders/{id}/return/` (+ `receive/`, `close/`,
  `withdraw/`); movimiento de Kardex «Devolución de cliente»; plazo de devolución por empresa.

**Changed**
- Una cancelación cerrada por devolución lo dice («Cancelado por devolución»).
- Con una devolución en curso no se anula la venta, no avanza el despacho (una vez recibida) ni se
  identifica otro equipo; lo descontado al cerrar queda retenido.

## 2026-10-19 — Corrección de salida con equipos identificados (#132, desplegado `055cf0d`)

**Changed**
- Un equipo identificado que el mismo «Enviado» erróneo marcó como salido ya no impide
  corregir ese registro (decisión del propietario). Cada salida nombra los equipos que selló.

**Added**
- Propuesta de devolución posterior a la entrega (`docs/diseno-devolucion-post-entrega.md`).

## 2026-10-17 — ORDER-FULFILLMENT-SAFETY-01 · despacho seguro (#128, desplegado `7903a49`)

**Deployed**
- Producción pasa de `68137e5` a `7903a49` el 2026-10-17; sin migraciones; copia previa
  `20261017-013840`; imágenes `rollback-68137e5`.

**Added**
- Panel «Estado de despacho» con pasos según la modalidad, historial de despacho y
  «Corregir marcado de salida» (superior, motivo, declaración; entrada de auditoría nueva).
- `POST /api/admin/orders/{id}/left-markings/{audit_id}/correct/`; bloque `dispatch` en el detalle.

**Changed**
- El despacho avanza un paso a la vez; recojo sin «Enviado»; envío sin «Listo para retiro»;
  un paso atrás exige motivo (400 `reason_required`) y sólo si nunca salió;
  `available_fulfillment_transitions` trae sólo el paso siguiente y el anterior.

**Removed**
- «Cancelado operativo» del selector manual (sólo lo escribe la cancelación comercial).

**Fixed**
- Borrar la guía, el seguimiento o la fecha de envío ya no permite cancelar ni corregir un
  pedido que salió.

## 2026-10-16 — Despliegue de #125 y #126 en producción (`68137e5`)

**Deployed**
- Producción pasa de `04640cf` a `68137e5`: COMMERCE-ADMIN-UX-01 (#125, con #119 y #121) y su
  cierre financiero (#126). Migraciones `0124`, `0125`, `0136`, `0137` y `0138`, aplicadas con
  `lock_timeout` y `statement_timeout` tras ensayarlas en una copia de la base.
- Copia previa `20261016-225654`; imágenes `rollback-04640cf`; Caddyfile sin cambios.

## 2026-10-16 — COMMERCE-ADMIN-UX-01 · cierre financiero (#126, desplegado)

Rama `fix/financial-closure` desde `master` `315bedf`; migración `0138`.

**Added**
- Plazo de anulación simple de ventas POS por empresa (`pos_cancel_simple_days`, 7 días,
  0–365) y anulación por superior para ventas antiguas o complejas.
- Evidencia e importe exacto al confirmar un reembolso.
- Estado «Liquidada» de comisiones y ajuste por anulación (saldo a recuperar).

**Changed**
- Informe de comisiones por estado; tablero interno y ficha del cliente dicen aparte el dinero
  de los cancelados; filtro «Pagado (vigente)» en Órdenes.

**Fixed**
- (Revisión) Evidencia y referencia de un reembolso: sin caracteres invisibles, con letras o
  números reales; un carácter de control es 400 (antes un NUL daba 500). Anular sin reponer
  stock exige superior. El informe de comisiones separa la venta anulada de una comisión
  liquidada. La nota del tablero sale también con sólo un cobro tardío o un reembolso pendiente.
- La previsión de demanda contaba las salidas de ventas canceladas; el uso de promociones
  contaba ventas anuladas; lo recibido de un proveedor dejaba de figurar como por pagar al
  cancelarse el pedido.

## 2026-10-16 — Base local SQLite: `migrate` vuelve a pasar `0131`

**Fixed**
- En SQLite (la base local de desarrollo y la de las pruebas de navegador), `migrate` se
  detenía en `0131` con «unknown function: GEN_RANDOM_UUID()». La app le da a esa función
  su forma para SQLite (32 dígitos hexadecimales al azar); la migración aplicada no se
  edita y PostgreSQL (producción) no cambia.

## 2026-10-16 — COMMERCE-ADMIN-UX-01 · ronda de correcciones (en rama, NO desplegado)

- **Fixed** — costo de referencia «1e30»/«Infinity» ya no da 500; equipo dado de baja sin cambio de costo; motivo de costo sólo texto; «Costos y precios» envía sólo lo cambiado y avisa no escribir montos en el motivo; aviso por «Bajo pedido» contradictorio en ofertas; modelo nuevo oculto si su primera variante no se publica. Sin migración (DEC-COMMERCE-ADMIN-UX-01 · Registro).

## 2026-10-15 — COMMERCE-ADMIN-UX-01 · cancelaciones (en rama, NO desplegado)

Rama `feat/commerce-admin-ux` (sin push). Integra #121/#119 por merge; migraciones `0124`,
`0125`, `0136` (fusión) y `0137` (reembolsos). Estado: **en rama, sin desplegar**.

**Added**
- Cancelar un pedido web sin pagar, con motivo: queda en el historial como cancelado, sin
  ingreso; los equipos apartados vuelven a la tienda. Un pago que llega después se registra
  como cobro a devolver y el pedido lo dice.
- «Anular venta» POS, con motivo y la casilla «La mercadería volvió al local»: stock de vuelta
  al costo de la venta, nota interna anulada, comprobante fiscal conservado y marcado.
- Reembolsos registrados por pedido cancelado (pendiente / confirmado con referencia), con
  tope en lo cobrado; filtro «Reembolso» y botón «Cancelaciones / historial» en Órdenes.
- Resumen de ventas: sección «Cobros»; margen: «Cobro neto» y «Cancelados (fuera del margen)».

**Changed**
- Las líneas bajo pedido de un pedido cancelado devuelven lo comprometido a su oferta.
- Los más vendidos no cuentan las salidas de pedidos cancelados.
- Al anular/cancelar una venta pagada se anula la comisión devengada del vendedor.

**Fixed (revisión independiente)**
- Una venta POS con una devolución manual posterior del mismo producto ya no repone stock dos
  veces: se rechaza y se ofrece «Anular sin reponer stock».
- Un medio de reembolso que no es texto responde 400; los cobros duplicados con claves TEST no
  cuentan como dinero.

**Not changed**
- Ningún pago confirmado se borra ni se modifica; no se llama a la pasarela; no se emite
  nota de crédito ni baja SUNAT; el Kardex y las instantáneas de costo no se reescriben.

## 2026-10-15 — COMMERCE-ADMIN-UX-01 · Registro único, publicación y navegación (en rama, NO desplegado)

- **Added** — «Productos › Registro / Carga masiva»: asistente de cuatro pasos para equipo nuevo,
  seminuevo, accesorio/repuesto o disponible por proveedor sobre `POST /api/admin/products/register/`
  (servicios existentes en una transacción, `Idempotency-Key`, `dry_run`); «Productos › Publicación
  e-commerce» para todos los tipos y modelos (`POST /api/admin/catalog/models/bulk-visibility/`);
  «Inventario › Stock y disponibilidad»; columna opcional «Bajo pedido (Sí/No)» en la plantilla de
  ofertas; menú según el árbol del propietario. Sin migración (DEC-COMMERCE-ADMIN-UX-01 · Registro).

## 2026-10-15 — COMMERCE-ADMIN-UX-01 · A1 «Costos y precios» — en rama (`wip/costs-ux`)

- Inventario › Costos y precios (`costs.view`): margen, inventario al costo, costo promedio por
  sucursal y costo de cada equipo, con su origen; precio y costo de referencia editables;
  «Corregir costo» de un equipo con motivo auditado; estados de carga, vacío y error. Sin
  migración. Corregir el promedio de una sucursal: PENDIENTE (DEC-DEVICE-COMMERCE-01 · A1).

## 2026-10-15 — COMMERCE-ADMIN-UX-01 · «Consultar disponibilidad» (en rama, NO desplegado)

- **Added** — «Ventas › Consultar disponibilidad» (`GET /api/admin/availability/`, `sales.pos.use`,
  sólo lectura, sin migración): stock propio por sucursal y por estado del equipo, ofertas de
  proveedores aparte («no es stock propio»), equipos con IMEI enmascarado; nunca costos.
  «Inventario › Equipos» filtra por línea, modelo y opciones y muestra el detalle de cada equipo
  (DEC-COMMERCE-ADMIN-UX-01).

## 2026-10-15 — Despliegue de #123 (DEVICE-COMMERCE-01)

**Deployed**
- `master` `04640cf` en producción desde el 2026-10-15, 14:52 (Lima), desde `041d0fd`, con
  copia previa `20261015-144747` y las migraciones `0126`–`0135` (con `lock_timeout`).
- Cron nuevo, con `flock`: `release_expired_unit_holds` y `expire_supplier_offers`.

**Verified**
- Copia restaurada; migraciones ensayadas ida, vuelta e ida sobre ella; datos iguales antes y
  después; chequeo de salud en verde; ningún dato financiero en la tienda pública.

**Not changed**
- #119–#122 sin desplegar; pasarela en TEST; Caddyfile y variables de entorno.

## 2026-10-15 — DEVICE-COMMERCE-01 en `master` (#123) — desplegado el mismo día (ver arriba)

**Changed**
- DEVICE-COMMERCE-01 (costos y margen, nuevos y seminuevos por unidad, proveedores y bajo
  pedido) entra en `master` por el PR #123. Producción no cambia hasta un despliegue
  autorizado.

**Verified**
- CI de `129d82f`: 7417 pruebas en seis fragmentos, en verde. Revisión de integración: GO.
- Migraciones `0126`–`0135` medidas con 200 000 líneas y 50 000 equipos: 4–6 s de ida.

## 2026-10-15 — DEVICE-COMMERCE-01 · revisión de integración del PR #123 (en rama, NO desplegado)

- **Fixed** — los apartados web que retiene un pago pendiente cuentan en los topes por carrito, por origen, reparto del estante y techo de la empresa; una línea bajo pedido tomada con el estante vacío ya no se atribuye al comprador como «elegida sobre stock» si la web no lo permite; la liquidación pendiente al proveedor se limita a las sucursales de quien consulta; el stock que vuelve de la venta de un pedido sin costo declarado vuelve al costo de su salida.
- **Added** — `ops_status` «apartados web»; `uncosted_after_rollback` (informe y, con `--apply`, costo pendiente sin inventar costos) para regresar de una versión anterior a DC1.

## 2026-10-15 — DEVICE-COMMERCE-01 · decisiones del propietario (implementado en rama, NO desplegado)

Migración `0135` (aditiva, `db_default` con el comportamiento anterior, reversible).

**Added**
- Configuración por empresa de los apartados web de equipos: minutos de carrito, de checkout
  y tope (15/45/60 al crear; checkout nunca menos de 30, lo que vive el formulario de pago).
  Cambiarlos exige `settings.manage`.
- «Bajo pedido» aunque haya stock propio, sólo explícito: en caja con `sales.orders.manage` y
  un motivo; en la web sólo si la empresa enciende «Permitir que el comprador web elija
  proveedor aunque haya stock», y la tienda dice que sale del proveedor con su plazo. La
  línea guarda `supply_chosen_over_stock` y `supply_choice_reason`; auditoría
  `pos_on_order_over_stock` / `web_on_order_over_stock`.
- Margen comercial estimado y realizado (costo confirmado: stock propio vendido o bajo
  pedido recibido), «costo desconocido» y reembolsos con y sin IGV, aparte.

**Changed**
- Un apartado web cuyo pedido tiene un pago pendiente que la pasarela aún puede confirmar
  ya no vuelve al estante por vencimiento: decide la conciliación (2 h; sin límite si la
  respuesta fue indecidible). La caja tampoco lo libera dentro de ese plazo.
- La liberación de apartados vencidos se decide bajo el bloqueo del estante y del equipo:
  barridos simultáneos escriben una sola línea de Kardex.
- «Bajo pedido» con stock: el 409 `stock_available` sigue siendo la respuesta por omisión
  (antes «por confirmar»; ahora aprobado por el propietario con las dos excepciones).

## 2026-10-15 — DEVICE-COMMERCE-01 · DC3: correcciones de la revisión independiente

Migración `0134` editada antes de publicar (índice único de ofertas y CHECK más estricto).

**Fixed**
- La carga Excel de ofertas ya no crea dos veces la misma oferta (dos vistas previas del mismo
  archivo, o dos personas a la vez): índice único por proveedor, variante y vigencia, y el
  segundo intento se rechaza con «El archivo cambió: vuelve a previsualizarlo».
- El costo pactado sólo lo escribe quien aplica con `costs.view`; una celda de costo vacía no
  borra el costo; una oferta desactivada a mano no se reactiva (columna opcional «Activa»).
- Un pedido reembolsado o cancelado devuelve a la oferta lo comprometido («No se pudo
  abastecer»); el pago deja «por confirmar» una línea cuyo producto salió de «Bajo pedido»;
  un pedido sin sucursal compromete igual sus líneas bajo pedido.
- «Bajo pedido» se rechaza (409 `stock_available`) en la web y en caja mientras el stock
  propio cubre la línea (por confirmar con el propietario).
- La carga bloquea sus ofertas en orden de id; el CHECK exige cantidad confirmada para
  comprometer; la vuelta atrás también vacía las líneas de carrito bajo pedido.
- «Mis pedidos» (`GET /api/orders/`) vuelve a la forma fijada de la línea; una línea bajo
  pedido añade sólo su plazo y ya no se publica el estado interno del abastecimiento.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC3: proveedores y venta bajo pedido

Migración `0134` (proveedores, ofertas, modo de venta, origen de la línea; aditiva, reversible).

**Added**
- Proveedores y ofertas de proveedor por variante, con historial y desactivación; modo
  «Bajo pedido» por producto; carga Excel «Ofertas de proveedor» (vista previa con errores
  por fila, aplicación idempotente, columna de costo sólo con `costs.view`) y exportación.
- Venta bajo pedido en la tienda («Bajo pedido — entrega en N días», a pedido explícito del
  comprador) y en caja; el pago compromete la oferta sin mover stock; recibir del proveedor
  escribe entrada y salida; «No se pudo abastecer» devuelve lo comprometido y marca el pedido.
- `expire_supplier_offers` y su línea de cron; `ops_status` avisa de pedidos pagados con
  abastecimiento por confirmar o fallido; resumen de solo lectura «Pendiente de liquidar al
  proveedor».

**Changed**
- Un pedido con productos bajo pedido no recibidos no pasa a listo, enviado ni entregado.

**Fixed (segunda revisión de DC2)**
- Compradores de seminuevos distintos de una variante pequeña ya no se bloquean entre sí
  (reparto sólo desde 4 equipos + techo por empresa); el pedido pagado de un carrito gana su
  equipo frente a un checkout posterior sin pagar; un solo orden de bloqueos al apartar;
  equipos sin costo entran al costo con que salieron.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC2: revisión independiente

Migración `0133` (`StockUnit.hold_origin`, `hold_started_at`; aditiva, reversible).

**Security**
- Un mismo origen aparta a lo sumo 3 equipos entre todos sus carritos; los apartados web no
  pasan de la mitad de los equipos de un producto en la sucursal (mínimo uno); throttle
  `device_hold` 10/min por dirección; un apartado no se amplía más allá de 60 minutos desde
  que se tomó.
- El precio de venta de un equipo exige `products.manage`.

**Fixed**
- `bind_to_order` ya no ata el apartado de otro carrito.
- Dos checkouts de un carrito: el pedido anterior no abre pago ni vende el equipo.
- Archivos de productos anteriores a DC2 con «Modelo» o «Color» cargan como antes.
- Quitar un equipo que se está vendiendo ya no da 500; no se aparta un equipo de un modelo
  retirado de la web; la app no lista seminuevos; el recuento de seminuevos del panel es el
  de la sucursal de la tienda; un equipo sin costo sale al costo de referencia.

**Added**
- `release_expired_unit_holds --all-web`, `cancel_pending_unit_orders`, la línea de cron y
  los pasos para volver atrás.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC2: variantes, nuevos y seminuevos

En la rama `feat/device-commerce` (sin push). Migraciones `0130`–`0132` (aditivas, nulas o
con `db_default`, reversibles; `0132` sólo se deshace mientras ningún pedido tenga dos
equipos de un mismo producto). DEC-DEVICE-COMMERCE-01 · DC2.

**Added**
- Modelos (`ProductModel`), atributos y opciones con muestra de color, variantes
  (`Product.product_model`, `condition_line`), escala de estado estético por empresa.
- `GET /api/catalog/models/` y `/api/catalog/models/<slug>/`; página de modelo con
  capacidad, colores y combinaciones no disponibles deshabilitadas; seminuevos uno por uno.
- Apartado web de un equipo concreto en el carrito (`POST /api/cart/add/` con `unit`,
  `POST /api/cart/release-unit/`), con vencimiento y `release_expired_unit_holds`.
- Batería, estado estético, notas públicas, precio de venta y fotos públicas por equipo;
  lista «Seminuevos» en el panel; administración de modelos, opciones y escala.
- Columnas «Modelo», «Capacidad», «Color», «Línea» (productos y equipos) y «Estado
  estético», «Batería %», «Precio de venta» (equipos).

**Changed**
- Lo que se vende por unidad (variante serializada de un modelo): el pago vende el equipo
  apartado, no el más antiguo; cada equipo es su línea de pedido con su precio y su costo.
- La lista pública de productos no lista variantes seminuevas.
- La app nativa rechaza en el checkout los productos que se compran eligiendo el equipo.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC1: revisión independiente

**Security**
- Sin `costs.view`, la columna «Costo de referencia» de la carga de productos se ignora (no
  se compara ni se escribe).

**Fixed**
- El stock que entra sin costo queda «costo pendiente» (`0129`, `uncosted_quantity`) en vez
  de valorarse a un promedio viejo; sale primero.
- La rebaja de una promoción va a sus líneas, como en el comprobante F6.
- Instantáneas y promedios de sólo lectura en el admin de Django; `costs_view_report`.
- Desgloses del margen con el neto estimado de las líneas anteriores a DC1.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC1: costos y margen comercial

En la rama `feat/device-commerce` (sin push). Migraciones `0126` (datos), `0127` y `0128`
(aditivas, `db_default` en las columnas NOT NULL, reversibles). DEC-DEVICE-COMMERCE-01.

**Security**
- `GET /api/admin/inventory/units/` devolvía el costo de cada equipo a quien tenía sólo
  `inventory.view`. Ahora el costo viaja sólo con `costs.view`, en la lista, el detalle, la
  recepción y las descargas.

**Added**
- Capacidad `costs.view` («Ver costos y márgenes») para el preset `Administrador` sin
  modificar (`0126`) y los roles heredados `admin`/`superadmin`.
- Costo promedio ponderado por sucursal, costo específico por equipo y costo de cada línea
  de Kardex; `Product.reference_cost`.
- Costo y base sin IGV congelados en cada línea vendida (web y caja).
- `GET /api/admin/sales/margin/` y la sección «Margen comercial» del resumen de ventas.
- «Valor al costo» junto al valor a precio de venta en el inventario.
- Columnas «Costo de referencia» (productos) y «Costo unitario» (stock, opcional).

**Changed**
- El costo en las descargas es de `costs.view`, no de `inventory.adjust`.
- «Precio costo» de la plantilla POS ya no se ignora: es el costo de referencia.
- El reporte de stock trae «Costo promedio» (vacío sin `costs.view`).
- El costo de un equipo vendido no se puede cambiar.

## 2026-10-14 — SALES-NOTE-CONTROL-01

En la rama `feat/sales-note-control` (sin push), sobre #119. Migración nueva `0125` (aditiva).

**Added**
- Panel › Ventas › Notas de venta: lista con filtros (vigentes, anuladas, prueba, fechas,
  sucursal, emisor), marca ANULADA y detalle con el registro de anulación.
- «Anular nota de venta», sólo para el usuario Master en la empresa de la nota: tipo de
  motivo (prueba, error de emisión, otro) y motivo obligatorios; auditoría `sales_note_voided`.
  `POST /api/admin/sales-notes/<id>/void/`.
- Observaciones internas de una nota, sólo añadir, nunca impresas; auditoría
  `sales_note_remark_added`. `POST /api/admin/sales-notes/<id>/remarks/`.
- `GET /api/admin/sales-notes/` y `GET /api/admin/sales-notes/<id>/` (PUT, PATCH y DELETE
  responden 405).

**Changed**
- La nota de un pedido cancelado guarda el mismo registro de anulación (`pedido_cancelado`).
- La API de la nota del pedido devuelve el registro de anulación y la sucursal; el panel del
  pedido muestra ANULADA y el motivo.
- La térmica no reimprime una nota anulada; un ticket encolado antes sale con ANULADA.
- El admin de Django de las notas de venta es entero de sólo lectura.
- (Revisión) Las columnas nuevas de texto llevan valor por defecto en la base: la versión
  anterior sigue emitiendo notas mientras se migra. Una nota de un pedido cancelado ya se
  trata como anulada (409). Un ticket de una nota anulada encolado antes se cancela al
  entregarlo y no se reenvía. La nota anulada con el filtro «Vigentes» sale de la lista.

**Not changed**
- Número, fecha, líneas, importes, impuestos y pago de una nota emitida; el pedido, los
  pagos, el stock, los comprobantes SUNAT, la serie y su «Próximo número»; tableros e
  ingresos. Ninguna nota existente se marca como prueba automáticamente.

## 2026-10-14 — ORDER-CANCEL-01 y SALES-NOTE-DESIGN-01

En la rama `feat/order-cancel-sales-note` (sin push). Migración nueva `0124` (aditiva).

**Added**
- Panel › Pedido: «Cancelar pedido» para un pedido en línea pagado cuyos productos no han
  salido, con confirmación y motivo obligatorio; después, el bloque «Pedido cancelado» (quién,
  cuándo, por qué, stock, aviso fiscal). `POST /api/admin/orders/<id>/cancel/`.
- Kardex: tipo «Devolución por pedido cancelado» (`order_cancel_return`).
- Registro `OrderCancellation` (uno por pedido) y entrada de auditoría `order_cancelled`.

**Changed**
- La nota de venta se titula «NOTA DE VENTA» y termina con la línea «Documento de control
  interno, sin validez tributaria», sin la franja enmarcada (A4, 80 mm y térmica).
- Un pedido cancelado no cambia de estado de despacho (409 `order_cancelled`).
- Los PDF de un pedido cancelado (nota A4 y 80 mm, constancia) se siguen descargando,
  marcados «ANULADA» / «PEDIDO ANULADO»; su ticket no se imprime en la térmica.
- Un cobro que llega para un pedido cancelado se archiva como cobro duplicado, con el estado
  del pedido en la auditoría; el pedido sigue cancelado y pagado.
- Marcar un fallo de pago sólo afecta a un pedido que todavía espera su pago.
- Un pedido que alguna vez salió (equipo marcado como entregado, paso por enviado o
  entregado, o fecha de envío en su entrega) no se cancela aunque se le devuelva a un estado
  anterior.
- No se emite boleta/factura ni nota de venta interna de un pedido que se canceló mientras
  tanto; ninguno gasta su número.
- Un equipo devuelto antes de cancelar no se repone dos veces, y el que se entregó en su
  lugar vuelve al estante: se repone todo equipo que siga vendido al pedido.
- El resumen de un cliente (pedidos e importe pagados, última compra) ya no cuenta los
  pedidos cancelados.

**Not changed**
- Pagos (no hay reembolso), comprobantes fiscales (ni nota de crédito ni baja automáticas),
  avisos al comprador, tableros e ingresos, y el «Cancelado operativo» del despacho.

## 2026-10-14 — Despliegue de #115, #116 y #117

**Deployed**
- `master` `041d0fd` (merge de #117) en producción desde el 2026-10-14, 01:14 (Lima). El
  servidor pasó de `14e658c` a `041d0fd` con copia previa (`db-20261014-011108.sql.gz`,
  `evidence-20261014-011108.tar.gz`) y las migraciones `0122` y `0123`.
- Entra INVENTORY-UNIFIED-01: visibilidad en bloque, exportaciones, centro de gestión masiva
  e imágenes en lote, con las decisiones del propietario del 13 y 14 de octubre.
- #116 cambia sólo el CI (seis fragmentos); #115, sólo documentación.

**Verified**
- Copia previa restaurada en un PostgreSQL desechable; `0122` y `0123` aplicadas, deshechas y
  aplicadas otra vez sobre esa copia con la imagen nueva, con el esquema de vuelta idéntico.
- Empresas, productos, imágenes, movimientos de stock, pedidos, totales e intentos de pago,
  iguales antes y después. Chequeo de salud en verde; páginas públicas, catálogo, carrito y
  checkout responden; las rutas nuevas del panel piden sesión; la ruta de notificación de pago
  responde 405 a un GET.
- CI de `ce2f8d0` (árbol de `041d0fd`): 7109 pruebas en seis fragmentos, en verde.

**Not changed**
- Pasarela de pago («Mi Cuenta Web» en TEST, sin pausa), variables de entorno (no se definió
  `ADMIN_EXPORT_RATE`: vale 6/min), `deploy/Caddyfile` y cron del servidor.

## 2026-10-13 — INVENTORY-UNIFIED-01: decisiones del propietario

En `master` por #117 y desplegado el 2026-10-14. Migración nueva `0123` (aditiva).

**Added**
- Configuración › «Publicación en e-commerce»: exigir imagen para publicar en bloque
  (apagado por omisión).
- El archivo del catálogo para volver a cargar trae las columnas «Estado» e «Identificador».
- `ADMIN_EXPORT_RATE`: cupo configurable de descargas del inventario (6 por minuto).

**Changed**
- Un producto nuevo cargado sin «Destino» queda como «Solo stock interno».
- Publicar en bloque —la acción de la lista y la carga de productos— pide estar activo,
  con precio y con categoría; la imagen, sólo si la empresa lo configura. En la carga, lo
  que no cumple se carga igual, queda interno y la fila dice por qué.
- El archivo para volver a cargar lleva productos activos e inactivos y respeta el filtro.
- Las celdas que empiezan por `=`, `+`, `-` o `@` se ven tal cual en los Excel descargados,
  sin apóstrofo, y siguen sin ejecutarse.
- Cuando se piden demasiadas descargas, el panel dice en cuántos segundos volver.

**Fixed**
- Un producto sin código renombrado ya no se crea otra vez al recargar su archivo.
- Imágenes en lote (decisión del 2026-10-14): el código se busca sin distinguir mayúsculas, espacios
  ni forma Unicode; si coincide con más de un producto no se asigna y se dice; lo que no tiene
  producto se lista y el resto se aplica.
- Una carga nunca reactiva ni desactiva un producto existente.

**Security**
- La app ya no da la dirección de un producto que dejó de venderse en «Mis pedidos».

**Verified**
- Suite completa del backend sobre `6e556c9`: 7056 pruebas, 0 fallos, 6 omitidas.
- Jest 1341/1341, tipos y lint limpios; Playwright 97/97; `sh deploy/rehearsal.sh` `ENSAYO: OK`
  (149) con `0122` y `0123`.
- Tres revisiones independientes sin P0 ni P1.

## 2026-10-13 — INVENTORY-UNIFIED-01: correcciones tras la revisión independiente

En la rama `feat/inventory-unified`; sin desplegar. Sin migraciones nuevas.

**Security**
- La descarga anterior del inventario y el `errors.csv` de las cargas ya no escriben texto que
  una hoja de cálculo ejecute como fórmula.
- «Mis pedidos» ya no muestra el precio de hoy, la descripción, el stock ni la galería de un
  producto que dejó de venderse en la web.
- La acción en bloque de productos se aplica en la empresa para la que se abrió la pantalla,
  aunque otra pestaña cambie de empresa.

**Fixed**
- «Todo lo que coincide» ya no puede cambiar un producto que no se contó.
- La carga de productos y la acción en bloque, a la vez sobre los mismos productos, ya no se
  bloquean entre sí (error 500).
- El archivo del catálogo para volver a cargar sólo lleva productos activos.
- Una búsqueda con un carácter nulo, o una sucursal con un carácter de control en el nombre, ya
  no dan error 500.
- La carga de imágenes nota si cambió la imagen principal o el orden desde la vista previa, no
  deja copias de lo que ya estaba en la galería, y su resultado enlaza a cada galería.
- Lista de productos: una respuesta lenta no pisa a la nueva; la selección se quita al desactivar
  una fila; no se confirma una selección vacía; tras vaciar la última página se vuelve a la anterior.
- El pago no deja enviar un carrito con una línea que ya no está disponible y dice qué producto
  rechazó el servidor; el contador del carrito no cuenta esas líneas.

**Changed**
- Las descargas del inventario tienen su propio límite: 6 por minuto.
- La vista previa de la carga de productos avisa de cada producto existente que pasa a publicarse
  sin estar listo, y dice cuántos cambian de destino antes de aplicar.
- La tarjeta del ZIP avisa cuando los filtros hacen que no sea el inventario completo.
- Las tarjetas del catálogo y del ZIP avisan cuando se pidió «Inactivos»: un archivo para volver
  a cargar lleva sólo productos activos.

## 2026-10-13 — INVENTORY-UNIFIED-01 (partes 3 y 4)

En la rama `feat/inventory-unified`; sin desplegar. Migración `0122` (sólo opciones de un campo).

**Added**
- Panel › Inventario › **Gestión masiva de inventario**: importar, exportar, imágenes en lote e
  historial en una pantalla. Las tres cargas (productos, stock, equipos) siguen donde estaban.
- «¿Qué archivo tengo?»: se elige un Excel y el sistema dice a qué carga corresponde y por qué, o
  que es un reporte, o que mezcla dos cargas. No carga nada.
- Imágenes en lote para productos que ya existen: muchos archivos o un ZIP, cada uno a su
  producto por el nombre del archivo (`FUNDA.png`, `FUNDA_2.png`…) o con un libro «Imágenes».
  Vista previa antes de aplicar; añadir o, con confirmación escrita, reemplazar la galería.
- El historial de cargas muestra las cargas de imágenes y permite descargar los errores de
  cualquier carga.
- `docs/imagenes-y-evidencias.md`: dónde viven las imágenes, cómo se copian y se sirven, y qué
  haría falta para pasar a un almacenamiento de objetos.

**Changed**
- `POST /api/admin/imports/inspect/` responde además `suggested_import_type` y acepta
  `import_type=auto`.
- `POST /api/admin/products/import/preview/` acepta `kind=images`.

## 2026-10-13 — INVENTORY-UNIFIED-01 (partes 1 y 2)

En la rama `feat/inventory-unified`; sin desplegar. Sin migraciones ni capacidades nuevas.

**Added**
- Publicar u ocultar varios productos a la vez desde Panel › Productos: columna «E-commerce»,
  filtros Todos / Publicados / Internos y por tipo (por serie / por cantidad), selección por fila,
  por página o de todos los resultados con su total exacto, confirmación con la cuenta y resultado
  con lo que cambió, lo que ya estaba y lo que no se publicó y por qué.
- `POST /api/admin/products/bulk-visibility/` (`products.manage`). Sólo cambia «en la web»: ni
  activo, ni precio, ni stock, ni equipos, ni Kardex.
- La lista de productos del panel filtra por `is_serialized` y cada fila dice qué le falta para
  publicarse en bloque (`publication_problems`).
- Descargas del inventario: `GET /api/admin/inventory/exports/{products,stock,units,full}/`, en
  modo «para volver a cargar» o «reporte», con filtros de sucursal, categoría, tipo, existencia,
  condición, visibilidad y estado. El inventario completo es un ZIP con `Productos.xlsx`,
  `Stock.xlsx`, `Equipos.xlsx` y `LEEME.txt`.
- Panel «Exportar» en Inventario › Carga de stock.

**Changed**
- El carrito marca `available: false` la línea de un producto que dejó de venderse en la web, la
  muestra como «Ya no está disponible», no la suma y no ofrece seguir al pago mientras esté.
- Las cargas masivas rechazan los reportes (hojas «Reporte de …») y leen sin el apóstrofo de
  protección el texto que las descargas guardan delante de `=`, `+`, `-` o `@`.
- En la lista de productos, «Solo stock interno» se muestra en la columna «E-commerce».

**Fixed**
- El archivo de stock para volver a cargar ya no incluye productos con número de serie, que la
  carga de stock rechazaba: lo descargado vuelve a entrar sin filas con error.

**Security**
- La lectura del carrito ya no entrega precio, stock ni descripción de un producto oculto o
  desactivado después de añadirse, y no se puede cambiar la cantidad de esa línea.
- Las descargas nuevas protegen las celdas que una hoja de cálculo ejecutaría como fórmula,
  entregan «Costo» sólo a quien puede ajustar inventario y no salen de las sucursales de quien
  descarga.

## 2026-10-13 — CI-OPTIMIZATION-01: la suite del backend, en fragmentos paralelos

En la rama `chore/ci-optimization`; sin push. Sólo CI, scripts de desarrollo y sus pruebas: ningún cambio
de comportamiento, de migraciones ni de settings.

**Changed**
- `Backend PostgreSQL Validation` corre la suite completa en 6 trabajos a la vez (`Django shard N/6`) y
  un trabajo final, `Django tests on PostgreSQL`, que falla si alguno no pasó. Antes: un solo trabajo de
  50–100 min. Esperado: 12–20 min (por medir en GitHub).
- El backend también se dispara cuando cambia un archivo de fuera de `backend/` que leen sus tests:
  `deploy/**`, `docker-compose.prod.yml`, `docs/internal-parity-matrix.md`, `frontend/app/admin/**`,
  `frontend/app/auth/components/DevQuickLogin.tsx`.
- Un push a `master` ya no cancela el run del push anterior (en los PR, sí). `Frontend UX/UI Validation`
  corre también en `master`: valida el merge y deja la caché de npm donde los PR pueden leerla.

**Added**
- `backend/scripts/ci_shards.py` (reparto por clase, sin lista que mantener) y `ci_shard_weights.json`.
  Añadir o borrar un test no mueve a ningún otro de fragmento, y las `TransactionTestCase` van siempre
  juntas: cinco de ellas sólo pasan después de que otra haya vaciado la base (TEST-TTC-RESET-SEQ).
- `backend/scripts/affected_tests.py`: qué tests correr primero según lo que cambió. Ayuda local; el CI
  no la usa.
- `store/test_ci_shards.py`, `test_affected_tests.py`, `test_ci_workflow.py` (44 pruebas).

## 2026-10-13 — Despliegue de #111 (y constancia del de #110)

**Deployed**
- `master` `14e658c` (merge de #111) en producción desde el 2026-10-13, 15:35 (Lima). El
  servidor pasó de `dfc817a` a `14e658c` con copia previa (`db-20261013-152521.sql.gz`,
  `evidence-20261013-152521.tar.gz`) y las migraciones `0118`–`0121`.
- Entran CLIENT-SEC-01 (fase 1) y la base de DELIVERY-01.
- #110 («Mi Cuenta Web», migración `0117`) estaba en producción desde el 2026-10-11,
  22:46 (Lima), de `3057dd2` a `dfc817a`.

**Verified**
- Copia previa restaurada en un PostgreSQL desechable; `0118`–`0121` aplicadas,
  deshechas y aplicadas otra vez sobre esa copia, con el esquema de vuelta idéntico al
  de `0117`.
- Pedidos, totales, pagos e intentos, iguales antes y después. Salud, páginas, rutas
  nuevas y registro, sin sesión y sin crear datos.

**Not changed**
- Pasarela de pago (TEST), variables de entorno, `deploy/Caddyfile` y cron del servidor.

## 2026-10-13 — CLIENT-SEC-01: ajustes tras la revisión previa al despliegue

En la rama `feat/client-sec-delivery`; sin desplegar. Sin migraciones.

**Fixed**
- Un pedido de invitado cuyo documento está en la ficha de una cuenta recibe, al pagarse, una
  ficha propia sin documento: el comprador vuelve a recibir «listo para recoger» y «enviado».
  Antes (en esta rama, nunca publicado) quedaba sin ficha y sin avisos.
- Dos personas archivando el mismo documento a la vez: la segunda recibe «ya existe» (409) en
  vez de un error.
- Un pedido pagado que pierde una carrera al archivarse se reintenta; un fallo al archivarlo
  ya no puede hacer fallar la confirmación del pago.

**Security**
- Para tratar un pedido pagado como «la misma persona reintentando» tienen que coincidir
  nombre, teléfono y correo con la ficha provisional, no sólo el correo.

**Changed**
- `ops_status` no cuenta como «sin evaluación» los pedidos anteriores a la primera evaluación.

## 2026-10-13 — CLIENT-SEC-01 y DELIVERY-01: correcciones tras la tercera revisión

En la rama `feat/client-sec-delivery`; sin desplegar. Sin migraciones nuevas.

**Security**
- Los avisos de un pedido («listo para recoger», «enviado») van al correo escrito en ESE
  pedido, no al de la ficha a la que está enlazado.
- El pedido de un invitado sólo se enlaza por documento con una ficha establecida (creada por
  el personal, o con un pedido pagado o una reparación). Una ficha que sólo dejó un checkout
  sin pagar ya no recibe el pedido de la persona real; ese pedido se archiva al pagarse.
- En el mostrador, una ficha no establecida que tiene el documento de la persona que se está
  atendiendo lo suelta (auditado) en vez de devolver «ya existe»; una establecida conserva
  el 409. Vale también para la ficha que una cuenta declaró para sí en «Mi cuenta».
- Un checkout con sesión nunca escribe el documento en la ficha de la cuenta: se añade en
  «Mi cuenta». Deja de saltarse el cupo diario y de responder si un documento está en uso.
- Al desvincular una cuenta de una ficha, su libreta de direcciones se apaga.
- El checkout de la app exige para el carné de extranjería lo mismo que la web (6 a 12 letras
  o números ASCII).

**Fixed**
- Quien compró con cuenta y tiene la ficha sin documento puede completarlo una vez desde
  «Mi cuenta» aunque ya tenga un pedido pagado.
- Reenviar el enlace de seguimiento derivado junto con otra guía ya no lo deja congelado; la
  respuesta del personal dice si el enlace es escrito (`tracking_url_typed`).
- Un medio carácter en la dirección de seguimiento o en la plantilla del transportista se
  rechaza en vez de dar error.
- Con 200 direcciones quitadas ya no se bloquea la libreta: la nueva ocupa la fila quitada
  más antigua.
- Un teléfono pegado con espacios duros se acepta en «Mi cuenta» igual que en el checkout.
- La explicación de la retención dice los tres estados que retiene: listo para retiro,
  enviado y entregado.

**Changed**
- En la señal de contacto, pedidos con el mismo teléfono, correo y documento son un solo
  comprador (una persona en varios dispositivos).
- `ops_status` añade dos notas, nunca alarmas: pedidos pagados que esperan revisión (con la
  antigüedad del más viejo) y pedidos en línea del último día sin evaluación.

## 2026-10-12 — CLIENT-SEC-01 y DELIVERY-01: correcciones tras la segunda revisión

En la rama `feat/client-sec-delivery`; sin desplegar.

**Security**
- Un checkout con sesión ya no deja en la ficha de la cuenta un documento que el perfil no
  dejaría guardar: la ficha lo toma sólo si la cuenta probó su correo y nadie en la tienda
  lo tiene; si no, la ficha nace sin documento y el pedido conserva lo escrito.
- El código de «Verificar mi correo» tiene un propósito de token propio y su correo no lleva
  enlace: una cuenta apagada después de pedirlo ya no se reactiva por la recuperación, el
  enlace o el reenvío. El código queda ligado a la dirección a la que se envió.
- El cupo de tres cambios de documento al día se cuenta en la auditoría
  (`customer_document_change_attempted`), no en la memoria del proceso: un reinicio no lo
  devuelve. Un doble toque en el primer guardado gasta un intento.
- Los dos checkout (web y v1) sólo admiten los dígitos 0-9 en documento y teléfono.
- Una ficha archivada no se borra al reclamar una reparación ni admite direcciones nuevas.

**Fixed**
- Una fecha de envío fuera de 2000-01-01 … hoy + 1 año se rechaza (antes una fecha del año
  9999 se guardaba y dejaba el pedido ilegible para todos); el flete se acota antes de
  redondearse; un texto con medio carácter se rechaza en vez de dar error.
- El enlace de seguimiento sigue al transportista y a la guía cuando es el derivado, y sólo
  cambia al escribirlo cuando lo escribió una persona (`OrderDelivery.tracking_url_typed`).
- Una entrada de agencia se puede corregir aunque su transportista se haya apagado.
- La tarjeta «Entrega» muestra el nombre de quien la escribió o «Personal de la tienda»,
  nunca el usuario.

**Changed**
- Las señales de velocidad cuentan también pedidos pagados (el sexto en una hora por origen,
  o en un día por teléfono, correo o documento) y se vuelven a mirar cuando el pedido se paga.
- Un comprador tiene como mucho 200 entradas de libreta entre activas y quitadas (desde la
  tercera revisión, al llegar se reutiliza la quitada más antigua).
- Deshacer la migración `0121` ya no borra los registros de entrega.
- `0118` añade la tercera opción de `AccountToken.purpose`; `0119` crea `OrderDelivery` con
  `tracking_url_typed` y sin `source_address` (ninguna de las dos estaba publicada).

**Removed**
- `OrderDelivery.source_address`: ningún código la escribía.

## 2026-10-12 — DELIVERY-01 (base)

En la rama `feat/client-sec-delivery`; sin desplegar. Aditivo: el checkout, los precios, los
documentos y los correos no cambian, y el flete no entra a ningún total.

**Added**
- Configuración › Transportistas: las agencias y couriers de cada empresa, con su dirección de
  seguimiento (`/api/admin/carriers/`). No se crea ninguno por defecto.
- «Mis direcciones» en «Mi cuenta»: varias direcciones y agencias por tienda, con destinatario
  propio y una predeterminada (`/api/account/addresses/…`, `/api/v1/customer/<slug>/addresses/…`).
- Registro de entrega por pedido en línea (`OrderDelivery`): se crea con el pedido y el
  personal completa transportista, agencia, flete (informativo), guía y seguimiento desde la
  tarjeta «Entrega» del pedido (`…/orders/<id>/delivery/`, panel y API interna).
- En «Mis pedidos»: transportista, agencia, número de guía y enlace «Seguir mi envío».
- En el checkout: «Usar una dirección guardada» (sólo rellena el formulario).
- Migraciones `0119_delivery_base` y `0121_delivery_records_backfill`.

**Not started** (decisiones del propietario)
- Recojo en agencia como opción del checkout, cobro del flete, y los datos nuevos en
  documentos y correos.

## 2026-10-12 — CLIENT-SEC-01: correcciones tras la revisión

**Security**
- El pedido de un invitado ya no se enlaza por documento con la ficha de una cuenta: una
  cuenta no puede recibir el pedido ni los avisos de otra persona por tener su documento
  (enmienda a DEC-CUSTOMER-01).
- Guardar un documento en el perfil exige el correo verificado y tiene un cupo de tres cambios
  al día por cuenta.
- El titular no puede cambiar una ficha archivada, ni el documento de una ficha que creó la
  tienda o que ya tiene un pedido pagado, ni dejar un documento en blanco.
- DNI, RUC y teléfono sólo admiten los dígitos 0-9; los nombres no admiten caracteres de control.

**Added**
- «Verificar mi correo» en «Mi cuenta»: código de 6 dígitos al correo de la propia cuenta
  (`/api/account/verify-email/send/` y `/confirm/`, y sus equivalentes en v1).
- Todo enlace de recuperación de contraseña usado deja el correo como verificado.

**Changed**
- Un pedido pasa a «por revisar» —y avisa al personal— cuando se paga, no cuando se crea.
- Las señales de velocidad cuentan compradores distintos, y a los demás sólo si pagaron.
- La retención opcional incluye «listo para retiro».
- Reclamar una reparación funciona aunque antes se haya abierto «Mi cuenta», si esa ficha está vacía.
- Quien revisa un pedido se muestra por su nombre o como «Personal de la tienda».
- Migración `0120_client_sec_verified_backfill`: los datos de `0118` pasan a su propia migración.

## 2026-10-12 — CLIENT-SEC-01 (fase 1)

En la rama `feat/client-sec-delivery`; sin desplegar. Consultivo por defecto: sin tocar la
configuración, comprar, pagar y despachar funcionan como antes.

**Added**
- «Mi cuenta»: el comprador ve y corrige su nombre, teléfono y documento en la tienda
  (`/account`; `GET/PATCH /api/account/profile/` y `/api/v1/customer/<slug>/profile/`). El correo
  se muestra con «Verificado / Sin verificar» y no se edita ahí.
- `UserProfile.email_verified_at`: cuándo se probó el buzón de una cuenta. Campo añadido
  `email_verified_at` en `auth/me/` y `v1/auth/me/`.
- Evaluación de cada pedido en línea al crearse (`OrderRiskAssessment`): nivel bajo, medio o
  alto y los motivos, sin datos personales ni la IP.
- Revisión manual en el panel y en la API interna (`…/orders/<id>/risk-review/`): Aprobar o
  Rechazar con una nota, una vez por pedido. Es una referencia: no cancela, no devuelve ni
  cambia el pago o el despacho.
- En la lista de pedidos: etiqueta de riesgo y filtro «Por revisar» (`?review=pending`).
- Aviso en el panel «Pedido por revisar» para quien gestiona pedidos en esa sucursal.
- Configuración › Revisión de pedidos: «Monto de revisión» y «Retener los pedidos por
  revisar», las dos apagadas al nacer. Con la retención encendida, un pedido con revisión
  pendiente no se puede marcar como enviado o entregado hasta que alguien decida.

**Changed**
- El rechazo de «enviado / entregado» dice su motivo en `code`: `units_pending` (como antes) o
  `review_pending`.
- Migración `0118_client_sec`.

**Known, not fixed** (`docs/AUDIT_MEMORY.md`)
- CLIENT-SEC-VALIDATORS, CLIENT-SEC-IPHASH-RETENTION y las demás CLIENT-SEC-* de la tabla de
  deuda. (CLIENT-SEC-DOC-CLAIM y CLIENT-SEC-VERIFY-ACTIVE se cerraron: ver la entrada de arriba.)

## 2026-10-11 — PAY-GUARD-03

**Fixed**
- El checkout de la web limita también a las cuentas con sesión (10 por minuto, por usuario).
- «SDK web / Checkout»: `IZIPAY_ENV` con mayúsculas o espacios se lee igual que lo lee
  el cobro; antes un cambio de pasarela podía pasar con un formulario de producción abierto.
- Una pausa de pagos ya no vuelve al reactivar una pasarela que se apagó al cambiar de producto.
- `deploy/preflight.py` avisa de `PAYMENTS_PUBLIC_TEST` con cualquier valor que no sea
  vacío, `0`, `false`, `no` u `off`.
- El rechazo de un cambio de pasarela dice qué se puede hacer cuando la pasarela cobra
  con las variables del servidor (ahí no hay pausa).

**Added**
- La comprobación de estado da ATENCIÓN si `PAYMENTS_PUBLIC_TEST` está encendido en un
  servidor que no es de desarrollo.

**Known, not fixed** (`docs/AUDIT_MEMORY.md`)
- PAY-CAP-ATOMIC, PAY-WEB-ORPHAN-ORDER, PAY-FOREIGN-GOLIVE, PAY-RECONCILE-CAPACITY,
  PAY-DEFERRED-WINDOW, PAY-NOGATEWAY-500.

## 2026-10-11 — PAY-GUARD-02

**Added**
- «Pausar pagos nuevos» / «Reanudar pagos» en la consola: la pasarela deja de abrir pagos
  al momento, sin palabra, y sigue confirmando los ya abiertos (`IntegrationConfig.paused`,
  en la migración `0117`). Registros `integration_paused` e `integration_resumed`.
- Ajuste `PAYMENTS_PUBLIC_TEST` (apagado por defecto): el único modo de abrir los pagos
  de TEST a cualquiera. Las pruebas lo encienden en `store.suite_runner` (`TEST_RUNNER`).
- Límite por usuario en el checkout de la app (`checkout_user`, 10 por minuto) y tope de
  tres formularios vivos por pedido.
- Campo `code` en las respuestas de la app que no llevan formulario.
- `deploy/preflight.py`: ATENCIÓN si `PAYMENTS_PUBLIC_TEST` está encendido.

**Changed**
- Las claves de TEST no cobran a compradores vengan de la consola o de las variables del
  servidor. Antes, sólo las de la consola estaban restringidas.
- Revocar la configuración de pagos de la consola se rechaza si con ello pasaran a cobrar
  las claves de las variables del servidor.
- Un cambio de pasarela que no puede consultar los cobros abiertos (pasarela caída, que
  rechaza, que contesta con error, o sin credenciales) pide `INTERRUMPIR` y los deja en la
  auditoría, sean cuantos sean. La consulta previa dura como mucho 20 segundos.
- La conciliación no pregunta por intentos abiertos con otra tienda u otro entorno, cierra
  lo que nadie intentó pagar al final de su ventana, y reserva una cuarta parte de cada
  pasada para quien lleva más sin respuesta.
- Un rechazo con el formulario todavía vivo queda anotado en el intento, que sigue abierto.
- `reconcile_payments --reference` contesta con una frase que dice qué pasó.
- `deploy/preflight.py`: las claves de TEST del archivo ya no son «OK» («no para una
  tienda publicada»), y `MICUENTAWEB_API_URL` tiene que ser la oficial.
- Un checkout que encuentra la pasarela cambiada o en pausa ya no marca el pedido como
  fallido.

**Security**
- Las credenciales de «SDK web / Checkout» ya no muestran sus claves al imprimirse.

## 2026-10-11 — PAY-GUARD-01

**Added**
- Cambios de pasarela con cobros abiertos: el servidor consulta a «Mi Cuenta Web» y
  rechaza (409) activar el otro producto, apagar, revocar, cambiar de tienda o de
  entorno mientras un cobro pueda cobrarse todavía o siga sin resolver. La pantalla dice
  «El cambio no se hizo», cuántos son y cuándo reintentar.
- Registro de auditoría `payments_interrupted`, por empresa, cuando un cambio se hace
  sin poder comprobar los cobros abiertos («SDK web», o pasarela que no responde).
- Con claves de TEST de la consola sólo un MASTER abre un pago; los demás reciben «El
  pago en línea todavía no está disponible en esta tienda» (503, `payment_test_only`).
- `PaymentTransaction`: entorno y tienda con que se abrió el intento, cuándo se
  consultó por última vez, y los estados «Sin intento de pago» y «Cobro duplicado»
  (migración `0117_payment_attempt_origin`).
- Registros de auditoría `payment_confirmed_in_test`, `payment_duplicate`,
  `payment_undecided` y `payment_integrity_failed`, con la empresa del pedido.
- `reconcile_payments --reference <referencia>`: un solo intento, tenga la antigüedad
  que tenga.
- La comprobación de estado da ATENCIÓN por un cobro duplicado y por una respuesta de
  la pasarela que no se pudo decidir.

**Changed**
- Renovar contraseña o claves de «Mi Cuenta Web» en la misma tienda y entorno ya no
  pide `INTERRUMPIR` con cobros abiertos.
- Una notificación de rechazo de «Mi Cuenta Web» deja el intento esperando mientras su
  formulario pueda cobrar (30 minutos); antes lo cerraba y el pago que viniera después
  ya no se consultaba.
- Un pago autorizado sobre un pedido ya pagado queda como «Cobro duplicado»; antes,
  como un pago más.
- La conciliación pregunta primero por quien lleva más sin respuesta (antes, por el más
  reciente), cierra lo que nadie intentó pagar, y termina si la pasarela rechaza a
  quien llama.
- Antes de abrir un segundo formulario para un pedido se pregunta a la pasarela por el
  anterior.
- «Dirección de la API» de «Mi Cuenta Web» sólo admite `https://api.micuentaweb.pe`. No
  se siguen redirecciones.
- `reconcile_payments --hours` exige 1 o más.

**Fixed**
- Un pago repetido ya no vuelve a pagar un pedido que dejó de estar pagado.
- Varias conciliaciones a la vez dejan un solo `payment_reconciled`.
- Una respuesta cortada o ilegible de la pasarela ya no escapa como excepción.

**Security**
- Las credenciales de «Mi Cuenta Web» ya no muestran la contraseña al imprimirse.
- Un intento abierto con una tienda o un entorno no se confirma con otros.
- Un resultado que no declara haber sido firmado no se registra como firmado.

**Not verified**
- Nada contra la pasarela real. Lo que la prueba con claves de TEST tiene que contestar
  está en `docs/pagos-equipos-documentos.md` §1.5.

## 2026-10-11 — IZIPAY-MCW-AUDIT-01

**Added**
- `python manage.py reconcile_payments`: pregunta a «Mi Cuenta Web» (`Order/Get`) por
  los pagos que siguen sin notificación y paga los que estén cobrados, por el mismo
  camino y con las mismas comprobaciones que una notificación. Opciones `--hours` y
  `--limit`. Línea nueva en `deploy/crontab.example`, cada cinco minutos.
- Registro de auditoría `payment_reconciled`, con la empresa del pedido, por cada pago
  confirmado así. El intento queda sin firma verificada.
- La pasarela falsa de las pruebas exige también el contrato publicado de `Order/Get`.
- Segunda prueba contra el entorno real (se omite sin claves de TEST): qué contesta
  `Order/Get` de una referencia que nadie pagó.

**Changed**
- Notificación de «Mi Cuenta Web»: la firma se comprueba sobre la cadena recibida o,
  como el ejemplo oficial, con `\/` vuelto `/`. Antes, una notificación auténtica con las
  barras escapadas se rechazaba.
- La nota «pagos sin respuesta» de la comprobación de estado dice cómo preguntar a la
  pasarela.

**Docs**
- `docs/pagos-equipos-documentos.md` §1: comparación punto por punto con la
  documentación oficial y su página; pasos para escribir las claves de TEST en el
  panel; la consulta de pagos sin notificación.
- DEC-PAY-03. Deuda nueva: PAY-TEST-PUBLIC, KRYPTON-GLUE, PAY-RECONCILE-REHEARSAL.

**Not verified**
- Nada contra la pasarela real: no hay credenciales. No se hizo ningún cargo.

## 2026-10-11 — Despliegue de #102–#108

**Deployed**
- `master` `3057dd2` (merge de #108) en producción desde el 2026-10-11, 16:32 (Lima). El
  servidor pasó de `36c2fa4` a `3057dd2` con copia previa (`db-20261011-162832.sql.gz`,
  `evidence-20261011-162832.tar.gz`) y las migraciones `0113`–`0116` aplicadas.
- Entran MAIL-TEMPLATE-01 (fases 1, 2 y 3A), PRODUCT-DESTINATION-01, REGISTER-RACE-01,
  SERIAL-PICK-01 e IMPORT-SPLIT-01.

**Verified**
- Copia previa restaurada en un PostgreSQL desechable; `0113`–`0116` aplicadas,
  deshechas y aplicadas otra vez sobre esa copia, con el esquema de vuelta idéntico al
  de `0112`.
- Salud, páginas públicas, catálogo, rutas nuevas, esquema y registro, sin crear datos.

**Not changed**
- Pasarela de pago, variables de entorno y `deploy/Caddyfile` del servidor.
- La columna `price_override` sigue en la base (SERIAL-PRICE-DROP).

## 2026-10-11 — IMPORT-SPLIT-01

**Changed**
- Plantilla de productos: una sola hoja, «Productos», con sus cabeceras. Se retiran la
  fila de ayuda, la de ejemplo y las hojas «Categorías», «Instrucciones» y «Ejemplo».
  Se descarga como `carga-masiva-productos.xlsx`.
- Plantilla de equipos: una sola hoja, «Equipos». Se retiran la fila de ejemplo y la
  hoja «Instrucciones». Se descarga como `carga-masiva-equipos.xlsx`.
- La categoría se elige de una lista dentro de la celda, con las categorías activas de
  la empresa. Antes apuntaba a una hoja aparte.
- Menú: «Carga de productos» y «Carga de stock» en lugar de dos «Carga masiva».
- Carga de equipos: se rechazan más cabeceras de cantidad o de stock («Cantidad
  inicial», «Stock inicial», «Nº de unidades», «Inventario»…), no sólo las llamadas
  exactamente «Cantidad» o «Stock». No se rechazan las que cuentan otra cosa («Stock
  mínimo», «Unidades de medida», «Cantidad de SIM»).
- Carga de equipos: la columna de serie se reconoce también como «Nº de serie», «No. de
  serie», «Nro. de serie» y «S/N».
- La lista de «Destino» ya no bloquea lo escrito; un valor que no es ninguno de los dos
  sigue siendo un error de fila al subir.
- En la previsualización de cualquier carga, las filas con aviso van justo después de
  las de error.

**Added**
- Nota en cada cabecera de las dos plantillas con lo que va debajo.
- Plantilla de equipos: lista de condición y lista de las sucursales de quien descarga.
- Entrada de menú «Carga de equipos».
- Cada pantalla de carga dice qué hace y enlaza a las otras dos.
- Carga de productos: una columna de stock, de serie o de IMEI se avisa a la vista en la
  previsualización, con a dónde ir.
- Carga de equipos: aviso en la fila cuando trae IMEI y el producto no está marcado
  «lleva IMEI». El IMEI se guarda, como en «Registrar equipo». La cuenta de esas filas
  queda en el resumen del trabajo y en la auditoría de la carga
  (`imei_on_unmarked_products`).

**Sin cambio**
- Los archivos hechos con las plantillas anteriores se siguen importando.
- Sin migración.

**De la auditoría independiente, antes de publicar el cambio**
- El aviso de IMEI bajo un producto sin marca no se veía en archivos de más de 300
  filas: la fila avisada quedaba fuera de las mostradas y la cuenta no salía en pantalla.
- La cuenta de la auditoría era la de la previsualización, no la de lo guardado.
- El rechazo por columna de cantidad alcanzaba a «Stock mínimo» o «Unidades de medida» y
  dejaba pasar «Nº de unidades» o «Inventario».
- Un carácter de control en el nombre de una sucursal hacía fallar la descarga de la
  plantilla de equipos.
- Un nombre de categoría o de sucursal que empieza como una fórmula se ofrecía en la
  lista, y elegirlo en Excel lo habría escrito como fórmula.
- El enlace «Descargar errores» de la carga de equipos no llevaba la empresa elegida.

Desplegado el 2026-10-11 (`3057dd2`).

## 2026-10-11 — SERIAL-PICK-01

**Changed**
- Caja: un producto con serie no se vende sin identificar el equipo. Se escanea su IMEI
  o su serie, o se escribe a mano cuando el lector no lee. Antes se vendía el más antiguo
  de la sucursal sin dejar elegir.
- A mano: el IMEI se valida (15 dígitos y dígito de control), se muestra qué equipo es y
  se confirma. Queda registrado que se escribió a mano y quién lo hizo.
- Pedido web: el equipo que se entrega se identifica en la ficha del pedido. Sin eso el
  pedido no pasa a «enviado» ni a «entregado» (409). Puede ser otro equipo del mismo
  modelo que el que apartó el pago.
- La nota de venta y el ticket imprimen el equipo identificado, y ninguno antes.

**Added**
- `OrderUnit`: qué equipo, de qué pedido, cómo se identificó, quién y cuándo
  (`0116_serial_pick`). Auditoría `order_unit_identified` y `order_unit_forgotten`.
- `GET /api/admin/pos/units/lookup/` y `GET /api/v1/internal/<empresa>/sales/pos/units/lookup/`.
- `/api/admin/orders/<id>/units/…` y `/api/v1/internal/<empresa>/orders/<id>/units/…` para
  consultar (GET `lookup/`), identificar (POST) y quitar (DELETE) el equipo de un pedido.
  El detalle del pedido dice qué falta y qué se puede quitar todavía.
- La venta de caja acepta `units: [{id, method}]` por línea y la respuesta los devuelve.
- Los componentes de un combo dicen si llevan serie.

**Removed**
- `StockUnit.price_override`, el «precio propio» de un equipo. Se cobra el precio del
  producto. El campo desaparece del formulario de equipos y de la API. La columna sigue
  en la base una versión más (SERIAL-PRICE-DROP).

**Migración**
- Lo ya vendido se copia como «asignado por el sistema (regla anterior)» para que sus
  documentos se reimpriman igual. Un pedido web pagado y aún sin entregar no se copia:
  pedirá identificar su equipo.

**De la revisión independiente, antes de publicar el cambio**
- Un equipo identificado y devuelto antes de entregarse seguía contando como
  identificado: el pedido se podía entregar nombrando un equipo que estaba en el estante.
- Había pedidos que no se podían entregar nunca: si el equipo apartado se devolvía, o si
  el producto pasó a llevar serie después de pagarse el pedido.
- La migración borraba una columna que la versión anterior, aún en línea durante el
  despliegue, seguía leyendo: errores en caja, inventario y confirmación de pagos hasta
  levantar la versión nueva. La columna se conserva una versión más.
- La entrega bloqueaba en orden contrario a una venta: podían interbloquearse.
- Devolver un pedido entregado a «en preparación» permitía cambiarle el equipo.
- La app (API v1) recibía la negativa pero no tenía cómo identificar el equipo.
- Con dos pedidos del mismo modelo en espera, la caja del otro pedido se rechazaba como
  «vendida».
- Una negativa nombraba la sucursal donde estaba el equipo.
- Una serie de 15 dígitos no se encontraba, y una serie numérica no se podía escribir a mano.
- Un código pegado después de una lectura heredaba el ritmo del lector.

Desplegado el 2026-10-11 (`3057dd2`).

## 2026-10-08 — REGISTER-RACE-01

**Fixed**
- Varios registros simultáneos del mismo correo creaban varias cuentas, y ese correo
  quedaba sin poder verificarse ni recuperar su contraseña.

**Added**
- Índice único del correo de las cuentas, sin distinguir mayúsculas
  (`0115_user_email_unique_ci`). La migración se detiene si encuentra duplicados y no
  fusiona nada.
- `python manage.py list_duplicate_emails`: lista, en sólo lectura, las cuentas que
  comparten correo.

**Changed**
- Un registro que pierde la carrera —por el correo o por el nombre de usuario— responde
  como uno que llega tarde, no con un error 500. La entrada con Google, igual.
- El registro sólo acepta correos con caracteres ASCII.

**De la revisión independiente, antes de publicar el cambio**
- El índice comparaba con `LOWER` y el código con `UPPER`: en la base de producción
  seguían cabiendo dos cuentas con direcciones como «ınfo@» e «info@». El índice usa
  ahora la misma expresión que las búsquedas, y los duplicados se cuentan con ella.
- Dos pruebas antiguas creaban cuentas duplicadas y quedaban rotas.
- Un rechazo de la base por otro motivo se respondía como «inténtalo de nuevo» sin
  dejar rastro; ahora sólo las dos reglas de identidad se responden como «ya existe».
- Si un duplicado aparecía mientras se migraba, el error de la base citaba el correo.
- El listado mostraba enteros los nombres de una o dos letras.

Desplegado el 2026-10-11 (`3057dd2`).

## 2026-10-08 — PRODUCT-DESTINATION-01

**Added**
- `Product.is_published_online`: «Publicar en e-commerce» o «Solo stock interno», aparte
  de «activo». Campo «Destino» en la ficha del producto, etiqueta en la lista y filtro
  `?is_published_online=` en la API del panel.
- Carga masiva: columna «Destino» (también «Destino (pendiente web)»), validada y visible
  en la vista previa; categoría para las filas que no traen una.
- Plantilla de productos: hoja «Categorías» con las de la empresa, listas desplegables
  en «Categoría» y «Destino», códigos como texto.

**Changed**
- El catálogo público, su búsqueda, el detalle, el carrito, la cotización, el pago, las
  campañas y las reseñas dejan fuera lo que es sólo stock interno. Una sola definición
  (`tenancy.company_storefront_products` y `Product.is_sold_online`).
- La fila de ayuda de una plantilla se reconoce aunque diga «OBLIGATORIO» en mayúsculas,
  y la hoja «Categorías» nunca se ofrece como hoja de productos.

**De la revisión independiente, antes de publicar el cambio**
- La pantalla recuerda las columnas que usó para cada forma de archivo. Un archivo visto
  antes de existir «Destino» se importaba con ese recuerdo, sin la columna: las filas
  «Solo stock interno» nacían publicadas. La columna se busca ahora por su cabecera
  siempre que la asignación recibida no la traiga, y la vista previa dice de cuál la tomó.
- El checkout de la app respondía «<nombre> ya no está disponible» para un producto
  interno y «Producto no disponible» para uno inexistente: quien adivinara una dirección
  sabía qué hay en la trastienda. Ahora responde igual en los dos casos.
- Una cabecera parecida («Destino web», «Destino del producto») no se leía y la fila se
  publicaba sin aviso.
- Menores: un formulario que no menciona el destino creaba el producto oculto; la
  categoría elegida para filas sin categoría aceptaba una desactivada y un número
  imposible respondía 500; la vista previa anunciaba esa categoría para productos que ya
  tenían una; un nombre de categoría que empieza por `=` se escribía como fórmula; la
  ficha reenviaba el destino con que se abrió; un fallo al leer las categorías se
  mostraba como «no tienes categorías»; crear un producto o moverlo desde una carga no
  dejaba el destino en el registro de auditoría.

**Migración** `0114_product_is_published_online`: una columna con valor por defecto
(también en la base). Los productos existentes quedan publicados. Desplegado el 2026-10-11 (`3057dd2`).
## 2026-10-08 — MAIL-TEMPLATE-01 (fase 3A): código de verificación

**Added**
- El correo de verificación lleva, además del enlace, un código de 6 dígitos. Pantalla
  para escribirlo al terminar el registro y en `/auth/verify-email`: se puede pegar con
  espacios o guiones, se comprueba solo al completarlo y el móvil lo reconoce como código.
- `POST /api/auth/verify-email/code/` (correo y código).

**Changed**
- Tras registrarse, la pantalla pide el código en vez de mostrar sólo «revisa tu correo».
- Reenviar la verificación: una cuenta recibe como mucho un código por minuto y cinco
  en 24 horas. La respuesta es la misma se haya enviado o no.

El enlace sigue siendo el camino principal y no cambia. Reglas del código: 15 minutos,
5 intentos, uno nuevo anula el anterior; sólo se guarda su HMAC.

**De la revisión de seguridad independiente, antes de publicar el cambio**
- Un código correcto que llegaba a la vez que un «enviar otro» se interbloqueaba y uno
  de los dos respondía 500. Las dos operaciones bloquean ahora en el mismo orden.
- Con el servidor de correo fallando, cada reenvío gastaba la espera y el tope del día y
  mataba el código anterior sin entregar nada: 24 horas sin verificación. Un código cuyo
  correo no salió se retira.
- Las columnas nuevas no tenían valor por defecto en la base: durante un despliegue, o
  tras volver atrás, la versión anterior fallaba al crear tokens.
- El código reactivaba cuentas inactivas por otro motivo. Sólo termina las nunca
  verificadas, y reenviar sólo emite para ellas.
- Verificar por código dejaba vivos los enlaces anteriores de esa cuenta.
- Un byte nulo en el correo respondía 500; un número JSON se aceptaba como código.
- La pantalla decía «el anterior ya no sirve» aunque el servidor no hubiera enviado
  nada, y dejaba pedir otro código mientras comprobaba uno.
- El correo no pedía no compartir el código; un código agotado no dejaba rastro.
- Seis pruebas que no podían fallar, rehechas; pruebas de concurrencia nuevas.

**Migración** `0113_account_token_code`: tres columnas nuevas en `AccountToken`, con
valor por defecto también en la base; no toca filas existentes. Desplegado el 2026-10-11 (`3057dd2`).

**Deuda que la revisión dejó a la vista, anterior a este cambio (REGISTER-RACE):** varios
registros simultáneos del mismo correo crean varias cuentas (no hay restricción única en
el correo), y desde ahí ese correo no puede verificar, reenviar ni recuperar. No se
arregla aquí.

## 2026-10-08 — MAIL-TEMPLATE-01 (fase 2)

**Changed**
- Los avisos que van por correo (cotización disponible, equipo listo para recoger,
  pedido listo para recoger, pedido enviado) salen con la plantilla cuando son de la
  empresa a la que pertenece, con un botón a la página donde está el detalle: la del
  cliente o el panel, según a quién vaya. Al cliente le muestran además en qué paso está.
- **El aviso de una reparación lleva al cliente su enlace de seguimiento**
  (MAIL-TRACKING-LINK, decisión del propietario): ahí se lee y se responde la
  cotización, sin necesidad de cuenta. Sólo al cliente de esa reparación; un enlace
  revocado no se vuelve a crear.
- La prueba del servidor de correo de la consola de integraciones sale con la plantilla
  donde la tienda de la instalación la tiene. Sigue sin llevar ningún enlace de acceso.

No cambia qué eventos envían correo, a quién, ni que cada uno salga una vez. Un aviso
sigue sin llevar precios, diagnóstico ni datos del equipo. Sin migraciones. Desplegado el 2026-10-11 (`3057dd2`).

**De la revisión independiente, antes de publicar el cambio**
- El botón del cliente llevaba a `/repairs` o `/orders` aunque esa persona no tuviera
  cuenta, o el pedido no fuera de la suya: una pantalla de acceso y una lista vacía.
  Ahora el botón sólo sale cuando esa página le va a mostrar lo que el aviso anuncia, y
  el de una reparación lleva a su seguimiento.
- El mensaje de prueba del servidor de correo podía fallar al armarse en vez de salir
  sin plantilla.
- Un aviso de algo sin página conocida mostraba el progreso sin etiqueta ni botón.

## 2026-10-08 — MAIL-TEMPLATE-01 (fase 1)

**Added**
- `store/mail/`: una plantilla HTML para todos los correos de la tienda, un constructor
  de datos por tipo de correo y `mail.send(tipo, destinatario, datos)`, que envía HTML y
  texto plano con los mismos datos.
- Vista previa de cada correo sin enviarlo, sólo en desarrollo: `/api/dev/mail-preview/`.
- `docs/correos.md`.
- Dependencia `chevron==0.14.0`.

**Changed**
- Verificar correo, restablecer contraseña, invitación de personal, compra confirmada y
  aviso interno de pedido salen con la plantilla cuando son de la empresa a la que
  pertenece. Montos como `S/ 1,234.00`, fechas `DD/MM/AAAA`.
- El resto de empresas, y la plataforma cuando la tienda de la instalación es otra,
  conservan el correo de texto que tenían.

**De la revisión independiente, antes de publicar el cambio**
- Un correo de cuenta o una invitación que el servidor no aceptaba se intentaba dos
  veces, con y sin plantilla: dos correos con el mismo enlace si el servidor lo había
  tomado, y el doble de espera si estaba caído. Ahora hay un solo intento.
- Una razón social con `{{` dentro se ejecutaba como plantilla. Entra como texto.
- Un correo de cuenta pedido desde la dirección de otra empresa llevaba la plantilla.
- Los enlaces aceptaban `https://tienda@otro-sitio` y hosts ilegibles.
- El pie de texto salía de los ajustes y el HTML de la plantilla: podían firmar distinto.
- Dos pruebas de neutralidad dependían de una variable del entorno.

Sin migraciones ni variables de entorno nuevas. Desplegado el 2026-10-11 (`3057dd2`).

## 2026-10-07 — STAFF-ONBOARDING-01

**Fixed**
- Una persona invitada podía quedar sin salida: «Crear cuenta» la llevaba a un registro
  que, con la verificación de correo activa, dejaba una cuenta inactiva que no podía
  iniciar sesión, registrarse otra vez ni recuperar su contraseña.
- La invitación ofrecía «Crear cuenta» abriendo la pantalla de inicio de sesión, sin el
  correo de la invitación.
- Con el cupo de lecturas agotado, una invitación válida se mostraba como «no válida».
- Pedir la recuperación, o reenviar la verificación, para un correo con dos cuentas
  respondía 500.
- De la revisión de seguridad, antes de publicar el cambio: un token de invitación
  malformado dejaba una cuenta activa sin verificar (el registro decide ahora antes de
  crear la cuenta y falla cerrado); una cuenta terminada por recuperación podía
  reactivarse más tarde; la vuelta a la invitación se comprobaba sólo por su forma.

**Changed**
- Registrarse con una invitación válida para ese correo crea la cuenta activa.
- La recuperación de contraseña sirve también a una cuenta registrada y nunca
  verificada, y la deja activa; su enlace puede volver a una invitación y su respuesta
  dice con qué usuario se entra.
- La invitación dice qué hace falta (`account_state`) y ofrece sólo eso: crear la
  cuenta, iniciar sesión o establecer la contraseña.
- El formulario de Personal explica que la contraseña la elige el trabajador.

- El ensayo de producción fallaba en `master` desde el aviso de privacidad: su
  comprobación «sin Continuar con Google» buscaba la palabra en toda la página de
  acceso, y el aviso de cookies nombra a Google Analytics. Ahora busca el botón.

Sin migraciones. En `master` con #100 (`36c2fa4`) y **desplegado el 2026-10-08**: el
servidor pasó de `9fd9536` a `36c2fa4`, con copia previa y sin migraciones que aplicar.

## 2026-10-07 — Aviso de privacidad y cookies para medición

**PARCIAL · pendiente de publicación y validación del responsable.** Se prepara `/privacy`,
con identidad y contacto del tenant, datos de navegación y compra, envío al confirmar
el pago, proveedores, conservación técnica y cambio de preferencias. Enlaces desde
el banner y pie; consentimiento v2: las respuestas del aviso anterior no habilitan
medición. No se cambian conversiones, credenciales ni ajustes de producción.

El aviso describe medición, no sustituye la política completa de cuentas, pedidos,
comprobantes y servicio técnico. No se declara cumplimiento legal, ni se acepta en
nombre del titular la declaración de Google. Antes de publicar: validar identidad y
contacto configurados, conservación de Analytics y política para las demás finalidades.

## 2026-10-07 — CHECKPOINT 2: preparación del servidor (PARCIAL)

- Con `GO AWS`, creado Lightsail en São Paulo (4 GB / 2 vCPU / 80 GB,
  US$24/mes) y Static IP adjunta. IPv6 apagado, SSH restringido en AWS;
  puertos web cerrados, sin cambios DNS ni publicación.
- Host con Docker/Compose oficiales, deploy, UFW, swap, hora de Lima y copia
  limpia de `c5f8c93`. Secretos generados sólo en el servidor, permisos 600.
- Compose validado sin expandir valores; preflight bloqueado por destinatario
  de pedidos. Despliegue, MASTER, integraciones y backup/restore pendientes.
- Corregida la continuidad documental: #95 ya está mergeado; dominio y servidor
  ya existen. Sin cambios de código, migraciones ni frontend; #80/#85 intactos.

## 2026-10-07 — FRESH-PRODUCTION-DATA-01

**Added**
- `manage.py bootstrap_pilot_store`: en una base de producción nueva retira los tres
  productos de ejemplo que deja la migración `0002` (y su stock), sólo si están intactos,
  sin referencias y la base no se ha usado; si no, se niega y no cambia nada. Deja a la
  tienda piloto con sus cinco categorías aprobadas. Sin `--apply` sólo informa. Se niega
  también si quedan uno o dos de los tres productos, si `DEBUG` está activo y si tendría
  que escribir en una base que ya se usa.
- El ensayo mide una base recién migrada («PRODUCTION FRESH DATABASE CHECK», paso 3b) y
  comprueba el primer administrador: un usuario, MASTER, sin membresía.

**Changed**
- El ensayo crea su propio producto, con su movimiento de stock inicial: la tienda ya no
  empieza con uno de ejemplo.
- `docs/despliegue-produccion.md` §4 y §5: arranque desde una base vacía, primer
  administrador con `createsuperuser` y orden de un primer arranque.

Sin migraciones ni cambios de modelo, API o frontend.

## 2026-10-07 — CHECKPOINT 1B revalidado

- #93 mergeado con CI backend verde en su HEAD exacto; test de error real y ausencia
  de secretos, sin tocar código productivo.
- Revalidado master `a1d2a29`: frontend 933, backend relevante 445 (2 sandbox sin
  credenciales), tipos/lint/build, imágenes, compose y preflight de ensayo correctos.
- Ensayo completo 139 comprobaciones, navegador, backup y dos restores: OK.
- READY FOR CHECKPOINT 2, sólo tras aprobación para Lightsail; ningún recurso AWS.

## 2026-10-07 — FIX-MEAS-LOG-TEST-01

- La prueba de logs de conversiones deja de depender de un INFO incidental de correo.
- Provoca errores inesperados en la frontera HTTP simulada y verifica el logging real
  de `store.measurement`: sin tokens, secretos de API, identificadores ni PII; sin
  traceback, sin deshacer el pago y con reintento pendiente.
- Sólo test backend; sin cambios productivos ni migraciones. RED → GREEN en PostgreSQL.

## 2026-10-07 — Cierre de ANALYTICS-MARKETING-INTEGRATIONS-01

- La fase está en `master`: PR #91, merge `021ad13`. Sin despliegue.
- **Decidido por el propietario** (DEC-MEAS-08): CSP-01 es condición para activar un
  proveedor en producción y no se implanta con una lista especulativa; la IP y el
  navegador se envían a Meta y TikTok sólo bajo consentimiento de marketing (MEAS-IP-UA,
  aprobado); la coincidencia avanzada automática sigue desactivada; los dos ciclos de
  imports de la fase se corrigen en la microfase INT-IMPORT-CYCLE-01.
- Pendiente nuevo: MEAS-PRIVACY-NOTICE, decir en la política de privacidad y cookies lo
  que se envía a Meta y TikTok, antes de producción.
- `docs/CODEBASE_MAP.md` incorpora la consola de integraciones (#90) y la medición (#91).

## 2026-10-06 — ANALYTICS-MARKETING-INTEGRATIONS-01

**Added**
- **Google Analytics 4, Meta y TikTok** en Panel › Configuración › Integraciones ›
  Analítica y marketing, sobre la consola de #90: identificadores públicos, claves
  cifradas, sin direcciones configurables.
- **Aviso de cookies** con tres respuestas (aceptar todas, rechazar opcionales,
  configurar) y «Preferencias de cookies» en el pie.
- Servicio de analítica de la tienda y tres adaptadores; eventos de catálogo, carrito,
  checkout, registro, inicio de sesión y contacto.
- **La compra como conversión única**: una fila por proveedor escrita con el pago, enviada
  desde el servidor (Measurement Protocol, Conversions API, Events API) con reintentos.
  `manage.py send_pending_conversions`; línea nueva en `deploy/crontab.example`.
- `GET /api/measurement/config/`: lo que la tienda puede usar para medir, sin secretos.
- `ops_status` informa de las conversiones.
- [docs/analytics-marketing.md](docs/analytics-marketing.md).

**Changed**
- El icono de la pestaña es el isotipo oficial (antes: el triángulo por defecto del
  framework y un círculo de prototipo).
- La referencia del pago ya no viaja en la dirección de la página de éxito.
- Desde «Mis reparaciones», el enlace de seguimiento abre un documento nuevo.

**Fixed**
- Una ficha de producto podía contarse dos veces si su efecto se ejecutaba dos veces.
- De la revisión de seguridad independiente (1 P1, 6 P2): un script de medición ya cargado
  seguía presente al navegar dentro de la tienda a una dirección privada (ahora esa
  navegación abre un documento nuevo); los píxeles de marketing estaban en las páginas con
  formulario; el primer envío a los proveedores podía retrasar o cancelar el correo de
  confirmación; los identificadores del navegador se guardaban sin que nadie fuera a
  leerlos; la página de estado daba el evento de compra indefinidamente.

## 2026-10-06 — INTEGRATIONS-CONSOLE-01

**Added**
- **Panel › Configuración › Integraciones**, sólo para el MASTER: correo SMTP, Izipay (sus
  dos productos), WhatsApp Business por empresa, ID de cliente de Google y SUNAT se
  configuran, se prueban y se activan sin tocar el servidor ni reiniciar.
- Almacén de secretos (`store.integrations.secret_store`): Fernet con la clave raíz
  `APP_CONFIG_ENCRYPTION_KEY`; `manage.py reseal_integration_secrets` para rotarla.
- Registro de proveedores y adaptadores (`store.integrations`); modelo `IntegrationConfig`
  (migración `0111`); API `/api/admin/integrations/…`.
- `ops_status` y `healthcheck.sh` informan de las integraciones y dan la alarma si la
  tienda no puede enviar correo o cobrar, o si lo guardado no se puede leer.
- El ensayo recorre la consola en la pila de producción con un segundo servidor de correo.
- [docs/integraciones-y-secretos.md](docs/integraciones-y-secretos.md).

**Changed**
- El checkout, el correo, WhatsApp, Google y SUNAT leen su configuración en cada uso:
  la de la consola si hay una activa; si no, las variables de entorno, que quedan como
  respaldo. Una integración apagada en la consola no recurre al entorno.
- El backend arranca en producción sin correo configurado (se configura en la consola).
- `deploy/.env.production.example`: `APP_CONFIG_ENCRYPTION_KEY` es obligatoria; el correo,
  la pasarela y `WHATSAPP_PROVIDER` vienen sin rellenar, para la consola.
- `deploy/preflight.py`: exige la clave raíz; un archivo sin correo ni pasarela es
  «suficiente para arrancar», y los dos siguen figurando como datos que debe el propietario.

**Fixed**
- La prueba de conexión de la pasarela distinguía «no responde» de «rechaza las claves»
  por el texto del error; ahora por su tipo.
- Con `WHATSAPP_PROVIDER=disabled` de serie, WhatsApp no se podía activar desde la consola.
- Una configuración guardada que el servidor no puede leer rompía la petición que quería
  enviar un correo.
- De la revisión independiente (0 P1, 4 P2, 7 P3): una prueba podía dejar marcado como
  probado lo que se guardó mientras corría; un primer borrador con la prueba fallida
  tumbaba `ops_status`; las claves de producción de la pasarela salían como «Correcto»
  sin que nada las comprobara (ahora «Coherente, sin verificar»); apagar o cambiar la
  pasarela con cobros abiertos no avisaba (ahora pide `INTERRUMPIR`); se guardaban en
  claro los cuatro últimos caracteres de cada secreto.

## 2026-10-05 — EXTERNAL-PRODUCTION-CONFIG-01

**Added**
- `deploy/preflight.py`: dice qué falta del propietario (`BLOCKED/OWNER-DATA`), qué es
  opcional y qué se contradice en `deploy/.env.production`, sin imprimir ningún valor.
  Con `--smtp` entra al servidor de correo; con `--smtp-send-to` entrega un mensaje de
  prueba; con `--dns` pregunta a qué dirección apunta el dominio.
- `EMAIL_TIMEOUT` (10 s por omisión) y `EMAIL_USE_SSL` (TLS desde el primer byte, puerto 465).
- El ensayo envía el correo por SMTP de verdad, a un servidor de correo propio
  (`deploy/rehearsal_smtp_sink.py`): registro con verificación, recuperación de
  contraseña, servidor de correo caído y contraseña rechazada.
- El ensayo restaura la copia en un «servidor nuevo»: otro proyecto de Docker, volúmenes
  vacíos, el mismo archivo de variables.

**Changed**
- El ensayo ya no usa el correo de consola: comprueba que la tienda envía por SMTP y que
  ningún enlace de un solo uso queda en el registro.

**Fixed**
- MAIL-TIMEOUT: sin tiempo de espera, un servidor de correo que dejara de responder
  retenía la petición de cada registro o recuperación de contraseña indefinidamente.
- MAIL-SSL: no se podía configurar un proveedor que sólo ofrece el puerto 465.
- De la revisión de la rama: `preflight.py` podía imprimir un valor del archivo y lo leía
  distinto de como lo leen Compose y Django.

## 2026-10-05 — PRODUCTION-READINESS-01

**Added**
- Tope de tamaño para el cuerpo de toda petición, por ruta: `store/request_limits.py`
  (middleware) y los mismos números en `deploy/Caddyfile`.
- Tiempos de espera en Caddy y lectura previa de los cuerpos pequeños.
- Registro de acceso sin lo que una dirección puede llevar (`backend/log_redaction.py`,
  `backend/gunicorn_logging.py`) y el mismo filtro en el registro de la aplicación.
- `python manage.py ops_status` (sólo lectura): migraciones sin aplicar, notificaciones
  de pago rechazadas, pagos esperando y avisos de WhatsApp fallidos o atrasados.
- `deploy/healthcheck.sh` y `deploy/crontab.example`.
- `backups/LAST_OK`: la hora de la última copia completa.
- Rotación de los registros de cada contenedor (5 archivos de 10 MB).
- Ficha de cliente › «Desvincular cuenta».
- `deploy/rehearsal_flows.py`: el ensayo recorre límites, integridad de la notificación
  de pago, un equipo con serie de la recepción a la venta, documentos, seguimiento,
  WhatsApp y Google apagados, y lo que guardan los registros.

**Changed**
- Producción no arranca sin `EMAIL_BACKEND`.
- gunicorn arranca sin socket de control.
- `/seguimiento/<código>` responde `Referrer-Policy: no-referrer` en su cabecera.
- La comprobación CSRF de la API lee el token sólo de la cabecera `X-CSRFToken`; ya no lo
  acepta como campo de formulario (ninguna pantalla lo enviaba así).
- El ensayo comprueba la portada V3 (ya no pide una variante de hero), la tienda a 390 y
  1440 px sin imágenes rotas ni errores de script, y las pantallas nuevas del panel.
- `.gitignore` y los `.dockerignore`: variantes de `.env`, claves, volcados y la
  configuración real del agente de impresión.

**Fixed**
- DOC-TIMEZONE: la nota de venta, su ticket, la salida térmica y el comprobante de pedido
  imprimían la hora en UTC (una venta de las 21:30 salía fechada al día siguiente).
- BACKUP-FILES-PARTIAL: un archivo de imágenes y evidencias cortado quedaba como copia.
- RESTORE-LATE-CHECK: la restauración miraba el archivo de imágenes después de haber
  reemplazado la base de datos.
- GUNICORN-CONTROL-SOCKET: un `[ERROR]` en cada arranque del backend.
- SLOW-BODY: nueve peticiones a medio enviar, sin sesión, dejaban la API sin responder.
- BODY-LIMIT-502: una petición demasiado grande recibía a veces 502 en vez de 413.
- CSRF-BODY-READ: una cuenta sin permiso podía hacer que el servidor recibiera y guardara
  una subida completa antes de negársela.
- REHEARSAL-HERO-V3: el ensayo fallaba en `master` por una comprobación de la portada
  anterior.

## 2026-10-05 — PAYMENTS-EQUIPMENT-DOCUMENTS

**Added**
- Adaptador de pagos «Mi Cuenta Web» (API REST V4 de Izipay): creación del pago,
  formulario Krypton en el checkout y notificación firmada en
  `/api/payments/micuentaweb/notification/`. Variables `MICUENTAWEB_*`.
- `PAYMENT_PROVIDER` elige el único producto de Izipay de la instalación (`izipay` o
  `micuentaweb`).
- Inventario › Equipos › «+ Registrar equipo»: formulario propio con «Guardar y añadir
  otro» y activación del seguimiento por serie.
- Inventario › Equipos › «Cargar desde Excel»: plantilla «Equipos serializados.xlsx»,
  previsualización y registro (migración `0110`).
- Plantilla de productos con hojas «Instrucciones» y «Ejemplo»; ayuda «¿Cómo preparo
  las imágenes?» en la pantalla de importación.
- Trazado común de documentos (`document_style`, `document_layout`): logotipo, caja de
  documento, tabla con código y unidad, serie e IMEI por línea, importe en letras,
  «Página X de Y».
- Guía `docs/pagos-equipos-documentos.md`.

**Changed**
- La nota de venta A4, su ticket de 80 mm, la salida térmica, el comprobante de pedido y
  el ticket de cotización usan el mismo diseño y llevan sucursal y tipo de pago.
- La nota de venta guarda el logotipo con el que se emitió.
- Un error al registrar un equipo nombra el campo que está mal.
- El archivo de stock que trae un producto con serie indica la plantilla de equipos.

**Fixed**
- IZIPAY-SIG-ASCII, DOC-DELIVERY-CITY, DOC-MARKUP, PANEL-OPTION-AMBIGUA (ver
  `02_ESTADO_ACTUAL.md`).
- De la revisión independiente: las notificaciones de pago aceptaban un cuerpo sin tope
  (WEBHOOK-BODY); un juego de caracteres inventado o un sustituto UTF-16 suelto daban
  500; una carga de equipos se leía sin alcanzar todas sus sucursales; un costo «NaN»
  rompía la previsualización; la lista de productos del formulario sumaba el stock de
  toda la empresa.

**Blocked**
- Prueba real de cualquiera de los dos productos de Izipay: faltan las claves de TEST y
  saber cuál tiene contratado el propietario.

## 2026-10-05 — SERVICE-TRACKING

**Added**
- Identificación de equipos: serie e IMEI según el tipo, dígito de control, segundo
  IMEI, motivo cuando no se puede leer, búsqueda «ya estuvo aquí».
- Enlace de seguimiento por orden (`/seguimiento/<código>`) y «Mis reparaciones»
  (`/repairs`); el cliente aprueba o rechaza la cotización desde el enlace.
- Decisión de cotización anotada por el personal (capacidad
  `service.quotes.record_decision`), reapertura de una cotización aprobada y ticket de
  80 mm de la cotización aprobada.
- Avisos por WhatsApp por la API oficial: proveedor intercambiable, configuración por
  empresa, consentimiento, plantillas, bandeja de salida con reintentos
  (`send_pending_notifications`), webhook firmado, estados entregado/leído, pantalla
  Administración › Mensajería, `configure_whatsapp`.
- Inventario › Equipos: productos con número de serie como parte del stock; KPI
  «Equipos disponibles».
- Productos › Categorías: activa, en la portada y orden, por tienda.
- «Continuar con Google», verificado en el servidor.
- Guía `docs/seguimiento-whatsapp-equipos.md`.

**Changed**
- La orden de servicio emite sus avisos desde los pasos reales, y avisa también al
  recibir el equipo y al empezar la reparación.
- La lista pública de categorías sale en el orden de la tienda y sin las retiradas;
  la portada ilustra sólo las marcadas.
- El carrusel de productos se arrastra con ratón, se asienta sin saltos, no trabaja
  fuera de pantalla y no retiene la rueda vertical.
- El stock de un producto con serie no se mueve por cantidad en ningún camino.
- `PyJWT` se declara en `requirements.txt` (ya se instalaba como dependencia).

**Fixed**
- NOTIFY-REAL-PATHS, TRACKING-REVOKE, TRACKING-BORN, TRACKING-SPELLING.
- CAROUSEL-JUMP: «Inicio»/«Fin» tras una flecha dejaban la fila entre tarjetas; el salto
  se reafirma hasta que la fila se queda donde se pidió. CAROUSEL-RESUME: reanudar
  enseguida de pausar ya no la inmoviliza.
- READ-ONLY-GET: el estado del enlace y el ticket son lecturas puras; la migración
  `0109` da su enlace a las órdenes anteriores.
- De la revisión: el enlace se entregaba a quien sólo podía abrir la orden; tener un
  enlace bastaba para quedarse con un cliente entero; un carácter no ASCII en el
  webhook daba 500; el historial de un equipo nombraba órdenes de otra sucursal; un
  movimiento por cantidad podía cruzarse con el cambio a «con serie»; una petición sin
  respuesta a WhatsApp se reenviaba sola.

**Security**
- Aislamiento por empresa y por sucursal probado en equipos, enlaces, cotizaciones,
  avisos, webhook, unidades con serie y categorías.
- El enlace de seguimiento no es enumerable, tiene una sola escritura y se revela al
  personal sólo con capacidad y con registro.
- Ninguna credencial de WhatsApp en la base de datos, en la API ni en los registros.
- Google: sólo RS256, audiencia, emisor, caducidad, correo verificado, intento ligado
  al navegador y de un solo uso; ningún enlace automático por correo.

## 2026-10-04 — MEDIA-EVIDENCE

**Added**
- Galería de producto subida desde el panel: varias imágenes, principal, orden, texto
  alternativo; `images` en el producto público; miniatura en la lista del panel.
- Carga masiva de productos con imágenes: columnas «Imagen principal» e «Imágenes»,
  archivos sueltos o ZIP, resumen de coincidencias en la previsualización.
- Evidencias de servicio: nota por foto, etapas de repuestos, listo para entrega y
  garantía/reingreso, conteo por etapa; varias fotos a la vez, cámara del teléfono, visor
  y antes/después.
- Guía `docs/imagenes-y-evidencias.md`.

**Changed**
- `Product.image_url` pasa a `CharField` validado y es la dirección de la principal.
- La plantilla de productos lleva las dos columnas de imagen y se reconoce al subirla.
- El cliente lee la nota de la evidencia que se le comparte.
- Límites propios para evidencias (lectura 600/min, escritura 120/min) y para cargas
  masivas (30/min).

**Fixed**
- IMPORT-ERRORS-404, AUTH-REGISTER-CSRF, DEV-PROXY-UPLOAD, EVIDENCE-THROTTLE.
- Guardar el texto alternativo se comía el clic siguiente.
- De la revisión: tope de 100 archivos de Django por debajo del nuestro; un flujo
  comprimido dañado daba 500; guardar el producto podía pisar la imagen de la galería; una
  imagen quitada por otra persona daba 500; archivos huérfanos tras descartar una
  previsualización; consultas por línea en carrito y pedidos; etapa de una foto en cola;
  reemplazo de una imagen con el mismo nombre; zona de soltar deshabilitada.

**Security**
- Aislamiento por empresa probado en galería, trabajos de importación, imágenes en
  espera y evidencias; por sucursal en evidencias.
- ZIP sin extraer: rutas que salen, enlaces simbólicos, cifrado, índice desproporcionado,
  tamaño expandido y entradas que mienten sobre su tamaño.
- Una imagen subida no se coloca escribiendo su dirección, ni en el panel ni en el Excel.

**Database**
- `0100_product_images`: `ProductImage`; `Product.image_url` a `CharField(500)`.
- `0101_evidence_caption_and_stages`: `RepairEvidence.caption`; etapas nuevas.

**Tests**
- Backend 4965 (4 omitidas); Jest 659/659; Playwright 179/179.
- Nuevos: `test_product_images.py`, `test_import_media.py`, `test_evidence_context.py`,
  `product-gallery`, `product-import-media`, `evidence-workflow`, `register-csrf`,
  `api-proxy-expect` (Jest), `e2e/media.spec.ts`.

**Decisions** DEC-MEDIA-01, DEC-IMPORT-MEDIA-01, DEC-EVIDENCE-01.

**Pending** IMPORT-BODY-LIMIT, EVIDENCE-CUSTOMER-WEB, TEST-ORDER-C15; repetir el ensayo de
producción.

## 2026-10-04 — Frontend V3 y cierre

- La V3 es el único diseño de la tienda, las páginas de cuenta y el panel (#82). El
  frontend anterior queda en la etiqueta `frontend-anterior-2026-10-04`.
- Un solo hero, que sigue al tema; se retira la opción «Estilo del hero» del panel.
- CART-CSRF-01: el carrito de un cliente con sesión iniciada vuelve a aceptar cambios.
- Integrados #81 (PAYMENT-FISCAL-PRINT-01), #67 (`reportlab` 5, validado a la vista),
  #64 (`actions/setup-python` 7) y #59 (infraestructura de producción).
- Backend 4865 pruebas; Jest 609/609; Playwright 176/176, 0 omitidas.
- Abierta: #80 (TypeScript 6, DEP-TS6).

## 2026-10-04 — PAYMENT-FISCAL-PRINT-01

- Izipay falso para pruebas (token y notificaciones) y corrección de una repetición
  contradictoria que reescribía un pago autorizado. Sandbox real: BLOCKED/CREDENTIALS.
- Comprobante impreso: etiqueta del documento del adquirente por su tipo, leyenda, unidad
  de medida, precio unitario, importe en letras y forma de pago (Anexos I y II, RS 114-2019).
- Logotipo de la tienda en sus comprobantes, congelado con cada uno y sin sombra.
- Impresión en tienda: cola por sucursal, agente local para térmicas de 80 mm en red,
  ticket automático tras la confirmación de la venta e idempotencia de extremo a extremo.
- Panel: Administración › Impresoras y Configuración › Comprobantes.
- Migraciones `0098` y `0099`.

## 2026-10-04 — Infraestructura de producción: ensayo final

- `deploy/rehearsal.sh`: ensayo completo y repetible. Última pasada: `ENSAYO: OK` (83 + 26).
- Imágenes de la tienda públicas y evidencias privadas en el mismo volumen, sin rutas de archivo.
- Persistencia tras reinicio, reconstrucción y restauración de copia.
- PostgreSQL 16 en producción; limpieza diaria de imágenes sin uso; Dependabot para imágenes base.
## 2026-10-04 — Playwright completo y aislamiento de pruebas

- Tres pruebas E2E dejan de depender de otro servidor, de un detalle de Playwright y del orden.
- Playwright 171/171, 0 omitidas.
- Dependabot: #63 y #66 mergeados; #64 pendiente del propietario; #67 (`reportlab` 5) abierta.

## 2026-10-04 — Limpieza de imágenes de la tienda

- Una imagen reemplazada o nunca colocada se borra cuando nadie la usa; nunca una en uso.
- Comando `cleanup_storefront_images` (con `--dry-run`).
- PNG con transparencia por color clave; imágenes de empresas desactivadas dejan de servirse.
- Backend: 4744 pruebas en PostgreSQL, 0 fallos.

## 2026-10-04 — Cierre previo al despliegue

- Menú del panel: una entrada por pantalla, un identificador por módulo.
- `Permissions-Policy` en todas las páginas.
- AUDIT-01…06, CSP-01, LINT-EFFECT-01 e INTERNAL-UI-KIT clasificados como propuesta; la rama
  `uxui/phase-03-internal-ui`, obsoleta.

## 2026-10-04 — Cabecera y pie V4

- Categorías de la cabecera accesibles con teclado y lector de pantalla.
- Carrito móvil con nombre accesible; Escape cierra el menú; objetivos táctiles de 44 px.
- Pie: una sola marca, banda de cierre V4, navegaciones con nombre.
- Lint: de 25 a 23 avisos. Next 16.3.8, React 19.3.0, Playwright 1.63.0.

## 2026-10-04 — Profundidad de los recortes (revisión de #60)

- La sombra de un recorte se ve sobre superficies oscuras (hero oscuro, promoción, tema
  oscuro): dos variables de CSS, una sola regla.
- Sin azulejo detrás de las imágenes de servicio y ubicación.
- `alt` correcto en la imagen de la portada; nombre accesible del campo de subida (WCAG 2.5.3).

## 2026-10-04 — Actualización de dependencias (CI-03)

- Dependabot semanal para `pip`, `npm` y GitHub Actions, sin merge automático.

## 2026-10-04 — Sistema de agentes

- Se incorpora política persistente de agentes y eficiencia de contexto.

## 2026-10-03 — Portada V4 · Imágenes subidas desde el panel

- La tienda sube sus imágenes desde «Escaparate»: hero, campaña, categorías,
  servicio técnico y ubicación. PNG/WebP conservan transparencia y las imágenes
  gestionadas por el panel reciben una sombra suave de profundidad.
- El hero tiene estilo oscuro o claro, elegido por la tienda (`9c39716`).
- Portada V4: tarjetas de categoría con imagen, franja de marca, servicios de la
  tienda y «Cerca de ti» (`9c39716`).
- Migraciones `0096_storefront_images` (tubería/hero/categorías) y
  `0097_storefront_section_images` (servicio/ubicación).
- Corregido: la importación de productos y stock respondía 415 (`b5de287`).
- `backend/private-media/` pasa a `.gitignore` (`3fa9b42`).
- Frontend 563 pruebas, typecheck, lint 0/25; Playwright 170 de 170, sin fallos, omitidas ni reintentos, 10,5 min.

## 2026-10-02 — F-TENANT-01

- Seguridad multiempresa: un administrador de empresa ya no puede vincular una
  cuenta global arbitraria por id mediante `POST /api/admin/memberships/`.
- El alta directa queda reservada a `User.is_superuser` / administrador de
  plataforma para bootstrap y migración.
- El onboarding normal de personal usa el flujo existente de
  `StaffInvitation` + aceptación.
- GET/PATCH de membresías mantienen su alcance tenant/branch actual.
- Sin migraciones.
- Cobertura nueva: `backend/store/test_membership_consent.py`.
## 2026-10-03 — DEPLOY-PREP · Infraestructura de producción

- Imágenes de producción de backend y frontend, `docker-compose.prod.yml`, Caddy
  con HTTPS automático, ejemplo de variables y guiones de copia y restauración.
- Guía completa en `docs/despliegue-produccion.md`, con la cadena de proxy, el
  proceso único de Django, el almacenamiento y la limpieza diaria de sesiones.
- Ensayo de 31 comprobaciones sobre el master actual: todas pasan.
- Sin cambios en el código de la aplicación ni migraciones. Nada desplegado.

## 2026-10-02 — FE-AUTH-06 · Enlace de salto

- El proxy `/api` devuelve al navegador las redirecciones hacia otro origen y sólo
  sigue las del propio backend bajo `/api/` (`085aa2e`).
- «Saltar al contenido» vuelve a ser el primer tabulador en la tienda y en el
  panel (`cf72ee5`).
- Frontend 538 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  169/169.
- PR #55 (endurecimiento del backend) y PR #56 (AUDIT-07, DEP-05) ya están en
  master; su CI de backend pasó con 4682 y 4686 pruebas.

## 2026-10-02 — AUDIT-07 · DEP-05

- Los cambios de producto y la creación de categorías quedan en la auditoría de su
  empresa (`06cd798`).
- Cinco dependencias del backend al último parche de su línea (`65d34ad`).
- Sin migraciones.

## 2026-10-02 — Endurecimiento del backend

- Límite de 30 por minuto y dirección al renovar la sesión, web y app
  (SEC-SET-04-A, `e066183`).
- Registro de cada intento de inicio de sesión, sin la contraseña
  (AUTH-LOGGING-01, `7d5efc8`).
- Configuración que falla cerrada: `SameSite`, sólo JSON en producción, URL
  públicas obligatorias, registro a stderr (SEC-SET-03/06/09/10, `e2dff73`).
- La importación de Excel aplica su límite antes de leer (SEC-SET-05, `e2dff73`).
- `.env.example` describe las 27 variables que faltaban (ENV-04, `a6beda6`).
- La edición de líneas bloquea la transferencia; `page_size` negativo ya no da 500
  (INV-LEGACY-V1-F2/F3, `1f965f7`).
- El admin de Django no se registra en producción (SEC-SET-02, `79d1077`).
- Sin migraciones.
- PR #49 (CI de backend) y PR #54 (endurecimiento del frontend) ya están en master.

## 2026-10-02 — Backend PostgreSQL CI

- Añadido workflow `.github/workflows/backend-postgres-validation.yml`.
- Ejecuta Django con Python 3.12 y PostgreSQL 16 en pull requests que toquen
  `backend/**` o el propio workflow.
- Gates: `manage.py check`, migraciones sin cambios pendientes y suite backend
  completa en un solo proceso.
- Sin cambios funcionales ni migraciones.
- La primera ejecución completa encontró dos defectos reales: `qrcode` faltaba en
  `requirements.txt` (los PDF fiscales fallaban en una instalación limpia) y una
  prueba de orden por nombre dependía de la colación de la base. Corregidos.

## 2026-10-02 — FE-AUTH-02 · FE-AUTH-04 · FE-AUTH-07 · Endurecimiento del frontend

- La clave del carrito anónimo sale de una fuente segura (`5adcd28`).
- `fetchWithAuth` sólo envía la sesión a la API propia (`40f9769`).
- Cabeceras de seguridad en todas las rutas; `no-referrer` en las páginas con
  token en la URL (`113a8dd`).
- Sin cambios de backend, migraciones ni API.
- Frontend 527 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #53 (DRIFT-02) ya está integrado en master por merge `3f70ca0`.

## 2026-10-02 — DRIFT-02 · Sucursal en el ajuste de inventario

- Con varias sucursales al alcance, el ajuste de inventario pregunta en cuál se
  aplica y la envía; con una sola no cambia nada (`068bee1`).
- Sin cambios de backend, migraciones ni API: el servidor ya aceptaba `branch`.
- Cobertura nueva: Jest `inventory-adjust-branch.test.tsx`.
- Frontend 511 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #52 (RBAC-F3, RBAC-F4) ya está integrado en master por merge `a6d725b`.

## 2026-10-02 — RBAC-F3 · RBAC-F4 · El panel no ofrece lo que el servidor niega

- Personal sólo ofrece invitar, desactivar acceso y reenviar o revocar invitaciones
  a quien tiene `memberships.manage` (`58d17de`).
- El menú del panel usa la misma regla que las páginas: con empresa mandan las
  capacidades; Auditoría declara `memberships.view` (`847d3bf`).
- Eliminado `BranchAccessPanel.tsx`, sin uso (`4f5d736`).
- RBAC-F6, RBAC-F7 y RBAC-F11 ya estaban corregidos en `master`; RBAC-F5 era el
  mismo defecto que RBAC-F3.
- Sin cambios de backend, migraciones ni API.
- Frontend 506 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #51 (botón del menú móvil) ya está integrado en master por merge `64b4d5e`.

## 2026-10-02 — ADMIN-MENU-ARIA · Botón del menú móvil del panel

- El botón que abre la navegación del panel en un teléfono anuncia que abre un
  diálogo y si está abierto (`aria-haspopup`, `aria-expanded`, `aria-controls`)
  (`e2ce0e2`).
- Sin cambios de backend, migraciones, auth, RBAC ni API.
- Cobertura nueva: Jest `admin-menu-trigger-a11y.test.tsx`.
- Frontend 494 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- PR #50 (FE-AUTH-05) ya está integrado en master por merge `2d9cc97`.

## 2026-10-02 — FE-AUTH-05 · Tope al cuerpo en el proxy `/api`

- El proxy de Next ya no guarda en memoria un cuerpo sin límite: lee a trozos
  hasta 32 MiB (`API_PROXY_MAX_BODY_BYTES`) y responde 413 si se supera, sin llamar
  al backend (`4fad36b`).
- Sin cambios de backend, migraciones, auth, RBAC ni API.
- Cobertura nueva: Jest `api-proxy-body-limit.test.ts`.
- Frontend 491 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
  164/164.
- Barrido móvil del panel sobre `master` `1815ac1`: 37 rutas, sin desbordes.

## 2026-10-02 — ADMIN-INVENTORY-MOBILE-OVERFLOW

- Corregido el desbordamiento móvil de `/admin/inventory`: los paneles y su cuerpo
  ahora pueden encogerse dentro del grid y las tablas de 640 px permanecen dentro
  de su `overflow-x-auto` local.
- Sin cambios de backend, migraciones, auth, RBAC, tenant scope ni API.
- Cobertura nueva: Jest `inventory-mobile-layout.test.tsx` y Playwright
  `admin-inventory-mobile.spec.ts` para 320/360/375/390/414 px.
- PR #47 (HERO-MOBILE-CLIP) ya está integrado en master por merge `03581ea`.

## 2026-10-02 — HERO-MOBILE-CLIP · El texto de la tienda cabe en un teléfono

Rama `fix/hero-mobile-clip`, desde `master` `c47c538`. El titular y el párrafo del
hero ya no se cortan en pantallas de hasta 414 px (`ff564e9`); lo mismo para el
titular del catálogo y «Productos relacionados» de la ficha (`e082b26`). De 640 px
en adelante todo sale idéntico a `master`. Dos pruebas de navegador nuevas miden el
texto contra la pantalla, no sólo el ancho de la página (`ff564e9`, `e082b26`,
`42ef631`). Sin backend, sin migraciones, sin cambios en el panel. Frontend 483
pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright 163/163.

Revisión del diseño V3 pendiente, sin código: el perrito es el isotipo de cada
tienda y ya está; las imágenes del trabajo paralelo siguen fuera por falta de
licencia (STOREFRONT-IMAGES-LICENSE); INTERNAL-UI-V3 sigue pendiente porque sólo
toca piezas compartidas del panel; TENANT-TYPOGRAPHY y STOREFRONT-PILLARS-CMS
siguen como propuesta. Deuda nueva: ADMIN-INVENTORY-MOBILE-OVERFLOW.

## 2026-10-01 — STOREFRONT-V3 · Tienda pública sobre el master auditado

Rama `reconcile/storefront-v3-after-ux`. Cabecera, pie y portada leen las
categorías del catálogo real y el pie filtra con `?category=` (`a57c72e`). Carrusel
de productos, entradas con movimiento reducido respetado y línea del carrito
enlazada a su ficha (`9ba1a70`). Portada sobre el CMS de la tienda, con campaña
`home_hero` opcional y la losa oscura del hero intacta (`11f69f0`). Páginas
`/about` y `/contact` con datos de la tienda (`5380ca6`). Pruebas de navegador de
la experiencia pública (`7f49168`). Sin backend ni migraciones; ningún archivo del
panel interno cambia (su contenido queda envuelto en `internal-surface`).
Frontend 483 pruebas, typecheck, lint 0/25 y build OK (52 páginas); Playwright
143/143.

## 2026-10-01 — UX-RECON-SVC-01 · Interfaz reconciliada con servicio y caja

Rama `reconcile/uxui-after-svc`: la capa UX/UI del PR #43 (`9c1486b`) sobre el
`master` que ya incluye SVC-FUNC-01 (`d98d70c`), en el merge `ee3a8d3`. La raíz del
servicio sigue redirigiendo a Órdenes, las seis colas conservan su ruta y adoptan el
diseño de #43 en `ServiceQueue`, y la caja mantiene «Productos / Servicio técnico».
Asignación y cobro quedan como en `master`. Sin backend ni migraciones. Frontend 437
pruebas, typecheck, lint 0/26 y build OK (50 páginas); Playwright 121/121.

## 2026-10-01 — SVC-FUNC-01 · Servicio técnico e integración con la caja

Rama `feature/service-pos-functional-integration`. Cada módulo del
servicio técnico tiene su ruta y su cola (`937cf82`). Nueva capacidad
`service.orders.assign`: asignar técnico ya no exige `service.orders.manage`, y el
técnico debe poder ver órdenes y alcanzar la sucursal (`1928b05`). Nueva capacidad
`service.payments.collect`: el técnico registra pagos y no puede reversarlos
(`d62fa30`). La caja recibe equipos para servicio y asigna al técnico en la misma
operación, sin crear venta, stock, comprobante ni comisión (`6caa88c`). Prueba de
navegador del flujo caja → técnico → cobro y reparación de prueba sembrada por
`seed_demo_users --e2e-fixtures` (`1d35b7d`). Dejar una orden sin técnico exige
`service.orders.manage`; con `service.orders.assign` sólo se asigna y se reasigna
(`4796db0`). Nombrar a un técnico exige en todos los caminos
`service.orders.manage`, o `service.orders.assign` con `service.orders.view`
(`9b59a31`). Migraciones `0094` y `0095` (sólo roles estándar sin modificar).
Backend 4643 pruebas, 0 fallos, en PostgreSQL; frontend 428 pruebas, typecheck,
lint 0/33 y build OK (50 páginas); Playwright 121 de 121, sin fallos, omitidas ni reintentos, 9,0 min.
## 2026-10-01 — Integración en master y reconciliación UX/UI

ERP + F1/F2 integrados en `master` por el PR #42 (`ef9890f`). Reconciliación de la rama
UX/UI (#39) en `reconcile/uxui-after-f2` (`1fea6b9`): diseño del control interno,
carrito, catálogo y detalle adoptado; portada, cabecera, pie, servicios e inicio de
sesión se quedan como estaban en `master`. Frontend 411 pruebas OK, typecheck, lint
0/26, build OK y Playwright 118/118. Sin backend ni migraciones.

## 2026-09-30 — F2 completada

Cierre de la fase «dónde» de la auditoría (`c191a84`): E2E-02 (`9c3445f`,
trabajador desechable para las pruebas de personal, `seed_demo_users
--e2e-fixtures`) y E2E-01 (`c191a84`, la prueba fiscal afirma «Preparar factura» y
«Enviar a SUNAT» como dos pasos). Backend 4569 pruebas, 0 fallos, en PostgreSQL;
frontend 402 pruebas, typecheck, lint 0/33 y build OK; Playwright 118/118. Sin
migraciones en toda la fase.

## 2026-09-30 — F2 · DRIFT-01 y DRIFT-07

Consola de servicio: el selector de técnicos lee `candidates` (`a4be03b`). Panel de
administración: las acciones se ofrecen sólo si la persona tiene la capacidad y el
alcance por sucursal que el servidor exige (`6958ec0`, `f6dc9ca`). Frontend 400
pruebas OK, typecheck, lint 0/33 y build OK. Backend sin cambios.

## 2026-09-30 — F2 · WRITE-SCOPE-01

Las modificaciones de nivel empresa (crear sucursal, sucursal de despacho, serie de
empresa, alcance de numeración, ajustes de empresa) exigen alcance sobre toda la
empresa además de `company.manage`; quien tiene sucursales seleccionadas sólo edita
las suyas (`fa85d41`, `c076120`). Backend 4563 pruebas, 0 fallos, en PostgreSQL.
Sin migraciones.

## 2026-09-30 — F2 · RBAC-01 y RBAC-02

Empresa y sucursales sólo se leen con capacidad de lectura (`5afdb81`); sin ella,
lista vacía y 404. La sucursal por defecto de una membresía responde un único 404
para ids inexistentes, ajenos o fuera de alcance (`d18e983`). Backend 4545 pruebas,
0 fallos, en PostgreSQL. Sin migraciones.

## 2026-09-30 — F2 · F-CAP-01

Crear un producto con stock inicial exige `inventory.adjust` además de
`products.manage`; sin ella responde 403 sin efectos. Crear sin stock no cambia.
Commit `c042fea`. Backend 4528 pruebas OK en PostgreSQL. Sin migraciones.

## 2026-09-30 — F2 · Delegación por sucursal

Nueva regla `can_delegate_branch_scope`: una persona limitada a sucursales
seleccionadas ya no puede ampliarse su propio acceso, pasar a nadie a «todas»,
conceder o retirar sucursales que no alcanza (membresías e invitaciones), crear o
modificar promociones fuera de su alcance, ni leer o reescribir por id la serie
interna de otra sucursal. La edición de membresías deja de guardar cambios a medias
cuando la lista de sucursales se rechaza. Commits `20d110c`, `cccb4d2`, `70286d1`.
Backend 4520 pruebas OK en PostgreSQL. Sin migraciones.

## 2026-09-30 — Auditoría F1 y memoria central

`4a9dd5c` corrige FE-AUTH-01 (HIGH): el proxy de Next ya no puede salir de `/api/`
con segmentos codificados (`%2f`, `%2e%2e`, `%5c`). Se añade `docs/AUDIT_MEMORY.md`,
índice verificado de la auditoría (baseline, invariantes, hallazgos abiertos y
cerrados, tests y consultas rápidas). Sin cambios de backend ni migraciones.

## 2026-09-29 — Cierre de ERP-FISCAL-6 y apertura de la auditoría integral

Commits de cierre en `erp/sales-fiscal-ui`: `97043d6`, `c9e64b9`, `fabfa38`, `9525b08`
(HEAD empujado). Rama `audit/full-system-2026-09` desde `9525b08` con `origin/master`
`2dca0a3` integrado (`65aa8c1`, merge limpio, sólo documentación). Baseline medido sobre
`65aa8c1`: backend 4489 pruebas OK (3 omitidas) en PostgreSQL; frontend 357 pruebas OK,
typecheck, lint 0/33 y build OK; Playwright 113/118 con un fallo preexistente (E2E-01) y
4 pruebas no ejecutadas por él. Hallazgo CI-01 confirmado: sin integración continua
propia. La remediación de hallazgos no comienza hasta fijar el baseline.

## 2026-09-29 — ERP-FISCAL-6 · Descuentos declarados

Promociones: atribución determinista del descuento a cada componente (proporcional
al valor regular, ROUND_DOWN, mayor residuo, desempate por `product_id`), congelada
en `AppliedPromotion.metadata`; el snapshot congelado es la autoridad y el legacy se
reconstruye en memoria (ADR-42). Fiscal: factura y boleta declaran cupón y manual
como `AllowanceCharge` global `02` y promociones por línea con `00`, con importes
netos derivados del snapshot; `PayableAmount` es lo cobrado y no hay doble descuento
(ADR-43, reglas oficiales de validación 21.04.2025). POS: `receipt_options` explica
por qué una opción no está disponible, el selector no permite elegirla, y la venta
completada muestra el comprobante real; `seed_demo_users --fiscal-beta` prepara las
series DEMO en BETA. Corregido: la venta preparaba el comprobante antes de congelar
la promoción; `resolve_series` decía «factura» cuando faltaba una boleta.

Validación: backend 4489 pruebas, OK (3 omitidas), 1917,4 s; frontend 357 pruebas, OK; typecheck, lint (0
errores, 33 advertencias) y build PASAN; Playwright `pos-ticket` PASA,
`pos-receipt-options` PASA. Migraciones nuevas: 0.

## 2026-09-28 — Estabilización funcional

POS: selección de documento autorizada y transaccional, idempotencia y numeración
existente. Servicio: recepción web, cola por capacidad, entrega limitada para
Ventas y timeline corregido. Avisos/portal: respeto de estados ocultos y estado
real de correo. Fiscal UI: boleta BETA, firma previa al envío y permisos de acción.
El envío de boletas corresponde al Resumen Diario, cuya gestión web sigue
pendiente. Las notas de crédito/débito ya no reemplazan al comprobante original
en la consulta histórica de una venta. Migraciones locales aplicadas con respaldo.

Validación: backend 4412 pruebas, OK (3 omitidas); frontend 350 pruebas, OK.
Typecheck y build de producción PASAN: los dos badges de inventario se extrajeron
al módulo compartido de componentes, que es de donde esas páginas ya importaban
sus primitivas. Playwright: 17 de 17 en POS y acceso interno.

Detalle y validación:
[Informe de entrega](docs/estabilizacion-funcional-2026-09-28.md).
