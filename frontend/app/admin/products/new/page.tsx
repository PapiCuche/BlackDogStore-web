"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AccessGuard } from "../../components/AccessGuard";
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
          <h1 className="text-xl font-semibold text-foreground">Nuevo producto</h1>
          <p className="mt-1 text-sm text-muted">
            Completa los datos del producto. El slug se genera automáticamente si lo dejas vacío.
          </p>
        </div>
        <div className="rounded-xl border border-bd-border bg-surface p-6">
          <ProductForm categories={categories} onSaved={handleSaved} />
        </div>
      </div>
    </AdminShell>
  );
}

export default function NewProductPage() {
  // H4.1.2A: quien puede crear productos lo dice `products.manage` en esta
  // empresa. El doble chequeo por rol que había aquí dentro sobraba y, peor,
  // echaba a quien la empresa sí había autorizado.
  return (
    <AccessGuard capability="products.manage" legacyRoles={["admin", "superadmin"]}>
      {(access) => <NewProductContent user={access.user} />}
    </AccessGuard>
  );
}
