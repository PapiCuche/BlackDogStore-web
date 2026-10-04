"use client";

/**
 * Administración › Impresoras.
 *
 * Configurar impresoras y agentes es configurar la empresa (`company.manage`).
 * La cola la ve también quien opera la caja. El servidor aplica las dos reglas
 * por su cuenta; aquí sólo se decide qué formularios se pintan.
 */

import { useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../../components/InternalControlGuard";
import { PageHeader } from "../../components/internal-ui";
import { fetchCompanyBranches, fetchCompanyConfiguration } from "../../lib/internal-api";
import { PrintingSettings } from "./PrintingSettings";

function PrintingContent({ user, ctx }: { user: InternalContext["user"]; ctx: InternalContext }) {
  const companyId = ctx.dashboard?.company?.id ?? null;
  const [branches, setBranches] = useState<{ id: number; name: string }[] | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [rows, config] = await Promise.all([
          fetchCompanyBranches(companyId),
          // Sin permiso sobre la configuración, la pantalla sigue sirviendo
          // para ver la cola: no es un error.
          fetchCompanyConfiguration(companyId).catch(() => null),
        ]);
        if (cancelled) return;
        setBranches(rows.results.filter((b) => b.is_active).map((b) => ({ id: b.id, name: b.name })));
        setCanManage(config?.can_manage ?? false);
      } catch (err) {
        if (!cancelled) {
          setBranches([]);
          setError(err instanceof Error ? err.message : "No se pudieron cargar las sucursales.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [companyId]);

  return (
    <AdminShell user={user}>
      <div className="space-y-6">
        <PageHeader
          eyebrow="Administración"
          title="Impresoras"
          description="Las térmicas de cada local, el agente que las alimenta y los tickets recientes."
        />
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
        {branches === null ? (
          <p className="text-sm text-muted">Cargando…</p>
        ) : (
          <PrintingSettings companyId={companyId} branches={branches} canManage={canManage} />
        )}
      </div>
    </AdminShell>
  );
}

export default function PrintingPage() {
  return (
    <InternalControlGuard>
      {(ctx) => <PrintingContent user={ctx.user} ctx={ctx} />}
    </InternalControlGuard>
  );
}
