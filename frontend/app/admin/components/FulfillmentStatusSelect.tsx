"use client";

import { useState } from "react";
import { FULFILLMENT_STATUS_OPTIONS, updateOrderFulfillment } from "../../lib/admin";

type Props = {
  orderId: number;
  current: string;
  /**
   * The states this person may set, AS THE SERVER SAYS — the order detail's
   * `available_fulfillment_transitions` (H4.1.2).
   *
   * This component used to decide it from `user.role`, the global legacy label.
   * Inside a company the capability decides, so a panel carrying its own
   * role-keyed copy of the rule showed four options where the server allowed
   * seven. The PATCH is re-checked on the server regardless.
   */
  allowed: readonly string[];
  onChanged: (newStatus: string) => void;
};

export function FulfillmentStatusSelect({ orderId, current, allowed, onChanged }: Props) {
  const [value, setValue] = useState(current);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const allowedSet = new Set(allowed);
  // The order's current state stays listed even when this person could not set
  // it, so the control never shows the order somewhere it is not.
  const options = FULFILLMENT_STATUS_OPTIONS.filter(
    (o) => allowedSet.has(o.value) || o.value === current,
  );

  const hasChanged = value !== current;

  async function handleSave() {
    if (!hasChanged) return;
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      await updateOrderFulfillment(orderId, value, note);
      setSuccess(true);
      setNote("");
      onChanged(value);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar el estado.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <select
          value={value}
          onChange={(e) => { setValue(e.target.value); setSuccess(false); }}
          disabled={saving}
          aria-label="Estado de despacho"
          className="bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground focus:outline-none focus:border-bd-border disabled:opacity-50"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value} disabled={!allowedSet.has(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Nota opcional (auditoría)…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={500}
          disabled={saving}
          className="flex-1 min-w-48 bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground placeholder-muted focus:outline-none focus:border-bd-border disabled:opacity-50"
        />
        <button
          onClick={handleSave}
          disabled={!hasChanged || saving}
          className="px-4 py-2 bg-foreground text-background text-sm font-medium rounded hover:bg-foreground/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {success && <p className="text-sm text-muted">Estado de despacho actualizado.</p>}
    </div>
  );
}
