"use client";

// Register a MANUAL stock entry or exit. Phase 6.0, per branch from Phase 2D.
//
// The backend is the source of truth: it re-reads stock under a row lock and
// rejects anything that would go negative. This form only pre-warns the operator,
// and its warning is deliberately approximate — the product list carries the
// company-wide aggregate, while the movement lands in ONE branch. The
// authoritative refusal comes from the server.
//
// There is no "todas las sucursales" option and there will not be one: units are
// added to a place, not to a set.

import { useState } from "react";
import {
  MANUAL_MOVEMENT_TYPES,
  createStockMovement,
  type MovementType,
  type StockMovement,
} from "../../lib/inventory";
import { ErrorBox } from "./InventoryUi";

type ProductOption = { id: number; name: string; inventory: number };
type BranchOption = { id: number; name: string };

type Props = {
  products: ProductOption[];
  branches?: BranchOption[];
  defaultBranch?: number | null;
  onCreated?: (movement: StockMovement) => void;
};

export function StockMovementForm({
  products,
  branches = [],
  defaultBranch = null,
  onCreated,
}: Props) {
  const [productId, setProductId] = useState<string>("");
  const [branchId, setBranchId] = useState<string>(
    defaultBranch === null ? "" : String(defaultBranch),
  );
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

  // Company-wide check only: if the whole company does not hold enough, no
  // branch does either. The per-branch answer belongs to the server.
  const wouldGoNegative =
    isExit && selected !== undefined && qtyValid && selected.inventory - qty < 0;

  // A branch is required whenever the operator can reach more than one. With a
  // single branch the field is not rendered and the backend fills in their
  // default, which is that same branch.
  const branchRequired = branches.length > 1;
  const canSubmit =
    !submitting &&
    productId !== "" &&
    (!branchRequired || branchId !== "") &&
    qtyValid &&
    reason.trim().length > 0 &&
    !wouldGoNegative;

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
        branch: branchId === "" ? undefined : Number(branchId),
        movement_type: movementType,
        quantity: qty,
        reason: reason.trim(),
      });
      setSuccess(
        `Movimiento registrado en ${movement.branch_name}: ` +
          `${movement.movement_type_label} · ` +
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
    "w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground " +
    "outline-none transition focus:border-bd-border disabled:opacity-50";
  const labelClass = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

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

        {branches.length > 1 ? (
          <div>
            <label className={labelClass} htmlFor="sm-branch">Sucursal</label>
            <select
              id="sm-branch"
              className={fieldClass}
              value={branchId}
              onChange={(e) => setBranchId(e.target.value)}
              disabled={submitting}
            >
              <option value="">Selecciona una sucursal…</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          </div>
        ) : null}

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
            <p className="mt-1 text-xs text-muted">
              Stock de la empresa: {selected.inventory} u. El stock de la sucursal
              se valida en el servidor.
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
        <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
          <p className="text-sm text-foreground">{success}</p>
        </div>
      ) : null}

      {branchRequired && branchId === "" ? (
        <p className="text-xs text-muted">Selecciona la sucursal donde ocurre el movimiento.</p>
      ) : null}

      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {submitting ? "Registrando…" : "Registrar movimiento"}
      </button>
    </form>
  );
}
