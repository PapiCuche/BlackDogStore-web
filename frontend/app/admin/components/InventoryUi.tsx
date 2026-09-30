"use client";

import Link from "next/link";
import type { BranchStockRow, MovementType, StockMovement } from "../../lib/inventory";
import {
  ErrorBox,
  EmptyState,
  Panel as BasePanel,
  Spinner,
  StatCard as BaseStatCard,
  TableWrap,
  Td,
  Th,
} from "./internal-ui";

export { ErrorBox, Spinner, TableWrap, Td, Th };

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
  return <BaseStatCard label={label} value={value} hint={hint} emphasis={emphasis} />;
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
  return <BasePanel title={title} description={description} action={action}>{children}</BasePanel>;
}

export function EmptyBox({ message }: { message: string }) {
  return <EmptyState message={message} />;
}

export function StockBadge({ value, threshold = 5 }: { value: number; threshold?: number }) {
  const tone =
    value <= 0
      ? "border-red-500/30 bg-red-500/[0.07] text-red-300"
      : value <= threshold
        ? "border-amber-400/30 bg-amber-400/[0.07] text-amber-300"
        : "border-bd-border bg-background text-muted-foreground";
  const label = value <= 0 ? "Agotado" : `${value} u.`;

  return (
    <span className={`inline-flex whitespace-nowrap rounded-md border px-2 py-1 text-[10px] font-medium tabular-nums ${tone}`}>
      {label}
    </span>
  );
}

export function MovementBadge({ movement }: { movement: StockMovement }) {
  return (
    <span
      className={`inline-flex whitespace-nowrap rounded-md border px-2 py-1 text-[10px] font-medium ${
        movement.is_entry
          ? "border-emerald-400/25 bg-emerald-400/[0.06] text-emerald-300"
          : "border-bd-border bg-background text-muted-foreground"
      }`}
      title={movement.movement_type_label}
    >
      {movement.movement_type_label}
    </span>
  );
}

export function SignedQty({ movement }: { movement: StockMovement }) {
  return (
    <span className={`tabular-nums font-medium ${movement.is_entry ? "text-emerald-300" : "text-muted-foreground"}`}>
      {movement.is_entry ? "+" : "−"}
      {movement.quantity}
    </span>
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
      <thead className="bg-background/50">
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
          <tr key={row.id} className="border-b border-bd-border last:border-0 transition hover:bg-foreground/[0.02]">
            <Td>
              <Link
                href={`/admin/products/${row.product}/stock-card`}
                className="transition hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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
                <span className="tabular-nums text-foreground">{row.suggested_quantity || "—"}</span>
              </Td>
            ) : null}
          </tr>
        ))}
      </tbody>
    </TableWrap>
  );
}
