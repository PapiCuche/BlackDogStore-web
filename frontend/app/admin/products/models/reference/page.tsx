"use client";

/**
 * Productos › Modelos › Catálogo de referencia — CATALOG-REFERENCE-01 · PR E.
 *
 * `products.view` para llegar y previsualizar; importar exige `products.manage`. El servidor comprueba las dos; aquí
 * sólo se decide qué controles se pintan. Importar nunca publica nada.
 */

import { AccessGuard } from "../../../components/AccessGuard";
import { AdminShell } from "../../../components/AdminShell";
import { ReferenceCatalogImport } from "../../../components/ReferenceCatalogImport";
import { PageHeader } from "../../../components/internal-ui";
import type { InternalAccess } from "../../../lib/internal-access";
import type { AuthUser } from "../../../../lib/auth";

function ReferenceContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos › Modelos"
          title="Catálogo de referencia"
          description="Modelos con sus capacidades, colores, SIM y regiones verificados en las páginas oficiales de cada fabricante. Importa los que vendes y personalízalos después."
        />
        <ReferenceCatalogImport
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
      </div>
    </AdminShell>
  );
}

export default function ReferenceCatalogPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <ReferenceContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
