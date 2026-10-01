"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AdminGuard } from "../../components/AdminGuard";
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
          <p className="rounded-xl border border-amber-400/25 bg-amber-400/10 px-4 py-3 text-sm text-amber-200">
            No se pudieron cargar las categorías. Puedes crear el producto sin categoría y editarlo después.
          </p>
        ) : null}

        <section className="rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
          <ProductForm categories={categories} onSaved={handleSaved} />
        </section>
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
              <div className="rounded-2xl border border-bd-border bg-surface p-5 text-sm text-muted">
                Solo los administradores pueden crear productos.
              </div>
            </AdminShell>
          );
        }
        return <NewProductContent user={user} />;
      }}
    </AdminGuard>
  );
}
