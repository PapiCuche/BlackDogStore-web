"use client";

/**
 * Productos › Proveedores › Ofertas › «Actualizar lista de proveedor» — CATALOG-COMMERCIAL-01 · B1-1.
 *
 * `products.view` para llegar y leer cómo se interpreta una lista; guardar ofertas, crear una variante o registrar
 * un alias exige `products.manage`; un costo, `costs.view`. El servidor comprueba cada cosa; aquí sólo se decide qué
 * controles se pintan. «Recibir mercadería» (stock propio) no cambia: es otro camino.
 */

import Link from "next/link";

import { AccessGuard } from "../../../../components/AccessGuard";
import { AdminShell } from "../../../../components/AdminShell";
import { SupplierListAssistant } from "../../../../components/SupplierListAssistant";
import { PageHeader, internalButtonClass } from "../../../../components/internal-ui";
import type { InternalAccess } from "../../../../lib/internal-access";
import type { AuthUser } from "../../../../../lib/auth";

function AssistantContent({ user, access }: { user: AuthUser; access: InternalAccess }) {
  return (
    <AdminShell user={user}>
      <div className="min-w-0 space-y-6">
        <PageHeader
          eyebrow="Productos › Proveedores › Ofertas"
          title="Actualizar lista de proveedor"
          description="Pega la lista de precios que el proveedor mandó por WhatsApp. Cada línea se lee con el vocabulario de tu catálogo; tú decides qué es cada importe, escribes el precio comercial y eliges qué ofertas guardar. Lo que tenga dudas queda en pantalla."
          actions={<Link href="/admin/products/suppliers/offers" className={internalButtonClass}>Volver a ofertas</Link>}
        />
        <SupplierListAssistant
          canManage={access.can("products.manage", ["admin", "superadmin"])}
          canSeeCosts={access.can("costs.view", ["admin", "superadmin"])}
          canViewCompany={access.can("company.view", ["admin", "superadmin"])}
          companyId={access.dashboard?.company?.id ?? null}
        />
      </div>
    </AdminShell>
  );
}

export default function SupplierListAssistantPage() {
  return (
    <AccessGuard capability="products.view" legacyRoles={["inventory", "sales", "admin", "superadmin"]}>
      {(access) => <AssistantContent user={access.user} access={access} />}
    </AccessGuard>
  );
}
