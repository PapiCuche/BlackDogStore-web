"use client";

/**
 * POS-SVC-01 — what the till is doing right now: selling products, or taking a
 * device in for technical service.
 *
 * They are two different things on purpose. A service order is NOT a sale: it
 * creates no order, moves no stock, charges nothing and issues no receipt. That
 * is why this is a switch between two screens and not a line in the basket.
 */

import {
  CAP_ORDERS_CREATE,
  CAP_ORDERS_VIEW,
  mayAssignTechnician,
} from "../../../lib/service-console";

export type PosMode = "products" | "service";

const INTAKE_CAPABILITIES = [
  CAP_ORDERS_VIEW, CAP_ORDERS_CREATE, "service.customers.view", "service.devices.view",
];

/**
 * From the till the technician is mandatory, so the entry is offered only to
 * whoever may both receive a device and say who gets it. A courtesy: the server
 * checks each of these again on the request.
 */
export function mayOpenServiceFromTill(capabilities: string[]): boolean {
  return INTAKE_CAPABILITIES.every((cap) => capabilities.includes(cap))
    && mayAssignTechnician((cap) => capabilities.includes(cap));
}

const MODES: { value: PosMode; label: string }[] = [
  { value: "products", label: "Productos" },
  { value: "service", label: "Servicio técnico" },
];

export function PosModeSwitch({ mode, onChange, capabilities }: {
  mode: PosMode; onChange: (mode: PosMode) => void; capabilities: string[];
}) {
  if (!mayOpenServiceFromTill(capabilities)) return null;
  return (
    <div role="group" aria-label="Tipo de operación" className="flex flex-wrap items-center gap-2">
      {MODES.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={mode === option.value}
          onClick={() => onChange(option.value)}
          className={`rounded-lg border px-4 py-2 text-sm transition ${
            mode === option.value
              ? "border-bd-border bg-surface-2 text-foreground"
              : "border-bd-border text-muted hover:text-foreground/85"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
