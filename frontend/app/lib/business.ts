/**
 * Business labels and copy for the checkout — Phase 3.
 *
 * WHAT LEFT THIS FILE
 * The store's identity: name, legal name, RUC, address, phone, WhatsApp. Those
 * were module constants naming one specific business, imported into a checkout
 * that every tenant's customers use. They now come from the storefront config,
 * resolved from the request host — see `lib/storefront.ts`.
 *
 * WHAT STAYS
 * Vocabulary of the domain: what a "boleta" is, what delivery methods exist.
 * Those are the same words for every tenant on this platform and are matched by
 * value against the backend's own choices; making them configurable would mean
 * two places to keep a fixed enum in sync.
 *
 * The consent texts are BUILT from the tenant's identity rather than stored,
 * because a customer agreeing to share their data must be told which company
 * they are sharing it with — and that is different per storefront.
 */

import type { StorefrontConfig } from "./storefront";

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  dni: "DNI",
  ruc: "RUC",
  ce: "Carnet de Extranjería",
};

export const DELIVERY_METHOD_LABELS: Record<string, string> = {
  pickup_store: "Recojo en tienda",
  delivery_arequipa: "Delivery local",
  national_shipping: "Envío nacional",
};

export const RECEIPT_TYPE_LABELS: Record<string, string> = {
  boleta: "Boleta",
  factura: "Factura",
};

/**
 * The data-processing consent, naming the company the customer is buying from.
 *
 * Falls back to "la tienda" when the tenant has not filled in its legal details.
 * Vague is not ideal; naming the wrong company would be worse than vague.
 */
export function termsText(config: StorefrontConfig): string {
  const { name, legal_name, tax_id } = config.company;
  const who = name || "la tienda";
  const operator =
    legal_name && tax_id
      ? `, operada por ${legal_name} con RUC ${tax_id},`
      : legal_name
        ? `, operada por ${legal_name},`
        : "";
  return (
    `Declaro que la información ingresada es correcta y acepto que ${who}` +
    `${operator} utilice estos datos únicamente para procesar mi compra, ` +
    `coordinar la entrega y emitir el comprobante correspondiente.`
  );
}

/** The warranty consent — this tenant's own policy when it has written one. */
export function warrantyText(config: StorefrontConfig): string {
  const who = config.company.name || "la tienda";
  if (config.policies.warranty_text) {
    return `Acepto la política de garantía de ${who}: ${config.policies.warranty_text}`;
  }
  return (
    `Acepto la política de garantía de ${who}. Entiendo que las condiciones ` +
    `pueden variar según el producto y los términos informados al momento de la compra.`
  );
}

/** What each delivery method means for THIS shop, using its own contact details. */
export function deliveryDescriptions(
  config: StorefrontConfig,
): Record<string, string> {
  const { address, city, phone } = config.contact;
  const where = [address, city].filter(Boolean).join(", ");
  const pickup = where
    ? `Podrás recoger tu pedido en ${where}.`
    : "Podrás recoger tu pedido en nuestra tienda.";
  const contactLine = phone ? ` Te contactaremos al ${phone} para coordinar.` : "";

  return {
    pickup_store: pickup + contactLine,
    delivery_arequipa:
      `Realizamos delivery${city ? ` dentro de ${city}` : " local"}. ` +
      `Nos comunicaremos contigo para coordinar la entrega.`,
    national_shipping:
      "Realizamos envíos a nivel nacional. Confirmaremos los datos de envío " +
      "antes del despacho.",
  };
}
