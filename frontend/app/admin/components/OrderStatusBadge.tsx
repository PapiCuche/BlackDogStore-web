"use client";

import { PAYMENT_STATUS_LABELS } from "../../lib/admin";

const STATUS_STYLES: Record<string, string> = {
  paid: "bg-white/10 text-white border-white/20",
  pending_payment: "bg-zinc-800 text-zinc-400 border-zinc-700",
  failed: "bg-zinc-900 text-zinc-500 border-zinc-700",
  cancelled: "bg-zinc-900 text-zinc-500 border-zinc-700",
  expired: "bg-zinc-900 text-zinc-500 border-zinc-700",
  refunded: "bg-zinc-900 text-zinc-400 border-zinc-700",
};

type Props = { status: string };

export function OrderStatusBadge({ status }: Props) {
  const style = STATUS_STYLES[status] ?? "bg-zinc-900 text-zinc-500 border-zinc-700";
  const label = PAYMENT_STATUS_LABELS[status] ?? status;
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium ${style}`}>
      {label}
    </span>
  );
}
