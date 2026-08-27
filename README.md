# Black Dog Store — Web

E-commerce Apple Specialist. Arequipa, Perú.
Stack: Next.js 16 (App Router) + Django 5.2 LTS + DRF + Stripe + PostgreSQL.

---

## Configuración inicial

### 1. Variables de entorno

```bash
cp .env.example backend/.env
# Edita backend/.env con tus valores reales
```

Valores mínimos para desarrollo local:

```env
SECRET_KEY=changeme-dev-only   # OK solo en dev (DEBUG=True)
DEBUG=1
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=                   # vacío = SQLite automático
STRIPE_SECRET_KEY=sk_test_xxx  # clave de test Stripe
STRIPE_WEBHOOK_SECRET=whsec_xxx
STRIPE_CURRENCY=pen             # Soles peruanos — NO cambiar a usd
STRIPE_DOMAIN=http://localhost:3000
```

Para producción, genera un SECRET_KEY real:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Stack de versiones

| Componente | Versión | Notas |
|---|---|---|
| **Django** | 5.2.15 LTS | LTS hasta abril 2028 |
| **Python (Docker)** | 3.12-slim | EOL octubre 2028, supera Django LTS |
| **Python (local dev)** | 3.14.x | Compatible con Django 5.2 en práctica |
| **DRF** | 3.17.1 | |
| **Next.js** | 16.2.9 | |

## Backend (Django)

### Requisitos

- Python 3.12+ (en Docker) / 3.14 (local macOS)
- Paquetes: `pip install -r backend/requirements.txt`

### Correr migraciones

```bash
cd backend
python manage.py migrate
```

### Verificar migraciones pendientes

```bash
python manage.py makemigrations --check --dry-run
# Debe responder: No changes detected
```

### Crear superusuario (acceso al admin)

```bash
python manage.py createsuperuser
```

### Ejecutar en desarrollo

```bash
python manage.py runserver
# API disponible en: http://localhost:8000/api/
# Admin: http://localhost:8000/admin/
```

### Ejecutar en producción

```bash
gunicorn backend.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

### Tests

```bash
python manage.py test store --verbosity=2
```

### Verificar configuración de seguridad

```bash
python manage.py check
python manage.py check --deploy   # Solo para simular entorno de producción
```

### Verificar que Stripe usa PEN

```bash
grep -i "stripe_currency" backend/backend/settings.py backend/.env .env.example
# Todos deben mostrar "pen", ninguno "usd"
```

---

## Frontend (Next.js)

### Requisitos

- Node.js 20+
- `cd frontend && npm install`

### Ejecutar en desarrollo

```bash
cd frontend
npm run dev
# Disponible en: http://localhost:3000
```

### Lint y type check

```bash
npm run lint          # ESLint — debe terminar con 0 errors
npx tsc --noEmit      # TypeScript — sin salida = sin errores
```

### Build de producción

```bash
npm run build
```

---

## Docker (dev + prod)

```bash
docker-compose up --build
```

Puertos:
- PostgreSQL: `5433` (host) → `5432` (contenedor)
- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3002`

> **Nota**: El Docker Compose usa `runserver` de Django (desarrollo).
> Para producción reemplazar con gunicorn en el Dockerfile.

---

## Arquitectura

```
BlackDogStore-web/
├── backend/
│   ├── backend/          # Django project settings
│   │   ├── settings.py   # Configuración por entorno (DEBUG-aware)
│   │   └── urls.py
│   ├── store/            # App principal
│   │   ├── models.py     # Category, Product, Order, CartItem, Coupon, Review
│   │   ├── views.py      # ViewSets + Stripe checkout + Webhook
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   ├── throttles.py  # Clases de throttle por endpoint
│   │   └── tests.py      # 84 tests unitarios e integración
│   ├── requirements.txt  # Versiones fijadas
│   └── manage.py
└── frontend/
    ├── app/              # Next.js App Router
    │   ├── components/   # Header, Footer, Hero, ProductCard, ProductDetail
    │   ├── lib/          # api.ts, auth.ts, cart.ts, format.ts
    │   ├── cart/         # Carrito con cupones
    │   ├── checkout/     # Pago Stripe
    │   ├── orders/       # Historial de pedidos
    │   └── product/      # Catálogo y detalle
    ├── public/assets/branding/  # Logo, favicon
    └── package.json
```

---

## API Endpoints — Permisos y Rate Limits (Fase 2.0)

| Método | Endpoint | Permiso | Rate Limit | Notas |
|--------|----------|---------|------------|-------|
| GET | `/api/products/` | Público | — | Filtros: slug, category, search |
| GET | `/api/categories/` | Público | — | |
| GET | `/api/reviews/?product=N` | Público | — | |
| POST | `/api/reviews/` | **Auth requerida** | 5/min | Requiere JWT; sin auth → 401 |
| GET | `/api/cart/?session_key=xxx` | Público (invitado) | 60/min | Aislado por session_key |
| POST | `/api/cart/add/` | Público (invitado) | 60/min | Valida stock |
| PATCH | `/api/cart/{id}/?session_key=xxx` | Público (invitado) | 60/min | Solo cambia `quantity`; valida stock |
| DELETE | `/api/cart/{id}/?session_key=xxx` | Público (invitado) | 60/min | Solo elimina ítems de la propia sesión |
| POST | `/api/coupons/validate/` | Público | 20/min | Normaliza código a mayúsculas |
| POST | `/api/payments/create-checkout-session/` | Público (guest checkout) | 10/min | Total calculado en backend |
| POST | `/api/payments/webhook/` | Público + firma Stripe | — | No usar JWT; idempotente |
| GET | `/api/payments/status/?session_id=cs_xxx` | Público | 30/min | Cross-user: 403 si usuario ajeno |
| GET | `/api/orders/` | **Auth requerida** | — | Solo propias órdenes |
| GET | `/api/orders/{id}/` | **Auth requerida** | — | 404 si orden ajena |
| POST | `/api/auth/register/` | Público | **5/min** | Email normalizado; password validado |
| POST | `/api/auth/login/` | Público | **5/min** | Tokens en cookies HttpOnly (no en body) |
| POST | `/api/auth/refresh/` | Público | — | Lee cookie refresh, emite nueva cookie access |
| POST | `/api/auth/logout/` | Público | — | Borra cookies blackdog_access y blackdog_refresh |
| GET | `/api/auth/csrf/` | Público | — | Emite csrftoken cookie (JS-legible) |
| GET | `/api/auth/me/` | **Auth requerida** | — | Perfil del usuario |

### Reglas de contraseña (registro)

- Mínimo 8 caracteres (Django `MinimumLengthValidator`)
- No puede ser solo numérica (Django `NumericPasswordValidator`)
- No puede ser una contraseña común ("password", "12345678", etc.)
- No puede ser similar al nombre de usuario (Django `UserAttributeSimilarityValidator`)
- Email normalizado a minúsculas antes de guardar
- Duplicado de email (case-insensitive) rechazado con 400

### Variables de entorno para producción

```env
SECRET_KEY=<generar con python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DEBUG=0
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DATABASE_URL=postgres://user:password@host:5432/dbname
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
STRIPE_SECRET_KEY=sk_live_...   # NUNCA commitear
STRIPE_WEBHOOK_SECRET=whsec_... # NUNCA commitear
STRIPE_CURRENCY=pen
STRIPE_DOMAIN=https://yourdomain.com
SECURE_SSL_REDIRECT=1
SECURE_HSTS_SECONDS=31536000
```

### Seguridad JWT — cookies HttpOnly (Fase 2.1)

- Los tokens `blackdog_access` y `blackdog_refresh` viajan en cookies **HttpOnly** — el JS del navegador nunca puede leerlos
- El `csrftoken` NO es HttpOnly (debe ser legible para que el frontend envíe `X-CSRFToken`)
- El frontend usa `fetchWithAuth()` que incluye `credentials: "include"` + cabecera `X-CSRFToken` automáticamente
- Si el access token expira, `fetchWithAuth()` llama a `/auth/refresh/` automáticamente (token transparente)
- `Authorization: Bearer` ya **no es aceptado** — solo cookies

| Cookie | HttpOnly | SameSite | Secure | TTL |
|--------|----------|----------|--------|-----|
| `blackdog_access` | ✓ | Lax | Prod only | 30 min |
| `blackdog_refresh` | ✓ | Lax | Prod only | 7 días |
| `csrftoken` | ✗ | Lax | Prod only | sesión |

### Seguridad JWT — Token Blacklist + CSRF en logout (Fase 2.2)

**Token blacklist (invalidación de refresh tokens):**
- `rest_framework_simplejwt.token_blacklist` está instalado y activo
- `BLACKLIST_AFTER_ROTATION=True`: el refresh token anterior queda invalidado después de cada rotación
- Al hacer logout, el refresh token activo es **blacklisteado inmediatamente** — no puede reutilizarse aunque no haya expirado
- Un token ya blacklisteado o expirado no rompe el logout — los errores se silencian
- Requiere ejecutar `python manage.py migrate` (13 migraciones de `token_blacklist` incluidas)

```
Flujo de rotación:
  1. Cliente tiene refresh_token_A (TTL 7 días)
  2. POST /auth/refresh/ → refresh_token_A es blacklisteado; se emite refresh_token_B
  3. Intentar usar refresh_token_A → 401 (blacklisted)

Flujo de logout:
  1. POST /auth/logout/ → refresh_token actual es blacklisteado + cookies borradas (max-age=0)
  2. Intentar usar ese refresh_token → 401 (blacklisted)
```

**Logout CSRF:**
- `LogoutView` usa `authentication_classes=[]` — el acceso se permite siempre (incluso con token expirado)
- Si la request trae `blackdog_access` **o** `blackdog_refresh`, se exige `X-CSRFToken` válido
- Si no hay cookies auth presentes (ya deslogueado), la limpieza de cookies ocurre sin CSRF
- El frontend ya envía `X-CSRFToken` en todas las llamadas a `logout()` — compatible sin cambios

### Seguridad de cuenta — Verificación de correo y recuperación de contraseña (Fase 2.3)

**Decisión de implementación:** Se usa `is_active=False` (User estándar de Django) para email no verificado. No se reemplaza el modelo de User para evitar romper todas las migraciones existentes.

**Configuración de email:**

```bash
# Desarrollo (por consola, sin SMTP real):
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
FRONTEND_URL=http://localhost:3000
REQUIRE_EMAIL_VERIFICATION=False  # cambiar a True para activar el flujo

# Producción (SMTP real):
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.tu-proveedor.com
EMAIL_PORT=587
EMAIL_HOST_USER=tu_usuario
EMAIL_HOST_PASSWORD=tu_contraseña
EMAIL_USE_TLS=True
```

**Modelo `AccountToken` — tokens hasheados:**
- Token plano generado con `secrets.token_urlsafe(48)` — 64 bytes de entropía
- Solo el SHA-256 del token se almacena en DB — una brecha de DB no permite verificar cuentas ni resetear contraseñas
- Token de verificación: expira en 24 horas
- Token de reset: expira en 1 hora
- Un solo uso — `used_at` se llena al consumir

**Flujo de verificación de correo:**
```
REQUIRE_EMAIL_VERIFICATION=True:
  1. POST /api/auth/register/ → usuario creado con is_active=False
  2. Email con link: FRONTEND_URL/auth/verify-email?token=<raw>
  3. Frontend POSTea token a POST /api/auth/verify-email/
  4. is_active=True → puede iniciar sesión

REQUIRE_EMAIL_VERIFICATION=False (default dev):
  1. POST /api/auth/register/ → usuario activo directamente
```

**Flujo de recuperación de contraseña:**
```
  1. POST /api/auth/password-reset/request/ {email} → respuesta genérica (anti-enumeración)
  2. Email con link: FRONTEND_URL/auth/reset-password?token=<raw>
  3. POST /api/auth/password-reset/confirm/ {token, new_password}
  4. Contraseña cambiada, refresh token blacklisteado, cookies borradas
  5. Usuario debe iniciar sesión con nueva contraseña
```

**Flujo de cambio de contraseña (autenticado):**
```
  1. POST /api/auth/change-password/ {current_password, new_password}
  2. Verifica contraseña actual → valida nueva → cambia
  3. Refresh token blacklisteado, cookies borradas — sesión cerrada en todos los dispositivos
  4. Usuario debe iniciar sesión nuevamente
```

**Endpoints nuevos:**

| Endpoint | Método | Auth | Rate limit | Propósito |
|----------|--------|------|------------|-----------|
| `/api/auth/verify-email/` | POST | No | - | Verificar correo con token |
| `/api/auth/resend-verification/` | POST | No | 3/min | Reenviar correo de verificación |
| `/api/auth/password-reset/request/` | POST | No | 3/min | Solicitar reset de contraseña |
| `/api/auth/password-reset/confirm/` | POST | No | 5/min | Confirmar reset con token |
| `/api/auth/change-password/` | POST | Sí (cookie) | 5/min | Cambiar contraseña autenticado |

**Anti-enumeración:** `resend-verification` y `password-reset/request` siempre devuelven la misma respuesta genérica independientemente de si el correo existe o no.

**Migraciones requeridas:**
```bash
python manage.py migrate  # aplica store.0007_account_token
```

**Páginas frontend nuevas:**
- `/auth/verify-email` — verifica el token al cargar (desde URL)
- `/auth/forgot-password` — formulario con correo
- `/auth/reset-password` — formulario con nueva contraseña (token desde URL)

### Pendientes explícitos (fases futuras)

- **Fase 3**: Roles avanzados (staff/admin/cliente); auditoría de acciones administrativas
- **Fase 4**: Dirección de envío en checkout; emails transaccionales avanzados
- **Fase 5**: Paginación en API; fix N+1 en `average_rating`/`review_count`

## Flujo de checkout seguro (Fase 1)

```
[Frontend]                    [Backend]                     [Stripe]
   |                              |                              |
   |-- POST /payments/create-checkout-session/ -->              |
   |   (session_key, name, email, coupon?)                      |
   |                              |                              |
   |          Valida carrito y stock desde DB                   |
   |          Calcula total desde precios en DB (no frontend)   |
   |          Valida cupón desde DB                             |
   |          Crea Order(status=pending_payment)                |
   |          Carrito NO se elimina aún                         |
   |          Inventario NO se decrementa aún                   |
   |                              |                              |
   |                              |-- stripe.Session.create() -->|
   |                              |<- {id: cs_xxx, url: ...} ---|
   |                              |                              |
   |          Guarda stripe_session_id en Order                 |
   |<-- {url: "https://checkout.stripe.com/..."} --------------|
   |                              |                              |
   |-- redirect to Stripe URL -->                               |
   |                              |                              |
   |                              |       Usuario paga          |
   |                              |                              |
   |                              |<-- POST /payments/webhook/ --|
   |                              |   (checkout.session.completed)
   |          Verifica firma Stripe                             |
   |          select_for_update() (idempotencia)                |
   |          Decrementa inventario con F()                     |
   |          Order.status = paid, paid_at = now()              |
   |          Elimina carrito                                   |
   |                              |                              |
   |<-- redirect to /checkout/success?session_id=cs_xxx --------|
   |                              |                              |
   |-- GET /payments/status/?session_id=cs_xxx -->              |
   |<-- {status: "paid", paid: true, total: "..."} ------------|
   |                              |                              |
   |  Muestra "¡Pago confirmado!" SOLO con confirmación backend |
```

### Invariantes de seguridad

- Precios, totales y descuentos calculados **solo en backend**
- El carrito se elimina **solo** cuando el webhook de Stripe confirma el pago
- El inventario se decrementa **solo** en el webhook, usando `F()` expressions atómicas
- La página de éxito consulta el backend antes de mostrar cualquier mensaje positivo
- El webhook es idempotente: un segundo evento `completed` no decrementa inventario dos veces

---

## Fase 3.0 — RBAC y Auditoría de Acciones Admin

### Decisiones de diseño

- **Sin custom User model**: se usa `UserProfile` (OneToOne con Django's `User`) para no romper migraciones ni integraciones futuras.
- **Django `is_superuser=True`** siempre equivale a `superadmin`, sin importar el valor en `UserProfile.role`. Esto garantiza compatibilidad con la cuenta de superusuario creada con `createsuperuser`.
- **`is_staff=True`** solo controla acceso al admin de Django; el acceso a la API se gestiona exclusivamente por `UserProfile.role`.
- **Señal `post_save`** en `store/signals.py` crea automáticamente un `UserProfile(role='customer')` al crear cualquier `User`.
- **`AdminAuditLog`** almacena actor, acción, tipo/ID objetivo, metadata JSON, IP y user-agent. El método de clase `AdminAuditLog.log()` simplifica el registro desde cualquier vista.

### Roles de negocio

| Rol | Código | Puede ver todas las órdenes | Puede cambiar roles | Puede listar usuarios |
|-----|--------|-----------------------------|--------------------|-----------------------|
| Cliente | `customer` | ✗ (solo propias) | ✗ | ✗ |
| Vendedor | `sales` | ✓ | ✗ | ✗ |
| Inventario | `inventory` | ✗ | ✗ | ✗ |
| Técnico | `technician` | ✗ | ✗ | ✗ |
| Administrador | `admin` | ✓ | ✗ | ✓ |
| Superadministrador | `superadmin` | ✓ | ✓ | ✓ |

### Nuevos endpoints

| Método | URL | Permiso | Descripción |
|--------|-----|---------|-------------|
| `GET` | `/api/admin/users/` | Admin+ | Lista todos los usuarios con su rol |
| `PATCH` | `/api/admin/users/{id}/role/` | Superadmin | Cambia el rol de un usuario |
| `GET` | `/api/admin/audit-logs/` | Admin+ | Lista últimas 200 entradas del audit log |

### `/auth/me/` — campos nuevos

El endpoint ahora incluye `role` e `is_staff` en su respuesta:

```json
{
  "id": 1,
  "username": "carlos",
  "email": "carlos@example.com",
  "first_name": "Carlos",
  "last_name": "",
  "role": "admin",
  "is_staff": false
}
```

### Archivos nuevos/modificados

- `backend/store/models.py` — `UserProfile`, `AdminAuditLog`
- `backend/store/permissions.py` — `get_user_role()`, `IsAdminRole`, `IsSuperAdminRole`, `CanManageOrders`
- `backend/store/admin_views.py` — `AdminUserListView`, `AdminUserRoleView`, `AdminAuditLogListView`
- `backend/store/signals.py` — señal `post_save` para auto-crear `UserProfile`
- `backend/store/apps.py` — registra señal en `StoreConfig.ready()`
- `backend/store/views.py` — `OrderViewSet.get_queryset()` actualizado con roles
- `backend/store/auth_views.py` — `UserDetailView.get()` incluye `role` e `is_staff`
- `backend/store/admin.py` — registra `UserProfile` y `AdminAuditLog`
- `backend/store/urls.py` — rutas `/api/admin/*`
- `backend/backend/settings.py` — `INSTALLED_APPS` usa `store.apps.StoreConfig`
- `frontend/app/lib/auth.ts` — tipo `AuthUser` incluye `role` e `is_staff`, helper `isAdminRole()`
- `frontend/app/components/Header.tsx` — muestra enlace "Admin" solo para roles admin/superadmin
- `backend/store/migrations/0008_adminauditlog_userprofile.py` — migración nueva

---

## Fase 6.0 — Inventario avanzado y notas de venta internas

Todo cambio de stock pasa por `store/inventory_services.py` y genera una línea de
Kardex (`StockMovement`) en la misma transacción: stock y Kardex nunca divergen.

- **Kardex completo** — cada movimiento guarda `stock_before`, `stock_after`, motivo, responsable y referencia.
- **Salidas por venta idempotentes** — un webhook de Stripe reintentado nunca descuenta stock dos veces.
- **Stock nunca negativo** — una salida que lo dejaría bajo cero se rechaza y hace rollback.
- **Reportes operativos** — bajo/alto stock, agotados, más vendidos, valor del inventario, productos sin movimiento.
- **Notas de venta internas** (`NV-000001`) con PDF para órdenes pagadas.

> ⚠️ Las notas de venta son **documentos internos**. No son comprobantes
> electrónicos SUNAT, no son numeración fiscal y no tienen validez tributaria.

Separación de roles: `inventory` mueve stock pero no emite documentos ni toca
pagos; `sales` emite notas de venta pero no puede alterar stock.

Panel: `/admin/inventory`, `/admin/inventory/movements`, `/admin/inventory/reports`,
`/admin/products/{id}/stock-card`.

Detalle completo — modelos, endpoints, permisos, auditoría y pendientes:
[docs/inventario-y-notas-de-venta.md](docs/inventario-y-notas-de-venta.md)

---

## Fase SaaS 1 — Fundación multiempresa

Base estructural para que la plataforma deje de asumir una sola empresa.
`Company` → `Branch` → `Membership`, más un módulo de resolución de tenant.

> **Estado: PARCIAL.** Los modelos existen y los endpoints de administración
> aíslan por empresa, pero el e-commerce **todavía no está tenantizado**:
> catálogo, carrito, checkout, inventario y notas de venta operan exactamente
> igual que antes.

- **El tenant nunca se toma del cliente.** Un `company_id` en el body es dato a
  validar contra las membresías del propio llamante, nunca la respuesta a "¿de
  qué empresa es esta petición?".
- **Black Dog Store es el primer tenant**, creado por migración de datos — no hay
  ninguna constante de empresa en la capa de negocio.
- **Tres niveles de autoridad separados**: `User.is_superuser` (plataforma),
  `Membership.role` (empresa) y `UserProfile.role` (legacy global). Ninguno
  implica otro — un `Membership.role="superadmin"` no da autoridad de plataforma.
- **El dominio comercial sigue en RBAC legacy** (`UserProfile.role`) porque
  `Product`, `Order`, `StockMovement` y `SalesNote` todavía no tienen `company`.
  Cambiar sus permisos antes que sus datos daría una falsa sensación de aislamiento.

```
Autenticación única                      IMPLEMENTADO
E-commerce / portal externo              IMPLEMENTADO
Control interno — fundamento             IMPLEMENTADO
Control interno — módulos completos      PENDIENTE
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

### Acceso interno configurable (Fase 2A.1)

Tres superficies sobre **una sola identidad** `User`:

| Superficie | Requisito | Alcance |
|---|---|---|
| Portal externo | **Cualquier usuario** (y anónimo en las partes públicas) | Tienda, carrito, checkout, compras, cuenta |
| Control interno | `User` + Membership activa + Company activa + capabilities | Panel de su empresa, según sus roles |
| Platform control | `User.is_superuser` | Todos los tenants |

Son **superficies, no tipos de usuario**: un mismo `User` puede estar en varias a
la vez. Tener Membership no deja de convertirte en cliente — un técnico de Black
Dog sigue comprando en la tienda con la misma cuenta.

Cada empresa define sus propias **áreas** (Taller, Recepción, Caja…) y sus
propios **roles** (Técnico Senior, Almacenero…) eligiendo de un catálogo de
capacidades que controla la plataforma. Un usuario puede llevar varios roles en
una misma empresa sin duplicar su membresía.

> **Las áreas no otorgan permisos.** Pertenecer a «Inventario» no habilita
> `inventory.adjust`; la autoridad viene solo de las capacidades del rol.

Un administrador de empresa **solo puede delegar capacidades que él mismo tiene**.

Toda empresa nueva recibe sus áreas y roles iniciales mediante
`provision_company_access_defaults()` — el mismo servicio para la API y el Django
Admin, idempotente y neutral. Son **presets**, no las únicas opciones válidas.

Detalle, deuda pendiente y próximas fases:
[docs/saas-multiempresa.md](docs/saas-multiempresa.md) · [CHANGELOG.md](CHANGELOG.md)

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
