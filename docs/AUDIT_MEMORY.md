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
| Frontend tests | 402/402 OK, 37 suites, @ `c191a84` |
| TypeScript | `tsc --noEmit` OK @ `c191a84` |
| Lint | 0 errores / 33 advertencias @ `c191a84` |
| Build | OK (44 páginas) @ `c191a84` |
| E2E | Playwright 118/118, 0 omitidas, sin reintentos, 7,6 min, 1 worker @ `c191a84` |

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
Estado: VERIFICADO @ `4a9dd5c`. Excepción de exposición: F-TENANT-01.

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
la orden. Asignar: `service.orders.assign` (con `service.orders.view`) o
`service.orders.manage` (`V1ServiceOrderAssignmentView.require_assignment_authority`).
El id del técnico se resuelve dentro del conjunto elegible: fuera de él, 404.
Tests: `SvcAssignCapabilityTest`, `SvcTechnicianEligibilityTest`, `SvcIntakeWithAssignmentTest`.
Estado: VERIFICADO @ `1d35b7d`.

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

**PROXY-01** — El proxy Next nunca sale de `${BACKEND_API}/`.
Autoridad: `frontend/app/api/[...path]/route.ts` (rechaza con 400 segmentos `.`, `..` o con `/` `\` tras decodificar).
Tests: `frontend/__tests__/api-proxy-scope.test.ts` (11/11).
Estado: VERIFICADO @ `4a9dd5c`.

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
- Frontend: `app/admin/components/BranchAccessPanel.tsx`, `app/admin/staff/page.tsx`, `app/admin/users/page.tsx`, `app/lib/staff.ts`, `app/admin/lib/internal-api.ts` (sequences, promotions).

### Servicio técnico
- `service_services.py` (`eligible_technicians`, `assign_technician`, `create_repair_order_with_assignment`, `record_service_payment`), `v1_service_views.py` (`V1ServiceSurfaceMixin.get_order`, asignación devuelve `{current, candidates}`, `V1ServiceTechnicianCandidatesView`, `ASSIGN_AUTHORITY`, `COLLECT_AUTHORITY`; la lista de órdenes acepta `status=a,b,c`).
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
| F-TENANT-01 | MEDIUM | TENANCY | `tenant_views.py::AdminMembershipListView.post` + `serializers.py::MembershipSerializer` | POST membresía con `user` de otra plataforma → 201 devuelve su `username` |
| SEC-SET-04-A | MEDIUM | AUTH | `auth_views.py` refresh, `v1_auth_views.py` refresh | refresh sin throttle; filas `OutstandingToken` sin límite |
| THROTTLE-CACHE-01 | MEDIUM | INFRA/AUTH | `backend/backend/settings.py` (sin `CACHES`) | LocMemCache por proceso: límite ×N workers |
| SEC-SET-02 | MEDIUM | INFRA/AUTH | `backend/backend/urls.py` `admin/` | admin Django sin limitador/bloqueo/MFA en origen backend |
| INV-LEGACY-V1-F2 | MEDIUM | INVENTORY | `v1_transfer_views.py` PUT items | no re-bloquea la transferencia (carrera INV-10) |
| RBAC-F3 (sidebar) | MEDIUM | FRONTEND | `app/admin/lib/internal-modules.ts::canAccessModule` | cae a `Membership.role`; `admin.audit` sólo por rol legacy |
| DRIFT-02 | MEDIUM | INVENTORY/FRONTEND | `frontend/app/lib/admin.ts` ajuste | no permite elegir sucursal; backend usa default |
| AUDIT-01…07 | MEDIUM→LOW | AUDIT | `service_services.py`, `inventory_services.py`, `announcement_services.py`, `admin_views.py` | escrituras sin fila de auditoría / sin `company` |
| FE-AUTH-05 | MEDIUM | FRONTEND | `route.ts` | cuerpo del proxy sin límite de tamaño |
| INFRA-01/02/03 | MEDIUM | INFRA | `backend/Dockerfile`, `docker-compose.yml` | imagen dev-grade, secretos copiados, DEBUG=1 por defecto |
| CI-01 · CI-03 | MEDIUM | INFRA | `.github/` | sin CI, sin alertas Dependabot |
| DEP-05 | MEDIUM | INFRA | `backend/requirements.txt` | pins atrasados |
| RBAC-F6/F7/F4/F5/F11 | LOW | FRONTEND | transfers `[id]`, `users/page.tsx`, `staff/page.tsx`, `internal-modules.ts`, `orders/[id]` | UI ofrece acciones que el backend niega |
| INV-LEGACY-V1-F1/F3 | LOW | INVENTORY | `serializers.py` movimientos legacy; v1 transfers `page_size<0` → 500 | |
| Sweep LOW/INFO | LOW/INFO | varios | SEC-SET-01/03/05…10, SEC-SET-04-B, AUTH-LOGGING-01, REFRESH-CSRF-01, ENUM-01, COOKIE-PATH-01, TOKEN-HYGIENE-01, ENV-01…04, INFRA-04…08, DEP-01…04/06…08, CI-02, DOC-01 | ver checkpoint |

SIN VEREDICTO (no abiertos por F1): SEC-01…SEC-10 (secrets), FE-AUTH-02/04/06/07/08/09.
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
DEUDA (surgida en DRIFT-07): `app/admin/components/BranchAccessPanel.tsx` no se monta en ninguna
pantalla (código muerto) y conserva la oferta de «Todas»; el botón «Añadir trabajador» de
`staff/page.tsx` no comprueba capacidad (RBAC-F4 de F1, LOW, sin cambio).
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
| SVC-TX-01 | MEDIUM | `1928b05` | `assign_technician` sin transacción propia (decorador desplazado a `_notify` en `108a904`) | `SvcAssignOutsideATransactionTest` | CORREGIDO |
| SVC-PAY-01 | MEDIUM | `d62fa30` | `V1ServicePaymentView.post`, `PaymentSection` (`canCollect` / `canReverse`), migración 0095 | `SvcPaymentCollectTest`, `SvcCollectPresetTest`, `service-authority-console.test.tsx` | IMPLEMENTADO |
| POS-SVC-01 | — | `6caa88c` | `PosModeSwitch`, `PosServiceIntake`, `ServiceIntake` (técnico obligatorio en caja) | `pos-service-intake.test.tsx`, `SvcIntakeWithAssignmentTest`, E2E `service-pos` (`1d35b7d`) | IMPLEMENTADO |
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
| INVENTORY-ADJUST branch | acepta sucursal / usa default | no envía sucursal (`app/lib/admin.ts`) | DRIFT (DRIFT-02) |
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
- SERVICE: `M8*`, `M9*`, `M10CapabilitySeparationTest`, `P0CServiceTransitionIsolationTest`, `SvcQueueStatusFilterTest`, `SvcAssignCapabilityTest`, `SvcTechnicianEligibilityTest`, `SvcIntakeWithAssignmentTest`, `SvcAssignOutsideATransactionTest`, `SvcAssignPresetTest`, `SvcPaymentCollectTest`, `SvcCollectPresetTest`, `SvcCustomServiceLineTest`, `SvcE2eServiceFixtureSeedTest`.
- STAFF: `H41Staff*`.
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

---

## 10. Deuda técnica activa

| ID | Dominio | Motivo | Prioridad | Depende de |
|---|---|---|---|---|
| THROTTLE-CACHE-01 | INFRA | cache compartida antes de calibrar throttles | Alta | — |
| SEC-SET-04-A | AUTH | throttle de refresh | Media | THROTTLE-CACHE-01 |
| TOKEN-HYGIENE-01 | AUTH | purga de `OutstandingToken`/`BlacklistedToken` | Baja | — |
| CI-01 | INFRA | sin CI | Media | — |
| INFRA-01/02/03 | INFRA | imágenes dev-grade | Media | — |
| FISCAL-PDF-01 | FISCAL | PDF sin línea de descuentos globales | Baja | — |
| IDOR-01 limpieza | SALES | segunda fuente de verdad en resend | Baja | — |
| Lint 33 warnings | FRONTEND | 24 `set-state-in-effect` y otras | Baja | — |
| POS-CUSTOM-PRODUCT | POS | PROPUESTA: vender en caja un artículo que no está en el catálogo. No se implementa con productos falsos ni con `OrderItem.product` nulo | Por decidir | decisión de producto |
| FISCAL-SERVICE | FISCAL | PENDIENTE: un pago de servicio (`RepairPayment`) no produce comprobante electrónico | Por decidir | decisión fiscal |
| SVC-QUOTE-INSHOP | SERVICE | PENDIENTE: la cotización sólo se aprueba desde la cuenta del cliente; no hay aprobación en tienda | Media | decisión de producto |
| SVC-POS-DRAFT | FRONTEND | la recepción a medio llenar se pierde al volver a «Productos»; el modo servicio depende de que cargue el contexto de la caja | Baja | — |
| SVC-ASSIGN-UNASSIGN | SERVICE | `service.orders.assign` también permite retirar al técnico (misma ruta) | Baja | decisión de producto |
| SVC-ELIGIBLE-COST | SERVICE | la lista de candidatos resuelve capacidades y alcance por cada miembro del personal; el camino de asignar ya consulta a una sola persona | Baja | — |
| MIG-ADMIN-LIVE | RBAC | 13 migraciones anteriores (0033…0082) comparan el rol Administrador contra el catálogo vivo; una base atrasada que cruce varias en un solo `migrate` puede dejarlo sin ampliar. 0094 y 0095 usan conjuntos congelados | Media | — |

---

## 11. Últimas fases

- **F0 — Baseline**: medido @ `65aa8c1`; docs @ `d383806`.
- **F1 — Security / tenancy / authorization**: COMPLETED @ `4a9dd5c`. Un fix (FE-AUTH-01). Backend sin cambios. Checkpoint completo en la transcripción de la sesión `acdf85aa`; artefactos en su scratchpad (`audit_f1_investigation.json`, `audit_security_sweep.json`, `demo_branch_scope.py`).
- **F2 — Eje DÓNDE: delegación y alcance por sucursal**: COMPLETADA @ `c191a84`. F-BRANCH-01 (`20d110c`), F-BRANCH-02 (`cccb4d2`), F-BRANCH-03 (`70286d1`), F-CAP-01 (`c042fea`), RBAC-01 (`5afdb81`), RBAC-02 (`d18e983`), WRITE-SCOPE-01 (`fa85d41`, `c076120`), DRIFT-01 (`a4be03b`), DRIFT-07 (`6958ec0`, `f6dc9ca`), E2E-02 (`9c3445f`), E2E-01 (`c191a84`). Sin push. Siguiente fase: por decidir.
- **Integración**: ERP + F1/F2 en `master` por el PR #42 (merge `ef9890f`).
- **SVC-FUNC-01 — Servicio técnico operativo e integración con la caja**: código @ `1d35b7d` en `feature/service-pos-functional-integration` (desde `ef9890f`). SVC-NAV-01 (`937cf82`), SVC-ASSIGN-01 y SVC-TX-01 (`1928b05`), SVC-PAY-01 (`d62fa30`), POS-SVC-01 (`6caa88c`), pruebas del flujo y fixture E2E (`1d35b7d`). Backend 4624 pruebas, 0 fallos; frontend 426; Playwright sin pasada completa limpia: el equipo entró en suspensión durante las corridas y agotó el tiempo de la prueba que estuviera en curso (110 de 121 en la completa, con 4 tiempos agotados y 7 sin ejecutar; `service-pos` 3 de 3 y `h411-auth-interop` 8 de 8 en verde); queda por repetir con el equipo conectado. Sin push, sin PR.

---

## 12. Mapa de consulta rápida

- «¿Quién decide qué sucursales puede operar alguien?» → `tenancy.py::visible_branches`, `_branch_authority`, `_granted_branches`; modelo `MembershipBranchAccess`; `Phase2dBranchAccessApiTest`, `H412SelectedBranchWebTest`.
- «¿Quién puede conceder sucursales?» → `can_manage_company_memberships` (QUÉ) + `tenancy.py::can_delegate_branch_scope` (DÓNDE); escritor `tenant_views.py::_apply_branch_access`; `F2BranchDelegationTest`.
- «¿Quién puede modificar una promoción por sucursal?» → `promotion_views.py::_write_promotion` + `can_delegate_branch_scope`; `F2PromotionBranchScopeTest`, `C13PromotionApiTest`.
- «¿Quién cambia la numeración de una sucursal?» → `settings_views.py::AdminSequenceListView` / `AdminSequenceDetailView` / `AdminSequenceScopeView`; `Phase2eSequenceApiTest`.
- «¿Quién puede leer la ficha de la empresa o sus sucursales?» → `tenant_views.READ_COMPANY` / `READ_BRANCHES` + `access_views._scope_readable`; `F2TenantReadAuthorizationTest`.
- «¿Quién puede cambiar algo que afecta a toda la empresa?» → capacidad + `tenancy.has_company_wide_scope`; `F2SelectedBranchWriteScopeTest`, `F2SelectedCompanySettingsScopeTest`.
- «¿A quién se le puede asignar una orden de servicio?» → `service_services.py::eligible_technicians`; `SvcTechnicianEligibilityTest`.
- «¿Quién puede asignar técnico?» → `V1ServiceOrderAssignmentView.require_assignment_authority`, `ASSIGN_AUTHORITY`; `SvcAssignCapabilityTest`.
- «¿Quién puede cobrar un servicio y quién reversar?» → `v1_service_views.py::COLLECT_AUTHORITY` y `V1ServicePaymentReverseView`; `SvcPaymentCollectTest`.
- «¿Cómo se recibe un equipo desde la caja?» → `app/admin/sales/pos/PosServiceIntake.tsx` → `POST service/orders/` con `technician_id` → `create_repair_order_with_assignment`.
- «¿Cómo se decide un tenant?» → `resolve_company_for_user`, `resolve_public_storefront_company`, `resolve_storefront_company`, `V1InternalSurfaceMixin`.
- «¿Quién puede delegar capacidades?» → `tenancy.py::can_delegate_capabilities`, `can_grant_company_role`; `M11AntiEscalationTest`.
- «¿El proxy puede tocar el admin de Django?» → no, PROXY-01.
