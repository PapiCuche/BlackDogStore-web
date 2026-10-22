"use client";

// INV-ID-01 — with exactly 14 digits typed, offer the 15th (Luhn) and show the
// whole number; it is filled in only when the person asks. Fifteen digits are
// left alone: those are checked, never rewritten.

import { imeiCompletion } from "../lib/unit-entry";

export function ImeiCompletionHint({ value, onComplete }: { value: string; onComplete: (full: string) => void }) {
  const full = imeiCompletion(value);
  if (!full) return null;
  return (
    <p role="status" className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
      <span>Falta un dígito: ¿es <span className="font-mono text-foreground">{full}</span>?</span>
      <button
        type="button"
        onClick={() => onComplete(full)}
        className="rounded border border-bd-border px-2 py-0.5 font-semibold text-foreground hover:bg-surface-2"
        aria-label={`Completar con ${full.slice(-1)}`}
      >
        Completar
      </button>
    </p>
  );
}
