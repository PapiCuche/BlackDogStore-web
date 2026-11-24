"use client";

/** Nueva cotización — SALES-QUOTE-01. Elaborarla pide `sales.quotes.manage`; el servidor lo vuelve a decidir. */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AccessGuard } from "../../../components/AccessGuard";
import { AdminShell } from "../../../components/AdminShell";
import { PageHeader } from "../../../components/internal-ui";
import { QuoteEditor } from "../../../components/sales-quotes/QuoteEditor";
import { emptyDraft, fetchQuoteContext, type QuoteContext } from "../../../lib/sales-quotes-api";

function NewQuote() {
  const router = useRouter();
  const [context, setContext] = useState<QuoteContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchQuoteContext().then(setContext).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "No se pudo abrir cotizaciones.");
    });
  }, []);
  if (error) return <p role="alert" className="text-sm text-danger">{error}</p>;
  if (!context) return <p className="text-sm text-muted">Cargando…</p>;
  return (
    <QuoteEditor
      context={context}
      initial={emptyDraft(context)}
      quoteId={null}
      onSaved={(quote) => router.push(`/admin/sales/quotes/${quote.id}`)}
    />
  );
}

export default function NewSalesQuotePage() {
  return (
    <AccessGuard capability="sales.quotes.manage" legacyRoles={[]}>
      {(access) => (
        <AdminShell user={access.user}>
          <div className="space-y-6">
            <Link href="/admin/sales/quotes" className="text-sm text-muted underline-offset-2 hover:underline">← Cotizaciones</Link>
            <PageHeader eyebrow="Ventas" title="Nueva cotización" />
            <NewQuote />
          </div>
        </AdminShell>
      )}
    </AccessGuard>
  );
}
