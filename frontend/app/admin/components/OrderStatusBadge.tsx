"use client";

import { PAYMENT_STATUS_LABELS } from "../../lib/admin";

const STATUS_STYLES: Record<string, string> = {
  paid: "bg-surface-2 text-foreground border-bd-border",
  pending_payment: "bg-surface-2 text-muted border-bd-border",
  failed: "bg-surface text-muted border-bd-border",
  cancelled: "bg-surface text-muted border-bd-border",
  expired: "bg-surface text-muted border-bd-border",
  refunded: "bg-surface text-muted border-bd-border",
};

type Props = { status: string };

export function OrderStatusBadge({ status }: Props) {
  const style = STATUS_STYLES[status] ?? "bg-surface text-muted border-bd-border";
  const label = PAYMENT_STATUS_LABELS[status] ?? status;
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {label}
    </span>
  );
}
