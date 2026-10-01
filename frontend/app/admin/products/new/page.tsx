"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { PageHeader } from "../../components/internal-ui";
import { ProductForm } from "../../components/ProductForm";
import { AdminCategory, AdminProduct, fetchAdminCategories } from "../../../lib/admin";
import type { AuthUser } from "../../../lib/auth";

function NewProductContent({ user }: { user: AuthUser }) {
  const router = useRouter();
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [categoryError, setCategoryError] = useState(false);

  useEffect(() => {
    fetchAdminCategories()
      .then((data) => {
        setCategories(data);
        setCategoryError(false);
      })
      .catch(() => {
        setCategories([]);
        setCategoryError(true);
      });
  }, []);

  function handleSaved(product: AdminProduct) {
    router.push(`/admin/products/${product.id}`);
  }

  return (
    <AdminShell user={user}>
      <div className="max-w-3xl space-y-6">
        <PageHeader
          eyebrow="Catálogo"
          title="Nuevo producto"
          description="Completa los datos del producto. El slug se genera automáticamente si lo dejas vacío."
        />

        {categoryError ? (
          <p className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
            No se pudieron cargar las categorías. Puedes crear el producto sin categoría y editarlo después.
          </p>
        ) : null}

        <section className="rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
          <ProductForm categories={categories} onSaved={handleSaved} />
        </section>
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
