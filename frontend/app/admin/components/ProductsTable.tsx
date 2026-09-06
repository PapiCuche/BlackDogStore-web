"use client";

import { useState } from "react";
import Link from "next/link";
import { AdminProduct, patchAdminProduct } from "../../lib/admin";
import { AuthUser } from "../../lib/auth";
import { ProductStatusBadge } from "./ProductStatusBadge";

type Props = {
  products: AdminProduct[];
  currentUser: AuthUser;
  onChanged: () => void;
};

export function ProductsTable({ products, currentUser, onChanged }: Props) {
  const canManage =
    currentUser.role === "admin" || currentUser.role === "superadmin";
  const [toggleError, setToggleError] = useState<string | null>(null);

  async function toggleActive(product: AdminProduct) {
    setToggleError(null);
    try {
      await patchAdminProduct(product.id, { is_active: !product.is_active });
      onChanged();
    } catch (err) {
      setToggleError(
        err instanceof Error
          ? err.message
          : "No se pudo cambiar el estado del producto.",
      );
    }
  }

  if (products.length === 0) {
    return (
      <p className="text-muted text-sm py-6 text-center">
        No hay productos que coincidan.
      </p>
    );
  }

  return (
    <div>
      {toggleError && (
        <p className="text-sm text-danger mb-3">{toggleError}</p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-bd-border text-muted">
              <th className="text-left pb-3 pr-4 font-medium">Nombre</th>
              <th className="text-left pb-3 pr-4 font-medium hidden md:table-cell">
                Categoría
              </th>
              <th className="text-right pb-3 pr-4 font-medium">Precio</th>
              <th className="text-right pb-3 pr-4 font-medium">Stock</th>
              <th className="text-left pb-3 pr-4 font-medium">Estado</th>
              {canManage && (
                <th className="text-right pb-3 font-medium">Acciones</th>
              )}
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <tr
                key={p.id}
                className="border-b border-bd-border hover:bg-surface transition-colors"
              >
                <td className="py-3 pr-4">
                  <Link
                    href={`/admin/products/${p.id}`}
                    className="text-foreground hover:text-foreground hover:underline font-medium"
                  >
                    {p.name}
                  </Link>
                  <div className="text-muted text-xs mt-0.5">{p.slug}</div>
                </td>
                <td className="py-3 pr-4 text-muted hidden md:table-cell">
                  {p.category_name ?? "—"}
                </td>
                <td className="py-3 pr-4 text-right text-foreground">
                  S/ {parseFloat(p.price).toFixed(2)}
                </td>
                <td
                  className={`py-3 pr-4 text-right font-medium ${
                    p.inventory === 0
                      ? "text-danger"
                      : p.inventory <= 5
                        ? "text-warning"
                        : "text-foreground"
                  }`}
                >
                  {p.inventory}
                </td>
                <td className="py-3 pr-4">
                  <ProductStatusBadge isActive={p.is_active} />
                </td>
                {canManage && (
                  <td className="py-3 text-right">
                    <Link
                      href={`/admin/products/${p.id}`}
                      className="text-muted hover:text-foreground text-xs mr-4 hover:underline"
                    >
                      Editar
                    </Link>
                    <button
                      onClick={() => toggleActive(p)}
                      className="text-muted hover:text-foreground text-xs hover:underline"
                    >
                      {p.is_active ? "Desactivar" : "Activar"}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
