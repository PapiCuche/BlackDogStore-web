"use client";

/**
 * When this company is reminded of what it must pay.
 *
 * Everybody with `finance.view` reads it; only `can_manage` (the server's word, from
 * `finance.automation.manage`) edits it. A reminder is an internal notice in the bell and, if the company opts
 * in, an e-mail. There is no WhatsApp. A reminder never registers a payment or changes a debt, and reading,
 * snoozing or dismissing one leaves the payment exactly as it was.
 */
import { useId, useRef, useState } from "react";
import { updatePolicy, type ReminderPolicy } from "../../lib/finance-api";
import { formatInstant } from "../../lib/finance-dates";
import { internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import { hintClass, labelClass } from "./FinanceBits";

export const MAX_OFFSET = 60;
export const MAX_OFFSETS = 8;

/** «7, 3, 1, 0» → [7, 3, 1, 0]; or the sentence that says what is wrong. */
export function parseOffsets(text: string, { allowEmpty = false } = {}): { ok: true; value: number[] } | { ok: false; error: string } {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  if (parts.length === 0) {
    return allowEmpty ? { ok: true, value: [] } : { ok: false, error: "Indica al menos un día." };
  }
  const numbers: number[] = [];
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return { ok: false, error: `«${part}» no es un número de días.` };
    numbers.push(Number(part));
  }
  const unique = [...new Set(numbers)].sort((a, b) => b - a);
  if (unique.length > MAX_OFFSETS) return { ok: false, error: `Como mucho ${MAX_OFFSETS} días.` };
  if (unique.some((n) => n > MAX_OFFSET)) return { ok: false, error: `Cada día va de 0 a ${MAX_OFFSET}.` };
  return { ok: true, value: unique };
}

function describeOffset(n: number): string {
  if (n === 0) return "el día del vencimiento";
  return n === 1 ? "1 día antes" : `${n} días antes`;
}

export function ReminderPolicyPanel({
  companyId,
  policy,
  loading,
  error,
  onSaved,
}: {
  companyId: number | null;
  policy: ReminderPolicy | null;
  loading: boolean;
  error: string | null;
  onSaved: (policy: ReminderPolicy) => void;
}) {
  // Kept here, not in the form: a saved policy re-keys the form (its draft resets to the stored values), and the
  // confirmation has to survive that.
  const [saved, setSaved] = useState(false);
  if (loading && !policy) return <p role="status" className="text-sm text-muted">Cargando los avisos…</p>;
  if (error && !policy) return <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">{error}</p>;
  if (!policy) return null;
  // Re-keyed on what the server last said, so a saved policy resets the draft to the stored values.
  const stamp = `${policy.enabled}|${policy.offsets.join(",")}|${policy.overdue_every_days}|${policy.overdue_max}|${policy.email_enabled}|${policy.email_offsets.join(",")}|${policy.email_overdue}`;
  return (
    <PolicyForm
      key={stamp} companyId={companyId} policy={policy} saved={saved}
      onSaved={(next) => { setSaved(true); onSaved(next); }}
      onEdited={() => setSaved(false)}
    />
  );
}

function PolicyForm({
  companyId, policy, saved, onSaved, onEdited,
}: {
  companyId: number | null; policy: ReminderPolicy; saved: boolean;
  onSaved: (policy: ReminderPolicy) => void; onEdited: () => void;
}) {
  const ids = useId();
  const editable = policy.can_manage;
  const sending = useRef(false);
  const [enabled, setEnabled] = useState(policy.enabled);
  const [offsetsText, setOffsetsText] = useState(policy.offsets.join(", "));
  const [everyDays, setEveryDays] = useState(String(policy.overdue_every_days));
  const [maxOverdue, setMaxOverdue] = useState(String(policy.overdue_max));
  const [emailEnabled, setEmailEnabled] = useState(policy.email_enabled);
  const [emailOffsets, setEmailOffsets] = useState<number[]>(policy.email_offsets);
  const [emailOverdue, setEmailOverdue] = useState(policy.email_overdue);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const parsed = parseOffsets(offsetsText);
  const offsetChoices = parsed.ok ? parsed.value : policy.offsets;

  function toggleEmailOffset(n: number) {
    onEdited();
    setEmailOffsets((current) => (current.includes(n) ? current.filter((x) => x !== n) : [...current, n]));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current || !editable) return;
    onEdited();
    if (!parsed.ok) {
      setProblem(`Avisos antes del vencimiento: ${parsed.error}`);
      return;
    }
    const every = Number(everyDays);
    const max = Number(maxOverdue);
    if (!Number.isInteger(every) || every < 1 || every > 60) {
      setProblem("Los avisos de vencido se repiten cada 1 a 60 días.");
      return;
    }
    if (!Number.isInteger(max) || max < 0 || max > 12) {
      setProblem("Los avisos de vencido son de 0 a 12.");
      return;
    }
    sending.current = true;
    setBusy(true);
    setProblem(null);
    try {
      const next = await updatePolicy(companyId, {
        enabled,
        offsets: parsed.value,
        overdue_every_days: every,
        overdue_max: max,
        email_enabled: emailEnabled,
        email_offsets: emailOffsets.filter((n) => parsed.value.includes(n)).sort((a, b) => b - a),
        email_overdue: emailOverdue,
      });
      onSaved(next);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "No se pudo guardar la política de avisos.");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5" noValidate aria-label="Política de avisos">
      <div className="rounded-xl border border-bd-border bg-surface p-4 text-sm text-muted">
        <p className="font-semibold text-foreground">Cómo funcionan los avisos</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>Un aviso es una <strong className="text-foreground">notificación interna</strong>: aparece en la campana de quienes pueden ver los pagos de esa sucursal.</li>
          <li>El <strong className="text-foreground">correo es opcional</strong> y está apagado hasta que la empresa lo active. No se envía nada por WhatsApp.</li>
          <li>Un aviso nunca registra un pago ni cambia lo que se debe. Leerlo, posponerlo o descartarlo deja el pago igual.</li>
        </ul>
      </div>

      {!editable ? (
        <p role="note" className="rounded-lg border border-info-border bg-info-surface p-3 text-sm text-foreground">
          Estás viendo la política de avisos en modo lectura. Sólo quien tiene el permiso «Recordatorios internos» puede cambiarla.
        </p>
      ) : null}

      <fieldset disabled={!editable || busy} className="space-y-5">
        <label className="flex items-start gap-3 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => { setEnabled(e.target.checked); onEdited(); }}
                 className="mt-1" />
          <span>
            <span className="font-semibold text-foreground">Enviar avisos internos de pagos por vencer</span>
            <span className="block text-xs text-muted">Si lo apagas, no se crea ningún aviso nuevo. Los pagos y sus fechas no cambian.</span>
          </span>
        </label>

        <div>
          <label htmlFor={`${ids}-offsets`} className={labelClass}>Días antes del vencimiento</label>
          <input id={`${ids}-offsets`} value={offsetsText} inputMode="numeric"
                 onChange={(e) => { setOffsetsText(e.target.value); onEdited(); }}
                 className={`${internalInputClass} mt-1 max-w-xs`} aria-describedby={`${ids}-offsets-hint`} />
          <p id={`${ids}-offsets-hint`} className={hintClass}>
            Separados por comas; 0 es el día del vencimiento. Ejemplo: 7, 3, 1, 0. Se avisa una vez por cada día indicado.
          </p>
          {parsed.ok ? (
            <p className="mt-1 text-xs text-muted">Avisará: {parsed.value.map(describeOffset).join(", ")}.</p>
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${ids}-every`} className={labelClass}>Si ya venció, repetir cada (días)</label>
            <input id={`${ids}-every`} type="number" min={1} max={60} value={everyDays}
                   onChange={(e) => { setEveryDays(e.target.value); onEdited(); }}
                   className={`${internalInputClass} mt-1`} />
          </div>
          <div>
            <label htmlFor={`${ids}-max`} className={labelClass}>Máximo de avisos de vencido</label>
            <input id={`${ids}-max`} type="number" min={0} max={12} value={maxOverdue}
                   onChange={(e) => { setMaxOverdue(e.target.value); onEdited(); }}
                   className={`${internalInputClass} mt-1`} />
            <p className={hintClass}>0 = no avisar de lo vencido.</p>
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-bd-border p-4">
          <label className="flex items-start gap-3 text-sm">
            <input type="checkbox" checked={emailEnabled}
                   onChange={(e) => { setEmailEnabled(e.target.checked); onEdited(); }} className="mt-1" />
            <span>
              <span className="font-semibold text-foreground">Enviar también por correo</span>
              <span className="block text-xs text-muted">Opcional. Elige en qué momentos se manda el correo; el aviso interno sale siempre.</span>
            </span>
          </label>
          {emailEnabled ? (
            <div className="space-y-2 pl-7 text-sm">
              <p className="text-xs font-semibold text-foreground">Mandar correo…</p>
              {offsetChoices.map((n) => (
                <label key={n} className="flex items-center gap-2">
                  <input type="checkbox" checked={emailOffsets.includes(n)} onChange={() => toggleEmailOffset(n)} />
                  {describeOffset(n)}
                </label>
              ))}
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={emailOverdue}
                       onChange={(e) => { setEmailOverdue(e.target.checked); onEdited(); }} />
                Cuando ya venció
              </label>
            </div>
          ) : null}
        </div>
      </fieldset>

      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs text-muted">Última revisión automática</dt>
          <dd className="text-foreground">{policy.last_run_at ? formatInstant(policy.last_run_at) : "Todavía no se ejecutó"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Último error</dt>
          <dd className={policy.last_error ? "text-danger" : "text-foreground"}>{policy.last_error || "Ninguno"}</dd>
        </div>
      </dl>

      {problem ? (
        <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface p-3 text-sm text-danger">{problem}</p>
      ) : null}
      {saved ? <p role="status" className="text-sm text-success">Política de avisos guardada.</p> : null}

      {editable ? (
        <div className="flex justify-end">
          <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>
            {busy ? "Guardando…" : "Guardar política de avisos"}
          </button>
        </div>
      ) : null}
    </form>
  );
}
