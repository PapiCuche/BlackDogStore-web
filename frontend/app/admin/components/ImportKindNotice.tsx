/**
 * IMPORT-SPLIT-01 — qué hace ESTA carga, y a dónde ir para las otras.
 *
 * Tres archivos de Excel entran al sistema y ninguno hace el trabajo de otro:
 *
 *   productos   crea y edita el catálogo (también los accesorios). No mueve stock.
 *   equipos     una fila = un equipo físico, con su serie y su IMEI.
 *   stock       cantidades de lo que se cuenta por número.
 *
 * Se dice arriba de cada pantalla, antes de adjuntar nada: quien trae una
 * columna «Cantidad» a la carga de productos, o veinte teléfonos a la de stock,
 * se entera aquí y no en un error.
 */

import Link from "next/link";

export type ImportKind = "products" | "units" | "stock";

const LINKS: Record<ImportKind, { label: string; href: string }> = {
  products: { label: "Carga de productos", href: "/admin/products/import" },
  units: { label: "Carga de equipos", href: "/admin/inventory/units/import" },
  stock: { label: "Carga de stock", href: "/admin/inventory/import" },
};

function Other({ kind }: { kind: ImportKind }) {
  return (
    <Link href={LINKS[kind].href} className="font-semibold text-foreground underline underline-offset-2">
      {LINKS[kind].label}
    </Link>
  );
}

export function ImportKindNotice({ kind }: { kind: ImportKind }) {
  return (
    <div
      role="note"
      aria-label="Qué hace esta carga"
      className="rounded-lg border border-bd-border bg-background px-4 py-3 text-sm leading-6 text-foreground"
    >
      {kind === "products" ? (
        <>
          <p className="font-semibold">Esta carga crea y edita productos y accesorios del catálogo.</p>
          <p className="text-muted">
            No mueve stock. La plantilla es una sola hoja, «Productos»: cada cabecera lleva una nota
            con lo que va debajo. La categoría es una de tu empresa; las imágenes son opcionales y se
            pueden añadir después en la ficha; «Destino» decide si el producto sale en la tienda en
            línea o queda sólo como stock interno.
          </p>
          <p className="mt-2 text-xs text-muted">
            ¿Equipos con número de serie o IMEI? <Other kind="units" />. ¿Cantidades? <Other kind="stock" />.
          </p>
        </>
      ) : null}

      {kind === "units" ? (
        <>
          <p className="font-semibold">Una fila = un equipo físico.</p>
          <p className="text-muted">
            Dos teléfonos iguales son dos filas, cada una con su número de serie. No hay columna de
            cantidad: una cantidad no dice qué equipos son. Serie sola, serie + IMEI o serie + dos
            IMEI, según lo que lleve el producto; lo que un equipo no tiene se deja vacío.
          </p>
          <p className="mt-2 text-xs text-muted">
            La plantilla es una sola hoja, «Equipos»: Código · Producto · Sucursal · Número de serie ·
            IMEI · IMEI 2 · Condición · Costo · Motivo / referencia. El producto debe existir y estar
            marcado «con número de serie»; si aún no existe, créalo en <Other kind="products" />. Lo
            que se cuenta por cantidad va en <Other kind="stock" />.
          </p>
        </>
      ) : null}

      {kind === "stock" ? (
        <>
          <p className="font-semibold">Esta carga ajusta cantidades y deja su movimiento en el Kardex.</p>
          <p className="mt-2 text-xs text-muted">
            Un producto con número de serie no se carga por cantidad: sus equipos van uno por fila en{" "}
            <Other kind="units" />. Para crear o editar productos, <Other kind="products" />.
          </p>
        </>
      ) : null}
    </div>
  );
}
