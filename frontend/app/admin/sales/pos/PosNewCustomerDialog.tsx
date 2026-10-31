"use client";

import { useEffect, useRef } from "react";

import { CustomerForm } from "../../components/CustomerForm";
import type { CustomerRow } from "../../lib/internal-api";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * SALES-UX-02 — «Nuevo cliente» without leaving the till. The basket, the devices already scanned and the sale key
 * stay in the page; this only returns the customer (and any possible duplicates the server reports). Authority is the
 * server's (`service.customers.manage`).
 *
 * A modal dialog: focus enters once and returns to where it was; Tab never leaves it; the page behind does not
 * scroll; Escape closes it — except while a save is in flight, so it never closes under a request.
 */
export function PosNewCustomerDialog({ companyId, onCreated, onClose }: {
  companyId: number | null;
  onCreated: (customer: CustomerRow, possibleDuplicates: CustomerRow[]) => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement | null>(null);
  const busy = useRef(false);
  // The parent passes a new function on every render; the effect must not restart because of it.
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const scroll = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!busy.current) closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const stops = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!stops.length) {
        event.preventDefault();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panel.current || !panel.current.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !panel.current.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = scroll;
      previous?.focus?.();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pos-new-customer-title"
        tabIndex={-1}
        className="max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-t-2xl border border-bd-border bg-surface p-5 shadow-xl sm:rounded-2xl"
      >
        <h2 id="pos-new-customer-title" className="mb-4 font-display text-xl text-foreground">Nuevo cliente</h2>
        <CustomerForm
          companyId={companyId}
          customer={null}
          onSaved={(saved, duplicates) => onCreated(saved as unknown as CustomerRow, duplicates)}
          onCancel={onClose}
          onUseExisting={(existing) => onCreated(existing, [])}
          onBusyChange={(value) => { busy.current = value; }}
        />
      </div>
    </div>
  );
}
