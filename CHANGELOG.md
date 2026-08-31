# Changelog — Black Dog Store

Formato: cada entrada lista lo entregado y su estado
(`IMPLEMENTADO` · `PARCIAL` · `PENDIENTE` · `PROPUESTA` · `OBSOLETO`).

Este archivo se creó en la Fase SaaS 1. Las fases anteriores se reconstruyen a
partir del historial de git y de la documentación del repositorio; no se inventa
información que no esté respaldada por código o commits.

---

## P0 — Estabilización de runtime (incidente de esquema)

**Estado: RESUELTO.** Sin migraciones nuevas. Correcciones de código y de entorno.

### Síntomas
En localhost, con el código de las Fases 2D + 3 + 2E + 4 en el árbol:

- `/admin` — «No se pudo cargar el control interno.»
- `/cart` — «Error del servidor: 500 Internal Server Error»
- catálogo — «Error al cargar: Error del servidor: 500 Internal Server Error»

### Causa raíz — demostrada, no supuesta
La base de datos de desarrollo estaba en la migración **0023**. Faltaban
**diez**: 0024–0033.

El traceback real de las tres rutas públicas era el mismo:

```
OperationalError: no such column: store_company.default_inventory_branch_id
    store/views.py → store/tenancy.py
```

Esa columna la añade la 0024. El código era correcto y sus 1429 tests pasaban:
**el esquema de la base de datos de desarrollo iba por detrás del código.**

No era un fallo de `master` ni de las fases nuevas. Era un incidente de
despliegue.

### Por qué la suite no podía detectarlo
Django construye una base **nueva** para cada ejecución de tests y le aplica
todas las migraciones. Una suite verde dice «este código concuerda con estas
migraciones»; no dice absolutamente nada sobre la base a la que está conectado
el servidor. Ninguna cantidad de tests unitarios habría encontrado esto.

Por eso la corrección no es un test más, sino un **check de entorno**.

### Corregido
- **Migraciones aplicadas**, 0024 → 0033, con copia de seguridad previa fuera del
  repositorio. Sin resetear ni borrar nada. Las 45 unidades de stock histórico se
  ubicaron en la única sucursal activa (suma verificada: 45 = 45).
- **`store/checks.py`** — check de despliegue que avisa al arrancar cuando la base
  tiene migraciones sin aplicar, nombrándolas. `runserver` ahora lo dice en el
  arranque en vez de que la aplicación lo descubra tres páginas después como un
  500 con un nombre de columna dentro.
- **`POST /api/cart/` cerrado con 405.** `CreateModelMixin` exponía esa ruta y
  respondía **500 con cualquier entrada**, porque `CartItemSerializer.product` es
  de solo lectura. Se cierra en lugar de repararse: hacerla funcionar significaría
  una segunda forma de escribir un `CartItem` que no acota el producto a este
  storefront, no exige `session_key` y no valida stock — justamente el vector
  cross-tenant que `add` existe para cerrar.
- **`.env.example`** documenta `INVENTORY_MIGRATION_BRANCHES`: es lo primero que
  hay que mirar si `migrate` se detiene, y detenerse es su comportamiento
  correcto.
- 15 tests nuevos (1429 → **1444 OK**, 2 omitidos).

### Lo que NO se hizo
- **No se resetó ni se borró la base de datos.** Se probó una actualización real.
- **No se tocó la migración 0025.** No hizo falta: una empresa con una sucursal
  activa, sin ambigüedad. Su protección sigue intacta.
- **No se añadió ningún `try/except OperationalError`.** Un compatibility hack
  habría escondido un despliegue roto detrás de una pantalla que carga.

### Estado de Git al abrir el incidente
`master` local (`6d8c3e0`) está **por delante** de `origin/master` (`2624d47`) por
un merge. Quien creyera estar probando «master» estaba probando algo distinto de
lo que hay publicado.

### Deuda
- La copia de seguridad previa quedó en `$HOME`, fuera del repositorio.
- El check avisa; no aplica nada. Aplicar migraciones sigue siendo una decisión
  de despliegue, y la 0025 se detiene a propósito.

---

## Fase SaaS 4 — Clientes tenant-aware / CRM interno

**Estado: IMPLEMENTADO.** Migraciones **0031, 0032, 0033**.

### El problema que cierra
Hasta esta fase la plataforma no tenía clientes. Tenía `User` —un login global— y
los campos `customer_*` de cada `Order`, que son una fotografía de quién compró
ese día. No había ningún sitio donde decir *quién es este cliente para esta
empresa*, y por lo tanto tampoco había historial: dos compras de la misma persona
eran dos filas sin relación.

Servicio Técnico no puede construirse sobre eso. Un equipo pertenece a un
cliente, y un cliente que sólo existe como texto repetido dentro de pedidos no
puede tener equipos.

### IMPLEMENTADO
- **`Customer`** — el cliente comercial de UNA empresa. Persona o empresa,
  con o sin cuenta, con o sin documento.
- **Vínculo opcional con `User`.** `Customer(company=A, user=X)` y
  `Customer(company=B, user=X)` son dos registros independientes: comparten un
  login y nada más. Ni notas, ni dirección, ni historial.
- **`store/customer_services.py`** — resolución determinista y detección
  conservadora de duplicados.
- **`Order.customer`** nullable, `PROTECT`. Los campos `customer_*` del pedido
  **siguen existiendo**: son el snapshot de la venta, no un duplicado.
- **Vinculación en checkout**, best-effort: si el CRM falla, la venta continúa y
  el pedido conserva su snapshot.
- **`GET/POST /api/admin/customers/`** y **`GET/PATCH /api/admin/customers/{id}/`**
  — `service.customers.view` / `service.customers.manage`.
- **Búsqueda** por nombre, razón social, documento, teléfono y email, con el
  teléfono normalizado antes de comparar.
- **Archivado y reactivación.** No hay DELETE.
- **`DocumentType` compartido** entre `Order` y `Customer`, extraído a nivel de
  módulo. `Order.DocumentType` queda como alias.
- **Pantallas nuevas**: `/admin/customers` y `/admin/customers/{id}`.
- **`service.customers.view/manage` pasan de RESERVED a ACTIVE.**
- Django Admin con `company` bloqueada tras crear y sin borrado.
- 96 tests nuevos (1333 → 1429).

### Cambios de comportamiento
- Un checkout crea o reutiliza un `Customer` de la empresa del pedido. El
  emparejamiento es **sólo determinista**: la cuenta, o el documento que el
  checkout ya validaba.
- El preset `Administrador` de las empresas **nuevas** incluye las dos
  capacidades. El de las empresas **existentes** sólo se amplía si el rol no fue
  modificado nunca — igualdad exacta con el preset anterior.
- El preset `Servicio Técnico` de las empresas nuevas incluye `view`, no
  `manage`.

### Decisiones
- **Cuatro conceptos, cuatro modelos.** `User` es login de plataforma;
  `Membership` es personal interno; `Customer` es cliente de una empresa; los
  campos `customer_*` de `Order` son la venta congelada. Ninguno implica otro.
- **Nadie se fusiona por parecido.** Email, teléfono y nombre **no** son
  identidad: las familias comparten bandeja, las oficinas comparten central y dos
  «Juan Pérez» son dos personas. Se emparejan sólo la cuenta y el documento.
- **Los duplicados posibles se sugieren, no se fusionan.** Un email repetido
  devuelve `possible_duplicates` junto a un alta correcta.
- **Un documento repetido en la misma empresa sí se rechaza** — con 409 y la
  ficha existente adjunta, para que la UI ofrezca abrirla.
- **El pedido ambiguo se queda sin vincular.** Un pedido sin vincular es
  visiblemente incompleto y se arregla a mano; uno mal vinculado parece correcto
  para siempre.
- **Un problema de CRM nunca cuesta una venta** (§49).
- **`Customer` no tiene sucursal.** Un cliente compra en una tienda, deja un
  equipo en otra y lo recoge en una tercera. Las órdenes de servicio sí serán
  branch-scoped; el maestro de clientes no.
- **La auditoría guarda nombres de campo, nunca valores.** Copiar un documento a
  `AdminAuditLog` crearía un segundo almacén del dato que este modelo protege.
- **No hay endpoint público de clientes**, y esa ausencia es la garantía.
- **Merge de clientes: PENDIENTE a propósito.** Tiene que mover pedidos y, en
  fases siguientes, equipos, órdenes de servicio y garantías. Un merge que mueva
  unos y no otros es peor que ninguno.

### Corregido durante la fase
- **El backfill fusionaba mal por su propia regla «gana el pedido más
  reciente».** Un comprador que pagó una vez con su DNI y otra con el RUC de su
  empresa salía reclasificado como empresa con su propio nombre y, peor, soltaba
  su DNI: el siguiente pedido anónimo con ese mismo DNI creaba una **segunda
  ficha de la misma persona**. La regla ordenada fabricaba exactamente el
  duplicado que la migración existe para evitar. Ahora gana el primero, los
  huecos se rellenan sin reescribir, y un documento reclamado no se libera nunca.

### PENDIENTE
- Merge de clientes · múltiples direcciones por cliente · herramienta para
  vincular a mano un pedido histórico ambiguo · `service.customers.view` para los
  técnicos de empresas ya provisionadas (se concede manualmente) · portal del
  cliente · Devices (Fase 5).

---

## Fase SaaS 2E — Series y correlativos internos por empresa

**Estado: IMPLEMENTADO.** Migraciones **0029, 0030**.

### El problema que cierra
El número de una nota de venta se calculaba con `MAX(number) + 1` sobre **toda la
tabla**, y `SalesNote.number` tenía `unique=True` **global**. Dos consecuencias,
ambas visibles para el cliente final:

1. La numeración de una empresa dependía de la actividad de otra. Si la empresa A
   emitía `NV-000001`, la siguiente nota de la empresa B salía `NV-000002`. Su
   primer documento anunciaba que alguien más había vendido antes.
2. Dos empresas no podían tener cada una su `NV-000001`, que es exactamente lo
   que cualquier negocio espera de su propia numeración.

Además, `MAX + 1` no es seguro bajo concurrencia: dos ventas simultáneas leen el
mismo máximo y calculan el mismo número.

### IMPLEMENTADO
- **`InternalSequence`** — el contador como fila propia, con `company`, `branch`
  opcional, `document_type`, `prefix`, `padding`, `next_value` e `is_active`.
  Genérica desde el día uno: `document_type` deja sitio a futuros documentos
  internos sin volver a tocar el esquema.
- **`store/sequences.py`** — el único lugar que reparte números.
  `allocate()` bloquea la fila con `select_for_update()`, lee, formatea e
  incrementa. **Exige estar dentro de una transacción** y lo verifica: sin ella,
  un número podría escapar de una escritura que después falla.
- **`SalesNote.sequence` + `SalesNote.sequence_value`** — a qué serie pertenece y
  qué ordinal ocupa. `number` sigue siendo un **string almacenado**, no derivado.
- **Alcance configurable**: `CompanySettings.sales_note_sequence_scope`, una
  numeración por empresa o una por sucursal. La sucursal se deriva de
  `Order.fulfillment_branch`, nunca del cliente.
- **`GET /api/admin/sequences/`**, **`GET/PATCH /api/admin/sequences/{pk}/`**,
  **`PATCH /api/admin/sequences/scope/`** — `company.view` / `company.manage`.
- **Pantalla `/admin/settings` → «Numeración interna»**: prefijo, padding,
  próximo número y vista previa calculada en el navegador.
- Provisioning crea la serie de empresa; la lista la crea al leer si falta, de
  forma idempotente, para tenants anteriores a esta fase.
- 88 tests nuevos (1245 → 1333). Total: **1333 OK**, 2 omitidos (los dos casos
  de concurrencia real que SQLite no puede probar y que se saltan en voz alta).

### Cambios de comportamiento
- **`SalesNote.number` deja de ser `unique` global.** La unicidad pasa a
  `unique_value_per_sequence`: un ordinal por serie, que es el constraint que se
  quería desde el principio. Dos empresas pueden mostrar `NV-000001`.
- Los prefijos y el padding dejan de ser constantes de módulo
  (`NUMBER_PREFIX` / `NUMBER_PADDING`) y pasan a ser configuración del tenant.
- La numeración de un tenant deja de depender de la actividad de otro.

### Decisiones
- **El número se guarda, no se deriva.** Cambiar el prefijo hoy no puede
  reescribir lo que dice un documento que un cliente ya tiene en la mano.
- **Los huecos se aceptan.** Una nota anulada deja su ordinal consumido.
  Renumerar para cerrar el hueco reasignaría identificadores ya emitidos, que es
  precisamente lo que un correlativo existe para impedir.
- **El contador se congela tras el primer documento.** Antes es útil — un negocio
  que migra desde otro sistema empieza en 5001 —; después, bajarlo reemite y
  subirlo abre un hueco que alguien tendrá que explicar. Se rechaza en voz alta,
  no en silencio.
- **El alcance se congela tras el primer documento.** No es una limitación
  técnica sino de legibilidad: una empresa que emitió `NV-000001..000050` por
  empresa y cambiara a por sucursal volvería a `NV-000001`. Queda PENDIENTE con
  una decisión de negocio detrás, no un `TODO`.
- **Orden de bloqueo: pedido primero, serie después.** Fijo en todo el código,
  porque dos rutas con el orden invertido son un deadlock. Bloquear el pedido
  primero también hace que un segundo intento encuentre la nota ya escrita y
  devuelva sin gastar un número.
- **`/` prohibido en el prefijo**, aunque `NV/2026/` sea una convención
  plausible: es un separador de rutas y el prefijo llega al nombre de un archivo.
- **La migración 0029 no es reversible y lo dice.** Revertirla restauraría el
  unique global, insatisfacible en cuanto dos empresas tengan su `NV-000001`.
  Se niega con una explicación en lugar de fallar con un error de base de datos
  o de «funcionar» renumerando historia.
- **El backfill no escribe nunca la columna `number`.** Infiere prefijo y padding
  del historial **de cada empresa**, y una nota con un número no interpretable
  (`MANUAL-ABC`) conserva su string con `sequence_value` NULL.
- **Numeración interna, no fiscal.** Cada respuesta de la API y cada PDF lo
  repiten. `NV-000001` junto a un logo y un total se parece a un comprobante.

### Corregido durante la fase
- **`PATCH /api/admin/sequences/{pk}/` guardaba el objeto entero**, incluido el
  `next_value` leído al empezar la petición. Con la pantalla de configuración
  abierta mientras la tienda vende, ese guardado retrocedía el contador y el
  siguiente documento reutilizaba un ordinal ya emitido. Ahora escribe sólo los
  campos que cambiaron.

### PENDIENTE
- Cambiar el alcance después de emitir (necesita decidir qué pasa con los
  números ya emitidos) · series por tipo de documento adicional · anulación de
  notas con motivo · numeración fiscal SUNAT, que **no** es esto.

---

## Fase SaaS 3 — Configuración y branding por empresa

**Estado: IMPLEMENTADO.** Migraciones **0027, 0028**.

### El problema que cierra
Hasta esta fase, `email_services.py`, `pdf_services.py` y `sales_note_services.py`
llevaban cada uno su copia de seis constantes de módulo con el nombre, la razón
social, el RUC, la dirección y el teléfono de **una empresa concreta**. Los
clientes de un segundo tenant habrían recibido su email de confirmación y su PDF
de compra con la identidad legal de otra empresa, y sus ventas se habrían
anunciado en la bandeja de esa otra empresa. No es un problema estético.

### IMPLEMENTADO
- **`CompanySettings`** (OneToOne con `Company`): contacto, branding, políticas,
  timezone, currency y email de notificaciones internas.
- **`store/company_settings.py`** — servicio central. Nada más lee
  `CompanySettings` directamente.
- **`Order.company_snapshot`** — identidad comercial congelada al vender. Un PDF
  reimpreso un año después dice lo mismo que el día que se emitió.
- **Emails de pedido tenant-aware**: asunto, cuerpo, firma, garantía y punto de
  retiro salen de la empresa del pedido.
- **PDFs tenant-aware**: recibo de pedido y nota de venta interna, incluida la
  metadata del documento y el nombre del archivo.
- **Notificación interna por empresa** (`order_notification_email`).
- **`GET /api/storefront/config/`** — branding público, tenant resuelto por HOST.
- **`GET/PATCH /api/admin/company-settings/`** — `company.view` / `company.manage`.
- **`PATCH /api/admin/branches/{pk}/`** — cierra la deuda de UI de la Fase 2D.
- **Pantallas nuevas**: `/admin/settings` y `/admin/branches`.
- **Storefront con branding del tenant**: logo, nombre, paleta (variables CSS),
  footer, contacto, metadata y OpenGraph.
- Provisioning crea `CompanySettings` con tema neutro y campos vacíos.
- `PLATFORM_NAME` para los emails de seguridad de cuenta.
- Alerta de configuración incompleta en el dashboard.
- **Test estructural anti-hardcode** sobre los tres servicios comerciales.
- 76 tests nuevos (1160 → 1236).

### Cambios de comportamiento
- **`settings.ORDER_NOTIFICATION_EMAIL` deja de ser destinatario.** Cada empresa
  nombra su dirección; sin ella **no se envía aviso**, y no hay fallback de
  plataforma: esa variable guarda UNA dirección, así que caer en ella anunciaría
  las ventas de un segundo tenant en la bandeja de otro. La migración 0028 copia
  su valor actual a la empresa piloto, de modo que esta instalación no cambia.
- **El storefront ya no se prerenderiza estáticamente.** Su contenido depende del
  host, así que un prerender único sería precisamente el bug: el título de una
  empresa servido en todos los dominios.
- **El catálogo público muestra el nombre y los colores del tenant**, no una
  marca compilada.
- `DEFAULT_FROM_EMAIL` deja de traer un remitente con nombre de empresa por
  defecto.
- Los nombres de archivo de los PDF pasan de `blackdog-pedido-N.pdf` a
  `<slug>-pedido-N.pdf`, construidos desde el slug y filtrados.

### Decisiones
- **Fallback a vacío, nunca a otra empresa.** Un tenant incompleto muestra
  blancos. Un blanco es un estado visible y corregible; la identidad legal
  equivocada en un documento no lo es.
- **Snapshot histórico**: los documentos leen la identidad congelada; el
  storefront lee la configuración viva.
- **Identidad de plataforma vs de tenant**: los emails de verificación y de
  reseteo de contraseña son de la PLATAFORMA — un `User` es global —; los de
  pedido son del tenant.
- **Colores sólo `#RRGGBB`.** No es una restricción estética: estos valores
  entran en una custom property y en un atributo `style`, y cualquier cosa que
  pueda expresar `url(...)`, un esquema o una llave de cierre es una inyección
  CSS con un selector de color delante.
- **WhatsApp se guarda como dígitos**, el enlace se construye. Un `URLField` es
  un sitio donde cabe cualquier URL, y esta se renderiza como enlace en el correo
  de un cliente.
- **`currency` es de solo lectura**: el checkout cobra en la moneda de la
  plataforma. Un desplegable que ofreciera USD mientras Stripe cobra PEN sería
  una mentira con interfaz.
- **La auditoría guarda nombres de campo, nunca valores.**

### Corregido durante la fase
- Los valores de la empresa dejaron de ser constantes y pasaron a ser **entrada
  de un tenant**, así que ahora se escapan en el HTML de los emails. Antes se
  interpolaban en crudo, lo cual era correcto para una constante y sería una
  inyección HTML para un campo de formulario.

### PENDIENTE
- Series y correlativos (2E) · favicon por empresa · contenido de landing por
  empresa · subida de logos (hoy es una URL) · SMTP por tenant · multi-currency ·
  estados de reparación configurables.

---

## Fase SaaS 2D — Inventario multisucursal y acceso por sucursal

**Estado: IMPLEMENTADO.** Migraciones **0024, 0025, 0026**.

### IMPLEMENTADO
- **Acceso por sucursal.** `Membership.branch_access_mode` (`all` / `selected`) +
  `MembershipBranchAccess`. `SELECTED` con cero concesiones = ninguna sucursal, y
  **deniega**. `ALL` incluye automáticamente las sucursales futuras; `SELECTED`
  no, que es justamente su razón de ser.
- **`BranchStock(branch, product)`** — fuente de verdad del stock, con
  `minimum_stock` / `target_stock` por sucursal, `UNIQUE(branch, product)` y
  CHECK constraints.
- **`StockMovement.company` + `.branch`** NOT NULL, más FKs reales a
  `StockTransfer` e `InventoryCount`. `stock_before`/`stock_after` pasan a ser el
  saldo **de esa sucursal**.
- **`Order.fulfillment_branch`** y **`Company.default_inventory_branch`**: el
  checkout decide de qué sucursal sale una venta una sola vez y la estampa.
- **Transferencias** (`StockTransfer` + items): borrador → despacho → recepción,
  con `transfer_out` / `transfer_in`, despacho todo-o-nada e idempotencia en
  ambos bordes.
- **Recuentos físicos** (`InventoryCount` + items) con relectura del stock bajo
  lock al aprobar: la corrección es `físico − teórico al aprobar`, nunca contra la
  foto inicial.
- **Reposición sugerida** por mínimo/objetivo de cada sucursal, con excedente en
  otras sucursales. No crea compras ni transferencias.
- **Dashboard de inventario** por sucursal + sección en el dashboard principal.
- Capabilities `inventory.view` / `adjust` / `reports` pasan de `AVAILABLE` a
  **`ACTIVE`**: gobiernan de verdad sus endpoints.
- Selector de sucursal en las pantallas de inventario, con `scope` en cada
  respuesta indicando qué sucursales cubre la cifra.
- **Panel de acceso por sucursal** en `/admin/users`: modo (todas / seleccionadas),
  concesiones y sucursal predeterminada, sobre la API de membresías. Separado del
  editor de roles a propósito: dónde trabajas y qué puedes hacer son dos
  decisiones distintas.
- `PATCH /api/admin/companies/{pk}/fulfillment-branch/` — el administrador de la
  empresa configura de qué sucursal despacha su tienda online sin depender del
  operador de plataforma. El resto de `Company` (slug, `is_active`) sigue siendo
  exclusivo de plataforma.
- Rutas nuevas: `/admin/inventory/transfers`, `/counts`, `/replenishment`.
- Provisioning de una empresa nueva crea su primera sucursal y la apunta como
  sucursal de despacho.
- 126 tests nuevos (1034 → 1160).

### Cambios de comportamiento
- **`PATCH /api/admin/products/{pk}/` con `inventory` responde 400.** El stock es
  un agregado derivado; cambiarlo exige un movimiento con sucursal, responsable y
  motivo. Ignorarlo en silencio habría dejado un formulario que parece guardar.
- **`POST /api/admin/products/` con `inventory > 0`** genera un movimiento
  `initial_stock` en la sucursal del operador. Si la empresa no tiene ninguna
  sucursal, responde 400: las unidades tienen que estar en algún sitio.
- **`POST /api/admin/products/{pk}/inventory-adjust/`** pasa por el service layer,
  escribe Kardex y está scopeado por empresa y sucursal. Antes escribía
  `Product.inventory` directamente, sin Kardex y **sin scope de tenant**.
- **El catálogo público expone en `inventory` el stock de la sucursal de
  despacho**, no el agregado de la empresa. Mostrar 20 cuando el checkout sólo
  puede entregar 2 es prometer una venta que falla en el último paso.
- **Un superusuario debe indicar `?company=`** en los endpoints de inventario,
  igual que ya ocurría en catálogo y pedidos.
- Una empresa con varias sucursales activas y sin sucursal de despacho **no puede
  cerrar checkout**, y lo dice.

### Decisiones que se negaron a adivinar
- **La migración 0025 falla ruidosamente** si una empresa con stock tiene varias
  sucursales activas y ninguna indicada. Repartir las unidades o tomar la primera
  escribiría una cifra que parece autoritativa y es ficción.
- **Una transferencia despachada no se anula.** Sus unidades ya salieron;
  revertir el estado las repondría en la base mientras viajan.
- **Un faltante tras el pago nunca se cubre desde otra sucursal.** Eso crearía una
  segunda discrepancia donde nadie mira.
- **Un producto sin contar se omite al aprobar un recuento.** «Nadie contó esto»
  no es «no hay ninguno».
- **No se muestra utilidad, margen ni costo de inventario.** Sin precio de compra
  serían restas a un número que nadie proporcionó. El único importe es stock ×
  precio de venta, etiquetado como tal.

### Corregido durante la fase
- `AdminProductInventoryAdjustView` **no tenía scope de tenant**: cualquier
  usuario con rol de inventario podía ajustar el stock de un producto de otra
  empresa conociendo su id. Además escribía `Product.inventory` sin generar
  Kardex.
- El bridge legacy daba contexto de empresa a un operador pre-SaaS pero ninguna
  sucursal, dejándolo con un 403 disfrazado de lista vacía.

### PENDIENTE
- Recepción parcial de transferencias · anular una transferencia despachada ·
  recepción separada por el destino · asignación de un pedido entre varias
  sucursales · reservas multi-almacén · costos y rentabilidad · serial/IMEI ·
  pantalla de Sucursales · alta de membresías y edición de roles desde la UI.

---

## Fase SaaS 2C — Order / Cart / Checkout tenant-aware

**Estado: IMPLEMENTADO.** Migraciones **0021, 0022, 0023**.

### IMPLEMENTADO
- `Order.company` y `Coupon.company` (FK PROTECT). `UNIQUE(company, code)` en cupones.
- Invariante `Order.company == item.product.company`, en `clean()` y en un guard
  a nivel de conjunto para `bulk_create()`.
- Carrito con tenancy lógica (`session_key` + `product.company`) — **sin** modelo
  `Cart` ni columna `CartItem.company`. Un navegador puede tener un carrito por
  storefront a la vez.
- Checkout: tenant del storefront, cupón scopeado, carrito scopeado.
- Webhook: tenant desde `Order.company`; la metadata de Stripe se **contrasta**,
  nunca se impone; mismatch → rechazo registrado.
- Limpieza de carrito post-pago acotada a la empresa del pedido.
- Historial de cliente aislado por storefront; id ajeno → 404.
- Administración de pedidos tenant-scoped con `sales.orders.view/manage`.
- KPIs comerciales reales: ventas de hoy, ticket promedio, ingresos, pendientes,
  por despachar, tendencia de 7 días y pedidos por estado.
- 4 índices `(company, …)` en `Order`; 1 en `Coupon`.
- 61 tests nuevos.

### Cambios de comportamiento
- `/api/orders/` ya no devuelve todos los pedidos a un usuario de staff — era una
  fuga cross-tenant. La administración vive en `/api/admin/orders/`.
- Un superusuario debe indicar `?company=` en la administración de pedidos.

### Corregido durante la fase
- Al retirar `IsAdminRole` del reenvío de email se amplió el acceso a `sales` e
  `inventory`. Restaurado a solo-admin con un conjunto legacy propio.

### PENDIENTE
- `SalesNote.number` sigue siendo global · `StockMovement` sin columna `company`
  · emails sin branding por empresa · utilidad (no hay modelo de costos).

### Sin cambios
Login, JWT, cookies, CSRF, password reset, verificación de email, catálogo
público, reviews, firma del webhook, idempotencia, PDFs.

---

## Fase SaaS 2B.1 — Dashboard visual del Control Interno

**Estado: IMPLEMENTADO.** **Sin migraciones.**

### IMPLEMENTADO
- Dashboard rediseñado: cabecera con contexto, fila de KPIs, zona de análisis,
  mi acceso, avisos, accesos rápidos y cobertura del sistema.
- **5 gráficos, todos tenant-safe**: estado del catálogo (anillo), productos por
  categoría (barras), personal por área (barras), personal por rol (barras) y
  cobertura de módulos (barra apilada).
- Series nuevas en `/api/me/internal-dashboard/`: `catalog.inactive_products`,
  `catalog.products_per_category`, `organization.assignments_per_area`,
  `organization.assignments_per_role`. Mismo gate de capacidad que los totales;
  acotadas a 8 buckets; filas con solo `{label, value}`.
- Gráficos en **SVG propio, sin dependencia nueva** — el proyecto mantiene sus
  tres dependencias de runtime. Magnitud por opacidad, no por tono.
- Accesibilidad: cada gráfico es `role="img"` con `aria-label` **y** tabla oculta
  con las mismas cifras.
- Componentes nuevos: `charts.tsx` (`HorizontalBarChart`, `DonutChart`,
  `StackedBar`) y `dashboard-ui.tsx` (`DashboardHeader`, `DashboardSection`,
  `SummaryStatCard`, `ChartCard`, `AlertsPanel`, `DashboardSkeleton`).
- 16 tests nuevos de aislamiento de las series.

### Paleta
Sin colores nuevos: tokens de `globals.css` (`#080808` / `#111111` / `#1a1a1a`,
escala zinc) más la textura `dot-grid` ya existente.

### PENDIENTE
- KPIs comerciales (ventas, caja, stock, pedidos): bloqueados hasta que `Order` y
  `StockMovement` sean tenant-aware.

### Sin cambios
E-commerce público, checkout, Stripe, webhook, carrito, auth, guards de negocio.

---

## Fase SaaS 2B — Catálogo tenant-aware

**Estado: IMPLEMENTADO.** Migraciones **0018, 0019, 0020**.

### IMPLEMENTADO
- `Category.company` y `Product.company` (FK PROTECT). Slugs únicos **por empresa**.
- Invariante `Product.company == Product.category.company`, validada en modelo y serializer.
- Backfill del catálogo histórico al tenant piloto, identificado por firma (empresa más antigua), no por nombre.
- `resolve_storefront_company()`: host → `DEFAULT_STOREFRONT_COMPANY_SLUG` → empresa activa única. Sin resolución, catálogo vacío. **Sin fallback a "la primera empresa".**
- Catálogo público aislado: querysets que **nacen scopeados** (listado, slug, categoría, búsqueda, reseñas).
- **Límite del carrito**: `/api/cart/add/` solo acepta productos del storefront actual. `Cart.company` NO añadido.
- `products.view` / `products.manage` son autoridad real en los endpoints admin de catálogo.
- Bridge legacy acotado **al tenant piloto**; platform master excluido y obligado a elegir empresa.
- KPIs de catálogo en el dashboard interno, por empresa.
- Django Admin muestra la empresa; `company` bloqueada tras crear.
- 63 tests nuevos.

### Cambio de comportamiento
- `GET /api/admin/products/` sin `?company=` devuelve **403** a un superusuario: con el catálogo tenantizado no existe «todos los productos».
- `?company=` se lee solo del query string, nunca del body.

### PENDIENTE
- Order / Cart / Checkout tenant-aware (2C) · `StockMovement` (2D) · `Product.inventory` por sucursal.

### Sin cambios
Stripe, webhook, `PaymentStatusView`, checkout, emails, PDFs, login, JWT, cookies, CSRF.

---

## Fase SaaS 2A.2 — Control Interno + Dashboard empresarial v1

**Estado: IMPLEMENTADO.** **Sin migraciones** — el dashboard no necesita tabla nueva.

### IMPLEMENTADO
- `GET /api/me/internal-dashboard/[?company=]` — una fotografía segura del contexto
  de empresa: empresa, membresía, sucursal, roles, áreas, capacidades, contadores
  de organización y avisos. Reutiliza `tenancy.resolve_company_for_user()`.
- `InternalControlGuard` — entrar al control interno = Membership activa en Company
  activa, o platform master. Ya no es `UserProfile.role == "admin"`. Con fallback
  legacy para operadores sin Membership.
- Shell nuevo: **sidebar + topbar** en lugar de pestañas horizontales; drawer en móvil.
- `internal-modules.ts` — registro único de módulos (sidebar, accesos rápidos, mapa).
  Metadata UX, nunca autorización. Sin enlaces a módulos inexistentes.
- Selector de empresa; un master sin elegir ve «Selecciona una empresa», nunca un
  tenant arbitrario.
- Iconografía SVG inline — **sin dependencia nueva**.
- 27 tests nuevos de seguridad del endpoint.

### Eliminado
- `AdminNav.tsx` — la navegación horizontal que sustituye el sidebar. Nadie la importaba.

### PENDIENTE
- KPIs comerciales (necesitan Product/Order/Inventory tenant-aware).
- Pantallas de Empresa / Sucursales / Áreas / Roles (APIs listas desde 2A.1).
- Selector multisucursal (bloqueado por `Branch access model`).
- UI de Platform Control.

### Sin cambios
Guards legacy de negocio (`StaffGuard`/`AdminGuard` en products/orders/inventory/
audit), `lib/auth.ts`, checkout, Stripe, webhook, emails, PDFs, e-commerce público.

---

## Usuarios demo de desarrollo — TEMPORAL

**Estado: IMPLEMENTADO / TEMPORAL.** Herramienta de desarrollo, no parte del
producto. **Sin migraciones.**

### IMPLEMENTADO
- `python manage.py seed_demo_users --company-slug <slug>` crea 6 cuentas de
  prueba (`dev_customer`, `dev_sales`, `dev_inventory`, `dev_technician`,
  `dev_admin`, `dev_master`) con contraseña `Demo123!`.
- `--purge` las elimina.
- Reutiliza `provision_company_access_defaults()`; no duplica presets ni
  hardcodea ninguna empresa — funciona con cualquier `--company-slug`.
- Bloque "Accesos de desarrollo" en `/auth`, que **solo rellena** el formulario.
- 29 tests.

### Garantías de seguridad
- Falla con `DEBUG=False`, sin flag de override (test que introspecciona el parser).
- Sin bypass de auth: login real, JWT en cookie HttpOnly, CSRF y permisos normales.
- No se apropia de cuentas reales (firma por email `@example.invalid`); aborta si
  el username está ocupado por otra identidad, y `--purge` la omite.
- Sin migración, sin signal, sin auto-creación al arrancar.
- El bloque de `/auth` no se renderiza fuera de `NODE_ENV === "development"`.

### PENDIENTE
- Eliminar estas cuentas y el bloque de `/auth` antes de producción.

---

## Fase SaaS 2A.1 — Cierre arquitectónico

**Estado: IMPLEMENTADO.** Sin migraciones nuevas: el provisioning no requiere
cambio de esquema. `0016` y `0017` sin tocar.

### Corregido
- **Definición de portal externo.** Se había documentado «portal externo = `User`
  sin Membership». Es incorrecto: tener Membership no quita el acceso al
  e-commerce. Corregido en `models.py`, `tests.py`, `README.md` y
  `docs/saas-multiempresa.md`, con test en ambas direcciones.

### IMPLEMENTADO
- `store/company_provisioning.py` — `provision_company_access_defaults(company)`:
  única fuente en tiempo de ejecución de áreas y roles preset. Idempotente,
  neutral (test que escanea el archivo entero), no sobrescribe ediciones del
  operador, no asigna roles ni toca identidad.
- Conectado a `POST /api/admin/companies/` en una sola transacción con la
  creación, y a `CompanyAdmin.save_model()` para el Django Admin.
- Auditoría de `company_created` ampliada con el resumen del provisioning.
- Mapa oficial del CONTROL INTERNO documentado con el estado **real** de cada
  módulo (no el diseñado).
- Deuda `PENDIENTE — Branch access model` documentada: `Membership.branch` es
  single-valued y no expresa acceso a varias sucursales. Bloquea el inventario
  multisucursal.
- `Dashboard interno avanzado` marcado PENDIENTE con su razón.
- 21 tests nuevos: provisioning (idempotencia, no sobrescritura, aislamiento,
  neutralidad, no toca identidad, Django Admin) y separación de superficies.

### PENDIENTE
- Branch access model · Dashboard interno · módulos del Control Interno ·
  Membership Invitation Flow · tenantización del dominio comercial.

### Sin cambios
Frontend, catálogo, carrito, checkout, Stripe, webhook, `PaymentStatusView`,
emails, PDFs, inventario legacy, `SalesNote`, login, JWT, cookies, CSRF,
migraciones 0001–0017.

---

## Fase SaaS 2A.1 — Áreas, roles y permisos configurables por empresa

**Estado: IMPLEMENTADO** (la infraestructura de acceso interno; el dominio
comercial sigue en RBAC legacy hasta que sus datos estén tenantizados).

**Migraciones: 0016 y 0017.**

### Decisión arquitectónica
`Portal externo ≠ Control interno ≠ Platform control`, sobre **una sola
identidad** `User`. Sin `CustomerUser`/`StaffUser`/`MasterUser`.

### IMPLEMENTADO
- `store/capabilities.py` — catálogo de capacidades **en código**, propiedad de la plataforma (18 asignables, 10 reservadas). Alternativa `PermissionDefinition` evaluada y descartada; razones documentadas.
- `CompanyArea` — áreas configurables por empresa. **Las áreas no otorgan permisos.**
- `CompanyRole` — roles configurables por empresa, con su conjunto de capacidades.
- `MembershipRoleAssignment` — varios roles (y áreas) por membresía, sin duplicar la Membership.
- `resolve_capabilities()` — resolución **exclusiva**: platform master → roles propios → fallback legacy. Un rol personalizado restringe de verdad.
- Anti-escalada: un admin de empresa solo delega capacidades que él tiene.
- Endpoints `/api/admin/{capabilities,areas,roles,membership-role-assignments}/` y `/api/me/company-access/`, todos tenant-scoped.
- Auditoría: `area_created/updated`, `company_role_created/updated`, `role_permissions_updated`, `role_assignment_created/updated/disabled`.
- Presets de áreas y roles sembrados **por empresa**, sin asignarlos a nadie.
- 74 tests nuevos: catálogo, invariantes de modelo, resolución, API cross-tenant, escalada, auditoría y regresión.

### PARCIAL
- Control interno: las capacidades `available` son asignables pero aún no las consulta ningún endpoint comercial.

### PENDIENTE
- Conectar `products.*`, `inventory.*`, `sales.*` a endpoints reales (Fase 2B/2C).
- Módulo y portal de servicio técnico (10 capacidades reservadas).
- Membership Invitation Flow.

### Sin cambios
`authentication.py`, JWT, cookies, CSRF, `views.py`, `admin_views.py`, `inventory_views.py`, `tenant_views.py`, `email_services.py`, `pdf_services.py`, `sales_note_services.py`, `inventory_services.py`, migraciones 0001–0015, y todo el frontend.

---

## Fase SaaS 2A — RBAC tenant-aware y contexto empresarial

**Estado: IMPLEMENTADO** (la infraestructura RBAC empresarial; los endpoints
comerciales siguen en RBAC legacy hasta que sus datos estén tenantizados).

**Sin migraciones.** Esta fase no cambia el esquema.

### IMPLEMENTADO
- `tenancy.COMPANY_CAPABILITIES` — matriz de capacidades empresariales, fuente única de verdad.
- Helpers `get_company_role`, `has_company_role`, `has_company_capability`, `can_manage_company`, `can_manage_company_memberships/_inventory/_sales/_technical_service`, `can_grant_company_role`, `holds_any_capability`.
- `CompanyContext` + `build_company_context()`: empresa, membresía, rol y autoridad de plataforma en una estructura, resueltos sin confiar en el cliente.
- Clases DRF `CanManageCompanyMemberships`, `CanManageCompanySettings`, `CanManageCompanyInventory`, `CanManageCompanySales`, `CanManageCompanyTechnicalService` — delegan en la matriz, no redeclaran roles.
- Separación estricta PLATFORM / COMPANY / LEGACY con tests explícitos.
- Un admin de empresa ya no puede asignar el rol legacy `superadmin` (403); un platform admin sí.
- `POST /api/admin/memberships/` deja de ser oráculo de ids de usuario: misma respuesta para "no existe" y "ya es miembro".
- `sales`/`inventory`/`technician` reciben 403 (no 404) al intentar administrar membresías de su propia empresa.
- 63 tests nuevos: matriz de capacidades, niveles de autoridad, multi-empresa, `CompanyContext`, escalada de privilegios, auditoría y regresión del RBAC legacy.

### PARCIAL
- Tenant resolution: sin cambios respecto a Fase 1; sigue sin aplicarse al e-commerce.

### PENDIENTE
- Conectar `CanManageCompanyInventory` / `Sales` / `TechnicalService` a endpoints reales (requiere Fase 2B/2C).
- **Membership Invitation Flow** — onboarding con consentimiento del destinatario.
- Normalizar `UserProfile.role == "superadmin"` frente a `User.is_superuser`.

### Sin cambios
`authentication.py`, JWT, cookies, CSRF, `AccountToken`, password reset, verificación de email, `views.py`, `admin_views.py`, `inventory_views.py`, `email_services.py`, `pdf_services.py`, `sales_note_services.py`, `inventory_services.py`, migraciones, y todo el frontend.

---

## Fase SaaS 1 — Fundación multiempresa

**Estado: PARCIAL** (la fundación está completa; la tenantización de los modelos
de negocio es deliberadamente posterior).

```
Autenticación única                      IMPLEMENTADO
E-commerce / portal externo              IMPLEMENTADO
Control interno — shell v1               IMPLEMENTADO
Dashboard empresarial v1                 IMPLEMENTADO
Sidebar capability-aware                 IMPLEMENTADO
Selector de empresa                      IMPLEMENTADO
Category tenant-aware                    IMPLEMENTADO
Product tenant-aware                     IMPLEMENTADO
Public catalog isolation                 IMPLEMENTADO
Dashboard catalog KPIs                   IMPLEMENTADO
Dashboard visual / analytics UI          IMPLEMENTADO
Gráficos tenant-safe                     IMPLEMENTADO
KPIs comerciales reales                  PENDIENTE
Coupon tenant-aware                      IMPLEMENTADO
Cart tenant-aware                        IMPLEMENTADO
Order tenant-aware                       IMPLEMENTADO
Checkout tenant-aware                    IMPLEMENTADO
Stripe tenant-safe                       IMPLEMENTADO
Customer order isolation                 IMPLEMENTADO
Admin order isolation                    IMPLEMENTADO
Sales capabilities                       IMPLEMENTADO
Dashboard sales KPIs                     IMPLEMENTADO
Dashboard sales charts                   IMPLEMENTADO
StockMovement explicit tenancy           PENDIENTE 2D
Profitability                            PENDIENTE (sin modelo de costos)
Inventory company isolation              PARCIAL
Inventory branch isolation               PENDIENTE 2D
Dashboard sales KPIs                     PENDIENTE
Control interno — módulos completos      PENDIENTE
Selector multisucursal                   PENDIENTE
Platform MASTER                          IMPLEMENTADO
Membership                               IMPLEMENTADO
Áreas personalizadas                     IMPLEMENTADO
Roles personalizados                     IMPLEMENTADO
Capabilities                             IMPLEMENTADO
Provisioning de nuevas empresas          IMPLEMENTADO
Demo users de desarrollo                 IMPLEMENTADO / TEMPORAL
Platform MASTER — UI                     PENDIENTE
Legacy RBAC fallback                     IMPLEMENTADO / TRANSICIÓN
Tenant resolution                        PARCIAL
Branch access multisucursal              PENDIENTE
Product tenant-aware                     PENDIENTE
Order/Cart/Checkout tenant-aware         PENDIENTE
Inventory tenant-aware                   PENDIENTE
Servicio técnico                         PENDIENTE
Dashboard interno avanzado               PENDIENTE
Membership Invitation Flow               PENDIENTE
Branding                                 PENDIENTE
IMEI/Serial                              PENDIENTE
```

### IMPLEMENTADO
- Modelos `Company`, `Branch` y `Membership` (migración `0014`).
- Empresa y sucursal piloto + backfill de membresías de staff (migración `0015`, data migration reversible).
- `store/tenancy.py` — resolución de tenant que nunca confía en datos del cliente.
- Permisos `IsPlatformAdmin` y `HasCompanyMembership` (aditivos).
- Endpoints `/api/admin/companies|branches|memberships/` y `/api/me/memberships/` con scoping por membresía.
- `AdminAuditLog.company` nullable, sin reescribir el histórico.
- Registro seguro de los tres modelos en Django Admin (sin borrado de empresas, FKs bloqueadas tras la creación).
- 67 tests nuevos: modelos, resolución de tenant, aislamiento entre empresas y regresión del e-commerce.

### PARCIAL
- Resolución de tenant por host (`resolve_company_from_host`): implementada y testeada, **sin uso en endpoints públicos**.

### PENDIENTE
- Tenantizar `Product`, `Category`, `Order`, `CartItem`, `StockMovement`, `SalesNote`, `Coupon`, `Review`.
- RBAC basado en `Membership` (hoy sigue gobernando `UserProfile.role`).
- Branding por empresa: `_STORE_NAME`/`_STORE_RUC`/`_STORE_ADDRESS` siguen siendo constantes de módulo.
- Correlativo de `SalesNote` único por empresa (hoy es global).
- Constraints `NOT NULL` en los FKs de tenant de los modelos de negocio.
- Bootstrap neutral: `0015` crea el tenant piloto; una instalación nueva para un tercero no debe adquirirlo en silencio.

### PROPUESTA
- UI administrativa `/admin/companies` en el frontend.

### Sin cambios
`email_services.py`, `pdf_services.py`, `sales_note_services.py`,
`inventory_services.py`, carrito, checkout, webhook de Stripe,
`PaymentStatusView`, autenticación y todo el frontend.

Detalle: [docs/saas-multiempresa.md](docs/saas-multiempresa.md)

---

## Fase 6.0 — Inventario avanzado y notas de venta internas

**Estado: IMPLEMENTADO**

- `StockMovement` (Kardex, 10 tipos, `stock_before`/`stock_after`) y `SalesNote` (migración `0013`).
- `store/inventory_services.py`: todo cambio de stock en una transacción con `select_for_update()`; stock nunca negativo.
- Salidas por venta idempotentes por `(orden, producto)`: un webhook reintentado no descuenta dos veces.
- Reportes: bajo/alto stock, agotados, más vendidos, valor de inventario, productos sin movimiento.
- Notas de venta internas `NV-000001` con PDF y disclaimer de no-SUNAT.
- Roles separados: `inventory` mueve stock, `sales` emite documentos.
- Páginas `/admin/inventory`, `/admin/inventory/movements`, `/admin/inventory/reports`, `/admin/products/{id}/stock-card`.

Detalle: [docs/inventario-y-notas-de-venta.md](docs/inventario-y-notas-de-venta.md)

---

## Fases anteriores

Reconstruidas del historial de git. El detalle vive en la cabecera de
`backend/store/tests.py`, que documenta fase por fase qué tests cubren qué.

| Fase | Entrega | Estado |
|---|---|---|
| 4.3 | Reenvío manual de email de confirmación | IMPLEMENTADO |
| 4.2 | PDF interno de pedido (constancia de compra) | IMPLEMENTADO |
| 4.1 | Emails transaccionales idempotentes | IMPLEMENTADO |
| 4.0 | Campos comerciales de checkout (documento, entrega, comprobante) | IMPLEMENTADO |
| 3.3 | Panel de órdenes admin + estado de despacho | IMPLEMENTADO |
| 3.2 | CRUD de productos, ajuste de inventario, categorías | IMPLEMENTADO |
| 3.0-3.1 | RBAC, auditoría admin, paginación y rate limits | IMPLEMENTADO |
| 2.1-2.3 | Cookies HttpOnly + CSRF, verificación de email, reset de contraseña | IMPLEMENTADO |
| 2.0 | Endurecimiento de registro/login, validación de carrito, permisos de reseñas | IMPLEMENTADO |
| 1 | Checkout, Stripe, webhook idempotente, descuento de inventario | IMPLEMENTADO |
| 0.1 | Catálogo, carrito, cupones | IMPLEMENTADO |
