"use client";

/**
 * SUPPLIER-DEVICE-FLOW-02 — «Alertas operativas» on the dashboard.
 *
 * What of the paid orders is waiting for a person: the supplier, a promised
 * date, an expired offer, a line to confirm, a device to identify, goods that
 * arrived and are not delivered. Counts only; each one opens the orders list
 * filtered by that alert (`?alert=`), which the server answers with the same
 * question and the same scope (company, and the caller's branches).
 *
 * Rendered only for whoever may see orders; the endpoint decides anyway.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchSupplyAlerts, type SupplyAlert } from "../../lib/admin";

const TONE: Record<SupplyAlert["level"], string> = {
  critical: "border-danger-border bg-danger-surface",
  warning: "border-warning-border bg-warning-surface",
  info: "border-bd-border bg-surface",
};

export function SupplyAlertsCard() {
  const [alerts, setAlerts] = useState<SupplyAlert[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchSupplyAlerts().then(
      (body) => { if (alive) setAlerts(body.alerts); },
      (err) => { if (alive) setError(err instanceof Error ? err.message : "No se pudieron cargar las alertas operativas."); },
    );
    return () => { alive = false; };
  }, []);

  const active = (alerts ?? []).filter((alert) => alert.count > 0);

  return (
    <section aria-labelledby="supply-alerts-title" className="rounded-xl border border-bd-border bg-surface p-5">
      <h2 id="supply-alerts-title" className="text-sm font-semibold text-foreground">Alertas operativas</h2>
      <p className="mt-0.5 text-xs text-muted">Pedidos pagados que esperan a una persona: proveedor, fechas prometidas, equipos y entregas.</p>
      {error ? <p role="alert" className="mt-3 text-xs text-danger">{error}</p> : null}
      {!alerts && !error ? <p className="mt-3 text-xs text-muted">Cargando alertas…</p> : null}
      {alerts && active.length === 0 ? (
        <p className="mt-3 rounded-lg border border-dashed border-bd-border px-3 py-4 text-center text-xs text-muted">
          Nada esperando: sin pedidos atrasados ni pendientes de abastecer o entregar.
        </p>
      ) : null}
      {active.length > 0 ? (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {active.map((alert) => (
            <li key={alert.kind}>
              <Link
                href={alert.href}
                data-testid={`supply-alert-${alert.kind}`}
                className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-sm transition hover:border-foreground/25 ${TONE[alert.level]}`}
              >
                <span className="min-w-0">
                  <span className="block font-medium text-foreground">{alert.label}</span>
                  {alert.kind === "promise_due" && alert.overdue ? (
                    <span className="block text-[11px] text-danger">
                      {alert.overdue === 1 ? "1 ya vencido" : `${alert.overdue} ya vencidos`}
                    </span>
                  ) : null}
                </span>
                <span className="font-display text-lg font-bold tabular-nums text-foreground">{alert.count}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
