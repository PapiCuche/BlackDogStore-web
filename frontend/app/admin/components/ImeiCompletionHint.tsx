"use client";

// INV-ID-01 — with exactly 14 digits typed, offer the 15th (Luhn) and show the
// whole number; it is filled in only when the person asks. Fifteen digits are
// left alone: those are checked, never rewritten. The live region is always
// there (review a11y): one that appears with its text is often not announced.

import { imeiCompletion } from "../lib/unit-entry";

export function ImeiCompletionHint({ value, onComplete, id }: {
  value: string; onComplete: (full: string) => void; id?: string;
}) {
  const full = imeiCompletion(value);
  return (
    <div id={id} role="status" aria-live="polite" className="mt-1 text-xs text-muted">
      {full ? (
        <span className="flex flex-wrap items-center gap-2">
          <span>Falta un dígito: ¿es <span className="font-mono text-foreground">{full}</span>?</span>
          <button
            type="button"
            onClick={() => onComplete(full)}
            className="min-h-6 rounded border border-bd-border px-2 font-semibold text-foreground hover:bg-surface-2"
            aria-label={`Completar con ${full.slice(-1)}`}
          >
            Completar
          </button>
        </span>
      ) : null}
    </div>
  );
}
