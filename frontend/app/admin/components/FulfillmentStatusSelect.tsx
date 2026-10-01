"use client";

import { useState } from "react";
import { FULFILLMENT_STATUS_OPTIONS, updateOrderFulfillment } from "../../lib/admin";
import type { AuthUser } from "../../lib/auth";
import { internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const INVENTORY_ALLOWED = new Set(["preparing", "ready_for_pickup", "shipped", "delivered"]);

type Props = {
  orderId: number;
  current: string;
  currentUser: AuthUser;
  onChanged: (newStatus: string) => void;
};

export function FulfillmentStatusSelect({ orderId, current, currentUser, onChanged }: Props) {
  const [value, setValue] = useState(current);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const isInventory = currentUser.role === "inventory";
  const options = isInventory
    ? FULFILLMENT_STATUS_OPTIONS.filter((o) => INVENTORY_ALLOWED.has(o.value))
    : FULFILLMENT_STATUS_OPTIONS;

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
          className={internalInputClass}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
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
          className={`min-w-48 flex-1 ${internalInputClass}`}
        />
        <button
          onClick={handleSave}
          disabled={!hasChanged || saving}
          className={internalPrimaryButtonClass}
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
      </div>
      {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
      {success && <p role="status" className="text-sm text-muted">Estado de despacho actualizado.</p>}
    </div>
  );
}
