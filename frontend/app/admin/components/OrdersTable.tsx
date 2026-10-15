"use client";

import Link from "next/link";
import { AdminOrder, SUPPLY_STATUS_LABELS, formatAdminDate } from "../../lib/admin";
import { OrderStatusBadge } from "./OrderStatusBadge";
import { FulfillmentStatusBadge } from "./FulfillmentStatusBadge";

type Props = {
  orders: AdminOrder[];
};

const RISK_LEVEL_TEXT: Record<string, string> = { medium: "riesgo medio", high: "riesgo alto" };

/**
 * CLIENT-SEC-01 — what the list says about an order's assessment, or null.
 * Only what asks for attention: a review that is waiting, or a level above
 * low. An ordinary order, and one that was not assessed, say nothing.
 */
export function orderRiskBadge(order: Pick<AdminOrder, "risk_level" | "review_status">): string | null {
  const level = order.risk_level ? RISK_LEVEL_TEXT[order.risk_level] : undefined;
  if (order.review_status === "pending") return level ? `Por revisar · ${level}` : "Por revisar";
  return level ? level.charAt(0).toUpperCase() + level.slice(1) : null;
}

export function OrdersTable({ orders }: Props) {
  if (orders.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted">
        No hay órdenes que coincidan.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] text-sm">
        <thead>
          <tr className="border-b border-bd-border text-left text-[11px] uppercase tracking-[0.1em] text-muted">
            <th className="px-4 py-3 font-semibold">#</th>
            <th className="px-4 py-3 font-semibold">Cliente</th>
            <th className="hidden px-4 py-3 text-right font-semibold md:table-cell">Total</th>
            <th className="px-4 py-3 font-semibold">Pago</th>
            <th className="px-4 py-3 font-semibold">Despacho</th>
            <th className="hidden px-4 py-3 font-semibold lg:table-cell">Fecha</th>
            <th className="px-4 py-3 text-right font-semibold">Acción</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr
              key={order.id}
              className="border-b border-bd-border/70 transition last:border-0 hover:bg-foreground/[0.025]"
            >
              <td className="px-4 py-3 font-mono text-xs text-muted">#{order.id}</td>
              <td className="px-4 py-3">
                <p className="font-medium leading-tight text-foreground">{order.customer_name || "—"}</p>
                <p className="mt-1 text-xs text-muted">{order.customer_email}</p>
                {order.has_stock_shortfall && (
                  <span
                    data-testid="order-shortfall-badge"
                    className="mt-1 inline-flex items-center rounded border border-warning-border bg-warning-surface px-2 py-0.5 text-[11px] font-medium text-warning"
                  >
                    Faltante de stock
                  </span>
                )}
                {orderRiskBadge(order) && (
                  <span
                    data-testid="order-risk-badge"
                    className={
                      order.review_status === "pending"
                        ? "ml-1 mt-1 inline-flex items-center rounded border border-warning-border bg-warning-surface px-2 py-0.5 text-[11px] font-medium text-warning first:ml-0"
                        : "ml-1 mt-1 inline-flex items-center rounded border border-bd-border px-2 py-0.5 text-[11px] font-medium text-muted first:ml-0"
                    }
                  >
                    {orderRiskBadge(order)}
                  </span>
                )}
                {/* DC3: lo vendido bajo pedido. «Por confirmar» pide que alguien actúe. */}
                {order.supply_status && SUPPLY_STATUS_LABELS[order.supply_status] && (
                  <span
                    data-testid="order-supply-badge"
                    className={
                      order.supply_status === "to_confirm" || order.supply_status === "failed"
                        ? "ml-1 mt-1 inline-flex items-center rounded border border-warning-border bg-warning-surface px-2 py-0.5 text-[11px] font-semibold text-warning first:ml-0"
                        : "ml-1 mt-1 inline-flex items-center rounded border border-bd-border px-2 py-0.5 text-[11px] font-medium text-muted first:ml-0"
                    }
                  >
                    Bajo pedido · {SUPPLY_STATUS_LABELS[order.supply_status]}
                  </span>
                )}
              </td>
              <td className="hidden px-4 py-3 text-right font-medium tabular-nums text-foreground md:table-cell">
                S/ {parseFloat(order.total).toFixed(2)}
              </td>
              <td className="px-4 py-3"><OrderStatusBadge status={order.status} /></td>
              <td className="px-4 py-3"><FulfillmentStatusBadge status={order.fulfillment_status} /></td>
              <td className="hidden px-4 py-3 text-xs text-muted lg:table-cell">
                {formatAdminDate(order.created_at)}
              </td>
              <td className="px-4 py-3 text-right">
                <Link
                  href={`/admin/orders/${order.id}`}
                  className="text-xs font-semibold text-foreground transition hover:underline"
                >
                  Ver detalle
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
