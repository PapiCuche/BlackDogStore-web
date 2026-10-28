"use client";

/**
 * INV-FAST-B2 — «Revisar y registrar». Reemplaza «copiar a Excel» (que sigue disponible): las filas del borrador
 * pasan por la vista previa del importador oficial de equipos y, con confirmación explícita, por su aplicar.
 * Cada fila muestra su estado: borrador, listo, necesita revisión, registrado. Nada se guarda en el navegador.
 */
import { useRef, useState } from "react";
import { NextStepBar, extractLabelBatch } from "./NextStepBar";
import { rememberBranch } from "../lib/last-branch";
import type { DraftCandidate, DraftPlan } from "../lib/inventory-text-draft";
import {
  STATUS_LABELS, applyReceiveJob, receivePayload, receiveRows, requestReceivePreview,
  type ReceiveJob, type ReceiveSettings,
} from "../lib/inventory-unit-receive";

type Props = {
  rows: DraftCandidate[];
  plan: DraftPlan;
  settings: ReceiveSettings;
  canSeeCosts: boolean;
  companyId?: number | null;
  onKeepEditing: () => void;
};

const STATUS_CLASS = { draft: "text-muted", ready: "text-success", review: "text-warning", registered: "text-success" };

export function UnitReceivePanel({ rows, plan, settings, canSeeCosts, companyId = null, onKeepEditing }: Props) {
  const [job, setJob] = useState<ReceiveJob | null>(null);
  const [done, setDone] = useState<ReceiveJob | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const working = useRef(false);

  const views = receiveRows(rows, plan, done ?? job, done !== null);
  const toRegister = job?.counts.create ?? 0;
  const rejected = job?.counts.error ?? 0;
  const noBranch = settings.branchId === null;
  const noReason = !settings.reason.trim();

  async function review() {
    if (working.current || noBranch || noReason || !plan.included.length) return;
    working.current = true;
    setBusy(true); setError(""); setConfirmed(false);
    try {
      setJob(await requestReceivePreview(receivePayload(plan, settings, canSeeCosts), companyId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo revisar la lista.");
    } finally {
      working.current = false; setBusy(false);
    }
  }

  async function register() {
    if (working.current || !job || !confirmed || rejected || !toRegister) return;
    working.current = true;
    setBusy(true); setError("");
    try {
      setDone(await applyReceiveJob(job.id, companyId));
      rememberBranch(settings.branchId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudieron registrar los equipos.");
    } finally {
      working.current = false; setBusy(false);
    }
  }

  const units = done?.summary?.applied?.units ?? toRegister;
  // «Publicación» searches by name: only when everything registered is one product.
  const names = new Set(plan.included.map(row => row.productId));
  const single = names.size === 1 ? { id: plan.included[0].productId as number, name: plan.included[0].productName } : { id: 0, name: "" };
  return (
    <section aria-label="Revisar y registrar" className="space-y-3 rounded-lg border border-bd-border p-3 text-xs">
      <div>
        <h4 className="text-sm font-semibold">Revisar y registrar</h4>
        <p className="mt-1 text-muted">
          Las filas pasan por el importador oficial de equipos: mismas reglas de serie, IMEI y sucursal. Revisar no registra nada;
          registrar es todo o nada.
        </p>
      </div>
      <div className="relative overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">Estado de cada fila de la lista</caption>
          <thead>
            <tr className="border-b border-bd-border">
              <th scope="col" className="p-2">#</th>
              <th scope="col" className="p-2">Producto</th>
              <th scope="col" className="p-2">Serie</th>
              <th scope="col" className="p-2">Estado</th>
              <th scope="col" className="p-2">Detalle</th>
            </tr>
          </thead>
          <tbody>
            {views.map(view => (
              <tr key={view.id} className="border-b border-bd-border align-top">
                <td className="p-2">{view.id}</td>
                <td className="p-2">{view.product}</td>
                <td className="p-2 font-mono">{view.serialTail}</td>
                <td className={`p-2 font-semibold ${STATUS_CLASS[view.status]}`}>{STATUS_LABELS[view.status]}</td>
                <td className="p-2">
                  {view.reason ? <span className="text-warning">{view.reason}</span> : null}
                  {view.notes.map((note, index) => <span key={index} className="block text-muted">{note}</span>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {job?.cost_column_ignored ? <p className="text-muted">El costo no se registró: tu acceso no incluye costos.</p> : null}
      {error ? <p role="alert" className="text-danger">{error}</p> : null}
      {done ? <Done job={done} units={units} product={single} /> : (
        <div className="space-y-2">
          {noBranch ? <p className="text-warning">Elige la sucursal donde entran los equipos.</p> : null}
          {noReason ? <p className="text-warning">Escribe el motivo o el documento de ingreso.</p> : null}
          {job && rejected ? (
            <p className="text-warning">
              {rejected} fila(s) necesitan revisión: el importador no registra nada mientras haya errores. Corrige y vuelve a revisar.
            </p>
          ) : null}
          {job && !rejected && toRegister ? (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
              <span>{`Confirmo registrar ${toRegister} ${toRegister === 1 ? "equipo" : "equipos"} en ${settings.branch}`}</span>
            </label>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {!job ? (
              <button type="button" onClick={() => void review()} disabled={busy || noBranch || noReason || !plan.included.length}
                className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-background disabled:opacity-40">
                {busy ? "Revisando…" : "Revisar y registrar"}
              </button>
            ) : (
              <button type="button" onClick={() => void register()} disabled={busy || !confirmed || Boolean(rejected) || !toRegister}
                className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-background disabled:opacity-40">
                {busy ? "Registrando…" : `Registrar ${toRegister} ${toRegister === 1 ? "equipo" : "equipos"}`}
              </button>
            )}
            <button type="button" onClick={onKeepEditing} disabled={busy}
              className="rounded-lg border border-bd-border px-3 py-1.5 font-semibold">
              Seguir corrigiendo
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/** INV-UX-A1's «Siguiente paso» (labels, publication), with LABEL-A's batches from the official apply's answer. */
function Done({ job, units, product }: { job: ReceiveJob; units: number; product: { id: number; name: string } }) {
  return (
    <div className="space-y-2">
      <p role="status" className="font-semibold text-success">
        {`${units} ${units === 1 ? "equipo registrado" : "equipos registrados"}.`}
      </p>
      {job.label_error ? (
        <p role="alert" className="text-warning">
          Los equipos están registrados, pero las etiquetas no se pudieron preparar: genéralas desde Etiquetas.
        </p>
      ) : null}
      <NextStepBar product={product} showStorefront={false} labelBatch={job.label_error ? null : extractLabelBatch(job)} />
    </div>
  );
}
