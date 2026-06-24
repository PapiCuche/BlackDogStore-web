"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AdminGuard } from "../../components/AdminGuard";
import { AdminShell } from "../../components/AdminShell";
import { ProductForm } from "../../components/ProductForm";
import { AdminCategory, AdminProduct, fetchAdminCategories } from "../../../lib/admin";
import type { AuthUser } from "../../../lib/auth";

function NewProductContent({ user }: { user: AuthUser }) {
  const router = useRouter();
  const [categories, setCategories] = useState<AdminCategory[]>([]);

  useEffect(() => {
    fetchAdminCategories()
      .then(setCategories)
      .catch(() => {});
  }, []);

  function handleSaved(product: AdminProduct) {
    router.push(`/admin/products/${product.id}`);
  }

  return (
    <AdminShell user={user}>
      <div className="space-y-6 max-w-2xl">
        <div>
          <h1 className="text-xl font-semibold text-white">Nuevo producto</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Completa los datos del producto. El slug se genera automáticamente si lo dejas vacío.
          </p>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-6">
          <ProductForm categories={categories} onSaved={handleSaved} />
        </div>
      </div>
    </AdminShell>
  );
}

export default function NewProductPage() {
  return (
    <AdminGuard>
      {(user) => {
        if (user.role !== "admin" && user.role !== "superadmin") {
          return (
            <AdminShell user={user}>
              <p className="text-zinc-400 text-sm">
                Solo los administradores pueden crear productos.
              </p>
            </AdminShell>
          );
        }
        return <NewProductContent user={user} />;
      }}
    </AdminGuard>
  );
}
