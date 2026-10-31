# Estado actual

Este archivo no existía en el baseline. Se incorpora como entrada resumida a la
documentación real, sin reemplazar su historial.

## 2026-10-31 — FISCAL-PROD-01 F1 — AUDITADO — BLOQUEADO POR DECISIÓN (SUNAT producción no habilitado)

- **Veredicto.** La integración es madura para BETA y está blindada contra los errores caros (doble envío, respuesta incierta, «aceptado» sin CDR, secretos en logs, PDF distinto del XML), pero **no puede emitir en producción por diseño** (`fiscal_config.py`: `ONLY_SUPPORTED_ENVIRONMENT = BETA`, ambiente que falla cerrado, endpoint de producción no declarado).
- **Brechas de código** (sección 2 del documento): credenciales por instalación y no por empresa; sin comprobación RUC SOL = RUC emisor = RUC del certificado ni vigencia al firmar; sin alta de series de producción; sin control de plazos (factura 3 días, resumen y baja 7 días) ni cron fiscal; boleta sigue «firmada» con su resumen aceptado; la firma reconstruye desde datos vivos sin compararlos con los importes congelados; estado de factura incierta provisional; defecto H-14 (endpoint de consulta vacío) y H-06 (CDR de `sendBill` sin ligar al documento).
- **Normativa leída** (fuentes oficiales en el documento): RS 287-2017, 114-2019, 003-2023, 000048-2026 y 000143-2026 (entra el 1-ene-2027), manual del programador. **No se pudieron leer** las páginas HTML de `cpe.sunat.gob.pe` (403), las reglas de validación vigentes (xlsx) ni la tabla de errores: marcado NO VERIFICADO.
- **Pendiente del propietario / contador / SUNAT** (sección 6): canal (SEE-Del contribuyente, PSE u OSE), alta como emisor y certificado registrado en SOL, usuario SOL secundario, datos y series del emisor, afectación de IGV por producto, política de plazos y huecos de numeración, y el GO escrito del primer comprobante real.
- **Qué NO se hizo.** No se implementó soporte productivo (el diseño debe revisarse formalmente antes), no se activó nada, no se emitió nada. Los cambios de código son PR posteriores (P1–P11 del documento) y P11 no se fusiona sin GO escrito.

## 2026-10-30 — INV-COM-01C — IMPLEMENTADO — PR abierto, NO desplegado (precio por unidad seminueva en caja)

Base: `master` `a6e2f81` (producción en `d52e9b8`). **Sin migraciones.**

- **Contrato de precio.** Seminuevo identificado → `StockUnit.sale_price`; nuevo y accesorio → `Product.price`; bajo pedido → su oferta. Sin precio válido ese equipo no se vende (mensaje único para la vista previa, la venta y la pantalla). El costo nunca decide el precio y nada que envíe el cliente lo fija. Predicado `priced_by_unit` = serializado + `condition_line == used` (el mismo de `device_catalog._unit_price` de la tienda en línea).
- **Motor.** `pos_services.unit_prices` es la autoridad única; en la venta bloquea las filas (primero el stock de la sucursal, luego las unidades, orden documentado del módulo) para que el precio leído sea el cobrado. Cada equipo es un `OrderItem` con `stock_unit`; la salida de Kardex sigue siendo una por (venta, producto) y nombra los equipos; el margen usa el costo propio de cada unidad; el descuento manual se reparte por mayor resto. `_sell_chosen_units(strict=True)` vende los equipos que el cajero identificó, sin retenciones web.
- **Cambio de precio.** `expected_total` opcional → 409 `price_changed` con vista previa nueva, evaluado antes del efectivo; reintento con la misma clave posible; sin él, la venta usa el precio del servidor.
- **Paridad.** Caja web y POS v1 comparten servicio, mensajes y 409. Tienda en línea sin cambios.
- **Caja (UI).** Ficha por equipo, aviso sin precio, «Cobrar» bloqueado con vista previa desactualizada, subtotal y precios de la vista previa. Un cajero sin `products.manage` no puede cambiar precios.
- **Pruebas.** Backend 42 casos nuevos; regresión dirigida de 31 módulos (949 pruebas, OK); frontend 2590 (25 nuevos), tsc y lint limpios; E2E real 4/4 (dos equipos de una variante con precios distintos, sin precio, precio cambiado tras la vista previa, móvil 390 px); mutaciones: 15 de 16 muertas, la superviviente es equivalente (la transición de estado de la unidad repite la comprobación de disponibilidad).
- **Auditoría independiente.** Sin P0/P1. P2 (el 409 no se alcanzaba con efectivo exacto; orden de bloqueos) y P3 corregidos con prueba. Queda como nota de despliegue: seminuevos sin precio dejarán de venderse.
- **TRADE-IN.** Sigue en PROPUESTA. `docs/decisiones-propietario-trade-in.md` (D1–D12) y ADR corregido; nada de 01B/01C implementado; sin decisiones fiscales.

## 2026-10-30 — INV-COM-01B — IMPLEMENTADO — PR abierto, NO desplegado (destino comercial de cada línea)

Lectura del servidor sobre INV-COM-01A; **sin migraciones y sin escrituras nuevas**.

- **Qué dice el paso 3.** «Destino comercial» por línea: la cadena **modelo → línea → variante → unidades identificadas** (p. ej. iPhone 13 → Seminuevos → iPhone 13 128 GB Azul Seminuevo → 2 unidades identificadas), dónde se ofrece (un seminuevo, equipo por equipo en la **página de su modelo**, sin tarjeta pública propia; lo demás, tarjeta propia) y el estado: **publicado** (activo y marcado para la tienda; no promete que el cliente lo vea: eso además depende de las existencias de la sucursal de despacho y, en seminuevos, del precio de cada equipo), **preparado** (oculto y sin nada que falte: publicarlo es decisión aparte, en «Publicación e-commerce») o **pendiente** (qué falta: activar, categoría, imagen si la empresa la pide, precio propio de N equipos). Avisa si el modelo está oculto aunque la variante esté publicada y si la sucursal de la recepción **no es la de despacho** (o la empresa no tiene una): lo recibido ahí no se ofrece en línea hasta que esté en la de despacho.
- **Reglas.** Usa `product_publication.publication_problems` (la definición de siempre); en un seminuevo con modelo el precio del producto (0.00) no es un problema: cada equipo se precia. La recepción **no activa, no publica ni cambia el precio de nada**. El bloque no repite precios ni identificadores.
- **Paso 4.** «Para completar en la ficha de cada seminuevo»: cuántos equipos quedaron sin precio propio (con quién debe fijarlo según la autoridad de quien recibió) y que las fotos se añaden en su ficha, con enlace a Equipos seminuevos.
- **Pruebas.** Backend 8 casos nuevos (ubicación, pendiente/preparado/publicado, inactivo, sin precio, sucursal de despacho, sin sucursal de despacho) con 6 mutaciones muertas; frontend 12 de componente con 6 mutaciones muertas y 3 de pantalla; E2E real (Chromium 1280, iPhone 13 y WebKit) 4/4 con el destino y el paso 4.
- **No se hizo (PROPUESTA).** Fotos por unidad dentro de la recepción (una unidad no existe antes de recibirse; se añaden en su ficha), garantía por unidad (el modelo solo tiene la política de la empresa), accesorios incluidos y evaluación técnica como campos propios (hoy van en las notas), y verificar bloqueo de activación/MDM como casilla de elegibilidad: requieren ampliar el modelo y decisión del propietario.

## 2026-10-30 — INV-COM-01A — IMPLEMENTADO — PR abierto, NO desplegado (precio propio y detalles de un seminuevo en la recepción)

Contrato mínimo de `receipts/preview|confirm`; **sin migraciones**. Base: `master` `0f143a7` (producción en `d52e9b8`).

- **Qué cambia.** Una unidad de un artículo de línea **seminuevo** puede llevar su `sale_price` (lo que pide la tienda en línea por ESE equipo) y `public_notes` (lo que lee el comprador, hasta 500 caracteres): los campos que «Registrar equipos» y la carga de equipos ya aceptaban, ahora también en la recepción (`_unit` los descartaba). Un artículo nuevo o una línea por cantidad **no llevan precio propio**: se venden al `Product.price`, que una recepción nunca cambia, publica ni crea.
- **Tres importes que no se confunden.** Costo de adquisición (`costs.view`), precio de venta del equipo (`products.manage`, la misma autoridad que `PATCH` de la ficha: el precio es decisión del catálogo, no del almacén) y, más adelante, la valorización de un equipo recibido como parte de pago (TRADE-IN, solo diseño). Sin autoridad sobre precios: 400 `prices_forbidden` **solo si se envía un precio**; el equipo se recibe sin precio y queda pendiente (la vista previa lo avisa y el paso 3 lo dice). Un precio es un importe ASCII con hasta 2 decimales y mayor que cero (nunca float, booleano ni cero).
- **Huella e idempotencia.** El precio forma parte de la huella: la misma clave con otro precio es 409; el reintento no duplica. La respuesta y la auditoría **no llevan importes** (solo el conteo `units_unpriced`).
- **Frontend.** Campos «Precio de venta del equipo» (solo seminuevos y con autoridad), «Detalles para el comprador» y «Observaciones internas» en la fila de la lista pegada y en el editor individual; el precio y los detalles viajan en el cuerpo solo cuando corresponde; la revisión muestra «precio S/ x» o «sin precio propio (pendiente)».
- **Pruebas.** Backend `test_receipt_commercial` (17 casos: precio y notas guardados, varios equipos de una variante con distintos precios, sin autoridad, costos y precios como autoridades separadas, pérdida posterior de la autoridad, empresa ajena y sucursal, importes inválidos, nuevos sin precio, sin efectos de publicación) con 8 mutaciones muertas, más las 82 de recepción y el flujo de 11 equipos; frontend 19 de contrato y 3 de pantalla con 6 mutaciones muertas; E2E real (Chromium 1280, iPhone 13 y WebKit) 4/4.
- **Hallazgo que condiciona lo siguiente.** **La caja precia por `Product.price`, nunca por `StockUnit.sale_price`**, y rechaza productos con precio ≤ 0: hoy el precio propio de un seminuevo solo rige en la tienda en línea y en la etiqueta. Queda como INV-COM-01C (PROPUESTA, necesita diseño propio): `docs/AUDIT_MEMORY.md`.
- **Siguiente.** INV-COM-01B (destino comercial: dónde queda cada línea en la tienda y qué falta), en su propio PR.

## 2026-10-30 — INV-RECEIVE-UX-02 + NAV-UX-01 + NAV-UX-02 — IMPLEMENTADO — DESPLEGADO

Producción actualizada desde `b21c7d7efbe7e26b4678eb478def56419d4ae9d5` a **`d52e9b8794151c2abca1061ee7b3c483886e8440`**, el 30 de octubre a las **11:58 (Lima)**, con GO explícito del propietario. Esta entrada sustituye los estados «no desplegado» históricos de estas tres fases que siguen abajo.

- **Imágenes:** backend `blackdog-backend:release-d52e9b8` / `37cb59d55205`; frontend `blackdog-frontend:release-d52e9b8` / `6f67fc548f35`; ambos healthy, construidos del SHA exacto. CI 8/8 PASS sobre ese SHA. Sin cambios de infraestructura, configuración, dependencias ni migraciones respecto de `b21c7d7`; el único cambio funcional de backend está en el reconocimiento de candidatos de texto.
- **Respaldo previo obligatorio:** 30/10/2026 11:55 (Lima), BD 149 KB y archivos 13 MB. Gzip e integridad PASS; copia externa local, fuera del repositorio, con SHA-256 coincidentes con el servidor. Restauración aislada en PostgreSQL 16.15 local, sin red ni puertos: 132 tablas / 2.102 filas coinciden por cantidad y hash, 131 secuencias coinciden, 15 archivos íntegros; ningún índice ni restricción inválidos. Recursos desechables eliminados. BD consistente; archivos copiados en vivo, sin atomicidad conjunta garantizada.
- **Comprobaciones de la imagen nueva:** `manage.py check` PASS; `migrate --plan` sin operaciones pendientes, conexión forzada a sólo lectura. **No se ejecutaron migraciones**; esquema `0159_label_batches`.
- **Despliegue limitado:** sólo backend y frontend, `up -d --no-build --no-deps --wait`. PostgreSQL y Caddy conservaron ID y arranque; archivos de variables, Compose y Caddyfile sin cambio. Caddyfile MD5 `9f0aab332b6b2b29931afb6d72d9d6e7`. Sin cambios de firewall, certificados, credenciales, permisos ni SUNAT.
- **Humo técnico PASS:** healthcheck exit 0, web/acceso/caja/productos/inventario/asistente/herramientas HTTP 200, panel `no-store`, APIs de recepción 401 sin sesión. Lecturas autenticadas en proceso, sin crear sesión y con BD forzada a sólo lectura: contexto de caja, productos, stock y equipos 200; preview 200 con permiso y 403 sin `inventory.adjust`; coincidencia de candidatos de lista 200. Hash del módulo backend coincide con el candidato; rutas frontend nuevas presentes. Sin operaciones comerciales ficticias.
- **Baseline antes/después:** movimientos 29, obligaciones 0, lotes 8, auditorías 214, unidades 1, pedidos 7, transacciones de pago 6, productos 9 y existencias por sucursal 10: todos sin cambio durante el humo. Registros examinados desde el despliegue: 0 errores y 0 HTTP 5xx. SUNAT BETA efectivo desactivado antes y después.
- **Rollback conservado:** `blackdog-{backend,frontend}:rollback-b21c7d7`, IDs `f01b8b68e239` / `2461a31ee95c`, fijados a las imágenes anteriores verificadas; el procedimiento impide sobrescribir una etiqueta existente distinta. También se conservan `ops-b21c7d7` y R3. No fue necesario rollback; una vuelta atrás sería sólo de código, sin borrar datos ni restaurar automáticamente la BD productiva.
- **Humo visual autenticado PASS**, tras el inicio de sesión proporcionado por el propietario: nueve destinos del menú, «Más herramientas», pestañas de Stock y equipos y selección de Existencias, enlace de Productos a Recibir, paso 1 al 2 del asistente, «Pegar una lista» vacía y «Atrás» al paso 1; caja sin alertas. Menú móvil, stock, caja y asistente sin desborde a 390 px; recorridos de escritorio a 1280 px. No se añadieron líneas ni se confirmó ninguna operación. **PENDIENTE:** aceptación funcional expresa del propietario del release; no se confunde con el humo técnico. LABEL-B, LABEL-C, INV-OPS-02, deudas P3 y propuestas de precio por equipo/buscador de acciones/recepción por fila conservan su estado.
- **Evidencia:** informes privados locales `deployment.json` y `verification.json` del release `d52e9b8`, fuera del repositorio público; aquí sólo se registra su resumen sin datos sensibles.

## 2026-10-30 — NAV-UX-02 — IMPLEMENTADO — PR #214 (pestañas, página de herramientas, recibir desde Productos), NO desplegado

Sólo frontend de navegación sobre NAV-UX-01 (#213); **sin migraciones, sin cambios de backend y sin tocar rutas, permisos ni endpoints**.

- **Pestañas por destino** (`SectionTabs`, montado una sola vez en `AdminShell`): Stock y equipos, Productos y variantes, Proveedores, Etiquetas y Movimientos muestran dentro de la página las mismas pantallas, con el mismo nombre y la misma regla de acceso que el menú (`sectionTabsFor` sobre `sidebarSections`). Solo en la pantalla misma (no en fichas, formularios ni asistentes), después de conocer el acceso, con la franja desplazándose por dentro y la pestaña actual a la vista. Directorio y Ofertas siguen siendo dos pestañas con su nombre: una oferta nunca es stock propio. «Más herramientas» no lleva pestañas (son tareas distintas).
- **«Más herramientas»** tiene su página (`/admin/inventory/tools`, módulo `inventory.tools` con `anyCapabilities`): cargas con Excel, análisis y reposición por tarea; cada tarjeta es una pantalla de siempre con su permiso. El «Modo avanzado» del Centro de operaciones también va por tarea y no pierde ninguna pantalla.
- **Recibir mercadería.** Enlace en la cabecera de Productos (con permiso de ajustar inventario) hacia el asistente; los enlaces por fila «Recibir mercadería de <producto>» conservan su pantalla de siempre. En el paso 2, enlaces a las cargas oficiales de Excel (equipos / cantidades), fuera del grupo de botones y diciendo que salen del asistente; conservan su vista previa y confirmación. No hay un segundo asistente.
- **«Publicado» sin prometer.** Un artículo inactivo y publicado dice que la tienda no lo muestra (el catálogo público solo lista activos y publicados).
- **Medido** (escritorio): de Existencias a Equipos seminuevos 2 → 1 clic (pestaña); de Productos a Recibir mercadería 2 → 1; abrir la carga de equipos 2 → 2 (Más herramientas › tarjeta).
- **Pruebas.** 20 nuevas, 8 mutaciones muertas. E2E real (Chromium 1280 / 768 / iPhone 13 con toque, WebKit): 6/6 de este PR y 9/9 del menú sobre este código. Frontend 2512 verdes, `tsc` limpio, lint 0 errores.
- **Revisión independiente.** 0 P0/P1, 3 P2 (texto «sólo lectura» falso para Costos, pestañas que aparecían tarde en la configuración de etiquetas, franja móvil sin pista de desplazamiento) y varios P3; todos los P2 y los P3 aplicables corregidos.
- **Propuesto, no hecho.** Buscador de acciones del menú, que el enlace por fila de Productos abra el asistente (cambia un contrato con E2E existentes: decisión del propietario) y una sola «carcasa» del panel en `admin/layout`. Ver `docs/AUDIT_MEMORY.md` (NAV-*).

## 2026-10-30 — NAV-UX-01 — IMPLEMENTADO — PR #213 (menú lateral por destinos), NO desplegado

Sólo frontend de navegación; **sin migraciones, sin cambios de backend y sin tocar rutas, permisos ni endpoints**. Base: `master` `f3be2fa`.

- **Problema.** Productos e Inventario mostraban 25 enlaces planos para un usuario con todos los permisos (el propietario contó 23), varios de ellos puertas distintas a una misma tarea.
- **Menú nuevo (9 destinos).** Catálogo: *Productos y variantes*, *Proveedores*. Inventario: *Centro de operaciones*, *Recibir mercadería*, *Stock y equipos*, *Recuentos*, *Etiquetas*, *Movimientos*, *Más herramientas*. Las pantallas que eran enlaces son las opciones del destino en el que estás (se abre solo) o de su botón «Opciones de …» (`aria-expanded`, 44 px). «Más herramientas» agrupa por tarea las cargas con Excel y el análisis; todavía no tiene página propia.
- **Cómo está hecho.** `INTERNAL_MODULES` no cambia (lo siguen leyendo el Dashboard, las acciones rápidas, el Centro de operaciones y los permisos). Encima hay una capa de destinos (`sidebarSections`, `SIDEBAR_DESTINATIONS` en `internal-modules.ts`): cada módulo de Productos/Inventario pertenece a exactamente un destino (prueba que falla si aparece uno sin destino), el destino usa el mismo `isNavigable` que el registro y, si la pantalla principal no se puede abrir, cae en la primera que sí. El menú es UX, no autorización.
- **Medido** (escritorio, menú a la vista): entradas de primer nivel 25 → 9; iniciar una recepción 1 → 1 clic; encontrar etiquetas 1 → 1; consultar un equipo seminuevo 1 → 2; abrir la carga de equipos 1 → 2. En pantalla estrecha todo suma un clic para abrir el cajón, antes y ahora.
- **Pruebas.** 61 nuevas (invariantes del registro y los permisos para 10 perfiles de acceso: todo enlace del menú es un módulo al que se puede entrar y viceversa; activo correcto; ids únicos con barra y cajón a la vez; encabezados de grupo; teclado). E2E real (Chromium 1280 / 768 / iPhone 13 con toque, WebKit): 9/9 — nueve destinos, las 26 rutas de siempre abren y marcan un solo destino, Atrás/Adelante, teclado, cajón, tres perfiles de permisos y otra empresa. Frontend 2492 verdes, `tsc` limpio, lint 0 errores. Mutaciones del menú y de los ids/encabezados: todas muertas.
- **Revisión independiente.** 1 P1 (un E2E existente, `import-split.spec.ts`, seguía un enlace antiguo), 2 P2 (ids duplicados entre barra y cajón; encabezado que se perdía sin `costs.view`) y 5 P3; los P1/P2 y tres P3 corregidos.
- **Observado, fuera de alcance.** `/admin` desborda 24–45 px a 390 px por una tabla `sr-only` de sus gráficos (preexistente). `import-split.spec.ts` A y B fallan en el entorno local también sin este cambio. En WebKit/macOS Tab no llega a enlaces ni botones y el foco no vuelve al botón del cajón (comportamiento de Safari): allí no se exigen.
- **P3 abiertos.** Un usuario que sólo gestiona productos (sin `inventory.view`) ve «Inventario» con una sola entrada (Más herramientas › Carga de productos); sin permiso de imprimir, «Etiquetas» cae en la configuración (como antes con `company.view`).

## 2026-10-30 — INV-RECEIVE-UX-02 — IMPLEMENTADO — EN MASTER (#210, `b49bcf2`), NO desplegado

Fusionado en `master` por #210 (merge `b49bcf2`, cabeza de la rama `d0c3f1e`, CI verde 8/8 sobre ese SHA exacto). Base: `master` `2607c6b` (producción ya en INV-OPS-01 `b21c7d7`, esquema `0159`). **Sin migraciones** y sin cambios de producción.

- **Causa raíz.** El reconocedor de listas vivía en otra pantalla (carga masiva) y obligaba a elegir a mano la variante de cada fila: «Buscar en el catálogo» era un clic aparte, sólo la coincidencia exacta se preseleccionaba y cada fila bloqueaba con «Elige la variante del catálogo». Con 11 seminuevos cuyas variantes no existían, las 11 filas quedaban bloqueadas por el mismo motivo repetido, sin una acción única que las resolviera.
- **Flujo nuevo.** Dentro del paso 2 del asistente de recepción hay una pestaña «Pegar una lista» (`ReceivingListStage`, lógica pura en `receive-list.ts`): pegar → «Analizar y preparar» (el texto se vacía) → el catálogo se busca solo, **una pregunta por combinación** de modelo, capacidad, color y línea (en tandas de 100) → la coincidencia exacta se asocia sola; la sugerida (una opción parcial) y la ambigua (varias) las confirma una persona **una vez por grupo** («Usar para N equipos»); lo que no existe se propone con `CatalogProposalPanel` (sólo `products.manage` + `inventory.adjust`, atómico, idempotente, oculto de la web) y se vuelve a buscar sin perder la lista → cada equipo se revisa en su fila (serie, IMEI, estado, batería, estado estético, observaciones, costo con `costs.view`) → «Agregar N equipos listos» los pasa a las líneas → vista previa y confirmación idempotente de siempre (`receipts/preview` y `receipts/confirm`; no hay segundo escritor).
- **Resumen persistente** (listos · requieren asociación · falta información · duplicados o inválidos) y «Resolver pendientes» (catálogo primero, luego la primera fila con algo por corregir). Filas resumidas y expandibles. El lector de códigos (teclado + Enter) avanza de campo en campo.
- **Bloqueos vs avisos.** Bloquean: artículo sin asociar o desactivado, tipo de control incompatible, serie/IMEI ausentes cuando se exigen, IMEI inválido (Luhn) o repetido, batería/costo mal escritos. Avisan: línea del artículo distinta de la de la fila, RAM/chip/región que el catálogo no distingue.
- **Datos por equipo.** `battery_health`, `cosmetic_grade` (escala de la empresa) y `notes` viajan por unidad (el servidor ya los aceptaba); un equipo nuevo no lleva batería ni estado estético. La vista de revisión lista cada equipo con identificadores enmascarados. Sin `sale_price` (ver PROPUESTA en `05_` y `AUDIT_MEMORY` INV-RECEIVE-SALE-PRICE).
- **Topes.** 200 equipos y 50 artículos por recepción: se dice cuántas caben antes de agregar y el resto queda en la lista; las filas que no se pueden recibir aún pueden «dejarse para otra recepción» (memoria de la página). Nunca se confirma una parte en silencio.
- **Backend.** Un único cambio, de sólo lectura: `text_preview_match._option_payload` añade `is_serialized`, `requires_imei` e `is_active` (nada que un lector del catálogo no vea).
- **Memoria y privacidad.** Serie, IMEI, batería, costos y texto original sólo en memoria de la página (nada en `localStorage`, `sessionStorage`, URL, registros ni analítica); el borrador «crear artículo» se entrega una sola vez a la misma persona y empresa.
- **Pruebas.** Frontend 2431 verdes (lógica `receive-list.test.ts` con 10 + 9 mutaciones muertas y 2 más en la pantalla; pantalla `receiving-list-stage.test.tsx` con un catálogo falso en memoria y el caso de los 11 equipos de punta a punta). Backend `store.test_receive_list_flow` (13) contra los endpoints reales: 11 unidades, una entrada de Kardex y un lote de etiquetas por artículo, reintento y conflicto idempotentes, permisos, aislamiento entre empresas, costos, IMEI repetido o ya registrado y tope de 200 (3 mutaciones muertas); focal 194 verdes.
- **E2E real** (`frontend/e2e/inventory-receive-list.spec.ts`, navegador real + Django real + base desechable): 25/25 en la corrida final sobre el código definitivo — Chromium 1280, 768, iPhone 13 a 390 con toque y WebKit (sin desborde, botones principales y del panel de propuesta ≥ 44 px, foco visible, sin identificadores en almacenamiento, URL ni consola). El lector simulado a 0 ms por carácter no pierde ninguno (60 campos en Chromium y 60 en WebKit; antes de la corrección fallaban 4 de 6). No se probó con un lector físico, ni Firefox, ni anchos distintos de 390/768/1280.
- **Revisión independiente** (agente sin acceso a mis conclusiones), en dos rondas: 0 P0, 4 P1 y 9 P2 reales (segunda pegada sin asociar, colisión de ids, edición que reconstruía la tarjeta bajo el cursor, batería en equipos nuevos, artículo inactivo, costos de accesorios, topes, tandas, «dejar en espera») y, en la segunda, 3 P2 más (números de fila reutilizados que heredaban «en espera», aviso que tapaba el bloqueo real, fusión de accesorios con costos alternados). Todos corregidos con RED → GREEN. La validación E2E añadió un P2 real (el clic en «Quitar» se perdía justo tras editar el modelo porque la tarjeta se movía al salir del campo): ahora el cambio se aplica con Enter o con su botón.
- **Puerta de seguridad final (PR #212).** Una segunda auditoría independiente, sobre el código definitivo y sin acceso a mis conclusiones, no encontró P0 ni P1 y confirmó del lado del servidor (confirmación idempotente, permisos, costos, creación de catálogo) y de la privacidad (sin identificadores hacia el catálogo, borradores por usuario y empresa). Encontró cuatro P2 del manejo de la lista en el cliente, corregidos con prueba RED→GREEN: «Descartar lo que falta» borraba toda la lista (ahora pregunta con las cuentas y solo descarta lo que no se puede recibir); las filas en espera podían entrar a la recepción actual (ahora quedan fuera, marcadas «En espera»); una fila nueva de una combinación con artículo elegido a mano tomaba la respuesta del servidor (ahora hereda el del grupo); «Son nuevos» borraba batería y estado (ahora los conserva sin mostrarlos ni enviarlos). Más P3: el color también se limpia de identificadores, la clave de «crear lo aprobado» sale del generador seguro y cambia con la aprobación. La revisión del arreglo no halló P0–P2; sus P3 aplicables también se corrigieron. Precio de venta por equipo: auditado en `docs/AUDIT_MEMORY.md` (INV-RECEIVE-SALE-PRICE), sigue PROPUESTA.
- **Deuda y propuestas.** Precio de venta por equipo en la recepción (PROPUESTA, requiere decisión); RAM/chip/región como opciones del catálogo (P3); la zona de trabajo no se guarda por diseño; barra de acción fija en móvil (P3); controles de 42 px en filas (P3). LABEL-B sigue como fase independiente. **No desplegado:** requiere GO aparte.

## 2026-10-29 — INV-OPS-01 — IMPLEMENTADO — DESPLEGADO — ACEPTADO

Código desplegado: `b21c7d7efbe7e26b4678eb478def56419d4ae9d5` (29 de octubre, 23:12, hora Lima). El propietario confirma **8/8 PASS** de aceptación funcional en producción. Esta entrada sustituye como estado vigente los predeploy históricos de abajo.

- **Imágenes:** backend `blackdog-backend:ops-b21c7d7`, ID `f01b8b68e239`; frontend `blackdog-frontend:ops-b21c7d7`, ID `2461a31ee95c`. Ambos sanos según el informe de despliegue de Codex.
- **Verificación ya realizada:** CI del SHA exacto 7/7 PASS; frontend idéntico al árbol con CI verde de `f4240cd`; build, `manage.py check`, healthcheck (exit 0), humo HTTP y permisos de preview PASS (administrador 200, sin `inventory.adjust` 403, sin sesión 401). Tienda y acceso sin desborde a 390 y 1280 px. Los conteos de negocio comparados no cambiaron durante el humo.
- **Esquema:** `0159_label_batches`, sin migraciones nuevas ni ejecución de migraciones. Sólo se recrearon backend y frontend; PostgreSQL y Caddy conservaron identidad y fecha de arranque.
- **Respaldo y restauración:** copia nueva del 29 de octubre, 21:36, íntegra y con SHA-256 coincidente tras descargarla; restauración local aislada en PostgreSQL 16.15, sin red, 132 tablas / 2.044 filas comparadas por cantidad y hash, 131 secuencias coincidentes y 14 archivos leídos íntegramente. Sin índices ni restricciones inválidos; contenedor desechable eliminado. La BD es una instantánea consistente; los archivos se copiaron con la aplicación activa, sin simultaneidad exacta garantizada entre ambos.
- **Configuración observada:** SUNAT BETA desactivado en preflight y al terminar el despliegue; Caddyfile MD5 `9f0aab332b6b2b29931afb6d72d9d6e7` conservado. No se cambiaron firewall, certificados, credenciales, permisos ni SUNAT.
- **Rollback:** imágenes R3 `blackdog-{backend,frontend}:r3-a1a94a7` conservadas (`92f1275c4f9f` / `2da21e607288`); también las anteriores `rollback-42eccc8`. No fue necesario volver atrás. La recuperación de INV-OPS-01 es de código, sin borrar datos ni restaurar sobre transacciones posteriores.
- **Pendientes:** LABEL-B (bandeja, lectura/impresión y recuperación de movimientos sin lote), LABEL-C (plantillas y logotipo) e INV-OPS-02 (recuento). LABEL-D sigue BLOCKED: HARDWARE. Deudas P3 de navegación tras recarga, foco tras «Corregir» + «Atrás», texto de líneas con error y selector de empresa: [registro vigente](docs/release-inv-ops-01.md#deudas-y-siguientes-entregas).
- **Fuentes:** informes locales existentes de Codex `deployment.json` y `verification.json` (no se publican copias, volcados ni datos privados), resumidos en [el runbook](docs/release-inv-ops-01.md#0-cierre-del-despliegue). La aceptación 8/8 es declaración del propietario; este cierre es exclusivamente documental y no repite verificaciones del servidor.

> **Historial:** las entradas siguientes conservan el estado y las fechas originales de cada fase; «no desplegado», «sin GO» y «pendiente de aceptación» describen ese momento, no el estado vigente de INV-OPS-01.

## 2026-10-30 — INV-OPS-01 PREDEPLOY: READY FOR DEPLOY (código `b21c7d7`), NO desplegado

Candidato de código: `b21c7d7efbe7e26b4678eb478def56419d4ae9d5` (master tras #206). Producción sigue en R3 `a1a94a7`, esquema `0159`. Sin migraciones: son cambios de código sobre R3 (imágenes nuevas y `up -d`, sin `migrate`).

- **Incluye** desde R3: #203 (API de recepción), #204 (centro de operaciones y asistente) y #206 (la repetición idempotente revalida el acceso a la sucursal de la recepción: sin ello, un operador sin acceso podía releer una recepción repitiendo su clave; ahora responde 404 sin datos, también con registros dañados).
- **Verificado:** CI en verde sobre cada cabeza exacta; revisión independiente GO en backend, frontend y #206; E2E con backend real 11/11 (también contra #206, 2 corridas) y `inventory-first` 8/8.
- **Antes del GO de despliegue:** CI de master sobre `b21c7d7`; preflight de solo lectura; respaldo y restauración de prueba; imágenes nuevas construidas desde el código exacto y etiquetadas aparte; la vuelta atrás es de código (imágenes R3 `r3-a1a94a7`, ya etiquetadas en el servidor).
- **PENDIENTE:** pasada manual del botón «atrás» en navegador real; LABEL-B, LABEL-C, INV-OPS-02 (recuento).

## 2026-10-29 — INV-OPS-01: autorización de la repetición idempotente (rama `fix/receipt-replay-authz`) — IMPLEMENTADO EN RAMA, sin PR

- **P2:** `InventoryReceiptConfirmView` devolvía la recepción guardada al repetir la misma clave y cuerpo sin volver a comprobar que el operador alcanza hoy la sucursal de esa recepción (nombres de sucursal, proveedor y productos, cantidades y lotes de etiquetas quedaban legibles tras revocar su acceso).
- **Corrección:** al repetir, antes de cualquier otra cosa y en la misma transacción (tras el bloqueo asesor), se autoriza la sucursal GUARDADA en el registro con el mismo `_branch` / `resolve_branch_for_user` de la ruta nueva: sucursal fuera de alcance, desactivada o sin asignación → 404 `not_found` sin datos de la recepción. Se usa la sucursal almacenada, no la del cuerpo (que puede omitirse) ni la predeterminada actual. **Orden:** la autorización va ANTES de comparar la huella, de modo que un operador revocado que envía otro cuerpo con la misma clave recibe 404 y no 409 (no se revela que la clave existe). `inventory.adjust` sigue exigiéndose primero (403), otra empresa sigue en 404, la misma clave con otro cuerpo y acceso vigente sigue siendo 409. Registro histórico intacto (movimientos, existencias, unidades, auditoría y lotes idénticos antes y después).
- **Pruebas:** `ReplayAuthorizationTest` (8) en `store.test_inventory_receipts` (82); sin la corrección fallan 5 de las 7 primeras.
- **Robustez (revisión #206):** la sucursal guardada se lee de forma defensiva (`_stored_branch`): un rastro sin `record`, sin `branch_id`, nulo, no entero, booleano, fuera de rango o de una sucursal inexistente es una DENEGACIÓN explícita (404 `not_found`, sin datos, sin 500 y sin caer jamás en la sucursal predeterminada del llamante); probado con diez daños.

## 2026-10-30 — INV-OPS-01 CERRADO EN MASTER (`f4240cd`), NO DESPLEGADO

Producción sigue en R3 (`a1a94a7`, `0159`). En `master`: #203 (API de recepción, `18b8047`) y #204 (centro de operaciones y asistente, `f4240cd`). Sin migraciones.

- **IMPLEMENTADO:** `POST /api/admin/inventory/receipts/preview|confirm` (todo o nada, idempotente, auditoría sin series ni IMEI, proveedor y documento en el motivo del movimiento, costos con `costs.view`, bloqueo asesor por identificador para evitar IMEI duplicado entre `imei` e `imei2`, 409 `busy` ante interbloqueo); centro de operaciones con cinco acciones; asistente de cuatro pasos con clave de idempotencia criptográfica, panel de fallo ambiguo, aviso de salida, historial por paso, foco accesible y borrador solo en memoria ligado a usuario y empresa; E2E con backend real (11 pruebas) y capturas reales en móvil y escritorio.
- **PARCIAL:** el enlace «Etiquetas pendientes» lleva a la pantalla de etiquetas existente (LABEL-B construirá la bandeja).
- **PENDIENTE:** pasada manual en navegador real del botón «atrás» (pasos 2, 3, 4, enlace directo y pestaña nueva); simplificación del recuento (INV-OPS-02); LABEL-B (bandeja de lotes pendientes, recuperación de movimientos sin lote, estado de impresión real) y LABEL-C (plantillas por empresa y logotipo); LABEL-D (impresora, bloqueada por hardware).
- **Deuda P3:** «Registrar equipos» sin las mismas guardas de entrada que la API nueva; el selector de empresa no actúa en la pantalla de recepción; el resumen de finanzas no escala a decenas de miles de obligaciones; la espera mutua entre identificadores de distintas líneas se resuelve con reintento (409 `busy`).
- **Para desplegarlo** hace falta un GO específico: son cambios de código sin migraciones sobre R3 (imágenes nuevas, `up -d`).

## 2026-10-29 — INV-OPS-01 (frontend): correcciones de la revisión independiente (#204) — en rama

- **IMPLEMENTADO en la rama:** edición bloqueada mientras una confirmación no tiene respuesta (panel con reintento de la misma clave, historial en otra pestaña y descarte explícito); un 2xx sin forma de recepción se trata como ambiguo; un paso por entrada del historial y diálogo accesible al salir con algo que perder (más fuerte con una confirmación en vuelo o sin respuesta); el borrador en memoria queda atado a persona y empresa y se vacía al cerrar sesión o cambiar de empresa; carga de todas las páginas de proveedores (tope 40); recibir pide `inventory.adjust` **y** `products.view`; números de equipo del servidor traducidos a las filas de la pantalla; aviso de filas que no se envían; series repetidas por producto; el resultado nombra todos los artículos y sólo ofrece enlaces permitidos.
- E2E (pendiente de repetir por el agente de E2E): caso peligroso de respuesta perdida, usuario sin `products.view`, escenario 7 independiente y limpieza que falla en voz alta.

## 2026-10-29 — INV-OPS-01 (frontend): centro de operaciones y asistente de recepción — PARCIAL (rama `feat/inv-ops-01-ui`, sin fusionar)

- **IMPLEMENTADO en la rama (frontend):** `/admin/inventory/operations` (cinco tarjetas por capacidad, desactivadas con el motivo cuando falta el permiso, y «Modo avanzado» con todas las pantallas) y `/admin/inventory/receive` (asistente de cuatro pasos: Procedencia, Artículos, Revisar y confirmar, Terminado). Ambas entradas están primero en el grupo Inventario de `internal-modules.ts`; las URL anteriores no cambian.
- **PARCIAL:** el asistente habla con `POST /api/admin/inventory/receipts/preview/` y `/confirm/` (cliente en `app/admin/lib/inventory-receipts.ts`), alineado con el contrato real de la rama de la API de recibos (c78b91b) y probado sólo con simulacros (jest y Playwright con `page.route`). **No** se ha probado de punta a punta contra el backend real.
- Reglas: borrador sólo en memoria (nada de serie, IMEI o costo en `localStorage`/`sessionStorage`); `Idempotency-Key` por intento (CSPRNG), reutilizada al reintentar y nueva sólo si cambia el contenido; costos sólo con `costs.view`; «crear artículo» sólo con `products.manage` (reutiliza `ArticleRegistrationWizard`, que vuelve con `?product=`).

## 2026-10-29 — INV-OPS-01 E2E re-ejecutado sobre `ca32a3d` (revisión independiente P2/P3 + sin trampa al volver atrás) — VERDE

- 11/11 en 3 corridas seguidas y 1 desde base vacía con `next dev` frío; `inventory-first` 8/8; `storefront` + `service-pos` 84/84.
- Nuevo en el E2E: 5b (la respuesta se pierde DESPUÉS de procesarse: +10 una sola vez, sin editar, panel de confirmación sin respuesta y aviso fuerte al salir), usuario sin `products.view` (recibir desactivado con su motivo), 9b (historial por paso, «atrás» desde el paso 1 y enlaces preguntan con la lista armada, Escape = quedarse, los botones del asistente no preguntan; a 390 y 1280 px) y, al terminar, «atrás» del navegador sale del flujo de un salto y «Volver al centro de operaciones» no pregunta.
- Observación menor: tras un «Corregir» y volver con «Atrás», el foco cae en el primer campo de la línea corregida (el estado `focus` del editor no se limpia) y no en el encabezado del paso 2; nunca en `<body>`.
- Trampa del E2E: reutilizar una página cuyo historial trae entradas de escenarios previos hace que «atrás» salte a ellas; los escenarios de historial entran por la tarjeta del centro de operaciones o en una pestaña nueva.

## 2026-10-29 — INV-OPS-01 E2E re-ejecutado sobre `eed042d` (API con bloqueos por identificador + correcciones de UX) — VERDE

- 9/9 en 3 corridas seguidas y 1 desde base vacía con `next dev` frío; `inventory-first` 8/8; `storefront` + `service-pos` 84/84. El E2E ahora también exige: foco en el encabezado de cada paso y del resultado (390 y 1280 px) con el anuncio «Paso N de 4», ningún «Entrarán …» mientras una línea tiene errores, los títulos «Repetidos en esta recepción» / «Ya registrados en el sistema», y mensajes del servidor sin la serie ni el IMEI escritos.
- Resto menor (no bloquea): en una línea con error la tarjeta aún dice «Entran 0 equipos identificados».

## 2026-10-29 — INV-OPS-01 (frontend + API): E2E de punta a punta — VALIDADO EN LOCAL (rama `feat/inv-ops-01-ui`, sin PR)

- **Prueba:** `frontend/e2e/inventory-receive.spec.ts` (+ `inventory-receive-support.ts`), interfaz real y API real, sin simulacros salvo cortar la RESPUESTA de «confirm» después de que el servidor la procesó. Mismo molde que `inventory-first`: guarda de base local y desechable, marca `TAG`, limpieza por marca, escenarios seriales.
- **Escenarios (9, todos verdes; 3 corridas seguidas + 1 desde base vacía con `next dev` frío):** 1 centro de operaciones (cinco tarjetas, desactivadas con motivo para quien sólo ve, modo avanzado, 390 px); 2 seminuevo (1 equipo, 1 movimiento `purchase_entry` con documento y proveedor, +1 existencia, 1 lote de 1); 3 equipos nuevos (3 identidades distintas, repetido marcado en su fila por la pantalla y por el servidor, equipo ya en stock, IMEI de otra empresa sin filtración); 4 accesorios (+50 sobre 100 = 150, un lote de 50, ningún producto nuevo, costo oculto sin `costs.view` y 400 `costs_forbidden` por API; con permiso se guarda); 5 respuesta perdida (misma `Idempotency-Key`, respuesta `replayed`, nada duplicado; contenido distinto ⇒ clave nueva; 409 por API con otro cuerpo); 6 errores (403 llano en revisión y confirmación, 404 de producto y de enlace ajeno, sucursal fuera de alcance 404, `invalid_lines` tras una carrera sin recibir nada); 7 aislamiento (B no previsualiza ni confirma en A: 404 exactos, con control positivo, sin repetir recibos de A; B intacta); 8 conciliación (BranchStock = suma del Kardex, equipos = entradas); 9 los cuatro pasos y el resultado a 390 y 1280 px sin desborde, teclado (Tab, Enter serie→IMEI→fila nueva) y nada sensible en `localStorage`/`sessionStorage`.
- **Cómo correrla:** como `inventory-first` (AGENTS.md §6): `next dev` con la tarjeta de accesos, backend con `DEBUG=1`, `DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store`, base cuyo nombre lleve la PALABRA `e2e` (p. ej. `blackdog_ops_e2e_x`; `opse2e` no pasa la guarda), sembrada con `seed_demo_users --company-slug black-dog-store --e2e-fixtures`. `E2E_KEEP=1` conserva los datos; `OPS_SHOTS=<carpeta>` cambia dónde caen las capturas (`m390-`/`d1280-`).
- **Trampas:** el servidor guarda la serie en MAYÚSCULAS; el navegador llama a `/api/...` por el proxy de Next (un 308 de barra final precede a cada POST: esperar la respuesta que no sea 308); los usuarios por API necesitan `GET /auth/csrf/` antes de escribir; el límite de 5 inicios/min obliga a una espera de ~60 s en la corrida (se entra una vez por usuario).
- **Hallazgos (no corregidos, ver NEXT_ACTION):** tras cambiar de paso el foco no se mueve al encabezado y al terminar cae en `<body>`; el título «Ya existen en el sistema:» también encabeza los repetidos DENTRO de la misma recepción; con errores, el resumen de la revisión cuenta sólo las líneas buenas («Entrarán 0 … equipos»).

## 2026-10-29 — INV-OPS-01 (backend): recepción unificada de mercadería — IMPLEMENTADO EN RAMA `feat/inv-ops-01-api`, sin PR ni despliegue

- **Contrato:** `POST /api/admin/inventory/receipts/preview/` (sólo lectura: no escribe unidades, movimientos, códigos, lotes, auditoría ni trabajos) y `POST /api/admin/inventory/receipts/confirm/` (cabecera `Idempotency-Key`, 1–64 caracteres). Cuerpo: sucursal, proveedor opcional, referencia de documento (≤60), notas (≤300) y hasta 50 líneas, cada una `quantity` (producto no serializado, 1..100000) o `units` (producto serializado; hasta `MAX_BATCH`=200 equipos en total). Sin modelo ni migración.
- **Compone, no escribe:** `stock_unit_services.receive_units` (equipos, `purchase_entry`), `inventory_services.apply_manual_stock_movement` (cantidades, `purchase_entry`; gana un `unit_cost` opcional que sólo viaja al escritor único) y LABEL-A (`label_batches.create_for_movement`, un lote por movimiento que etiqueta). Los módulos nuevos (`receipt_services.py`, `receipt_views.py`) no crean `StockUnit`/`StockMovement`/`BranchStock`; una prueba lo fija.
- **Todo o nada:** cada línea se valida con la misma función que responde la vista previa (`clean_unit` de «Registrar equipos»; duplicados contra el stock de la empresa y dentro de la petición); una línea inválida → 400 `invalid_lines` y no se recibe nada. Las escrituras van en una transacción, en orden de producto (dos recepciones sobre los mismos estantes no se interbloquean). Un fallo de etiquetas no deshace nada: `label_error: true` por línea y global (regla LABEL-A).
- **Idempotencia:** `AdminAuditLog` `inventory_receipt_confirmed` (empresa, sucursal, proveedor, documento, clave, huella del cuerpo normalizado y un registro de ids y conteos; nunca serie, IMEI ni importes). Misma clave y cuerpo → 200 `replayed: true` sin escribir; otro cuerpo → 409 `idempotency_conflict`; bloqueo asesor por (empresa, autor, clave).
- **Proveedor y documento:** en el `reason` de cada línea del Kardex («Recepción [doc] · proveedor [nombre] · [notas]», cortado a 300) y en la auditoría.
- **Autoridad:** `inventory.adjust` y sucursal dentro de las asignadas; producto, proveedor o sucursal de otra empresa → 404; costos sólo con `costs.view` (400 `costs_forbidden` si no) y nunca en las respuestas. Cuerpos mal formados (tipos, NaN, enteros enormes, NUL, sustitutos, IMEI no ASCII, JSON anidado) → 400, nunca 500.
- **Pruebas:** `store.test_inventory_receipts` (60) + `TwoConfirmsAtOnceTest` en la lista `flushing`; `test_label_batches` fija a `receipt_services.py` entre los llamantes permitidos del servicio de lotes.
- **Revisión independiente (PR #203), correcciones:** P2 — un mismo IMEI como `imei` en un equipo e `imei2` en otro podía entrar dos veces con dos recepciones simultáneas (las restricciones únicas son por columna y la comprobación de duplicados es una lectura sin bloqueo). `receive_units` toma ahora, antes de cualquier consulta, un `pg_advisory_xact_lock` por identificador del lote (IMEI de cualquiera de las dos columnas = una sola clave por empresa; serie por producto), en orden determinista y dentro de la transacción de quien llama; la perdedora ve la fila de la ganadora y falla con el error de duplicado de siempre (también «Registrar equipos» y la carga masiva, que usan `receive_units`). P3 — (a) interbloqueo/fallo de serialización (`40P01`/`40001`) → 409 `busy` con la transacción deshecha y la clave libre; (b) ningún mensaje repite la serie ni el IMEI enviados («Equipo N» y el campo); (c) `serial`/`serial_number` o `unit_cost`/`cost` con valores distintos → 400 con el campo; (d) la respuesta (`present`) se lee DESPUÉS del commit, fuera de `atomic`, y una lectura de lotes que falla da `label_error: true`, nunca un 201 de algo deshecho; (e) el índice/rendimiento de la búsqueda por clave en `AdminAuditLog` queda sólo documentado (la consulta filtra por empresa, autor y acción; sin índice sobre `metadata`). Contrato sin cambios (sólo aditivo: el código `busy`).
- **CI #203 (`test_request_limits`):** las vistas de recepción vuelven a los parsers por defecto (no reciben archivos ni son una ruta de subida; aplica el nivel por defecto de 1 MB de `request_limits`, que cubre con holgura el peor cuerpo legal, < 600 KB, probado). El JSON anidado en exceso se atrapa en la vista (400) y un cuerpo de formulario es 400 en lugar de 415. Se corrió además `test_request_limits`, `test_production_url_surface`, `test_deploy_scripts`, `test_image_import` y 38 módulos que tocan inventario (1387 pruebas).
- **Pendiente:** CI en PR #203, frontend (otro worktree). Producción sigue en R3 `a1a94a7`.

## 2026-10-29 — RELEASE R3 DESPLEGADO (`a1a94a7`) — DEPLOYED / PENDING ACCEPTANCE

Producción sirve `a1a94a7d901f3dd9fc9ad191707d2246033fc90f` (antes R2 `42eccc8`) desde 2026-10-29 ~13:45 Lima, por orden expresa del propietario (la ventana de las 22:00 se sustituyó por ejecución inmediata). Migraciones `0156`–`0159` aplicadas con `lock_timeout=5000` (0156 6,9 s; 0157 0,16 s; 0158 1,1 s; 0159 1,9 s); `store` termina en `0159_label_batches`; `makemigrations --check` y `check` sin hallazgos; 19 restricciones nuevas, 0 índices inválidos, tablas nuevas vacías.

- **Imágenes activas:** backend `92f1275c4f9f`, frontend `2da21e607288` (el ID del frontend informado antes, `5cb271bd10b2`, ya no existe en el almacén de imágenes; la imagen se creó en una única compilación del 2026-10-29 11:45 y contiene las funciones de R3 —«Completar ficha» y «Publicar listos»—, que la de R2 no tiene). Vuelta atrás disponible: `blackdog-{backend,frontend}:rollback-42eccc8` (`5630f5977d83`, `8995ea7b418d`).
- **Respaldo previo:** `db-20261029-134309.sql.gz` y `evidence-20261029-134309.tar.gz`, `gzip -t` correcto y restauración en una base desechable correcta (1 empresa, 6 usuarios, 2 productos, 7 pedidos, `0155`), borrada después.
- **Permisos:** el único rol con `company.manage` (administrador) pasó de 50 a 53 capacidades (`finance.view/manage/payments.record`); ningún permiso explícito concedido.
- **Sin cambios:** Caddy, PostgreSQL, `Caddyfile` (md5 `9f0aab33…`), `.env.production`; `DEBUG=False`, cookies seguras, HSTS, `FISCAL_ENABLED=0`.
- **Hallazgo previo (no causado por R3):** existe una integración `sunat` activa en la consola (alta del 2026-10-27 por la consola, con certificado y usuario SOL); el código la limita al ambiente BETA y hay 0 documentos fiscales emitidos. Pendiente de decisión del propietario.
- **Humo sin ventas ni cobros reales:** externo (HTTPS, DNS por Cloudflare, certificado de Let's Encrypt hasta 2027-01-05), páginas públicas a 390 y 1280 px sin desborde, APIs protegidas 401 sin sesión, administrador 200 en inventario, publicación, vista previa, etiquetas, contabilidad y finanzas; el rol sin finanzas 403; `backfill_repair_receivables --dry-run`: 0. No se comprobó el inicio de sesión real ni la caducidad por falta de credenciales; el catálogo público está vacío (no hay productos publicados).

## 2026-10-29 — R3 PREDEPLOY: candidato `a1a94a7`, a la espera del GO del propietario (NO desplegado)

Candidato de **código**: `a1a94a7d901f3dd9fc9ad191707d2246033fc90f` (master tras #199). Producción sigue en R2 `42eccc8` (`0155`). Migraciones previstas `0156`–`0159` (aditivas). Ensayo local y procedimiento: `docs/release-r3-rehearsal.md` (scripts `strip_r3_capabilities.sql`, `regrant_r3_capabilities.py` —sólo añade—, `predeploy_checks.sql`). Revisión independiente transversal R2 → R3: GO con condiciones; sus dos P2 se cerraron en #199 (verificado, GO). Preflight de solo lectura en producción (2026-10-28): 0 productos activos a precio 0, 1 rol con `company.manage`, 0 claves POS en vuelo. Estado: IMPLEMENTADO (ensayo, revisión, correcciones); PENDIENTE (CI de master en el SHA final, preflight el día de la ventana, GO). Sin GO no se toca el servidor.

## 2026-10-29 — Revisión R3: errores 500 de INV-FAST-B y dos P3 (rama `fix/r3-review-p2`)

- **Precios e ids de la propuesta:** `catalog_proposal.parse_price` / `parse_id` validan en estricto (dígitos ASCII, hasta 2 decimales, id 1..2³¹−1; sin NaN, Infinity, exponentes, booleanos ni contenedores): un valor mal formado es 400, nunca 500, en `catalog-proposal/`, `…/apply/` (`prices`, `category_id`) y en la vista previa de equipos (`branch`, `product_id`). Un id desconocido sigue siendo 404.
- **Etiquetas:** `answer()`, `summaries()` y `summaries_for_job()` devuelven el marcador de error si la lectura posterior al commit falla; el aplicar de equipos y el alta guiada ponen `label_error` a partir de él. El movimiento queda registrado y no se reintenta.
- **Serie financiera:** su prefijo no se puede cambiar (400), igual que desactivarla.
- Sin cambios: precio cero en caja y rendimiento de totales financieros (notas P3 documentadas).
## 2026-10-28 — INV-QA-E: validación integral del flujo inventario primero (rama `test/inv-qa-e`, sin PR)

- **Qué es:** `frontend/e2e/inventory-first.spec.ts` (+ `inventory-first-support.ts`), 8 pruebas seriales con navegador y API reales, sin mocks: 12 equipos pegados → «Proponer lo que falta» (nace desmarcado) → «Crear lo aprobado» → «Revisar y registrar» con confirmación → 12 equipos, 12 IMEI distintos, ningún producto por IMEI, etiquetas listas; repetir (reenvío del aplicar y la misma lista otra vez) no suma nada; «Recibir mercadería» +50 → un movimiento `purchase_entry`, un lote de 50 copias con 100 previas, y entrada manual / corrección positiva sin lote; ficha → precio/categoría/foto → «Publicar listos» sin nada marcado → sólo lo marcado sale a `/api/products/` y a la tienda (`Últimas 3 unidades` / `Agotado`, sin costo, IMEI, serie ni proveedor); aislamiento entre empresas; conciliación BranchStock = suma del Kardex; 390 px sin desborde y con foco atrapado; almacenamiento del navegador sin datos sensibles.
- **Hallazgo corregido:** a 390 px el Centro de Publicación medía 556 px: sus tablas desplazaban en un contenedor sin `relative`, y el `sr-only` de la cabecera escapaba y ensanchaba la página. Arreglo de una palabra (`relative`) en las dos tablas de `PublicationCentre.tsx` + prueba de jest.
- **Datos:** sintéticos (IMEI Luhn inventados) con una marca única por corrida (`Qae…`, sólo letras: una palabra con dígitos parecería una serie al buscar el modelo); `afterAll` borra lo marcado y desactiva la empresa auxiliar (una empresa no se borra). `E2E_KEEP=1` conserva los datos; `E2E_VERBOSE=1` imprime la limpieza.
- **Observaciones, no cambiadas:** la tienda responde 200 con «Producto no encontrado» (no 404) para un producto sin publicar; no existe API de lectura de lotes de etiquetas (LABEL-B), así que su aislamiento se prueba por los intentos de escritura y la vista previa de etiquetas.
- **Revisión de #198 (cerrada en la rama):** el ayudante de siembra/limpieza se niega a correr sin DEBUG, con servidor no local o con una base cuyo nombre no contenga `qae`/`e2e`/`test` (o no sea la que acepta `E2E_DB_OK=<nombre exacto>`); la guarda es una función pura probada con jest. El aislamiento publica un producto oculto y oculta uno publicado desde la otra empresa y exige 403/404 exactos con el estado intacto; cada negativa tiene su control positivo y revisa que el cuerpo ajeno no traiga nombre, dirección ni ids. `signIn` falla en voz alta sin la tarjeta de desarrollo salvo `E2E_ALLOW_SKIP=1`. La conciliación suma `stock_after - stock_before` (una salida lleva cantidad positiva). Catálogo que persiste a propósito: atributos Capacidad/Color y opciones 128/256/512 GB, Negro/Azul/Rojo (documentado en el encabezado del ayudante).
- **Ensayo R3:** los `getByText` de página excluyen el `<title>` (con `next dev` 16.3.8 viaja en el cuerpo y rompía el modo estricto); la guarda de la base compara `qae`/`e2e`/`test` como palabra completa (`latest_dev` se rechaza; en SQLite, el nombre del archivo); los controles que cambian estado restauran en `finally`; el producto de la empresa auxiliar lleva un código propio (`AUX…`) para que la búsqueda no coincida por subcadena con `P000001`/`P000002`. El servidor debe correr con `DEFAULT_STOREFRONT_COMPANY_SLUG=black-dog-store`: con dos empresas activas la tienda pública queda vacía en DEBUG por diseño.

## 2026-10-28 — INV-FAST-B: correcciones de la revisión de #196 (rama `feat/inv-fast-b1`)

- **IMEI:** toda variante creada exige IMEI; sólo es opcional si la persona marca «Sin IMEI» en esa variante (`without_imei`). La fila sin IMEI ya no envía ninguna pista.
- **Precio 0.00 y caja:** `build_pos_sale` (vista previa, venta y caja móvil) rechaza una línea cuyo producto no tiene precio; la vista previa de equipos avisa «Sin precio». Las ventas ya hechas se reproducen antes de fijar precios.
- **Nombres:** modelos y opciones se comparan NFC + minúsculas + espacios colapsados; un color con identificador no se propone. El panel de propuesta empieza sin nada marcado; una fila `skip` del importador es «Necesita revisión»; si no cargan las sucursales se dice y se puede reintentar.
- Vistas previas sin aplicar y ventana propuesta → aplicar: documentadas y acotadas en `docs/adr-inv-text-03.md` (adenda de la revisión).

## 2026-10-28 — INV-FAST-B: lista de WhatsApp → catálogo → equipos registrados (rama `feat/inv-fast-b1`, sin fusionar ni desplegar)

Dos tramos sobre la vista previa de texto (#142/#154), sin migraciones y sin reglas nuevas.

- **B1 — «Proponer lo que falta» (IMPLEMENTADO en rama).** `POST /api/admin/inventory/text-preview/catalog-proposal/` (`products.view`, sólo lectura) devuelve el plan de las filas sin variante: modelos, opciones (capacidad / color) y variantes que faltan, con su nombre sugerido y lo que crearía cada aprobación. `…/catalog-proposal/apply/` (`products.manage` **y** `inventory.adjust`, como el alta guiada, porque la variante se cuenta por unidad) crea **sólo lo aprobado**, en una transacción, con un candado por empresa y el plan recalculado tras el candado: repetir la aprobación no crea nada (referencias estables por nombre, no por id). `Idempotency-Key` repite la respuesta guardada en `AdminAuditLog` (misma clave con otra solicitud: 409). Escribe con los escritores del catálogo (`create_model`, `create_option`, `set_variant`, `ensure_code`, `set_serialized`). Lo creado: oculto de la web (modelo y variante), activo, por unidad, con código de producto y **precio 0.00 si la fila no sugiere uno** (el mismo valor que escribe la carga de productos; `publication_problems` lo marca `no_price` y no se publica en masa hasta fijarlo). Un color nuevo lleva una muestra gris neutra (`#9CA3AF`) para ajustar. Nunca crea atributos ni inventa nombres de fabricante; una fila sin atributo de capacidad / color único, sin capacidad, con un color de varios atributos o con un identificador en el modelo queda **sin propuesta y con su motivo**.
- **B2 — «Revisar y registrar» (IMPLEMENTADO en rama).** `POST …/text-preview/units/preview/` (`inventory.adjust`) escribe las filas del borrador como la plantilla oficial «Equipos» en memoria y corre `unit_import_services.preview_units` (mismo lector, mismas validaciones de serie / IMEI / unicidad / sucursal, mismo `BulkImportJob`); cada fila vuelve con la `key` que traía. Registrar es el aplicar oficial `…/units/import/<id>/apply/` **sin cambios** (`receive_units` sigue siendo el único escritor; devuelve `label_batches` / `label_error` de LABEL-A). La vista previa no mueve stock, Kardex ni etiquetas. En pantalla: estado por fila (borrador / listo / necesita revisión / registrado), confirmación explícita, `NextStepBar` (Etiquetas / Publicación, con los lotes de etiquetas y el aviso de `label_error`); «copiar a Excel» sigue disponible. La serie sigue siendo obligatoria y los duplicados sin serie siguen sin sumarse, pero ahora cada fila dice por qué queda fuera.
- **Decisión de privacidad:** las series e IMEI siguen sin guardarse en el navegador; ahora viajan al servidor **sólo** al pedir «Revisar y registrar», a la ruta oficial de equipos de la empresa (ADR INV-TEXT-03 §2.2 actualizado).
- **eSIM / región / RAM:** no son opciones de variante; se avisan por fila («sin reflejo en el catálogo») y no se descartan en silencio.
- **Pruebas:** `test_catalog_proposal` (34), `test_unit_receive_draft` (14), jest `inventory-catalog-proposal` y `inventory-unit-receive`.

## 2026-10-28 — ECOM-READY-D1: completar ficha y publicar desde una pantalla (rama `feat/ecom-ready-d1`, sin PR)

Primera rebanada de «completar ficha y publicar online» en Productos › Publicación e-commerce. **PARCIAL** (rama; no está en `master` ni en producción). Sin migración.

| Pieza | Estado |
|---|---|
| `GET /api/admin/products/<id>/storefront-preview/` (`products.view`, de la empresa; otra empresa = 404): la tarjeta pública (`ProductSerializer`, la misma de `/api/products/`) también si el producto está oculto, más visibilidad, disponibilidad (stock propio de la sucursal de despacho / bajo pedido / ninguna) y pendientes. Sin costo, IMEI ni serie | IMPLEMENTADO (rama) |
| Seminuevos: «Unidades ofrecidas» de la sucursal de despacho con código de etiqueta (nunca IMEI, serie ni sus últimos dígitos) y el motivo si no se ofrece («Falta el precio de este equipo») | IMPLEMENTADO (rama) |
| Filtro `ready_to_publish` en la lista y en la acción en bloque (la regla de `publication_problems` hecha consulta, con la regla de imagen de la empresa) | IMPLEMENTADO (rama) |
| Panel «Completar ficha» (precio, categoría, descripción, foto por la galería, pendientes en vivo, tarjeta del cliente, publicar/ocultar de un id con `AdminAuditLog`), «Listos para publicar» y «Publicar listos»: diálogo con TODOS los candidatos de la página (máx. 50 por lote; nombre, código, precio, categoría), ninguno marcado por defecto, «Seleccionar todos los visibles», «Publicar N»; publica por los ids marcados (nunca por filtro + conteo) | IMPLEMENTADO (rama) |
| Fotos por unidad, edición por lotes, categoría/precio masivos, tema | PENDIENTE (D2) |

Reglas que no cambian: publicar en bloque rechaza lo que tiene problemas; ocultar siempre se puede; publicado no es disponible; `imei_hint` público intacto. Revisión independiente (#197): P2-1 (publicar listos por elección explícita) y P2-2 (pruebas de trampa de foco, retorno de foco, carreras, bloqueo de scroll y almacenamiento, con mutaciones) corregidos, más P3 (empresa en la dirección de subida/detalle, tabla estable al recargar, filtro «En e-commerce» explicado). Pruebas: 32 backend (`store.test_ecom_ready_d1`) y 44 frontend (`complete-product-panel`, `admin-company-urls`); jest completo 2200 verdes.

## 2026-10-28 — INV-UX-A1: selector de producto, «Recibir mercadería» y «Siguiente paso» (rama `feat/inv-ux-a1`, sin PR)

Primera rebanada de INVENTORY-FIRST-01: cargar mercadería con los menos pasos y sin recrear fichas. **PARCIAL** (rama; no está en `master` ni en producción).

| Pieza | Estado |
|---|---|
| Búsqueda de la lista de productos por nombre, slug, **código** (SKU interno o escaneado, `ProductBarcode` activos de la propia empresa); coincidencia exacta primero (`search_exact`), `primary_code` en cada fila; sin migración ni consultas extra | IMPLEMENTADO (rama) |
| `ProductPicker` (debounce, teclado, Enter de escáner con coincidencia exacta o única) en «Registrar entrada o salida» y «Registrar equipo»; ya no hay `<select>` de 100 productos | IMPLEMENTADO (rama) |
| «Recibir mercadería» en filas y detalle de producto: equipos por serie → Equipos (`?register=1&product=`), por cantidad → Movimientos (`?receive=1&product=`, entrada por compra) | IMPLEMENTADO (rama) |
| Última sucursal usada: sólo en memoria de la página (no en `localStorage`/`sessionStorage`) | IMPLEMENTADO (rama) |
| «Siguiente paso» (Etiquetas, Publicación filtrada con `?search=`, Ver en la tienda si está publicado, «Guardar y recibir más stock»); anuncia `label_batch`/`label_batches` si el servidor los envía (LABEL-A aún no) | IMPLEMENTADO (rama) |

Notas: `Product` no tiene campo `sku`; el SKU es el código principal de `ProductBarcode`. La cantidad mostrada en el selector es el stock total de la empresa (la lista no trae stock por sucursal). El asistente de alta conserva sus enlaces y añade la barra; sus mejoras son INV-UX-A2.

## 2026-10-28 — LABEL-A: lotes de etiquetas automáticos (rama `feat/label-auto-a`, sin fusionar ni desplegar)

Backend de LABEL-AUTO-01. Migración `0159_label_batches` (aditiva; se renumera al fusionar si FIN-F entra antes).

- **Modelos:** `LabelBatch` (empresa, sucursal, `reason` auto/manual/reprint, `source_key` único por empresa, movimiento PROTECT, estado `ready/printed/void`), `LabelBatchItem` (una unidad con 1 copia, o un producto con cantidad; `needs_review` en vez de descartar) y `LabelPrintEvent` (sólo la tabla; no guarda IMEI ni serie).
- **Servicio:** `store/label_batches.py`, llamado de forma **explícita** tras cada entrada confirmada, en un punto de guardado propio que incluye las lecturas de preparación (nunca desde `create_stock_movement`). Equipos: un ítem por `StockUnit` recién recibida. Accesorios: copias = cantidad **recibida** del movimiento (no el total de la sucursal), partida en ítems de 500 como máximo (sin otro tope: ceil(cantidad/500) filas pequeñas).
- **Qué etiqueta:** sólo `purchase_entry` e `initial_stock`. `manual_entry` **no** etiqueta (los ajustes manuales positivos suelen ser correcciones o hallazgos; decisión tras la revisión de #194).
- **Transacción:** dentro de la del evento en el alta guiada, la carga masiva y el alta de producto con stock inicial. En `inventory_views`, `v1_inventory_views` y «Registrar equipos» el gancho corre **después** del commit propio del servicio: una caída entre ambos deja un movimiento sin lote, que recupera la lista derivada «pendientes sin lote» de LABEL-B (no un reintento de la petición).
- **Fallos:** se registran (`logger.exception`) y no deshacen la recepción, pero ya no son silenciosos: el servicio devuelve `FAILED` (distinto de `None`, «este movimiento no etiqueta») y la respuesta lleva `label_error: true` con un resumen de estado `error`; el replay de un registro o de una carga ya aplicada lo repite.
- **Entradas que etiquetan:** alta guiada (`registration_services.register`, no en `dry_run`), «Registrar equipos» (`stock_unit_views`), carga masiva de equipos aplicada (un lote por trabajo y sucursal, clave `auto:label:job:<trabajo>:branch:<sucursal>`), alta de producto con stock inicial (`admin_views`) y compras manuales `purchase_entry` (`inventory_views`, `v1_inventory_views`). El ajuste de inventario de `admin_views` sólo escribe `manual_entry`/`manual_exit`, así que nunca etiqueta.
- **No etiquetan (fijado por pruebas):** `manual_entry`, conciliación de stock, traslados, recuentos, devoluciones, correcciones, ofertas de proveedor, recepción bajo pedido, cambios de estado de una unidad y `dry_run`.
- **Respuestas:** las APIs de recepción devuelven `label_batch` / `label_batches` (`id`, `status`, `items`, `labels_total`, `needs_review`) y `label_error`; el replay con `Idempotency-Key` muestra el mismo lote y no crea otro.
- **Pruebas:** `store.test_label_batches` (65), con comprobaciones de mutación. Pendiente: LABEL-B (API de lectura, pestañas, PDF), LABEL-C (plantillas y logotipo), LABEL-D (BLOCKED: HARDWARE).

## 2026-10-26 — LABEL-AUTO-01: etiquetas automáticas, logotipo editable y reimpresión (auditoría hecha; sin código aún)

Orden del propietario tras cerrar R2. Auditoría dirigida sobre `master` 5a735e6 (R2 no se toca):

| Requisito | Estado |
|---|---|
| Lote automático al ingresar stock físico | PENDIENTE (ningún servicio de entrada llama a etiquetas; no hay modelo de lote) |
| Pestañas Pendientes / Manual / Reimpresión / Diseños | PARCIAL (solo la pantalla manual y los ajustes de prefijo y símbolo) |
| Logotipo editable por empresa | PARCIAL (usa el logotipo de documentos, sin logotipo propio ni congelado por lote) |
| Etiquetas personalizadas sin movimiento | PARCIAL (vista previa y PDF son de solo lectura; faltan texto libre y registro de lo impreso) |
| Diseños 30×20, 50×25, técnica | PARCIAL (constantes en `labels/layout.py`; sin plantillas por empresa) |
| Lotes, historial de reimpresión, trazabilidad | PENDIENTE |
| Impresora Advance ADV-9010N | **BLOCKED: HARDWARE** (el repositorio no trae driver, comandos ni manual; el PDF a escala real sigue siendo la vía) |
| Seguridad (`inventory.labels.print`/`.sensitive`, IMEI solo con perfil y capacidad) | IMPLEMENTADO (base) |

Partición en PR pequeños: LABEL-A (backend: `LabelBatch`, `LabelBatchItem`, `LabelPrintEvent`, ganchos en las entradas físicas), LABEL-B (API y pestañas), LABEL-C (`LabelTemplate` y logotipo propio), LABEL-D (impresora, bloqueada). Entradas que etiquetan: alta guiada, registro de equipos, carga masiva de equipos aplicada, alta de producto con stock inicial y entradas manuales de compra o ingreso. No etiquetan: conciliaciones, traslados, recuentos, devoluciones, correcciones, ofertas de proveedor ni recepciones bajo pedido. Nada se despliega ni se imprime físicamente sin GO.

## 2026-10-26 — RELEASE R2 DEPLOYED / ACCEPTED (`42eccc8`)

Producción sirve `42eccc807b07170f81c3ab55a2fcf5c79851b823` (antes R1 `6834bc2`). Migraciones `0146`–`0155` aplicadas (`0150` no existe) con `lock_timeout`; `store` termina en `0155_pos_sale_key_reservation`. No incluye finanzas (`0156`–`0158`). Estado: **`R2 DEPLOYED / ACCEPTED`**. La aceptación funcional del propietario se confirmó el 2026-10-26, después de seis verificaciones manuales satisfactorias: inicio de sesión, navegación en caja sin vender, servicio técnico, inventario y etiquetas, contabilidad y ausencia de publicación de stock ficticio. La aceptación no constituye un ensayo con cobros reales ni una comprobación de caducidad por tiempo o sesión móvil real.

- **Respaldo previo:** `backups/db-20261026-080641.sql.gz` y `evidence-20261026-080641.tar.gz`, `gzip -t` correcto y restauración comprobada en una base desechable (ya borrada). Imágenes de vuelta atrás: `blackdog-{backend,frontend}:rollback-6834bc2`.
- **Roles tras migrar:** `inventory.counts.approve`, `accounting.view/export` y `inventory.labels.print` según lo concedido por `0148`, `0149` y `0152`; ningún rol con el permiso base sin el nuevo; `inventory.labels.sensitive` sólo para el Administrador. Códigos de equipo sin asignar (`assign_unit_codes` no se ejecutó).
- **Sin cambios:** `deploy/Caddyfile` (md5 `9f0aab33…`), `.env.production`, crontab. SUNAT sin configurar y `FISCAL_ENABLED=0`; sin integraciones nuevas.
- **Pendiente fuera de la aceptación:** `assign_unit_codes --apply` (sólo con GO separado), datos de Izipay y SMTP (preflight); pedidos TEST antiguos (2 `pending_payment`) sin dinero real. No borrar datos ni mover inventario sin decisión expresa.
- **Código posterior separado:** `master` ya integra FIN-B1 (#188, `0156`/`0157`) y FIN-E (#190, `0158`), pero producción R2 **no** ejecuta estas migraciones ni expone FIN-F/FIN-B2/FIN-D. #192 permanece pendiente de gates. Desplegar finanzas requiere release propio y GO nuevo.

## 2026-10-26 — FIN-E: cuentas por cobrar (rama local `feat/finance-receivables`, NO fusionado, NO desplegado)

Rama sobre `master` 95d2993 (FIN-B1 #188 y #186 dentro). Diseño: `docs/adr-finance-obligations.md` §6. Sólo backend; la
pantalla es FIN-B2. La venta al crédito sigue **BLOCKED** y no se crea; ningún mensaje se envía (FIN-F/G).

- **IMPLEMENTADO en la rama:**
  - migración `0158_finance_receivable_origin` (aditiva y reversible: `Obligation.repair_order` OneToOne,
    `creation_reason` y tres restricciones CHECK), sin migración de datos;
  - `store/finance/receivables.py`, comando `backfill_repair_receivables`, y la extensión de `services.py`,
    `finance_views.py` y de dos puntos de `service_services.py` (gancho al aprobar la cotización y al escribir el
    historial de la orden).
- **Reparación, derivada al leer:** al aprobarse la cotización, en la misma transacción y con el orden de bloqueo
  `RepairOrder → Obligation`, se crea `Obligation(origin_type=repair, origin_key=repair:<id>)` con cliente, sucursal
  y vencimiento. Importe, pagado, saldo, crédito y estado salen de la cotización y de `RepairPayment` (las listas
  los anotan con la misma aritmética que `service_payment_summary`; una prueba compara ambas). No hay importes
  copiados ni `ObligationPayment` en una reparación: el cobro sigue siendo `record_service_payment`.
  - No existe para orden cancelada, cotización sin aprobar ni total 0. Una orden cancelada sale de los pendientes y
    el historial la muestra como `origin_cancelled` («origen cancelado»).
  - Re-cotización: `quote_review` («en revisión de cotización») con el saldo de la última revisión aprobada.
    Sobrepago: `paid`, con `credit` aparte.
  - Vencimiento: fecha local de la empresa en que la orden llega por primera vez a «Listo para recoger» (o, si no, se
    entrega) más `REPAIR_DUE_DAYS` (0 por defecto, constante documentada; **pregunta 5 del propietario sin
    responder**). Antes: sin vencimiento y nunca vencida. Una vez fijado no se mueve.
  - Si el gancho fallara, la aprobación de la cotización no se deshace (queda registrado con traza) y el comando
    lo reconstruye.
- **Cuentas por cobrar manuales:** `finance.receivables.manual` (ya en el catálogo y de asignación explícita desde
  FIN-B1), motivo obligatorio (columna `creation_reason`), cliente de la empresa, sin vínculo a reparación ni venta
  (el servidor rechaza `repair_order`, `order`, `sale`, `origin_*`…), y se rechaza (409) la que nombre la referencia de
  una reparación abierta del mismo cliente. Ajustes, castigo, anulación con motivo (no con pagos confirmados) y
  disputa usan el motor de FIN-B1.
- **Comando** `backfill_repair_receivables [--company <slug>] [--dry-run]`: idempotente, por empresa, rellena también el
  vencimiento desde el historial.
- Lista, detalle y totales de `/api/admin/finance/…` incluyen ya las reparaciones (cliente, reparación, crédito);
  los totales siguen por moneda; empresa y sucursal se filtran en cada consulta.
- **Pruebas:** `test_finance_receivables.py`, `_manual.py`, `_backfill.py` (la clase `LocksTest`, con hilos, va en
  `flushing`). Una prueba de FIN-B1 que fabricaba una fila `repair` falsa (404) pasó a comprobar que la base de
  datos la rechaza.

## 2026-10-25 — FIN-B1: pagos fijos y obligaciones, núcleo de backend (rama local, NO fusionado, NO desplegado)

Rama `feat/finance-core`. Diseño: `docs/adr-finance-obligations.md` (#185), con los valores por defecto provisionales de
§2. Sólo backend; la pantalla es FIN-B2.

- **IMPLEMENTADO en la rama:**
  - modelos `FinanceCategory`, `RecurringObligationRule`, `RulePause`, `Obligation` (con «en disputa»),
    `ObligationPayment` y `ObligationAdjustment`;
  - migraciones `0156_finance_core` y `0157_finance_capabilities`, con numeración provisional que se renumera al
    fusionar.
- **Calendario calculado** desde el ancla y el índice: el día 31 cae el 28 o 29 en febrero y vuelve al 31; también
  trimestral, anual y semanal. Un período existe mientras venza en `end_date` o antes. Por defecto, una regla genera
  desde el primer período que vence hoy o después; los anteriores sólo si se piden, y queda auditado.
- **Generación idempotente:** la regla se bloquea y la clave (regla, índice) es única; dos procesos dan un solo
  período. Se generan los que vencen en los próximos 35 días; el comando `generate_finance_obligations` corre cada
  hora en `deploy/crontab.example`.
- **Pausa por intervalos, sin relleno al reanudar. Pausar no anula nada.** Un período generado, sin pagos ni ajustes, que
  vence dentro de la pausa queda «En pausa» por estado derivado (filtro `state=paused`, fuera de los totales, con su
  historial). Lo vencido antes de la pausa, o con pagos o ajustes, sigue debiéndose. Las fechas son las de la empresa. Si se reanuda antes, o si se incluye a mano con «Generar este
  período», vuelve a deberse. Al **terminar** la regla se anulan como «nunca llegó a deberse», con auditoría, sólo los
  períodos que todavía no vencen, es decir, los que vencen después del fin y de hoy. Lo ya vencido sólo se anula con
  `finance.void`.
- Una referencia como el CCI (20 dígitos) es válida; sólo se rechaza un número de tarjeta (13–19 dígitos que pasan
  Luhn), con 400. Tampoco se admiten importes con más de dos decimales: se rechazan y no se redondean.
- **Saldo derivado y libro sólo de altas:**
  - pagos parciales, sin sobrepago;
  - reversión con motivo que reabre;
  - ajustes con motivo y el importe variable de un solo período;
  - anulación sólo sin pagos confirmados;
  - disputa con motivo.

  Todo bloquea primero la obligación, y la clave de idempotencia incluye la obligación.
- **Fechas y totales:** estados derivados en la fecha local de la empresa; totales por moneda, sin sumar PEN y USD.
- **Capacidades `finance.*` (12):** la migración concede `view`, `manage` y `payments.record` a los roles con
  `company.manage`. Las otras nueve son de asignación explícita y el preset Administrador de las empresas nuevas no
  las incluye.
- **API:** `/api/admin/finance/…` (categorías, reglas, obligaciones, pagos, ajustes, anulación, disputa y resumen).
  Otra empresa ⇒ 404; otra sucursal ⇒ 404; una obligación general exige alcance de toda la empresa.

## 2026-10-25 — FIN-H1: integridad del cobro de reparaciones (PR en borrador, NO fusionado, NO desplegado)

Rama `fix/repair-reversal-lock`. Hallazgos de la auditoría de FINANCE-OBLIGATIONS-01 (`docs/adr-finance-obligations.md`, #185). Sin migraciones.
- **H-1:** `reverse_service_payment` bloquea ahora la `RepairOrder` antes del pago (mismo orden que la entrega y el registro). Antes, una reversión podía sellarse en medio de una entrega que ya había leído «pagado»: con «exigir pago antes de entregar» activado, el equipo salía con saldo.
- **Pagos perdidos en el panel:** la clave de idempotencia se guardaba por «orden:importe:medio:referencia» toda la sesión. Dos pagos reales idénticos (dos S/ 100 en efectivo sin referencia) usaban la misma clave, el servidor respondía el segundo como repetición del primero y **el segundo nunca se registraba**. Lo mismo pasaba con el consumo de repuestos. Ahora hay una clave por intento (`createAttemptKeys`, aleatoria por `crypto`): se conserva en los reintentos del mismo intento y se descarta tras el éxito.
- **Alcance de la clave:** la huella del pago y la de la entrega incluyen la orden. Una clave usada en otra reparación de la misma empresa es un conflicto (409), no la repetición de lo de otra orden. Las filas anteriores se siguen aceptando como repetición en su propia orden.
- H-2: corregido el comentario que decía que se puede cobrar un depósito antes de cotizar (el código lo rechaza).

## 2026-10-25 — AUTH-C: aviso de sesión e inactividad en el navegador (PR en borrador sobre AUTH-B, NO fusionado)

Rama `feat/auth-session-frontend`, apilada sobre `feat/auth-session-idle` (#181): se fusionan una tras otra, nunca
AUTH-B sola. Diseño: `docs/adr-auth-sessions.md` §4 y §2.10.

- **IMPLEMENTADO en la rama:** `X-Session-Activity` sólo en escrituras o lecturas pedidas por una persona y sólo
  segundos después de una pulsación real (`event.isTrusted` / `navigator.userActivation`): ningún temporizador,
  sondeo, foco, efecto al montar ni sincronización de la URL mantiene viva una sesión. `session_expired` o
  `X-Session-Expired` terminan la sesión en todas las pestañas y nada la renueva hasta un nuevo inicio de sesión.
- Aviso a los 25 min (la última sexta parte) con cuenta atrás del servidor, «Continuar sesión» (keepalive) y «Cerrar
  sesión»; a cero se oculta la pantalla, se pregunta al servidor y lo interno va al login con el motivo (inactividad o
  tope de 8 h). `/admin` va `no-store` y la bfcache se oculta hasta que el servidor responde.
- Checkout: antes de crear el pedido pregunta si la sesión sigue; si no, se detiene y pregunta (nunca reenvía solo).
  Caja: guarda sólo `{clave, huella de la cesta, hora}` de la venta en curso y, al volver a entrar, consulta por clave
  (`GET /api/admin/pos/sales/by-key/`, sólo lectura) y no deja cobrar hasta que la persona confirma.
- Validaciones del propietario (2026-10-25): una respuesta anterior a un nuevo inicio de sesión no puede cerrar la
  sesión nueva (época de petición; aviso «started» entre pestañas, también desde el login); clave de caja sólo de
  `crypto` (sin ella no se cobra); la venta en curso se resuelve con la consulta o con la decisión «no se cobró» que
  registra el servidor (`POST /api/admin/pos/sales/by-key/decision/`, 409 si la venta existe); el checkout iniciado
  con sesión envía `signed_in` y el servidor lo rechaza sin sesión en vez de crear un pedido de invitado; el
  almacenamiento del navegador pasa por un único módulo con sus claves listadas (ESLint + prueba de fuente).
- Revisión de #184: «no se cobró» **reserva la clave** (una venta posterior con ella responde 409 `key_reserved`;
  la reserva vive en `PosSaleKeyReservation`, migración `0155`, única por empresa y clave; venta y decisión comparten un bloqueo consultivo de PostgreSQL y gana una sola; la auditoría se sigue escribiendo); la generación del
  inicio de sesión se comparte entre pestañas y un «terminó» más viejo se ignora; el vigilante sólo da por terminada
  la sesión con `session_expired`.
- Playwright contra el backend real (inactividad de 60 s), Chromium y WebKit: aviso, continuar, caducidad, dos
  pestañas, respuesta tardía de la sesión anterior y venta de caja sin duplicar.

## 2026-10-25 — AUTH-B: caducidad de la sesión web por inactividad (PR en borrador, NO fusionado, NO desplegado)

Rama `feat/auth-session-idle`. Diseño: `docs/adr-auth-sessions.md` (PR #180), con los valores por defecto
provisionales de §12.1 y los cambios de la revisión independiente (§2.10). Sólo backend.

- **IMPLEMENTADO en la rama (PENDIENTE de fusionar):** `AuthSession` (migración `0154_auth_session`, tras la `0153`
  fiscal de #179), una fila por navegador con sesión; su `sid` va en el refresh y en el
  access y sobrevive a la rotación. `CookieJWTAuthentication` la consulta en cada petición (sustituye a la consulta
  del usuario). 30 min sin actividad real ⇒ sesión terminada; 8 h para personal (unión de membresías, master,
  puente heredado y roles heredados), 7 días para clientes, sin deslizar; endurecimiento inmediato al ganar una
  membresía o un rol de personal.
- Actividad = `X-Session-Activity: 1` con CSRF válido (también en GET), registrada después de la respuesta y sólo
  si fue 2xx/3xx, como mucho cada 30 s; la renovación, el logout y `GET /api/auth/session/` nunca cuentan.
  `POST /api/auth/session/keepalive/` es «Continuar sesión».
- Sesión muerta ⇒ lecturas como invitado y cookies borradas; escrituras 401 `session_expired` (el checkout incluido),
  salvo login, registro, carrito, cupón, cotización y la propia renovación. Un access caducado de una sesión viva
  sigue en 401 para renovar (salvo en las entradas: login, CSRF y renovación, donde es invitado). Toda respuesta a
  una sesión muerta lleva `X-Session-Expired: 1`, la única señal legible (las cookies son HttpOnly).
- Renovación: bloqueo de la fila y lista negra preguntada otra vez bajo el bloqueo (dos renovaciones con el mismo
  refresh ya no ganan las dos). Logout, cambio y restablecimiento de contraseña y cuenta desactivada terminan la
  sesión. `sid` separa los canales: un token web no sirve en V1 ni un token V1 en la cookie.
- **Despliegue: BLOCKED (GO del propietario).** Al desplegar, todas las sesiones web abiertas caen una vez (P4 del
  ADR). AUTH-B no se despliega sin AUTH-C: el frontend actual no envía la señal de actividad y toda sesión caería a
  los 30 min del inicio aunque se trabaje.
- Fuera de esta fase (AUTH-D): la regla de autoridad del correo de Google (H-5) y el uso único del `nonce` de Google
  en la base (H-3).

## 2026-10-23 — RELEASE R1 DESPLEGADO (`6834bc2`) — R1 DEPLOYED / VERIFIED (smoke) · PENDING OWNER ACCEPTANCE

Autorización del propietario para el SHA exacto `6834bc28a78fb496a8356e24d6a99b1dbb637c84`; producción de
partida `6daf9ae`. Ejecutado el 2026-10-23 (~14:40–14:50 UTC):
1. Preflight: servidor `ip-172-26-14-2`, `6daf9ae`, 4 contenedores sanos, `ops_status` todo OK (sin pagos,
   pedidos ni apartados pendientes; Izipay TEST; SUNAT sin configurar), 59 GB libres.
2. Copia `db-20261023-094219` + evidencias; `Caddyfile` local (md5 `9f0aab33…`), `crontab` y md5 del env
   guardados en `backups/*.before-r1-20261023-094219`. Imágenes `rollback-6daf9ae` etiquetadas.
3. Checkout `6834bc2` (el `Caddyfile` local se conserva: no cambia en el código), imágenes construidas con
   la tienda sirviendo.
4. Ensayo con la imagen nueva sobre esa copia (PostgreSQL desechable, red interna): `0142`/`0143` con la
   imagen anterior funcionando encima, `0144`/`0145`, vuelta a `0143`, ida; esquema idéntico (diferencia
   sólo en las claves `\restrict` de `pg_dump`); conteos iguales. Primer intento abortado sin efectos (al
   `docker run` le faltaba el entorno de compose; se resolvió con el entorno de `compose config`).
5. Producción: `migrate store 0143` con `6daf9ae` sirviendo → `up -d` backend y frontend nuevos (sanos) →
   `migrate` (`0144`, `0145`). `makemigrations --check` y `check` limpios.
6. Verificación: `healthcheck.sh` y `ops_status` todo OK; `INVENTORY_AI_ENABLED=False`, `FISCAL_ENABLED=False`,
   `_PROVIDERS = {}`; conteos iguales a la copia (2 productos, 2 modelos, 7 pedidos, 0 ofertas, 1 equipo);
   tienda, `/product`, `/modelo/iphone-17-pro`, API pública y pantallas del panel en 200; API de
   administración sin sesión 401; el build servido contiene «Gestionar variantes», «Continuar al importador
   oficial» y «Generar borrador»; sin errores en los registros; `Caddyfile`, `crontab` y env sin cambios.
No se hizo ninguna escritura sobre datos comerciales. Aceptación funcional: `docs/acceptance-r1.md`
(PENDING OWNER ACCEPTANCE; antes de las variantes, crear atributos y opciones: sección B0).

## 2026-10-23 — Checkpoint de integración (master `fa9db72`, NO desplegado; producción sigue en `6daf9ae`)

| PR | Fase | Estado | SHA de integración |
|---|---|---|---|
| #153 | CATALOG-EDITOR-FIX-01 | MERGED · TESTED (CI 8/8, Playwright 4/4 Chromium y 4/4 WebKit, revisión GO) | `29fd780` |
| #148 | INV-AI-01 | MERGED · TESTED (CI 8/8, auditoría GO; `_PROVIDERS = {}`, IA apagada) | `b593148` |
| #154 | INV-TEXT-03 | MERGED · TESTED (CI 8/8, revisión GO sin P0/P1/P2) | `fa9db72` |
| #156 | INV-TEXT-04 | MERGED · TESTED (CI 8/8, revisión GO; P3 corregidos) | `94d1f3f` |
| #155 | TEST-FIXTURE-SEQ-01 | MERGED · TESTED (CI 7/7 backend, revisión GO) | `1c3db64` |
| #158 · #159 · #160 | Guía de aceptación R1 · cierre de deuda de pruebas · ensayo local de migraciones R1 | MERGED (docs) | `ad4ace9` · `20a8618` · `b3da8f7` |

**INV-TEXT-03 (#154):** de la lista de WhatsApp revisada a un BORRADOR. Ofertas: ensayo en seco del importador
oficial (`supplier_offer_import.preview` sin cambios, dentro de una transacción que siempre se revierte; sin
filas, trabajos, auditorías ni efectos externos; deja huecos en las secuencias de ids). Stock propio: TSV en el
navegador; IMEI y series no salen de él. `products.manage` como el preview oficial; lista completa sólo con
alcance de toda la empresa (403); otra empresa, 404. Sin botón de aplicar. ADR `docs/adr-inv-text-03.md`.
**Deuda de pruebas (preexistente):** `C15InitialRaceTest` y `M12BConcurrencyTest` fallan solas en una base recién
migrada (`reset_sequences=True` frente a la empresa pk 1 que crean las migraciones 0019/0022); en CI pasan por
el orden. Corregido en #155 sin desactivar pruebas: las cinco clases afectadas pasan solas y en la suite.
**INV-TEXT-04 (#156):** «Continuar al importador oficial» abre el importador oficial de ofertas con el libro
ensayado ya cargado y la elección de lista completa; sólo su «Aplicar», con sus confirmaciones, escribe. En
prueba local con backend real: 0 ofertas tras la vista previa, 1 tras aplicar.
**Ensayo local de migraciones R1 (#160):** código `6daf9ae` sobre `0142`/`0143` sigue funcionando; `0144`
corrige lo que escribe entretanto; vuelta atrás a `0143` y de nuevo adelante; esquema final idéntico al de
una base nueva (`docs/release-r1-rehearsal.md`).
**Seminuevos externos (ADR #150, Decisión 2, D3/D4):** PENDIENTE; plan en `feat/inv-used-ext-01` (migración
`0146`), fuera del lanzamiento R1. BLOCKED por decisión del propietario: sustitución legítima de un equipo
distinto (B1) y tratamiento fiscal de IMEI de equipos externos (B2).

## 2026-10-23 — INV-AI-01 autorizado para integrar (#148, registro de proveedores vacío, NO desplegado)

El propietario autorizó fusionar el adaptador con `_PROVIDERS = {}`, `INVENTORY_AI_ENABLED=false`, sin
claves ni llamadas externas, tras auditoría independiente GO en `e4d36ac` (sin P0/P1): prueba de punta a
punta reconocedor #142 → petición estructurada con datos ficticios (nada sensible llega al proveedor
simulado), `item_from_preview` resiste 21 candidatos hostiles, ninguna ruta de producción llama a
`propose`. Aprobó la excepción de AGENTS.md §2 sólo para vocabularios técnicos cerrados de varios
fabricantes (ADR §6). Migración `0145_inventory_ai_usage` tras `0144`. **INV-AI-02 (proveedor real):
NO-GO** hasta probar la ruta HTTP con `clean()` del servidor, nueva auditoría y autorización específica.

## 2026-10-22 — INV-AI-01 (rama `feat/inv-ai-01`, #148, borrador, NO-GO del propietario hasta la auditoría, NO desplegado)

Migración `0145_inventory_ai_usage` (aditiva; renumerada desde `0143` tras integrarse #151). Puerta del adaptador de IA: redacción de identificadores,
respuesta de forma fija, cuotas, presupuesto mensual y cortacircuitos; sin proveedor real ni clave, no se
envía nada. DEC-INV-AI-01. Aún no hay pantalla que lo use: la vista previa de texto (#142) es local.

Arquitectura decidida por el propietario (2026-10-22): la IA no recibe texto libre; sólo una representación
estructurada, mínima y validada por el reconocedor local (ADR `docs/adr-inv-ai-structured.md`). Contrato
implementado: `inventory_ai_payload.build_request`/`validate_answer` e `inventory_ai.propose`, que sólo acepta
un `StructuredRequest`; la ruta de texto libre se eliminó. NO-GO del propietario para cualquier proveedor
externo; sin integrar hasta revisión independiente GO. **Numeración:** #151 entró antes (`b25ef1d`); esta migración pasó a
`0145_inventory_ai_usage`, después de `0144_supplier_offer_confirmed_has_quantity`.

## 2026-10-22 — CATALOG-EDITOR-FIX-01 (rama `fix/catalog-model-variant-editor-ux`, NO desplegado)

El propietario no podía editar modelos ni variantes en producción (`6daf9ae`, #137 desplegado).
**Causa raíz confirmada:** en los registros de producción (sólo lectura), el propietario abrió el listado
de modelos cuatro veces el 22/10 (18:38–18:48) y **nunca** pidió el detalle de un modelo ni guardó nada.
El listado no tenía acciones visibles y el nombre (único enlace al editor) parecía texto. Además, la
empresa no tiene atributos: el editor de variante no ofrecía capacidad ni color y no decía por qué.
Datos productivos leídos sin modificar: «iPhone 17 Pro» con una variante seminuevo sin opciones; «Nuevos»
es un modelo vacío.
Cambios: columna «Acciones» (Ver detalles · Editar modelo · Gestionar variantes); «Editar modelo» con
«Guardar cambios»/«Cancelar», precargado, y confirmación al cambiar la dirección de un modelo publicado;
variantes con columna «Tienda» y aviso con enlace a «Atributos y opciones» si no hay atributos; la
variante se guarda en UNA operación (modelo, línea, opciones, precio y publicación juntos o nada),
DEC-CATALOG-EDITOR-FIX-01. Sin migraciones. Aceptación funcional de #137 sigue pendiente del propietario.

## 2026-10-22 — INV-TEXT-02 (rama `feat/inv-safe-text-preview-01`, #142, borrador, NO desplegado)

Sin migraciones. El reconocedor local de #142 lee listas reales por etapas
(`inventory-text-stages.ts`): limpieza conservadora de WhatsApp, segmentación, equipo o accesorio,
atributos por significado (capacidad con o sin GB, RAM, SIM, región LL/A·ZA/A, vatios, longitud,
generación, color), costo = un único importe o «Costo:» explícito (dos o más: vacío y aviso), duplicados
por variante completa, y cada línea no reconocida con su motivo. Revisión editable: línea, modelo,
capacidad, color y costo (con `costs.view`) corregibles, texto original visible con IMEI enmascarado, y
conciliación con las variantes reales por `POST /api/admin/inventory/text-preview/match/` (sólo lectura,
`products.view`; viajan sólo modelo, capacidad, color, tipo y línea; costos e identificadores = 400). Sigue
siendo vista previa: sin escrituras, cantidad desconocida, nada se aplica.

## 2026-10-21 — INV-TEXT-01 (rama feat/inv-safe-text-preview-01; NO MERGEADO, NO DESPLEGADO)

- Vista previa local de texto de WhatsApp montada en Gestión masiva de inventario; solo en navegador, sin red, sin IA API, sin stock/precios/comprobantes escritos.
- Clasificación declarada nuevo/seminuevo/accesorio, origen propio/proveedor, extracción orientativa de características, comprobación Luhn, detección conservadora de repetidos y cantidad siempre NO CONFIRMADA.
- La propuesta conserva modo minucioso existente; no incorpora importación de lotes, IA remota, exportación adicional ni autocompletado IMEI en formularios: esos puntos están PROPUESTOS en docs/inventory-ai-safe-rollout.md.
- Archivos: frontend/app/admin/lib/inventory-text-preview.ts; frontend/app/admin/components/InventoryTextPreview.tsx; frontend/app/admin/components/BulkInventoryCentre.tsx; frontend/__tests__/inventory-text-preview.test.ts; docs/inventory-ai-safe-rollout.md.
- Migraciones: ninguna. Tests añadidos (ejecutados después en INV-TEXT-02: Jest, tsc, lint y CI en verde); baseline real de repo no ejecutable en este entorno (sin clone). GitHub no devolvió checks del commit base. PR borrador, revisión y CI obligatorios antes de merge.

## 2026-10-22 — INV-SNAPSHOT-01 integrado (#151, en master `b25ef1d`, NO desplegado)

Integrado tras CI 8/8 en `05e28e0` y revisión independiente GO (dos rondas: corte por lista truncada
recalculado bajo bloqueo, edición manual sin confirmación silenciosa, restricción separada en `0144`,
confirmación explícita y su reinicio con cantidad en blanco probados). Ensayo de migraciones: adelante,
atrás a `0142` y adelante en dos pasos. Migraciones en master, sin aplicar en producción: `0143_supplier_offer_availability_confirmed` (campo y
relleno) y `0144_supplier_offer_confirmed_has_quantity` (restricción). Despliegue en dos pasos según
`docs/NEXT_ACTION.md`; producción (`6daf9ae`)
NO-GO hasta el GO del propietario para esa versión. #148 renumeró su migración a `0145_inventory_ai_usage`.

## 2026-10-22 — INV-SNAPSHOT-01 (rama `feat/inv-snapshot-01`, NO desplegado)

Migraciones `0143_supplier_offer_availability_confirmed` (campo y relleno) y `0144_supplier_offer_confirmed_has_quantity`
(restricción, se aplica después de reiniciar con el código nuevo). Lista completa de un
proveedor en la carga semanal, con confirmación del número de ofertas ausentes a desactivar y de que la lista
está completa; las programadas no cambian; las ausentes con ventas siguen sus compromisos. Disponibilidad no
confirmada como estado explícito. DEC-INV-SNAPSHOT-01. **Numeración:** #148 propone `0143_inventory_ai_usage`
(sin aplicar en ninguna parte); si esta entra antes, #148 pasa a `0145`.

## 2026-10-22 — ADR listas completas y seminuevos externos (#150, documentación, NO implementado)

`docs/adr-inv-snapshot-external-used.md` con las decisiones del propietario D1–D4: las ofertas programadas no
se desactivan por ausencia; las vigentes ausentes dejan de venderse sin tocar compromisos, pagos ni
reembolsos; un seminuevo externo sólo publica datos comerciales; una discrepancia de IMEI/serie al recibir
bloquea y se revisa, nunca se sustituye en silencio. Disponibilidad no confirmada como estado explícito
(`availability_confirmed`). Implementación en una rama aparte; venta bajo pedido de seminuevos externos y
fiscal, sin cambios.

**Deuda fiscal registrada (aparte de #149, sin tocar inventario):** un intento queda abierto si el proveedor
falla fuera del transporte (reenviable a los 10 minutos sin consultar el CDR); un timeout incierto se reenvía
sin reconciliar antes; una baja en curso no bloquea el envío individual. Facturación sigue deshabilitada en
producción.

## 2026-10-22 — FISCAL-DOUBLE-SUBMIT (#149, en master `97c2dd0`, NO desplegado)

Sin migraciones. Un comprobante no se vuelve a transmitir a SUNAT si un segundo envío llega justo después de
la respuesta del primero (DEC-FISCAL-DOUBLE-SUBMIT-01). Era la causa de la falla intermitente de
`C22A1ConcurrencyTest` en CI de #142; nueva prueba determinista `test_fiscal_double_submit`. Quedan
pendientes aparte (DEC-FISCAL-DOUBLE-SUBMIT-01): excepción del proveedor fuera del transporte, reenvío tras
un timeout incierto sin reconciliar, y una baja en curso que no bloquea el envío.

## 2026-10-22 — INV-EXP-02 (#146, en master `fb7fcee`, NO desplegado)

Sin migraciones. «Reporte integral»: un Excel de consulta con «Stock propio», «Equipos» y «Disponibilidad de
proveedores» en hojas separadas; nada se suma entre hojas; cantidad no informada = «Desconocida»; «Se puede
vender» con la regla de la tienda (`valid_offer_q`); costo pactado sólo con `costs.view`; las ofertas son de
toda la empresa, el filtro de sucursal acota stock y equipos.

## 2026-10-22 — INV-ID-01 (#147, en master `c1c0ad9`, NO desplegado)

Sólo frontend. Con exactamente 14 dígitos de IMEI se ofrece el decimoquinto (Luhn) mostrando el número
completo, y se completa sólo si la persona lo pide; con 15 sólo se valida, nunca se cambia el último. En la
recepción de proveedor y el registro de seminuevos. El backend (`device_identity`) sigue siendo la
autoridad; Luhn no prueba que el IMEI sea de ese equipo.

## 2026-10-22 — INV-EXP-01 (#145, en master `5474c52`, NO desplegado)

Sin migraciones. Exportaciones de inventario con filtro «Línea»: todo, solo nuevos, solo seminuevos,
nuevos y seminuevos, solo accesorios (`line=new,used,accessory`). Un equipo pertenece a un modelo o va por
serie; lo demás de la línea nueva es accesorio. Sólo elige filas: los archivos para volver a cargar
conservan sus columnas.

## 2026-10-22 — INV-SWITCH-01 (#144, en master `b54ca45`, NO desplegado)

Migración `0142_inventory_ai_settings` (aditiva). Interruptor de IA: global `INVENTORY_AI_ENABLED=false`,
por empresa en Configuración › «Inventario · Inteligencia artificial», y por importación («Usar IA en esta
importación» en la lista semanal). Sin proveedor de IA: nunca se envía nada; la vista previa dice por qué
no se usó (p. ej. «IA no configurada»). Inventario con «Modo rápido» (tareas del día, enlaces a las
pantallas existentes) y «Modo avanzado» (el tablero de siempre). DEC-INV-SWITCH-01.

## 2026-10-22 — CANCEL-CROSSED-PAYMENT (#143, en master `0d484a0`, NO desplegado)

Sin migraciones. Cancelar un pedido envía el modo que se vio; si llegó un pago entre medio, 409
`order_changed` y nada escrito (DEC-CANCEL-CROSSED-PAYMENT-01). Corrige la prueba de carrera que fallaba de
forma intermitente en CI (#142 shard 6/6, `'paid' != 'duplicate'`): era un hueco real, no del PR #142.

## 2026-10-22 — Desplegado #137 + #138 + #139 + #140 (`acf20fc` → `6daf9ae`, 0140–0141)

Autorizado por el propietario. Migración 2026-10-22 03:59 UTC (22:59 Lima del 21) con
`lock_timeout=5000`/`statement_timeout=300000`; servicios arriba a las 04:00 UTC.
- Preflight: identidad (sa-east-1, clave de host conocida), 17 OK, Caddyfile md5 `9f0aab332b6b`
  intacto, 63 GB libres, 0 líneas al pase, 0 ofertas, 2 pagos `pending` de ~10 días (pedidos sin
  pagar, no se tocaron) y 1 reembolso pendiente (no se tocó).
- Copia `db-20261021-224936` (`gzip -t` OK) restaurada en un PostgreSQL desechable: recuentos
  iguales a producción; 0140/0141 ida, vuelta a 0139 (esquema idéntico), ida (idéntico); huella de
  montos y cantidades igual en todos los pasos; imagen `rollback-acf20fc` funciona sobre el esquema
  nuevo.
- Imágenes de vuelta atrás `blackdog-{backend,frontend}:rollback-acf20fc` = las que corrían.
  Respaldos `backups/Caddyfile.before-6daf9ae` y `backups/crontab.before-6daf9ae`.
- El primer build del frontend falló al descargar Google Fonts; se repitió sin cambios y pasó.
- Después: 17 OK, recuentos iguales, crontab igual, sin errores; smoke de sólo lectura (catálogo
  con reglas de edición, proveedores, plantilla, alertas, pedidos; otra empresa 403, anónimo 401,
  liquidación sólo con costos; sin escrituras). El editor de variantes está en el frontend servido.
- Pendiente: aceptación del propietario (editor #137 con guardado y persistencia, lista semanal,
  agotado/bajo pedido en tienda, compra al pase con Izipay TEST, recepción con IMEI, margen).
  Vuelta atrás sólo con las imágenes y sólo sin datos nuevos de proveedores o costos.

## 2026-10-21 — D3: una falla del proveedor no libera disponibilidad (#140, desplegado el 2026-10-22)

Sin migraciones. Decisión del propietario D3 (en DEC-SUPPLIER-WEEKLY-01). La causa se lee del
pedido: marcar «No se pudo abastecer» en un pedido pagado es una falla del proveedor y resuelve el
compromiso sin aumentar lo disponible (comprometido y confirmado bajan lo mismo); cancelar el
pedido (o marcar la falla de uno ya cancelado o reembolsado) es una liberación ordinaria y devuelve
lo disponible una sola vez. Nunca negativo ni dos veces. 9 pruebas nuevas (`test_supply_failure`,
con carrera falla/pago).

## 2026-10-21 — D1/D2: cantidad neta semanal y margen realizado con costo real (#139 en `master` `1b3e093`, NO desplegado)

Desde `master` `a9dfd00`. Migración `0141_orderitem_received_cost` (aditiva, columnas nulas).
- **D1:** la cantidad de la lista semanal es la disponibilidad neta del proveedor a la fecha de
  corte: al actualizar, confirmado = comprometido actual + cantidad del archivo (disponible =
  archivo); nueva oferta = archivo; 0 = agotada. Mismo archivo otra vez: sin cambios.
- **D2:** margen estimado con el costo acordado (instantánea de la venta, nunca reescrita);
  margen realizado con el costo real registrado al recibir (`OrderItem.received_unit_cost`). Sin
  costo real (no recibido, recibido sin `costs.view` o antes de este cambio) = «costo real
  pendiente», aparte y nunca como realizado; `costs.view` lo registra después
  (`POST …/supply/<línea>/real-cost/`), sin tocar Kardex ni costo promedio.

## 2026-10-21 — SUPPLIER-DEVICE-FLOW-02 (#138 en `master` `a9dfd00`, NO desplegado)

Desde `master` `f3f6c29` (con #137). Migración `0140_supplier_product_ref` (aditiva). Decisiones:
DEC-SUPPLIER-WEEKLY-01 y la sección de recepción en 05.
- **Ya IMPLEMENTADO antes (se reutilizó):** asistente con cuatro tipos (nuevo propio, al pase,
  seminuevo, accesorio); seminuevos por unidad; ofertas sin IMEI ni stock; venta bajo pedido con
  Izipay, revalidación y `to_confirm`; recepción `receive_line`; consulta de disponibilidad.
- **Implementado ahora:** lista semanal **parcial** (sólo cambian las filas del archivo; las
  ausentes quedan intactas; cantidad 0 = agotada), diferencias antes/después, decisión explícita y
  auditada del precio comercial, vista previa obsoleta rechazada, códigos de proveedor reutilizables,
  una oferta por proveedor y producto sin ventanas solapadas (la lista de la semana siguiente nunca
  detiene la vigente). Recepción rápida: datos precargados, escáner (Enter al siguiente),
  observación, costo real junto al acordado (sólo `costs.view`), repetición = 409, rechazada con
  devolución abierta. Alertas operativas por empresa en el tablero. «Agotado» visible y no
  comprable en la tienda. «Vender en caja» desde disponibilidad. IMEI 2 en seminuevos.
- **Fiscal:** la boleta/factura no espera a la recepción (contrato actual: pedido pagado).
- Decisiones D1/D2 del propietario aplicadas en la corrección siguiente.
- Pruebas: suite completa 7945 OK (antes de las correcciones de la revisión); Jest 1727;
  `next build`; Playwright 101/101. Revisión independiente: NO-GO (oferta solapada desde el
  asistente; la lista de la semana siguiente detenía la vigente; solape al aplicar), corregido.

## 2026-10-20 — CATALOG-VARIANTS-UX + auditoría de módulos (#137 en `master` `f3f6c29`, NO desplegado)

Desde `master` `50e7afe`. Sin migraciones. Decisión **DEC-CATALOG-VARIANTS-UX-01**.
- **IMPLEMENTADO:** «Editar variante» en cada fila del modelo (modelo, línea, opciones, precio,
  publicación) con reglas por historial; «Quitar del modelo» con la misma regla; catálogo de
  módulos corregido; tablero con «Herramientas operativas» y «En desarrollo» aparte.
- Sigue PENDIENTE de verdad: cotizaciones, caja (sesiones, movimientos, caja chica), cuentas por
  cobrar, órdenes de compra y recepciones de compra, cuentas por pagar (PAYABLES-01), garantías,
  reportes por técnico. PARCIAL: equipos de clientes, facturación (resúmenes diarios y bajas sin
  pantalla).
- Pruebas: 19 en `test_variant_edit` (mutación 12/13, la restante equivalente); suite completa
  7853 OK sobre `e11702d`; focal 357 OK al final; Jest 1682; `next build`; Playwright 24/24.
  Revisión independiente: NO-GO (quitar y volver a ubicar saltaba la regla), corregido en
  `bb35a74`; segunda pasada GO con dos P2 (checkouts abandonados, productos sueltos usados)
  corregidos en `c529792`.

## 2026-10-20 — ORDER-RETURN-01: verificación automática; aceptación funcional PENDIENTE

Estado: **IMPLEMENTADO y DESPLEGADO** (última comprobación por SSH, 2026-10-19: `acf20fc`, `0139`
aplicada, 17 OK). **Aceptación funcional del propietario: PENDIENTE** (`docs/NEXT_ACTION.md`).
- Verificación automática (evidencia técnica, no aceptación): 323 pruebas OK sobre `1448a5d`
  (R1–R9) y 6 nuevas en `test_order_return_amounts`. Una venta de S/ 5,000 con S/ 500 de
  descuento comercial (cobrado S/ 4,500) reembolsa S/ 4,500; con S/ 100 descontados por la
  devolución, S/ 4,400; S/ 4,000 se rechaza. El descuento comercial nunca se resta dos veces
  (mutación que lo resta otra vez: detectada).
- **Verificación de producción pendiente:** el 2026-10-20 el SSH no respondió desde la IP del
  equipo de trabajo; el sitio respondía 200 por HTTPS. No es una caída del servidor; no se tocó el
  firewall. Repetir SHA, migraciones y salud cuando vuelva el acceso.

## 2026-10-19 — ORDER-RETURN-01 fase 1 desplegada en producción (#134)

**Producción en `acf20fc`** desde `055cf0d`, el 2026-10-19 (noche, Lima). Migración `0139` aplicada
con `lock_timeout` y `statement_timeout`.
- Antes: copia `20261019-192121` (`gzip -t` OK) restaurada en una base desechable: `0139` de ida en
  6 s, vuelta a `0138` con esquema idéntico, reaplicación idéntica; la imagen anterior
  (`rollback-055cf0d`) funciona sobre el esquema nuevo. Caddyfile respaldado
  (`backups/Caddyfile.before-134`) y sin cambios. Pagos pendientes: 2 intentos de hace casi 8 días.
- Después: `deploy/healthcheck.sh` 17 OK; recuentos iguales (6 pedidos, 4 cancelaciones, 3
  reembolsos pendientes, 10 movimientos); sin errores en el backend.
- Smoke: tienda en 200; endpoints de devolución piden sesión (401). Con las 3 membresías, sólo
  lectura (0 escrituras): `return_block` en cada pedido; ninguno elegible hoy (los pagados ya
  están cancelados); otra empresa 403.
- **Vuelta atrás:** imágenes `rollback-055cf0d` sólo mientras no haya devoluciones ni movimientos
  «Devolución de cliente»; después, corregir hacia adelante.

Desde `master` `91b927c`. Decisión **DEC-ORDER-RETURN-01**; migración `0139`.
- **IMPLEMENTADO:** devolución del pedido completo después de la entrega: registrar, recibir
  (stock al costo de la venta, estado del equipo), cerrar (cancelación por devolución, reembolso
  pendiente menos descuento) y retirar; panel «Devolución» en el pedido; historial de despacho.
- **PENDIENTE (fase 2):** devolución parcial; «a revisión» para cantidades; liberar un apartado
  por devolución desde su propia acción.
- Pruebas: 60 en `test_order_returns` (mutación 12/12 en las guardas); suite completa 7824 OK
  sobre `75ff3fa`; Jest 1655; Playwright 10/10 (incluye la devolución en navegador); `next build`.
- Revisión independiente: NO-GO con dos P1 (un gestor podía registrar el descuento como otro
  reembolso; un equipo podía perderse si se identificaba tras la recepción) y un P2 (doble entrada
  tras una devolución manual), corregidos en `29cbf85`.

## 2026-10-19 — #132 desplegado: corrección de salida con equipos identificados

**Producción en `055cf0d`** desde `7903a49`. Sin migraciones. Decisión del propietario: el sello
`OrderUnit.left_at` que puso el mismo «Enviado»/«Entregado» erróneo no cuenta
(`excused_unit_ids`: sellado por una salida corregida y por ninguna posterior sin corregir); el
equipo vuelve a poder cambiarse o devolverse como si no hubiera salido, y una salida real
posterior lo vuelve a sellar. Dos revisiones independientes: la primera NO-GO (P1: el sello
ignorado congelaba el registro del equipo; P2: enviado→entregado con equipo no se podía
corregir), corregido; la segunda GO. Focal 1191 OK; CI en verde.
- Antes: copia `20261019-153213` (`gzip -t` OK) restaurada en una base desechable; imagen nueva
  probada sobre la copia; imágenes `rollback-7903a49`; Caddyfile sin cambios.
- Después: 17 OK; recuentos iguales; sin errores; smoke de solo lectura con las 3 membresías
  (0 escrituras); otra empresa 403.
- **Prueba 5 del propietario hecha (observada, sólo lectura):** el 2026-10-19 la cuenta
  `administrador` corrigió el «Enviado» de la orden #4 y la canceló con un reembolso pendiente;
  el registro original sigue y figura «Corregido». El pago fue Izipay TEST: no hay dinero real
  que devolver.
- Devolución posterior a la entrega: propuesta en `docs/diseno-devolucion-post-entrega.md`,
  pendiente de decisiones D1–D8.

## 2026-10-17 — ORDER-FULFILLMENT-SAFETY-01 desplegado en producción (#128)

**Producción en `7903a49`** desde el 2026-10-17, 01:39 (Lima), desde `68137e5`. Sólo #128; sin
migraciones (`migrate --check` limpio).
- Antes: copia `20261017-013840` (`gzip -t` OK), restaurada en un PostgreSQL desechable de red
  interna (esquema igual al de producción); la imagen nueva arrancó sobre esa copia. Imágenes en
  uso etiquetadas `rollback-68137e5`; Caddyfile respaldado en `backups/Caddyfile.before-128` y
  sin cambios. Pagos pendientes: 2 intentos de hace más de 5 días, ninguno en curso.
- Después: `deploy/healthcheck.sh` 17 OK; recuentos iguales (6 pedidos, 3 cancelaciones y 2
  reembolsos pendientes de las pruebas del propietario, 7 movimientos, 5 intentos); sin errores
  en los registros del backend.
- Smoke: tienda en 200; panel y corrección piden sesión (401). Con las 3 membresías, sólo
  lectura (GET con `force_authenticate`, 0 filas de auditoría escritas): la #4 se ve como
  recojo (sin «Enviado»), con su historial (6 entradas, sin correos), sin pasos disponibles y
  con la cancelación bloqueada por `goods_left`; «Corregir» sólo para la membresía
  `administrador`, sin bloqueos. Otra empresa: 403.
- **Orden #4 (sin modificar): elegible para corrección.** Evidencia: ninguna fecha de envío,
  guía, seguimiento ni equipo identificado; una sola entrada de salida (12/10, cuenta de
  plataforma); su única edición de entrega cambió `freight_paid_by`. Quién: la cuenta con el rol
  `administrador`. Pasos en `docs/NEXT_ACTION.md`.
- **Vuelta atrás:** imágenes `rollback-68137e5` (sin migraciones). La versión anterior ignora
  las correcciones: una orden corregida volvería a quedar bloqueada (dirección segura).

Desde `master` `ebde440`. Sin migraciones. Decisión: **DEC-ORDER-FULFILLMENT-SAFETY-01**.
- **IMPLEMENTADO:** despacho por pasos según la modalidad (recojo / envío), sin saltos, un paso
  atrás con motivo sólo si nunca salió, «Cancelado operativo» sólo por cancelación comercial,
  guía y transportista antes de «Enviado» por transportista; panel «Estado de despacho» siempre
  visible con pasos, responsable, fecha, motivo del bloqueo e historial; corrección auditada del
  marcado de salida por un superior. Mismas reglas en la app V1.
- **PENDIENTE:** devolución posterior a la entrega de un pedido web (decisiones abiertas en la
  DEC); estado propio de «retenido».
- Pruebas: 45 nuevas en `test_fulfillment_safety` (mutación 22/23, la restante equivalente);
  suites existentes ajustadas al paso a paso sin perder lo que protegían; suite completa 7763 OK
  sobre `f20de11`; Jest 1633 + 21 del panel; Playwright: pedidos, caja y equipos 11/11 y el caso
  #4 en navegador; `next build` OK; `ENSAYO: OK`.
- Revisión independiente: un P1 (borrar guía/fecha antes de corregir) corregido en `97e0fbe`.
- Orden #4: elegible para corrección (sin guía, fecha ni equipos; su única edición de entrega
  cambió `freight_paid_by`). No se tocó.

## 2026-10-16 — #125 y #126 (COMMERCE-ADMIN-UX-01 y su cierre financiero) desplegados en producción

**Producción en `68137e5`** desde el 2026-10-16, 22:58 (Lima), desde `04640cf`. Sólo `master`:
#120 y #122 siguen abiertos y sin desplegar.
- Antes: copia `20261016-225654` (base y evidencias, `gzip -t` OK), restaurada en un PostgreSQL
  desechable de red interna. Imágenes en uso etiquetadas `blackdog-{backend,frontend}:rollback-04640cf`.
  Caddyfile local respaldado en `backups/Caddyfile.before-126` y sin cambios (md5 igual).
- Abiertos al desplegar: 3 pedidos `pending_payment` y 2 intentos `pending`, todos de hace unos
  5 días (pruebas TEST); ningún pago en curso.
- Ensayo sobre la copia con la imagen nueva, `lock_timeout=5s` y `statement_timeout=300s`:
  `0124`, `0125`, `0136`, `0137`, `0138` de ida en 8 s; vuelta a `0124_order_cancellation` y
  reaplicación con esquema idéntico; la imagen anterior funciona sobre el esquema nuevo.
- En producción: `migrate` con los mismos límites aplicó las cinco; `up -d` cambió `backend` y
  `frontend` (sanos). `deploy/healthcheck.sh`: 17 OK. Recuentos iguales antes y después
  (6 pedidos, total pagado 14970.00, 5 intentos, 2 notas emitidas, 1 producto, 5 movimientos);
  0 cancelaciones, 0 reembolsos, 0 ajustes; plazo de anulación 7 días.
- Smoke sin sesión: tienda, `/auth`, carrito, servicios y nosotros en 200; catálogo público sin
  campos de costo ni margen; panel de ventas, comisiones, anular, reembolsos, inventario,
  Kardex y disponibilidad piden sesión (401).
- Smoke autenticado de sólo lectura dentro del servidor (GET con `force_authenticate`, sin
  sesión ni contraseña), por cada una de las 3 membresías: tablero con el bloque de cobros,
  comisiones, pedidos, disponibilidad, stock, movimientos, tarjeta de existencias, clientes y
  configuración en 200. Margen y costos sólo para `administrador` (las otras dos: margen 403,
  stock sin campos de costo). Otra empresa: pedidos 403, tablero 404. KPIs de 7/30/90 días =
  14970.00 en 3 pedidos (los impagos no cuentan).
- No probado por el agente (cambia datos comerciales): anular, reembolsar, liquidar comisiones,
  registrar artículos y pagar con Izipay TEST. Lista en `docs/NEXT_ACTION.md`.
- **Vuelta atrás:** sólo imágenes `rollback-04640cf`, con las migraciones aplicadas (DEC del
  cierre financiero en `05_DECISIONES_TECNICAS.md`).

## 2026-10-16 — Cierre financiero de COMMERCE-ADMIN-UX-01 (#126, desplegado arriba)

Desde `master` `315bedf`. Decisiones del propietario del
2026-10-16, aprobadas: **DEC-COMMERCE-ADMIN-UX-01 · Cierre financiero**. Migración `0138`
(aditiva, reversible).
- **Anular venta POS:** simple como hasta hoy; antigua (más de `pos_cancel_simple_days`, 7),
  con boleta/factura viva (nunca se anula aquí), con comisión liquidada o con stock de varias
  sucursales → sólo un superior (`company.manage`); la negativa dice por qué; ajuste nuevo en
  Configuración.
- **Reembolso confirmado:** `company.manage` + referencia + evidencia + importe exacto del
  pendiente; la auditoría guarda la huella de la evidencia. Cancelar nunca confirma.
- **Comisiones:** estado `settled` y ajuste compensatorio único («saldo a recuperar»); el
  informe separa devengada, liquidada, anulada y por recuperar. Sin pantalla de liquidación aún.
- **Tableros:** auditados todos (tabla en la DEC); corregidos previsión de demanda, tablero
  interno, resumen del cliente, `?paid=true`, promociones y pendiente al proveedor.
- Costo promedio por sucursal: sigue PENDIENTE (fase aparte); ninguna instantánea de venta se
  reescribe (probado).
- Pruebas: focal 1001 OK (cancelaciones, reembolsos, margen, comisiones, analítica, pagos,
  clientes, `test_ci_shards`, paridades); Jest 1613 OK; `tsc` y ESLint sin errores;
  `makemigrations --check` sin cambios; mutación 27/26 + 1 equivalente. Después: suite completa
  7716 OK sobre `b7ae083`, `ENSAYO: OK`, revisión independiente GO sin P0/P1 (sus P2 corregidos
  en `14a6c01` y `ed43fc9`, focal 362 OK) y CI en verde sobre `2ebbc5b`.

## 2026-10-16 — COMMERCE-ADMIN-UX-01 completo (PR a `master`, NO desplegado)

Rama `feat/commerce-admin-ux` desde `master` `5317114`. Integra #121 (y con él #119) por merge
commit y añade, en ramas de trabajo fusionadas aquí:
- **Cancelaciones** (abajo): pedido web pagado o impago, anulación de venta de caja con
  mercadería devuelta, reembolsos registrados (pendiente/confirmado) sin tocar la pasarela,
  métricas de cobros y margen sin doble conteo.
- **Costos y precios (A1):** entrada propia en Inventario para `costs.view`; margen, valor al
  costo, costo promedio por sucursal y costo por equipo, con estados de carga/vacío/error;
  edición de costo de referencia y corrección de costo con motivo. Causa de A1: la sección no
  tenía entrada de menú y desaparecía en silencio; el backend ya respondía bien.
- **Consultar disponibilidad** (Ventas, `sales.pos.use`): stock propio por estado y ofertas
  de proveedor aparte, sin costos, IMEI enmascarado; filtros nuevos en Inventario › Equipos.
- **Registro único de artículos** (nuevo, seminuevo, accesorio, por proveedor) que compone los
  servicios existentes en una transacción idempotente; **Publicación e-commerce** para todos
  los tipos; menú reordenado según el propietario, rutas antiguas vigentes.
- Base local SQLite: `migrate` vuelve a pasar `0131`.

Migraciones nuevas para `master`: `0124`, `0125` (de #119/#121), `0136` (fusión vacía) y `0137`
(reembolsos). Suite completa 7664 OK sobre `b664f1f`; ensayo `ENSAYO: OK`; Jest 1597; navegador
8 specs en verde tras alinear dos pruebas viejas; cuatro revisiones independientes sin P0/P1.

## 2026-10-15 — COMMERCE-ADMIN-UX-01 · cancelaciones (rama `feat/commerce-admin-ux`, incluida arriba)

**EN LA RAMA, sin push ni PR, sin desplegar.** Sobre `master` `5317114` (producción `04640cf`).

- **Integra #121 (y #119)** con un merge (`6472bef`): ORDER-CANCEL-01, SALES-NOTE-DESIGN-01 y
  SALES-NOTE-CONTROL-01. Conflictos resueltos conservando ambos lados (despacho: códigos
  `supply_pending` y `order_cancelled`; detalle del pedido: panel de abastecimiento y de
  cancelación; documentos: ambas entradas). Migraciones `0124`, `0125` (desde `0123`) y
  `0126`–`0135` unidas por `0136_merge_cancellations_device_commerce` (vacía, sin renumerar).
- **Checklist DEVICE-COMMERCE con la cancelación** (`ad5a765`): la devolución
  `order_cancel_return` lleva `order=`, así que vuelve al costo de su salida (probado: promedio y
  «costo pendiente» intactos tras ida y vuelta; un equipo vuelve a su costo; la línea de
  equipo exacto devuelve ESE equipo). Las líneas bajo pedido que esperan al proveedor quedan
  «No se pudo abastecer» y devuelven a la oferta lo comprometido, una vez, auditado.
- **Lo que pidió el propietario** (`800a798`, `9446f35`, migración `0137`):
  cancelar un pedido web **sin pagar** (motivo, sin ingreso, libera los equipos apartados; un
  pago tardío queda como cobro a devolver y marcado en el pedido, nunca lo reabre); **anular una
  venta POS** cuando quien anula marca «la mercadería volvió al local» (stock al costo de la
  venta, nota interna anulada, boleta/factura conservada con «nota de crédito pendiente»,
  comisión anulada); **reembolsos registrados** (`OrderRefund`: pendiente → confirmado con
  referencia, hasta lo cobrado; nunca toca `PaymentTransaction` ni la pasarela); **métricas**:
  ventas sin cancelados, «Cobros» aparte (vigentes, cancelados con cobro, cobros tardíos,
  reembolsos, neto, aún en la tienda), margen con los cancelados listados aparte.
- Decisiones a confirmar por el propietario: **DEC-COMMERCE-CANCEL-01**. Deuda en
  `docs/AUDIT_MEMORY.md` §10. Despliegue: `docs/despliegue-produccion.md` §7 (`0124`, `0125`,
  `0136`, `0137`).

## 2026-10-15 — #123 (DEVICE-COMMERCE-01) desplegado en producción

**Producción en `04640cf`** desde el 2026-10-15, 14:52 (Lima), desde `041d0fd`. Sólo #123:
#119–#122 siguen abiertos y sin desplegar.
- Antes: copia `20261015-144747` (base y evidencias), restaurada en un PostgreSQL desechable
  (103 tablas, 6 usuarios, 1 empresa, 1 producto, 6 pedidos). Imágenes en uso etiquetadas
  `blackdog-{backend,frontend}:rollback-041d0fd`. Caddyfile local respaldado y sin cambios.
- Ensayo sobre una copia con la imagen nueva, con `lock_timeout=5s` y `statement_timeout=300s`:
  `0126`–`0135` de ida en 13 s, vuelta a `0123` con esquema idéntico, reaplicación idéntica.
- En producción: `migrate` con los mismos límites aplicó `0126`–`0135` en 21 s; `up -d`
  cambió `backend` y `frontend` (sanos). `deploy/healthcheck.sh`: 17 OK. Recuentos de
  productos, imágenes, movimientos, pedidos por estado, total pagado e intentos: iguales.
- `costs.view`: sólo el rol `administrador` (por `0126`); `costs_view_report` sin roles
  personalizados pendientes. Ningún otro rol recibió permisos financieros.
- Cron nuevo con `flock`: `release_expired_unit_holds` cada 5 min y `expire_supplier_offers`
  cada hora; ejecutados dos veces a mano sin cambios (idempotentes). Crontab anterior en
  `backups/crontab.before-123`.
- Smoke sin sesión: tienda, carrito, checkout, `/auth`, `/orders`, panel e inventario en 200;
  catálogo y `/api/catalog/models/` sin ningún campo de costo, proveedor, IMEI ni serie;
  margen, proveedores y unidades del panel piden sesión (401); notificación de pago 405 a
  un GET. `ops_status`: abastecimiento y apartados web en OK.
- **Pendiente del propietario (con sesión):** ver la lista en `docs/NEXT_ACTION.md`.
- **Vuelta atrás:** §7 de la guía (pausar pagos, `cancel_pending_unit_orders --apply`,
  `cancel_pending_on_order_orders --apply`, `release_expired_unit_holds --all-web`, quitar
  las dos líneas de cron, `rollback-041d0fd`, dejar las migraciones aplicadas y luego
  `uncosted_after_rollback`).

## 2026-10-15 — DEVICE-COMMERCE-01 en `master` por el PR #123 — IMPLEMENTADO (desplegado: ver arriba)

El propietario autorizó el merge de #123 (no el despliegue). Producción sigue en `041d0fd`
(migraciones hasta `0123`); nada de esta fase corre allí.

**Migraciones `0126`–`0135`:** cadena lineal desde `0123`, una sola hoja. Medidas en una base
desechable con 200 000 pedidos y líneas y 50 000 equipos: ida completa 4–6 s, vuelta a `0123`
7–9 s; `0131` 2,8 s y `0132` 1,2 s. Son cambios de catálogo salvo `0131` (reescribe
`store_stockunit`). Una primera corrida quedó 9 minutos en `0128` **esperando un bloqueo**
(ALTER TABLE pide ACCESS EXCLUSIVE y espera detrás de cualquier transacción abierta sobre
`store_orderitem`); repetida, tardó 0 s. En producción, `migrate` debe correr con
`lock_timeout` (p. ej. `PGOPTIONS='-c lock_timeout=5s'`) para fallar rápido en vez de
encolar el checkout detrás.

**PR abiertos #119–#122:** #119 y #120 traen cada uno su `0124` y #121 una `0125`. No se
renumera nada: el PR que se fusione después de #123 añade su migración de fusión
(`makemigrations --merge`), sin AlterField pendientes (revisado sobre los grafos).

**Preflight para autorizar el despliegue (pendiente, no hecho):**
1. Copia de seguridad y restauración verificada; imágenes etiquetadas `rollback-<sha>`.
2. Ensayo de `0126`–`0135` sobre una copia de la base de producción con la imagen nueva
   (ida, vuelta, ida; esquema idéntico), y `migrate` en producción con `lock_timeout`.
3. `costs_view_report` tras migrar: asignar `costs.view` a mano sólo en empresas cuyo rol
   Administrador fue personalizado; nunca a vendedores por defecto.
4. Cron: `release_expired_unit_holds` y `expire_supplier_offers`; `ops_status` revisado.
5. Prueba con sesión (propietario): reserva y compra de una unidad exacta con pago TEST de
   Izipay, POS con stock y bajo pedido, oferta vencida → «por confirmar», margen y
   «Valor al costo» con y sin `costs.view`, carga de ofertas por Excel.
6. Al fusionar #119 (cancelación): su devolución debe heredar el costo de la salida de venta
   (lista en `docs/despliegue-produccion.md` §7).
7. No tocar el `deploy/Caddyfile` local del servidor ni las variables de entorno.

## 2026-10-15 — DEVICE-COMMERCE-01 · decisiones del propietario implementadas en rama local

- Rama `feat/device-commerce`, commits locales sin push; **no desplegado**. Tres decisiones del
  propietario aprobadas el 2026-10-15 (DEC-DEVICE-COMMERCE-01 · Decisiones del propietario):
  1. Apartados de equipos con duración por empresa y canal (carrito 15, checkout 45, tope 60
     al crear; con límites) y un apartado cuyo pedido tiene un pago que la pasarela aún puede
     confirmar no se libera por vencimiento hasta que la conciliación decida (2 h, o mientras
     la respuesta sea indecidible); liberación idempotente bajo bloqueo.
  2. «Bajo pedido» con stock propio sólo por elección explícita: en caja con
     `sales.orders.manage` y motivo; en la web sólo si la empresa lo permite (apagado al
     crear). La línea guarda fuente, costo, plazo, la marca y el motivo; queda auditado.
  3. Margen estimado y realizado, costo desconocido y reembolsos aparte, con importe y %.
- Migración `0135` (aditiva). Sin cron nuevo.
- Sigue pendiente (deuda): seminuevos de proveedor externo, reembolsos a mano, bajo pedido
  en la app nativa, fricciones del carrito.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC3 en rama local (y segunda revisión de DC2)

- Rama `feat/device-commerce`, commits locales sin push. DC3 (DEC-DEVICE-COMMERCE-01 · DC3):
  proveedores y ofertas por variante (precio comercial, costo pactado sólo con
  `costs.view`, cantidad confirmada, vigencia, plazo), modo «Bajo pedido» por producto,
  venta bajo pedido en la web y en caja (la oferta se compromete al pagar, sin movimiento
  de stock; oferta vencida o agotada al pagar → pago registrado y «abastecimiento por
  confirmar»), recibir del proveedor (entrada + salida, neto cero), «no se pudo abastecer»,
  carga Excel semanal y exportación, resumen «pendiente de liquidar al proveedor» (sólo
  lectura, gancho de PAYABLES-01).
- Segunda revisión de DC2: el reparto del estante sólo desde 4 equipos más un techo por
  empresa; el pedido pagado gana su equipo; orden de bloqueos en el apartado; costo de
  entrada de equipos sin costo propio.
- Migración `0134` (aditiva). Nuevo cron: `expire_supplier_offers` cada hora.
- PENDIENTE: seminuevos de proveedor externo (diseño en la DEC). Pendiente del
  propietario: las decisiones «por confirmar» de DC3 y del techo de apartados.

## 2026-10-14 — DEVICE-COMMERCE-01 · DC2 en rama local (y revisión de DC1)

- Rama `feat/device-commerce`, commits locales sin push. DC2 (DEC-DEVICE-COMMERCE-01 · DC2):
  modelos con variantes (capacidad, color… configurables por empresa), pestañas
  «Nuevos» / «Seminuevos» con rótulos de la empresa, página de modelo, seminuevos uno por
  uno (batería o «no informado», estado estético, fotos reales, precio propio, IMEI sólo
  en sus 4 últimos dígitos) y **compra de unidad concreta** para nuevos y seminuevos: el
  carrito aparta el equipo y el pago vende ese equipo. Excel con las columnas nuevas.
- Revisión independiente de DC1: cinco P2 y los P3 corregidos (ver la DEC). Tras `migrate`,
  correr `costs_view_report`.
- Migraciones: `0129` (DC1 revisión, `uncosted_quantity` + marca del stock sin costo),
  `0130`–`0132` (DC2). Dependen de `0128` → `0123`; hay PR abiertos con `0124`/`0125`:
  se renumeran al fusionar.
- Nuevo cron: `release_expired_unit_holds` cada pocos minutos (también se libera al leer).
- Pendiente del propietario: confirmar las decisiones marcadas «por confirmar» (precio por
  equipo en líneas separadas, productos serializados sin modelo con SERIAL-PICK, salida
  primero de lo que no tiene costo), y crear modelos/opciones/escala en su empresa.
- Siguiente: DC3 (disponibilidad de proveedor sobre `availability`).

## 2026-10-14 — DEVICE-COMMERCE-01 · DC1 en rama local

- Rama `feat/device-commerce` desde `master` `abfc6bd`, commits locales sin push.
- Costos y margen comercial (DEC-DEVICE-COMMERCE-01): `costs.view`, costo promedio por
  sucursal y costo específico por equipo en el Kardex, instantáneas de costo y base sin IGV
  en cada línea vendida, `GET /api/admin/sales/margin/`, inventario al costo y columnas de
  costo en las cargas y descargas. Cierra la fuga de costo de la lista de equipos.
- Migraciones `0126`–`0128` dependen de `0123`; hay PR abiertos con `0124`/`0125`: se
  renumeran al fusionar.
- Pendiente del propietario: confirmar las decisiones marcadas en DEC-DEVICE-COMMERCE-01
  (costos sin IGV, «Precio costo» como referencia, margen sólo sobre líneas con costo).
- Siguiente: DC2 (desgloses por unidad/variante).

## 2026-10-14 — SALES-NOTE-CONTROL-01 (rama, sin push)

Rama `feat/sales-note-control`, apilada sobre `feat/order-cancel-sales-note` (#119, `3dd4f54`).
**EN LA RAMA: sin push, sin PR, sin desplegar.** Una migración nueva, `0125` (aditiva: cuatro
columnas en `store_salesnote`, las de texto con `db_default`, y la tabla
`store_salesnoteremark`; no escribe datos). Sin
capacidades nuevas ni cambios de rol.

- **Anular una nota de venta es un acto del usuario Master**: el master de plataforma
  (`is_platform_admin`, hoy `is_superuser`) actuando en la empresa que nombra. Un
  administrador de empresa con todas las capacidades recibe 403. `POST
  /api/admin/sales-notes/<id>/void/` con `reason_type` (`prueba`, `error`, `otro`) y motivo
  obligatorio (5–500 caracteres); una nota ya anulada, 409 `already_void`. Guarda
  `voided_at`, `voided_by`, `void_reason_type`, `void_reason` y la auditoría
  `sales_note_voided` (empresa, sucursal, número, motivo). La nota conserva número, ordinal,
  fecha de emisión, emisor y lo que imprimió; sus PDF y la térmica dicen ANULADA.
- **No toca nada más**: ni el pedido (sigue pagado), ni pagos, ni stock, ni comprobantes
  SUNAT, ni la serie (el número no se reutiliza y «Próximo número» sigue bloqueado). Los
  tableros leen pedidos pagados, no notas: el dinero cobrado no cambia. La cancelación de
  pedidos de #119 escribe ahora el mismo registro con `pedido_cancelado`.
- **Observaciones internas** (`SalesNoteRemark`): sólo se añaden (ni editar ni borrar), las
  añade el Master o quien tenga `sales.notes.manage`, quedan en auditoría
  (`sales_note_remark_added`) y no se imprimen.
- **Panel › Ventas › Notas de venta** (`/admin/sales/notes`): filtros vigentes / anuladas /
  prueba, fechas, sucursal y emisor, con el alcance de empresa y sucursal de los pedidos;
  marca ANULADA (y «Prueba»); «Anular» sólo si el servidor dice `can_void`. Las notas ya
  emitidas no se marcan solas: «prueba» lo escribe el Master al anularlas.
- La térmica no reimprime una nota anulada, y un ticket encolado antes sale con ANULADA. El
  admin de Django de `SalesNote` queda entero de sólo lectura (antes `status`, `sequence` y
  `sequence_value` se podían editar sin auditoría).
- Revisión independiente (`093ea47`): las dos columnas de texto de `0125` llevan
  `db_default=''` (P1: sin él, la versión anterior fallaba al emitir una nota mientras se
  migra); las observaciones tampoco se reescriben ni se borran en bloque; una nota de un
  pedido cancelado ya cuenta como anulada (409); un ticket encolado antes de anular se
  cancela al entregarlo al agente; en el panel, la nota anulada sale de «Vigentes» y el
  tipo de motivo es un solo grupo de radio (una parada de Tab, flechas).
- Pruebas: `test_sales_note_control` (41, con dos carreras de dos conexiones; mutación 18/20 muertas, M2 cubierta después, M19 equivalente); regresión
  focal de 417 pruebas (notas, series, POS, analítica, impresión, `test_order_cancel`,
  `test_document_design`, `test_ci_shards`) en verde; Jest 1370/1370; `tsc` y lint sin
  errores (Jest 1372/1372 tras la revisión); `makemigrations --check` sin cambios (sólo `0125`). Decisión en
  DEC-SALES-NOTE-CONTROL-01; deuda en `docs/AUDIT_MEMORY.md` §10.

## 2026-10-14 — ORDER-CANCEL-01 y SALES-NOTE-DESIGN-01 (rama, sin push)

Rama `feat/order-cancel-sales-note` desde `abfc6bd`. **EN LA RAMA: sin push, sin PR, sin
desplegar.** Una migración nueva, `0124` (aditiva). Sin capacidades nuevas ni cambios de rol.

- **SALES-NOTE-DESIGN-01** (`2ba90e7`). La nota de venta (A4, ticket de 80 mm y térmica
  ESC/POS) se titula «NOTA DE VENTA» y la franja inferior enmarcada se sustituye por una línea
  discreta: «Documento de control interno, sin validez tributaria» (una sola constante,
  `SALES_NOTE_CONTROL_LINE`, que también repite el aviso de la API). RUC, datos de la empresa,
  numeración `NV-`, importes e información comercial sin cambios. La constancia de pedido
  conserva su franja (`legal` de `render_a4` intacto). Comprobantes fiscales sin tocar.
- **ORDER-CANCEL-01** (`169362b`). Cancelar desde el detalle del pedido un pedido EN LÍNEA
  PAGADO cuyos productos no han salido (pendiente, confirmado, en preparación, listo para
  retiro o «Cancelado operativo»), con motivo obligatorio (5–500 caracteres). Enviado o
  entregado: 409 «requiere devolución»; caja (POS) y pedidos sin pagar: fuera de este flujo.
  El stock vuelve una vez (`order_cancel_return`, con el pedido, a la misma sucursal; los
  equipos con serie a «disponible» y sin su identificación). La nota interna queda anulada y
  se conserva; sus PDF y la constancia se descargan con «ANULADA» / «PEDIDO ANULADO»; la
  térmica no imprime un pedido cancelado. Una boleta/factura viva se conserva y queda
  «nota de crédito / baja pendiente» (nada fiscal se emite solo). **No es un reembolso:**
  `paid` sigue verdadero, ningún intento de pago cambia y no se llama a la pasarela; un cobro
  que llegue después queda como cobro duplicado (evidencia) y el pedido sigue cancelado.
  Detalle de decisiones en DEC-ORDER-CANCEL-01; deuda en `docs/AUDIT_MEMORY.md` §10.
- Revisión independiente (`e142874`): «no ha salido» se lee de la historia del pedido (P1),
  boleta/factura y nota interna re-comprueban el pago con el pedido bloqueado (P2), un
  equipo devuelto antes no impide ni duplica la reposición, estadísticas del cliente sin
  cancelados y pruebas que antes no fijaban nada.
- Pruebas en la rama: `test_order_cancel` (44, con tres carreras de dos conexiones),
  regresión focal 2451 pruebas en verde; Jest 1356/1356; `tsc` y lint sin errores;
  `makemigrations --check` sin cambios (sólo `0124`).

## 2026-10-14 — #115, #116 y #117 en `master` y desplegados

| | #115 | #116 | #117 |
|---|---|---|---|
| Qué | Registro de los despliegues de #110 y #111 | CI del backend en seis fragmentos | INVENTORY-UNIFIED-01 |
| Merge en `master` | `071e021` | `133a3ec` | `041d0fd` |
| CI del SHA fusionado | sin CI (sólo docs) | 6791 pruebas, verde (`0bd808a`) | 7109 pruebas, verde (`ce2f8d0`) |

**Producción en `041d0fd`** desde el 2026-10-14, 01:14 (Lima), desde `14e658c`.
- Antes: copia `20261014-011108` (base y evidencias), restaurada en un PostgreSQL
  desechable (103 tablas, 6 usuarios, 1 empresa, 1 producto, 5 pedidos). Imágenes en uso
  etiquetadas `blackdog-{backend,frontend}:rollback-14e658c`.
- Ensayo sobre una copia de esa base con la imagen nueva: `0122` y `0123` aplicadas,
  deshechas (`migrate store 0121`) y aplicadas otra vez; esquema idéntico en cada vuelta.
- En producción: `migrate` aplicó `0122` y `0123`; `up -d` cambió `backend` y `frontend`
  (sanos). `deploy/healthcheck.sh`: todo OK. Recuentos de empresas, productos, imágenes,
  movimientos, pedidos por estado, total pagado e intentos de pago: iguales antes y después.
  Pasarela «Mi Cuenta Web» en TEST, sin pausa, sin cambios. Caddyfile y cron sin tocar.
- Smoke sin sesión: `/`, `/cart`, `/checkout`, `/auth`, `/product`, `/services`, `/orders`,
  `/admin/inventory/bulk`, catálogo, categorías y configuración de la tienda en 200; carrito
  por clave en 200; exportaciones, reglas de imágenes y acción en bloque piden sesión (401);
  la notificación de pago responde 405 a un GET.
- **Pendiente del propietario (con sesión):** ocultar y publicar un producto en bloque,
  descargar un archivo, subir una imagen en lote a un producto real y un pago TEST. No creo
  cuentas ni escribo contraseñas en producción.

**Vuelta atrás:** volver los contenedores a `rollback-14e658c` (`git checkout 14e658c`,
`docker tag … :latest`, `up -d`) y dejar `0122`/`0123` aplicadas: la versión anterior
funciona con ellas. Deshacer `0123` borraría la elección de imagen de cada empresa.

## 2026-10-14 — INVENTORY-UNIFIED-01: decisión del propietario sobre imágenes en lote

PR #117 (fusionado y desplegado, ver arriba). `41c164f`, `7099642` (prueba) y `f780088` (docs), luego `master` (#115, #116) fusionado
en la rama. Sólo la carga de imágenes en lote: el código se compara con NFKC, sin mayúsculas y
con los espacios normalizados, contra todos los códigos de la empresa; si coincide con dos o más
productos no se asigna y se dice. Una fila del libro «Imágenes» sin producto (o ambigua) se
lista como los archivos huérfanos y ya no frena el resto; un error en las imágenes de un producto
encontrado (archivo roto, dos archivos distintos con un nombre, más del límite) sigue frenando
la carga. Fotos opcionales sin cambio. La carga de productos sigue comparando exacto
(deuda IMG-IMPORT-KEY-ONLY-IMAGES). Sin migración.

## 2026-10-13 — INVENTORY-UNIFIED-01: las siete decisiones del propietario

Misma rama (`feat/inventory-unified`), sobre `8dd3e59`: `e2c9c84`…`4a46012`, un commit por
decisión, más `a94c8fa` y `0dbf3b3` (pruebas que cerraron mutantes; la segunda es G01 de la
tercera revisión) y `1f21cfe` (F1 de la tercera revisión: «Estado» lee Sí/No, 1/0,
verdadero/falso, y un valor ilegible sólo es error al CREAR; al actualizar, aviso). **EN LA RAMA, probado por partes: sin push, sin PR, sin desplegar.** Una migración
nueva, `0123` (aditiva); una variable opcional, `ADMIN_EXPORT_RATE`. Sin capacidades nuevas,
sin rutas nuevas, sin tocar el Kardex ni `StockUnit`, ni el Caddyfile ni `request_limits.py`.

| | Decisión | Qué hace ahora |
|---|---|---|
| D1 | Productos nuevos importados: «Solo stock interno» por defecto | Un producto NUEVO sin «Destino» (celda vacía o sin columna) nace interno. Un «Destino» explícito se respeta (con D2). Uno existente sin «Destino» se queda como está. La ficha y las demás altas no cambian |
| D2 | Publicación masiva: rechazar sólo lo inelegible, con motivo por fila; imagen sólo si se configura | Regla única: activo, con precio y con categoría; imagen sólo con `CompanySettings.publication_requires_image` (casilla en Configuración, apagada). En la carga, una fila que pide la web para algo inelegible NO es error: se carga, queda interna y dice el motivo; la vista previa y el resultado cuentan los rechazos. Aplicar vuelve a preguntar con el producto bloqueado. Lo ya publicado no se despublica por esta regla |
| D3 | Exportación reimportable: activos e inactivos; no reactivar al importar | El archivo para volver a cargar (y el ZIP) lleva activos e inactivos y respeta el filtro de estado; gana la columna «Estado» (se lee Activo/Inactivo, Sí/No, 1/0, verdadero/falso). La carga nunca cambia `is_active` de un producto existente (lo avisa si el archivo dice otra cosa); un producto nuevo «Inactivo» nace inactivo e interno |
| D4 | Descargas: 6/min configurable, 429 claro | `ADMIN_EXPORT_RATE` (6/min), comprobado al arrancar: un valor inválido vuelve al de por omisión con una línea en el registro. El 429 dice en castellano que están limitadas y en cuántos segundos volver, con `Retry-After`; el panel lo muestra. Las cinco tarjetas seguidas caben |
| D5 | Productos sin código: identidad estable, sin emparejar nombres ambiguos | El archivo para volver a cargar lleva «Identificador» (`<empresa>-<producto>`). Si nombra un producto de ESTA empresa decide: un renombrado se actualiza. Otro, ajeno o inexistente: se ignora con un aviso y deciden los códigos. Identificador y código en productos distintos: error. Sin identificador ni código, un nombre que llevan dos productos es error (ya lo era) |
| D6 | App v1: lo oculto no se expone; el pedido conserva lo suyo | «Mis pedidos» de la app: de un producto que ya no se vende, nombre e imagen (lo mismo que la web) y `product_slug` vacío; cantidad y precio son los del pedido. Lo que se vende no cambia |
| D7 | Excel sin apóstrofos visibles, sin fórmulas | Un texto que empieza por `= + - @`, tabulador o retorno se escribe como celda de texto (`quotePrefix`), sin apóstrofo. El lector ya no quita ninguno y rechaza las fórmulas por el tipo de celda. `errors.csv` conserva el apóstrofo: un CSV no tiene tipos |

**Cambió, además de lo pedido.** El lector de Excel lee como texto una celda de TEXTO que
empieza por `=` (antes la rechazaba como fórmula); una celda que ES fórmula se sigue
rechazando. `effective_filters` desapareció y la auditoría de una descarga anota los
filtros tal como se pidieron. En la carga, el `destination` de la vista previa es lo que
va a pasar (un rechazo de D2 muestra «Sin cambios» o «Solo stock interno»). La columna
«Estado» se lee por el campo de carga `is_active`, que ya existía y no se usaba. La línea
cerrada de «Mis pedidos» en la web ya no construye el producto entero (deuda
ORDER-LINE-CLOSED-WASTE). Dos especificaciones de Playwright (`product-destination.spec.ts`
e `import-split.spec.ts`) se ajustaron a D1/D2 sin correrlas aquí.

**Pruebas.** Cada decisión con su prueba vista en rojo antes del código. Pasadas focales
(PostgreSQL, `blackdog_focal`) sobre `4a46012`: 656 OK (módulos nuevos, importación,
plantilla, equipos, medios, límites, visibilidad, stock por serie, `test_preflight`,
`test_deploy_scripts`); sobre los commits de D1–D6, las clases de importación, producto,
pedidos, carrito, checkout y configuración de `store/tests.py`, `Ip1ParityManifestTest`,
`test_delivery_base` y `test_serial_pick`, en verde. Mutaciones: 32 sobre el código de D1–D7; 28 detectadas en la
primera pasada; tres supervivientes eran huecos de prueba y se cerraron (`a94c8fa`, `0dbf3b3`);
la cuarta es equivalente (marcar como texto una cabecera: ninguna cabecera puede empezar por
`=`, `+`, `-` o `@`).
`makemigrations --check`: una migración nueva, `0123`.
- Suite completa del backend en PostgreSQL sobre `6e556c9`: 7056 pruebas, 0 fallos, 6 omitidas
  (6716 en `master` `14e658c`).
- Jest 1341/1341 en 121 suites, `tsc --noEmit` limpio, lint 0 errores y 22 avisos, sobre
  `4a46012` (el frontend no cambia después).
- Playwright 97/97 sobre `6e556c9` (`product-destination` e `import-split` ya con D1/D2,
  `storefront`, `tax-breakdown`, `krypton-smartform`), y recorrido en navegador real sin
  errores de consola: acción en bloque con `?company=`, carrito y pago con un producto
  ocultado, descarga del catálogo.
- `sh deploy/rehearsal.sh` sobre `6e556c9`: `ENSAYO: OK` (149), con `0122` y `0123`.
- Tercera revisión independiente (`8dd3e59..4a46012`): sin P0, sin P1, sin regresiones; sus
  dos hallazgos menores (F1, G01) corregidos en `1f21cfe` y `0dbf3b3`.

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

## 2026-10-13 — CI-OPTIMIZATION-01: auditoría de GitHub Actions y suite del backend en fragmentos

Rama `chore/ci-optimization` (desde `14e658c`), commits locales, **sin push**: PARCIAL hasta que corra en
GitHub. Sólo CI, dos scripts de desarrollo y sus pruebas. Cómo queda organizado: `docs/CODEBASE_MAP.md` § CI.

**Auditoría (85 runs del backend, 2026-10-02 → 10-13).** El paso de tests es más del 99 % del trabajo;
checkout, Python, `pip` (caché ya activa, acierta) y migraciones (~30 s) suman menos de 1 min. 60 runs
verdes: mediana 72 min, de 43 a 99, bimodal según la máquina que toca (los últimos 30: mediana 82). 21
cancelados, 10 de ellos en `master` (el grupo de concurrencia cancelaba también ahí: el 2026-10-11, de
siete merges seguidos sólo se probó el último). El 28 % de los minutos son runs verdes de `master`, que
con el PR al día repiten el árbol que ese PR ya validó. Frontend: 1–2 min; su caché de npm nunca se guardaba donde otro PR
pudiera leerla. `master` no tiene protección de rama ni checks obligatorios.

| | Antes (medido) | Después |
|---|---|---|
| Backend, un run en GitHub | mediana 72 min (43–99) | **esperado** 12–20 min: 6 fragmentos a la vez; sin medir hasta el primer push |
| Aviso de un fallo | a los 15–73 min, un solo trabajo | cada fragmento informa por separado, con su lista de fallos en el resumen |
| Suite completa en local, un proceso | 6747 tests, 2285 s (38 min) | igual; los 6 fragmentos uno tras otro: 6783 tests, 2316 s |
| Fragmento más pesado / media (local) | — | 394 s / 386 s medido en el primer reparto; 385 s / 383 s por pesos en el definitivo |
| Runs de `master` cancelados | 10 de 30 | 0 (cada merge se valida) |
| Disparo por `deploy/**`, `frontend/app/admin/**`… | no | sí (los leen tests del backend) |

**Equivalencia.** Descubrimiento del runner: 923 clases, 6791 tests (6747 + 44 nuevos), ningún id
repetido. De 1 a 16 fragmentos, la unión es la suite y ninguna clase está en dos; las etiquetas de cada
fragmento cargan por el runner exactamente sus tests; cada fragmento conserva el orden de la suite
entera. Corridos en local los seis fragmentos del primer reparto: 1141 + 1039 + 1057 + 1054 + 1405 +
1087 = 6783 = la suite de ese commit, 6 omitidos como en la corrida entera, y **un error**, que es el
hallazgo de abajo. Del reparto definitivo (`5f22aff`) se corrieron de punta a punta dos fragmentos: el 6,
que lleva las 24 clases que vacían la base (1120 tests, 374 s, OK), y el 3, el más numeroso (1566 tests,
385 s, OK); en los dos, «Ran» coincide con lo repartido. 22 mutaciones deliberadas de los scripts y del
workflow (quitar un fragmento de la matriz, una puerta que se puede saltar, una clase en dos fragmentos,
un import roto que se calla, un archivo externo olvidado…): las pruebas de guarda detectan todas.

**Hallazgo: dependencia de orden que la suite entera esconde.** `store.tests.C15InitialRaceTest`
(`TransactionTestCase` con `reset_sequences`) sólo pasa si antes corrió otra clase que vacía la base; sola
falla con «duplicate key store_company_pkey (id)=(1)» — también en `master`, a mano. Hay cinco clases
así. No se tocó ningún test: las 24 `TransactionTestCase` van siempre juntas al último fragmento, en el
orden de la suite, y el reparto dejó de depender de qué otras clases existen (añadir un test no mueve a
ningún otro). Queda como deuda **TEST-TTC-RESET-SEQ** (PENDIENTE, baja): que esas cinco clases no
dependan de que otra limpie antes. **RESUELTO el 2026-10-23 en #155 (TEST-FIXTURE-SEQ-01):** ninguna usa ya
`reset_sequences`, cada una pasa sola en una base recién migrada, y `test_ci_shards` lo impide en adelante.

**Lo que no se implementó, y por qué.**
- `--parallel`: en un subconjunto de 601 tests (7 clases de concurrencia real) pasa y va 3,7× más rápido,
  pero aquí arranca procesos con `spawn` y el CI con `fork`: no es prueba. PROPUESTA.
- Hasher de contraseñas rápido en tests: ~74 % del tiempo medido en tres módulos es PBKDF2 (229 tests,
  150,6 s → 39,3 s con MD5, todos verdes). Cambia lo que ejercitan los tests de autenticación: decisión
  del propietario. PROPUESTA **TEST-HASHER**.
- Saltarse en `master` la suite de un árbol idéntico al del PR: ahorra minutos, no tiempo de espera, y
  añade lógica que podría saltarse algo. No.

**Del propietario.** Nada en los ajustes de GitHub para mergear esto (no hay checks obligatorios y el
nombre `Django tests on PostgreSQL` se conserva). Si algún día se exigen checks, ver la advertencia de
`docs/CODEBASE_MAP.md` § CI sobre los filtros por ruta.

**Sin verificar hasta el push.** La duración real; la sintaxis del workflow en GitHub (no hay
`actionlint` aquí: se analizó el YAML y se contrastaron las expresiones con la documentación); Python
3.12 y PostgreSQL 16 (en local: 3.14 y 14); los fragmentos 1, 2, 4 y 5 del reparto definitivo de punta
a punta.

## 2026-10-13 — #110 y #111 en `master` y desplegados

**#111 (CLIENT-SEC-01 fase 1 y base de DELIVERY-01)** se fusionó con commit de merge y CI
verde en `c76317e`. `master` quedó en `14e658c`, con el mismo árbol: el que pasó la
suite completa (6747, 0 fallos, 6 omitidas), el ensayo (`ENSAYO: OK`) y cuatro
revisiones independientes, la última sin P0 ni P1.

**Desplegado el 2026-10-13, 15:35 (Lima).** El servidor pasó de `dfc817a` a `14e658c`
siguiendo `docs/despliegue-produccion.md` §7: copia previa `db-20261013-152521.sql.gz` y
`evidence-20261013-152521.tar.gz`, imágenes construidas con la web en línea, `migrate
--plan` con cuatro operaciones (`0118`–`0121`), `migrate` sin incidencias, contenedores
reemplazados y sanos, `deploy/healthcheck.sh` en verde, `manage.py check` sin
incidencias y sin errores en el registro. El `deploy/Caddyfile` local del servidor no
cambió. No se tocó la pasarela ni ninguna variable.

**Antes de migrar:** 5 pedidos en línea (2 pagados, 3 por pagar), 5 intentos de pago, 1
cliente, 6 usuarios; pasarela «Mi Cuenta Web» en TEST. La copia previa se restauró en un
PostgreSQL desechable sin red; sobre esa copia, con la imagen nueva, `0118`–`0121` se
aplicaron, se deshicieron y se aplicaron otra vez, y tras deshacerlas el esquema es
idéntico al de `0117`.

**Lo que hicieron las migraciones aquí:** un registro de entrega por cada uno de los 5
pedidos (los cinco «recojo en tienda») y «correo verificado» en 2 cuentas. Pedidos,
totales (S/ 24 950,00), pagos e intentos, iguales que antes. Ninguna evaluación de
riesgo: se crean con los pedidos nuevos.

**Vuelta atrás.** Lo recomendado es volver sólo los contenedores a las imágenes
guardadas como `rollback-dfc817a` y dejar las migraciones aplicadas: el código anterior
funciona sobre el esquema nuevo. Revertir el esquema (`migrate store 0117` con la
imagen nueva) borra transportistas, libretas, registros de entrega, evaluaciones y
decisiones; sólo como último recurso y con la copia previa a mano.

**Comprobado en producción después**, sin sesión y sin enviar formularios: tienda,
carrito, checkout, «Mi cuenta», pedidos y panel responden 200; el perfil, la libreta de
direcciones, los transportistas, la entrega y la revisión de un pedido responden 401
sin sesión; las rutas de pago responden 405 a una lectura. `PAYMENTS_PUBLIC_TEST`
apagado; la retención de envíos y el importe de revisión, sin configurar.

**#110 («Mi Cuenta Web»)** estaba desplegado desde el 2026-10-11, 22:46 (Lima), de
`3057dd2` a `dfc817a`, con la migración `0117`; no quedó escrito entonces. El 12 de
octubre el propietario escribió sus claves de TEST en la consola y completó pagos de
prueba con Mastercard y American Express; con VISA la autenticación 3DS de la tienda en
TEST falla en Izipay (`PSP_727`), también en el formulario mínimo oficial.

**Falta, y sólo puede hacerlo el propietario con su sesión:** abrir «Mi cuenta»,
verificar el correo con el código, guardar una dirección, y en el panel revisar un
pedido y completar su entrega con transportista y guía.

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
pequeños. En `master` y desplegado el 2026-10-11 (`dfc817a`). Nada se probó contra la pasarela real.

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
En `master` y desplegado el 2026-10-11 (`dfc817a`). **Nada se probó contra la pasarela real: no hay credenciales.**

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
nuevas.** Decisión: DEC-PAY-03. En `master` y desplegado el 2026-10-11 (`dfc817a`). **Nada se probó contra la pasarela real:
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
