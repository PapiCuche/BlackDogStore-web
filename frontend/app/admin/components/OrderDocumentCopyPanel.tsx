"use client";

/**
 * SALES-EMAIL-03 — «Enviar copia a otro correo».
 *
 * Una copia de UN documento de la venta a una dirección que no es la del
 * cliente (su contador, el correo de su empresa). El correo del cliente no
 * cambia y no recibe nada.
 *
 * Tres pasos, porque el PDF lleva datos personales y comerciales:
 *
 *   1. elegir el documento (los nombra el servidor: lo que el adjunto ES) y
 *      escribir la dirección dos veces;
 *   2. revisar: destinatario exacto, documento, nombre del adjunto y el aviso;
 *   3. confirmar → «Enviando…» → enviado o el error del servidor.
 *
 * El servidor vuelve a comprobar la dirección, el permiso y el documento.
 */
import { useEffect, useId, useRef, useState } from "react";
import {
  DocumentCopyEntry,
  DocumentCopyKind,
  DocumentCopyOptions,
  DocumentCopyResult,
  fetchOrderDocumentCopyOptions,
  sendOrderDocumentCopy,
} from "../../lib/admin";
import { MAX_COPY_ADDRESS_LENGTH, copyAddressProblem } from "../../lib/document-copy";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

type Props = {
  orderId: number;
  customerEmail: string;
};

export function OrderDocumentCopyPanel({ orderId, customerEmail }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={internalButtonClass}>
        Enviar copia a otro correo
      </button>
      {open ? (
        <OrderDocumentCopyDialog orderId={orderId} customerEmail={customerEmail} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

type Step = "form" | "review" | "sending" | "sent";

export function OrderDocumentCopyDialog({
  orderId, customerEmail, onClose,
}: Props & { onClose: () => void }) {
  const titleId = useId();
  const toId = useId();
  const confirmId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLHeadingElement>(null);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const toField = useRef<HTMLInputElement>(null);
  const shownStep = useRef<Step>("form");

  const [options, setOptions] = useState<DocumentCopyOptions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [kind, setKind] = useState<DocumentCopyKind | null>(null);
  const [to, setTo] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("form");
  const [sendError, setSendError] = useState<string | null>(null);
  const [result, setResult] = useState<DocumentCopyResult | null>(null);
  const busy = step === "sending";

  useEffect(() => {
    let live = true;
    fetchOrderDocumentCopyOptions(orderId)
      .then((loaded) => {
        if (!live) return;
        setOptions(loaded);
        const firstAvailable = loaded.documents.find((entry) => entry.available);
        if (firstAvailable) setKind(firstAvailable.kind);
      })
      .catch((err) => live && setLoadError(err instanceof Error ? err.message : "No se pudo cargar."));
    return () => { live = false; };
  }, [orderId]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    first.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);

  // Each step change puts the focus where the new content starts, instead of
  // leaving it on a button that no longer exists (which drops it to <body>).
  useEffect(() => {
    const previous = shownStep.current;
    shownStep.current = step;
    if (step === previous) return;
    // Also back from «sending» after an error: the disabled button lost the focus,
    // and the error is announced (role="alert") inside the review.
    if (step === "review") reviewHeading.current?.focus();
    else if (step === "sent") resultHeading.current?.focus();
    else if (step === "form") toField.current?.focus();
  }, [step]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!busy) onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      // El foco da la vuelta dentro del diálogo: lo de detrás no se alcanza.
      // A radio group is ONE stop (its checked radio, or the first one if none
      // is): the arrow keys move inside it, as the platform does.
      const all = [...panel.current.querySelectorAll<HTMLElement>(
        "input:not([disabled]), button:not([disabled])",
      )];
      const stops = all.filter((el) => {
        if (!(el instanceof HTMLInputElement) || el.type !== "radio") return true;
        const group = all.filter(
          (other): other is HTMLInputElement =>
            other instanceof HTMLInputElement && other.type === "radio" && other.name === el.name,
        );
        const checked = group.find((radio) => radio.checked);
        return checked ? checked === el : group[0] === el;
      });
      if (stops.length === 0) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
      event.preventDefault();
      stops[next].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const chosen: DocumentCopyEntry | undefined = options?.documents.find((entry) => entry.kind === kind);
  const recipient = to.trim();

  function review() {
    const found = copyAddressProblem(to, confirmation);
    setProblem(found);
    if (found || !chosen?.available) return;
    setSendError(null);
    setStep("review");
  }

  async function send() {
    if (!chosen || busy) return;
    setStep("sending");
    setSendError(null);
    try {
      setResult(await sendOrderDocumentCopy(orderId, { document: chosen.kind, to: recipient, toConfirmation: confirmation }));
      setStep("sent");
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "No se pudo enviar la copia.");
      setStep("review");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="document-copy-dialog"
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-bd-border bg-surface p-5 shadow-xl sm:p-6"
      >
        <h2 id={titleId} ref={first} tabIndex={-1} className="text-lg font-bold text-foreground outline-none">
          Enviar copia a otro correo · Orden #{orderId}
        </h2>
        <p className="mt-2 text-sm text-muted">
          La copia va sólo a la dirección que escribas. El correo del cliente
          {customerEmail ? <> (<span className="break-all">{customerEmail}</span>)</> : null} no cambia y no recibe nada.
        </p>

        {loadError ? <p role="alert" className="mt-4 text-sm text-danger">{loadError}</p> : null}
        {!options && !loadError ? <p className="mt-4 text-sm text-muted">Cargando documentos…</p> : null}
        {options && !options.can_send ? (
          <p role="alert" className="mt-4 text-sm text-danger">{options.blocked_reason}</p>
        ) : null}

        {options?.can_send && step === "form" ? (
          <form
            className="mt-4 space-y-4"
            noValidate
            onSubmit={(event) => { event.preventDefault(); review(); }}
          >
            <fieldset>
              <legend className="text-sm font-semibold text-foreground">Documento que se envía</legend>
              <div className="mt-2 space-y-2">
                {options.documents.map((entry) => (
                  <label
                    key={entry.kind}
                    className={`flex items-start gap-2 rounded-lg border border-bd-border p-3 text-sm ${
                      entry.available ? "text-foreground" : "text-muted"
                    }`}
                  >
                    <input
                      type="radio"
                      name="document-copy-kind"
                      value={entry.kind}
                      checked={kind === entry.kind}
                      disabled={!entry.available}
                      onChange={() => setKind(entry.kind)}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="block font-medium">{entry.label}</span>
                      {entry.available && entry.note ? <span className="block text-xs text-muted">{entry.note}</span> : null}
                      {!entry.available ? <span className="block text-xs">{entry.unavailable_reason}</span> : null}
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div>
              <label htmlFor={toId} className="block text-sm font-semibold text-foreground">Correo de destino</label>
              <input
                id={toId}
                ref={toField}
                type="email"
                inputMode="email"
                autoComplete="off"
                spellCheck={false}
                maxLength={MAX_COPY_ADDRESS_LENGTH}
                value={to}
                onChange={(event) => { setTo(event.target.value); setProblem(null); }}
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? `${toId}-problem` : undefined}
                className={`mt-1 ${internalInputClass}`}
              />
            </div>
            <div>
              <label htmlFor={confirmId} className="block text-sm font-semibold text-foreground">
                Repite el correo de destino
              </label>
              <input
                id={confirmId}
                type="email"
                inputMode="email"
                autoComplete="off"
                spellCheck={false}
                maxLength={MAX_COPY_ADDRESS_LENGTH}
                value={confirmation}
                onChange={(event) => { setConfirmation(event.target.value); setProblem(null); }}
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? `${toId}-problem` : undefined}
                className={`mt-1 ${internalInputClass}`}
              />
              <p className="mt-1 text-xs text-muted">Una sola dirección, sin nombre ni separadores.</p>
            </div>
            {problem ? <p id={`${toId}-problem`} role="alert" className="text-sm text-danger">{problem}</p> : null}
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={onClose} className={internalButtonClass}>Cancelar</button>
              <button type="submit" disabled={!chosen?.available} className={internalPrimaryButtonClass}>
                Revisar envío
              </button>
            </div>
          </form>
        ) : null}

        {chosen && (step === "review" || step === "sending") ? (
          <div className="mt-4 space-y-3" data-testid="document-copy-review">
            <h3 ref={reviewHeading} tabIndex={-1} className="text-sm font-semibold text-foreground outline-none">
              Revisa el envío
            </h3>
            <dl className="space-y-2 rounded-lg border border-bd-border bg-background p-3 text-sm">
              <div>
                <dt className="text-xs text-muted">Se enviará a</dt>
                <dd className="break-all font-mono font-semibold text-foreground" data-testid="document-copy-recipient">
                  {recipient}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Documento</dt>
                <dd className="text-foreground">{chosen.label}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Archivo adjunto</dt>
                <dd className="break-all text-foreground">{chosen.attachment_name}</dd>
              </div>
              {chosen.note ? <p className="text-xs text-muted">{chosen.note}</p> : null}
            </dl>
            <p className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-foreground">
              <span className="font-semibold">Contiene datos personales y comerciales</span> del cliente: nombre,
              documento, teléfono, productos e importes. Comprueba la dirección: un correo enviado no se puede
              recuperar. El envío queda registrado con tu usuario.
            </p>
            {sendError ? <p role="alert" className="text-sm text-danger">{sendError}</p> : null}
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => setStep("form")} disabled={busy} className={internalButtonClass}>
                Volver y corregir
              </button>
              <button type="button" onClick={send} disabled={busy} className={internalPrimaryButtonClass}>
                {busy ? "Enviando…" : "Confirmar y enviar"}
              </button>
            </div>
          </div>
        ) : null}

        {step === "sent" && result ? (
          <div className="mt-4 space-y-3">
            <h3 ref={resultHeading} tabIndex={-1} className="text-sm font-semibold text-foreground outline-none">
              Copia enviada
            </h3>
            <p role="status" className="rounded-lg border border-success-border bg-success-surface px-3 py-2 text-sm text-foreground">
              Copia enviada a <span className="break-all font-semibold">{result.sent_to}</span>: {result.document_label}.
            </p>
            <div className="flex justify-end">
              <button type="button" onClick={onClose} className={internalButtonClass}>Cerrar</button>
            </div>
          </div>
        ) : null}

        {(loadError || (options && !options.can_send)) ? (
          <div className="mt-4 flex justify-end">
            <button type="button" onClick={onClose} className={internalButtonClass}>Cerrar</button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
