"use client";

/**
 * Productos › Publicación e-commerce — COMMERCE-ADMIN-UX-01.
 *
 * `products.view` para llegar (como la lista de productos); publicar u ocultar
 * exige `products.manage`, que el servidor comprueba en cada acción.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { PublicationCentre } from "../../components/PublicationCentre";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";
import type { AuthUser } from "../../../lib/auth";

function PublicationContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Productos"
          title="Publicación e-commerce"
          description="Qué se ve en la tienda en línea: equipos nuevos, seminuevos, accesorios, repuestos y lo que se vende bajo pedido. Lo que no está listo no se publica, y se dice por qué."
        />
        <PublicationCentre
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
      </div>
    </AdminShell>
  );
}

export default function PublicationPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <PublicationContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
