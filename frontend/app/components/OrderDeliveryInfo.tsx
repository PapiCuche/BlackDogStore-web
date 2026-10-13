/**
 * DELIVERY-01 — what the buyer is shown of how their order travels: which
 * carrier, which agency and where, the waybill, and a link to follow it.
 *
 * The link is rendered ONLY for an https address, in a new tab that cannot
 * reach back into this page. Anything else the server might send is text or
 * nothing — never an `href`.
 */

export type BuyerDelivery = {
  carrier_name: string;
  agency_name: string;
  agency_locality: string;
  waybill_number: string;
  tracking_url: string;
};

export function OrderDeliveryInfo({ delivery }: { delivery: BuyerDelivery | null | undefined }) {
  if (!delivery) return null;
  const agency = [delivery.agency_name, delivery.agency_locality].filter(Boolean).join(" · ");
  const link = typeof delivery.tracking_url === "string" && delivery.tracking_url.startsWith("https://")
    ? delivery.tracking_url : "";
  if (!delivery.carrier_name && !agency && !delivery.waybill_number && !link) return null;

  return (
    <div data-testid="order-delivery-info" className="mb-4 rounded-xl border border-bd-border bg-background px-4 py-3 text-sm">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Envío</p>
      <dl className="mt-2 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
        {delivery.carrier_name ? (
          <div><dt className="text-xs text-muted">Transportista</dt><dd className="break-words text-foreground">{delivery.carrier_name}</dd></div>
        ) : null}
        {agency ? (
          <div><dt className="text-xs text-muted">Agencia</dt><dd className="break-words text-foreground">{agency}</dd></div>
        ) : null}
        {delivery.waybill_number ? (
          <div><dt className="text-xs text-muted">Número de guía</dt><dd className="break-all text-foreground">{delivery.waybill_number}</dd></div>
        ) : null}
      </dl>
      {link ? (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex font-semibold text-foreground underline underline-offset-4"
        >
          Seguir mi envío
        </a>
      ) : null}
    </div>
  );
}
