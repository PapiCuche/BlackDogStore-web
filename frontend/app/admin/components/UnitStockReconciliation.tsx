"use client";

/**
 * INV-LOAD-02 · «Conciliación» del stock de equipos.
 *
 * Por sucursal y producto con número de serie, el stock contra los equipos disponibles: sólo los que NO cuadran.
 * Es una lectura (el servidor no corrige nada) y llega sólo a las sucursales de quien la pide. Es la misma
 * comprobación que `manage.py check_unit_stock` en el procedimiento de la carga real.
 */

import { useState } from "react";

import { fetchUnitReconciliation, type UnitStockMismatch } from "@/app/lib/stock-units";

import { Panel, TableWrap, Td, Th } from "./InventoryUi";
import { internalButtonClass } from "./internal-ui";

export function UnitStockReconciliation() {
  const [result, setResult] = useState<{ checked: number; mismatches: UnitStockMismatch[] } | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    setWorking(true);
    setError(null);
    try {
      setResult(await fetchUnitReconciliation());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "No se pudo comprobar el stock.");
    } finally {
      setWorking(false);
    }
  }

  return (
    <Panel
      title="Conciliación de equipos"
      description="Comprueba que el stock de cada producto con número de serie sea igual a sus equipos disponibles."
      action={
        <button type="button" className={internalButtonClass} disabled={working} onClick={check}>
          {working ? "Comprobando…" : "Comprobar ahora"}
        </button>
      }
    >
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {result && result.mismatches.length === 0 ? (
        <p role="status" className="text-sm text-success">
          Todo cuadra: {result.checked} {result.checked === 1 ? "estante comprobado" : "estantes comprobados"}.
        </p>
      ) : null}
      {result && result.mismatches.length > 0 ? (
        <div className="space-y-2">
          <p role="status" className="text-sm text-danger">
            {result.mismatches.length} {result.mismatches.length === 1 ? "estante no cuadra" : "estantes no cuadran"}.
            No se corrigió nada: revisa el Kardex de cada uno antes de tocarlo.
          </p>
          <TableWrap>
            <caption className="sr-only">Estantes que no cuadran</caption>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Sucursal</Th><Th>Producto</Th><Th right>Stock</Th><Th right>Disponibles</Th><Th right>Diferencia</Th>
              </tr>
            </thead>
            <tbody>
              {result.mismatches.map((row) => (
                <tr key={`${row.branch_id}-${row.product_id}`} className="border-b border-bd-border">
                  <Td>{row.branch}</Td>
                  <Td>{row.product}</Td>
                  <Td right>{row.quantity}</Td>
                  <Td right>{row.available}</Td>
                  <Td right>{row.difference > 0 ? `+${row.difference}` : row.difference}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </div>
      ) : null}
    </Panel>
  );
}
