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

import { useState } from "react";
import type { ImportJob, ImportRow } from "../lib/internal-api";
import { ImageDropzone } from "./ImageDropzone";

export const STEP_LABELS_PRODUCTS = [
  "Archivo", "Hoja", "Columnas e imágenes", "Previsualización", "Confirmación", "Resultado",
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
                ? "bg-foreground/15 text-foreground"
                : state === "hecho"
                  ? "bg-success-surface text-success"
                  : "bg-foreground/[0.03] text-muted/70")
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
  create: "bg-success-surface text-success",
  update: "bg-info-surface text-info",
  no_change: "bg-foreground/[0.04] text-muted",
  skip: "bg-warning-surface text-warning",
  error: "bg-danger-surface text-danger",
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
        (ACTION_STYLE[action] ?? "bg-foreground/[0.04] text-muted")
      }
    >
      {ACTION_LABEL[action] ?? action}
    </span>
  );
}

export function CountsBar({ job }: { job: ImportJob }) {
  const items: [string, number, string][] = [
    ["Total", job.counts.total, "text-foreground"],
    ["Crear", job.counts.create, "text-success"],
    ["Actualizar", job.counts.update, "text-info"],
    ["Sin cambios", job.counts.no_change, "text-muted"],
    ["Omitir", job.counts.skip, "text-warning"],
    ["Errores", job.counts.error, "text-danger"],
  ];
  return (
    <div className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
      {items.map(([label, value, tone]) => (
        <div
          key={label}
          className="rounded-xl border border-bd-border bg-surface px-3 py-2"
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
          className="rounded-lg border border-info-border bg-info-surface px-3 py-2 text-info"
        >
          {note}
        </p>
      ))}
      {unmapped.length > 0 && (
        <details className="rounded-lg border border-bd-border bg-surface px-3 py-2 text-muted">
          <summary className="cursor-pointer text-foreground">
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
      <p className="rounded-xl border border-bd-border bg-surface px-4 py-6 text-center text-sm text-muted">
        El archivo no tiene filas de datos.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface">
      <table className="w-full min-w-[720px] text-left text-xs">
        <thead className="bg-surface-2 text-[10px] uppercase tracking-widest text-muted">
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
                "border-t border-bd-border/70 " +
                (row.action === "error" ? "bg-danger-surface" : "")
              }
            >
              <td className="px-3 py-2 tabular-nums text-muted">{row.row}</td>
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-2 text-foreground">
                  {column.get(row)}
                </td>
              ))}
              <td className="px-3 py-2">
                <ActionTag action={row.action} />
              </td>
              <td className="px-3 py-2">
                {row.errors.map((message) => (
                  <p key={message} className="text-danger">
                    {message}
                  </p>
                ))}
                {row.warnings.map((message) => (
                  <p key={message} className="text-warning">
                    {message}
                  </p>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {truncated && (
        <p className="border-t border-bd-border/70 px-3 py-2 text-[11px] text-muted">
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
    <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface">
      <table className="w-full min-w-[760px] text-left text-xs">
        <thead className="bg-surface-2 text-[10px] uppercase tracking-widest text-muted">
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
            <tr key={job.id} className="border-t border-bd-border/70">
              <td className="px-3 py-2 text-muted">
                {new Date(job.created_at).toLocaleString("es-PE")}
              </td>
              <td className="px-3 py-2 text-foreground">
                {job.import_type === "products" ? "Productos" : "Inventario"}
              </td>
              <td className="px-3 py-2 text-muted">{job.original_filename}</td>
              <td className="px-3 py-2 text-muted">{job.created_by || "—"}</td>
              <td className="px-3 py-2">
                <span
                  className={
                    "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider " +
                    (job.status === "applied"
                      ? "bg-success-surface text-success"
                      : job.status === "failed"
                        ? "bg-danger-surface text-danger"
                        : "bg-warning-surface text-warning")
                  }
                >
                  {job.status === "applied"
                    ? `Aplicado ${job.applied_by ? `· ${job.applied_by}` : ""}`
                    : job.status === "failed"
                      ? "Fallido"
                      : "Sólo previsualizado"}
                </span>
              </td>
              <td className="px-3 py-2 tabular-nums text-foreground">{job.counts.total}</td>
              <td className="px-3 py-2 tabular-nums text-success">
                {job.counts.create}
              </td>
              <td className="px-3 py-2 tabular-nums text-info">{job.counts.update}</td>
              <td className="px-3 py-2 tabular-nums text-danger">{job.counts.error}</td>
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


// ---------------------------------------------------------------------------
// BULK-MEDIA — las imágenes que acompañan a una carga masiva de productos
// ---------------------------------------------------------------------------

const IMPORT_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp";
const IMAGE_NAME = /\.(png|jpe?g|webp)$/i;

function megabytes(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/**
 * Adjuntar las imágenes de la importación: archivos sueltos, un ZIP, o ambos.
 *
 * NO SUBE NADA. Los archivos viajan con el libro al previsualizar, y es el
 * servidor quien los casa con las filas por su nombre. Aquí sólo se descarta lo
 * que por su nombre no es una imagen, para no enviar lo que se va a ignorar.
 */
export function ImportImagesField({
  images, zip, onChange, disabled = false,
}: {
  images: File[];
  zip: File | null;
  onChange: (images: File[], zip: File | null) => void;
  disabled?: boolean;
}) {
  const [notice, setNotice] = useState<string | null>(null);

  function addImages(files: File[]) {
    const known = new Set(images.map((file) => file.name.toLowerCase()));
    const next = [...images];
    const skipped: string[] = [];
    for (const file of files) {
      if (!IMAGE_NAME.test(file.name)) {
        skipped.push(file.name);
      } else if (!known.has(file.name.toLowerCase())) {
        known.add(file.name.toLowerCase());
        next.push(file);
      }
    }
    setNotice(skipped.length ? `No son imágenes PNG, JPEG o WebP y no se adjuntan: ${skipped.join(", ")}.` : null);
    onChange(next, zip);
  }

  const total = images.reduce((sum, file) => sum + file.size, 0) + (zip?.size ?? 0);

  return (
    <div className="space-y-3 rounded-xl border border-bd-border bg-surface px-4 py-4">
      <div>
        <p className="text-sm font-semibold text-foreground">Imágenes de los productos (opcional)</p>
        <p className="mt-1 text-xs text-muted">
          En el Excel, la columna «Imagen principal» lleva el nombre de un archivo y «Imágenes» varios
          separados por <code>|</code>. Adjunta aquí esos archivos: se casan por su nombre, sin mirar
          mayúsculas ni carpetas.
        </p>
      </div>

      <ImageDropzone
        label="Elegir imágenes"
        accept={IMPORT_IMAGE_ACCEPT}
        onFiles={addImages}
        disabled={disabled}
        hint="PNG, JPEG o WebP, hasta 8 MB cada una. También puedes arrastrarlas hasta aquí."
      />

      <label className="block text-xs text-muted">
        <span className="font-semibold text-foreground">Elegir un ZIP</span> con las imágenes, si son muchas
        <input
          type="file"
          accept=".zip,application/zip"
          disabled={disabled}
          onChange={(event) => {
            const selected = event.target.files?.[0] ?? null;
            event.target.value = "";
            if (selected) onChange(images, selected);
          }}
          className="mt-1.5 block w-full text-sm text-muted file:mr-4 file:rounded-full file:border file:border-bd-border file:bg-background file:px-4 file:py-2 file:text-sm file:font-semibold file:text-foreground"
        />
      </label>

      {notice ? <p role="status" className="text-xs text-warning">{notice}</p> : null}

      {images.length || zip ? (
        <div className="rounded-lg border border-bd-border bg-background p-3 text-xs">
          <p className="font-semibold text-foreground">
            {images.length === 1 ? "1 imagen" : `${images.length} imágenes`}
            {zip ? " y un ZIP" : ""} · {megabytes(total)}
          </p>
          <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
            {zip ? (
              <li className="flex items-center justify-between gap-3">
                <span className="min-w-0 break-all text-foreground">{zip.name}</span>
                <button type="button" aria-label={`Quitar ${zip.name}`} disabled={disabled} onClick={() => onChange(images, null)} className="shrink-0 text-muted underline underline-offset-4 hover:text-danger">
                  Quitar
                </button>
              </li>
            ) : null}
            {images.map((file) => (
              <li key={file.name} className="flex items-center justify-between gap-3">
                <span className="min-w-0 break-all text-foreground">{file.name}</span>
                <button
                  type="button"
                  aria-label={`Quitar ${file.name}`}
                  disabled={disabled}
                  onClick={() => onChange(images.filter((entry) => entry !== file), zip)}
                  className="shrink-0 text-muted underline underline-offset-4 hover:text-danger"
                >
                  Quitar
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** Lo que el servidor casó entre filas e imágenes. Vacío si el archivo no habla de imágenes. */
export function MediaSummary({ job }: { job: ImportJob }) {
  const media = job.summary?.media;
  if (!media || (media.attached === 0 && media.referenced === 0 && media.ignored === 0)) return null;

  const items: [string, number, string][] = [
    ["Adjuntas", media.attached, "text-foreground"],
    ["Válidas", media.valid, "text-success"],
    ["Faltantes", media.missing, media.missing ? "text-danger" : "text-muted"],
    ["Inválidas", media.invalid, media.invalid ? "text-danger" : "text-muted"],
    ["Duplicadas", media.duplicates, "text-muted"],
    ["Sin usar", media.orphans, media.orphans ? "text-warning" : "text-muted"],
  ];
  const nothingAttached = media.attached === 0 && media.referenced > 0;

  return (
    <section aria-label="Imágenes de la importación" className="mb-4 rounded-xl border border-bd-border bg-surface px-4 py-3">
      <h3 className="text-xs font-semibold uppercase tracking-widest text-muted">Imágenes</h3>
      <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
        {items.map(([label, value, tone]) => (
          <div key={label} className="rounded-lg border border-bd-border bg-background px-3 py-2">
            <p className="text-[10px] uppercase tracking-widest text-muted">{label}</p>
            <p className={`text-lg font-semibold tabular-nums ${tone}`}>{value}</p>
          </div>
        ))}
      </div>
      <div className="mt-2 space-y-1 text-xs text-muted">
        {nothingAttached ? (
          <p className="text-danger">
            Las filas citan {media.referenced} imagen(es) y no se adjuntó ninguna. Adjunta las imágenes en el paso
            anterior y vuelve a previsualizar.
          </p>
        ) : null}
        {media.already_present ? (
          <p>
            {media.already_present === 1
              ? "1 ya estaba en la galería de su producto y no se duplica."
              : `${media.already_present} ya estaban en la galería de su producto y no se duplican.`}
          </p>
        ) : null}
        {media.orphans ? (
          <p>
            Adjuntas que ninguna fila cita (no se guardan): {media.orphan_names.join(", ")}
            {media.orphans > media.orphan_names.length ? "…" : ""}
          </p>
        ) : null}
        {media.ignored ? (
          <p>
            Archivos que no son imágenes (se ignoran): {media.ignored_names.join(", ")}
            {media.ignored > media.ignored_names.length ? "…" : ""}
          </p>
        ) : null}
      </div>
    </section>
  );
}

/** «★ principal.png + 2»: la principal por su nombre y cuántas más lleva la fila. */
export function rowImagesLabel(row: { data: Record<string, unknown> }): string {
  const images = (row.data?.images as { name: string; primary: boolean }[] | undefined) ?? [];
  if (!images.length) return "";
  const primary = images.find((image) => image.primary);
  const first = primary ?? images[0];
  const rest = images.length - 1;
  return `${primary ? "★ " : ""}${first.name}${rest ? ` + ${rest}` : ""}`;
}
