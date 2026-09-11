# Black Dog Store — Estado actual y auditoría técnica

**Fecha de auditoría:** 20 de junio de 2026  
**Estado del proyecto:** MVP en desarrollo; no apto todavía para producción ni pagos reales  
**Objetivo del documento:** proporcionar contexto verificable a desarrolladores y asistentes de IA sobre la arquitectura, funcionalidades, problemas, riesgos y prioridades actuales del repositorio.

---

## 1. Resumen ejecutivo

Black Dog Store es un ecommerce en construcción compuesto por:

- Un frontend público en Next.js, React y TypeScript.
- Una API backend en Django REST Framework.
- Django Admin como panel administrativo básico.
- SQLite para desarrollo local.
- PostgreSQL disponible mediante Docker Compose.
- Autenticación JWT.
- Carrito para visitantes.
- Integración inicial con la pasarela anterior (retirada en P0-F).
- Enlaces de contacto mediante WhatsApp.

La tienda tiene una identidad visual pública definida y ya cuenta con catálogo, detalle de producto, carrito, autenticación, pedidos, reseñas, cupones y checkout inicial.

Sin embargo, existen problemas críticos en:

- Migraciones de base de datos.
- Gestión de inventario.
- Seguridad del carrito.
- Ciclo de vida de las órdenes.
- Confirmación de pagos.
- Configuración de producción.
- Pruebas automatizadas.

No se deben habilitar pagos reales hasta resolver estos puntos.

---

## 2. Estructura del repositorio

```text
BlackDogStore-web/
├── backend/
│   ├── backend/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   ├── asgi.py
│   │   └── wsgi.py
│   ├── store/
│   │   ├── migrations/
│   │   ├── admin.py
│   │   ├── auth_serializers.py
│   │   ├── auth_views.py
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── urls.py
│   │   └── views.py
│   ├── db.sqlite3
│   ├── Dockerfile
│   ├── manage.py
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── auth/
│   │   ├── cart/
│   │   ├── checkout/
│   │   ├── components/
│   │   ├── lib/
│   │   ├── orders/
│   │   ├── product/
│   │   ├── services/
│   │   ├── globals.css
│   │   ├── layout.tsx
│   │   └── page.tsx
│   ├── public/assets/branding/
│   ├── Dockerfile
│   ├── eslint.config.mjs
│   ├── next.config.ts
│   ├── package.json
│   └── tsconfig.json
├── docs/
├── .env.example
├── docker-compose.yml
└── README.md
```

La separación principal entre frontend y backend es correcta para la etapa actual. El backend, no obstante, concentra todos los dominios en una sola aplicación llamada `store`.

---

## 3. Tecnologías verificadas

### Frontend

- Next.js `16.2.9`.
- React `19.2.4`.
- React DOM `19.2.4`.
- TypeScript 5.
- Tailwind CSS 4.
- App Router.
- `next/font` con Inter y Unbounded.

### Backend

- Django REST Framework.
- Simple JWT.
- django-environ.
- django-cors-headers.
- Izipay (REST, sin SDK de servidor: `urllib` de la stdlib).
- psycopg2-binary.

### Infraestructura

- Docker.
- Docker Compose.
- PostgreSQL 15.
- SQLite para desarrollo local.

### Advertencia de versiones

`backend/requirements.txt` no fija versiones exactas. En la máquina auditada se ejecutó Django `6.0.6`, aunque:

- El Dockerfile usa Python 3.11.
- Los comentarios de configuración hacen referencia a Django 4.2.
- La documentación de arquitectura propone Django 5.2 LTS.

Existe deriva entre entornos. Se deben fijar versiones compatibles y reproducibles.

---

## 4. Funcionalidades existentes

### Tienda pública

- Página de inicio.
- Página de servicios técnicos.
- Catálogo de productos.
- Filtro de productos por categoría en memoria.
- Búsqueda de productos por nombre en memoria.
- Detalle dinámico por slug.
- Productos relacionados.
- Metadatos SEO globales y por producto.
- Diseño responsive mediante Tailwind.

### Carrito

- Carrito para visitantes.
- Persistencia de una clave en `localStorage`.
- Agregar productos.
- Actualizar cantidades.
- Eliminar productos.
- Mostrar subtotal y total.
- Aplicar cupones.

### Autenticación

- Registro.
- Login mediante JWT.
- Refresh endpoint.
- Consulta del perfil autenticado.
- Logout en frontend.
- Vista de pedidos para usuarios autenticados.

### Pedidos y pagos

- Creación de órdenes.
- Creación de intentos de pago Izipay (token de sesión).
- Webhook con verificación de firma.
- Marcado básico de una orden como pagada.

### Contenido social y comercial

- Reseñas.
- Cupones.
- WhatsApp.
- Instagram.
- Facebook.
- Django Admin.

---

## 5. Rutas frontend existentes

| Ruta | Función |
|---|---|
| `/` | Inicio |
| `/services` | Servicios técnicos |
| `/product` | Catálogo |
| `/product/[slug]` | Detalle de producto |
| `/cart` | Carrito |
| `/checkout` | Inicio de pago |
| `/checkout/success` | Pantalla de supuesto pago exitoso |
| `/auth` | Login, registro y perfil |
| `/auth/logout` | Cierre de sesión |
| `/orders` | Pedidos del usuario |

---

## 6. Endpoints backend existentes

La API se publica bajo `/api/`.

| Endpoint | Métodos principales | Función |
|---|---|---|
| `/api/categories/` | GET | Listar categorías |
| `/api/products/` | GET | Listar y filtrar productos |
| `/api/reviews/` | GET, POST | Listar y crear reseñas |
| `/api/cart/` | GET, POST | Operaciones base del carrito |
| `/api/cart/{id}/` | PATCH, DELETE | Modificar o eliminar una línea |
| `/api/cart/add/` | POST | Agregar producto al carrito |
| `/api/orders/` | CRUD parcial | Pedidos |
| `/api/coupons/validate/` | POST | Validar cupón |
| `/api/auth/register/` | POST | Registrar usuario |
| `/api/auth/login/` | POST | Obtener JWT |
| `/api/auth/refresh/` | POST | Renovar JWT |
| `/api/auth/me/` | GET | Consultar perfil |
| `/api/payments/create-checkout-session/` | POST | Crear orden e intento de pago Izipay |
| `/api/payments/izipay/notification/` | POST | Recibir notificaciones Izipay firmadas |

---

## 7. Modelos de base de datos existentes

### Category

- `name`
- `slug`

### Product

- `name`
- `slug`
- `description`
- `price`
- `inventory`
- `image_url`
- `category`
- `created_at`

### Coupon

- `code`
- `discount_percent`
- `is_active`
- `expires_at`

### Order

- `user`
- `customer_name`
- `customer_email`
- `total`
- `discount_amount`
- `coupon_code`
- `created_at`
- `paid`

### OrderItem

- `order`
- `product`
- `quantity`
- `price`

### CartItem

- `session_key`
- `product`
- `quantity`
- `added_at`

### Review

- `product`
- `user`
- `author_name`
- `rating`
- `comment`
- `created_at`

### Datos locales encontrados

- 3 productos.
- 2 categorías.
- 0 órdenes.
- 0 elementos de carrito.
- 0 reseñas.
- 0 cupones.

---

## 8. Problemas críticos confirmados

### 8.1 La base de datos local no coincide con los modelos

Los campos `Order.customer_name` y `Order.customer_email` existen en:

```text
backend/store/models.py
```

Pero no existe una migración registrada para ellos.

El comando:

```bash
python3 manage.py makemigrations --check --dry-run
```

solicita crear:

```text
store/migrations/0004_order_customer_email_order_customer_name.py
```

Una consulta real sobre `Order` falla con:

```text
django.db.utils.OperationalError:
no such column: store_order.customer_name
```

#### Corrección

```bash
cd backend
python3 manage.py makemigrations store
python3 manage.py migrate
```

La migración generada debe guardarse en el repositorio.

---

### 8.2 El checkout elimina el carrito antes de confirmar el pago

Archivo:

```text
backend/store/serializers.py
```

`OrderSerializer.create()`:

1. Lee el carrito.
2. Crea una orden.
3. Crea sus líneas.
4. Elimina el carrito.

Después de eso, `CreateCheckoutSessionView` abre un intento de pago con Izipay.

Si la pasarela falla o el cliente cancela:

- El carrito ya no existe.
- Queda una orden pendiente.
- El frontend afirma incorrectamente que el carrito sigue disponible.

#### Solución requerida

- No eliminar el carrito durante la creación de la sesión.
- Mantener una orden en estado `pending_payment`.
- Confirmar el pago mediante webhook.
- Consumir o cerrar el carrito únicamente tras confirmación.
- Usar transacciones.
- Permitir reintentos idempotentes.

---

### 8.3 No hay validación real ni descuento de inventario

El backend no verifica:

- Que la cantidad sea mayor que cero.
- Que la cantidad no supere el stock.
- Que el producto siga disponible al pagar.
- Que dos clientes no compren simultáneamente la misma unidad.

El inventario tampoco se descuenta cuando el pago se confirma.

#### Solución requerida

- Validadores de cantidad.
- Restricciones de base de datos.
- `transaction.atomic()`.
- `select_for_update()`.
- Reserva temporal de stock.
- Liberación de reservas vencidas.
- Descuento final al confirmar el pago.
- Historial de movimientos de inventario.

---

### 8.4 La página de éxito no confirma el pago

Archivo:

```text
frontend/app/checkout/success/page.tsx
```

Cualquier persona puede abrir `/checkout/success` directamente y verá:

```text
¡Pago exitoso!
```

No se consulta el estado de la orden ni el intento de pago.

Además, la página promete un correo de confirmación, pero no existe un servicio de correo implementado.

#### Solución requerida

- Crear un endpoint seguro de consulta de estado.
- Leer un identificador no predecible de orden o sesión.
- Verificar que el webhook confirmó `payment_status=paid`.
- Mostrar estado pendiente mientras se procesa el webhook.
- No prometer correos hasta implementar el servicio.

---

### 8.5 El carrito depende de una clave controlada por el cliente

El frontend genera una clave con fecha y `Math.random()` y la guarda en `localStorage`.

El backend acepta cualquier `session_key` enviada por el cliente.

Esto permite leer o modificar un carrito si se conoce o adivina su clave.

#### Solución requerida

- Generar el identificador en el servidor.
- Usar UUID criptográfico.
- Guardarlo en cookie `HttpOnly`, `Secure` y `SameSite`.
- Asociar carritos autenticados al usuario.
- Verificar pertenencia en cada operación.

---

## 9. Seguridad

### Configuración insegura actual

Archivo:

```text
backend/backend/settings.py
```

Problemas:

- `SECRET_KEY` usa `changeme` por defecto.
- `DEBUG` es verdadero por defecto.
- `ALLOWED_HOSTS` permite `*`.
- `CORS_ALLOW_ALL_ORIGINS = True`.
- El permiso global de DRF es `AllowAny`.
- No se configura HTTPS obligatorio.
- No se configura HSTS.
- Las cookies de sesión y CSRF no se marcan como seguras.

`python3 manage.py check --deploy` reportó cinco advertencias de seguridad.

### Autenticación

Los tokens JWT se almacenan en `localStorage`.

Riesgos:

- Un XSS podría extraer access y refresh token.
- El refresh token se guarda pero el frontend no lo utiliza.
- No hay rotación ni revocación.
- No hay blacklist.
- No hay gestión clara de expiración.

### Registro

Faltan:

- `validate_password`.
- Verificación de correo.
- Normalización y unicidad comercial del correo.
- Rate limiting.
- Protección contra registro automatizado.

### Otros riesgos

- Reseñas anónimas sin moderación ni rate limiting.
- Cupones sin límite de uso.
- Errores de entrada que pueden provocar respuestas 500.
- `next/image` permite imágenes desde cualquier dominio HTTPS.
- No hay políticas de Content Security Policy.
- No hay auditoría de acciones administrativas.

---

## 10. Roles y permisos

Actualmente se usan las capacidades estándar de Django:

- Usuario normal.
- Usuario `is_staff`.
- Superusuario.

No existe un modelo explícito de roles de negocio.

Roles recomendados:

- Cliente.
- Operador de ventas.
- Operador de inventario.
- Técnico.
- Administrador.
- Superadministrador.

Los permisos deben definirse por módulo y acción, no solamente mediante `is_staff`.

---

## 11. Evaluación del frontend y UX/UI

### Aspectos positivos

- Identidad pública consistente en negro y blanco.
- Tipografía con personalidad.
- Buena jerarquía en inicio, servicios, catálogo y carrito.
- Componentes reutilizables.
- Estados vacíos y esqueletos de carga.
- Diseño responsive basado en breakpoints de Tailwind.
- Metadatos SEO iniciales.
- Navegación móvil.

### Inconsistencias

Las páginas públicas principales usan una estética de marca monocromática. Sin embargo:

- Login.
- Perfil.
- Pedidos.
- Checkout.
- Detalle de producto.
- Pantalla de pago exitoso.

usan una estética verde/esmeralda más genérica. La aplicación parece tener dos sistemas visuales diferentes.

### Problemas UX

- El checkout solo solicita nombre y correo.
- No se solicita dirección.
- No se solicita teléfono.
- No existe elección de delivery o recojo.
- No se calcula envío.
- No se solicita documento ni tipo de comprobante.
- No hay aceptación de políticas.
- No hay resumen completo antes de pagar.
- El contador del carrito cuenta líneas, no unidades.
- El filtro del catálogo no se sincroniza correctamente con la URL.
- El footer usa `?cat=`, mientras el detalle usa `?category=`.
- El catálogo no inicializa su filtro desde ninguno de esos parámetros.
- Algunos errores se silencian con `catch {}`.
- Faltan estados de error en reseñas y pedidos.

### Accesibilidad

Mejoras pendientes:

- Asociar `label` e `input` con `htmlFor` e `id`.
- Añadir foco visible consistente.
- Respetar `prefers-reduced-motion`.
- Detener o desactivar el marquee para usuarios sensibles al movimiento.
- Mejorar textos alternativos.
- Revisar contraste de textos `zinc-600` y `zinc-700`.
- Añadir `aria-expanded` al menú móvil.
- Añadir anuncios accesibles para estados de carga, error y éxito.

### Limitación de esta auditoría

No se pudo realizar una inspección visual por capturas porque el navegador integrado no estuvo disponible durante la auditoría. La evaluación UX/UI se realizó a partir de componentes, estilos y breakpoints definidos en el código.

---

## 12. Rendimiento

### Estado actual

- El build de producción termina correctamente.
- TypeScript no reporta errores.
- Las páginas estáticas se generan correctamente.
- El detalle de producto usa renderizado dinámico.

### Problemas

- Inicio y catálogo cargan los productos en el cliente.
- Esto retrasa el contenido y reduce el valor SEO del catálogo.
- Hay múltiples etiquetas `<img>` sin optimización de Next.js.
- La página dinámica consulta dos veces el mismo producto: metadatos y render.
- Usa `cache: "no-store"` incluso para catálogo relativamente estable.
- No existe paginación.
- Las valoraciones se calculan en Python por cada producto.
- El frontend incluye varios formatos duplicados de recursos gráficos.
- Next.js detecta un `package-lock.json` externo y selecciona una raíz incorrecta para Turbopack.

### Mejoras recomendadas

- Renderizar catálogo y destacados en servidor.
- Usar revalidación controlada.
- Utilizar `next/image`.
- Aplicar `cache()` o una capa de datos compartida.
- Añadir paginación backend.
- Calcular rating con agregaciones SQL.
- Optimizar y depurar recursos de marca.
- Configurar explícitamente `turbopack.root`.

---

## 13. SEO

### Implementado

- Títulos globales.
- Descripción global.
- Keywords.
- Open Graph básico.
- Metadatos por producto.
- Idioma `es`.
- Favicon.

### Pendiente

- `sitemap.xml`.
- `robots.txt`.
- URL canónica.
- `metadataBase`.
- Open Graph image global.
- Twitter cards.
- JSON-LD de producto.
- JSON-LD de negocio local.
- Breadcrumb structured data.
- Páginas de categoría indexables.
- Manejo correcto de productos inexistentes mediante `notFound()`.

---

## 14. Integraciones

### Implementadas

- Checkout Izipay (Web SDK oficial).
- Notificación (IPN) Izipay firmada.
- WhatsApp.
- Facebook.
- Instagram.

### No implementadas

- Servicio de correo.
- Carga de archivos.
- Almacenamiento S3.
- Facturación electrónica.
- Generación de documentos.
- Redis.
- Celery.
- Operadores de entrega.
- Sentry.
- Analítica.
- Webhooks internos.
- Reembolsos.
- Conciliación de pagos.

---

## 15. Funcionalidades de ecommerce aún ausentes

- Variantes por capacidad y color.
- SKU.
- Galería de imágenes.
- Precios promocionales con vigencia.
- Historial de precios.
- Inventario reservado.
- Movimientos de inventario.
- IMEI.
- Número de serie.
- Condición nuevo/seminuevo.
- Estado de batería.
- Estado estético.
- Ubicación física.
- Costos internos y margen.
- Direcciones.
- Entregas.
- Recojo en tienda.
- Comprobantes.
- Devoluciones.
- Reembolsos.
- Garantías.
- Servicio técnico como flujo transaccional.
- Auditoría.
- Notificaciones.

---

## 16. Errores de calidad y herramientas

### Build

Resultado:

```text
Correcto
```

El build necesitó acceso a Google Fonts para descargar Inter y Unbounded.

### TypeScript

Comando:

```bash
npx tsc --noEmit
```

Resultado:

```text
Correcto
```

### Pruebas backend

Resultado:

```text
0 tests
```

No existe cobertura automatizada.

### Lint

El script actual:

```json
"lint": "next lint"
```

no funciona con Next.js 16.

Debe cambiarse a:

```json
"lint": "eslint ."
```

ESLint directo encontró:

- 7 errores.
- 15 advertencias.

Entre ellos:

- Actualizaciones de estado problemáticas dentro de efectos.
- Enlaces internos con `<a>` en lugar de `Link`.
- Dependencias faltantes en hooks.
- Uso de `<img>` sin optimización.
- Imports CommonJS en el script de imágenes.
- Un import no utilizado.

### Dependencias frontend

`npm audit --omit=dev` informó dos vulnerabilidades moderadas relacionadas con PostCSS incluido por Next.js.

No ejecutar automáticamente:

```bash
npm audit fix --force
```

porque la solución propuesta instala una versión incompatible y antigua de Next.js.

---

## 17. Archivos prioritarios

| Archivo | Motivo |
|---|---|
| `backend/store/models.py` | Modelo de datos incompleto y migración pendiente |
| `backend/store/serializers.py` | Creación insegura de órdenes y eliminación prematura del carrito |
| `backend/store/views.py` | Carrito, pagos, webhook, stock y permisos |
| `backend/backend/settings.py` | Configuración insegura para producción |
| `backend/store/auth_serializers.py` | Validación insuficiente de registro |
| `frontend/app/lib/auth.ts` | Tokens en localStorage |
| `frontend/app/lib/cart.ts` | Clave de carrito insegura |
| `frontend/app/checkout/page.tsx` | Checkout incompleto |
| `frontend/app/checkout/success/page.tsx` | Éxito no verificado |
| `frontend/app/components/ProductDetail.tsx` | Cantidades, reseñas y estados de error |
| `frontend/app/cart/page.tsx` | Manejo de errores, cantidades y cupones |
| `frontend/package.json` | Script de lint inválido |
| `frontend/next.config.ts` | Dominios de imágenes demasiado abiertos |
| `docker-compose.yml` | Configuración orientada solo a desarrollo |

---

## 18. Plan de trabajo recomendado

### Fase 0 — Recuperar estabilidad

1. Crear la migración faltante.
2. Aplicar todas las migraciones.
3. Fijar versiones de Python y dependencias.
4. Corregir el script de lint.
5. Resolver los errores de ESLint.
6. Añadir pruebas mínimas.
7. Separar configuración de desarrollo y producción.

### Fase 1 — Asegurar carrito, checkout y pagos

1. Rediseñar el identificador del carrito.
2. Asociar carritos a usuario o cookie segura.
3. Validar cantidades en backend.
4. Validar stock.
5. Introducir reservas temporales.
6. Crear órdenes mediante transacciones.
7. No vaciar el carrito antes del pago.
8. Añadir estados detallados de orden.
9. Registrar pagos y eventos.
10. Hacer idempotentes checkout y webhooks.
11. Verificar el pago antes de mostrar éxito.

### Fase 2 — Seguridad y autenticación

1. Cerrar CORS.
2. Configurar `ALLOWED_HOSTS`.
3. Eliminar secretos por defecto.
4. Habilitar configuración HTTPS.
5. Añadir rate limiting.
6. Aplicar validadores de contraseña.
7. Verificar correo.
8. Migrar tokens a cookies HttpOnly o usar un Backend For Frontend.
9. Añadir rotación y revocación.
10. Implementar roles y permisos.

### Fase 3 — Modelo ecommerce profesional

Separar o modularizar:

- Cuentas.
- Catálogo.
- Inventario.
- Carritos.
- Checkout.
- Pedidos.
- Pagos.
- Promociones.
- Entregas.
- Garantías.
- Servicio técnico.
- Auditoría.
- Notificaciones.

### Fase 4 — UX/UI

1. Unificar el sistema visual.
2. Completar checkout.
3. Mejorar accesibilidad.
4. Sincronizar filtros con URL.
5. Mejorar errores y recuperación.
6. Añadir páginas legales.
7. Revisar promesas comerciales no respaldadas por funciones reales.

### Fase 5 — Producción y rendimiento

1. SSR o revalidación para catálogo.
2. Optimización de imágenes.
3. Sitemap, robots y datos estructurados.
4. Servidor de producción para Django.
5. Docker multistage.
6. CI/CD.
7. Sentry y logs estructurados.
8. Backups.
9. Monitoreo.
10. Pruebas E2E del proceso de compra.

---

## 19. Orden estricto de prioridad

1. Corregir migraciones.
2. Corregir checkout y conservación del carrito.
3. Implementar validación y reserva de inventario.
4. Confirmar pagos correctamente.
5. Cerrar vulnerabilidades de configuración.
6. Añadir pruebas.
7. Implementar roles.
8. Completar el checkout comercial.
9. Mejorar rendimiento y SEO.
10. Implementar módulos avanzados.

---

## 20. Reglas para futuras modificaciones

Toda IA o desarrollador que trabaje en este proyecto debe:

1. Revisar este documento antes de modificar el código.
2. No asumir que una funcionalidad documentada en los planes ya está implementada.
3. Verificar modelos y migraciones antes de cambiar base de datos.
4. No confiar en precios, cantidades, cupones o permisos enviados por el frontend.
5. Mantener los cálculos económicos en el backend.
6. Usar `Decimal`, nunca números flotantes, para valores monetarios en backend.
7. Usar transacciones para inventario, pedidos y pagos.
8. Hacer idempotentes los flujos de pago.
9. Añadir pruebas para cada corrección crítica.
10. No introducir secretos en el repositorio.
11. No habilitar pagos reales hasta completar las fases 0, 1 y 2.
12. Actualizar este documento cuando cambie la arquitectura o se resuelva un hallazgo.

---

## 21. Criterio mínimo para considerar el MVP apto para producción

El MVP solo podrá considerarse desplegable cuando:

- No existan migraciones pendientes.
- El build, lint, TypeScript y pruebas sean correctos.
- El carrito no pueda ser manipulado por terceros.
- El stock se valide y reserve de forma transaccional.
- El carrito no se pierda al cancelar un pago.
- El webhook sea idempotente.
- La página de éxito confirme el pago.
- La configuración de producción sea segura.
- Los secretos se administren externamente.
- Exista monitoreo y registro de errores.
- Existan backups.
- El checkout recoja la información comercial necesaria.
- Se hayan probado los flujos de éxito, cancelación, error y reintento.

---

## Actualización — Fase SaaS 1 (fundación multiempresa)

> Este documento describe el estado del proyecto en su auditoría original. Las
> fases posteriores se registran en [CHANGELOG.md](../CHANGELOG.md).

Cambios estructurales posteriores a esta auditoría:

| Fase | Añade | Estado |
|---|---|---|
| 6.0 | `StockMovement` (Kardex), `SalesNote`, servicio de inventario transaccional | IMPLEMENTADO |
| SaaS 1 | `Company`, `Branch`, `Membership`, resolución de tenant | PARCIAL |
| SaaS 2A | Matriz de capacidades empresariales, `CompanyContext`, separación PLATFORM/COMPANY/LEGACY | IMPLEMENTADO |
| SaaS 2A.1 | `CompanyArea`, `CompanyRole`, `MembershipRoleAssignment`, catálogo de capacidades | IMPLEMENTADO |
| SaaS 2A.1 cierre | Provisioning de empresas, corrección de superficies, mapa del Control Interno | IMPLEMENTADO |
| Demo users | `seed_demo_users` — cuentas de prueba de roles | IMPLEMENTADO / **TEMPORAL, eliminar antes de producción** |
| SaaS 2A.2 | Control Interno v1: shell sidebar+topbar, dashboard empresarial, endpoint agregado | IMPLEMENTADO |
| SaaS 2B | Catálogo tenant-aware: `Category.company`, `Product.company`, storefront por host, aislamiento público | IMPLEMENTADO |
| SaaS 2B.1 | Dashboard visual: gráficos tenant-safe, series de catálogo y organización, SVG propio | IMPLEMENTADO |
| SaaS 2C | Comercio tenant-aware: `Order.company`, `Coupon.company`, carrito lógico, checkout, webhook, KPIs de ventas | IMPLEMENTADO |
| SaaS 2D | Inventario multisucursal: `BranchStock`, `MembershipBranchAccess`, `StockMovement.company/branch`, transferencias, recuentos, reposición, sucursal de despacho | IMPLEMENTADO |
| SaaS 3 | Configuración y branding por empresa: `CompanySettings`, `Order.company_snapshot`, emails y PDFs tenant-aware, storefront config pública, pantallas de Configuración y Sucursales | IMPLEMENTADO |

**Modelos de base de datos actuales**, además de los listados en la sección 7:
`UserProfile`, `AdminAuditLog`, `AccountToken`, `StockMovement`, `SalesNote`,
`Company`, `Branch`, `Membership`, `CompanyArea`, `CompanyRole`,
`MembershipRoleAssignment`, `MembershipBranchAccess`, `BranchStock`,
`StockTransfer`, `StockTransferItem`, `InventoryCount`, `InventoryCountItem`,
`CompanySettings`.

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
Pasarela tenant-safe                     IMPLEMENTADO
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
Emails y PDFs por empresa                IMPLEMENTADO
Notificación interna por empresa         IMPLEMENTADO
Snapshot histórico de identidad          IMPLEMENTADO
Pantalla de Configuración                IMPLEMENTADO
Pantalla de Sucursales                   IMPLEMENTADO
Timezone por empresa                     PARCIAL
Currency por empresa                     PARCIAL
Runtime Admin / Control Interno          FUNCIONAL
Dashboard company admin                  FUNCIONAL
Dashboard MASTER                         FUNCIONAL
Catálogo storefront                      FUNCIONAL
Carrito storefront                       FUNCIONAL
Esquema local alineado con el código     SÍ (0033)
Migraciones pendientes                   0
500 en peticiones válidas                0
Series / correlativos internos           IMPLEMENTADO
Promociones automáticas / combos         IMPLEMENTADO
Snapshot de promoción aplicada           IMPLEMENTADO
Atajo de combos en POS                   IMPLEMENTADO
Administración de cupones                IMPLEMENTADO
Analítica de promociones                 IMPLEMENTADO
Elegibilidad de vendedor                 IMPLEMENTADO
Huella de idempotencia canónica          IMPLEMENTADO
Reparación de histórico de descuentos    IMPLEMENTADO
Carga masiva de productos (Excel)        PENDIENTE
Carga masiva de inventario (Excel)       PENDIENTE
Apilado promoción + cupón                PENDIENTE
Promociones en e-commerce                PENDIENTE
POS interno                              IMPLEMENTADO
Cliente en POS                           IMPLEMENTADO
Vendedor / operador separados            IMPLEMENTADO
Reasignación de vendedor                 IMPLEMENTADO
Comisión por Membership                  IMPLEMENTADO
Comisión congelada por venta             IMPLEMENTADO
Analítica de comisiones                  IMPLEMENTADO
Cupón en POS                             IMPLEMENTADO
Descuento manual autorizado              IMPLEMENTADO
Preview de totales (servidor)            IMPLEMENTADO
Efectivo y vuelto                        IMPLEMENTADO
Referencia de pago / observaciones       IMPLEMENTADO
Pagos mixtos                             PENDIENTE C2
Liquidación de comisiones                PENDIENTE C2
Comisión dividida                        PENDIENTE
Grupos de clientes                       PENDIENTE
Venta con código de barras               IMPLEMENTADO
ProductBarcode                           IMPLEMENTADO
POS aislamiento empresa/sucursal         IMPLEMENTADO
POS idempotencia                         IMPLEMENTADO
POS descuento transaccional de stock     IMPLEMENTADO
Canal de venta (online/pos)              IMPLEMENTADO
Medio de pago                            IMPLEMENTADO
Vendedor (sold_by)                       IMPLEMENTADO
Dashboard comercial                      IMPLEMENTADO
Productos más vendidos                   IMPLEMENTADO
Analítica por canal                      IMPLEMENTADO
Pronóstico de demanda v1                 IMPLEMENTADO
Confianza del pronóstico                 IMPLEMENTADO
Días de cobertura                        IMPLEMENTADO
Fecha estimada de quiebre                IMPLEMENTADO
Lead time / safety stock                 IMPLEMENTADO
Punto de reposición                      IMPLEMENTADO
Reposición sugerida                      IMPLEMENTADO
Sugerencia de transferencia interna      IMPLEMENTADO
Caja / arqueo                            PENDIENTE C2
Devoluciones / anulaciones               PENDIENTE C2
Compras / proveedores                    PENDIENTE
Costo y rentabilidad                     PENDIENTE
Pronóstico estacional / ML               PENDIENTE
Clientes tenant-aware (CRM)              IMPLEMENTADO
Customer ↔ User (vínculo opcional)       IMPLEMENTADO
Historial comercial del cliente          IMPLEMENTADO
Backfill de pedidos históricos           PARCIAL (los ambiguos quedan sin vincular)
Detección de duplicados                  IMPLEMENTADO
Merge de clientes                        PENDIENTE
Direcciones múltiples por cliente        PENDIENTE
Devices / equipos                        PENDIENTE Fase 5
Órdenes de servicio                      PENDIENTE Fase 6
Portal de seguimiento del cliente        PENDIENTE
Numeración fiscal SUNAT                  FUERA DE ALCANCE
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

**Nota sobre multiempresa:** el e-commerce **está tenantizado** hasta la Fase
2D. Catálogo (2B), pedidos/carrito/checkout (2C) e inventario multisucursal (2D)
resuelven su empresa sin tomarla nunca del cliente, y el inventario resuelve
además la sucursal. La Fase 3 sacó del runtime la identidad del tenant piloto: emails, PDFs y
storefront leen `CompanySettings`. La Fase 2E cerró la última pieza estructural:
el correlativo de las notas de venta dejó de ser global y pasa por
`InternalSequence`, una serie por empresa —o por sucursal— con su propio
contador. La Fase 4 añadió el primer dominio de Servicio Técnico: `Customer`,
el cliente comercial de UNA empresa, con vínculo opcional a un `User` global e
historial de pedidos. La empresa piloto (Black Dog Store) se crea por migración
de datos, no por una constante en el código.

Detalle: [saas-multiempresa.md](saas-multiempresa.md) · [inventario-y-notas-de-venta.md](inventario-y-notas-de-venta.md)

---

## Actualización — API v1 para clientes nativos

### Catálogo público — IMPLEMENTADO

`/api/v1/storefront/<company_slug>/{products,categories}/`. El tenant va en la
ruta porque una app móvil llega a un host de API compartido y no tiene el Host
por el que se identifica el storefront web. Ese slug **selecciona** un escaparate
público y no autoriza nada.

### Autenticación nativa — IMPLEMENTADO (BR-001A)

`/api/v1/auth/{login,refresh,logout,me}/`. Tokens en el cuerpo, `Bearer` en vez
de cookie, sin CSRF.

`V1BearerAuthentication` **no es global** y no debe añadirse a
`DEFAULT_AUTHENTICATION_CLASSES`: eso abriría toda la superficie legacy a un
token del contrato móvil. Cada vista privada v1 la declara explícitamente.

### Deuda registrada en esta fase

- **`email` no es unique** en `auth_user`. El registro lo valida en el serializer,
  con una race, y no cubre filas creadas por `createsuperuser` o desde el admin.
  El login v1 lo resuelve rechazando cuando hay más de una coincidencia. Añadir
  la constraint es una decisión aparte: la migración fallaría durante el deploy
  en cualquier instalación que ya tenga duplicados, así que necesita antes una
  auditoría de datos y un plan de deduplicación.
- **No existe columna de verificación de correo.** Verificar y activar son el
  mismo hecho (`User.is_active`). Separarlos, si se quiere, es BR-001B.
- **Ciclo de vida de cuenta nativo pendiente** (BR-001B): registro, verificación,
  reenvío, reset y cambio de contraseña siguen siendo solo web.

---

## Actualización — superficie de cliente (M4)

`/api/v1/customer/<slug>/orders/` es la tercera audiencia del contrato v1, en su
propio espacio de URL. La propiedad de un pedido son dos FKs (`Order.user` o
`Order.customer.user`); el email no es propiedad.

**Ser empleado no es ser cliente**: una membresía no abre esta superficie. El
acceso interno a los pedidos de la empresa será `sales.orders.view` sobre
`/api/v1/internal/`, que todavía no existe.

**BR-003 cerrado para v1**: `fulfillment_status` se expone en el serializer de
cliente. El legacy no se tocó — pertenece al frontend web y además lista
un identificador de pasarela.

### Deuda registrada

- **`OrderSerializer` legacy exponía un identificador de pasarela.** Resuelto en P0-F; fuera del alcance de
  esta fase (es el contrato del frontend web), pero merece una revisión propia:
  un identificador de pasarela de pago no aporta nada a un cliente.
- **`delivery_method` es `blank=True` sin default.** Los pedidos anteriores al
  campo no lo tienen; el serializer devuelve etiqueta vacía, que es la respuesta
  honesta.
- **Superficie interna v1 pendiente.** `sales.orders.view` e `inventory.view`
  existen en el catálogo de capabilities y todavía no tienen endpoints v1.

---

## Actualización — checkout nativo (M5)

`checkout_services.py` concentra el dominio comercial del checkout. Las dos
superficies —navegador y app— conservan su autenticación, su resolución de
tenant y su forma de entrada, y comparten precios, stock, cupón, sucursal de
despacho y creación del `Order`.

**Migración 0034**: `Order.idempotency_key`, `Order.idempotency_fingerprint` y
una `UniqueConstraint(company, user, idempotency_key)` **parcial**
(`idempotency_key__isnull=False`). Parcial a propósito: todo pedido de navegador
tiene la clave nula, y una constraint no parcial permitiría exactamente un
pedido de invitado por empresa.

`build_storefront_config_payload()` extraído de `StorefrontConfigView` para que
la variante por slug devuelva exactamente lo mismo.

### Deuda registrada

- **Sin reserva de stock.** Dos compradores pueden validar la última unidad y
  ambos llegar a la pasarela; el stock definitivo se resuelve en la notificación, como ya
  ocurría. No se cambió la semántica de inventario en esta fase.
- **El identificador de pasarela es único**, lo que es correcto, y conviene
  recordarlo al escribir tests con mocks: dos pedidos no pueden compartir id.
- **Sesión de pago no persistida.** En un replay se abre un intento nuevo; si la
  sesión caducó, `checkout_url` es null y el cliente lee el estado del pedido.
- **Superficie interna v1 pendiente**, con `sales.orders.view` ya en el catálogo.


---

## Fase Comercial C1.4 — grafo de migraciones y carga masiva

**Grafo reconciliado.** Dos ramas salieron de la 0033 (`0034_checkout_idempotency`
en master y `0034_commercial_pos_barcode → 0040` en la rama comercial). Se creó
`0041`, una migración de merge real y vacía que depende de ambas hojas. **No se
renumeró nada**: esas siete migraciones están aplicadas en una base de datos real,
y renumerarlas le mostraría a Django siete migraciones que nunca ha ejecutado
contra tablas que ya existen.

**Defecto silencioso del auto-merge.** Git dejó **dos asignaciones
`constraints = [...]`** en `Order.Meta`; la segunda tapaba a la primera. Python
válido, semántica equivocada: desaparecía la unicidad de `pos_idempotency_key`.
Las dos idempotencias se conservan y **no se unifican** — la del POS es única por
empresa, la del checkout por empresa **y usuario**.

**Defecto en producción encontrado por las pruebas nuevas.** `PATCH` sobre una
promoción no era parcial: una clave ausente significaba «ponlo en None», así que
`PATCH {is_active: false}` —lo que manda el botón de archivar— borraba el precio
del combo y la petición se rechazaba con 400. El botón nunca funcionó. El único
test de C1.3 que mandaba `is_active` apuntaba a la promoción de otra empresa y
esperaba 404: pasaba por el control de tenant y nunca llegaba a ese código.

**Fechas.** `promotion.starts_at = request.data[...]` no validaba nada: un
`DateTimeField` acepta cualquier objeto en Python y sólo se convierte al llegar a
la base de datos, ya dentro de la transacción. Entrada inválida daba 500 y una
fecha sin zona horaria se guardaba naive. Nuevo `api_parsing.py`.

**Carga masiva.** Ver `docs/saas-multiempresa.md` §8-unvicies. Lo esencial para
una auditoría: la previsualización no escribe nada de negocio, aplicar lee del
staging y no del navegador, el archivo original no se guarda, el stock se escribe
únicamente por `inventory_services`, y **una celda vacía nunca es un cero**.

---

---

## Actualización — superficie interna (M6)

`/api/v1/internal/<slug>/` es la cuarta audiencia del contrato v1. Dos puertas:
pertenencia (404 indistinguible) y permiso (403).

`order_fulfillment_services.py` extraído de `AdminOrderFulfillmentView`; ambas
superficies lo llaman, con comportamiento idéntico. La restricción del rol de
inventario se preservó **exacta**, aunque siga clavada al `UserProfile.role`
legacy en lugar de a una capability.

`sales.orders.view` y `sales.orders.manage` promovidas a **ACTIVE**: v1 las
impone sin ruta de rol legacy, que es la definición de ACTIVE en el catálogo.

### Deuda registrada

- **La restricción de inventario usa `UserProfile.role`, no una capability.** Es
  la regla vigente y se conservó tal cual; convertirla en capability es una
  decisión de negocio, no un refactor.
- **Sin superficie interna de inventario v1**, aunque `inventory.*` lleve
  ACTIVE desde la Fase 2D. Mobile no debe llamar `/api/admin/inventory/`.
- **`sales.notes.manage` sigue AVAILABLE**: el módulo no se implementó.
- **Servicio técnico sigue RESERVED**: `RepairOrder` no existe.


---

## Fase Comercial C1.5 — cierre de la auditoría de C1.4

Seis defectos, todos en los bordes del importador:

1. **Truncamiento silencioso.** 5001 filas → 5000 preparadas, 0 errores, trabajo
   aplicable. Ahora `FULL_IMPORT` rechaza el archivo entero con 400 y sin crear
   trabajo; `SAMPLE` (la inspección) sigue leyendo 25 filas y eso significa
   «muestra», no «el archivo tiene 25».
2. **Identidad en mayúsculas.** El importador fusionaba `AbC123` y `abc123`, que
   el POS distingue. Eliminado todo `upper()` de la identidad.
3. **Códigos inactivos invisibles.** `UNIQUE(company, code)` no filtra por
   `is_active`, así que un código retirado sigue ocupando su cadena. El índice
   ahora carga propiedad completa y distingue propiedad de escaneo.
4. **Deriva entre preview y apply.** Aplicar exige el mismo producto que se
   aprobó; si no, aborta todo pidiendo re-previsualizar.
5. **INITIAL sin revalidar.** Ahora: locks → revalidación del conjunto →
   escritura. Nunca se degrada a corrección.
6. **Fuga de permisos en el historial.** `products.manage` veía trabajos de
   inventario. Y el detalle era un oráculo de enumeración (403 vs 404).

Además: enteros exactos para stock, sucursal inactiva rechazada, aviso de celda
numérica basado en el tipo real de la celda, filas obsoletas que abortan en vez de
omitirse, y el fallo al guardar un perfil de mapeo registrado en el log.

---

---

## Actualización — inventario interno (M7A)

`/api/v1/internal/<slug>/inventory/` cierra la deuda que M6 dejó anotada: la app
ya no tiene ningún motivo para mirar hacia `/api/admin/inventory/`.

Cuatro endpoints, ninguna migración, ningún modelo nuevo:

| Método | Ruta | Capability |
|---|---|---|
| GET | `inventory/summary/` | `inventory.view` |
| GET | `inventory/stock/` | `inventory.view` |
| GET | `inventory/movements/` | `inventory.view` |
| POST | `inventory/adjustments/` | `inventory.adjust` |

### Tres puertas, no dos

Las dos de M6 siguen igual: pertenencia (404 indistinguible) y capability (403).
El inventario añade una tercera, la **sucursal**, y responde **404** — no 403 —
para un `branch_id` que el miembro no puede operar. Es deliberado: distinguir
«esa sucursal no existe» de «existe pero no es tuya» le regalaría a cualquier
empleado el mapa de sucursales de su empresa. Sin `branch_id`, la lectura se
agrega sobre el conjunto visible, que puede ser vacío: un miembro con
`branch_access_mode=SELECTED` y cero sucursales asignadas lee 200 con cero filas.

### El ajuste no escribe stock

La vista resuelve autoridad y delega. `inventory_services.apply_manual_stock_movement()`
es dueño del lock, del `StockMovement`, del `BranchStock` y de la auditoría —
exactamente el mismo servicio que usa el admin web, así que las dos superficies
no pueden divergir. Un test estructural parsea el AST de la vista y falla si
aparece `BranchStock` o `.save(` en su código ejecutable.

El contrato **no tiene** campo de stock final. Un `quantity_after` enviado por el
cliente es una afirmación sobre un número que otra persona puede estar cambiando
en ese instante.

### Deuda registrada

- **`visible_branches()` conserva un puente legacy**: un usuario sin Membership
  cae a `legacy_catalog_company()` y obtiene todas las sucursales. Es inalcanzable
  desde v1 porque `get_internal_company()` exige Membership activa **antes**, pero
  el puente sigue vivo para el admin web y desaparece cuando todo operador tenga
  Membership.
- **Transferencias y recuentos siguen sin superficie v1**, a propósito.
- **`inventory_value` se calcula a precio de venta**, no a costo; la respuesta lo
  declara en `inventory_value_basis` en lugar de dejarlo implícito.
- **Sin reserva de stock**: no cambió nada de esa semántica en esta fase.



---

## Fase 0.3 / P0-A — Dependencias y cadena de suministro

Primera subfase del hardening de seguridad, sobre `master` (que ya integra C1 y
M7). Verificado hoy contra OSV/GHSA, PyPI y npm.

### Clasificación de los hallazgos

| Hallazgo | Clasificación | Acción |
|---|---|---|
| next 16.2.9 — 9 advisories | CONFIRMADO | → 16.3.4 |
| `hostname: "**"` en next.config | CONFIRMADO | allowlist por env, fail-closed |
| sharp 0.32.6 — CVE de libvips | CONFIRMADO | → 0.35.4 (libvips 8.18.6) |
| postcss ≤8.5.22 | CONFIRMADO | vía next 16.3.4 + audit fix |
| Django 5.2.15 — 4 CVE | CONFIRMADO (2 sin superficie) | → 5.2.17 |
| Pillow 12.2.0 — 13 CVE | CONFIRMADO, **sin superficie alcanzable** | → 12.3.0 igualmente |
| sqlparse 0.5.5 — 4 CVE | CONFIRMADO (transitiva) | fijado a 0.6.0 |
| `openpyxl` ausente de requirements | CONFIRMADO — rompe despliegue limpio | añadido |
| DRF, SimpleJWT, gunicorn, reportlab, psycopg2, cors-headers, django-environ | sin advisories | ninguna |

### Por qué la «mínima segura» no fue la mínima obvia

16.2.11 es la primera versión que cierra los nueve advisories de Next, pero
16.2.12 seguía dejando `postcss@8.4.31` y `sharp@0.34.5` **anidados dentro de
`node_modules/next/`**, fuera del alcance de una subida en la raíz. La mínima que
realmente cierra el conjunto es 16.3.4.

### Análisis de superficie, no sólo de versión

Dos de los cuatro CVE de Django y los trece de Pillow no tienen camino de
explotación en este código: no hay middleware de caché, no hay GIS, no hay
`ImageField`/`FileField` y nada importa PIL. Se actualizó igual, pero la
prioridad real del lote estaba en Next y en el comodín de imágenes.

### Deuda que deja P0-A

- El lint pasa de 18 a **19 warnings**: Next 16.3 añade la regla
  `no-location-assign-relative-destination`, que marca un
  `window.location.href = "/"` **preexistente** en `Header.tsx:60`. No es una
  regresión de esta fase y no se toca aquí para no mezclar un refactor de
  navegación con correcciones de seguridad.
- `NEXT_PUBLIC_IMAGE_HOSTS` debe configurarse en producción antes de que ninguna
  imagen remota vuelva a cargar. Sin ella no hay hosts remotos permitidos.


---

## Fase 0.3 / P0-B — Trusted proxy, IP del cliente y rate limiting

### El problema

`X-Forwarded-For` es un header. Los headers vienen de quien nos habla, y quien
nos habla puede ser el atacante. Antes de esta subfase había **tres respuestas
distintas** a "¿cuál es la IP del cliente?":

| Consumidor | Cómo la calculaba | Consecuencia |
|---|---|---|
| DRF throttling | `get_ident()` con `NUM_PROXIES` **sin configurar** | En DRF 3.17 eso significa «usa el header entero como identidad» → un valor distinto por petición = un cubo de rate limit nuevo por petición |
| `AdminAuditLog.log()` | `xff.split(',')[0]` | La entrada **más a la izquierda**: la posición que el llamante controla del todo. Cualquiera elegía bajo qué IP quedaban registradas sus acciones |
| El resto | `REMOTE_ADDR` | Correcto |

Y el proxy de Next reenviaba **todos** los headers salvo hop-by-hop y `host`, así
que el navegador podía escribir cualquiera de ellos.

### Semántica real de DRF 3.17.1, leída del código instalado

```python
if num_proxies is not None:
    if num_proxies == 0 or xff is None:
        return remote_addr
    addrs = xff.split(',')
    return addrs[-min(num_proxies, len(addrs))].strip()
return ''.join(xff.split()) if xff else remote_addr   # NUM_PROXIES=None
```

| `NUM_PROXIES` | Con `X-Forwarded-For` presente | Seguro |
|---|---|---|
| `None` (defecto de DRF) | devuelve **el header entero** | **NO** |
| `0` | devuelve `REMOTE_ADDR` | Sí |
| `N > 0` | N-ésima desde la derecha | Sólo si N proxies **añaden** de verdad |

### La política

Una sola variable, `TRUSTED_PROXY_COUNT` (por defecto **0**), alimenta a la vez
`NUM_PROXIES` de DRF y a `store/client_ip.py`, que es la autoridad única que usa
la auditoría. Que dos subsistemas discrepen sobre quién llama sería peor que
equivocarse los dos igual: el log diría una cosa y el limitador otra.

### Por qué el defecto es 0 y no 1

Con 0 y un proxy no declarado delante, todos los clientes comparten contador:
**demasiado estricto**, nunca evitable. Con 1 y un proxy que **no añade** nada a
`X-Forwarded-For` —que es el caso del proxy de Next— la entrada más a la derecha
es la que escribió el cliente: la configuración que parecía establecer confianza
se la regala al atacante. Declarar un número de proxies es una afirmación sobre
lo que el proxy **hace**, no sobre cuántos saltos hay.

### Por qué el arreglo no puede vivir en Next

`NextRequest` **no expone la IP de la conexión** en Next 16.3.4 (la propiedad
`ip` se eliminó), así que el proxy sólo puede leer headers — justo lo que no se
puede creer. Reconstruir ahí una IP sería inventarla. El proxy por tanto sólo
**elimina** los headers de identidad; la autoridad es Django.

Y tiene que ser Django, porque `docker-compose.yml` publica el backend en
`ports: '8000:8000'`: **es alcanzable sin pasar por Next**. Verificado en vivo —
hablando directo a Django con `X-Forwarded-For` variable, el límite sigue
aplicando.

### `SECURE_PROXY_SSL_HEADER`

Estaba activo incondicionalmente en producción. Es un header: si el backend es
alcanzable directamente, cualquiera puede enviarlo y Django creerá que una
petición en claro fue segura, lo que anula `SECURE_SSL_REDIRECT` y permite poner
cookies `Secure` sobre texto plano. Ahora sólo se activa cuando
`TRUSTED_PROXY_COUNT > 0`.

### Lo que NO resuelve esta subfase

- **Cache del throttle**: no hay bloque `CACHES`, así que Django usa
  `LocMemCache`, **por proceso**. Con varios workers o réplicas cada uno lleva su
  propio contador y el límite efectivo se multiplica por el número de procesos.
  `PENDIENTE INFRA` — requiere cache compartida antes de escalar horizontalmente.
- **Rate limiting de borde / DDoS**: el throttle de DRF es control de abuso
  aplicativo, no protección volumétrica.
- **Credential stuffing distribuido**: limitar sólo por IP no lo cubre. Añadir
  identidad de cuenta al cubo del login queda como `PROPUESTA / P1`.

---

---

## Actualización — servicio técnico, núcleo (M8 / BR-005A)

`RepairOrder` existe. Las tres líneas de esta documentación que decían lo
contrario están corregidas.

### Qué se construyó

`Device`, `RepairOrder`, `RepairStatusSetting`, `RepairStatusHistory` y
`TechnicianAssignment`, más `service_services.py` y dos superficies:
`/api/v1/internal/<slug>/service/` (9 rutas) y
`/api/v1/customer/<slug>/repairs/` (lectura).

Migraciones **0035** (esquema), **0036** (semilla de estados y serie) y **0037**
(capacidades para presets sin modificar).

### Decisiones que conviene poder defender

- **`brand` y `model` son texto normalizado, no un catálogo.** Un catálogo de
  marcas tiene que ser de alguien: de la plataforma, y se queda obsoleto la
  semana que sale un teléfono nuevo sin que ningún tenant pueda arreglarlo; del
  tenant, y son tres tablas de CRUD entre un recepcionista y el equipo que tiene
  delante. Los campos están indexados y migrar a FKs después no cambiaría la API.
- **Sin unicidad global de serial ni de IMEI.** Un equipo vuelve al taller, los
  seriales se transcriben a mano de una pegatina, muchos equipos no tienen uno
  legible, y dos tenants pueden tener el mismo teléfono de segunda mano. La base
  lo permite y el operador recibe un aviso de posible duplicado.
- **La sucursal es de la orden, no del equipo.** Un equipo no vive en una tienda;
  una visita, sí.
- **No hay columna `current_technician`.** La asignación abierta se deriva de la
  tabla, que es la única fuente. Una columna sería una segunda verdad que
  mantener sincronizada.

### Defecto corregido de paso

`sequences.sequence_scope(company, document_type)` aceptaba `document_type` y lo
ignoraba: devolvía siempre el scope configurado para las notas de venta. Con un
solo tipo de documento era inofensivo; el segundo habría numerado órdenes de
servicio por sucursal porque alguien configuró así sus notas.

### Deuda

- Una empresa registrada antes de M8 no recibe automáticamente las capacidades
  de servicio en su rol `Servicio Técnico` — solo en `Administrador`, y solo si
  no lo editó. Misma decisión y misma razón que la migración 0033.
- Sin evidencias fotográficas: no hay proveedor de almacenamiento y no existe un
  solo `FileField` en el backend.
- La serie de servicio numera por empresa; el scope por sucursal se añade cuando
  un negocio lo pida.
- BR-008 sigue `API_PENDING`, pero ahora tiene contra qué diseñarse.



---

## Fase 0.3 / P0-C — Aislamiento administrativo legacy

### Hallazgos revalidados contra HEAD

| ID | Hallazgo | Resultado | Severidad |
|---|---|---|---|
| P0-C-01 | `AdminUserListView` — `IsAdminRole` + `User.objects` global | **CONFIRMADO** | Alta — fuga de PII entre inquilinos |
| P0-C-02 | `AdminUserRoleView` — `IsSuperAdminRole` sobre rol global | **CONFIRMADO** | **Crítica** — escalada de privilegios |
| P0-C-03 | `AdminAuditLogListView` — `IsAdminRole` + log global | **CONFIRMADO** | Alta — rastro de otros inquilinos |
| P0-C-04 | `IsPlatformAdmin` ya existía | **YA CORREGIDO** | — |
| P0-C-05 | `/admin/memberships/` ya tenant-scoped (`scope_queryset`) | **YA CORREGIDO** | — |
| P0-C-06 | M8 `Device` / `RepairOrder` invariantes de empresa | **YA CORREGIDO** | — |
| P0-C-07 | M8 vistas internas: queryset desde el tenant, 404 | **YA CORREGIDO** | — |
| P0-C-08 | M8 propiedad de cliente por FK, no por email | **YA CORREGIDO** | — |
| P0-C-09 | M8 transición de orden ajena **sin test** | **REQUIERE TEST** → añadido | — |
| P0-C-10 | `TechnicianAssignment.clean()` no lo llama `save()` | **PARCIAL por diseño** — el service layer es el guardián, documentado y ahora probado | Baja |

### La escalada de P0-C-02

`IsSuperAdminRole` acepta `UserProfile.role == 'superadmin'`, y **ese endpoint
escribe ese valor**. Un superadmin legacy sin `is_superuser` podía concedérselo a
quien quisiera, en toda la plataforma. La escalera era el propio endpoint.

### Principio aplicado

```
PLATAFORMA                        EMPRESA
User.is_superuser                 Membership + CompanyRole + capabilities
   ↓                                 ↓
operar la plataforma              sólo SU empresa
```

Nunca `UserProfile.role → autoridad global`. El rol legacy **no se retira**
(sigue OBSOLETO / TRANSICIÓN); se le impide cruzar la frontera.

### Un falso verde encontrado y corregido

El primer intento de test hacía `UserProfile.objects.update_or_create(...)` sobre
un usuario recién creado. Crear un usuario dispara una señal que fabrica un
`UserProfile` con rol `customer`, y Django **cachea ese objeto en la instancia**:
actualizar la fila no toca la copia cacheada, así que `get_user_role()` seguía
respondiendo `customer`. El test «al rol legacy se le deniega» pasaba porque la
fixture nunca llegó a ser administrador, no porque el endpoint rechazara a uno.

Se corrigió con `refresh_from_db()` y se añadió un test que vigila la propia
fixture. Comprobación: revirtiendo el arreglo, fallan **3 tests**; con el arreglo,
pasan los 21.


### Cambio de contrato, y los 35 tests que lo señalaron

Al ejecutar la suite completa fallaron **35 tests preexistentes** de
`AdminUserListTest`, `AdminUserRoleChangeTest`, `AdminAuditLog*Test`,
`Audit31*` y los bloques de regresión de las fases 3.2 y 3.3.

No eran daño colateral: **codificaban el contrato antiguo**. Cada uno creaba un
usuario con `UserProfile.role = 'admin'` y **sin ninguna empresa**, y afirmaba
que ese usuario obtenía 200 sobre la lista global de usuarios o sobre el registro
de auditoría completo. Esa afirmación *era* la vulnerabilidad, escrita como
expectativa.

La distinción que hubo que hacer, test a test:

| Lo que probaba | Resolución |
|---|---|
| «el rol legacy abre la superficie global» | **La expectativa era el fallo.** Sustituida por autoridad de plataforma |
| paginación, filtros, ausencia de contraseñas, metadatos de auditoría, 404, no cambiarse el propio rol | **Sigue siendo válido.** Sólo cambió la fixture que llega al endpoint |

Un detalle que obligó a rectificar: promover la fixture *compartida* de
`Phase33RegressionTest` a superusuario arregló los dos tests globales y rompió
otros dos de catálogo y pedidos, porque un administrador de plataforma se
resuelve distinto en la capa de tenant — debe elegir empresa explícitamente. La
fixture de plataforma quedó separada, sólo para los dos endpoints globales.

Y se conserva explícitamente el test que afirma lo contrario de lo que afirmaban
los antiguos: `P0CLegacyAdminIsolationTest.test_the_legacy_global_role_alone_grants_nothing`.
---

## Actualización — diagnóstico, cotización y aprobación (M9 / BR-005B)

M8 se detuvo en `waiting_approval` a propósito. M9 le da contenido.

### La invariante

`waiting_approval` ⇒ existe una cotización SENT.
`approved` ⇒ existe una cotización APPROVED y una decisión del cliente.
`rejected` ⇒ lo mismo del otro lado.

Se sostiene porque los tres estados son inalcanzables por el endpoint genérico
de transición: `EVENT_ONLY_STATES` los rechaza y `available_transitions()` no los
ofrece, así que la app ni siquiera puede dibujar el botón. El paso
`waiting_approval → diagnosing` es un **borde** de evento por la misma razón:
existe solo para `cancel_quote()`, porque moverlo a mano dejaría una cotización
viva contra una orden que ya no la espera.

### Decisiones que conviene poder defender

- **Impuestos cero, no inventados.** No hay tasa, régimen ni configuración en
  ninguna parte de este backend. La columna existe para congelar; nada la
  calcula.
- **Descuento bajo `service.diagnostic.manage`**, no bajo `sales.discounts.apply`:
  quien puede descontar una venta de mostrador no es necesariamente quien puede
  descontar una reparación, y conectarlas habría ampliado en silencio lo que
  significa un permiso existente.
- **Cotización a cero permitida.** Un diagnóstico de cortesía es real, y exigir
  `total > 0` obligaría a escribir un céntimo para describir trabajo gratis.
- **Vigencia derivada, sin scheduler.** Un GET que mutara la base para
  «refrescar» un estado convertiría leer en escribir.
- **El motivo del rechazo no toca el historial.** Vive en la decisión, donde lo
  lee la superficie interna; en un timeline visible para el cliente estaría a un
  cambio de política de acabar publicado.

### P0-B preservado

La IP de una decisión sale de `client_ip.get_client_ip()`, y `AdminAuditLog` ya
la usaba. Un test estructural comprueba que ningún módulo de M9 nombra
`HTTP_X_FORWARDED_FOR` ni `REMOTE_ADDR`, y un test funcional comprueba que una
cabecera falsificada no elige la dirección registrada bajo la configuración por
defecto.

### Deuda

- Sin notificaciones: `sent` significa «disponible», no «correo enviado».
- Sin evidencias (DEC-016).
- Sin política tributaria.
- Asimetría de presets, igual que en 0033 y en M8.



---

## Fase 0.3 / P0-D — Reseñas tenant-safe

### Hallazgos revalidados contra HEAD `36b8a8c`

| ID | Hallazgo | Resultado | Severidad |
|---|---|---|---|
| P0-D-01 | Escritura cross-tenant: `product` escribible resuelto globalmente | **CONFIRMADO** | Alta |
| P0-D-02 | Lectura acotada por `storefront_products` | **YA CORREGIDO** | — |
| P0-D-03 | `author_name` texto libre → suplantación | **CONFIRMADO** | Media |
| P0-D-04 | `user` inyectable por payload | **FALSO POSITIVO** — no está en `fields`; probado igualmente | — |
| P0-D-05 | `id` / `created_at` inyectables | **FALSO POSITIVO** — read-only; probado | — |
| P0-D-06 | Producto ajeno vs. inexistente distinguibles | **CONFIRMADO** | Media |
| P0-D-07 | Producto inactivo aceptaba reseñas | **CONFIRMADO** | Baja |
| P0-D-08 | `average_rating` / `review_count` cruzan inquilinos | **FALSO POSITIVO** — el agregado recorre `product.reviews` y el Product ya viene acotado; probado |
| P0-D-09 | `ReviewCreateThrottle` roto por P0-B | **FALSO POSITIVO** — sigue activo en POST |
| P0-D-10 | Formulario del navegador sin credenciales → 401 siempre | **CONFIRMADO** (preexistente, funcional) | Media |
| P0-D-11 | Sin constraint `(user, product)` | **PROPUESTA** — no existe la regla |
| P0-D-12 | Compra verificada | **PROPUESTA** |

### Arquitectura de escritura resultante

```
POST /api/reviews/
   ↓
IsAuthenticatedOrReadOnly        (anónimo lee, no escribe)
   ↓
ReviewCreateThrottle
   ↓
resolve_storefront_company(request)      ← servidor, no cliente
   ↓
storefront_products(request)             ← is_active=True, del inquilino
   ↓
product = <id> resuelto DENTRO de ese conjunto
   ↓  (fuera de él: mismo mensaje que un id inexistente)
user y author_name derivados de request.user
   ↓
Review
```

### Decisión sobre la resolución del producto

Se compararon las dos vías del §12. Un `ReviewCreateSerializer` aparte habría
dejado dos clases que mantener en paralelo, y la que se olvidara de acotar
volvería a ser global. Acotar el queryset del campo **dentro del propio
serializer**, y dejarlo **vacío** cuando falta el contexto, hace que el camino
inseguro deje de existir: no hay forma de escribir sin storefront.


---

## Fase 0.3 / P0-E — Integridad de cantidades

### Hallazgos revalidados contra HEAD `ea5ecc5`

| ID | Hallazgo | Resultado | Severidad |
|---|---|---|---|
| P0-E-01 | `CartItem` sin `Meta` ni unicidad | **CONFIRMADO** | Alta |
| P0-E-02 | Alta al carrito TOCTOU (`filter().first()` → `create()`) | **CONFIRMADO** | Alta |
| P0-E-03 | Incremento read-modify-write → lost update | **CONFIRMADO** | Media |
| P0-E-04 | `validate_lines_and_subtotal` valida línea a línea | **CONFIRMADO** | Alta |
| P0-E-05 | V1 y POS normalizan; el checkout web no | **CONFIRMADO** | Alta |
| P0-E-06 | `OrderItem` sin unicidad | **CONFIRMADO** | Alta |
| P0-E-STOCK-01 | Dos líneas del mismo producto descuentan sólo una | **CONFIRMADO — CRÍTICO** | Crítica |
| P0-E-08 | Duplicados en datos existentes | **NINGUNO** | — |
| P0-E-09 | Concurrencia real bajo PostgreSQL | **PENDIENTE — requiere PostgreSQL** | — |

### La medición que resolvió la contradicción

El docstring de `normalize_items` (POS) afirmaba que el servicio de inventario se
salta la segunda línea repetida. Una primera lectura del bucle sugería lo
contrario, porque el extracto revisado terminaba **antes** de la línea 525,
`already_recorded.add(item.product_id)`, que es la que muta el conjunto dentro del
bucle.

No se decidió por el comentario. Se midió:

```
OrderItems:      P × 3  +  P × 3   (6 unidades)
Stock inicial:   10
Stock final:     7          ← bajaron 3
Movimientos:     1, cantidad 3
Replay ×2:       7          ← idempotente, no vuelve a descontar
```

**El docstring tenía razón; la lectura del código estaba equivocada.** Escenario A.

### Por qué no se toca la guarda

Su clave `(order, product)` es lo que impide que un webhook repetido descuente dos
veces. No puede distinguir un replay de un pedido con dos líneas del mismo
artículo. Debilitarla para arreglar el duplicado reabriría el doble descuento —
cambiar un error de menos por uno de más. Lo que se vuelve imposible es el
duplicado.

### SQLite y PostgreSQL — qué queda probado

| Propiedad | Probada aquí | Cómo |
|---|---|---|
| Unicidad de línea de carrito | **Sí** | Constraint, verificada en SQLite |
| Unicidad de línea de pedido | **Sí** | Constraint |
| Normalización de líneas repetidas | **Sí** | Funcional |
| Validación de stock agregada | **Sí** | Funcional |
| Incremento del lado de la BD | **Sí** | Estructural, sobre el AST |
| Carrera perdida no da 500 | **Sí** | Estructural |
| Interleaving real de dos escritores | **No** | SQLite serializa y responde «database table is locked» |

Los tests con hilos y barrera están escritos y **se omiten explícitamente** en
SQLite con esa razón. Correrán sin cambios el día que la suite apunte a
PostgreSQL. Lo que garantiza la invariante —constraint más incremento atómico— no
depende de cómo el motor planifique los escritores.

## M10 / BR-005C — Ejecución de reparación y consumo transaccional de inventario

### Hallazgos revalidados contra HEAD `2116b17`

`StockMovement.SERVICE_EXIT` («Salida por servicio técnico») estaba **declarado
desde la migración 0013 y sin un solo uso**: ningún camino de código lo creaba y
`MANUAL_TYPES` lo excluye, así que la API de ajuste manual lo rechazaba. No era
un tipo que faltase; era un tipo esperando su módulo.

`BranchStock.quantity` es `PositiveIntegerField` con check constraint
`quantity >= 0`. `RepairQuoteItem.quantity` es `DecimalField(10,2)`. Las dos
escalas no coinciden, y no había regla escrita para convertir una en otra. M10
la escribe: un repuesto se consume en unidades enteras y una línea con cantidad
fraccionaria se rechaza en el servicio, en vez de redondearse en silencio hacia
el inventario de alguien.

`Product.inventory` **no tiene check constraint** — solo `BranchStock.quantity`
lo tiene. Una segunda implementación del descuento corrompería el agregado que
lee el escaparate sin que ningún error saltara. Es la razón concreta por la que
`create_stock_movement` es el único escritor y M10 no lo esquiva.

No existía **ninguna** función de compensación en todo el backend.
`cancel_transfer` documenta la ausencia: se niega a cancelar una transferencia en
tránsito porque «revertirla exige movimientos compensatorios que todavía no están
implementados». M10 escribe la primera, y solo para su propio dominio.

### El orden de bloqueo canónico

Los seis consumidores de stock que ya existían coinciden en uno: agregado
propietario primero, después `BranchStock` en orden `(branch_id, product_id)`,
`Product` nunca. `service_services` coincide: `RepairOrder`, después
`RepairQuote`. M10 es la concatenación y no inventa nada:

    RepairOrder → RepairExecution → PartUsage → BranchStock

### Idempotencia: había precedente, y se copió

Dos veces en el repositorio (`Order.pos_idempotency_key` y
`Order.idempotency_key`), con la misma forma: clave del cliente + huella SHA-256
como columnas, `UniqueConstraint` parcial, `IntegrityError` capturado y relectura.
No hay modelo genérico de idempotencia y M10 no creó uno: cuatro columnas en
`PartUsage` y una constraint parcial.

El detalle que importa: el reintento que **pierde** la carrera deja que su
transacción entera se deshaga —llevándose su movimiento de stock— y solo después
el envoltorio devuelve la fila ganadora. Devolverla desde dentro de la transacción
habría confirmado un descuento huérfano, que es exactamente el doble consumo que
todo el mecanismo existe para impedir.

### Decisiones que conviene poder defender

**`WAITING_PARTS` se implementó.** El objetivo de la fase incluye «pausar por
falta de repuestos», y sin el estado una reparación bloqueada se queda en
`in_repair` indefinidamente — un estado que miente. Pero **no** se activa como
efecto secundario de un consumo fallido: el stock insuficiente responde 409 y no
mueve nada. Un taller no debe descubrir su propio estado leyendo logs de error.

**`REPAIRED` no es terminal y su etiqueta por defecto no promete recogida.** El
técnico terminó; nadie ha revisado el trabajo y nadie ha avisado al cliente. M11
y M12 son fases distintas.

**Una pieza extra no aprobada no se consume.** Vuelve por diagnóstico, cotización
nueva y aprobación nueva. Es más lento, y es la diferencia entre una factura y
una sorpresa.

### Deuda

Reserva de stock al cotizar (no existe, deliberadamente). Devolución de piezas
después de finalizar. Transferencia entre sucursales dentro del flujo de
reparación. Control de calidad, entrega, pago, garantía, evidencias, BR-008.

---

## Fase 0.3 / P0-F — Pasarela de pago Izipay e integridad monetaria

**Estado: IMPLEMENTADO**, con la verificación end-to-end en sandbox **pendiente
de credenciales**. Migraciones **0043**, **0044** y **0045**.

### La decisión que ordena todo lo demás

> **El callback del navegador no es autoridad de pago.**

El SDK devuelve un resultado a la página del comprador. Esa página es el peor
testigo disponible: se edita, se repite y se inventa desde la consola. Sirve para
elegir qué pantalla mostrar. Lo único que puede marcar un pedido como pagado es
una notificación firmada, verificada en el servidor con una clave que el
navegador nunca ve.

### Sólo los bytes firmados son el mensaje

Izipay firma `payloadHttp`, y **nada más**. La copia decodificada que viaja al
lado —`response`— es cómoda y no está protegida por ninguna firma: cualquiera que
alcance el endpoint puede editarla sin invalidar nada.

Por eso todo valor con consecuencias —importe, moneda, número de orden, comercio,
transacción, código— se lee **de dentro** del `payloadHttp` parseado, y el
adaptador devuelve un `NotificationResult` en el que no sobrevive ningún dato del
sobre exterior. Un objeto del que no se puede leer la copia equivocada.

Ese payload **no se vuelve a serializar** para verificarlo:

```
json.loads(payloadHttp)  →  json.dumps(...)     ✗  otros bytes, otro HMAC
str tal cual, como llegó                        ✓
```

Reordenar claves, cambiar separadores o re-escapar el acento de «Operación» basta
para que la firma no cuadre y se rechacen todas las notificaciones legítimas.

### Comprobaciones antes de tocar nada

| Orden | Comprobación | Qué evita |
|---|---|---|
| 1 | Firma HMAC-SHA256 con `compare_digest` | Mensaje falsificado o alterado |
| 2 | `transactionId` conocido | Autorización de una operación que no existe aquí |
| 3 | `orderNumber` del propio intento | Pagar un pedido con la autorización de otro |
| 4 | `merchantCode` configurado | Autorización de otro comercio |
| 5 | Moneda exacta | 100 USD tomados por 100 PEN |
| 6 | Importe **exacto** contra `Order.total` | Cobro de menos y de más |
| 7 | Código de respuesta autorizado | Rechazo tratado como pago |

Sólo después, bajo `select_for_update()`, cambian estado, stock y carrito.

### El importe cuadra en las dos direcciones

`99.99` no paga un pedido de `100.00`. **`100.01` tampoco.** Recibir de más no es
un golpe de suerte: es una autorización que pertenece a otra intención, y
quedarse con la diferencia es reconciliar por accidente. Una discrepancia no se
registra como rechazo sino como `integrity_failed` — un rechazo lo dice la
pasarela; esto lo dicen la pasarela y la base de datos a la vez, y necesita una
persona, no un reintento.

### `PaymentTransaction`: los intentos son filas, no columnas

| Opción | Por qué no / sí |
|---|---|
| **A** — campos en `Order` | Más simple, y **pierde historial**: un comprador con dos rechazos y una aceptación produce tres intentos, y un solo campo guarda el último. Justo lo que hace falta cuando alguien impugna un cargo |
| **B** — `PaymentTransaction` | **Elegida.** Una fila por intento; `UNIQUE(provider, transaction_id)` es la idempotencia decidida por la base de datos, no por un `if` |

Y hay una razón del propio protocolo: Izipay rechaza un `orderNumber` repetido
(código **P69**), así que un reintento necesita uno nuevo. El número pertenece al
intento.

**Sin columna de empresa**: el inquilino es `order.company`. Una segunda copia
crearía dos respuestas a «de quién es este pago» — el mismo criterio que dejó
`company` fuera de `Review` en P0-D y de `CartItem` en P0-E.

### Qué NO se guarda

No el payload completo, no el bloque de facturación, no el de envío, no la
tarjeta enmascarada, no el documento que la pasarela devuelve. Guardarlo «para
auditoría» construiría una segunda base de datos de clientes dentro de la tabla
de pagos, con otra política de retención y sin que nadie la pidiera.

### Secretos y entorno

`IZIPAY_API_KEY` e `IZIPAY_HASH_KEY` no salen del backend ni aparecen en ninguna
respuesta. Al navegador van sólo código de comercio, clave pública RSA y un token
de sesión emitido para **una** transacción — los tres públicos por documentación.

El frontend recibe el **nombre** del entorno, nunca una URL: las dos direcciones
oficiales del SDK son constantes en el código. Una dirección de script que viaja
como dato la elige quien pueda modificar la respuesta, en la única página donde
se teclea una tarjeta.

`IZIPAY_ENV` es explícito. **Nunca se deduce de `DEBUG`**: un staging con
`DEBUG=0` no es producción, y deducirlo es la forma de empezar a cobrar de verdad
sin que nadie lo haya decidido.

### Multiempresa — lo que existe hoy

| Modelo | Estado |
|---|---|
| **A** — una instalación, un comercio Izipay | **Es el actual**, igual que la configuración anterior |
| **B** — un comercio por empresa | **PENDIENTE DE DECISIÓN / INFRA** |
| **C** — la plataforma procesa por los tenants | **PENDIENTE DE DECISIÓN** (implicaciones regulatorias) |

No se inventa la decisión comercial. Las credenciales se pasan como un valor
(`IzipayCredentials`) a cada llamada, así que el día que se decida el modelo B
sólo cambia de dónde sale ese valor, no quién lo usa. **No se guardan secretos en
`CompanySettings`.**

### Lo que queda sin verificar, y por qué

| Punto | Estado |
|---|---|
| Flujo end-to-end contra sandbox | **PENDIENTE — requiere credenciales Izipay** |
| `IZIPAY_TOKEN_URL` | Configuración **sin valor por defecto**: la referencia REST se renderiza en el navegador y la autoridad es el panel del comercio. Una URL adivinada habría sido un endpoint inventado |
| `dateTimeTransaction` | Se envía en milisegundos de época — la forma de todos los ejemplos oficiales; el formato exacto no está publicado en ninguna página servida estáticamente |
| Código `P66` | Figura como «Operación exitosa» en la tabla oficial y **no** se acepta como autorización hasta confirmarlo con Izipay. Un código aceptado de más regala mercadería; uno rechazado de más deja un pedido pendiente y visible |

## H1 + M11 / BR-005D — Paridad de permisos y control de calidad

### H1A — no fue necesario

La colisión de migraciones que M10 dejó señalada (pagos `0043-0045` contra
servicio `0043-0045`, ambas colgando de `0042_enforce_line_uniqueness`) **ya
estaba resuelta en master** por `0046_merge_payments_and_service_execution`, un
nodo vacío que depende de las dos hojas. El grafo tiene una sola hoja y
`makemigrations --check` está limpio. No se creó ningún merge artificial.

### H1B — la matriz real, resuelta por el resolver

No leída: ejecutada contra `resolve_capabilities()` en una base de pruebas.

```
                                VIEW   MANAGE  DIAGNOSE   REPAIR  QUALITY
platform master                  sí       sí        sí       sí       no
company admin                    sí       sí        sí       sí       no
technician preset NUEVO          sí       sí        sí       sí       no
technician preset PRE-M8         no       no        no       no       no
ventas / inventario              no       no        no       no       no
técnico custom restringido       sí       no        no       sí       no
cliente / otra empresa           no       no        no       no       no
```

(QUALITY en `no` para todos porque en ese momento seguía RESERVED.)

El hallazgo: **el preset técnico de una empresa registrada antes de M8 no podía
hacer nada**. Siete migraciones de capacidades y ninguna tocó `servicio-tecnico`.
`0047` lo cierra con un discriminador de cuatro campos, más estricto que el de
`Administrador` porque tiene que serlo: el conjunto histórico son dos o tres
códigos y colisiona con un rol limitado ordinario.

### El fallback legacy no se amplió

`LEGACY_ROLE_CAPABILITIES['technician']` sigue valiendo `{company.view,
service.manage}`. Ensancharlo habría concedido el ciclo de vida completo a todos
los técnicos legacy de todos los tenants sin roles configurados, sin que nadie lo
decidiera. La dirección declarada es migrar hacia RBAC por empresa.

### Paridad Web / Mobile — la brecha, dicha en voz alta

| Acción | Backend | Web UI | Mobile |
|---|---|---|---|
| Listar órdenes | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Abrir orden | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Cambiar estado | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Diagnosticar / cotizar | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Iniciar / completar reparación | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Registrar repuesto | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Control de calidad | IMPLEMENTADO | **PENDIENTE** | INTEGRADO (M11 mobile) |
| Editar capacidades de un rol | IMPLEMENTADO (API) | **PENDIENTE** | fuera de alcance |

**H2 CERRÓ ESA BRECHA.** `/admin/service` y `/admin/service/orders/[id]`
existen, contra los **mismos endpoints v1** que consume Mobile, y el registro de
módulos ya apunta a ellas. La tabla anterior describe el estado previo a H2 y se
conserva por trazabilidad; la vigente es:

| Acción | Backend | Web UI | Mobile |
|---|---|---|---|
| Listar / abrir orden | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Cambiar estado | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Asignar técnico | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Diagnosticar / cotizar / publicar | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Iniciar / pausar / completar reparación | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Repuestos y reverso | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Control de calidad (PASS/FAIL) | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Portal de cliente de reparación | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |

**Lo que la Web sigue sin tener** es el portal de CLIENTE de reparaciones: el
cliente autenticado no puede seguir su equipo desde la Web como sí lo hace desde
Mobile. Se declara PENDIENTE.

**Y no hay ni un test de frontend en el repositorio**: sin runner, sin
configuración, sin script. La consola de servicio se valida con `tsc`, `eslint`
y `next build`, que no es lo mismo que estar probada. Es la deuda más grande que
deja H2.

**La paridad que sí se cumple es la que importa**: misma capability, mismo
endpoint, misma regla de tenant y de sucursal, misma transición. Backend es la
fuente única. Lo que no existe es la interfaz Web, y eso se declara PENDIENTE en
lugar de disimularlo.

### Deuda

Entrega, cobro, garantía, evidencias, BR-008. Interfaz Web de servicio técnico.
Editor de roles en la Web — mientras no exista, «que lo conceda el
administrador» no es una salida real para un tenant.

---

## M12 / BR-005E — Entrega del equipo

### Lo que se construyó

`RepairDelivery` + `deliver_repair()` +
`POST /api/v1/internal/<slug>/service/orders/<id>/delivery/` + botón en la
consola Web + sección en Mobile. Doce estados de ciclo de vida; `DELIVERED` es
el duodécimo y el segundo terminal.

### El hallazgo que ordenaba la fase: no hay cobro de servicio

La instrucción decía «si NO existe integración financiera correcta: SERVICE
PAYMENT = PENDIENTE. No crear `paid = true` como sustituto». Se verificó leyendo
el modelo, no la documentación:

```python
class PaymentTransaction(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT,
                              related_name='payment_transactions')
```

**Sin `null=True`, sin columna de empresa, sin relación genérica.** Una
`RepairOrder` no puede pagarse por ahí. No hay puente que construir sin cambiar
ese modelo, y cambiarlo es una decisión de esquema que toca el checkout de
e-commerce y la pasarela izipay — fuera del alcance de una fase de entrega.

**SERVICE PAYMENT = PENDIENTE.** La fase se partió: M12A (entrega) se
implementa, M12B (cobro) se declara. `DELIVERED` significa que el equipo salió
con alguien y nada más. No se escribió ningún booleano de pago, no se creó
ninguna `Order` de e-commerce, y un test estructural prohíbe `PaymentTransaction`,
`paid = True`, `Order.objects`, `stripe` e `izipay` en el módulo de servicio y en
sus vistas.

### Separación de deberes, ahora expresable

`service.delivery.manage` es capability propia. La matriz, ejecutada contra
`resolve_capabilities()` y no leída:

```
                                  VIEW  ORDERS.MANAGE  REPAIR  QUALITY  DELIVERY
platform master (tenant explícito)  sí        sí          sí      sí       sí
company admin                       sí        sí          sí      sí       sí
preset Servicio Técnico             sí        sí          sí      sí       sí
preset Ventas / Inventario          no        no          no      no       no
técnico "solo taller" (custom)      sí        no          sí      sí       no
mostrador "solo entrega" (custom)   sí        no          no      no       sí
rol vacío / nombre "technician"     no        no          no      no       no
cliente / admin de otra empresa     no        no          no      no       no
```

Las dos filas custom son el punto: **`service.orders.manage` sola NO entrega**, y
**`service.delivery.manage` sola no mueve ni cancela nada**. Un taller que quiere
que el técnico repare y el mostrador libere puede decirlo; si la entrega hubiera
colgado de la capability ancha, no podría.

### Paridad de superficies

| Acción | Backend | Web UI | Mobile |
|---|---|---|---|
| Listar / abrir orden | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Cambiar estado / asignar | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Diagnosticar / cotizar / publicar | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Reparar / repuestos / reverso | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Control de calidad | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| **Registrar entrega** | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| **Cobro de servicio** | **NO EXISTE** | **NO EXISTE** | **NO EXISTE** |
| Garantía / reentrada | NO EXISTE | NO EXISTE | NO EXISTE |
| Evidencias (firma/foto) | NO EXISTE (DEC-016) | — | — |
| Portal de cliente de reparación | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |

### Deuda que deja M12

1. **Cobro de servicio (M12B)** — la mayor. Requiere decidir si es documento
   nuevo o si `PaymentTransaction` se generaliza, y arrastra la política
   tributaria que `tax_amount` deja en cero desde M9.
2. **Portal Web de cliente de reparaciones** — sigue PENDIENTE desde H2. El
   cliente autenticado sigue sin poder seguir su equipo desde la Web.
3. **Sin tests de frontend en el repositorio** — sin runner, sin configuración,
   sin script. La consola de servicio se valida con `tsc`, `eslint` y
   `next build`. Es la misma deuda que dejó H2 y M12 no la paga.
4. **Garantía** — será una reentrada que cita a la orden anterior, nunca una
   edición de una orden cerrada.
5. **Evidencias** (DEC-016), **notificaciones**, **BR-008**, **editor de roles en
   la Web**.

---

## G3 + H3 + M12B — consolidación, baseline de tests y cobro del servicio

### G3 — las dos ramas abiertas, consolidadas

Ambas se integraron con `master` mediante **merge normal**, nunca rebase. Un
compañero había hecho en paralelo la reconciliación del grafo de migraciones
**renumerando** la cadena RBAC encima de la de entrega en lugar de añadir un
nodo de merge; su solución es mejor que la mía y se adoptó, descartando la
propia. La secuencia final es lineal, con **una sola hoja**.

Lo que sí aportó esta fase a PR #10 fueron cuatro defectos que la auditoría
encontró y que su autor no había visto:

**1. Escalada apagando y encendiendo un rol.** `capability_set` devuelve
`frozenset()` mientras el rol está inactivo — correcto para **resolver**,
equivocado para **delegar** — y el PATCH del rol solo revalidaba delegación si
el cuerpo traía `capabilities`. Encadenado:

```
PATCH /admin/roles/R/  {"is_active": false}       → 200
POST  /admin/membership-role-assignments/ {yo, R} → 201  (!)
PATCH /admin/roles/R/  {"is_active": true}        → 200
```

y el llamador resolvía una capacidad que nunca tuvo. Reproducido ejecutando: el
test del viaje completo fallaba con la capacidad efectivamente concedida.
Cerrado en tres sitios.

**2. Escalada por delegado en la ruta legacy.** `can_delegate_capabilities()`
guardaba los cuatro caminos RBAC y no el de acuñar una membresía;
`LEGACY_ROLE_CAPABILITIES['admin']` es todo el catálogo. Cerrado, **estrechando
deliberadamente** un comportamiento existente.

**3. El discriminador de `Ventas` no filtraba por slug** y ensanchaba cualquier
rol cuyas capacidades igualaran un conjunto de siete códigos — «el rol de
mostrador», lo más natural que un taller arma a mano.

**4. El selector de empresa estaba muerto** en `/admin/roles` y `/admin/users`:
decían «selecciona una empresa» y no pasaban `onSelectCompany`.

### PR #17 — la renumeración arregló el orden, no la comparación

`0057` comparaba contra un `_TECHNICIAN_CAPS` **importado en vivo**. Con la
cadena lineal el orden queda garantizado, así que una base al día migra bien —
pero una base **atrasada** no: añádase una capacidad número trece en `0058`, y
un tenant que cruce ambos nodos en un solo `migrate` compara doce contra trece,
omite a **todos** los técnicos y culpa al taller de una personalización que
nunca hizo. Congelado, con un tripwire que falla el día que el preset crezca.

### H3 — baseline de tests del frontend

**Jest + React Testing Library vía `next/jest`.** Elegido por lo instalado: Next
16.3.4 lo trae, configura el SWC con el que ya se compila, lee el alias del
tsconfig y stubea CSS. Vitest necesitaría cuatro piezas para llegar al mismo
sitio.

Dos cosas se aprendieron de ejecuciones fallidas y quedan documentadas en la
config: el alias hay que declararlo **dos veces** (SWC reescribe los imports
reales, pero `jest.mock('@/…')` es un string que no toca), y `next/navigation`
se mockea una vez en el setup porque es un hecho del **entorno**.

El test del selector de empresa, corrido contra el código tal como se publicó,
falla con `Number of calls: 0`. Esa es la demostración de que la baseline sirve.

### M12B — el libro mayor de cobros

**Arquitectura A**, decidida leyendo el modelo:

| pregunta | respuesta |
|---|---|
| ¿`PaymentTransaction` es reutilizable? | **No.** FK no nula a `Order`, `PROTECT`, y **sin columna de empresa**: su tenancy es `order.company` |
| ¿Qué asume `Order`? | la FK, la tenancy ausente, el índice `(order, created_at)`, `order_number` único **global**, y `amount` contra `Order.total` |
| ¿Qué webhook lo asume? | `IzipayNotificationView`: resuelve sin pista de tenant y entra por `attempt.order` para comparar, marcar `order.paid`, mover stock, vaciar carrito y mandar correos |
| ¿Qué sí es reutilizable? | **todo `store/payments/izipay.py`** — no importa un solo modelo |
| ¿Cuál es el saldo autoritativo? | `financial_quote()` → última revisión aprobada. **Nunca la suma** |
| ¿Parciales? | sí. ¿Pagar de más? no |
| ¿Reembolsos? | **no existen en ninguna parte del repo**. `Order.Status.REFUNDED` no lo escribe nadie |
| ¿Impuestos reales? | **no.** `tax_amount` es columna muerta |
| ¿Comprobante fiscal? | **no.** `SalesNote` lleva un recuadro rojo que dice que no lo es |
| ¿Merchant por tenant? | **no.** `load_credentials()` lee de `settings`: config **global** |

### Estado de superficies

| Acción | Backend | Web | Mobile |
|---|---|---|---|
| Consola de roles y permisos | IMPLEMENTADO | INTEGRADO | fuera de alcance |
| «Mis reparaciones» | IMPLEMENTADO | INTEGRADO | PENDIENTE |
| Libro mayor de cobros | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Resumen de saldo (cliente) | IMPLEMENTADO | **PENDIENTE** | INTEGRADO |
| Reverso de pago | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| Política pago-antes-de-entregar | IMPLEMENTADO | INTEGRADO | INTEGRADO |
| **Pago en línea del servicio** | **NO EXISTE** | — | — |
| Portal Web de cliente | IMPLEMENTADO (API) | **PENDIENTE** | INTEGRADO |
| Garantía / reingreso | NO EXISTE | — | — |
| Impuestos · comprobante fiscal | NO EXISTE | — | — |

### Deuda que deja esta fase

1. **Pago en línea (M12B2)** — el adaptador es reutilizable; faltan
   `RepairPaymentAttempt`, el flujo de sesión y la notificación firmada.
2. **Recotizar tras aprobar es imposible hoy.** Hueco del módulo de cotización.
3. **Merchant global**, no por tenant.
4. **Impuestos y comprobante fiscal** siguen sin existir.
5. **Portal Web de cliente de reparaciones** — sigue PENDIENTE desde H2.
6. **`Membership.adopted_rbac_at`** — la marca de adopción RBAC es la existencia
   de una fila, y `CASCADE` la borra si alguien elimina y recrea la membresía.
   Solo alcanzable desde el admin de Django.
7. **`MembershipAdmin`** es el único admin hermano sin `has_delete_permission`.


## M12B — Centro de notificaciones

**Estado: IMPLEMENTADO** para `IN_APP` y `EMAIL`. Migración **0058**.

### Por qué tres tablas y no una

```
NotificationEvent      algo ocurrió
Notification           alguien debe saberlo
NotificationDelivery   intentamos avisarle por un canal
```

Una sola tabla con un booleano `email_enviado` se rompe con el segundo
destinatario, el segundo canal o el primer reintento — y se rompe en silencio.

`IN_APP` no tiene fila de entrega: la `Notification` **es** la entrega in-app, y
una fila diciendo «escribimos con éxito la fila a la que estamos unidos» sería
una tautología con un índice encima.

### Idempotencia en tres capas

| Capa | Constraint |
| --- | --- |
| Evento | `event_key` UNIQUE, derivado de la entidad |
| Destinatario | `UNIQUE(event, user)` · `UNIQUE(event, customer)` |
| Canal | `UNIQUE(notification, channel)` |

Más una `CheckConstraint`: exactamente un destinatario. Ninguno significa que no
es de nadie; ambos, que dos superficies la reclamarían.

### Eventos implementados

| Evento | Origen real | Audiencia |
| --- | --- | --- |
| `service.assignment.created` | `assign_technician()` | técnico asignado |
| `service.quote.available` | `publish_quote()` | cliente |
| `service.quote.approved/rejected` | `record_quote_decision()` | técnico o gestión |
| `service.ready_for_pickup` | transición real | cliente **y** personal de entrega de esa sucursal |
| `service.delivered` | transición real | cliente |
| `service.status.changed` | transición real | cliente (sólo estados con mensaje) |
| `commerce.payment.confirmed` | IPN verificado | cliente |
| `commerce.fulfillment.ready/shipped/delivered` | `change_fulfillment_status()` | cliente |
| `commerce.order.cancelled` | `change_fulfillment_status()` | cliente |

Todos respaldados por una transición que ya existía. No se inventó ningún
estado, y `wallet.*` no aparece porque el módulo no existe.

### El master de plataforma no es destinatario automático

`resolve_capabilities()` devuelve todas las capacidades al superusuario en todas
las empresas. Una consulta de «quién tiene esta capacidad» le entregaría cada
evento de cada tenant. **Autorizar y direccionar son preguntas distintas**, y la
resolución de destinatarios lo excluye a propósito.

### El correo de confirmación no se duplicó

`commerce.payment.confirmed` no está en `EMAIL_WORTHY_EVENTS`: `email_services`
ya lo envía con su recibo y su idempotencia. M12B aporta el registro in-app.

### Deuda declarada

Preferencias por evento/canal (**PARCIAL**) · reintentos automáticos (existe
`retry_failed_delivery`, sin planificador — **PROPUESTA**) · push, WhatsApp, SMS
(**PROPUESTA**) · portal web de reparaciones del cliente (**PENDIENTE**) ·
Mobile (**PENDIENTE**) · `Administrador 18/37` en instalación desde cero
(**PENDIENTE PREEXISTENTE**, no ampliado por esta fase).


---

## M12B.1 — Paridad de presets e integración con el libro de pagos

**Estado: IMPLEMENTADO.** Migraciones **0060** (renumerada desde 0058) y **0061**.

### Auditoría de migraciones que amplían presets

| Migración | Preset | Discriminador | ¿Segura en fresh install? |
| --- | --- | --- | --- |
| 0017 seed | todos | literal congelado | **SAFE** (siembra, no compara) |
| 0033 customer | Administrador | catálogo vivo − nuevas | **UNSAFE** |
| 0035 commercial | Administrador + Ventas | vivo (admin) / congelado (ventas) | **UNSAFE** para admin |
| 0037 commission | Administrador | catálogo vivo − nuevas | **UNSAFE** |
| 0037 service | Administrador | catálogo vivo − nuevas | **UNSAFE** |
| 0040 diagnostic | Administrador | catálogo vivo − nuevas | **UNSAFE** |
| 0040 promotion | Administrador | catálogo vivo − nuevas | **UNSAFE** |
| 0045 repair | Administrador | catálogo vivo − nuevas | **UNSAFE** |
| 0047 technician | Servicio Técnico | literal congelado | **SAFE** |
| 0050 quality | Admin + Técnico | vivo (admin) / congelado (técnico) | **UNSAFE** para admin |
| 0053 delivery | Admin + Técnico | vivo (admin) / congelado (técnico) | **UNSAFE** para admin |
| 0054 supervisor | Ventas + Supervisor | congelado (compara) / vivo (crea) | **SAFE** |
| 0057 legacy tech | asignaciones | congelado | NO APLICA |
| 0059 payment | Admin + Ventas | vivo (admin) / congelado (ventas) | **UNSAFE** para admin |
| **0061 paridad** | Administrador | **congelado** | **SAFE** |

El patrón se ve de un vistazo: **todo discriminador congelado funciona; todo
discriminador vivo falla**, y sólo el de `Administrador` es vivo. Ventas,
Inventario, Servicio Técnico y Supervisor Técnico ya tenían paridad correcta
antes de esta subfase — el defecto era de un solo preset.

### Reconstrucción empírica de la forma histórica

Base limpia, migración a migración:

```
tras 0017 → Administrador = 18
tras 0033 → 18      tras 0045 → 18
tras 0035 → 18      tras 0050 → 18
tras 0037 → 18      tras 0053 → 18
tras 0040 → 18      tras 0059 → 18
```

Una sola forma que reparar, y es determinista: exactamente el `_ALL_ASSIGNABLE`
de 0017. No hay formas intermedias porque ningún grant llegó nunca a producir una.

### El discriminador de 0061

Cuatro campos en conjunción, el patrón que 0053 estableció: `slug`, `name`, una
de las descripciones que **la propia plataforma** escribió (dos variantes
históricas), e igualdad **exacta** de conjunto.

Un tenant tendría que haber elegido el slug de la plataforma, su nombre, una de
sus dos frases literales y precisamente esos dieciocho códigos —ni diecisiete ni
diecinueve— para ser confundido con un preset intacto.

**Limitación declarada:** un `Administrador` del que un taller quitó una capacidad
y luego añadió otra distinta, quedando en dieciocho, sería indistinguible. Se
acepta el riesgo con el criterio conservador: la conjunción exige igualdad exacta
del conjunto, no del tamaño.

### Camino de upgrade, verificado con datos inyectados

| Rol | Antes | Después | Resultado |
| --- | ---: | ---: | --- |
| Administrador sembrado | 18 | 38 | completado |
| Administrador intacto (otro tenant) | 18 | 38 | completado |
| Administrador recortado | 17 | 17 | **intacto** |
| Administrador ampliado | 19 | 19 | **intacto** |
| Rol renombrado | 18 | 18 | **intacto** |
| Supervisor con duplicado | 15 | 14 | dedup, misma autoridad |

El duplicado se inyectó por SQL crudo a propósito: `CompanyRole.save()` normaliza,
así que la única forma de que llegara a la base es la que realmente ocurrió — una
migración escribiendo por el modelo histórico, que no lleva ese `save()`.

### Eventos del libro de pagos

| Evento | Audiencia | Correo | Por qué |
| --- | --- | --- | --- |
| `service.payment.recorded` | cliente | no | El resumen ya le expone `paid` y `outstanding`; está en el mostrador |
| `service.payment.reversed` | **interno** (`service.payments.manage`) | no | Un reverso es corrección contable, no reembolso |

Clave de idempotencia desde `RepairPayment.id`, nunca del request. **Ninguna
capability nueva:** M12B.1 usa las que PR #19 trajo.

### Deuda que sigue abierta

Preferencias por evento/canal (**PARCIAL**) · reintentos automáticos, sin
planificador (**PROPUESTA**) · push, WhatsApp, SMS (**PROPUESTA**) · portal web de
reparaciones del cliente (**PENDIENTE**) · Mobile (**PENDIENTE**) · comunicados
internos, M12C (**PENDIENTE**) · evidencias, M12D (**PENDIENTE**).

---

## M12C — Comunicados internos

**Estado: IMPLEMENTADO** para `IN_APP`. Migraciones **0062** (esquema) y **0063**
(capability). Catálogo asignable: **39**.

### Responsabilidades

| Entidad | Qué es | Qué NO es |
| --- | --- | --- |
| `Announcement` | El documento: autor, texto completo, prioridad, estado | No es una bandeja |
| `AnnouncementAudienceRule` | Una línea de «a quién», siempre con empresa | No es un JSON opaco |
| `NotificationEvent` | Que el comunicado salió, **por empresa** | No es el documento |
| `Notification` | La copia de un destinatario, con su `read_at` | No es el documento |

`Notification.body` sigue en 400 caracteres. Es un preview; el texto vive en el
`Announcement` al que la notificación apunta con `target_type='announcement'`.

### Ciclo de vida

```
DRAFT ──publish──► PUBLISHED   (inmutable: ni texto, ni audiencia, ni borrado)
  │
  └──cancel──► CANCELLED       (borrador descartado; NO es recall)
```

Guardar un borrador diez veces produce diez guardados y **cero destinatarios**.

### Las cinco audiencias

| Tipo | Cómo resuelve | Nota |
| --- | --- | --- |
| `ALL_COMPANY` | membresías activas, usuarios activos, empresa activa | master excluido |
| `BRANCH` | `has_branch_access()`, no `membership.branch_id` | quien trabaja en tres locales, trabaja en tres |
| `ROLE` | `MembershipRoleAssignment` activa sobre `CompanyRole` real | funciona con roles personalizados, sin nombres hardcodeados |
| `CAPABILITY` | `resolve_capabilities()` contra el catálogo real | rechaza códigos inexistentes |
| `USER` | membresía activa en ESA empresa | ids cross-tenant rechazados |

Una persona que cumple cuatro reglas recibe **un** aviso. La unión se toma en
`resolve_audience()` y la constraint de M12B sigue detrás como garantía.

`active_internal_users()` es ahora **el único sitio** que decide quién cuenta
como personal: `resolve_internal_recipients()` de M12B se reescribió encima en
lugar de duplicar las cuatro condiciones.

### Superficies

```
/api/v1/internal/<slug>/communications/…    tenant · communications.manage
/api/v1/internal/<slug>/announcements/<id>/ destinatario · SIN capability
/api/v1/platform/announcements/…            master · is_superuser
```

La superficie platform está separada **porque** apunta a varias empresas.
Esconder multiempresa bajo una ruta que promete un solo slug sería mentir en el
propio path.

### Deuda declarada

Correo de comunicados (**PENDIENTE** — necesita cola y reintento) · programación
diferida (**PROPUESTA**) · lista individual de lectores (**PROPUESTA**, no MVP:
saber que once de cuarenta leyeron es gestión; saber cuáles once, por defecto, es
vigilancia) · broadcast a clientes (**PROPUESTA** — exige consentimiento,
unsubscribe y reglas de marketing) · push, WhatsApp, SMS (**PROPUESTA**) ·
adjuntos (**PROPUESTA**) · M12D evidencias (**PENDIENTE**) · wallet
(**PROPUESTA**).

---

## M12D — Evidencias fotográficas

**Estado: IMPLEMENTADO** para web interna. Migración **0064**.

### Capas

```
EvidenceImageProcessor   normaliza y comprime      (evidence_images.py)
EvidenceStorage          guarda, abre, firma, borra (evidence_storage.py)
evidence_services        el dominio                 (evidence_services.py)
evidence_views           las dos superficies        (evidence_views.py)
```

El dominio conoce cuatro verbos. No sabe que detrás hay Cloudflare.

### Storage por entorno

| Entorno | Backend | Cómo se sirve |
| --- | --- | --- |
| Producción | S3-compatible (Cloudflare R2), bucket **privado** | URL firmada, TTL 300 s |
| Desarrollo | `FileSystemStorage`, sin `base_url` | streaming autenticado |
| Tests | directorio temporal | streaming autenticado, **cero red** |

`boto3` y `django-storages` se importan **perezosamente**. Importarlos arriba
haría que `manage.py test` fallara en cualquier máquina sin ellos — que hoy es
el caso. Y si se pide `s3` sin poder construirlo, falla ruidosamente: una
configuración que dice «usa R2» y escribe en disco local en silencio parecería
funcionar hasta que alguien buscara una evidencia que nunca salió del contenedor.

### Formatos

| Entrada | Aceptada | Nota |
| --- | :---: | --- |
| JPEG, PNG, WebP | sí | Pillow 12.3.0 |
| HEIC / HEIF | sí | `pillow-heif==1.6.0`, sólo decodificación |
| SVG, PDF, ZIP, vídeo, binarios | **no** | superficie de ataque mínima |

Salida **siempre** `image/webp`. Ni el `Content-Type` ni la extensión deciden
nada: sólo si el decodificador consigue abrirlo.

### Settings

`SERVICE_EVIDENCE_MAX_UPLOAD_BYTES` 25 MB · `MAX_EDGE` 1600 · `IMAGE_QUALITY` 75
· `MIN_QUALITY` 60 · `TARGET_BYTES` 1 MB · `MAX_COMPRESSION_ATTEMPTS` 6 ·
`MAX_PIXELS` 60 M.

Ninguno nombra a Black Dog Store, y el de píxeles existe por una razón concreta:
un PNG de 20 KB puede declarar 30.000 de lado y pedir varios GB al decodificarse.
El límite de bytes no lo ve venir.

### Privacidad verificada

| Comprobación | Resultado |
| --- | --- |
| EXIF tras procesar | 5 tags → **0** |
| GPS | eliminado |
| `b'iPhone'` / `b'Apple'` en los bytes finales | ausentes |
| Orientación EXIF 6 | aplicada: 900×600 → 600×900 |
| `Cache-Control` del contenido | `private, no-store` |
| Serializador cliente | allowlist de 5 campos |

Lo de la metadata se pregunta a los **bytes**, no a la API de Pillow: un
decodificador puede exponer una vista limpia de un archivo que aún lleva la
cadena dentro, y lo que se sube a un bucket son los bytes.

### Deuda declarada

Original forense (**PROPUESTA** — exige política propia de integridad y
retención) · vídeo (**PROPUESTA**) · adjuntos generales (**PROPUESTA**) · firma
digital (**PENDIENTE**) · evidencia de garantía (**PENDIENTE**, el dominio no
existe) · retención y borrado definitivo (**PENDIENTE**) · limpieza de huérfanos
en segundo plano (**PROPUESTA**, sin Celery) · upload directo a R2
(**PROPUESTA**) · galería web del cliente (**PENDIENTE**, el portal no existe) ·
Mobile (**PENDIENTE**, no auditado en esta fase) · avisos por foto
(**PROPUESTA**, deliberadamente no implementados: una notificación por imagen
subida sería ruido).


## M12F.1 — Reconciliación del escaparate

**Lo que esta fase corrigió no es una omisión: es una contradicción.** La página
de servicios afirmaba que todos los servicios llevan seis meses de garantía, y
tanto el manual de marca v3.0 del piloto como la fila de configuración del
propio tenant dicen lo contrario — los seis meses son de los equipos seminuevos,
y la cobertura de una reparación depende del trabajo y del repuesto.

Las afirmaciones sin respaldo se retiran; las que el manual sí sostiene se
conservan con su forma: los tiempos pasan a llamarse `estimated_time_text` y la
página los rotula «Estimado:», porque el manual pide informar que pueden variar.

**Segundo hallazgo, y es de arquitectura.** La traducción de paleta de M12F
convirtió superficies de color fijo en superficies que siguen el tema, y las
declaraciones `surface="dark"` de tres bloques se quedaron atrás. El logotipo
blanco acabó sobre fondo crema. La lección se guarda como defensa: un fichero
que pinta con tokens del tema no puede declarar un contraste fijo, y un test
estructural lo comprueba.

**Tercero: el navegador encuentra lo que el código no dice.** 194 px de
desbordamiento en la portada a 320 px, celdas de 112 px útiles y un titular
partido a mitad de palabra — ninguno era visible en una aserción sobre el
código. La matriz de viewports pasa a comprobarse con Chromium de verdad.

Estado de clasificación tras la fase: tema global y contraste de logotipo
IMPLEMENTADOS; responsive del escaparate IMPLEMENTADO con navegador; responsive
del admin PARCIAL; contenido de servicios, preguntas y métricas editable por el
tenant.


## Auditoría integral de frontend

Rama `audit/frontend-complete`, sobre `feat/storefront-cms-responsive`.

**El runtime nunca sirvió otra rama.** Se verificó que el proceso de `:3000`
corre desde el mismo árbol de trabajo y que su HTML contiene los marcadores del
último commit. Lo que faltaba no eran los cambios: era su alcance. La fase
anterior se detuvo deliberadamente en la portada, así que el resto de la web
conservaba el lenguaje anterior.

### El panel llevaba puesta la ropa de la tienda

El layout raíz montaba cabecera, pie y botón de WhatsApp en TODAS las rutas. El
control interno salía con «Catálogo · Servicios · Carrito» encima y el pie de
marketing completo debajo. Se separa por ruta: `/admin` tiene su propio armazón
y es el único que debe llevar.

### Trece pantallas del panel no cargaban nunca

Productos, inventario, movimientos, transferencias, recuentos y siete más usan
`StaffGuard`, que resuelve empresa sólo por membresías. Un master de plataforma
no tiene ninguna, así que veía «No tienes permisos» en las trece. La API ya
aceptaba `?company=`; el arreglo es de frontend y no amplía autoridad, porque
el backend sigue tratando ese parámetro como untrusted.

Además, la empresa elegida vivía en un `useState` de un guard que se monta una
vez por página: se perdía al navegar.

### Colores de estado

105 textos de estado usaban el extremo claro de cada escala, herencia de cuando
sólo había tema oscuro. En claro rendían 1.02:1. Se separan dos familias:

    danger / warning / success / info    texto sobre la página — giran
    *-solid                              relleno con texto encima — no giran

Confundirlas produjo dos defectos seguidos, ambos encontrados midiendo.

### Formularios

58 etiquetas sin asociar a su control, checkout la peor con 11. El lector de
pantalla anunciaba el campo sin nombre. Lo descubrió el arnés de auditoría al
no poder iniciar sesión.

### Checkout

No mostraba nada del pedido: ni artículos, ni cantidades, ni total. Se pagaba a
ciegas.

### Estado

Escaparate y panel comparten sistema visual; 22 rutas × 2 temas sin un solo
texto por debajo de AA; sin desbordamiento en 320–1440; verificado en Chromium
y WebKit.


## Accesos de desarrollo

La tarjeta de `/auth` anunciaba seis cuentas que no existían, porque la base de
desarrollo no tenía ningún usuario y nadie había ejecutado `seed_demo_users`.

**La causa de que no se detectara importa más que el defecto.** La auditoría
anterior capturó esa pantalla 108 veces sin pulsar un solo botón, y su arnés
entraba con un usuario propio: verificó el mecanismo de login y dio por buena la
promesa de la interfaz sin comprobarla. Una captura demuestra que algo se pinta,
no que funcione.

Ahora la interfaz no puede prometer de más: pregunta a
`GET /api/dev/demo-accounts/` —sólo en desarrollo, 404 en producción— qué
cuentas existen y cuáles están activas, y no dibuja botón para las que no
sirven. Eso elimina además la segunda lista escrita a mano.

Y `seed_demo_users` reactiva al refrescar. No lo hacía, y el mensaje que
producía —«No active account found with the given credentials»— apunta al sitio
equivocado.

Los seis accesos están cubiertos por un E2E que pulsa el botón, envía el
formulario, comprueba la sesión por la interfaz y verifica destino y módulos
visibles. El limitador de 5 intentos por minuto se espera, no se desactiva.

---

## Fase C2.1 — Impuestos Perú, desglose y ticket imprimible

### La pregunta que había que responder antes de escribir nada

**¿`Product.price` incluye el impuesto o no?** De la respuesta dependía todo: si
no lo incluye, hay que sumar 18 % y todos los precios de la tienda suben; si lo
incluye, hay que extraerlo y nada cambia de precio.

No se supuso. Se leyó `checkout_services.price_checkout` y
`pos_services.calculate_pos_totals`, y ambos construyen

    subtotal = Σ(Product.price × cantidad)
    total    = subtotal − descuento

sin impuesto en ninguna parte. Ese total es exactamente lo que la pasarela
cobra hoy y lo que el cliente paga. Por tanto **el precio del catálogo es un
precio final con impuesto incluido**, y el desglose se extrae hacia atrás.

Sumarlo encima habría convertido un artículo de S/ 118 en S/ 139,24 dentro de
una migración: un cambio de precios de toda la tienda disfrazado de mejora
contable.

### El impuesto se calcula restando

    base     = total / (1 + tasa)     redondeado a céntimos
    impuesto = total − base

No es un atajo. La alternativa —calcular `base × tasa` y redondear las dos
cifras por separado— produce sumas que no cuadran: dos redondeos independientes
pueden separarse un céntimo del total. Restando, `base + impuesto = total` se
cumple por construcción, para cualquier importe y cualquier tasa.

Hay una prueba que recorre un importe de cada siete céntimos entre S/ 0,01 y
S/ 1 000 comprobando la identidad. No es una muestra: si existe un importe donde
falla, está en ese barrido.

### Por qué la tasa viaja congelada en cada venta

La **Ley N.º 32387** reparte el 18 % entre IGV e Impuesto de Promoción Municipal
de forma distinta cada año:

| Año | IGV | IPM | Total |
|---|---|---|---|
| 2026 | 15,5 % | 2,5 % | 18 % |
| 2027 | 15,0 % | 3,0 % | 18 % |
| 2028 | 14,5 % | 3,5 % | 18 % |
| 2029 | 14,0 % | 4,0 % | 18 % |

Verificado en la página oficial de orientación de SUNAT, no en un blog. El total
no se mueve, pero el reparto sí, y un documento emitido hoy tiene que seguir
diciendo lo que dijo. Eso hace obligatorio —no prudente— guardar la tasa con la
venta.

`Order` ganó seis columnas: `currency`, `subtotal_amount`, `taxable_amount`,
`tax_amount`, `tax_rate`, `tax_treatment`. `breakdown_for_order()` las **lee**;
no recalcula nunca. Sólo cae al cálculo en vivo para ventas anteriores a que el
desglose existiera, y sin escribir nada.

### Se muestra una sola línea de impuesto

Rotulada «IGV», que es lo que muestra la representación impresa peruana y lo que
el comprador reconoce. Partirla en dos columnas obligaría a redondear dos veces
—y a que la suma dejara de cuadrar— a cambio de un detalle que el documento no
necesita separar y que, además, cambia cada año.

### Una autoridad de cálculo

`store/tax_services.py` es la única función que descompone una venta. Sin
condicionales por `slug`: el piloto no es un caso especial del motor, y hay un
test que lee el código fuente del módulo y falla si aparece uno.

En el punto de venta el desglose se calcula **dentro de `calculate_pos_totals`**,
en la misma llamada que el total. La previsualización y la venta leen ese único
resultado, así que no pueden separarse ni un céntimo: si cada una llamara al
motor por su cuenta, un cambio en una de las dos llamadas dejaría de verse hasta
que un cliente reclamara.

### El escaparate no calcula el suyo

Pregunta a `POST /api/checkout/quote/`. El carrito se lee del servidor —no del
cuerpo de la petición, o un cliente se cotizaría los precios que quisiera— y del
cuerpo se aceptan sólo la clave de sesión y el cupón.

La alternativa era dividir el total entre 1,18 en el navegador, y eso habría
creado una segunda autoridad de cálculo: una en Python con `Decimal` y otra en
JavaScript con coma flotante. `0.1 + 0.2 !== 0.3` no es una curiosidad
académica; es este error exacto, servido al cliente en pantalla y contradicho por
el papel que se lleva.

Si la cotización falla, la pantalla **calla**: muestra el total, que es lo que se
va a cobrar, y no dice nada del impuesto. Mejor callar que inventar una cifra.

### Los dos documentos

| Formato | Ruta | Papel |
|---|---|---|
| A4 | `/api/admin/orders/{pk}/sales-note/pdf/` | Hoja, desglose completo |
| Ticket | `…/pdf/?formato=ticket80` | Rollo de 80 mm, altura continua |

**Sin parámetros sigue devolviendo el A4.** Hay enlaces ya escritos contra esa
ruta y añadir un formato no puede cambiar lo que devuelve la llamada de siempre.

**No se llama `?format=`.** Se comprobó empíricamente: `format` es el
`URL_FORMAT_OVERRIDE` de DRF, que negocia el renderizador antes de que la vista
corra y responde 404 a un valor que no reconoce. El nombre obvio estaba ocupado.

El ticket vive en `store/ticket_services.py`, no en un `if` dentro del A4: son
dos geometrías sin nada en común —17 cm con tablas de cuatro columnas frente a
7,2 cm de una sola columna y altura desconocida—, y compartirlas habría
significado un condicional en cada línea. Lo que sí comparten son los DATOS:
ambos leen el mismo contexto, que lee el mismo desglose congelado.

Se dibuja **dos veces**: la primera para medir, la segunda sobre una página de
exactamente esa altura. Sin eso, la impresora escupe palmos de papel en blanco
tras un ticket de dos líneas.

### La nota se crea al imprimir, no al cobrar

Decisión deliberada. Generarla dentro de la venta metería la reserva de un
correlativo interno dentro de la transacción que ya mueve stock y cobra: un fallo
al numerar tumbaría un cobro que sí ocurrió. Y gastaría un número por cada venta
que nadie llega a imprimir.

No abre la puerta a duplicados. `SalesNote` es uno-a-uno con `Order` y
`get_or_create_sales_note` es idempotente, así que imprimir cinco veces devuelve
cinco veces **la misma nota con el mismo correlativo**. La idempotencia del punto
de venta tampoco se toca: la impresión ocurre después de la venta, fuera de su
transacción y fuera de su clave.

### Lo que estos documentos NO son

Ninguno es un comprobante electrónico SUNAT. No se firma nada, no se habla con
SUNAT, no se finge una respuesta, no se marca nada como aceptado. El aviso sigue
impreso en los dos formatos y el lenguaje es **«solicitado»**, nunca «emitido».

Hay una prueba que lee los dos PDF y falla si aparece «factura emitida», «boleta
emitida», «comprobante SUNAT» o «factura/boleta electrónica».

**La emisión electrónica real es C2.2 y no está hecha.**

### Los PDF se leen, no se dan por buenos

`pdf.startswith(b'%PDF')` pasa igual si el documento sale en blanco, con el total
equivocado o con un identificador de pasarela impreso. Las pruebas descomprimen
los streams —ASCII85 sobre Flate, con `zlib` y `base64` de la biblioteca
estándar, sin añadir dependencias al proyecto— y comprueban las cifras, el aviso,
el ancho real del `MediaBox` y que la altura del ticket siga al contenido.

Un detalle que costó encontrar: `pdf_bytes.split(b'stream')` **no sirve**, porque
la palabra `endstream` contiene `stream`. El cuerpo se quedaba con un `end`
pegado detrás del terminador `~>`, la decodificación fallaba en silencio y las
aserciones buscaban su texto dentro de basura.

También se verifica que ninguno de los dos documentos contenga identificadores de
pasarela, códigos de autorización, tokens, números de tarjeta ni `payment_error`.

### Defecto corregido de paso

El nombre del PDF descargado se componía en el frontend como
`blackdog-nota-venta-…`: el nombre de **un** inquilino escrito en código
compartido de una plataforma multiempresa, así que cualquier otra empresa se
descargaba sus ventas con la marca ajena en el archivo. Ahora se usa el nombre
que manda el servidor en `Content-Disposition`, construido con el slug de la
empresa dueña del pedido y filtrado a ASCII seguro.

### Lo que encontró la revisión adversarial

Treinta y dos agentes en seis lentes independientes levantaron 26 hallazgos;
cada uno pasó por un verificador cuyo trabajo era REFUTARLO. Sobrevivieron dos.
Reviso a mano los marcados «high» porque al verificador se le instruyó descartar
ante la duda, y esa instrucción produce falsos negativos — **produjo uno**, y era
el peor de todos.

Ninguno de estos defectos lo habrían visto los tests que yo mismo escribí,
porque todos probaban el camino feliz.

**1. Cotizar gastaba el presupuesto de pagar.** `CheckoutQuoteView` nació
compartiendo `CheckoutThrottle` —el limitador de 10/min— con
`payments/create-checkout-session/`. Reproducido: doce cotizaciones y la
creación de la sesión de pago responde 429 sin llegar a ejecutarse.

Era grave por cómo funciona la pantalla: el checkout vuelve a cotizar en CADA
cambio del carrito o del cupón, así que alguien ajustando cantidades se cerraba
la compra a sí mismo. **Mirar el checkout se había convertido en un motivo para
no poder comprar.**

Arreglado con un cubo propio (`checkout_quote`, 60/min). Sigue limitado porque
recalcula precios y stock, que es trabajo real contra la base de datos.

**2. Una palabra sin espacios se salía del rollo.** El cortador de línea del
ticket sólo separaba entre palabras. Un nombre como
`MacBookProM4Max16Pulgadas1TBNegroEspacialConCargadorMagSafe140W` se dibujaba
entero: 258 pt de ancho sobre una página de 227 pt. No se recortaba con un
aviso — se salía del papel y desaparecía.

Lo mismo valía para el nombre de la empresa, que va centrado y es texto libre de
cada inquilino: al desbordar, se sale por los dos lados a la vez.

En el A4 no pasaba porque `Paragraph` parte palabras largas por su cuenta, y esa
diferencia es justo la que lo hacía difícil de ver: el mismo dato, correcto en un
documento y perdido en el otro.

Ahora `_wrap` trocea por caracteres, midiendo, cuando una palabra sola no cabe.

**3. Los botones de imprimir se bloqueaban para siempre.** Éste el verificador
lo DESCARTÓ, y se equivocó. `printSalesNoteTicket` esperaba el `onload` de un
marco oculto para llamar a imprimir; medido en navegador, con un PDF servido como
blob ese evento **no dispara**. La promesa no se resolvía nunca, así que los dos
botones se quedaban en «Preparando…» indefinidamente: veinticinco segundos
después seguían bloqueados, con el cliente delante y sin más salida que recargar
a media venta.

Lo resolvió una medición de treinta segundos, no un razonamiento. La espera está
acotada ahora (3 s), y si el marco no carga el ticket se **descarga** y la
pantalla lo dice — un botón que promete imprimir y no imprime deja al operador
mirando una impresora que no ha recibido nada.

**4. El ticket decía «Comprobante: Boleta».** El A4 rotulaba «Comprobante
solicitado:» y el ticket no, así que el mismo dato afirmaba dos cosas distintas
según el formato — y la del ticket era la falsa: ese papel no es una boleta, no
ha habido emisión ni aceptación de SUNAT.

**5. El escaparate y el mostrador redondeaban distinto.** `price_checkout` usaba
`quantize(CENTS)` a secas —media al par, el redondeo bancario— mientras el punto
de venta usa media al alza. Medido: el 40 % de los subtotales diverge para algún
porcentaje corriente de cupón, y con precios reales también (S/ 129,90 al 15 %
daba 19,48 en la web y 19,49 en la tienda).

Es anterior a C2.1, pero C2.1 lo empeoraba al **congelar e imprimir** esa cifra.
Se unificó en media al alza, que es lo que esta fase exige para todo importe.
La dirección importa y se comprobó sobre 6 667 subtotales: redondear el
DESCUENTO hacia arriba sólo puede bajar el total, así que **ningún cliente paga
más que antes** por esta corrección.

**6. El admin de Django podía descuadrar una venta cerrada.** `total` era
editable y los campos del desglose ni se mostraban. Ese formulario no pasa por
`tax_services`, así que editarlo dejaba la venta diciendo `base + impuesto !=
total` y el siguiente PDF salía contradictorio consigo mismo.

Recalcular al guardar tampoco valía: reescribiría en silencio un documento ya
entregado, que es justo lo que el congelado existe para impedir. El dinero de una
venta cerrada es ahora de sólo lectura, y el desglose se muestra para que un
descuadre se vea en vez de descubrirse al imprimir. Una corrección de importe es
una operación comercial —nota de crédito—, no una edición de fila.

**7. `money()` aceptaba floats en silencio.** `Decimal(0.1)` no vale 0,1, y
redondear ese error a céntimos lo esconde hasta que un total deja de cuadrar.
Ahora levanta `TypeError`.

**Un agente dejó basura en el repositorio**: `store/tests_dumpticket.py`, un
arnés de depuración sin aserciones que imprimía dos volcados de PDF en cada
corrida. Eliminado.

**Las dos pruebas de regresión se verificaron al revés**: se revirtió el arreglo
y se comprobó que fallan, y se restauró y se comprobó que pasan. Un test que
pasa igual con el código roto no prueba nada — es el mismo pecado que un test
que se salta en silencio.

### Deuda declarada

- **`tax_treatment` sólo emite `taxed` hoy.** El campo existe, viaja congelado y
  los documentos ya saben pintar `exempt` e `inafecta`, pero no hay superficie
  para marcarlo. Cuando un tenant venda algo exonerado hará falta esa pantalla.
- **La tasa es una constante del módulo, no configuración por empresa.**
  `resolve_tax_rate()` ya recibe la empresa, así que el día que haya
  jurisdicciones distintas no cambia quien llama — pero hoy devuelve 18 % para
  todos.
- **Las pantallas escriben `S/` fijo.** El servidor ya manda la moneda en cada
  desglose, pero el escaparate y el punto de venta la rotulan a mano. Hoy no se
  nota porque sólo hay PEN; el día que un inquilino use otra, la pantalla y el
  papel dirán cosas distintas. NO se ha medio-arreglado a propósito: usar el
  símbolo del servidor sólo en las líneas de impuesto dejaría la misma pantalla
  mezclando dos notaciones, que es exactamente el defecto que se acaba de
  corregir en el PDF. Se arregla entero o no se toca.
- **No hay serie fiscal.** El correlativo `NV-` es interno y así se rotula en los
  dos documentos. La numeración fiscal pertenece a C2.2.


---

## Fase C2.2A.1B — la superficie de la factura electrónica

### Se auditó antes de exponer, y las cinco invariantes tenían un defecto

Ninguno se veía desde las pruebas del camino feliz. Todos habrían sido reales el
día que alguien usara esto.

**1. No había guarda de ambiente.** La consulta de series no filtraba por
`environment`: una serie de producción se elegía si era la más antigua. Y no
existía ninguna configuración fiscal en el proyecto, así que «producción» estaba
a un literal de distancia. Ahora lo decide el servidor y falla cerrado; crear la
fila a mano no basta.

**2. «El id más bajo» era política tributaria.** `.filter(...).order_by('pk')
.first()` elegía la serie. El resolver filtra por empresa, ambiente y sucursal, y
**falla ante ambigüedad**: un desempate improvisado se convierte en la política
de la empresa sin que nadie la haya decidido.

**3. Un rechazo reemitía solo.** El queryset excluía `REJECTED`, así que volver a
pulsar «Emitir» gastaba otro correlativo. SUNAT considera **usado** el número de
un documento rechazado.

**4. Un 4000+ sin CDR se marcaba aceptado.** Ese código significa «aceptada con
observaciones» sólo dentro de una constancia. En un `faultstring` no prueba nada.

**5. Dos envíos simultáneos llamaban los dos.** `attempts.count() + 1` se
calculaba justo antes de la red.

### El defecto que más importaba: el descuento

Una venta de 2 × 118,00 con 18,00 de descuento producía una línea que declaraba
«cantidad 2, valor unitario 100,00, importe 184,75». Dos por cien no son 184,75:
la aritmética no cerraba y la rebaja **no aparecía en ninguna parte del
documento**. SUNAT habría recibido un precio unitario que nadie cobró.

**El XSD lo aceptaba**, porque no comprueba aritmética. Hay un test que lo
demuestra explícitamente, y existe para que nadie confunda «pasa el esquema» con
«es correcto».

Se falla cerrado. Un descuento se declara con `cac:AllowanceCharge`, y escribirlo
exige leer su semántica en la guía: inventarla sería la misma clase de error,
sólo que más difícil de ver. **FACTURA CON DESCUENTO: PENDIENTE.**

### Concurrencia, ahora con evidencia

Hasta aquí los casos concurrentes se saltaban en SQLite, donde
`select_for_update` es inocuo. Se ejecutaron contra **PostgreSQL 14.18 real**:

- ocho emisiones simultáneas → ocho correlativos distintos y consecutivos
- la misma venta dos veces a la vez → un solo documento
- dos empresas a la vez → contadores aislados
- dos envíos simultáneos → **una sola** transmisión externa

El último cuenta las llamadas al proveedor con una demora deliberada, para que la
ventana de carrera sea real y no un accidente del planificador.

### Evidencia sobre PostgreSQL, no sólo sobre SQLite

Los **131 tests fiscales** corren en verde contra PostgreSQL 14.18 real, **sin
saltarse ninguno**. En SQLite se saltan cuatro —los que necesitan bloqueo por
fila—, que es la convención del proyecto.

    PostgreSQL   Ran 131 tests · OK
    SQLite       Ran 131 tests · OK (skipped=4)

Eso es lo que convierte el contador fiscal en algo defendible: `select_for_update`
sólo significa algo donde hay bloqueo por fila.

### Cinco defectos del arnés de pruebas, todos con salto silencioso

Los E2E fiscales no funcionaron a la primera, y ninguna de las causas era del
producto:

1. `page.request` no lleva las cookies de sesión y devuelve 401.
2. El listado de pedidos no trae `receipt_type`: el filtro no encontraba nada.
3. Un contexto creado a mano no hereda `baseURL`, y el `test.skip` de la entrada
   saltaba la suite entera.
4. En Playwright `*` no cruza `/`: el glob no casaba con la barra final y la
   intercepción no se aplicaba.
5. Las pruebas responsive volvían a entrar estando ya autenticadas, donde
   `/auth` no muestra la tarjeta.

Los cinco producían **saltos silenciosos**, que es la clase de fallo que se
parece demasiado a un aprobado. Quedan escritos porque volverán a aparecer.

### Deuda declarada

- **Factura con descuento**: pendiente, y bloqueada a propósito.
- **Cobertura comercial completa**: cupones, promociones y ventas de POS no se
  han auditado una por una contra el generador.
- **Perfil de firma**: sigue apoyado en los ejemplos de la guía, no en una
  lectura del anexo. Conviene cerrarlo antes de producción.
- **Boleta, resumen diario, notas de crédito y débito, producción**: fuera de
  alcance por decisión, no por olvido.

---

## Fase H4.1 — Personal, onboarding y áreas internas

**Estado: IMPLEMENTADO.** Migración **0083_staff_invitations**, la única de la
fase. Rama `feat/h4-1-personnel-onboarding`.

### El problema que resuelve

Dar de alta a un trabajador exigía tocar `Membership`, `CompanyRole`,
`MembershipRoleAssignment` y `MembershipBranchAccess` a mano, cada uno por su
identificador. Es un trabajo de administrador de base de datos, y quien lleva
una tienda no lo es. H4.1 lo convierte en un formulario: nombre, correo, rol,
área y alcance de sucursal, todo elegido por su **nombre**.

### La invitación no concede acceso: lo hace la persona

> **El token prueba que la invitación es auténtica. No prueba quién la usa.**

Quien invita no crea la cuenta de nadie. Crea una invitación con un token que
sólo existe **hasheado** en la base de datos, se envía por correo y caduca a los
7 días. Aceptarla exige haber iniciado sesión con **ese** correo.

Esto se pidió explícitamente y se comprobó de tres maneras: prueba de backend,
prueba de navegador con una sesión de otra persona, y llamada directa a la ruta
saltándose la pantalla. Las tres responden **401**, que es la respuesta correcta:
lo que falta no es autoridad, es demostrar quién eres.

### Crear e invitar otra vez no son lo mismo

Un doble clic accidental **no** rota el token. `create_invitation` es
idempotente: si ya hay una invitación viva para ese correo, la devuelve tal cual.
Rotar el token allí habría invalidado el correo que la persona ya tenía en la
bandeja — un fallo que sólo se descubre cuando alguien no puede entrar y nadie
sabe por qué. Sólo **Reenviar**, que es una decisión explícita, genera uno nuevo.

### Lo que no se dice

El error de «esta persona ya trabaja en otra empresa» no existe. Contestarlo
convertiría el formulario en un buscador de dónde trabaja la gente. La respuesta
es la misma —y el efecto es el mismo— se conozca o no el correo de antemano.

En la pantalla pública de aceptación pasa igual: inexistente, alterada,
caducada, revocada y ya usada comparten **un solo mensaje**. Distinguirlas
diría a quien prueba tokens si acertó el formato o sólo el plazo.

### Las áreas no dan permisos

Se repite porque es fácil de romper: pertenecer a «Servicio Técnico» no concede
nada. La autoridad vive en las capacidades del rol. El área organiza, filtra y
aparece en los informes. Por eso desactivar un área con gente dentro **avisa de
lo que no pasa**: no quita roles ni permisos, sólo deja de ofrecerse.

### Defectos encontrados al cerrar la fase

Cuatro, y ninguno se veía desde las pruebas que ya estaban verdes.

**1. La pantalla de Personal giraba para siempre.** Leía
`ctx.selectedCompanyId`, que es el selector del master y vale `null` para quien
pertenece a una sola empresa — el caso normal, es decir, casi todo el mundo. Se
encontró **mirando una captura**; el smoke pasaba porque comprobaba que la ruta
renderizaba. Ahora la empresa sale de `ctx.dashboard.company.id` y hay cinco
pruebas de Jest que fallan si alguien lo devuelve atrás, comprobado quitando el
arreglo a propósito: **4 de 5 rojas**.

**2. Crear un área era imposible.** El serializador exigía `slug`, el formulario
no lo mandaba y «Crear área» respondía 400 **siempre**. La pantalla decía «No se
pudo crear el área» y no había forma de crear ninguna. El slug se deriva ahora en
el servidor con `slugify`, la misma convención de las áreas del aprovisionamiento,
con sufijo numérico si la empresa ya lo tiene tomado.

El detalle que costó encontrar: derivarlo en `create()` **no sirve**. La unicidad
de `(company, slug)` se comprueba con un validador de conjunto que exige el campo
antes de mirar ningún valor, así que se deriva en `to_internal_value`.

**3. «Desactivar acceso» aparecía en tu propia ficha.** Nadie puede desactivarse
a sí mismo —dejaría a la empresa sin quien devuelva el acceso— y el servidor ya
lo rechazaba con un 400. El botón sólo servía para llegar a ese error. El modelo
de lectura marca ahora `is_self` y la ficha explica por qué no hay botón.

**4. La página de aceptación ofrecía «Aceptar invitación» a quien no podía.**
El texto decía «crea tu cuenta con este correo» y justo debajo había un botón
principal cuyo único destino era un error. Ahora la acción depende de quién esté
conectado: si la sesión no es la del correo invitado, la página lo dice con
nombre y apellidos —«estás dentro como X, esta invitación es para Y»— y ofrece
iniciar sesión. El servidor sigue decidiendo; esto sólo evita el clic inútil.

### Cargando, vacío y sin empresa son tres cosas distintas

Y ahora se ven distintas. «Cargando personal» es que no se sabe todavía; «no hay
personal que coincida» es que la empresa está y la búsqueda no devolvió nada; y
«no hay ninguna empresa seleccionada» es que no hay desde dónde mirar. Ese estado
es real: un rol heredado entra al control interno sin empresa. Confundir los tres
fue el defecto 1.

### Verificación

- **Playwright, 13 escenarios en navegador real, sin mocks del backend**: carga
  con una sola membresía, búsqueda, filtros, alta sin escribir identificadores,
  doble alta, reenvío, revocación, aceptación, control de cuenta, token
  inventado, desactivar/reactivar, áreas con aviso de impacto, autorización y
  frontera de tenant. Más el camino del **master con selector explícito**, que es
  justo lo que el arreglo del defecto 1 podía haber roto.
- **Revisión visual a 390, 768 y 1440, en claro y oscuro**, de cinco pantallas.
  Desborde horizontal medido: **0 px en las 30 combinaciones**.
- La pantalla de Personal se lee de un vistazo en un móvil de 390: nombre,
  correo, área, roles y sucursales por su nombre, **cero identificadores**.

### Deuda registrada

- **H4.1.1 — interoperabilidad de auth web ↔ v1 interno**: las superficies
  `/api/v1/internal/<slug>/…` autentican **sólo con Bearer** y la web usa cookies
  HttpOnly, así que Servicio Técnico responde 401 desde el navegador. Reproducido
  con evidencia en [h41-servicio-tecnico-401.md](h41-servicio-tecnico-401.md). No
  se arregla aquí: añadir cookie sin más abriría CSRF en cada mutación de v1.
  Afecta a servicio, notificaciones, comunicados, anuncios y evidencias.
- **Lista de invitaciones sin paginar ni plegar**: con muchas pendientes empuja
  el personal fuera de la primera pantalla. Se ve claramente en las capturas.
- **Sin reenvío de correo real en desarrollo**: el enlace en claro sólo existe
  con `DEBUG`, que es lo correcto, pero deja el camino del correo sin probar
  end-to-end.
- **H4.2 y H4.3**: fuera de alcance por decisión.

---

## Fase H4.1.1 — Web ↔ v1 interno: autenticación y acceso real del técnico

**Estado: IMPLEMENTADO.** Sin migraciones. Rama `feat/h4-1-1-web-v1-auth-interop`,
apilada sobre H4.1. Decisión: [adr-auth-v1-internal.md](adr-auth-v1-internal.md)
(DEC-API-004).

### El problema

El panel web consume la API interna v1 —servicio técnico, evidencias,
notificaciones, comunicados— porque la regla del proyecto es una sola API por
dominio. Esa superficie aceptaba **sólo Bearer**, y la web se autentica con cookie
HttpOnly. Con la sesión válida, todo respondía 401: 51 de las 70 rutas internas
tienen consumidor web y ninguna funcionaba desde el navegador. La campana, que
vive en todo el panel, fallaba en silencio.

Y cada 401 tenía un coste escondido: disparaba un refresh que rota el token y lo
deja en lista negra, para volver a recibir 401. La base de desarrollo acumulaba
978 tokens emitidos y 695 revocados.

A eso se sumaba que un técnico **no podía descubrir** el panel: la cabecera sólo
lo ofrecía a administradores y el login llevaba siempre a la tienda.

### La decisión: una request, un canal

`V1InternalAuthentication` elige qué credencial se evalúa según lo que el cliente
**presentó**. Header y cookie a la vez es 401, sin comparar identidades; un
`Authorization` explícito —aunque sea `Basic`, esté vacío o mal formado— nunca cae
a la cookie; la cookie trae su CSRF y el Bearer no lo necesita. No reimplementa
nada: orquesta las dos clases existentes, que no cambian.

**Por qué no simplemente apilar las dos clases de DRF.** Se ejecutó antes de
decidir. Con Bearer primero, «Bearer de A + cookie de B» entraba como A y sin
CSRF; con la cookie primero, un Bearer inválido caía a la cookie.

### La condición previa: métodos seguros que no escriben

La cookie exime de CSRF a GET, HEAD y OPTIONS. Antes de activarla se ejecutaron
las 70 rutas con una orden llevada al final de su ciclo, contando SQL:
**OPTIONS en 70 rutas y GET/HEAD en 41, cero escrituras**. La primera ejecución
marcó escrituras que venían del propio arnés —acuñar el token dentro de la
medición registra el token emitido— y se corrigió el arnés, no la conclusión.
Queda como prueba permanente por los dos canales.

### Defectos encontrados por el camino

1. **Cinco pruebas fijaban el contrato viejo** («la cookie web no abre la
   superficie interna», «cada vista declara sólo Bearer»). El PASO 1 afirmó que
   no había ninguna: la búsqueda que lo sostenía no las encontró, y aparecieron al
   ejecutar la suite. Se reescribieron conservando su intención.
2. **La subida de evidencias no habría funcionado aun con la autenticación
   resuelta**: `fetchWithAuth` forzaba `Content-Type: application/json` sobre un
   `FormData`, sin boundary, y el servidor no encontraba el archivo.
3. **Tormenta de refresh**: cada 401 refrescaba por su cuenta. Ahora hay un refresh
   compartido y un único reintento; 403, 404, las rutas de autenticación y las
   peticiones con `Authorization` explícito no refrescan.
4. **`?next=` se ignoraba**: el enlace «Iniciar sesión» de la invitación de H4.1
   devolvía a la portada en vez de a la invitación.
5. **La campana y la bandeja daban por hecho lo que fallaba**: marcar como leída no
   miraba la respuesta, y un contador que dejaba de cargar conservaba el último
   número.
6. **`isStaffRole` no es el defecto que parecía.** Excluye al técnico, pero es
   espejo de los permisos legacy del backend, que tampoco lo admiten. Añadirlo
   abriría 13 páginas cuyo backend responde 403. Se corrigió el docstring del
   guard que afirmaba lo contrario y se fijó con pruebas.
7. **El arnés de Playwright confundía la 308 del proxy de Next con la respuesta
   de la API**: las rutas con barra final se redirigen y el navegador las repite.

### Acceso del técnico y del cliente

La cabecera muestra **Control interno** cuando el servidor dice que hay acceso
interno —membresía activa o master— y nunca por `user.role`. Tras el login, un
`next` local manda; sin él, el panel para quien trabaja en una empresa y la
tienda para quien no. Quien es cliente y trabajador conserva las dos cosas.

Para probarlo existe una séptima cuenta demo, `dev_customer_technician`, con
ficha de cliente y membresía de técnico. `purge_e2e_data` borra lo que crean las
pruebas de navegador —sólo lo marcado `[E2E]` o con correo en `e2e.invalid`— para
no repetir la basura que dejó H4.1.

### Verificación

| Qué | Resultado |
|---|---|
| Backend completo (SQLite) | **3982 OK**, 22 saltadas, 0 errores (1197 s) — baseline 3934 |
| H4.1.1 dirigido en PostgreSQL 14 | **120 OK, 0 saltadas** |
| Jest | **291 OK** en 22 suites — baseline 227 en 18 |
| `tsc --noEmit` | limpio |
| ESLint | 0 errores, 33 avisos — sin regresión |
| `next build` | 44/44 |
| Playwright H4.1.1 | **8/8** |
| Playwright completo | **108 OK** · 1 fallo · 7 no ejecutados · 1 omitido |
| `makemigrations --check` · `migrate --plan` | sin cambios · sin operaciones |

**El fallo y el omitido de la suite completa no son de H4.1.1**, y cada uno se repitió aislado:

- `tax-breakdown · light · 320px` es el inestable conocido de C2.1: el navegador ve el carrito vacío y no pide cotización. Aislado pasa en 1,3 s. Los 7 no ejecutados son las combinaciones que van en serie detrás. **Deuda preexistente C2.1.**
- `fiscal-invoice · dark · 1440px` se omite cuando esa pasada no encuentra un pedido pagado con factura. Aislado pasa en 1,8 s.

**Auditoría de métodos seguros:** OPTIONS en 70 rutas y GET/HEAD en 41, **0 escrituras**.

**Sabotaje** (sin commitear, archivos restaurados con hash idéntico):

| Protección retirada | Pruebas en rojo |
|---|---|
| Rechazo de doble credencial | 2 |
| Bearer inválido no cae a la cookie | 9 |
| CSRF del canal cookie | 5 |
| Puerta de tenant | 3 |

**Tormenta de refresh:** antes, cada 401 costaba una fila `OutstandingToken` y una `BlacklistedToken`. Después, cinco 401 simultáneos hacen **un** refresh, y la navegación del técnico en el navegador no hizo **ninguno**.

**Arnés de navegador.** Además de los tres fallos anotados en el ADR (la 308 del proxy, el anunciador de rutas y la carrera de la tarjeta de accesos), la suite completa destapó que añadir una séptima cuenta demo agota antes el limitador de login y el de peticiones del panel. Las pruebas esperan lo que el servidor indica en vez de desactivarlos.

### Deuda registrada

| Clave | Estado | Qué |
|---|---|---|
| **BRANCH-SCOPE-01** | **RESUELTO en H4.1.2** | Los pedidos comerciales de v1 interno no filtraban por sucursal. **Preexistente, no introducido por H4.1.1**, igual por ambos canales. Servicio técnico sí filtra, y sus pruebas siguen verdes |
| **RBAC-LEGACY-01** | **RESUELTO en H4.1.2** | `order_fulfillment_services` decidía los estados permitidos con `UserProfile.role` global, no con capacidades de empresa |
| **AUTH-REVOCATION-01** | PENDIENTE — deuda de seguridad | El access token no consulta la lista negra: tras el logout sigue válido hasta ~30 min. Evaluar antes de producción |
| **AUDIT-INTERNAL-01** | PENDIENTE | Lecturas y rechazos de v1 interno no se auditan; requiere diseño para no generar volúmenes enormes |
| **NAV-SERVICE-01** | DEFECTO / PENDIENTE | Seis entradas del menú llevan a `/admin/service` |
| **NAV-01** | DEFECTO / PENDIENTE | «Inventario › Reportes» y «Reportes › Inventario» son la misma pantalla |
| **CAT-01** | PENDIENTE | Una sola `image_url` por producto; sin subida, galería ni orden. Sin separación activo/publicado: una única bandera decide escaparate y POS |
| **LEGAL-01** | PENDIENTE | No existen `/terminos`, `/privacidad`, `/garantia`, `/preguntas-frecuentes` ni `/contacto`. Las URLs legales se editan en el panel pero no se enlazan |
| **LEGAL-02** | PENDIENTE | Sin documento legal versionado |
| **LEGAL-03** | PARCIAL | `Order` guarda dos booleanos; la garantía queda congelada en `company_snapshot`, los términos no |
| FAQ | IMPLEMENTADO como dato · PARCIAL en publicación | `StorefrontFaq` completo; sólo se pinta dentro de `/services` |
| Dirección/contacto | IMPLEMENTADO + DEFECTO | El footer lee el tenant; quedan restos del piloto en código (`DELIVERY_AREQUIPA`, categorías del footer) |
| **INV-ALERTS** | Infraestructura PARCIAL · alertas PENDIENTE | Riesgo calculado en cada GET y no persistido; sin eventos de inventario ni estado de alerta; `safety_stock` y `lead_time_days` sin escritura por API |
| INV-SERIAL, SVC-EVIDENCE-UX, NOTIFY-RT, FISCAL-SERVICE | PENDIENTE / PARCIAL | Sin cambios en esta fase |
| H4.2, H4.3 | PENDIENTE | Fuera de alcance por decisión |
| Fiscal | PENDIENTE | Factura con descuento, boleta y resumen diario, producción SUNAT |
| Proxy 308 | OBSERVACIÓN | Toda llamada del navegador con barra final hace una redirección 308 en Next antes de llegar a Django: un viaje de ida y vuelta extra por petición. Preexistente |

---

## Fase H4.1.2 — Autoridad interna: pedidos por sucursal y RBAC por capability

**Estado: IMPLEMENTADO.** Sin migraciones. Rama
`feat/h4-1-2-internal-authority-hardening`, apilada sobre H4.1.1. Cierra
**BRANCH-SCOPE-01** y **RBAC-LEGACY-01** con las decisiones D1–D4. Servicio técnico
y trade-in se registran al final de esta sección; en esta rama no se implementan.

### El problema

**BRANCH-SCOPE-01 era más ancho de lo registrado.** Todas las superficies de pedidos
comerciales filtraban sólo por empresa. Un miembro limitado a la sucursal A podía,
con los pedidos de B y por la web o por la app:

- listarlos, abrirlos e imprimir su recibo;
- reenviar sus correos y mover su despacho;
- emitir su nota de venta y su factura, esta con la serie de B.

Además veía en el panel los ingresos de toda la empresa. La analítica comercial y
los KPIs de inventario sí filtraban por sucursal, así que el dashboard mezclaba en
una misma pantalla cifras con alcance y cifras sin él.

**RBAC-LEGACY-01.** `allowed_fulfillment_statuses(user)` decidía con el
`UserProfile.role` global también dentro de una empresa SaaS. Quien tenía perfil
«inventory» y un rol de empresa con `sales.orders.manage` no podía cancelar. El
panel web, además, llevaba su propia copia de esa regla, atada al mismo rol.

### La frontera: `visible_orders(user, company)`

Es una función de `tenancy.py`. Todas las superficies parten de ella **antes** de
buscar, contar, agregar o paginar. Fuera de alcance la respuesta es **404**, la
misma que para un pedido inexistente.

| Quién | Qué pedidos de la empresa ve |
|---|---|
| Master con empresa explícita | todos, también los que no tienen sucursal y los de sucursales cerradas |
| Puente legacy (sólo el piloto) | todos los del piloto, también los que no tienen sucursal |
| Membresía `ALL` | todos, también los que no tienen sucursal y los de sucursales cerradas |
| Membresía `SELECTED` | sólo los despachados por una sucursal **activa** con concesión **activa** |
| Cualquiera, sobre otra empresa | ninguno |

**Pedidos que ninguna sucursal puede responder (D1).** Un pedido sin
`fulfillment_branch` —historial anterior a la migración 0025— o de una sucursal
desactivada no pertenece a ninguna sucursal que opere un miembro `SELECTED`, así
que ese miembro no lo ve.

- **No se amplió `visible_branches()`.** Responde dónde trabaja hoy una persona, no
  qué historial hereda.
- **No se hizo backfill** ni se inventó ninguna sucursal.

**Una sola escalera de autoridad.** La decisión «master / puente / ALL / SELECTED /
nada» vivía dentro de `visible_branches()`. Se extrajo a `_branch_authority()` y la
leen las dos funciones, así que sucursales y pedidos no pueden discrepar sobre
quién tiene alcance de toda la empresa. `visible_branches()` se comporta igual que
antes, y su batería de la Fase 2D sigue verde en SQLite y en PostgreSQL.

### Superficies protegidas

| Superficie | Antes | Ahora |
|---|---|---|
| v1: lista, detalle y despacho | empresa | `visible_orders`; 404 fuera de alcance |
| Web: lista, detalle y despacho | empresa | ídem |
| Recibo PDF y reenvío de correo | empresa | ídem; fuera de alcance no se envía nada |
| Nota de venta: GET, POST y PDF | empresa, «deliberadamente sin sucursal» | ídem; no se gasta correlativo |
| Comprobante por pedido: GET y POST | empresa | ídem; no se tocan serie ni número |
| Comprobante por id: enviar, XML, CDR y PDF | empresa | el comprobante hereda el alcance de su pedido (`order__in`) |
| KPIs de ventas del dashboard | empresa | todas las cifras derivan de una única base con alcance |
| Ficha de cliente (CRM): historial y totales | empresa | la ficha no se fragmenta; su historial sí tiene alcance |

**La nota de venta cambió de criterio a propósito.** Su docstring decía que la
sucursal no aplicaba porque es «papel sobre una venta». Pero la nota **lleva** la
venta —cliente, documento, líneas e importes—, y quien no puede abrir el pedido
tampoco debe leer ni emitir su nota.

**La ficha de cliente es el «lookup equivalente» que D2 pedía buscar.** No aparecía
en la auditoría y mostraba, a quien tiene `service.customers.view`, los pedidos e
importes de todas las sucursales. El cliente sigue siendo de toda la empresa; lo
que compró en cada sucursal, no.

**Los 404 hablan igual.** El detalle y el despacho respondían con el texto por
defecto de Django, «No Order matches the given query.», que además nombra el
modelo. Ahora responden «Orden no encontrada.», como ya lo hacían el recibo y el
reenvío; en v1, «No encontrado.», como la puerta de empresa.

Ya estaban bien y no se tocaron:

- la analítica comercial;
- más vendidos y comisiones;
- el Kardex filtrado por pedido;
- la creación de ventas del POS.

### RBAC: en SaaS decide la capability (D4 · opción A)

`allowed_fulfillment_statuses(user, company)`:

| Camino | Regla |
|---|---|
| Membresía o master | Con `sales.orders.manage` en esa empresa, los siete estados; sin ella, ninguno. El rol global no se consulta |
| Puente legacy | La regla histórica, intacta: `inventory` mueve mercancía (4 estados); ventas y administración, todos |

- **Quien sólo tiene `sales.orders.view` recibe una lista vacía.** Antes recibía los
  siete estados, y el PATCH los rechazaba.
- **El detalle web devuelve `available_fulfillment_transitions`**, igual que v1, y
  `FulfillmentStatusSelect` pinta esa lista. El componente ya no recibe al usuario,
  así que no tiene de dónde leer un rol.
- **Se borró `admin_views._INVENTORY_ALLOWED_FULFILLMENT`**, una segunda copia de la
  regla que nadie usaba.
- **No se creó `sales.orders.fulfill` ni ninguna otra capability.**

**Cuatro pruebas cambiaron a propósito**, con la decisión escrita en su docstring:

- `M6InternalFulfillmentTest.test_an_INVENTORY_role_is_limited_to_moving_goods` pasa
  a llamarse `test_a_global_INVENTORY_role_does_not_narrow_what_the_company_granted`
  y espera 200 al cancelar.
- `test_it_reports_the_transitions_that_actor_may_use` espera `[]`.
- `M12BCommerceEventsTest.test_28` y `test_29` movían el despacho a través del
  servicio con el propio comprador como actor. Funcionaba porque la regla vieja
  dejaba fijar cualquier estado a todo rol que no fuera inventory, clientes
  incluidos. Ahora el actor es una vendedora del piloto. Lo que prueban —que el
  cliente reciba el aviso— no cambia.

Las de `Phase33FulfillmentStatusChangeTest`, que cubren el puente legacy, siguen
verdes sin tocarlas.

### Búsqueda estructural

Apariciones de `Order.objects`, `company.orders` y `get_object_or_404(Order` en
código de producción, después de implementar:

| Dónde | Clase |
|---|---|
| `tenancy.visible_orders` | INTERNAL SCOPED — es la frontera |
| `fiscal_views._fiscal_document` | INTERNAL SCOPED — `order__in=visible_orders` |
| `sales_analytics_views._paid_orders`; `OrderItem` en más vendidos y analítica; `SalesCommission` en comisiones | INTERNAL SCOPED — por sucursales visibles, desde antes |
| `tenancy.storefront_orders`, `tenancy.customer_owned_orders`, `v1_checkout_views` (idempotencia: empresa + `user=request.user` + clave) | PÚBLICO / CUSTOMER OWNERSHIP |
| `admin_views`: `email_send_error` del reenvío y relectura tras el PATCH; `v1_internal_views`: relectura tras el PATCH | SEGURO — el mismo pk que `visible_orders` acaba de resolver |
| `email_services`, `sales_note_services` (bloqueo), `fiscal_services`, `ticket_services`, `inventory_services.record_sale_stock_movements` | SEGURO — reciben un pedido ya resuelto por quien los llama |
| `views._confirm` (webhook de pago) | SEGURO — el pk sale de un `PaymentAttempt` del servidor |
| `pos_services._existing_for_key` | SEGURO — clave de idempotencia secreta, dentro de la empresa; ver POS-IDEMP-409 |
| `models.assert_all_match_company`, `checkout_services.create_pending_order`, `sequences` | SEGURO — invariante, creación y numeración |

**No quedó ningún acceso interno por id sin frontera.** La bitácora de auditoría
también contiene entradas de pedidos, pero no es una búsqueda de pedidos: es un
control de empresa protegido por `memberships.view`. Queda anotada como
AUDIT-BRANCH-SCOPE-01.

### Lo que encontró la suite completa

La primera ejecución completa dio **4019 pruebas: 1 fallo y 2 errores**. Las
suites dirigidas, que no incluían esas clases, estaban en verde.

1. **Las dos de M12B descritas arriba.** El servicio de despacho ya no acepta a un
   cliente como actor. Se corrigió el fixture, no el servicio: relajar el servicio
   para que las pruebas pasaran habría sido reintroducir RBAC-LEGACY-01.
2. **La matriz de paridad se protegió a sí misma.** Al anotar el alcance de
   sucursal escribí `tenancy.visible_orders` y «(H4.1.2)» dentro de la columna
   *Capability*, y `Ip1ParityManifestTest` exige que todo lo que aparece en esa
   columna sea una capability del catálogo. La nota pasó a un párrafo bajo la
   tabla y las filas volvieron a su forma original.
3. **Una prueba inestable de H4.1, no de esta fase.** La segunda ejecución
   completa dio 4019 pruebas con **1 fallo**:
   `H41AcceptEndpointTest.test_every_bad_token_answers_the_same`, caso «alterado».
   - **Causa.** La prueba altera el token con `self.raw[:-1] + 'z'`, y el token sale
     de `secrets.token_urlsafe(48)`. Cuando ya termina en `z` —una vez de cada 64—
     el token «alterado» es el correcto y el endpoint responde 200.
   - **Evidencia.** Forzando el final del token, falla siempre si acaba en `z` y
     nunca si acaba en `A`. Aislada falló en 1 de 5 pasadas.
   - **Decisión.** No se corrige aquí, por alcance. Queda como TEST-H41-TOKEN-FLAKY.

### Verificación

| Qué | Resultado |
|---|---|
| Batería H4.1.2 + `M6InternalFulfillmentTest` + `Phase33FulfillmentStatusChangeTest` | **67 OK** |
| Dirigido en SQLite: pedidos web y v1, despacho, notas, fiscal, dashboard, clientes, Fases 2B–2E, M6–M8, Phase33 y 60, C22, H4.1.1 y H4.1.2 | **1030 OK**, 6 saltadas |
| Dirigido en PostgreSQL: H4.1.2, despacho, M6, H4.1.1, M8 y Fase 2D | **310 OK** |
| Backend completo (SQLite) | **4019 OK**, 22 saltadas, 0 errores (1414 s) — baseline 3982, +37 de H4.1.2. Antes hubo dos ejecuciones con fallos: la primera, 1 fallo y 2 errores, corregidos; la segunda, 1 fallo de TEST-H41-TOKEN-FLAKY. Ver arriba |
| Jest | **294 OK** en 23 suites — baseline: 291 en 22 |
| `tsc --noEmit` | limpio |
| ESLint | 0 errores y 33 avisos, sin regresión |
| `next build` | 44/44 |
| Playwright dirigido: `demo-accounts`, `fiscal-invoice`, `h411-auth-interop` y `pos-ticket` | **24 OK** · 2 omitidos |
| `makemigrations --check` · `migrate --plan` | sin cambios · sin operaciones |

**Los 2 omitidos no son de H4.1.2.** Son `fiscal-invoice · dark · 390px` y
`dark · 1440px`: el arnés agota el limitador `admin_orders` mientras busca el
pedido con factura y se salta la prueba. Ejecutados aislados pasan (1,9 s y
1,8 s). Queda registrado como E2E-FISCAL-THROTTLE.

**En desarrollo, las cuentas demo no pierden visibilidad.** Sus siete membresías
son `ALL`, y de los 26 pedidos que había al medir ninguno estaba sin sucursal ni en
una sucursal cerrada. `dev_inventory`, `dev_technician` y
`dev_customer_technician` reciben ahora 0 transiciones porque no tienen
`sales.orders.manage`; el PATCH ya les respondía 403.

### Sabotaje

Cada protección se retiró por separado y se ejecutó la batería de 67 pruebas.
Después, el archivo se restauró byte a byte con hash verificado. Nada se commiteó.

| Protección retirada | Pruebas en rojo |
|---|---|
| A · frontera del detalle, web y v1 | 5 (11 fallos y 1 error contando subtests) |
| B1 · frontera del recibo PDF | 1 |
| B2 · frontera fiscal, por pedido y por id | 2 |
| C · base con alcance de los KPIs del dashboard | 1 |
| D · SaaS decidido otra vez por `UserProfile.role` | 6 |

### Servicio técnico — registrado, no implementado

Auditoría del PASO 1, confirmada. En H4.1.2 no se toca nada de esto.

- **SVC-INTAKE-WEB — PENDIENTE.** El backend ya crea la orden (`POST
  service/orders/`), registra el equipo (`POST service/devices/`) y busca clientes
  (`GET service/customers/`). La web no tiene «Nueva orden de servicio»:
  `service-console.ts` no exporta ninguna de esas tres llamadas.
  - Flujo a construir: cliente → equipo → sucursal → falla → condición física →
    accesorios → evidencias → confirmación → constancia.
  - Servicio técnico no depende del POS.
- **Responsabilidad del técnico (regla de producto).**
  - Puede crear una orden si tiene `service.orders.create`. Es configurable por
    empresa: no significa que todo técnico pueda, ni que deba usar el POS.
  - Registra los hechos que ejecuta: diagnóstico, inicio y fin de reparación,
    trabajo realizado, repuestos, QC y entrega cuando correspondan, y evidencias.
  - No puede fabricar un estado sin el evento que lo respalda. Los estados
    sólo-evento de `service_services` ya lo imponen.
- **SVC-CAP-SPLIT — PROPUESTA PRIORITARIA.**
  - `service.orders.manage` mezcla asignar, reasignar, desasignar, la transición
    genérica y cancelar, y el preset Servicio Técnico la incluye.
  - Hay que separar «registro mi trabajo» de «administro el taller». Nombres
    orientativos: `service.assignments.manage` y `service.orders.cancel`, con el
    inicio de diagnóstico en `service.diagnostic.manage`. Los nombres finales salen
    del catálogo real.
  - Requiere catálogo, presets y migración, así que va en una fase explícita.
- **SVC-ASSIGNEE-01 — DEFECTO.**
  - `eligible_technicians(company)` admite a cualquier usuario con membresía
    activa, así que alguien de Ventas o de Inventario puede aparecer como técnico
    asignable.
  - No es una escalada, porque los endpoints siguen comprobando capabilities. Sí es
    una inconsistencia operativa y de trazabilidad.
  - No se arregla aquí. Se resuelve con SVC-CAP-SPLIT, con una regla por
    capabilities y nunca por el nombre del rol.
- **Cliente nuevo en recepción.**
  - El técnico estándar tiene `service.customers.view` y no `manage`: abre órdenes
    para clientes existentes pero no da de alta a clientes nuevos.
  - **No se le concede `manage` automáticamente.**
  - Opciones a decidir en SVC-OPS, con preferencia por el mínimo privilegio: (A) lo
    da de alta recepción o ventas; (B) la empresa concede `manage` al técnico; (C)
    una capability de alta más estrecha en una fase futura, si el código y los
    casos reales la justifican.
- **SVC-QUOTE-INSHOP — PENDIENTE.**
  - La decisión sobre una cotización sólo se registra desde la superficie del
    cliente.
  - Hace falta que un empleado autorizado registre la de un cliente presencial, por
    teléfono o por WhatsApp: revisión exacta, decisión, canal, importe congelado,
    quién la registró, cuándo y constancia.
  - Nunca «el técnico marca aprobado» sin constancia de quién tomó la decisión.
- **SVC-QC-SEGREGATION — PROPUESTA.** Hoy quien repara puede hacer el QC, algo
  válido en un taller pequeño. A futuro podrá exigirse que lo haga otra persona
  (`checked_by` distinto de quien reparó), configurable por empresa y por tipo de
  reparación; nunca como regla universal.
- **Pago y entrega: separación de responsabilidades, no defecto.**
  - El técnico estándar entrega y no cobra; ventas cobra.
  - Una empresa que quiera un técnico con caja concede la capability por RBAC.
  - El preset global no se amplía sin una decisión de producto.

### Trade-in — registrado, no implementado

**TRADE-IN — PENDIENTE.** No existe en backend, frontend ni migraciones. No se
mezcla con la orden de reparación, y el valor del equipo entregado **no** es un
`discount_amount`.

**La regla central.** Una venta de S/ 3500 con un equipo entregado valorado en
S/ 1200 se registra así:

| Concepto | Importe |
|---|---|
| Precio comercial | 3500 |
| Adquisición del equipo usado | 1200 |
| Crédito aplicado a la liquidación | 1200 |
| Saldo monetario | 2300 |

Nunca «precio = 2300» ni «descuento = 1200». Tratarlo como descuento tendría cuatro
efectos:

- recortaría la comisión, que se calcula sobre subtotal menos descuento;
- falsearía el desglose tributario;
- imprimiría «Descuento» en el ticket;
- ocultaría que la empresa adquirió un activo.

**Dominio de referencia:**

- `TradeInCase`.
- `TradeInDevice`.
- `TradeInInspection`.
- `TradeInValuation`: revisión, importe, moneda, evaluador, validez y notas.
- `TradeInDecision` / `OfferAcceptance`.
- `TradeInEvidence`: modelo propio sobre el almacenamiento común, sin reutilizar
  `RepairEvidence`.

La valoración es **manual**: el sistema registra y controla, pero no calcula el valor.

- **Inspección:**
  - Plantillas por empresa y por tipo de dispositivo. Para un teléfono pueden
    incluir, si aplica: serial/IMEI, capacidad, color, estado físico, pantalla,
    cámaras, micrófonos y altavoces, puertos, biometría, batería, conectividad,
    estado de activación, accesorios y evidencias.
  - Ningún checklist «iPhone» fijado en el núcleo del SaaS.
  - No se guardan PIN ni contraseñas.
- **TRADEIN-OWNERSHIP — PROPUESTA PRIORITARIA.**
  - Antes de aceptar el equipo físicamente se registran: identidad del cliente
    según la política, declaración de propiedad y origen, serial/IMEI, constancia
    de entrega, observaciones y evidencias.
  - Consultar una lista negra externa de IMEI sería una integración posterior; no
    se da por existente.
- **Valoraciones versionadas, nunca sobrescritas.** Bajar de 1200 a 1050 crea una
  revisión nueva que conserva ambas cifras, quién, cuándo y por qué. El cliente
  acepta una revisión concreta.

**Preguntas de negocio que deben decidirse ANTES de TRADEIN-POS:**

| | Pregunta |
|---|---|
| A | ¿Se aceptan varios equipos como parte de pago de una misma venta? |
| B | ¿Qué pasa si el valor del trade-in supera el total de la compra? Opciones: no se permite, crédito a favor o devolución de dinero. No se asume ninguna |
| C | ¿Puede aplicarse una valoración parcialmente? |
| D | ¿Puede usarse una valoración en más de una venta? Preferencia inicial: no; una valoración aceptada se consume una sola vez |
| E | ¿Cuánto dura la oferta? |
| F | ¿Los importes a partir de cierto monto requieren aprobación de un supervisor? |
| G | ¿Qué ocurre si el cliente acepta y luego se anula la compra? |
| H | ¿Qué pasa con el equipo que ya quedó en custodia física? |

**Lo que trade-in necesita alrededor:**

- **INV-SERIAL — PENDIENTE.**
  - Un trade-in aceptado no suma +1 a un producto agregado. Hace falta una unidad
    serializada: `SerializedStockUnit` o equivalente.
  - Campos: empresa, sucursal, producto, serial, IMEI, condición, grado, origen
    (compra, trade-in, devolución…), costo de adquisición y estado.
  - Estados orientativos: pendiente de QC, reacondicionamiento, lista para venta,
    reservada, vendida, cuarentena y retirada. Los nombres finales se fijan tras
    auditarlo.
- **Un `Device` no es inventario.**
  - `Device` es el equipo de un cliente en servicio técnico; la unidad serializada
    es propiedad de la empresa.
  - Ni `Device` ni `TradeInDevice` funcionan como stock. El flujo es:
    `TradeInDevice` aceptado → unidad serializada.
- **REFURB-UNIT — PROPUESTA.**
  - Un equipo recibido puede necesitar batería, pantalla, limpieza, reparación y QC
    antes de venderse.
  - No se crea un cliente ficticio para registrarlo como `RepairOrder`.
  - Se evaluará un flujo de reacondicionamiento de la unidad, o reutilizar piezas
    del motor técnico sin mezclar la propiedad del cliente con la de la empresa.
- **SALE-TENDER-LEDGER — PENDIENTE.**
  - Una venta puede liquidarse con crédito de trade-in (1200), tarjeta (2000) y
    efectivo (300). `Order.payment_method` admite un solo medio y no alcanza.
  - Diseño posible: `OrderSettlement` con `Tender[]` (efectivo, tarjeta,
    transferencia, crédito de trade-in…).
- **TRADEIN-REVERSAL — PENDIENTE.**
  - Anular una venta con trade-in no es reembolsar el pedido: hay dos activos, el
    producto que salió y el equipo que entró.
  - La política debe definir si el equipo se devuelve, qué pasa si ya se
    reacondicionó o se vendió, cómo se revierte el crédito y cómo queda la
    liquidación.
- **TRADEIN-MARGIN — PROPUESTA.**
  - Costo de adquisición 1200 + acondicionamiento 180 = costo total 1380. Venta
    1850. Margen bruto 470.
  - Sin mezclar costo, precio y crédito comercial.
- **FISCAL-TRADEIN — BLOQUEADO** hasta que haya una definición contable y
  tributaria.
  - Falta decidir cómo se documenta la adquisición del equipo usado, cómo se
    representa el crédito, qué importe va en el comprobante de la venta y qué
    documentos adicionales corresponden.
  - El motor fiscal no se toca.

### Orden recomendado de fases

1. **H4.1.2** — alcance de sucursal y RBAC legacy (esta fase).
2. **SVC-OPS-01** — operación real del taller:
   - nueva orden en la web, recepción guiada y constancia;
   - SVC-CAP-SPLIT y SVC-ASSIGNEE-01;
   - aprobación presencial o telefónica;
   - cadena de custodia y requisitos mínimos por etapa.
3. **INV-SERIAL-01** — unidad serializada individual.
4. **TRADEIN-01** — caso, inspección, valoración, oferta, aceptación y evidencias.
5. **SALE-TENDER-01** — liquidación con varios medios de pago.
6. **TRADEIN-02** — aceptación física → stock serializado → crédito en el POS.
7. **CAT-01** — imágenes, publicación y catálogo para productos nuevos y seminuevos.

Siguen pendientes LEGAL-01/02/03, INV-ALERTS, NAV-01, NAV-SERVICE-01, H4.2, H4.3,
NOTIFY-RT, FISCAL-SERVICE y la deuda fiscal.

### Deuda registrada

| Clave | Estado | Qué |
|---|---|---|
| **BRANCH-SCOPE-01** | RESUELTO | Ver arriba |
| **RBAC-LEGACY-01** | RESUELTO en el backend y en el selector de despacho | El resto de la interfaz queda en RBAC-LEGACY-UI-01 |
| **RBAC-LEGACY-UI-01** | PENDIENTE · nuevo | La web todavía decide por `user.role` si muestra «Reenviar email» (`canResendEmail`), el panel de nota de venta (`canManageSalesNotes`) y las páginas de `isStaffRole`. El servidor autoriza bien, pero la interfaz puede esconder la acción a quien tiene la capability o mostrarla a quien recibirá 403 |
| **DASH-SCOPE-LABEL** | PENDIENTE · nuevo | El bloque de ventas del dashboard ya tiene alcance, pero no declara qué sucursales cubre; el de inventario sí (`scope`) |
| **CRM-HISTORY-CAP** | OBSERVACIÓN · nueva | La ficha de cliente muestra historial e importes con `service.customers.view`, sin exigir `sales.orders.view`. Ya tiene alcance de sucursal; la pregunta de capacidad sigue abierta |
| **AUDIT-BRANCH-SCOPE-01** | OBSERVACIÓN · nueva | La bitácora de auditoría es de empresa (`memberships.view`) e incluye entradas de pedidos de todas las sucursales, con el correo del cliente en los metadatos |
| **POS-IDEMP-409** | OBSERVACIÓN · nueva | Repetir una clave de idempotencia del POS con otra cesta responde 409 con el id del pedido existente. La clave es secreta y la genera el dispositivo |
| **ORDER-BACKFILL-01** | PENDIENTE | Pedidos sin `fulfillment_branch`: 0 en desarrollo; producción sin medir. Backfill sólo en una fase separada y con certeza sobre la sucursal histórica |
| **E2E-FISCAL-THROTTLE** | DEFECTO del arnés · preexistente | `fiscal-invoice.spec.ts` busca en cada prueba el pedido con factura abriendo en serie el detalle de cada pedido pagado. Nueve pruebas seguidas superan el limitador `admin_orders` (120/min): las últimas reciben 429, no encuentran pedido y se **omiten en silencio**. Cada venta que crea `pos-ticket` añade una petición por búsqueda: con 26 pedidos se omitía una prueba y con 27, dos. Aisladas pasan. Arreglo en el arnés (buscar una vez y reutilizar el id), no en el limitador |
| **TEST-H41-TOKEN-FLAKY** | DEFECTO del arnés · preexistente (H4.1) | `H41AcceptEndpointTest.test_every_bad_token_answers_the_same` construye el token «alterado» sustituyendo el último carácter por `z`. Si el token aleatorio ya acababa en `z` (1 de cada 64 veces), no hay alteración y el endpoint responde 200. Reproducido forzando el final del token. Arreglo: sustituir por un carácter distinto del último |
| **SVC-INTAKE-WEB** | PENDIENTE | Nueva orden de servicio en la web |
| **SVC-CAP-SPLIT** | PROPUESTA PRIORITARIA | Separar `service.orders.manage` |
| **SVC-ASSIGNEE-01** | DEFECTO · nuevo | Cualquier miembro activo es técnico asignable |
| **SVC-QUOTE-INSHOP** | PENDIENTE | Aprobación del cliente registrada por el personal |
| **SVC-QC-SEGREGATION** | PROPUESTA | QC por otra persona, configurable |
| SVC-DELIVERY-ID · SVC-PART-RESERVE · SVC-SLA · SVC-CUSTODY · SVC-STAGE-TEMPLATES | PROPUESTA | Del PASO 1 |
| **TRADE-IN** | PENDIENTE | Sin implementación |
| **TRADEIN-OWNERSHIP** | PROPUESTA PRIORITARIA | Propiedad y origen antes de aceptar |
| **INV-SERIAL** · PRODUCT-CONDITION | PENDIENTE | Unidad serializada y condición por unidad |
| **REFURB-UNIT** | PROPUESTA · nueva | Reacondicionamiento de una unidad de la empresa |
| **SALE-TENDER-LEDGER** | PENDIENTE | Liquidación con varios medios de pago |
| **TRADEIN-REVERSAL** | PENDIENTE · nueva | Anulación con dos activos |
| **TRADEIN-MARGIN** · TRADEIN-QC · WARRANTY-SERIAL | PROPUESTA | — |
| **FISCAL-TRADEIN** | BLOQUEADO | Pendiente de definición contable y tributaria |
| SALES-FULFILL-CAP | DESCARTADA | Opción B de D4: no se crea capability de despacho |
| AUTH-REVOCATION-01 · AUDIT-INTERNAL-01 · NAV-01 · NAV-SERVICE-01 · CAT-01 · LEGAL-01/02/03 · INV-ALERTS · NOTIFY-RT · FISCAL-SERVICE · H4.2 · H4.3 · fiscal | Sin cambios | Ver H4.1.1 |
| Inestable C2.1 (`tax-breakdown`) | PREEXISTENTE | Sin cambios |
