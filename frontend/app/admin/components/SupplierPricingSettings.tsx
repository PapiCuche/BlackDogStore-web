"use client";

// SUPPLY-MARGIN-01 (propietario) — «Precio sugerido: costo más margen configurable por
// empresa, presentado sólo como propuesta revisable. No cambiar automáticamente los
// precios publicados.»
//
// Dos campos y su propio guardado. Sólo llegan (y esta sección sólo se muestra) a quien
// ve costos: margen + precio sugerido revelan el costo. Cambiarlos exige además
// `company.manage` y `settings.manage`; si ESTA persona puede lo dice el servidor
// (`canEdit`), y el servidor vuelve a comprobar los límites. El cálculo es del servidor.

import { useState } from "react";
import { updateCompanyConfiguration, type CompanyConfiguration } from "../lib/internal-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";

const MARGIN = "supplier_margin_percent";
const ROUND_TO = "supplier_price_round_to";
const MARGIN_MAX = 500;
const ROUND_TO_MAX = 100000;
const MARGIN_ERROR = "Indica un porcentaje entre 0 y 500, con hasta dos decimales.";
const ROUND_TO_ERROR = "Indica un número entero entre 1 y 100000, o déjalo vacío.";

/** The server sends the two keys only to `costs.view`: their presence decides whether the section exists. */
export function showsSupplierPricing(settings: object): boolean {
  return Object.prototype.hasOwnProperty.call(settings, MARGIN);
}

type Props = {
  companyId: number | null;
  margin: string | null;
  roundTo: number | null;
  canEdit: boolean;
  onSaved: (config: CompanyConfiguration) => void;
};

function marginValue(typed: string): string | null | undefined {
  const text = typed.trim();
  if (!text) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(text) || Number(text) > MARGIN_MAX) return undefined;
  return text;
}

function roundToValue(typed: string): number | null | undefined {
  const text = typed.trim();
  if (!text) return null;
  if (!/^\d+$/.test(text)) return undefined;
  const value = Number(text);
  return value >= 1 && value <= ROUND_TO_MAX ? value : undefined;
}

export function SupplierPricingSettings({ companyId, margin, roundTo, canEdit, onSaved }: Props) {
  const [marginDraft, setMarginDraft] = useState(margin ?? "");
  const [roundDraft, setRoundDraft] = useState(roundTo === null || roundTo === undefined ? "" : String(roundTo));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaved(false);
    setError(null);
    const marginSent = marginValue(marginDraft);
    const roundSent = roundToValue(roundDraft);
    const problems: Record<string, string> = {};
    if (marginSent === undefined) problems[MARGIN] = MARGIN_ERROR;
    if (roundSent === undefined) problems[ROUND_TO] = ROUND_TO_ERROR;
    setFieldErrors(problems);
    if (Object.keys(problems).length) return;
    setSaving(true);
    try {
      const updated = await updateCompanyConfiguration(companyId, {
        [MARGIN]: marginSent ?? null, [ROUND_TO]: roundSent ?? null,
      });
      onSaved(updated);
      const settings = updated.settings;
      if (settings && MARGIN in settings) setMarginDraft(settings.supplier_margin_percent ?? "");
      if (settings && ROUND_TO in settings) {
        setRoundDraft(settings.supplier_price_round_to == null ? "" : String(settings.supplier_price_round_to));
      }
      setSaved(true);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      if (fields && (fields[MARGIN] || fields[ROUND_TO])) {
        setFieldErrors({ ...(fields[MARGIN] ? { [MARGIN]: fields[MARGIN] } : {}),
          ...(fields[ROUND_TO] ? { [ROUND_TO]: fields[ROUND_TO] } : {}) });
      } else {
        setError(err instanceof Error ? err.message : "No se pudo guardar.");
      }
    } finally {
      setSaving(false);
    }
  }

  const field = (
    name: string, label: string, value: string, onChange: (value: string) => void, hint: string,
    extra: React.InputHTMLAttributes<HTMLInputElement>,
  ) => (
    <div className="max-w-xs">
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted" htmlFor={`cfg-${name}`}>
        {label}
      </label>
      <input
        id={`cfg-${name}`}
        className={internalInputClass}
        value={value}
        disabled={!canEdit || saving}
        aria-invalid={Boolean(fieldErrors[name])}
        aria-describedby={`cfg-${name}-hint`}
        onChange={(e) => { onChange(e.target.value); setSaved(false); }}
        {...extra}
      />
      <p id={`cfg-${name}-hint`} className={`mt-1.5 text-[11px] ${fieldErrors[name] ? "text-danger" : "text-muted"}`}>
        {fieldErrors[name] ?? hint}
      </p>
    </div>
  );

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">
        El precio sugerido de una oferta de proveedor es su costo pactado más este margen, redondeado hacia arriba.
        Es sólo una propuesta para revisar: nunca cambia un precio de oferta ni el precio publicado de un producto.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        {field(MARGIN, "Margen sobre el costo (%)", marginDraft, setMarginDraft,
          "Entre 0 y 500. Vacío: no se sugiere ningún precio.", { inputMode: "decimal" })}
        {field(ROUND_TO, "Redondear hacia arriba a múltiplos de", roundDraft, setRoundDraft,
          "Un número entero (1, 10, 50…). Vacío: al céntimo. 9 da múltiplos de 9, no precios terminados en 9.",
          { inputMode: "numeric" })}
      </div>
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={saving} className={internalButtonClass}>
            {saving ? "Guardando…" : "Guardar margen sugerido"}
          </button>
          {saved ? <span role="status" className="text-sm text-muted">Cambios guardados.</span> : null}
        </div>
      ) : (
        <p className="text-xs text-muted">
          Puedes ver este margen pero no cambiarlo. Se requieren las capacidades{" "}
          <code className="text-foreground/85">company.manage</code>,{" "}
          <code className="text-foreground/85">settings.manage</code> y{" "}
          <code className="text-foreground/85">costs.view</code>, con acceso a toda la empresa.
        </p>
      )}
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </div>
  );
}
