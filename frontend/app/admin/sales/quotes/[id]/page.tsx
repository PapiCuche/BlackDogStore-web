"use client";

/** Detalle de una cotización — SALES-QUOTE-01. */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AccessGuard } from "../../../components/AccessGuard";
import { AdminShell } from "../../../components/AdminShell";
import { PageHeader } from "../../../components/internal-ui";
import { QuoteDetailView } from "../../../components/sales-quotes/QuoteDetailView";
import { fetchQuote, fetchQuoteContext, type QuoteContext, type QuoteDetail } from "../../../lib/sales-quotes-api";

function QuoteScreen() {
  const { id } = useParams<{ id: string }>();
  const quoteId = Number.parseInt(id, 10);
  const [quote, setQuote] = useState<QuoteDetail | null>(null);
  const [context, setContext] = useState<QuoteContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchQuote(quoteId), fetchQuoteContext()])
      .then(([q, c]) => { if (!cancelled) { setError(null); setQuote(q); setContext(c); } })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "No se pudo cargar la cotización.");
      });
    return () => { cancelled = true; };
  }, [quoteId]);

  if (error) return <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">{error}</p>;
  if (!quote || !context) return <p className="text-sm text-muted">Cargando…</p>;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Ventas · Cotización" title={quote.label} />
      <QuoteDetailView quote={quote} context={context} onChange={setQuote} />
    </div>
  );
}

export default function SalesQuoteDetailPage() {
  return (
    <AccessGuard capability="sales.quotes.view" legacyRoles={[]}>
      {(access) => (
        <AdminShell user={access.user}>
          <div className="space-y-4">
            <Link href="/admin/sales/quotes" className="text-sm text-muted underline-offset-2 hover:underline">← Cotizaciones</Link>
            <QuoteScreen />
          </div>
        </AdminShell>
      )}
    </AccessGuard>
  );
}
