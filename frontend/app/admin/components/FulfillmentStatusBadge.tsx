"use client";

import { FULFILLMENT_STATUS_LABELS } from "../../lib/admin";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-zinc-800 text-zinc-400 border-zinc-700",
  confirmed: "bg-white/5 text-zinc-300 border-white/10",
  preparing: "bg-white/5 text-zinc-200 border-white/15",
  ready_for_pickup: "bg-white/10 text-white border-white/20",
  shipped: "bg-white/10 text-white border-white/20",
  delivered: "bg-white/15 text-white border-white/25",
  cancelled: "bg-zinc-900 text-zinc-500 border-zinc-700",
};

type Props = { status: string };

export function FulfillmentStatusBadge({ status }: Props) {
  const style = STATUS_STYLES[status] ?? "bg-zinc-900 text-zinc-500 border-zinc-700";
  const label = FULFILLMENT_STATUS_LABELS[status] ?? status;
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {label}
    </span>
  );
}
