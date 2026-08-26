# Fundación SaaS multiempresa

> **Fase SaaS 1** · Base estructural para operar más de un negocio en la plataforma.

**Estado global de la fase: PARCIAL** — la fundación está `IMPLEMENTADO`; la
tenantización de los modelos de negocio queda `PENDIENTE` por diseño.

```
Autenticación única                    IMPLEMENTADO
Portal externo e-commerce              IMPLEMENTADO
Control interno                        PARCIAL
Platform master                        IMPLEMENTADO
Membership                             IMPLEMENTADO
CompanyArea                            IMPLEMENTADO
CompanyRole                            IMPLEMENTADO
Role assignments                       IMPLEMENTADO
Capabilities configurables por rol     IMPLEMENTADO
Legacy RBAC fallback                   IMPLEMENTADO / TRANSICIÓN
Tenant resolution                      PARCIAL
Portal cliente servicio técnico        PENDIENTE
Product tenant-aware                   PENDIENTE
Order tenant-aware                     PENDIENTE
Inventory tenant-aware                 PENDIENTE
Membership Invitation Flow             PENDIENTE
Branding                               PENDIENTE
IMEI/Serial                            PENDIENTE
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

| Superficie | Quién es | Cómo se identifica | Qué ve |
|---|---|---|---|
| **PORTAL EXTERNO** | Cliente del e-commerce | `User` **sin** Membership | Catálogo, carrito, checkout, sus compras, reseñas, su cuenta |
| **CONTROL INTERNO** | Personal de una empresa | `User` + Membership activa + Company activa | Panel de la empresa, según las capacidades de sus roles |
| **PLATFORM CONTROL** | Operador del SaaS | `User.is_superuser` — y solo eso | Todos los tenants |

**Una sola identidad**: no hay `CustomerUser`, `StaffUser` ni `MasterUser`. El
alcance sale de las relaciones del `User`, no de su modelo. Un mismo usuario
puede comprar en la tienda y ser empleado de una empresa sin duplicar credenciales.

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
12. **`frontend/db.sqlite3` está versionado** (0 bytes, de antes de que `.gitignore`
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
