"use client";

/**
 * Company selector for internal control.
 *
 * The available list is server-filtered and selecting a company is only a UX
 * hint: every subsequent request is re-authorized by the backend.
 */

import { useEffect, useRef, useState } from "react";
import { IconChevronDown, IconStore } from "./icons";
import type { CompanySummary } from "../lib/internal-api";

type Props = {
  current: CompanySummary | null;
  available: CompanySummary[];
  onSelect: (companyId: number) => void;
};

export function CompanySwitcher({ current, available, onSelect }: Props) {
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

  if (available.length <= 1 && current) {
    return (
      <div className="hidden items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2 sm:flex">
        <IconStore className="h-4 w-4 text-muted-foreground" />
        <span className="max-w-40 truncate text-sm font-medium text-foreground">
          {current.name}
        </span>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm transition hover:border-foreground/20 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <IconStore className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="max-w-[8rem] truncate font-medium text-foreground sm:max-w-[11rem]">
          {current ? current.name : "Selecciona una empresa"}
        </span>
        <IconChevronDown
          className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open ? (
        <ul
          role="listbox"
          aria-label="Empresas disponibles"
          className="absolute right-0 z-50 mt-2 max-h-80 w-72 overflow-y-auto rounded-xl border border-bd-border bg-surface p-1.5 shadow-2xl shadow-black/30"
        >
          {available.map((company) => {
            const isCurrent = current?.id === company.id;
            return (
              <li key={company.id} role="option" aria-selected={isCurrent}>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    onSelect(company.id);
                  }}
                  className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
                    isCurrent
                      ? "bg-surface-elevated text-foreground"
                      : "text-muted-foreground hover:bg-background hover:text-foreground"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{company.name}</span>
                    <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
                      {company.slug}
                    </span>
                  </span>
                  {!company.is_active ? (
                    <span className="shrink-0 rounded-md border border-bd-border px-1.5 py-0.5 text-[9px] uppercase tracking-[0.1em] text-muted-foreground">
                      inactiva
                    </span>
                  ) : isCurrent ? (
                    <span className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                      actual
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
