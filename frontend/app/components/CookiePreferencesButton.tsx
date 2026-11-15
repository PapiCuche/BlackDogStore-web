"use client";

import { openConsentPreferences } from "../lib/consent";

/** Opens the EXISTING consent dialog: one banner, one consent state. */
export function CookiePreferencesButton({ className = "" }: { className?: string }) {
  return (
    <button
      type="button"
      onClick={openConsentPreferences}
      className={`min-h-11 rounded-xl border border-bd-border px-4 py-2 text-sm font-semibold hover:bg-surface ${className}`}
    >
      Cambiar preferencias de cookies
    </button>
  );
}
