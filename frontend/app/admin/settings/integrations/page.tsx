"use client";

/**
 * Configuración › Integraciones.
 *
 * SÓLO EL MASTER. No hay una capacidad de empresa que abra esta pantalla: las
 * credenciales de los servicios externos son de la instalación. El servidor
 * responde 403 a cualquier otra cuenta en cada una de sus rutas; aquí, además,
 * la consola ni se monta, así que no se hace ninguna petición.
 */

import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { IntegrationsConsole } from "./IntegrationsConsole";

function IntegrationsContent({ ctx }: { ctx: InternalContext }) {
  const isMaster = Boolean(ctx.dashboard?.access.is_platform_admin);

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Configuración"
          title="Integraciones"
          description="Correo, pasarela de pago, WhatsApp, Google y SUNAT: se configuran, se prueban y se activan aquí, sin tocar el servidor."
        />
        {isMaster ? (
          <IntegrationsConsole />
        ) : (
          <p className="text-sm text-muted">Esta sección no está disponible para tu cuenta.</p>
        )}
      </div>
    </AdminShell>
  );
}

export default function IntegrationsPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <IntegrationsContent ctx={ctx} />}
    </InternalControlGuard>
  );
}
