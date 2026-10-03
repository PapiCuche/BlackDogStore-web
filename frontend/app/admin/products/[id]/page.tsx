"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AccessGuard } from "../../components/AccessGuard";
import type { InternalAccess } from "../../lib/internal-access";
import { AdminShell } from "../../components/AdminShell";
import { PageHeader } from "../../components/internal-ui";
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

function ProductDetailContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const { id } = useParams<{ id: string }>();
  const productId = parseInt(id, 10);

  const [product, setProduct] = useState<AdminProduct | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canManage = access.can("products.manage", ["admin", "superadmin"]);
  const canAdjustInventory = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);

  useEffect(() => {
    Promise.all([
      fetchAdminProductDetail(productId),
      fetchAdminCategories(),
    ])
      .then(([nextProduct, nextCategories]) => {
        setProduct(nextProduct);
        setCategories(nextCategories);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Error al cargar el producto.");
      })
      .finally(() => setLoading(false));
  }, [productId]);

  if (loading) {
    return (
      <AdminShell user={user}>
        <div className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-sm text-muted">
          Cargando producto…
        </div>
      </AdminShell>
    );
  }

  if (error || !product) {
    return (
      <AdminShell user={user}>
        <div className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger" role="alert">
          {error ?? "Producto no encontrado."}
        </div>
        <Link href="/admin/products" className="mt-4 inline-flex text-sm font-semibold text-muted transition hover:text-foreground">
          ← Volver a productos
        </Link>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={user}>
      <div className="max-w-4xl space-y-8">
        <div>
          <Link href="/admin/products" className="mb-3 inline-flex text-xs font-semibold text-muted transition hover:text-foreground">
            ← Volver a productos
          </Link>
          <PageHeader
            eyebrow="Catálogo"
            title={product.name}
            description={product.slug}
            actions={<ProductStatusBadge isActive={product.is_active} />}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-bd-border bg-surface p-4">
            <p className="text-xs text-muted">Precio</p>
            <p className="mt-1 text-lg font-semibold tabular-nums text-foreground">
              S/ {parseFloat(product.price).toFixed(2)}
            </p>
          </div>
          <div className="rounded-xl border border-bd-border bg-surface p-4">
            <p className="text-xs text-muted">Inventario</p>
            <p
              className={`mt-1 text-lg font-semibold tabular-nums ${
                product.inventory === 0
                  ? "text-danger"
                  : product.inventory <= 5
                    ? "text-warning"
                    : "text-foreground"
              }`}
            >
              {product.inventory}
            </p>
          </div>
          <div className="rounded-xl border border-bd-border bg-surface p-4">
            <p className="text-xs text-muted">Categoría</p>
            <p className="mt-1 text-sm font-medium text-foreground">
              {product.category_name ?? "Sin categoría"}
            </p>
          </div>
        </div>

        {canAdjustInventory ? (
          <section className="rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
            <h2 className="text-sm font-semibold text-foreground">Ajuste de inventario</h2>
            <p className="mt-1 mb-4 text-xs text-muted">Modifica el stock usando el flujo de inventario existente.</p>
            <InventoryAdjustForm
              productId={product.id}
              currentInventory={product.inventory}
              branches={access.dashboard?.branch_scope?.branches ?? []}
              defaultBranchId={access.dashboard?.branch_scope?.default_branch?.id ?? null}
              onAdjusted={(newInventory) =>
                setProduct((prev) => prev ? { ...prev, inventory: newInventory } : prev)
              }
            />
          </section>
        ) : null}

        {canManage ? (
          <section className="rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
            <h2 className="text-sm font-semibold text-foreground">Editar producto</h2>
            <p className="mt-1 mb-4 text-xs text-muted">Nombre, categoría, precio, contenido y publicación.</p>
            <ProductForm
              product={product}
              categories={categories}
              onSaved={(updated) => setProduct(updated)}
            />
          </section>
        ) : null}

        {!canManage && !canAdjustInventory ? (
          <div className="rounded-xl border border-bd-border bg-surface p-6">
            <p className="text-sm text-muted">
              No tienes permisos para editar este producto o ajustar su inventario.
            </p>
          </div>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function ProductDetailPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <ProductDetailContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
