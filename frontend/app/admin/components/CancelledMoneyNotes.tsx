/**
 * Cierre financiero (propietario, 2026-10-16) — el dinero de los pedidos
 * cancelados, dicho aparte en los tableros secundarios. Un cancelado no es
 * venta, pero lo cobrado sigue cobrado hasta que un reembolso se CONFIRMA.
 * Las cifras las calcula el servidor; aquí sólo se nombran.
 */
import type { DashboardCollections } from "../lib/internal-api";

function soles(value: string) {
  return `S/ ${Number(value).toFixed(2)}`;
}

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

export function DashboardCancelledMoney({ collections }: { collections?: DashboardCollections }) {
  if (!collections || collections.cancelled_paid.orders === 0) return null;
  return (
    <p data-testid="dashboard-cancelled-money" className="mt-3 text-xs text-muted">
      {plural(collections.cancelled_paid.orders, "pedido cancelado con pago", "pedidos cancelados con pago")}:{" "}
      {soles(collections.cancelled_paid.amount)} cobrados · {soles(collections.refunds_confirmed)} devueltos ·{" "}
      {soles(collections.refunds_pending)} por devolver · {soles(collections.cancelled_held)} aún en la tienda. No
      cuentan como ventas.
    </p>
  );
}

export function CustomerCancelledNote({
  cancelledOrders, refundsConfirmed,
}: { cancelledOrders?: number; refundsConfirmed?: string }) {
  if (!cancelledOrders) return null;
  return (
    <p data-testid="customer-cancelled-note" className="mt-3 text-xs text-muted">
      {plural(cancelledOrders, "pedido cancelado", "pedidos cancelados")} (fuera de las compras pagadas) ·{" "}
      {soles(refundsConfirmed ?? "0")} reembolsados
    </p>
  );
}
