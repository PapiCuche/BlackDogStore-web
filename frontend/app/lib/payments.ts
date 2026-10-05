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

/**
 * IZIPAY SELLS TWO PRODUCTS, and an installation holds credentials for one:
 *
 *   izipay        «SDK web / Checkout»        — `Izipay({config}).LoadForm(...)`
 *   micuentaweb   «Mi Cuenta Web», REST V4    — the Krypton client and a formToken
 *
 * The backend says which one opened the payment. The browser draws THAT form
 * and no other: the two are never loaded together, and neither is chosen here.
 */
export type IzipaySession = {
  order_id: number;
  provider: "izipay";
  environment: PaymentEnvironment;
  transaction_id: string;
  authorization: string;
  merchant_code: string;
  public_key: string;
  config: Record<string, unknown>;
};

export type MiCuentaWebSession = {
  order_id: number;
  provider: "micuentaweb";
  /** Decided by the shop's keys, not by a setting: TEST keys or production keys. */
  environment: "test" | "production";
  transaction_id: string;
  form_token: string;
  public_key: string;
};

/** What `POST /payments/create-checkout-session/` answers with. */
export type PaymentSession = IzipaySession | MiCuentaWebSession;

function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/**
 * Whether a response is a payment this page knows how to open.
 *
 * Anything else opens NOTHING. An unknown provider, an environment that is not
 * that provider's, or a missing token is a checkout that says so — not one
 * that falls back to whichever form happens to be available.
 */
export function isPaymentSession(value: unknown): value is PaymentSession {
  if (typeof value !== "object" || value === null) return false;
  const data = value as Record<string, unknown>;
  if (!text(data.transaction_id) || !text(data.public_key)) return false;
  if (data.provider === "izipay") {
    return isPaymentEnvironment(data.environment) && text(data.authorization)
      && typeof data.config === "object" && data.config !== null;
  }
  if (data.provider === "micuentaweb") {
    return (data.environment === "test" || data.environment === "production")
      && text(data.form_token);
  }
  return false;
}

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

/** The part of the Krypton client this page uses. */
type KryptonClient = {
  onSubmit: (callback: (answer: unknown) => boolean) => unknown;
  setFormConfig?: (config: Record<string, string>) => unknown;
  renderElements?: () => unknown;
  removeForms?: () => unknown;
};

declare global {
  interface Window {
    Izipay?: IzipayConstructor;
    KR?: KryptonClient;
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
  session: IzipaySession,
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

/**
 * «Mi Cuenta Web»: the three official addresses of the Krypton client.
 *
 * Constants for the same reason as the SDK URLs above. Test and production use
 * the SAME script — the shop's public key decides the mode — so there is no
 * environment to pick here at all.
 */
export const KRYPTON_ASSETS = {
  script: "https://static.micuentaweb.pe/static/js/krypton-client/V4.0/stable/kr-payment-form.min.js",
  theme: "https://static.micuentaweb.pe/static/js/krypton-client/V4.0/ext/neon.js",
  stylesheet: "https://static.micuentaweb.pe/static/js/krypton-client/V4.0/ext/neon-reset.min.css",
} as const;

const LOAD_ERROR = "No se pudo cargar el formulario de pago.";

function loadScript(src: string, attributes: Record<string, string> = {}): Promise<void> {
  return new Promise((resolve, reject) => {
    let script = Array.from(document.head.querySelectorAll("script")).find(
      (candidate) => candidate.getAttribute("src") === src,
    );
    if (script?.dataset.loaded === "true") {
      resolve();
      return;
    }
    if (!script) {
      script = document.createElement("script");
      for (const [name, value] of Object.entries(attributes)) script.setAttribute(name, value);
      script.src = src;
      document.head.appendChild(script);
    }
    const element = script;
    element.addEventListener("load", () => {
      element.dataset.loaded = "true";
      resolve();
    });
    element.addEventListener("error", () => {
      // Removed so that the next attempt asks again instead of waiting on a
      // script that will never fire another event.
      element.remove();
      reject(new Error(LOAD_ERROR));
    });
  });
}

/**
 * Draw the gateway's form inside the `.kr-smart-form` element already on the
 * page, and say what to do when it finishes.
 *
 * THE CONTAINER COMES FIRST. The Krypton client looks for it when it loads, so
 * the element (with its `kr-form-token`) must be in the document before this
 * is called. The card fields are the gateway's, inside its own frames.
 *
 * `onSettled` IS CALLED WITH NOTHING. The client hands this callback the
 * gateway's answer and a hash; none of it is read. A browser's copy of
 * "PAID" is a hint to change screens, and the callback returns `false` so the
 * client does not post that copy anywhere either. Whether the order is paid is
 * asked of our backend, which hears it from the gateway's server.
 */
export async function mountKryptonForm(
  session: MiCuentaWebSession,
  onSettled: () => void,
): Promise<void> {
  if (window.KR) {
    // A second payment in the same tab: the client is already here.
    await window.KR.setFormConfig?.({ formToken: session.form_token });
    await window.KR.renderElements?.();
  } else {
    if (!document.head.querySelector(`link[href="${KRYPTON_ASSETS.stylesheet}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = KRYPTON_ASSETS.stylesheet;
      document.head.appendChild(link);
    }
    await loadScript(KRYPTON_ASSETS.script, {
      "kr-public-key": session.public_key,
      "kr-language": "es-PE",
    });
    await loadScript(KRYPTON_ASSETS.theme);
  }

  const client = window.KR;
  if (!client) throw new Error(LOAD_ERROR);
  await client.onSubmit(() => {
    onSettled();
    return false;
  });
}
