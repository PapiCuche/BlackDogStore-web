"use client";

import { useEffect, useReducer, useState } from "react";
import Link from "next/link";
import Script from "next/script";
import { useRouter } from "next/navigation";
import { getSessionKey } from "../lib/cart";
import { API_BASE } from "../lib/api";
import { fetchWithAuth, getCurrentUser } from "../lib/auth";
import {
  deliveryDescriptions,
  termsText,
  warrantyText,
} from "../lib/business";
import { useStorefront } from "../components/StorefrontProvider";
import {
  openPaymentForm,
  sdkUrlFor,
  type PaymentSession,
} from "../lib/payments";

type Coupon = { code: string; discount_percent: number };
type FieldErrors = Record<string, string>;

type FormState = {
  customer_name: string;
  customer_email: string;
  customer_phone: string;
  document_type: string;
  document_number: string;
  delivery_method: string;
  address_line: string;
  city: string;
  district: string;
  reference: string;
  notes: string;
  receipt_type: string;
  accepted_terms: boolean;
  accepted_warranty_policy: boolean;
};

type FormAction =
  | { type: "set_str"; field: keyof FormState; value: string }
  | { type: "set_bool"; field: keyof FormState; value: boolean }
  | { type: "prefill"; name: string; email: string };

function formReducer(state: FormState, action: FormAction): FormState {
  switch (action.type) {
    case "set_str":
      return { ...state, [action.field]: action.value };
    case "set_bool":
      return { ...state, [action.field]: action.value };
    case "prefill":
      return {
        ...state,
        customer_name: action.name || state.customer_name,
        customer_email: action.email || state.customer_email,
      };
    default:
      return state;
  }
}

const initialForm: FormState = {
  customer_name: "",
  customer_email: "",
  customer_phone: "",
  document_type: "dni",
  document_number: "",
  delivery_method: "pickup_store",
  address_line: "",
  city: "",
  district: "",
  reference: "",
  notes: "",
  receipt_type: "boleta",
  accepted_terms: false,
  accepted_warranty_policy: false,
};

function FieldError({ msg, id }: { msg?: string; id?: string }) {
  if (!msg) return null;
  return <p id={id} role="alert" className="mt-1 text-xs text-red-400">{msg}</p>;
}

export default function CheckoutPage() {
  // Phase 3: the consent texts and the pickup point name THIS shop, resolved
  // from the request host. A customer agreeing to share their data has to be
  // told which company they are sharing it with.
  const storefront = useStorefront();
  const terms = termsText(storefront);
  const warranty = warrantyText(storefront);
  const deliveryCopy = deliveryDescriptions(storefront);
  const [form, dispatch] = useReducer(formReducer, initialForm);
  const [loading, setLoading] = useState(false);
  // Set once the backend has opened a payment attempt. Its presence is what
  // causes the official SDK to be loaded — never a URL from the response.
  const [payment, setPayment] = useState<PaymentSession | null>(null);
  const router = useRouter();
  const [message, setMessage] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [cancelled, setCancelled] = useState(false);
  const [coupon, setCoupon] = useState<Coupon | null>(null);

  const sessionKey = getSessionKey();

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    setCancelled(params.get("cancelled") === "true");
    const saved = sessionStorage.getItem("blackdog_coupon");
    if (saved) {
      try { setCoupon(JSON.parse(saved)); } catch { /* ignore */ }
    }
    getCurrentUser()
      .then((profile) => {
        if (profile) {
          dispatch({ type: "prefill", name: profile.first_name || "", email: profile.email || "" });
        }
      })
      .catch(() => {});
  }, []);

  // factura forces document_type = ruc
  useEffect(() => {
    if (form.receipt_type === "factura" && form.document_type !== "ruc") {
      dispatch({ type: "set_str", field: "document_type", value: "ruc" });
    }
  }, [form.receipt_type, form.document_type]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setMessage(null);
    setFieldErrors({});

    const body = {
      session_key: sessionKey,
      customer_name: form.customer_name,
      customer_email: form.customer_email,
      customer_phone: form.customer_phone,
      document_type: form.document_type,
      document_number: form.document_number,
      delivery_method: form.delivery_method,
      address_line: form.address_line,
      city: form.city,
      district: form.district,
      reference: form.reference,
      notes: form.notes,
      receipt_type: form.receipt_type,
      accepted_terms: form.accepted_terms,
      accepted_warranty_policy: form.accepted_warranty_policy,
      ...(coupon ? { coupon_code: coupon.code } : {}),
    };

    try {
      const res = await fetchWithAuth(
        `${API_BASE}/payments/create-checkout-session/`,
        { method: "POST", body: JSON.stringify(body) },
      );

      if (res.status === 400) {
        const err = await res.json().catch(() => null);
        if (err && typeof err === "object" && !("detail" in err)) {
          const fields: FieldErrors = {};
          for (const [k, v] of Object.entries(err)) {
            fields[k] = Array.isArray(v) ? (v as string[]).join(" ") : String(v);
          }
          setFieldErrors(fields);
          setLoading(false);
          return;
        }
        throw new Error((err as { detail?: string })?.detail ?? "Datos inválidos.");
      }

      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error((err as { detail?: string })?.detail ?? "No se pudo crear la sesión de pago.");
      }

      // A pending order now exists and the gateway has issued a session token
      // for ONE attempt against it. Nothing has been charged: this only lets
      // the SDK draw its form.
      const data = (await res.json()) as PaymentSession;
      if (!sdkUrlFor(data.environment)) {
        throw new Error("Entorno de pago no reconocido.");
      }
      sessionStorage.removeItem("blackdog_coupon");
      setPayment(data);
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : "Error al iniciar el pago.");
      setLoading(false);
    }
  }

  /**
   * The gateway's form has closed.
   *
   * WHATEVER IT SAID, THIS DOES NOT MEAN PAID. The result object lives in the
   * buyer's browser and can be replayed or fabricated; treating a `code: "00"`
   * here as confirmation would let anyone with a console mark their own order
   * paid. So the page goes to the success screen carrying only the reference,
   * and that screen asks our backend — which will not say "paid" until a
   * signed notification has been verified server-side.
   */
  function handlePaymentSettled() {
    if (!payment) return;
    router.push(
      `/checkout/success?reference=${encodeURIComponent(payment.transaction_id)}`,
    );
  }

  const fe = fieldErrors;
  const needsAddress =
    form.delivery_method === "delivery_arequipa" ||
    form.delivery_method === "national_shipping";
  const needsCity = form.delivery_method === "national_shipping";
  const localDeliveryLabel = storefront.contact.city
    ? `Delivery ${storefront.contact.city}`
    : "Delivery local";

  const inputClass =
    "mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3.5 py-3 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none";
  const labelClass = "block text-xs font-bold text-muted";
  const sectionClass =
    "rounded-2xl border border-bd-border bg-surface p-5 space-y-4 sm:p-6";

  return (
    <div className="min-h-screen bg-background px-4 py-10 sm:py-12">
      {/* Loaded ONLY from the constant map in lib/payments, and only once the
          backend has said which environment. The response never supplies a
          script address. */}
      {payment && (
        <Script
          src={sdkUrlFor(payment.environment) as string}
          strategy="afterInteractive"
          onLoad={() => {
            try {
              openPaymentForm(payment, handlePaymentSettled);
            } catch (e: unknown) {
              setMessage(
                e instanceof Error ? e.message : "No se pudo abrir el pago.",
              );
              setLoading(false);
            }
          }}
          onError={() => {
            setMessage("No se pudo cargar el formulario de pago.");
            setLoading(false);
          }}
        />
      )}
      <div className="mx-auto max-w-2xl">

        <div className="mb-8 border-b border-bd-border pb-7">
          <span className="section-label">Compra</span>
          <h1 className="mt-2 font-display text-4xl font-black italic uppercase tracking-[-0.04em] text-foreground sm:text-5xl">
            Checkout
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted">
            Completa tus datos de entrega y comprobante antes de abrir el formulario de pago.
          </p>
        </div>

        {cancelled && (
          <div className="mb-5 rounded-xl border border-bd-border bg-surface p-4 text-sm text-foreground">
            Pago cancelado. Tu carrito sigue disponible.
          </div>
        )}
        {message && (
          <div className="mb-5 rounded-xl border border-red-500/20 bg-red-500/[0.06] p-4 text-sm text-red-300">
            {message}
          </div>
        )}
        {coupon && (
          <div className="mb-5 flex items-center justify-between rounded-xl border border-bd-border bg-surface px-4 py-3">
            <div>
              <span className="text-xs text-muted">Cupón aplicado</span>
              <div className="text-sm font-semibold text-foreground">
                {coupon.code} — {coupon.discount_percent}% de descuento
              </div>
            </div>
            <Link href="/cart" className="text-xs text-muted hover:text-foreground">
              Cambiar
            </Link>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">

          {/* 1. Datos personales */}
          <div className={sectionClass}>
            <h2 className="text-sm font-semibold text-foreground">Datos personales</h2>

            <div>
              <label htmlFor="checkout-customer-name" className={labelClass}>Nombre completo *</label>
              <input
                id="checkout-customer-name"
                name="customer_name"
                autoComplete="name"
                aria-invalid={Boolean(fe.customer_name)}
                aria-describedby={fe.customer_name ? "checkout-customer-name-error" : undefined}
                value={form.customer_name}
                onChange={(e) => dispatch({ type: "set_str", field: "customer_name", value: e.target.value })}
                className={inputClass}
                placeholder="Juan Pérez"
                required
                maxLength={255}
              />
              <FieldError id="checkout-customer-name-error" msg={fe.customer_name} />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="checkout-customer-email" className={labelClass}>Correo electrónico *</label>
                <input
                  id="checkout-customer-email"
                  name="customer_email"
                  type="email"
                  autoComplete="email"
                  aria-invalid={Boolean(fe.customer_email)}
                  aria-describedby={fe.customer_email ? "checkout-customer-email-error" : undefined}
                  value={form.customer_email}
                  onChange={(e) => dispatch({ type: "set_str", field: "customer_email", value: e.target.value })}
                  className={inputClass}
                  placeholder="juan@ejemplo.com"
                  required
                  maxLength={254}
                />
                <FieldError id="checkout-customer-email-error" msg={fe.customer_email} />
              </div>
              <div>
                <label htmlFor="checkout-customer-phone" className={labelClass}>Teléfono *</label>
                <input
                  id="checkout-customer-phone"
                  name="customer_phone"
                  type="tel"
                  autoComplete="tel"
                  aria-invalid={Boolean(fe.customer_phone)}
                  aria-describedby={fe.customer_phone ? "checkout-customer-phone-error" : undefined}
                  value={form.customer_phone}
                  onChange={(e) => dispatch({ type: "set_str", field: "customer_phone", value: e.target.value })}
                  className={inputClass}
                  placeholder="999 999 999"
                  required
                  maxLength={30}
                />
                <FieldError id="checkout-customer-phone-error" msg={fe.customer_phone} />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="checkout-document-type" className={labelClass}>Tipo de documento *</label>
                <select
                  id="checkout-document-type"
                  name="document_type"
                  aria-invalid={Boolean(fe.document_type)}
                  aria-describedby={fe.document_type ? "checkout-document-type-error" : undefined}
                  value={form.document_type}
                  onChange={(e) => dispatch({ type: "set_str", field: "document_type", value: e.target.value })}
                  className={inputClass}
                  disabled={form.receipt_type === "factura"}
                >
                  <option value="dni">DNI</option>
                  <option value="ce">Carnet de Extranjería</option>
                  <option value="ruc">RUC</option>
                </select>
                <FieldError id="checkout-document-type-error" msg={fe.document_type} />
              </div>
              <div>
                <label htmlFor="checkout-document-number" className={labelClass}>Número de documento *</label>
                <input
                  id="checkout-document-number"
                  name="document_number"
                  autoComplete="off"
                  aria-invalid={Boolean(fe.document_number)}
                  aria-describedby={fe.document_number ? "checkout-document-number-error" : undefined}
                  value={form.document_number}
                  onChange={(e) => dispatch({ type: "set_str", field: "document_number", value: e.target.value })}
                  className={inputClass}
                  placeholder={
                    form.document_type === "dni"
                      ? "12345678"
                      : form.document_type === "ruc"
                      ? "20123456789"
                      : "ABC123456"
                  }
                  required
                  maxLength={20}
                />
                <FieldError id="checkout-document-number-error" msg={fe.document_number} />
              </div>
            </div>
          </div>

          {/* 2. Método de entrega */}
          <div className={sectionClass}>
            <h2 className="text-sm font-semibold text-foreground">Método de entrega</h2>

            <div className="space-y-2">
              {(
                [
                  ["pickup_store", "Recojo en tienda"],
                  ["delivery_arequipa", localDeliveryLabel],
                  ["national_shipping", "Envío nacional"],
                ] as const
              ).map(([value, label]) => (
                <label
                  key={value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3.5 transition-colors ${
                    form.delivery_method === value
                      ? "border-foreground/25 bg-foreground/[0.04]"
                      : "border-bd-border hover:border-foreground/20"
                  }`}
                >
                  <input
                    type="radio"
                    name="delivery_method"
                    value={value}
                    checked={form.delivery_method === value}
                    onChange={() => dispatch({ type: "set_str", field: "delivery_method", value })}
                    className="mt-0.5 accent-current"
                  />
                  <div>
                    <p className="text-sm font-medium text-foreground">{label}</p>
                    <p className="mt-0.5 text-xs text-muted leading-relaxed">
                      {deliveryCopy[value]}
                    </p>
                  </div>
                </label>
              ))}
            </div>
            <FieldError msg={fe.delivery_method} />

            {needsAddress && (
              <div className="space-y-4 pt-1">
                <div>
                  <label htmlFor="checkout-address" className={labelClass}>Dirección *</label>
                  <input
                    id="checkout-address"
                    name="address_line"
                    autoComplete="street-address"
                    aria-invalid={Boolean(fe.address_line)}
                    aria-describedby={fe.address_line ? "checkout-address-error" : undefined}
                    value={form.address_line}
                    onChange={(e) => dispatch({ type: "set_str", field: "address_line", value: e.target.value })}
                    className={inputClass}
                    placeholder="Av. Ejemplo 123, Dpto 4B"
                    maxLength={300}
                  />
                  <FieldError id="checkout-address-error" msg={fe.address_line} />
                </div>

                {needsCity && (
                  <div>
                    <label htmlFor="checkout-city" className={labelClass}>Ciudad *</label>
                    <input
                      id="checkout-city"
                      name="city"
                      autoComplete="address-level2"
                      aria-invalid={Boolean(fe.city)}
                      aria-describedby={fe.city ? "checkout-city-error" : undefined}
                      value={form.city}
                      onChange={(e) => dispatch({ type: "set_str", field: "city", value: e.target.value })}
                      className={inputClass}
                      placeholder="Lima"
                      maxLength={100}
                    />
                    <FieldError id="checkout-city-error" msg={fe.city} />
                  </div>
                )}

                <div>
                  <label htmlFor="checkout-district" className={labelClass}>
                    {needsCity ? "Distrito / Departamento *" : "Distrito *"}
                  </label>
                  <input
                    id="checkout-district"
                    name="district"
                    autoComplete="address-level3"
                    aria-invalid={Boolean(fe.district)}
                    aria-describedby={fe.district ? "checkout-district-error" : undefined}
                    value={form.district}
                    onChange={(e) => dispatch({ type: "set_str", field: "district", value: e.target.value })}
                    className={inputClass}
                    placeholder={needsCity ? "Miraflores / Lima" : "Miraflores"}
                    maxLength={100}
                  />
                  <FieldError id="checkout-district-error" msg={fe.district} />
                </div>

                <div>
                  <label htmlFor="checkout-reference" className={labelClass}>Referencia (opcional)</label>
                  <input
                    id="checkout-reference"
                    name="reference"
                    autoComplete="off"
                    aria-invalid={Boolean(fe.reference)}
                    aria-describedby={fe.reference ? "checkout-reference-error" : undefined}
                    value={form.reference}
                    onChange={(e) => dispatch({ type: "set_str", field: "reference", value: e.target.value })}
                    className={inputClass}
                    placeholder="Frente al parque, edificio blanco"
                    maxLength={250}
                  />
                  <FieldError id="checkout-reference-error" msg={fe.reference} />
                </div>
              </div>
            )}
          </div>

          {/* 3. Tipo de comprobante */}
          <div className={sectionClass}>
            <h2 className="text-sm font-semibold text-foreground">Comprobante</h2>

            <div className="flex gap-3">
              {(["boleta", "factura"] as const).map((type) => (
                <label
                  key={type}
                  className={`flex flex-1 cursor-pointer items-center gap-2.5 rounded-lg border p-3.5 transition-colors ${
                    form.receipt_type === type
                      ? "border-foreground/25 bg-foreground/[0.04]"
                      : "border-bd-border hover:border-foreground/20"
                  }`}
                >
                  <input
                    type="radio"
                    name="receipt_type"
                    value={type}
                    checked={form.receipt_type === type}
                    onChange={() => dispatch({ type: "set_str", field: "receipt_type", value: type })}
                    className="accent-current"
                  />
                  <span className="text-sm font-medium text-foreground capitalize">{type}</span>
                </label>
              ))}
            </div>
            {form.receipt_type === "factura" && (
              <p className="text-xs text-muted">
                La factura requiere RUC. El tipo de documento se ha fijado automáticamente.
              </p>
            )}
            <FieldError msg={fe.receipt_type} />
          </div>

          {/* 4. Notas */}
          <div className={sectionClass}>
            <h2 id="checkout-notes-label" className="text-sm font-semibold text-foreground">Notas del pedido (opcional)</h2>
            <textarea
              id="checkout-notes"
              name="notes"
              aria-labelledby="checkout-notes-label"
              aria-invalid={Boolean(fe.notes)}
              aria-describedby={fe.notes ? "checkout-notes-error" : undefined}
              value={form.notes}
              onChange={(e) => dispatch({ type: "set_str", field: "notes", value: e.target.value })}
              className={`${inputClass} resize-none`}
              placeholder="Indicaciones especiales para tu pedido…"
              rows={3}
              maxLength={500}
            />
            <p className="text-xs text-muted text-right">{form.notes.length}/500</p>
            <FieldError id="checkout-notes-error" msg={fe.notes} />
          </div>

          {/* 5. Aceptaciones */}
          <div className={sectionClass}>
            <h2 className="text-sm font-semibold text-foreground">Declaraciones</h2>

            <label className="flex cursor-pointer items-start gap-3">
              <input
                id="checkout-accepted-terms"
                name="accepted_terms"
                type="checkbox"
                aria-invalid={Boolean(fe.accepted_terms)}
                aria-describedby={fe.accepted_terms ? "checkout-accepted-terms-error" : undefined}
                checked={form.accepted_terms}
                onChange={(e) =>
                  dispatch({ type: "set_bool", field: "accepted_terms", value: e.target.checked })
                }
                className="mt-0.5 h-4 w-4 accent-current"
              />
              <span className="text-xs text-muted leading-relaxed">
                {terms}
              </span>
            </label>
            <FieldError id="checkout-accepted-terms-error" msg={fe.accepted_terms} />

            <label className="flex cursor-pointer items-start gap-3">
              <input
                id="checkout-accepted-warranty"
                name="accepted_warranty_policy"
                type="checkbox"
                aria-invalid={Boolean(fe.accepted_warranty_policy)}
                aria-describedby={fe.accepted_warranty_policy ? "checkout-accepted-warranty-error" : undefined}
                checked={form.accepted_warranty_policy}
                onChange={(e) =>
                  dispatch({ type: "set_bool", field: "accepted_warranty_policy", value: e.target.checked })
                }
                className="mt-0.5 h-4 w-4 accent-current"
              />
              <span className="text-xs text-muted leading-relaxed">
                {warranty}
              </span>
            </label>
            <FieldError id="checkout-accepted-warranty-error" msg={fe.accepted_warranty_policy} />
          </div>

          {/* Punto de retiro */}
          {form.delivery_method === "pickup_store" && (
            <div className="rounded-xl border border-bd-border bg-surface p-4 text-xs text-muted leading-relaxed">
              <p className="font-medium text-foreground mb-1">Punto de retiro</p>
              {storefront.contact.address ? (
                <p>
                  {storefront.contact.address}
                  {storefront.contact.city ? `, ${storefront.contact.city}` : ""}
                </p>
              ) : (
                <p>La tienda confirmará el punto de retiro al coordinar la entrega.</p>
              )}
              {storefront.contact.phone ? (
                <p className="mt-1">Tel: {storefront.contact.phone}</p>
              ) : null}
            </div>
          )}

          <button
            type="submit"
            className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-bold uppercase tracking-[0.06em] text-background transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={loading}
          >
            {loading ? "Abriendo el pago…" : "Continuar al pago →"}
          </button>

          <div className="text-center">
            <Link
              href="/cart"
              className="text-sm text-muted hover:text-foreground transition"
            >
              ← Volver al carrito
            </Link>
          </div>
        </form>

        <p className="mt-6 text-center text-xs leading-5 text-muted">
          El resultado del pago se confirma contra el backend antes de marcar una orden como pagada.
        </p>
      </div>
    </div>
  );
}
