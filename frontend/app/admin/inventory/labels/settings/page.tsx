"use client";

/**
 * Inventario › Etiquetas › Configuración — LABEL-03b.
 *
 * Verla pide «Ver empresa» (`company.view`); cambiarla, «Administrar empresa»
 * (`company.manage`), que el servidor comunica como `can_manage`. La empresa es
 * la que el control interno tiene elegida; el servidor la vuelve a comprobar.
 */

import Link from "next/link";

import { AdminShell } from "../../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../../components/InternalControlGuard";
import { PageHeader } from "../../../components/internal-ui";
import { LabelSettingsPanel } from "./LabelSettingsPanel";

function LabelSettingsContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? ctx.selectedCompanyId ?? null;
  return (
    <AdminShell user={ctx.user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Inventario · Etiquetas"
          title="Configuración de etiquetas"
          description="El prefijo del código interno de los equipos y qué código lleva su etiqueta."
          actions={
            <Link
              href="/admin/inventory/labels"
              className="rounded-lg border border-bd-border px-3 py-2 text-sm font-semibold text-foreground hover:border-foreground/25"
            >
              Ir a etiquetas
            </Link>
          }
        />
        <LabelSettingsPanel key={companyId ?? "none"} companyId={companyId} />
      </div>
    </AdminShell>
  );
}

export default function LabelSettingsPage() {
  return <InternalControlGuard>{(ctx) => <LabelSettingsContent ctx={ctx} />}</InternalControlGuard>;
}
