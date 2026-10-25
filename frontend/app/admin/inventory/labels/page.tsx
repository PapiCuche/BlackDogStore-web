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
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";
import { LabelsWorkbench } from "./LabelsWorkbench";

const INVENTORY_VIEW_LEGACY = ["inventory", "admin", "superadmin"];

function Header({ actions }: { actions?: React.ReactNode }) {
  return (
    <PageHeader
      eyebrow="Inventario"
      title="Etiquetas y códigos de barras"
      description="Vista previa y PDF a escala real: accesorios de 30×20 mm y equipos de 50×25 mm."
      actions={actions}
    />
  );
}

/**
 * The sucursales, the stock and the devices all come from inventory endpoints
 * that require «Ver inventario». Holding only «Imprimir etiquetas» opens this
 * screen, so it says what is missing and calls nothing that would answer 403.
 */
function WithoutInventoryView() {
  return (
    <div role="note" aria-label="Falta Ver inventario" className="rounded-xl border border-bd-border bg-surface p-5 text-sm text-foreground">
      <p className="font-semibold">Puedes generar etiquetas, pero no elegir qué etiquetar.</p>
      <p className="mt-1 text-muted">
        Para ver las sucursales, los productos y los equipos necesitas también el permiso «Ver inventario»
        (<span className="font-mono">inventory.view</span>). Pídeselo a quien administra la empresa.
      </p>
    </div>
  );
}

function BranchLabels() {
  const scope = useBranchScope();
  const branch = typeof scope.branch === "number" ? scope.branch : null;
  return (
    <>
      <Header
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
    </>
  );
}

function LabelsContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        {access.can("inventory.view", INVENTORY_VIEW_LEGACY) ? (
          <BranchLabels />
        ) : (
          <>
            <Header />
            <WithoutInventoryView />
          </>
        )}
      </div>
    </AdminShell>
  );
}

export default function InventoryLabelsPage() {
  return (
    <AccessGuard capability="inventory.labels.print" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <LabelsContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
