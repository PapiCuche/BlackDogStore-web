"use client";

/**
 * Inventario › Recibir mercadería — INV-OPS-01.
 *
 * Asistente de cuatro pasos. Recibir mueve stock, así que se pide
 * `inventory.adjust` desde la puerta (el servidor la comprueba otra vez al
 * revisar y al confirmar). Los costos sólo se piden con `costs.view`; «crear
 * artículo» sólo se ofrece con `products.manage`.
 */

import Link from "next/link";
import { useState } from "react";
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { ErrorBox, Spinner } from "../../components/InventoryUi";
import { PageHeader } from "../../components/internal-ui";
import { ReceivingWizard } from "../../components/ReceivingWizard";
import type { InternalAccess } from "../../lib/internal-access";
import { productIdFromSearch } from "../../lib/receive-links";
import { useBranchScope } from "../../lib/use-branch-scope";

function ReceiveContent({ access }: { access: InternalAccess }) {
  const scope = useBranchScope({ preferAggregate: false });
  const [productId] = useState(() => (typeof window === "undefined" ? null : productIdFromSearch(window.location.search)));
  const can = {
    costs: access.can("costs.view", ["admin", "superadmin"]),
    manageProducts: access.can("products.manage", ["admin", "superadmin"]),
    viewProducts: access.can("products.view", ["admin", "superadmin"]),
    labels: access.can("inventory.labels.print", ["inventory", "admin", "superadmin"]),
  };
  const companyId = access.dashboard?.company?.id ?? null;
  const branches = (scope.access?.results ?? []).map((b) => ({ id: b.id, name: b.name }));
  return (
    <AdminShell user={access.user}>
      <div className="space-y-6">
        <nav aria-label="Ruta de navegación" className="text-xs text-muted">
          <Link href="/admin/inventory/operations" className="underline underline-offset-4">Inventario</Link>
          <span aria-hidden="true"> / </span>
          <span>Recibir mercadería</span>
        </nav>
        <PageHeader
          eyebrow="Inventario"
          title="Recibir mercadería"
          description="Registra lo que llegó en cuatro pasos: de dónde viene, qué trae, una revisión final y las etiquetas listas."
          actions={<Link href="/admin/inventory/operations" className="text-sm font-semibold underline underline-offset-4">Volver al centro de operaciones</Link>}
        />
        {/* The product search (and the supplier list) need `products.view`; without it the server answers 403. */}
        {!can.viewProducts ? (
          <div role="note" className="rounded-xl border border-warning-border bg-warning-surface p-5 text-sm text-foreground">
            <p className="font-semibold">Para recibir necesitas poder ver productos.</p>
            <p className="mt-1 text-muted">
              Recibir mercadería empieza buscando el artículo que llegó, y tu usuario todavía no puede ver el
              catálogo. Pide a quien administra el sistema que te dé el permiso «Ver productos».
            </p>
          </div>
        ) : scope.error ? <ErrorBox message={scope.error} /> : scope.loading ? <Spinner label="Cargando sucursales…" /> : (
          <ReceivingWizard
            // Another company is another wizard: nothing typed for the first one carries over.
            key={companyId ?? "none"}
            access={can}
            scope={{ user: access.user.id, company: companyId }}
            branches={branches}
            defaultBranch={scope.access?.default_branch?.id ?? null}
            initialProductId={productId}
          />
        )}
      </div>
    </AdminShell>
  );
}

export default function ReceiveInventoryPage() {
  return (
    <AccessGuard
      capability="inventory.adjust"
      legacyRoles={["inventory", "admin", "superadmin"]}
      deniedMessage="Recibir mercadería mueve el stock y pide el permiso de ajustar inventario. Pídeselo a quien administra el inventario."
    >
      {(access) => <ReceiveContent access={access} />}
    </AccessGuard>
  );
}
