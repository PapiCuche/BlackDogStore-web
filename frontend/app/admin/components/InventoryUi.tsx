"use client";

// Shared presentational pieces for the Phase 6.0 inventory screens.
// Monochrome to match the rest of the admin panel — no accent hues.

import type { MovementType, StockMovement } from "../../lib/inventory";

export function StatCard({
  label,
  value,
  hint,
  emphasis = false,
}: {
  label: string;
  value: string | number;
  hint?: string;
  emphasis?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border p-5 ${
        emphasis
          ? "border-white/15 bg-white/[0.06]"
          : "border-white/[0.06] bg-white/[0.02]"
      }`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
        {label}
      </p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-white">{value}</p>
      {hint ? <p className="mt-1 text-xs text-zinc-500">{hint}</p> : null}
    </div>
  );
}

export function Panel({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-white/[0.06] bg-white/[0.02]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-xs text-zinc-500">{description}</p>
          ) : null}
        </div>
        {action}
      </header>
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Spinner({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-8 text-sm text-zinc-500">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-600 border-t-transparent" />
      {label}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-500/25 bg-red-500/[0.07] px-4 py-3">
      <p className="text-sm text-red-300">{message}</p>
    </div>
  );
}

export function EmptyBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-white/10 px-4 py-8 text-center">
      <p className="text-sm text-zinc-500">{message}</p>
    </div>
  );
}

export function StockBadge({ value, threshold = 5 }: { value: number; threshold?: number }) {
  const tone =
    value <= 0
      ? "border-white/25 bg-white/[0.10] text-white"
      : value <= threshold
        ? "border-white/15 bg-white/[0.05] text-zinc-200"
        : "border-white/[0.08] bg-transparent text-zinc-400";
  const label = value <= 0 ? "Agotado" : `${value} u.`;
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-2 py-0.5 text-[11px] font-medium tabular-nums ${tone}`}
    >
      {label}
    </span>
  );
}

export function MovementBadge({ movement }: { movement: StockMovement }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-2 py-0.5 text-[11px] font-medium ${
        movement.is_entry
          ? "border-white/20 bg-white/[0.07] text-white"
          : "border-white/[0.08] bg-transparent text-zinc-400"
      }`}
      title={movement.movement_type_label}
    >
      {movement.movement_type_label}
    </span>
  );
}

export function SignedQty({ movement }: { movement: StockMovement }) {
  return (
    <span
      className={`tabular-nums font-medium ${
        movement.is_entry ? "text-white" : "text-zinc-400"
      }`}
    >
      {movement.is_entry ? "+" : "−"}
      {movement.quantity}
    </span>
  );
}

export function Th({ children, right = false }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={`whitespace-nowrap px-3 py-2.5 text-[11px] font-semibold uppercase tracking-widest text-zinc-500 ${
        right ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  right = false,
  muted = false,
}: {
  children: React.ReactNode;
  right?: boolean;
  muted?: boolean;
}) {
  return (
    <td
      className={`px-3 py-2.5 text-sm ${right ? "text-right" : "text-left"} ${
        muted ? "text-zinc-500" : "text-zinc-300"
      }`}
    >
      {children}
    </td>
  );
}

export function TableWrap({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse">{children}</table>
    </div>
  );
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-PE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatSoles(value: string | number): string {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (Number.isNaN(n)) return "S/ 0.00";
  return `S/ ${n.toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function movementReference(movement: StockMovement): string {
  if (movement.order) return `Orden #${movement.order}`;
  if (movement.reference_type === "manual") return "Manual";
  if (movement.reference_id) return `${movement.reference_type} ${movement.reference_id}`;
  return "—";
}

export type { MovementType };
