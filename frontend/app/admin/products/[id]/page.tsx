"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { StaffGuard } from "../../components/StaffGuard";
import { AdminShell } from "../../components/AdminShell";
import { ProductForm } from "../../components/ProductForm";
import { InventoryAdjustForm } from "../../components/InventoryAdjustForm";
import { ProductStatusBadge } from "../../components/ProductStatusBadge";
import {
  AdminProduct,
  AdminCategory,
  fetchAdminProductDetail,
  fetchAdminCategories,
} from "../../../lib/admin";
import type { AuthUser } from "../../../lib/auth";

function ProductDetailContent({ user }: { user: AuthUser }) {
  const { id } = useParams<{ id: string }>();
  const productId = parseInt(id, 10);

  const [product, setProduct] = useState<AdminProduct | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canManage = user.role === "admin" || user.role === "superadmin";
  const canAdjustInventory =
    user.role === "inventory" || user.role === "admin" || user.role === "superadmin";

  useEffect(() => {
    Promise.all([
      fetchAdminProductDetail(productId),
      fetchAdminCategories(),
    ])
      .then(([p, cats]) => {
        setProduct(p);
        setCategories(cats);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Error al cargar el producto.");
      })
      .finally(() => setLoading(false));
  }, [productId]);

  if (loading) {
    return (
      <AdminShell user={user}>
        <p className="text-zinc-500 text-sm">Cargando…</p>
      </AdminShell>
    );
  }

  if (error || !product) {
    return (
      <AdminShell user={user}>
        <p className="text-red-400 text-sm">{error ?? "Producto no encontrado."}</p>
        <Link href="/admin/products" className="text-zinc-400 hover:text-zinc-100 text-sm mt-4 block">
          ← Volver a productos
        </Link>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={user}>
      <div className="space-y-8 max-w-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <Link
              href="/admin/products"
              className="text-xs text-zinc-500 hover:text-zinc-300 mb-2 block"
            >
              ← Volver a productos
            </Link>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold text-white">{product.name}</h1>
              <ProductStatusBadge isActive={product.is_active} />
            </div>
            <p className="mt-1 text-sm text-zinc-500">{product.slug}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4 text-sm">
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs text-zinc-500 mb-1">Precio</p>
            <p className="text-lg font-semibold text-white">
              S/ {parseFloat(product.price).toFixed(2)}
            </p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs text-zinc-500 mb-1">Inventario</p>
            <p
              className={`text-lg font-semibold ${
                product.inventory === 0
                  ? "text-red-400"
                  : product.inventory <= 5
                    ? "text-yellow-400"
                    : "text-white"
              }`}
            >
              {product.inventory}
            </p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="text-xs text-zinc-500 mb-1">Categoría</p>
            <p className="text-sm font-medium text-zinc-200">
              {product.category_name ?? "Sin categoría"}
            </p>
          </div>
        </div>

        {canAdjustInventory && (
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold text-white mb-4">
              Ajuste de inventario
            </h2>
            <InventoryAdjustForm
              productId={product.id}
              currentInventory={product.inventory}
              onAdjusted={(newInventory) =>
                setProduct((prev) => prev ? { ...prev, inventory: newInventory } : prev)
              }
            />
          </section>
        )}

        {canManage && (
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold text-white mb-4">
              Editar producto
            </h2>
            <ProductForm
              product={product}
              categories={categories}
              onSaved={(updated) => setProduct(updated)}
            />
          </section>
        )}

        {!canManage && !canAdjustInventory && (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
            <p className="text-sm text-zinc-400">
              No tienes permisos para editar este producto o ajustar su inventario.
            </p>
          </div>
        )}
      </div>
    </AdminShell>
  );
}

export default function ProductDetailPage() {
  return (
    <StaffGuard>{(user) => <ProductDetailContent user={user} />}</StaffGuard>
  );
}
