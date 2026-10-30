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

/**
 * INVENTORY-UNIFIED-01. «En la web» es otro dato que «activo», y en la lista
 * tiene su propia columna: «Publicado» o «Solo stock interno».
 */
export function ProductVisibilityBadge({ publishedOnline, isActive }: { publishedOnline: boolean; isActive?: boolean }) {
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <span
        title={publishedOnline
          ? "Configurado para la tienda. Que un cliente lo vea además depende de otras condiciones, por ejemplo que el artículo esté activo."
          : undefined}
        className={`${BADGE} ${
          publishedOnline
            ? "border-success-border text-success bg-success-surface"
            : "border-bd-border text-muted bg-background"
        }`}
      >
        {publishedOnline ? "Publicado" : "Solo stock interno"}
      </span>
      {/* NAV-UX-02: «Publicado» is a setting, not a promise. An inactive article is not offered by the shop. */}
      {publishedOnline && isActive === false ? (
        <span className="text-xs text-warning">Configurado, pero inactivo: la tienda no lo muestra.</span>
      ) : null}
    </span>
  );
}
