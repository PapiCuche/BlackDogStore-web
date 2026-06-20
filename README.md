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

### Pendientes explícitos (fases futuras)

- **Fase 2.3**: Verificación de correo electrónico tras registro
- **Fase 3**: Roles avanzados (staff/admin/cliente); auditoría de acciones administrativas
- **Fase 4**: Dirección de envío en checkout; emails transaccionales
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
