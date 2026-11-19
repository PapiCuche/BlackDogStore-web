"use client";

/**
 * NOTE-NOT-SUBSTITUTE — registrar la boleta o factura emitida fuera del sistema.
 *
 * Mientras la facturación electrónica del sistema esté desactivada, el
 * comprobante de cada venta se emite por otro canal de SUNAT (SOL u otro
 * medio). Este panel deja anotado cuál fue, para conciliar. El sistema NO lo
 * verifica en SUNAT y esta pantalla nunca dice «aceptado»: dice «registrado».
 * Un registro equivocado se anula con su motivo; no se edita ni se borra.
 */
import { useEffect, useState } from "react";
import {
  fetchExternalReceipts,
  registerExternalReceipt,
  voidExternalReceipt,
  type ExternalReceipt,
  type ExternalReceiptBlock,
  type ExternalReceiptInput,
} from "../../lib/external-receipts";
import type { ReceiptCoverage } from "../../lib/admin";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const EMPTY: ExternalReceiptInput = {
  document_type: "03", series: "", number: "", issue_date: "", channel: "sunat_sol", reference: "",
};

function Registration({ row, canVoid, onVoid }: {
  row: ExternalReceipt; canVoid: boolean; onVoid: (row: ExternalReceipt) => void;
}) {
  const live = row.status === "registered";
  return (
    <li data-testid={`external-receipt-${row.id}`} className="rounded-lg border border-bd-border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-foreground">
          <span className="font-medium">{row.document_type_label}</span>{" "}
          <span className="font-mono">{row.document_id}</span>
          <span className={live ? "ml-2 text-xs text-muted" : "ml-2 text-xs font-semibold text-danger"}>
            {live ? "Registrado (no verificado en SUNAT por el sistema)" : "Anulado"}
          </span>
        </p>
        {live && canVoid ? (
          <button type="button" onClick={() => onVoid(row)} className={internalButtonClass}>Anular registro</button>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted">
        Emitido el {row.issue_date} · {row.channel_label} · Referencia: <span className="break-all">{row.reference}</span>
      </p>
      {row.issued_after_payment ? (
        <p className="mt-1 text-xs text-warning">
          Emitido después del día del cobro: el comprobante correspondía al cobrar o entregar.
        </p>
      ) : null}
      <p className="mt-1 text-xs text-muted">Registrado por {row.registered_by ?? "—"}</p>
      {!live ? (
        <p className="mt-1 text-xs text-muted">
          Anulado por {row.voided_by ?? "—"}: <span className="whitespace-pre-wrap">{row.void_reason}</span>
        </p>
      ) : null}
    </li>
  );
}

export function ExternalReceiptPanel({ orderId, onCoverage }: {
  orderId: number;
  /** El estado del comprobante cambió (registro o anulación): la ficha vuelve a decirlo. */
  onCoverage?: (coverage: ReceiptCoverage) => void;
}) {
  const [block, setBlock] = useState<ExternalReceiptBlock | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [form, setForm] = useState<ExternalReceiptInput>(EMPTY);
  const [voiding, setVoiding] = useState<ExternalReceipt | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let gone = false;
    fetchExternalReceipts(orderId)
      .then((result) => { if (!gone) setBlock(result); })
      .catch((err) => { if (!gone) setError(err instanceof Error ? err.message : "No se pudo cargar."); })
      .finally(() => { if (!gone) setLoaded(true); });
    return () => { gone = true; };
  }, [orderId]);

  function apply(next: ExternalReceiptBlock) {
    setBlock(next);
    onCoverage?.(next.coverage);
  }

  async function run(action: () => Promise<ExternalReceiptBlock>, after: () => void) {
    setBusy(true);
    setError(null);
    try {
      apply(await action());
      after();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar la acción.");
    } finally {
      setBusy(false);
    }
  }

  // Sin permiso para ver comprobantes, o sin nada que mostrar: el panel no aparece.
  if (!loaded || !block) {
    return error ? <p role="alert" className="text-sm text-danger">{error}</p> : null;
  }
  if (!block.registrations.length && !block.can_register) return null;

  const set = (key: keyof ExternalReceiptInput) =>
    (event: { target: { value: string } }) => setForm((current) => ({ ...current, [key]: event.target.value }));

  return (
    <section className="rounded-xl border border-bd-border bg-surface p-6" aria-label="Comprobante emitido fuera del sistema">
      <h2 className="mb-1 text-sm font-semibold text-foreground">Comprobante emitido fuera del sistema</h2>
      <p className="mb-4 text-xs text-muted">{block.notice}</p>

      {block.registrations.length ? (
        <ul className="mb-4 space-y-2">
          {block.registrations.map((row) => (
            <Registration key={row.id} row={row} canVoid={block.can_void} onVoid={(r) => { setVoiding(r); setReason(""); }} />
          ))}
        </ul>
      ) : null}

      {voiding ? (
        <form
          className="mb-4 space-y-2 rounded-lg border border-warning-border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(() => voidExternalReceipt(orderId, voiding.id, reason), () => setVoiding(null));
          }}
        >
          <label className="block text-xs text-muted">
            Motivo de la anulación de {voiding.document_id}
            <textarea className={`${internalInputClass} mt-1`} rows={2} maxLength={1000}
              value={reason} onChange={(event) => setReason(event.target.value)} required />
          </label>
          <div className="flex gap-2">
            <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>Anular registro</button>
            <button type="button" onClick={() => setVoiding(null)} className={internalButtonClass}>Cancelar</button>
          </div>
        </form>
      ) : null}

      {block.can_register ? (
        <form
          aria-label="Registrar comprobante externo"
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void run(() => registerExternalReceipt(orderId, form), () => setForm(EMPTY));
          }}
        >
          <label className="block text-xs text-muted">
            Tipo
            <select className={`${internalInputClass} mt-1`} value={form.document_type} onChange={set("document_type")}>
              {block.document_types.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Medio de emisión
            <select className={`${internalInputClass} mt-1`} value={form.channel} onChange={set("channel")}>
              {block.channels.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Serie
            <input className={`${internalInputClass} mt-1 font-mono uppercase`} maxLength={4} required
              placeholder="EB01" value={form.series} onChange={set("series")} />
          </label>
          <label className="block text-xs text-muted">
            Número
            <input className={`${internalInputClass} mt-1 font-mono`} inputMode="numeric" maxLength={8} required
              placeholder="123" value={form.number} onChange={set("number")} />
          </label>
          <label className="block text-xs text-muted">
            Fecha de emisión
            <input type="date" className={`${internalInputClass} mt-1`} required
              value={form.issue_date} onChange={set("issue_date")} />
          </label>
          <label className="block text-xs text-muted">
            Referencia verificable
            <input className={`${internalInputClass} mt-1`} maxLength={500} required
              placeholder="Consulta en SOL, enlace o identificador" value={form.reference} onChange={set("reference")} />
          </label>
          <div className="sm:col-span-2">
            <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>
              {busy ? "Registrando…" : "Registrar comprobante externo"}
            </button>
          </div>
        </form>
      ) : null}

      {error ? <p role="alert" className="mt-3 text-sm text-danger">{error}</p> : null}
    </section>
  );
}
