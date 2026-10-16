"use client";

/**
 * COMMERCE-ADMIN-UX-01 — «Cobros» del resumen de ventas: el dinero, aparte de
 * las ventas.
 *
 * Las ventas (el resumen de arriba) son sólo pedidos pagados y vigentes. Aquí
 * se ve además el dinero de pedidos cancelados después de pagarse, y los
 * cobros que llegaron con el pedido ya cancelado: sigue en la tienda hasta que
 * su reembolso se confirma. Los reembolsos confirmados bajan el cobro neto;
 * los pendientes se dicen, no se restan.
 */
import type { SalesCollections } from "../lib/internal-api";
import { DashboardSection } from "./dashboard-ui";

function money(value: string) {
  return `S/ ${Number(value).toFixed(2)}`;
}

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

function Box({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId: string }) {
  return (
    <div className="rounded-xl border border-bd-border bg-surface p-4" data-testid={testId}>
      <p className="text-[11px] uppercase tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-display text-xl text-foreground">{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

export function SalesCollectionsSection({
  collections, window = "last_30d",
}: {
  collections: Record<string, SalesCollections> | undefined;
  window?: string;
}) {
  const block = collections?.[window];
  if (!block) return null;
  const late = Number(block.late_charges.amount) > 0
    ? ` · ${money(block.late_charges.amount)} cobrados con el pedido ya cancelado`
    : "";
  return (
    <section role="region" aria-label="Cobros">
      <DashboardSection
        title="Cobros"
        description={`Últimos 30 días. El dinero, aparte de las ventas: ${plural(block.cancelled_orders, "pedido cancelado", "pedidos cancelados")} quedan fuera de las ventas y se conservan en el historial.`}
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Box testId="collections-live" label="Ventas vigentes cobradas" value={money(block.live.amount)}
            hint={plural(block.live.orders, "pedido", "pedidos")} />
          <Box testId="collections-cancelled" label="Cancelados con cobro" value={money(block.cancelled_paid.amount)}
            hint={`${plural(block.cancelled_paid.orders, "pedido", "pedidos")}${late} · aún en la tienda ${money(block.cancelled_held)}`} />
          <Box testId="collections-refunds" label="Reembolsos confirmados" value={money(block.refunds_confirmed)}
            hint={`${money(block.refunds_pending)} pendientes · no se restan hasta confirmarse`} />
          <Box testId="collections-net" label="Cobro neto" value={money(block.net)}
            hint={`${money(block.gross)} cobrados − reembolsos confirmados`} />
        </div>
      </DashboardSection>
    </section>
  );
}
