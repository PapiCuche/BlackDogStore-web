"use client";

// DEVICE-COMMERCE — how long a serialised device stays set aside for a buyer,
// and whether a web buyer may ask the supplier for it although the shelf has it.
//
// Saved on their own, with only their four fields. Changing them takes
// `settings.manage` as well as `company.manage`; whether THIS person may is the
// server's answer (`canEdit`), and the server checks the bounds again.

import { useState } from "react";
import { updateCompanyConfiguration, type CompanyConfiguration } from "../lib/internal-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";

type Props = {
  companyId: number | null;
  cartMinutes: number;
  checkoutMinutes: number;
  ceilingMinutes: number;
  overStock: boolean;
  canEdit: boolean;
  /** Given the configuration the server answered with after a save. */
  onSaved: (config: CompanyConfiguration) => void;
};

type MinutesField = "device_cart_hold_minutes" | "device_checkout_hold_minutes" | "device_hold_ceiling_minutes";

const MINUTE_FIELDS: { name: MinutesField; label: string; hint: string }[] = [
  {
    name: "device_cart_hold_minutes",
    label: "Carrito (minutos)",
    hint: "Entre 5 y 60. Cuánto queda apartado un equipo mientras está en el carrito.",
  },
  {
    name: "device_checkout_hold_minutes",
    label: "Checkout / pago (minutos)",
    hint: "Entre 30 y 120. Nunca menos de 30: el formulario de pago dura 30 minutos.",
  },
  {
    name: "device_hold_ceiling_minutes",
    label: "Tope total (minutos)",
    hint: "Entre 35 y 240, y al menos el carrito más 30 minutos.",
  },
];

const FIELD_NAMES = [...MINUTE_FIELDS.map((f) => f.name), "web_on_order_over_stock"];

export function DeviceHoldSettings({
  companyId, cartMinutes, checkoutMinutes, ceilingMinutes, overStock, canEdit, onSaved,
}: Props) {
  const [minutes, setMinutes] = useState<Record<MinutesField, string>>({
    device_cart_hold_minutes: String(cartMinutes),
    device_checkout_hold_minutes: String(checkoutMinutes),
    device_hold_ceiling_minutes: String(ceilingMinutes),
  });
  const [overStockDraft, setOverStockDraft] = useState(overStock);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function save() {
    setSaved(false);
    setError(null);
    setFieldErrors({});

    const parsed: Partial<Record<MinutesField, number>> = {};
    const local: Record<string, string> = {};
    for (const { name } of MINUTE_FIELDS) {
      const typed = minutes[name].trim();
      if (!/^\d+$/.test(typed)) local[name] = "Indica un número entero de minutos.";
      else parsed[name] = Number(typed);
    }
    if (Object.keys(local).length) {
      setFieldErrors(local);
      return;
    }

    setSaving(true);
    try {
      const updated = await updateCompanyConfiguration(companyId, {
        device_cart_hold_minutes: parsed.device_cart_hold_minutes!,
        device_checkout_hold_minutes: parsed.device_checkout_hold_minutes!,
        device_hold_ceiling_minutes: parsed.device_hold_ceiling_minutes!,
        web_on_order_over_stock: overStockDraft,
      });
      onSaved(updated);
      const s = updated.settings;
      setMinutes({
        device_cart_hold_minutes: String(s.device_cart_hold_minutes ?? parsed.device_cart_hold_minutes),
        device_checkout_hold_minutes: String(s.device_checkout_hold_minutes ?? parsed.device_checkout_hold_minutes),
        device_hold_ceiling_minutes: String(s.device_hold_ceiling_minutes ?? parsed.device_hold_ceiling_minutes),
      });
      setOverStockDraft(Boolean(s.web_on_order_over_stock));
      setSaved(true);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      const mine = fields
        ? Object.fromEntries(Object.entries(fields).filter(([k]) => FIELD_NAMES.includes(k)))
        : {};
      if (Object.keys(mine).length) {
        setFieldErrors(mine);
        // A rule that ties two fields together may come back without a field.
        const general = fields?.non_field_errors;
        if (general) setError(general);
      } else if (fields?.non_field_errors) {
        setError(fields.non_field_errors);
      } else {
        setError(err instanceof Error ? err.message : "No se pudo guardar.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">
        Un equipo con número de serie queda apartado para quien lo puso en el carrito o está pagando, y se
        libera solo cuando vence su plazo. Un apartado cuyo pago aún lo está confirmando la pasarela no se
        libera por estos plazos.
      </p>

      <div className="grid gap-4 sm:grid-cols-3">
        {MINUTE_FIELDS.map(({ name, label, hint }) => {
          const fieldError = fieldErrors[name];
          return (
            <div key={name}>
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
                htmlFor={`cfg-${name}`}
              >
                {label}
              </label>
              <input
                id={`cfg-${name}`}
                type="number"
                inputMode="numeric"
                step={1}
                className={internalInputClass}
                value={minutes[name]}
                disabled={!canEdit || saving}
                aria-invalid={Boolean(fieldError)}
                aria-describedby={`cfg-${name}-hint`}
                onChange={(e) => {
                  const value = e.target.value;
                  setMinutes((current) => ({ ...current, [name]: value }));
                  setSaved(false);
                }}
              />
              <p id={`cfg-${name}-hint`} className={`mt-1.5 text-[11px] ${fieldError ? "text-danger" : "text-muted"}`}>
                {fieldError ?? hint}
              </p>
            </div>
          );
        })}
      </div>

      <div>
        <label className="inline-flex cursor-pointer select-none items-start gap-2 text-sm text-foreground/85">
          <input
            type="checkbox"
            className="mt-0.5 accent-warning"
            checked={overStockDraft}
            disabled={!canEdit || saving}
            aria-invalid={Boolean(fieldErrors.web_on_order_over_stock)}
            onChange={(e) => { setOverStockDraft(e.target.checked); setSaved(false); }}
          />
          Permitir que el comprador web elija proveedor aunque haya stock
        </label>
        <p className={`mt-1.5 text-[11px] ${fieldErrors.web_on_order_over_stock ? "text-danger" : "text-muted"}`}>
          {fieldErrors.web_on_order_over_stock ??
            "Apagado por defecto. Activado, la tienda ofrece, junto al stock propio, pedirlo al proveedor: al comprador se le indica que se despacha desde el proveedor, con su plazo de entrega."}
        </p>
      </div>

      {canEdit ? (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void save()} disabled={saving} className={internalButtonClass}>
            {saving ? "Guardando…" : "Guardar apartados y venta bajo pedido"}
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
