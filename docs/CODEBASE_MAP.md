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
| Auth | `auth_views.py`, `verification_codes.py`, `authentication.py`, `v1_auth_views.py`, `token_revocation.py`, `throttles.py`, `security_log.py`, `client_ip.py` | `/api/auth/*` (cookies), `/api/v1/auth/*` (cuerpo) | HttpOnly + CSRF; límites por IP real; contraseña nunca en logs |
| Catálogo y pedidos | `views.py`, `admin_views.py`, `serializers.py`, `tenancy.py` (`company_storefront_products`: lo que la web vende, sin lo «sólo stock interno»), `checkout_services.py`, `checkout_quote_views.py`, `order_fulfillment_services.py`, `tax_services.py` | `/api/products`, `/api/categories`, `/api/cart`, `/api/checkout/*`, `/api/admin/{products,categories,orders}/` | Precio, stock e impuestos sólo en servidor; idempotencia de pago |
| Perfil del comprador y revisión de pedidos (CLIENT-SEC-01) | `identity_validation.py` (teléfono y documento: un solo juez, sólo dígitos 0-9), `customer_profile.py` (perfil; documento fijo o con cupo contado en la auditoría), `account_views.py`, `v1_customer_views.py`, `verification_codes.issue_own` y `confirm_own` (código para una cuenta con sesión, con propósito de token propio y sin enlace), `order_risk.py` (señales, evaluación, `review_when_paid` con segunda mirada a la velocidad, revisión, retención), `customer_services.is_established` (qué ficha respalda la tienda), `_guest_record` (a qué ficha se enlaza un invitado), `file_paid_guest_order` (el pedido de un invitado se archiva al pagarse) y `free_document_for_staff` (en el mostrador una ficha provisional suelta el documento); `notification_services._recipient_email` (el aviso de un pedido va al correo del pedido), modelo `OrderRiskAssessment` | `GET/PATCH /api/account/profile/`, `POST /api/account/verify-email/{send,confirm}/` y sus equivalentes en `/api/v1/customer/<slug>/`; `POST /api/admin/orders/<id>/risk-review/`, `POST /api/v1/internal/<slug>/orders/<id>/risk-review/`; `risk` en el detalle del pedido, `risk_level` / `review_status` y `?review=` en las listas | Empresa por Host o por la ruta, nunca del cuerpo; consultivo por defecto; la revisión empieza al pagar; sin IP ni dato personal en la evaluación; ver = `sales.orders.view`, decidir = `sales.orders.manage` por `visible_orders` |
| Entrega (DELIVERY-01, base) | `delivery_services.py` (transportistas, libreta, registro de entrega, qué ve el comprador), `delivery_views.py` (panel), `account_views.AddressBookActions` (libreta web y v1), modelos `Carrier`, `CustomerAddress`, `OrderDelivery` | `/api/admin/carriers/`, `/api/account/addresses/…`, `/api/v1/customer/<slug>/addresses/…`, `GET/PATCH /api/admin/orders/<id>/delivery/`, `GET/PATCH /api/v1/internal/<slug>/orders/<id>/delivery/`; `delivery` en el detalle del pedido y en los pedidos del comprador | Aditivo: `Order.delivery_method` y sus columnas no cambian; el flete es un dato; un enlace sólo si es https; lo ajeno responde 404
| Inventario | `inventory_services.py`, `inventory_views.py`, `v1_inventory_views.py`, `v1_transfer_views.py`, `import_*`, `stock_import_services.py`, `xlsx_reader.py` | ajustes, Kardex, transferencias, recuentos, importación | `select_for_update`; sucursal por alcance |
| POS / promociones | `pos_services.py`, `unit_identification.py` (qué equipo sale: caja y entrega de pedidos, SERIAL-PICK-01), `pos_views.py`, `v1_pos_views.py`, `promotion_*`, `sales_note_services.py`, `ticket_services.py` | `/api/v1/internal/<slug>/sales/pos/*` | Idempotencia por empresa |
| Servicio técnico | `service_services.py`, `v1_service_views.py`, `evidence_*` | `/api/v1/internal/<slug>/service/*`, `…/service/orders/<id>/evidence/` (lista, subir, nota, compartir, anular) | Evidencias privadas, por etapa y sucursal; se anulan, no se borran; asignar ≠ cobrar |
| Escaparate (CMS) | `storefront_content_services.py`, `storefront_content_views.py`, `company_settings.py`, `settings_views.py` | `/api/storefront/config/`, `/api/admin/storefront/{page,campaigns,<kind>}/` | `company.manage`; publicar es acción propia |
| Imágenes de producto | `product_media.py`, `product_media_views.py`, modelo `ProductImage` | `/api/admin/products/<id>/images/` (lista, subir, editar, quitar, `order/`); `images` en el producto público | `products.manage`; los píxeles son un `StorefrontImage`; `Product.image_url` = principal |
| Inventario unificado (INVENTORY-UNIFIED-01) | `product_publication.py` (qué puede publicarse en bloque y el cambio en bloque), `admin_views.py` (`filter_admin_products`: el filtro de la lista, compartido; `AdminProductBulkVisibilityView`), `inventory_exports.py` (los tres libros y el ZIP), `inventory_export_views.py`, `xlsx_reader.py` (`guard_text` / `unguard_text`), `import_exports.py` (`inventory_export_table`, `product_codes`, `report_sheet_refusal`) | `POST /api/admin/products/bulk-visibility/`; `GET /api/admin/inventory/exports/{products,stock,units,full}/?mode=reimport\|report`; `GET /api/admin/inventory/export/` sigue igual | `products.manage` para publicar u ocultar; `products.view` para el catálogo, `inventory.view` + sucursales visibles para stock y equipos; «Costo» sólo con `inventory.adjust`; un id, sucursal o categoría de otra empresa responde 404 |
| Centro de gestión masiva: asistente e imágenes en lote (INVENTORY-UNIFIED-01, 3 y 4) | `import_assistant.py` (a qué carga va un libro, por hojas y cabeceras), `image_import_services.py` (imágenes por nombre de archivo o libro «Imágenes»; añadir / reemplazar), `import_views.py` (`AdminImportInspectView` con `import_type=auto`; `AdminProductImportPreviewView` con `kind=images`; `AdminProductImagesImportApplyView`, `…RulesView`), migración `0122` | `POST /api/admin/imports/inspect/` (misma ruta, responde además `suggested_import_type`); `POST /api/admin/products/import/preview/` con `kind=images`; `POST /api/admin/products/images/import/<id>/apply/`; `GET /api/admin/products/images/import/rules/` | El asistente no lee filas ni escribe nada y no nombra una carga que quien pregunta no puede hacer; imágenes: `products.manage`, todas las protecciones de `import_media`, un código de otra empresa nunca casa, reemplazar exige `REEMPLAZAR` |
| Carga masiva: tres cargas (IMPORT-SPLIT-01) | productos `import_services.py` + `import_exports.py` (plantilla de una hoja); equipos `unit_import_services.py`; stock `stock_import_services.py` | `/api/admin/products/import/*`, `/api/admin/inventory/units/import/*`, `/api/admin/inventory/import/*` | Productos no mueve stock; equipos y stock sólo en sucursales de quien carga |
| Carga masiva con imágenes | `import_media.py` (lote y ZIP), `import_services.py` | `images` e `images_zip` en `/api/admin/products/import/preview/` | ZIP sin extraer; imágenes en espera = sin colocar, de la empresa del trabajo |
| Imágenes de la tienda | `storefront_media.py`, `storefront_media_views.py`, modelo `StorefrontImage` | `POST /api/admin/storefront/images/`, `GET /api/storefront/images/<id>` | Públicas; el decodificador decide el tipo; comparten almacén con evidencias, no autorización |
| Pagos (Izipay) | `payments/izipay.py`, `checkout_services.py`, `views.py` (`IzipayNotificationView`), `payments/fake_izipay.py` (sólo pruebas) | `/api/payments/create-checkout-session/`, `/api/payments/izipay/notification/` | Sólo la notificación firmada paga un pedido; importe, moneda y comercio contra la base |
| Integraciones (consola) | `integrations/` (`registry.py`, `service.py`, `secret_store.py`, `health.py`, `mail.py`, `payments.py`, `providers/`), `integration_views.py` | `/api/admin/integrations/` (lista, detalle, `draft/`, acciones) | Sólo MASTER (`IsPlatformAdmin`); secretos cifrados con `APP_CONFIG_ENCRYPTION_KEY`, nunca en una respuesta |
| Medición (analítica y marketing) | `integrations/providers/measurement.py`, `measurement/` (`adapters.py`, `conversions.py`), `measurement_views.py`, `management/commands/send_pending_conversions.py`, modelos `MeasurementContext` y `ConversionDelivery` | `GET /api/measurement/config/` (público, sólo identificadores) | El consentimiento del pedido gobierna el envío; la compra nace en la confirmación del pago; nada privado sale |
| Correos | `mail/` (`service.py`, `builders.py`, `contract.py`, `skin.py`, `text.py`, `format.py`, `plantilla/`, `ejemplos/`), `emails.py`, `email_services.py`, `notification_services.py` (`_dressed`), `mail_preview_views.py` | `mail.send(tipo, destinatario, datos, company=…)`; vista previa `/api/dev/mail-preview/` (sólo `DEBUG`) | La plantilla es de una empresa (`marca.json`); las demás, correo neutro. Datos escapados, enlaces sólo `https://` o la propia web, cuerpo nunca en el registro. `docs/correos.md` |
| Impresión en tienda | `printing/services.py`, `printing/escpos.py`, `print_views.py`, `fiscal_logo.py`, `backend/print_agent/` | `/api/admin/printing/{printers,agents,jobs}/`, `/api/v1/print-agent/jobs/…` | Todo por sucursal; token de agente por local; direcciones sólo de red local |
| Fiscal (SUNAT) | `fiscal/`, `fiscal_*` | emisión, notas, bajas, resúmenes | Apagado por defecto (`FISCAL_ENABLED=0`) |
| Configuración | `backend/backend/settings.py`, `urls.py` | variables en `.env.example` | Falla cerrado con `DEBUG=0`; sin admin de Django |

## Frontend

| Módulo | Archivos | Notas |
|---|---|---|
| Armazón de tienda | `layout.tsx`, `components/StorefrontChrome.tsx`, `StorefrontProvider.tsx`, `ThemeProvider.tsx`, `Header.tsx`, `Footer.tsx`, `BrandLogo.tsx` | `shop-surface` / `internal-surface`; logo por contraste |
| Portada | `page.tsx`, `components/Hero.tsx`, `ProductCarousel.tsx`, `lib/storefront.ts`, `lib/storefront-media.ts`, `lib/catalog-categories.ts` | Todo desde `useStorefront()`; hero `dark`/`light` por tienda |
| Comercio | `product/`, `cart/`, `checkout/`, `orders/`, `lib/cart.ts`, `lib/payments.ts`, `components/CartItemCard.tsx` | Carrito anónimo por clave aleatoria; Izipay por SDK; una línea `available: false` se muestra «Ya no está disponible», no se suma y bloquea el paso al pago |
| Mi cuenta (CLIENT-SEC-01, DELIVERY-01) | `account/page.tsx`, `lib/account.ts`, `components/AddressBook.tsx`, `lib/addresses.ts` | Perfil propio; envía sólo lo que cambió; el correo se verifica con un código; el documento puede estar fijo; «Mis direcciones»; `/account` fuera de toda medición |
| Entrega (DELIVERY-01) | `admin/components/CarrierSettings.tsx`, `OrderDeliveryCard.tsx`, `lib/delivery.ts`, `components/OrderDeliveryInfo.tsx`, `components/SavedAddressSelect.tsx` | Quién puede cambiar qué lo dice el servidor; un enlace de seguimiento sólo si es https, en pestaña nueva; en el checkout sólo se rellena el formulario |
| Revisión de pedidos (CLIENT-SEC-01) | `admin/components/OrderRiskCard.tsx`, `OrdersTable.tsx` (`orderRiskBadge`), `OrderReviewSettings.tsx`, `admin/orders/`, `admin/settings/page.tsx` | Nivel, motivos y `can_review` vienen del servidor; las dos opciones se guardan aparte del resto de la configuración |
| Analítica y consentimiento | `lib/consent.ts`, `components/ConsentBanner.tsx`, `components/AnalyticsProvider.tsx`, `lib/analytics/` (`service.ts`, `events.ts`, `privacy.ts`, `items.ts`, `adapters/`) | `gtag`, `fbq` y `ttq` sólo en `adapters/`; ningún script sin consentimiento ni en rutas privadas |
| Sesión | `auth/`, `invitacion/`, `lib/auth.ts`, `lib/api.ts`, `api/[...path]/route.ts` | `fetchWithAuth`; proxy con tope de cuerpo y redirecciones propias |
| Panel | `admin/components/` (`AdminShell`, `InternalSidebar`, `InternalTopbar`, `AccessGuard`, `ImageUploadField`), `admin/lib/` (`internal-api.ts`, `internal-modules.ts`, `internal-access.ts`, `branch-authority.ts`) | Menú y botones siguen capacidades |
| Productos en bloque y exportación (INVENTORY-UNIFIED-01) | `admin/components/ProductsManager.tsx` (lista, filtros y selección), `ProductsTable.tsx`, `ProductBulkVisibility.tsx` (barra, confirmación, resultado), `ProductStatusBadge.tsx` (`ProductVisibilityBadge`), `InventoryExportPanel.tsx` (`InventoryExportPanel` e `InventoryExports`), `lib/admin.ts` (`bulkSetProductVisibility`), `lib/inventory-exports.ts` | «Todo lo que coincide» viaja como filtro y total esperado; el panel «Exportar» vive por ahora en `admin/inventory/import/` |
| Gestión masiva de inventario (INVENTORY-UNIFIED-01, 3 y 4) | `admin/inventory/bulk/page.tsx`, `admin/components/BulkInventoryCentre.tsx` (secciones, `ImportAssistant`, historial), `BulkImageImport.tsx`, `ImportKindNotice.tsx` (`IMPORT_KINDS`: qué hace y qué no cada carga), `ImportWizard.tsx` (`HistoryTable` con «Descargar errores»), `admin/lib/internal-modules.ts` (`anyCapabilities`) | Una entrada de menú más; las tres cargas conservan la suya. Cada sección según la capacidad de quien mira |
| Panel · pantallas | `admin/{products,inventory,sales,customers,service,settings,staff,users,roles,orders,...}/` | Escaparate: `admin/settings/storefront/`; integraciones (sólo MASTER): `admin/settings/integrations/` |
| Cabeceras | `next.config.ts` | `frame-ancestors`, `no-referrer` en páginas con token |

## Infraestructura

| Pieza | Archivos | Notas |
|---|---|---|
| CI | `.github/workflows/frontend-uxui-validation.yml`, `backend-postgres-validation.yml`, `backend/scripts/ci_shards.py` | Filtros por ruta; backend en 6 fragmentos en paralelo (ver «CI») |
| Producción | `backend/Dockerfile.prod`, `frontend/Dockerfile.prod`, `docker-compose.prod.yml`, `deploy/` | `sh deploy/rehearsal.sh` ensaya la pila completa; `deploy/preflight.py` revisa el archivo de variables |
| Almacenamiento | `evidence_storage.py` (`filesystem` o `s3`) | Evidencias y `companies/<id>/storefront/` en el mismo almacén |
| Datos locales | `backend/private-media/`, `*.sqlite3`, `.env` | Ignorados por git; nunca se versionan |

## CI

Dos workflows. Corren en los PR hacia `master` y en `master` tras el merge. Un push nuevo a un PR cancela
el run del commit anterior; en `master` no se cancela ninguno.

- **Backend PostgreSQL Validation.** Siempre la suite completa: ningún paso elige tests según lo que
  cambió. Se reparte en 6 trabajos `Django shard N/6` que corren a la vez (el mismo `manage.py test`, los
  mismos settings y `TEST_RUNNER`, PostgreSQL 16, un proceso cada uno) y un trabajo final,
  **`Django tests on PostgreSQL`**, que falla si algún fragmento falló, se canceló o agotó su tiempo. Ése es
  el check que se mira antes de mergear. En un solo trabajo tardaba 50–100 min (mediana 72 en 60 runs
  verdes); en fragmentos se esperan 12–20 min, **por medir en el primer run real**.
- **Quién reparte.** `backend/scripts/ci_shards.py` pregunta al runner qué tests existen y los reparte
  todos, por clase: un archivo o una clase nuevos entran solos. `store/test_ci_shards.py` y
  `store/test_ci_workflow.py` (dentro de la suite) prueban en cada run que los fragmentos son disjuntos y
  suman la suite; cada fragmento comprueba además que corrió exactamente los tests que le tocaban.
  A qué fragmento va una clase depende sólo de `ci_shard_weights.json` (segundos por clase de la última
  medición) y de su propio nombre: añadir o borrar un test no mueve a ningún otro. Si el archivo envejece,
  los fragmentos quedan desiguales, nunca incompletos; se regenera con
  `python manage.py test --noinput --durations 0 2>&1 | python scripts/ci_shards.py weights` (eso sí
  cambia el reparto: va en un PR propio). El número de fragmentos es la lista `shard: [...]` del workflow.
- **Las clases que vacían la base van juntas.** Las 24 `TransactionTestCase` van siempre al último
  fragmento, en el orden de la suite entera. Ninguna usa `reset_sequences` (TEST-FIXTURE-SEQ-01, #155):
  reiniciar los ids en 1 chocaba con lo que siembran las migraciones (empresa 1).
  `test_ci_shards…test_none_of_them_restarts_the_ids` lo impide. Cada una se puede correr sola en local,
  también sobre una base recién creada.
- **Cuándo corre el backend.** `backend/**` y los archivos de fuera que leen sus tests: `deploy/**`,
  `docker-compose.prod.yml`, `docs/internal-parity-matrix.md`, `frontend/app/admin/**` y
  `frontend/app/auth/components/DevQuickLogin.tsx`. Un PR sólo de documentación no arranca nada. Un test
  nuevo que lea un archivo de fuera de `backend/` lo añade a `EXTERNAL_INPUTS`
  (`backend/scripts/affected_tests.py`) y al workflow; `store/test_affected_tests.py` falla si falta.
- **Frontend UX/UI Validation.** Tests, lint y build en un trabajo (`Tests, lint and build`, 1–2 min);
  `frontend/**`.
- **Mientras se programa.** Desde `backend/`, con el `DATABASE_URL` de PostgreSQL de siempre:
  `python3 scripts/affected_tests.py --run` corre primero las clases que nombran lo que cambió frente a
  `origin/master` (`--why` dice por qué cada una, `--wide` añade las que están a un import, `--files a b`
  usa esas rutas, sin `--run` sólo las lista). Es una ayuda de escritorio, no una compuerta: adivina por
  el texto de los tests y puede no ver alguno. Ningún workflow la usa; el CI corre siempre todo.
- **Ajustes de GitHub.** Hoy `master` no tiene protección de rama ni checks obligatorios: nada que
  cambiar al mergear esto. Si se activa, los obligatorios son `Django tests on PostgreSQL` y
  `Tests, lint and build`; nunca los `Django shard N/6`, que cambian de nombre con el número de
  fragmentos. Ojo: con filtros por ruta, un check obligatorio se queda en «Expected» para siempre en un
  PR que no lo dispara (sólo docs; o sólo frontend, para el del backend). Antes de exigirlos hay que
  quitar `paths:` del disparador, decidir en un primer trabajo si los fragmentos corren, y dejar que el
  trabajo final corra siempre.

## E2E por área

`storefront*.spec.ts`, `hero-mobile-clip`, `brand-contrast`, `skip-link` (tienda) · `storefront-v4-images`
(subida desde el panel) · `h411-auth-interop`, `demo-accounts` (sesión) · `pos-*`, `service-pos`,
`tax-breakdown`, `fiscal-invoice` (venta) · `staff-*`, `admin-inventory-mobile`, `h41-visual` (panel).
