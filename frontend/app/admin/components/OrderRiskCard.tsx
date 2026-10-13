"use client";

// CLIENT-SEC-01 — «Revisión del pedido»: what was noticed about an online
// order when it was made, and what a person decided after looking.
//
// Everything shown is the server's: the level, the reasons in plain words and
// whether THIS caller may decide (`can_review`). The decision is advice — it
// cancels nothing, refunds nothing and changes no payment — and it is made
// once: the server refuses a second answer and says why.

import { useState } from "react";
import { OrderRisk, formatAdminDate, reviewOrderRisk } from "../../lib/admin";
import { internalButtonClass, internalInputClass } from "./internal-ui";

type Props = {
  orderId: number;
  /** Null for an order that was not assessed: there is nothing to show. */
  risk: OrderRisk | null | undefined;
  /** Given the assessment the server answered with. Never faked here. */
  onReviewed: (risk: OrderRisk) => void;
};

const NOTE_MAX = 300;

export function OrderRiskCard({ orderId, risk, onReviewed }: Props) {
  const [note, setNote] = useState("");
  const [sending, setSending] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!risk) return null;

  async function decide(decision: "approve" | "reject") {
    setSending(decision);
    setError(null);
    try {
      onReviewed(await reviewOrderRisk(orderId, decision, note.trim()));
      setNote("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar la revisión.");
    } finally {
      setSending(null);
    }
  }

  const pending = risk.review_status === "pending";
  const decided = risk.review_status === "approved" || risk.review_status === "rejected";

  return (
    <section
      data-testid="order-risk-card"
      aria-labelledby="order-risk-title"
      className={
        pending
          ? "rounded-xl border border-warning-border bg-warning-surface p-6"
          : "rounded-xl border border-bd-border bg-surface p-6"
      }
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="order-risk-title" className="text-sm font-semibold text-foreground">Revisión del pedido</h2>
        <span className="rounded border border-bd-border px-2 py-0.5 text-[11px] font-medium text-foreground">
          {risk.review_status_label}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <div>
          <dt className="mb-0.5 text-xs text-muted">Nivel de riesgo</dt>
          <dd className="text-foreground">{risk.level_label}</dd>
        </div>
        <div>
          <dt className="mb-0.5 text-xs text-muted">Evaluado el</dt>
          <dd className="text-foreground">{formatAdminDate(risk.assessed_at)}</dd>
        </div>
      </dl>

      <div className="mt-4">
        <p className="mb-1 text-xs text-muted">Motivos</p>
        {risk.signals.length > 0 ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-foreground">
            {risk.signals.map((signal) => <li key={signal.code}>{signal.label}</li>)}
          </ul>
        ) : (
          <p className="text-sm text-muted">No se observó nada fuera de lo habitual.</p>
        )}
      </div>

      {decided ? (
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-bd-border pt-4 text-sm">
          <div>
            <dt className="mb-0.5 text-xs text-muted">Revisado por</dt>
            <dd className="text-foreground">{risk.reviewed_by ?? "—"}</dd>
          </div>
          <div>
            <dt className="mb-0.5 text-xs text-muted">Revisado el</dt>
            <dd className="text-foreground">{risk.reviewed_at ? formatAdminDate(risk.reviewed_at) : "—"}</dd>
          </div>
          {risk.review_note ? (
            <div className="col-span-2">
              <dt className="mb-0.5 text-xs text-muted">Nota</dt>
              <dd className="whitespace-pre-wrap break-words text-foreground">{risk.review_note}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {pending && !risk.can_review ? (
        <p className="mt-4 text-xs text-muted">
          Espera la decisión de alguien con permiso para gestionar pedidos.
        </p>
      ) : null}

      {risk.can_review ? (
        <div className="mt-4 border-t border-warning-border pt-4">
          <p className="text-xs text-muted">
            Tu decisión queda registrada como referencia: no cancela el pedido, no devuelve el pago
            ni cambia su despacho.
          </p>
          <label className="mt-3 block text-xs text-muted">
            Nota (opcional)
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={NOTE_MAX}
              rows={2}
              className={`${internalInputClass} mt-1.5`}
            />
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => decide("approve")}
              disabled={sending !== null}
              className={internalButtonClass}
            >
              {sending === "approve" ? "Guardando…" : "Aprobar"}
            </button>
            <button
              type="button"
              onClick={() => decide("reject")}
              disabled={sending !== null}
              className={internalButtonClass}
            >
              {sending === "reject" ? "Guardando…" : "Rechazar"}
            </button>
          </div>
        </div>
      ) : null}

      {error ? <p role="alert" className="mt-3 text-xs text-danger">{error}</p> : null}
    </section>
  );
}
