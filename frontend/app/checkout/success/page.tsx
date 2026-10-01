"use client";

import { useEffect, useState } from "react";
import { useStorefront } from "../../components/StorefrontProvider";
import Link from "next/link";
import { API_BASE } from "../../lib/api";

type PaymentStatus =
  | "pending_payment"
  | "paid"
  | "failed"
  | "cancelled"
  | "expired"
  | "refunded";

type StatusData = {
  order_id: number;
  status: PaymentStatus;
  paid: boolean;
  total: string;
  message: string;
};

function StatusShell({
  eyebrow,
  title,
  description,
  tone = "neutral",
  children,
}: {
  eyebrow: string;
  title: string;
  description: React.ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger";
  children?: React.ReactNode;
}) {
  const indicator = {
    neutral: "border-bd-border bg-surface text-muted",
    success: "border-success-border bg-success-surface text-success",
    warning: "border-warning-border bg-warning-surface text-warning",
    danger: "border-danger-border bg-danger-surface text-danger",
  }[tone];

  return (
    <div className="flex min-h-[70vh] items-center justify-center bg-background px-6 py-12">
      <div className="w-full max-w-xl">
        <div className="rounded-[1.75rem] border border-bd-border bg-surface p-7 sm:p-10">
          <div className={`mb-7 inline-flex rounded-xl border px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] ${indicator}`}>
            {eyebrow}
          </div>
          <h1 className="font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground sm:text-5xl">
            {title}
          </h1>
          <div className="mt-5 text-sm leading-7 text-muted">{description}</div>
          {children ? <div className="mt-8">{children}</div> : null}
        </div>
      </div>
    </div>
  );
}

/**
 * The browser never decides whether money was paid.
 * The reference identifies an attempt; the backend is the authority for status.
 */
export default function CheckoutSuccessPage() {
  const whatsappLink = useStorefront().contact.whatsapp_link;
  const [reference] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("reference");
  });
  const [statusData, setStatusData] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(() =>
    typeof window !== "undefined" &&
    !new URLSearchParams(window.location.search).get("reference")
      ? "No se recibió la referencia del pago."
      : null,
  );
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (!reference) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function checkStatus() {
      try {
        const res = await fetch(
          `${API_BASE}/payments/status/?reference=${encodeURIComponent(reference as string)}`,
          { credentials: "include" },
        );
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          if (!cancelled) {
            setError(body?.detail || "No se pudo verificar el estado del pago.");
            setLoading(false);
          }
          return;
        }

        const data: StatusData = await res.json();
        if (cancelled) return;
        setStatusData(data);

        if (data.status === "pending_payment" && retryCount < 5) {
          timer = setTimeout(() => setRetryCount((count) => count + 1), 2000);
        } else {
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError("Error de red al verificar el pago.");
          setLoading(false);
        }
      }
    }

    void checkStatus();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [reference, retryCount]);

  if (loading) {
    return (
      <StatusShell
        eyebrow="Verificación"
        title="Confirmando el estado del pago"
        description="Estamos consultando al backend antes de mostrar un resultado. Esta pantalla no asume que el pago fue aprobado por el hecho de volver desde la pasarela."
      >
        <div className="flex items-center gap-3 rounded-xl border border-bd-border bg-background px-4 py-3 text-sm text-muted" role="status">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden="true" />
          Verificando…
        </div>
      </StatusShell>
    );
  }

  if (error) {
    return (
      <StatusShell eyebrow="No verificado" title="No pudimos confirmar el pago" description={error} tone="danger">
        <Link href="/checkout" className="inline-flex rounded-xl bg-primary px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
          Volver al checkout
        </Link>
      </StatusShell>
    );
  }

  if (statusData?.status === "paid") {
    return (
      <StatusShell
        eyebrow="Pago confirmado"
        title="Tu pago figura como aprobado"
        description={
          <>
            <p>La orden <strong className="text-foreground">#{statusData.order_id}</strong> está registrada como pagada en el backend.</p>
            <p className="mt-2">Total confirmado: <strong className="tabular-nums text-foreground">S/ {Number(statusData.total).toFixed(2)}</strong></p>
          </>
        }
        tone="success"
      >
        <div className="flex flex-col gap-3 sm:flex-row">
          <Link href="/orders" className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
            Ver mis pedidos
          </Link>
          <Link href="/product" className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
            Seguir comprando
          </Link>
          {whatsappLink ? (
            <a href={whatsappLink} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-12 items-center justify-center rounded-xl px-4 py-3 text-xs font-semibold text-muted transition hover:text-foreground">
              Consultar por WhatsApp ↗
            </a>
          ) : null}
        </div>
      </StatusShell>
    );
  }

  if (statusData?.status === "pending_payment") {
    return (
      <StatusShell
        eyebrow="Pendiente"
        title="El pago todavía no está confirmado"
        description="La referencia existe, pero el backend aún no registra el pago como aprobado. Puedes volver a verificar sin crear otra afirmación de pago."
        tone="warning"
      >
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            setRetryCount(0);
          }}
          className="rounded-xl bg-primary px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
        >
          Verificar de nuevo
        </button>
      </StatusShell>
    );
  }

  const failureMessages: Record<string, string> = {
    failed: "El pago no pudo procesarse.",
    expired: "La sesión de pago expiró.",
    cancelled: "La orden fue cancelada.",
    refunded: "El pago figura como reembolsado.",
  };

  return (
    <StatusShell
      eyebrow="Pago no completado"
      title="La operación no figura como pagada"
      description={
        statusData
          ? failureMessages[statusData.status] ?? statusData.message
          : "No pudimos determinar el estado de la operación."
      }
      tone="danger"
    >
      <div className="flex flex-col gap-3 sm:flex-row">
        <Link href="/checkout" className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
          Intentar de nuevo
        </Link>
        <Link href="/cart" className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
          Ver carrito
        </Link>
      </div>
    </StatusShell>
  );
}
