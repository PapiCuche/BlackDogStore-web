"use client";

import { FULFILLMENT_STATUS_LABELS } from "../../lib/admin";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-surface-2 text-muted border-bd-border",
  confirmed: "bg-surface text-foreground/85 border-bd-border",
  preparing: "bg-surface text-foreground border-bd-border",
  ready_for_pickup: "bg-surface-2 text-foreground border-bd-border",
  shipped: "bg-surface-2 text-foreground border-bd-border",
  delivered: "bg-surface-2 text-foreground border-bd-border",
  cancelled: "bg-surface text-muted border-bd-border",
};

type Props = { status: string };

export function FulfillmentStatusBadge({ status }: Props) {
  const style = STATUS_STYLES[status] ?? "bg-surface text-muted border-bd-border";
  const label = FULFILLMENT_STATUS_LABELS[status] ?? status;
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {label}
    </span>
  );
}
