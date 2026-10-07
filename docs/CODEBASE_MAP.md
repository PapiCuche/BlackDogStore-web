# Mapa del código

Dónde vive cada cosa. No lista todos los archivos: para un símbolo, `rg`. Los dominios de backend con
sus funciones y tests están en `docs/AUDIT_MEMORY.md` §4 y §8; aquí va la estructura y lo que allí falta.

Backend: `backend/store/` (una sola app Django). Rutas: `urls.py` (`/api/`) y `v1_urls.py` (`/api/v1/`).
Frontend: `frontend/app/` (Next.js 16, app router). Tests: `backend/store/tests.py` + `test_*.py`,
`frontend/__tests__/`, `frontend/e2e/`.

## Backend

| Módulo | Archivos | Contrato | Frontera de seguridad |
|---|---|---|---|
| Tenancy / RBAC | `tenancy.py`, `capabilities.py`, `permissions.py`, `tenant_views.py`, `access_views.py`, `staff_views.py`, `company_provisioning.py` | `/api/admin/{companies,branches,memberships,roles}/`, invitaciones de personal | Empresa por membresía; capacidades; alta directa de membresía sólo plataforma |
| Auth | `auth_views.py`, `authentication.py`, `v1_auth_views.py`, `token_revocation.py`, `throttles.py`, `security_log.py`, `client_ip.py` | `/api/auth/*` (cookies), `/api/v1/auth/*` (cuerpo) | HttpOnly + CSRF; límites por IP real; contraseña nunca en logs |
| Catálogo y pedidos | `views.py`, `admin_views.py`, `serializers.py`, `checkout_services.py`, `checkout_quote_views.py`, `order_fulfillment_services.py`, `tax_services.py` | `/api/products`, `/api/categories`, `/api/cart`, `/api/checkout/*`, `/api/admin/{products,categories,orders}/` | Precio, stock e impuestos sólo en servidor; idempotencia de pago |
| Inventario | `inventory_services.py`, `inventory_views.py`, `v1_inventory_views.py`, `v1_transfer_views.py`, `import_*`, `stock_import_services.py`, `xlsx_reader.py` | ajustes, Kardex, transferencias, recuentos, importación | `select_for_update`; sucursal por alcance |
| POS / promociones | `pos_services.py`, `pos_views.py`, `v1_pos_views.py`, `promotion_*`, `sales_note_services.py`, `ticket_services.py` | `/api/v1/internal/<slug>/sales/pos/*` | Idempotencia por empresa |
| Servicio técnico | `service_services.py`, `v1_service_views.py`, `evidence_*` | `/api/v1/internal/<slug>/service/*`, `…/service/orders/<id>/evidence/` (lista, subir, nota, compartir, anular) | Evidencias privadas, por etapa y sucursal; se anulan, no se borran; asignar ≠ cobrar |
| Escaparate (CMS) | `storefront_content_services.py`, `storefront_content_views.py`, `company_settings.py`, `settings_views.py` | `/api/storefront/config/`, `/api/admin/storefront/{page,campaigns,<kind>}/` | `company.manage`; publicar es acción propia |
| Imágenes de producto | `product_media.py`, `product_media_views.py`, modelo `ProductImage` | `/api/admin/products/<id>/images/` (lista, subir, editar, quitar, `order/`); `images` en el producto público | `products.manage`; los píxeles son un `StorefrontImage`; `Product.image_url` = principal |
| Carga masiva con imágenes | `import_media.py` (lote y ZIP), `import_services.py` | `images` e `images_zip` en `/api/admin/products/import/preview/` | ZIP sin extraer; imágenes en espera = sin colocar, de la empresa del trabajo |
| Imágenes de la tienda | `storefront_media.py`, `storefront_media_views.py`, modelo `StorefrontImage` | `POST /api/admin/storefront/images/`, `GET /api/storefront/images/<id>` | Públicas; el decodificador decide el tipo; comparten almacén con evidencias, no autorización |
| Pagos (Izipay) | `payments/izipay.py`, `checkout_services.py`, `views.py` (`IzipayNotificationView`), `payments/fake_izipay.py` (sólo pruebas) | `/api/payments/create-checkout-session/`, `/api/payments/izipay/notification/` | Sólo la notificación firmada paga un pedido; importe, moneda y comercio contra la base |
| Integraciones (consola) | `integrations/` (`registry.py`, `service.py`, `secret_store.py`, `health.py`, `mail.py`, `payments.py`, `providers/`), `integration_views.py` | `/api/admin/integrations/` (lista, detalle, `draft/`, acciones) | Sólo MASTER (`IsPlatformAdmin`); secretos cifrados con `APP_CONFIG_ENCRYPTION_KEY`, nunca en una respuesta |
| Medición (analítica y marketing) | `integrations/providers/measurement.py`, `measurement/` (`adapters.py`, `conversions.py`), `measurement_views.py`, `management/commands/send_pending_conversions.py`, modelos `MeasurementContext` y `ConversionDelivery` | `GET /api/measurement/config/` (público, sólo identificadores) | El consentimiento del pedido gobierna el envío; la compra nace en la confirmación del pago; nada privado sale |
| Impresión en tienda | `printing/services.py`, `printing/escpos.py`, `print_views.py`, `fiscal_logo.py`, `backend/print_agent/` | `/api/admin/printing/{printers,agents,jobs}/`, `/api/v1/print-agent/jobs/…` | Todo por sucursal; token de agente por local; direcciones sólo de red local |
| Fiscal (SUNAT) | `fiscal/`, `fiscal_*` | emisión, notas, bajas, resúmenes | Apagado por defecto (`FISCAL_ENABLED=0`) |
| Configuración | `backend/backend/settings.py`, `urls.py` | variables en `.env.example` | Falla cerrado con `DEBUG=0`; sin admin de Django |

## Frontend

| Módulo | Archivos | Notas |
|---|---|---|
| Armazón de tienda | `layout.tsx`, `components/StorefrontChrome.tsx`, `StorefrontProvider.tsx`, `ThemeProvider.tsx`, `Header.tsx`, `Footer.tsx`, `BrandLogo.tsx` | `shop-surface` / `internal-surface`; logo por contraste |
| Portada | `page.tsx`, `components/Hero.tsx`, `ProductCarousel.tsx`, `lib/storefront.ts`, `lib/storefront-media.ts`, `lib/catalog-categories.ts` | Todo desde `useStorefront()`; hero `dark`/`light` por tienda |
| Comercio | `product/`, `cart/`, `checkout/`, `orders/`, `lib/cart.ts`, `lib/payments.ts` | Carrito anónimo por clave aleatoria; Izipay por SDK |
| Analítica y consentimiento | `lib/consent.ts`, `components/ConsentBanner.tsx`, `components/AnalyticsProvider.tsx`, `lib/analytics/` (`service.ts`, `events.ts`, `privacy.ts`, `items.ts`, `adapters/`) | `gtag`, `fbq` y `ttq` sólo en `adapters/`; ningún script sin consentimiento ni en rutas privadas |
| Sesión | `auth/`, `invitacion/`, `lib/auth.ts`, `lib/api.ts`, `api/[...path]/route.ts` | `fetchWithAuth`; proxy con tope de cuerpo y redirecciones propias |
| Panel | `admin/components/` (`AdminShell`, `InternalSidebar`, `InternalTopbar`, `AccessGuard`, `ImageUploadField`), `admin/lib/` (`internal-api.ts`, `internal-modules.ts`, `internal-access.ts`, `branch-authority.ts`) | Menú y botones siguen capacidades |
| Panel · pantallas | `admin/{products,inventory,sales,customers,service,settings,staff,users,roles,orders,...}/` | Escaparate: `admin/settings/storefront/`; integraciones (sólo MASTER): `admin/settings/integrations/` |
| Cabeceras | `next.config.ts` | `frame-ancestors`, `no-referrer` en páginas con token |

## Infraestructura

| Pieza | Archivos | Notas |
|---|---|---|
| CI | `.github/workflows/frontend-uxui-validation.yml`, `backend-postgres-validation.yml` | Filtros por ruta; backend ~70 min |
| Producción | `backend/Dockerfile.prod`, `frontend/Dockerfile.prod`, `docker-compose.prod.yml`, `deploy/` | `sh deploy/rehearsal.sh` ensaya la pila completa; `deploy/preflight.py` revisa el archivo de variables |
| Almacenamiento | `evidence_storage.py` (`filesystem` o `s3`) | Evidencias y `companies/<id>/storefront/` en el mismo almacén |
| Datos locales | `backend/private-media/`, `*.sqlite3`, `.env` | Ignorados por git; nunca se versionan |

## E2E por área

`storefront*.spec.ts`, `hero-mobile-clip`, `brand-contrast`, `skip-link` (tienda) · `storefront-v4-images`
(subida desde el panel) · `h411-auth-interop`, `demo-accounts` (sesión) · `pos-*`, `service-pos`,
`tax-breakdown`, `fiscal-invoice` (venta) · `staff-*`, `admin-inventory-mobile`, `h41-visual` (panel).
