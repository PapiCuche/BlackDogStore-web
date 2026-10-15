"use client";

/**
 * Productos › Proveedores — DEVICE-COMMERCE-01 · DC3.
 *
 * `products.view` para llegar; crear o cambiar exige `products.manage`. Lo
 * pendiente de liquidar sólo se pinta con `costs.view`. El servidor comprueba
 * las tres; aquí sólo se decide qué controles se pintan.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { SuppliersManager } from "../../components/SuppliersManager";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";

function SuppliersContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Proveedores"
          description="Quién te abastece de lo que vendes bajo pedido. Sus ofertas se gestionan en «Ofertas de proveedores»."
        />
        <SuppliersManager
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          canSeeCosts={access.can("costs.view", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
      </div>
    </AdminShell>
  );
}

export default function SuppliersPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <SuppliersContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
