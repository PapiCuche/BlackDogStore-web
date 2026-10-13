"use client";

/**
 * INVENTORY-UNIFIED-01 — imágenes en lote.
 *
 * Añade imágenes a productos que YA EXISTEN: muchas a la vez o en un ZIP, sin
 * libro de productos. Cada archivo va a su producto por su NOMBRE (el código
 * del producto, y un número para el orden); quien prefiera decirlo en un Excel
 * adjunta el libro «Imágenes».
 *
 * TRES PASOS, COMO TODA CARGA. Adjuntar, ver qué pasaría, y aplicar. Nada se
 * coloca al previsualizar. Reemplazar una galería se elige a propósito y pide
 * escribir una palabra: aquí no hay un borrado que alguien no haya leído.
 *
 * Qué archivo es de qué producto, los límites y las protecciones del ZIP son
 * del servidor. Para el orden fino, el texto alternativo y los retoques queda
 * la galería de cada producto, enlazada desde la vista previa y el resultado.
 */
import Link from "next/link";
import { useEffect, useId, useState } from "react";
import {
  applyImageImport,
  fetchImageImportRules,
  importErrorReportUrl,
  previewImageImport,
  type ImageImportMode,
  type ImageImportRules,
  type ImportJob,
  type ImportRow,
} from "../lib/internal-api";
import { ImportImagesField } from "./ImportWizard";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

const EXAMPLES = ["FUNDA.png", "FUNDA_2.png", "FUNDA-3.jpg", "FUNDA (4).webp"];
const CONFIRM_WORD = "REEMPLAZAR";

type StagedImage = { name: string; address: string | null; primary: boolean; existing: boolean };

const images = (count: number) => (count === 1 ? "1 imagen" : `${count} imágenes`);
const products = (count: number) => (count === 1 ? "1 producto" : `${count} productos`);

function staged(row: ImportRow): StagedImage[] {
  return Array.isArray(row.data.images) ? (row.data.images as StagedImage[]) : [];
}

function GalleryLink({ row }: { row: ImportRow }) {
  return (
    <Link
      href={`/admin/products/${row.data.product_id}`}
      className="text-xs font-semibold text-foreground underline underline-offset-2"
    >
      Ver galería de {String(row.data.name ?? "")}
    </Link>
  );
}

function resultText(applied: Record<string, number>): string {
  const added = applied.images_added ?? 0;
  const removed = applied.images_removed ?? 0;
  const touched = applied.products ?? 0;
  if (removed > 0) {
    const gone = removed === 1 ? "Se quitó 1 imagen" : `Se quitaron ${removed} imágenes`;
    const came = added === 1 ? "se añadió 1 imagen" : `se añadieron ${added} imágenes`;
    return `${gone} y ${came} en ${products(touched)}.`;
  }
  if (added === 0) return "No se añadió ninguna imagen.";
  return `${added === 1 ? "Se añadió 1 imagen" : `Se añadieron ${added} imágenes`} a ${products(touched)}.`;
}

export function BulkImageImport({ companyId }: { companyId: number | null }) {
  const bookId = useId();
  const wordId = useId();
  const [rules, setRules] = useState<ImageImportRules | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [zip, setZip] = useState<File | null>(null);
  const [book, setBook] = useState<File | null>(null);
  const [mode, setMode] = useState<ImageImportMode>("add");
  const [useMain, setUseMain] = useState(false);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState<"preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Los límites son del servidor. Sin ellos la regla de nombres sigue valiendo.
    fetchImageImportRules(companyId)
      .then((found) => { if (!cancelled) setRules(found); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [companyId]);

  function reset() {
    setFiles([]);
    setZip(null);
    setBook(null);
    setMode("add");
    setUseMain(false);
    setJob(null);
    setWord("");
    setError(null);
  }

  const replacing = job?.options?.mode === "replace";

  async function preview() {
    if (busy) return;
    setBusy("preview");
    setError(null);
    setJob(null);
    setWord("");
    try {
      setJob(await previewImageImport(companyId, {
        images: files, imagesZip: zip, book, mode, useMain: mode === "add" && useMain,
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudieron revisar las imágenes.");
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    if (!job || busy) return;
    setBusy("apply");
    setError(null);
    try {
      const done = await applyImageImport(companyId, job.id, replacing ? word : undefined);
      // The answer to apply is the job WITHOUT its rows: only the preview
      // carries them. The result links to each product's gallery, so the rows
      // of the preview are kept.
      setJob({ ...done, rows: done.rows ?? job.rows });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudieron aplicar las imágenes.");
    } finally {
      setBusy(null);
    }
  }

  const limits = rules?.image_rules;
  const rows = job?.rows ?? [];
  const productRows = rows.filter((row) => row.action !== "skip");
  const skipped = rows.filter((row) => row.action === "skip");
  const toAdd = job?.summary.images_to_add ?? 0;
  const toRemove = job?.summary.images_to_remove ?? 0;
  const changing = productRows.filter((row) => row.action === "update").length;
  const applied = job?.status === "applied" ? job.summary.applied ?? {} : null;
  const ignored = job?.summary.media?.ignored_names ?? [];
  const confirmWord = job?.summary.confirm_word || rules?.confirm_word || CONFIRM_WORD;

  return (
    <div className="space-y-5 rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
      <div role="note" aria-label="Cómo nombrar las imágenes" className="rounded-lg border border-bd-border bg-background px-4 py-3 text-sm leading-6">
        <p className="font-semibold text-foreground">
          Nombra cada archivo con el código del producto: <code>{EXAMPLES[0]}</code>
        </p>
        <p className="text-muted">
          Para varias imágenes del mismo producto añade un número al final:{" "}
          {EXAMPLES.slice(1).map((example, index) => (
            <span key={example}>{index ? ", " : ""}<code>{example}</code></span>
          ))}. La de número más bajo —o la que no lleva número— es la principal. El código es su código
          interno o su código de barras, tal cual está escrito, con sus mayúsculas. No se busca por el
          nombre del producto.
        </p>
        {limits ? (
          <p className="mt-2 text-xs text-muted">
            Hasta {limits.max_per_product} imágenes por producto, {limits.max_file_mb} MB cada una y{" "}
            {limits.max_files} archivos por carga. Formatos: {limits.formats.join(", ")}.
          </p>
        ) : null}
      </div>

      {applied ? null : (
        <>
          <ImportImagesField
            images={files}
            zip={zip}
            disabled={busy !== null}
            onChange={(nextImages, nextZip) => { setFiles(nextImages); setZip(nextZip); }}
            title="Imágenes"
            description="Varias a la vez, un ZIP, o las dos cosas. No se sube nada hasta previsualizar."
          />

          <div className="rounded-xl border border-bd-border bg-surface px-4 py-4 text-xs text-muted">
            <label htmlFor={bookId} className="block text-sm font-semibold text-foreground">
              Libro «Imágenes» (opcional)
            </label>
            <p className="mt-1">
              Si los nombres de los archivos no son códigos, dilo en un Excel de una hoja «Imágenes» con las
              columnas Código, Imagen principal e Imágenes (varios nombres separados por <code>|</code>). Con
              el libro, los nombres de los archivos no deciden nada.
            </p>
            <input
              id={bookId}
              type="file"
              accept=".xlsx"
              disabled={busy !== null}
              onChange={(event) => {
                const selected = event.target.files?.[0] ?? null;
                event.target.value = "";
                if (selected) setBook(selected);
              }}
              className="mt-2 block w-full text-sm text-muted file:mr-4 file:rounded-full file:border file:border-bd-border file:bg-background file:px-4 file:py-2 file:text-sm file:font-semibold file:text-foreground"
            />
            {book ? (
              <p className="mt-2 flex items-center justify-between gap-3">
                <span className="break-all text-foreground">{book.name}</span>
                <button type="button" onClick={() => setBook(null)} className="shrink-0 underline underline-offset-4 hover:text-danger">
                  Quitar
                </button>
              </p>
            ) : null}
          </div>

          <fieldset role="radiogroup" aria-label="Qué hacer con la galería" className="grid gap-3 sm:grid-cols-2">
            <label className="flex cursor-pointer gap-3 rounded-lg border border-bd-border bg-background p-3 text-sm">
              <input type="radio" name="image-mode" className="mt-1" checked={mode === "add"} onChange={() => setMode("add")} />
              <span>
                <span className="block font-semibold text-foreground">Añadir a la galería</span>
                <span className="mt-0.5 block text-xs text-muted">
                  Las imágenes se suman a las que el producto ya tiene. No se quita ninguna.
                </span>
              </span>
            </label>
            <label className="flex cursor-pointer gap-3 rounded-lg border border-bd-border bg-background p-3 text-sm">
              <input type="radio" name="image-mode" className="mt-1" checked={mode === "replace"} onChange={() => setMode("replace")} />
              <span>
                <span className="block font-semibold text-foreground">Reemplazar la galería</span>
                <span className="mt-0.5 block text-xs text-muted">
                  Quita las imágenes que el producto tenía y deja sólo las que subes, en su orden. Sólo en los
                  productos de esta carga, y pide confirmación.
                </span>
              </span>
            </label>
          </fieldset>

          {mode === "add" ? (
            <label className="flex items-start gap-2 text-sm text-foreground">
              <input type="checkbox" className="mt-1" checked={useMain} onChange={(event) => setUseMain(event.target.checked)} />
              <span>
                Usar como principal la primera imagen de cada producto
                <span className="block text-xs text-muted">
                  Sin marcar, un producto que ya tiene imagen principal la conserva.
                </span>
              </span>
            </label>
          ) : null}

          <button
            type="button"
            onClick={() => void preview()}
            disabled={busy !== null || (files.length === 0 && !zip)}
            className={internalPrimaryButtonClass}
          >
            {busy === "preview" ? "Subiendo y revisando…" : "Previsualizar"}
          </button>
        </>
      )}

      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}

      {job && !applied ? (
        <div className="space-y-4">
          <p className="text-sm text-foreground">
            {toAdd === 0 && toRemove === 0 && changing === 0 && job.counts.error === 0
              ? "No hay nada que añadir: lo que subiste ya estaba en esas galerías."
              : `${images(toAdd)} para ${products(job.summary.products ?? productRows.length)}. Todavía no se ha cambiado nada.`}
          </p>

          {job.counts.error > 0 ? (
            <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
              {products(job.counts.error)} con error. Corrige los archivos y vuelve a previsualizar: no se aplica
              una carga a medias.{" "}
              <a href={importErrorReportUrl(companyId, job.id)} className="font-semibold underline underline-offset-2">
                Descargar errores
              </a>
            </p>
          ) : null}

          {productRows.length ? (
            <div className="overflow-x-auto rounded-xl border border-bd-border bg-surface">
              <table className="w-full min-w-[720px] text-left text-xs">
                <thead className="bg-surface-2 text-[10px] uppercase tracking-widest text-muted">
                  <tr>
                    <th className="px-3 py-2">Producto</th>
                    <th className="px-3 py-2">Imágenes a añadir</th>
                    <th className="px-3 py-2">Principal</th>
                    <th className="px-3 py-2">Galería</th>
                    <th className="px-3 py-2">Avisos</th>
                  </tr>
                </thead>
                <tbody>
                  {productRows.map((row) => {
                    const fresh = staged(row).filter((image) => !image.existing);
                    const already = staged(row).length - fresh.length;
                    const removed = Number(row.data.will_remove ?? 0);
                    return (
                      <tr key={`${row.row}-${row.match_key}`} className="border-t border-bd-border/70 align-top">
                        <td className="px-3 py-2">
                          <p className="font-semibold text-foreground">{String(row.data.name || "—")}</p>
                          <p className="text-muted">{String(row.data.code || row.data.barcode || row.match_key)}</p>
                          {row.data.product_id ? <GalleryLink row={row} /> : null}
                        </td>
                        <td className="px-3 py-2">
                          <ul className="flex flex-wrap gap-2">
                            {fresh.map((image) => (
                              <li key={image.name} className="w-16 text-center">
                                {image.address ? (
                                  // eslint-disable-next-line @next/next/no-img-element
                                  <img
                                    src={image.address}
                                    alt={image.name}
                                    loading="lazy"
                                    className="h-14 w-14 rounded-md border border-bd-border bg-background object-contain"
                                  />
                                ) : null}
                                <span className="mt-1 block break-all text-[10px] text-muted">{image.name}</span>
                              </li>
                            ))}
                          </ul>
                        </td>
                        <td className="px-3 py-2 text-foreground">
                          {row.data.main ? `Principal: ${String(row.data.main)}` : "La principal no cambia"}
                        </td>
                        <td className="px-3 py-2 text-muted">
                          {already > 0 ? <p>{already === 1 ? "1 ya estaba en la galería" : `${already} ya estaban en la galería`}</p> : null}
                          {removed > 0 ? <p className="font-semibold text-danger">Se quitan {removed}</p> : null}
                          {already === 0 && removed === 0 ? "—" : null}
                        </td>
                        <td className="px-3 py-2">
                          {row.errors.map((text) => <p key={text} className="text-danger">{text}</p>)}
                          {row.warnings.map((text) => <p key={text} className="text-warning">{text}</p>)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}

          {skipped.length ? (
            <section role="region" aria-label="Archivos que no se cargarán" className="rounded-lg border border-bd-border bg-background px-4 py-3 text-xs">
              <p className="font-semibold text-foreground">
                {skipped.length === 1 ? "1 archivo no se cargará" : `${skipped.length} archivos no se cargarán`}
              </p>
              <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-y-auto pl-5 text-muted">
                {skipped.map((row) => <li key={row.match_key}>{row.warnings[0] ?? row.match_key}</li>)}
              </ul>
            </section>
          ) : null}

          {ignored.length ? (
            <p className="text-xs text-muted">No son imágenes y se ignoraron: {ignored.join(", ")}.</p>
          ) : null}

          {job.is_applicable && (toAdd > 0 || toRemove > 0 || changing > 0) ? (
            replacing ? (
              <div className="space-y-3 rounded-lg border border-danger-border bg-danger-surface px-4 py-3">
                <p role="alert" className="text-sm font-semibold text-danger">
                  Se quitarán {images(toRemove)} de {products(changing)} y quedarán las que subiste. No se puede deshacer.
                </p>
                <label htmlFor={wordId} className="block text-xs font-semibold text-foreground">
                  Escribe {confirmWord} para confirmar
                </label>
                <input
                  id={wordId}
                  type="text"
                  autoComplete="off"
                  value={word}
                  onChange={(event) => setWord(event.target.value)}
                  className="w-full max-w-xs rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground"
                />
                <button
                  type="button"
                  onClick={() => void apply()}
                  disabled={busy !== null || word !== confirmWord}
                  className={`${internalPrimaryButtonClass} block`}
                >
                  {busy === "apply" ? "Aplicando…" : "Reemplazar galerías"}
                </button>
              </div>
            ) : (
              <button type="button" onClick={() => void apply()} disabled={busy !== null} className={internalPrimaryButtonClass}>
                {busy === "apply" ? "Aplicando…" : toAdd > 0 ? `Añadir ${images(toAdd)}` : "Aplicar"}
              </button>
            )
          ) : null}
        </div>
      ) : null}

      {job && applied ? (
        <div role="status" className="space-y-3 rounded-lg border border-success-border bg-success-surface px-4 py-3 text-sm">
          <p className="font-semibold text-success">{resultText(applied)}</p>
          <p className="text-xs text-muted">
            El orden fino, el texto alternativo y cualquier retoque se hacen en la galería de cada producto:
          </p>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {productRows.filter((row) => row.action === "update").map((row) => (
              <li key={`${row.row}-${row.match_key}`}><GalleryLink row={row} /></li>
            ))}
          </ul>
          <button type="button" onClick={reset} className={internalButtonClass}>
            Empezar otra carga
          </button>
        </div>
      ) : null}
    </div>
  );
}
