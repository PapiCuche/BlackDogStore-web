"use client";

/**
 * Ventas › Consultar disponibilidad — COMMERCE-ADMIN-UX-01.
 *
 * `sales.pos.use`: whoever may sell at the counter may ask where a product is.
 * No legacy bridge — like the till, the endpoint asks for a Membership — so the
 * guard's legacy list is empty. The server checks the capability, the company
 * and the branches on every request.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { AvailabilityPanel } from "../../components/AvailabilityPanel";
import { PageHeader } from "../../components/internal-ui";
import type { AuthUser } from "../../../lib/auth";

function AvailabilityContent({ user }: { user: AuthUser }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Ventas"
          title="Consultar disponibilidad"
          description="Qué hay en tus sucursales —disponible, apartado, vendido o de baja— y, aparte, lo que un proveedor puede traer bajo pedido. Lo del proveedor nunca se suma al stock propio."
        />
        <AvailabilityPanel />
      </div>
    </AdminShell>
  );
}

export default function AvailabilityPage() {
  return (
    <AccessGuard capability="sales.pos.use" legacyRoles={[]}>
      {(access) => <AvailabilityContent user={access.user} />}
    </AccessGuard>
  );
}
