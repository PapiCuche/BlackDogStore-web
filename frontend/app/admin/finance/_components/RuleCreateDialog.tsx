"use client";

/**
 * «Nuevo pago fijo»: a recurring rule (rent, services, subscriptions) created from the panel.
 *
 * It needs `finance.manage`, and the server re-checks it together with WHERE: a branch the caller does not reach
 * answers 404, and a general rule (no branch) needs company-wide scope, so «General» is only offered to whom the
 * server would accept it from. The checks below mirror the server's limits so a plain mistake never travels; the
 * server's sentence is shown for anything else.
 *
 * Rules carry no idempotency key on the server: a ref keeps a double click or a double Enter to ONE request, and
 * an answer that never came (network) or a server failure (5xx) FREEZES the form: the rule — and its first periods
 * — may already exist, and sending again would create a second one. The person is sent to «Reglas» to check.
 */
import { useId, useRef, useState } from "react";
import {
  FREQUENCY_LABELS,
  METHOD_LABELS,
  PERIOD_WINDOW_DAYS,
  WEEKDAYS,
  FinanceApiError,
  createRule,
  type Category,
  type Frequency,
  type Rule,
  type RuleInput,
} from "../../lib/finance-api";
import { parseIso, weekdayOf } from "../../lib/finance-dates";
import type { BranchScope } from "../../lib/internal-api";
import { hasCompanyWideScope } from "../../lib/branch-authority";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import { FinanceModal } from "./FinanceModal";
import { hintClass, labelClass } from "./FinanceBits";
import { amountProblem } from "./PaymentDialog";

const FREQUENCIES: Frequency[] = ["monthly", "yearly", "quarterly", "weekly"];
const CURRENCIES = ["PEN", "USD"];
const METHODS = ["transfer", "cash", "card", "other"] as const;
/** `services._positive`: the amount must stay below this. */
const AMOUNT_LIMIT = 10_000_000_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function RuleCreateDialog({
  companyId,
  scope,
  categories,
  today,
  onClose,
  onCreated,
  onUncertain,
  onVerify,
}: {
  companyId: number | null;
  /** The caller's branch scope, from the panel context. It decides what the branch list offers, not what is allowed. */
  scope: BranchScope | null | undefined;
  categories: Category[];
  /** The company's today (from the server): the default start. */
  today: string;
  onClose: () => void;
  onCreated: (rule: Rule) => void;
  /** The answer was lost or the server failed: the screen reads the rules and the lists again. */
  onUncertain: () => void;
  /** «Ver reglas»: close this dialog and show the Reglas tab, to check whether the rule was created. */
  onVerify: () => void;
}) {
  const ids = useId();
  const sending = useRef(false);
  const companyWide = hasCompanyWideScope(scope);
  const branches = scope?.branches ?? [];
  const parsedToday = parseIso(today);
  const preferred = scope?.default_branch && branches.some((b) => b.id === scope.default_branch?.id)
    ? scope.default_branch.id : branches[0]?.id;

  const [concept, setConcept] = useState("");
  const [payee, setPayee] = useState("");
  const [branch, setBranch] = useState(companyWide || preferred === undefined ? "" : String(preferred));
  const [category, setCategory] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("");
  const [frequency, setFrequency] = useState<Frequency>("monthly");
  const [monthDay, setMonthDay] = useState(String(parsedToday?.day ?? 1));
  const [weekDay, setWeekDay] = useState(
    String(parsedToday ? weekdayOf(parsedToday.year, parsedToday.month, parsedToday.day) : 0));
  const [anchor, setAnchor] = useState(today);
  const [endDate, setEndDate] = useState("");
  const [method, setMethod] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The rule may exist although no answer said so: nothing more is sent from this dialog.
  const [uncertain, setUncertain] = useState(false);
  const frozen = busy || uncertain;

  const weekly = frequency === "weekly";
  const noBranch = !companyWide && branches.length === 0;

  /** What is wrong, in the words the person needs; null when the server may be asked. */
  function problem(): string | null {
    const title = concept.trim();
    if (!title) return "Escribe el concepto.";
    if (title.length > 140) return "El concepto admite hasta 140 caracteres.";
    if (payee.trim().length > 140) return "El beneficiario admite hasta 140 caracteres.";
    if (noBranch) return "No tienes sucursales asignadas en esta empresa: no puedes crear pagos fijos.";
    if (!companyWide && !branch) return "Elige la sucursal.";
    const wrongAmount = amountProblem(amount);
    if (wrongAmount) return wrongAmount;
    if (Number(amount.trim()) >= AMOUNT_LIMIT) return "El importe es demasiado grande.";
    if (!weekly) {
      const day = monthDay.trim();
      if (!/^\d+$/.test(day) || Number(day) < 1 || Number(day) > 31) return "El día de vencimiento va del 1 al 31.";
    }
    if (!ISO_DATE.test(anchor) || !parseIso(anchor)) return "Elige la fecha de inicio.";
    if (endDate && !ISO_DATE.test(endDate)) return "La fecha de fin no es válida.";
    if (endDate && endDate < anchor) return "La fecha de fin no puede ser anterior al inicio.";
    if (notes.trim().length > 1000) return "Las notas admiten hasta 1000 caracteres.";
    return null;
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current || uncertain) return; // double click / double Enter / after a lost answer
    const wrong = problem();
    if (wrong) {
      setError(wrong);
      return;
    }
    const input: RuleInput = {
      concept: concept.trim(),
      payee_name: payee.trim(),
      branch: branch ? Number(branch) : null,
      category: category ? Number(category) : null,
      amount: amount.trim(),
      frequency,
      due_day: Number(weekly ? weekDay : monthDay.trim()),
      anchor_date: anchor,
      end_date: endDate || null,
      planned_method: method,
      notes: notes.trim(),
    };
    // Left out, the server takes the company's currency.
    if (currency) input.currency = currency;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      onCreated(await createRule(companyId, input));
    } catch (err) {
      const status = err instanceof FinanceApiError ? err.status : -1;
      setError(err instanceof Error ? err.message : "No se pudo crear el pago fijo.");
      if (status === 0 || status >= 500) {
        setUncertain(true);
        onUncertain();
      }
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <FinanceModal title="Nuevo pago fijo" description="Un pago que se repite: alquiler, servicios, suscripciones."
                  onClose={onClose} busy={busy}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <div>
          <label htmlFor={`${ids}-concept`} className={labelClass}>Concepto</label>
          <input id={`${ids}-concept`} value={concept} maxLength={140} disabled={frozen} autoComplete="off"
                 onChange={(e) => setConcept(e.target.value)} className={`${internalInputClass} mt-1`} />
        </div>
        <div>
          <label htmlFor={`${ids}-payee`} className={labelClass}>Beneficiario</label>
          <input id={`${ids}-payee`} value={payee} maxLength={140} disabled={frozen} autoComplete="off"
                 onChange={(e) => setPayee(e.target.value)} className={`${internalInputClass} mt-1`} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-branch`} className={labelClass}>Sucursal</label>
            <select id={`${ids}-branch`} value={branch} disabled={frozen || noBranch}
                    onChange={(e) => setBranch(e.target.value)} className={`${internalInputClass} mt-1`}>
              {companyWide ? <option value="">General (toda la empresa)</option> : null}
              {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={`${ids}-category`} className={labelClass}>Categoría (opcional)</label>
            <select id={`${ids}-category`} value={category} disabled={frozen}
                    onChange={(e) => setCategory(e.target.value)} className={`${internalInputClass} mt-1`}>
              <option value="">Sin categoría</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-amount`} className={labelClass}>Importe</label>
            <input id={`${ids}-amount`} inputMode="decimal" value={amount} disabled={frozen} placeholder="1250.00"
                   onChange={(e) => setAmount(e.target.value)} className={`${internalInputClass} mt-1`} />
          </div>
          <div>
            <label htmlFor={`${ids}-currency`} className={labelClass}>Moneda</label>
            <select id={`${ids}-currency`} value={currency} disabled={frozen}
                    onChange={(e) => setCurrency(e.target.value)} className={`${internalInputClass} mt-1`}>
              <option value="">La de la empresa</option>
              {CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
            </select>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-frequency`} className={labelClass}>Frecuencia</label>
            <select id={`${ids}-frequency`} value={frequency} disabled={frozen}
                    onChange={(e) => setFrequency(e.target.value as Frequency)} className={`${internalInputClass} mt-1`}>
              {FREQUENCIES.map((f) => <option key={f} value={f}>{FREQUENCY_LABELS[f]}</option>)}
            </select>
          </div>
          <div>
            {weekly ? (
              <>
                <label htmlFor={`${ids}-weekday`} className={labelClass}>Día de la semana</label>
                <select id={`${ids}-weekday`} value={weekDay} disabled={frozen}
                        onChange={(e) => setWeekDay(e.target.value)} className={`${internalInputClass} mt-1`}>
                  {WEEKDAYS.map((name, index) => <option key={name} value={index}>{name}</option>)}
                </select>
              </>
            ) : (
              <>
                <label htmlFor={`${ids}-day`} className={labelClass}>Día del mes</label>
                <input id={`${ids}-day`} type="number" min={1} max={31} value={monthDay} disabled={frozen}
                       aria-describedby={`${ids}-day-hint`}
                       onChange={(e) => setMonthDay(e.target.value)} className={`${internalInputClass} mt-1`} />
                <p id={`${ids}-day-hint`} className={hintClass}>En un mes más corto vence el último día.</p>
              </>
            )}
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-anchor`} className={labelClass}>Inicio</label>
            <input id={`${ids}-anchor`} type="date" value={anchor} disabled={frozen} aria-describedby={`${ids}-anchor-hint`}
                   onChange={(e) => setAnchor(e.target.value)} className={`${internalInputClass} mt-1`} />
            <p id={`${ids}-anchor-hint`} className={hintClass}>
              El primer vencimiento es el primero que cae en esta fecha o después (si es anterior a hoy, empieza
              desde hoy: no se crean vencimientos pasados). En un pago anual o trimestral, ese mes marca los siguientes.
            </p>
          </div>
          <div>
            <label htmlFor={`${ids}-end`} className={labelClass}>Fin (opcional)</label>
            <input id={`${ids}-end`} type="date" value={endDate} min={anchor || undefined} disabled={frozen}
                   onChange={(e) => setEndDate(e.target.value)} className={`${internalInputClass} mt-1`} />
          </div>
        </div>
        <div>
          <label htmlFor={`${ids}-method`} className={labelClass}>Forma de pago prevista (opcional)</label>
          <select id={`${ids}-method`} value={method} disabled={frozen}
                  onChange={(e) => setMethod(e.target.value)} className={`${internalInputClass} mt-1`}>
            <option value="">Sin indicar</option>
            {METHODS.map((m) => <option key={m} value={m}>{METHOD_LABELS[m]}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${ids}-notes`} className={labelClass}>Notas (opcional)</label>
          <textarea id={`${ids}-notes`} value={notes} rows={2} maxLength={1000} disabled={frozen}
                    onChange={(e) => setNotes(e.target.value)} className={`${internalInputClass} mt-1`} />
        </div>
        <ul className="list-disc space-y-1 rounded-lg border border-bd-border bg-surface-2 p-3 pl-7 text-xs text-muted">
          <li>
            Al crearla se generan los vencimientos de los próximos {PERIOD_WINDOW_DAYS} días; desde ese momento la
            frecuencia, el día y la moneda ya no se pueden cambiar (habría que terminar la regla y crear otra).
          </li>
          <li>Los avisos siguen la política de la empresa en la pestaña «Avisos».</li>
          <li>
            Cada vencimiento aparece {PERIOD_WINDOW_DAYS} días antes de la fecha: un aviso de más
            de {PERIOD_WINDOW_DAYS} días no llega a tiempo (el detalle está en «Avisos»).
          </li>
        </ul>
        {noBranch ? (
          <p role="note" className="rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-foreground">
            No tienes sucursales asignadas en esta empresa: no puedes crear pagos fijos.
          </p>
        ) : null}
        {error ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">{error}</p> : null}
        {uncertain ? (
          <div role="note" className="space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-foreground">
            <p className="font-semibold">Revisa la pestaña «Reglas» antes de intentarlo otra vez.</p>
            <p className="text-muted">
              No llegó una respuesta clara del servidor, así que el pago fijo pudo haberse creado. Los datos quedaron
              congelados para no crearlo dos veces. Si en «Reglas» no aparece, cierra este diálogo y créalo de nuevo.
            </p>
            <button type="button" onClick={onVerify} className={internalButtonClass}>Ver reglas</button>
          </div>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>Cancelar</button>
          <button type="submit" disabled={frozen || noBranch} className={internalPrimaryButtonClass}>
            {busy ? "Creando…" : "Crear pago fijo"}
          </button>
        </div>
      </form>
    </FinanceModal>
  );
}
