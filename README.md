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

### API v1 — catálogo público para clientes nativos

Superficie **versionada y aditiva**. `/api/` no cambia.

| Método | Endpoint | Permiso | Notas |
|--------|----------|---------|-------|
| GET | `/api/v1/storefront/<company_slug>/products/` | Público, anónimo | Filtros: category, search, in_stock, ordering |
| GET | `/api/v1/storefront/<company_slug>/products/<product_slug>/` | Público, anónimo | Lookup por slug |
| GET | `/api/v1/storefront/<company_slug>/categories/` | Público, anónimo | |

El storefront web resuelve su empresa por **Host**. Una app móvil llega a un
host de API compartido y no tiene ese Host, así que aquí el tenant va **en la
ruta**.

Ese slug **selecciona un escaparate público; no autoriza nada**. Las superficies
privadas siguen derivando su empresa de la membresía del usuario autenticado,
nunca de un segmento de ruta.

Empresa desconocida, inactiva, malformada o vacía → **el mismo 404**, para que
el endpoint no pueda recorrerse y enumerar qué empresas existen.

Autenticación **apagada explícitamente** en estas vistas: un navegador con
sesión y una app anónima reciben el mismo catálogo. `/api/v1/auth/*` no existe
todavía.

Detalle en `docs/saas-multiempresa.md` § 8-septendecies.

### API v1 — autenticación nativa para clientes móviles

Contrato **separado** del web. `/api/auth/*` no cambia.

| Método | Endpoint | Auth | Notas |
|--------|----------|------|-------|
| POST | `/api/v1/auth/login/` | Ninguna · 5/min | `{email, password}` → tokens en el **cuerpo** |
| POST | `/api/v1/auth/refresh/` | Ninguna | `{refresh}` → access + refresh **rotado** |
| POST | `/api/v1/auth/logout/` | Ninguna | Best-effort, siempre 200 |
| GET | `/api/v1/auth/me/` | **Bearer v1** | Identidad + empresas verificadas |

La web autentica por cookie HttpOnly + CSRF, porque el navegador adjunta cookies
a peticiones que el usuario no inició. Una app nativa guarda su token y lo envía
a propósito, así que usa `Authorization: Bearer` y no necesita CSRF.

**`V1BearerAuthentication` NUNCA es global.** No está en
`DEFAULT_AUTHENTICATION_CLASSES` y no debe añadirse: eso abriría `/api/admin/`,
`/api/auth/me/` y todas las vistas privadas web a un token del contrato móvil.
Solo las vistas privadas v1 la declaran, con tests que lo verifican.

**Login por email.** `email` no es unique en esta DB, así que 0 coincidencias, 1
con contraseña incorrecta, cuenta inactiva y >1 coincidencias devuelven **el
mismo 401**, y sin usuario se verifica igual contra un hash dummy para no filtrar
por tiempo. No se añadió constraint unique: fallaría en cualquier instalación con
duplicados, durante el deploy.

**`available_companies`** se calcula desde `Membership` activa **o** `Customer`
activo del usuario autenticado, con la empresa activa, etiquetando cuál es. No es
autorización: toda API privada debe revalidar por su cuenta.

**Fuera de scope (BR-001B):** registro, verificación, reset y cambio de
contraseña nativos. Devuelven 404 a propósito.

Detalle en `docs/saas-multiempresa.md` § 8-duodevicies.

### API v1 — superficie de CLIENTE

Tercera audiencia, en su propio espacio de URL (**DEC-API-001**):

| Prefijo | Audiencia | Auth |
|---|---|---|
| `/api/v1/storefront/<slug>/` | pública | ninguna |
| `/api/v1/customer/<slug>/` | **cliente, sus propios registros** | Bearer v1 |
| `/api/v1/internal/<slug>/` | staff bajo capability | **no existe todavía** |

| Método | Endpoint | Notas |
|--------|----------|-------|
| GET | `/api/v1/customer/<company_slug>/orders/` | Solo los pedidos del llamante |
| GET | `/api/v1/customer/<company_slug>/orders/<id>/` | 404 si no es suyo |

**La propiedad son dos FKs**: `Order.user` (compró con sesión) o
`Order.customer.user` (compra anónima que el negocio emparejó por documento con
su ficha CRM, ficha luego enlazada a su cuenta). **El email nunca es propiedad**:
no tiene unicidad y una familia comparte dirección.

**Ser empleado no es ser cliente.** Vendedor, almacenero, técnico, administrador
de empresa y platform master reciben **404** aquí. El acceso interno a los
pedidos de la empresa será `sales.orders.view` en la superficie interna.

Empresa desconocida, inactiva y "no eres cliente" → **el mismo 404**.

`fulfillment_status` se expone por fin (**BR-003**, cerrado para v1). No viajan
identificadores de Stripe, diagnósticos operativos ni claves de sesión.

### `access_contexts` en el contrato de auth

`login` y `me` incorporan, **junto a** `available_companies` (que no cambia):

```json
"access_contexts": [{"company": {...}, "customer": true, "member": true,
                     "capabilities": ["inventory.view"]}],
"platform": {"is_master": false}
```

`customer` y `member` son booleanos independientes, no un rol. Las capabilities
salen de `resolve_capabilities()` y sirven **solo para presentación**: todo
endpoint interno las vuelve a validar en el servidor.

Detalle en `docs/saas-multiempresa.md` § 8-undevicies.

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

> **Estado: IMPLEMENTADO hasta la Fase 2D.** Catálogo (2B), pedidos, carrito y
> checkout (2C) e inventario multisucursal (2D) están tenantizados. Queda el
> branding por empresa y el correlativo de notas de venta.

- **El tenant nunca se toma del cliente.** Un `company_id` en el body es dato a
  validar contra las membresías del propio llamante, nunca la respuesta a "¿de
  qué empresa es esta petición?".
- **Black Dog Store es el primer tenant**, creado por migración de datos — no hay
  ninguna constante de empresa en la capa de negocio.
- **Tres niveles de autoridad separados**: `User.is_superuser` (plataforma),
  `Membership.role` (empresa) y `UserProfile.role` (legacy global). Ninguno
  implica otro — un `Membership.role="superadmin"` no da autoridad de plataforma.
- **El dominio comercial ya NO está en RBAC legacy.** `Product` (2B), `Order` y
  `Coupon` (2C), `StockMovement` (2D) e `InternalSequence` (2E) tienen `company`,
  y sus capabilities gobiernan de verdad. Queda el bridge para operadores
  pre-SaaS sin Membership.

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
KPIs comerciales reales                  IMPLEMENTADO
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
Dashboard inventory KPIs                 IMPLEMENTADO
StockMovement explicit tenancy           IMPLEMENTADO
Profitability                            PENDIENTE (sin modelo de costos)
Inventory company isolation              IMPLEMENTADO
Inventory branch isolation               IMPLEMENTADO
Branch access model                      IMPLEMENTADO
Membership multisucursal                 IMPLEMENTADO
BranchStock                              IMPLEMENTADO
Kardex por sucursal                      IMPLEMENTADO
Entradas / salidas manuales              IMPLEMENTADO
Salidas por venta por sucursal           IMPLEMENTADO
Transferencias entre sucursales          IMPLEMENTADO
Recuentos físicos                        IMPLEMENTADO
Reposición sugerida                      IMPLEMENTADO
Dashboard de inventario                  IMPLEMENTADO
Selector de sucursal                     IMPLEMENTADO
UI de acceso por sucursal                IMPLEMENTADO
CompanySettings                          IMPLEMENTADO
Identidad comercial por empresa          IMPLEMENTADO
Branding del storefront                  IMPLEMENTADO
Branding del control interno             IMPLEMENTADO
Emails de pedido por empresa             IMPLEMENTADO
PDFs por empresa                         IMPLEMENTADO
Notificación interna por empresa         IMPLEMENTADO
Snapshot histórico de identidad          IMPLEMENTADO
Política de garantía por empresa         IMPLEMENTADO
Datos de negocio por sucursal            IMPLEMENTADO
Pantalla de Configuración                IMPLEMENTADO
Pantalla de Sucursales                   IMPLEMENTADO
Timezone por empresa                     PARCIAL (almacenado y validado)
Currency por empresa                     PARCIAL (solo lectura: Stripe en PEN)
Favicon por empresa                      PENDIENTE
Contenido de landing por empresa         PENDIENTE
Subida de logos                          PENDIENTE (hoy es una URL)
SMTP por tenant                          PENDIENTE (no se guardan secretos)
Series / correlativos                    PENDIENTE 2E
Product.inventory                        OBSOLETO (agregado de compatibilidad)
Recepción parcial de transferencias      PENDIENTE
Reservas multi-almacén                   PENDIENTE
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
Servicio técnico                         PENDIENTE
Dashboard interno avanzado               PENDIENTE
Membership Invitation Flow               PENDIENTE
IMEI/Serial                              PENDIENTE
```

### Punto de venta y reposición inteligente (Fase Comercial C1)

El mostrador entra al sistema. Antes, un negocio con tienda física vendía fuera
de la plataforma, así que el stock del panel era el que quedaba *después de
restar lo que nadie había registrado*.

```
   ONLINE ──┐
            ├──► Order ──► SALE_EXIT ──► BranchStock ──► pronóstico
   POS ─────┘
```

- **Una sola venta, dos canales.** Una venta de mostrador es un `Order` normal:
  aparece en el historial, en los reportes y puede emitir su nota interna.
- **Lector de código de barras USB o Bluetooth**, del tipo que se comporta como
  un teclado. Sin drivers ni SDK. Todo funciona igual tecleando el código.
- **Varios códigos por artículo**: el EAN del fabricante, un UPC y la etiqueta
  propia de la tienda conviven. Se guardan como texto, así que
  `0123456789012` no se convierte en `123456789012`.
- **Todo o nada.** Si un artículo de la cesta no está, la venta entera se
  rechaza: no se cobra, no se mueve stock y no queda pedido a medias.
- **Un doble clic no cobra dos veces.** Cada cesta lleva su clave —obligatoria—
  y su huella; un reintento devuelve la misma venta, y la misma clave con otra
  cesta se rechaza en vez de confundirlas.
- **Las condiciones las confirma quien vende**, marcándolo antes de cobrar. El
  sistema no da por hecho que entregar el producto equivale a haberlas
  explicado.
- **Cada empresa cuenta sus días en su propia zona horaria**, así que la venta
  de las diez de la noche cae en el día que le corresponde.
- **El stock sale de la sucursal donde se vende**, nunca de otra. Si hay
  unidades en otra tienda el sistema lo dice — pero moverlas es una
  transferencia, y esa es una decisión con papeleo.
- **Pronóstico de demanda explicable**: `0.50·avg7 + 0.30·avg30 + 0.20·avg90`,
  sobre las ventas reales de estante. Los días sin venta cuentan como cero, que
  es donde casi todo el mundo se equivoca.
- **Sin historial suficiente, el sistema lo dice** en vez de inventar una fecha.
  Las alertas de "sin stock" y "bajo mínimo" siguen funcionando igual.
- **Punto de reposición y cantidad sugerida** por sucursal, con el plazo de
  entrega que configure el negocio. Sugiere; no compra nada.

- **Cliente, vendedor y comisión en cada venta.** El POS registra a quién se le
  vendió, a quién se le acredita —que no siempre es quien cobra— y cuánto genera
  de comisión, congelado en el momento: cambiar un porcentaje mañana no reescribe
  lo de ayer.
- **Descuentos con reglas.** Un cupón que la empresa configuró lo aplica
  cualquiera; teclear un descuento a mano necesita permiso, motivo y queda
  firmado. Los dos juntos se rechazan: apilar promociones es una política que el
  negocio debe decidir, no la caja.
- **Efectivo y vuelto los calcula el servidor**, y en tarjeta simplemente no
  existen en lugar de figurar como cero.

Pantallas: `/admin/sales` (facturación, canales, más vendidos, reposición),
`/admin/sales/pos` (la caja) y `/admin/sales/commissions` (devengado por vendedor
y porcentajes del equipo).

> **No hay margen ni utilidad, a propósito.** La plataforma no registra cuánto
> costó nada, así que cualquier cifra de rentabilidad sería inventada. Se
> muestra facturación, que sí se sabe. Caja, arqueo y devoluciones son la
> siguiente fase comercial.

### Actualizar una instalación existente

El código y la base de datos tienen que ir a la par. Si la base se queda por
detrás, rutas perfectamente válidas responden **500** con `no such column` — no
es un bug del código, es el esquema sin actualizar.

```bash
cd backend
python manage.py migrate --plan     # qué falta
python manage.py migrate            # aplicarlo
```

Desde el incidente P0, `manage.py check` y `runserver` **avisan al arrancar** si
faltan migraciones y dicen cuáles. El aviso no las aplica: aplicarlas es una
decisión de despliegue, y algunas cambian datos.

> **Haz copia de seguridad antes de migrar una base con datos reales.** La
> migración 0025 reparte entre sucursales el stock que antes era una sola cifra
> por producto. Con una sucursal activa lo resuelve sola; con varias **se
> detiene** y pide `INVENTORY_MIGRATION_BRANCHES` (ver `.env.example`). Que se
> detenga es correcto: prefiere no repartir unidades a una tienda que nunca las
> tuvo.

### Clientes por empresa — CRM interno (Fase 4)

Primer dominio del núcleo de Servicio Técnico. La plataforma pasa a saber *quién
es este cliente para esta empresa*, que es lo que un equipo en reparación
necesitará tener detrás.

```
User        un login de plataforma
Membership  esta persona TRABAJA en esta empresa
Customer    esta persona COMPRA a esta empresa
Order.customer_*  quién compró ESE DÍA, congelado
```

- **Un cliente existe sin cuenta, y es el caso normal.** La mayoría llega al
  mostrador, llama o escribe por WhatsApp y nunca tendrá login.
- **Un mismo login puede ser cliente de varias empresas, con fichas
  independientes.** Comparten la cuenta y nada más: ni notas, ni dirección, ni
  historial. Dos negocios que atienden a la misma persona no se leen la ficha.
- **Nadie se fusiona por parecido.** Email, teléfono y nombre **no** son
  identidad: las familias comparten bandeja y las oficinas comparten central. Se
  empareja sólo por cuenta o por documento, exacto.
- **Los duplicados posibles se sugieren; los documentos repetidos se rechazan**
  con la ficha existente adjunta, para poder abrirla en lugar de chocar contra un
  error.
- **El historial no se reescribe.** `Order.customer` es quién es hoy; los campos
  `customer_*` del pedido son quién era entonces. Cambiar de teléfono no cambia
  lo que dice un pedido del año pasado.
- **Un problema de CRM nunca cuesta una venta.** Si el emparejamiento falla, el
  pedido se guarda sin cliente y conserva todo lo necesario para vincularlo a
  mano.
- **Un cliente no pertenece a una sucursal.** Compra en una, deja un equipo en
  otra y lo recoge en una tercera.
- **Se archiva, no se borra.** Un cliente con compras es indeleble, y lo dice la
  base de datos.

Pantallas: `/admin/customers` (listado, búsqueda, alta) y `/admin/customers/{id}`
(resumen, historial comercial y notas internas).

> **Los datos del cliente no salen a internet.** No existe ningún endpoint
> público de clientes, y esa ausencia es la garantía. El listado interno tampoco
> devuelve las notas: se leen en la ficha, no de reojo en un mostrador.

### Numeración interna por empresa (Fase 2E)

Cada empresa lleva su propio correlativo, y la actividad de una deja de ser
visible en los números de otra.

```
InternalSequence   el contador como fila: prefijo, padding, próximo número
  └── SalesNote    guarda a qué serie pertenece y qué ordinal ocupa
```

- **El número se reparte, no se calcula.** Antes era `MAX(number) + 1` sobre toda
  la tabla: la empresa B emitía `NV-000002` porque la A había emitido
  `NV-000001`. Ahora cada serie es una fila que se bloquea, se lee y se
  incrementa dentro de la misma transacción que escribe el documento.
- **Dos empresas pueden tener cada una su `NV-000001`.** El `unique` global de
  `SalesNote.number` desapareció; la unicidad es *un ordinal por serie*, que es
  lo que se quería desde el principio.
- **El número se guarda, no se deriva.** Cambiar el prefijo hoy no reescribe lo
  que dice un documento que un cliente ya tiene en la mano.
- **Los huecos son historia.** Una nota anulada deja su ordinal consumido.
  Renumerar para cerrarlo reasignaría un identificador ya emitido.
- **Una numeración por empresa o una por sucursal**, configurable — y congelada
  tras el primer documento, para que un mismo negocio no muestre el mismo número
  en dos documentos.
- **El próximo número es editable sólo antes de emitir**, para quien migra desde
  otro sistema y quiere seguir en 5001.

Pantalla: `/admin/settings` → «Numeración interna», con vista previa que **no**
consume un número.

> **Esto es numeración interna, no fiscal.** No es una serie SUNAT, ni un CPE, ni
> una boleta o factura electrónica. No hay XML, UBL, firma digital ni OSE. La API
> y los PDFs lo repiten, porque `NV-000001` junto a un logo y un total se parece
> mucho a un comprobante.

### Configuración y branding por empresa (Fase 3)

Cada empresa deja de ser descrita por constantes en el código y pasa a
describirse a sí misma.

```
Company            quién es este tenant para la plataforma
  └── CompanySettings   cómo se presenta y cómo habla con sus clientes
```

- **Ya no hay identidad compilada.** Tres servicios comerciales llevaban seis
  constantes con el nombre, la razón social, el RUC, la dirección y el teléfono
  de una empresa concreta. Los clientes de un segundo tenant habrían recibido su
  email y su PDF con la identidad legal de otra empresa. Un test estructural
  vigila que no vuelvan.
- **El fallback es a vacío, nunca a otra empresa.** Un tenant incompleto muestra
  blancos, y la pantalla de configuración dice cuáles. Un blanco se corrige; una
  identidad equivocada en un documento no.
- **Los documentos se congelan al vender.** `Order.company_snapshot` guarda la
  identidad del momento, así que un recibo reimpreso un año después dice lo mismo
  que el día que se emitió — aunque el negocio se haya renombrado o mudado.
- **La notificación de venta va a la empresa del pedido**, y sin dirección
  configurada no se envía. No hay fallback de plataforma: esa variable guardaba
  una sola dirección.
- **El storefront toma su branding del host**: logo, nombre, paleta, footer,
  contacto, metadata y OpenGraph.
- **Emails de cuenta ≠ emails de pedido.** Verificación y reseteo de contraseña
  son de la PLATAFORMA (un `User` es global); los de compra son del tenant.

Pantallas: `/admin/settings` (identidad, branding, contacto, políticas,
notificaciones) y `/admin/branches` (ubicaciones y sucursal de despacho).

> **Colores sólo `#RRGGBB`.** No es estética: estos valores entran en una custom
> property y en un atributo `style`, y cualquier cosa que pueda expresar
> `url(...)` o un esquema es una inyección CSS con un selector de color delante.
> Se validan en el modelo, en el serializer y otra vez antes de renderizar.

**Cambio de build:** el storefront pasó de estático a dinámico. Su contenido
depende del host, así que un prerender único habría servido el título de una
empresa en todos los dominios.

### Inventario multisucursal (Fase 2D)

El stock deja de ser un entero por producto y pasa a vivir en sucursales.

```
Company
 ├── Branch                          ubicación real de stock
 ├── Product
 └── BranchStock(branch, product)    ← la fuente de verdad
```

- **Dos ejes de autoridad, y ambos deben pasar.** La *capability*
  (`inventory.view` / `adjust` / `reports`) dice **qué** puedes hacer; el acceso a
  sucursal dice **dónde**. Tener `inventory.adjust` no es permiso para ajustar
  todas las sucursales, y llegar a una sucursal no es permiso para mover su stock.
- **Alcance de sucursales explícito.** `todas` (incluye las que se abran mañana) o
  `seleccionadas` (y sólo ésas — una sucursal nueva **no** se concede sola). Cero
  sucursales seleccionadas significa ninguna, y deniega. Se rechazó el diseño
  «sin filas = todas» porque falla abierta.
- **`Product.inventory` es ahora un agregado de compatibilidad**, mantenido en la
  misma transacción que cada movimiento. Es derivado: ninguna decisión sobre si
  una venta puede cumplirse sale de él.
- **La tienda online vende desde una sucursal de despacho declarada.** El catálogo
  público muestra el stock de **esa** sucursal, no el total de la empresa —
  mostrar 20 cuando el checkout sólo puede entregar 2 es prometer una venta que
  falla en el último paso. Con varias sucursales y ninguna declarada, el checkout
  se niega y lo dice.
- **Transferencias entre sucursales**: el stock sale al despachar y entra al
  recibir. Nunca las dos cosas a la vez: acreditar el destino al despachar
  mostraría stock en una tienda que lo tiene en una furgoneta.
- **Recuentos físicos** que releen el stock **al aprobar**, bajo lock. La
  corrección es `físico − teórico al aprobar`; usar la foto del inicio descontaría
  en silencio todo lo vendido durante el conteo.
- **Reposición sugerida** por mínimo y objetivo de cada sucursal. Sugiere: no
  genera compras ni transferencias.

> **Ninguna cifra de costo, utilidad ni margen.** El sistema no registra precio de
> compra. El único importe del inventario es **stock × precio de venta**, y está
> etiquetado como tal en la API y en la UI.

Panel: `/admin/inventory`, `/admin/inventory/movements`,
`/admin/inventory/transfers`, `/admin/inventory/counts`,
`/admin/inventory/replenishment`, `/admin/inventory/reports`.

**Migración:** con una sola sucursal activa por empresa se resuelve sola. Con
varias y ninguna indicada, la migración **falla ruidosamente** y pide
`INVENTORY_MIGRATION_BRANCHES` en settings — repartir las unidades escribiría una
cifra que parece autoritativa y es ficción.

### Comercio multiempresa (Fase 2C)

`Order` y `Coupon` pertenecen a una `Company`. El flujo completo —carrito,
checkout, Stripe, webhook, inventario— conserva el tenant de punta a punta.

- **Un navegador puede tener un carrito por tienda** a la vez: el carrito es
  `session_key` + la empresa del producto, sin modelo `Cart`.
- **El checkout toma su empresa del storefront**, nunca del body.
- **El webhook resuelve el tenant desde `Order.company`**, no desde el host (Stripe
  llama a un único endpoint) ni desde la metadata (que solo se contrasta).
- **El cliente ve en cada tienda solo sus pedidos de esa tienda**, con una única cuenta.
- Dos empresas pueden correr el mismo código de cupón.

El dashboard muestra ya **ventas reales por empresa**: ventas de hoy, ticket
promedio, ingresos, pendientes de pago, por despachar, tendencia de 7 días y
pedidos por estado.

> **Sin utilidad ni margen.** No hay modelo de costos, así que cualquier cifra de
> margen sería una resta inventada.

### Dashboard visual del Control Interno (Fase 2B.1)

`/admin` muestra ahora KPIs y **gráficos** de la empresa activa: estado del
catálogo, productos por categoría, personal por área y por rol, y cobertura de
módulos del sistema.

> **No hay ventas, caja ni stock en el dashboard.** `Order` y `StockMovement`
> todavía no pertenecen a una empresa: cualquier cifra suya sería un número de
> toda la plataforma mostrado como si fuera de esta empresa. Llegan en 2C/2D.

Gráficos en SVG propio, sin añadir dependencias — la marca es monocroma, así que
la magnitud se codifica con opacidad y no con color. Cada gráfico lleva
`aria-label` y una tabla oculta con las mismas cifras.

### Catálogo multiempresa (Fase 2B)

`Category` y `Product` pertenecen a una `Company`. Los slugs son únicos **por
empresa**: dos tenants pueden tener cada uno `iphone-15`.

El storefront público resuelve su tenant por **host** (`empresa.dominio.com`), o
por `DEFAULT_STOREFRONT_COMPANY_SLUG`, o —si solo hay una empresa activa— por esa
única empresa. Sin resolución sirve un catálogo **vacío**: es el fallo seguro.

> Con una sola empresa no hay que configurar nada. En cuanto crees la segunda,
> define `DEFAULT_STOREFRONT_COMPANY_SLUG` o sirve por subdominios.

`products.view` / `products.manage` ya gobiernan de verdad los endpoints admin de
catálogo. Un superusuario debe indicar `?company=`: con el catálogo tenantizado ya
no existe «todos los productos».

### Control Interno v1 (Fase 2A.2)

`/admin` es ahora una aplicación empresarial con sidebar y topbar, no el panel
admin del e-commerce. Muestra empresa, sucursal, roles, áreas y permisos efectivos.

Entrar requiere **Membership activa en Company activa** (o platform master), no el
rol legacy — por eso un vendedor o un técnico ven el dashboard. Abrir el dashboard
no es abrir cada módulo: cada endpoint sigue decidiendo en el backend.

> El dashboard **no muestra ventas, ingresos ni stock**. `Product`, `Order` y
> `StockMovement` aún no son tenant-aware, así que esas cifras serían globales
> mostradas como si fueran de la empresa. Llegan en 2B/2C.

El sidebar solo enlaza módulos que **existen** y a los que tienes acceso; el resto
aparece en el mapa del dashboard con su estado real.

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
