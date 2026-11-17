"use client";

/**
 * P2 · F5c — claims-control warnings (Manual V4 §21) under a text the shop publishes.
 *
 * Staff write free text that the storefront shows: descriptions, grades, used-device notes, campaigns, the home
 * page and the services. A short pause after typing, the text goes to `POST /admin/claims/check/` and each phrase
 * the manual forbids or restricts («oficial», «batería al 100 %», «garantizado»…) is listed with its advice. The
 * rules live on the server (`claims_check.py`), in one place.
 *
 * IT NEVER BLOCKS. It is not an error: no `aria-invalid`, nothing disabled, and if the check fails (no permission,
 * rate limit, network) nothing is shown. The live region is always in the page, so a screen reader hears a warning
 * when it appears and nothing while the list stays the same.
 */

import { useContext, useEffect, useId, useState } from "react";

import { CLAIMS_TEXT_MAX_LENGTH, checkClaims, type ClaimWarning } from "../lib/internal-api";
import { InternalAccessContext } from "./AccessGuard";

/** The pause after the last keystroke before the text is checked. */
export const CLAIMS_CHECK_DELAY_MS = 700;

type Props = {
  /** The text as it is now in the field. */
  text: string;
  /** The company of the screen; by default the one of the guarded page. */
  companyId?: number | null;
  /** For `aria-describedby` on the field. */
  id?: string;
  className?: string;
};

export function ClaimsHint({ text, companyId, id, className = "" }: Props) {
  const access = useContext(InternalAccessContext);
  const company = companyId !== undefined ? companyId : access?.dashboard?.company?.id ?? null;
  const ownId = useId();
  const [found, setFound] = useState<ClaimWarning[]>([]);

  const checkable = text.trim() !== "" && text.length <= CLAIMS_TEXT_MAX_LENGTH;

  useEffect(() => {
    if (!checkable) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      checkClaims(text, company, controller.signal).then(
        (warnings) => {
          if (!controller.signal.aborted) setFound(warnings);
        },
        () => {
          // Advice only: a failed check shows nothing and never stands in the way of saving.
          if (!controller.signal.aborted) setFound([]);
        },
      );
    }, CLAIMS_CHECK_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text, company, checkable]);

  // While the person types, the last answer stays (a list that blinks on every key would be re-announced on every
  // pause); an emptied or over-long field clears at once. An aborted request never writes, so an old answer
  // never replaces a newer one.
  const warnings = checkable ? found : [];

  return (
    <div id={id ?? ownId} role="status" aria-live="polite" className={className}>
      {warnings.length > 0 ? (
        <div className="mt-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">
          <p className="font-semibold">Control de afirmaciones · No impide guardar</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {warnings.map((w) => (
              <li key={`${w.rule}:${w.phrase}`}>
                Revisa: &quot;{w.phrase}&quot; {w.advice}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
