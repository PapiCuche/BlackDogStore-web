"use client";

/**
 * DC3 — the shelf does not cover what the buyer asked for, but an offer would.
 *
 * The server answers `on_order_available` with the offer (days and price); the
 * page shows it here and the buyer decides. Nothing is added «bajo pedido»
 * without that explicit press — it is another price and another wait.
 */

import { deliveryDaysText, type OnOrder } from "../lib/catalog-models";
import { formatMoney } from "../lib/format";

export function OnOrderOfferPrompt({
  offer, busy = false, onAccept,
}: {
  offer: OnOrder;
  busy?: boolean;
  onAccept: () => void;
}) {
  return (
    <div className="mt-4 rounded-xl border border-bd-border bg-background p-4 text-sm" data-testid="on-order-offer">
      <p className="text-foreground">
        No tenemos stock suficiente ahora, pero podemos pedirlo para ti a{" "}
        <span className="font-bold tabular-nums">S/ {formatMoney(offer.price)}</span> c/u.
      </p>
      <button
        type="button"
        onClick={onAccept}
        disabled={busy}
        className="mt-3 w-full rounded-full border border-foreground px-4 py-3 text-sm font-extrabold text-foreground transition hover:bg-foreground hover:text-background focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy ? "Agregando…" : `Pedir bajo pedido — ${deliveryDaysText(offer.lead_time_days)}`}
      </button>
    </div>
  );
}
