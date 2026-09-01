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
RESUELTO en la Fase 2D — ver «8-duodecies. Inventario multisucursal»
```

Se resolvió exactamente como anticipaba esta sección, con una corrección
importante: **`MembershipBranchAccess` por sí solo no basta.** Una tabla de
concesiones donde «cero filas = todas las sucursales» falla abierta — revocar la
última sucursal de alguien lo ascendería en silencio a todas — así que hizo falta
además un modo explícito, `Membership.branch_access_mode`.

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

## 8-nonies. Catálogo tenant-aware (Fase 2B)

### Grafo real, verificado en código

```
Company
  ├── Category  (PROTECT)
  └── Product   (PROTECT)
        ├── OrderItem.product      (PROTECT)   → tenant derivado, Fase 2C
        ├── CartItem.product       (CASCADE)   → tenant derivado, Fase 2C
        ├── Review.product         (CASCADE)   → tenant derivado
        └── StockMovement.product  (PROTECT)   → tenant derivado, Fase 2D
```

`Category` y `Product` ahora tienen dueño. Todo lo demás alcanza su tenant **a
través de `Product`** — no se añadió un `company` redundante a `Review`,
`CartItem` ni `OrderItem`: sería una segunda fuente de verdad que puede
desincronizarse.

### Unicidad por empresa

`Category.slug` y `Product.slug` dejan de ser únicos globalmente. Dos empresas
pueden tener cada una `iphone` e `iphone-15` — un `UNIQUE` global habría hecho la
plataforma invendible a un segundo revendedor Apple. Constraints nuevas:
`unique_category_slug_per_company` y `unique_product_slug_per_company`.

### Invariante producto/categoría

`Product.company == Product.category.company`. Se valida en tres sitios
independientes: `Product.clean()` (cubre toda escritura ORM), el queryset del
serializer (una categoría ajena ni siquiera es una opción válida) y una
comprobación explícita en `validate()`. Ningún camino depende de uno solo.

### Migraciones

| # | Qué hace |
|---|---|
| `0018_catalog_company_nullable` | `company` nullable, quita el UNIQUE global de los slugs, añade índices `(company, slug)`, `(company, is_active)`, `(company, category)` y las constraints por empresa |
| `0019_backfill_catalog_company` | Data migration: adopta el catálogo existente |
| `0020_catalog_company_required` | `company` pasa a obligatorio |

Tres pasos, no uno: hacer `NOT NULL` de golpe habría fallado en cualquier base
con catálogo. `0020` está escrita a mano porque `makemigrations` pregunta
interactivamente por un valor por defecto, y la respuesta no es un valor — es
«0019 ya llenó todas las filas».

**Cómo identifica `0019` al tenant piloto:** por la **firma** de la migración
`0015`, es decir la `Company` más antigua (menor pk), no por el slug
`black-dog-store`. Una instalación cuyo primer tenant sea otro negocio
backfillea sobre *su* primer tenant sin tocar código. Si no existe ninguna
empresa, la migración **falla ruidosamente** en vez de inventar un dueño.

Probado: cadena limpia `0001→0020` desde cero; upgrade sobre una base con
catálogo, pedidos, carritos y reseñas históricos **sin pérdida** (precio, stock,
imagen y relaciones intactos); reverso `0020→0018` sin borrar nada.

### Resolución de tenant — storefront público

`resolve_storefront_company(request)`, en orden:

1. **Host** — `blackdog.example.com` → `Company.slug == "blackdog"`. Lo fija DNS
   y el proxy, no el JavaScript de la página.
2. **`DEFAULT_STOREFRONT_COMPANY_SLUG`** — despliegue explícito de una sola tienda.
3. **Empresa activa única** — solo cuando la base tiene exactamente **una**.

Sin resolución → **catálogo vacío**. Un storefront vacío es el fallo seguro;
servir productos de otro no lo es.

> **No existe** un fallback a «la primera empresa de la base». El paso 3 no es
> «la primera de muchas», es «la única», y deja de aplicar en cuanto existe una
> segunda — que es justo cuando la variable pasa a ser obligatoria. Ese paso
> también es lo que evita que un despliegue de una sola tienda se quede a oscuras
> al aplicar estas migraciones.

Para desarrollo en `localhost` no hace falta configurar nada mientras haya una
sola empresa. En cuanto crees la segunda, define
`DEFAULT_STOREFRONT_COMPANY_SLUG` o sirve por subdominios.

### Catálogo público aislado

Los querysets **nacen scopeados**: `storefront_products()` /
`storefront_categories()` filtran por tenant *antes* que slug, categoría,
búsqueda u orden. Nunca se busca en global para luego ocultar en el serializer —
eso se filtra por `count()`, paginación y ordenación mucho antes de llegar al
serializer.

Reseñas: se filtran por `product__in=storefront_products(request)`.

### Límite del carrito

`Cart` es Fase 2C, pero tenantizar `Product` abre un vector nuevo: un storefront
podría meter el id de un producto ajeno en su carrito y arrastrarlo al checkout.
`/api/cart/add/` busca ahora dentro de `storefront_products(request)`, así que un
producto de otro tenant responde igual que uno inexistente. **No** se añadió
`Cart.company`.

### Capabilities reales

`products.view` y `products.manage` son ya **autoridad efectiva** sobre
`/api/admin/products/` y `/api/admin/categories/`. Las clases de permiso DRF
legacy se retiraron de esas vistas: no podían expresar la decisión, porque *en
qué empresa actúas* determina *qué puedes hacer*, y además rechazaban a un admin
SaaS cuyo `UserProfile.role` sigue siendo `customer` — el estado normal de un
usuario creado por la API de membresías.

### Bridge legacy — acotado al piloto

Un operador con rol legacy y **sin** Membership resuelve al **tenant piloto y a
ninguno más**, y su autoridad sigue siendo su rol legacy. La lectura peligrosa
—«admin legacy ve todo»— habría significado que el personal de una empresa
administre el catálogo de otra.

Un **platform master está excluido del bridge**: no es un operador pre-SaaS, y
elegirle un tenant en silencio contradice que su función es actuar entre tenants.
Debe nombrar la empresa con `?company=`.

> **Cambio de comportamiento:** `GET /api/admin/products/` sin `?company=`
> devuelve `403` a un superusuario. Con el catálogo tenantizado ya no existe
> «todos los productos».

`?company=` se lee **solo del query string**, nunca del body: seleccionar
contexto y enviar un payload son cosas distintas, y una clave `company` perdida
dentro de un producto no debe cambiar el tenant sobre el que actúas.

---

## 8-decies. Dashboard visual (Fase 2B.1)

### Qué se ve, y qué deliberadamente no

El dashboard del control interno pasa de una lista de contadores a una vista de
gestión: cabecera con contexto, fila de KPIs, cuatro gráficos, mi acceso, avisos,
accesos rápidos y cobertura del sistema.

**Gráficos activos** — todos calculados con un filtro `company=` explícito:

| Gráfico | Tipo | Datos | Gate |
|---|---|---|---|
| Estado del catálogo | Anillo | publicados vs ocultos | `products.view` |
| Productos por categoría | Barras | composición del catálogo | `products.view` |
| Personal por área | Barras | asignaciones activas | `company.view` |
| Personal por rol | Barras | asignaciones activas | `company.view` |
| Cobertura de módulos | Barra apilada | estado del sistema (frontend) | — |

**Ausentes a propósito:** ventas por día, utilidad, ticket promedio, ingresos,
egresos, métodos de pago, pedidos por estado, stock por sucursal, más vendidos,
mejores clientes, caja, compras, reparaciones, garantías y comisiones.

`Order` y `StockMovement` **no tienen columna `company`**. Cualquiera de esas
cifras sería un número **de toda la plataforma** dibujado dentro del marco de una
empresa — y un número global en un dashboard de tenant no se lee como un error de
alcance, se lee como el dato de esa empresa. Llegan con 2C/2D, a estas mismas
tarjetas.

### Por qué SVG a mano y no una librería de gráficos

El proyecto tiene **tres dependencias de runtime**: `next`, `react`, `react-dom`.
Recharts —o cualquier opción basada en d3— triplicaría ese grafo para tres formas
simples: barras horizontales, un anillo y una barra apilada. Ninguna necesita
escalas, ejes, transiciones ni hit-testing más allá de un `title`.

La paleta lo decide igual de claro. Black Dog Store es **estrictamente monocroma**
(`#080808` / `#111111` / `#1a1a1a`, texto zinc), así que casi todo lo que aporta
una librería de charts —escalas categóricas de color, temas, leyendas en doce
tonos— es exactamente lo que **no** debe aparecer. Devolver los defaults de una
librería a escala de grises cuesta más que dibujar las formas.

Mismo criterio que `icons.tsx`. La magnitud se codifica con **opacidad**, no con
tono: mantiene la marca y sigue siendo legible para daltonismo.

### Accesibilidad de los gráficos

Un gráfico es una imagen para un lector de pantalla. Cada uno lleva `role="img"`
con `aria-label` descriptivo **y una tabla oculta** (`sr-only`) con las mismas
cifras — los datos se leen, no solo la figura.

### Series del endpoint

`GET /api/me/internal-dashboard/` añade, con el **mismo gate de capacidad** que
los totales que acompañan:

- `catalog.inactive_products`, `catalog.products_per_category`
- `organization.assignments_per_area`, `organization.assignments_per_role`

Una distribución es información de la empresa igual que un total. Cada fila lleva
solo `{label, value}` — ningún id ni campo interno se filtra por un payload de
gráfico. Las series están **acotadas a 8 buckets**: un gráfico no es un volcado
de datos.

Los productos sin categoría aparecen como su propio bucket en lugar de omitirse:
un gráfico que descarta filas en silencio contradice el total que tiene al lado.

### Paleta

Sin colores nuevos. Se usan los tokens de `globals.css`: fondo `#080808`,
superficie `#111111`, blanco a baja opacidad para elevación, escala zinc para
texto, y la textura `dot-grid` que ya formaba parte del lenguaje visual. Los
únicos acentos cromáticos son los estados de aviso (rojo/ámbar a muy baja
opacidad), que ya existían.

---

## 8-undecies. Comercio tenant-aware (Fase 2C)

### Grafo cerrado

```
Company
 ├── Category ── Product ──┬── CartItem   (tenant lógico, sin columna)
 │                         ├── OrderItem  ── Order ── Company  (explícito)
 │                         ├── Review
 │                         └── StockMovement
 └── Coupon
```

**Invariante:** `Order.company == item.product.company` para todos los items.

### Por qué `Order.company` explícito y no derivado

El tenant **no** se infiere de los items en cada lectura. La orden la usan la
administración, los reportes, el dashboard, el fulfillment, la auditoría, el
portal del cliente, el webhook y los emails; hacer que cada uno la re-derive por
un join sería lento y fácil de equivocar una vez. La invariante se impone al
escribir: `OrderItem.clean()` para escrituras de objeto y
`assert_items_match_order()` para las masivas — **`bulk_create()` no llama a
`clean()`**, así que una comprobación a nivel de conjunto tenía que existir.

### Carrito sin modelo `Cart`

Un carrito es `session_key` + la empresa del storefront, derivada por
`CartItem.product.company`. **No se añadió** un modelo `Cart` ni una columna
`CartItem.company`: ninguno diría nada que el producto no diga ya, y un campo
duplicado es una segunda fuente de verdad que puede desincronizarse.

Consecuencia deliberada: **un navegador puede tener varios carritos a la vez**,
uno por storefront, compartiendo session key. Es correcto — la misma persona
comprando en dos tenants tiene dos carritos, y vaciar uno no debe tocar el otro.

Todas las operaciones están scopeadas: listar, añadir, actualizar cantidad,
borrar y la carga del checkout.

### Cupones

`Coupon.company` con `UNIQUE(company, code)`. Dos empresas pueden correr
`BIENVENIDO10` a la vez; honrar el descuento de otra sería a la vez una fuga y un
error financiero. El checkout busca `Coupon.objects.filter(company=..., code=...)`,
nunca global.

### Checkout

El tenant sale del **storefront**, nunca del body. Un campo `company` en el
payload no se consulta en ningún punto del flujo. El carrito se carga ya
scopeado, así que un carrito con productos de otro tenant sencillamente **no es
visible** para este checkout: el estado mixto no llega al paso de crear la orden.

### Webhook — resolución de tenant

`Order.company`, de la base de datos, y de ningún otro sitio.

- **No del host**: Stripe llama a un único endpoint, así que el host no dice nada
  sobre quién vendió.
- **No de la metadata**: enviamos `company_id` a Stripe y vuelve; es dato que pasó
  por un tercero, así que solo puede **contrastarse** contra la base, nunca
  imponerse sobre ella. Si no coincide, se registra y se rechaza.

### Limpieza del carrito post-pago

Antes borraba todo el `session_key`. Ahora borra
`session_key + product__company=order.company`: pagar en un storefront no puede
vaciar el carrito que el navegador tiene en otro.

### Historial del cliente

Un mismo `User` puede comprar en varias empresas — es **una identidad, no varias**.
Pero dentro del storefront A solo ve sus pedidos de A: listar los de B filtraría
lo que compró en otro sitio hacia la cuenta de un negocio sin relación. Conocer el
id de un pedido ajeno devuelve `404`.

> **Cambio de comportamiento:** `/api/orders/` devolvía **todos** los pedidos de
> la base a cualquier usuario de staff. Con los pedidos tenantizados eso era una
> fuga cross-tenant, así que ese atajo desapareció. La administración interna vive
> en `/api/admin/orders/`, que scopea por empresa y comprueba capacidades.

### Administración de pedidos

`sales.orders.view` / `sales.orders.manage` son ya autoridad efectiva. Listado,
detalle, fulfillment, PDF de recibo, reenvío de email y notas de venta están
scopeados; un pedido ajeno responde igual que uno inexistente. El reenvío de email
sigue siendo **solo admin**: pone un mensaje en la bandeja de un cliente, que es
una autoridad más estrecha que mover un pedido por el fulfillment.

> **Cambio de comportamiento:** un superusuario debe indicar `?company=`. Con los
> pedidos tenantizados ya no existe «todos los pedidos».

### KPIs comerciales — qué cuenta como venta

Un pedido **pagado**, y nada más. `pending_payment`, `failed`, `cancelled`,
`expired` y `refunded` no son ingresos; contarlos inflaría cada cifra del
dashboard y lo volvería inútil para decidir.

Los ingresos se fechan por **`paid_at`**, no `created_at`: un pedido creado ayer y
pagado hoy es dinero de hoy. Las fechas se calculan en la zona horaria del
proyecto, así que «hoy» es el hoy del operador.

KPIs: ventas de hoy, pedidos de hoy, ticket promedio, ingresos totales, pedidos
pagados, pendientes de pago, por despachar. Gráficos: ventas de los últimos 7
días (los días sin ventas se dibujan en cero, no se omiten — un hueco se lee como
dato faltante) y pedidos por estado. Todo bajo `sales.orders.view`.

> **No se muestra utilidad ni margen.** El sistema no tiene modelo de costos, así
> que cualquier «utilidad» sería ingresos con una resta inventada. Un dashboard
> que adivina es peor que uno que calla.

### Inventario

`Order.company == Product.company == StockMovement.product.company` para toda
`sale_exit`. Eso cierra el cruce **a nivel de empresa**. El stock por sucursal
sigue siendo Fase 2D.

---

## 8-duodecies. Inventario multisucursal (Fase 2D)

La fase que convierte «cuánto tenemos» en una pregunta que no se puede hacer sin
preguntar también «¿dónde?».

### El grafo, antes y después

```
ANTES                              DESPUÉS
Company                            Company
 ├── Branch      (decorativa)       ├── Branch            ← ubicación real de stock
 ├── Product                        ├── Product
 │    └── inventory  ← la verdad    │    └── inventory    ← agregado de compatibilidad
 └── StockMovement                  ├── BranchStock(branch, product) ← LA VERDAD
      └── product                   ├── StockMovement
                                    │    ├── company + branch
                                    │    ├── transfer (FK)
                                    │    └── inventory_count (FK)
                                    ├── StockTransfer + items
                                    └── InventoryCount + items
```

### Dos ejes de autoridad, y ambos deben pasar

```
capability   QUÉ puedes hacer      inventory.view / adjust / reports
branch       DÓNDE puedes hacerlo  Membership.branch_access_mode + MembershipBranchAccess
```

Ninguno implica al otro. `inventory.adjust` **no** es permiso para ajustar todas
las sucursales, y llegar a una sucursal **no** es permiso para mover su stock.
Confundirlos es el error que esta separación existe para impedir, y hay un test
para cada dirección.

### Modo de acceso — por qué no basta una tabla de concesiones

```
ALL       todas las sucursales ACTIVAS de la empresa, incluidas las que se abran
          mañana. Para dueños y negocios pequeños, donde restringir sería fricción.
SELECTED  exactamente las concesiones activas, y ninguna más. Una sucursal nueva
          NO se concede automáticamente — ése es justamente el punto del modo.
```

`SELECTED` con cero concesiones significa **ninguna sucursal**. Es un estado real
(alguien a quien todavía no han ubicado) y **deniega**.

El diseño obvio era una tabla de concesiones donde «sin filas = todas». Se
descartó porque falla abierta: revocar la última sucursal de una persona la
ascendería a todas, y un bug que borrase concesiones ampliaría el acceso en vez
de reducirlo.

`Membership.branch` sobrevive como **sucursal predeterminada** — por cuál abre el
Control Interno — y **ya no decide nada**. Está marcado LEGACY/DEPRECATED en el
modelo; eliminarlo en la misma fase que cambia su significado habría hecho la
migración irrevisable.

### `Product.inventory` — estrategia de compatibilidad

Se eligió la opción **B: agregado mantenido transaccionalmente**.

```
BranchStock.quantity   FUENTE DE VERDAD, por sucursal
Product.inventory      SUM(BranchStock.quantity) de la empresa, mantenido en la
                       MISMA transacción por inventory_services
```

Por qué no las otras dos:

- *Eliminarlo* rompería el catálogo público, la lista de productos del admin y
  varios reportes que llevan exponiendo ese nombre desde la Fase 0, a cambio de
  nada.
- *Dejarlo desincronizado* crearía una segunda fuente de verdad, que es peor que
  no tener el campo.

Es **derivado**: nada fuera de `inventory_services` lo escribe, y **ninguna**
decisión sobre si una venta puede cumplirse sale de él — esa pregunta es siempre
«cuánto hay en ESTA sucursal», y sólo `BranchStock` la responde.
`product_inventory_drift()` demuestra la invariante; hay tests que la ejercen
para transferencias, recuentos y cada tipo de movimiento.

Rutas de escritura directa **eliminadas** en esta fase:

| Ruta | Antes | Ahora |
|---|---|---|
| `POST /admin/products/{pk}/inventory-adjust/` | `update(inventory=F(...))`, sin Kardex, sin tenant | movimiento manual por el service layer, con sucursal |
| `POST /admin/products/` con `inventory` | se escribía en la fila | movimiento `initial_stock` en la sucursal del operador |
| `PATCH /admin/products/` con `inventory` | se escribía en la fila | **400** — el stock se mueve, no se edita |

### Sucursal de despacho — de dónde vende la tienda online

El e-commerce no tiene selector de sucursal: el cliente compra y paga sin nombrar
un sitio. Alguien tiene que decidir de qué sucursal salió la venta, y «la que
devuelva primero la query» no es una decisión, es un bug que sólo aparece cuando
la empresa abre su segunda sucursal.

```
Company.default_inventory_branch   la empresa lo declara, una vez
Order.fulfillment_branch           se estampa en el checkout, y no se vuelve a decidir
```

`company_fulfillment_branch()` resuelve en este orden y no hay un tercero:

1. `default_inventory_branch`, si está y sigue activa.
2. La **única** sucursal activa — inequívoco por construcción, no «la primera de
   varias» sino «la única que hay». Sin esta regla, toda instalación de una sola
   tienda dejaría de vender hasta configurar un campo.

Con dos o más sucursales activas y sin default: `None`, y el checkout **se niega
y lo dice**. Elegir una en silencio sería despachar desde una tienda que no sabe
que vendió.

El catálogo público expone en `inventory` el stock de **esa** sucursal, no el
agregado: mostrar 20 cuando el checkout sólo puede entregar 2 es prometer una
venta que va a fallar en el último paso.

### Kardex — `stock_before` / `stock_after` cambiaron de significado

Son el saldo **de esa sucursal**, no un total de empresa. Es la única lectura que
hace auditable un Kardex: el saldo de una sucursal tiene que poder reconstruirse
desde sus propias líneas. Los movimientos migrados pertenecen a la sucursal que
eligió la migración 0025, y sus snapshots son los totales previos a 2D —
correctos, porque en ese momento la empresa tenía una sola ubicación de stock.

### Transferencias — el stock se mueve en los BORDES

```
BORRADOR ──despachar──▶ EN TRÁNSITO ──recibir──▶ RECIBIDA
    │                   (origen −q)              (destino +q)
    └──anular──▶ ANULADA
```

No existe una escritura que haga las dos cosas. Acreditar el destino al despachar
mostraría stock en una tienda que físicamente no lo tiene, y todo recuento allí
quedaría equivocado por el contenido de una furgoneta.

Ambos bordes son **idempotentes comprobando el estado bajo un row lock**, no
confiando en que nadie haga doble clic.

**Una transferencia despachada no se anula.** Es una negativa deliberada, no una
función que falte: sus unidades ya salieron del origen, y devolver el estado
atrás las repondría en la base de datos mientras viajan en una furgoneta.
Revertir un despacho exige movimientos compensatorios, que V1 no implementa.

### Recuentos físicos — la relectura es todo el punto

Contar no es instantáneo: alguien recorre las estanterías una hora mientras la
tienda sigue vendiendo. Por eso cada línea guarda **tres** números:

```
theoretical_at_start      lo que decía el sistema al empezar   ← evidencia
physical_quantity         lo que la persona encontró
theoretical_at_approval   lo que dice el sistema AL APROBAR, releído bajo lock
```

La corrección aplicada es `physical − theoretical_at_approval`. Usar la foto
inicial descontaría en silencio todo lo vendido durante el conteo, destruyendo
stock e ingresos reales de una sola vez.

Un producto sin cantidad física registrada se **omite**. «Nadie contó esto» no es
«no hay ninguno de éstos».

### Reposición — sugiere, no ejecuta

```
suggested = max(target_stock − quantity, 0)   cuando quantity <= minimum_stock
```

No abre compras ni transferencias. `surplus_branches` muestra dónde puede haber
unidades dentro de la empresa; moverlas es una transferencia que el operador abre
a propósito y que se verifica cuando la abre.

### Valorización — qué NO se muestra

El único número monetario es **stock × precio de VENTA**, etiquetado así en la
API (`inventory_value_basis: "sale_price"`) y en la UI («A precio de venta — no
es costo»). No hay utilidad, margen ni «capital invertido»: el sistema no
registra precio de compra, así que las tres serían una resta a un número que
nadie proporcionó.

### Concurrencia y orden de bloqueo

Toda operación que toca más de una fila de stock las bloquea en orden ascendente
`(branch_id, product_id)`, vía `_locked_branch_stocks()`. Dos operaciones
concurrentes sobre conjuntos solapados piden sus locks en la misma secuencia y
hacen cola en vez de bloquearse mutuamente.

El lock es sobre `BranchStock`, **nunca** sobre `Product`: bloquear el producto
serializaría todas las sucursales de una cadena entre sí para el mismo artículo,
convirtiendo tiendas sin relación en la cola una de la otra.

**Limitación de SQLite:** `select_for_update()` es un no-op — el motor serializa
escrituras con un lock global. El test de carrera real sólo corre en un backend
con locking por fila; en SQLite se **salta explícitamente** en vez de fingir que
pasa. Lo que sí se verifica en cualquier backend: el invariante secuencial (el
stock nunca baja de cero) y, por introspección, que el lock está sobre
`BranchStock` con el orden documentado.

### Idempotencia de `sale_exit`

La clave sigue siendo `(order, product)`, y eso sigue siendo correcto porque un
pedido tiene **exactamente una** `fulfillment_branch`. Añadir la sucursal a la
clave la ampliaría y debilitaría la garantía, no al revés. Si en el futuro un
pedido puede salir de varias sucursales, esta clave debe cambiar con ese diseño.

Ante stock insuficiente tras el pago: se registra el faltante en
`order.payment_error` con producto, sucursal y pedido, y **nunca** se cubre desde
otra sucursal — eso crearía una segunda discrepancia invisible donde nadie mira.

### Migración 0025 — se niega antes que adivinar

Regla, en orden:

1. `settings.INVENTORY_MIGRATION_BRANCHES` nombra la sucursal explícitamente.
2. La empresa tiene **exactamente una** sucursal activa.
3. Cualquier otra cosa → **RuntimeError** con los ids afectados y qué configurar.

Con dos sucursales y un entero no existe ningún dato que diga dónde están las
unidades. Repartirlas, o tomar el id más bajo, escribiría una cifra que *parece*
autoritativa y es ficción; todo recuento, reporte y decisión de reposición
posterior la heredaría, y nadie se enteraría nunca, porque una cifra de stock
equivocada se ve exactamente igual que una correcta.

Negarse es ruidoso, ocurre una vez, en el deploy, delante de quien puede
responder. Ése es todo el intercambio: cinco minutos de interrupción en lugar de
corrupción silenciosa.

---

## 8-terdecies. Configuración y branding por empresa (Fase 3)

La fase que saca del runtime la identidad de una empresa concreta.

### Qué había, exactamente

Tres servicios comerciales llevaban cada uno su copia de seis constantes de
módulo — `_STORE_NAME`, `_STORE_LEGAL_NAME`, `_STORE_RUC`, `_STORE_ADDRESS`,
`_STORE_PHONE`, `_STORE_WHATSAPP_LINK` — con los valores literales del tenant
piloto. No eran valores por defecto: eran la identidad legal de un negocio
concreto compilada en el código. Un segundo tenant habría enviado a **sus**
clientes un email y un PDF con el nombre y el RUC de **otra** empresa.

### `Company` vs `CompanySettings`

```
Company            ESTRUCTURAL — quién es este tenant para la plataforma
  name / legal_name / tax_id     identidad, editable por el negocio
  slug                           routing            ← decisión de plataforma
  is_active                      puede operar       ← decisión de plataforma

CompanySettings    OPERATIVO — cómo se presenta y cómo habla con sus clientes
  contacto · branding · políticas · timezone · currency · notificaciones
```

Separarlos significa que el endpoint que usa un administrador de empresa **no
puede alcanzar** `slug` ni `is_active` — no porque un serializer se acuerde de
excluirlos, sino porque no están en la tabla que escribe.

**No se duplican** `name`, `legal_name` ni `tax_id`. Un `public_name` junto a
`Company.name` crearía dos respuestas a «cómo se llama este negocio» sin una
regla que diga cuál gana.

### La regla del fallback

```
CompanySettings.<campo>  →  Company.<equivalente>  →  VACÍO
```

Y se detiene ahí. **Nunca cae en los valores del piloto.** Una empresa que no ha
puesto su dirección no muestra dirección; no muestra la de otra. Vacío es un
estado visible y corregible. Equivocado no lo es.

El piloto conserva su identidad porque la migración 0028 la **escribió en su
propia fila de `CompanySettings`** —como dato, que es donde va— no porque quede
código que sepa de qué empresa se trata. Un test escanea los tres servicios
comerciales y falla si alguno de esos literales reaparece.

### Snapshot histórico

Cada documento de un pedido lleva la identidad legal del vendedor. Generarla
desde la configuración ACTUAL significa que un negocio que se muda, se renombra o
se vuelve a registrar reescribe en silencio lo que dice un recibo de hace seis
meses — y un cliente con la copia impresa encontraría que ya no coincide con la
que el sistema reimprime.

```
Order.company_snapshot   se congela al crear el pedido
   → documentos históricos (email, PDF, nota de venta)

CompanySettings actual
   → storefront y todo lo demás
```

Los pedidos anteriores a la Fase 3 recibieron su snapshot en la migración, con la
identidad vigente en ese momento. **Limitación documentada:** para un tenant no
piloto, ese snapshot es la identidad de hoy, no la del día de la venta — la
plataforma nunca registró la anterior, e inventarla sería peor que registrar la
verdad disponible.

### Dirección legal vs punto de retiro

Responden preguntas distintas: una es quién factura, la otra es a qué puerta
llama el cliente. `order_pickup_location()` prefiere la sucursal congelada en el
pedido, luego la sucursal de despacho viva, y sólo como último recurso la
dirección legal — devolviendo `source` para que el documento pueda etiquetarlo
con honestidad en lugar de imprimir una oficina bajo el título «punto de retiro».

### Notificación interna — el cambio de ruteo

`settings.ORDER_NOTIFICATION_EMAIL` guardaba **una** dirección. En una
instalación multiempresa eso es una fuga con sello de aprobación: las ventas de
un segundo tenant —nombre del cliente, teléfono, dirección, qué compró— se
habrían anunciado en la bandeja del piloto.

Ahora el destinatario sale de `CompanySettings.order_notification_email` de la
empresa del pedido, y **no hay fallback de plataforma**. Una empresa sin
dirección configurada no recibe aviso, y ése es el fallo correcto: el silencio se
nota y se corrige; un aviso ya entregado a la empresa equivocada no se recupera.

### Plataforma vs tenant

```
PLATAFORMA   verificación de email · reseteo de contraseña
             Un User es GLOBAL — una identidad en todas las tiendas donde compra
             o trabaja — así que un correo sobre esa cuenta es de la plataforma.
             settings.PLATFORM_NAME, vacío por defecto.

TENANT       storefront · emails de pedido · PDFs · notas de venta ·
             control interno de esa empresa
```

Mezclarlas significaría que un cliente de tres tiendas recibe el reseteo de su
contraseña de un negocio por el que no preguntó.

### Colores — por qué sólo `#RRGGBB`

No es una restricción estética. Estos valores acaban dentro de una custom
property de CSS y de un atributo `style`. Cualquier cosa más rica que seis
dígitos hexadecimales —`url(...)`, `var(...)`, un esquema `javascript:`, una
llave que cierre la regla— es una inyección CSS con un selector de color delante.
Seis dígitos hex no pueden expresar ninguna de esas.

Validado tres veces: en el modelo (`clean()`, así que también cubre migraciones y
comandos), en el serializer, y otra vez en `brandingStyle()` antes de tocar el
atributo. El fallback es **por campo**: fijar sólo el fondo no pierde el resto de
la paleta.

### WhatsApp

Se guarda como **dígitos**, y el enlace se construye. Un `URLField` es un sitio
donde cabe cualquier URL, y ésta se renderiza como `<a href>` en el correo de un
cliente. Dígitos entran, un esquema conocido sale.

### El storefront dejó de ser estático

`generateMetadata` y el layout raíz consultan `/api/storefront/config/`, así que
todas las rutas pasaron de `○ (Static)` a `ƒ (Dynamic)` en el build.

**No es una regresión.** Una página cuyo contenido depende del host de la
petición no se puede prerenderizar una sola vez; el prerender era el bug — el
título de una empresa servido en todos los dominios.

### Lo que la API pública NO devuelve

`order_notification_email`. Es a dónde van los avisos de venta de un tenant, y
publicarlo entregaría a cualquier visitante una bandeja de operaciones a la que
apuntar. El serializer público está escrito a mano por eso: un `ModelSerializer`
con `exclude` filtraría cada campo añadido después de que alguien olvidara
actualizarlo.

### Currency — almacenado, no editable

El checkout cobra por Stripe en una moneda configurada a nivel de plataforma. El
campo existe para que el modelo esté listo, pero es de solo lectura en la UI: un
desplegable que dejara elegir USD mientras Stripe cobra PEN sería una mentira con
interfaz. Clasificación honesta: **PARCIAL**.

---

## 8-quaterdecies. Series y correlativos internos (Fase 2E)

Migraciones **0029** (esquema) y **0030** (backfill). Cierra la última pieza
estructural de aislamiento entre tenants.

### Qué había, exactamente

```python
# sales_note_services.py, antes
def _next_note_number():
    last = SalesNote.objects.aggregate(Max('number'))['number__max']
    ...
```

`MAX(number) + 1` sobre **toda la tabla**, y `SalesNote.number` con `unique=True`
**global**. Tres defectos distintos, no uno:

1. **Fuga entre tenants.** La empresa B emitía `NV-000002` porque la empresa A
   había emitido `NV-000001`. Su primer documento revelaba actividad ajena.
2. **Imposibilidad legítima.** Dos empresas no podían tener cada una su
   `NV-000001`, que es lo mínimo que un negocio espera de su numeración.
3. **Carrera.** Dos ventas simultáneas leen el mismo máximo y calculan el mismo
   número. La ventana es pequeña; el resultado es un duplicado.

### El contador como fila, no como cálculo

`InternalSequence(company, branch?, document_type, prefix, padding, next_value,
is_active)`. El número deja de deducirse de los documentos existentes y pasa a
ser estado propio, que es lo que permite bloquearlo.

`document_type` existe desde el día uno con un solo valor (`sales_note`). No es
especulación: la alternativa era una tabla `SalesNoteSequence` y una migración
completa el día que aparezca el segundo documento interno.

### Unicidad — dónde vive ahora

| Constraint | Qué garantiza |
|---|---|
| `unique_company_sequence_per_document` (`WHERE branch IS NULL`) | Una serie de empresa por tipo de documento |
| `unique_branch_sequence_per_document` (`WHERE branch IS NOT NULL`) | Una serie por sucursal y tipo |
| `unique_value_per_sequence` (`WHERE ambos NOT NULL`) | **Un ordinal por serie** |

Los tres son **condicionales**, y no por elegancia: en SQL `NULL != NULL`, así
que un unique plano sobre `(company, branch, document_type)` dejaría acumular
infinitas filas de empresa — exactamente la que debe ser única.

`unique_value_per_sequence` es el constraint que se quería desde el principio.
La unicidad no era «este string no se repite en la instalación» sino «este
ordinal no se repite en esta serie».

### `allocate()` — el único lugar que reparte números

```python
locked = InternalSequence.objects.select_for_update().get(pk=sequence.pk)
value = locked.next_value
formatted = locked.format(value)
locked.next_value = value + 1
locked.save(update_fields=['next_value', 'updated_at'])
```

Bloquea **una fila**: la serie. No `CompanySettings`, que es el atajo tentador
porque el alcance vive ahí — bloquearlo serializaría todas las sucursales de una
empresa entre sí y dejaría su configuración entera retenida durante el tiempo de
un número.

`allocate()` **exige estar dentro de una transacción** y lo comprueba. Fuera de
ella, un `select_for_update()` no bloquea nada y un número podría escapar de una
escritura que después falla: el documento se pierde, el ordinal no.

### Orden de bloqueo: pedido primero, serie después

Fijo en todo el código. Dos rutas con el orden invertido son un deadlock, y el
orden no es arbitrario: bloquear el pedido primero hace que un segundo intento
sobre el mismo pedido encuentre la nota ya escrita y **devuelva sin gastar un
número**. Asignar antes de comprobar quemaría un ordinal en una nota que nunca
se escribe — un hueco sin documento que lo explique.

### El número se guarda, no se deriva

`SalesNote.number` es un `CharField` almacenado. La tentación es hacerlo una
propiedad calculada desde `sequence` + `sequence_value`, y sería un error: un
PDF se regenera meses después, cuando el cliente pide su copia. Si el prefijo
cambió en el intermedio, el papel que tiene en la mano y el sistema dejarían de
coincidir.

`sequence_value` guarda el ordinal para el constraint y para ordenar; `number`
guarda lo que el documento **dice**.

### Alcance: empresa o sucursal

`CompanySettings.sales_note_sequence_scope`. La sucursal se deriva de
`Order.fulfillment_branch` — el mismo campo que la Fase 2D estampa una sola vez
al vender —, nunca de un parámetro del cliente.

**Se congela tras el primer documento.** No es una limitación técnica: cada
ordinal seguiría siendo único dentro de su serie. Es de legibilidad. Una empresa
que emitió `NV-000001..000050` por empresa y cambiara a por sucursal vería su
siguiente nota numerada `NV-000001` otra vez, y un mismo negocio mostrando el
mismo identificador en dos documentos es justo lo que un correlativo impide.
Hacerlo bien exige decidir qué pasa con los números ya emitidos, que es una
respuesta de negocio que esta fase no tiene. Queda como PENDIENTE explícito.

### El contador se congela tras el primer documento

Antes del primer documento es genuinamente útil: un negocio que migra desde otro
sistema empieza en 5001 en lugar de en 1. Después, bajarlo reemite identificadores
que ya están en papel y subirlo abre un hueco que alguien tendrá que explicar.

Se rechaza con un 400 y un mensaje, no ignorando el campo: un formulario que
parece guardar y no guarda es peor que uno que dice que no.

### Los huecos son historia, no un error

Una nota anulada deja su ordinal consumido. Reescribir `NV-000004` como
`NV-000003` para cerrar el hueco reasignaría un identificador ya emitido. El
hueco es aceptable; la reasignación no.

### Prefijo — por qué `/` está prohibido

El validador acepta `[A-Za-z0-9_-]{1,12}`. `NV/2026/` es una convención
plausible y aun así se rechaza: es un separador de rutas, el prefijo llega al
constructor del nombre de archivo del PDF, y permitirlo deja `../` a un error de
tecleo de distancia. El constructor de nombres además vuelve a filtrar; ninguna
de las dos defensas es el único punto de fallo.

### Migración 0029 — irreversible, y lo dice

Revertirla restauraría el unique global de `number`. En cuanto dos empresas
tengan cada una su `NV-000001` —el propósito entero de la fase— ese constraint
no puede satisfacerse sin renumerar documentos ya emitidos de alguien.

Renumerar historia para satisfacer un esquema no es un rollback, es pérdida de
datos con una migración delante. El guard es la **última** operación de la lista
para ser la **primera** en ejecutarse al revertir, y lanza un `RuntimeError` que
explica y remite a restaurar una copia de seguridad.

### Migración 0030 — infiere del historial de cada empresa, y nunca escribe `number`

Crea una serie de empresa por empresa, deduce prefijo y padding del historial
**de esa empresa** —no de una constante global— y coloca `next_value` por encima
del ordinal más alto encontrado.

Una nota con un número no interpretable (`MANUAL-ABC`, tecleado a mano) conserva
su string con `sequence_value` NULL. Inventarle un ordinal la metería en la serie
y arriesgaría chocar con un número real; el constraint es condicional
precisamente para que estas convivan en lugar de bloquear la migración.

La columna `number` no se escribe nunca.

### Verificación de la actualización

Contra una base poblada en 0028 y migrada hacia adelante: los números salieron
**byte a byte idénticos** (`NV-000001, NV-000002, NV-000003, NV-000004,
NV-000015, MANUAL-ABC`), los huecos se conservaron, `MANUAL-ABC` quedó con
ordinal NULL, y los contadores quedaron en 16 y en 5 respectivamente. Cero
pérdida de datos.

### Lo que esto NO es

Numeración **interna**. No es una serie fiscal, ni un CPE, ni una boleta o
factura electrónica. No hay XML, ni UBL, ni firma digital, ni OSE, ni llamadas a
SUNAT. Cada respuesta de la API y cada PDF lo repiten, porque `NV-000001` junto a
un logo, un RUC y un total se parece mucho a un comprobante.

---

## 8-quindecies. Clientes tenant-aware / CRM interno (Fase 4)

Migraciones **0031** (esquema), **0032** (backfill) y **0033** (capacidades).
Primer dominio del núcleo de Servicio Técnico.

### Cuatro conceptos que no deben mezclarse

| Concepto | Alcance | Qué significa |
|---|---|---|
| `User` | Plataforma | Un login. Global, único, transversal a empresas. |
| `Membership` | Empresa | Esta persona es **personal interno** de esta empresa. |
| `Customer` | Empresa | Esta persona o negocio **compra** a esta empresa. |
| `Order.customer_*` | Un pedido | Quién compró **ese día**, congelado. |

Ninguno implica otro. El mismo humano puede tener cuenta, trabajar en la empresa
A y ser cliente de la B, y las tres cosas son independientes.

### Customer existe sin User, y ese es el caso normal

La mayoría de los clientes de un servicio técnico llegan al mostrador, llaman o
escriben por WhatsApp. Nunca van a tener login. `Customer.user` es nullable y un
nulo ahí **no es un dato que falta**: es un cliente que no tiene cuenta y no la
necesita.

Al revés tampoco: registrarse en la plataforma no convierte a nadie en cliente de
ninguna empresa.

### Un login, varias empresas, varios registros

```
User X
 ├── Customer(company=A, user=X)   notas de A, dirección de A, historial de A
 └── Customer(company=B, user=X)   notas de B, dirección de B, historial de B
```

Dos negocios que atienden a la misma persona **no pueden leerse la ficha** el uno
al otro. `UNIQUE(company, user) WHERE user IS NOT NULL` — condicional, porque en
SQL los NULL son distintos entre sí, que es justo lo que se quiere: cualquier
número de clientes puede no tener cuenta.

### Unicidad del documento — por empresa, nunca global

`UNIQUE(company, document_type, document_number) WHERE document_number != ''`

La empresa A y la empresa B pueden tener cada una una ficha del DNI 12345678. Son
dos fichas de la misma persona, y ninguna ve la otra. Dentro de **una** empresa,
en cambio, dos fichas con el mismo documento no son dos clientes: son uno
introducido dos veces, con la mitad del historial en cada una.

### La regla que gobierna todo el módulo: nadie se fusiona por parecido

Decidir que «Juan Pérez, juan@gmail.com» y «Juan Perez, juanp@gmail.com» son la
misma persona es tirar una moneda. Perderla significa que un cliente lee la
dirección, el historial y las **notas internas** de otro desde dentro de su
propia ficha. Es una fuga de privacidad producida por una función de comodidad.

Por eso:

**EMPAREJAMIENTO — determinista, exacto, dos claves y ninguna más**

1. `(company, user)` — la cuenta con la que la persona se autenticó.
2. `(company, document_type, document_number)` — el documento que el cliente
   tiene en la mano.

**DETECCIÓN DE DUPLICADOS — orientativa, nunca automática**

Email y teléfono compartidos devuelven `possible_duplicates` para que un humano
mire. No fusionan nada y no bloquean el alta: las familias comparten bandeja, las
oficinas comparten central, y el móvil de una recepcionista acaba en veinte
fichas. Eso no son clientes repetidos.

**DOCUMENTO REPETIDO EN LA MISMA EMPRESA — 409, con la ficha existente adjunta**

Este sí bloquea, y responde con el registro que ya existe para que la interfaz
ofrezca abrirlo en lugar de dejar al usuario en un callejón sin salida.

### `Order.customer` y el snapshot conviven, y no son redundantes

`Order.customer` es **quién es hoy**. `Order.customer_name`, `customer_phone`,
`document_number`, `address_line`… son **quién era entonces**.

Un cliente cambia de teléfono. El pedido del año pasado tiene que seguir diciendo
lo que decía: el paquete fue a esa dirección y lo recibió esa persona. Un
historial que se reescribiera solo sería inútil para la única pregunta que se le
hace a un historial — qué pasó realmente.

`PROTECT`, además: un cliente con compras no se borra. Se archiva.

### Vinculación en checkout — política explícita

| Comprador | Qué se hace |
|---|---|
| Autenticado | Resolver o crear `Customer(company, user)`. La cuenta es prueba. |
| Anónimo | Emparejar por documento —que el checkout **siempre** valida— y crear si no hay. |
| Falla algo | `Order.customer = NULL`, la venta continúa. |

La tercera fila es la importante. `link_order_to_customer()` se traga sus propios
errores y devuelve `None`; el pedido conserva todos los campos necesarios para
vincularlo a mano después. **Un problema de CRM no cuesta una venta.**

Se crea ficha también para un pedido no pagado, y es deliberado: quien entregó un
documento validado, un teléfono y una dirección es un cliente de ese negocio. Lo
que el producto no hace es llamar «venta» a eso — el resumen separa pagado de no
pagado en todas partes.

### Migración 0032 — conservadora por diseño

Dos claves concluyentes (cuenta, documento) y una tercera opción honesta: dejar
`Order.customer` en NULL y contarlo. Un pedido sin vincular es visiblemente
incompleto y se arregla a mano; uno mal vinculado parece correcto para siempre.

**Regla del primero, no del más reciente.** La versión inicial refrescaba la
ficha con el pedido más nuevo, que suena obviamente correcto y no lo es: un
comprador que pagó una vez con su DNI y otra con el RUC de su empresa salía
convertido en empresa con su propio nombre, y soltaba su DNI — que el siguiente
pedido anónimo con ese mismo DNI usaba para crear una segunda ficha de la misma
persona. La regla ordenada fabricaba el duplicado que la migración existe para
evitar. Ahora la primera compra establece la ficha, las siguientes sólo rellenan
huecos, y **nunca** cruzan la frontera persona/empresa.

Verificado contra una base poblada en 0030 con doce pedidos que cubren: cuenta en
dos empresas, mismo DNI anónimo y autenticado, documento en conflicto, email
compartido, teléfono compartido, mismo DNI en dos empresas y un pedido sin
identidad. Resultado: ocho fichas, once pedidos vinculados, uno sin vincular,
cero fusiones incorrectas, snapshots intactos.

### Migración 0033 — capacidades sin ampliar autoridad

`service.customers.view/manage` pasan a ACTIVE, así que el preset `Administrador`
de toda empresa **nueva** las incluye automáticamente: ese preset se define como
«todas las capacidades asignables» y se evalúa al cargar el código.

Las empresas **existentes** tienen sus roles congelados en la base de datos. La
migración las concede **sólo** a un rol cuyo conjunto de capacidades sea
EXACTAMENTE el que el preset `Administrador` otorgaba antes de esta fase —
igualdad en ambos sentidos. Una capacidad añadida o quitada y el rol es del
inquilino, no de la plataforma, y se deja intacto.

Ese discriminador es lo que lo hace seguro. `Administrador` es un caso especial
precisamente porque su definición es «todo»: ampliarlo no cambia lo que el rol
significa. Ampliar un rol cuya definición es una lista concreta sí. Por eso el
preset `Servicio Técnico` **no** se toca en empresas existentes.

`service.manage` **no** es un paraguas sobre estas capacidades. Dejar que
absorbiera todo lo que el módulo de servicio añada en el futuro lo convertiría en
el súper-permiso implícito que el catálogo de capacidades existe para eliminar.

### Customer es Company-level, no Branch-level

Un cliente compra en una tienda, deja un equipo en otra y lo recoge en una
tercera. Restringir el maestro de clientes por sucursal partiría a una persona en
tres fichas y rompería el historial que este módulo existe para conservar.

El segundo eje de autoridad de la Fase 2D sigue gobernando stock, y gobernará las
órdenes de servicio — que son **operaciones sobre** un cliente, no el cliente.

### Privacidad

- **No hay endpoint público de clientes**, y esa ausencia es la garantía real.
- El listado **no** devuelve `notes`: se lee de un vistazo en un mostrador, a
  veces con el cliente delante.
- La auditoría guarda **nombres de campo**, nunca valores. Una tabla de auditoría
  la leen más personas y no la purga nadie.
- Los errores identifican `customer_id` y `company_id`, sin volcar PII.

---

## 8-sexdecies. Incidente P0 — deriva de esquema (estabilización)

No es una fase de producto. Es el registro de una caída de runtime y de lo que se
aprendió, porque el modo de fallo va a repetirse en cada despliegue de este SaaS.

### Lo que se veía

`/admin`, `/cart` y el catálogo respondían 500 en localhost, con las Fases
2D + 3 + 2E + 4 en el árbol de trabajo.

### Lo que era

```
OperationalError: no such column: store_company.default_inventory_branch_id
    store/views.py → store/tenancy.py
```

La base de datos de desarrollo estaba en la **0023**. El código esperaba la
**0033**. Diez migraciones de diferencia, y la primera de ellas —la 0024— añade
precisamente la columna que `tenancy.py` consulta en cada resolución de tenant.

Como la resolución de tenant es lo PRIMERO que hace cualquier ruta del
storefront, las tres pantallas caían en el mismo punto.

### La lección, que es sobre los tests

Los 1429 tests pasaban, y habrían seguido pasando durante todo el incidente.

Django crea una base **nueva** para cada ejecución y le aplica todas las
migraciones. Una suite verde afirma *«este código es coherente con estas
migraciones»*. No afirma nada sobre la base a la que está conectado el servidor.

Son dos propiedades distintas, y sólo una de ellas estaba siendo verificada:

| Propiedad | Quién la verifica |
|---|---|
| El código concuerda con sus migraciones | La suite de tests |
| La BASE DE DATOS concuerda con el código | **Nadie, hasta ahora** |

Por eso la corrección no fue añadir tests. Fue añadir `store/checks.py`: un check
de despliegue que, al arrancar, dice cuántas migraciones faltan y cómo se llaman.
El fallo pasa de «tres pantallas dan 500 y hay que leer un traceback» a «el
servidor lo dice antes de servir la primera petición».

### Lo que el check deliberadamente NO hace

No migra. Un check que aplicara migraciones por su cuenta sería peor que el fallo
que sustituye: aplicar migraciones es una decisión de despliegue, algunas llevan
cambios de datos, y la 0025 de este proyecto está diseñada para **detenerse y
preguntar** en lugar de adivinar dónde estaba el stock histórico.

Tampoco silencia nada. Si una consulta falla más adelante por una columna
ausente, sigue lanzando. El check sólo hace que la causa se vea primero.

### Lo que tampoco se hizo

No se reseteó la base. Resetear habría hecho que la interfaz cargara y habría
dejado sin probar la actualización real —que es lo único que importa cuando haya
datos de un cliente al otro lado.

No se añadió ningún `except OperationalError: return []`. Eso convierte un
despliegue roto en una pantalla que carga con datos que faltan, que es un fallo
peor porque nadie lo denuncia.

### Hallazgo colateral: `POST /api/cart/`

Buscando la causa apareció otra ruta que respondía 500 con cualquier entrada:
`CreateModelMixin` exponía el `create` genérico del carrito, y
`CartItemSerializer.product` es de solo lectura, así que el INSERT llegaba a la
base con producto nulo.

Se cerró con 405 en vez de repararse. Repararlo habría significado una segunda
forma de escribir un `CartItem` sin acotar el producto a este storefront, sin
exigir `session_key` y sin validar stock — exactamente el vector cross-tenant que
el comentario de `add` describe cerrando. Una ruta que sólo funcionaba a medias
era, en realidad, un agujero que no llegó a abrirse porque fallaba antes.

---

## 8-septendecies. API pública versionada para clientes nativos (`/api/v1/`)

**Estado: IMPLEMENTADO (solo catálogo público).** Sin migraciones. Aditiva.

### El problema que resuelve

El storefront web resuelve su empresa por **Host**: lo fijan el DNS y el proxy
inverso, y el JavaScript de la página no puede tocarlo. Para la web eso es
correcto y sigue intacto.

Una app móvil no tiene ese Host. Llega a **un único host de API compartido**, así
que sin un selector explícito solo caben dos finales: catálogo vacío, o —mucho
peor— el catálogo de la empresa que el fallback eligiera.

Por eso `/api/v1/` nombra el tenant **en la ruta**. No en una cabecera, no en un
query param, no en el body: en la ruta, donde es imposible añadirlo por
accidente e imposible no verlo al leer un log.

### Endpoints

| Método | Ruta | Quién |
|---|---|---|
| `GET` | `/api/v1/storefront/<company_slug>/products/` | Público, anónimo |
| `GET` | `/api/v1/storefront/<company_slug>/products/<product_slug>/` | Público, anónimo |
| `GET` | `/api/v1/storefront/<company_slug>/categories/` | Público, anónimo |
| `GET` | `/api/v1/storefront/<company_slug>/categories/<slug>/` | Público, anónimo |

Filtros de productos: `?category=`, `?search=`, `?in_stock=true`,
`?ordering=` (allowlist: `price`, `-price`, `name`, `-name`, `newest`).

Respuesta: **array crudo**, igual que la superficie legacy.

### Qué es el slug — y qué no

**Es** un selector de escaparate público.
**No es** autorización, ni identidad, ni una concesión de ningún tipo.

Nombrar un tenant elige qué estantería pública leer. No puede alcanzar datos
privados, porque este resolutor solo lo usan las vistas públicas de catálogo:
toda superficie privada sigue derivando su empresa de la membresía del usuario
autenticado, jamás de un segmento de ruta. Cualquiera puede escribir cualquier
slug; eso es inofensivo cuando la respuesta es un escaparate y fatal cuando es
un historial de pedidos, y por eso **los dos caminos nunca convergen**.

### Fail-safe

Desconocida, inactiva, malformada y vacía producen **el mismo 404**, con el mismo
cuerpo. Un 403 para "inactiva" y un 404 para "desconocida" responderían, a quien
esté dispuesto a iterar, la pregunta "¿qué empresas existen en esta plataforma?".

No hay fallback a "la primera empresa": el queryset nace scopeado desde la
`Company` resuelta, o no hay queryset.

### Reutilización, no duplicación

`storefront_products()` y `storefront_categories()` se refactorizaron para
delegar en `company_storefront_products(company)` y
`company_storefront_categories(company)`. La lógica de scoping y de stock por
sucursal de despacho existe **una sola vez**. Una segunda copia derivaría, y la
deriva sería una fuga cross-tenant que ningún diff enseña.

`resolve_public_storefront_company(slug)` es el único punto nuevo de decisión.

### Serializers propios

`v1_serializers.py` declara sus **propias** listas de campos. Los serializers
legacy pertenecen al frontend web y pueden cambiar cuando el equipo web lo
necesite; una app móvil pasa por colas de revisión y vive meses en dispositivos,
así que no puede compartir una forma libre de moverse bajo sus pies.

Un campo añadido a `ProductSerializer` no aparece en el contrato móvil, y uno
eliminado falla ruidosamente en los tests de v1 en vez de vaciar en silencio una
pantalla ya publicada.

Campos expuestos: `id`, `name`, `slug`, `description`, `price`, `inventory`,
`category`, `image_url`, `average_rating`, `review_count`. Nada interno: ni
costos, ni márgenes, ni proveedor, ni reparto por sucursal, ni identidad fiscal,
ni identificadores de Stripe.

### Autenticación: deliberadamente apagada

`authentication_classes = []`. `CookieJWTAuthentication` se ejecutaría si no, y
una superficie que lee una cookie de sesión es una superficie cuyo
comportamiento depende de quién esté logueado — exactamente lo que un escaparate
anónimo y cacheable no debe ser. Un navegador con sesión y una app anónima
reciben catálogos byte a byte idénticos.

**BR-001 sigue `API_PENDING`.** Esta fase no toca `CookieJWTAuthentication`,
CSRF, login, refresh, logout ni el admin. No hay Bearer global. No existen
`/api/v1/auth/*` ni ninguna superficie privada v1.

### Aditiva por construcción

Nada aquí importa ni modifica las vistas legacy, y `store/urls.py` no cambió.
`/api/` se comporta hoy exactamente igual que antes de que este módulo
existiera, y hay tests de regresión que lo demuestran.

### Archivos

| Archivo | Qué |
|---|---|
| `store/v1_serializers.py` | Contrato de campos, propio de v1 |
| `store/v1_views.py` | Vistas públicas y resolución de tenant por ruta |
| `store/v1_urls.py` | Rutas v1, montadas en `backend/urls.py` |
| `store/tenancy.py` | `resolve_public_storefront_company` + helpers company-first |
| `backend/urls.py` | `path('api/v1/', include('store.v1_urls'))` |

### Estado de los requerimientos de Mobile

| ID | Qué pide Mobile | Estado |
|---|---|---|
| BR-002 | Selección de tenant validada server-side | **Catálogo público: IMPLEMENTADO.** Autorización de tenant en datos privados: PENDIENTE (depende de BR-001) |
| BR-007 | Superficie versionada `/api/v1/` | **PARCIAL** — existe el slice de catálogo; auth y superficie privada, PENDIENTE |
| BR-001 | Contrato de autenticación nativo | `API_PENDING` |
| BR-003 | `fulfillment_status` en pedidos | PENDIENTE |
| BR-005 | Dominio de reparaciones | PENDIENTE — no existe `RepairOrder` |
| BR-006 | Endpoint público de marca | PENDIENTE |
| BR-008 | Seguimiento seguro por enlace | `API_PENDING` — depende de BR-005 |

---

## 8-duodevicies. Autenticación nativa para clientes móviles (`/api/v1/auth/`)

**Estado: IMPLEMENTADO (núcleo de sesión, BR-001A).** Sin migraciones. Aditiva.

### El problema

La autenticación web lee un JWT desde una cookie HttpOnly y aplica CSRF. Ambas
cosas existen porque **el navegador adjunta cookies a peticiones que el usuario
no inició**. Una app nativa no tiene ese comportamiento: guarda un token y lo
envía deliberadamente, así que CSRF no protege de nada y la cookie sobra.

Fusionar los dos mecanismos habría significado que la superficie web empieza a
aceptar `Authorization: Bearer`. Ese es exactamente el cambio que nadie quiere
hacer por accidente, así que van separados.

### Endpoints

| Método | Ruta | Auth |
|---|---|---|
| `POST` | `/api/v1/auth/login/` | Ninguna. Throttle `login` (5/min) |
| `POST` | `/api/v1/auth/refresh/` | Ninguna. El refresh token es la credencial |
| `POST` | `/api/v1/auth/logout/` | Ninguna. Best-effort |
| `GET` | `/api/v1/auth/me/` | `V1BearerAuthentication` + `IsAuthenticated` |

Tokens en el **cuerpo**, nunca en cookies. Hay test de que ninguna respuesta
emite `Set-Cookie`.

### `V1BearerAuthentication` — nunca global

**No está en `DEFAULT_AUTHENTICATION_CLASSES` y no debe añadirse ahí.** Añadirla
abriría en silencio `/api/admin/`, `/api/auth/me/` y todas las vistas privadas
web a un token emitido para el contrato móvil.

Cada vista privada v1 la declara explícitamente, así que habilitar Bearer en un
endpoint es una línea visible en un diff. Hay tests de que un Bearer válido —
incluso de un superadmin — devuelve 401/403 en la superficie legacy.

### Login por email, y por qué es delicado

La UI móvil pide correo. El login web pide usuario, porque `USERNAME_FIELD` es
`username`. Se creó un contrato nativo propio en vez de forzar al usuario a
recordar un username que le generó un formulario.

**`email` NO es unique en esta base de datos.** `AUTH_USER_MODEL` es el `User`
estándar de Django, cuya columna `email` no tiene constraint; el registro valida
duplicados en el serializer, pero esa comprobación tiene una race y no dice nada
de filas creadas antes, por `createsuperuser` o desde el admin.

**No se añadió una migración unique.** Fallaría en cualquier instalación que ya
tenga un duplicado, y se descubriría en producción durante el deploy. La
ambigüedad se resuelve en la vista:

| Coincidencias por email | Resultado |
|---|---|
| 0 | 401 genérico |
| 1 | se comprueba la contraseña |
| >1 | **401 genérico** + warning en logs, sin adivinar |

Elegir "la primera" dejaría entrar como otra persona a quien registrara un
correo duplicado.

### Sin enumeración de cuentas

Email desconocido, contraseña incorrecta, cuenta inactiva y email duplicado
devuelven **el mismo 401 con el mismo cuerpo**. Cuando no hay usuario se
verifica igualmente contra un hash dummy, para que el tiempo de respuesta no
conteste la pregunta que el mensaje se niega a contestar.

### Refresh con rotación

`ROTATE_REFRESH_TOKENS` y `BLACKLIST_AFTER_ROTATION` ya estaban activos en el
proyecto. El token enviado queda muerto al responder; el cliente debe **persistir
el nuevo refresh ANTES** de usar el nuevo access, o un crash entre medias lo deja
con una credencial en blacklist.

Un usuario desactivado no puede extender su sesión aunque tuviera un refresh
válido: la desactivación surte efecto en el siguiente refresh, no cuando el token
caduque.

### Logout best-effort

Siempre 200. Sin refresh, con uno caducado, malformado o ya en blacklist: 200.
No exige un access token vivo — justo cuando la sesión expira es cuando el
usuario pulsa "cerrar sesión", y exigirlo lo haría imposible.

La respuesta uniforme también evita que esto sea un oráculo sobre qué refresh
tokens siguen vivos.

### `available_companies` — relaciones verificadas, no reclamos del cliente

Esta fue la decisión menos obvia de la fase.

La migración 0015 **deliberadamente no creó Membership para clientes**: un
comprador no es staff del tenant, y convertirlo en miembro habría sido una
escalada de privilegios silenciosa. Esa decisión es correcta y no se revisa.

Pero significa que las memberships por sí solas responden la pregunta equivocada
para una app cuyo público **entero** son compradores: un cliente recibiría una
lista vacía y la app concluiría que no tiene empresa.

Así que se reportan las dos relaciones, **etiquetadas**, porque no son lo mismo:

| `relation` | Origen | Significa |
|---|---|---|
| `member` | `Membership` activa, empresa activa | Es staff |
| `customer` | `Customer` activo, empresa activa | Compra aquí |

Si alguien es ambas cosas en la misma empresa, gana `member`.

**No es autorización.** Es una constatación de hechos ya presentes en la base de
datos, calculada desde las filas del propio usuario autenticado y **nunca** desde
nada que el cliente enviara. Toda API privada futura debe revalidar membership
por su cuenta: esta lista es lo que la app puede **mostrar y seleccionar**, jamás
una concesión que pueda presentar como prueba.

`is_superuser` se ignora a propósito. Un administrador de plataforma no recibe
todos los tenants en un teléfono; si algún día hace falta será una función
explícita y auditada, no el efecto colateral de un booleano.

### `is_email_verified`

Esta instalación **no tiene columna de verificación**. El registro crea la cuenta
con `is_active=False` y `VerifyEmailView` la pone en `True`: verificar y activar
son el mismo hecho. Por eso el campo siempre vale `true` para quien logra
autenticarse — un usuario inactivo no obtiene token.

Se reporta igualmente porque el modelo de sesión móvil tiene el campo y porque
BR-001B podría separar los dos conceptos.

### Fuera de scope — BR-001B

**No** hay registro, verificación de correo, reenvío, reset ni cambio de
contraseña nativos. Siguen siendo solo web. Hay tests que fijan que
`/api/v1/auth/register/`, `/api/v1/auth/password-reset/` y compañía devuelven 404,
para que un cliente móvil no presente un formulario que este contrato no puede
atender.

### Sin cambios en la web

`CookieJWTAuthentication`, CSRF, `/api/auth/login|refresh|logout|me/`, el admin y
`DEFAULT_AUTHENTICATION_CLASSES` están intactos. Hay tests de regresión: el login
web sigue autenticando por username, sigue devolviendo sus JWT en cookies y no en
el cuerpo, y su cookie **no** abre la superficie nativa.

### Estado de los requerimientos de Mobile

| ID | Estado |
|---|---|
| BR-001A núcleo de sesión nativa | **IMPLEMENTADO** |
| BR-001 completo | **PARCIAL** — falta el ciclo de vida de cuenta (BR-001B) |
| BR-002 | **PARCIAL** — catálogo público sí; autorización de datos privados, pendiente |
| BR-007 | **PARCIAL ampliado** — catálogo + auth; superficie privada de negocio, pendiente |
| BR-003 · BR-005 · BR-006 · BR-008 | PENDIENTE |

---

## 8-undevicies. Superficie de cliente y contexto de acceso (`/api/v1/customer/`)

**Estado: IMPLEMENTADO (pedidos de cliente).** Sin migraciones. Aditiva.

### Tres audiencias, tres superficies — DEC-API-001

| Prefijo | Audiencia | Autenticación |
|---|---|---|
| `/api/v1/storefront/<slug>/` | **PÚBLICA** — cualquiera | ninguna |
| `/api/v1/customer/<slug>/` | **CLIENTE** — sus *propios* registros | Bearer v1 |
| `/api/v1/internal/<slug>/` | **INTERNA** — registros de la *empresa* bajo capability | **NO EXISTE TODAVÍA** |

Son espacios de URL **separados**, no un endpoint que ensancha su queryset
cuando quien pregunta es staff. Un endpoint cuyo resultado depende de quién
llama está a un refactor de devolver el conjunto equivocado, y el fallo es
silencioso: una pantalla de cliente listando las compras de todos.

### Endpoints

```
GET /api/v1/customer/<company_slug>/orders/
GET /api/v1/customer/<company_slug>/orders/<id>/
```

Solo lectura. Cancelar o reembolsar son decisiones del negocio; un cliente móvil
afirmándolas estaría afirmando un desenlace que el servidor debe poseer.

### La propiedad son DOS claves foráneas

```python
Order.objects.filter(
    Q(user=user) | Q(customer__user=user),
    company=verified_company,
)
```

| FK | Qué cubre |
|---|---|
| `Order.user` | Compra hecha con sesión iniciada. Inequívoca |
| `Order.customer.user` | Compra anónima que el negocio emparejó **por documento** con la ficha CRM del cliente, ficha que luego se enlazó a su cuenta |

La segunda no es un extra: es el caso real que `Order.user` sola se pierde. Ver
`customer_services.link_order_to_customer()`.

**El email NUNCA es propiedad.** `Order.customer_email` es una instantánea de lo
que se escribió al pagar, no tiene unicidad, y `find_possible_duplicates` existe
precisamente porque una familia o una oficina pequeña comparten una dirección.
Emparejar por ahí entregaría el historial de compras de una persona a otra.

El documento tampoco se consulta aquí: emparejar documentos es trabajo del CRM
en el momento del checkout, bajo la vista del negocio, no una regla de acceso.

### Ser empleado no es ser cliente

`has_customer_relation()` exige una de dos cosas, ambas hechos ya presentes en la
base de datos:

1. una ficha `Customer` **activa**, o
2. ser dueño de al menos un pedido en esa empresa.

La segunda existe para que **archivar una ficha no deje a nadie fuera de su
propio historial**. Cerrar el expediente de un cliente es una decisión comercial;
"ya no puedes ver lo que compraste" no es una que deba tomarse de rebote.

Una **membresía no cuenta**. Un vendedor, un almacenero, un técnico y un
administrador de empresa reciben **404** aquí, con test para cada uno. Y un
empleado que además compra en su empresa ve **solo sus propias compras**.

El acceso interno a los pedidos de toda la empresa será `sales.orders.view` en
la superficie interna, que es otra cosa.

### Fail-safe indistinguible

Empresa desconocida, empresa inactiva y "no eres cliente aquí" devuelven **el
mismo 404 con el mismo cuerpo**. Distinguirlos permitiría a cualquier cuenta
válida mapear qué empresas existen en la plataforma, un slug cada vez.

### BR-003 — cerrado para v1

`fulfillment_status` existe en el modelo desde la Fase 2C, pero `OrderSerializer`
nunca lo expuso: un cliente veía que su pago salió bien y **nada** sobre si la
mercancía se había movido. El serializer v1 lo incluye, con su etiqueta legible.

No se modificó el serializer legacy: pertenece al frontend web, y además lista
`stripe_session_id` entre sus campos.

### Qué NO viaja al cliente

Identificadores de Stripe · `payment_error` · `email_send_error` ·
`cart_session_key` · marcas de envío de correos internos · `company_snapshot` ·
`fulfillment_branch` · notas internas · costos · márgenes · capabilities · datos
de otros clientes.

La lista de campos es una **allowlist**: añadir una columna al modelo no la añade
a esta respuesta.

### `access_contexts` — aditivo, nunca sustituto

`/api/v1/auth/login/` y `/me/` incorporan dos campos **junto a**
`available_companies`, que sigue intacto: un cliente ya publicado lo lee y debe
seguir funcionando mientras esté instalado.

```json
{
  "access_contexts": [
    {
      "company": {"slug": "...", "name": "..."},
      "customer": true,
      "member": true,
      "capabilities": ["inventory.view", "sales.orders.view"]
    }
  ],
  "platform": {"is_master": false}
}
```

`customer` y `member` son booleanos **independientes**, no un rol. La misma
persona puede comprarle a una empresa y trabajar en ella; colapsarlo en un campo
obligaría al cliente a elegir cuál de dos verdades creer.

Las capabilities salen de `resolve_capabilities()`, el resolutor real — no de una
lista escrita a mano.

### Las capabilities son presentación, jamás autorización — DEC-MOBILE-008

Viajan para que la app decida **qué pestaña dibujar**, nunca si una operación
está permitida. Todo endpoint interno las vuelve a resolver en el servidor: un
cliente que mienta sobre tener `inventory.view` recibe un 403 del endpoint, no
inventario.

### Platform master, aparte

`platform.is_master` se reporta **separado** de cualquier empresa y no concede
nada: `access_contexts` se sigue construyendo desde filas reales. Ser
administrador de plataforma no es ser miembro de todos los tenants, y no
enumera los tenants dentro de un teléfono. Un platform master recibe **404** en
la superficie de cliente, con test.

### Sin cambios

`/api/` legacy, autenticación web por cookie + CSRF, admin, Stripe, checkout web,
`DEFAULT_AUTHENTICATION_CLASSES`, migraciones. Con tests de regresión.

### Estado de los requerimientos de Mobile

| ID | Estado |
|---|---|
| BR-003 `fulfillment_status` | **IMPLEMENTADO para v1** |
| BR-002 | **PARCIAL** — público + cliente resueltos; superficie interna pendiente |
| BR-007 | **PARCIAL** — catálogo + auth + pedidos de cliente |
| BR-001B ciclo de cuenta | PENDIENTE |
| BR-005 reparaciones · BR-006 marca · BR-008 tracking | PENDIENTE |

---

## 8-vicies. Checkout nativo autenticado y configuración pública por slug

**Estado: IMPLEMENTADO.** Migración **0034**. Aditiva.

### Dos transportes, un solo conjunto de reglas comerciales — DEC-API-002

| Superficie | Quién | Carrito | Tenant |
|---|---|---|---|
| `POST /api/payments/create-checkout-session/` | **anónimo** | de sesión, en servidor | Host |
| `POST /api/v1/customer/<slug>/checkout/` | **Bearer v1** | intenciones en el cuerpo | ruta |

Lo que difiere es cómo se identifica una petición y de dónde salen sus ítems. Lo
que **no** puede diferir es cuánto cuesta algo, si hay stock, qué sucursal
despacha, cuánto vale el cupón y qué aspecto tiene el `Order` después.

Por eso el razonamiento comercial se extrajo a **`checkout_services.py`** y
ambas superficies lo llaman. Dos copias derivarían, y la deriva sería un cliente
al que la web cobra un precio y la app otro.

`CreateCheckoutSessionView` **sigue siendo `AllowAny`**. Esta tienda acepta
pedidos de invitados desde antes de que existieran las cuentas; exigir login ahí
echaría a todo comprador que no quiera una. La regla del móvil es distinta
porque una app ya sabe quién la sostiene y el pedido necesita dueño para
aparecer en «mis pedidos».

### El cliente no es autoridad comercial

`V1CheckoutSerializer` **rechaza** —no ignora— `price`, `subtotal`, `total`,
`discount_amount`, `stock`, `company_id`, `branch_id`, `status`, `paid`,
`user_id`, `stripe_session_id` y `session_key`. Un cliente que manda un precio
cree que fija precios; el silencio le dejaría seguir creyéndolo hasta el día en
que los importes no cuadraran.

Cada línea se resuelve **por slug dentro de la empresa**. Un id es un entero
pequeño que existe en todos los tenants, así que uno filtrado es una conjetura
plausible en otro; un slug resuelto dentro de la empresa del llamante existe
ahí o no existe.

### Idempotencia durable — DEC-API-003

Un doble toque, un reintento o una respuesta que nunca llegó no pueden crear dos
pedidos ni dos sesiones de pago. Tres capas, y cada una cubre lo que las otras
no:

1. **Búsqueda previa** — barata, resuelve el reintento común.
2. **`UniqueConstraint(company, user, idempotency_key)`** con condición
   `idempotency_key__isnull=False` — lo único que aguanta una carrera, porque
   decide la base de datos. Parcial a propósito: cada pedido de navegador tiene
   la clave nula, y una constraint no parcial permitiría exactamente **un**
   pedido de invitado por empresa.
3. **`idempotency_key` de Stripe** — para cuando el pedido se creó, Stripe
   aceptó la llamada y la respuesta se perdió.

Acotada a **empresa + usuario**, no a la clave sola: dos clientes pueden generar
la misma clave, y una constraint global haría fallar el checkout de uno por
culpa del otro.

**Misma clave, distinto contenido → 409.** Devolver el primer pedido en silencio
le diría al cliente que su nueva cesta se aceptó cuando no fue así. Se compara
un **hash SHA-256** del payload canónico, no el payload: nada del comprador se
guarda dos veces.

### Nada se consume antes de pagar

Ni se vacía el carrito ni se descuenta stock al crear el checkout. Un pedido que
nunca se pague no debe haberle costado a nadie su cesta ni a la tienda su
inventario. Ambas cosas ocurren en el webhook, cuando Stripe confirma —
exactamente como ya hacía la web.

### Configuración pública por slug — BR-006

```
GET /api/v1/storefront/<company_slug>/config/
```

Anónimo. **El mismo constructor de payload** que `/api/storefront/config/`, ahora
extraído a `build_storefront_config_payload(company)`: dos constructores
derivarían, y la deriva sería una tienda cuya app enseña un teléfono y cuya web
enseña otro. Hay un test que compara ambas respuestas byte a byte.

Devuelve `company` (name, slug, legal_name, tax_id — los tres últimos aparecen en
cada boleta y factura), `branding`, `contact` (incluido `whatsapp_link`) y
`policies`. **Nada operativo**: ni `order_notification_email`, ni configuración
de sucursal, ni credenciales, ni capabilities.

Empresa desconocida e inactiva devuelven el mismo 404 que el catálogo.

### Estado de los requerimientos de Mobile

| ID | Estado |
|---|---|
| BR-003 | **IMPLEMENTADO para v1** |
| BR-006 marca pública | **IMPLEMENTADO** |
| BR-002 | **PARCIAL** — público, cliente y checkout resueltos; interno pendiente |
| BR-007 | **PARCIAL** — falta la superficie interna |
| BR-001B · BR-005 · BR-008 | PENDIENTE |

---

## 8-unvicies. Superficie interna y pedidos de venta (`/api/v1/internal/`)

**Estado: IMPLEMENTADO (contexto + pedidos de venta).** Sin migraciones. Aditiva.

### Cuatro audiencias, cuatro superficies

| Prefijo | Audiencia | Auth |
|---|---|---|
| `/api/v1/storefront/<slug>/` | **pública** | ninguna |
| `/api/v1/customer/<slug>/` | **cliente**, sus propios registros | Bearer v1 |
| `/api/v1/internal/<slug>/` | **staff**, registros de la empresa | Bearer v1 + capability |
| `/api/admin/` | panel web | cookie + CSRF, **sin tocar** |

No existe ningún endpoint que ensanche su resultado según quién llame. Un cliente
que pide pedidos recibe los suyos; un empleado que pide pedidos recibe los de la
empresa. Son URLs distintas, permisos distintos y serializers distintos.

### Dos puertas, en orden, y responden distinto

| Situación | Respuesta | Por qué |
|---|---|---|
| Empresa desconocida | **404** | — |
| Empresa inactiva | **404** | indistinguible |
| Sin membresía activa | **404** | otro código dejaría mapear los tenants de la plataforma, un slug cada vez |
| Con membresía, sin capability | **403** | el servidor ya admitió que la empresa existe y que trabajas ahí; ocultar el motivo ya no protege nada |

Una relación de **cliente no sirve**: comprarle a un negocio no es trabajar en él.

Un **platform master** pasa la primera puerta, pero solo para la empresa **nombrada
en la ruta**. Nunca recibe un tenant implícito, y eso no le concede capability
alguna — `resolve_capabilities` decide eso aparte.

### Endpoints

```
GET   /api/v1/internal/<slug>/context/
GET   /api/v1/internal/<slug>/orders/
GET   /api/v1/internal/<slug>/orders/<id>/
PATCH /api/v1/internal/<slug>/orders/<id>/fulfillment/
```

`context/` se llama **al entrar** al área interna, no se lee de la sesión. El
contexto emitido en el login es una instantánea: los roles cambian mientras una
sesión sigue viva, y a quien le revocaron un permiso hace una hora no debe
seguir viendo un módulo porque su token aún vale.

Devuelve **solo** empresa, membresía, capabilities y platform. Ni clientes, ni
pedidos, ni personal, ni configuración: responde «¿qué puedo ver?», y responder
más lo convertiría en una fuente de datos que nadie auditó.

### Una sola máquina de estados

`order_fulfillment_services.py` concentra quién puede mover qué estado y qué se
escribe en la auditoría. La vista del admin web y la de v1 la llaman.

Una regla impuesta en un sitio y olvidada en el otro es cómo una operación acaba
siendo posible desde un teléfono y rechazada en un escritorio.

Se preserva **exacta** la restricción del rol de inventario —mover mercancía sí,
cancelar ventas no— aunque esté clavada al `UserProfile.role` legacy y no a una
capability. Ensanchar en silencio lo que puede hacer un almacenero no es algo que
decida un refactor.

El detalle devuelve `available_fulfillment_transitions`, **desde el servidor**,
para que la app no cargue una segunda copia de la tabla. Es entrada de
presentación: el PATCH vuelve a comprobar.

### Nada de pagos

Cambiar el fulfillment no toca el estado de pago, no manda correo y no mueve
stock — igual que la vista legacy. Si el dinero llegó lo dice Stripe, por el
webhook, nunca un miembro del personal afirmándolo.

### Capabilities promovidas a ACTIVE

`sales.orders.view` y `sales.orders.manage` pasan de AVAILABLE a **ACTIVE**.

El catálogo define AVAILABLE como «el módulo existe pero sus endpoints los
autoriza aún el RBAC legacy». `/api/v1/internal/` comprueba `has_capability` y
**nada más**, sin ruta de rol legacy, así que conceder o retirar una de estas
decide de verdad lo que ocurre.

`sales.notes.manage` **sigue AVAILABLE** precisamente porque M6 no construyó ese
módulo. Las de servicio técnico **siguen RESERVED**: `RepairOrder` no existe, y
no hay permisos falsos para funciones ausentes.

### Estado de los requerimientos

| ID | Estado |
|---|---|
| BR-002 | **RESUELTO** para público, cliente e interno |
| BR-007 | **PARCIAL** — catálogo, auth, cliente, checkout e interno de ventas |
| Inventario interno v1 | PENDIENTE |
| BR-005 servicio técnico | PENDIENTE |
| BR-001B | PENDIENTE |

---

## 9. Deuda pendiente

1. ~~**Branding por empresa**~~ → **RESUELTO en la Fase 3**: `CompanySettings`
   + `store/company_settings.py`. No quedan constantes de identidad en los
   servicios comerciales, y un test estructural lo vigila.
2. **Correlativo de `SalesNote` global** — `NV-` se intercalaría entre empresas.
3. **`get_user_role()` sigue siendo global** — un `admin` lo es en todas partes.
   La membresía todavía no gobierna los permisos.
4. ~~**Catálogo público sin tenant**~~ → resuelto en 2B; el branding del
   storefront, en la Fase 3.
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
12. ~~**PENDIENTE — Branch access model**~~ → **RESUELTO en 2D**:
   `Membership.branch_access_mode` + `MembershipBranchAccess`. `Membership.branch`
   sobrevive como sucursal predeterminada, marcado LEGACY, sin autoridad.
13. **KPIs comerciales tenant-aware** — el dashboard tiene el marco visual
   (`MetricCard`) pero ninguna métrica comercial, porque sería global. Se llenan
   en 2B/2C.
14. **Pantallas de Áreas y Roles** — las APIs existen desde 2A.1, las pantallas
   no. Empresa (`/admin/settings`) y Sucursales (`/admin/branches`) se
   construyeron en la Fase 3.
15. **Dashboard interno avanzado** — pospuesto a propósito hasta que el dominio
   comercial esté tenantizado.
16. ~~**`Product.inventory` es un entero global por producto**~~ → **RESUELTO en
   2D**: `BranchStock` es la fuente de verdad y `Product.inventory` queda como
   agregado de compatibilidad mantenido transaccionalmente.
   `Inventory company isolation: IMPLEMENTADO` ·
   `Inventory branch isolation: IMPLEMENTADO` · `Product.inventory: OBSOLETO
   (compatibilidad)`.
17. ~~**`StockMovement` no está tenantizado**~~ → **RESUELTO en 2D**: lleva
   `company` y `branch` NOT NULL, más FKs a `StockTransfer` e `InventoryCount`.
   Las capabilities `inventory.*` pasaron a `ACTIVE` y gobiernan sus endpoints.
18. **Bridge legacy del catálogo** — desaparece cuando todo operador tenga
   Membership.
19. ~~**KPIs comerciales del dashboard**~~ → ventas en 2C, inventario en 2D.
   Queda **caja**, que no tiene modelo todavía.
20. **`SalesNote.number` sigue siendo un correlativo global** (`NV-000001`). Con
   dos empresas emitiendo notas, la numeración se intercala entre tenants. La
   nota en sí ya está scopeada por su pedido; lo que falta es la serie por
   empresa. Deuda conocida, no escondida.
21. ~~**`StockMovement` no tiene columna `company`**~~ → **RESUELTO en 2D**
   (duplicado del punto 17).
22. ~~**Emails sin branding por empresa**~~ → **RESUELTO en la Fase 3**.
23. **`frontend/db.sqlite3` está versionado** (0 bytes, de antes de que `.gitignore`
   cubriera `*.sqlite3`). Conviene sacarlo del índice en un commit aparte.

### Deuda que deja la Fase 2D

24. **PENDIENTE — recepción parcial de transferencias.** V1 recibe la
   transferencia completa. Hacerlo bien exige una cantidad recibida por línea, un
   flujo de discrepancias y una decisión sobre de quién son las unidades que
   faltan; una versión a medias perdería stock en silencio.
25. **PENDIENTE — anular una transferencia ya despachada.** Hoy se bloquea con un
   mensaje que explica por qué. Soportarlo exige movimientos compensatorios
   explícitos (en la práctica, una transferencia de vuelta), no un cambio de
   estado.
26. **PENDIENTE — transferencia con recepción separada.** Operar una transferencia
   exige acceso a AMBAS sucursales. El flujo real (el origen despacha, el destino
   confirma después) necesita notificaciones, una cola de «pendientes de recibir»
   y una regla sobre quién persigue una transferencia sin recibir. La restricción
   actual es la que no puede perder unidades mientras eso se diseña.
27. **PENDIENTE — asignación automática de un pedido entre varias sucursales.** Un
   pedido sale de UNA sucursal. Si esto cambia, la clave de idempotencia de
   `sale_exit` — hoy `(order, product)` — debe cambiar con ese diseño.
28. **PENDIENTE — reservas multi-almacén.** No hay reserva de stock entre el
   checkout y el pago; el comportamiento ante faltante tras el pago sigue siendo
   marcar el pedido, como antes de 2D.
29. **PENDIENTE — costos, utilidad y margen.** Sin precio de compra no hay
   valorización real. El único número monetario del inventario es stock × precio
   de venta, etiquetado como tal.
30. **PENDIENTE — serial / IMEI.** Trazabilidad por unidad, no por cantidad.
31. **Pantalla de Sucursales.** La API existe (incluida la sucursal de despacho,
   `PATCH /api/admin/companies/{pk}/fulfillment-branch/`); la pantalla no.
32. **Pantalla de Personal completa.** `/admin/users` ya expone el **acceso por
   sucursal** (modo, concesiones y sucursal predeterminada) sobre la API de
   membresías. Lo que sigue pendiente de esa pantalla es el alta de membresías y
   la edición de roles/áreas personalizados, que siguen siendo deuda de 2A.1.
33. **Bridge legacy del inventario.** Un operador pre-SaaS sin Membership sigue
   alcanzando el tenant piloto y **todas** sus sucursales, con su rol legacy como
   autoridad. Desaparece cuando todo operador tenga Membership.

### Deuda que deja la Fase 3

34. ~~**Series y correlativos por empresa**~~ → **RESUELTO en la Fase 2E**:
   `InternalSequence` + `store/sequences.py`. El unique global de
   `SalesNote.number` desapareció; la unicidad es por serie.
35. **PENDIENTE — favicon por empresa.** Requiere una ruta de icono dinámica o un
   pipeline de subida; ninguno existe. El favicon sigue siendo de plataforma.
36. **PENDIENTE — contenido de landing por empresa.** El copy de marketing de la
   home y de `/services` sigue siendo del piloto (reparación Apple, baterías,
   plazos de garantía). La IDENTIDAD sí es del tenant; el CONTENIDO necesitaría
   un sistema de contenidos, que es otra fase.
37. **PENDIENTE — subida de logos.** `logo_url` es una URL validada. No se
   introdujo S3 ni ningún proveedor sólo para esta fase, y no se guardan blobs ni
   base64 en la base de datos.
38. **PENDIENTE — SMTP por tenant.** El transporte sigue siendo de plataforma, y
   `CompanySettings` **no** guarda secretos. Lo que sí es por tenant es la
   identidad DENTRO del mensaje.
39. **PARCIAL — currency.** Almacenada, de solo lectura, hasta que el checkout
   soporte varias monedas de verdad.
40. **PARCIAL — timezone.** Almacenada y validada como zona IANA. Los reportes y
   el dashboard siguen usando `settings.TIME_ZONE`; migrarlos es un cambio
   transversal que no pertenece a esta fase.
41. **PENDIENTE — estados de reparación configurables.** El módulo de servicio
   técnico no existe; una abstracción de estados sin dominio sería huérfana.

### Deuda que deja la Fase 2E

42. **PENDIENTE — cambiar el alcance después de emitir.** Hoy se congela con el
   primer documento. Reabrirlo exige decidir qué pasa con los números ya
   emitidos: renumerar está prohibido, así que la respuesta pasa por una serie
   nueva o por un prefijo distinto por sucursal. Es una decisión de negocio.
43. **PENDIENTE — anulación de notas con motivo.** Hoy una nota anulada deja su
   ordinal consumido y no hay campo donde escribir por qué. El hueco es correcto;
   la explicación falta.
44. **PENDIENTE — series para otros documentos internos.** `document_type` está
   listo y tiene un solo valor. La segunda serie no necesitará migración de
   esquema, sólo su servicio.
45. **NO ES DEUDA — numeración fiscal SUNAT.** Fuera de alcance por decisión, no
   por olvido. Esto es numeración interna y el producto lo dice en cada
   superficie donde el número aparece.

### Deuda que deja la Fase 4

46. **PENDIENTE — merge de clientes.** Deliberado. Tiene que mover pedidos y, en
   fases siguientes, equipos, órdenes de servicio y garantías. Un merge que mueva
   unos y no otros es peor que ninguno.
47. **PENDIENTE — vincular a mano un pedido histórico ambiguo.** La migración
   deja en NULL lo que no puede atribuir con certeza; falta la herramienta para
   que un humano lo resuelva.
48. **PENDIENTE — múltiples direcciones por cliente.** Hoy hay una dirección de
   contacto dentro de `Customer`. `CustomerAddress` se pospone hasta que exista
   un caso real que la necesite.
49. **PENDIENTE — `service.customers.view` para técnicos de empresas ya
   provisionadas.** Por least privilege, la migración 0033 sólo amplía el preset
   `Administrador` intacto. Los técnicos de empresas existentes reciben la
   capacidad cuando su administrador la marca.
50. **PENDIENTE — portal del cliente.** Sin login propio de Customer, sin token
   de seguimiento, sin QR. El login sigue siendo `User`, global.
51. **PENDIENTE — Devices (Fase 5).** La ficha no muestra sección de equipos
   todavía: una tarjeta vacía prometiendo una función es una promesa que el
   producto no puede cumplir.

### Deuda que deja el incidente P0

52. **El check avisa, no aplica.** Es intencional: aplicar migraciones es una
   decisión de despliegue y la 0025 se detiene a propósito. Queda pendiente
   decidir si el arranque en producción debe además NEGARSE a servir con el
   esquema por detrás, en lugar de sólo avisar.
53. **`INVENTORY_MIGRATION_BRANCHES` sigue siendo un dict en `settings.py`**, no
   una variable de entorno. Documentado en `.env.example`, pero configurarlo
   todavía es tocar código. No se cambió aquí porque no hizo falta y habría sido
   ampliar el alcance de una fase de estabilización.

---

## 10. Próximas fases

**A. RBAC tenant-aware y aislamiento completo** — hecho en 2A/2A.1/2B/2C/2D
para catálogo, comercio e inventario. Queda `get_user_role()` global y el bridge
legacy.

**B. Series y correlativos por empresa** — hecho en la Fase 2E.

**C. Configuración y branding por empresa** — hecho en la Fase 3.

**D. FASE 5 — Equipos / Devices tenant-aware.** Con el cliente ya aislado, el
equipo es lo que le pertenece: tipo, marca, modelo, IMEI/serie e historial.
`Customer` → `Device` → `RepairOrder`.

**E. Inventario serializado IMEI/serie** — trazabilidad por unidad.

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
