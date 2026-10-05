"use client";

/**
 * Lo que ve el cliente al abrir su enlace de seguimiento.
 *
 * Dibuja lo que el servidor manda. No hay aquí un estado calculado, un total
 * sumado ni una regla sobre qué se puede responder: `can_decide` lo dice el
 * servidor, y después de responder se vuelve a leer la orden entera.
 */

import { useCallback, useEffect, useState } from "react";

import {
  decideTrackingQuote, fetchTracking, trackingEvidenceUrl, TrackingError, type Tracking,
} from "@/app/lib/tracking";

function when(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("es-PE", { dateStyle: "medium", timeStyle: "short" });
}

const PAYMENT_LABELS: Record<string, string> = {
  no_quote: "Sin importe acordado todavía",
  unpaid: "Pendiente de pago",
  partial: "Pago parcial",
  paid: "Pagado",
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function TrackingView({ token }: { token: string }) {
  const [data, setData] = useState<Tracking | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState<"approve" | "reject" | null>(null);
  const [sending, setSending] = useState(false);

  const show = useCallback((result: Tracking | unknown) => {
    if (result instanceof TrackingError && result.isNotFound) {
      setData(null);
      setMissing(true);
    } else if (result instanceof Error) {
      setError(result.message);
    } else {
      setData(result as Tracking);
      setMissing(false);
    }
  }, []);

  const load = useCallback(
    () => fetchTracking(token).then(show, show),
    [token, show],
  );

  useEffect(() => {
    let cancelled = false;
    const settle = (result: unknown) => { if (!cancelled) show(result); };
    fetchTracking(token).then(settle, settle);
    return () => { cancelled = true; };
  }, [token, show]);

  async function decide(decision: "approve" | "reject") {
    if (!data?.quote) return;
    setSending(true);
    setError(null);
    try {
      await decideTrackingQuote(token, data.quote.id, decision);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar tu respuesta.");
    } finally {
      setAsking(null);
      // Pase lo que pase, se dibuja lo que el servidor tiene ahora.
      await load();
      setSending(false);
    }
  }

  if (missing) {
    return (
      <main className="mx-auto w-full max-w-xl px-4 py-16 text-center">
        <h1 className="text-xl font-semibold text-foreground">Este enlace no está disponible</h1>
        <p className="mt-3 text-sm text-muted">
          Puede estar incompleto o haber sido reemplazado. Pide a la tienda el enlace vigente de tu orden.
        </p>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="mx-auto w-full max-w-xl px-4 py-16 text-center">
        <p className="text-sm text-muted" role="status">
          {error ?? "Consultando tu reparación…"}
        </p>
      </main>
    );
  }

  const { company, order, device, timeline, quote, payments, evidence } = data;
  const lastStep = timeline.length - 1;

  return (
    <main className="mx-auto w-full max-w-2xl space-y-4 px-4 py-8 sm:py-12">
      <header className="rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted">{company.name}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-foreground">{order.number}</h1>
        <p className="mt-3 inline-flex rounded-full border border-bd-border px-3 py-1 text-sm font-semibold text-foreground">
          {order.status_label}
        </p>
        <p className="mt-3 text-xs text-muted">Última actualización: {when(order.updated_at)}</p>
      </header>

      {error ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {quote ? (
        <Card title="Cotización">
          {quote.items.length > 0 ? (
            <ul className="divide-y divide-bd-border">
              {quote.items.map((item) => (
                <li key={item.id} className="flex items-start justify-between gap-4 py-2 text-sm">
                  <span className="text-foreground">{item.description}</span>
                  <span className="shrink-0 tabular-nums text-muted">{quote.currency} {item.line_total}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-3 flex items-center justify-between border-t border-bd-border pt-3">
            <span className="text-sm font-semibold text-foreground">Total</span>
            <span data-testid="quote-total" className="text-lg font-semibold tabular-nums text-foreground">
              {quote.currency} {quote.total}
            </span>
          </div>
          {quote.customer_notes ? <p className="mt-3 text-sm text-muted">{quote.customer_notes}</p> : null}
          {quote.valid_until && !quote.decision ? (
            <p className="mt-2 text-xs text-muted">Válida hasta {when(quote.valid_until)}.</p>
          ) : null}

          {quote.status === "approved" ? (
            <p className="mt-4 rounded-xl border border-success-border bg-success-surface px-4 py-3 text-sm text-success">
              Aprobaste esta cotización{quote.decision ? ` el ${when(quote.decision.decided_at)}` : ""}.
            </p>
          ) : null}
          {quote.status === "rejected" ? (
            <p className="mt-4 rounded-xl border border-bd-border px-4 py-3 text-sm text-muted">
              Rechazaste esta cotización. Si cambias de opinión, escribe a la tienda.
            </p>
          ) : null}
          {quote.status === "superseded" ? (
            <p className="mt-4 rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
              Esta cotización fue reemplazada: la tienda está preparando una nueva.
            </p>
          ) : null}
          {quote.status === "sent" && quote.is_expired ? (
            <p className="mt-4 rounded-xl border border-bd-border px-4 py-3 text-sm text-muted">
              Esta cotización venció. Pide a la tienda una actualizada.
            </p>
          ) : null}

          {data.can_decide ? (
            asking ? (
              <div className="mt-4 rounded-xl border border-bd-border p-4">
                <p className="text-sm text-foreground">
                  {asking === "approve"
                    ? `¿Apruebas la reparación por ${quote.currency} ${quote.total}?`
                    : "¿Rechazas esta cotización?"}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={sending}
                    onClick={() => void decide(asking)}
                    className="rounded-xl border border-primary bg-primary px-4 py-2.5 text-sm font-semibold text-background transition-opacity hover:opacity-90 disabled:opacity-40"
                  >
                    {asking === "approve" ? "Sí, aprobar" : "Sí, rechazar"}
                  </button>
                  <button
                    type="button"
                    disabled={sending}
                    onClick={() => setAsking(null)}
                    className="rounded-xl border border-bd-border px-4 py-2.5 text-sm font-semibold text-muted transition-colors hover:text-foreground disabled:opacity-40"
                  >
                    Volver
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setAsking("approve")}
                  className="rounded-xl border border-primary bg-primary px-4 py-2.5 text-sm font-semibold text-background transition-opacity hover:opacity-90"
                >
                  Aprobar cotización
                </button>
                <button
                  type="button"
                  onClick={() => setAsking("reject")}
                  className="rounded-xl border border-bd-border px-4 py-2.5 text-sm font-semibold text-muted transition-colors hover:text-foreground"
                >
                  Rechazar
                </button>
              </div>
            )
          ) : null}
        </Card>
      ) : null}

      <Card title="Avance">
        <ol aria-label="Avance de la reparación" className="space-y-3">
          {timeline.map((step, index) => (
            <li key={step.id} className="flex items-start gap-3">
              <span
                aria-hidden="true"
                className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${index === lastStep ? "bg-primary" : "bg-bd-border"}`}
              />
              <span>
                <span className={`block text-sm ${index === lastStep ? "font-semibold text-foreground" : "text-foreground"}`}>
                  {step.status_label}
                </span>
                <span className="block text-xs text-muted">{when(step.occurred_at)}</span>
              </span>
            </li>
          ))}
        </ol>
      </Card>

      <Card title="Tu equipo">
        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          {device ? (
            <>
              <div>
                <dt className="text-xs text-muted">{device.type_label}</dt>
                <dd className="text-foreground">{[device.brand, device.model].filter(Boolean).join(" ")}</dd>
              </div>
              {device.serial_number ? (
                <div>
                  <dt className="text-xs text-muted">Serie</dt>
                  <dd className="font-mono text-foreground">{device.serial_number}</dd>
                </div>
              ) : null}
              {device.imei ? (
                <div>
                  <dt className="text-xs text-muted">IMEI</dt>
                  <dd className="font-mono text-foreground">{device.imei}</dd>
                </div>
              ) : null}
            </>
          ) : null}
          <div className="sm:col-span-2">
            <dt className="text-xs text-muted">Lo que nos contaste</dt>
            <dd className="text-foreground">{order.reported_issue}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Recibido</dt>
            <dd className="text-foreground">{when(order.received_at)}</dd>
          </div>
        </dl>
      </Card>

      {payments.quoted_total !== null ? (
        <Card title="Pagos">
          <dl className="grid grid-cols-3 gap-2 text-sm">
            <div>
              <dt className="text-xs text-muted">Acordado</dt>
              <dd className="tabular-nums text-foreground">{payments.currency} {payments.quoted_total}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Pagado</dt>
              <dd className="tabular-nums text-foreground">{payments.currency} {payments.paid}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Saldo</dt>
              <dd className="tabular-nums text-foreground">
                {payments.outstanding === null ? "—" : `${payments.currency} ${payments.outstanding}`}
              </dd>
            </div>
          </dl>
          <p className="mt-2 text-xs text-muted">{PAYMENT_LABELS[payments.status] ?? ""}</p>
        </Card>
      ) : null}

      {evidence.length > 0 ? (
        <Card title="Fotos de tu equipo">
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {evidence.map((photo) => (
              <li key={photo.id}>
                <a href={trackingEvidenceUrl(token, photo.id)} target="_blank" rel="noreferrer">
                  {/* eslint-disable-next-line @next/next/no-img-element -- privada y sin optimizador: se sirve con el enlace */}
                  <img
                    src={trackingEvidenceUrl(token, photo.id)}
                    alt={photo.caption || `Foto de ${photo.stage_label.toLowerCase()}`}
                    width={photo.width ?? undefined}
                    height={photo.height ?? undefined}
                    loading="lazy"
                    className="aspect-square w-full rounded-xl border border-bd-border object-cover"
                  />
                </a>
                <p className="mt-1 text-xs text-muted">
                  {photo.stage_label}{photo.caption ? ` · ${photo.caption}` : ""}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {order.status === "delivered" && (company.warranty_policy_text || company.warranty_policy_url) ? (
        <Card title="Garantía">
          {company.warranty_policy_text ? (
            <p className="whitespace-pre-line text-sm text-foreground">{company.warranty_policy_text}</p>
          ) : null}
          {company.warranty_policy_url ? (
            <a
              href={company.warranty_policy_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-sm font-semibold text-foreground underline underline-offset-4"
            >
              Ver la política completa
            </a>
          ) : null}
        </Card>
      ) : null}

      {company.whatsapp_link ? (
        <p className="text-center text-sm text-muted">
          ¿Tienes una duda?{" "}
          <a
            href={company.whatsapp_link}
            target="_blank"
            rel="noreferrer"
            className="font-semibold text-foreground underline underline-offset-4"
          >
            Escribe a {company.name}
          </a>
        </p>
      ) : null}
    </main>
  );
}
