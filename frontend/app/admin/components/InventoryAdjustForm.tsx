"use client";

import { useState } from "react";
import { adjustInventory } from "../../lib/admin";
import { internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

type Props = {
  productId: number;
  currentInventory: number;
  /** Branches the caller reaches. With more than one, the form asks which. */
  branches?: { id: number; name: string }[];
  defaultBranchId?: number | null;
  onAdjusted: (newInventory: number) => void;
};

export function InventoryAdjustForm({
  productId,
  currentInventory,
  branches = [],
  defaultBranchId = null,
  onAdjusted,
}: Props) {
  // DRIFT-02: the server takes `branch` and falls back to the caller's default
  // branch when it is missing. With several branches that fallback is a choice
  // nobody made, so the form asks; with one there is nothing to ask and the
  // request stays as it was.
  const asksBranch = branches.length > 1;
  const [branchId, setBranchId] = useState(
    defaultBranchId !== null && branches.some((b) => b.id === defaultBranchId)
      ? String(defaultBranchId)
      : "",
  );
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
    if (asksBranch && !branchId) {
      setError("Elige la sucursal donde se aplica el ajuste.");
      return;
    }

    setSaving(true);
    try {
      const updated = await adjustInventory(
        productId,
        parsedDelta,
        reason.trim(),
        asksBranch ? Number(branchId) : undefined,
      );
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
      {asksBranch ? (
        <div>
          <label htmlFor="inventory-adjust-branch" className="mb-1.5 block text-xs text-muted">
            Sucursal
          </label>
          <select
            id="inventory-adjust-branch"
            value={branchId}
            onChange={(e) => setBranchId(e.target.value)}
            disabled={saving}
            className={internalInputClass}
          >
            <option value="">Elige una sucursal</option>
            {branches.map((branch) => (
              <option key={branch.id} value={branch.id}>{branch.name}</option>
            ))}
          </select>
          <p className="mt-1.5 text-xs text-muted">
            El ajuste se registra en el Kardex de esta sucursal.
          </p>
        </div>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-[1fr_2fr]">
        <div className="flex-1">
          <label htmlFor="inventory-adjust-delta" className="mb-1.5 block text-xs text-muted">
            Delta (positivo = ingreso, negativo = salida)
          </label>
          <input
            id="inventory-adjust-delta"
            type="number"
            value={delta}
            onChange={(e) => setDelta(e.target.value)}
            placeholder="ej. +5 o -3"
            disabled={saving}
            className={internalInputClass}
          />
          {preview !== null && (
            <p className="text-xs mt-1.5 text-muted">
              {asksBranch ? "Total de la empresa tras el ajuste:" : "Resultado:"}{" "}
              <span
                className={
                  preview < 0
                    ? "text-danger"
                    : preview === 0
                      ? "text-warning"
                      : "text-foreground"
                }
              >
                {preview} unidades
              </span>
            </p>
          )}
        </div>
        <div className="flex-[2]">
          <label htmlFor="inventory-adjust-reason" className="mb-1.5 block text-xs text-muted">Motivo</label>
          <input
            id="inventory-adjust-reason"
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="ej. Restock de proveedor"
            maxLength={500}
            disabled={saving}
            className={internalInputClass}
          />
        </div>
      </div>

      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {success && <p role="status" className="text-sm text-muted">{success}</p>}

      <button
        type="submit"
        disabled={saving}
        className={internalPrimaryButtonClass}
      >
        {saving ? "Guardando…" : "Aplicar ajuste"}
      </button>
    </form>
  );
}
