"use client";

/**
 * The recurring rules behind the fixed payments (rent, services, subscriptions): what they are, how often, which
 * day, and whether they are active, paused or ended. Pausing, resuming, ending and editing need
 * `finance.manage`; without it this is a read-only list. Changing a rule never rewrites a period already
 * generated: it shapes the periods to come.
 */
import { useId, useRef, useState } from "react";
import {
  FREQUENCY_LABELS,
  WEEKDAYS,
  actOnRule,
  dueDayLabel,
  money,
  updateRule,
  type Rule,
  type RuleAction,
  type RuleChange,
} from "../../lib/finance-api";
import { formatDate } from "../../lib/finance-dates";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import { FinanceModal } from "./FinanceModal";
import { hintClass, labelClass, linkButtonClass } from "./FinanceBits";

function RuleStatus({ rule }: { rule: Rule }) {
  const [label, tone] = rule.ended
    ? ["Terminada", "border-bd-border text-muted"]
    : rule.paused
      ? ["Pausada", "border-warning-border bg-warning-surface text-warning"]
      : ["Activa", "border-success-border bg-success-surface text-success"];
  return <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${tone}`}>{label}</span>;
}

export function RulesPanel({
  rules,
  canManage,
  loading,
  error,
  onEdit,
  onAct,
}: {
  rules: Rule[] | null;
  canManage: boolean;
  loading: boolean;
  error: string | null;
  onEdit: (rule: Rule) => void;
  onAct: (rule: Rule, action: RuleAction) => void;
}) {
  if (loading && !rules) return <p role="status" className="text-sm text-muted">Cargando las reglas…</p>;
  if (error) return <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">{error}</p>;
  if (!rules || rules.length === 0) {
    return (
      <p className="rounded-xl border border-bd-border bg-surface p-6 text-sm text-muted">
        Todavía no hay pagos fijos configurados. Cuando existan (alquiler, servicios, suscripciones) los verás aquí.
      </p>
    );
  }
  return (
    <ul className="space-y-3">
      {rules.map((rule) => (
        <li key={rule.id} data-rule-id={rule.id} className="rounded-xl border border-bd-border bg-surface p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1 basis-56">
              <p className="break-words font-semibold text-foreground">{rule.concept}</p>
              <p className="mt-0.5 break-words text-xs text-muted">
                {[rule.payee_name, rule.category?.name, rule.branch?.name ?? "General"].filter(Boolean).join(" · ")}
              </p>
              <p className="mt-1 text-sm text-foreground">
                {FREQUENCY_LABELS[rule.frequency] ?? rule.frequency} · {dueDayLabel(rule)}
              </p>
              {rule.end_date ? (
                <p className="text-xs text-muted">{rule.ended ? "Terminó" : "Termina"} el {formatDate(rule.end_date)}{rule.end_reason ? ` · ${rule.end_reason}` : ""}</p>
              ) : null}
              {rule.paused && rule.paused_from ? (
                <p className="text-xs text-muted">En pausa desde el {formatDate(rule.paused_from)}</p>
              ) : null}
            </div>
            <div className="text-right">
              <p className="text-base font-bold tabular-nums text-foreground">{money(rule.amount, rule.currency)}</p>
              <RuleStatus rule={rule} />
            </div>
          </div>
          {canManage && !rule.ended ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className={linkButtonClass} onClick={() => onEdit(rule)}
                      aria-label={`Editar regla: ${rule.concept}`}>Editar</button>
              {rule.paused ? (
                <button type="button" className={linkButtonClass} onClick={() => onAct(rule, "resume")}
                        aria-label={`Reanudar regla: ${rule.concept}`}>Reanudar</button>
              ) : (
                <button type="button" className={linkButtonClass} onClick={() => onAct(rule, "pause")}
                        aria-label={`Pausar regla: ${rule.concept}`}>Pausar</button>
              )}
              <button type="button" className={linkButtonClass} onClick={() => onAct(rule, "end")}
                      aria-label={`Terminar regla: ${rule.concept}`}>Terminar</button>
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function RuleEditDialog({
  companyId, rule, onClose, onSaved,
}: {
  companyId: number | null;
  rule: Rule;
  onClose: () => void;
  onSaved: (rule: Rule) => void;
}) {
  const ids = useId();
  const sending = useRef(false);
  const [concept, setConcept] = useState(rule.concept);
  const [amount, setAmount] = useState(rule.amount);
  const [payee, setPayee] = useState(rule.payee_name);
  const [dueDay, setDueDay] = useState(String(rule.due_day));
  const [notes, setNotes] = useState(rule.notes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    const change: RuleChange = {};
    if (concept.trim() !== rule.concept) change.concept = concept.trim();
    if (amount.trim() !== rule.amount) change.amount = amount.trim();
    if (payee.trim() !== rule.payee_name) change.payee_name = payee.trim();
    if (notes.trim() !== rule.notes) change.notes = notes.trim();
    if (!rule.calendar_locked && Number(dueDay) !== rule.due_day) change.due_day = Number(dueDay);
    if (Object.keys(change).length === 0) {
      setError("No cambiaste nada.");
      return;
    }
    if (change.concept === "") {
      setError("Escribe el concepto.");
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      onSaved(await updateRule(companyId, rule.id, change));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la regla.");
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <FinanceModal title="Editar regla" description={rule.concept} onClose={onClose} busy={busy}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <p className="rounded-lg border border-bd-border bg-surface-2 p-3 text-xs text-muted">
          Los cambios valen para los períodos que se generen a partir de ahora. Un período ya generado conserva su importe.
        </p>
        <div>
          <label htmlFor={`${ids}-concept`} className={labelClass}>Concepto</label>
          <input id={`${ids}-concept`} value={concept} maxLength={140} disabled={busy}
                 onChange={(e) => setConcept(e.target.value)} className={`${internalInputClass} mt-1`} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-amount`} className={labelClass}>Importe ({rule.currency})</label>
            <input id={`${ids}-amount`} inputMode="decimal" value={amount} disabled={busy}
                   onChange={(e) => setAmount(e.target.value)} className={`${internalInputClass} mt-1`} />
          </div>
          <div>
            <label htmlFor={`${ids}-due`} className={labelClass}>
              {rule.frequency === "weekly" ? "Día de la semana" : "Día del mes"}
            </label>
            {rule.frequency === "weekly" ? (
              <select id={`${ids}-due`} value={dueDay} disabled={busy || rule.calendar_locked}
                      onChange={(e) => setDueDay(e.target.value)} className={`${internalInputClass} mt-1`}>
                {WEEKDAYS.map((name, index) => <option key={name} value={index}>{name}</option>)}
              </select>
            ) : (
              <input id={`${ids}-due`} type="number" min={1} max={31} value={dueDay}
                     disabled={busy || rule.calendar_locked}
                     onChange={(e) => setDueDay(e.target.value)} className={`${internalInputClass} mt-1`} />
            )}
            {rule.calendar_locked ? (
              <p className={hintClass}>
                El calendario no cambia después del primer período: termina esta regla y crea otra.
              </p>
            ) : null}
          </div>
        </div>
        <div>
          <label htmlFor={`${ids}-payee`} className={labelClass}>Beneficiario</label>
          <input id={`${ids}-payee`} value={payee} disabled={busy}
                 onChange={(e) => setPayee(e.target.value)} className={`${internalInputClass} mt-1`} />
        </div>
        <div>
          <label htmlFor={`${ids}-notes`} className={labelClass}>Notas</label>
          <textarea id={`${ids}-notes`} value={notes} rows={2} maxLength={1000} disabled={busy}
                    onChange={(e) => setNotes(e.target.value)} className={`${internalInputClass} mt-1`} />
        </div>
        {error ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">{error}</p> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>Cancelar</button>
          <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>{busy ? "Guardando…" : "Guardar regla"}</button>
        </div>
      </form>
    </FinanceModal>
  );
}

const ACTION_COPY: Record<RuleAction, { title: string; explain: string; confirm: string; reason: boolean }> = {
  pause: {
    title: "Pausar regla",
    explain: "Pausar no borra nada: los períodos que ya vencieron o tienen pagos siguen debiéndose. Los períodos que caigan dentro de la pausa dejan de contar como pendientes hasta que reanudes.",
    confirm: "Pausar",
    reason: true,
  },
  resume: {
    title: "Reanudar regla",
    explain: "Los períodos de la pausa vuelven a contar como pendientes.",
    confirm: "Reanudar",
    reason: false,
  },
  end: {
    title: "Terminar regla",
    explain: "La regla deja de generar períodos. Los períodos futuros sin pagos se anulan como «nunca vencidos»; lo ya vencido o pagado no se toca.",
    confirm: "Terminar regla",
    reason: true,
  },
};

export function RuleActionDialog({
  companyId, rule, action, today, onClose, onDone,
}: {
  companyId: number | null;
  rule: Rule;
  action: RuleAction;
  today: string;
  onClose: () => void;
  onDone: (rule: Rule) => void;
}) {
  const ids = useId();
  const copy = ACTION_COPY[action];
  const sending = useRef(false);
  const [reason, setReason] = useState("");
  const [endDate, setEndDate] = useState(today);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    if (copy.reason && !reason.trim()) {
      setError("Escribe el motivo.");
      return;
    }
    if (action === "end" && !endDate) {
      setError("Elige la fecha de fin.");
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      const body = action === "end" ? { reason: reason.trim(), end_date: endDate }
        : action === "pause" ? { reason: reason.trim() } : {};
      onDone(await actOnRule(companyId, rule.id, action, body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo aplicar la acción.");
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <FinanceModal title={copy.title} description={rule.concept} onClose={onClose} busy={busy}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <p className="rounded-lg border border-bd-border bg-surface-2 p-3 text-sm text-muted">{copy.explain}</p>
        {action === "end" ? (
          <div>
            <label htmlFor={`${ids}-end`} className={labelClass}>Fecha de fin</label>
            <input id={`${ids}-end`} type="date" value={endDate} min={rule.anchor_date} disabled={busy}
                   onChange={(e) => setEndDate(e.target.value)} className={`${internalInputClass} mt-1`} />
          </div>
        ) : null}
        {copy.reason ? (
          <div>
            <label htmlFor={`${ids}-reason`} className={labelClass}>Motivo</label>
            <input id={`${ids}-reason`} value={reason} maxLength={300} disabled={busy}
                   onChange={(e) => setReason(e.target.value)} className={`${internalInputClass} mt-1`} />
          </div>
        ) : null}
        {error ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">{error}</p> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} disabled={busy} className={internalButtonClass}>Cancelar</button>
          <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>{busy ? "Aplicando…" : copy.confirm}</button>
        </div>
      </form>
    </FinanceModal>
  );
}
