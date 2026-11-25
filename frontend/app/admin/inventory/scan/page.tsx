"use client";

/**
 * Inventario › Registrar equipo por escaneo — SCAN-TO-STOCK-01.
 *
 * `inventory.adjust` para llegar, como «Recibir mercadería» y «Registrar equipos»: registrar un equipo mueve el stock.
 * Lo demás (crear una variante, el costo, el precio y la garantía de un seminuevo, entregar a un pedido) lo dice el
 * servidor en `can` y lo vuelve a comprobar al confirmar.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import { ScanIntake } from "../../components/ScanIntake";
import type { InternalAccess } from "../../lib/internal-access";
import { useBranchScope } from "../../lib/use-branch-scope";

function ScanContent({ access }: { access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: false });
  const companyId = access.dashboard?.company?.id ?? null;
  const branches = (scope.access?.results ?? []).map((b) => ({ id: b.id, name: b.name }));
  const canPrintLabels = access.can("inventory.labels.print", ["inventory", "admin", "superadmin"]);

  return (
    <AdminShell user={access.user}>
      <div className="min-w-0 space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Registrar equipo por escaneo"
          description="Elige la sucursal y el modelo (o escanea el código comercial de la caja), lee el IMEI 1, el IMEI 2 y la serie con el lector, revisa y confirma. Nada se registra sin confirmar."
        />
        {scope.error ? <ErrorBox message={scope.error} /> : scope.loading ? <Spinner label="Cargando sucursales…" /> : branches.length === 0 ? (
          <p className="text-sm text-muted">Todavía no tienes ninguna sucursal asignada.</p>
        ) : (
          <ScanIntake
            key={companyId ?? "none"}
            companyId={companyId}
            branches={branches}
            defaultBranch={scope.access?.default_branch?.id ?? null}
            canPrintLabels={canPrintLabels}
          />
        )}
      </div>
    </AdminShell>
  );
}

export default function ScanIntakePage() {
  return (
    <AccessGuard
      capability="inventory.adjust"
      legacyRoles={["inventory", "admin", "superadmin"]}
      deniedMessage="Registrar un equipo mueve el stock y pide el permiso de ajustar inventario. Pídeselo a quien administra el inventario."
    >
      {(access) => <ScanContent access={access} />}
    </AccessGuard>
  );
}
