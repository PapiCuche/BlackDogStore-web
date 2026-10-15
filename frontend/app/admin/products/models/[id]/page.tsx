"use client";

/**
 * Productos › Modelos › un modelo — DEVICE-COMMERCE-01 · DC2.
 *
 * `products.view` para llegar; cambiar el modelo o sus variantes exige
 * `products.manage`, que el servidor comprueba en cada guardado.
 */

import { useParams } from "next/navigation";
import { AccessGuard } from "../../../components/AccessGuard";
import { AdminShell } from "../../../components/AdminShell";
import { CatalogModelEditor } from "../../../components/CatalogModelEditor";
import { PageHeader } from "../../../components/internal-ui";
import type { InternalAccess } from "../../../lib/internal-access";
import type { AuthUser } from "../../../../lib/auth";

function ModelContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const { id } = useParams<{ id: string }>();
  const modelId = Number.parseInt(id, 10);
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader eyebrow="Productos › Modelos" title="Modelo" />
        {Number.isFinite(modelId) ? (
          <CatalogModelEditor
            modelId={modelId}
            canManage={access.can("products.manage", ["admin", "superadmin"])}
            companyId={access.dashboard?.company?.id ?? null}
          />
        ) : (
          <p className="text-sm text-muted">La dirección no corresponde a un modelo.</p>
        )}
      </div>
    </AdminShell>
  );
}

export default function CatalogModelPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <ModelContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
