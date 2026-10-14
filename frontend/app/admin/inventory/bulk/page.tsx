"use client";

/**
 * Gestión masiva de inventario — INVENTORY-UNIFIED-01.
 *
 * Un centro: importar, exportar, imágenes en lote e historial. No es una carga
 * nueva: las tres cargas (productos, stock, equipos) siguen siendo tres, con
 * sus pantallas y sus rutas, y desde aquí se llega a ellas.
 *
 * Entra quien tenga CUALQUIERA de las cuatro capacidades; cada sección se
 * ofrece según la suya. La autoridad es del servidor en cada petición.
 */

import { AdminShell } from "../../components/AdminShell";
import { BulkInventoryCentre } from "../../components/BulkInventoryCentre";
import { InternalControlGuard } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { buildInternalAccess } from "../../lib/internal-access";

export default function BulkInventoryPage() {
  return (
    <InternalControlGuard>
      {(ctx) => {
        const access = buildInternalAccess(ctx.user, ctx.dashboard);
        return (
          <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
            <div className="space-y-8">
              <PageHeader
                eyebrow="Inventario"
                title="Gestión masiva de inventario"
                description="Importar, exportar, añadir imágenes en lote y revisar lo que se cargó."
              />
              <BulkInventoryCentre
                companyId={ctx.selectedCompanyId}
                can={{
                  productsManage: access.can("products.manage", ["admin", "superadmin"]),
                  inventoryAdjust: access.can("inventory.adjust", ["inventory", "admin", "superadmin"]),
                  productsView: access.can("products.view", ["inventory", "sales", "admin", "superadmin"]),
                  inventoryView: access.can("inventory.view", ["inventory", "admin", "superadmin"]),
                }}
              />
            </div>
          </AdminShell>
        );
      }}
    </InternalControlGuard>
  );
}
