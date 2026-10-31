/**
 * The dialog and the side panel of «Pagos fijos y vencimientos».
 *
 * Focus goes in on open (the first field, else the panel), Tab and Shift+Tab stay inside, Esc closes unless a
 * request is in flight, and focus returns to what opened it.
 *
 * THE KEYS ARE HEARD ON THE WINDOW, NOT ON THE PANEL. A button that gets `disabled` while its request is in flight
 * drops the focus to the page, and a handler on the panel would then never hear Esc or Tab again. Only the
 * TOPMOST open dialog answers (a dialog opened over the side panel does not fight it for the keyboard), and a
 * Tab that arrives while the focus is outside pulls it back in.
 */
import { useEffect, useId, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Open dialogs, oldest first. */
const open: symbol[] = [];

export function FinanceModal({
  title,
  description,
  onClose,
  busy = false,
  variant = "dialog",
  children,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  /** A request is in flight: Esc and the close button wait. */
  busy?: boolean;
  variant?: "dialog" | "drawer";
  children: React.ReactNode;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panel = useRef<HTMLDivElement>(null);

  const busyRef = useRef(busy);
  const closeRef = useRef(onClose);
  useEffect(() => {
    busyRef.current = busy;
    closeRef.current = onClose;
  });

  useEffect(() => {
    const token = Symbol("finance-dialog");
    open.push(token);
    const previous = document.activeElement as HTMLElement | null;
    const node = panel.current;
    const first = node?.querySelector<HTMLElement>(
      'input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled])');
    (first ?? node)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (open[open.length - 1] !== token || !panel.current) return;
      if (event.key === "Escape") {
        event.stopPropagation();
        if (!busyRef.current) closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const stops = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (stops.length === 0) {
        event.preventDefault();
        panel.current.focus();
        return;
      }
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = at === -1 ? 0 : event.shiftKey ? (at === 0 ? stops.length - 1 : at - 1) : at === stops.length - 1 ? 0 : at + 1;
      event.preventDefault();
      stops[next].focus();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      const at = open.indexOf(token);
      if (at !== -1) open.splice(at, 1);
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  const shell =
    variant === "drawer"
      ? "fixed inset-0 z-50 flex justify-end bg-black/50"
      : "fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4";
  const box =
    variant === "drawer"
      ? "h-full w-full max-w-xl overflow-y-auto border-l border-bd-border bg-surface p-5 shadow-xl sm:p-6"
      : "max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6";

  return (
    <div className={shell}>
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={`${box} outline-none`}
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id={titleId} className="min-w-0 break-words text-lg font-bold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={() => { if (!busy) onClose(); }}
            disabled={busy}
            aria-label="Cerrar"
            className="shrink-0 rounded-lg border border-bd-border px-2.5 py-1 text-sm text-muted transition hover:text-foreground disabled:opacity-40"
          >
            ✕
          </button>
        </div>
        {description ? <p id={descriptionId} className="mt-2 text-sm text-muted">{description}</p> : null}
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}
