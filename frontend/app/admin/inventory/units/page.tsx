"use client";

/**
 * Inventario › Equipos.
 *
 * `inventory.view` para llegar; registrar, apartar o dar de baja exige
 * `inventory.adjust`, que el servidor comprueba en cada operación. Como el
 * resto del inventario, todo ocurre dentro de las sucursales de quien mira.
 */

import { useState } from "react";
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { BranchSelector } from "../../components/BranchSelector";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import { StockUnitsPanel, unitFiltersFromSearch } from "../../components/StockUnitsPanel";
import type { InternalAccess } from "../../lib/internal-access";
import { useBranchScope } from "../../lib/use-branch-scope";
import type { AuthUser } from "../../../lib/auth";

function UnitsContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });
  const canAdjust = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);
  // COMMERCE-ADMIN-UX-01: «Equipos nuevos» is this screen opened with `?line=new`.
  // Read once on the client; the panel only mounts once the branches arrive.
  const [initialFilters] = useState(() => (typeof window === "undefined" ? {} : unitFiltersFromSearch(window.location.search)));

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Equipos"
          description="Equipos con número de serie: cada uno es una unidad del stock de su producto, con su serie, su IMEI y su estado."
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
          <StockUnitsPanel
            branch={scope.branch}
            branches={(scope.access?.results ?? []).map((b) => ({ id: b.id, name: b.name }))}
            canAdjust={canAdjust}
            canSeeCost={access.can("costs.view", ["admin", "superadmin"])}
            initialFilters={initialFilters}
          />
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function StockUnitsPage() {
  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <UnitsContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
