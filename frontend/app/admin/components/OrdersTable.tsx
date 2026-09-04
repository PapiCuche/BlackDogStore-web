"use client";

import Link from "next/link";
import { AdminOrder } from "../../lib/admin";
import { OrderStatusBadge } from "./OrderStatusBadge";
import { FulfillmentStatusBadge } from "./FulfillmentStatusBadge";
import { formatAdminDate } from "../../lib/admin";

type Props = {
  orders: AdminOrder[];
};

export function OrdersTable({ orders }: Props) {
  if (orders.length === 0) {
    return (
      <p className="text-muted text-sm py-6 text-center">
        No hay órdenes que coincidan.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-bd-border text-muted">
            <th className="text-left pb-3 pr-4 font-medium">#</th>
            <th className="text-left pb-3 pr-4 font-medium">Cliente</th>
            <th className="text-right pb-3 pr-4 font-medium hidden md:table-cell">Total</th>
            <th className="text-left pb-3 pr-4 font-medium">Pago</th>
            <th className="text-left pb-3 pr-4 font-medium">Despacho</th>
            <th className="text-left pb-3 pr-4 font-medium hidden lg:table-cell">Fecha</th>
            <th className="text-right pb-3 font-medium">Ver</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr
              key={o.id}
              className="border-b border-bd-border hover:bg-surface transition-colors"
            >
              <td className="py-3 pr-4 text-muted font-mono text-xs">#{o.id}</td>
              <td className="py-3 pr-4">
                <p className="text-foreground font-medium leading-tight">{o.customer_name || "—"}</p>
                <p className="text-muted text-xs mt-0.5">{o.customer_email}</p>
              </td>
              <td className="py-3 pr-4 text-right text-foreground hidden md:table-cell">
                S/ {parseFloat(o.total).toFixed(2)}
              </td>
              <td className="py-3 pr-4">
                <OrderStatusBadge status={o.status} />
              </td>
              <td className="py-3 pr-4">
                <FulfillmentStatusBadge status={o.fulfillment_status} />
              </td>
              <td className="py-3 pr-4 text-muted text-xs hidden lg:table-cell">
                {formatAdminDate(o.created_at)}
              </td>
              <td className="py-3 text-right">
                <Link
                  href={`/admin/orders/${o.id}`}
                  className="text-muted hover:text-foreground text-xs hover:underline"
                >
                  Detalle
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
