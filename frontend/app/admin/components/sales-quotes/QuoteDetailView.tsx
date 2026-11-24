"use client";

/**
 * SALES-QUOTE-01 — una cotización: lo ofrecido, sus revisiones y lo que se puede hacer con ella ahora.
 *
 * Los botones son los que el servidor dice (`actions`); el servidor vuelve a decidir cada uno. Editar un
 * borrador lo cambia; editar lo ya ofrecido crea una revisión nueva (la anterior queda «Reemplazada»).
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { formatAdminDate } from "../../../lib/admin";
import {
  downloadQuotePdf,
  draftFromQuote,
  formatMoney,
  newQuoteKey,
  printQuoteTicket,
  quoteTransition,
  type QuoteContext,
  type QuoteDetail,
  type QuoteTransition,
} from "../../lib/sales-quotes-api";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../internal-ui";
import { QuoteConvertDialog } from "./QuoteConvertDialog";
import { QuoteEditor } from "./QuoteEditor";
import { QuoteStatusBadge } from "./QuoteStatusBadge";

export function QuoteDetailView({
  quote, context, onChange,
}: {
  quote: QuoteDetail;
  context: QuoteContext;
  onChange: (quote: QuoteDetail) => void;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [converting, setConverting] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const printKey = useRef(newQuoteKey("cot-ticket"));
  const a = quote.actions;

  async function run(action: QuoteTransition, body: Record<string, unknown> = {}) {
    setBusy(action);
    setError(null);
    setMessage(null);
    try {
      const next = await quoteTransition(quote.id, action, body);
      if (next.id !== quote.id) {
        router.push(`/admin/sales/quotes/${next.id}`);
        return;
      }
      onChange(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar la acción.");
    } finally {
      setBusy(null);
    }
  }

  async function download(format: "a4" | "ticket80") {
    setBusy(format);
    setError(null);
    try {
      const { blob, filename } = await downloadQuotePdf(quote.id, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo generar el PDF.");
    } finally {
      setBusy(null);
    }
  }

  async function print() {
    setBusy("print");
    setError(null);
    try {
      const job = await printQuoteTicket(quote.id, printKey.current);
      setMessage(`Ticket enviado a ${job.printer_name}.`);
      printKey.current = newQuoteKey("cot-ticket");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo enviar a la impresora.");
    } finally {
      setBusy(null);
    }
  }

  if (editing) {
    return (
      <div className="space-y-4">
        <button type="button" className={internalButtonClass} onClick={() => setEditing(false)}>Volver sin guardar</button>
        <QuoteEditor
          context={context}
          initial={draftFromQuote(quote)}
          quoteId={quote.id}
          offered={quote.status !== "draft"}
          onSaved={(saved) => {
            setEditing(false);
            if (saved.id !== quote.id) router.push(`/admin/sales/quotes/${saved.id}`);
            else onChange(saved);
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <QuoteStatusBadge status={quote.status} />
        <span className="text-sm text-muted">
          Válida hasta {quote.valid_until ?? "—"} · {quote.branch_name}
          {quote.seller_name ? ` · ${quote.seller_name}` : ""}
        </span>
      </div>

      <div className="flex flex-wrap gap-2" role="toolbar" aria-label="Acciones de la cotización">
        {a.edit || a.revise ? (
          <button type="button" className={internalButtonClass} onClick={() => setEditing(true)}>
            {a.edit ? "Editar" : "Nueva revisión"}
          </button>
        ) : null}
        {a.approve ? <button type="button" className={internalPrimaryButtonClass} disabled={busy !== null} onClick={() => void run("approve")}>Aprobar</button> : null}
        {a.send ? <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void run("send")}>Marcar como enviada</button> : null}
        {a.convert ? <button type="button" className={internalPrimaryButtonClass} onClick={() => setConverting(true)}>Convertir en venta</button> : null}
        <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void download("a4")}>PDF A4</button>
        <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void download("ticket80")}>PDF 80 mm</button>
        <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void print()}>Imprimir ticket</button>
        {a.duplicate ? <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void run("duplicate")}>Duplicar</button> : null}
      </div>

      {a.decide ? (
        <section className="space-y-2 rounded-xl border border-bd-border bg-surface p-4" aria-label="Respuesta del cliente">
          <label className="block text-sm">
            <span className="text-xs font-semibold uppercase tracking-widest text-muted">Respuesta del cliente (nota opcional)</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} className={internalInputClass} maxLength={300} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void run("accept", { note })}>Aceptó</button>
            <button type="button" className={internalButtonClass} disabled={busy !== null} onClick={() => void run("reject", { note })}>Rechazó</button>
          </div>
        </section>
      ) : null}

      {message ? <p role="status" className="rounded-lg border border-success-border bg-success-surface px-4 py-3 text-sm">{message}</p> : null}
      {error ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">{error}</p> : null}

      {quote.converted_order ? (
        <p className="rounded-lg border border-success-border bg-success-surface px-4 py-3 text-sm">
          Convertida en la venta <Link className="font-semibold underline" href={`/admin/orders/${quote.converted_order}`}>#{quote.converted_order}</Link>.
        </p>
      ) : null}

      <section className="grid gap-4 rounded-xl border border-bd-border bg-surface p-4 text-sm sm:grid-cols-2">
        <div>
          <h2 className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-muted">Cliente</h2>
          <p className="font-semibold">{quote.customer_name || "Sin identificar"}</p>
          {quote.customer_document_number ? <p>{quote.customer_document_type.toUpperCase()} {quote.customer_document_number}</p> : null}
          {quote.customer_phone ? <p>{quote.customer_phone}</p> : null}
          {quote.customer_email ? <p>{quote.customer_email}</p> : null}
        </div>
        <div>
          <h2 className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-muted">Seguimiento</h2>
          <p>Creada {formatAdminDate(quote.created_at)}{quote.created_by ? ` por ${quote.created_by}` : ""}</p>
          {quote.approved_at ? <p>Aprobada {formatAdminDate(quote.approved_at)} por {quote.approved_by}</p> : null}
          {quote.sent_at ? <p>Enviada {formatAdminDate(quote.sent_at)}</p> : null}
          {quote.decided_at ? <p>Respuesta {formatAdminDate(quote.decided_at)} ({quote.decided_by}){quote.decision_note ? `: ${quote.decision_note}` : ""}</p> : null}
        </div>
      </section>

      <section className="rounded-xl border border-bd-border bg-surface">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <caption className="sr-only">Líneas de la cotización</caption>
            <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
              <tr>
                <th scope="col" className="px-4 py-3">Producto</th>
                <th scope="col" className="px-4 py-3 text-right">Cant.</th>
                <th scope="col" className="px-4 py-3 text-right">P. unit.</th>
                <th scope="col" className="px-4 py-3 text-right">Importe</th>
              </tr>
            </thead>
            <tbody>
              {quote.items.map((item) => (
                <tr key={item.id} className="border-b border-bd-border/60 last:border-0">
                  <td className="px-4 py-3">
                    <span className="font-medium">{item.description}</span>
                    {item.details ? <span className="block text-xs text-muted">{item.details}</span> : null}
                    {item.unit ? <span className="block text-xs text-muted">{item.unit.imei ? `IMEI ${item.unit.imei}` : `Serie ${item.unit.serial_number}`}</span> : null}
                    {item.on_order ? <span className="block text-xs text-muted">Bajo pedido</span> : null}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{item.quantity}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{formatMoney(item.unit_price, quote.currency)}</td>
                  <td className="px-4 py-3 text-right font-semibold tabular-nums">{formatMoney(item.line_total, quote.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 border-t border-bd-border px-4 py-3 text-sm tabular-nums">
          <dt>Subtotal</dt><dd className="text-right">{formatMoney(quote.subtotal, quote.currency)}</dd>
          {Number(quote.discount_amount) > 0 ? (
            <>
              <dt>Descuento{quote.discount_reason ? ` (${quote.discount_reason})` : ""}</dt>
              <dd className="text-right">- {formatMoney(quote.discount_amount, quote.currency)}</dd>
            </>
          ) : null}
          <dt className="text-muted">Op. gravada</dt><dd className="text-right text-muted">{formatMoney(quote.taxable_amount, quote.currency)}</dd>
          <dt className="text-muted">IGV</dt><dd className="text-right text-muted">{formatMoney(quote.tax_amount, quote.currency)}</dd>
          <dt className="text-base font-bold">Total</dt><dd className="text-right text-base font-bold">{formatMoney(quote.total, quote.currency)}</dd>
        </dl>
      </section>

      {quote.conditions ? (
        <section className="rounded-xl border border-bd-border bg-surface p-4 text-sm">
          <h2 className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-muted">Condiciones comerciales</h2>
          <p className="whitespace-pre-line">{quote.conditions}</p>
        </section>
      ) : null}
      {quote.notes ? (
        <section className="rounded-xl border border-bd-border bg-surface p-4 text-sm">
          <h2 className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-muted">Notas internas</h2>
          <p className="whitespace-pre-line">{quote.notes}</p>
        </section>
      ) : null}

      <section className="rounded-xl border border-bd-border bg-surface p-4 text-sm" aria-labelledby="quote-history">
        <h2 id="quote-history" className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-muted">Revisiones</h2>
        <ol className="space-y-1">
          {quote.history.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center justify-between gap-2">
              {row.id === quote.id ? (
                <span className="font-semibold" aria-current="page">{row.label}</span>
              ) : (
                <Link href={`/admin/sales/quotes/${row.id}`} className="underline-offset-2 hover:underline">{row.label}</Link>
              )}
              <span className="flex items-center gap-2">
                <QuoteStatusBadge status={row.status} />
                <span className="tabular-nums">{formatMoney(row.total, quote.currency)}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      {converting ? (
        <QuoteConvertDialog
          quote={quote}
          context={context}
          onClose={() => setConverting(false)}
          onConverted={(result) => { setConverting(false); onChange(result.quote); }}
        />
      ) : null}
    </div>
  );
}
