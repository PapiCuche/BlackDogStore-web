"use client";

/**
 * NOTE-NOT-SUBSTITUTE — what the staff must do about the sale's boleta or factura.
 *
 * Sólo para el personal: la nota de venta no lleva leyenda (decisión del
 * propietario) y nunca se presenta como comprobante. El texto lo decide el
 * servidor (`receipt_coverage`): esta pantalla no deduce «emitido» de nada,
 * y un comprobante BETA o una nota de venta no cuentan como evidencia.
 * docs/nota-de-venta-verificacion-tributaria.md
 */
import type { ReceiptCoverage } from "../../lib/admin";

export function ReceiptCoverageNotice({ coverage, className = "" }: {
  coverage: ReceiptCoverage | null | undefined;
  className?: string;
}) {
  if (!coverage) return null;
  if (coverage.state === "system" && coverage.receipt) {
    return (
      <p data-testid="receipt-coverage" data-state="system" className={`text-xs text-muted ${className}`}>
        Comprobante electrónico aceptado por SUNAT en el sistema: <span className="font-mono">{coverage.receipt}</span>
      </p>
    );
  }
  if (!coverage.warning) return null;
  const pending = coverage.state === "pending";
  return (
    <div
      role={pending ? "alert" : "note"}
      data-testid="receipt-coverage"
      data-state={coverage.state}
      className={
        pending
          ? `rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-left text-sm text-warning ${className}`
          : `rounded-lg border border-bd-border bg-background px-4 py-3 text-left text-xs text-muted ${className}`
      }
    >
      {pending ? <p className="mb-1 font-semibold">Comprobante de pago pendiente</p> : null}
      {coverage.state === "external" ? (
        <p className="mb-1 font-semibold text-foreground">Comprobante externo registrado</p>
      ) : null}
      <p>{coverage.warning}</p>
    </div>
  );
}
