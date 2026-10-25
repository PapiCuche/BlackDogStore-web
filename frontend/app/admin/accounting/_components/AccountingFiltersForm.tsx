"use client";

/**
 * The question: period, branch, channel, type, documents (and, for view B, the
 * fiscal environment). A form with a draft: nothing is asked of the server until
 * the person applies it. The branch list is the one the server returned for
 * this caller — a branch they cannot see is never offered.
 */
import { useId, useState } from "react";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import {
  CHANNEL_LABELS,
  DEFAULT_FILTERS,
  DOCUMENT_LABELS,
  FISCAL_MAX_DAYS,
  KIND_LABELS,
  LEDGER_MAX_DAYS,
  type AccountingFilters,
  type Channel,
  type DocumentFilter,
  type FiscalEnvironment,
  type Kind,
  type PeriodPreset,
} from "../../lib/accounting-api";

const PRESETS: { value: PeriodPreset; label: string }[] = [
  { value: "day", label: "Día" },
  { value: "week", label: "Semana" },
  { value: "month", label: "Mes" },
  { value: "year", label: "Año" },
  { value: "tax_period", label: "Período tributario" },
  { value: "range", label: "Rango personalizado" },
];

const MONTHS = [
  "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
  "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
];

/** This month as the COMPANY reckons it (its timezone), not the browser. */
export function currentTaxPeriod(timeZone?: string): string {
  const now = new Date();
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit" }).formatToParts(now);
    const year = parts.find((p) => p.type === "year")?.value;
    const month = parts.find((p) => p.type === "month")?.value;
    if (year && month) return `${year}${month}`;
  } catch {
    // An unknown zone name falls back to the browser's month.
  }
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function validate(filters: AccountingFilters, maxDays: number = LEDGER_MAX_DAYS): string | null {
  if (filters.period === "year" && maxDays < 365) {
    return `Esta vista abarca como mucho ${maxDays} días: un año sólo se consulta en la vista B (comprobantes).`;
  }
  if (filters.period === "range") {
    if (!filters.from || !filters.to) return "Indica la fecha inicial y la final del rango.";
    if (filters.to < filters.from) return "El rango termina antes de empezar.";
    const days = (Date.parse(filters.to) - Date.parse(filters.from)) / 86_400_000 + 1;
    if (days > maxDays) return `Esta vista abarca como mucho ${maxDays} días.`;
  }
  if (filters.period === "tax_period" && !/^\d{4}(0[1-9]|1[0-2])$/.test(filters.taxPeriod)) {
    return "Elige el mes y el año del período tributario.";
  }
  return null;
}

type Props = {
  value: AccountingFilters;
  branches: { id: number; name: string }[];
  showEnvironment: boolean;
  /** The company's timezone, as the server reported it. */
  timezone?: string;
  onApply: (filters: AccountingFilters) => void;
};

export function AccountingFiltersForm({ value, branches, showEnvironment, timezone, onApply }: Props) {
  const [draft, setDraft] = useState<AccountingFilters>(value);
  const [problem, setProblem] = useState<string | null>(null);
  const [applied, setApplied] = useState<AccountingFilters>(value);
  const id = useId();

  // The applied filters changed from outside (a reset, a new company): the
  // draft follows them. Adjusted while rendering, not in an effect.
  if (applied !== value) {
    setApplied(value);
    setDraft(value);
  }

  function set<K extends keyof AccountingFilters>(key: K, next: AccountingFilters[K]) {
    setDraft((prev) => ({ ...prev, [key]: next }));
  }

  function choosePreset(preset: PeriodPreset) {
    setDraft((prev) => ({
      ...prev,
      period: preset,
      taxPeriod: preset === "tax_period" && !prev.taxPeriod ? currentTaxPeriod(timezone) : prev.taxPeriod,
    }));
  }

  function toggleDocument(doc: DocumentFilter) {
    setDraft((prev) => ({
      ...prev,
      documents: prev.documents.includes(doc) ? prev.documents.filter((d) => d !== doc) : [...prev.documents, doc],
    }));
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const issue = validate(draft, showEnvironment ? FISCAL_MAX_DAYS : LEDGER_MAX_DAYS);
    setProblem(issue);
    if (!issue) onApply(draft);
  }

  function reset() {
    setProblem(null);
    setDraft(DEFAULT_FILTERS);
    onApply(DEFAULT_FILTERS);
  }

  const taxYear = draft.taxPeriod.slice(0, 4);
  const taxMonth = draft.taxPeriod.slice(4, 6);
  const label = "mb-1 block text-xs font-semibold text-muted";

  return (
    <form
      onSubmit={submit}
      aria-label="Filtros del centro contable"
      className="rounded-xl border border-bd-border bg-surface p-4 sm:p-5"
    >
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor={`${id}-period`} className={label}>Período</label>
          <select
            id={`${id}-period`}
            className={internalInputClass}
            value={draft.period}
            onChange={(e) => choosePreset(e.target.value as PeriodPreset)}
          >
            {PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.value === "year" && !showEnvironment ? `${p.label} (sólo vista B)` : p.label}
              </option>
            ))}
          </select>
        </div>

        {draft.period === "tax_period" ? (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor={`${id}-tax-month`} className={label}>Mes</label>
              <select
                id={`${id}-tax-month`}
                className={internalInputClass}
                value={taxMonth}
                onChange={(e) => set("taxPeriod", `${taxYear}${e.target.value}`)}
              >
                {MONTHS.map((name, i) => {
                  const mm = String(i + 1).padStart(2, "0");
                  return <option key={mm} value={mm}>{name}</option>;
                })}
              </select>
            </div>
            <div>
              <label htmlFor={`${id}-tax-year`} className={label}>Año</label>
              <input
                id={`${id}-tax-year`}
                className={internalInputClass}
                inputMode="numeric"
                pattern="\d{4}"
                maxLength={4}
                value={taxYear}
                onChange={(e) => set("taxPeriod", `${e.target.value.replace(/\D/g, "").slice(0, 4)}${taxMonth}`)}
              />
            </div>
          </div>
        ) : draft.period === "range" ? (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor={`${id}-from`} className={label}>Desde</label>
              <input id={`${id}-from`} type="date" className={internalInputClass} value={draft.from}
                     onChange={(e) => set("from", e.target.value)} />
            </div>
            <div>
              <label htmlFor={`${id}-to`} className={label}>Hasta</label>
              <input id={`${id}-to`} type="date" className={internalInputClass} value={draft.to}
                     onChange={(e) => set("to", e.target.value)} />
            </div>
          </div>
        ) : (
          <div>
            <label htmlFor={`${id}-date`} className={label}>Fecha de referencia</label>
            <input id={`${id}-date`} type="date" className={internalInputClass} value={draft.date}
                   aria-describedby={`${id}-date-hint`} onChange={(e) => set("date", e.target.value)} />
            <p id={`${id}-date-hint`} className="mt-1 text-[11px] text-muted">Vacía: hoy, en la hora de la empresa.</p>
          </div>
        )}

        <div>
          <label htmlFor={`${id}-branch`} className={label}>Sucursal</label>
          <select
            id={`${id}-branch`}
            className={internalInputClass}
            value={draft.branch ?? ""}
            onChange={(e) => set("branch", e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Todas las que puedo ver</option>
            {branches.map((b) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor={`${id}-channel`} className={label}>Canal</label>
          <select
            id={`${id}-channel`}
            className={internalInputClass}
            value={draft.channel}
            onChange={(e) => set("channel", e.target.value as Channel | "")}
          >
            <option value="">Todos</option>
            {(Object.keys(CHANNEL_LABELS) as Channel[]).map((c) => (
              <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor={`${id}-kind`} className={label}>Tipo de operación</label>
          <select
            id={`${id}-kind`}
            className={internalInputClass}
            value={draft.kind}
            onChange={(e) => set("kind", e.target.value as Kind | "")}
          >
            <option value="">Todas</option>
            {(Object.keys(KIND_LABELS) as Kind[]).map((k) => (
              <option key={k} value={k}>{KIND_LABELS[k]}</option>
            ))}
          </select>
        </div>

        {showEnvironment ? (
          <div>
            <label htmlFor={`${id}-env`} className={label}>Ambiente fiscal</label>
            <select
              id={`${id}-env`}
              className={internalInputClass}
              value={draft.environment}
              onChange={(e) => set("environment", e.target.value as FiscalEnvironment)}
            >
              <option value="production">Producción</option>
              <option value="beta">Pruebas (BETA, sin validez tributaria)</option>
            </select>
          </div>
        ) : null}
      </div>

      <fieldset className="mt-4">
        <legend className={label}>Documento</legend>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(DOCUMENT_LABELS) as DocumentFilter[]).map((doc) => {
            const checked = draft.documents.includes(doc);
            return (
              <label
                key={doc}
                className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold focus-within:ring-2 focus-within:ring-foreground/40 ${
                  checked ? "border-foreground/40 bg-foreground/[0.06] text-foreground" : "border-bd-border text-muted"
                }`}
              >
                <input type="checkbox" className="h-3.5 w-3.5" checked={checked} onChange={() => toggleDocument(doc)} />
                {DOCUMENT_LABELS[doc]}
              </label>
            );
          })}
        </div>
      </fieldset>

      {problem ? (
        <p role="alert" className="mt-3 text-sm text-danger">{problem}</p>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="submit" className={internalPrimaryButtonClass}>Aplicar filtros</button>
        <button type="button" onClick={reset} className={internalButtonClass}>Restablecer</button>
      </div>
    </form>
  );
}
