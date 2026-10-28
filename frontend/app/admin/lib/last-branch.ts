/**
 * INV-UX-A1 — the branch the person last received into, for the rest of this
 * visit.
 *
 * MEMORY OF THE PAGE, NOT OF THE BROWSER. A module variable: it survives moving
 * between admin screens (client navigation) and dies on reload or sign-out. It
 * is never written to `localStorage`/`sessionStorage` — which branch someone
 * works in is a hint the server re-validates on every request, and persisting
 * it would outlive a revoked grant (same rule as `useBranchScope`).
 */
let lastBranchId: number | null = null;

export const rememberBranch = (id: number | null | undefined): void => {
  lastBranchId = typeof id === "number" && Number.isFinite(id) ? id : null;
};

/** The remembered branch, only if the caller still reaches it; else `fallback`. */
export function preferredBranch(
  reachable: { id: number }[],
  fallback: number | null = null,
): number | null {
  if (lastBranchId !== null && reachable.some((b) => b.id === lastBranchId)) return lastBranchId;
  return fallback;
}

/** For tests only. */
export const forgetBranch = (): void => { lastBranchId = null; };
