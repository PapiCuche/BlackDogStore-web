"use client";

/**
 * Inventario › Seminuevos — DEVICE-COMMERCE-01 · DC2, INV-RECEIVE-PRICE-03.
 *
 * `inventory.view` para llegar, como «Equipos». Tres autoridades separadas (`lib/used-units-access.ts`), que el
 * servidor comprueba otra vez: datos y fotos del equipo con `inventory.adjust`; su precio de venta con
 * `products.manage` (y `inventory.adjust`, porque vive en la ficha del equipo); el costo sólo con `costs.view`.
 *
 * `?product=<id>&precio=sin` llega desde el resumen de una recepción: los equipos de ese artículo sin precio.
 */

import { useState } from "react";

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { BranchSelector } from "../../components/BranchSelector";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import { UsedUnitsPanel } from "../../components/UsedUnitsPanel";
import type { InternalAccess } from "../../lib/internal-access";
import { usedUnitsAccess, usedUnitsLinkFilters } from "../../lib/used-units-access";
import { useBranchScope } from "../../lib/use-branch-scope";
import type { AuthUser } from "../../../lib/auth";

function UsedContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });
  const [filters] = useState(() => usedUnitsLinkFilters(typeof window === "undefined" ? "" : window.location.search));
  const can = usedUnitsAccess(access);

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Seminuevos"
          description="Los equipos seminuevos, uno por uno: su precio de venta, su batería, su estado y sus fotos se completan aquí."
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
            canEdit={can.canEdit}
            canManagePrice={can.canManagePrice}
            canSeeCost={can.canSeeCost}
            initialProduct={filters.product}
            initialPriced={filters.priced}
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
