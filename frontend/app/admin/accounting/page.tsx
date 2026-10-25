"use client";

/**
 * Contabilidad → Centro contable — ACCOUNTING-03 (`docs/adr-accounting.md`).
 *
 * Read-only. Three views of the same period — every operation once (A), the
 * fiscal documents and their real SUNAT circuit (B), what is internal, pending
 * or inconsistent (C) — over the API of ACCOUNTING-02. The server decides who
 * sees what (`accounting.view`), whether buyers' documents are masked
 * (`accounting.export`), and which rules are still provisional; this screen
 * shows its answer and invents nothing. It is not complete accounting, and
 * nothing in it is a filed declaration: it says so on every view.
 */
import { AdminShell } from "../components/AdminShell";
import { InternalControlGuard, type InternalContext } from "../components/InternalControlGuard";
import { AccountingCentre } from "./_components/AccountingCentre";

function AccountingContent({ ctx }: { ctx: InternalContext }) {
  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <AccountingCentre companyId={ctx.selectedCompanyId} />
    </AdminShell>
  );
}

export default function AccountingPage() {
  return <InternalControlGuard>{(ctx) => <AccountingContent ctx={ctx} />}</InternalControlGuard>;
}
