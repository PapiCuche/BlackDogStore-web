"use client";

/**
 * Branch selector for inventory screens.
 *
 * This is UX, never authority. Options are server-filtered and every selected
 * id is re-validated by the backend. Nothing is persisted on the client.
 */

import { useEffect, useRef, useState } from "react";
import { IconBranch, IconChevronDown } from "./icons";
import type { BranchAccessInfo, BranchParam, InventoryScope } from "../../lib/inventory";

export const ALL_BRANCHES = "all" as const;

type Props = {
  access: BranchAccessInfo | null;
  value: BranchParam;
  onChange: (value: BranchParam) => void;
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
      <div className="flex items-center gap-2 rounded-lg border border-amber-400/25 bg-amber-400/[0.07] px-3 py-2">
        <IconBranch className="h-4 w-4 text-amber-300" />
        <span className="text-sm text-amber-200">Sin sucursales asignadas</span>
      </div>
    );
  }

  if (branches.length === 1 && !canAggregate) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2">
        <IconBranch className="h-4 w-4 text-muted-foreground" />
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
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm text-foreground transition hover:border-foreground/20 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <IconBranch className="h-4 w-4 text-muted-foreground" />
        <span className="max-w-[12rem] truncate font-medium">{labelFor(access, value)}</span>
        <IconChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label="Sucursales disponibles"
          className="absolute right-0 z-50 mt-2 min-w-[15rem] overflow-hidden rounded-xl border border-bd-border bg-surface p-1.5 shadow-2xl shadow-black/30"
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
                className={`flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
                  selected
                    ? "bg-surface-elevated text-foreground"
                    : "text-muted-foreground hover:bg-background hover:text-foreground"
                }`}
              >
                <span className="truncate">{option.label}</span>
                {selected ? <span className="ml-auto text-xs text-muted-foreground">✓</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export function ScopeNote({ scope }: { scope: InventoryScope | null | undefined }) {
  if (!scope || !scope.is_aggregate) return null;
  const names = scope.branches.map((b) => b.name).join(" · ");
  return (
    <p className="text-xs leading-5 text-muted-foreground">
      {scope.branches.length === 0
        ? "Sin sucursales visibles."
        : `Agregado de ${scope.branches.length} sucursal${
            scope.branches.length === 1 ? "" : "es"
          }: ${names}`}
    </p>
  );
}
