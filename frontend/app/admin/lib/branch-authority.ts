/**
 * WHERE the caller may act, read from the server's `branch_scope`.
 *
 * Capabilities answer WHAT; this answers WHERE, and a write needs both. The
 * backend enforces it (F-BRANCH-01, WRITE-SCOPE-01) and stays the authority:
 * these helpers only keep the interface from offering actions the server will
 * refuse. They never widen anything — an unknown or missing scope means "no".
 *
 * The mode comes from `describe_branch_scope` on the server. It is never
 * inferred from how many branches are visible: a SELECTED member granted every
 * branch today still does not reach the one opened tomorrow.
 */
import type { BranchScope } from "./internal-api";

/**
 * Mirror of `tenancy.has_company_wide_scope`: platform master, mode ALL and
 * the legacy bridge reach the whole company.
 */
export function hasCompanyWideScope(scope: BranchScope | null | undefined): boolean {
  return scope?.mode === "platform" || scope?.mode === "all" || scope?.mode === "legacy";
}

/** Whether the caller may operate `branchId` (it is one of their visible branches). */
export function reachesBranch(
  scope: BranchScope | null | undefined,
  branchId: number,
): boolean {
  if (hasCompanyWideScope(scope)) return true;
  return Boolean(scope?.branches.some((b) => b.id === branchId));
}

/** The ids a SELECTED caller may grant: their own visible branches. */
export function reachableBranchIds(scope: BranchScope | null | undefined): Set<number> {
  return new Set(scope?.branches.map((b) => b.id) ?? []);
}

/**
 * Mirror of `tenancy.can_delegate_branch_scope` with `current`: whether the
 * caller may change the branch scope of someone who today holds `mode` +
 * `grantedIds`. A SELECTED caller may not touch anyone who reaches further than
 * they do — taking a branch away is operating it as much as granting it.
 */
export function canEditBranchScopeOf(
  scope: BranchScope | null | undefined,
  target: { mode: "all" | "selected"; grantedIds: number[] },
): boolean {
  if (hasCompanyWideScope(scope)) return true;
  if (scope?.mode !== "selected") return false;
  if (target.mode === "all") return false;
  const reachable = reachableBranchIds(scope);
  return target.grantedIds.every((id) => reachable.has(id));
}
