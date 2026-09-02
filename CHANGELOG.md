# Changelog — Black Dog Store

Formato: cada entrada lista lo entregado y su estado
(`IMPLEMENTADO` · `PARCIAL` · `PENDIENTE` · `PROPUESTA` · `OBSOLETO`).

Este archivo se creó en la Fase SaaS 1. Las fases anteriores se reconstruyen a
partir del historial de git y de la documentación del repositorio; no se inventa
información que no esté respaldada por código o commits.

---

## M10 / BR-005C — Ejecución de la reparación y consumo de repuestos

**Estado: IMPLEMENTADO.** Migraciones **0043** (esquema), **0044** (estados) y
**0045** (capability). Primera integración Servicio ↔ Inventario.

### Lo que cierra

`APPROVED → IN_REPAIR → REPAIRED`, con `WAITING_PARTS` para la pausa real que
existe cuando una pieza no ha llegado. Un técnico abre el trabajo, registra lo
que hizo, consume los repuestos que el cliente aprobó, y termina.

### Dos modelos, y por qué no uno

`RepairOrder` es el ticket: quién trajo qué, en qué punto de su vida está, qué se
le dijo al cliente. `RepairExecution` es el banco de trabajo: cuándo empezó
alguien, qué hizo de verdad, cuándo paró. Juntarlos habría ahorrado medio día y
habría dejado sin respuesta «¿cuánto tarda un cambio de pantalla?» — el reloj del
ticket arranca en el mostrador, horas o días antes de que un técnico toque nada.

`PartUsage` responde «qué pieza usó esta reparación». `StockMovement` responde
«qué pasó físicamente con el inventario». Están enlazados uno a uno y ninguno
sustituye al otro: una usage sin movimiento sería una pieza que no dejó hueco, y
un movimiento sin usage sería un hueco que nadie sabe explicar.

### El orden de bloqueo, que es todo el riesgo de esta fase

Es la primera operación que cruza dos agregados con disciplinas propias.
Equivocarse no rompe un test: bloquea una caja contra un banco de trabajo un
sábado por la tarde.

No hubo nada que reconciliar, solo que obedecer. `service_services` bloquea el
DOCUMENTO primero (`RepairOrder`, luego `RepairQuote`). `inventory_services`
también (`StockTransfer`, `InventoryCount`) y después las filas de `BranchStock`
en orden `(branch_id, product_id)` vía `_locked_branch_stocks`, y deliberadamente
**nunca** `Product` — bloquear el artículo serializaría todas las sucursales de
una cadena entre sí.

    RepairOrder → RepairExecution → PartUsage → BranchStock

`BranchStock` va siempre al final y siempre a través de
`inventory_services.create_stock_movement`, que es la única función del
repositorio que escribe stock. M10 no toca `BranchStock.quantity` ni
`Product.inventory`: ese agregado **no tiene check constraint**, así que una
segunda implementación equivocada lo corrompería sin que nada saltara.

Un test estructural comprueba las posiciones relativas en el AST, porque los dos
tests concurrentes se saltan en SQLite y una garantía que solo existe en un test
que no corre no es una garantía.

### `service_exit` llevaba dos años declarado y sin usar

Existía desde la migración 0013, excluido de `MANUAL_TYPES`, sin un solo camino
de código que lo creara. M10 es ese camino. Se añade su espejo, `service_return`,
para el reverso: `return_entry` habría funcionado y habría perdido el origen — una
línea de Kardex que dice solo «devolución» no distingue el cambio de opinión de un
cliente de un técnico corrigiendo una pieza mal escaneada.

Ninguno de los dos entra en `MANUAL_TYPES`. Un `service_exit` escrito a mano sería
una línea de Kardex que reclama una reparación que no se puede encontrar.

### Idempotencia: columnas, no convención

La misma forma que ya usan la venta POS y el checkout nativo: clave acuñada por
el cliente más huella SHA-256 de la petición, guardadas como columnas de la fila,
con un `UniqueConstraint` parcial que decide en la base de datos. Reservar una
pieza es un hecho físico y un timeout no puede producir dos.

Misma clave + misma petición → se devuelve la fila original. Misma clave + otra
petición → 409. El chequeo va **antes** que cualquier validación de negocio: un
reintento se responde desde la clave, no se vuelve a juzgar, porque el cupo que
llenó está lleno precisamente por su culpa.

### Reversión es compensación

`reverse_part_usage` escribe un movimiento contrario y marca la fila. No borra
nada y no edita una cantidad histórica. Es idempotente: revertir dos veces no
devuelve el stock dos veces.

Y **no** sirve para devolver a la estantería una batería ya instalada. Una vez
finalizado el trabajo la usage queda congelada; las devoluciones posteriores a la
finalización son una necesidad real y necesitan su propia fase, con una
inspección física que hoy no existe.

### `service.repair.manage` no es `inventory.adjust`

Consumir una pieza aquí es un paso de una reparación cuya cotización aprobó un
cliente, desde la sucursal de esa reparación, contra una línea que alguien
cotizó. Nada de eso es autoridad para ajustar una estantería, transferir entre
tiendas o hacer un recuento. Dos tests lo fijan en ambas direcciones.

### Lo que el cliente sigue sin ver

`work_performed`, notas internas, piezas consumidas, stock, coste, identidad del
técnico, sucursal. El serializer de cliente es una allowlist cerrada y M10 no le
añadió un solo campo; un test estructural comprueba su lista exacta.

### Un 500 preexistente corregido de paso

`GET` sobre `/api/v1/customer/<slug>/repairs/<pk>/quotes/<id>/decision/` heredaba
el `get()` de la vista padre, cuya firma no acepta `quote_id`: TypeError sin
convertir, 500 en vez de 405. Se declara `http_method_names = ['post']`.

### Deuda declarada

Reserva de stock al cotizar: **no existe y no se inventó**. El stock cambia
cuando la pieza se usa, nunca antes. Control de calidad, entrega, pago del
servicio, garantía, evidencias fotográficas (DEC-016, sin proveedor) y
seguimiento público BR-008 siguen PENDIENTES. `REPAIRED` no es terminal.

---

## Fase 0.3 / P0-E — Integridad de cantidades: carrito, pedido e inventario

**Estado: IMPLEMENTADO.** Migraciones **0041** (consolidación) y **0042** (constraints).

### Seis cobradas, tres descontadas

Medido, no deducido. Un pedido con dos líneas del mismo producto —3 y 3— dejaba
el stock en 10 → **7**, con **un solo** movimiento de venta. Seis unidades salían
de la tienda y los libros registraban la mitad.

La causa **no** es un fallo del servicio de inventario. Su clave de idempotencia
es `(order, product)`, que es exactamente lo que impide que un webhook de Stripe
repetido descuente dos veces, y **no puede distinguir** un replay de un pedido que
de verdad lleva el mismo artículo en dos líneas: escribe la salida de la primera y
se salta la segunda.

Así que la guarda se queda como está —debilitarla reabriría el doble descuento— y
lo que se vuelve imposible es la línea duplicada.

### Tres huecos encadenados

1. **`CartItem` sin unicidad** y un writer que leía y luego escribía: dos altas
   simultáneas no encontraban fila, ambas creaban una.
2. **El checkout web** convertía cada fila del carrito en su propia línea y
   validaba el stock **línea a línea**: `3 ≤ 5` dos veces sobre un estante de 5.
3. La guarda anterior convertía el duplicado en pérdida de inventario.

Ninguno es un fallo por separado.

### Lo que cambia

- **`UNIQUE(session_key, product)`** en `CartItem` y **`UNIQUE(order, product)`**
  en `OrderItem`. La segunda no es una regla nueva: el POS ya fusionaba líneas
  repetidas *«por corrección, no por limpieza»* según su propio código, y el
  checkout nativo ya sumaba slugs repetidos. Sólo el web no lo hacía.
- **`merge_lines()`** compartido, aplicado en `validate_lines_and_subtotal` y en
  `create_pending_order`. Fusionar algo ya fusionado no hace nada, así que
  hacerlo en ambos sitios no cuesta y elimina la necesidad de acordarse.
- **Stock validado sobre el total** por producto, no por línea.
- **Alta al carrito concurrency-safe**: `get_or_create` respaldado por la
  constraint, incremento con `F()` calculado por la base de datos, y la carrera
  perdida se atiende como un alta normal — nunca un 500.

### Consolidación que se detiene antes de inventar

La migración 0041 suma cantidades duplicadas. Para pedidos **comprueba los
precios**: si dos líneas del mismo producto discrepan, **se detiene**. Sumarlas
bajo uno de los dos precios cambiaría en silencio lo que se cobró en un pedido ya
emitido, dentro de una migración que nadie está mirando. Negarse es el daño menor.

### Lo que no queda probado aquí

La suite local usa SQLite, que **serializa** los escritores y responde «database
table is locked» en vez de intercalarlos. Los tests con hilos y barrera existen y
se **omiten explícitamente** en SQLite; correrán el día que la suite apunte a
PostgreSQL. Lo que sí queda garantizado con independencia del motor son las
constraints y el incremento del lado de la base de datos, y eso está probado.

---

## Fase 0.3 / P0-D — Reseñas tenant-safe

**Estado: IMPLEMENTADO.** Sin migraciones.

### El id era la autoridad

La **lectura** de reseñas ya estaba acotada (`product__in=storefront_products(request)`).
La **escritura** no: `ReviewSerializer` es un `ModelSerializer` con `product`
entre sus campos escribibles, y DRF resuelve una relación escribible contra el
queryset **completo** del modelo. Un cliente autenticado navegando la tienda A
ponía el id de un producto de la tienda B en el cuerpo y la reseña aterrizaba en
el catálogo de B — visible para sus clientes, contada en su calificación, escrita
por alguien que nunca vio esa tienda.

El id que envía el cliente es un **selector**. La autoridad sale del storefront
que resuelve el servidor.

El contrato externo no cambia: `product` sigue siendo un id. Lo que cambia es el
conjunto que ese id puede nombrar.

### Falla cerrado

Sin `request` en el contexto no hay storefront, y el queryset queda **vacío** en
vez de global. Quien olvide pasar el contexto obtiene un serializer que no puede
escribir en ninguna parte, en lugar de uno que puede escribir en cualquiera.

### Producto ajeno = producto inexistente

Un mismo mensaje —«Producto no disponible en esta tienda.»— para los dos casos.
Dos respuestas distintas dejarían mapear el catálogo ajeno mirando cuáles se
rechazan de forma diferente. Un producto **inactivo** recibe el mismo trato, y no
es una regla inventada: `company_storefront_products` filtra `is_active=True`, así
que un artículo retirado no está en la tienda.

### Suplantación de autor

`author_name` era texto libre en un endpoint autenticado: cualquiera podía
publicar con el nombre de soporte de la propia tienda, o como otro cliente.
Ahora es de sólo
lectura y el servidor lo deriva de la cuenta. **La columna se conserva**: las
reseñas anteriores a la exigencia de login llevan nombre y no llevan usuario, y
ese nombre es su única atribución.

### El formulario nunca había funcionado

El POST del navegador usaba `fetch` plano —sin cookie ni CSRF— contra un endpoint
que exige sesión, así que **siempre respondía 401**. Eso explica que el nombre
fuera texto libre: el formulario es anterior a que se exigiera identificarse.
Ahora usa `fetchWithAuth`, ya no envía el nombre y muestra «Inicia sesión para
publicar una reseña» cuando corresponde.

### Lo que NO se hizo, y por qué

- **Sin `Review.company`**: el inquilino llega por `Product`. Una segunda columna
  sería una segunda fuente de verdad que puede divergir.
- **Sin constraint `(user, product)`**: no existe esa regla en código, tests,
  frontend ni documentación, y no hay datos duplicados. Queda **PROPUESTA**; es
  una decisión de producto, no de seguridad.
- **Sin compra verificada**: **PROPUESTA**, por lo mismo.

---

## Fase 0.3 / P0-C — Aislamiento administrativo legacy y regresión multitenant

**Estado: IMPLEMENTADO.** Sin migraciones.

### «Administrador» de una empresa significaba administrador de la plataforma

`UserProfile.role` es una columna global de una sola fila por usuario. Se diseñó
cuando había una tienda, así que **no contiene ninguna empresa** y no puede
adquirirla leyéndola con más cuidado. Tres endpoints seguían autorizando sobre
ella, encima de querysets sin filtrar:

| Endpoint | Antes | Consecuencia |
|---|---|---|
| `GET /admin/users/` | `IsAdminRole` + `User.objects` | Un admin de UNA empresa listaba **todos los usuarios de la plataforma**, con sus emails |
| `PATCH /admin/users/{pk}/role/` | `IsSuperAdminRole` + `User.objects` | **Escalada de privilegios** |
| `GET /admin/audit-logs/` | `IsAdminRole` + `AdminAuditLog.objects` | Un admin leía **el rastro de auditoría de todos los inquilinos** |

### La escalada

`IsSuperAdminRole` se satisface con `UserProfile.role == 'superadmin'` — un valor
que **ese mismo endpoint escribe**. Un superadmin legacy que no fuera superusuario
de Django podía concederse ese rol a sí mismo o a cualquiera, en toda la
plataforma. La escalera era el propio endpoint.

Ahora exige **`IsPlatformAdmin`**: cambiar un rol global es una operación de
plataforma se mire por donde se mire. La autoridad por empresa se concede con
`Membership`, en `/admin/memberships/`, que ya existía.

### Lo demás nace del queryset

Listado de personas y auditoría se construyen **hacia abajo** desde las empresas
del llamante: un usuario de otro inquilino no se filtra del resultado, **nunca
está en él**. La autoridad es la capacidad `memberships.view` dentro de una
empresa, no una etiqueta global. Los administradores de plataforma siguen viendo
todo.

Las entradas de auditoría **sin empresa** (anteriores a la multiempresa, o de
plataforma) quedan sólo para administradores de plataforma: un nulo no es permiso.

No se retira `UserProfile.role` — sigue clasificado **OBSOLETO / TRANSICIÓN**.
Esta subfase le impide cruzar la frontera multiempresa, no lo elimina.

### M8 revisado, no reescrito

El núcleo de servicio técnico ya estaba bien: `Device.clean()` y
`RepairOrder.clean()` validan empresa de cliente, sucursal y equipo y se invocan
desde `save()`; las vistas internas parten de `filter(company=..., branch__in=
visible_branches(...))` y responden 404; la propiedad del cliente es una FK
(`Customer.user`), nunca el email; el serializer de cliente documenta cada
omisión. El hueco era que **nadie probaba mover una orden ajena** por su máquina
de estados — la parte que cambia registros de otro. Añadido.

---

## Servicio técnico — diagnóstico, cotización y aprobación (M9 / BR-005B)

**Estado: IMPLEMENTADO (núcleo comercial).** Migraciones **0038–0040**. Aditiva.

### Entregado

- `RepairDiagnostic` — versionado, congelado al publicar
- `RepairQuote` + `RepairQuoteItem` — revisiones, totales del servidor
- `RepairQuoteDecision` — una por cotización, garantizada por la base
- Estados `approved` y `rejected`
- `publish_quote()` y `record_quote_decision()` — las dos operaciones de evento
- 8 rutas internas y 2 de cliente
- 76 tests nuevos

### La invariante de M9

`waiting_approval` dejó de significar «alguien pulsó un botón». Ahora significa
«existe una cotización concreta, congelada, publicada y vigente esperando una
decisión del cliente», y **solo publicarla puede producir ese estado**.

`approved` y `rejected` son el resultado registrado de que un cliente decida.
El endpoint genérico de transición los rechaza los tres.

### Reglas

- La cotización enviada es evidencia: ni ella ni sus líneas se pueden editar.
  Un cambio de opinión es una **revisión nueva**.
- El servidor calcula `line_total`, `subtotal` y `total`. El interno elige
  cantidad y precio; la aritmética no es suya.
- La moneda se congela desde `CompanySettings`, nunca desde el cliente.
- Una decisión por cotización, con `OneToOne` en la base. Repetir la misma
  respuesta es idempotente; la contraria devuelve **409**.
- Una cotización vencida se puede seguir viendo pero no aprobar.
- El `channel` y la IP los fija el servidor — la IP por `client_ip.get_client_ip()`,
  respetando `TRUSTED_PROXY_COUNT` (P0-B).

### Impuestos: cero, y documentado

Esta plataforma **no modela impuestos en ninguna parte** — sin tasa, sin régimen,
sin configuración. Inventar un 18% porque el piloto es peruano sería escribir la
ley de un país en un esquema SaaS. La columna existe para que una cotización ya
enviada conserve lo que llevaba cuando el impuesto llegue; nada lo calcula.

### Lo que NO se implementó

Ejecución, repuestos, reserva de stock, control de calidad, entrega, garantía,
evidencias, tracking público y pagos de reparación. Cotizar una pieza **no la
reserva**.

### Capabilities

`service.diagnostic.manage` → **ACTIVE**. Siguen RESERVED `service.repair.manage`
y `service.quality.manage`.

### No tocado

`Order`, carrito, checkout, Stripe, POS, inventario, `/api/admin/`, P0-B.

---
## Fase 0.3 / P0-B — Trusted proxy, IP del cliente y rate limiting

**Estado: PARCIAL — REQUIERE INFRA.** Sin migraciones.

### El límite de peticiones era decorativo

DRF 3.17 con `NUM_PROXIES` sin configurar usa **el header `X-Forwarded-For`
entero** como identidad del throttle. El proxy de Next reenviaba ese header tal
cual desde el navegador. Resultado: un valor distinto por petición era un cubo de
rate limit nuevo por petición, y el límite de 5 logins/minuto no llegaba a
dispararse nunca.

Verificado con tests que **fallan sin el arreglo** (5 fallos) y pasan con él, y en
vivo contra el servidor.

### La auditoría registraba la IP que el sujeto eligiera

`AdminAuditLog.log()` tomaba `xff.split(',')[0]` — la entrada más a la izquierda,
la que el llamante controla por completo. Cualquiera podía decidir bajo qué IP
quedaban registradas sus propias acciones administrativas. Un registro que anota
la dirección que eligió el investigado es peor que uno que no anota ninguna,
porque alguien acabará creyéndoselo.

### Una sola autoridad

Nuevo `store/client_ip.py` con `get_client_ip()`, y una única variable
`TRUSTED_PROXY_COUNT` (por defecto **0**) que alimenta a la vez a `NUM_PROXIES`
de DRF y a la auditoría.

- **0** — se ignora `X-Forwarded-For`; el cliente es `REMOTE_ADDR`.
- **N > 0** — el operador afirma que N proxies **añaden** su entrada, y el cliente
  es la N-ésima desde la derecha.

Poner 1 «porque hay un proxy» es la configuración peligrosa si ese proxy no añade
nada: la entrada más a la derecha pasa a ser la que escribió el cliente.

### El proxy de Next elimina, no reconstruye

`NextRequest` no expone la IP de la conexión en Next 16.3.4, así que el proxy sólo
puede leer headers. Reconstruir ahí una IP sería inventarla, de modo que
simplemente **elimina** los headers de identidad de red y deja que Django use
`REMOTE_ADDR`.

Tiene que ser así porque `docker-compose.yml` publica Django en `8000:8000`: es
alcanzable sin pasar por Next. Comprobado en vivo que el límite aplica también
hablando directo al backend.

### `SECURE_PROXY_SSL_HEADER`

Sólo se activa si `TRUSTED_PROXY_COUNT > 0`. Antes estaba siempre puesto en
producción, y siendo un header, un backend alcanzable directamente creería que una
petición en claro fue HTTPS.

### Por qué PARCIAL

El throttle guarda sus contadores en `LocMemCache` (no hay bloque `CACHES`), que
es **por proceso**: con varios workers cada uno lleva su propia cuenta. Cerrar eso
requiere cache compartida, que es una decisión de infraestructura, no de código.

---

## Servicio técnico — núcleo multiempresa (M8 / BR-005A)

**Estado: IMPLEMENTADO (núcleo).** Migraciones **0035–0037**. Aditiva.

### Entregado

- `Device` — el equipo de un cliente, reutilizable entre visitas
- `RepairOrder` — una visita de un equipo al taller
- `RepairStatusCode` (códigos estables) + `RepairStatusSetting` (etiqueta por empresa)
- `RepairStatusHistory` — append-only, `save()` y `delete()` se niegan
- `TechnicianAssignment` — historial de responsables, no una columna
- `service_services.py` — la máquina de estados, el lock y el número
- `/api/v1/internal/<slug>/service/…` — 9 endpoints
- `/api/v1/customer/<slug>/repairs/` — lectura del cliente
- 113 tests nuevos

### Reglas

- **Tres puertas**: pertenencia → 404; capability → 403; **sucursal → 404**.
- El estado inicial, el número, la empresa y quién recibió el equipo los fija el
  servidor. El payload no tiene campo para ninguno.
- `RepairOrder.status` es una **proyección**; `RepairStatusHistory` es la
  evidencia, y las dos se escriben en la misma transacción con la fila bloqueada.
- La numeración **reutiliza `InternalSequence`** — no hay un segundo sistema.
- El técnico debe tener `Membership` activa en la empresa. `UserProfile.role` no
  autoriza nada.
- El serializer de cliente y el interno son clases distintas, no una con un flag.

### Capabilities

Promovidas a **ACTIVE**: `service.devices.view`, `service.devices.manage`,
`service.orders.view`, `service.orders.create`, `service.orders.manage`.
Siguen **RESERVED**: `service.diagnostic.manage`, `service.repair.manage`,
`service.quality.manage`. No se inventó ninguna capability nueva.

### Lo que NO se implementó

Diagnóstico, cotización, aprobación, ejecución, repuestos, control de calidad,
garantía, evidencias fotográficas y el token público de seguimiento (BR-008).
Cuatro estados, y la máquina se detiene en `waiting_approval` a propósito.

### No tocado

`Order`, `OrderItem`, carrito, checkout, Stripe, inventario, `/api/admin/`,
cookie + CSRF.

---

## Fase 0.3 / P0-A — Dependencias y cadena de suministro

**Estado: IMPLEMENTADO.** Sin migraciones.

Primera subfase del hardening de seguridad. Todo lo de aquí se verificó hoy
contra OSV/GHSA y PyPI/npm, no contra memoria.

### Frontend — de 7 vulnerabilidades a 0

`next 16.2.9 → 16.3.4` · `sharp 0.32.6 → 0.35.4` · `eslint-config-next 16.3.4`,
más `npm audit fix` para las transitivas de build.

La versión mínima que cierra los **nueve** advisories de Next es 16.2.11, pero
16.2.12 **no bastaba**: Next empaqueta sus propias copias anidadas de
`postcss@8.4.31` y `sharp@0.34.5`, ambas vulnerables, que una subida de `sharp`
en la raíz no alcanza. `16.3.4` trae `postcss@8.5.23` y ya no depende de sharp.
Por eso la «actualización mínima segura» acabó siendo 16.3.4 y no 16.2.12.

`npm audit`: **0 vulnerabilidades**.

### El proxy de imágenes abierto

`next.config.ts` tenía `{ protocol: "https", hostname: "**" }`. La documentación
de la propia versión de Next dice que `**` casa cualquier subdominio y que omitir
`pathname`/`search` implica comodín, «which may allow malicious actors to
optimize urls you did not intend».

Como la tienda renderiza con `next/image`, `/_next/image` era un **proxy de
imágenes abierto**: cualquiera podía hacer que el servidor descargara cualquier
URL HTTPS, con su IP y su ancho de banda. Era además la superficie del DoS de la
Image Optimization API.

Ahora los hosts salen de `NEXT_PUBLIC_IMAGE_HOSTS`, y **si no se configura no se
permite ninguno**. Es deliberado: una imagen que no carga se ve y se corrige; un
comodín restaurado en silencio, no. Un `**` puesto en la variable se descarta.
Verificado en vivo: `169.254.169.254` (metadatos de AWS) y cualquier otro host
responden 400.

### Backend

`Django 5.2.15 → 5.2.17` (sólo parches de seguridad en la línea LTS; 5.2.16
corrige tres CVE y 5.2.17 una) · `Pillow 12.2.0 → 12.3.0` · `sqlparse` fijado a
`0.6.0`.

De los cuatro CVE de Django, **dos no aplicaban** a este proyecto: no se usa el
middleware de caché (CVE-2026-48588) ni GIS/GDALRaster (CVE-2026-53877). Sí
aplica el de `DomainNameValidator`, porque `image_url`, `website_url`,
`facebook_url` e `instagram_url` son `URLField`. Se sube igual a la última.

Los **trece** CVE de Pillow **no tienen superficie alcanzable**: no hay un solo
`ImageField` ni `FileField` en el proyecto y nada bajo `backend/` importa PIL.
Se actualiza porque es gratis, no porque estuviera expuesto.

### `openpyxl` faltaba en `requirements.txt`

Estaba instalado en desarrollo y todo el importador de la Fase C1.4 depende de
él, así que localmente funcionaba mientras un despliegue limpio habría reventado
con `ModuleNotFoundError` al abrir la carga masiva. Una dependencia que sólo
existe en la máquina donde se escribió no es una dependencia.

---

## Fase Comercial C1.5 — Hardening de la carga masiva previo al merge

**Estado: IMPLEMENTADO.** Sin migraciones nuevas.

Cierra los defectos de la auditoría remota de C1.4. Todos son de la misma
familia: el importador era correcto en el camino feliz y demasiado confiado en
los bordes.

### 5000 filas no puede significar «recorta y sigue»

El lector paraba en 5000 filas y marcaba `truncated`, pero el preview seguía
adelante: un archivo de 5001 filas producía **5000 filas preparadas, cero
errores y un trabajo aplicable**. Eso es una importación parcial que se presenta
como completa.

Ahora hay dos modos explícitos. `SAMPLE` (la inspección) para de leer a
propósito y eso significa «hay más». `FULL_IMPORT` **no recorta nunca**: lee una
fila más que el límite sólo para saber si se pasó, y si se pasó **rechaza el
archivo entero con 400**, antes de crear ningún trabajo. Las filas vacías del
final no cuentan: una hoja con 5000 registros y 200 filas de formato sobrante es
un archivo de 5000.

### Una sola semántica de identidad

`normalize_barcode()` recorta y nada más, porque Code128 lleva mayúsculas y
minúsculas que el lector reproduce. El importador **ponía todo en mayúsculas**,
así que fusionaba dos artículos que la caja distingue. `AbC123` y `abc123` vuelven
a ser dos códigos distintos, en el import igual que en el POS.

### Un código desactivado sigue ocupando su código

`UNIQUE(company, code)` no tiene condición sobre `is_active`. El índice cargaba
sólo los activos, así que era ciego justo a las filas que iban a reventar al
aplicar. Ahora se distinguen dos preguntas: **propiedad** (todos los códigos,
activos o no — la que necesita un importador) y **escaneo** (sólo activos — la
del POS). Un código retirado identifica a su producto, con aviso, **no se
reactiva** y **no se duplica**; si pertenece a otro producto, es error de fila.

### Preview y apply tienen que coincidir

Si entre las dos pantallas alguien ocupa un código, aplicar ya no adivina: el
trabajo **aborta entero** pidiendo volver a previsualizar. Antes actualizaba
silenciosamente el producto que hubiera aparecido — seguro cuando es el mismo
artículo, y un renombrado de un producto ajeno cuando no lo es. Nada en los datos
distingue los dos casos; una persona sí.

### La carga inicial se revalida al aplicar

«Stock inicial» afirma que antes no había nada. El preview lo comprobaba y luego
el operador se iba a almorzar. Ahora, **bajo todos los locks y antes de escribir
un solo movimiento**, se vuelve a exigir: stock en cero y sin Kardex previo. Si
cambió, **falla todo** — no se degrada a corrección, porque eso convertiría «esto
es con lo que empezamos» en «esto es un ajuste» sin decirlo. También se rechaza
stock existente sin Kardex: es un estado que este sistema no produce y cuya
procedencia se desconoce.

### El stock es un entero exacto

`int(float(text))` convertía `9007199254740993` en `…992`: la cuenta volvía corta,
en silencio. Se parsea como entero exacto y se valida contra el techo real de la
columna; una cifra imposible es error de fila, no un 500.

### El historial filtraba entre capacidades

`/api/admin/imports/` elegía la capacidad según `?type` pero **no filtraba la
consulta**: con sólo `products.manage` se veían los trabajos de inventario —
nombres de archivo, sucursales, filas y quién los ejecutó. Ahora cada tipo exige
su capacidad y sin `?type` se ve exactamente lo que se tiene. Sin ninguna: 403.

El detalle y el reporte de errores buscaban el trabajo **por pk global** y luego
elegían la capacidad según lo encontrado, lo que convertía el endpoint en un
oráculo: 403 si existía un trabajo de ese tipo en algún sitio, 404 si no. Ahora se
resuelve primero la empresa, se busca dentro de ella, y otro inquilino recibe 404
indistinguible.

### Además

- Una fila preparada que ya no resuelve (producto o sucursal desaparecidos) **aborta
  el trabajo**, no se salta en silencio.
- Sucursal inactiva: rechazada en preview y en apply, igual que ya hacían los
  conteos físicos y las transferencias.
- El aviso de «posible cero perdido» ya no afirma que Excel guardó la celda como
  número salvo que **la celda fuera realmente numérica**; una columna de texto con
  dígitos ya no se marca.
- El fallo al guardar un perfil de mapeo se registra en el log (sin contenido del
  archivo) en vez de tragarse por completo.

---

## Fase Comercial C1.4 — Carga masiva de productos e inventario desde Excel

**Estado: IMPLEMENTADO.** Migración **0042**.

### Los dos formatos reales, auditados

`Carga_Masiva_(Productos).xlsx` — SHA256 `b14bca62…6b6d534`. Cinco hojas, pero
**no son cinco hojas de productos**: `Productos` es la de datos y
`Unidades de medida` (63 filas), `Marcas` (vacía), `Variables` (vacía) y
`Afectaciones` (4 filas) son vocabularios de validación. La fila 1 es un banner
de títulos combinados y la **fila 2** tiene los 18 encabezados reales. La hoja de
productos está **vacía**: es la plantilla en blanco.

`Carga_Masiva_(inventarios)-2.xlsx` — SHA256 `83bb2a69…30150a0`. Una hoja,
cabecera simple, **696 productos**, `CODIGO` único en las 696 filas, `CODIGO EAN`
presente en 692 y guardado como **número**, y la **columna de cantidad
completamente vacía**.

### Dos hallazgos que cambiaron el diseño

**Los EAN del archivo no son códigos de barras.** Son la serie
`310000000001…310000000696` con 4 huecos, y sólo 67 de 692 pasan el dígito de
control —lo que da el azar—. Por eso la simbología sólo se nombra cuando el
dígito de control lo confirma; en caso contrario `unknown`. Etiquetarlos
`upca` metería una afirmación falsa en el catálogo e invitaría a alguien a
imprimir uno.

**El precio de la plantilla es por sucursal.** No hay «Precio de venta»: sólo
`Precio venta - 11834`, que es la lista de precios de una tienda. El catálogo
guarda un precio por producto, así que se importa —es el único que hay— pero la
previsualización lo advierte.

### Vacío no es cero

La regla alrededor de la que está construido todo el importador de stock:

    celda vacía  →  no tocar ese stock
    cero escrito →  poner ese stock en cero

Son instrucciones opuestas y en una hoja de cálculo se parecen. El archivo real
tiene 696 filas con la columna de cantidad vacía: es el catálogo impreso,
esperando a que alguien recorra los estantes. Leer vacío como cero da de baja
la tienda entera en una sola carga.

### Objetivo, no diferencia

El número del archivo es el stock **contado**, no cuánto sumar. El movimiento se
calcula como `objetivo − actual` **bajo lock en el momento de aplicar**, nunca
con la diferencia que mostró la previsualización: entre las dos pantallas la caja
puede haber vendido dos unidades.

### Arquitectura

`BulkImportJob` · `BulkImportRow` · `ImportMappingProfile`. Dos pasos siempre:
previsualizar (no escribe nada comercial) y aplicar (lee del staging, no del
navegador). **El archivo no se guarda**: sólo su SHA256 y las filas ya
normalizadas.

- **Lector seguro** (`xlsx_reader.py`): sólo `.xlsx`, 10 MB, tope de filas,
  defensa contra zip bomb, rechazo de macros, y **saneado en memoria** de los
  `dataValidation` que Google Sheets escribe con `errorStyle="error"` —valor que
  no existe en el estándar y que impide abrir el archivo del propietario. El
  original nunca se modifica. Las fórmulas ni se evalúan ni se leen de caché.
- **Presets por firma de encabezados**, no por empresa. Cualquier inquilino cuyo
  sistema exporte esas columnas queda reconocido; un `if company.slug == …`
  habría soldado un cliente a la plataforma.
- **Escritura de stock sólo por `inventory_services`**: todo cambio es un
  `StockMovement` con referencia al job. Locks en orden `(branch_id, product_id)`.
- **Aplicar es idempotente**: el segundo clic devuelve el resultado del primero.
- Plantilla de productos e **exportación del inventario** (con cantidades o en
  blanco para conteo físico), reimportables por el mismo sistema.

### PENDIENTE
- Importación parcial · CSV · compras a proveedor · promociones · reversión
  automática de una importación ya aplicada · limpieza/retención de
  `BulkImportRow`.

---

## Fase Comercial C1.4 — Reconciliación del grafo de migraciones y hardening de promociones

**Estado: IMPLEMENTADO.** Migración **0041** (merge, sin operaciones).

### El grafo tenía dos hojas

Dos líneas de trabajo salieron de la 0033 sin saber una de otra: `0034_checkout_idempotency`
(checkout nativo, en master) y `0034_commercial_pos_barcode → … → 0040` (POS y
promociones). Django se niega a ejecutar un grafo con dos hojas, y hace bien: la
respuesta no está en los archivos.

Se creó una **migración de merge real** (`0041`), vacía, que depende de ambas
hojas. No se renumeró nada. Renumerar la rama comercial habría hecho que Django
viera siete migraciones nunca ejecutadas contra tablas que ya existen, y de eso
se sale a mano.

### Dos idempotencias, y el merge automático casi se lleva una

El auto-merge de `models.py` dejó **dos asignaciones `constraints = [...]`** en
`Order.Meta`; la segunda tapaba a la primera en silencio. Python válido, semántica
equivocada: la unicidad de `pos_idempotency_key` desaparecía, y con ella la
garantía de que un reintento del POS no duplica una venta. Se unificaron en una
sola lista. **No se fusionan** las dos idempotencias: la del POS es única por
empresa (un mostrador tiene una sola secuencia de ventas), la del checkout es
única por empresa **y usuario** (dos clientes pueden generar la misma clave).

### Fechas de promociones y cupones

`promotion.starts_at = request.data['starts_at']` parecía validar. No validaba:
un `DateTimeField` acepta cualquier cosa en Python y sólo se convierte al llegar
a la base de datos, ya dentro de la transacción. `"mañana"` daba **500**, y
`"2026-01-01 10:00"` se guardaba naive, con una hora que significaba lo que
dijera el reloj del servidor. Una ventana de promoción decide si un descuento se
dispara en una caja: equivocarse cinco horas es un precio equivocado en un recibo
real.

Nuevo `store/api_parsing.py`: `parse_optional_datetime()` y `parse_window()`.
Vacío → `None`; ISO-8601 → aware; naive → hora local (America/Lima), porque el
control HTML obvio no manda zona horaria; inválido → **400**, nunca 500. Un
número JSON **no** es una fecha: `20260301` se rechaza en vez de adivinar entre
fecha compacta y epoch.

### El botón de archivar nunca funcionó

`PATCH` no era parcial. Una clave ausente significaba «ponlo en None», así que
`PATCH {is_active: false}` —exactamente lo que manda el botón de archivar—
borraba el precio del combo y `Promotion.clean()` rechazaba la petición con 400.
El único test de C1.3 que mandaba `is_active` apuntaba a la promoción de **otra**
empresa y esperaba 404: pasaba por el control de tenant y nunca llegaba a este
código. Ahora una clave ausente significa «déjalo como está».

### Invariantes de tenant

`PromotionBranch`, `PromotionItem` y `AppliedPromotion` validan en `clean()` que
promoción, sucursal, producto y pedido pertenezcan a la misma empresa. Como
`bulk_create()` **no** llama a `save()` —y es justo el camino que escribe estas
filas—, cada uno tiene además un `assert_all_match_*()` de conjunto, resuelto en
una consulta, que los caminos masivos invocan antes de escribir.

---

## Fase Comercial C1.3 — Hardening C1.2 + promociones automáticas y combos

**Estado: IMPLEMENTADO PARCIALMENTE.** Migraciones **0038, 0039, 0040**.

Dos entregas: cerrar los hallazgos de la auditoría de C1.2, y añadir promociones
automáticas. **La carga masiva por Excel NO se implementó** — ver el apartado
final.

### Hardening C1.2

- **El histórico de descuentos estaba mal etiquetado.** La migración 0036 puso
  `discount_source = none` en todos los pedidos anteriores, afirmando que
  ninguno había tenido descuento. Pero `coupon_code` y `discount_amount` existen
  desde antes y el checkout aplica cupones desde la Fase 1, así que cada pedido
  con cupón quedó marcado como si no lo hubiera tenido. La 0038 lo repara. Un
  descuento **sin** código se deja en `none` a propósito: llamarlo `manual`
  inventaría una autorización que nadie dio, y `discount_authorized_by` quedaría
  vacío. Se cuentan y se reportan.
- **La huella de idempotencia dependía del catálogo.** Hasheaba el descuento ya
  *resuelto*, así que un reintento noventa segundos después —tras editar una
  promoción o vencer un cupón— hasheaba distinto y se rechazaba por conflicto: al
  operador se le decía que su propia venta chocaba consigo misma. Ahora hashea
  sólo lo que el operador tecleó, y el reintento se resuelve **antes** de mirar
  un solo precio.
- **El efectivo entra en la huella** (§7): sin él, un reintento entregando otro
  billete devolvía la venta anterior y el vuelto salía mal.
- **Las referencias ya no se truncan.** Recortar un código de autorización guarda
  algo que ya no coincide con el registro del banco, sin avisar. Ahora se rechaza.
- **403 ≠ 400.** Falta de permiso para descontar o para atribuir la venta
  responde 403. Un 400 mandaba al operador a buscar un error de tecleo inexistente.
- **Elegibilidad del vendedor.** Antes valía cualquier membresía activa, así que
  se podía acreditar una venta —y pagar comisión— a un almacenero o a un técnico.
  Ahora hace falta `sales.pos.use` resuelto por el motor real de capacidades, y
  acceso a la sucursal de esa venta.

### Promociones automáticas y combos

- **`Promotion` + `PromotionItem` + `PromotionBranch` + `AppliedPromotion`.**
- **Un combo NO es un producto.** No existe un `Product` "Combo iPhone": no
  tendría stock. La venta lleva los tres artículos reales, salen tres
  `SALE_EXIT` de tres estantes reales, y la promoción sólo cambia el dinero.
- **Motor determinista** en `promotion_services.py`: `prioridad DESC, id ASC`,
  cada promoción consume unidades y una unidad ya usada no se reutiliza.
- **Se aplica sola.** El operador no pulsa nada: la empresa ya configuró la regla.
- **Atajo de combos en el POS**, con disponibilidad calculada desde el
  componente más escaso — nunca se ofrece un combo que el estante no completa.
- **Snapshot congelado**: editar, renombrar o desactivar una promoción no
  reescribe una venta pasada.
- **Sin apilar** con cupón ni descuento manual: apilar es una política de negocio
  con reglas que nadie ha escrito.
- **Administración** en `/admin/sales/promotions`, con la pestaña de códigos que
  da a `Coupon` la UI tenant-aware que nunca tuvo.
- Cuatro capabilities nuevas, todas **ACTIVE**.

### NO implementado en esta fase
- **Carga masiva de productos por Excel.**
- **Carga masiva de inventario por Excel.**

La auditoría de los dos archivos adjuntos **sí** se hizo y se entrega, porque es
el trabajo previo que esa fase necesita. Lo que no se hizo es el importador. Un
importador a medias que escribe stock es peor que ninguno: el stock es
irreversible sin un movimiento compensatorio, y la mitad del diseño que pide el
prompt —preview/apply con hash, mapeo configurable, reconciliación contra el
Kardex, bloqueo determinista y atomicidad— sólo aporta seguridad si está
completo.

---

## Fase Comercial C1.2 — Venta enriquecida: cliente, vendedor, comisiones y descuentos

**Estado: IMPLEMENTADO.** Migraciones **0036, 0037**.

### El problema que cierra
El POS de C1 sabía cobrar y descontar stock, y no sabía nada más. No podía decir
a quién se le vendió, a quién se le acredita la venta, cuánto gana quien la hizo,
ni por qué se cobró menos de lo que marcaba la etiqueta. Un mostrador real
necesita las cuatro cosas, y un negocio que paga comisiones las necesita
registradas en el momento, no reconstruidas después.

### IMPLEMENTADO
- **Cliente en el POS**: sin identificar, buscar o seleccionar uno del CRM. Se
  reutiliza `Customer`; no hay un segundo modelo de cliente.
- **Operador ≠ vendedor.** `request.user` es quien opera y firma la auditoría;
  `Order.sold_by` es a quién se acredita la venta.
- **Reasignación de vendedor** con `sales.pos.assign_seller`.
- **`Membership.commission_rate_percent`** — la comisión pertenece al empleo.
- **`SalesCommission`** — el ledger: una fila por venta, congelada.
- **Descuentos**: cupón existente reutilizado, o descuento manual con
  `sales.discounts.apply`, motivo obligatorio y autor registrado.
- **`POST /api/admin/pos/preview/`** — el total del servidor antes de cobrar.
- **Efectivo y vuelto**, calculados en el servidor.
- **Datos opcionales acotados**: `payment_reference`, `external_reference`,
  `sale_notes`.
- **`/admin/sales/commissions`** — devengado por vendedor y porcentajes del equipo.
- Cuatro capabilities nuevas, todas **ACTIVE**.
- 59 tests nuevos (1676 → **1735 OK**, 4 omitidos).

### Decisiones
- **La comisión es del empleo, no de la persona.** Un mismo humano vende para
  dos empresas en condiciones distintas, así que una tasa en `User` no podría
  decir la verdad. Tampoco va en el rol: dos vendedores de una misma tienda
  suelen tener tratos distintos.
- **`SalesCommission` es una tabla, no tres columnas en `Order`.** Una comisión
  no es una propiedad de la venta: es una obligación con vida propia —se
  devenga, puede anularse cuando el producto vuelve, y algún día se liquida en
  lote. Nada de eso cabe en tres campos colgando de un pedido.
- **Todo se congela al vender.** Tasa, base e importe son snapshots. Si mañana
  alguien pasa de 3% a 5%, lo de ayer sigue en 3%: la empresa acordó pagar 3%
  por esas ventas, y recalcularlas desde la tasa de hoy reescribiría una deuda
  a posteriori.
- **La comisión se calcula sobre la venta NETA.** El descuento se resta primero:
  pagar un porcentaje de dinero que la tienda no cobró haría que cada descuento
  costara más de lo que aparenta.
- **Tasa 0% no escribe fila.** Un ledger lista obligaciones, y «no se debe nada»
  no es una. Una tabla de ceros habría que filtrarla en cada informe.
- **Un cupón no necesita permiso; un descuento manual sí.** La empresa configuró
  la promoción de antemano, así que aplicarla no es una decisión del cajero.
  Teclear un precio sí lo es, y por eso lleva permiso, motivo y autor.
- **Cupón y descuento manual no se apilan.** Apilar es una política de negocio
  con reglas —cuál se aplica primero, si componen, cuál es el suelo—. Adivinar
  una aquí habría metido una política sin examinar dentro de una caja.
- **El total lo calcula el servidor, también en el preview.** Recalcular el
  porcentaje del cupón en el navegador significaría que el número que el
  operador lee en voz alta lo produce un código distinto del que cobra, y el
  cliente está delante cuando no coinciden.
- **Sin efectivo no hay venta en efectivo, y sin efectivo no hay vuelto en
  tarjeta.** Los campos quedan NULL: escribir cero haría indistinguible «pagó
  justo en efectivo» de «no pagó en efectivo».
- **Campos con nombre, no un JSON cajón de sastre.** Un blob libre se convierte
  en el sitio donde cae todo y nada se puede validar, reportar ni borrar.

### Corregido durante la fase
- **El preview era inusable para ventas en efectivo.** Validaba el efectivo
  recibido, así que exigía contar el dinero *antes* de poder mostrar el total —
  justo al revés de para qué sirve un preview. Ahora el preview no lo valida y
  la venta sí.

### PENDIENTE (Comercial C2)
- Pagos mixtos (efectivo + tarjeta) · liquidación y pago de comisiones ·
  devoluciones, que deberán anular la comisión con un movimiento compensatorio ·
  comisión dividida entre dos vendedores · grupos de clientes · caja y arqueo.

---

## Fase Comercial C1.1 — Hardening previo al merge

**Estado: IMPLEMENTADO.** Sin migraciones nuevas.

Correcciones de una auditoría remota sobre C1, más la integración con el
catálogo público móvil (`/api/v1/`) que entró a `master` en paralelo.

### Corregido

- **La clave de idempotencia era opcional.** Una venta sin clave no tenía
  protección alguna: el único camino donde un doble clic cobra dos veces. Ahora
  es obligatoria, y **no se trunca**: `str(value)[:64]` habría fundido dos claves
  distintas de 80 caracteres en una sola y respondido la segunda venta con el
  pedido de la primera. Una clave demasiado larga se rechaza.
- **La recuperación del `IntegrityError` ocurría dentro del mismo `atomic`.** Un
  `IntegrityError` marca la transacción para rollback, así que la consulta
  posterior lanzaba `TransactionManagementError` en vez de responder. El INSERT
  va ahora en su propio savepoint. Además, un `IntegrityError` de **otro**
  constraint se re-lanza: sólo la colisión de esta clave es un reintento.
- **El pronóstico borraba días cero reales.** `_history_start` empezaba la
  ventana en la primera venta, así que un artículo en stock hace 30 días con su
  primera venta hace 3 se leía como 3 días de historia en lugar de 30 con 27
  ceros — unas diez veces la demanda real, y la cobertura, el punto de
  reposición y la sugerencia heredaban el error. Ahora manda `tracked_since`
  cuando existe; `first_observed` es sólo el respaldo.
- **Lo mismo por otra puerta en «más vendidos»:** la cobertura agregada llamaba
  al pronóstico sin `tracked_since`. Ahora deriva la fecha del `BranchStock` más
  antiguo visible.
- **La sucursal fuente no protegía su punto de reposición.** Una tienda con
  `target=10` y punto de reposición 18 aparecía con 10 unidades disponibles para
  transferir mientras estaba 2 por encima de su propio umbral de reposición: la
  transferencia habría resuelto una escasez abriendo otra donde nadie miraba.
  El excedente usa ahora el pronóstico de la fuente; sin datos suficientes cae a
  sus umbrales configurados, **nunca a cero**.
- **Los filtros diarios usaban el timezone de la conexión.** `paid_at__date` y
  `TruncDate` sin `tzinfo` se resuelven en la zona del servidor: para un tenant
  a catorce horas, la tarde de ventas cae en el día siguiente. Las métricas C1
  construyen ahora los límites en la zona de la empresa y comparan timestamps
  crudos — más correcto y además indexable.
- **El POS registraba aceptación de términos sin que nadie la diera.** Entregar
  el artículo no prueba que se informó al cliente. El operador confirma
  explícitamente antes de cobrar; sin esa confirmación el backend responde 400.
  Lo que queda registrado es una afirmación que una persona hizo de verdad, y la
  auditoría ya dice quién.

### Integración con el catálogo móvil
`origin/master` se fusionó en la rama. Tres conflictos —`CHANGELOG.md`,
`docs/saas-multiempresa.md` y `tests.py`— resueltos **conservando ambos lados**:
los tests y funciones de `/api/v1/`, las capabilities de C1 y la documentación de
los dos desarrollos.

### Tests
29 nuevos. Total: **1597 OK**, 4 omitidos (los casos de concurrencia real que
SQLite no puede probar y que se saltan en voz alta).

---

## Fase Comercial C1 — Punto de venta, códigos de barras e inteligencia de stock

**Estado: IMPLEMENTADO.** Migraciones **0034, 0035**.

### El problema que cierra
La plataforma sabía vender por internet y no sabía vender en el mostrador. Un
negocio con tienda física llevaba las ventas presenciales fuera del sistema, así
que el stock que mostraba el panel era el que quedaba *después de restar lo que
nadie había registrado*. El Kardex describía media operación.

Y sin ventas físicas registradas no había demanda real que medir: la reposición
de la Fase 2D sólo podía decir «estás por debajo del mínimo», nunca «esto se te
acaba el jueves».

### IMPLEMENTADO
- **Punto de venta** en `/admin/sales/pos`: escaneo, búsqueda, carrito, cliente
  opcional, medio de pago y cobro.
- **`ProductBarcode`** — varios códigos por artículo (EAN del fabricante, UPC,
  etiqueta interna). `UNIQUE(company, code)`.
- **Lector USB/Bluetooth** estándar (HID keyboard wedge), sin SDK propietario.
  Todo funciona igual tecleando el código a mano.
- **`Order.sales_channel`** (`online` | `pos`), **`payment_method`**,
  **`sold_by`**, **`pos_idempotency_key`** + **`pos_request_fingerprint`**.
- **`store/pos_services.py`** — la venta de mostrador, todo-o-nada.
- **`store/inventory_forecasting.py`** — pronóstico de demanda explicable,
  cobertura, punto de reposición, cantidad sugerida y riesgo.
- **`BranchStock.safety_stock`** y **`lead_time_days`**.
- **Dashboard comercial** en `/admin/sales`: facturación, tendencia, canales,
  más vendidos, inventario en riesgo y reposición sugerida.
- **`sales.pos.use`** y **`sales.analytics.view`** — nuevas y **ACTIVE**.
- 64 tests nuevos (1444 → **1508 OK**, 3 omitidos).

### Cambios de comportamiento
- Una venta POS crea un `Order` normal, pagado, entregado y con su sucursal;
  aparece en el historial, en los reportes y puede emitir su nota interna con la
  numeración de la Fase 2E.
- El stock se descuenta **de la sucursal donde se vende**, dentro de la misma
  transacción que crea la venta.
- Los pedidos históricos quedan `online` / `stripe` por el default de columna,
  que es la evidencia real: todos se hicieron por la tienda.

### Decisiones
- **Un solo núcleo de ventas.** Un modelo `PosSale` aparte habría obligado a
  calcular dos veces cada reporte, cada movimiento de stock, cada documento y
  cada historial de cliente, y luego a conciliarlos.
- **Dos canales, dos políticas ante el faltante — una implementación.** Online:
  el dinero ya se capturó, así que el faltante se anota y el ítem se salta. POS:
  no se ha cobrado nada, así que **lanza** y la transacción entera se deshace.
  Dos copias del mismo bucle habrían divergido, y la que divergiera sería la que
  nadie estaba mirando.
- **El navegador nunca decide un precio.** Muestra uno para que el operador lea
  el total en voz alta; el servidor cobra desde su propio catálogo.
- **Idempotencia con huella.** La clave dice «es el mismo intento»; la huella
  dice «es la misma venta». Sin ella, una clave reutilizada devolvería la venta
  de otro y diría que salió bien. Misma clave + otra cesta → **409**.
- **Las líneas repetidas se agrupan**, y no por orden: `SALE_EXIT` es idempotente
  por `(order, product)`, así que dos `OrderItem` del mismo producto habrían
  vendido dos unidades descontando una.
- **El código de barras es texto, no número.** `0123456789012` y `123456789012`
  son artículos distintos; un cast a entero los fusiona en silencio. Tampoco se
  pasa a mayúsculas: Code128 distingue.
- **Los días sin ventas cuentan como cero.** Es el error clásico: 2, 0, 0, 2 son
  1/día, no 2/día. Omitir los ceros infla todo lo que viene después.
- **Sin historial suficiente no hay pronóstico**, y se dice. Pero las alertas
  físicas —sin stock, bajo mínimo— siguen funcionando.
- **`lead_time_days = 0` significa sin configurar**, no «llega al instante». Un
  plazo inventado produce un número seguro y equivocado.
- **La sugerencia de transferencia es conservadora**: una sucursal sólo cede lo
  que excede su propio umbral más alto. Vaciar una tienda para llenar otra es la
  misma escasez en otro barrio.
- **Nada de margen ni utilidad.** La plataforma no registra costos, así que
  cualquier cifra sería inventada. Se reporta facturación, no ganancia.
- **`sales.orders.*` sigue AVAILABLE**, no ACTIVE: su puente RBAC legacy sigue
  en pie y cambiar la etiqueta sin quitarlo sería una mentira del catálogo.

### PENDIENTE (Comercial C2)
- Caja / arqueo · devoluciones y anulaciones compensatorias · compras y
  proveedores · costo real y rentabilidad · descuentos manuales en POS ·
  pronóstico estacional · lector por cámara.

---

## API v1 — inventario interno (M7A)

**Estado: IMPLEMENTADO.** Sin migraciones. Aditiva.

### Entregado

- `GET /api/v1/internal/<slug>/inventory/summary/` — KPIs + `available_branches`
- `GET /api/v1/internal/<slug>/inventory/stock/` — stock por sucursal, paginado
- `GET /api/v1/internal/<slug>/inventory/movements/` — Kardex, paginado
- `POST /api/v1/internal/<slug>/inventory/adjustments/` — entrada/salida manual
- `v1_inventory_serializers.py` + `v1_inventory_views.py`, serializers propios
  de la superficie interna
- 62 tests nuevos

### Reglas

- **Tres puertas**: pertenencia → 404; capability → 403; **sucursal → 404**.
- Un `branch_id` fuera del acceso del miembro es *no encontrado*, no *prohibido*:
  un 403 confirmaría que esa sucursal existe.
- Sin `branch_id` se agrega sobre las sucursales visibles; el conjunto vacío es
  200 con cero filas, no un error.
- El ajuste manda **intención** (producto, sucursal, tipo, cantidad positiva,
  motivo). No existe campo de stock final.
- Solo `StockMovement.MANUAL_TYPES`; `sale_exit` y las transferencias, fuera.
- La vista **no escribe stock**: todo pasa por
  `inventory_services.apply_manual_stock_movement()`, el mismo servicio del admin
  web. Un test estructural sobre el AST lo vigila.
- Transferencias y recuentos **no se exponen**: son flujos de varios pasos.

### Capabilities

Ninguna promovida. `inventory.view` e `inventory.adjust` ya eran **ACTIVE** desde
la Fase 2D; M7A las consume.

### No tocado

`/api/admin/`, cookie + CSRF, Stripe, `inventory_services`, modelos, migraciones,
catálogo público, cliente.

---

## API v1 — superficie interna y pedidos de venta (M6)

**Estado: IMPLEMENTADO.** Sin migraciones. Aditiva.

### Entregado

- `GET /api/v1/internal/<slug>/context/` — capabilities frescas al entrar
- `GET /api/v1/internal/<slug>/orders/` y `/<id>/` — requieren `sales.orders.view`
- `PATCH .../orders/<id>/fulfillment/` — requiere `sales.orders.manage`
- `order_fulfillment_services.py` — una sola máquina de estados, compartida con
  el admin web
- Serializers internos propios, separados de los de cliente
- 59 tests nuevos

### Reglas

- **Dos puertas**: sin membresía activa → 404 indistinguible; con membresía y sin
  capability → 403.
- Una relación de cliente **no** abre el área interna.
- Un platform master solo opera sobre el tenant **nombrado en la ruta**.
- El detalle devuelve `available_fulfillment_transitions` desde el servidor.
- Cambiar fulfillment no toca el pago, no manda correo y no mueve stock.

### Capabilities

`sales.orders.view` y `sales.orders.manage` → **ACTIVE**, porque v1 las impone
sin ruta de rol legacy. `sales.notes.manage` sigue AVAILABLE (no implementada).
Las de servicio técnico siguen RESERVED.

### No tocado

`/api/admin/`, cookie + CSRF, Stripe, inventario, catálogo público, cliente.

---

## API v1 — checkout autenticado y config pública por slug (M5)

**Estado: IMPLEMENTADO.** Migración **0034**. Aditiva.

### Entregado

- `POST /api/v1/customer/<company_slug>/checkout/` — idempotente, Bearer v1
- `GET /api/v1/storefront/<company_slug>/config/` — público, cierra **BR-006**
- `checkout_services.py` — dominio comercial compartido por ambas superficies
- `build_storefront_config_payload()` — un solo constructor para web y app
- `Order.idempotency_key` + `idempotency_fingerprint` + constraint parcial única
- 61 tests nuevos

### Decisiones

- **DEC-API-002** — el checkout nativo y el del navegador comparten servicios de
  dominio, no contrato de transporte ni de sesión.
- **DEC-API-003** — la creación de checkout es idempotente por empresa + cliente
  autenticado + clave de petición.

### Reglas

- El cliente propone ítems; **el servidor calcula todo** y rechaza cualquier
  campo comercial que llegue del cliente.
- Nada se consume antes del pago: ni carrito ni stock.
- Misma clave con distinto contenido → **409**, nunca el pedido anterior.
- El checkout web sigue **AllowAny**: acepta invitados como siempre.

---

## API v1 — superficie de cliente y contexto de acceso (M4)

**Estado: IMPLEMENTADO (pedidos de cliente).** Sin migraciones. Aditiva.

### Entregado

- `GET /api/v1/customer/<company_slug>/orders/` y `/<id>/`
- `customer_owned_orders()`, `has_customer_relation()`, `access_contexts()` en `tenancy.py`
- Serializers de cliente propios, con `fulfillment_status` — **BR-003 cerrado para v1**
- `access_contexts` y `platform` añadidos a `login` y `me`, junto a `available_companies`
- 47 tests nuevos

### Tres audiencias, tres superficies (DEC-API-001)

Pública, cliente e interna son espacios de URL separados, no un endpoint que
ensancha su queryset según quién pregunte. Un endpoint así está a un refactor de
devolver el conjunto equivocado, en silencio.

### Reglas

- **Propiedad = `Order.user` o `Order.customer.user`.** El email nunca: no tiene
  unicidad y una familia comparte dirección.
- **Una membresía no da acceso al historial de clientes.** Vendedor, almacenero,
  técnico, admin de empresa y platform master reciben 404, con test cada uno.
- **Archivar una ficha CRM no quita acceso a las propias compras.**
- Empresa desconocida, inactiva y "no eres cliente" → el mismo 404.
- **Capabilities = presentación, jamás autorización.**
- `platform.is_master` va aparte y no enumera tenants.

### No tocado

`/api/` legacy, cookie + CSRF, admin, Stripe, checkout web, migraciones.

---

## API v1 — autenticación nativa (BR-001A)

**Estado: IMPLEMENTADO (núcleo de sesión).** Sin migraciones. Aditiva.

### Entregado

- `POST /api/v1/auth/login/` — `{email, password}` → tokens en el cuerpo
- `POST /api/v1/auth/refresh/` — rotación + blacklist del anterior
- `POST /api/v1/auth/logout/` — best-effort, siempre 200
- `GET /api/v1/auth/me/` — identidad + empresas verificadas
- `V1BearerAuthentication` — **declarada por vista, jamás global**
- `verified_company_relations()` en `tenancy.py`
- 79 tests nuevos

### Por qué un contrato aparte

La web autentica por cookie HttpOnly + CSRF porque el navegador adjunta cookies
a peticiones que el usuario no inició. Una app nativa guarda su token y lo envía
a propósito. Fusionarlos habría hecho que la superficie web empezara a aceptar
`Authorization: Bearer`.

### Decisiones

- **Login por email.** `email` NO es unique en esta DB (User estándar de Django).
  No se añadió constraint: fallaría durante el deploy en cualquier instalación
  con duplicados. 0, 1-con-password-mala, inactivo y >1 devuelven el mismo 401,
  y sin usuario se verifica contra un hash dummy para no filtrar por tiempo.
- **`available_companies` incluye clientes, no solo staff.** La migración 0015
  deliberadamente no dio Membership a los `customer`; memberships solas habrían
  devuelto lista vacía al público entero de la app. Se reportan `Membership`
  activa y `Customer` activo, etiquetados. No es autorización.
- **`is_superuser` se ignora**: un admin de plataforma no recibe todos los
  tenants en un teléfono.

### Fuera de scope — BR-001B

Registro, verificación de correo, reenvío, reset y cambio de contraseña nativos.
Devuelven 404, con tests que lo fijan.

### No tocado

`CookieJWTAuthentication`, CSRF, `/api/auth/*`, admin, `/api/` legacy,
`DEFAULT_AUTHENTICATION_CLASSES`, migraciones. Tests de regresión incluidos.

### Estado

| ID | Estado |
|---|---|
| BR-001A | **IMPLEMENTADO** |
| BR-001 completo | **PARCIAL** — falta BR-001B |
| BR-002 · BR-007 | **PARCIAL** |
| BR-003 · BR-005 · BR-006 · BR-008 | PENDIENTE |

---

## API v1 — catálogo público para clientes nativos

**Estado: IMPLEMENTADO (solo catálogo público).** Sin migraciones. Aditiva.

### Entregado

- `GET /api/v1/storefront/<company_slug>/products/` — `IMPLEMENTADO`
- `GET /api/v1/storefront/<company_slug>/products/<product_slug>/` — `IMPLEMENTADO`
- `GET /api/v1/storefront/<company_slug>/categories/` — `IMPLEMENTADO`
- `resolve_public_storefront_company()` — resolución de tenant por ruta, solo empresas activas
- `company_storefront_products()` / `company_storefront_categories()` — extraídos de los
  helpers por Host para que la lógica de scoping exista **una sola vez**
- `v1_serializers.py` — contrato de campos propio, desacoplado del serializer web
- 60 tests nuevos

### Por qué

El storefront web identifica su empresa por **Host**, y eso es correcto para la
web: lo fijan el DNS y el proxy, no la página. Una app móvil llega a un host de
API compartido y no tiene ese Host. Sin un selector explícito solo caben dos
finales: catálogo vacío, o el catálogo de la empresa que el fallback eligiera.

### Reglas

- El slug de la ruta **selecciona** un escaparate público. No autoriza nada.
- Empresa desconocida, inactiva, malformada y vacía → **el mismo 404**. Un 403
  para "inactiva" respondería qué empresas existen.
- Sin fallback a "la primera empresa": el queryset nace scopeado o no existe.
- Ni query param, ni cabecera, ni Host, ni `DEFAULT_STOREFRONT_COMPANY_SLUG`
  pueden cambiar el tenant de la ruta. Con test para cada uno.
- Autenticación apagada explícitamente en estas vistas.

### No tocado

`CookieJWTAuthentication`, CSRF, login, refresh, logout, admin, `/api/` legacy,
migraciones. **BR-001 sigue `API_PENDING`**; no hay Bearer global ni superficie
privada v1.

### Estado de los requerimientos de Mobile

| ID | Estado |
|---|---|
| BR-002 | Catálogo público: **IMPLEMENTADO**. Autorización de datos privados: PENDIENTE |
| BR-007 | **PARCIAL** — slice de catálogo únicamente |
| BR-001 · BR-003 · BR-005 · BR-006 · BR-008 | PENDIENTE / `API_PENDING` |

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
