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
- Integración inicial con Stripe Checkout.
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
- Stripe SDK.
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
- Creación de sesiones de Stripe Checkout.
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
| `/api/payments/create-checkout-session/` | POST | Crear orden y sesión Stripe |
| `/api/payments/webhook/` | POST | Recibir eventos Stripe |

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

Después de eso, `CreateCheckoutSessionView` intenta comunicarse con Stripe.

Si Stripe falla o el cliente cancela:

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

No se consulta el estado de la orden ni la sesión de Stripe.

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

- Stripe Checkout.
- Stripe webhook.
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
`stripe_session_id`.

### Deuda registrada

- **`OrderSerializer` legacy expone `stripe_session_id`.** Fuera del alcance de
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
  ambos llegar a Stripe; el stock definitivo se resuelve en el webhook, como ya
  ocurría. No se cambió la semántica de inventario en esta fase.
- **`stripe_session_id` es `unique=True`**, lo que es correcto, y conviene
  recordarlo al escribir tests con mocks: dos pedidos no pueden compartir id.
- **URL de checkout no persistida.** En un replay se recupera de Stripe; si la
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
| DRF, SimpleJWT, Stripe, gunicorn, reportlab, psycopg2, cors-headers, django-environ | sin advisories | ninguna |

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
