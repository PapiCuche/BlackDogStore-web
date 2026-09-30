"use client";

/**
 * Internal control topbar.
 *
 * The company, branch scope and platform-admin markers are presentation of
 * backend-resolved context. Nothing in this component grants authority.
 */

import Link from "next/link";
import { CompanySwitcher } from "./CompanySwitcher";
import { IconBranch, IconMenu, IconShield } from "./icons";
import type { InternalDashboard } from "../lib/internal-api";
import { roleLabel, type AuthUser } from "../../lib/auth";

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
  const reachable = dashboard?.inventory?.branches ?? [];
  const scopeLabel = branch
    ? branch.name
    : reachable.length === 0
      ? "Sin sucursal"
      : reachable.length === 1
        ? reachable[0].name
        : `${reachable.length} sucursales`;

  return (
    <header className="sticky top-0 z-40 border-b border-bd-border bg-background/95 backdrop-blur-xl">
      <div className="flex min-h-[64px] items-center justify-between gap-3 px-4 sm:px-6 xl:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onOpenMenu}
            aria-label="Abrir menú de módulos"
            className="rounded-lg p-2.5 text-muted-foreground transition hover:bg-surface hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent lg:hidden"
          >
            <IconMenu />
          </button>

          <div className="min-w-0">
            {hasCompany ? (
              <>
                <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Contexto activo
                </p>
                <div className="mt-0.5 flex min-w-0 items-center gap-2">
                  <p className="truncate text-sm font-semibold text-foreground">
                    {dashboard?.company?.name}
                  </p>
                  <span
                    className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex"
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
              <>
                <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Control interno
                </p>
                <p className="mt-0.5 text-sm font-semibold text-foreground">
                  Panel administrativo
                </p>
              </>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {isMaster ? (
            <span
              title="Administrador de plataforma (User.is_superuser)"
              className="hidden items-center gap-1.5 rounded-lg border border-foreground/20 bg-foreground/[0.06] px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-foreground sm:flex"
            >
              <IconShield className="h-3.5 w-3.5" />
              Master
            </span>
          ) : null}

          {dashboard && dashboard.available_companies.length > 0 ? (
            <CompanySwitcher
              current={dashboard.company}
              available={dashboard.available_companies}
              onSelect={onSelectCompany}
            />
          ) : null}

          <div className="hidden max-w-48 text-right md:block">
            <p className="truncate text-sm font-medium text-foreground">
              {user.first_name || user.username}
            </p>
            <p className="truncate text-[11px] text-muted-foreground">
              {access?.roles.length
                ? access.roles.map((r) => r.name).join(" · ")
                : roleLabel(access?.legacy_role ?? user.role)}
            </p>
          </div>

          <Link
            href="/"
            className="hidden rounded-lg border border-bd-border bg-surface px-3 py-2 text-xs font-medium text-muted-foreground transition hover:border-foreground/20 hover:bg-surface-elevated hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent sm:inline-flex"
          >
            Tienda ↗
          </Link>
        </div>
      </div>
    </header>
  );
}
