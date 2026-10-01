"use client";

// Phase 6.0 — INTERNAL sales note for a paid order.
// This is NOT a SUNAT electronic receipt. Issuing it never touches payment
// state and never touches inventory. No gateway identifier is shown here.

import { useEffect, useState } from "react";
import { Button, ErrorBox, Panel, Spinner } from "./internal-ui";
import {
  SALES_NOTE_NOTICE,
  createSalesNote,
  downloadSalesNotePdf,
  fetchSalesNote,
  type SalesNote,
} from "../../lib/inventory";

type Props = { orderId: number; isPaid: boolean };

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("es-PE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function SalesNotePanel({ orderId, isPaid }: Props) {
  const [note, setNote] = useState<SalesNote | null>(null);
  // Starts false for unpaid orders so the effect never has to setState synchronously.
  const [loading, setLoading] = useState(isPaid);
  const [issuing, setIssuing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isPaid) return;
    let cancelled = false;
    void (async () => {
      try {
        const existing = await fetchSalesNote(orderId);
        if (!cancelled) setNote(existing);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la nota de venta.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orderId, isPaid]);

  async function handleIssue() {
    setIssuing(true);
    setError(null);
    try {
      setNote(await createSalesNote(orderId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo emitir la nota de venta.");
    } finally {
      setIssuing(false);
    }
  }

  async function handleDownload() {
    if (!note) return;
    setDownloading(true);
    setError(null);
    try {
      await downloadSalesNotePdf(orderId, note.number);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo descargar el PDF.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Panel title="Nota de venta interna" description={SALES_NOTE_NOTICE}>

      {!isPaid ? (
        <p className="text-sm text-muted-foreground">
          Solo se puede emitir una nota de venta interna para órdenes pagadas.
        </p>
      ) : loading ? (
        <Spinner />
      ) : (
        <>
          {note ? (
            <dl className="mb-4 grid gap-x-6 gap-y-2 sm:grid-cols-2">
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Número interno
                </dt>
                <dd className="mt-0.5 font-mono text-sm text-foreground">{note.number}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Emitida
                </dt>
                <dd className="mt-0.5 text-sm text-foreground">{formatWhen(note.issued_at)}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Estado
                </dt>
                <dd className="mt-0.5 text-sm text-foreground">{note.status_label}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Emitida por
                </dt>
                <dd className="mt-0.5 text-sm text-foreground">
                  {note.created_by_username ?? "—"}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mb-4 text-sm text-muted-foreground">
              Esta orden todavía no tiene nota de venta interna.
            </p>
          )}

          {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}

          <div className="flex flex-wrap gap-2">
            {!note ? (
              <Button onClick={() => void handleIssue()} disabled={issuing} tone="primary">
                {issuing ? "Emitiendo…" : "Generar nota de venta"}
              </Button>
            ) : (
              <Button onClick={() => void handleDownload()} disabled={downloading} tone="primary">
                {downloading ? "Generando PDF…" : "Descargar PDF"}
              </Button>
            )}
          </div>
        </>
      )}
    </Panel>
  );
}
