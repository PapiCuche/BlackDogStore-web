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
| Último SHA verificado | `70286d1` (F2: F-BRANCH-01/02/03). Backend cambia en `tenancy.py`, `tenant_views.py`, `staff_views.py`, `promotion_views.py`, `settings_views.py`, `tests.py` |
| Fecha | 2026-09-30 |
| Working tree | limpio |
| Migraciones | 105 aplicadas / 0 pendientes / 0 por generar (`store` llega a `0093_sales_service_delivery`) |
| Backend tests | 4520 OK (3 skipped), PostgreSQL 14, 1520,6 s @ `70286d1` (baseline: 4489 @ `65aa8c1`) |
| Frontend tests | 368/368 OK, 34 suites, @ `4a9dd5c` |
| TypeScript | `tsc --noEmit` OK @ `4a9dd5c` |
| Lint | 0 errores / 33 advertencias @ `4a9dd5c` |
| Build | OK (44 páginas) @ `65aa8c1` |
| E2E | 113/118 @ `65aa8c1`; E2E-01 falla, 4 serie no ejecutadas |

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

**BRANCH-02** — Un grant de sucursal sólo puede apuntar a sucursales de la misma
empresa (queryset limitado a `membership.company`).
Autoridad: `tenant_views.py::_apply_branch_access`.
Estado: VERIFICADO @ `70286d1`.

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
Estado: VERIFICADO @ `4a9dd5c`. Consecuencia en E2E: E2E-02.

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
- `service_services.py`, `v1_service_views.py` (`V1ServiceSurfaceMixin.get_order`, asignación devuelve `{current, candidates}`).
- Frontend: `app/lib/service-console.ts`, `app/admin/service/`.
- Tests: `M8*`, `M9*`, `M10CapabilitySeparationTest`, `StabilizationServiceAccessTest`, `P0CServiceTransitionIsolationTest`.

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
| DRIFT-07 | LOW | BRANCH/FRONTEND | `BranchAccessPanel.tsx`, `staff/page.tsx` (invitación, default «todas»), `promotion_views.py` lista `branches` = todas las de la empresa | la UI ofrece a un admin SELECTED «todas» o sucursales que no alcanza; el backend responde 403 con `detail` legible |
| F-CAP-01 | MEDIUM | RBAC/INVENTORY | `admin_views.py` product create `post` (`opening_stock`) | `products.manage` sin `inventory.adjust`: POST producto `inventory=7` → 201 + Kardex `initial_stock` |
| F-TENANT-01 | MEDIUM | TENANCY | `tenant_views.py::AdminMembershipListView.post` + `serializers.py::MembershipSerializer` | POST membresía con `user` de otra plataforma → 201 devuelve su `username` |
| DRIFT-01 | MEDIUM | SERVICE/FRONTEND | `frontend/app/lib/service-console.ts` (`technicians`) vs `v1_service_views.py` (`candidates`) | selector de técnicos siempre vacío |
| E2E-02 | MEDIUM | TESTS | `frontend/e2e/staff-personnel.spec.ts` (test I) | desactiva la primera ficha; cuenta queda con 0 capacidades |
| E2E-01 | MEDIUM | TESTS/FISCAL | `frontend/e2e/fiscal-invoice.spec.ts` | espera «Emitir factura», UI dice «Preparar factura» desde `b3b1cfc` |
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
| RBAC-01 | LOW | RBAC | `tenant_views.py` company/branch list/detail, catálogo de capacidades | legible con 0 capacidades |
| RBAC-02 | LOW | RBAC | `AdminMembershipListView.post`, `AdminMembershipDetailView.patch` (`branch`) | 404 vs 400: oráculo de existencia de sucursal; test fija conducta actual (`tests.py` ~7407) |
| RBAC-F6/F7/F4/F5/F11 | LOW | FRONTEND | transfers `[id]`, `users/page.tsx`, `staff/page.tsx`, `internal-modules.ts`, `orders/[id]` | UI ofrece acciones que el backend niega |
| INV-LEGACY-V1-F1/F3 | LOW | INVENTORY | `serializers.py` movimientos legacy; v1 transfers `page_size<0` → 500 | |
| Sweep LOW/INFO | LOW/INFO | varios | SEC-SET-01/03/05…10, SEC-SET-04-B, AUTH-LOGGING-01, REFRESH-CSRF-01, ENUM-01, COOKIE-PATH-01, TOKEN-HYGIENE-01, ENV-01…04, INFRA-04…08, DEP-01…04/06…08, CI-02, DOC-01 | ver checkpoint |

SIN VEREDICTO (no abiertos por F1): SEC-01…SEC-10 (secrets), FE-AUTH-02/04/06/07/08/09.
NO AUDITADO: webhook de pagos (firma/replay), `AdminAuditLogListView` (acotado por tenant),
contrato `V1StockAdjustmentSerializer`.

NO AUDITADO (surgido en F2): una admin SELECTED con `company.manage` puede editar la
serie de nivel empresa y cambiar el alcance de numeración (`AdminSequenceScopeView`),
que afectan a todas las sucursales; y lista promociones de todas las sucursales
(lectura). Requiere decisión de negocio antes de clasificarlo.

---

## 6. Hallazgos cerrados

| ID | Sev. original | Fix SHA | Símbolo | Regresión | Estado |
|---|---|---|---|---|---|
| FE-AUTH-01 | HIGH | `4a9dd5c` | `frontend/app/api/[...path]/route.ts` | `frontend/__tests__/api-proxy-scope.test.ts` 11/11 + Playwright `pos-receipt-options`, `pos-ticket`, `h411-auth-interop` 8/8 | CORREGIDO |
| F-BRANCH-01 | MEDIUM | `20d110c` | `tenancy.can_delegate_branch_scope`; `tenant_views` membership create/patch; `staff_views` invitaciones (mismo hueco, hallado en F2) | `F2BranchDelegationTest` | CORREGIDO |
| F-BRANCH-01 · escritura parcial | LOW | `20d110c` | `AdminMembershipDetailView.patch` en `transaction.atomic` | `F2BranchDelegationTest.test_a_rejected_grant_list_rolls_the_whole_update_back` | CORREGIDO |
| F-BRANCH-02 | MEDIUM | `cccb4d2` | `promotion_views._write_promotion` | `F2PromotionBranchScopeTest` | CORREGIDO |
| F-BRANCH-03 | MEDIUM | `70286d1` | `settings_views.AdminSequenceDetailView._scoped` | `F2SequenceBranchScopeTest` | CORREGIDO |
| IDOR-01 | — | — | `admin_views.py::AdminOrderResendEmailView.post` | — | REFUTADO (deuda de limpieza: `order.save(update_fields=…)`) |
| DEV-DEMO-01 | — | — | `dev_accounts_views.py` | — | REFUTADO (fail-closed por `DEBUG`) |

---

## 7. Contratos frontend ↔ backend

| Contrato | Backend | Frontend | Estado |
|---|---|---|---|
| SERVICE-ASSIGNMENT | `{current, candidates}` (`v1_service_views.py`; fijado por tests M8) | `{current, technicians}` (`service-console.ts`) | DRIFT (DRIFT-01) |
| INVENTORY-ADJUST branch | acepta sucursal / usa default | no envía sucursal (`app/lib/admin.ts`) | DRIFT (DRIFT-02) |
| DRF field errors | `{field: [msg]}` | clientes que sólo leen `detail` | DRIFT LOW (DRIFT-03) |
| Legacy role sets | backend | frontend | OK (`H412bFrontendLegacyRoleParityTest`) |
| POS `receipt_options` | `enabled` + causa | selector | OK (`Fiscal6ReceiptOptionsTest`, E2E `pos-receipt-options`) |

---

## 8. Tests índice

Backend: `backend/store/tests.py` (≈60 k líneas, 539 clases). Frontend: `frontend/__tests__/`. E2E: `frontend/e2e/`.

- TENANCY: `SaasTenancyResolutionTest`, `SaasIsolationApiTest`, `Phase2dCrossTenantIsolationTest`, `Erp1CrossCompanyReadIsolationTest`, `V1TenantSelectorAuthorityTest`, `H412bIsolationMatrixTest`.
- RBAC: `Phase2aCapabilityMatrixTest`, `M11AntiEscalationTest`, `G3*`, `M6CapabilityRevocationTest`, `H412SaasCapabilityAuthorityTest`.
- BRANCH: `F2BranchDelegationTest`, `F2PromotionBranchScopeTest`, `F2SequenceBranchScopeTest`, `Phase2dBranchAccessApiTest`, `Phase2dBranchScopedReadsTest`, `C15BranchAccessRevocationTest`, `Phase2eBranchScopeTest`, `Phase2eSequenceApiTest`, `Phase4BranchScopeTest`, `H412SelectedBranch*`.
- INVENTORY: `Phase60*`, `M7Inventory*`, `C11TransferReserveTest`, `C14StockWriterDisciplineTest`.
- POS: `C1Pos*`, `HardeningPosPaymentAuthorityTest`, `Ip1Pos*`.
- PROMOTIONS: `C13Promotion*`, `C14PromotionTenantInvariantTest`.
- FISCAL: `C22B*`, `Fiscal5a*`, `Fiscal5b*`, `Fiscal6*`.
- SERVICE: `M8*`, `M9*`, `M10CapabilitySeparationTest`, `P0CServiceTransitionIsolationTest`.
- STAFF: `H41Staff*`.
- FRONTEND: `api-proxy-scope.test.ts`.
- E2E: `h411-auth-interop`, `pos-ticket`, `pos-receipt-options`, `staff-personnel` (E2E-02), `fiscal-invoice` (E2E-01).

---

## 9. Migraciones relevantes

| Número | Objeto | Invariante |
|---|---|---|
| 0014 | Company, Branch, Membership | TENANT-01 |
| 0016 / 0017 | áreas, roles, presets (0017 reparte `products.manage` + `inventory.adjust` juntos) | RBAC-G3, F-CAP-01 atenuante |
| 0024 / 0025 | multisucursal, `MembershipBranchAccess`, `branch_access_mode` | BRANCH-01/02 |
| 0029 / 0030 | `InternalSequence` | F-BRANCH-03 |
| 0056 | unicidad de asignación de rol | RBAC |
| 0083 | invitaciones de personal (la invitación lleva su propio `branch_access_mode`) | BRANCH |
| 0084 | revocación de tokens | AUTH |

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

---

## 11. Últimas fases

- **F0 — Baseline**: medido @ `65aa8c1`; docs @ `d383806`.
- **F1 — Security / tenancy / authorization**: COMPLETED @ `4a9dd5c`. Un fix (FE-AUTH-01). Backend sin cambios. Checkpoint completo en la transcripción de la sesión `acdf85aa`; artefactos en su scratchpad (`audit_f1_investigation.json`, `audit_security_sweep.json`, `demo_branch_scope.py`).
- **F2 — Eje DÓNDE: delegación y alcance por sucursal**: EN CURSO. Cerrados F-BRANCH-01 (`20d110c`), F-BRANCH-02 (`cccb4d2`), F-BRANCH-03 (`70286d1`). Pendiente: F-CAP-01 (decisión: ¿`products.manage` abre saldo sin `inventory.adjust`?) → RBAC-01/02 → DRIFT-01 → DRIFT-07 → E2E-02 → E2E-01.

---

## 12. Mapa de consulta rápida

- «¿Quién decide qué sucursales puede operar alguien?» → `tenancy.py::visible_branches`, `_branch_authority`, `_granted_branches`; modelo `MembershipBranchAccess`; `Phase2dBranchAccessApiTest`, `H412SelectedBranchWebTest`.
- «¿Quién puede conceder sucursales?» → `can_manage_company_memberships` (QUÉ) + `tenancy.py::can_delegate_branch_scope` (DÓNDE); escritor `tenant_views.py::_apply_branch_access`; `F2BranchDelegationTest`.
- «¿Quién puede modificar una promoción por sucursal?» → `promotion_views.py::_write_promotion` + `can_delegate_branch_scope`; `F2PromotionBranchScopeTest`, `C13PromotionApiTest`.
- «¿Quién cambia la numeración de una sucursal?» → `settings_views.py::AdminSequenceListView` / `AdminSequenceDetailView` / `AdminSequenceScopeView`; `Phase2eSequenceApiTest`.
- «¿Cómo se decide un tenant?» → `resolve_company_for_user`, `resolve_public_storefront_company`, `resolve_storefront_company`, `V1InternalSurfaceMixin`.
- «¿Quién puede delegar capacidades?» → `tenancy.py::can_delegate_capabilities`, `can_grant_company_role`; `M11AntiEscalationTest`.
- «¿El proxy puede tocar el admin de Django?» → no, PROXY-01.
