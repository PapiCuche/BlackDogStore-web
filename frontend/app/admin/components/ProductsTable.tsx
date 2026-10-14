"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AdminProduct, patchAdminProduct, publicationProblemLabel } from "../../lib/admin";
import { ProductStatusBadge, ProductVisibilityBadge } from "./ProductStatusBadge";

/**
 * INVENTORY-UNIFIED-01. Qué filas están marcadas para la acción en bloque.
 * Sin esto la tabla no enseña casillas: quien sólo consulta no selecciona.
 */
export type ProductSelection = {
  isSelected: (productId: number) => boolean;
  onToggle: (productId: number, checked: boolean) => void;
  /** La casilla de la cabecera: toda la página, o nada. */
  onTogglePage: (checked: boolean) => void;
};

type Props = {
  products: AdminProduct[];
  /** H4.1.2A: lo decide el servidor, no el rol global de la sesión. */
  canManage: boolean;
  onChanged: () => void;
  selection?: ProductSelection;
};

export function ProductsTable({ products, canManage, onChanged, selection }: Props) {
  const [toggleError, setToggleError] = useState<string | null>(null);
  const pageBox = useRef<HTMLInputElement>(null);

  const selectedOnPage = selection ? products.filter((product) => selection.isSelected(product.id)).length : 0;
  const wholePage = products.length > 0 && selectedOnPage === products.length;

  useEffect(() => {
    // «Algunas sí, otras no» no es un atributo de HTML: se pone en el elemento.
    if (pageBox.current) pageBox.current.indeterminate = selectedOnPage > 0 && !wholePage;
  }, [selectedOnPage, wholePage]);

  async function toggleActive(product: AdminProduct) {
    setToggleError(null);
    try {
      await patchAdminProduct(product.id, { is_active: !product.is_active });
      onChanged();
    } catch (err) {
      setToggleError(
        err instanceof Error ? err.message : "No se pudo cambiar el estado del producto.",
      );
    }
  }

  if (products.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">No hay productos que coincidan.</p>;
  }

  return (
    <div>
      {toggleError ? <p className="mb-3 text-sm text-danger" role="alert">{toggleError}</p> : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[52rem] text-sm">
          <thead>
            <tr className="border-b border-bd-border text-left text-[11px] uppercase tracking-[0.1em] text-muted">
              {selection ? (
                <th className="w-10 px-4 py-3">
                  <input
                    ref={pageBox}
                    type="checkbox"
                    checked={wholePage}
                    onChange={(event) => selection.onTogglePage(event.target.checked)}
                    aria-label={`Seleccionar los ${products.length} productos de esta página`}
                    className="h-4 w-4 cursor-pointer accent-primary"
                  />
                </th>
              ) : null}
              <th className="px-4 py-3 font-semibold">Nombre</th>
              <th className="hidden px-4 py-3 font-semibold md:table-cell">Categoría</th>
              <th className="px-4 py-3 text-right font-semibold">Precio</th>
              <th className="px-4 py-3 text-right font-semibold">Stock</th>
              <th className="px-4 py-3 font-semibold">Estado</th>
              <th className="px-4 py-3 font-semibold">E-commerce</th>
              {canManage ? <th className="px-4 py-3 text-right font-semibold">Acciones</th> : null}
            </tr>
          </thead>
          <tbody>
            {products.map((product) => {
              const problems = product.publication_problems ?? [];
              return (
                <tr
                  key={product.id}
                  className="border-b border-bd-border/70 transition last:border-0 hover:bg-foreground/[0.025]"
                >
                  {selection ? (
                    <td className="w-10 px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selection.isSelected(product.id)}
                        onChange={(event) => selection.onToggle(product.id, event.target.checked)}
                        aria-label={`Seleccionar ${product.name}`}
                        className="h-4 w-4 cursor-pointer accent-primary"
                      />
                    </td>
                  ) : null}
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      {/* La principal de su galería (o su dirección de siempre).
                          Decorativa: el nombre está al lado. */}
                      {product.image_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={product.image_url}
                          alt=""
                          loading="lazy"
                          className="h-10 w-10 shrink-0 rounded-md border border-bd-border bg-background object-contain"
                        />
                      ) : (
                        <span data-thumb="empty" aria-hidden="true" className="h-10 w-10 shrink-0 rounded-md border border-dashed border-bd-border bg-background" />
                      )}
                      <div className="min-w-0">
                        <Link
                          href={`/admin/products/${product.id}`}
                          className="font-medium text-foreground transition hover:underline"
                        >
                          {product.name}
                        </Link>
                        <div className="mt-1 text-xs text-muted">{product.slug}</div>
                      </div>
                    </div>
                  </td>
                  <td className="hidden px-4 py-3 text-muted md:table-cell">{product.category_name ?? "—"}</td>
                  <td className="px-4 py-3 text-right font-medium tabular-nums text-foreground">
                    S/ {parseFloat(product.price).toFixed(2)}
                  </td>
                  <td
                    className={`px-4 py-3 text-right font-semibold tabular-nums ${
                      product.inventory === 0
                        ? "text-danger"
                        : product.inventory <= 5
                          ? "text-warning"
                          : "text-foreground"
                    }`}
                  >
                    {product.inventory}
                  </td>
                  <td className="px-4 py-3"><ProductStatusBadge isActive={product.is_active} /></td>
                  <td className="px-4 py-3">
                    <ProductVisibilityBadge publishedOnline={product.is_published_online} />
                    {/* Sólo en lo que no está publicado: es ahí donde importa saber
                        por qué la acción en bloque lo dejaría fuera. */}
                    {!product.is_published_online && problems.length > 0 ? (
                      <p className="mt-1 max-w-[16rem] text-xs text-muted">
                        No se publica en bloque: {problems.map(publicationProblemLabel).join(", ")}
                      </p>
                    ) : null}
                  </td>
                  {canManage ? (
                    <td className="px-4 py-3 text-right">
                      <Link href={`/admin/products/${product.id}`} className="mr-4 text-xs font-semibold text-muted transition hover:text-foreground hover:underline">
                        Editar
                      </Link>
                      <button type="button" onClick={() => toggleActive(product)} className="text-xs font-semibold text-muted transition hover:text-foreground hover:underline">
                        {product.is_active ? "Desactivar" : "Activar"}
                      </button>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
