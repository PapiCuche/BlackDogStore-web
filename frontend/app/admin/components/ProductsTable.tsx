"use client";

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

  async function toggleActive(product: AdminProduct) {
    await patchAdminProduct(product.id, { is_active: !product.is_active });
    onChanged();
  }

  if (products.length === 0) {
    return (
      <p className="text-zinc-500 text-sm py-6 text-center">
        No hay productos que coincidan.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.08] text-zinc-400">
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
              className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
            >
              <td className="py-3 pr-4">
                <Link
                  href={`/admin/products/${p.id}`}
                  className="text-zinc-100 hover:text-white hover:underline font-medium"
                >
                  {p.name}
                </Link>
                <div className="text-zinc-500 text-xs mt-0.5">{p.slug}</div>
              </td>
              <td className="py-3 pr-4 text-zinc-400 hidden md:table-cell">
                {p.category_name ?? "—"}
              </td>
              <td className="py-3 pr-4 text-right text-zinc-200">
                S/ {parseFloat(p.price).toFixed(2)}
              </td>
              <td
                className={`py-3 pr-4 text-right font-medium ${
                  p.inventory === 0
                    ? "text-red-400"
                    : p.inventory <= 5
                      ? "text-yellow-400"
                      : "text-zinc-200"
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
                    className="text-zinc-400 hover:text-zinc-100 text-xs mr-4 hover:underline"
                  >
                    Editar
                  </Link>
                  <button
                    onClick={() => toggleActive(p)}
                    className="text-zinc-400 hover:text-zinc-100 text-xs hover:underline"
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
  );
}
