"use client";

/**
 * Configuración › Integraciones › Series fiscales (SUNAT-SERIES-PANEL, C3).
 *
 * SÓLO EL MASTER, como la consola de SUNAT: qué series declara una empresa ante SUNAT es
 * configuración tributaria de la instalación. El servidor responde 403 a cualquier otra
 * cuenta; aquí la pantalla ni se monta.
 *
 * Las reglas las pone el servidor y aquí sólo se dicen: BETA y PRODUCCIÓN son series
 * distintas; una activa por empresa o sucursal, tipo y ambiente; el correlativo inicial se
 * corrige sólo mientras no se haya emitido nada con la serie, y una serie usada no se borra.
 */

import Link from "next/link";
import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { FiscalSeriesPanel } from "./FiscalSeriesPanel";

function FiscalSeriesContent({ ctx }: { ctx: InternalContext }) {
  const isMaster = Boolean(ctx.dashboard?.access.is_platform_admin);
  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Configuración · SUNAT"
          title="Series fiscales"
          description="Las series con que cada empresa emite facturas, boletas y notas, en BETA y en PRODUCCIÓN."
        />
        <Link href="/admin/settings/integrations" className="text-sm text-muted underline">Volver a Integraciones</Link>
        {isMaster ? <FiscalSeriesPanel /> : (
          <p className="text-sm text-muted">Esta sección no está disponible para tu cuenta.</p>
        )}
      </div>
    </AdminShell>
  );
}

export default function FiscalSeriesPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <FiscalSeriesContent ctx={ctx} />}
    </InternalControlGuard>
  );
}
