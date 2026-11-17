"use client";

/**
 * Productos › Modelos › «Paletas por armar» — PALETTE-SUGGEST-01.
 *
 * Every model of the company WITHOUT a palette, with the palette its variants suggest (the options they already
 * use, grouped by attribute in role order). Without a palette the supplier assistant and the pasted list cannot
 * validate a variant, so this is what blocks registering inventory.
 *
 * It reads `GET /admin/catalog/palette-suggestions/` (`products.view`). Saving is ONE model at a time, after an
 * explicit confirmation, with the model's own `PUT …/palette/` (`products.manage`, audited) and exactly the ids on
 * screen. There is no «save all». Before saving, the model's palette is read again: one that somebody saved in
 * the meantime is never replaced from here.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  CatalogApiError,
  fetchModelPalette,
  fetchPaletteSuggestions,
  setModelPalette,
  type PaletteSuggestionModel,
  type PaletteSuggestions,
} from "../lib/internal-api";
import { ROLE_LABELS } from "../lib/palette";
import { Panel, Spinner } from "./InventoryUi";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

const FALLBACK_ERROR = "No se pudieron leer las paletas por armar.";
const SAVE_ERROR = "No se pudo guardar la paleta.";
const ALREADY_SET = "Este modelo ya tiene paleta: alguien la guardó mientras tanto. Revísala en su ficha; desde aquí no se reemplaza.";

const touch = "min-h-[44px]";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const modelHref = (id: number) => `/admin/products/models/${id}#paleta`;

type RowState = { confirming: boolean; saving: boolean; error: string | null; saved: number | null };
const IDLE: RowState = { confirming: false, saving: false, error: null, saved: null };

function errorText(error: unknown, fallback: string): string {
  if (error instanceof CatalogApiError) return error.message || fallback;
  if (error instanceof TypeError) return "No se pudo conectar con el servidor. Revisa tu conexión y vuelve a intentarlo.";
  return fallback;
}

function SuggestedGroups({ row }: { row: PaletteSuggestionModel }) {
  return (
    <div className="space-y-2">
      {row.groups.map((group) => {
        const role = ROLE_LABELS[group.role] ?? ROLE_LABELS.other;
        const sameName = role.localeCompare(group.name, "es", { sensitivity: "base" }) === 0;
        return (
          <div key={group.attribute_id} role="group" aria-label={group.name} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-xs font-semibold text-foreground">{group.name}</span>
            {!sameName && group.role !== "other" ? <span className="text-xs text-muted">rol: {role}</span> : null}
            <ul className="flex flex-wrap gap-1.5">
              {group.options.map((option) => (
                <li key={option.id} className="inline-flex items-center gap-1.5 rounded-full border border-bd-border px-2.5 py-0.5 text-xs text-foreground">
                  {group.kind === "color" && option.swatch_hex ? (
                    <span aria-hidden="true" className="h-3 w-3 rounded-full border border-foreground/25" style={{ backgroundColor: option.swatch_hex }} />
                  ) : null}
                  {option.label}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function ModelRow({
  row, state, canManage, onAsk, onCancel, onConfirm,
}: {
  row: PaletteSuggestionModel;
  state: RowState;
  canManage: boolean;
  onAsk: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const count = row.suggested_option_ids.length;
  const confirmRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (state.confirming) confirmRef.current?.focus(); }, [state.confirming]);

  return (
    <li aria-label={row.name} className="rounded-lg border border-bd-border bg-background p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <h3 className="text-sm font-semibold text-foreground [overflow-wrap:anywhere]">{row.name}</h3>
            <span className="text-xs text-muted">{plural(row.variant_count, "variante", "variantes")}</span>
          </div>
          {count === 0 ? (
            <p className="text-sm text-muted">
              {row.variant_count === 0
                ? "Sin variantes: arma la paleta a mano."
                : "Sus variantes no llevan opciones: arma la paleta a mano."}
            </p>
          ) : (
            <SuggestedGroups row={row} />
          )}
          {count > 0 && row.incomplete_variants > 0 && state.saved === null ? (
            <p className="text-xs text-warning">
              {row.incomplete_variants === 1
                ? "1 variante no lleva todas estas opciones: con esta paleta quedará «Incompleta» hasta que la completes. Sigue vendiéndose."
                : `${row.incomplete_variants} variantes no llevan todas estas opciones: con esta paleta quedarán «Incompleta» hasta que las completes. Siguen vendiéndose.`}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2 self-start">
          <Link href={modelHref(row.model_id)} className={`${touch} ${internalButtonClass} inline-flex items-center justify-center`}>
            {count === 0 ? "Armar a mano" : canManage ? "Revisar y guardar" : "Revisar"}
          </Link>
          {canManage && count > 0 && state.saved === null && !state.confirming ? (
            <button type="button" className={`${touch} ${internalButtonClass}`} onClick={onAsk} disabled={state.saving}>
              Guardar esta paleta
            </button>
          ) : null}
        </div>
      </div>

      {state.confirming ? (
        <div
          ref={confirmRef}
          tabIndex={-1}
          role="group"
          aria-label={`Confirmar la paleta de ${row.name}`}
          className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-bd-border px-4 py-3 focus:outline-none"
        >
          <p className="min-w-0 flex-1 text-sm text-foreground">
            {`¿Guardar la paleta de «${row.name}» con ${count === 1 ? "esta opción" : `estas ${count} opciones`}?`}{" "}
            <span className="text-muted">Sus variantes sólo podrán combinar estas opciones; se puede cambiar después en su ficha.</span>
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={`${touch} ${internalButtonClass}`} onClick={onCancel} disabled={state.saving}>
              Cancelar
            </button>
            <button type="button" className={`${touch} ${internalPrimaryButtonClass}`} onClick={onConfirm} disabled={state.saving}>
              {state.saving ? "Guardando…" : "Confirmar y guardar"}
            </button>
          </div>
        </div>
      ) : null}
      {state.error ? (
        <p role="alert" className="mt-3 rounded-lg border border-danger-border px-4 py-3 text-sm text-danger">{state.error}</p>
      ) : null}
      {state.saved !== null ? (
        <p role="status" className="mt-3 rounded-lg border border-success-border px-4 py-3 text-sm text-success">
          {`Paleta guardada con ${plural(state.saved, "opción", "opciones")}.`}
        </p>
      ) : null}
    </li>
  );
}

export function PalettesToBuildPanel({ companyId, canManage }: { companyId: number | null; canManage: boolean }) {
  const [data, setData] = useState<PaletteSuggestions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<Record<number, RowState>>({});
  // The company the state on screen belongs to: a switch clears the previous company's list in the same render.
  const [shownFor, setShownFor] = useState<number | null>(companyId);
  if (shownFor !== companyId) {
    setShownFor(companyId);
    setData(null);
    setError(null);
    setRows({});
    setLoading(true);
  }
  // Only the latest read may write: a late answer for an earlier company is dropped.
  const latest = useRef(0);
  // A second click before React re-renders must not send a second PUT for the same model.
  const sending = useRef(new Set<number>());

  const request = useCallback(() => {
    const id = ++latest.current;
    const current = () => id === latest.current;
    fetchPaletteSuggestions(companyId)
      .then((body) => {
        if (!current()) return;
        setData(body);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!current()) return;
        setData(null);
        setError(errorText(err, FALLBACK_ERROR));
      })
      .finally(() => {
        if (current()) setLoading(false);
      });
    return () => {
      if (current()) latest.current += 1;
    };
  }, [companyId]);

  useEffect(() => request(), [request]);

  function retry() {
    setError(null);
    setLoading(true);
    request();
  }

  function update(modelId: number, change: Partial<RowState>) {
    setRows((all) => ({ ...all, [modelId]: { ...(all[modelId] ?? IDLE), ...change } }));
  }

  async function confirm(row: PaletteSuggestionModel) {
    if (sending.current.has(row.model_id)) return;
    sending.current.add(row.model_id);
    update(row.model_id, { saving: true, error: null });
    try {
      // Read again first: a palette saved by somebody else meanwhile is never replaced from this list.
      const now = await fetchModelPalette(row.model_id, companyId);
      if (now.has_palette) {
        update(row.model_id, { saving: false, confirming: false, error: ALREADY_SET });
        return;
      }
      const ids = [...row.suggested_option_ids];
      await setModelPalette(row.model_id, ids, companyId);
      update(row.model_id, { saving: false, confirming: false, saved: ids.length });
    } catch (err) {
      update(row.model_id, { saving: false, confirming: false, error: errorText(err, SAVE_ERROR) });
    } finally {
      sending.current.delete(row.model_id);
    }
  }

  // Nothing to build: the panel is not shown at all.
  if (data && data.models.length === 0) return null;

  const pending = data ? data.models.filter((row) => rows[row.model_id]?.saved == null).length : 0;

  return (
    <Panel
      id="paletas-por-armar"
      title="Paletas por armar"
      description="Los modelos sin paleta, con la que proponen sus variantes. Sin paleta, el asistente de proveedores y la lista pegada no pueden validar variantes. Revisa cada modelo y guárdalo: nada se guarda solo."
    >
      {loading && !data ? <Spinner label="Leyendo las paletas por armar…" /> : null}
      {error ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-danger-border bg-danger-surface px-4 py-3">
          <p className="text-sm text-danger">{error}</p>
          <button type="button" className={internalButtonClass} onClick={retry} disabled={loading}>
            Reintentar
          </button>
        </div>
      ) : null}
      {data ? (
        <div className="space-y-3">
          <p className="text-sm text-muted">
            {`${plural(data.models.length, "modelo sin paleta", "modelos sin paleta")} · ${pending} por confirmar. `}
            La propuesta son las opciones que ya usan sus variantes (nuevas y seminuevas, activas o no).
          </p>
          <ul className="space-y-3" aria-label="Modelos sin paleta">
            {data.models.map((row) => (
              <ModelRow
                key={row.model_id}
                row={row}
                state={rows[row.model_id] ?? IDLE}
                canManage={canManage}
                onAsk={() => update(row.model_id, { confirming: true, error: null })}
                onCancel={() => update(row.model_id, { confirming: false })}
                onConfirm={() => void confirm(row)}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}
