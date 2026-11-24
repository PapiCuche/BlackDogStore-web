"use client";

/**
 * SALES-QUOTE-01 — el estado de una cotización. «Vencida» lo dice el servidor (la vigencia pasó), no el reloj
 * del navegador.
 */
import { QUOTE_STATUS_LABEL, type QuoteStatus } from "../../lib/sales-quotes-api";

const TONE: Record<QuoteStatus, string> = {
  draft: "border-bd-border text-foreground/80",
  approved: "border-info-border bg-info-surface text-info",
  sent: "border-info-border bg-info-surface text-info",
  accepted: "border-success-border bg-success-surface text-success",
  converted: "border-success-border bg-success-surface text-success",
  rejected: "border-danger-border bg-danger-surface text-danger",
  expired: "border-warning-border bg-warning-surface text-foreground",
  superseded: "border-bd-border text-muted line-through",
};

export function QuoteStatusBadge({ status }: { status: QuoteStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${TONE[status] ?? TONE.draft}`}>
      {QUOTE_STATUS_LABEL[status] ?? status}
    </span>
  );
}
