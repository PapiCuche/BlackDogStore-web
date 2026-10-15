"use client";

/**
 * Inventario › Seminuevos — DEVICE-COMMERCE-01 · DC2.
 *
 * `inventory.view` para llegar, como «Equipos»; cambiar la ficha de un equipo
 * (batería, estado, precio, fotos) exige `inventory.adjust`, y el costo sólo
 * llega a quien tiene `costs.view`. El servidor comprueba las tres.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { BranchSelector } from "../../components/BranchSelector";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import { UsedUnitsPanel } from "../../components/UsedUnitsPanel";
import type { InternalAccess } from "../../lib/internal-access";
import { useBranchScope } from "../../lib/use-branch-scope";
import type { AuthUser } from "../../../lib/auth";

function UsedContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Seminuevos"
          description="Los equipos seminuevos, uno por uno: lo que la tienda publica de cada uno —batería, estado, precio y fotos— se completa aquí."
          actions={
            scope.access ? (
              <BranchSelector access={scope.access} value={scope.branch} onChange={scope.setBranch} />
            ) : null
          }
        />
        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {scope.loading ? <Spinner label="Cargando sucursales…" /> : null}
        {!scope.loading && !scope.ready && !scope.error ? (
          <p className="text-sm text-muted">Todavía no tienes ninguna sucursal asignada.</p>
        ) : null}
        {scope.ready ? (
          <UsedUnitsPanel
            branch={scope.branch}
            canEdit={access.can("inventory.adjust", ["inventory", "admin", "superadmin"])}
            canSeeCost={access.can("costs.view", ["admin", "superadmin"])}
          />
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function UsedUnitsPage() {
  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <UsedContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
