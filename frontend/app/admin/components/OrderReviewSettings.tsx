"use client";

// CLIENT-SEC-01 — the two company options about reviewing orders.
//
// Every online order is assessed when it is made; that needs no setting. These
// two decide what the company adds to it, and BOTH START OFF: with them as
// they are created, nothing about selling or dispatching changes.
//
// They are saved on their own, with only their two fields. Changing them takes
// `settings.manage` as well as `company.manage`; whether THIS person may is the
// server's answer (`canEdit`), and the server checks again on the save.

import { useState } from "react";
import { updateCompanyConfiguration, type CompanyConfiguration } from "../lib/internal-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";

type Props = {
  companyId: number | null;
  /** The amount as the server keeps it ("1500.00"), or null: no amount named. */
  amount: string | null;
  hold: boolean;
  canEdit: boolean;
  /** Given the configuration the server answered with after a save. */
  onSaved: (config: CompanyConfiguration) => void;
};

export function OrderReviewSettings({ companyId, amount, hold, canEdit, onSaved }: Props) {
  const [amountDraft, setAmountDraft] = useState(amount ?? "");
  const [holdDraft, setHoldDraft] = useState(hold);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [amountError, setAmountError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    setAmountError(null);
    try {
      const typed = amountDraft.trim().replace(",", ".");
      const updated = await updateCompanyConfiguration(companyId, {
        // Empty is «no amount»: the rule is off. Never a zero.
        risk_review_amount: typed === "" ? null : typed,
        hold_orders_pending_review: holdDraft,
      });
      onSaved(updated);
      setAmountDraft(updated.settings.risk_review_amount ?? "");
      setHoldDraft(Boolean(updated.settings.hold_orders_pending_review));
      setSaved(true);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      if (fields?.risk_review_amount) setAmountError(fields.risk_review_amount);
      else setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">
        Cada pedido en línea se evalúa al crearse y, si varias señales coinciden, queda «por revisar»
        para quien gestiona pedidos. La revisión es una referencia: no cancela pedidos ni toca pagos.
        Con estas dos opciones apagadas, la venta y el despacho funcionan como siempre.
      </p>

      <div>
        <label
          className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
          htmlFor="cfg-risk_review_amount"
        >
          Monto de revisión (S/)
        </label>
        <input
          id="cfg-risk_review_amount"
          inputMode="decimal"
          className={`${internalInputClass} sm:max-w-xs`}
          value={amountDraft}
          placeholder="Sin monto"
          disabled={!canEdit || saving}
          aria-invalid={Boolean(amountError)}
          aria-describedby="cfg-risk_review_amount-hint"
          onChange={(e) => { setAmountDraft(e.target.value); setSaved(false); }}
        />
        <p id="cfg-risk_review_amount-hint" className={`mt-1.5 text-[11px] ${amountError ? "text-danger" : "text-muted"}`}>
          {amountError ?? "Un pedido de este total o más suma un motivo de revisión. Vacío: no se usa."}
        </p>
      </div>

      <div>
        <label className="inline-flex cursor-pointer select-none items-start gap-2 text-sm text-foreground/85">
          <input
            type="checkbox"
            className="mt-0.5 accent-warning"
            checked={holdDraft}
            disabled={!canEdit || saving}
            onChange={(e) => { setHoldDraft(e.target.checked); setSaved(false); }}
          />
          Retener los pedidos por revisar
        </label>
        <p className="mt-1.5 text-[11px] text-muted">
          Activado, un pedido con revisión pendiente no se puede marcar como enviado o entregado hasta
          que alguien la apruebe o la rechace. Apagado, la revisión es sólo informativa.
        </p>
      </div>

      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={saving} className={internalButtonClass}>
            {saving ? "Guardando…" : "Guardar revisión de pedidos"}
          </button>
          {saved ? <span role="status" className="text-sm text-muted">Cambios guardados.</span> : null}
        </div>
      ) : (
        <p className="text-xs text-muted">
          Puedes ver estas opciones pero no cambiarlas. Se requieren las capacidades{" "}
          <code className="text-foreground/85">company.manage</code> y{" "}
          <code className="text-foreground/85">settings.manage</code>, con acceso a toda la empresa.
        </p>
      )}
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </div>
  );
}
