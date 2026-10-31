"use client";

import { useEffect, useRef } from "react";

import { CustomerForm } from "../../components/CustomerForm";
import type { CustomerRow } from "../../lib/internal-api";

/**
 * SALES-UX-02 — «Nuevo cliente» without leaving the till. The basket, the devices already scanned and the sale key
 * stay in the page; this only returns the customer. Authority is the server's (`service.customers.manage`).
 */
export function PosNewCustomerDialog({ companyId, onCreated, onClose }: {
  companyId: number | null;
  onCreated: (customer: CustomerRow) => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [onClose]);

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
          onSaved={(saved) => onCreated(saved as unknown as CustomerRow)}
          onCancel={onClose}
          onUseExisting={onCreated}
        />
      </div>
    </div>
  );
}
