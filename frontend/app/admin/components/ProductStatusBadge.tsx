"use client";

type Props = {
  isActive: boolean;
  /**
   * PRODUCT-DESTINATION-01. `false` = «sólo stock interno». Sin el dato no se
   * afirma nada: una pantalla que todavía no lo pide no etiqueta de más.
   */
  publishedOnline?: boolean;
};

const BADGE = "inline-block px-2 py-0.5 rounded text-xs font-medium border";

export function ProductStatusBadge({ isActive, publishedOnline }: Props) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span
        className={`${BADGE} ${
          isActive
            ? "border-bd-border text-foreground bg-surface"
            : "border-danger-border text-danger bg-danger-surface"
        }`}
      >
        {isActive ? "Activo" : "Inactivo"}
      </span>
      {publishedOnline === false ? (
        <span className={`${BADGE} border-bd-border text-muted bg-background`}>Solo stock interno</span>
      ) : null}
    </span>
  );
}
