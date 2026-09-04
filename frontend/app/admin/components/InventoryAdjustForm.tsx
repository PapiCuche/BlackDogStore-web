"use client";

import { useState } from "react";
import { adjustInventory } from "../../lib/admin";

type Props = {
  productId: number;
  currentInventory: number;
  onAdjusted: (newInventory: number) => void;
};

export function InventoryAdjustForm({
  productId,
  currentInventory,
  onAdjusted,
}: Props) {
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const parsedDelta = parseInt(delta, 10);
  const preview =
    !isNaN(parsedDelta) && parsedDelta !== 0
      ? currentInventory + parsedDelta
      : null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (isNaN(parsedDelta) || parsedDelta === 0) {
      setError("El delta debe ser un entero distinto de 0.");
      return;
    }
    if (!reason.trim() || reason.trim().length < 3) {
      setError("El motivo debe tener al menos 3 caracteres.");
      return;
    }

    setSaving(true);
    try {
      const updated = await adjustInventory(productId, parsedDelta, reason.trim());
      onAdjusted(updated.inventory);
      setDelta("");
      setReason("");
      setSuccess(`Inventario actualizado a ${updated.inventory} unidades.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al ajustar inventario.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex gap-4">
        <div className="flex-1">
          <label htmlFor="mponents-inventoryadjustform-delta-positivo-ingreso-negativo-salida" className="block text-xs text-muted mb-1.5">
            Delta (positivo = ingreso, negativo = salida)
          </label>
          <input id="mponents-inventoryadjustform-delta-positivo-ingreso-negativo-salida"
            type="number"
            value={delta}
            onChange={(e) => setDelta(e.target.value)}
            placeholder="ej. +5 o -3"
            disabled={saving}
            className="w-full bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground placeholder-muted focus:outline-none focus:border-bd-border disabled:opacity-50"
          />
          {preview !== null && (
            <p className="text-xs mt-1.5 text-muted">
              Resultado:{" "}
              <span
                className={
                  preview < 0
                    ? "text-red-400"
                    : preview === 0
                      ? "text-yellow-400"
                      : "text-foreground"
                }
              >
                {preview} unidades
              </span>
            </p>
          )}
        </div>
        <div className="flex-[2]">
          <label htmlFor="mponents-inventoryadjustform-motivo" className="block text-xs text-muted mb-1.5">Motivo</label>
          <input id="mponents-inventoryadjustform-motivo"
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="ej. Restock de proveedor"
            maxLength={500}
            disabled={saving}
            className="w-full bg-surface border border-bd-border rounded px-3 py-2 text-sm text-foreground placeholder-muted focus:outline-none focus:border-bd-border disabled:opacity-50"
          />
        </div>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      {success && <p className="text-sm text-foreground/85">{success}</p>}

      <button
        type="submit"
        disabled={saving}
        className="px-4 py-2 bg-foreground text-background text-sm font-medium rounded hover:bg-foreground/90 disabled:opacity-50 transition-colors"
      >
        {saving ? "Guardando…" : "Aplicar ajuste"}
      </button>
    </form>
  );
}
