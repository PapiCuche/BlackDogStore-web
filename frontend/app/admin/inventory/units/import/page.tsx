"use client";

/**
 * Inventario › Equipos › Cargar desde Excel.
 *
 * Se llega con `inventory.view`, como al resto de Equipos; cargar exige
 * `inventory.adjust`, que el servidor comprueba al previsualizar y otra vez al
 * registrar. Cada fila entra en una sucursal de quien carga, o es un error.
 */

import Link from "next/link";

import { AccessGuard } from "../../../components/AccessGuard";
import { AdminShell } from "../../../components/AdminShell";
import { ErrorBox, Spinner } from "../../../components/InventoryUi";
import { PageHeader, internalButtonClass } from "../../../components/internal-ui";
import { StockUnitImport } from "../../../components/StockUnitImport";
import type { InternalAccess } from "../../../lib/internal-access";
import { useBranchScope } from "../../../lib/use-branch-scope";
import type { AuthUser } from "../../../../lib/auth";

function ImportContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: true });
  const canAdjust = access.can("inventory.adjust", ["inventory", "admin", "superadmin"]);

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario › Equipos"
          title="Cargar equipos desde Excel"
          description="Muchos equipos con número de serie de una vez. Una fila del archivo es un equipo físico."
          actions={<Link className={internalButtonClass} href="/admin/inventory/units">← Equipos</Link>}
        />
        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {scope.loading ? <Spinner label="Cargando sucursales…" /> : null}
        {!canAdjust ? (
          <p className="text-sm text-muted">
            Para registrar equipos hace falta el permiso de ajustar inventario.
          </p>
        ) : null}
        {canAdjust && scope.ready ? (
          <StockUnitImport branches={(scope.access?.results ?? []).map((b) => ({ id: b.id, name: b.name }))} />
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function StockUnitImportPage() {
  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <ImportContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
