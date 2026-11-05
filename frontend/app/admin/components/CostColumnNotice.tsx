/**
 * COST-WRITE-AUTHORITY (decisión C del propietario) — quien no tiene «Ver costos
 * y márgenes» no registra costos desde un Excel.
 *
 * El servidor marca con error cada fila que trae la columna de costo llena y una
 * carga con errores no se aplica. Se dice aquí, antes de adjuntar nada, para que
 * nadie se entere en la previsualización. Quien sí tiene el permiso no ve el aviso.
 */
export function CostColumnNotice({ column }: { column: string }) {
  return (
    <p
      role="note"
      aria-label="Costos en el archivo"
      className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning"
    >
      Tu usuario no puede registrar costos: deja vacía (o quita) la columna «{column}». Una fila con costo se
      marca con error y la carga no se aplica. Para cargar costos, pide a un administrador el permiso «Ver
      costos y márgenes».
    </p>
  );
}
