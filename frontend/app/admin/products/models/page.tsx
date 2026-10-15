"use client";

/**
 * Productos › Modelos — DEVICE-COMMERCE-01 · DC2.
 *
 * `products.view` para llegar; crear o cambiar exige `products.manage`. El
 * servidor comprueba las dos; aquí sólo se decide qué controles se pintan.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { CatalogModelsManager } from "../../components/CatalogModelsManager";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";

function ModelsContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Modelos"
          description="Agrupa en un modelo los productos que son opciones de un mismo equipo —capacidad, color, nuevo o seminuevo— para venderlos desde una sola página."
        />
        <CatalogModelsManager
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
      </div>
    </AdminShell>
  );
}

export default function CatalogModelsPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <ModelsContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
