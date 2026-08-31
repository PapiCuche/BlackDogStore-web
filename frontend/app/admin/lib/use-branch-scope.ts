"use client";

/**
 * Branch scope for the inventory screens — Phase 2D.
 *
 * Loads the branches the caller may operate and holds the current selection.
 * The selection lives in React state and NOWHERE else: not localStorage, not a
 * cookie. It is a hint the backend re-validates on every request, so persisting
 * it would cache something that is not ours to cache and would survive a
 * revoked grant.
 *
 * The initial selection is the backend's `default_branch` (the member's own
 * default, else the company's fulfillment branch, else the first they reach),
 * except on screens that can aggregate and callers who reach several branches —
 * those open on "all", because a chain manager's first question is about the
 * chain.
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchInventoryBranches,
  type BranchAccessInfo,
  type BranchParam,
} from "../../lib/inventory";

export const ALL_BRANCHES = "all" as const;

export type BranchScope = {
  access: BranchAccessInfo | null;
  branch: BranchParam;
  setBranch: (value: BranchParam) => void;
  loading: boolean;
  error: string | null;
  /** True once a branch (or the aggregate) has been chosen and data may load. */
  ready: boolean;
};

export function useBranchScope(options?: { preferAggregate?: boolean }): BranchScope {
  const preferAggregate = options?.preferAggregate ?? false;
  const [access, setAccess] = useState<BranchAccessInfo | null>(null);
  const [branch, setBranch] = useState<BranchParam>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchInventoryBranches();
        if (cancelled) return;
        setAccess(data);
        if (preferAggregate && data.allows_aggregate) {
          setBranch(ALL_BRANCHES);
        } else if (data.default_branch) {
          setBranch(data.default_branch.id);
        } else if (data.results.length > 0) {
          setBranch(data.results[0].id);
        } else {
          // A real state, not a failure: SELECTED mode with no grants yet.
          setBranch(undefined);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar las sucursales.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [preferAggregate]);

  const change = useCallback((value: BranchParam) => setBranch(value), []);

  return {
    access,
    branch,
    setBranch: change,
    loading,
    error,
    ready: !loading && branch !== undefined,
  };
}
