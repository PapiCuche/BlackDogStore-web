"use client";

/**
 * El comprobante electrónico de una venta. NO es la nota de venta interna.
 *
 * Son dos documentos distintos y por eso son dos bloques distintos. La nota
 * interna se numera con `NV-` y lleva impreso que no vale ante SUNAT; esto es
 * una factura electrónica. Juntarlas invitaría a confundir un papel que no tiene
 * validez tributaria con uno que sí la tiene.
 *
 * EL BACKEND MANDA
 * ----------------
 * Los botones se muestran según `can_submit`, `can_retry` y `can_download_pdf`,
 * que vienen en la respuesta. Eso es cortesía, no seguridad: cada acción vuelve
 * a comprobarse en el servidor, y ocultar un botón nunca impidió que alguien
 * llame a la ruta.
 *
 * NO SE DICE «ACEPTADA» SIN CONSTANCIA. El estado que se pinta es el que manda
 * el backend, que sólo lo marca aceptado cuando hay CDR persistida.
 */

import { useEffect, useState } from "react";
import {
  downloadFiscalArtifact,
  fetchFiscalDocument,
  issueFiscalDocument,
  submitFiscalDocument,
  type FiscalDocument,
} from "../../lib/fiscal";

type Props = {
  orderId: number;
  /** Sólo se emite comprobante de una venta pagada. */
  isPaid: boolean;
  /** `factura`, `boleta`… Esta fase sólo emite facturas. */
  receiptType: string;
};

/** Colores por estado. Un rechazo tiene que verse como un rechazo. */
const TONE: Record<string, string> = {
  accepted: "text-success",
  accepted_observed: "text-warning",
  rejected: "text-danger",
  submission_error: "text-warning",
};

export function FiscalDocumentPanel({ orderId, isPaid, receiptType }: Props) {
  const [document, setDocument] = useState<FiscalDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // La carga vive DENTRO del efecto y con bandera de cancelación: así el
    // `setState` es inequívocamente asíncrono —la regla de React no puede
    // probarlo si se esconde tras un `useCallback`— y una respuesta que llega
    // tras desmontar el panel no intenta pintar nada.
    let cancelled = false;
    void (async () => {
      try {
        const found = await fetchFiscalDocument(orderId);
        if (!cancelled) setDocument(found);
      } catch {
        // Un 404 significa «todavía no hay comprobante», que es un estado
        // normal y no un fallo: la pantalla lo dice y ofrece emitirlo.
        if (!cancelled) setDocument(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [orderId]);

  async function run(label: string, action: () => Promise<FiscalDocument>) {
    if (busy) return;
    setBusy(label);
    setError(null);
    try {
      setDocument(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar la acción.");
    } finally {
      setBusy(null);
    }
  }

  async function download(kind: "pdf" | "ticket" | "xml" | "cdr") {
    if (!document || busy) return;
    setBusy(kind);
    setError(null);
    try {
      await downloadFiscalArtifact(document.id, kind);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo descargar el archivo.");
    } finally {
      setBusy(null);
    }
  }

  // Esta fase sólo emite facturas. Una venta que pidió boleta no muestra el
  // bloque en vez de mostrar un botón que siempre falla.
  if (receiptType !== "factura") return null;

  return (
    <section className="rounded-xl border border-bd-border bg-surface p-6">
      <h2 className="mb-1 text-sm font-semibold text-foreground">
        Comprobante electrónico
      </h2>
      <p className="mb-4 text-xs text-muted">
        Factura solicitada. Distinta de la nota de venta interna.
      </p>

      {!isPaid ? (
        <p className="text-sm text-muted">
          Sólo se emite comprobante de una venta pagada. El comprobante no es
          autoridad del pago.
        </p>
      ) : loading ? (
        <div className="flex items-center gap-3 text-sm text-muted">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-bd-border border-t-transparent" />
          Cargando…
        </div>
      ) : (
        <>
          {document ? (
            <dl className="mb-4 grid gap-x-6 gap-y-2 sm:grid-cols-2">
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                  Número
                </dt>
                <dd className="mt-0.5 font-mono text-sm text-foreground">
                  {document.identifier}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                  Estado
                </dt>
                <dd
                  className={`mt-0.5 text-sm font-medium ${
                    TONE[document.status] ?? "text-foreground/85"
                  }`}
                >
                  {document.status_label}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                  Ambiente
                </dt>
                <dd className="mt-0.5 text-sm text-foreground/85">
                  {document.environment_label}
                </dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                  Total
                </dt>
                <dd className="mt-0.5 text-sm tabular-nums text-foreground/85">
                  {document.currency} {document.total}
                </dd>
              </div>
              {document.response_message ? (
                <div className="sm:col-span-2">
                  <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                    Respuesta de SUNAT
                  </dt>
                  <dd className="mt-0.5 break-words text-sm text-foreground/85">
                    {document.response_code ? `${document.response_code} · ` : ""}
                    {document.response_message}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : (
            <p className="mb-4 text-sm text-muted">
              Esta venta todavía no tiene comprobante electrónico.
            </p>
          )}

          {error ? (
            <div className="mb-4 rounded-lg border border-danger-border bg-danger-surface px-4 py-3">
              <p className="text-sm text-danger">{error}</p>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {!document ? (
              <button
                type="button"
                onClick={() => void run("issue", () => issueFiscalDocument(orderId))}
                disabled={busy !== null}
                className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "issue" ? "Emitiendo…" : "Emitir factura"}
              </button>
            ) : null}

            {document?.can_submit ? (
              <button
                type="button"
                onClick={() =>
                  void run("submit", () => submitFiscalDocument(document.id))
                }
                disabled={busy !== null}
                className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "submit" ? "Enviando…" : "Enviar a SUNAT"}
              </button>
            ) : null}

            {/*
              REINTENTAR, no «emitir otra vez»: es el MISMO comprobante, con su
              misma serie y su mismo correlativo. Que el botón lo diga evita que
              alguien crea que está creando uno nuevo.
            */}
            {document?.can_retry ? (
              <button
                type="button"
                onClick={() =>
                  void run("retry", () => submitFiscalDocument(document.id))
                }
                disabled={busy !== null}
                className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy === "retry" ? "Reintentando…" : "Reintentar el mismo comprobante"}
              </button>
            ) : null}

            {document?.can_download_pdf ? (
              <>
                <button
                  type="button"
                  onClick={() => void download("pdf")}
                  disabled={busy !== null}
                  className="rounded-lg border border-bd-border px-4 py-2 text-sm text-foreground transition hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy === "pdf" ? "Generando…" : "PDF A4"}
                </button>
                <button
                  type="button"
                  onClick={() => void download("ticket")}
                  disabled={busy !== null}
                  className="rounded-lg border border-bd-border px-4 py-2 text-sm text-foreground transition hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy === "ticket" ? "Generando…" : "Ticket 80 mm"}
                </button>
              </>
            ) : null}

            {document?.has_xml ? (
              <button
                type="button"
                onClick={() => void download("xml")}
                disabled={busy !== null}
                className="rounded-lg border border-bd-border px-4 py-2 text-sm text-muted transition hover:bg-surface-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                XML
              </button>
            ) : null}

            {document?.has_cdr ? (
              <button
                type="button"
                onClick={() => void download("cdr")}
                disabled={busy !== null}
                className="rounded-lg border border-bd-border px-4 py-2 text-sm text-muted transition hover:bg-surface-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                CDR
              </button>
            ) : null}
          </div>

          {/*
            UN RECHAZO ES TERMINAL PARA EL BOTÓN GENÉRICO. No se ofrece «emitir
            otra vez»: SUNAT considera usado el correlativo de un documento
            rechazado, y cada clic gastaría otro sin resolver la causa.
          */}
          {document?.status === "rejected" ? (
            <p className="mt-3 text-xs leading-relaxed text-muted">
              El comprobante fue rechazado. Corrija la causa antes de emitir uno
              nuevo: SUNAT considera usado este correlativo, así que volver a
              emitir gastaría otro número.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
