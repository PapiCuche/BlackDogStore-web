"use client";

/**
 * Productos › Proveedores › Ofertas — DEVICE-COMMERCE-01 · DC3.
 *
 * `products.view` para llegar; crear, cambiar, desactivar o subir el Excel
 * exige `products.manage`. El costo acordado sólo llega con `costs.view`. El
 * servidor comprueba cada cosa; aquí sólo se decide qué controles se pintan.
 */

import { AccessGuard } from "../../../components/AccessGuard";
import { AdminShell } from "../../../components/AdminShell";
import { SupplierOffersManager } from "../../../components/SupplierOffersManager";
import { PageHeader } from "../../../components/internal-ui";
import type { InternalAccess } from "../../../lib/internal-access";
import type { AuthUser } from "../../../../lib/auth";

function OffersContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos › Proveedores"
          title="Ofertas de proveedores"
          description="Qué vende cada proveedor, a qué precio, cuántas unidades confirmó y en cuántos días entrega. Es lo que permite vender bajo pedido."
        />
        <SupplierOffersManager
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          canSeeCosts={access.can("costs.view", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
      </div>
    </AdminShell>
  );
}

export default function SupplierOffersPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <OffersContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
