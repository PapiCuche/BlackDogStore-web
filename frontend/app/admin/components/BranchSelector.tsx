"use client";

/**
 * Branch selector for the inventory screens — Phase 2D.
 *
 * THIS IS UX, NOT AUTHORISATION.
 * The options come from `/admin/inventory/branches/`, which returns only the
 * branches the caller may operate. Choosing one re-requests the data with
 * `?branch=`, and the backend re-resolves that id against the caller's own
 * grants: an invented id answers 404, not another shop's stock. Nothing is
 * persisted — no localStorage, no cookie. Authority must not be cacheable on the
 * client, and a stale selection is worse than re-resolving.
 *
 * "Todas las sucursales" is offered only when the caller reaches more than one
 * AND the screen can actually aggregate. It means "everything I can see", which
 * for a restricted operator is NOT the whole company — the caller-visible label
 * under the selector says so, so no heading claims more than it counted.
 */

import { useEffect, useRef, useState } from "react";
import { IconBranch, IconChevronDown } from "./icons";
import type { BranchAccessInfo, BranchParam, InventoryScope } from "../../lib/inventory";

export const ALL_BRANCHES = "all" as const;

type Props = {
  access: BranchAccessInfo | null;
  value: BranchParam;
  onChange: (value: BranchParam) => void;
  /** False on screens where an aggregate makes no sense (a stock movement). */
  allowAll?: boolean;
};

function labelFor(access: BranchAccessInfo | null, value: BranchParam): string {
  if (value === ALL_BRANCHES) return "Todas las sucursales";
  const match = access?.results.find((b) => b.id === value);
  return match?.name ?? "Sucursal";
}

export function BranchSelector({ access, value, onChange, allowAll = true }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocumentClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onDocumentClick);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  const branches = access?.results ?? [];
  const canAggregate = allowAll && Boolean(access?.allows_aggregate);

  if (branches.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-warning-border bg-amber-400/[0.06] px-3 py-2">
        <IconBranch className="h-4 w-4 text-warning" />
        <span className="text-sm text-warning">Sin sucursales asignadas</span>
      </div>
    );
  }

  // One branch and nothing to aggregate: a dropdown with a single option is
  // noise, not a choice.
  if (branches.length === 1 && !canAggregate) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2">
        <IconBranch className="h-4 w-4 text-muted" />
        <span className="truncate text-sm font-medium text-foreground">{branches[0].name}</span>
      </div>
    );
  }

  const options: { key: string; label: string; value: BranchParam }[] = [
    ...(canAggregate
      ? [{ key: ALL_BRANCHES, label: "Todas las sucursales", value: ALL_BRANCHES as BranchParam }]
      : []),
    ...branches.map((b) => ({ key: String(b.id), label: b.name, value: b.id as BranchParam })),
  ];

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm text-foreground transition hover:border-bd-border"
      >
        <IconBranch className="h-4 w-4 text-muted" />
        <span className="max-w-[12rem] truncate font-medium">{labelFor(access, value)}</span>
        <IconChevronDown className="h-3.5 w-3.5 text-muted" />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-50 mt-1.5 min-w-[14rem] overflow-hidden rounded-xl border border-bd-border bg-surface py-1 shadow-2xl"
        >
          {options.map((option) => {
            const selected = option.value === value;
            return (
              <button
                key={option.key}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-2 px-3.5 py-2 text-left text-sm transition hover:bg-surface ${
                  selected ? "text-foreground" : "text-muted"
                }`}
              >
                <span className="truncate">{option.label}</span>
                {selected && <span className="ml-auto text-xs text-muted">✓</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * One line naming the branches a figure actually covers.
 *
 * Rendered under an aggregate so nobody reads a company-wide heading over a
 * two-shop total. Silent when the scope is a single branch: the selector
 * already says which one.
 */
export function ScopeNote({ scope }: { scope: InventoryScope | null | undefined }) {
  if (!scope || !scope.is_aggregate) return null;
  const names = scope.branches.map((b) => b.name).join(" · ");
  return (
    <p className="text-xs text-muted">
      {scope.branches.length === 0
        ? "Sin sucursales visibles."
        : `Agregado de ${scope.branches.length} sucursal${
            scope.branches.length === 1 ? "" : "es"
          }: ${names}`}
    </p>
  );
}
