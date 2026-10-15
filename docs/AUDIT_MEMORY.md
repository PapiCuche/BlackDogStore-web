# AUDIT MEMORY — Black Dog Store

Índice operativo de la auditoría. Guarda sólo conocimiento **comprobado** con
símbolo, archivo, test y SHA. No reemplaza al código: si esta memoria contradice
al código, a las migraciones o a los tests, gana el código y esta entrada se
corrige.

Orden de autoridad: código › migraciones › tests › `02_ESTADO_ACTUAL.md` ›
`05_DECISIONES_TECNICAS.md` › documentación maestra (`docs/`) › esta memoria ›
checkpoints históricos.

Invalidación: una entrada `VERIFICADO @ SHA` deja de bastar si cambió el archivo
del símbolo, una dependencia directa, el modelo/migración, la capacidad, la regla
de tenancy, si falla su test o si la tarea pide una propiedad nunca auditada.
Comprobar con `git diff <SHA>..HEAD -- <archivos>` y revalidar sólo ese subárbol.

---

## 1. Baseline vigente

| Campo | Valor |
|---|---|
| Branch | `audit/full-system-2026-09` (local; `origin/master` es el master autoritativo) |
| Baseline medido | `65aa8c1` (merge de `origin/master` `2dca0a3` sobre `9525b08`) |
| HEAD al sembrar | `4a9dd5c` — delta vs baseline: sólo `02_`, `07_`, `frontend/app/api/[...path]/route.ts`, `frontend/__tests__/api-proxy-scope.test.ts` |
| Último SHA verificado | `c191a84` (F2 completa: F-BRANCH-01/02/03, F-CAP-01, RBAC-01/02, WRITE-SCOPE-01, DRIFT-01/07, E2E-02/01). Backend cambia en `tenancy.py`, `tenant_views.py`, `staff_views.py`, `promotion_views.py`, `settings_views.py`, `tests.py` |
| Fecha | 2026-09-30 |
| Working tree | limpio |
| Migraciones | 105 aplicadas / 0 pendientes / 0 por generar (`store` llega a `0093_sales_service_delivery`) |
| Backend tests | 4569 ejecutadas, 4566 OK, 3 skipped, 0 fallos, PostgreSQL 14, 1544,8 s @ `c191a84` (baseline: 4489 @ `65aa8c1`) |
| Frontend tests | 411/411 OK, 40 suites, @ `1fea6b9` (rama `reconcile/uxui-after-f2`; en `master` `ef9890f`: 402/37) |
| TypeScript | `tsc --noEmit` OK @ `1fea6b9` |
| Lint | 0 errores / 26 advertencias @ `1fea6b9` (en `master`: 0 / 33) |
| Build | OK (44 páginas) @ `1fea6b9` |
| E2E | Playwright 118/118, 0 omitidas, sin reintentos, 1 worker @ `1fea6b9` y @ `c191a84` |

Reglas de medición: suite backend completa sólo en PostgreSQL y un proceso a la
vez; Playwright contra los dev servers :3000/:8000 (SQLite dev, `FISCAL_ENABLED=True`
en `.env` ignorado). Un baseline nuevo se mide, no se hereda.

Documentos maestros presentes: `02_ESTADO_ACTUAL.md`, `05_DECISIONES_TECNICAS.md`,
`07_CHANGELOG.md`. **No existen** `00_`, `01_`, `03_`, `04_`, `06_`; su contenido vive en
`docs/` (arquitectura: `docs/black-dog-store-ecommerce-architecture.md`, SaaS:
`docs/saas-multiempresa.md`, RBAC: `docs/rbac-access-audit-2026-09-02.md`, fiscal:
`docs/adr-fiscal-c22a1.md`).

---

## 2. Arquitectura verificada

| Pieza | Estado | Dónde |
|---|---|---|
| Backend | IMPLEMENTADO | Django 5.2 + DRF 3.17, app única `backend/store/` |
| Frontend | IMPLEMENTADO | Next 16 / React 19, `frontend/app/`; proxy `frontend/app/api/[...path]/route.ts` |
| DB | IMPLEMENTADO | PostgreSQL (tests), SQLite (dev local) |
| Auth web | IMPLEMENTADO | JWT en cookies, `store/auth_views.py`, `store/authentication.py` |
| Auth v1 | IMPLEMENTADO | Bearer, `store/v1_auth_views.py`, `store/v1_internal_authentication.py` |
| Tenancy | IMPLEMENTADO | `store/tenancy.py` (Company/Branch/Membership) |
| RBAC (QUÉ) | IMPLEMENTADO | `tenancy.resolve_capabilities`, `store/capabilities.py`, `can_delegate_capabilities` |
| Branch scope (DÓNDE) | IMPLEMENTADO | lectura acotada; delegación con `can_delegate_branch_scope` desde `70286d1` |
| Legacy bridge | IMPLEMENTADO (no eliminar) | `tenancy.uses_legacy_bridge`, `tenancy.legacy_catalog_company` |
| Payments | IMPLEMENTADO | `store/payments/`; webhook **no auditado** |
| Fiscal | IMPLEMENTADO en BETA; producción PENDIENTE | `store/fiscal/`, `store/fiscal_services.py` |
| Servicio técnico | IMPLEMENTADO (sólo v1) | `store/service_services.py`, `store/v1_service_views.py` |
| Inventory | IMPLEMENTADO | `store/inventory_services.py`, `store/v1_transfer_views.py` |
| POS | IMPLEMENTADO (web + v1 gemelas) | `store/pos_views.py`, `store/v1_pos_views.py`, `store/pos_services.py` |
| CI | PENDIENTE | no hay `.github/workflows` (CI-01) |

---

## 3. Invariantes de seguridad verificadas

**TENANT-01** — La empresa se resuelve en el servidor; `?company=` es sólo un
selector validado.
Autoridad: `tenancy.py::resolve_company_for_user`, `build_company_context`;
v1: slug + membresía activa (`V1TenantSelectorAuthorityTest`).
Tests: `SaasTenancyResolutionTest`, `SaasIsolationApiTest`, `V1TenantSelectorAuthorityTest`, `V1UnresolvableTenantTest`.
Estado: VERIFICADO @ `4a9dd5c` (F1, 51 modelos, ningún cruce de empresa).

**TENANT-02** — Serializers escribibles excluyen `company`/`branch`; el servicio
re-deriva el alcance.
Tests: `Phase2dCrossTenantIsolationTest`, `C14PromotionTenantInvariantTest`, `M12CTenantIsolationTest`, `C22BFiscalTenantIsolationTest`, `H412bIsolationMatrixTest`.
Estado: VERIFICADO @ `4a9dd5c`. F-TENANT-01 queda cerrado por el límite de alta directa descrito abajo.

**BRANCH-01** — Lecturas por sucursal nacen acotadas.
Autoridad: `tenancy.py::visible_branches`, `_branch_authority`, `_granted_branches`, `visible_orders`, `resolve_branch_for_user`; servicio: `v1_service_views.py::V1ServiceSurfaceMixin.get_order` (`branch__in=allowed`); POS: `resolve_pos_branch`.
Tests: `Phase2dBranchScopedReadsTest`, `H412SelectedBranchWebTest`, `H412SelectedBranchV1Test`, `M8BranchScopeTest`, `M7InventoryBranchScopeTest`.
Estado: VERIFICADO @ `4a9dd5c` para lectura; escritura: BRANCH-03 @ `70286d1`.

**BRANCH-02** — Un grant de sucursal y la sucursal por defecto de una membresía sólo
apuntan a sucursales de la misma empresa que el actor puede conceder; inexistente, de
otro tenant y fuera de alcance responden igual: `404 {'detail': 'Sucursal no encontrada o sin acceso.'}` (`tenant_views._BRANCH_NOT_FOUND`).
Autoridad: `tenant_views.py::_apply_branch_access` (`branch_access`), `tenant_views.py::_grantable_branch` (`branch`, RBAC-02).
Tests: `F2MembershipBranchOracleTest` (7), `Phase2dBranchAccessApiTest`, `SaasIsolationApiTest.test_branch_from_another_company_is_rejected_by_api` (404).
Estado: VERIFICADO @ `d18e983`.

**WRITE-SCOPE-01** — `branch_scope` limita también el radio de impacto de las mutaciones.
Una capability `company.manage` no convierte SELECTED en autoridad company-wide.
Autoridad: `tenancy.py::has_company_wide_scope` (peldaño company-wide de `_branch_authority`:
plataforma, modo ALL, puente legacy); mensaje `tenancy.COMPANY_WIDE_SCOPE_REQUIRED`.
Company-scoped (exigen company-wide + capacidad): `AdminBranchListView.post` (crear sucursal),
`AdminCompanyFulfillmentBranchView.patch`, `settings_views.AdminSequenceDetailView.patch` sobre
la serie de empresa (`branch_id is None`), `AdminSequenceScopeView.patch`,
`AdminCompanySettingsView.patch` (vía `settings_views._require_company_wide_scope`).
Branch-scoped: `AdminBranchDetailView.patch` exige company-wide o la sucursal en
`visible_branches` (403 `_BRANCH_NOT_REACHED`; inexistente/ajena siguen 404); serie de una
sucursal alcanzada sigue editable. Lecturas sin cambio (RBAC-01).
Un SELECTED ya no puede reactivar una sucursal inactiva (no está en `visible_branches`).
Tests: `F2SelectedBranchWriteScopeTest` (10), `F2SelectedCompanySettingsScopeTest` (8).
Estado: VERIFICADO @ `c076120`.

**E2E-FIXTURE-01** — Las pruebas de navegador no mutan las cuentas demo con las que otras
suites inician sesión. Quien necesita desactivar personal usa `dev_e2e_staff`
(`seed_demo_users --e2e-fixtures`; constante `DEMO_E2E_STAFF_USERNAME`): no es cuenta de
acceso, no está en `ALL_DEMO_USERNAMES` ni en la tarjeta de desarrollo, `--purge` lo elimina
y volver a sembrar lo restaura. El seed sin la bandera no cambia.
Tests: `F2E2eFixtureSeedTest` (6), `DemoUsersCommandTest`; spec `e2e/staff-personnel.spec.ts` test I.
Estado: VERIFICADO @ `9c3445f`.

**FISCAL-FLOW-01** — Emitir son dos pasos con dos nombres. `POST /admin/orders/<pk>/fiscal-document/`
numera y firma y NO habla con SUNAT («Preparar factura|boleta»; «Firmar comprobante» si existe
sin XML); el envío es otra llamada («Enviar a SUNAT», sólo factura firmada con `can_submit`).
Autoridad: `fiscal_views.AdminOrderFiscalDocumentView`, `FiscalDocumentPanel.tsx`.
Tests: `fiscal-document-panel.test.tsx`, `e2e/fiscal-invoice.spec.ts`.
Estado: VERIFICADO @ `c191a84`.

**UI-SCOPE-01** — La UI representa conjuntamente capabilities (QUÉ) y branch scope (DÓNDE);
el backend sigue siendo la autoridad. Leer un recurso de nivel empresa no implica que un
SELECTED pueda modificarlo.
Autoridad UI: `frontend/app/admin/lib/branch-authority.ts` — `hasCompanyWideScope` (espejo de
`tenancy.has_company_wide_scope`), `reachesBranch`, `canEditBranchScopeOf` y
`ownBranchScopePayload` (espejos de `can_delegate_branch_scope`). Fuente: `dashboard.branch_scope`
del servidor; nunca se infiere del número de sucursales visibles.
Pantallas: `branches/page.tsx` (crear y despacho exigen company-wide; editar exige alcanzar la
fila), `settings/page.tsx` + `SequenceSettings` (ajustes, serie de empresa y alcance sólo
company-wide; serie de sucursal propia editable), `users/page.tsx` (sin «Todas» ni sucursales no
alcanzadas; sin edición de quien llega más lejos), `staff/page.tsx` (invitación),
`sales/promotions/page.tsx` (archivar y crear combo).
Tests: `admin-write-scope.test.tsx` (15), `branch-authority.test.ts` (14).
Estado: VERIFICADO @ `f6dc9ca`.

**UX-RECON-01** — En la reconciliación con UX/UI (#39) master decide el comportamiento y
#39 la presentación donde es compatible. Las seis pantallas de F2 (`branches/page.tsx`,
`SequenceSettings.tsx`, `sales/promotions/page.tsx`, `service/orders/[id]/page.tsx`,
`settings/page.tsx`, `users/page.tsx`) conservan su lógica QUÉ + DÓNDE con el diseño de #39
(`PageHeader`, `internalButtonClass`, `internalInputClass` de `internal-ui.tsx`);
`branch-authority.ts`, `internal-modules.ts` e `InternalControlGuard.tsx` quedan idénticos a
master. Los colores de estado sólo-oscuros de #39 se expresan con los tokens del tema
(`danger`/`warning`/`success`/`info` y sus `-surface`/`-border`), que es lo que exigen
`surfaces.test.ts` y `theme-coverage.test.ts`.
Tests: suites F2 de frontend sin cambios (`admin-write-scope`, `branch-authority`,
`service-assignment-contract`, `fiscal-document-panel`, `api-proxy-scope`), `surfaces.test.ts`,
`internal-sidebar-a11y.test.tsx`, Playwright 118/118.
Estado: VERIFICADO @ `1fea6b9` (rama `reconcile/uxui-after-f2`, pendiente de merge).

**READ-01** — Pertenecer a una empresa no autoriza a leerla. `GET /admin/companies/`,
`/admin/companies/{pk}/`, `/admin/branches/`, `/admin/branches/{pk}/` exigen capacidad de
lectura; sin ella la lista sale vacía y el detalle responde 404 (igual que un id
inexistente o ajeno). Empresa: `tenant_views.READ_COMPANY` (`company.view`, `company.manage`).
Plantilla de sucursales: `tenant_views.READ_BRANCHES` (+ `memberships.view`, `memberships.manage`, por el selector de acceso).
La plantilla es de nivel empresa (incluye inactivas); un SELECTED con lectura la ve completa.
Maestro de plataforma: alcance global. Sin membresía: 403 (`HasCompanyMembership`); el puente legacy no llega a estos endpoints.
Autoridad: `access_views.py::_scope_readable` (mismo helper que M11 usa para áreas y roles).
Tests: `F2TenantReadAuthorizationTest` (10).
Estado: VERIFICADO @ `5afdb81`.

**BRANCH-03** — Nadie concede, retira ni opera por escritura una sucursal que no alcanza.
Autoridad: `tenancy.py::can_delegate_branch_scope(user, company, *, mode, branch_ids, current)`. Company-wide (plataforma, modo ALL, puente legacy) concede todo; SELECTED sólo subconjuntos de `visible_branch_ids`, nunca ALL, y no toca un objeto cuyo alcance `current` ya excede el suyo. Mensaje: `tenancy.BRANCH_SCOPE_NOT_GRANTABLE`.
Llamadores: `tenant_views.py::AdminMembershipListView.post`, `AdminMembershipDetailView.patch`; `staff_views.py` invitaciones `post`; `promotion_views.py::_write_promotion`.
Detalle: `settings_views.py::AdminSequenceDetailView._scoped` responde 404 a series de sucursales fuera de `visible_branches`.
Tests: `F2BranchDelegationTest` (18), `F2PromotionBranchScopeTest` (8), `F2SequenceBranchScopeTest` (5).
Estado: VERIFICADO @ `70286d1`.

**RBAC-G3** — Nadie delega capacidades que no tiene.
Autoridad: `tenancy.py::can_delegate_capabilities`, `can_grant_company_role`.
Tests: `M11AntiEscalationTest`, `G3LegacyRoleEscalationTest`, `G3DeactivationEscalationTest`.
Estado: VERIFICADO @ `4a9dd5c`. Equivalente del eje DÓNDE: BRANCH-03.

**RBAC-DEACT** — Desactivar una membresía retira sus asignaciones de rol.
Autoridad: `tenant_views.py::AdminMembershipDetailView.patch` (`role_assignments...update(is_active=False)`), `staff_views.py` (guarda «No puedes desactivar tu propio acceso.»).
Tests: `H41StaffDeactivationTest`, `G3DeactivationEscalationTest`.
Estado: VERIFICADO @ `4a9dd5c`. Por eso ninguna prueba de navegador desactiva una cuenta demo: E2E-FIXTURE-01.

**CAP-STOCK-01** — Abrir saldo exige autoridad sobre existencias, no sólo sobre catálogo.
Autoridad: `admin_views.py::AdminProductListView.post` — con `inventory > 0` llama a
`_company_context(request, CAP_INVENTORY_ADJUST, _LEGACY_ADJUST_INVENTORY_ROLES)`, la
misma puerta que `inventory-adjust`, antes de cualquier escritura. `inventory` omitido
o 0 sólo requiere `products.manage`. La sucursal del saldo la elige el servidor
(`resolve_branch_for_user(user, company, None)` → dentro de `visible_branches`); el
payload no puede nombrarla. Otras vías de saldo: PATCH de producto rechaza `inventory`
(400); importación de productos no crea stock; importación de stock exige
`inventory.adjust` + `_branch_access_error` en preview y apply.
Tests: `F2ProductInitialInventoryCapabilityTest` (8), `Phase2dConsistencyTest`, `AdminProductCreateTest`.
Estado: VERIFICADO @ `c042fea`.

**LEGACY-01** — El puente legacy (`UserProfile.role` sin membresía, sólo tenant
piloto) se limita a catálogo/pedidos/inventario/notas.
Autoridad: `tenancy.py::uses_legacy_bridge`. Paridad frontend: `H412bFrontendLegacyRoleParityTest`.
Estado: VERIFICADO @ `4a9dd5c` (ACEPTADO POR DISEÑO).

**PARITY-01** — `/api/admin/*` y `/api/v1/internal/<slug>/*` tienen tenencia y
permisos equivalentes en POS, pedidos e inventario.
Tests: `Ip1PosWebParityTest`, `Ip1TransferWebParityTest`, `Ip1ParityManifestTest`.
Estado: VERIFICADO @ `4a9dd5c`. Excepciones: INV-LEGACY-V1-F1/F2/F3.

**FISCAL-SCOPE-01** — La serie fiscal se resuelve desde `order.fulfillment_branch`
en el servidor; `resolve_series` no lee entrada del cliente.
Autoridad: `fiscal_config.py::resolve_series`, llamado desde `fiscal_services.py`.
Estado: VERIFICADO @ `4a9dd5c`. Resúmenes diarios son por empresa (por diseño, F6 INFO).

**SVC-ASSIGN-01** — Un técnico asignable puede ver la orden que recibe.
Autoridad: `service_services.py::eligible_technicians(company, branch, user_id=None)`
(usuario activo, membresía activa, `service.orders.view`, sucursal dentro de
`tenancy.visible_branches`); `assign_technician` lo vuelve a exigir bajo el bloqueo de
la orden. Nombrar a un técnico: `service.orders.manage` O (`service.orders.assign` Y
`service.orders.view`), una sola regla en
`V1ServiceSurfaceMixin.require_assignment_authority`, usada por la asignación (GET y
POST), los candidatos de una sucursal y la recepción con `technician_id`. Recibir
sin técnico no la exige.
El id del técnico se resuelve dentro del conjunto elegible: fuera de él, 404.
Desasignar (`technician_id: null`) exige `service.orders.manage`; `assign` sólo asigna
y reasigna, y la negativa se decide antes de buscar la orden (sin efectos).
Tests: `SvcAssignCapabilityTest`, `SvcAssignVisibilityTest`, `SvcUnassignAuthorityTest`, `SvcTechnicianEligibilityTest`, `SvcIntakeWithAssignmentTest`.
Estado: VERIFICADO @ `9b59a31`.

**SVC-ATOMIC-01** — Recibir un equipo con técnico es una sola transacción.
Autoridad: `service_services.py::create_repair_order_with_assignment`; `assign_technician`
es `@transaction.atomic`.
Tests: `SvcIntakeWithAssignmentTest` (sin orden, historial ni auditoría si falla la
asignación), `SvcAssignOutsideATransactionTest`.
Estado: VERIFICADO @ `1d35b7d`.

**SVC-PAY-01** — Registrar un pago y reversarlo son autoridades distintas.
Autoridad: `v1_service_views.py::COLLECT_AUTHORITY` (`service.payments.collect` o
`service.payments.manage`) en `V1ServicePaymentView.post`; la reversa exige
`service.payments.manage`. La cotización aprobada sigue siendo necesaria.
Tests: `SvcPaymentCollectTest`; E2E `service-pos` (403 al reversar).
Estado: VERIFICADO @ `1d35b7d`.

**POS-SVC-01** — Una orden de servicio no es una venta.
Autoridad: el flujo de la caja sólo llama a `POST service/orders/`; no hay `Order`,
`OrderItem`, `StockMovement`, `FiscalDocument` ni `SalesCommission`.
Tests: `SvcIntakeWithAssignmentTest.test_it_touches_nothing_of_the_shop`,
`SvcCustomServiceLineTest.test_it_is_not_a_sale`.
Estado: VERIFICADO @ `1d35b7d`.

**UX-RECON-SVC-01** — Al traer la interfaz de #43 sobre el master con SVC-FUNC-01,
master decide comportamiento y #43 presentación. `/admin/service` es sólo una
redirección; las seis colas tienen ruta y entrada propia; la caja conserva
`PosModeSwitch` y `PosServiceIntake`; el detalle de la orden conserva
`mayAssignTechnician`, `mayCollectPayment`, «Quitar» sólo con `service.orders.manage` y
«Reversar» sólo con `service.payments.manage`. `branch-authority.ts`,
`internal-modules.ts`, `InternalControlGuard.tsx`, `auth.ts`, `service-console.ts`, el
proxy y `backend/` quedan idénticos a master `d98d70c`.
Tests: `service-navigation.test.tsx`, `service-authority-console.test.tsx`,
`pos-service-intake.test.tsx`, suites F2 de frontend, Playwright 121/121 (incluye
`service-pos`).
Estado: VERIFICADO @ `ee3a8d3` (rama `reconcile/uxui-after-svc`, pendiente de merge).

**STOREFRONT-V3** — La tienda pública no supone qué vende ni quién es la empresa.
Autoridad: `app/lib/catalog-categories.ts::useCatalogCategories` (única lista de
categorías; cabecera, pie y portada); `Hero.tsx` (losa `bg-slab` en ambos temas;
campaña `campaigns.home_hero` opcional; sin imagen por slug); `StoreInformation.tsx`
(`/about`, `/contact`, sólo datos de `useStorefront()`); `StorefrontChrome.tsx`
(`StorefrontContent` marca `shop-surface` / `internal-surface`; sin armazón en
`/admin`; ningún archivo bajo `app/admin` cambia y el panel se comprobó idéntico
píxel a píxel contra master en cuatro pantallas); `globals.css` (clases `v3-*`, la
variable `:root --v3-ease-out`, `@starting-style` y un `@keyframes`; ningún selector
de elemento que alcance al panel).
Tests: `storefront-v3-navigation`, `storefront-v3-home`, `storefront-v3-information`,
`storefront-v3-products`; E2E `storefront-v3`, `storefront`, `brand-contrast` (sin
modificar).
Estado: VERIFICADO @ `7f49168` (rama `reconcile/storefront-v3-after-ux`, pendiente de merge).

**PROXY-01** — El proxy Next nunca sale de `${BACKEND_API}/`.
Autoridad: `frontend/app/api/[...path]/route.ts` (rechaza con 400 segmentos `.`, `..` o con `/` `\` tras decodificar).
Tests: `frontend/__tests__/api-proxy-scope.test.ts` (11/11).
Estado: VERIFICADO @ `4a9dd5c`.

**MEAS-CONSENT-01** — Sin el permiso de su categoría no se carga el script de un proveedor ni se le envía nada, desde el navegador ni desde el servidor.
Autoridad: `frontend/app/lib/analytics/service.ts` (`allowed`, `reconcile`, `dispatch`), `frontend/app/lib/consent.ts`; `backend/store/measurement/conversions.py` (`_capture` con `is True`, `_plan`, re-comprobación en `deliver`).
Tests: `analytics-service.test.ts`, `consent-banner.test.tsx`; `test_measurement_conversions.ConsentTest`; E2E `analytics-consent`.
Estado: VERIFICADO en `feat/analytics-marketing`.

**MEAS-PRIVATE-01** — Un script de medición y una dirección privada nunca están en el mismo documento; los de marketing tampoco en una página con formulario de datos personales.
Autoridad: `frontend/app/lib/analytics/privacy.ts` (`mayMeasure`), `service.ts` (`guardHistory`: `pushState`/`replaceState` envueltos, `popstate` en captura); `frontend/app/lib/payment-reference.ts`; `app/repairs/page.tsx` (enlace de seguimiento como `<a>`).
Tests: `analytics-service.test.ts` («a loaded script never sees a private address»), `analytics-wiring.test.tsx`; E2E `analytics-consent` (navegación interna y botón Atrás).
Estado: VERIFICADO.

**MEAS-PURCHASE-01** — Una venta es una conversión por proveedor, nacida de la notificación firmada de la pasarela.
Autoridad: `backend/store/views.py::_confirm` → `conversions.record_purchase` (punto de guardado) → `send_after_commit` (tras el correo, `robust`); `ConversionDelivery` única por pedido, proveedor y evento; `browser_purchase` sólo para un pedido pagado y durante una hora.
Tests: `test_measurement_conversions` (OneSaleOneConversionTest, NeverInTheWayTest, RuntimeTest); E2E `analytics-consent`.
Estado: VERIFICADO.

**MEAS-BOUNDARY-01** — `gtag`, `fbq` y `ttq` existen sólo en `frontend/app/lib/analytics/adapters/`; las direcciones de los scripts y de las API son constantes del código; ningún ID viene de `NEXT_PUBLIC_*`.
Autoridad: los tres adaptadores; `backend/store/measurement/adapters.py`; proveedores sin campos de URL.
Tests: `analytics-boundaries.test.ts`; `test_measurement_providers.RegistryTest`.
Estado: VERIFICADO.

**INT-SECRET-01** — Un secreto de integración se escribe y no vuelve.
Autoridad: `backend/store/integrations/secret_store.py` (Fernet, clave raíz `APP_CONFIG_ENCRYPTION_KEY`, fuera de la base), `service.py` (`_describe_row`: sólo `configured`, cuándo y quién; nada del secreto queda en claro; `_audit`: nombres de campos, nunca valores). `Resolved` no imprime sus secretos.
Tests: `test_integrations_core` (SecretStoreTest, WriteOnlyApiTest, AuditTest), y en cada `test_integrations_<proveedor>` «no secret comes back»; `frontend/__tests__/integrations-console.test.tsx`; E2E `integrations-console`.
Estado: VERIFICADO en la rama `feat/integrations-console`.

**INT-MASTER-01** — La consola de integraciones es sólo del MASTER.
Autoridad: `backend/store/integration_views.py` (`IsAuthenticated` + `IsPlatformAdmin` en las cuatro vistas); `frontend/app/admin/lib/internal-modules.ts` (`platformAdminOnly`, comprobado antes que capacidades y rol antiguo).
Tests: `test_integrations_core.MasterOnlyTest` (administrador con todas las capacidades, técnico, `is_staff`, cliente, anónimo × todas las rutas); `internal-modules-access.test.ts`; `integrations-page.test.tsx`; E2E.
Estado: VERIFICADO.

**INT-OFF-01** — Apagado es apagado: una integración desactivada en la consola, o guardada y que el servidor no puede leer, no recurre al entorno.
Autoridad: `service.resolve` / `resolve_panel`; `integrations/mail.py::_legacy_backend`; `integrations/payments.py::active_code`; `messaging/__init__.py::_console`; `fiscal_config._console_row`.
Tests: «does not fall back» y «cannot read» en cada `test_integrations_<proveedor>`.
Estado: VERIFICADO.

**INT-ACTIVATE-01** — Sólo se activa lo que pasó la prueba, y esa versión.
Autoridad: `service.activate` (borrador bloqueado, `version` exacta, `validated`); `service.test` cambia la versión y sólo escribe su resultado si la fila sigue en la versión que leyó (409 si no); guardar borra `validated`.
Tests: `test_integrations_core` (LifecycleTest, ConcurrencyTest); ensayo, paso 11c.
Estado: VERIFICADO.

---

## 4. Mapa de dominios

### Tenancy / RBAC
- `tenancy.py`: `resolve_company_for_user`, `resolve_capabilities`, `has_capability`, `can_delegate_capabilities`, `can_manage_company_memberships`, `can_grant_company_role`, `is_platform_admin`, `uses_legacy_bridge`, `resolve_public_storefront_company`, `resolve_storefront_company`.
- Vistas: `tenant_views.py` (`AdminCompany*`, `AdminBranch*`, `AdminMembershipListView`, `AdminMembershipDetailView`), `access_views.py` (áreas, roles), `staff_views.py`.
- Modelos: `Membership` (`branch_access_mode`), `MembershipBranchAccess`, `CompanyRole`, asignaciones de rol.
- Tests: `Phase2aCapabilityMatrixTest`, `Phase2dBranchAccessApiTest`, `C15BranchAccessRevocationTest`, `M11AntiEscalationTest`, `H412*`.
- Docs: `docs/saas-multiempresa.md`, `docs/rbac-access-audit-2026-09-02.md`.

### Sucursal (DÓNDE)
- `tenancy.py`: `can_delegate_branch_scope`, `visible_branches`, `visible_branch_ids`, `_branch_authority`, `_granted_branches`, `describe_branch_scope`, `has_branch_access`, `assert_branch_access`, `assert_branch_in_company`, `default_branch_for_user`, `resolve_branch_for_user`.
- Escritores de sucursal: `tenant_views.py::_apply_branch_access`, `promotion_views.py::_write_promotion`, `settings_views.py::AdminSequenceDetailView.patch` / `AdminSequenceScopeView`.
- Frontend: `app/admin/staff/page.tsx`, `app/admin/users/page.tsx`, `app/lib/staff.ts`, `app/admin/lib/internal-api.ts` (sequences, promotions).

### Servicio técnico
- `service_services.py` (`eligible_technicians`, `assign_technician`, `create_repair_order_with_assignment`, `record_service_payment`), `v1_service_views.py` (`V1ServiceSurfaceMixin.get_order`, asignación devuelve `{current, candidates}`, `V1ServiceTechnicianCandidatesView`, `require_assignment_authority`, `COLLECT_AUTHORITY`; la lista de órdenes acepta `status=a,b,c`).
- Capacidades separadas en SVC-FUNC-01: `service.orders.assign` (de `service.orders.manage`), `service.payments.collect` (de `service.payments.manage`). La amplia sigue implicando la estrecha.
- Frontend: `app/lib/service-console.ts` (`mayAssignTechnician`, `mayCollectPayment`), `app/admin/service/` (`queues.ts`, `components/ServiceQueue.tsx`, `components/ServiceIntake.tsx`, una página por cola), `app/admin/sales/pos/PosModeSwitch.tsx`, `PosServiceIntake.tsx`.
- Tests: `M8*`, `M9*`, `M10CapabilitySeparationTest`, `StabilizationServiceAccessTest`, `P0CServiceTransitionIsolationTest`, `Svc*`.

### Inventario
- `inventory_services.py`, `inventory_views.py`, `v1_inventory_views.py`, `v1_transfer_views.py`; modelos `StockMovement`, `BranchStock`, transferencias, conteos.
- Tests: `Phase60StockMovementRulesTest`, `M7Inventory*`, `C11TransferReserveTest`, `C14StockWriterDisciplineTest`, `Ip1TransferWebParityTest`.

### POS / ventas / promociones
- `pos_views.py`, `v1_pos_views.py`, `pos_services.py`, `promotion_services.py`, `promotion_views.py`.
- Tests: `C1PosSecurityTest`, `HardeningPosPaymentAuthorityTest`, `Ip1Pos*`, `C13Promotion*`, `C14PromotionTenantInvariantTest`, `Fiscal6ReceiptOptionsTest`.

### Fiscal
- `store/fiscal/`, `fiscal_services.py`, `fiscal_note_services.py`, `fiscal_void_services.py`, `fiscal_summary_services.py`.
- Tests: `C22B*`, `Fiscal5a*`, `Fiscal5b*`, `Fiscal6DiscountDeclarationTest`.
- Decisiones: ADR-1…ADR-43 (`docs/adr-fiscal-c22a1.md`), DEC-42/43.

### Auth / sesiones
- `auth_views.py`, `v1_auth_views.py`, `authentication.py`, `token_revocation.py`, `throttles.py`; `backend/backend/settings.py` (throttles, sin `CACHES`).
- Docs: `docs/adr-auth-v1-internal.md`, `docs/adr-token-revocation.md`.

### Frontend auth / proxy
- `frontend/app/api/[...path]/route.ts`; sidebar `app/admin/lib/internal-modules.ts::canAccessModule`.

---

## 5. Hallazgos abiertos

Estado de auditoría: CONFIRMADO salvo que se diga. Todos PENDIENTE de corrección.
Detalle y reproducción: checkpoint «AUDIT F1» (sección 11).

| ID | Sev. | Dominio | Símbolo | Reproducción |
|---|---|---|---|---|
| F-TENANT-01 | MEDIUM | TENANCY | `tenant_views.py::AdminMembershipListView.post`; `test_membership_consent.py` | CORREGIDO: alta directa sólo platform admin; administradores de empresa usan invitación + aceptación; sin oracle de username ni enrolamiento sin consentimiento |
| THROTTLE-CACHE-01 | MEDIUM | INFRA/AUTH | `backend/backend/settings.py` (sin `CACHES`) | LocMemCache por proceso: límite ×N workers |
| IZIPAY-PRODUCT | MEDIUM | PAYMENTS | `payments/izipay.py`, `payments/micuentaweb.py`, `checkout_services.py::require_payment_provider_configured` | BLOCKED/CREDENTIALS: Izipay tiene dos productos y los dos están integrados detrás de `PAYMENT_PROVIDER`; falta saber cuál tiene el propietario y probarlo en TEST (`MiCuentaWebSandboxSmokeTest` / `IzipaySandboxSmokeTest`) |
| IZIPAY-TOKEN-CONTRACT | MEDIUM | PAYMENTS | `payments/izipay.py::request_session_token`, `checkout_services.py::build_payment_config` | BLOCKED/CREDENTIALS: la forma exacta del cuerpo de `Token/Generate` no se pudo leer en la referencia pública; sólo el sandbox real lo confirma (`IzipaySandboxSmokeTest`) |
| FISCAL-PRINT-EXO | LOW | FISCAL | `fiscal_pdf_services.py`, `fiscal/representation.py` | PENDIENTE: totales exonerado/inafecto y descuentos no se imprimen por separado; el dominio sólo emite operaciones gravadas |
| PRINT-USB | LOW | PRINTING | `backend/print_agent/agent.py` | PROPUESTA: sólo impresoras en red; USB y Bluetooth fuera |
| AUDIT-01 | LOW | AUDIT | `service_services.py::add_quote_item`, `remove_quote_item`, `update_quote` | PROPUESTA: editar una cotización en borrador no deja fila; `publish_quote` y `cancel_quote` sí |
| AUDIT-02 | LOW | AUDIT | `inventory_services.py::set_transfer_item` | PROPUESTA: editar líneas de una transferencia en borrador no deja fila; crear, despachar, recibir y cancelar sí |
| AUDIT-03 | LOW | AUDIT | `inventory_services.py::set_count_item` | PROPUESTA: anotar cantidades de un recuento no deja fila; crear, aprobar y cancelar sí |
| AUDIT-04 | LOW | AUDIT | `announcement_services.py::set_audience`, `update_draft`, `create_draft` | PROPUESTA: preparar un comunicado no deja fila; `publish` y `cancel_draft` sí |
| AUDIT-05 | LOW | AUDIT | `service_services.py::update_diagnostic`, `update_execution` | PROPUESTA: notas de trabajo en curso sin fila; los cambios de estado de la orden sí |
| AUDIT-06 | LOW | AUDIT | `service_services.py::record_quality_result` | PROPUESTA: el resultado de control de calidad no deja fila propia; la entrega sí |
| CSP-01 | LOW | FRONTEND | `frontend/next.config.ts` | PROPUESTA: la política sólo limita marcos y `<base>` (sin `unsafe-*` ni comodines). Una política de scripts exige un nonce por petición y la captura de red del SDK de Izipay en sandbox |
| LINT-EFFECT-01 | LOW | FRONTEND | 23 sitios, 20 pantallas del panel | PROPUESTA: `react-hooks/set-state-in-effect`; quitarlo cambia el patrón de carga de datos. No se silencia |
| INTERNAL-UI-KIT | LOW | FRONTEND | `admin/components/internal-ui.tsx`, `InventoryUi.tsx`, `service/components/ServiceUi.tsx` | PROPUESTA: `Panel`, `Field`, `Button`, `StatCard` definidos en tres sitios. La rama `uxui/phase-03-internal-ui` (578 commits detrás) es OBSOLETA: no se mergea |
| DEP-TS7 · DEP-ESLINT10 | LOW | INFRA | `frontend/package.json` | PROPUESTA: TypeScript 7 y ESLint 10 rompen la CI (#71, #72 cerrados); migración planificada |
| DEP-TS6 | LOW | INFRA | `frontend/package.json` | PROPUESTA: TypeScript 6 (#80, abierta). Su CI pasa; es una versión mayor que el lanzamiento no necesita |
| HERO-VARIANT-LEGACY | LOW | STOREFRONT | `StorefrontPageSettings.hero_variant`, `frontend/app/lib/storefront.ts` | OBSOLETO: la V3 tiene un solo hero y no lee el campo; retirarlo del modelo y de la API en una fase de limpieza |
| EVIDENCE-CUSTOMER-WEB | LOW | SERVICE | `evidence_views.py::CustomerEvidenceListView` | PENDIENTE: el contrato del cliente existe (sólo lo compartido, con su nota); la web no tiene pantalla que lo use |
| TEST-ORDER-C15 | LOW | TESTS | `tests.py::C15InitialRaceTest` (`TransactionTestCase`, `reset_sequences`) | PENDIENTE: falla con «duplicate key store_company_pkey» si es la primera clase en ejecutarse; pasa en la suite completa. Anterior a esta fase |
| CI-03 | MEDIUM | INFRA | `.github/dependabot.yml` | CORREGIDO: PR semanales para pip, npm y Actions, sin merge automático. Pendiente del propietario: alertas de vulnerabilidad, escaneo de secretos y protección de push en la configuración del repositorio |
| INV-LEGACY-V1-F1 | LOW | INVENTORY | `serializers.py` movimientos legacy | |
| Sweep LOW/INFO | LOW/INFO | varios | SEC-SET-01/07/08, SEC-SET-04-B, REFRESH-CSRF-01, ENUM-01, COOKIE-PATH-01, ENV-01/03, DEP-04/06…08, CI-02 | ver checkpoint |

SIN VEREDICTO (no abiertos por F1): SEC-01…SEC-10 (secrets). De `frontend-auth`:
FE-AUTH-06 corregido (sección 6); FE-AUTH-08
ACEPTADO (tarjeta de demostración sólo en desarrollo); FE-AUTH-09 PROPUESTA (tienda
por dominio); FE-AUTH-02/04/07 corregidos, ver sección 6.
Presets vigentes (`company_provisioning.PRESET_ROLES` @ `c042fea`): sólo `administrador` tiene
`products.manage`, y también tiene `inventory.adjust`; `inventario` tiene `inventory.adjust`.

DEUDA (surgida en F-CAP-01): Django admin `ProductAdmin` deja editar `Product.inventory` a
un superusuario de plataforma sin línea de Kardex (fuera de RBAC de empresa).
E2E bajo `--repeat-each`: `fiscal-invoice` se omite en la segunda pasada por el limitador
`admin_orders` (120/min, E2E-FISCAL-THROTTLE documentado en el spec); una pasada normal no se omite.
TEST-ENV-01: `C15InitialRaceTest.test_a_deactivated_branch_is_refused_at_preview_too` da
`IntegrityError store_company_pkey` si se ejecuta aislado (también @ `7977d53`); pasa en la
suite completa. Además, tras una `TransactionTestCase` una BD `--keepdb` queda sin datos
sembrados: usar `--noinput` (BD nueva) para subconjuntos.

NO AUDITADO: webhook de pagos (firma/replay), `AdminAuditLogListView` (acotado por tenant),
contrato `V1StockAdjustmentSerializer`.

NO AUDITADO (surgido en F2): una admin SELECTED con `company.manage` puede editar la
serie de nivel empresa y cambiar el alcance de numeración (`AdminSequenceScopeView`),
que afectan a todas las sucursales; y lista promociones de todas las sucursales
(lectura). La parte de escritura (sucursales, despacho, serie de empresa, alcance de
numeración, ajustes de empresa) quedó cerrada por WRITE-SCOPE-01.
DEUDA UX (surgida en la reconciliación de #39, `1fea6b9`): el rediseño de #39 NO se adoptó en
los archivos que ambos lados reescribieron y donde master lleva funcionalidad que #39 no
conoce (escaparate por CMS, tema claro/oscuro, `BrandLogo`): `app/page.tsx`, `components/Hero.tsx`,
`Header.tsx`, `Footer.tsx`, `services/page.tsx`, `layout.tsx`, `globals.css` y `auth/page.tsx`.
Tampoco `AppChrome.tsx` (duplicaba `StorefrontChrome`; se pierde su enlace «Saltar al contenido»)
ni la fila de sellos de confianza de `ProductDetail`. #39 queda PARCIAL.
NO AUDITADO bajo WRITE-SCOPE-01: `storefront_content_views` (campañas y páginas de la
tienda, también de nivel empresa) y otras mutaciones de nivel empresa fuera de
`tenant_views`/`settings_views`.
`GET /admin/capabilities/` (`access_views.CapabilityCatalogView`) sigue abierto a
cualquier miembro: devuelve el catálogo de la plataforma y las capacidades del propio
llamador, sin datos del tenant. Evaluado en RBAC-01 y aceptado.

---

## 6. Hallazgos cerrados

| ID | Sev. original | Fix SHA | Símbolo | Regresión | Estado |
|---|---|---|---|---|---|
| FE-AUTH-01 | HIGH | `4a9dd5c` | `frontend/app/api/[...path]/route.ts` | `frontend/__tests__/api-proxy-scope.test.ts` 11/11 + Playwright `pos-receipt-options`, `pos-ticket`, `h411-auth-interop` 8/8 | CORREGIDO |
| F-BRANCH-01 | MEDIUM | `20d110c` | `tenancy.can_delegate_branch_scope`; `tenant_views` membership create/patch; `staff_views` invitaciones (mismo hueco, hallado en F2) | `F2BranchDelegationTest` | CORREGIDO |
| F-BRANCH-01 · escritura parcial | LOW | `20d110c` | `AdminMembershipDetailView.patch` en `transaction.atomic` | `F2BranchDelegationTest.test_a_rejected_grant_list_rolls_the_whole_update_back` | CORREGIDO |
| F-BRANCH-02 | MEDIUM | `cccb4d2` | `promotion_views._write_promotion` | `F2PromotionBranchScopeTest` | CORREGIDO |
| E2E-02 | MEDIUM | `9c3445f` | `seed_demo_users --e2e-fixtures` + `staff-personnel.spec.ts` test I | `F2E2eFixtureSeedTest`; Playwright 118/118 | CORREGIDO |
| E2E-01 | MEDIUM | `c191a84` | `fiscal-invoice.spec.ts` (deriva del test; el producto era correcto) | `fiscal-document-panel.test.tsx`; Playwright 118/118 | CORREGIDO |
| DRIFT-01 | MEDIUM | `a4be03b` | `service-console.fetchServiceAssignmentOptions`, `service/orders/[id]/page.tsx` | `service-assignment-contract.test.tsx` | CORREGIDO |
| DRIFT-07 | LOW | `6958ec0`, `f6dc9ca` | `branch-authority.ts` + 6 pantallas | `admin-write-scope.test.tsx` | CORREGIDO |
| WRITE-SCOPE-01 · sucursales | MEDIUM | `fa85d41` | `tenant_views` crear/editar sucursal, sucursal de despacho | `F2SelectedBranchWriteScopeTest` | CORREGIDO |
| WRITE-SCOPE-01 · configuración | MEDIUM | `c076120` | `settings_views` serie de empresa, alcance, ajustes | `F2SelectedCompanySettingsScopeTest` | CORREGIDO |
| RBAC-01 | LOW | `5afdb81` | `tenant_views` company/branch list+detail vía `access_views._scope_readable` | `F2TenantReadAuthorizationTest` | CORREGIDO |
| RBAC-02 | LOW | `d18e983` | `tenant_views._grantable_branch` (POST y PATCH de membresías) | `F2MembershipBranchOracleTest` | CORREGIDO |
| F-CAP-01 | MEDIUM | `c042fea` | `admin_views.AdminProductListView.post` (única ruta pública que abre saldo) | `F2ProductInitialInventoryCapabilityTest` | CORREGIDO |
| F-BRANCH-03 | MEDIUM | `70286d1` | `settings_views.AdminSequenceDetailView._scoped` | `F2SequenceBranchScopeTest` | CORREGIDO |
| SVC-NAV-01 | MEDIUM | `937cf82` | `internal-modules.ts`, `app/admin/service/queues.ts`, `ServiceQueue.tsx`; `V1ServiceOrderListView` (`status` múltiple) | `service-navigation.test.tsx`, `SvcQueueStatusFilterTest` | CORREGIDO |
| SVC-ASSIGN-01 | MEDIUM | `1928b05` | `eligible_technicians`, `V1ServiceOrderAssignmentView`, `V1ServiceTechnicianCandidatesView`, migración 0094 | `SvcAssignCapabilityTest`, `SvcTechnicianEligibilityTest`, `SvcAssignPresetTest` | IMPLEMENTADO |
| SVC-ASSIGN-UNASSIGN | MEDIUM | `4796db0` | `V1ServiceOrderAssignmentView.post` (null exige `service.orders.manage`); «Quitar» en `orders/[id]/page.tsx` | `SvcUnassignAuthorityTest`, `service-authority-console.test.tsx` | CORREGIDO |
| SVC-ASSIGN-VIEW-01 | MEDIUM | `9b59a31` | `V1ServiceSurfaceMixin.require_assignment_authority` en candidatos y en recepción con técnico (antes bastaba `assign` sin `view`) | `SvcAssignVisibilityTest` | CORREGIDO |
| HERO-MOBILE-CLIP | MEDIUM | `ff564e9`, `e082b26` | `Hero.tsx` (columna `min-w-0`, titular `min(clamp(…), 7.4vw)`), `product/page.tsx`, `ProductDetail.tsx` | E2E `hero-mobile-clip`, `storefront-text-fit` | CORREGIDO (pendiente de merge) |
| FE-AUTH-05 | MEDIUM | `4fad36b` | `frontend/app/api/[...path]/route.ts` (`readBody`, `API_PROXY_MAX_BODY_BYTES`, 413) | `frontend/__tests__/api-proxy-body-limit.test.ts` | CORREGIDO |
| RBAC-F3 · RBAC-F5 | MEDIUM | `847d3bf` | `app/admin/lib/internal-modules.ts::canAccessModule` (misma regla que `InternalAccess.can`); `admin.audit` declara `memberships.view` | `frontend/__tests__/internal-modules-access.test.ts` | CORREGIDO |
| RBAC-F4 | LOW | `58d17de` | `app/admin/staff/page.tsx` (`canManage` = `memberships.manage`) | `frontend/__tests__/staff-screen-authority.test.tsx` | CORREGIDO |
| RBAC-F6 · F7 · F11 | LOW | — | transfers `[id]` (`mayTransfer`), `users/page.tsx` (`canManage`), `orders/[id]` (`access.can`) | revisado @ `64b4d5e` | OBSOLETO (ya corregido en master) |
| DRIFT-02 | MEDIUM | `068bee1` | `app/admin/components/InventoryAdjustForm.tsx` (selector de sucursal), `app/lib/admin.ts::adjustInventory` (`branch` opcional) | `frontend/__tests__/inventory-adjust-branch.test.tsx` | CORREGIDO |
| FE-AUTH-02 | LOW | `5adcd28` | `app/lib/cart.ts::getSessionKey` (`crypto.randomUUID` / `getRandomValues`) | `frontend/__tests__/cart-session-key.test.ts` | CORREGIDO |
| FE-AUTH-07 | LOW | `40f9769` | `app/lib/auth.ts::fetchWithAuth` (`isOwnApiUrl`) | `frontend/__tests__/fetch-with-auth.test.ts` | CORREGIDO |
| FE-AUTH-04 | LOW | `113a8dd` | `frontend/next.config.ts` (`headers()`) | `frontend/__tests__/security-headers.test.ts` | CORREGIDO (sin política de scripts: CSP-SCRIPT = PROPUESTA) |
| SEC-SET-04-A | MEDIUM | `e066183` | `throttles.RefreshThrottle` (por IP, sólo peticiones con credencial) en `RefreshView` y `V1RefreshView` | `store/test_refresh_throttle.py` | CORREGIDO |
| SEC-SET-02 | MEDIUM | `79d1077` | `backend/urls.py::build_urlpatterns` (admin sólo con `DEBUG`) | `store/test_production_url_surface.py` | CORREGIDO |
| INV-LEGACY-V1-F2 · F3 | MEDIUM · LOW | `1f965f7` | `inventory_services.set_transfer_item` (`select_for_update`), `v1_transfer_views` (`page_size` ≥ 1) | `store/test_transfer_line_lock.py` | CORREGIDO |
| SEC-SET-03 · 05 · 06 · 09 · 10 · ENV-02 | LOW | `e2dff73` | `backend/settings.py`, `xlsx_reader.check_upload` | `store/test_production_settings.py` | CORREGIDO |
| AUTH-LOGGING-01 | LOW | `7d5efc8` | `store/security_log.py`, `LoginView`, `V1LoginView` | `store/test_security_log.py` | CORREGIDO |
| ENV-04 | LOW | `a6beda6` | `.env.example` | — | CORREGIDO |
| CI-01 | MEDIUM | `ba4e9ff` | `.github/workflows/backend-postgres-validation.yml` (encontró `qrcode` sin declarar y una prueba dependiente de la colación) | CI en PR | CORREGIDO |
| AUDIT-07 | LOW | `06cd798` | `admin_views.py` (`product_updated`/`deactivated`/`reactivated`, `category_created` con `company`) | `store/test_audit_company.py` | CORREGIDO |
| DEP-05 | MEDIUM | `65d34ad` | `backend/requirements.txt` | CI de backend en instalación limpia | CORREGIDO |
| FE-AUTH-06 | LOW | `085aa2e` | `frontend/app/api/[...path]/route.ts::fetchFollowingOwnRedirects` (`redirect: "manual"`) | `frontend/__tests__/api-proxy-redirects.test.ts` | CORREGIDO |
| SKIP-LINK | LOW | `cf72ee5` | `StorefrontChrome.tsx::SkipLink`, `#contenido`, `#admin-main-content` | `frontend/__tests__/skip-link.test.tsx`, E2E `skip-link` | CORREGIDO |
| INFRA-01…08 · DEP-01…03 · DOC-01 | MEDIUM→LOW | `91f1c9f`, `9b76dfd` | `backend/Dockerfile.prod` (gunicorn, sin root, sin migrar al arrancar), `frontend/Dockerfile.prod` (`npm ci`, `next build`, Node 22), `docker-compose.prod.yml` (sólo Caddy publica, healthchecks, sin contraseñas escritas), `.dockerignore` | ensayo de 31 comprobaciones, `docs/despliegue-produccion.md` §9 | CORREGIDO para producción (los archivos de desarrollo no cambian) |
| TOKEN-HYGIENE-01 | LOW | `f0c29ce` | `docs/despliegue-produccion.md` §6.1.1 (`flushexpiredtokens` diario) | ensayo: el comando corre sin error | CORREGIDO (procedimiento) |
| STOREFRONT-HERO-VARIANT | — | `0e9db9d`, `9c39716` | `StorefrontPageSettings.hero_variant` / `hero_image_url`, `Hero.tsx` (`HeroLight`) | `store/test_storefront_media.py`, `storefront-v4-home.test.tsx`, E2E `storefront-v4-images` | IMPLEMENTADO |
| STOREFRONT-IMAGE-UPLOAD | — | `0e9db9d`, `57ed321` | `storefront_media.py` (PNG/WebP conservan alfa), `StorefrontImage`, `ImageUploadField.tsx`, migración 0096 | `store/test_storefront_media.py`, `image-upload-field.test.tsx` | IMPLEMENTADO |
| STOREFRONT-IMAGE-CLEANUP | — | `634a6e8`, `6f29193` | `storefront_media.py` (`reference_fields`, `reference_count`, `claim`, `release`, `unplaced`), `cleanup_storefront_images` | `store/test_storefront_image_cleanup.py` (23), `test_storefront_media.py` | CORREGIDO: una imagen reemplazada o nunca colocada se borra sólo con cero referencias en toda la plataforma; contar y colocar bloquean la misma fila |
| PRODUCT-MEDIA | — | `1f12e26`, `8165640` | `product_media.py`, `product_media_views.py`, `ProductImage`, migración `0100`; `ProductGallery.tsx` | `test_product_images.py`, `product-gallery.test.tsx`, `e2e/media.spec.ts` | IMPLEMENTADO: galería subida desde el panel; cierra PRODUCT-PHOTO-UPLOAD |
| BULK-MEDIA | — | `4a1d096`, `860e700` | `import_media.py`, `import_services.py::_place_row_images`, `ImportWizard.tsx` | `test_import_media.py`, `product-import-media.test.tsx`, `e2e/media.spec.ts` | IMPLEMENTADO: imágenes y ZIP en la carga masiva; imágenes en espera = sin colocar de la empresa |
| SERVICE-EVIDENCE-CONTEXT | — | `77baf36`, `17c4f5f` | `evidence_services.py`, `evidence_views.py`, migración `0101`; `EvidenceGallery.tsx` | `test_evidence_context.py`, `evidence-workflow.test.tsx`, `e2e/media.spec.ts` | IMPLEMENTADO: nota, tres etapas, conteo; varias fotos, visor |
| EVIDENCE-THROTTLE | MEDIUM | `3354cd6` | `evidence_views.py::_InternalEvidenceMixin.get_throttles`, `throttles.py` | `test_evidence_context.py::EvidenceThrottleTest` | CORREGIDO: la galería compartía el cupo de 60/min de cambios de estado |
| IMPORT-ERRORS-404 | MEDIUM | `c38c037` | `urls.py` (`errors.csv/`) | `tests.py::C14ImportApiTest` | CORREGIDO: Caddy y el proxy añaden barra final; la ruta no la tenía |
| AUTH-REGISTER-CSRF | LOW | `755e98e` | `frontend/app/lib/auth.ts::register` | `register-csrf.test.ts` | CORREGIDO |
| DEV-PROXY-UPLOAD | LOW | `7382918` | `frontend/app/api/[...path]/route.ts` (`expect`) | `api-proxy-expect.test.ts` | CORREGIDO: se reenviaba `Expect: 100-continue`; sólo fallaba con curl |
| MEDIA-REVIEW | MEDIUM→LOW | `50598c5`, `608beb7`, `3fd55f2` | `settings.py` (`DATA_UPLOAD_MAX_NUMBER_FILES`), `import_media.py::_check_end_record`, `admin_views.py::AdminProductDetailView.patch` (bloqueo), `import_services.py` (URL gestionada) | `ReviewHardeningTest` en `test_import_media.py` y `test_product_images.py` | CORREGIDO: 12 hallazgos de la revisión independiente, ninguno entre empresas |
| CART-CSRF-01 | MEDIUM | `78eee09` | `frontend/app/components/ProductDetail.tsx`, `frontend/app/cart/page.tsx` (escrituras por `fetchWithAuth`) | `__tests__/cart-session-csrf.test.tsx` | CORREGIDO: con sesión iniciada el carrito respondía «CSRF token missing» al agregar, cambiar, quitar o validar un cupón |
| FRONTEND-V3 | — | `5d46635`, `288dd8d` | `frontend/app/page.tsx`, `components/Hero.tsx`, `components/ProductImage.tsx`, `globals.css`, `admin/components/*` | Jest 609, Playwright 176 (`motion-carousel`, `storefront`, `hero-mobile-clip`, `storefront-v4-images`) | IMPLEMENTADO: la V3 es el único diseño; el anterior queda en la etiqueta `frontend-anterior-2026-10-04` |
| DEP-REPORTLAB5 | LOW | `8196f6b` | `backend/requirements.txt` | comparación visual 4.4.10 / 5.0.1 de nota de venta, ticket, recibo y comprobante fiscal | CORREGIDO: integrada tras validar los PDF a la vista |
| IZIPAY-REPLAY-INTEGRITY | MEDIUM | `29bbf3a` | `views.py::IzipayNotificationView._record_integrity_failure` (sólo desde `PENDING`) | `test_izipay_contract.py` | CORREGIDO: un mensaje contradictorio tras autorizar ya no reescribe el intento |
| IZIPAY-CONTRACT-HARNESS | — | `ca73423` | `payments/fake_izipay.py` (`FakeIzipay`) | `test_izipay_contract.py` (21) | IMPLEMENTADO: token y notificaciones sin credenciales |
| FISCAL-PRINT-LABEL | MEDIUM | `ca91705` | `fiscal_pdf_services.py::_customer_block`, `fiscal/representation.py` | `test_fiscal_print.py` (10) | CORREGIDO: etiqueta por catálogo 06, leyenda, unidad, precio, letras, forma de pago |
| FISCAL-LOGO-SNAPSHOT | — | `dc8fad8` | `fiscal_logo.py`, `FiscalDocument.logo_storage_key`, migración `0098` | `test_fiscal_logo.py` (10) | IMPLEMENTADO |
| PRINT-QUEUE | — | `86de056`, `264292f`, `0dd10be` | `printing/services.py`, `printing/escpos.py`, `print_views.py`, `print_agent/agent.py`, migración `0099` | `test_printing.py`, `test_printing_api.py`, `test_print_agent.py`, `test_payment_to_print.py` | IMPLEMENTADO |
| IMPORT-UPLOAD-415 | MEDIUM | `b5de287` | `app/lib/auth.ts::fetchWithAuth` (descarta el `Content-Type` del llamador con `FormData`), `postForm` | `fetch-with-auth.test.ts` | CORREGIDO |
| SVC-TX-01 | MEDIUM | `1928b05` | `assign_technician` sin transacción propia (decorador desplazado a `_notify` en `108a904`) | `SvcAssignOutsideATransactionTest` | CORREGIDO |
| SVC-PAY-01 | MEDIUM | `d62fa30` | `V1ServicePaymentView.post`, `PaymentSection` (`canCollect` / `canReverse`), migración 0095 | `SvcPaymentCollectTest`, `SvcCollectPresetTest`, `service-authority-console.test.tsx` | IMPLEMENTADO |
| POS-SVC-01 | — | `6caa88c` | `PosModeSwitch`, `PosServiceIntake`, `ServiceIntake` (técnico obligatorio en caja) | `pos-service-intake.test.tsx`, `SvcIntakeWithAssignmentTest`, E2E `service-pos` (`1d35b7d`) | IMPLEMENTADO |
| DEVICE-IDENTITY | — | `c066ba6`, `a71ccb4` | `device_identity.py`, `service_services.py::create_device`, `V1ServiceDeviceLookupView`, migración `0102`; `DeviceRegistration.tsx` | `test_device_identity.py`, `device-registration.test.tsx` | IMPLEMENTADO: serie/IMEI por tipo, Luhn, NULL en vez de marcadores, historial por equipo acotado a las sucursales visibles |
| TRACKING | — | `a0fe1d4`, `13caf3f`, `1f02a7b` | `tracking_services.py`, `tracking_views.py`, `RepairTrackingLink`, migración `0103`; `app/seguimiento/[token]`, `app/repairs` | `test_tracking.py`, `tracking-view.test.tsx`, `account-repairs.test.tsx`, E2E `service-tracking` | IMPLEMENTADO: enlace opaco con HMAC, una sola escritura, revocable; vista pública por lista de lo permitido |
| QUOTE-DECISION · QUOTE-TICKET | — | `a0fe1d4`, `962697a` | `service_services.py::record_staff_quote_decision`, `reopen_approved_quote`, `quote_ticket.py`, capacidad `service.quotes.record_decision`, migración `0104` | `test_quote_decisions.py`, `test_quote_ticket.py`, `quote-decision-panel.test.tsx` | IMPLEMENTADO |
| NOTIFY-REAL-PATHS | HIGH | `1479cb9` | `service_services.py::_apply_transition` (emite), `_emit_order_created` | `test_service_notifications.py` | CORREGIDO: listo/entregado/repuesto no se avisaban por los pasos reales; las pruebas llamaban al emisor directamente |
| WHATSAPP-NOTIFY | — | `10d9322`, `0fc108c` | `messaging/`, `whatsapp_services.py`, `whatsapp_views.py`, `CompanyMessagingSettings`, `send_pending_notifications`, `configure_whatsapp`, migración `0105` | `test_whatsapp.py`, `customer-notices-panel.test.tsx`, `whatsapp-settings.test.tsx` | IMPLEMENTADO con proveedor falso; envío real BLOQUEADO por credenciales |
| SERIALIZED-STOCK | — | `214c3ef`, `059a8e0` | `stock_unit_services.py`, `stock_unit_views.py`, `inventory_services.py::_units_for_movement`, `StockUnit`, migración `0106`; `StockUnitsPanel.tsx` | `test_stock_units.py`, `stock-units-panel.test.tsx`, `dashboard-equipment.test.tsx` | IMPLEMENTADO: cantidad == equipos disponibles, impuesto en el escritor único |
| STOREFRONT-CATEGORIES · CAROUSEL-MOTION | — | `35f9594`, `b3597f7`, `571ebf9` | `Category.is_active/show_on_home/home_order`, migración `0107`; `CategoriesManager.tsx`, `ProductCarousel.tsx` | `test_storefront_categories.py`, `categories-manager.test.tsx`, `product-carousel-motion.test.tsx` | IMPLEMENTADO |
| GOOGLE-AUTH | — | `697437f`, `7271b3c` | `google_identity.py`, `google_views.py`, `ExternalIdentity`, migración `0108`; `GoogleSignIn.tsx` | `test_google_auth.py`, `google-sign-in.test.tsx` | IMPLEMENTADO; el botón aparece sólo con `GOOGLE_OAUTH_CLIENT_ID` (BLOQUEADO por credenciales) |
| TRACKING-REVOKE · TRACKING-BORN · TRACKING-SPELLING | MEDIUM | `493d1e2`, `1f02a7b` | `tracking_services.py::link_for`, `resolve` | `test_tracking.py` | CORREGIDO |
| CAROUSEL-JUMP · CAROUSEL-RESUME | MEDIUM | `f1de9c4`, `38a50c9` | `ProductCarousel.tsx::jumpTo` (reafirma el destino por fotogramas), `settle` (cancelable) | `product-carousel-motion.test.tsx`, E2E `motion-carousel` | CORREGIDO: un `scrollTo` instantáneo no cancela uno suave en marcha (medido en Chromium); se veía como fallo intermitente del E2E con la máquina cargada |
| READ-ONLY-GET | LOW | `31592f2` | `tracking_services.py::staff_payload` (lectura pura), migración `0109`, `V1ServiceQuoteTicketView` (sin auditoría), `V1ServiceTrackingLinkActionView` | `H411SafeMethodsAreReadOnlyTest`, `test_tracking.py` | CORREGIDO |
| SERVICE-REVIEW | MEDIUM→LOW | `fd99611`, `a718fc0` | `V1ServiceTrackingLinkView` (`reveal/` con `service.quotes.record_decision`), `tracking_services.py::claim` (documento, intentos), `unlink_account`, comparaciones en bytes, `_orders` por sucursal, relectura del modo en `create_stock_movement`, `whatsapp_cloud.py` (sin respuesta no se reenvía) | `test_tracking.py`, `test_whatsapp.py`, `test_google_auth.py`, `test_device_identity.py`, `test_stock_units.py` | CORREGIDO: 2 P2 y 5 P3 de la revisión independiente |
| IDOR-01 | — | — | `admin_views.py::AdminOrderResendEmailView.post` | — | REFUTADO (deuda de limpieza: `order.save(update_fields=…)`) |
| DEV-DEMO-01 | — | — | `dev_accounts_views.py` | — | REFUTADO (fail-closed por `DEBUG`) |

---

## 7. Contratos frontend ↔ backend

| Contrato | Backend | Frontend | Estado |
|---|---|---|---|
| SERVICE-ASSIGNMENT | `{current, candidates}` (`v1_service_views.V1ServiceOrderAssignmentView`; tests M8) | `ServiceAssignment` / `ServiceAssignmentCandidate` (`service-console.ts`), `service-assignment-contract.test.tsx` | OK @ `a4be03b` |
| BRANCH-SCOPE (autoridad) | `dashboard.branch_scope.mode` = `describe_branch_scope` (`platform`/`legacy`/`all`/`selected`/`none`) | `app/admin/lib/branch-authority.ts` | OK @ `f6dc9ca` |
| SERVICE-TECHNICIANS | `GET service/technicians/?branch_id=` → `{candidates:[{id,name}]}` (`V1ServiceTechnicianCandidatesView`) | `fetchServiceTechnicians` (`service-console.ts`), `ServiceIntake.tsx` | OK @ `1d35b7d` |
| SERVICE-INTAKE técnico | `POST service/orders/` acepta `technician_id` opcional; crea y asigna en una transacción | `createServiceOrder`; obligatorio en la caja, opcional en recepción | OK @ `1d35b7d` |
| SERVICE-PAYMENT autoridad | registrar: `collect` o `manage`; reversar: `manage` | `mayCollectPayment` / `CAP_PAYMENTS_MANAGE` en `orders/[id]/page.tsx` | OK @ `1d35b7d` |
| SERVICE-QUEUES | `status` admite varios códigos separados por comas | `app/admin/service/queues.ts` | OK @ `1d35b7d` |
| INVENTORY-ADJUST branch | acepta sucursal / usa default | envía `branch` cuando hay más de una sucursal al alcance (`InventoryAdjustForm`) | OK (DRIFT-02 corregido) |
| DRF field errors | `{field: [msg]}` | clientes que sólo leen `detail` | DRIFT LOW (DRIFT-03) |
| Legacy role sets | backend | frontend | OK (`H412bFrontendLegacyRoleParityTest`) |
| POS `receipt_options` | `enabled` + causa | selector | OK (`Fiscal6ReceiptOptionsTest`, E2E `pos-receipt-options`) |

---

## 8. Tests índice

Backend: `backend/store/tests.py` (≈60 k líneas, 539 clases). Frontend: `frontend/__tests__/`. E2E: `frontend/e2e/`.

- TENANCY: `F2TenantReadAuthorizationTest`, `F2MembershipBranchOracleTest`, `SaasTenancyResolutionTest`, `SaasIsolationApiTest`, `Phase2dCrossTenantIsolationTest`, `Erp1CrossCompanyReadIsolationTest`, `V1TenantSelectorAuthorityTest`, `H412bIsolationMatrixTest`.
- RBAC: `Phase2aCapabilityMatrixTest`, `M11AntiEscalationTest`, `G3*`, `M6CapabilityRevocationTest`, `H412SaasCapabilityAuthorityTest`.
- BRANCH: `F2SelectedBranchWriteScopeTest`, `F2SelectedCompanySettingsScopeTest`, `F2BranchDelegationTest`, `F2PromotionBranchScopeTest`, `F2SequenceBranchScopeTest`, `Phase2dBranchAccessApiTest`, `Phase2dBranchScopedReadsTest`, `C15BranchAccessRevocationTest`, `Phase2eBranchScopeTest`, `Phase2eSequenceApiTest`, `Phase4BranchScopeTest`, `H412SelectedBranch*`.
- INVENTORY: `F2ProductInitialInventoryCapabilityTest`, `Phase60*`, `M7Inventory*`, `C11TransferReserveTest`, `C14StockWriterDisciplineTest`.
- POS: `C1Pos*`, `HardeningPosPaymentAuthorityTest`, `Ip1Pos*`.
- PROMOTIONS: `C13Promotion*`, `C14PromotionTenantInvariantTest`.
- FISCAL: `C22B*`, `Fiscal5a*`, `Fiscal5b*`, `Fiscal6*`.
- SERVICE: `M8*`, `M9*`, `M10CapabilitySeparationTest`, `P0CServiceTransitionIsolationTest`, `SvcQueueStatusFilterTest`, `SvcAssignCapabilityTest`, `SvcAssignVisibilityTest`, `SvcUnassignAuthorityTest`, `SvcTechnicianEligibilityTest`, `SvcIntakeWithAssignmentTest`, `SvcAssignOutsideATransactionTest`, `SvcAssignPresetTest`, `SvcPaymentCollectTest`, `SvcCollectPresetTest`, `SvcCustomServiceLineTest`, `SvcE2eServiceFixtureSeedTest`.
- STAFF: `H41Staff*`.
- MEDIA: `test_product_images.py`, `test_import_media.py` (incluye `ZipSafetyTest`), `test_evidence_context.py`, `test_storefront_media.py`, `M12D*`.
- FRONTEND: `api-proxy-scope.test.ts`, `service-assignment-contract.test.tsx`, `admin-write-scope.test.tsx`, `branch-authority.test.ts`, `service-navigation.test.tsx`, `service-authority-console.test.tsx`, `pos-service-intake.test.tsx`.
- E2E: `h411-auth-interop`, `pos-ticket`, `pos-receipt-options`, `staff-personnel`, `fiscal-invoice`, `service-pos` (requieren `seed_demo_users --company-slug <slug> --e2e-fixtures --fiscal-beta` y los dev servers :3000/:8000).

---

## 9. Migraciones relevantes

| Número | Objeto | Invariante |
|---|---|---|
| 0014 | Company, Branch, Membership | TENANT-01 |
| 0016 / 0017 | áreas, roles, presets | RBAC-G3 |
| 0024 / 0025 | multisucursal, `MembershipBranchAccess`, `branch_access_mode` | BRANCH-01/02 |
| 0029 / 0030 | `InternalSequence` | F-BRANCH-03 |
| 0056 | unicidad de asignación de rol | RBAC |
| 0083 | invitaciones de personal (la invitación lleva su propio `branch_access_mode`) | BRANCH |
| 0084 | revocación de tokens | AUTH |
| 0094 | `service.orders.assign` para Ventas y Administrador sin modificar (conjuntos congelados) | SVC-ASSIGN-01 |
| 0095 | `service.payments.collect` para Servicio Técnico, Supervisor Técnico y Administrador sin modificar; parte de donde termina 0094 | SVC-PAY-01 |
| 0100 | `ProductImage`; `Product.image_url` de `URLField` a `CharField` validado | PRODUCT-MEDIA |
| 0101 | `RepairEvidence.caption`; etapas `parts`, `ready`, `warranty` | SERVICE-EVIDENCE-CONTEXT |
| 0118 | `UserProfile.email_verified_at`, `CompanySettings.risk_review_amount` y `hold_orders_pending_review` (`db_default` apagado), `OrderRiskAssessment`, y el tercer propósito de `AccountToken` (`own_email_proof`: sólo las opciones del campo). Sólo esquema | CLIENT-SEC-01 |
| 0119 | `Carrier`, `CustomerAddress` (una predeterminada activa por cliente), `OrderDelivery` (con `tracking_url_typed`; sin `source_address`). Sólo esquema | DELIVERY-01 |
| 0120 | copia a `email_verified_at` las pruebas que ya existían, en una sola sentencia | CLIENT-SEC-01 |
| 0121 | un `OrderDelivery` por pedido en línea existente con método de entrega; por lotes, no atómica, repetible. Deshacerla no borra nada (los registros los quita deshacer 0119) | DELIVERY-01 |
| 0122 | `BulkImportJob.import_type` gana `product_images` (sólo las opciones del campo) | INVENTORY-UNIFIED-01 |
| 0123 | `CompanySettings.publication_requires_image`, booleano con `db_default` apagado. Sólo esquema; deshacerla quita la columna | INVENTORY-UNIFIED-01 (D2) |
| 0126 | `costs.view` para el `Administrador` sin modificar (conjunto congelado, parte de donde termina 0104). Sólo datos | DEVICE-COMMERCE-01 |
| 0127 | `BranchStock.average_cost`, `Product.reference_cost`, `StockMovement.unit_cost` (nulas) y `cost_source` (`db_default` ''). Sólo esquema, sin backfill | DEVICE-COMMERCE-01 |
| 0128 | `OrderItem.unit_cost`, `unit_price_net`, `discount_net`, `net_amount` (nulas) y `cost_source` (`db_default` 'unknown'). Sólo esquema, sin backfill | DEVICE-COMMERCE-01 |
| 0129 | `BranchStock.uncosted_quantity` (`db_default` 0) y marca como pendiente el stock que ya había sin promedio (`= quantity`) | DEVICE-COMMERCE-01 (revisión DC1) |
| 0130 | `ProductModel`, `VariantAttribute`, `VariantOption`, `ProductVariantOption`; `Product.product_model` (nula), `condition_line` y `variant_key` (`db_default`), único parcial de variante; rótulos de línea en `StorefrontPageSettings`. Sólo esquema | DEVICE-COMMERCE-01 · DC2 |
| 0131 | `CosmeticGrade`, `UnitPhoto`; en `StockUnit`: `public_id` (UUID único, `db_default gen_random_uuid()`: reescribe la tabla, pequeña), batería (CHECK ≤ 100), estado, notas públicas, precio de venta y campos de apartado web. Sin datos | DEVICE-COMMERCE-01 · DC2 |
| 0132 | `OrderItem.stock_unit` (nula); el único (pedido, producto) pasa a parcial (sin equipo), único (pedido, equipo) y CHECK cantidad = 1 con equipo; `OrderUnit.method` gana `buyer` | DEVICE-COMMERCE-01 · DC2 |
| 0133 | `StockUnit.hold_origin` (`db_default` '') y `hold_started_at` (nula): de dónde vino un apartado web y desde cuándo. Sólo esquema | DEVICE-COMMERCE-01 (revisión DC2) |
| 0134 | `Supplier`, `SupplierOffer` (CHECK comprometido ≤ confirmado y no nulo, vigencia; UNIQUE empresa+proveedor+variante+vigencia), `Product.sale_mode` (`db_default` 'stock'), `OrderItem.supply_origin`/`supply_status`/`supply_note` (`db_default`), `lead_time_days` y `supplier_offer` (nulas), `CartItem.supply_origin` (`db_default` 'own'), tipo de carga `supplier_offers`. Sólo esquema | DEVICE-COMMERCE-01 · DC3 |
| 0135 | `CompanySettings.device_cart_hold_minutes`/`device_checkout_hold_minutes`/`device_hold_ceiling_minutes` (`db_default` 15/45/60) y `web_on_order_over_stock` (`db_default` false); `OrderItem.supply_chosen_over_stock` (`db_default` false) y `supply_choice_reason` (`db_default` ''). Sólo esquema | DEVICE-COMMERCE-01 (decisiones del propietario) |

---

## 10. Deuda técnica activa

| ID | Dominio | Motivo | Prioridad | Depende de |
|---|---|---|---|---|
| CSP-01 (agravada) | FRONTEND | sin `script-src`; desde esta fase hay tres scripts de terceros que se cargan con consentimiento. Bloqueante para activar un proveedor en producción (decisión del propietario, 2026-10-07), sin lista especulativa; lista de dominios en `docs/analytics-marketing.md` §7 | Media | probar con el SDK de la pasarela real |
| FRESH-SAMPLE-DEV | DATOS | las bases de desarrollo y de pruebas siguen naciendo con los tres productos de ejemplo de la migración `0002`; en producción los retira `bootstrap_pilot_store` | Baja | deliberado: las pruebas los usan |
| FRESH-CATEGORY-ORDER | DATOS | `bootstrap_pilot_store` da posición a una categoría aprobada sólo si su orden es 0 (el valor por defecto); una puesta en 0 a propósito se movería | Baja | — |
| MEAS-FORM-PAGES | MEDICIÓN | Meta y TikTok no reciben `AddPaymentInfo` ni `CompleteRegistration`: no están en las páginas con formulario | Baja | decisión de privacidad |
| MEAS-PRIVACY-NOTICE | MEDICIÓN | la política de privacidad y cookies no dice que la IP y el navegador van a Meta y TikTok con la compra (aprobado por el propietario el 2026-10-07, sólo bajo consentimiento de marketing) | Media | texto del propietario, antes de producción |
| INT-IMPORT-CYCLE | INTEGRACIONES | dos ciclos de imports por archivo introducidos por #91 (`providers/measurement.py` con el paquete `integrations`, y con `store/measurement/__init__.py`); ninguno falla al cargar | Baja | microfase INT-IMPORT-CYCLE-01 |
| MEAS-SERVER-EVENTS | MEDICIÓN | sólo la compra se envía desde el servidor | Baja | — |
| MEAS-HARD-NAV | MEDICIÓN | entrar en una ruta privada o en el checkout con scripts cargados recarga la página | Baja | — |
| MEAS-CONSENT-LOG | MEDICIÓN | sin registro de consentimientos en el servidor (sólo el del pedido) | Baja | requisito legal, si aparece |
| PAY-TENANT-SCOPE | PAGOS | la pasarela es de la instalación: checkout, rutas de notificación y frontend asumen un comercio | Media | decisión de producto |
| FISCAL-TENANT-SCOPE | FISCAL | SUNAT es de la instalación y sólo BETA; `fiscal_config` ya recibe la empresa | Media | certificado y credenciales reales |
| PAY-SWITCH-PENDING | PAGOS | RESUELTO para «Mi Cuenta Web» (DEC-PAY-04): un cambio de producto, tienda o entorno, apagar o revocar con cobros abiertos se consulta a la pasarela y se bloquea (409) mientras alguno pueda cobrarse o siga sin resolver; `INTERRUMPIR` sólo si la pasarela no responde, con registro de los intentos. «SDK web / Checkout» sigue con la palabra. Un cambio en las variables del servidor no se vigila | Baja | — |
| PAY-ROLLBACK | PAGOS | sustituir claves no conserva las anteriores | Baja | — |
| IZIPAY-MODE-DECLARED | PAGOS | en «SDK web / Checkout» el entorno lo declara quien configura; `token_url` admite cualquier https | Baja | claves reales |
| SUNAT-SOL-CHECK | FISCAL | la prueba de la consola no comprueba la clave SOL (sólo al emitir) | Baja | — |
| ENV-FALLBACK-RETIRE | INFRA | las variables de entorno de las cinco integraciones siguen como respaldo; plan de retirada en `docs/integraciones-y-secretos.md` §6 | Baja | instalación real en la consola |
| INTEGRATION-READ-COST | INFRA | cada uso lee y descifra la configuración activa, sin caché (DEC-INT-06) | Baja | medir con tráfico |
| MAIL-TENANT-SCOPE | CORREO | un solo correo saliente para todas las empresas | Baja | decisión de producto |
| THROTTLE-CACHE-01 | INFRA | CONTROLADO: producción corre un proceso de gunicorn y los límites son exactos; subir procesos exige antes una caché compartida (`docs/despliegue-produccion.md`) | Media | infraestructura |
| LOGIN-CSRF-01 | AUTH | el inicio de sesión no rechaza por origen (401 con credenciales erróneas desde un origen ajeno); las operaciones con sesión sí | Baja | — |
| FISCAL-PDF-01 | FISCAL | PDF sin línea de descuentos globales | Baja | — |
| IDOR-01 limpieza | SALES | segunda fuente de verdad en resend | Baja | — |
| Lint 33 warnings | FRONTEND | 24 `set-state-in-effect` y otras | Baja | — |
| STOREFRONT-EDITORIAL-CMS | STOREFRONT | PARCIAL: hay imagen de hero y por categoría, subidas desde el panel; sin más material editorial | Baja | decisión de producto |
| STOREFRONT-IMAGE-CLEANUP | STOREFRONT | una imagen sustituida queda guardada; sin listado ni borrado | Baja | — |
| TENANT-TYPOGRAPHY | BRANDING | PROPUESTA: tipografía por tienda (el manual del piloto pide Montserrat; la plataforma usa Inter + Unbounded) | Baja | — |
| INTERNAL-UI-V3 | FRONTEND | PENDIENTE: el trabajo paralelo V3 sólo cambia piezas compartidas del panel (`AdminShell`, menú lateral con diálogo móvil, barra superior, selectores, campana, gráficos, `.admin-workspace`), ninguna página; alcanza a todas las rutas a la vez y 6 de 9 archivos chocan con master | Media | fase propia |
| STOREFRONT-IMAGES-LICENSE | STOREFRONT | BLOQUEADO para el repositorio: las imágenes de la propuesta de Figma no tienen licencia demostrada y no se versionan. Ya no bloquea la portada: la tienda sube las suyas desde el panel | Baja | decisión legal |
| ADMIN-INVENTORY-MOBILE-OVERFLOW | FRONTEND | `/admin/inventory` desborda la página 284–378 px hasta 414 px de ancho; ya ocurre en master `c47c538` | Baja | INTERNAL-UI-V3 |
| HERO-WATERMARK-MOBILE | STOREFRONT | PROPUESTA: el isotipo del hero sólo se muestra desde `lg` | Baja | — |
| STOREFRONT-PILLARS-CMS | STOREFRONT | PROPUESTA: los pilares (sólo con el hero oscuro) y la frase de la franja de marca (hero claro) están compilados; sin campo en el CMS | Baja | — |
| POS-CUSTOM-PRODUCT | POS | PROPUESTA: vender en caja un artículo que no está en el catálogo. No se implementa con productos falsos ni con `OrderItem.product` nulo | Por decidir | decisión de producto |
| FISCAL-SERVICE | FISCAL | PENDIENTE: un pago de servicio (`RepairPayment`) no produce comprobante electrónico | Por decidir | decisión fiscal |
| SVC-POS-DRAFT | FRONTEND | la recepción a medio llenar se pierde al volver a «Productos»; el modo servicio depende de que cargue el contexto de la caja | Baja | — |
| SVC-ELIGIBLE-COST | SERVICE | la lista de candidatos resuelve capacidades y alcance por cada miembro del personal; el camino de asignar ya consulta a una sola persona | Baja | — |
| PAY-RECONCILE | PAYMENTS | IMPLEMENTADO para «Mi Cuenta Web» (DEC-PAY-03): `reconcile_payments` pregunta `Order/Get` y paga por el mismo camino que una notificación. PENDIENTE para «SDK web / Checkout», que no tiene esa consulta. Sin probar contra la pasarela real | Media | credenciales |
| PAY-TEST-PUBLIC | PAYMENTS | RESUELTO (DEC-PAY-05): con claves de TEST de la consola sólo paga un MASTER; el intento guarda su entorno (`0117`) y un pago en TEST deja `payment_confirmed_in_test` | — | — |
| PAY-GET-UNKNOWNS | PAYMENTS | sin claves no se sabe: qué contesta `Order/Get` de una referencia sin transacciones (hasta saberlo, esos intentos no se cierran solos si contesta con un error), si devuelve reembolsos sin `operationType`, si cada transacción trae `mode` y `shopId`, si hay una notificación por tarjeta rechazada, y si «una llamada por minuto» es por referencia o por tienda (`pagos-equipos-documentos.md` §1.5) | Media | credenciales |
| PAY-DEFERRED-WINDOW | PAYMENTS | un medio de pago que tarda más de 2 h en resolverse sale de la ventana de la consulta: lo confirma su notificación o `reconcile_payments --reference` | Baja | — |
| PAY-SDK-SECRETS-REPR | PAYMENTS | RESUELTO: `IzipayCredentials` ya no muestra `api_key` ni `hash_key` en `repr()` | — | — |
| PAY-TEST-ENV | PAYMENTS | RESUELTO (DEC-PAY-07): la puerta de TEST depende del entorno de lo que cobra, venga de la consola o de las variables; `PAYMENTS_PUBLIC_TEST` (apagado por defecto) es la única excepción | — | — |
| PAY-CAP-ATOMIC | PAYMENTS | el tope de tres formularios vivos por pedido se cuenta y después se escribe: reintentos simultáneos de la app pueden pasarlo. El límite por usuario (10 por minuto) lo acota | Baja | — |
| PAY-WEB-ORPHAN-ORDER | PAYMENTS | en la web, un rechazo con código al abrir el pago (pasarela cambiada o en pausa justo entonces) deja un pedido pendiente sin intento; el comprador reintenta y nace otro pedido | Baja | — |
| PAY-FOREIGN-GOLIVE | PAYMENTS | tras el paso de TEST a PRODUCCIÓN del propio propietario, sus intentos de prueba que siguieran abiertos dan la ATENCIÓN «abiertos con otra tienda o en otro entorno» durante 24 h: es una falsa alarma | Baja | — |
| PAY-RECONCILE-CAPACITY | PAYMENTS | la consulta atiende 20 intentos cada 5 minutos, menos de los que el límite del checkout deja abrir a una sola dirección (10 por minuto); con un abuso sostenido la cola crece, y lo dice («sin consultar») | Baja | — |
| PAY-NOGATEWAY-500 | PAYMENTS | una instalación sin pasarela configurada responde 500 al checkout en vez de 503 (anterior a este trabajo; ver PAY-UNCONFIGURED-500) | Baja | — |
| PAY-PAUSE-ENV | PAYMENTS | «Pausar pagos nuevos» necesita una fila activa en la consola: una instalación que cobra sólo con variables del servidor tiene que llevar antes esa configuración a la consola («usar la del servidor» y activar, que con la misma tienda no se bloquea) | Baja | ENV-FALLBACK-RETIRE |
| PAY-APP-CODES | FRONTEND | la app todavía no lee el campo `code` de un reintento sin formulario (`order_already_paid`, `payment_in_progress`, `too_many_payment_attempts`, `payment_paused`, `payment_test_only`) | Baja | — |
| PAY-TEST-BADGE | FRONTEND | el panel no muestra «pago de prueba» junto a un pedido pagado en TEST: está en el intento y en la auditoría | Baja | — |
| KRYPTON-GLUE | FRONTEND | la documentación pide `embedded-form-glue` para React; se inserta el guion oficial directamente. Sin comprobar en un navegador contra la pasarela real | Baja | credenciales |
| PAY-RECONCILE-REHEARSAL | DEPLOY | `reconcile_payments` está en `crontab.example` pero no en `deploy/rehearsal.sh` | Baja | — |
| NOTE-LINE-DISCOUNT | DOCUMENTS | PENDIENTE: la venta guarda un precio por línea y el descuento del pedido; la columna «Dscto.» de la nota muestra 0.00. La atribución por línea de una promoción sólo existe en el camino fiscal | Baja | — |
| UNIT-IMPORT-MAPPING | INVENTORY | PROPUESTA: la carga de equipos sólo lee la plantilla (cabeceras conocidas); no tiene asignación manual de columnas ni perfiles como la de stock | Baja | — |
| DOC-FISCAL-STYLE | DOCUMENTS | PROPUESTA: las representaciones fiscales conservan su trazado propio (regulado); comparten logotipo e identidad pero no el módulo `document_style` | Baja | — |
| WHATSAPP-CREDENTIALS | NOTIFY | BLOQUEADO: el envío real necesita número de WhatsApp Business, plantillas aprobadas y credenciales del propietario; contrato probado con `FakeProvider` y con `urlopen` simulado | Media | propietario |
| GOOGLE-CLIENT-ID | AUTH | BLOQUEADO: falta `GOOGLE_OAUTH_CLIENT_ID` con el dominio autorizado; sin él el botón no se ofrece | Baja | propietario |
| CUSTOMER-MERGE | SERVICE | no hay fusión de dos registros del mismo cliente (deshacer una vinculación ya tiene pantalla: ficha del cliente › «Desvincular cuenta») | Baja | — |
| SERIAL-PRICE-DROP | INVENTORY | PENDIENTE: borrar la columna `store_stockunit.price_override` (fuera del modelo desde la migración 0116) en la versión siguiente a la que publique SERIAL-PICK-01 | Baja | — |
| SERIAL-PICK | INVENTORY | RESUELTO en SERIAL-PICK-01 (rama `feat/serial-pick`, migración 0116): el equipo se identifica al vender en caja o al entregar un pedido web (`unit_identification.py`, `OrderUnit`). Se retiró el precio propio del equipo | Alta | — |
| SERIAL-TRANSFER · SERIAL-COUNT | INVENTORY | PENDIENTE POST-LAUNCH: transferir equipos con serie entre sucursales (hoy imposible: la serie no entra dos veces) y recuento por lectura de series | Media | decisión de producto |
| PAY-UNCONFIGURED-500 | PAYMENTS | sin credenciales de Izipay, la notificación y el inicio de un pago responden 500 «la pasarela no está configurada» (deliberado y probado: falla cerrado); cualquiera puede provocar esa línea de error | Baja | credenciales |
| LOGIN-IDENTIFIER-LOG | AUTH | `security_log.py` registra lo escrito como usuario en un inicio de sesión fallido (nunca la contraseña) | Baja | — |
| LIMIT-ENV-DRIFT | INFRA | los topes de `deploy/Caddyfile` son los valores por omisión; quien suba un límite por variable de entorno tiene que subirlo también ahí (la prueba sólo compara los valores por omisión) | Baja | — |
| GOOGLE-NONCE-CACHE | AUTH | el uso único del intento de Google y el bloqueo de intentos al vincular una orden viven en la caché del proceso; comparte condición con THROTTLE-CACHE-01. La cookie HttpOnly del intento no depende de ella | Baja | THROTTLE-CACHE-01 |
| CLIENT-SEC-VALIDATORS | CLIENTES | el teléfono y el documento se juzgan de cuatro maneras: checkout web (`serializers.CheckoutInputSerializer`), checkout v1 (`v1_checkout_serializers`: CE «6 o más», sin tope), ficha del panel (`CustomerWriteSerializer`: 4–20 con guiones, teléfono de 6–20 dígitos) y el perfil (`identity_validation`, el único con dígito de control de RUC). Los tres primeros no se tocaron en la fase 1. Un RUC que el perfil rechaza se puede escribir en el checkout | Media | decisión del propietario sobre endurecer el checkout |
| CLIENT-SEC-RUC-CHECK | CLIENTES | `fiscal/rules.check_ruc` decidió a propósito NO comprobar el dígito de control del RUC; el perfil sí lo comprueba (módulo 11, pedido en la fase). Son dos criterios; si SUNAT aceptara un RUC que el perfil rechaza, se corrige en `identity_validation.ruc_check_digit_matches` | Baja | — |
| CLIENT-SEC-IPHASH-RETENTION | PRIVACIDAD | `OrderRiskAssessment.ip_hash` responde una pregunta de una hora (¿el mismo origen que otro pedido?) y se conserva para siempre. Falta vaciarlo pasado un plazo; no se añadió una tarea programada porque el número de tareas está fijado en 7 por una prueba y es decisión aparte | Baja | — |
| CLIENT-SEC-THROTTLE-MEMORY | CLIENTES | (antes CLIENT-SEC-DOC-BUDGET-MEMORY) el cupo de documento YA NO vive en memoria: se cuenta en la auditoría (`customer_document_change_attempted`). Lo que sigue en la memoria del proceso son los límites de DRF de esta fase (`account_profile_write`, `account_address_write`, `account_verify_send`, `account_verify_confirm`): un reinicio los pone a cero y cada proceso lleva su cuenta. La espera de un minuto, los cinco códigos al día y los cinco intentos por código se cuentan en la base y no dependen de eso | Baja | THROTTLE-CACHE-01 |
| CLIENT-SEC-DOC-ATTEMPT-ROWS | CLIENTES | cada intento de cambiar el documento deja una fila de auditoría (como mucho tres al día por cuenta). La consulta del cupo filtra por actor, acción y fecha sin un índice propio: con el volumen de hoy no hace falta; si la auditoría creciera mucho, un índice `(actor, action, created_at)` | Baja | — |
| CLIENT-SEC-CHECKOUT-DOC-SILENT | CLIENTES | la ficha que crea un checkout con sesión nace SIEMPRE sin documento y nadie se lo dice a la cuenta (el pedido sí lo lleva): lo añade en «Mi cuenta», con el correo probado. No hay aviso en el checkout ni en la cuenta que invite a completarlo; si la tienda lo quiere, es un texto en «Mi cuenta» | Baja | propietario |
| CLIENT-SEC-RISK-TUNING | PEDIDOS | DECISIÓN DEL PROPIETARIO, no tocada: los pesos, los umbrales (`MEDIUM_SCORE` 30, `HIGH_SCORE` 60; tres compradores o seis pedidos pagados por origen en una hora o por contacto en un día) y si el monto por sí solo debe abrir una revisión. Son constantes elegidas sin historial real. Lo que la tercera revisión midió: **falsos positivos** — con un monto de revisión configurado, el tercer invitado de una misma conexión (una familia, una oficina) y el sexto pedido del día de una empresa salen `high` y piden revisión; una persona en tres navegadores con el mismo contacto llegaba a `high` (corregido: mismo teléfono, correo y documento es un solo comprador). **Evasión** — quien cambia de contacto y de origen en cada pedido no activa ninguna señal de velocidad; la evaluación sólo ve lo que se repite. La revisión es un consejo y, con los ajustes como nacen, no retiene nada | Media | propietario |
| DELIVERY-CHECKOUT-AGENCY | ENTREGA | el checkout no ofrece «recojo en agencia» ni envía qué dirección de la libreta se usó (el registro de entrega no apunta a la libreta: la columna `source_address` se retiró porque nada la escribía): el personal cambia la modalidad a mano. Decisión del propietario | Media | propietario |
| DELIVERY-ARCHIVED-BOOK | ENTREGA | una ficha archivada no admite entradas nuevas ni direcciones escritas sobre otra (respuesta neutra). Marcar otra como predeterminada y quitar una entrada siguen permitidos: no añaden ningún dato. Si la tienda quiere congelar también eso, es un cambio pequeño | Baja | — |
| CLIENT-SEC-PROVISIONAL-RECORDS | CLIENTES | quedan fichas sin documento que nadie limpia: la provisional que soltó el suyo (porque el personal archivó a la persona real, o porque otro pedido con ese documento se pagó), y la ficha propia que recibe CADA pedido de invitado pagado cuyo documento está en la ficha de una cuenta (una por pedido: no se unen por parecido, DEC-CUSTOMER-01). Quien compra a veces con sesión y a veces sin ella acumula una por compra sin sesión. Un pedido de invitado SIN pagar sigue sin ficha hasta que se paga. `customer_services.assert_document_available` ya no lo llama ninguna vista; lo usan pruebas anteriores | Baja | — |
| CLIENT-SEC-PAID-SEED | CLIENTES | quien PAGA un pedido de invitado con el documento de otra persona deja una ficha establecida con su propio contacto: el siguiente pedido de invitado de la persona real se enlaza a ella (sus avisos van al contacto de su pedido, no al de la ficha) y en el mostrador el personal recibe el 409 hacia esa ficha; una reparación abierta ahí avisaría al contacto de la ficha. Cuesta un pago real y deja rastro. Sugerencia de la revisión: que el 409 diga al personal si la ficha la creó la tienda o nació de una compra en línea, para que mire el contacto antes de usarla. No hay comprobación de identidad en el checkout, por decisión (DEC-CLIENT-SEC-01) | Baja | propietario |
| STOREFRONT-HYDRATION-FLAKE | FRONTEND | en modo desarrollo `/cart` (y posiblemente cualquier página) registra de forma intermitente un error de hidratación: el servidor envía un `<meta charset>` dentro de `<body>`, antes del enlace «saltar al contenido». Visto 3 veces en unas 320 cargas en desarrollo bajo carga en esta rama y 0 en unas 216 en `master`; nunca reproducido de forma determinista; el layout raíz no lo toca esta rama. Sin causa conocida: no se sabe si afecta a un build de producción | Baja | — |
| M12B-CONCURRENCY-ISOLATION | PRUEBAS | `store.tests.M12BConcurrencyTest` (`TransactionTestCase` con `reset_sequences`) falla en su `setUp` si es la primera de su tipo en ejecutarse: «store_company_pkey id=1 ya existe», porque la empresa piloto sembrada por las migraciones sigue en la base. Pasa cuando otra `TransactionTestCase` vació la base antes (la suite completa y las selecciones amplias). Anterior a esta rama; no se tocó | Baja | — |
| CLIENT-SEC-CREATED-BY-SETNULL | CLIENTES | «la creó el personal» se lee de `Customer.created_by`, que es `SET_NULL`: si se BORRA el usuario que archivó una ficha de mostrador sin pedidos pagados ni reparaciones, la ficha pasa a provisional y un pedido pagado con ese documento se lo quita. Ningún código de producción borra usuarios (el personal que se va pierde su `Membership`), pero el dato debería ser propio de la ficha (una marca de origen) y no depender de una clave ajena | Baja | — |
| CLIENT-SEC-COUNTER-TYPO | CLIENTES | en el mostrador, un documento mal tecleado que coincide con el de una ficha provisional ya no se detecta con un 409: esa ficha suelta el documento y la nueva se guarda con el número equivocado. Queda en la auditoría (`customer_document_released`) y se corrige editando la ficha. Con una ficha establecida el 409 sigue | Baja | — |
| CLIENT-SEC-APP-CE | CLIENTES | el carné de extranjería en el checkout de la app sigue ahora la regla de la web (6 a 12 letras o números ASCII): se rechazan guiones, espacios y más de 12 caracteres, que la app antes aceptaba. Si la app enviaba el CE con guion, hay que quitarlo en el cliente | Baja | — |
| CLIENT-SEC-ORDER-NO-EMAIL | NOTIFY | un pedido sin correo escrito no recibe por correo «listo para recoger» ni «enviado» (el aviso va al contacto del pedido, nunca al de la ficha). El checkout web exige el correo; pueden no tenerlo pedidos de la app o creados por otra vía | Baja | — |
| DELIVERY-FREIGHT-CHARGE | ENTREGA | el flete es informativo: no se cobra, no entra al total ni al comprobante. Cobrarlo es una decisión comercial y fiscal | Media | propietario |
| DELIVERY-DOCUMENTS | ENTREGA | documentos, PDF, tickets y correos siguen leyendo las columnas de entrega del pedido; no muestran transportista, guía ni destinatario distinto | Media | propietario |
| DELIVERY-PICKUP-CODE | ENTREGA | el comprador no ve la clave de recojo (`tracking_code`) ni el destinatario: algunas agencias la exigen para entregar. Hoy la tienda se la comunica por su cuenta | Baja | propietario |
| DELIVERY-STAFF-ADDRESS | ENTREGA | el personal no corrige la dirección del registro de entrega (sólo destinatario, modalidad, transportista, agencia, flete, guía y seguimiento) | Baja | — |
| DELIVERY-APP-UI | FRONTEND | la app todavía no dibuja la libreta, el registro de entrega ni el seguimiento: las rutas v1 existen y están probadas | Baja | — |
| DELIVERY-ORDER-PROTECT | PEDIDOS | `OrderDelivery.order` protege al pedido (`PROTECT`), como la evaluación: quien borre pedidos en una prueba borra antes su registro | Baja | — |
| CLIENT-SEC-V1-FIRST-PROFILE | CLIENTES | en la app el perfil exige ser ya cliente de la empresa (el portón de toda la superficie `customer/`): quien nunca compró no puede crearlo ahí; en la web sí, en la tienda que está mirando | Baja | — |
| CLIENT-SEC-MEASUREMENT-IP | PRIVACIDAD | `MeasurementContext` (anterior, con consentimiento de medición) sí guarda IP y navegador del pedido; la evaluación no los lee ni los copia. Son dos reglas de retención distintas sobre el mismo pedido | Baja | MEAS-PRIVACY-NOTICE |
| CLIENT-SEC-ORDER-PROTECT | PEDIDOS | `OrderRiskAssessment.order` protege al pedido (`PROTECT`): quien borre pedidos en una prueba o en un script borra antes su evaluación. Ningún código de producción borra pedidos de comercio | Baja | — |
| CLIENT-SEC-APP-UI | FRONTEND | la app todavía no dibuja el perfil, la evaluación ni la revisión: las rutas v1 existen y están probadas | Baja | — |
| CLIENT-SEC-NOTICE-LINK | NOTIFY | el aviso «Pedido por revisar» lleva a la lista de pedidos (como todo aviso de pedido), no al pedido ni al filtro «Por revisar» | Baja | — |
| WHATSAPP-INLINE-SEND | NOTIFY | el primer intento ocurre en la petición (hasta 8 s si el proveedor tarda); con `WHATSAPP_SEND_INLINE=0` lo hace sólo la tarea programada | Baja | — |
| TRACKING-LINK-UI-COPY | SERVICE | el aviso al personal sin la capacidad dice a quién pedir el enlace; no hay flujo para solicitarlo desde la pantalla | Baja | — |
| MIG-ADMIN-LIVE | RBAC | 13 migraciones anteriores (0033…0082) comparan el rol Administrador contra el catálogo vivo; una base atrasada que cruce varias en un solo `migrate` puede dejarlo sin ampliar. 0094 y 0095 usan conjuntos congelados | Media | — |
| INV-EXPORT-LEGACY-SHAPE | INVENTARIO | `GET /api/admin/inventory/export/` ya escribe como celda de texto lo que empieza como fórmula (revisión N1, decisión D7), pero sigue incluyendo los productos con serie (su archivo no vuelve a entrar por la carga de stock), no escribe en modo write-only y no tiene límite de peticiones propio. Las descargas nuevas (`/api/admin/inventory/exports/…`) sí | Baja | decidir si se retira |
| INV-EXPORT-RESTORE-GAPS | INVENTARIO | restaurar el ZIP en una tienda VACÍA no es una copia: no viajan «con número de serie» / «lleva IMEI» (hay que marcarlos antes de cargar equipos), ni imágenes, ni códigos adicionales (uno de cada tipo), ni mínimos y objetivos de stock; un equipo apartado entra disponible. Los productos inactivos sí viajan (decisión D3) y se crean inactivos. La nota del ZIP lo dice | Media | propietario: si hace falta una copia fiel, es otra herramienta |
| INV-EXPORT-IMPORT-LIMITS | INVENTARIO | una descarga admite 20 000 filas por libro y las cargas 5000 (1000 equipos) por archivo: un archivo mayor hay que dividirlo antes de cargarlo | Baja | — |
| INV-EXPORT-NAME-MATCH | INVENTARIO | en la MISMA tienda el archivo para volver a cargar reconoce cada producto por su «Identificador» (decisión D5). Fuera de él —un archivo hecho a mano o cargado en otra tienda— dos productos SIN código y con el mismo nombre siguen sin poder reconocerse: la carga pide un código (no duplica) | Baja | — |
| ORDER-HISTORY-LIVE-NAME | PEDIDOS | (tercera revisión) en «Mis pedidos», web y app, el nombre y la imagen de cada línea son los del producto ACTUAL: `OrderItem` no guarda ni nombre ni imagen, sólo cantidad y precio. Un cambio de nombre o de foto después de la venta se ve en el historial. No se filtra precio, stock ni dirección | Baja | — |
| IMPORT-ID-FOREIGN-WARNINGS | INVENTARIO | (decisión D5) un archivo para volver a cargar llevado a OTRA tienda trae en cada fila un «Identificador» que allí no vale: se ignora con un aviso por fila, así que todas las filas salen con aviso | Baja | uso real |
| IMPORT-STATE-NEVER-WRITES | PRODUCTOS | (decisión D3) la carga nunca activa ni desactiva un producto que existe: para retirar o reactivar muchos a la vez no hay acción en bloque, sólo la ficha de cada uno | Baja | propietario |
| INV-EXPORT-SYNC | INVENTARIO | la exportación se arma en memoria dentro de la petición (tope de filas y de 25 MB para el ZIP); no hay trabajo en segundo plano ni export del Kardex | Baja | volumen real |
| INV-EXPORT-LEGACY-BRIDGE-UI | FRONTEND | el panel «Exportar» no se ofrece a un operador del puente legacy (sin empresa resuelta): el servidor sí le respondería | Baja | — |
| INV-REPORT-RENAMED-SHEET | INVENTARIO | las cargas rechazan un reporte por el nombre de su hoja; con la hoja renombrada, el de productos entra como una plantilla con columnas de más (sobre la misma tienda no cambia nada) | Baja | — |
| BULK-VIS-CAP | PRODUCTOS | publicar u ocultar en bloque admite 2000 productos por petición; por encima responde 400 con el total y el panel no parte la selección en lotes | Baja | catálogos mayores |
| BULK-VIS-FORM-HINT | FRONTEND | la ficha del producto no avisa de lo que le falta para publicarse en bloque (la lista sí) | Baja | — |
| P0E-CART-RACE-ORDER | TESTS | `store.tests.P0ECartConcurrencyTest.test_many_at_once_still_add_up` falla en `setUp` (`store_company_pkey` duplicada) cuando esa clase es la primera `TransactionTestCase` del proceso: `reset_sequences` reinicia la secuencia con la empresa piloto todavía en la base. En la suite completa otra clase vacía la base antes. Reproducido sobre `14e658c` | Baja | — |
| IMG-IMPORT-NAME-READING | PRODUCTOS | (revisión N12) `<código>-12.png` se lee como la imagen 12 de `<código>` cuando no existe un producto `<código>-12`; si existen los dos, o si el nombre entero coincide con más de un producto, no se carga y se dice (nunca cae al código más corto). Código y nombres de archivo se comparan con la misma clave (NFKC, sin mayúsculas, espacios recortados) desde la decisión del 2026-10-14; las referencias a archivos dentro del libro «Imágenes» siguen comparándose sólo sin mayúsculas (NFC) | Baja | documentado en la pantalla y en `docs/imagenes-y-evidencias.md` §3.7 |
| IMG-IMPORT-KEY-ONLY-IMAGES | PRODUCTOS | (decisión del 2026-10-14) sólo la carga de imágenes compara el código sin mayúsculas: la carga de productos, la de stock, la de equipos y la caja siguen exactas. Un archivo `funda.png` va al producto `FUNDA`, pero una fila «funda» de la carga de productos crea otro producto | Baja | deliberado |
| PANEL-COMPANY-AT-SEND | FRONTEND | la pantalla de productos manda explícita la empresa de la lista y de la acción en bloque (revisión N5); las demás acciones del panel siguen tomando la empresa del navegador al enviar (`withSelectedCompany`). Actúan por id, así que en otra empresa responden 404 | Baja | — |
| IMG-IMPORT-OLD-JOB-WORDING | PRODUCTOS | (segunda revisión) una vista previa de imágenes hecha ANTES de desplegar esta fase, sobre un producto con galería, se rechaza al aplicar diciendo que la galería cambió aunque no cambió. Se arregla previsualizando otra vez | Baja | — |
| IMG-IMPORT-CODE-CHARS | PRODUCTOS | un código con caracteres que un nombre de archivo no admite (`/`, `:`) no se puede escribir en el nombre: para ésos queda el libro «Imágenes» | Baja | — |
| IMG-IMPORT-REUPLOAD | PRODUCTOS | un archivo con error o sin producto obliga a volver a adjuntar el lote para previsualizar otra vez (hasta 200 archivos / 100 MB); no hay corrección sobre la vista previa | Baja | uso real |
| IMG-IMPORT-REPLACE-REENCODE | PRODUCTOS | reemplazar quita y vuelve a colocar también las imágenes que ya estaban y vienen en la carga (nueva fila, nuevo archivo); el texto alternativo que tuvieran se pierde | Baja | — |
| IMG-IMPORT-ALT-TEXT | PRODUCTOS | la carga en lote no pone texto alternativo: se escribe después en la galería de cada producto | Baja | — |
| IMG-STORAGE-S3 | INFRA | el almacén de imágenes es el volumen local `evidence`; `EVIDENCE_STORAGE_BACKEND=s3` existe en el código pero nunca se ha probado contra un proveedor ni hay copia de un bucket (`docs/imagenes-y-evidencias.md` §5.4) | Media | plan y autorización del propietario |
| ASSISTANT-HEADERS-ONLY | INVENTARIO | «¿Qué archivo tengo?» decide por hojas y cabeceras (dos primeras filas): un libro con cabeceras en la fila 3 o con nombres propios de columna sale «no se reconoce» y hay que ir a su carga a mano | Baja | — |
| BULK-CENTRE-LEGACY-BRIDGE | FRONTEND | «Gestión masiva de inventario» no se ofrece a un operador del puente legacy (sin empresa resuelta): sus tres cargas siguen en el menú como antes | Baja | — |
| DC1-COST-NET-OF-IGV | COSTOS | los costos se guardan como se escriben; plantillas y notas dicen «sin IGV», pero nada lo verifica. «Precio costo» de la plantilla POS se lee como costo de referencia sin saber si incluye IGV | Media | confirmación del propietario |
| DC1-PAID-THEN-CANCELLED | COSTOS | el margen cuenta sólo pedidos `PAID`; un pedido pagado y luego cancelado saldrá solo cuando ORDER-CANCEL-01 cree ese estado, y entonces su salida de Kardex y su instantánea seguirán ahí | Media | ORDER-CANCEL-01 |
| DC1-WEB-UNIT-SWAP | COSTOS | una venta web congela al pagar la media de los equipos APARTADOS; si al entregar se identifica otro equipo (`identify_for_order`), la instantánea no cambia. DC2 la cierra para lo vendido por unidad (el equipo no se cambia); sigue para serializados sin modelo | Baja | DC2-SERIAL-PICK-NO-MODEL |
| DC1-UNKNOWN-STOCK-AVG | COSTOS | CERRADA en la revisión de DC1: las unidades sin costo quedan pendientes (`uncosted_quantity`) y salen primero; falta que el propietario confirme la regla | Baja | confirmación del propietario |
| DC2-SERIAL-PICK-NO-MODEL | VENTAS | un producto serializado sin modelo sigue con SERIAL-PICK-01 (el pago aparta el más antiguo); la compra de unidad concreta sólo rige para variantes de un modelo | Media | confirmación del propietario |
| DC2-PARTIAL-UNIT-SHORTFALL | VENTAS | si de dos equipos elegidos de un producto uno ya no está al confirmar el pago, el otro sale y el faltante queda en `payment_error`; reprocesar la salida no lo reintenta (clave pedido+producto ya registrada) | Baja | flujo de faltantes |
| DC2-HOLD-KARDEX-NOISE | INVENTARIO | cada apartado web y su liberación escriben líneas de Kardex (`manual_exit`/`manual_entry`, «Apartado web»); un carrito abandonado deja dos líneas | Baja | filtro de Kardex por tipo de referencia |
| DC2-NATIVE-UNIT-CART | VENTAS | la app nativa no puede comprar productos vendidos por unidad (rechazo `unit_required`) | Media | app con carrito de apartados |
| DC2-PROMO-UNIT-LINES | FISCAL | `fiscal_services._promotion_line_discounts` ubica la línea por producto; una promoción sobre un producto con varias líneas de equipo fallaría cerrado (hoy las promociones son de caja y la caja no crea líneas de equipo) | Baja | si una promoción llega a la web |
| DC2-HOLD-SHARE-RULE | VENTAS | el reparto del estante para apartados web (50 %, mínimo uno) es global (`settings`), no por empresa, y rige también para seminuevos de una misma variante | Media | confirmación del propietario |
| DC2-SUPERSEDED-PAID | PAGOS | si el formulario de un pedido reemplazado por otro checkout del mismo carrito se paga igual, el dinero se cobra y la línea queda como faltante: devolución o venta a mano | Baja | flujo de faltantes |
| DC2-HOLD-ORIGIN-SHARED-IP | VENTAS | el tope por origen cuenta una IP (o su /64): detrás de un NAT compartido (oficina, universidad) tres compradores agotan el cupo de los demás durante el plazo del apartado | Baja | observar en producción |
| DC3-EXTERNAL-USED-UNITS | VENTAS | seminuevos de proveedor externo (unidad concreta confirmada antes de vender) no implementados; diseño en DEC-DEVICE-COMMERCE-01 · DC3 | Media | siguiente subfase (sigue como deuda tras las decisiones del 2026-10-15) |
| DC3-REFUND-MANUAL | PAGOS | una línea «no se pudo abastecer» o «por confirmar» se cancela o reembolsa a mano: ORDER-CANCEL-01 no está en esta rama | Media | fusionar con ORDER-CANCEL-01 |
| DC3-NATIVE-ON-ORDER | VENTAS | la app nativa no compra bajo pedido (sólo stock propio) | Baja | app |
| DC-CART-FRICTIONS | VENTAS | fricciones del carrito que piden una acción manual al comprador: un apartado vencido obliga a volver a agregar el equipo; `cart_changed`, `held_by_others`, `stock_available` y `mixed_supply` se resuelven a mano; cambiar entre stock y bajo pedido exige quitar la línea | Baja | observar en producción |
| DC3-STOCK-FIRST-OWNER | VENTAS | (revisión DC3) «Bajo pedido» se rechaza mientras el stock propio cubre la línea (web y caja, 409 `stock_available`); decisión del agente, por confirmar con el propietario. El checkout no lo vuelve a exigir | Media | confirmación del propietario |
| DC3-PROMO-ON-ORDER | VENTAS | una promoción de caja se calcula sobre el precio de oferta de una línea bajo pedido como sobre cualquier precio; no se probó una promoción que excluya lo bajo pedido | Baja | confirmación del propietario |
| DC1-MANUAL-ENTRY-COST | COSTOS | la entrada manual de stock de la pantalla de inventario no pide costo (sólo el servicio lo admite): esas entradas quedan `unknown` | Baja | DC2 |
| DC1-REFUND-REVERSAL | COSTOS | un reembolso no devuelve stock ni revierte el costo; el margen simplemente deja de contar el pedido | Baja | flujo de devoluciones |
| DOCS-CADDY-LIMIT-NOTE | DOCS | `docs/imagenes-y-evidencias.md` §5.5 aún dice «Caddy, tal como se entrega, no pone tope», anterior a los niveles de `request_limits.py` y del Caddyfile | Baja | revisar con quien mantiene el despliegue |

---

## 11. Últimas fases

- **F0 — Baseline**: medido @ `65aa8c1`; docs @ `d383806`.
- **F1 — Security / tenancy / authorization**: COMPLETED @ `4a9dd5c`. Un fix (FE-AUTH-01). Backend sin cambios. Checkpoint completo en la transcripción de la sesión `acdf85aa`; artefactos en su scratchpad (`audit_f1_investigation.json`, `audit_security_sweep.json`, `demo_branch_scope.py`).
- **F2 — Eje DÓNDE: delegación y alcance por sucursal**: COMPLETADA @ `c191a84`. F-BRANCH-01 (`20d110c`), F-BRANCH-02 (`cccb4d2`), F-BRANCH-03 (`70286d1`), F-CAP-01 (`c042fea`), RBAC-01 (`5afdb81`), RBAC-02 (`d18e983`), WRITE-SCOPE-01 (`fa85d41`, `c076120`), DRIFT-01 (`a4be03b`), DRIFT-07 (`6958ec0`, `f6dc9ca`), E2E-02 (`9c3445f`), E2E-01 (`c191a84`). Sin push. Siguiente fase: por decidir.
- **Integración**: ERP + F1/F2 en `master` por el PR #42 (merge `ef9890f`).
- **SVC-FUNC-01 — Servicio técnico operativo e integración con la caja**: código @ `9b59a31` en `feature/service-pos-functional-integration` (desde `ef9890f`). SVC-NAV-01 (`937cf82`), SVC-ASSIGN-01 y SVC-TX-01 (`1928b05`), SVC-PAY-01 (`d62fa30`), POS-SVC-01 (`6caa88c`), pruebas del flujo y fixture E2E (`1d35b7d`), SVC-ASSIGN-UNASSIGN (`4796db0`), SVC-ASSIGN-VIEW-01 (`9b59a31`). Integrado en `master` por el PR #44 (merge `d98d70c`, árbol `b7d570e` idéntico al validado). Backend 4643 pruebas, 0 fallos; frontend 428; Playwright 121 de 121, sin fallos, omitidas ni reintentos, 9,0 min.
- **UX-RECON-SVC-01 — Interfaz de #43 sobre el master con servicio y caja**: merge `ee3a8d3` en `reconcile/uxui-after-svc` (padres `d98d70c` y `9c1486b`; 5 conflictos de texto, 1 archivo fusionado sin conflicto y revisado; 0 backend, 0 migraciones). Frontend 437 pruebas en 43 suites, lint 0/26, build 50 páginas, Playwright 121/121. Integrado en `master` por el PR #45 (merge `0c83381`, árbol `4fe7e61` idéntico al validado); #43 y #39 quedaron contenidos.
- **STOREFRONT-V3 — Convergencia de la tienda pública**: PARCIAL, código @ `7f49168` en `reconcile/storefront-v3-after-ux` (desde `0c83381`). Navegación y pie (`a57c72e`), carrusel y carrito (`9ba1a70`), portada y hero (`11f69f0`), Nosotros/Contacto (`5380ca6`), E2E (`7f49168`). Frontend 483 pruebas en 47 suites, lint 0/25, build 52 páginas, Playwright 143/143; 0 backend, 0 panel interno. #38 no se usó. Pendiente de merge.
- **HERO-MOBILE-CLIP y cierre del frontend V3**: código @ `42ef631` en `fix/hero-mobile-clip` (desde `c47c538`), pendiente de revisión y merge. Hero (`ff564e9`), titulares de catálogo y ficha (`e082b26`), prueba sin agotar el límite del carrito (`42ef631`). Frontend 483 pruebas, lint 0/25, build 52 páginas, Playwright 163/163. Una prueba de «texto recortado» mide rectángulos de texto, no `scrollWidth`.
- **Integración**: ERP + F1/F2 en `master` por PR #42 (merge `ef9890f`, árbol `44cfffd` idéntico al validado). Reconciliación UX/UI de #39: merge `1fea6b9` en `reconcile/uxui-after-f2` (75 archivos en conflicto, 0 backend, 0 migraciones), PARCIAL, pendiente de merge.

- **Cierre 2026-10-04**: `master` @ `23d2603`. Infraestructura de producción (#59, `da5cca5`), `actions/setup-python` 7 (#64, `b632dca`), `reportlab` 5 (#67, `8196f6b`), PAYMENT-FISCAL-PRINT-01 (#81, `1b62f31`) y frontend V3 (#82, `23d2603`). Backend 4865; Jest 609; Playwright 176/176.
- **MEDIA-EVIDENCE**: rama `feat/media-evidence`. Galería de producto, carga masiva con imágenes, evidencias con nota y ciclo completo. Backend 4965; Jest 659; Playwright 179/179. Decisiones DEC-MEDIA-01, DEC-IMPORT-MEDIA-01, DEC-EVIDENCE-01.
- **SERVICE-TRACKING**: rama `feat/service-tracking-equipment`. Identidad de equipo, seguimiento por enlace, decisiones de cotización y ticket, avisos reales y WhatsApp, equipos con serie, categorías de portada, carrusel y Google. Migraciones `0102`–`0109`. Backend 5172; Jest 765; Playwright 183/183. Decisiones DEC-DEVICE-01 … DEC-GOOGLE-01. Revisión independiente: 2 P2 y 5 P3, corregidos (`fd99611`).
- **PAYMENTS-EQUIPMENT-DOCUMENTS**: rama `feat/payments-equipment-documents`. Segundo producto de Izipay («Mi Cuenta Web») detrás de `PAYMENT_PROVIDER`; «Registrar equipo» y carga masiva «Equipos serializados.xlsx»; plantilla de productos con ayuda; trazado común de documentos con serie e IMEI por línea. Migración `0110`. Backend 5255 pruebas, 0 fallos, 5 omitidas; Jest 787/787; Playwright 184/184, 0 omitidas. Decisiones DEC-PAY-02, DEC-UNIT-IMPORT-01, DEC-DOC-01. Revisión independiente: 1 P2 y 5 P3, corregidos (`2d76298`).
- **PRODUCTION-READINESS-01**: rama `chore/production-readiness-01` desde `78ad79c`. Ensayo sobre el código actual (`ENSAYO: OK`, 107 + 49); tope de tamaño por ruta (DEC-LIMIT-01), registros sin tokens (DEC-LOG-01), cuerpos lentos, copias enteras, `healthcheck.sh` + `ops_status`, `crontab.example`, DOC-TIMEZONE, «Desvincular cuenta». Backend 5332 pruebas, 0 fallos, 5 omitidas; Jest 795/795; Playwright 184/184, 0 omitidas. Sin migraciones. READY WITH CONDITIONS.

- **EXTERNAL-PRODUCTION-CONFIG-01**: PR #89, `master` `5da4e99`. `preflight.py`, correo por SMTP en el ensayo, restauración en servidor nuevo. NOT READY por datos del propietario.
- **INTEGRATIONS-CONSOLE-01**: rama `feat/integrations-console` desde `5da4e99`. Panel › Configuración › Integraciones (sólo MASTER): almacén de secretos, registro de proveedores, API `/api/admin/integrations/`, y correo, Izipay, WhatsApp, Google y SUNAT leyendo su configuración en cada uso (consola → entorno). `ops_status` con integraciones; ensayo con la consola (paso 11c). Decisiones DEC-INT-01…07; invariantes INT-SECRET-01, INT-MASTER-01, INT-OFF-01, INT-ACTIVATE-01. Migración `0111`. Operación: `docs/integraciones-y-secretos.md`.
- **STAFF-ONBOARDING-01**: rama `fix/staff-invitation-onboarding` desde `54b26ac`. En producción una persona invitada veía «Crear cuenta» y luego «ya está registrado»: el registro ordinario, con la verificación de correo activa, deja una cuenta inactiva que no entraba, no se registraba y no recuperaba. Ahora el registro con una invitación válida para ese correo crea la cuenta activa (`_vouched_by_invitation`), la recuperación termina una cuenta nunca verificada (`accounts.is_unverified`) y vuelve sólo a una invitación, y `account_state` dice a la pantalla qué ofrecer. Sin migración. Decisión DEC-ONBOARD-01. Trampa: leer una invitación tiene cupo por red (20/min) y en desarrollo cada pantalla lee dos veces.
- **IMPORT-SPLIT-01**: rama `feat/bulk-import-split`, apilada sobre `feat/serial-pick`. Sin migración. Plantillas de UNA hoja (`import_exports.product_template_bytes(company)`, `unit_import_services.template_bytes(branches)`), con listas dentro de la celda (`cell_list`, límite de 255 caracteres), notas en las cabeceras (`header_notes`) y columnas de texto (`text_columns`). `import_formats._belongs_to_stock` marca `important` las columnas de stock/serie/IMEI en un archivo de productos. Menú: `inventory.units.import`. Decisión DEC-IMPORT-SPLIT-01. Trampas: las constantes `PRODUCT_TEMPLATE_HELP`/`_EXAMPLE` y `EXAMPLE_SERIAL_PREFIX` ya no se escriben en la plantilla pero SIGUEN reconociendo archivos anteriores; las pruebas construyen esos archivos a mano (`legacy_template`). El botón «Vaciar» de la caja usa `window.confirm`, que Playwright descarta. Servidores de prueba: `:3005` y `:8103`, base `review/e2e-bulk.sqlite3`. Las plantillas del propietario están en `tmp/plantillas/` de su árbol, sin versionar.
- **SERIAL-PICK-01**: rama `feat/serial-pick`, apilada sobre `fix/register-race`. `OrderUnit` (qué equipo, de qué pedido, cómo se identificó, quién) es lo único que leen los documentos (`pdf_services.sold_units`). Caja: `pos_services.check_line_units` rechaza una línea con serie sin sus equipos; `GET /api/admin/pos/units/lookup/`. Pedido web: `unit_identification.identify_for_order` (cambio de equipo como par entrada/salida manual en el Kardex), `forget`, y `FulfillmentBlocked` (409) para «enviado»/«entregado». Migración 0116: copia como `legacy` lo ya vendido salvo los pedidos aún por entregar, y quita `StockUnit.price_override` SÓLO del modelo (la columna sigue: SERIAL-PRICE-DROP). `OrderUnit.left_at` congela lo que salió; `released_by_return` suelta lo devuelto antes de salir; dos pedidos en espera intercambian apartados; la app tiene los mismos endpoints (`v1_internal_views.py`). Orden de bloqueos: pedido, estante, equipos. Decisión DEC-SERIAL-02. Trampas: `select_for_update` con `select_related` sobre una FK anulable necesita `of=('self',)`; el cliente de pruebas de DRF sustituye la cadena de consulta si se le pasa `data` en un GET; una venta de caja con serie sin `units` es 400, también en `deploy/rehearsal_flows.py`. Servidores de prueba de esta rama: `:3004` y `:8102`, base `review/e2e-serial.sqlite3`.
- **REGISTER-RACE-01**: rama `fix/register-race`, apilada sobre la carga masiva. Índice único `store_user_email_ci_uniq` sobre `LOWER(email)` de `auth_user` (migración 0115, se detiene si hay duplicados y no fusiona), `list_duplicate_emails` (sólo lectura) y registro/Google que responden «ya existe» al perder la carrera. Decisión DEC-REGISTER-01. Trampa: las pruebas que necesitan dos cuentas con un correo deben llamar a `test_register_race.before_the_rule()` (suelta el índice dentro de la transacción de la prueba).
- **PRODUCT-DESTINATION-01**: rama `feat/product-visibility-import` desde `6266402`. `Product.is_published_online` (migración 0114): «sólo stock interno» = en uso en caja, inventario y taller, y fuera de toda superficie pública (`tenancy.company_storefront_products`, `Product.is_sold_online`). Carga masiva: columna «Destino» (alias «Destino (pendiente web)»), hoja «Categorías» con las de la empresa, categoría por defecto para filas sin ella. Decisión DEC-PRODUCT-DEST-01. Trampa: formatear 500 celdas vacías en la plantilla produce 500 «fila vacía» en cada vista previa; el formato de texto va en la columna.
- **MAIL-TEMPLATE-01 (fase 1)**: rama `feat/mail-template` desde `6266402`. Motor de correo `store/mail/` sobre una sola plantilla Mustache (`chevron`), entregada por el propietario y sin editar. La plantilla es de UNA empresa (`marca.json`); las demás y la plataforma —salvo que la tienda de la instalación sea esa empresa— conservan el correo neutro, que además es el respaldo ante cualquier fallo. Cinco correos conectados; vista previa en `/api/dev/mail-preview/` sólo con `DEBUG`. Sin migración ni variables nuevas. Decisión DEC-MAIL-01. Trampa: las pruebas de neutralidad (`Phase3NoHardcodeTest`, «carries its own company», «no brand at all») prohíben nombrar a la tienda piloto en `email_services.py`; lo propio de la marca vive en `mail/plantilla/`. Fase 2 (`feat/mail-template-notifications`): `notification_services._send` y la prueba del SMTP visten la plantilla; sólo 4 de los 15 eventos van por correo (`EMAIL_WORTHY_EVENTS`) y un aviso nunca lleva el detalle del pedido o la reparación. Fase 3A (`feat/mail-verification-code`): código de 6 dígitos junto al enlace de verificación (`verification_codes.py`, migración 0113, `POST /api/auth/verify-email/code/`); 15 min, 5 intentos, 60 s entre códigos, 5 al día por cuenta, sólo HMAC, una respuesta para toda negativa (DEC-MAIL-03). Faltan 3B (garantía y posventa; choca con SERIAL-PICK) y 3C (promociones con consentimiento y baja).
- **FRESH-PRODUCTION-DATA-01**: rama `feat/fresh-production-data-01` desde `52a4ba3`. Una base nueva (`migrate`, sin semillas) trae 0 usuarios y 0 datos operativos, la empresa piloto con su portada, y tres productos de ejemplo de la migración `0002` con stock sin Kardex. `bootstrap_pilot_store` los retira (todo o nada: los tres intactos, sin referencias, base sin actividad, `DEBUG=False`) y deja las cinco categorías aprobadas; no escribe en una tienda ya en uso; sin migración. MASTER = `is_superuser`, por `createsuperuser`, sin membresía implícita. Ensayo: paso 3b. Decisión DEC-FRESH-DATA-01. Operación: `docs/despliegue-produccion.md` §4.3, §4.4 y §5.
- **ANALYTICS-MARKETING-INTEGRATIONS-01**: rama `feat/analytics-marketing` desde `643ba90`. Favicon con el isotipo; proveedores `google_analytics`, `meta`, `tiktok` sobre la consola de #90; `GET /api/measurement/config/`; aviso de cookies; servicio de analítica y tres adaptadores; compra como salida de conversiones (`ConversionDelivery`, migración `0112`), `send_pending_conversions`. Invariantes MEAS-CONSENT-01, MEAS-PRIVATE-01, MEAS-PURCHASE-01, MEAS-BOUNDARY-01. Decisiones DEC-MEAS-01…07. En este código «tracking» es el seguimiento de reparaciones: lo nuevo es `measurement` (backend) y `analytics` (frontend). Operación: `docs/analytics-marketing.md`. Backend 5654; Jest 933; Playwright 194/194. Grafo final con Graphify: deuda INT-IMPORT-CYCLE.

- **DEVICE-COMMERCE-01 · DC1**: rama `feat/device-commerce` desde `abfc6bd`. `costs.view`; `cost_services` (promedio por sucursal, costo por equipo) dentro de `create_stock_movement`; `margin_services` (instantáneas de costo y base sin IGV por línea, reparto por resto mayor); `GET /api/admin/sales/margin/`; inventario al costo; columnas de costo en Excel. Migraciones `0126`–`0128`. Decisión DEC-DEVICE-COMMERCE-01. Pasada de mutación: 22 mutantes, 20 muertos, 2 equivalentes (`qty_before <= 0` es redundante con la fórmula; la guarda de `snapshot_nets` duplica el filtro de su `update`). Trampas: `test_inventory_exports` y la cadena de presets (`SvcCollectPresetTest`) fijan formas que DC1 cambió; correr en paralelo módulos con `TransactionTestCase` borra las bases de los trabajadores; en zsh una variable con varias etiquetas no se parte en palabras.
- **DEVICE-COMMERCE-01 · revisión de DC1 y DC2**: cinco P2 de la revisión independiente corregidos (columna de costo ignorada sin `costs.view`, `uncosted_quantity` + `0129`, promociones con la atribución F6, admin de Django de sólo lectura, `costs_view_report`). DC2: modelos/opciones/variantes (`device_catalog`), `GET /api/catalog/models/…`, apartado web de un equipo concreto (`unit_hold_services`), venta del equipo elegido (`_sell_chosen_units`, línea por equipo), Excel, panel «Modelos» y «Seminuevos», página de modelo. Migraciones `0130`–`0132`. Mutación DC2: 26 mutantes, 25 muertos, 1 equivalente (quitar `exclude(hold_key='')` en `release_expired`: la relectura bajo bloqueo descarta igual un apartado del personal). Trampas: las pruebas con Host `<slug>.tienda.pe` necesitan `override_settings(ALLOWED_HOSTS=…)`; una lista de clases unida sin salto de línea final pega dos etiquetas; el manifiesto de paridad se lee al importar `tests.py`.
- **DEVICE-COMMERCE-01 · revisión de DC2**: topes de apartado por origen (HMAC de `order_risk`) y por reparto del estante, bajo bloqueos consultivos; techo del apartado (`hold_started_at` + 60 min); throttle `device_hold`; `bind_to_order` filtrado por carrito; pedido reemplazado por un segundo checkout (derivado de las líneas, `order_superseded`); Excel viejo con «Modelo»; comandos y pasos para volver atrás; P3 (liberar sin 500, modelo retirado, precio con `products.manage`, v1 sin seminuevos, recuento por sucursal, costo de referencia en la salida). Migración `0133`. Mutación: 30 mutantes, 29 muertos, 1 equivalente (quitar `exclude(hold_key='')` en `release_all_web`: la relectura descarta igual un apartado del personal). Regresión focal en serie: 2031 pruebas OK. Trampas: `HoldTest` y `ExactUnitSaleTest` apartan todo un estante y llevan `DEVICE_WEB_HOLD_MAX_SHARE_PERCENT=100`; las pruebas de plantilla de `test_unit_import`/`test_product_destination_import` fallaban ya en `4b45bee` (encabezados DC2) y se alinearon; `CartLimitRaceTest` es `TransactionTestCase` (en `flushing`); la regresión focal con `--parallel` choca con los `TransactionTestCase` (`store_company_pkey` duplicada): correrla en serie.
- **DEVICE-COMMERCE-01 · DC3 y segunda revisión de DC2**: `supply_services` (proveedores, ofertas, disponibilidad, compromiso al pagar bajo bloqueo de la oferta, confirmar/recibir/fallar), `supplier_views`, `supplier_offer_import`, `expire_supplier_offers`, `cancel_pending_on_order_orders`; venta bajo pedido en carrito (`on_order`), checkout, pago y caja; guardia de despacho `supply_pending`; `ops_status` «abastecimiento». Migración `0134`. Segunda revisión DC2: reparto por variante desde 4 equipos + techo por empresa, el pedido pagado gana su equipo, bloqueos consultivos antes del barrido, entrada de equipos sin costo al costo de salida. Trampas: las pruebas en paralelo y las corridas de mutación comparten la base de pruebas si usan el mismo `DATABASE_URL` (usar otra base o copias del árbol); `Analytics` contiene «Ana» (no buscar nombres cortos en la salida de `ops_status`); `ci_shard_weights.json` se edita a mano (re-serializarlo reformatea 1900 líneas).
- **DEVICE-COMMERCE-01 · DC3, revisión independiente**: `0134` editada (UNIQUE de oferta por clave, CHECK con confirmado no nulo); carga Excel: re-chequeo de claves nuevas al aplicar («El archivo cambió: vuelve a previsualizarlo», también desde el IntegrityError), costo según quien aplica, celda de costo vacía = sin cambio, columna opcional «Activa (Sí/No)», bloqueo de ofertas por id; `fail_line` sobre pedidos reembolsados/cancelados; el pago mira `sells_on_order`; compromiso antes de la salida «sin sucursal»; 409 `stock_available` en web y caja; la vuelta atrás vacía carritos bajo pedido; la línea pública de «Mis pedidos» vuelve a `id, product, quantity, price` (+ sólo `lead_time_days` si es bajo pedido y el producto sigue en la web). Mutación: 25 mutantes, 24 muertos, 1 equivalente. Trampas: `PublicExposureMatrixTest` fija la forma de la línea pública (no ampliarla sin pensar en el producto cerrado); las ofertas de prueba que compartían vigencia ahora chocan con el índice (dar a cada una su `valid_from`/`valid_until`); un test que espera `SupplyError` de una oferta mala debe usar otra vigencia o el duplicado lo «mata» por otra razón; para dividir commits por hunk sin `git add -p` sirve `git apply --cached` con los hunks elegidos.
- **DEVICE-COMMERCE-01 · decisiones del propietario (2026-10-15)**, implementado en rama, sin desplegar: apartados por empresa (`CompanySettings.device_*_minutes`, límites en `CompanySettings.hold_minutes_errors`, `settings.manage`), `unit_hold_services.payment_keeps_hold` (intento `pending` abierto mientras el apartado vivía y con menos de `reconcile.WINDOW`, o marcado `undecided:`/`unreadable:`) y `release_one_expired` (bloqueo de estante y fila, relectura); «bajo pedido» con stock sólo explícito (caja: `supply_reason` + `sales.orders.manage`; web: `web_on_order_over_stock`, oferta pública con `over_stock: true` sólo entonces); margen `estimated`/`realised`/`estimated_only`/`unknown_cost`, `refunds.net`. Migración `0135`. Mutación: 31 mutantes, 29 muertos, 2 sobrevivientes con prueba nueva (ahora muertos). Deuda que sigue: DC3-EXTERNAL-USED-UNITS, DC3-REFUND-MANUAL, DC3-NATIVE-ON-ORDER, DC-CART-FRICTIONS. Trampas: la mutación sin `--keepdb` recrea la base de pruebas por mutante (~1 min cada uno); `ci_shard_weights.json` con `indent=1` re-serializa sin reformatear; `test_margin_analytics` fija la forma de `refunds` (ahora con `net`).
- **DEVICE-COMMERCE-01 · regresión de descargas (`ddd6593`)**: DC2 alargó las plantillas y `inventory_exports` armaba las filas por posición: el archivo de productos para volver a cargar dejaba «Identificador» vacío y «Estado» bajo «Modelo» (D3/D5 de INVENTORY-UNIFIED-01 rotas). Ahora cada fila es `{encabezado: valor}` proyectada sobre los encabezados (`_project`), con todas las columnas de la plantilla llenas (variante sólo si se relee igual, estado estético, batería, precio de venta de un seminuevo; costos tras `costs.view`) y `DeviceCommerceRoundTripTest` como guarda de deriva e ida y vuelta.

---

## 12. Mapa de consulta rápida

- «¿Quién decide qué sucursales puede operar alguien?» → `tenancy.py::visible_branches`, `_branch_authority`, `_granted_branches`; modelo `MembershipBranchAccess`; `Phase2dBranchAccessApiTest`, `H412SelectedBranchWebTest`.
- «¿Quién puede conceder sucursales?» → `can_manage_company_memberships` (QUÉ) + `tenancy.py::can_delegate_branch_scope` (DÓNDE); escritor `tenant_views.py::_apply_branch_access`; `F2BranchDelegationTest`.
- «¿Quién puede modificar una promoción por sucursal?» → `promotion_views.py::_write_promotion` + `can_delegate_branch_scope`; `F2PromotionBranchScopeTest`, `C13PromotionApiTest`.
- «¿Quién cambia la numeración de una sucursal?» → `settings_views.py::AdminSequenceListView` / `AdminSequenceDetailView` / `AdminSequenceScopeView`; `Phase2eSequenceApiTest`.
- «¿Quién puede leer la ficha de la empresa o sus sucursales?» → `tenant_views.READ_COMPANY` / `READ_BRANCHES` + `access_views._scope_readable`; `F2TenantReadAuthorizationTest`.
- «¿Quién puede cambiar algo que afecta a toda la empresa?» → capacidad + `tenancy.has_company_wide_scope`; `F2SelectedBranchWriteScopeTest`, `F2SelectedCompanySettingsScopeTest`.
- «¿A quién se le puede asignar una orden de servicio?» → `service_services.py::eligible_technicians`; `SvcTechnicianEligibilityTest`.
- «¿Quién puede asignar técnico?» → `V1ServiceSurfaceMixin.require_assignment_authority` (`manage`, o `assign` con `view`); `SvcAssignCapabilityTest`, `SvcAssignVisibilityTest`.
- «¿Quién puede dejar una orden sin técnico?» → sólo `service.orders.manage` (`V1ServiceOrderAssignmentView.post`); `SvcUnassignAuthorityTest`.
- «¿Quién puede cobrar un servicio y quién reversar?» → `v1_service_views.py::COLLECT_AUTHORITY` y `V1ServicePaymentReverseView`; `SvcPaymentCollectTest`.
- «¿Cómo se recibe un equipo desde la caja?» → `app/admin/sales/pos/PosServiceIntake.tsx` → `POST service/orders/` con `technician_id` → `create_repair_order_with_assignment`.
- «¿Cómo se decide un tenant?» → `resolve_company_for_user`, `resolve_public_storefront_company`, `resolve_storefront_company`, `V1InternalSurfaceMixin`.
- «¿Quién puede delegar capacidades?» → `tenancy.py::can_delegate_capabilities`, `can_grant_company_role`; `M11AntiEscalationTest`.
- «¿El proxy puede tocar el admin de Django?» → no, PROXY-01.
