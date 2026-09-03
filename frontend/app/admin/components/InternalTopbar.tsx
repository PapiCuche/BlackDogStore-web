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
import type { InternalDashboard } from "../lib/internal-api";
import { roleLabel, type AuthUser } from "../../lib/auth";

import { NotificationBell } from "./NotificationBell";

type Props = {
  user: AuthUser;
  dashboard: InternalDashboard | null;
  onOpenMenu: () => void;
  onSelectCompany: (companyId: number) => void;
};

export function InternalTopbar({
  user,
  dashboard,
  onOpenMenu,
  onSelectCompany,
}: Props) {
  const access = dashboard?.access;
  const isMaster = Boolean(access?.is_platform_admin);
  const branch = dashboard?.membership?.branch ?? null;
  const hasCompany = Boolean(dashboard?.company);
  // Phase 2D: the topbar states the BRANCH SCOPE, which is now a real rule
  // rather than a placeholder. `Membership.branch` is the default branch — where
  // the internal control opens — and `inventory.branches` is what the person can
  // actually reach. There is no branch SELECTOR here on purpose: the choice
  // belongs to the screens that act on one, and a global selector would imply an
  // authority the topbar does not have.
  const reachable = dashboard?.inventory?.branches ?? [];
  const scopeLabel = branch
    ? branch.name
    : reachable.length === 0
      ? "Sin sucursal"
      : reachable.length === 1
        ? reachable[0].name
        : `${reachable.length} sucursales`;

  return (
    <header className="sticky top-0 z-40 border-b border-white/[0.06] bg-[#0a0a0a]/95 backdrop-blur">
      <div className="flex items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onOpenMenu}
            aria-label="Abrir menú de módulos"
            className="rounded-lg p-2 text-zinc-400 transition hover:bg-white/5 hover:text-white lg:hidden"
          >
            <IconMenu />
          </button>

          <div className="min-w-0">
            {hasCompany ? (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-[0.25em] text-zinc-600">
                  Empresa
                </p>
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-medium text-zinc-200">
                    {dashboard?.company?.name}
                  </p>
                  <span
                    className="hidden items-center gap-1 text-xs text-zinc-500 sm:flex"
                    title={
                      reachable.length > 0
                        ? reachable.map((b) => b.name).join(" · ")
                        : undefined
                    }
                  >
                    <IconBranch className="h-3.5 w-3.5" />
                    {scopeLabel}
                  </span>
                </div>
              </>
            ) : (
              <p className="text-sm text-zinc-400">Panel administrativo</p>
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
              className="hidden items-center gap-1.5 rounded-lg border border-white/25 bg-white/[0.08] px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-widest text-white sm:flex"
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
            <p className="truncate text-sm text-zinc-300">
              {user.first_name || user.username}
            </p>
            <p className="truncate text-[11px] text-zinc-600">
              {access?.roles.length
                ? access.roles.map((r) => r.name).join(" · ")
                : roleLabel(access?.legacy_role ?? user.role)}
            </p>
          </div>

          <Link
            href="/"
            className="hidden rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-400 transition hover:border-white/20 hover:text-white sm:block"
          >
            Volver a la tienda
          </Link>
        </div>
      </div>
    </header>
  );
}
