"use client";

// Register a MANUAL stock entry or exit. Phase 6.0.
// The backend is the source of truth: it re-reads stock under a row lock and
// rejects anything that would go negative. This form only pre-warns the operator.

import { useState } from "react";
import {
  MANUAL_MOVEMENT_TYPES,
  createStockMovement,
  type MovementType,
  type StockMovement,
} from "../../lib/inventory";
import { ErrorBox } from "./InventoryUi";

type ProductOption = { id: number; name: string; inventory: number };

type Props = {
  products: ProductOption[];
  onCreated?: (movement: StockMovement) => void;
};

export function StockMovementForm({ products, onCreated }: Props) {
  const [productId, setProductId] = useState<string>("");
  const [movementType, setMovementType] = useState<MovementType>("manual_entry");
  const [quantity, setQuantity] = useState<string>("1");
  const [reason, setReason] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const selected = products.find((p) => String(p.id) === productId);
  const typeMeta = MANUAL_MOVEMENT_TYPES.find((t) => t.value === movementType);
  const isExit = typeMeta ? !typeMeta.isEntry : false;
  const qty = parseInt(quantity, 10);
  const qtyValid = Number.isFinite(qty) && qty > 0;

  const wouldGoNegative =
    isExit && selected !== undefined && qtyValid && selected.inventory - qty < 0;

  const canSubmit =
    !submitting && productId !== "" && qtyValid && reason.trim().length > 0 && !wouldGoNegative;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    if (isExit) {
      const ok = window.confirm(
        `¿Confirmas registrar una SALIDA de ${qty} unidad(es) de "${selected?.name}"?\n\n` +
          "Esta acción queda registrada en el Kardex y en auditoría.",
      );
      if (!ok) return;
    }

    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const movement = await createStockMovement({
        product_id: Number(productId),
        movement_type: movementType,
        quantity: qty,
        reason: reason.trim(),
      });
      setSuccess(
        `Movimiento registrado: ${movement.movement_type_label} · ` +
          `stock ${movement.stock_before} → ${movement.stock_after}`,
      );
      setQuantity("1");
      setReason("");
      onCreated?.(movement);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo registrar el movimiento.");
    } finally {
      setSubmitting(false);
    }
  }

  const fieldClass =
    "w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 " +
    "outline-none transition focus:border-white/25 disabled:opacity-50";
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={labelClass} htmlFor="sm-product">Producto</label>
          <select
            id="sm-product"
            className={fieldClass}
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
            disabled={submitting}
          >
            <option value="">Selecciona un producto…</option>
            {products.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.inventory} u.
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className={labelClass} htmlFor="sm-type">Tipo de movimiento</label>
          <select
            id="sm-type"
            className={fieldClass}
            value={movementType}
            onChange={(e) => setMovementType(e.target.value as MovementType)}
            disabled={submitting}
          >
            <optgroup label="Entradas">
              {MANUAL_MOVEMENT_TYPES.filter((t) => t.isEntry).map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </optgroup>
            <optgroup label="Salidas">
              {MANUAL_MOVEMENT_TYPES.filter((t) => !t.isEntry).map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </optgroup>
          </select>
        </div>

        <div>
          <label className={labelClass} htmlFor="sm-qty">Cantidad</label>
          <input
            id="sm-qty"
            type="number"
            min={1}
            className={fieldClass}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            disabled={submitting}
          />
          {selected ? (
            <p className="mt-1 text-xs text-zinc-500">
              Stock actual: {selected.inventory} u.
              {isExit && qtyValid
                ? ` → quedaría ${selected.inventory - qty} u.`
                : ""}
            </p>
          ) : null}
        </div>

        <div>
          <label className={labelClass} htmlFor="sm-reason">Motivo (obligatorio)</label>
          <input
            id="sm-reason"
            type="text"
            maxLength={500}
            className={fieldClass}
            placeholder="Ej. Compra de stock a proveedor"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={submitting}
          />
        </div>
      </div>

      {wouldGoNegative ? (
        <ErrorBox message="Esta salida dejaría el stock en negativo. Reduce la cantidad." />
      ) : null}
      {error ? <ErrorBox message={error} /> : null}
      {success ? (
        <div className="rounded-lg border border-white/15 bg-white/[0.05] px-4 py-3">
          <p className="text-sm text-zinc-200">{success}</p>
        </div>
      ) : null}

      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black transition hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {submitting ? "Registrando…" : "Registrar movimiento"}
      </button>
    </form>
  );
}
