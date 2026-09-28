"use client";

export type ReceiptOption = { value: string; label: string; branches: number[] };

export function PosReceiptSelector({ options, branch, value, onChange, disabled = false }: {
  options: ReceiptOption[]; branch: number | null; value: string;
  onChange: (value: string) => void; disabled?: boolean;
}) {
  return (
    <div>
      <label className="block text-xs text-muted">
        Tipo de comprobante
        <select className="mt-2 w-full rounded-lg border border-bd-border bg-surface p-2 text-foreground"
          value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
          <option value="">Selecciona un documento</option>
          {options.filter((option) => branch !== null && option.branches.includes(branch)).map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
      <p className="mt-2 text-xs text-muted">
        {value === 'sales_note' ? 'Documento interno sin validez tributaria.' :
          value ? 'Solo BETA: se prepara el documento. Firmar, enviar y consultar la aceptación son pasos posteriores. Factura requiere un cliente con RUC; no se admiten descuentos fiscales.' :
            'Se muestran los documentos habilitados para tu cuenta y sucursal.'}
      </p>
    </div>
  );
}
