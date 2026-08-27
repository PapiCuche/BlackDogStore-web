# Fundación SaaS multiempresa

> **Fase SaaS 1** · Base estructural para operar más de un negocio en la plataforma.

**Estado global de la fase: PARCIAL** — la fundación está `IMPLEMENTADO`; la
tenantización de los modelos de negocio queda `PENDIENTE` por diseño.

```
Autenticación única                      IMPLEMENTADO
E-commerce / portal externo              IMPLEMENTADO
Control interno — shell v1               IMPLEMENTADO
Dashboard empresarial v1                 IMPLEMENTADO
Sidebar capability-aware                 IMPLEMENTADO
Selector de empresa                      IMPLEMENTADO
KPIs comerciales tenant-aware            PENDIENTE
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

---

## 1. Qué introduce esta fase

Tres modelos y un módulo de resolución de tenant. **Nada del flujo actual los
lee todavía**: catálogo, carrito, checkout, Stripe, inventario, Kardex y notas de
venta se comportan exactamente igual que antes.

```
Company ──< Branch
   │           │
   └──< Membership >── User
```

| Concepto | Estado | Nota |
|---|---|---|
| `Company` | IMPLEMENTADO | Tenant. Slug único. |
| `Branch` | IMPLEMENTADO | Pertenece a exactamente una empresa. |
| `Membership` | IMPLEMENTADO | Usuario + empresa + rol (+ sucursal opcional). |
| Resolución de tenant | IMPLEMENTADO | `store/tenancy.py` |
| Endpoints de administración | IMPLEMENTADO | `/api/admin/companies|branches|memberships/` |
| Aislamiento en esos endpoints | IMPLEMENTADO | Scoping por membresía, tests de cruce. |
| `AdminAuditLog.company` | IMPLEMENTADO | Nullable; el histórico no se reescribe. |
| Resolución por host | PARCIAL | Implementada y testeada; ningún endpoint público la usa aún. |
| Matriz de capacidades empresariales | IMPLEMENTADO | Fase 2A — `COMPANY_CAPABILITIES` |
| Helpers RBAC empresariales | IMPLEMENTADO | Fase 2A — `can_manage_company_*` |
| `CompanyContext` | IMPLEMENTADO | Fase 2A |
| RBAC legacy (`UserProfile.role`) | IMPLEMENTADO / TRANSICIÓN | Sigue gobernando el dominio comercial |
| Tenantización de `Product`/`Order`/… | PENDIENTE | Fase siguiente — ver §7. |
| RBAC tenant-aware | PENDIENTE | `get_user_role()` sigue intacto — ver §5. |
| Branding por empresa | PENDIENTE | Constantes aún en `email_services.py` / `pdf_services.py`. |
| UI administrativa multiempresa | PROPUESTA | Backend + Django Admin bastan por ahora. |

---

## 2. Modelos

### Company

| Campo | Tipo | Nota |
|---|---|---|
| `name` | CharField(200) | |
| `legal_name` | CharField(255) | Razón social |
| `tax_id` | CharField(20), indexado | RUC |
| `slug` | SlugField(80), **único** | Usado por la resolución por host |
| `is_active` | Boolean, indexado | Desactivar ≠ borrar |
| `created_at` / `updated_at` | DateTime | |

Una empresa desactivada **conserva todo su historial**. `Branch.company` y
`Membership.company` usan `on_delete=PROTECT`, así que una empresa con
operaciones no se puede borrar; el Django Admin además deshabilita el borrado.

### Branch

| Campo | Tipo | Nota |
|---|---|---|
| `company` | FK → Company, `PROTECT` | Una sucursal pertenece a **una sola** empresa |
| `name`, `address`, `phone`, `email` | | |
| `is_active` | Boolean, indexado | |

- Constraint `unique_branch_name_per_company` — el mismo nombre puede repetirse
  entre empresas distintas, nunca dentro de la misma.
- Índice `(company, is_active)`.

### Membership

| Campo | Tipo | Nota |
|---|---|---|
| `user` | FK → User, `CASCADE` | |
| `company` | FK → Company, `PROTECT` | |
| `role` | CharField(20) | Reutiliza `UserProfile.ROLE_CHOICES` |
| `branch` | FK → Branch, nullable, `SET_NULL` | |
| `is_active` | Boolean, indexado | |

- Constraint `unique_membership_per_user_company` — un usuario puede pertenecer
  a varias empresas, pero nunca dos veces a la misma.
- Índices `(company, is_active)` y `(user, is_active)`.
- `clean()` + `save()` rechazan una sucursal de otra empresa, así que la base
  nunca guarda una fila inconsistente.
- `grants_business_access` es `True` solo si la membresía **y** la empresa están
  activas.

---

## 3. Estrategia de resolución de tenant

> **La regla:** el tenant activo **nunca** se toma de datos del cliente. Un
> `company_id` en el body, query, header o cookie es *dato a validar*, jamás la
> respuesta a "¿de qué empresa es esta petición?".

Implementación en [`store/tenancy.py`](../backend/store/tenancy.py):

**1. Petición autenticada de staff** — el tenant sale de las `Membership` activas
del propio usuario:
- una sola membresía activa → esa empresa, sin intervención del cliente;
- varias → el llamante debe indicar cuál, y el valor se valida **contra sus
  propias membresías** antes de usarse;
- administrador de plataforma (`is_superuser`) → puede operar entre tenants, pero
  la empresa destino se nombra explícitamente y queda auditada.

**2. Tienda pública** *(diseñado, aún no aplicado)* — el tenant sale del host:
`blackdog.example.com` → `Company.slug = "blackdog"`. El host lo fija DNS y el
reverse proxy, no el JavaScript de la página. `resolve_company_from_host()` ya
existe y está testeada; ningún endpoint público la invoca porque catálogo,
carrito y checkout todavía no están tenantizados.

**3. Django Admin y comandos** — explícito; el operador es de confianza.

Un id de otra empresa devuelve **el mismo error** que uno inexistente, para que
no se puedan sondear ids ajenos.

### Prioridad y casos límite

| Situación | Comportamiento |
|---|---|
| Prioridad de fuentes | 1) membresías del usuario autenticado · 2) host (diseñado) · 3) admin/comandos |
| Sin resolución posible | `NoTenantError` — nunca se elige una empresa por defecto |
| Usuario en varias empresas | `NoTenantError` pidiendo elección explícita; el valor recibido se valida contra sus propias membresías |
| `company_id` arbitrario del cliente | Solo **selecciona** entre empresas ya accesibles; nunca amplía acceso → `CrossTenantError` |
| Host desconocido / reservado (`www`, `api`, `admin`, `app`) / sin subdominio | `None` — sin tenant, sin fallback |
| Company inactiva | `has_company_access` = False; `resolve_company_from_host` devuelve `None`; solo el admin de plataforma puede seleccionarla |
| Membresía inactiva | No cuenta: `active_memberships` la excluye |

Esta resolución **no** se aplica todavía al e-commerce: catálogo, carrito y
checkout siguen operando sin tenant, y esa compatibilidad es intencional.

---

## 4. Empresa piloto

La migración de datos `0015_seed_pilot_company` crea Black Dog Store como primer
tenant, con su sucursal inicial, y refleja en `Membership` el rol de cada usuario
de staff existente.

**Es seed, no arquitectura.** No existe ninguna constante tipo
`COMPANY_NAME = "Black Dog Store"` en la capa de negocio — hay un test
(`test_no_company_name_constant_in_business_layer`) que lo verifica. La
plataforma puede crear después una empresa completamente distinta sin tocar
código.

Los usuarios con rol `customer` **no** reciben membresía: un comprador es cliente
de una tienda, no personal de un tenant.

---

## 5. `superadmin` durante la transición

Hay dos conceptos que antes eran uno solo:

| Concepto | Cómo se expresa hoy | Alcance |
|---|---|---|
| **Administrador de plataforma** (operador SaaS) | `User.is_superuser` | Todos los tenants |
| **Administrador de empresa** | `Membership.role == 'admin'` | Una sola empresa |
| Rol global heredado | `UserProfile.role == 'superadmin'` | Global, se sigue respetando |

`is_platform_admin()` devuelve `User.is_superuser`, que es exactamente lo que
`get_user_role()` ya mapeaba a `superadmin`. Por eso esta fase **no cambia ningún
comportamiento existente**.

A largo plazo `UserProfile.role == 'superadmin'` y `User.is_superuser == True`
**no representan exactamente el mismo concepto**: el primero es un rol de negocio
heredado y el segundo es el flag de Django que hoy usamos como administrador de
plataforma. Hoy coinciden porque `get_user_role()` los colapsa. Normalizarlos
corresponde a la fase de RBAC, no a esta.

**`UserProfile.role` sigue siendo la fuente autoritativa de permisos.**
`get_user_role()` y todas las clases de permiso previas están intactas; las
membresías se crean en paralelo para que una fase posterior pueda migrar la capa
de permisos sin una migración de datos a contrarreloj.

---

## 6. Endpoints

| Método | Ruta | Quién |
|---|---|---|
| `GET` | `/api/admin/companies/` | Miembro de alguna empresa activa (scopeado) |
| `POST` | `/api/admin/companies/` | Solo administrador de plataforma |
| `GET` | `/api/admin/companies/{id}/` | Solo empresas visibles para el llamante |
| `PATCH` | `/api/admin/companies/{id}/` | Solo administrador de plataforma |
| `GET`/`POST` | `/api/admin/branches/` | Miembro; crear exige ser admin de esa empresa |
| `GET`/`POST` | `/api/admin/memberships/` | Miembro; crear exige ser admin de esa empresa |
| `GET`/`PATCH` | `/api/admin/memberships/{id}/` | Ver: scopeado. Escribir: admin de la empresa |
| `GET` | `/api/me/memberships/` | Las propias del llamante |

`DELETE` no existe en ninguna: una empresa se desactiva con `is_active=False`.

---

## 7. Qué NO se tenantizó, y por qué

Análisis por entidad antes de tocar nada:

| Entidad | Debería ser | Por qué se aplaza |
|---|---|---|
| `Product` | de `Company` | Filtrar el catálogo sin resolución por host activa lo dejaría **vacío en producción**. Necesita host routing primero. |
| `Category` | de `Company` (o global compartida) | Decisión de producto sin resolver: ¿taxonomía propia por empresa o compartida? |
| `Order` | de `Company` | Se deriva de `Product`; tenantizar `Product` primero evita backfill ambiguo. |
| `CartItem` | de `Company` | Sesión anónima; depende de la resolución por host. |
| `StockMovement` | de `Branch` | El stock es por sucursal, no por empresa. Rediseña el servicio de inventario entero. |
| `SalesNote` | de `Company` | **`number` es único global**: con dos empresas los `NV-` se intercalarían. Requiere unicidad por empresa + rediseño del correlativo. |
| `Coupon` | de `Company` | Bajo riesgo, pero sin valor aislado. |
| `Review` | se deriva de `Product` | Sigue a `Product`. |
| `AdminAuditLog` | `company` nullable | **Hecho ya** — es aditivo y sin backfill. |

Ninguna relación se añadió "porque parecía conveniente".

---

## 8. Aislamiento y mitigaciones

- **Lecturas scopeadas, no filtradas por el cliente.** `scope_queryset()` y
  `visible_companies()` restringen cada queryset a las empresas donde el llamante
  tiene membresía activa. Sin membresía → queryset **vacío**, nunca el completo.
- **Escrituras validadas contra el acceso propio.** El `company` del body
  selecciona entre empresas que el llamante ya administra; no puede ampliar acceso.
- **Ids ajenos indistinguibles de inexistentes** — sin sondeo de ids.
- **Ver ≠ escribir.** Un `sales` ve su empresa pero no puede otorgar roles.
- **Membresía inactiva o empresa desactivada no conceden nada.**
- **Sucursal cruzada rechazada** en el modelo (`clean()`) y en la API
  (`assert_branch_in_company`).
- La seguridad **no** depende de filtros del frontend: el frontend no se tocó.

---

## 8-bis. RBAC tenant-aware (Fase 2A)

### Tres niveles de autoridad, separados a propósito

| Nivel | Se expresa como | Alcance | Quién lo concede |
|---|---|---|---|
| **PLATFORM** | `User.is_superuser` | Toda la plataforma | Solo Django admin / operador |
| **COMPANY** | `Membership.role` | Una sola empresa | Admin de esa empresa (o platform admin) |
| **LEGACY** | `UserProfile.role` | Global | Endpoint legacy de cambio de rol |

Ninguno implica otro:

- `Membership.role == "superadmin"` **no** hace `is_platform_admin` ni toca `User.is_superuser`.
- `UserProfile.role == "superadmin"` **no** concede autoridad SaaS nueva.
- Un platform admin **no tiene rol de empresa**: `get_company_role()` devuelve `None`
  para él. Tiene toda la autoridad, pero no es "admin de la empresa X".

### Matriz de capacidades

Fuente única de verdad: `tenancy.COMPANY_CAPABILITIES`. Las vistas nunca
redeclaran conjuntos de roles.

| Rol de empresa | view | manage_company | memberships | inventory | sales | technical_service |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| `customer` | — | — | — | — | — | — |
| `sales` | ✓ | — | — | — | ✓ | — |
| `inventory` | ✓ | — | — | ✓ | — | — |
| `technician` | ✓ | — | — | — | — | ✓ |
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `superadmin` *(legacy)* | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **platform admin** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

`customer` no aparece en ninguna capacidad — hay un `assert` a nivel de módulo que
lo garantiza. Todas son **company-scoped**: tenerlas en la empresa A no dice nada
sobre la B.

### Rol `superadmin` — deuda resuelta sin romper compatibilidad

El valor sigue en el modelo y en las migraciones. Como rol de `Membership` se
comporta igual que `admin` **dentro de su empresa** y nunca implica autoridad de
plataforma.

Lo que cambió: **un administrador de empresa ya no puede asignarlo**
(`GRANTABLE_BY_COMPANY_ADMIN` lo excluye) ni escalar una membresía existente a
ese valor. Solo un platform admin puede, porque puede necesitarlo para migración
y administración. Los datos existentes no se tocan.

### Escalada de privilegios — qué está bloqueado

Un administrador de empresa **no puede**:

| Intento | Resultado |
|---|---|
| Crear membresía en otra empresa | `404` (ni siquiera aprende que existe) |
| Modificar membresía de otra empresa | `404` |
| Convertir a alguien en platform admin | Imposible: la API nunca escribe `is_superuser` |
| Cambiar `UserProfile.role` | Imposible: la API nunca lo toca |
| Cambiar la `company` de una membresía | Campo ausente del serializer de update |
| Cambiar el `user` de una membresía | Campo ausente del serializer de update |
| Asignar `superadmin` | `403` |

Y un `sales`, `inventory` o `technician` de la propia empresa recibe `403` al
intentar administrar membresías — no `404`, porque la empresa sí es visible para
ellos; el problema es de autoridad, no de visibilidad.

### `CompanyContext`

`build_company_context(user, requested_company_id=None)` devuelve
`company`, `membership`, `role`, `is_platform_admin` y un `.can(capability)` que
reutiliza la misma matriz. Resuelve la empresa con `resolve_company_for_user()`,
así que hereda la regla de que un `company_id` del cliente solo **selecciona**
entre empresas ya accesibles.

### Enumeración de usuarios — mitigado parcialmente

`POST /api/admin/memberships/` devuelve **una sola respuesta** (`400`, mismo
texto) tanto si el usuario no existe como si ya es miembro, para que el endpoint
no sea un oráculo de ids de usuario a nivel plataforma. Tampoco devuelve username
ni email en el fallo.

Lo que **no** resuelve: un admin de empresa todavía puede añadir a su empresa a
cualquier usuario existente de la plataforma sin consentimiento, y así descubrir
su username en la respuesta de éxito. Arreglarlo requiere onboarding por
invitación — ver `PENDIENTE — Membership Invitation Flow`. No se improvisa aquí.

### Qué sigue siendo legacy, y por qué

`/api/admin/products/`, `/api/admin/orders/`, `/api/admin/inventory/`,
`/api/admin/orders/{id}/sales-note/`, checkout y webhook **siguen autorizando con
`UserProfile.role`**.

Razón: `Product`, `Order`, `StockMovement` y `SalesNote` **no tienen columna
`company`**. Cambiar sus permisos a `Membership` antes de tenantizar los datos
daría permisos con forma de tenant sobre filas globales compartidas — una falsa
sensación de aislamiento, que es peor que el estado actual porque invita a
confiar en ella. La migración de esos endpoints es Fase 2B/2C.

Las clases `CanManageCompanyInventory`, `CanManageCompanySales` y
`CanManageCompanyTechnicalService` ya existen y están testeadas, pero
deliberadamente **no están conectadas a ninguna URL** todavía.

---

## 8-ter. Áreas, roles y permisos configurables (Fase 2A.1)

### Tres superficies, separadas formalmente

Son **superficies, no tipos de usuario**. Un mismo `User` puede estar en varias a
la vez, y estar en una no lo excluye de otra.

```
AUTHENTICATION
    User
      │
      ├── PORTAL EXTERNO / E-COMMERCE
      │     disponible para CUALQUIER usuario
      │     y sus partes públicas también para anónimos
      │
      ├── CONTROL INTERNO
      │     requiere: User + Membership activa
      │               + Company activa + capabilities
      │
      └── PLATFORM CONTROL
            requiere: User.is_superuser
```

| Superficie | Requisito de acceso | Alcance |
|---|---|---|
| **PORTAL EXTERNO** | Cualquier usuario autenticado; anónimo en catálogo, carrito y checkout | Catálogo, productos, carrito, checkout, login, registro, cuenta, compras, reseñas |
| **CONTROL INTERNO** | `User` + Membership activa + Company activa + capacidades del rol | Panel operativo de la empresa |
| **PLATFORM CONTROL** | `User.is_superuser` — y solo eso | Todos los tenants |

> **Corrección respecto a la redacción inicial de esta fase.** Se dijo «portal
> externo = `User` sin Membership». Es incorrecto. Tener Membership **no** quita
> el acceso al e-commerce: un técnico de una empresa sigue comprando en la tienda
> con la misma cuenta. Lo que la Membership añade es el CONTROL INTERNO.

```
User Carlos
├── compra productos como cliente          → portal externo
└── Membership @ Black Dog Store           → control interno
      └── Técnico
```

**Una sola identidad**: no hay `CustomerUser`, `StaffUser` ni `MasterUser`. El
alcance sale de las relaciones del `User`, no de su modelo.

Ningún `CompanyRole`, `Membership`, `MembershipRoleAssignment` ni `CompanyArea`
puede convertir a alguien en MASTER. La única vía es `User.is_superuser`.

Un cliente **no** recibe Membership automáticamente, y ninguna API empresarial
escribe `is_superuser`, `is_staff` ni `UserProfile.role`.

### Catálogo de capacidades — decisión de arquitectura

Se evaluaron dos alternativas:

**A. Tabla `PermissionDefinition`** — capacidades como filas en la base.
**B. Catálogo en código** (`store/capabilities.py`), los roles guardan `code`.

**Elegida: B.** Razones:

| Criterio | Por qué gana B |
|---|---|
| Seguridad | La **plataforma** es dueña del vocabulario. Con una tabla, quien pudiera escribirla inventaría capacidades; el tenant definiría el alcance de su propia autoridad. Aquí solo elige de la lista. |
| Migraciones | Añadir o renombrar una capacidad es un cambio de código y un test, no una migración de esquema más una de datos por entorno. |
| Una sola verdad | La Fase 2A ya expresaba capacidades como constantes de código. Una tabla habría creado una segunda autoridad divergente. |
| Integridad | Validación contra el catálogo en `clean()` **y** en el serializer, que es donde vive el significado. |
| UI | `/api/admin/capabilities/` sirve el catálogo en solo lectura; el front no duplica la lista. |

`django.contrib.auth.Permission` se descartó: es global, está atado a modelos y
no tiene dimensión de tenant, así que no puede expresar *«esta capacidad, dentro
de esta empresa»*.

Cada capacidad declara su estado, sin fingir:

| Estado | Significa |
|---|---|
| `active` | La plataforma la aplica hoy (`company.*`, `memberships.*`, `areas.manage`, `roles.manage`) |
| `available` | El módulo existe pero sus endpoints siguen autorizando por RBAC legacy; asignable, aún no aplicada |
| `reserved` | El módulo **no existe** (`service.customers.*`, `service.devices.*`, `service.orders.*`, `service.diagnostic.*`, `service.repair.*`, `service.quality.*`). Listada solo para diseño y **no asignable** |

18 asignables, 10 reservadas. Un rol que intente reclamar una reservada es
rechazado con `400`.

### Áreas ≠ permisos

**Regla dura, con test:** pertenecer al área «Inventario» **no** otorga
`inventory.adjust`. La autoridad viene exclusivamente de las capacidades del rol.
El área sirve para organización, filtros, asignaciones, dashboards y reportes.
Desactivar un área tampoco cambia la autoridad de nadie.

### Varios sombreros, una sola membresía

```
Usuario X @ Empresa A          (una sola Membership)
  ├── Técnico    — área Taller
  └── Recepción  — área Recepción
```

`MembershipRoleAssignment` cuelga de la Membership, así que un usuario puede
llevar varios roles sin duplicar su pertenencia a la empresa. El mismo rol puede
repetirse en dos áreas distintas, nunca dos veces en la misma.

### Resolución de capacidades — exclusiva, no aditiva

```
1. Platform master  → todas las capacidades asignables, en cualquier empresa
2. Roles propios    → UNIÓN de las capacidades de sus asignaciones activas
                      (Membership.role se IGNORA)
3. Fallback legacy  → capacidades equivalentes a Membership.role
```

El punto 2 es el importante: **si una empresa modela a alguien con roles
personalizados, restringirlo lo restringe de verdad**. Sumar además el fallback
legacy devolvería en silencio lo que el rol personalizado le quitó.

Una empresa que no haya configurado ningún rol sigue funcionando exactamente
como en Fase 2A — hay un test que compara ambos sistemas rol por rol y capacidad
por capacidad.

### Escalada de privilegios — política adoptada

**Un administrador de empresa solo puede delegar capacidades que él mismo tiene.**
Se aplica al crear un rol, al editar sus capacidades y al asignarlo (incluida la
reactivación de una asignación). Sin esta regla, un admin limitado podría
escribir un rol poderoso, asignárselo y escalar.

El platform master está exento, porque necesita poder configurar tenants desde cero.

### `superadmin` legacy

Sin cambios respecto a 2A: sigue siendo autoridad **solo dentro de su empresa**,
nunca implica `User.is_superuser`, y un admin de empresa no puede asignarlo.

---

## 8-quater. Provisioning de nuevas empresas

La migración `0017` sembró presets **solo para las empresas que existían cuando
corrió**. Una empresa creada después llegaría sin áreas ni roles.

`store/company_provisioning.py` es la **única fuente en tiempo de ejecución** de
esos defaults:

```python
provision_company_access_defaults(company) -> dict
```

- **Idempotente**: casa por `(company, slug)`; lo que ya existe se deja como está.
- **No sobrescribe** un preset que el operador editó, renombró o desactivó.
- **No asigna** roles a nadie, no crea Membership, no toca `UserProfile.role`,
  `Membership.role`, `is_superuser` ni `is_staff`.
- **Neutral**: un test escanea el archivo entero — prosa incluida — y falla si
  aparece el nombre, razón social o RUC de cualquier tenant.

Caminos que lo invocan hoy:

| Camino | Cómo |
|---|---|
| `POST /api/admin/companies/` | Creación y provisioning en **una sola transacción**: si el provisioning falla, la empresa hace rollback. Una empresa a medio configurar es peor que una que nunca se creó, porque el fallo es silencioso. |
| Django Admin → crear Company | `CompanyAdmin.save_model()` con `change=False`. Llamada explícita, no signal: un signal dispararía en cada escritura de Company (incluido el modelo histórico de `0015` y las fixtures), sería sorprendente y difícil de testear. |

Futuros comandos de onboarding, imports y pantallas de Platform Control **deben
usar este mismo servicio**. No se replica la lista de presets en otro sitio.

> **Sobre la copia dentro de `0017`:** la migración conserva su propia lista
> congelada a propósito, y eso **no** es duplicación a refactorizar. Una migración
> debe reproducir lo que hizo cuando corrió; importar este módulo dejaría que un
> cambio futuro del catálogo reescribiera la historia. `0017` es registro
> histórico; este módulo es el default actual. Se les permite divergir.

---

## 8-quinquies. Mapa oficial del CONTROL INTERNO

Mapa funcional aprobado. **Es un mapa, no una funcionalidad**: el estado de cada
módulo refleja el código real, no el diseño.

| Módulo | Submódulos | Estado real |
|---|---|---|
| **Dashboard** | operativo avanzado | `PENDIENTE` |
| **Ventas** | Nueva venta / POS | `PENDIENTE` |
| | Pedidos | `IMPLEMENTADO` (legacy, no tenant-aware) |
| | Cotizaciones | `PENDIENTE` |
| | Notas de venta | `IMPLEMENTADO` (legacy) |
| | Devoluciones / anulaciones | `PENDIENTE` |
| | Cuentas por cobrar · Comisiones | `PENDIENTE` |
| **Caja** | apertura/cierre, ingresos, egresos, caja chica, métodos de pago | `PENDIENTE` |
| **Compras** | proveedores, cotizaciones, órdenes, recepción, cuentas por pagar, costos | `PENDIENTE` |
| **Clientes** | clientes, historial, equipos, garantías | `PENDIENTE` |
| | Fidelización (programas, reglas, premios, reportes) | `PROPUESTA` |
| **Productos** | Productos · Categorías | `IMPLEMENTADO` (legacy, no tenant-aware) |
| | Marcas · Precios · Promociones · Serial/IMEI | `PENDIENTE` |
| **Inventario** | Estado de stock · Kardex · Entradas · Salidas · Reportes | `IMPLEMENTADO` (legacy, no tenant-aware) |
| | Alertas · Transferencias · Recuentos · Reposición · Valorización · Revalorización | `PENDIENTE` |
| | Inventario tenant-aware | `PENDIENTE` |
| **Servicio Técnico** | recepción, órdenes, diagnóstico, cotización, aprobación, reparación, repuestos, evidencias, control de calidad, entrega, garantías | `PENDIENTE` |
| **Reportes** | Ventas · Inventario | `IMPLEMENTADO` (legacy, parcial) |
| | Compras · Clientes · Caja · Técnicos · Rentabilidad · Exportaciones | `PENDIENTE` |
| **Herramientas** | carga masiva, descargas en segundo plano, papelera | `PROPUESTA` |
| **Dispositivos** | impresoras, etiquetadoras, periféricos | `PENDIENTE` |
| **Fiscal / SUNAT** | — | `PENDIENTE` (fase separada) |
| **Administración** | Empresa · Sucursales · Usuarios internos · Áreas · Roles y permisos | `IMPLEMENTADO` |
| | Auditoría | `IMPLEMENTADO` (parcial: cubre acciones admin, no todo el dominio) |
| | Configuración / branding | `PENDIENTE` |

### Regla del sidebar futuro

```
módulo visible  =  módulo realmente implementado
                   AND
                   el usuario posee la capability necesaria
```

Lo que **no** queremos: que `roles.manage == true` haga aparecer un módulo que no
existe. Y en ningún caso el frontend es la autorización — **la decisión final
siempre está en el backend**; ocultar un botón no protege un endpoint.

### Capacidades `available` ≠ aplicadas

Recordatorio explícito: una capacidad en estado `available` significa que el
módulo existe y la capacidad es asignable, **no** que algún endpoint la consulte.
Hoy `products.*`, `inventory.*`, `sales.*`, `reports.*`, `settings.*` y
`service.manage` no gobiernan nada: esos endpoints siguen autorizando por
`UserProfile.role`. Un rol que las conceda no abre nada todavía.

---

## 8-sexies. Deuda arquitectónica: acceso multisucursal

`Membership.branch` es opcional y **single-valued**: una membresía apunta a una
sucursal o a ninguna. Eso no cubre los casos que el Control Interno va a
necesitar:

```
Gerente     → Sucursal A + B + C
Supervisor  → Sucursal A + B
Técnico     → Sucursal Taller
```

Hoy la única forma de expresar «varias sucursales» es dejar `branch = NULL`, que
significa «toda la empresa» — demasiado ancho para un supervisor de dos tiendas
de cinco.

Modelo posible para el futuro:

```
MembershipBranchAccess
  membership
  branch
  is_active
```

**No se implementa en esta fase.**

```
PENDIENTE — Branch access model
```

**Debe resolverse antes o durante la fase de inventario multisucursal.**
Tenantizar `StockMovement` por sucursal sin un modelo de acceso a múltiples
branches obligaría a elegir entre dar a cada usuario una sola sucursal o darle
todas — y migrar después sería mucho más caro que decidirlo ahora.

---

## 8-septies. Dashboard interno

Las referencias visuales inspiran: selector de empresa, selector de sucursal,
periodo, ventas, ticket promedio, utilidad, métodos de pago, ventas por empleado,
productos más vendidos, clientes, alertas, inventario y reparaciones.

```
Dashboard interno avanzado — PENDIENTE
```

**No se implementan estos KPIs ahora, y la razón importa:** `Product`, `Order` e
`Inventory` todavía no están tenantizados. Un dashboard que aparenta ser
multiempresa mostrando métricas globales es peor que no tener dashboard — invita
a tomar decisiones sobre datos de otra empresa creyendo que son propios.

---

## 8-octies. Control Interno v1 (Fase 2A.2)

### La superficie

`/admin` deja de ser «el panel admin del e-commerce» y pasa a ser una aplicación
empresarial con su propio marco: **sidebar + topbar**, no pestañas horizontales.
La ruta se conserva por compatibilidad; renombrarla a `/control` o `/workspace`
queda como decisión futura, no se toca ahora.

Las tres superficies siguen separadas: la tienda no cambió visualmente, y el
control interno no vive dentro de ella.

### Guard nuevo — `InternalControlGuard`

Entrar al control interno ya **no** significa `UserProfile.role == "admin"`.
Significa: **Membership activa en Company activa**, o platform master. Por eso un
vendedor o un técnico ahora pueden abrir el dashboard.

Abrir el dashboard **no** es abrir cada módulo: cada página conserva su guard
(`StaffGuard` / `AdminGuard`) y **cada endpoint sigue decidiendo en el backend**.

> **Fallback legacy, deliberado.** Un operador con rol legacy pero **sin**
> Membership — el estado de todos los operadores actuales hasta que las empresas
> adopten membresías — sigue entrando. El guard pasa con `dashboard === null` y el
> shell renderiza en modo legacy. Exigir Membership aquí habría dejado fuera a
> quienes usan el panel hoy.

### Endpoint agregado

`GET /api/me/internal-dashboard/[?company=<id>]` devuelve una sola fotografía
segura: empresa, membresía, sucursal, roles, áreas, capacidades, contadores de
organización y avisos.

**Lo que NO devuelve, y por qué:** ninguna cifra de ventas, ingresos, pedidos,
stock, utilidad, productos más vendidos ni clientes. `Product`, `Order` y
`StockMovement` no tienen columna `company`, así que cualquiera de esas cifras
sería **global** mostrada dentro de un marco por empresa. Un número global en un
dashboard de tenant no es una imprecisión menor: se lee como el dato de esa
empresa. Llegan con 2B/2C, al mismo `MetricCard`.

`?company=` es input no confiable: pasa por `resolve_company_for_user()`, que
solo **selecciona** entre empresas que el llamante ya alcanza. Una empresa ajena
responde exactamente igual que una inexistente.

| Situación | Respuesta |
|---|---|
| Sin Membership y sin `is_superuser` | `403` |
| Membership o empresa inactiva | `403` |
| Una sola Membership | `200`, su empresa, sin selector |
| Varias Membership sin elegir | `200`, `requires_company_selection: true`, sin datos |
| Platform master sin elegir | igual — **nunca se le adivina un tenant** |
| Empresa ajena o inexistente | `404`, mismo texto |

Los contadores de organización solo se devuelven a quien tiene `company.view`:
también son información de la empresa.

### Avisos

Solo condiciones **realmente derivables** de forma segura: empresa desactivada,
platform master viendo una empresa sin pertenecer, membresía sin sucursal,
membresía sin capacidades, rol asignado sin permisos. **No** se fabrican avisos
comerciales tipo «3 productos sin stock»: el inventario no está tenantizado, así
que ese número sería global.

### Registro de módulos

`frontend/app/admin/lib/internal-modules.ts` es la fuente única de la navegación,
los accesos rápidos y el mapa de módulos. **Es metadata UX, no autorización.**

```
visible y clickeable = status === "implemented"
                       AND el usuario tiene acceso
                       AND el módulo tiene href
```

Un módulo inexistente **nunca** se vuelve un enlace muerto: aparece únicamente en
el mapa del dashboard, con su estado real.

Declara **dos predicados a propósito**: `requiredCapabilities` (el modelo
objetivo) y `legacyRoles` (lo que el endpoint comprueba hoy). Declarar solo
capacidades haría que el sidebar mostrara enlaces que luego dan `403` — peor que
un chequeo de rol, porque parece correcto. Según cada módulo se tenantice,
`legacyRoles` desaparece y `requiredCapabilities` pasa a gobernar, sin tocar el
resolver.

### MASTER

El badge sale de `access.is_platform_admin`, que el backend deriva de
`User.is_superuser` **y solo de eso** — nunca de un rol llamado `superadmin`, que
es un valor legacy con alcance de empresa. Un master sin empresa elegida ve
«Selecciona una empresa», no un tenant arbitrario. Las herramientas globales de
plataforma son otra superficie y siguen **PENDIENTE**.

### Sucursal

Se muestra `Sucursal: <nombre>` cuando existe, y `Alcance: empresa` cuando
`Membership.branch` es `NULL`. **No** se añadió selector multisucursal: sería una
UI que permite elegir sucursales que el modelo todavía no sabe restringir. Ver
`PENDIENTE — Branch access model`.

### Iconografía

SVG inline en `app/admin/components/icons.tsx`. **Sin dependencia nueva**: el
proyecto solo trae `next`, `react` y `react-dom`, y traer un paquete de iconos
completo para una docena de glifos era mal negocio.

---

## 9. Deuda pendiente

1. **Branding por empresa** — `_STORE_NAME`, `_STORE_RUC`, `_STORE_ADDRESS` y
   compañía siguen siendo constantes de módulo en `email_services.py` y
   `pdf_services.py`. Con dos tenants, el segundo recibiría emails y PDFs con los
   datos de Black Dog Store. **Bloquea la venta real a un segundo cliente.**
2. **Correlativo de `SalesNote` global** — `NV-` se intercalaría entre empresas.
3. **`get_user_role()` sigue siendo global** — un `admin` lo es en todas partes.
   La membresía todavía no gobierna los permisos.
4. **Catálogo público sin tenant** — devuelve todos los productos activos.
5. **Sin `NOT NULL`** en ningún FK nuevo de negocio: no se añadió ninguno.
6. **Sin UI administrativa** de empresas; Django Admin cubre esta fase.
7. **Bootstrap no neutral** — la migración `0015` crea el tenant piloto Black Dog
   Store. Es correcto para *esta* instalación, cuyos datos históricos son suyos,
   pero una instalación nueva vendida a un tercero no debe adquirir ese tenant en
   silencio. Hace falta un bootstrap neutral (comando de gestión que cree el
   primer tenant a partir de datos del operador, con la migración condicionada a
   que existan datos previos de tienda). No se rediseña ahora: cambiarlo alteraría
   la cadena de migraciones de la instalación en producción.
8. **`bulk_create()` / `queryset.update()` saltan `Membership.clean()`** — hoy nadie
   los usa para Membership; código futuro debe llamar `assert_branch_in_company()`.
9. **Capacidades `available` no aplicadas** — `products.*`, `inventory.*`,
   `sales.*`, `reports.*`, `settings.*` y `service.manage` son asignables pero
   ningún endpoint las consulta todavía: el dominio comercial sigue autorizando
   por `UserProfile.role`. Un rol que las conceda no abre nada aún. Se conectan
   en 2B/2C, cuando esos modelos tengan `company`.
10. **Módulo de servicio técnico inexistente** — las 10 capacidades `service.*`
   detalladas están reservadas y no son asignables. El portal del cliente
   (Mis equipos, Reparaciones, Cotizaciones, Garantías, Seguimiento) está
   PENDIENTE; cuando exista, el cliente no debe ver notas internas, costos
   internos, auditoría, otros clientes ni datos privados del técnico.
11. **PENDIENTE — Membership Invitation Flow.** Un admin de empresa puede añadir a
   cualquier usuario existente de la plataforma sin su consentimiento, y así
   confirmar su username. La mitigación actual uniforma las respuestas de error;
   la solución real es onboarding por invitación con aceptación del destinatario.
12. **PENDIENTE — Branch access model** — `Membership.branch` es single-valued;
   no expresa «este supervisor cubre A y B». Bloquea el inventario multisucursal.
13. **KPIs comerciales tenant-aware** — el dashboard tiene el marco visual
   (`MetricCard`) pero ninguna métrica comercial, porque sería global. Se llenan
   en 2B/2C.
14. **Pantallas de Empresa / Sucursales / Áreas / Roles** — las APIs existen desde
   2A.1, las pantallas no. Aparecen como `Parcial` en el mapa, sin enlace.
15. **Dashboard interno avanzado** — pospuesto a propósito hasta que el dominio
   comercial esté tenantizado.
16. **`frontend/db.sqlite3` está versionado** (0 bytes, de antes de que `.gitignore`
   cubriera `*.sqlite3`). Conviene sacarlo del índice en un commit aparte.

---

## 10. Próximas fases

**A. RBAC tenant-aware y aislamiento completo** — mover los permisos de
`UserProfile.role` a `Membership`, tenantizar `Product`/`Order`, activar la
resolución por host.

**B. Configuración y branding por empresa** — `CompanySettings` con nombre,
razón social, RUC, dirección, teléfono y logo; `email_services` y `pdf_services`
leyendo de ahí.

**C. Inventario serializado IMEI/serie** — trazabilidad por unidad.

---

## Usuarios demo de desarrollo — TEMPORAL

> **SOLO DESARROLLO · ELIMINAR ANTES DE PRODUCCIÓN**

Cuentas rápidas para probar roles sin crear usuarios a mano en cada prueba.

```bash
python manage.py seed_demo_users --company-slug <company-slug>
```

| Usuario | Perfil | Empresa |
|---|---|---|
| `dev_customer` | Cliente / e-commerce | — (sin Membership) |
| `dev_sales` | Ventas | la indicada |
| `dev_inventory` | Inventario | la indicada |
| `dev_technician` | Servicio Técnico | la indicada |
| `dev_admin` | Admin de empresa | la indicada |
| `dev_master` | PLATFORM MASTER (`is_superuser`) | — (sin Membership) |

Contraseña para todas: **`Demo123!`** — no es un secreto, es una fixture.

Eliminación:

```bash
python manage.py seed_demo_users --purge
```

**Garantías:**

- El comando **falla si `DEBUG=False`** y no ofrece ningún flag para saltárselo.
- **No hay bypass de autenticación**: estas cuentas entran por el login real, con
  JWT en cookie HttpOnly y CSRF, y pasan exactamente los mismos permisos que
  cualquiera. El bloque de `/auth` solo **rellena** el formulario.
- **No se apropia de cuentas reales**: la firma es el email `@example.invalid`.
  Si `dev_admin` existe con otra identidad, el comando **aborta**; `--purge`
  omite esa cuenta en vez de borrarla.
- **Sin migración, sin signal, sin auto-creación** al arrancar Django. Datos de
  desarrollo no son esquema de producción.
- Idempotente: ejecutarlo varias veces no duplica nada.
- El bloque de accesos rápidos de `/auth` **no se renderiza** fuera de
  `NODE_ENV === "development"` — no es un `display:none`.

**DEVELOPMENT BRIDGE / LEGACY TRANSITION:** los usuarios internos demo llevan a
propósito `UserProfile.role` **y** `Membership + CompanyRole + assignment`,
porque los endpoints comerciales aún autorizan por el primero y las APIs SaaS por
el segundo. Es un síntoma de la transición, no la arquitectura final.
