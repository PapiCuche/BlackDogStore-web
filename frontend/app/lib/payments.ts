/**
 * The payment gateway, as the browser sees it.
 *
 * TWO RULES LIVE HERE, AND THEY ARE THE REASON THIS FILE EXISTS.
 *
 * 1. THE SDK URL IS A CONSTANT, NEVER DATA. The backend tells us WHICH
 *    environment ("sandbox" or "production"); it does not tell us what to
 *    load. If a URL arrived in a response and we appended it to the document,
 *    then anything that could shape that response — a compromised backend, a
 *    proxy, a mistaken deploy — could run its own script on the checkout page,
 *    which is the one page where card data is being typed.
 *
 * 2. THE CALLBACK IS NOT A RECEIPT. The SDK hands the browser a result object,
 *    and the browser is the least trustworthy witness available: the buyer can
 *    edit it, replay it, or invent it from the console. It is used to decide
 *    which screen to show next, never to conclude that money arrived. That
 *    answer comes from our backend, which learns it from a signed notification.
 */

export const IZIPAY_SDK_URLS = {
  sandbox: "https://sandbox-checkout.izipay.pe/payments/v1/js/index.js",
  production: "https://checkout.izipay.pe/payments/v1/js/index.js",
} as const;

export type PaymentEnvironment = keyof typeof IZIPAY_SDK_URLS;

export function isPaymentEnvironment(value: unknown): value is PaymentEnvironment {
  return value === "sandbox" || value === "production";
}

/** What `POST /payments/create-checkout-session/` answers with. */
export type PaymentSession = {
  order_id: number;
  provider: string;
  environment: PaymentEnvironment;
  transaction_id: string;
  authorization: string;
  merchant_code: string;
  public_key: string;
  config: Record<string, unknown>;
};

/**
 * Resolve the script to load.
 *
 * Returns null for anything unrecognised rather than falling back to
 * production. A checkout that does not open is a visible failure; one that
 * silently opened the wrong environment is a payment nobody can find.
 */
export function sdkUrlFor(environment: string): string | null {
  return isPaymentEnvironment(environment) ? IZIPAY_SDK_URLS[environment] : null;
}

type IzipayConstructor = new (args: { config: Record<string, unknown> }) => {
  LoadForm: (args: {
    authorization: string;
    keyRSA: string;
    callbackResponse: (response: unknown) => void;
  }) => void;
};

declare global {
  interface Window {
    Izipay?: IzipayConstructor;
  }
}

/**
 * Draw the gateway's own payment form.
 *
 * NO FIELD FOR A CARD NUMBER EXISTS IN THIS APPLICATION. The SDK renders and
 * owns those inputs, so the PAN, the CVV and the expiry never enter our DOM,
 * our state or our backend — which is the entire reason for using it rather
 * than posting card data ourselves.
 */
export function openPaymentForm(
  session: PaymentSession,
  onSettled: (response: unknown) => void,
): void {
  const Izipay = window.Izipay;
  if (!Izipay) throw new Error("El formulario de pago no se pudo cargar.");

  const checkout = new Izipay({ config: session.config });
  checkout.LoadForm({
    authorization: session.authorization,
    keyRSA: session.public_key,
    callbackResponse: onSettled,
  });
}
