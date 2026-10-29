"use client";

/**
 * Inventario › Centro de operaciones — INV-OPS-01.
 *
 * `inventory.view` para llegar. Cada tarea de las tarjetas pide su propia
 * capacidad y el servidor la comprueba otra vez en cada operación.
 */

import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { InventoryOperationsHub } from "../../components/InventoryOperationsHub";
import { PageHeader } from "../../components/internal-ui";
import type { InternalAccess } from "../../lib/internal-access";

function OperationsContent({ access }: { access: InternalAccess }) {
  return (
    <AdminShell user={access.user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario"
          title="Centro de operaciones"
          description="Lo que haces cada día con la mercadería: recibirla, contarla, etiquetarla y consultarla. Elige una tarea."
        />
        <InventoryOperationsHub access={access} />
      </div>
    </AdminShell>
  );
}

export default function InventoryOperationsPage() {
  return (
    <AccessGuard capability="inventory.view" legacyRoles={["inventory", "admin", "superadmin"]}>
      {(access) => <OperationsContent access={access} />}
    </AccessGuard>
  );
}
