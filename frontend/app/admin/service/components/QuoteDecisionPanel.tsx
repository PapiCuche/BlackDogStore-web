"use client";

/**
 * QUOTE-DECISION — what the customer answered, and the ticket that proves it.
 *
 * The customer may answer from their account or their tracking link; this panel
 * is for the third way: they told a person, and that person writes it down.
 * The record never pretends otherwise — the server stores who recorded it and
 * by which channel the answer arrived.
 *
 * THE TICKET PRINTS AFTER THE SERVER SAYS YES, never on the click. And when the
 * browser does not open the print dialog, the button stays: nobody is left
 * without the paper because a dialog did not come.
 */

import { useState } from "react";

import type { PrintOutcome } from "@/app/lib/print-pdf";
import {
  printQuoteTicket, QUOTE_DECISION_CHANNELS, recordQuoteDecision, reopenQuote,
  type ServiceQuote,
} from "@/app/lib/service-console";

import { Button, Confirm, dateTime, ErrorNote, Field } from "./ServiceUi";

const CAP_RECORD = "service.quotes.record_decision";
const CAP_QUOTE = "service.diagnostic.manage";

const PRINT_NOTE: Record<PrintOutcome, string> = {
  printed: "Ticket enviado a la impresora.",
  downloaded:
    "El diálogo de impresión no se abrió y el ticket se descargó. Ábrelo para imprimirlo, o vuelve a intentarlo.",
};

export function QuoteDecisionPanel({
  slug, orderId, quote, orderStatus, may, onChanged,
}: {
  slug: string;
  orderId: number;
  quote: ServiceQuote;
  /** Reopening is only offered before the repair starts. */
  orderStatus: string;
  may: (capability: string) => boolean;
  /** The order changed on the server: the caller reloads it. */
  onChanged: () => void;
}) {
  const [channel, setChannel] = useState("");
  const [note, setNote] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [printed, setPrinted] = useState<PrintOutcome | null>(null);
  const [reopening, setReopening] = useState(false);
  const [reason, setReason] = useState("");

  async function print() {
    setError(null);
    setPrinted(null);
    try {
      setPrinted(await printQuoteTicket(slug, orderId, quote.id));
    } catch (err) {
      setError(err);
    }
  }

  async function record(decision: "approve" | "reject") {
    setWorking(true);
    setError(null);
    setPrinted(null);
    try {
      await recordQuoteDecision(slug, orderId, quote.id, { decision, channel, note: note.trim() });
      onChanged();
      // Only now: the approval exists on the server, so there is a ticket.
      if (decision === "approve") await print();
    } catch (err) {
      setError(err);
    } finally {
      setWorking(false);
    }
  }

  async function reopen() {
    setWorking(true);
    setError(null);
    try {
      await reopenQuote(slug, orderId, quote.id, reason.trim());
      setReopening(false);
      setReason("");
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setWorking(false);
    }
  }

  const decision = quote.decision;
  const approved = quote.status === "approved";
  const waiting = quote.status === "sent" && !quote.is_expired;

  return (
    <div className="mt-3 space-y-3 border-t border-bd-border pt-3">
      {decision ? (
        <p className="text-xs text-muted">
          {decision.source === "staff" ? (
            <>
              {decision.decision === "approve" ? "Aprobación" : "Rechazo"} registrado por{" "}
              <span className="font-semibold text-foreground">{decision.recorded_by ?? "el personal"}</span>
              {" · "}{decision.channel_label}{" · "}{dateTime(decision.decided_at)}
              {decision.note ? ` — ${decision.note}` : ""}
            </>
          ) : (
            <>
              El cliente {decision.decision === "approve" ? "aprobó" : "rechazó"}
              {" · "}{decision.channel_label}{" · "}{dateTime(decision.decided_at)}
              {decision.reason ? ` — “${decision.reason}”` : ""}
            </>
          )}
        </p>
      ) : null}

      {waiting && may(CAP_RECORD) ? (
        <div className="space-y-3 rounded-xl border border-bd-border bg-background p-3">
          <p className="text-xs text-muted">
            Si el cliente respondió en el mostrador, por teléfono o por mensaje, regístralo aquí.
            Queda anotado quién lo registró y por dónde llegó la respuesta.
          </p>
          <div className="grid gap-2 md:grid-cols-2">
            <label className="text-xs text-foreground/50">
              ¿Por dónde respondió?
              <select
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                className="mt-1.5 w-full rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition-colors focus:border-foreground/25"
              >
                <option value="">Elige una opción</option>
                {QUOTE_DECISION_CHANNELS.map((c) => (
                  <option key={c.value} value={c.value}>{c.label}</option>
                ))}
              </select>
            </label>
            <Field label="Nota (opcional)" value={note} onChange={setNote} placeholder="Ej.: llamó a las 10:15" />
          </div>
          <div className="flex flex-wrap gap-2">
            <Confirm
              label="Registrar aprobación"
              question="¿El cliente aprobó esta cotización?"
              tone="primary"
              disabled={working || !channel}
              onConfirm={() => void record("approve")}
            />
            <Confirm
              label="Registrar rechazo"
              question="¿El cliente rechazó esta cotización?"
              tone="danger"
              disabled={working || !channel}
              onConfirm={() => void record("reject")}
            />
          </div>
        </div>
      ) : null}

      {approved ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void print()} disabled={working}>Imprimir ticket</Button>
          {may(CAP_QUOTE) && orderStatus === "approved" && !reopening ? (
            <Button onClick={() => setReopening(true)} disabled={working}>Volver a cotizar</Button>
          ) : null}
        </div>
      ) : null}

      {approved && reopening ? (
        <div className="space-y-3 rounded-xl border border-warning-border bg-warning-surface p-3">
          <p className="text-xs text-warning">
            Volver a cotizar anula esta aprobación: la orden regresa a diagnóstico y se abre una
            revisión nueva, que el cliente tendrá que aprobar otra vez.
          </p>
          <Field label="Motivo" value={reason} onChange={setReason} placeholder="Ej.: el repuesto cambió de precio" />
          <div className="flex flex-wrap gap-2">
            <Button tone="danger" disabled={working || !reason.trim()} onClick={() => void reopen()}>
              Anular aprobación
            </Button>
            <Button onClick={() => { setReopening(false); setReason(""); }} disabled={working}>Cancelar</Button>
          </div>
        </div>
      ) : null}

      {printed ? (
        <p role="status" className="text-xs text-muted">{PRINT_NOTE[printed]}</p>
      ) : null}
      <ErrorNote error={error} />
    </div>
  );
}
