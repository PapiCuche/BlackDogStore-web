"use client";

/**
 * Finanzas → Pagos fijos y vencimientos (FIN-REMIND-01).
 *
 * What is owed, when, and the reminders that tell the right people. THE SERVER decides which company and branches
 * the caller sees, the state of every payment (`overdue`, `due_today`…), the amounts and what «hoy» is for the
 * company. This screen lists, filters over what the server returned, and offers actions the person may take.
 *
 * A REMINDER IS A NOTICE. Snoozing or reading one never changes a debt. The only things here that move a balance
 * are «Registrar pago» and «Revertir pago», each behind its own capability and re-checked by the server.
 *
 * LOADED, NOT INFINITE: the open payments are read in pages of 200 up to 1 000. The filters (text, state) and the
 * calendar work over what was loaded, and the screen says so when the server holds more. Branch and category are
 * asked of the server.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FilterBar, PageHeader, internalButtonClass, internalInputClass } from "../../components/internal-ui";
import type { InternalDashboard } from "../../lib/internal-api";
import {
  CAP_MANAGE,
  CAP_RECORD,
  CAP_REVERSE,
  STATE_LABELS,
  fetchAllObligations,
  fetchCategories,
  fetchPolicy,
  fetchRules,
  fetchSummary,
  hasFinanceCapability,
  type Category,
  type LoadedObligations,
  type Obligation,
  type ReminderPolicy,
  type Rule,
  type RuleAction,
  type Summary,
} from "../../lib/finance-api";
import { addDays, formatLongDate, monthOf, type YearMonth } from "../../lib/finance-dates";
import { FinanceCalendar } from "./FinanceCalendar";
import { ObligationDetail, type DetailPermissions } from "./ObligationDetail";
import { ObligationRow } from "./ObligationList";
import { PaymentDialog } from "./PaymentDialog";
import { ReminderPolicyPanel } from "./ReminderPolicyPanel";
import { RuleActionDialog, RuleEditDialog, RulesPanel } from "./RulesPanel";
import { SnoozeDialog } from "./SnoozeDialog";
import { SummaryCards, type SummaryTarget } from "./SummaryCards";

export type TabId = "upcoming" | "today" | "overdue" | "partial" | "completed" | "rules" | "notices";

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: "upcoming", label: "Próximos", hint: "Pagos que vencen dentro de los próximos días o más adelante." },
  { id: "today", label: "Hoy", hint: "Pagos que vencen hoy, según la fecha de la empresa." },
  { id: "overdue", label: "Vencidos", hint: "Pagos cuya fecha ya pasó y todavía tienen saldo." },
  { id: "partial", label: "Parciales", hint: "Pagos con un abono registrado y saldo pendiente." },
  { id: "completed", label: "Completados", hint: "Pagos saldados cuyo vencimiento cae en los últimos 90 días (cuenta la fecha de vencimiento, no la del pago). Los que no tienen fecha de vencimiento no se listan aquí." },
  { id: "rules", label: "Reglas", hint: "Los pagos fijos que se repiten (alquiler, servicios, suscripciones)." },
  { id: "notices", label: "Avisos", hint: "Cuándo y a quién se avisa de lo que vence." },
];

const SUMMARY_TAB: Record<SummaryTarget, TabId> = {
  overdue: "overdue", today: "today", upcoming: "upcoming", partial: "partial",
};

export const NOTICE_MS = 10_000;
const STATE_CHOICES = ["overdue", "due_today", "due_soon", "pending"] as const;

/** Which of the open payments belong to a tab. Pure, so it can be tested without a screen. */
export function segmentOf(tab: TabId, items: Obligation[]): Obligation[] {
  switch (tab) {
    case "upcoming": return items.filter((i) => i.state === "due_soon" || i.state === "pending");
    case "today": return items.filter((i) => i.state === "due_today");
    case "overdue": return items.filter((i) => i.state === "overdue");
    case "partial": return items.filter((i) => i.partially_paid);
    default: return items;
  }
}

/** The text filter: concept, payee or reference, over what is loaded. */
export function matchesSearch(item: Obligation, search: string): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [item.concept, item.payee_name, item.supplier?.name ?? "", item.reference]
    .some((text) => text.toLowerCase().includes(needle));
}

export function FinanceCentre({
  companyId,
  dashboard,
}: {
  companyId: number | null;
  dashboard: InternalDashboard | null;
}) {
  const baseId = useId();
  const searchParams = useSearchParams();
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const branches = dashboard?.branch_scope?.branches ?? [];

  // ── Data ────────────────────────────────────────────────────────────────
  const [reloadKey, setReloadKey] = useState(0);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [openData, setOpenData] = useState<{ scope: string; data: LoadedObligations } | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [doneData, setDoneData] = useState<{ scope: string; data: LoadedObligations } | null>(null);
  const [doneError, setDoneError] = useState<string | null>(null);
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [rulesError, setRulesError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<ReminderPolicy | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoriesError, setCategoriesError] = useState<string | null>(null);
  const [categoriesKey, setCategoriesKey] = useState(0);

  // ── View state ──────────────────────────────────────────────────────────
  const [tabChoice, setTabChoice] = useState<TabId | null>(null);
  const [branch, setBranch] = useState<number | null>(null);
  const [category, setCategory] = useState<number | null>(null);
  // Each list remembers the scope (company, branch, category) it was read for: another scope shows loading, not
  // the previous scope's rows.
  const scope = `${companyId}|${branch}|${category}`;
  const open = openData?.scope === scope ? openData.data : null;
  const done = doneData?.scope === scope ? doneData.data : null;
  const [stateFilter, setStateFilter] = useState("");
  const [search, setSearch] = useState("");
  const [monthChoice, setMonth] = useState<YearMonth | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // ── Dialogs ─────────────────────────────────────────────────────────────
  const [openId, setOpenId] = useState<number | null>(null);
  const [openSection, setOpenSection] = useState<"history" | undefined>(undefined);
  const [drawerKey, setDrawerKey] = useState(0);
  const [paying, setPaying] = useState<Obligation | null>(null);
  const [snoozing, setSnoozing] = useState<Pick<Obligation, "id" | "concept" | "snoozed_until"> | null>(null);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [ruleAction, setRuleAction] = useState<{ rule: Rule; action: RuleAction } | null>(null);

  const today = summary?.today ?? null;
  const refresh = useCallback(() => setReloadKey((n) => n + 1), []);
  const retry = useCallback(() => { setReloadKey((n) => n + 1); setCategoriesKey((n) => n + 1); }, []);
  // Recording a payment needs the company's today (its default and its upper bound): without the summary it is
  // not offered, rather than guessed from the browser clock.
  const permissions: DetailPermissions = {
    canRecord: hasFinanceCapability(dashboard, CAP_RECORD) && summary !== null,
    canReverse: hasFinanceCapability(dashboard, CAP_REVERSE),
    canManage: hasFinanceCapability(dashboard, CAP_MANAGE),
  };

  // A message about what the person just did stays for a while, not forever: it would end up describing a screen
  // that has long since changed.
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [notice]);

  // First screen: the most urgent list that has something in it, until the person chooses a tab.
  const urgent: TabId = !open ? "overdue"
    : open.results.some((i) => i.state === "overdue") ? "overdue"
    : open.results.some((i) => i.state === "due_today") ? "today" : "upcoming";
  const tab = tabChoice ?? urgent;
  // The month shown follows the company's today until the person moves it.
  const month = monthChoice ?? (today ? monthOf(today) : null);

  // The summary (and, with it, the company's «hoy»).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchSummary(companyId);
        if (cancelled) return;
        setSummary(data);
        setSummaryError(null);
      } catch (err) {
        if (cancelled) return;
        setSummaryError(err instanceof Error ? err.message : "No se pudo cargar el resumen.");
      }
    })();
    return () => { cancelled = true; };
  }, [companyId, reloadKey]);

  // The open payables: tabs, calendar and filters all read from this one list.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchAllObligations(companyId, { state: "open", branch, category });
        if (cancelled) return;
        setOpenData({ scope, data });
        setOpenError(null);
      } catch (err) {
        if (cancelled) return;
        setOpenError(err instanceof Error ? err.message : "No se pudieron cargar los pagos.");
      }
    })();
    return () => { cancelled = true; };
  }, [companyId, branch, category, reloadKey, scope]);

  // Completed payments: only when that tab is looked at, and only the last 90 days.
  useEffect(() => {
    if (tab !== "completed" || !today) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchAllObligations(companyId, {
          state: "paid", branch, category, dueFrom: addDays(today, -90),
        });
        if (cancelled) return;
        setDoneData({ scope, data });
        setDoneError(null);
      } catch (err) {
        if (cancelled) return;
        setDoneError(err instanceof Error ? err.message : "No se pudieron cargar los pagos completados.");
      }
    })();
    return () => { cancelled = true; };
  }, [tab, today, companyId, branch, category, reloadKey, scope]);

  // The rules change with what the person does here (pause, edit): read again after every refresh.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchRules(companyId);
        if (cancelled) return;
        setRules(data);
        setRulesError(null);
      } catch (err) {
        if (cancelled) return;
        setRulesError(err instanceof Error ? err.message : "No se pudieron cargar las reglas.");
      }
    })();
    return () => { cancelled = true; };
  }, [companyId, reloadKey]);

  // The reminder policy and the categories only change when the company does (the policy form saves itself).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [p, c] = await Promise.allSettled([fetchPolicy(companyId), fetchCategories(companyId)]);
      if (cancelled) return;
      if (p.status === "fulfilled") { setPolicy(p.value); setPolicyError(null); }
      else setPolicyError(p.reason instanceof Error ? p.reason.message : "No se pudo cargar la política de avisos.");
      if (c.status === "fulfilled") {
        setCategories(c.value.filter((x) => x.is_active));
        setCategoriesError(null);
      } else {
        setCategoriesError(c.reason instanceof Error ? c.reason.message : "No se pudieron cargar las categorías.");
      }
    })();
    return () => { cancelled = true; };
  }, [companyId, categoriesKey]);

  // Deep link: /admin/finance?obligation=<id> (where the bell sends people). Adjusted while rendering, when the
  // address changes, rather than in an effect: the panel opens in the same pass as the new address.
  const linked = searchParams?.get("obligation") ?? null;
  const [seenLink, setSeenLink] = useState<string | null>(null);
  if (linked !== seenLink) {
    setSeenLink(linked);
    const id = Number(linked);
    if (linked !== null && Number.isInteger(id) && id > 0) setOpenId(id);
  }

  function setAddress(id: number | null) {
    if (typeof window === "undefined") return;
    const url = id === null ? window.location.pathname : `${window.location.pathname}?obligation=${id}`;
    try {
      window.history.replaceState(window.history.state, "", url);
    } catch {
      // An address that cannot be rewritten does not stop the panel.
    }
  }

  function view(item: Pick<Obligation, "id">, section?: "history") {
    setNotice(null);
    setOpenSection(section);
    setOpenId(item.id);
    setAddress(item.id);
  }
  function closeView() {
    setOpenId(null);
    setOpenSection(undefined);
    setAddress(null);
  }

  function chooseTab(next: TabId) {
    setNotice(null);
    setTabChoice(next);
    setDay(null);
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
    chooseTab(TABS[next].id);
    tabRefs.current[TABS[next].id]?.focus();
  }

  // ── Derived ─────────────────────────────────────────────────────────────
  const passes = useCallback(
    (item: Obligation) => (!stateFilter || item.state === stateFilter) && matchesSearch(item, search),
    [stateFilter, search],
  );
  const openItems = useMemo(() => (open?.results ?? []).filter(passes), [open, passes]);
  const counts = useMemo(() => {
    const out = {} as Record<TabId, number>;
    for (const t of TABS) out[t.id] = segmentOf(t.id, openItems).length;
    return out;
  }, [openItems]);
  const partialCount = open ? open.results.filter((i) => i.partially_paid).length : null;

  const listed: Obligation[] | null = useMemo(() => {
    if (tab === "rules" || tab === "notices") return null;
    if (day) return openItems.filter((i) => i.due_date === day);
    if (tab === "completed") {
      return done ? done.results.filter(passes).slice().sort((a, b) => (b.due_date ?? "").localeCompare(a.due_date ?? "")) : null;
    }
    return open ? segmentOf(tab, openItems) : null;
  }, [tab, day, open, done, openItems, passes]);

  function localize(updated: Partial<Obligation> & { id: number }) {
    const swap = (held: { scope: string; data: LoadedObligations } | null) =>
      held
        ? { ...held, data: { ...held.data, results: held.data.results.map((r) => (r.id === updated.id ? { ...r, ...updated } : r)) } }
        : held;
    setOpenData(swap);
    setDoneData(swap);
  }

  // ── Handlers ────────────────────────────────────────────────────────────
  function editRule(ruleId: number) {
    const found = rules?.find((r) => r.id === ruleId);
    if (found) {
      setEditingRule(found);
      return;
    }
    void (async () => {
      try {
        const fresh = await fetchRules(companyId);
        setRules(fresh);
        const again = fresh.find((r) => r.id === ruleId);
        if (again) setEditingRule(again);
        else setNotice("No encontramos la regla de este pago: puede que ya no exista o que no sea de tus sucursales.");
      } catch (err) {
        setNotice(err instanceof Error ? err.message : "No se pudieron cargar las reglas.");
      }
    })();
  }

  function ruleUpdated(next: Rule, message: string) {
    setRules((current) => (current ? current.map((r) => (r.id === next.id ? next : r)) : current));
    setEditingRule(null);
    setRuleAction(null);
    setNotice(message);
    refresh();
  }

  // A refresh that fails while there is data on screen must not look like a good answer: the money shown may be
  // out of date. (With nothing on screen the plain error below says it.)
  const staleProblems = [
    openError && open ? openError : null,
    doneError && done ? doneError : null,
    summaryError && summary ? summaryError : null,
    categoriesError,
  ].filter((text): text is string => Boolean(text));
  const active = TABS.find((t) => t.id === tab)!;
  const loadingList = listed === null && tab !== "rules" && tab !== "notices" && !(tab === "completed" ? doneError : openError);
  const listError = tab === "completed" ? doneError : openError;
  const truncated = (tab === "completed" ? done?.truncated : open?.truncated) ?? false;
  const total = (tab === "completed" ? done?.count : open?.count) ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Finanzas"
        title="Pagos fijos y vencimientos"
        description="Lo que hay que pagar y cuándo, con avisos para que nada venza sin que nadie lo vea. Control interno: no es contabilidad completa ni sustituye a los registros de SUNAT."
      />

      <div role="status" aria-live="polite" className={notice ? "rounded-lg border border-info-border bg-info-surface p-3 text-sm text-foreground" : "sr-only"}>
        {notice}
      </div>

      {summaryError && !summary ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">{summaryError}</p>
      ) : null}

      {summary ? (
        <>
          <SummaryCards summary={summary} partialCount={partialCount} onOpen={(target) => chooseTab(SUMMARY_TAB[target])} />
          <p className="text-xs text-muted">
            Hoy es {formatLongDate(summary.today)} (fecha de la empresa). Los totales cubren todos los pagos que puedes ver;
            los filtros de abajo sólo afectan a las listas.
          </p>
        </>
      ) : !summaryError ? (
        <p role="status" className="text-sm text-muted">Cargando el resumen…</p>
      ) : null}

      <FilterBar>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label htmlFor={`${baseId}-branch`} className="block text-xs font-semibold text-muted">Sucursal</label>
            <select id={`${baseId}-branch`} value={branch ?? ""} className={`${internalInputClass} mt-1`}
                    onChange={(e) => { setBranch(e.target.value ? Number(e.target.value) : null); setDay(null); }}>
              <option value="">Todas las sucursales</option>
              {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${baseId}-category`} className="block text-xs font-semibold text-muted">Categoría</label>
            <select id={`${baseId}-category`} value={category ?? ""} className={`${internalInputClass} mt-1`}
                    onChange={(e) => { setCategory(e.target.value ? Number(e.target.value) : null); setDay(null); }}>
              <option value="">Todas las categorías</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${baseId}-state`} className="block text-xs font-semibold text-muted">Estado</label>
            <select id={`${baseId}-state`} value={stateFilter} className={`${internalInputClass} mt-1`}
                    onChange={(e) => setStateFilter(e.target.value)}>
              <option value="">Todos los estados</option>
              {STATE_CHOICES.map((s) => <option key={s} value={s}>{STATE_LABELS[s]}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${baseId}-search`} className="block text-xs font-semibold text-muted">Buscar</label>
            <input id={`${baseId}-search`} type="search" value={search} placeholder="Concepto o beneficiario"
                   className={`${internalInputClass} mt-1`} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>
        <p className="mt-2 text-xs text-muted">
          Sucursal y categoría se piden al servidor. Estado y búsqueda filtran los pagos ya cargados en esta pantalla.
        </p>
      </FilterBar>

      {staleProblems.length > 0 ? (
        <div role="alert" data-testid="finance-stale" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-foreground">
          <div>
            <p className="font-semibold">No se pudo actualizar; lo que ves puede estar desactualizado.</p>
            <ul className="mt-1 list-disc pl-5 text-xs text-muted">
              {staleProblems.map((text) => <li key={text}>{text}</li>)}
            </ul>
          </div>
          <button type="button" onClick={retry} className={internalButtonClass}>Reintentar</button>
        </div>
      ) : null}
      {openError && !open ? (
        <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">{openError}</p>
      ) : null}
      {open?.truncated ? (
        <p role="note" className="rounded-xl border border-warning-border bg-warning-surface px-4 py-3 text-sm text-foreground">
          Hay {open.count} pagos abiertos y se cargaron {open.results.length}. Usa la sucursal o la categoría para acotar:
          el calendario y los filtros de texto sólo ven lo cargado.
        </p>
      ) : null}

      {summary && month && open ? (
        <FinanceCalendar
          items={openItems}
          today={summary.today}
          month={month}
          onMonthChange={setMonth}
          selectedDay={day}
          onSelectDay={(iso) => setDay(iso)}
        />
      ) : null}

      <div>
        <div role="tablist" aria-label="Vistas de pagos" className="flex flex-wrap gap-1 border-b border-bd-border">
          {TABS.map((t, index) => {
            const selected = t.id === tab && !day;
            const count = t.id === "rules" ? rules?.length : t.id === "completed" || t.id === "notices" ? undefined : counts[t.id];
            return (
              <button
                key={t.id}
                ref={(el) => { tabRefs.current[t.id] = el; }}
                id={`${baseId}-tab-${t.id}`}
                type="button"
                role="tab"
                aria-selected={t.id === tab}
                aria-controls={`${baseId}-panel`}
                tabIndex={t.id === tab ? 0 : -1}
                onClick={() => chooseTab(t.id)}
                onKeyDown={(e) => onTabKey(e, index)}
                className={`-mb-px rounded-t-lg border-b-2 px-3 py-2.5 text-sm font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-foreground/40 ${
                  selected ? "border-foreground text-foreground" : "border-transparent text-muted hover:text-foreground"}`}
              >
                {t.label}{count !== undefined ? ` (${count})` : ""}
              </button>
            );
          })}
        </div>

        <section id={`${baseId}-panel`} role="tabpanel" aria-labelledby={`${baseId}-tab-${tab}`} className="pt-4">
          {day ? (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-bd-border bg-surface px-4 py-3 text-sm">
              <span className="font-semibold text-foreground">Vencen el {formatLongDate(day)}</span>
              <button type="button" onClick={() => setDay(null)}
                      className="rounded-lg border border-bd-border px-3 py-1.5 text-xs font-semibold text-foreground">
                Quitar filtro de día
              </button>
            </div>
          ) : (
            <p className="mb-3 text-sm text-muted">{active.hint}</p>
          )}

          {tab === "rules" ? (
            <RulesPanel
              rules={rules} canManage={permissions.canManage} loading={rules === null && !rulesError} error={rulesError}
              onEdit={(rule) => setEditingRule(rule)} onAct={(rule, action) => setRuleAction({ rule, action })}
            />
          ) : tab === "notices" ? (
            <ReminderPolicyPanel
              companyId={companyId} policy={policy}
              loading={policy === null && !policyError} error={policyError}
              onSaved={(next) => { setPolicy(next); setNotice("Política de avisos guardada."); }}
            />
          ) : loadingList ? (
            <p role="status" className="py-8 text-sm text-muted">Cargando los pagos…</p>
          ) : listError && listed === null ? (
            <p role="alert" className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">{listError}</p>
          ) : listed && listed.length === 0 ? (
            <p data-testid="finance-empty" className="rounded-xl border border-bd-border bg-surface p-6 text-sm text-muted">
              {day ? "No hay pagos que venzan ese día con estos filtros."
                : search || stateFilter ? "Ningún pago cargado coincide con los filtros."
                : tab === "overdue" ? "Nada vencido. Todo está al día."
                : tab === "today" ? "Hoy no vence ningún pago."
                : tab === "completed" ? "No hay pagos completados con vencimiento en los últimos 90 días."
                : "No hay pagos en esta vista."}
            </p>
          ) : listed ? (
            <>
              <ul data-testid="finance-list" className="space-y-3">
                {listed.map((item) => (
                  <ObligationRow
                    key={item.id}
                    item={item}
                    today={summary?.today ?? ""}
                    permissions={permissions}
                    onView={view}
                    onPay={(it) => setPaying(it)}
                    onSnooze={(it) => setSnoozing(it)}
                    onEditRule={editRule}
                  />
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted">
                {listed.length} {listed.length === 1 ? "pago" : "pagos"} en esta vista
                {truncated ? ` (cargados ${tab === "completed" ? done?.results.length : open?.results.length} de ${total})` : ""}.
              </p>
            </>
          ) : null}
        </section>
      </div>

      {openId !== null ? (
        <ObligationDetail
          companyId={companyId}
          id={openId}
          today={summary?.today ?? ""}
          permissions={permissions}
          refreshKey={drawerKey}
          section={openSection}
          onClose={closeView}
          onPay={(item) => setPaying(item)}
          onSnooze={(item) => setSnoozing(item)}
          onEditRule={editRule}
          onChanged={refresh}
        />
      ) : null}

      {paying && summary ? (
        <PaymentDialog
          companyId={companyId}
          obligation={paying}
          today={summary.today}
          onClose={() => setPaying(null)}
          onUncertain={() => { refresh(); setDrawerKey((n) => n + 1); }}
          onVerify={(item) => { setPaying(null); view(item); }}
          onRecorded={(result) => {
            setPaying(null);
            // The action's answer carries `snoozed_until: null` because it does not know THIS person's snooze:
            // merging it would erase a snooze the person set. Only the money fields are taken from it.
            const { snoozed_until: _ignored, ...fresh } = result.obligation;
            void _ignored;
            localize(fresh);
            setDrawerKey((n) => n + 1);
            setNotice(`Pago registrado en «${result.obligation.concept}».`);
            refresh();
          }}
        />
      ) : null}

      {snoozing ? (
        <SnoozeDialog
          companyId={companyId}
          obligation={snoozing}
          onClose={() => setSnoozing(null)}
          onChanged={(until) => {
            localize({ id: snoozing.id, snoozed_until: until });
            setSnoozing(null);
            setDrawerKey((n) => n + 1);
            setNotice(until
              ? `Tus avisos de «${snoozing.concept}» quedan pospuestos. El pago no cambió.`
              : `Volverás a recibir los avisos de «${snoozing.concept}». El pago no cambió.`);
          }}
        />
      ) : null}

      {editingRule ? (
        <RuleEditDialog
          companyId={companyId} rule={editingRule} onClose={() => setEditingRule(null)}
          onSaved={(next) => ruleUpdated(next, `Regla «${next.concept}» guardada.`)}
        />
      ) : null}

      {ruleAction && summary ? (
        <RuleActionDialog
          companyId={companyId} rule={ruleAction.rule} action={ruleAction.action} today={summary.today}
          onClose={() => setRuleAction(null)}
          onDone={(next) => ruleUpdated(next, `Regla «${next.concept}» actualizada.`)}
        />
      ) : null}
    </div>
  );
}

