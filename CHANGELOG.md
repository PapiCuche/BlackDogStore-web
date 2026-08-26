# Changelog — Black Dog Store

Formato: cada entrada lista lo entregado y su estado
(`IMPLEMENTADO` · `PARCIAL` · `PENDIENTE` · `PROPUESTA` · `OBSOLETO`).

Este archivo se creó en la Fase SaaS 1. Las fases anteriores se reconstruyen a
partir del historial de git y de la documentación del repositorio; no se inventa
información que no esté respaldada por código o commits.

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
Fundación SaaS                       IMPLEMENTADO
Tenant resolution                    PARCIAL
RBAC tenant-aware infraestructura    IMPLEMENTADO
RBAC legacy                          IMPLEMENTADO / TRANSICIÓN
Tenantización Product                PENDIENTE
Tenantización Order                  PENDIENTE
Tenantización Inventory              PENDIENTE
Membership Invitation Flow           PENDIENTE
Branding                             PENDIENTE
IMEI/Serial                          PENDIENTE
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
