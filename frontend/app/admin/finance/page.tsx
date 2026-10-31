"use client";

/**
 * Finanzas → Pagos fijos y vencimientos — FIN-REMIND-01.
 *
 * `finance.view` opens the screen; everything that changes something asks for its own capability
 * (`finance.payments.record`, `finance.payments.reverse`, `finance.manage`, `finance.automation.manage`) and the
 * server re-checks each one. This is also where the bell sends people: `/admin/finance?obligation=<id>`.
 */
import { Suspense } from "react";
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import { CAP_VIEW, hasFinanceCapability } from "../lib/finance-api";
import { FinanceCentre } from "./_components/FinanceCentre";

function NoAccess() {
  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-8 text-center">
      <p className="text-xs font-semibold uppercase tracking-[0.3em] text-muted">Finanzas</p>
      <h1 className="mt-3 font-display text-xl font-bold text-foreground">Sin acceso a pagos fijos y vencimientos</h1>
      <p className="mt-2 text-sm text-muted">
        Tu cuenta no tiene el permiso «Ver finanzas» en esta empresa. Pídelo a quien administra la empresa.
      </p>
    </div>
  );
}

function NoCompany() {
  return (
    <div className="rounded-2xl border border-bd-border bg-surface p-8 text-center">
      <p className="text-xs font-semibold uppercase tracking-[0.3em] text-muted">Finanzas</p>
      <h1 className="mt-3 font-display text-xl font-bold text-foreground">Selecciona una empresa</h1>
      <p className="mt-2 text-sm text-muted">Elige la empresa en la barra superior para ver sus pagos fijos y vencimientos.</p>
    </div>
  );
}

function FinanceContent({ ctx }: { ctx: InternalContext }) {
  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      {!ctx.dashboard?.company ? (
        <NoCompany />
      ) : hasFinanceCapability(ctx.dashboard, CAP_VIEW) ? (
        // useSearchParams (the deep link) needs a Suspense boundary on a statically built page.
        <Suspense fallback={<p role="status" className="text-sm text-muted">Cargando…</p>}>
          <FinanceCentre companyId={ctx.selectedCompanyId ?? ctx.dashboard.company.id} dashboard={ctx.dashboard} />
        </Suspense>
      ) : (
        <NoAccess />
      )}
    </AdminShell>
  );
}

export default function FinancePage() {
  return <InternalControlGuard>{(ctx) => <FinanceContent ctx={ctx} />}</InternalControlGuard>;
}
