"use client";

import { useState } from "react";
import Link from "next/link";
import { AdminProduct, patchAdminProduct } from "../../lib/admin";
import { ProductStatusBadge } from "./ProductStatusBadge";

type Props = {
  products: AdminProduct[];
  /** H4.1.2A: lo decide el servidor, no el rol global de la sesión. */
  canManage: boolean;
  onChanged: () => void;
};

export function ProductsTable({ products, canManage, onChanged }: Props) {
  const [toggleError, setToggleError] = useState<string | null>(null);

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
        <table className="w-full min-w-[44rem] text-sm">
          <thead>
            <tr className="border-b border-bd-border text-left text-[11px] uppercase tracking-[0.1em] text-muted">
              <th className="px-4 py-3 font-semibold">Nombre</th>
              <th className="hidden px-4 py-3 font-semibold md:table-cell">Categoría</th>
              <th className="px-4 py-3 text-right font-semibold">Precio</th>
              <th className="px-4 py-3 text-right font-semibold">Stock</th>
              <th className="px-4 py-3 font-semibold">Estado</th>
              {canManage ? <th className="px-4 py-3 text-right font-semibold">Acciones</th> : null}
            </tr>
          </thead>
          <tbody>
            {products.map((product) => (
              <tr
                key={product.id}
                className="border-b border-bd-border/70 transition last:border-0 hover:bg-foreground/[0.025]"
              >
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
