# Changelog — Black Dog Store

Formato: cada entrada lista lo entregado y su estado
(`IMPLEMENTADO` · `PARCIAL` · `PENDIENTE` · `PROPUESTA` · `OBSOLETO`).

Este archivo se creó en la Fase SaaS 1. Las fases anteriores se reconstruyen a
partir del historial de git y de la documentación del repositorio; no se inventa
información que no esté respaldada por código o commits.

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
Cart tenant-aware                        PARCIAL (límite cerrado, 2C)
Order tenant-aware                       PENDIENTE 2C
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
