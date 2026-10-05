"use client";

/**
 * Administración › Mensajería.
 *
 * `settings.view` para llegar, `settings.manage` para cambiar. El servidor
 * aplica las dos por su cuenta; aquí sólo se decide qué controles se pintan.
 */

import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { WhatsAppSettings } from "./WhatsAppSettings";

function MessagingContent({ ctx }: { ctx: InternalContext }) {
  const slug = ctx.dashboard?.company?.slug ?? null;
  const caps = ctx.dashboard?.access.capabilities ?? [];

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Administración"
          title="Mensajería"
          description="Avisos al cliente por WhatsApp: cuándo se envían, con qué plantilla y a quién."
        />
        {!slug ? (
          <p className="text-sm text-muted">Selecciona una empresa.</p>
        ) : !caps.includes("settings.view") ? (
          <p className="text-sm text-muted">Tu cuenta no tiene permiso para ver esta configuración.</p>
        ) : (
          <WhatsAppSettings slug={slug} canManage={caps.includes("settings.manage")} />
        )}
      </div>
    </AdminShell>
  );
}

export default function MessagingPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <MessagingContent ctx={ctx} />}
    </InternalControlGuard>
  );
}
