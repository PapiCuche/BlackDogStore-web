"use client";

import { useEffect, useState } from "react";
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

export default function CheckoutSuccessPage() {
  const [sessionId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("session_id");
  });
  const [statusData, setStatusData] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(
    () => (typeof window !== "undefined" && !new URLSearchParams(window.location.search).get("session_id")
      ? "No se recibió la referencia del pago."
      : null),
  );
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (!sessionId) {
      setLoading(false);
      return;
    }

    async function checkStatus() {
      try {
        const res = await fetch(
          `${API_BASE}/payments/status/?session_id=${encodeURIComponent(sessionId as string)}`,
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

        // If still pending, poll up to 5 more times (Stripe webhook may be in flight)
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
  }, [sessionId, retryCount]);

  // Loading / polling state
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-6">
        <div className="text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-white/10 bg-white/5">
            <svg
              className="h-6 w-6 animate-spin text-zinc-400"
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
          <p className="text-sm text-zinc-400">Verificando pago con Stripe...</p>
        </div>
      </div>
    );
  }

  // Generic error or missing session_id
  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-6">
        <div className="mx-auto max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-red-500/30 bg-red-500/10">
            <svg className="h-8 w-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <h1 className="mb-2 text-3xl font-bold text-white">Error</h1>
          <p className="mb-8 text-zinc-400">{error}</p>
          <Link
            href="/checkout"
            className="rounded-full bg-white px-8 py-3 text-sm font-semibold text-zinc-900 transition hover:bg-zinc-100"
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
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-6">
        <div className="mx-auto max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full border border-white/20 bg-white/5">
            <svg className="h-10 w-10 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>

          <h1 className="mb-2 text-4xl font-bold text-white">¡Pago confirmado!</h1>
          <p className="mb-1 text-zinc-400">Tu orden #{statusData.order_id} ha sido registrada.</p>
          <p className="mb-8 text-zinc-400">
            Total pagado:{" "}
            <span className="font-semibold text-white">
              S/ {Number(statusData.total).toFixed(2)}
            </span>
          </p>

          <div className="mb-8 rounded-2xl border border-white/10 bg-white/5 p-6 text-left text-sm text-zinc-400">
            <p className="mb-2 font-medium text-white">¿Qué sigue?</p>
            <ul className="space-y-1 list-disc list-inside">
              <li>Recibirás la confirmación de tu pedido al correo registrado.</li>
              <li>Nuestro equipo se comunicará contigo para coordinar la entrega.</li>
              <li>Para consultas inmediatas escríbenos por WhatsApp.</li>
            </ul>
          </div>

          <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            <Link
              href="/product"
              className="rounded-full bg-white px-8 py-3 text-sm font-semibold text-zinc-900 transition hover:bg-zinc-100"
            >
              Seguir comprando
            </Link>
            <a
              href="https://wa.me/51936449536"
              className="rounded-full border border-white/20 px-8 py-3 text-sm font-semibold text-white transition hover:border-white/40"
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
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-6">
        <div className="mx-auto max-w-lg text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-yellow-500/30 bg-yellow-500/10">
            <svg className="h-8 w-8 text-yellow-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <h1 className="mb-2 text-3xl font-bold text-white">Verificando pago</h1>
          <p className="mb-8 text-zinc-400">
            Tu pago está siendo procesado. Si ya completaste el pago en Stripe,
            espera unos segundos y recarga la página.
          </p>
          <button
            onClick={() => { setLoading(true); setRetryCount(0); }}
            className="rounded-full bg-white px-8 py-3 text-sm font-semibold text-zinc-900 transition hover:bg-zinc-100"
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
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-6">
      <div className="mx-auto max-w-lg text-center">
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full border border-red-500/30 bg-red-500/10">
          <svg className="h-8 w-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </div>
        <h1 className="mb-2 text-3xl font-bold text-white">Pago no completado</h1>
        <p className="mb-8 text-zinc-400">
          {statusData ? failureMessages[statusData.status] ?? statusData.message : "Estado desconocido."}
        </p>
        <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <Link
            href="/checkout"
            className="rounded-full bg-white px-8 py-3 text-sm font-semibold text-zinc-900 transition hover:bg-zinc-100"
          >
            Intentar de nuevo
          </Link>
          <Link
            href="/cart"
            className="rounded-full border border-white/20 px-8 py-3 text-sm font-semibold text-white transition hover:border-white/40"
          >
            Ver carrito
          </Link>
        </div>
      </div>
    </div>
  );
}
