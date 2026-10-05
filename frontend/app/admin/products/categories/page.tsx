"use client";

/**
 * Productos › Categorías.
 *
 * `products.view` para llegar; cambiar exige `products.manage`. El servidor
 * comprueba las dos; aquí sólo se decide qué controles se pintan.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { CategoriesManager } from "../../components/CategoriesManager";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";

function CategoriesContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Categorías"
          description="Las familias de tu catálogo: cuáles se ofrecen, cuáles ilustra la portada y en qué orden aparecen."
        />
        <CategoriesManager canManage={access.can("products.manage", ["admin", "superadmin"])} />
      </div>
    </AdminShell>
  );
}

export default function CategoriesPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <CategoriesContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
