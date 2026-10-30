"use client";

/**
 * Inventario › Más herramientas — NAV-UX-02.
 *
 * Entra quien tenga permiso para alguna herramienta (`anyCapabilities` del módulo); la página ofrece cada una
 * según la suya y las pantallas a las que lleva piden y comprueban las suyas.
 */

import { AdminShell, buildAccessContext } from "../../components/AdminShell";
import { InternalControlGuard } from "../../components/InternalControlGuard";
import { InventoryToolsHub } from "../../components/InventoryToolsHub";
import { PageHeader } from "../../components/internal-ui";

export default function InventoryToolsPage() {
  return (
    <InternalControlGuard>
      {(ctx) => (
        <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
          <div className="space-y-6">
            <PageHeader
              eyebrow="Inventario"
              title="Más herramientas"
              description="Lo que no se hace cada día: cargas con Excel, análisis y reposición. Para recibir mercadería usa «Recibir mercadería»; para contar, «Recuentos»."
            />
            <InventoryToolsHub access={buildAccessContext(ctx.user, ctx.dashboard)} />
          </div>
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}
