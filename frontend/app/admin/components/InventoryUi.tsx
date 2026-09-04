"use client";

// Shared presentational pieces for the inventory screens.
// Phase 6.0, extended in 2D for per-branch stock.
// Monochrome to match the rest of the admin panel — no accent hues.

import Link from "next/link";
import type { BranchStockRow, MovementType, StockMovement } from "../../lib/inventory";

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
          ? "border-bd-border bg-surface-2"
          : "border-bd-border bg-surface"
      }`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
        {label}
      </p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-foreground">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
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
    <section className="min-w-0 rounded-xl border border-bd-border bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-bd-border px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-xs text-muted">{description}</p>
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
    <div className="flex items-center gap-3 py-8 text-sm text-muted">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-bd-border border-t-transparent" />
      {label}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-danger-border bg-red-500/[0.07] px-4 py-3">
      <p className="text-sm text-danger">{message}</p>
    </div>
  );
}

export function EmptyBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-bd-border px-4 py-8 text-center">
      <p className="text-sm text-muted">{message}</p>
    </div>
  );
}

export function StockBadge({ value, threshold = 5 }: { value: number; threshold?: number }) {
  const tone =
    value <= 0
      ? "border-bd-border bg-surface-2 text-foreground"
      : value <= threshold
        ? "border-bd-border bg-surface text-foreground"
        : "border-bd-border bg-transparent text-muted";
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
          ? "border-bd-border bg-surface-2 text-foreground"
          : "border-bd-border bg-transparent text-muted"
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
        movement.is_entry ? "text-foreground" : "text-muted"
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
      className={`whitespace-nowrap px-3 py-2.5 text-[11px] font-semibold uppercase tracking-widest text-muted ${
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
        muted ? "text-muted" : "text-foreground/85"
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

// --- Phase 2D: stock rows are per branch ------------------------------------

/**
 * A table of BranchStock rows.
 *
 * The branch column appears only when the rows actually span more than one —
 * repeating the same shop name down a single-branch table is noise, and its
 * absence is not ambiguity because the selector above says which branch it is.
 */
export function BranchStockTable({
  rows,
  emptyMessage,
  showSuggested = false,
}: {
  rows: BranchStockRow[];
  emptyMessage: string;
  showSuggested?: boolean;
}) {
  if (rows.length === 0) return <EmptyBox message={emptyMessage} />;
  const multiBranch = new Set(rows.map((r) => r.branch)).size > 1;

  return (
    <TableWrap>
      <thead>
        <tr className="border-b border-bd-border">
          <Th>Producto</Th>
          {multiBranch ? <Th>Sucursal</Th> : null}
          <Th right>Precio</Th>
          <Th right>Stock</Th>
          <Th right>Mínimo</Th>
          {showSuggested ? <Th right>Sugerido</Th> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} className="border-b border-bd-border">
            <Td>
              <Link
                href={`/admin/products/${row.product}/stock-card`}
                className="transition hover:text-foreground"
              >
                {row.product_name}
              </Link>
            </Td>
            {multiBranch ? <Td muted>{row.branch_name}</Td> : null}
            <Td right muted>{formatSoles(row.product_price)}</Td>
            <Td right>
              <StockBadge
                value={row.quantity}
                threshold={row.minimum_stock > 0 ? row.minimum_stock : 5}
              />
            </Td>
            <Td right muted>{row.minimum_stock || "—"}</Td>
            {showSuggested ? (
              <Td right>
                <span className="tabular-nums text-foreground/85">
                  {row.suggested_quantity || "—"}
                </span>
              </Td>
            ) : null}
          </tr>
        ))}
      </tbody>
    </TableWrap>
  );
}
