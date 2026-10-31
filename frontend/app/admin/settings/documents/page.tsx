"use client";

/**
 * Ajustes › Documentos.
 *
 * La vista previa exige `company.manage`, la misma capacidad que el servidor
 * comprueba. Sin ella la pantalla no se monta y no se pide ningún PDF. Es una
 * comodidad de la interfaz: el servidor responde 403 por su cuenta.
 *
 * DOC-DESIGN-03: el diseño de los documentos internos se edita aquí, junto a la
 * vista previa. El borrador vive en esta página (nunca en el navegador ni en el
 * servidor) hasta que se confirma el guardado.
 */

import { useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { buildInternalAccess } from "../../lib/internal-access";
import {
  changedFields,
  draftProblems,
  fetchDocumentDesign,
  type DocumentDesign,
  type DocumentDesignSettings,
} from "../../lib/document-design-api";
import { DocumentDesignForm } from "./DocumentDesignForm";
import { DocumentPreview } from "./DocumentPreview";

const BLOCKED = "Corrige los campos marcados del diseño para ver la vista previa.";

function DocumentsWorkspace({ companyId }: { companyId: number | null }) {
  const [settings, setSettings] = useState<DocumentDesignSettings | null>(null);
  const [draft, setDraft] = useState<DocumentDesign | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await fetchDocumentDesign(companyId);
        if (cancelled) return;
        setSettings(loaded);
        setDraft(loaded.design);
        setLoadError(null);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "No se pudo cargar el diseño.");
      }
    })();
    return () => { cancelled = true; };
  }, [companyId]);

  const changes = settings && draft ? changedFields(settings.design, draft) : null;
  const blocked = settings && draft && Object.keys(draftProblems(draft, settings.limits)).length ? BLOCKED : null;

  let aside;
  if (loadError) {
    aside = (
      <p role="alert" className="rounded-xl border border-danger/40 bg-surface p-5 text-sm text-danger">
        {loadError} La vista previa sigue mostrando el diseño guardado.
      </p>
    );
  } else if (!settings || !draft) {
    aside = <p className="rounded-xl border border-bd-border bg-surface p-5 text-sm text-muted">Cargando el diseño…</p>;
  } else {
    aside = (
      <DocumentDesignForm
        companyId={companyId}
        settings={settings}
        draft={draft}
        onDraft={setDraft}
        onSaved={(next) => { setSettings(next); setDraft(next.design); }}
      />
    );
  }

  return <DocumentPreview companyId={companyId} draft={changes} blocked={blocked} aside={aside} />;
}

function DocumentsContent({ ctx }: { ctx: InternalContext }) {
  const canManage = buildInternalAccess(ctx.user, ctx.dashboard).can("company.manage");
  const companyId = ctx.dashboard?.company?.id ?? ctx.selectedCompanyId ?? null;

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="min-w-0 space-y-6">
        <PageHeader
          eyebrow="Ajustes"
          title="Documentos"
          description="Elige cómo salen los documentos internos de la empresa y míralo en una muestra antes de guardar."
        />
        {canManage ? (
          <DocumentsWorkspace companyId={companyId} />
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
