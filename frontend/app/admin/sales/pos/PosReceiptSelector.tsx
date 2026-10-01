"use client";

import { useEffect } from "react";

/**
 * Una opción del selector. `enabled`, `disabled_code` y `disabled_reason` son
 * opcionales para tolerar un contexto anterior al contrato de ERP-FISCAL-6:
 * sin ellos, la opción se considera habilitada donde `branches` la liste.
 */
export type ReceiptOption = {
  value: string;
  label: string;
  branches: number[];
  enabled?: boolean;
  disabled_code?: string;
  disabled_reason?: string;
};

/**
 * ¿Se puede elegir ESTA opción para ESTA sucursal?
 *
 * Dos condiciones, y las dos las dicta el backend: la opción está habilitada en
 * algún sitio y esta sucursal está entre los sitios. Una serie que existe en
 * otra sucursal no habilita la actual. Esto no es autoridad —el servidor vuelve
 * a decidir al cobrar—, es no ofrecer lo que va a devolver un error.
 */
export function isSelectable(option: ReceiptOption, branch: number | null): boolean {
  return option.enabled !== false && branch !== null && option.branches.includes(branch);
}

function unavailabilityReason(option: ReceiptOption, branch: number | null): string {
  if (option.enabled === false) return option.disabled_reason || "No disponible.";
  if (branch === null) return "Selecciona una sucursal.";
  return "No disponible en esta sucursal.";
}

export function PosReceiptSelector({ options, branch, value, onChange, disabled = false }: {
  options: ReceiptOption[]; branch: number | null; value: string;
  onChange: (value: string) => void; disabled?: boolean;
}) {
  // Si lo elegido deja de poder elegirse —cambió la sucursal, llegó otro
  // contexto—, se suelta. Un comprobante que esta sucursal no puede emitir no
  // puede quedarse marcado esperando a que el servidor lo rechace.
  useEffect(() => {
    if (!value) return;
    const stillValid = options.some((option) => option.value === value && isSelectable(option, branch));
    if (!stillValid) onChange("");
  }, [value, branch, options, onChange]);

  const unavailable = options.filter((option) => !isSelectable(option, branch));

  return (
    <div>
      <label className="block text-xs text-muted">
        Tipo de comprobante
        <select className="mt-2 w-full rounded-lg border border-bd-border bg-surface p-2 text-foreground"
          value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
          <option value="">Selecciona un documento</option>
          {options.map((option) => {
            const selectable = isSelectable(option, branch);
            return (
              <option key={option.value} value={option.value} disabled={!selectable}>
                {selectable ? option.label : `${option.label} — no disponible`}
              </option>
            );
          })}
        </select>
      </label>
      {unavailable.length ? (
        <ul className="mt-2 space-y-1 text-xs text-warning" aria-label="Documentos no disponibles">
          {unavailable.map((option) => (
            <li key={option.value}>
              <span className="font-medium">{option.label}:</span> {unavailabilityReason(option, branch)}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 text-xs text-muted">
        {value === 'sales_note' ? 'Documento interno sin validez tributaria.' :
          value ? 'Solo BETA: se prepara y firma el documento; enviar y consultar la aceptación son pasos posteriores. Factura requiere un cliente con RUC. Un descuento se declara en el comprobante.' :
            'Se muestran los documentos habilitados para tu cuenta y sucursal.'}
      </p>
    </div>
  );
}
