"use client";

/**
 * Whether to offer the accounting centre at all.
 *
 * The interface does not offer what the server will refuse (AGENTS §7), so the
 * screen is gated on `accounting.view` — or the platform master, who holds every
 * assignable capability. The authority stays on the server: every endpoint
 * checks it again, and a stale context still meets a 403 there.
 */
type Access = { access: { is_platform_admin: boolean; capabilities: string[] } } | null | undefined;

export const ACCOUNTING_VIEW = "accounting.view";

export function canSeeAccounting(dashboard: Access): boolean {
  if (!dashboard) return false;
  return dashboard.access.is_platform_admin || dashboard.access.capabilities.includes(ACCOUNTING_VIEW);
}

export function AccountingNoAccess() {
  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-8 text-center">
      <p className="text-xs font-semibold uppercase tracking-[0.3em] text-muted">Contabilidad</p>
      <h1 className="mt-3 font-display text-xl font-bold text-foreground">Sin acceso al centro contable</h1>
      <p className="mt-2 text-sm text-muted">
        Tu cuenta no tiene el permiso «Ver el centro contable» en esta empresa. Pídelo a quien administra la empresa.
      </p>
    </div>
  );
}
