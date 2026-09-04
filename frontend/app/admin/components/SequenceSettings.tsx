"use client";

/**
 * Configuración → Numeración interna — Phase 2E.
 *
 * Three things this screen is careful about, all of them for the same reason:
 * a document number is an identifier somebody else is holding.
 *
 *   1. THE PREVIEW ALLOCATES NOTHING. It is rendered in the browser from the
 *      values in the form. A preview that asked the server would either lag
 *      behind what is typed or, worse, invite an implementation that takes a
 *      number and throws it away.
 *
 *   2. THE COUNTER LOCKS AFTER THE FIRST DOCUMENT. Before then it is genuinely
 *      useful — a business migrating from another system starts at 5001. After,
 *      the field is disabled and says why, rather than accepting a value the
 *      backend will reject.
 *
 *   3. THE SCOPE LOCKS TOO, and the explanation is on screen. Switching after
 *      issuing would make the next note repeat a number this company has already
 *      printed.
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchSequences,
  previewNumber,
  updateSequence,
  updateSequenceScope,
  type InternalSequenceRow,
  type SequenceList,
  type SequenceScope,
} from "../lib/internal-api";

const FIELD =
  "w-full rounded-lg border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border disabled:opacity-50";

function SequenceRow({
  sequence,
  canManage,
  onSaved,
}: {
  sequence: InternalSequenceRow;
  canManage: boolean;
  onSaved: (next: InternalSequenceRow) => void;
}) {
  const [prefix, setPrefix] = useState(sequence.prefix);
  const [padding, setPadding] = useState(String(sequence.padding));
  const [nextValue, setNextValue] = useState(String(sequence.next_value));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const dirty =
    prefix !== sequence.prefix ||
    padding !== String(sequence.padding) ||
    nextValue !== String(sequence.next_value);

  async function save() {
    setSaving(true);
    setErrors({});
    setSaved(false);
    try {
      const payload: Record<string, string | number> = {
        prefix,
        padding: Number(padding) || sequence.padding,
      };
      // Only sent when it may legitimately change; the backend rejects it
      // otherwise, and sending it anyway would turn every save into an error.
      if (sequence.can_edit_next_value) payload.next_value = Number(nextValue) || 1;

      onSaved(await updateSequence(sequence.id, payload));
      setSaved(true);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      setErrors(fields ?? { __all__: err instanceof Error ? err.message : "Error." });
    } finally {
      setSaving(false);
    }
  }

  const label = sequence.branch_name ?? "Toda la empresa";
  const preview = previewNumber(prefix, Number(padding) || 1, Number(nextValue) || 1);

  return (
    <div className="rounded-xl border border-bd-border bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="font-mono text-sm text-muted">
          Próximo: <span className="text-foreground">{preview}</span>
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <label
            className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
            htmlFor={`seq-prefix-${sequence.id}`}
          >
            Prefijo
          </label>
          <input
            id={`seq-prefix-${sequence.id}`}
            className={`${FIELD} ${errors.prefix ? "border-danger-border" : "border-bd-border"} font-mono`}
            value={prefix}
            maxLength={12}
            disabled={!canManage || saving}
            onChange={(e) => setPrefix(e.target.value)}
          />
          {errors.prefix ? (
            <p className="mt-1.5 text-xs text-danger">{errors.prefix}</p>
          ) : (
            <p className="mt-1.5 text-[11px] text-muted">
              Letras, dígitos, guion y guion bajo.
            </p>
          )}
        </div>

        <div>
          <label
            className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
            htmlFor={`seq-padding-${sequence.id}`}
          >
            Dígitos
          </label>
          <input
            id={`seq-padding-${sequence.id}`}
            type="number"
            min={1}
            max={12}
            className={`${FIELD} ${errors.padding ? "border-danger-border" : "border-bd-border"}`}
            value={padding}
            disabled={!canManage || saving}
            onChange={(e) => setPadding(e.target.value)}
          />
          {errors.padding ? (
            <p className="mt-1.5 text-xs text-danger">{errors.padding}</p>
          ) : null}
        </div>

        <div>
          <label
            className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
            htmlFor={`seq-next-${sequence.id}`}
          >
            Próximo número
          </label>
          <input
            id={`seq-next-${sequence.id}`}
            type="number"
            min={1}
            className={`${FIELD} ${errors.next_value ? "border-danger-border" : "border-bd-border"}`}
            value={nextValue}
            disabled={!canManage || saving || !sequence.can_edit_next_value}
            onChange={(e) => setNextValue(e.target.value)}
          />
          {errors.next_value ? (
            <p className="mt-1.5 text-xs text-danger">{errors.next_value}</p>
          ) : (
            <p className="mt-1.5 text-[11px] text-muted">
              {sequence.can_edit_next_value
                ? "Puedes fijarlo antes de emitir el primer documento — útil si migras desde otro sistema."
                : "Bloqueado: esta serie ya emitió documentos."}
            </p>
          )}
        </div>
      </div>

      {errors.__all__ ? (
        <p className="mt-3 text-sm text-danger">{errors.__all__}</p>
      ) : null}

      {canManage ? (
        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            disabled={saving || !dirty}
            onClick={() => void save()}
            className="rounded-lg border border-bd-border px-4 py-2 text-sm font-medium text-foreground transition hover:border-bd-border hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? "Guardando…" : "Guardar serie"}
          </button>
          {saved && !dirty ? (
            <span className="text-sm text-muted">Guardado.</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function SequenceSettings({ companyId }: { companyId: number | null }) {
  const [data, setData] = useState<SequenceList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setData(await fetchSequences(companyId));
  }, [companyId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar la numeración.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function changeScope(scope: SequenceScope) {
    setBusy(true);
    setScopeError(null);
    try {
      await updateSequenceScope(companyId, scope);
      await load();
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      setScopeError(
        fields?.scope ?? (err instanceof Error ? err.message : "No se pudo cambiar."),
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="py-6 text-sm text-muted">Cargando numeración…</p>;
  if (error) {
    return (
      <div className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">
        {error}
      </div>
    );
  }
  if (!data) return null;

  const canManage = data.can_manage;

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-bd-border bg-surface px-4 py-3">
        <p className="text-xs text-muted">{data.notice}</p>
      </div>

      <fieldset className="space-y-3">
        <legend className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-muted">
          Alcance
        </legend>

        {(
          [
            ["company", "Una numeración para toda la empresa", "Todas las sucursales comparten el mismo correlativo."],
            ["branch", "Una numeración por sucursal", "Cada sucursal lleva su propio correlativo, empezando en 1."],
          ] as [SequenceScope, string, string][]
        ).map(([value, title, hint]) => (
          <label key={value} className="flex items-start gap-2 text-sm text-foreground/85">
            <input
              type="radio"
              name="sequence-scope"
              className="mt-1"
              checked={data.scope === value}
              disabled={!canManage || busy || !data.can_change_scope}
              onChange={() => void changeScope(value)}
            />
            <span>
              {title}
              <span className="block text-xs text-muted">{hint}</span>
            </span>
          </label>
        ))}

        {!data.can_change_scope ? (
          <p className="text-[11px] text-muted">
            El alcance queda fijo una vez emitido el primer documento: cambiarlo
            haría que una nota nueva repitiera un número que esta empresa ya
            imprimió.
          </p>
        ) : null}
        {scopeError ? <p className="text-sm text-danger">{scopeError}</p> : null}
      </fieldset>

      <div className="space-y-4">
        {data.results.map((sequence) => (
          <SequenceRow
            key={sequence.id}
            sequence={sequence}
            canManage={canManage}
            onSaved={(next) =>
              setData((prev) =>
                prev
                  ? {
                      ...prev,
                      results: prev.results.map((r) => (r.id === next.id ? next : r)),
                    }
                  : prev,
              )
            }
          />
        ))}

        {data.scope === "branch" && data.results.length === 1 ? (
          <p className="text-[11px] text-muted">
            Las series de cada sucursal se crean al emitir su primera nota.
          </p>
        ) : null}
      </div>
    </div>
  );
}
