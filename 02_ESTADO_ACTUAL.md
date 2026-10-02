# Estado actual

Este archivo no existía en el baseline. Se incorpora como entrada resumida a la
documentación real, sin reemplazar su historial.

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
  Los bloques de servicio sólo si la tienda publicó servicios. Sin copy que nombre
  una marca de equipos.
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
de tema, panel interno y `backend/` (subárbol idéntico a `master`, `736690e`;
vale su medición de 4643 pruebas, 0 fallos, 3 omitidas). Sin migraciones.

El defecto «identificador repetido en las líneas del carrito» no existe en
`master`: lo corrigió la reconciliación anterior. No se tocó.

Validación sobre `7f49168`: frontend 483 pruebas en 47 suites, OK; typecheck OK;
lint 0 errores y 25 advertencias; build OK (52 páginas); Playwright 143 de 143,
sin fallos, omitidas ni reintentos, 8,6 min.

Queda fuera, registrado:

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
