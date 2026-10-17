"use client";

// Cierre financiero (propietario, 2026-10-16) — cuántos días puede anular una
// venta POS quien cancela pedidos. Pasado ese plazo (o con boleta/factura viva,
// comisión liquidada o stock de varias sucursales) la anula un superior.
//
// Se guarda sola, con su único campo. Cambiarla exige `settings.manage` además
// de `company.manage`; si ESTA persona puede lo dice el servidor (`canEdit`), y
// el servidor vuelve a comprobar los límites.

import { useState } from "react";
import { updateCompanyConfiguration, type CompanyConfiguration } from "../lib/internal-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";

const FIELD = "pos_cancel_simple_days";
const MIN_DAYS = 0;
const MAX_DAYS = 365;
const HINT =
  "Entre 0 y 365. Pasado este plazo, una venta POS sólo la anula un superior (administrador de la empresa). " +
  "Con 0, toda anulación de caja la hace un superior.";

type Props = {
  companyId: number | null;
  days: number;
  canEdit: boolean;
  onSaved: (config: CompanyConfiguration) => void;
};

export function PosCancelSettings({ companyId, days, canEdit, onSaved }: Props) {
  const [draft, setDraft] = useState(String(days));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaved(false);
    setError(null);
    setFieldError(null);
    const typed = draft.trim();
    const value = Number(typed);
    if (!/^\d+$/.test(typed) || value < MIN_DAYS || value > MAX_DAYS) {
      setFieldError(`Indica un número entero de días entre ${MIN_DAYS} y ${MAX_DAYS}.`);
      return;
    }
    setSaving(true);
    try {
      const updated = await updateCompanyConfiguration(companyId, { [FIELD]: value });
      onSaved(updated);
      setDraft(String(updated.settings.pos_cancel_simple_days ?? value));
      setSaved(true);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      if (fields?.[FIELD]) setFieldError(fields[FIELD]);
      else setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="max-w-xs">
        <label
          className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
          htmlFor={`cfg-${FIELD}`}
        >
          Anulación simple de ventas POS (días)
        </label>
        <input
          id={`cfg-${FIELD}`}
          type="number"
          inputMode="numeric"
          step={1}
          min={MIN_DAYS}
          max={MAX_DAYS}
          className={internalInputClass}
          value={draft}
          disabled={!canEdit || saving}
          aria-invalid={Boolean(fieldError)}
          aria-describedby={`cfg-${FIELD}-hint`}
          onChange={(e) => { setDraft(e.target.value); setSaved(false); }}
        />
        <p id={`cfg-${FIELD}-hint`} className={`mt-1.5 text-[11px] ${fieldError ? "text-danger" : "text-muted"}`}>
          {fieldError ?? HINT}
        </p>
      </div>
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={saving} className={internalButtonClass}>
            {saving ? "Guardando…" : "Guardar plazo de anulación"}
          </button>
          {saved ? <span role="status" className="text-sm text-muted">Cambios guardados.</span> : null}
        </div>
      ) : (
        <p className="text-xs text-muted">
          Puedes ver este plazo pero no cambiarlo. Se requieren las capacidades{" "}
          <code className="text-foreground/85">company.manage</code> y{" "}
          <code className="text-foreground/85">settings.manage</code>, con acceso a toda la empresa.
        </p>
      )}
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </div>
  );
}
