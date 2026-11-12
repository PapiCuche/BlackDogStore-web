"use client";

// INV-04 — the operator's view of a paid order whose stock could not cover
// every line, and the action that resolves it. Reading the shortfall never
// touches payment_error (the derived server value is the authority); the retry
// creates only the missing SALE_EXITs and never repones inventory.
//
// F1 · DELIVERY-SHORTFALL-GUARD — while it is short the server refuses «Listo
// para retiro», «Enviado» and «Entregado» (409 `stock_shortfall`), and the
// dispatch panel lists that blocker under the next step. This panel says so up
// front, near the top of the order, and tells whoever cannot reprocess who can.

import { useState } from "react";
import {
  AdminOrderDetail,
  StockShortfallLine,
  reprocessOrderStockExit,
} from "../../lib/admin";

type Props = {
  orderId: number;
  shortfall: StockShortfallLine[];
  /** Server-decided: a shortfall to resolve AND inventory-move authority. */
  canReprocess: boolean;
  /** Given the server's recomputed detail after a retry. Never faked here. */
  onResolved: (updated: AdminOrderDetail) => void;
};

export function StockShortfallPanel({ orderId, shortfall, canReprocess, onResolved }: Props) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleReprocess() {
    if (loading) return;  // no double submit
    if (!window.confirm(
      "¿Reintentar la salida de stock de este pedido? Se descontarán solo las " +
      "unidades que aún faltan, de la sucursal de despacho. No repone inventario."
    )) return;
    setLoading(true);
    setMessage(null);
    setError(null);
    try {
      const updated = await reprocessOrderStockExit(orderId);
      const created = updated.created_movements ?? 0;
      const remaining = updated.stock_shortfall.length;
      setMessage(
        remaining === 0
          ? `Salida completada: ${created} movimiento(s) creado(s). El pedido ya no tiene faltante.`
          : `Se crearon ${created} movimiento(s); todavía faltan ${remaining} producto(s) por stock insuficiente.`,
      );
      onResolved(updated);  // the server's recomputed detail
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al reprocesar la salida de stock.");
    } finally {
      setLoading(false);
    }
  }

  // Nothing to show once the order is fully covered — but a just-completed
  // retry keeps its confirmation visible even after the shortfall clears.
  if (shortfall.length === 0) {
    return message ? (
      <p data-testid="reprocess-feedback" className="text-success text-sm">{message}</p>
    ) : null;
  }

  // F1: say only what is true — the reprocess completes a line only when none
  // of its product has left yet (`reprocessable`, decided by the server).
  const stuck = shortfall.filter((line) => line.reprocessable === false);
  const chosen = shortfall.filter((line) => line.chosen_unit && line.reprocessable !== false);
  const restockable = shortfall.some((line) => !line.chosen_unit && line.reprocessable !== false);
  const reprocessHelps = stuck.length < shortfall.length;

  return (
    <section
      data-testid="stock-shortfall-section"
      aria-labelledby="stock-shortfall-title"
      className="rounded-xl border border-warning-border bg-warning-surface p-6"
    >
      <h2 id="stock-shortfall-title" className="text-sm font-semibold text-warning mb-1">
        Faltante de stock · entrega bloqueada
      </h2>
      <p className="text-xs text-warning/80 mb-2">
        Este pedido está pagado, pero no había stock suficiente para descontar
        todas las unidades vendidas.
        {restockable ? " Repón el stock en la sucursal de despacho y reintenta la salida." : ""}
      </p>
      {stuck.length > 0 && (
        <p data-testid="stock-shortfall-stuck" className="text-xs text-foreground mb-2">
          De {stuck.map((line) => `«${line.product_name}»`).join(", ")} ya salió una parte del stock y
          reintentar la salida no la completa: cancela el pedido con «Cancelar pedido» y registra el reembolso.
        </p>
      )}
      {chosen.length > 0 && (
        <p data-testid="stock-shortfall-chosen" className="text-xs text-foreground mb-2">
          El equipo elegido por el cliente ya no está disponible y reponer otro equipo no lo resuelve.
          Si ese mismo equipo vuelve a estar libre, reintenta la salida; si no, cancela el pedido con
          «Cancelar pedido» y registra el reembolso.
        </p>
      )}
      <p data-testid="stock-shortfall-handover" className="text-xs font-medium text-foreground mb-4">
        Mientras falte stock no se puede marcar «Listo para retiro», «Enviado» ni «Entregado».
        {canReprocess || !reprocessHelps
          ? ""
          : " Pide a alguien con permiso para ajustar inventario que reintente la salida de stock."}
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-warning-border text-warning/80">
            <th className="text-left pb-2 pr-4 font-medium">Producto</th>
            <th className="text-right pb-2 pr-4 font-medium">Pedido</th>
            <th className="text-right pb-2 pr-4 font-medium">Atendido</th>
            <th className="text-right pb-2 font-medium">Faltante</th>
          </tr>
        </thead>
        <tbody>
          {shortfall.map((line) => (
            <tr key={line.product_id} className="border-b border-warning-border/40">
              <td className="py-2.5 pr-4 text-foreground">
                {line.product_name}
                {line.chosen_unit && <span className="ml-1.5 text-[10px] text-muted">(Equipo elegido)</span>}
              </td>
              <td className="py-2.5 pr-4 text-right text-muted">{line.ordered}</td>
              <td className="py-2.5 pr-4 text-right text-muted">{line.fulfilled}</td>
              <td className="py-2.5 text-right font-semibold text-warning">{line.shortfall}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canReprocess && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            data-testid="reprocess-stock-exit-button"
            onClick={handleReprocess}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-warning-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? "Reprocesando…" : "Reintentar salida de stock"}
          </button>
          {message && <p data-testid="reprocess-feedback" className="text-foreground text-xs">{message}</p>}
          {error && <p className="text-danger text-xs">{error}</p>}
        </div>
      )}
    </section>
  );
}
