"use client";

/**
 * SERIAL-PICK-01 — los equipos de un pedido web, al entregarlo.
 *
 * El pago apartó un equipo; el que sale de la tienda lo identifica quien lo
 * entrega, con el lector o a mano. Hasta que estén todos, el pedido no se puede
 * marcar como enviado ni entregado, y ningún documento nombra un equipo.
 *
 * Un pedido sin productos con serie no muestra nada aquí.
 */

import { useState } from "react";
import {
  forgetOrderUnit, identifyOrderUnit, lookupOrderUnit,
  type AdminOrderDetail,
} from "../../lib/admin";
import { unitLabel } from "../lib/unit-entry";
import { UnitEntry } from "./UnitEntry";

type Props = {
  order: AdminOrderDetail;
  /** El pedido tal como quedó en el servidor tras identificar o quitar un equipo. */
  onChanged: (order: AdminOrderDetail) => void;
};

export function OrderUnitsPanel({ order, onChanged }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const lines = order.items.filter((item) => item.is_serialized);
  if (!lines.length) return null;

  const pending = order.units_pending ?? 0;
  const waiting = lines.filter((item) => (item.units_pending ?? 0) > 0);

  async function remove(recordId: number) {
    setRemoving(recordId);
    setError(null);
    try {
      onChanged(await forgetOrderUnit(order.id, recordId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo quitar ese equipo.");
    } finally {
      setRemoving(null);
    }
  }

  return (
    <div className="space-y-4">
      <p className={`text-sm ${pending ? "text-warning" : "text-muted"}`}>
        {pending
          ? `Falta identificar ${pending} equipo${pending === 1 ? "" : "s"}. Sin eso el pedido no se puede marcar como enviado ni entregado.`
          : "Todos los equipos de este pedido están identificados."}
      </p>

      <ul className="space-y-3">
        {lines.map((item) => (
          <li key={item.id} className="rounded-lg border border-bd-border p-3">
            <p className="text-sm font-semibold text-foreground">{item.product_name}</p>
            {(item.units ?? []).map((unit) => (
              <div key={unit.id} className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-mono text-foreground">{unitLabel(unit)}</span>
                <span className="text-muted">
                  {unit.method_label}{unit.identified_by ? ` · ${unit.identified_by}` : ""}
                </span>
                {order.units_can_change && unit.can_remove !== false ? (
                  <button
                    type="button"
                    disabled={removing === unit.id}
                    onClick={() => void remove(unit.id)}
                    aria-label={`Quitar el equipo ${unit.imei || unit.serial_number}`}
                    className="text-muted underline transition hover:text-danger"
                  >
                    Quitar
                  </button>
                ) : null}
              </div>
            ))}
            {item.units_pending ? (
              <p className="mt-2 text-xs text-muted">Por identificar: {item.units_pending}</p>
            ) : null}
          </li>
        ))}
      </ul>

      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}

      {pending > 0 && order.units_can_change ? (
        <UnitEntry
          requiresImei={waiting.length > 0 && waiting.every((item) => item.requires_imei)}
          lookup={(code, method) => lookupOrderUnit(order.id, code, method)}
          onPicked={async (unit, method) => {
            // Por su IMEI si lo tiene: es lo que el servidor exige para lo escrito a mano.
            onChanged(await identifyOrderUnit(order.id, unit.imei || unit.serial_number, method));
          }}
        />
      ) : null}
    </div>
  );
}
