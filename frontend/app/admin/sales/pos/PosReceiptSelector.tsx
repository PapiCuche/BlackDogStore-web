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

/**
 * SALES-UX-02 — what KIND of document the operator is about to hand over, said out loud. An internal note must
 * never read as a tax receipt, and a tax receipt must say it is still BETA while SUNAT production is off.
 */
function DocumentNature({ value, options }: { value: string; options: ReceiptOption[] }) {
  const fiscal = options.filter((option) => option.value !== 'sales_note');
  const fiscalOff = fiscal.length > 0 && fiscal.every((option) => option.disabled_code === 'FISCAL_DISABLED');
  if (value === 'sales_note') {
    return (
      <div role="note" data-document-kind="internal" className="mt-3 rounded-lg border border-bd-border bg-background p-3 text-xs text-muted">
        <span className="mb-1 inline-block rounded bg-warning-surface px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-warning">
          Interno · no fiscal
        </span>
        <p>Documento interno sin validez tributaria. No es un comprobante de pago ante SUNAT: si el cliente lo necesita, emite una boleta o una factura.</p>
      </div>
    );
  }
  if (value) {
    return (
      <div role="note" data-document-kind="fiscal" className="mt-3 rounded-lg border border-bd-border bg-background p-3 text-xs text-muted">
        <span className="mb-1 inline-block rounded bg-info-surface px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-info">
          Comprobante electrónico · BETA
        </span>
        <p>
          Solo BETA: se prepara y firma el documento; enviar y consultar la aceptación son pasos posteriores. Factura requiere un
          cliente con RUC. Un descuento se declara en el comprobante.
        </p>
      </div>
    );
  }
  return (
    <div role="note" data-document-kind="none" className="mt-3 rounded-lg border border-bd-border bg-background p-3 text-xs text-muted">
      <ul className="space-y-1">
        <li><span className="font-medium text-foreground">Nota de venta interna:</span> control interno de la tienda; no es un comprobante fiscal.</li>
        <li>
          <span className="font-medium text-foreground">Boleta y factura electrónicas{fiscalOff ? ': no operativas' : fiscal.length === 0 ? ': no disponibles' : ''}:</span>{' '}
          {fiscalOff ? 'la emisión electrónica está apagada en este servidor.'
            : fiscal.length === 0 ? 'no disponibles para tu cuenta.' : 'comprobantes fiscales (hoy en BETA).'}
        </li>
      </ul>
    </div>
  );
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
      <DocumentNature value={value} options={options} />
    </div>
  );
}
