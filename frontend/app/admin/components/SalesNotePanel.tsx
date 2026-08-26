"use client";

// Phase 6.0 — INTERNAL sales note for a paid order.
// This is NOT a SUNAT electronic receipt. Issuing it never touches payment
// state and never touches inventory. No Stripe identifier is shown here.

import { useEffect, useState } from "react";
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
    <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
      <h2 className="mb-1 text-sm font-semibold text-white">Nota de venta interna</h2>
      <p className="mb-4 text-xs text-zinc-500">{SALES_NOTE_NOTICE}</p>

      {!isPaid ? (
        <p className="text-sm text-zinc-500">
          Solo se puede emitir una nota de venta interna para órdenes pagadas.
        </p>
      ) : loading ? (
        <div className="flex items-center gap-3 text-sm text-zinc-500">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-600 border-t-transparent" />
          Cargando…
        </div>
      ) : (
        <>
          {note ? (
            <dl className="mb-4 grid gap-x-6 gap-y-2 sm:grid-cols-2">
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Número interno
                </dt>
                <dd className="mt-0.5 font-mono text-sm text-white">{note.number}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Emitida
                </dt>
                <dd className="mt-0.5 text-sm text-zinc-300">{formatWhen(note.issued_at)}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Estado
                </dt>
                <dd className="mt-0.5 text-sm text-zinc-300">{note.status_label}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                  Emitida por
                </dt>
                <dd className="mt-0.5 text-sm text-zinc-300">
                  {note.created_by_username ?? "—"}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mb-4 text-sm text-zinc-500">
              Esta orden todavía no tiene nota de venta interna.
            </p>
          )}

          {error ? (
            <div className="mb-4 rounded-lg border border-red-500/25 bg-red-500/[0.07] px-4 py-3">
              <p className="text-sm text-red-300">{error}</p>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {!note ? (
              <button
                type="button"
                onClick={() => void handleIssue()}
                disabled={issuing}
                className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {issuing ? "Emitiendo…" : "Generar nota de venta"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void handleDownload()}
                disabled={downloading}
                className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {downloading ? "Generando PDF…" : "Descargar PDF"}
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
