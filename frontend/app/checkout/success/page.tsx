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

  // Loading / polling state
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6">
        <div className="text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-bd-border bg-surface">
            <svg
              className="h-6 w-6 animate-spin text-muted"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
          </div>
          <p className="text-sm text-muted">Verificando pago...</p>
        </div>
      </div>
    );
  }

  // Generic error or missing reference
  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6">
        <div className="mx-auto max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-danger-border bg-danger-surface">
            <svg className="h-8 w-8 text-danger" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h1 className="mb-2 text-3xl font-bold text-foreground">Error</h1>
          <p className="mb-8 text-muted">{error}</p>
          <Link
            href="/checkout"
            className="rounded-full bg-foreground px-8 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            Volver al checkout
          </Link>
        </div>
      </div>
    );
  }

  // Payment confirmed
  if (statusData?.status === "paid") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6">
        <div className="mx-auto max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-bd-border bg-surface">
            <svg className="h-10 w-10 text-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>

          <h1 className="mb-2 text-4xl font-bold text-foreground">¡Pago confirmado!</h1>
          <p className="mb-1 text-muted">Tu orden #{statusData.order_id} ha sido registrada.</p>
          <p className="mb-8 text-muted">
            Total pagado:{" "}
            <span className="font-semibold text-foreground">
              S/ {Number(statusData.total).toFixed(2)}
            </span>
          </p>

          <div className="mb-8 rounded-2xl border border-bd-border bg-surface p-6 text-left text-sm text-muted">
            <p className="mb-2 font-medium text-foreground">¿Qué sigue?</p>
            <ul className="space-y-1 list-disc list-inside">
              <li>Recibirás la confirmación de tu pedido al correo registrado.</li>
              <li>Nuestro equipo se comunicará contigo para coordinar la entrega.</li>
              <li>Para consultas inmediatas escríbenos por WhatsApp.</li>
            </ul>
          </div>

          <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            <Link
              href="/product"
              className="rounded-full bg-foreground px-8 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
            >
              Seguir comprando
            </Link>
            <a
              href={whatsappLink || "#"}
              className="rounded-full border border-bd-border px-8 py-3 text-sm font-semibold text-foreground transition hover:border-bd-border"
            >
              Contactar por WhatsApp
            </a>
          </div>
        </div>
      </div>
    );
  }

  // Pending after max retries (webhook delayed)
  if (statusData?.status === "pending_payment") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6">
        <div className="mx-auto max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-warning-border bg-warning-surface">
            <svg className="h-8 w-8 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <h1 className="mb-2 text-3xl font-bold text-foreground">Verificando pago</h1>
          <p className="mb-8 text-muted">
            Tu pago está siendo procesado. Si ya completaste el pago,
            espera unos segundos y recarga la página.
          </p>
          <button
            onClick={() => { setLoading(true); setRetryCount(0); }}
            className="rounded-full bg-foreground px-8 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            Verificar de nuevo
          </button>
        </div>
      </div>
    );
  }

  // Failed, expired, cancelled, refunded
  const failureMessages: Record<string, string> = {
    failed: "El pago no pudo procesarse.",
    expired: "La sesión de pago expiró.",
    cancelled: "La orden fue cancelada.",
    refunded: "El pago fue reembolsado.",
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="mx-auto max-w-lg text-center">
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-danger-border bg-danger-surface">
          <svg className="h-8 w-8 text-danger" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </div>
        <h1 className="mb-2 text-3xl font-bold text-foreground">Pago no completado</h1>
        <p className="mb-8 text-muted">
          {statusData ? failureMessages[statusData.status] ?? statusData.message : "Estado desconocido."}
        </p>
        <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            href="/checkout"
            className="rounded-full bg-foreground px-8 py-3 text-sm font-semibold text-background transition hover:bg-foreground/90"
          >
            Intentar de nuevo
          </Link>
          <Link
            href="/cart"
            className="rounded-full border border-bd-border px-8 py-3 text-sm font-semibold text-foreground transition hover:border-bd-border"
          >
            Ver carrito
          </Link>
        </div>
      </div>
    </div>
  );
}
