"use client";

/**
 * DEVICE-COMMERCE-01 · DC3 — whether a product may be sold «bajo pedido».
 *
 * «Sólo stock propio» sells what is on the shelf. «Stock o bajo pedido» also
 * sells against a current supplier offer when the shelf runs out. Changing it
 * needs `products.manage`; the server refuses it for a used variant (each used
 * device is one of a kind) and says so.
 */

import { useState } from "react";

import {
  PRODUCT_SALE_MODE_LABELS,
  updateProductSaleMode,
  type ProductSaleMode,
} from "../lib/internal-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";

const MODES: ProductSaleMode[] = ["stock", "stock_or_order"];

export function ProductSaleModeControl({
  productId, saleMode, canManage, companyId, onSaved,
}: {
  productId: number;
  saleMode: ProductSaleMode | undefined;
  canManage: boolean;
  companyId?: number | null;
  onSaved?: (saleMode: ProductSaleMode) => void;
}) {
  const current: ProductSaleMode = saleMode ?? "stock";
  const [chosen, setChosen] = useState<ProductSaleMode>(current);
  const [saved, setSaved] = useState<ProductSaleMode>(current);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await updateProductSaleMode(productId, chosen, companyId);
      const next = result?.sale_mode ?? chosen;
      setSaved(next);
      setChosen(next);
      setNotice("Modo de venta guardado.");
      onSaved?.(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cambiar el modo de venta.");
      setChosen(saved);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-xl border border-bd-border bg-surface p-5 sm:p-6" aria-labelledby={`sale-mode-${productId}`}>
      <h2 id={`sale-mode-${productId}`} className="text-sm font-semibold text-foreground">Modo de venta</h2>
      <p className="mt-1 mb-4 text-xs text-muted">
        Con «Stock o bajo pedido», cuando se acaba el stock propio se puede vender contra una oferta vigente de un proveedor, a su precio y con su plazo de entrega.
      </p>
      {canManage ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="block min-w-56 text-xs text-muted">
            Cómo se vende
            <select
              value={chosen}
              onChange={(e) => { setChosen(e.target.value as ProductSaleMode); setNotice(null); setError(null); }}
              className={`mt-1.5 ${internalInputClass}`}
            >
              {MODES.map((mode) => <option key={mode} value={mode}>{PRODUCT_SALE_MODE_LABELS[mode]}</option>)}
            </select>
          </label>
          <button type="button" className={internalButtonClass} onClick={() => void save()} disabled={saving || chosen === saved}>
            {saving ? "Guardando…" : "Guardar modo"}
          </button>
        </div>
      ) : (
        <p className="text-sm text-foreground" data-testid="sale-mode-readonly">{PRODUCT_SALE_MODE_LABELS[saved]}</p>
      )}
      {error ? <p role="alert" className="mt-3 text-xs text-danger">{error}</p> : null}
      {notice ? <p role="status" className="mt-3 text-xs text-success">{notice}</p> : null}
    </section>
  );
}
