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
      <p className="text-zinc-500 text-sm py-6 text-center">
        No hay órdenes que coincidan.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08] text-zinc-400">
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
              className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
            >
              <td className="py-3 pr-4 text-zinc-400 font-mono text-xs">#{o.id}</td>
              <td className="py-3 pr-4">
                <p className="text-zinc-100 font-medium leading-tight">{o.customer_name || "—"}</p>
                <p className="text-zinc-500 text-xs mt-0.5">{o.customer_email}</p>
              </td>
              <td className="py-3 pr-4 text-right text-zinc-200 hidden md:table-cell">
                S/ {parseFloat(o.total).toFixed(2)}
              </td>
              <td className="py-3 pr-4">
                <OrderStatusBadge status={o.status} />
              </td>
              <td className="py-3 pr-4">
                <FulfillmentStatusBadge status={o.fulfillment_status} />
              </td>
              <td className="py-3 pr-4 text-zinc-500 text-xs hidden lg:table-cell">
                {formatAdminDate(o.created_at)}
              </td>
              <td className="py-3 text-right">
                <Link
                  href={`/admin/orders/${o.id}`}
                  className="text-zinc-400 hover:text-zinc-100 text-xs hover:underline"
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
