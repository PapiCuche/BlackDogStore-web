/**
 * PRINT-THERMAL-01. Una frase junto al PDF de 80 mm y a los botones de imprimir.
 *
 * Una térmica ESC/POS instalada en el equipo con «Generic PostScript Printer»
 * recibe PostScript y lo imprime como texto: metros de `%!PS-Adobe…`. El PDF de
 * 80 mm sólo sale bien con el controlador de la propia térmica; la impresión
 * directa (ESC/POS) va por el agente de impresión del local, nunca por el
 * navegador.
 */
export const THERMAL_PRINT_HINT =
  "El PDF de 80 mm se imprime con el controlador de la propia térmica, no con " +
  "«Generic PostScript»; la impresión directa en la térmica va por el agente de impresión.";

export function ThermalPrintHint({ className = "" }: { className?: string }) {
  return (
    <p className={`text-xs leading-relaxed text-muted ${className}`.trim()}>
      {THERMAL_PRINT_HINT}
    </p>
  );
}
