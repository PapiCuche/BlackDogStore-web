"use client";

/**
 * Inventario › Etiquetas y códigos de barras — LABEL-03.
 *
 * `inventory.labels.print` para llegar (LABEL-02). El IMEI sólo va al PDF con el
 * perfil «IMEI 1» y `inventory.labels.sensitive`; la vista previa lo enmascara.
 * Una sucursal concreta: una etiqueta es de lo que hay en un estante, no de un
 * agregado. El servidor vuelve a comprobar empresa, sucursal y permisos.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { BranchSelector } from "../../components/BranchSelector";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import { useBranchScope } from "../../lib/use-branch-scope";
import type { AuthUser } from "../../../lib/auth";
import { LabelsWorkbench } from "./LabelsWorkbench";

function LabelsContent({ user }: { user: AuthUser }) {
  const scope = useBranchScope();
  const branch = typeof scope.branch === "number" ? scope.branch : null;

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Etiquetas y códigos de barras"
          description="Vista previa y PDF a escala real: accesorios de 30×20 mm y equipos de 50×25 mm."
          actions={
            scope.access ? (
              <BranchSelector access={scope.access} value={scope.branch} onChange={scope.setBranch} allowAll={false} />
            ) : null
          }
        />
        {scope.error ? <ErrorBox message={scope.error} /> : null}
        {scope.loading ? <Spinner label="Cargando sucursales…" /> : null}
        {!scope.loading && branch === null && !scope.error ? (
          <p className="text-sm text-muted">Elige una sucursal.</p>
        ) : null}
        {branch !== null ? <LabelsWorkbench key={branch} branch={branch} /> : null}
      </div>
    </AdminShell>
  );
}

export default function InventoryLabelsPage() {
  return (
    <AccessGuard capability="inventory.labels.print" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <LabelsContent user={access.user} />}
    </AccessGuard>
  );
}
