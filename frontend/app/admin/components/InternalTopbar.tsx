"use client";

/**
 * Internal control topbar — Phase 2A.2.
 *
 * Shows the tenant context the operator is acting in: company, branch scope,
 * their own roles, and the MASTER badge when it applies.
 *
 * Phase 2D: the branch line is the operator's real SCOPE — their default branch,
 * or how many they can reach. Choosing which one to act on happens on the
 * screens that act on one, not here: a global selector would look like authority
 * and would have to be re-validated on every request anyway.
 *
 * The MASTER badge comes from `access.is_platform_admin`, which the backend
 * derives from `User.is_superuser` alone — never from a role string called
 * "superadmin", which is a company-scoped legacy value.
 */

import Link from "next/link";
import { CompanySwitcher } from "./CompanySwitcher";
import { IconBranch, IconMenu, IconShield } from "./icons";
import type { BranchScope, InternalDashboard } from "../lib/internal-api";
import { roleLabel, type AuthUser } from "../../lib/auth";

import { NotificationBell } from "./NotificationBell";

type Props = {
  user: AuthUser;
  dashboard: InternalDashboard | null;
  onOpenMenu: () => void;
  onSelectCompany: (companyId: number) => void;
};

/**
 * Qué dice la barra sobre las sucursales de quien mira — H4.1.2A.
 *
 * Cero sucursales significa cosas distintas según cómo se conceden: a quien las
 * recibe una a una, que todavía no tiene ninguna; a quien las alcanza todas,
 * que la empresa no tiene ninguna activa. Decir «Sin sucursal» a las dos —y
 * también a quien sí tenía— era el defecto BRANCH-CONTEXT-UI-01.
 *
 * Se exporta aparte porque es la regla, y una regla se prueba sin pintar nada.
 */
export function branchScopeLabel(scope: BranchScope | null | undefined): string | null {
  if (!scope) return null;
  if (scope.branches.length === 1) return scope.branches[0].name;
  if (scope.branches.length > 1) return `${scope.branches.length} sucursales`;
  return scope.mode === "selected" ? "Sin sucursales asignadas" : "Sin sucursales activas";
}

export function InternalTopbar({
  user,
  dashboard,
  onOpenMenu,
  onSelectCompany,
}: Props) {
  const access = dashboard?.access;
  const isMaster = Boolean(access?.is_platform_admin);
  const hasCompany = Boolean(dashboard?.company);
  // Phase 2D: the topbar states the BRANCH SCOPE. There is no branch SELECTOR
  // here on purpose — the choice belongs to the screens that act on one, and a
  // global selector would imply an authority the topbar does not have.
  //
  // H4.1.2A: the scope comes from `branch_scope`, which is access context. It
  // used to come from `inventory.branches`, and the backend only builds that for
  // a caller holding an inventory capability, so a technician with a branch was
  // told they had none.
  const scope = dashboard?.branch_scope ?? null;
  const scopeLabel = branchScopeLabel(scope);

  return (
    <header className="sticky top-0 z-40 border-b border-bd-border bg-background/95 backdrop-blur">
      <div className="flex items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onOpenMenu}
            aria-label="Abrir menú de módulos"
            className="rounded-lg p-2 text-muted transition hover:bg-surface hover:text-foreground lg:hidden"
          >
            <IconMenu />
          </button>

          <div className="min-w-0">
            {hasCompany ? (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-[0.25em] text-muted">
                  Empresa
                </p>
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium text-foreground">
                    {dashboard?.company?.name}
                  </p>
                  {scopeLabel && (
                    <span
                      className="hidden items-center gap-1 text-xs text-muted sm:flex"
                      title={
                        scope && scope.branches.length > 0
                          ? scope.branches.map((b) => b.name).join(" · ")
                          : undefined
                      }
                    >
                      <IconBranch className="h-3.5 w-3.5" />
                      {scopeLabel}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <p className="text-sm text-muted">Panel administrativo</p>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {/* M12B — la bandeja usa SIEMPRE la empresa resuelta ahora, nunca un
              id recordado: un slug guardado sería una autorización guardada. */}
          <NotificationBell slug={dashboard?.company?.slug ?? null} />
          {isMaster && (
            <span
              title="Administrador de plataforma (User.is_superuser)"
              className="hidden items-center gap-1.5 rounded-lg border border-bd-border bg-surface-2 px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-widest text-foreground sm:flex"
            >
              <IconShield className="h-3.5 w-3.5" />
              Master
            </span>
          )}

          {dashboard && dashboard.available_companies.length > 0 && (
            <CompanySwitcher
              current={dashboard.company}
              available={dashboard.available_companies}
              onSelect={onSelectCompany}
            />
          )}

          <div className="hidden text-right md:block">
            <p className="truncate text-sm text-foreground/85">
              {user.first_name || user.username}
            </p>
            <p className="truncate text-[11px] text-muted">
              {access?.roles.length
                ? access.roles.map((r) => r.name).join(" · ")
                : roleLabel(access?.legacy_role ?? user.role)}
            </p>
          </div>

          <Link
            href="/"
            className="hidden rounded-lg border border-bd-border px-3 py-2 text-xs text-muted transition hover:border-bd-border hover:text-foreground sm:block"
          >
            Volver a la tienda
          </Link>
        </div>
      </div>
    </header>
  );
}
