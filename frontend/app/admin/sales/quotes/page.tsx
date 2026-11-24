"use client";

/**
 * Cotizaciones — SALES-QUOTE-01.
 *
 * Una cotización ofrece precios con una vigencia; no cobra, no separa stock y no es un comprobante. Se
 * convierte en venta desde su detalle, con las reglas de la caja.
 */
import { useEffect, useState } from "react";
import { AccessGuard } from "../../components/AccessGuard";
import { AdminShell } from "../../components/AdminShell";
import { PageHeader } from "../../components/internal-ui";
import { SalesQuotesList } from "../../components/sales-quotes/SalesQuotesList";
import { fetchQuoteContext, type QuoteContext } from "../../lib/sales-quotes-api";

function QuotesScreen() {
  const [context, setContext] = useState<QuoteContext | null>(null);
  useEffect(() => {
    fetchQuoteContext().then(setContext).catch(() => setContext(null));
  }, []);
  return <SalesQuotesList context={context} />;
}

export default function SalesQuotesPage() {
  return (
    <AccessGuard capability="sales.quotes.view" legacyRoles={[]}>
      {(access) => (
        <AdminShell user={access.user}>
          <div className="space-y-6">
            <PageHeader
              eyebrow="Ventas"
              title="Cotizaciones"
              description="Ofertas con precio y vigencia para un cliente. No cobran ni separan stock; al convertirlas se cobran en caja."
            />
            <QuotesScreen />
          </div>
        </AdminShell>
      )}
    </AccessGuard>
  );
}
