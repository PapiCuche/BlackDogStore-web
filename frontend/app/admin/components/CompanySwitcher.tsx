"use client";

/**
 * Company selector for the internal control topbar.
 *
 * The list comes from the backend (`available_companies`), which only ever
 * returns companies the caller can actually open. Choosing one re-requests the
 * dashboard with `?company=`, and the backend re-validates that id against the
 * caller's own memberships — the selection is a HINT, never authority.
 *
 * Nothing is persisted: no localStorage, no cookie. Authority must not be
 * cacheable on the client, and a stale selection is worse than re-resolving.
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

  // One company: a dropdown with a single option is noise, not a choice.
  if (available.length <= 1 && current) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2">
        <IconStore className="h-4 w-4 text-muted" />
        <span className="truncate text-sm font-medium text-foreground">
          {current.name}
        </span>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-bd-border bg-surface px-3 py-2 text-sm transition hover:border-bd-border"
      >
        <IconStore className="h-4 w-4 text-muted" />
        <span className="max-w-[10rem] truncate font-medium text-foreground">
          {current ? current.name : "Selecciona una empresa"}
        </span>
        <IconChevronDown className="h-4 w-4 text-muted" />
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute right-0 z-50 mt-2 max-h-80 w-72 overflow-y-auto rounded-xl border border-bd-border bg-background p-1.5 shadow-2xl"
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
                  className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition ${
                    isCurrent
                      ? "bg-surface-2 text-foreground"
                      : "text-muted hover:bg-surface hover:text-foreground"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate">{company.name}</span>
                    <span className="block truncate font-mono text-[11px] text-muted">
                      {company.slug}
                    </span>
                  </span>
                  {!company.is_active && (
                    <span className="shrink-0 rounded border border-bd-border px-1.5 py-0.5 text-[10px] text-muted">
                      inactiva
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
