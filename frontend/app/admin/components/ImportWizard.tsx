"use client";

/**
 * The parts both import wizards share — Commercial Phase C1.4.
 *
 * WHY A PREVIEW TABLE IS THE WHOLE PRODUCT
 * ----------------------------------------
 * Uploading a spreadsheet is easy. The hard part, and the reason this screen
 * exists at all, is that a person has to be able to look at six hundred rows
 * and tell whether the machine understood them — BEFORE anything is written.
 * A stock import in particular cannot be undone: it becomes Kardex movements,
 * and the Kardex is an append-only record of physical fact.
 *
 * So errors sort to the top, every row says what will happen to it in one word,
 * and the apply button is disabled while a single row is in error.
 */

import type { ImportJob, ImportRow } from "../lib/internal-api";

export const STEP_LABELS_PRODUCTS = [
  "Archivo", "Hoja", "Columnas", "Previsualización", "Confirmación", "Resultado",
];
export const STEP_LABELS_STOCK = [
  "Archivo", "Almacenes", "Modo", "Previsualización", "Confirmación", "Resultado",
];

export function Stepper({ labels, current }: { labels: string[]; current: number }) {
  return (
    <ol className="mb-6 flex flex-wrap gap-2 text-[11px] font-semibold uppercase tracking-widest">
      {labels.map((label, index) => {
        const state =
          index === current ? "actual" : index < current ? "hecho" : "pendiente";
        return (
          <li
            key={label}
            aria-current={state === "actual" ? "step" : undefined}
            className={
              "rounded-full px-3 py-1.5 " +
              (state === "actual"
                ? "bg-surface-2 text-foreground"
                : state === "hecho"
                  ? "bg-emerald-500/10 text-emerald-300"
                  : "bg-surface text-muted")
            }
          >
            {index + 1}. {label}
          </li>
        );
      })}
    </ol>
  );
}

const ACTION_STYLE: Record<string, string> = {
  create: "bg-emerald-500/10 text-emerald-300",
  update: "bg-sky-500/10 text-sky-300",
  no_change: "bg-surface text-muted",
  skip: "bg-amber-500/10 text-amber-300",
  error: "bg-rose-500/15 text-rose-300",
};

const ACTION_LABEL: Record<string, string> = {
  create: "Crear",
  update: "Actualizar",
  no_change: "Sin cambios",
  skip: "Omitir",
  error: "Error",
};

function ActionTag({ action }: { action: string }) {
  return (
    <span
      className={
        "inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider " +
        (ACTION_STYLE[action] ?? "bg-surface text-muted")
      }
    >
      {ACTION_LABEL[action] ?? action}
    </span>
  );
}

export function CountsBar({ job }: { job: ImportJob }) {
  const items: [string, number, string][] = [
    ["Total", job.counts.total, "text-foreground/85"],
    ["Crear", job.counts.create, "text-emerald-300"],
    ["Actualizar", job.counts.update, "text-sky-300"],
    ["Sin cambios", job.counts.no_change, "text-muted"],
    ["Omitir", job.counts.skip, "text-amber-300"],
    ["Errores", job.counts.error, "text-rose-300"],
  ];
  return (
    <div className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
      {items.map(([label, value, tone]) => (
        <div
          key={label}
          className="rounded-xl border border-bd-border bg-background/30 px-3 py-2"
        >
          <div className="text-[10px] uppercase tracking-widest text-muted">
            {label}
          </div>
          <div className={"text-lg font-semibold tabular-nums " + tone}>{value}</div>
        </div>
      ))}
    </div>
  );
}

export function Notices({ job }: { job: ImportJob }) {
  const reader = job.summary?.reader_notes ?? [];
  const format = job.summary?.format_notes ?? [];
  const unmapped = job.summary?.unmapped ?? [];
  if (!reader.length && !format.length && !unmapped.length) return null;
  // There is no "the preview was truncated" case any more, and that is the
  // point: a file over the row limit is now REFUSED outright by the backend
  // with a 400, instead of being trimmed into a job that reported no errors and
  // could be applied. A warning here would have been a cap announced after the
  // fact; the refusal happens before anything is staged.
  return (
    <div className="mb-4 space-y-2 text-xs">
      {[...reader, ...format].map((note) => (
        <p
          key={note}
          className="rounded-lg border border-sky-400/20 bg-sky-500/[0.06] px-3 py-2 text-sky-200/90"
        >
          {note}
        </p>
      ))}
      {unmapped.length > 0 && (
        <details className="rounded-lg border border-bd-border bg-background/30 px-3 py-2 text-muted">
          <summary className="cursor-pointer text-foreground/85">
            {unmapped.length} columna(s) reconocidas que NO se importan
          </summary>
          <ul className="mt-2 space-y-1">
            {unmapped.map((entry) => (
              <li key={entry.column}>
                <span className="text-foreground">{entry.column}</span> — {entry.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function PreviewTable({
  rows,
  truncated,
  columns,
}: {
  rows: ImportRow[];
  truncated?: boolean;
  columns: { key: string; label: string; get: (row: ImportRow) => string }[];
}) {
  if (!rows.length) {
    return (
      <p className="rounded-xl border border-bd-border bg-background/30 px-4 py-6 text-center text-sm text-muted">
        El archivo no tiene filas de datos.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border">
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead className="bg-surface text-[10px] uppercase tracking-widest text-muted">
          <tr>
            <th className="px-3 py-2">Fila</th>
            {columns.map((column) => (
              <th key={column.key} className="px-3 py-2">
                {column.label}
              </th>
            ))}
            <th className="px-3 py-2">Acción</th>
            <th className="px-3 py-2">Notas</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.sheet}-${row.row}-${row.match_key}-${row.action}`}
              className={
                "border-t border-bd-border " +
                (row.action === "error" ? "bg-rose-500/[0.04]" : "")
              }
            >
              <td className="px-3 py-2 tabular-nums text-muted">{row.row}</td>
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-2 text-foreground/85">
                  {column.get(row)}
                </td>
              ))}
              <td className="px-3 py-2">
                <ActionTag action={row.action} />
              </td>
              <td className="px-3 py-2">
                {row.errors.map((message) => (
                  <p key={message} className="text-rose-300">
                    {message}
                  </p>
                ))}
                {row.warnings.map((message) => (
                  <p key={message} className="text-amber-300/80">
                    {message}
                  </p>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {truncated && (
        <p className="border-t border-bd-border px-3 py-2 text-[11px] text-muted">
          Se muestran las primeras filas. Los errores se listan primero, así que
          si no ves ninguno arriba, no hay ninguno.
        </p>
      )}
    </div>
  );
}

export function HistoryTable({ jobs }: { jobs: ImportJob[] }) {
  if (!jobs.length) {
    return (
      <p className="text-sm text-muted">Todavía no se ha importado nada.</p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border">
      <table className="w-full min-w-[760px] text-left text-xs">
        <thead className="bg-surface text-[10px] uppercase tracking-widest text-muted">
          <tr>
            <th className="px-3 py-2">Fecha</th>
            <th className="px-3 py-2">Tipo</th>
            <th className="px-3 py-2">Archivo</th>
            <th className="px-3 py-2">Usuario</th>
            <th className="px-3 py-2">Estado</th>
            <th className="px-3 py-2">Filas</th>
            <th className="px-3 py-2">Crear</th>
            <th className="px-3 py-2">Actualizar</th>
            <th className="px-3 py-2">Errores</th>
            <th className="px-3 py-2">Sucursales</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className="border-t border-bd-border">
              <td className="px-3 py-2 text-muted">
                {new Date(job.created_at).toLocaleString("es-PE")}
              </td>
              <td className="px-3 py-2 text-foreground/85">
                {job.import_type === "products" ? "Productos" : "Inventario"}
              </td>
              <td className="px-3 py-2 text-muted">{job.original_filename}</td>
              <td className="px-3 py-2 text-muted">{job.created_by || "—"}</td>
              <td className="px-3 py-2">
                <span
                  className={
                    "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider " +
                    (job.status === "applied"
                      ? "bg-emerald-500/10 text-emerald-300"
                      : job.status === "failed"
                        ? "bg-rose-500/15 text-rose-300"
                        : "bg-amber-500/10 text-amber-300")
                  }
                >
                  {job.status === "applied"
                    ? `Aplicado ${job.applied_by ? `· ${job.applied_by}` : ""}`
                    : job.status === "failed"
                      ? "Fallido"
                      : "Sólo previsualizado"}
                </span>
              </td>
              <td className="px-3 py-2 tabular-nums text-foreground/85">{job.counts.total}</td>
              <td className="px-3 py-2 tabular-nums text-emerald-300">
                {job.counts.create}
              </td>
              <td className="px-3 py-2 tabular-nums text-sky-300">{job.counts.update}</td>
              <td className="px-3 py-2 tabular-nums text-rose-300">{job.counts.error}</td>
              <td className="px-3 py-2 text-muted">
                {(job.summary?.branches ?? [])
                  .map((entry) => entry.branch_name)
                  .join(", ") || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
