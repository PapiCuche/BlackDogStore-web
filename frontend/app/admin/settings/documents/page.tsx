"use client";

/**
 * Ajustes › Documentos.
 *
 * La vista previa exige `company.manage`, la misma capacidad que el servidor
 * comprueba. Sin ella la pantalla no se monta y no se pide ningún PDF. Es una
 * comodidad de la interfaz: el servidor responde 403 por su cuenta.
 */

import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { buildInternalAccess } from "../../lib/internal-access";
import { DocumentPreview } from "./DocumentPreview";

function DocumentsContent({ ctx }: { ctx: InternalContext }) {
  const canManage = buildInternalAccess(ctx.user, ctx.dashboard).can("company.manage");
  const companyId = ctx.dashboard?.company?.id ?? ctx.selectedCompanyId ?? null;

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="min-w-0 space-y-6">
        <PageHeader
          eyebrow="Ajustes"
          title="Documentos"
          description="Mira cómo salen los papeles impresos de la empresa, con datos de muestra."
        />
        {canManage ? (
          <DocumentPreview companyId={companyId} />
        ) : (
          <div role="alert" className="rounded-xl border border-bd-border bg-surface p-6 text-sm text-muted">
            <p className="font-semibold text-foreground">Sin acceso</p>
            <p className="mt-1">
              Tu cuenta no tiene permiso para ver la vista previa de documentos de esta empresa.
              Pídela a quien administra la empresa (capacidad <code>company.manage</code>).
            </p>
          </div>
        )}
      </div>
    </AdminShell>
  );
}

export default function DocumentsPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <DocumentsContent ctx={ctx} />}
    </InternalControlGuard>
  );
}
