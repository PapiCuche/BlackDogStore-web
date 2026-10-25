"use client";

/**
 * The accounting centre: filters, the three views, the totals and the notices.
 *
 * ONE FILTER SET FOR THE THREE VIEWS. Changing tab never changes the question:
 * the same period, branch, channel, type and documents are asked of view A, B
 * or C. The filters are a form — they travel when the person applies them, not
 * on every keystroke of a date.
 *
 * WHAT THIS SCREEN NEVER DOES: recompute an amount, decide a fiscal state,
 * unmask a document, or offer to issue, void or send anything. View C names
 * what is missing; acting on it happens on the order, under its own permissions.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { PageHeader } from "../../components/internal-ui";
import {
  AccountingError,
  DEFAULT_FILTERS,
  fetchFiscalDocuments,
  fetchOperations,
  fetchPending,
  fetchSummary,
  type AccountingFilters,
  type Envelope,
  type FiscalAnswer,
  type OperationsAnswer,
  type PendingAnswer,
  type SummaryAnswer,
} from "../../lib/accounting-api";
import { AccountingNotices } from "./AccountingNotices";
import { AccountingFiltersForm } from "./AccountingFiltersForm";
import { FiscalTable, OperationsTable, PendingTable } from "./AccountingTables";
import { TotalsPanel } from "./TotalsPanel";

export type TabId = "operations" | "fiscal" | "pending";

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: "operations", label: "A · Operaciones", hint: "Cada operación económica, una sola vez, con su documento." },
  { id: "fiscal", label: "B · Comprobantes", hint: "Facturas, boletas y notas con su estado real ante SUNAT." },
  { id: "pending", label: "C · Internos y pendientes", hint: "Notas internas, faltantes e inconsistencias por regularizar." },
];

type ViewState =
  | { tab: "operations"; data: OperationsAnswer }
  | { tab: "fiscal"; data: FiscalAnswer }
  | { tab: "pending"; data: PendingAnswer };

function initialFilters(): AccountingFilters {
  return { ...DEFAULT_FILTERS };
}

export function AccountingCentre({ companyId }: { companyId: number | null }) {
  const [filters, setFilters] = useState<AccountingFilters>(initialFilters);
  const [tab, setTab] = useState<TabId>("operations");
  const [summary, setSummary] = useState<SummaryAnswer | null>(null);
  const [view, setView] = useState<ViewState | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const tabRefs = useRef<Record<TabId, HTMLButtonElement | null>>({ operations: null, fiscal: null, pending: null });
  const baseId = useId();

  const loadView = useCallback(
    async (which: TabId, cursor?: string | null): Promise<ViewState> => {
      if (which === "operations") return { tab: which, data: await fetchOperations(companyId, filters, cursor) };
      if (which === "fiscal") return { tab: which, data: await fetchFiscalDocuments(companyId, filters, cursor) };
      return { tab: which, data: await fetchPending(companyId, filters, cursor) };
    },
    [companyId, filters],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const [nextSummary, nextView] = await Promise.all([fetchSummary(companyId, filters), loadView(tab)]);
        if (cancelled) return;
        setSummary(nextSummary);
        setView(nextView);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setView(null);
        setError({
          message: err instanceof Error ? err.message : "No se pudo cargar el centro contable.",
          status: err instanceof AccountingError ? err.status : 0,
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [companyId, filters, tab, loadView]);

  async function loadMore() {
    if (!view || !view.data.next_cursor) return;
    setLoadingMore(true);
    try {
      const more = await loadView(view.tab, view.data.next_cursor);
      setView((prev) => {
        if (!prev || prev.tab !== more.tab) return more;
        return {
          ...more,
          data: { ...more.data, results: [...prev.data.results, ...more.data.results] },
        } as ViewState;
      });
    } catch (err) {
      setError({ message: err instanceof Error ? err.message : "No se pudo cargar más.", status: 0 });
    } finally {
      setLoadingMore(false);
    }
  }

  function onTabKey(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = TABS.length - 1;
    let next = -1;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next < 0) return;
    event.preventDefault();
    const id = TABS[next].id;
    setTab(id);
    tabRefs.current[id]?.focus();
  }

  const envelope: Envelope | null = view?.data ?? summary;
  const active = TABS.find((t) => t.id === tab)!;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Contabilidad"
        title="Centro contable"
        description="Ventas, cobros y comprobantes del período, en sólo lectura. No es contabilidad completa ni una declaración ante SUNAT."
      />

      <AccountingNotices envelope={envelope} />

      <AccountingFiltersForm
        value={filters}
        branches={envelope?.scope.branches ?? []}
        showEnvironment={tab === "fiscal"}
        onApply={setFilters}
      />

      {envelope ? (
        <p className="text-xs text-muted" aria-live="polite">
          {envelope.company.name} · del {envelope.period.from} al {envelope.period.to} ({envelope.period.timezone})
          {envelope.scope.company_wide ? " · toda la empresa" : " · sólo tus sucursales"}
        </p>
      ) : null}

      {summary && !error ? <TotalsPanel totals={summary.totals} pending={summary.pending} /> : null}

      <div>
        <div role="tablist" aria-label="Vistas del centro contable" className="flex flex-wrap gap-1 border-b border-bd-border">
          {TABS.map((t, index) => {
            const selected = t.id === tab;
            return (
              <button
                key={t.id}
                ref={(el) => {
                  tabRefs.current[t.id] = el;
                }}
                id={`${baseId}-tab-${t.id}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`${baseId}-panel`}
                tabIndex={selected ? 0 : -1}
                onClick={() => setTab(t.id)}
                onKeyDown={(e) => onTabKey(e, index)}
                className={`-mb-px rounded-t-lg border-b-2 px-3 py-2.5 text-sm font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-foreground/40 ${
                  selected ? "border-foreground text-foreground" : "border-transparent text-muted hover:text-foreground"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        <section
          id={`${baseId}-panel`}
          role="tabpanel"
          aria-labelledby={`${baseId}-tab-${tab}`}
          aria-busy={loading}
          className="pt-4"
        >
          <p className="mb-3 text-sm text-muted">{active.hint}</p>
          {loading ? (
            <p role="status" className="py-8 text-sm text-muted">
              Cargando…
            </p>
          ) : error ? (
            <div role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">
              {error.message}
            </div>
          ) : view ? (
            <>
              {view.tab === "operations" ? <OperationsTable answer={view.data} /> : null}
              {view.tab === "fiscal" ? <FiscalTable answer={view.data} /> : null}
              {view.tab === "pending" ? <PendingTable answer={view.data} /> : null}
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-muted">
                <span>
                  {view.data.results.length} de {view.data.count}
                </span>
                {view.data.next_cursor ? (
                  <button
                    type="button"
                    onClick={() => void loadMore()}
                    disabled={loadingMore}
                    className="rounded-lg border border-bd-border px-3 py-2 text-sm font-semibold text-foreground disabled:opacity-40"
                  >
                    {loadingMore ? "Cargando…" : "Cargar más"}
                  </button>
                ) : null}
              </div>
            </>
          ) : null}
        </section>
      </div>
    </div>
  );
}
