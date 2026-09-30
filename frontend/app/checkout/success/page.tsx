"use client";

import { useEffect, useState } from "react";
import { useStorefront } from "../../components/StorefrontProvider";
import Link from "next/link";
import { API_BASE } from "../../lib/api";

type PaymentStatus = "pending_payment" | "paid" | "failed" | "cancelled" | "expired" | "refunded";

type StatusData = {
  order_id: number;
  status: PaymentStatus;
  paid: boolean;
  total: string;
  message: string;
};

/**
 * "Gracias por tu compra" is a claim about money, and this page is not entitled
 * to make it on its own.
 *
 * The buyer arrives here straight from the gateway's form, carrying a
 * reference in the URL — nothing more. The reference proves which attempt was
 * made; it proves nothing about whether it was paid. So the page opens saying
 * "Verificando pago", asks our backend, and only repeats what the backend says.
 * The backend, in turn, will not say `paid` until a notification signed with a
 * key the browser has never seen has been verified server-side.
 *
 * The polling exists because the two events race: the buyer's redirect and the
 * gateway's server-to-server notification are independent, and the redirect
 * usually wins.
 */
export default function CheckoutSuccessPage() {
  // Phase 3: the tenant's own WhatsApp, not a compiled-in number.
  const whatsappLink = useStorefront().contact.whatsapp_link;
  const [reference] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("reference");
  });
  const [statusData, setStatusData] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(
    () => (typeof window !== "undefined" && !new URLSearchParams(window.location.search).get("reference")
      ? "No se recibió la referencia del pago."
      : null),
  );
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (!reference) {
      setLoading(false);
      return;
    }

    async function checkStatus() {
      try {
        const res = await fetch(
          `${API_BASE}/payments/status/?reference=${encodeURIComponent(reference as string)}`,
          { credentials: "include" },
        );
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          setError(body?.detail || "No se pudo verificar el estado del pago.");
          setLoading(false);
          return;
        }
        const data: StatusData = await res.json();
        setStatusData(data);

        // Still pending: the gateway's notification may simply not have
        // arrived yet. Poll a few times before showing the pending screen.
        if (data.status === "pending_payment" && retryCount < 5) {
          setTimeout(() => setRetryCount((n) => n + 1), 2000);
        } else {
          setLoading(false);
        }
      } catch {
        setError("Error de red al verificar el pago.");
        setLoading(false);
      }
    }

    checkStatus();
  }, [reference, retryCount]);

  const failureMessages: Record<string, string> = {
    failed: "El pago no pudo procesarse.",
    expired: "La sesión de pago expiró.",
    cancelled: "La orden fue cancelada.",
    refunded: "El pago fue reembolsado.",
  };

  const shell = "flex min-h-screen items-center justify-center bg-background px-6 py-14 text-foreground";
  const primaryAction =
    "inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-6 py-3 text-sm font-bold text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";
  const secondaryAction =
    "inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border bg-surface px-6 py-3 text-sm font-bold text-foreground transition hover:border-accent/50 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

  if (loading) {
    return (
      <div className={shell}>
        <div className="w-full max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-xl border border-bd-border bg-surface">
            <svg className="h-6 w-6 animate-spin text-muted-foreground" fill="none" viewBox="0 0 24 24" aria-hidden="true">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
          <span className="section-label">Pago</span>
          <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
            Verificando el estado.
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            La confirmación se obtiene desde el backend; este paso puede tardar unos segundos.
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={shell}>
        <div className="w-full max-w-lg rounded-2xl border border-bd-border bg-surface p-8 text-center sm:p-10">
          <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-xl border border-red-500/30 bg-red-500/10 text-red-300" aria-hidden="true">
            <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <span className="section-label">Verificación</span>
          <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
            No pudimos verificar el pago.
          </h1>
          <p className="mt-4 text-sm leading-6 text-muted-foreground">{error}</p>
          <Link href="/checkout" className={primaryAction + " mt-8"}>
            Volver al checkout
          </Link>
        </div>
      </div>
    );
  }

  if (statusData?.status === "paid") {
    return (
      <div className={shell}>
        <div className="w-full max-w-xl rounded-2xl border border-bd-border bg-surface p-8 text-center sm:p-10">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-xl border border-bd-border bg-background text-foreground" aria-hidden="true">
            <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>

          <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-accent">Confirmado</span>
          <h1 className="mt-2 font-display text-4xl font-black uppercase text-foreground">
            Pago confirmado.
          </h1>
          <p className="mt-3 text-sm text-muted-foreground">
            Pedido #{statusData.order_id}
          </p>
          <p className="mt-1 font-display text-2xl font-black text-foreground">
            S/ {Number(statusData.total).toFixed(2)}
          </p>

          <div className="mt-8 rounded-xl border border-bd-border bg-background p-5 text-left">
            <p className="text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground">
              Siguiente paso
            </p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              El pedido ya figura como pagado. Puedes revisar su información desde “Mis pedidos”
              y usar los canales publicados por la tienda si necesitas hacer una consulta.
            </p>
          </div>

          <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:justify-center">
            <Link href="/orders" className={primaryAction}>
              Ver mis pedidos
            </Link>
            <Link href="/product" className={secondaryAction}>
              Seguir comprando
            </Link>
            {whatsappLink ? (
              <a href={whatsappLink} target="_blank" rel="noopener noreferrer" className={secondaryAction}>
                WhatsApp
              </a>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (statusData?.status === "pending_payment") {
    return (
      <div className={shell}>
        <div className="w-full max-w-lg rounded-2xl border border-bd-border bg-surface p-8 text-center sm:p-10">
          <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-xl border border-amber-400/30 bg-amber-400/10 text-amber-300" aria-hidden="true">
            <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <span className="section-label">Pendiente</span>
          <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
            Seguimos verificando.
          </h1>
          <p className="mt-4 text-sm leading-6 text-muted-foreground">
            El backend todavía no confirmó el resultado final. Si ya completaste el formulario de pago,
            puedes volver a consultar el estado.
          </p>
          <button
            type="button"
            onClick={() => { setLoading(true); setRetryCount(0); }}
            className={primaryAction + " mt-8"}
          >
            Verificar de nuevo
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={shell}>
      <div className="w-full max-w-lg rounded-2xl border border-bd-border bg-surface p-8 text-center sm:p-10">
        <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-xl border border-red-500/30 bg-red-500/10 text-red-300" aria-hidden="true">
          <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </div>
        <span className="section-label">Pago</span>
        <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
          Pago no completado.
        </h1>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          {statusData ? failureMessages[statusData.status] ?? statusData.message : "Estado desconocido."}
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:justify-center">
          <Link href="/checkout" className={primaryAction}>
            Intentar de nuevo
          </Link>
          <Link href="/cart" className={secondaryAction}>
            Ver carrito
          </Link>
        </div>
      </div>
    </div>
  );
}
