"use client";

/**
 * Carga masiva de productos — Commercial Phase C1.4.
 *
 * SIX STEPS, AND THE FOURTH IS THE ONE THAT MATTERS
 * -------------------------------------------------
 * Archivo → Hoja → Columnas → Previsualización → Confirmación → Resultado.
 *
 * The preview is not a courtesy. A file exported from another system will have
 * columns this catalogue has nowhere to put, prices in a format nobody agreed
 * on, and codes that may already belong to a different article — and the person
 * uploading it cannot know which until something shows them. Nothing is written
 * until the fifth step.
 *
 * The owner's own 18-column template is recognised automatically, including the
 * fact that its headers are on row TWO under a banner. That recognition is by
 * the SHAPE of the file, never by which company is looking at it.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import { DashboardSection } from "../../components/dashboard-ui";
import { PageHeader, internalButtonClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import {
  CountsBar,
  DefaultCategoryField,
  HistoryConfirmField,
  HistorySummaryPanel,
  HistoryTable,
  ImageHelp,
  ImportImagesField,
  SheetPicker,
  MediaSummary,
  Notices,
  PreviewTable,
  STEP_LABELS_PRODUCTS,
  Stepper,
  destinationLabel,
  historyClassLabel,
  historyCodesLabel,
  historyOf,
  historyQuantitiesLabel,
  historyText,
  rowImagesLabel,
} from "../../components/ImportWizard";
import { ImportKindNotice } from "../../components/ImportKindNotice";
import { fetchAdminCategories, type AdminCategory } from "../../../lib/admin";
import { API_BASE } from "../../../lib/api";
import { fetchWithAuth } from "../../../lib/auth";
import {
  applyImport,
  fetchImportHistory,
  importErrorReportUrl,
  inspectImportFile,
  previewProductImport,
  productTemplateUrl,
  type ImportJob,
  type InspectResult,
  type InspectedSheet,
} from "../../lib/internal-api";

/** The preset of the historical template (`import_formats.HISTORY_PRESET`). */
const HISTORY_PRESET = "products_history";

/**
 * LABEL-MIGRATION-01 — apply a product load, carrying how many rows to review.
 * The same endpoint as `applyImport`; the server decides whether the number is right.
 */
async function applyProductImport(companyId: number | null, job: ImportJob, confirmReview: string) {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/products/import/${job.id}/apply/${qs}`, {
    method: "POST",
    body: JSON.stringify(confirmReview ? { confirm_review: confirmReview } : {}),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ? String(body.detail) : "No se pudo aplicar la importación.");
  }
  return (await res.json()) as ImportJob;
}

function withQuery(url: string, key: string, value: string) {
  return `${url}${url.includes("?") ? "&" : "?"}${key}=${encodeURIComponent(value)}`;
}

const PRIMARY = internalPrimaryButtonClass;
const GHOST = internalButtonClass;

function text(row: { data: Record<string, unknown> }, key: string) {
  const value = row.data?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function ProductImportScreen({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<InspectResult | null>(null);
  const [sheet, setSheet] = useState<InspectedSheet | null>(null);
  const [mapping, setMapping] = useState<Record<string, number>>({});
  const [headerRow, setHeaderRow] = useState(1);
  const [createCategories, setCreateCategories] = useState(false);
  // PRODUCT-DESTINATION-01: las categorías de ESTA empresa, leídas del
  // servidor, y la elegida para las filas que no traen una.
  const [categories, setCategories] = useState<AdminCategory[] | null>([]);
  // Guardada CON su empresa: al cambiar de empresa la elegida deja de valer
  // sola, porque era una categoría de la otra.
  const [chosenCategory, setChosenCategory] = useState<{ companyId: number | null; id: number } | null>(null);
  const defaultCategory = chosenCategory && chosenCategory.companyId === companyId ? chosenCategory.id : null;
  const setDefaultCategory = (id: number | null) => setChosenCategory(id === null ? null : { companyId, id });
  const [mode, setMode] = useState<"upsert" | "create_only">("upsert");
  // LABEL-MIGRATION-01: «base histórica». Switched on by the historical template's
  // columns; the server decides it again from the file.
  const [historyMode, setHistoryMode] = useState(false);
  const [legacyAsCode, setLegacyAsCode] = useState(false);
  const [confirmReview, setConfirmReview] = useState("");
  // MIGRATION-B: which branch each old-quantity column speaks of. Reference only.
  const [quantityBranches, setQuantityBranches] = useState<Record<string, number>>({});
  const [images, setImages] = useState<File[]>([]);
  const [imagesZip, setImagesZip] = useState<File | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [history, setHistory] = useState<ImportJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const loadHistory = useCallback(() => {
    fetchImportHistory(companyId, "products").then(setHistory).catch(() => {});
  }, [companyId]);

  useEffect(loadHistory, [loadHistory]);

  useEffect(() => {
    let alive = true;
    fetchAdminCategories(companyId)
      .then((rows) => { if (alive) setCategories(rows.filter((row) => row.is_active !== false)); })
      .catch(() => { if (alive) setCategories(null); });
    return () => { alive = false; };
  }, [companyId]);

  function reset() {
    setStep(0);
    setFile(null);
    setInspection(null);
    setSheet(null);
    setMapping({});
    setImages([]);
    setImagesZip(null);
    setJob(null);
    setError(null);
    setHistoryMode(false);
    setLegacyAsCode(false);
    setConfirmReview("");
    setQuantityBranches({});
    if (inputRef.current) inputRef.current.value = "";
  }

  async function onFile(selected: File) {
    setBusy(true);
    setError(null);
    try {
      const result = await inspectImportFile(companyId, selected, "products");
      setFile(selected);
      setInspection(result);
      // Never a help sheet of a previous template («Instrucciones», «Ejemplo»).
      const candidates = result.sheets.filter((entry) => !entry.help);
      const preferred = candidates.find((entry) => entry.detected) ?? candidates[0];
      setSheet(preferred ?? null);
      setMapping(preferred?.profile?.mapping ?? preferred?.mapping ?? {});
      setHeaderRow(preferred?.header_row ?? 1);
      setHistoryMode(preferred?.preset === HISTORY_PRESET);
      setQuantityBranches({});
      setStep(1);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo leer el archivo.");
    } finally {
      setBusy(false);
    }
  }

  async function runPreview() {
    if (!file || !sheet) return;
    setBusy(true);
    setError(null);
    try {
      const result = await previewProductImport(companyId, file, {
        sheetName: sheet.name,
        headerRow,
        mapping,
        options: {
          mode,
          create_missing_categories: createCategories,
          default_category_id: defaultCategory,
          history_mode: historyMode,
          legacy_code_as_barcode: historyMode && legacyAsCode,
          ...(historyMode && Object.keys(quantityBranches).length ? { quantity_branches: quantityBranches } : {}),
        },
        images,
        imagesZip,
      });
      setJob(result);
      setConfirmReview("");
      setStep(3);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "No se pudo previsualizar.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function runApply() {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      const result = historyOf(job)
        ? await applyProductImport(companyId, job, confirmReview.trim())
        : await applyImport(companyId, job);
      setJob(result);
      setStep(5);
      loadHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo aplicar.");
    } finally {
      setBusy(false);
    }
  }

  // MIGRATION-B: the old-quantity columns the server found in the chosen sheet.
  const quantityColumns =
    (sheet as (InspectedSheet & { quantity_columns?: { index: number; header: string }[] }) | null)
      ?.quantity_columns ?? [];

  return (
    <div className="space-y-8">
      <Stepper labels={STEP_LABELS_PRODUCTS} current={step} />

      {error && (
        <p className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      )}

      {step === 0 && (
        <DashboardSection
          title="1 · Elige el archivo"
          description="Se aceptan archivos .xlsx de hasta 10 MB. No se guarda el archivo: sólo su huella y las filas ya interpretadas."
        >
          <div className="space-y-4">
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx"
              disabled={busy}
              onChange={(event) => {
                const selected = event.target.files?.[0];
                if (selected) void onFile(selected);
              }}
              className="block w-full text-sm text-muted file:mr-4 file:rounded-xl file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-semibold file:text-background"
            />
            <p className="text-xs text-muted">
              ¿No tienes un archivo? Descarga la plantilla, complétala y vuelve aquí.
            </p>
            {/* En su propio bloque: como enlace en línea, el texto de abajo se le montaba encima. */}
            <div className="flex flex-wrap gap-2">
              <a className={GHOST} href={productTemplateUrl(companyId)}>
                Descargar plantilla
              </a>
              <a className={GHOST} href={withQuery(productTemplateUrl(companyId), "variant", "historical")}>
                Plantilla de base histórica
              </a>
            </div>
            <p className="text-xs text-muted">
              La base histórica trae los códigos de un sistema anterior: se conservan tal cual, con sus ceros y
              mayúsculas, y nada se aplica sin revisar la vista previa.
            </p>
            <p className="text-xs text-muted">
              Las imágenes no van dentro del Excel: se adjuntan en el paso 3.
            </p>
            <ImageHelp rules={null} />
          </div>
        </DashboardSection>
      )}

      {step === 1 && inspection && (
        <DashboardSection
          title="2 · Elige la hoja"
          description="Un libro puede traer hojas de catálogo (unidades, marcas) que no son productos."
        >
          <div className="space-y-2">
            <SheetPicker
              sheets={inspection.sheets}
              selected={sheet}
              onChoose={(entry) => {
                setSheet(entry);
                setMapping(entry.profile?.mapping ?? entry.mapping ?? {});
                setHeaderRow(entry.header_row);
                setHistoryMode(entry.preset === HISTORY_PRESET);
                setQuantityBranches({});
              }}
            />
            <div className="flex gap-2 pt-2">
              <button className={GHOST} type="button" onClick={reset}>
                Cambiar archivo
              </button>
              <button
                className={PRIMARY}
                type="button"
                disabled={!sheet}
                onClick={() => setStep(2)}
              >
                Continuar
              </button>
            </div>
          </div>
        </DashboardSection>
      )}

      {step === 2 && inspection && sheet && (
        <DashboardSection
          title="3 · Asigna las columnas y adjunta las imágenes"
          description="Sólo se escriben los campos que asignes aquí. El resto del archivo se ignora."
        >
          <div className="space-y-4">
            <label className="block text-xs text-muted">
              Fila de encabezados
              <input
                type="number"
                min={1}
                value={headerRow}
                onChange={(event) => setHeaderRow(Number(event.target.value) || 1)}
                className="ml-2 w-20 rounded-xl border border-bd-border bg-background px-2 py-1 text-sm text-foreground"
              />
            </label>

            <div className="grid gap-3 sm:grid-cols-2">
              {Object.entries(inspection.fields)
                .filter(([, meta]) => historyMode || !(meta as { history?: boolean }).history)
                .map(([field, meta]) => (
                <label key={field} className="block text-xs">
                  <span className="mb-1 block uppercase tracking-widest text-muted">
                    {meta.label}
                    {meta.required && <span className="text-danger"> *</span>}
                  </span>
                  <select
                    value={mapping[field] ?? ""}
                    onChange={(event) => {
                      const next = { ...mapping };
                      if (event.target.value === "") delete next[field];
                      else next[field] = Number(event.target.value);
                      setMapping(next);
                    }}
                    className="w-full rounded-xl border border-bd-border bg-background px-3 py-2 text-sm text-foreground"
                  >
                    <option value="">— no importar —</option>
                    {sheet.headers.map((header, index) => (
                      <option key={`${header}-${index}`} value={index}>
                        {header || `Columna ${index + 1}`}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>

            <div className="space-y-2 rounded-xl border border-bd-border bg-surface px-4 py-3 text-xs text-muted">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={historyMode}
                  onChange={(event) => setHistoryMode(event.target.checked)}
                />
                Base histórica: códigos de un sistema anterior
                <span className="text-muted">
                  — revisión estricta de códigos; un nombre distinto no renombra el producto
                </span>
              </label>
              {historyMode && (
                <label className="flex items-center gap-2 pl-6">
                  <input
                    type="checkbox"
                    checked={legacyAsCode}
                    onChange={(event) => setLegacyAsCode(event.target.checked)}
                  />
                  El SKU antiguo también se escanea
                  <span className="text-muted">— si lo dejas apagado, queda sólo en el informe de conciliación</span>
                </label>
              )}
              {historyMode && quantityColumns.length > 0 && (
                <fieldset className="space-y-2 pl-6">
                  <legend className="text-muted">
                    Cantidades anteriores: sólo referencia para el recuento posterior, nunca se aplican al stock. Elige
                    de qué sucursal es cada columna; sin sucursal queda sólo en el informe.
                  </legend>
                  {quantityColumns.map((column) => (
                    <label key={column.index} className="flex flex-wrap items-center gap-2">
                      <span className="text-foreground">{column.header}</span>
                      <select
                        value={quantityBranches[String(column.index)] ?? ""}
                        onChange={(event) => {
                          const next = { ...quantityBranches };
                          if (event.target.value === "") delete next[String(column.index)];
                          else next[String(column.index)] = Number(event.target.value);
                          setQuantityBranches(next);
                        }}
                        className="rounded-xl border border-bd-border bg-background px-2 py-1 text-sm text-foreground"
                      >
                        <option value="">— sólo informe —</option>
                        {inspection.branches.map((branch) => (
                          <option key={branch.id} value={branch.id}>
                            {branch.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </fieldset>
              )}
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={mode === "create_only"}
                  onChange={(event) =>
                    setMode(event.target.checked ? "create_only" : "upsert")
                  }
                />
                Sólo crear productos nuevos (no tocar los que ya existen)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={createCategories}
                  onChange={(event) => setCreateCategories(event.target.checked)}
                />
                Crear las categorías que falten
                <span className="text-muted">
                  — si lo dejas apagado, una categoría desconocida es un error de fila
                </span>
              </label>
              <DefaultCategoryField categories={categories} value={defaultCategory} onChange={setDefaultCategory} />
              <p className="text-muted">
                Una celda vacía nunca borra lo que ya está guardado. En «Destino», vacío deja lo nuevo como
                «Solo stock interno» y lo existente como está: un producto sólo se publica si su fila lo dice.
              </p>
            </div>

            <ImageHelp rules={inspection.image_rules ?? null} />

            <ImportImagesField
              images={images}
              zip={imagesZip}
              disabled={busy}
              onChange={(nextImages, nextZip) => {
                setImages(nextImages);
                setImagesZip(nextZip);
              }}
            />

            <div className="flex gap-2">
              <button className={GHOST} type="button" onClick={() => setStep(1)}>
                Atrás
              </button>
              <button
                className={PRIMARY}
                type="button"
                disabled={busy || mapping.name === undefined}
                onClick={() => void runPreview()}
              >
                {busy
                  ? images.length || imagesZip ? "Enviando imágenes y leyendo…" : "Leyendo…"
                  : "Previsualizar"}
              </button>
            </div>
          </div>
        </DashboardSection>
      )}

      {(step === 3 || step === 4) && job && (
        <DashboardSection
          title={step === 3 ? "4 · Previsualización" : "5 · Confirmación"}
          description={
            job.summary?.detected
              ? `Formato detectado: ${job.summary.detected}`
              : undefined
          }
        >
          <CountsBar job={job} />
          <HistorySummaryPanel job={job} />
          <MediaSummary job={job} />
          <Notices job={job} />

          {job.counts.error > 0 && (
            <div className="mb-4 rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
              Hay {job.counts.error} fila(s) con error. No se aplica una
              importación a medias: corrige el archivo o adjunta las imágenes que
              falten, y vuelve a previsualizar.{" "}
              <a
                className="underline"
                href={importErrorReportUrl(companyId, job.id)}
              >
                Descargar el detalle
              </a>
            </div>
          )}

          {historyOf(job) && (
            <p className="mb-4 text-xs text-muted">
              <a className="underline" href={withQuery(importErrorReportUrl(companyId, job.id), "kind", "history")}>
                Descargar el informe de conciliación
              </a>{" "}
              (id externo, SKU antiguo, marca, variante, cantidades anteriores y qué pasó con cada fila).
            </p>
          )}

          <PreviewTable
            rows={job.rows ?? []}
            truncated={job.rows_truncated}
            columns={historyOf(job) ? [
              { key: "class", label: "Clasificación", get: (r) => historyClassLabel(r) },
              { key: "codes", label: "Códigos", get: (r) => historyCodesLabel(r) },
              { key: "legacy", label: "SKU antiguo", get: (r) => historyText(r, "legacy_code") },
              { key: "external", label: "Id externo", get: (r) => historyText(r, "external_id") },
              { key: "name", label: "Nombre", get: (r) => text(r, "name") },
              { key: "category", label: "Categoría", get: (r) => text(r, "category") || text(r, "category_default") },
              { key: "price", label: "Precio", get: (r) => text(r, "price") },
              { key: "quantities", label: "Cant. anterior (ref.)", get: (r) => historyQuantitiesLabel(r) },
            ] : [
              { key: "code", label: "Código", get: (r) => text(r, "code") },
              { key: "barcode", label: "EAN", get: (r) => text(r, "barcode") },
              { key: "name", label: "Nombre", get: (r) => text(r, "name") },
              { key: "category", label: "Categoría", get: (r) => text(r, "category") || text(r, "category_default") },
              { key: "destination", label: "Destino", get: (r) => destinationLabel(r) },
              { key: "price", label: "Precio", get: (r) => text(r, "price") },
              { key: "images", label: "Imágenes", get: (r) => rowImagesLabel(r) },
            ]}
          />

          {step === 4 && (
            <HistoryConfirmField
              count={historyOf(job)?.review ?? 0}
              value={confirmReview}
              onChange={setConfirmReview}
            />
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <button className={GHOST} type="button" onClick={reset}>
              Empezar de nuevo
            </button>
            {/* Vuelve a las columnas CON el archivo y las imágenes ya elegidas:
                corregir un error no obliga a adjuntarlo todo otra vez. */}
            <button
              className={GHOST}
              type="button"
              disabled={busy}
              onClick={() => { setJob(null); setStep(2); }}
            >
              Volver a columnas e imágenes
            </button>
            {step === 3 ? (
              <button
                className={PRIMARY}
                type="button"
                disabled={!job.is_applicable}
                onClick={() => setStep(4)}
              >
                Continuar
              </button>
            ) : (
              <button
                className={PRIMARY}
                type="button"
                disabled={
                  busy || !job.is_applicable
                  || ((historyOf(job)?.review ?? 0) > 0 && confirmReview.trim() !== String(historyOf(job)?.review))
                }
                onClick={() => void runApply()}
              >
                {busy ? "Aplicando…" : `Aplicar a ${job.counts.create + job.counts.update} producto(s)`}
              </button>
            )}
          </div>
        </DashboardSection>
      )}

      {step === 5 && job && (
        <DashboardSection title="6 · Resultado">
          <p className="mb-4 rounded-lg border border-success-border bg-success-surface px-4 py-3 text-sm text-success">
            Importación aplicada. Se crearon {job.summary?.applied?.created ?? 0} y
            se actualizaron {job.summary?.applied?.updated ?? 0} producto(s)
            {job.summary?.applied?.images_added
              ? `, con ${job.summary.applied.images_added} imagen(es) en sus galerías`
              : ""}.
            {job.summary?.applied?.publication_refused
              ? ` ${job.summary.applied.publication_refused} producto(s) pedían publicarse y quedaron como «Solo stock interno» porque no cumplen lo necesario.`
              : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            <Link className={GHOST} href="/admin/products">
              Ver el catálogo
            </Link>
            <button className={GHOST} type="button" onClick={reset}>
              Importar otro archivo
            </button>
          </div>
        </DashboardSection>
      )}

      <DashboardSection
        title="Historial de importaciones"
        description="Qué se importó, quién lo hizo y qué produjo."
      >
        <HistoryTable jobs={history} />
      </DashboardSection>
    </div>
  );
}

export default function ProductImportPage() {
  return (
    <InternalControlGuard>
      {(ctx) => (
        <AdminShell
          user={ctx.user}
          dashboard={ctx.dashboard}
          onSelectCompany={ctx.selectCompany}
        >
          <div className="space-y-6">
            <PageHeader
              eyebrow="Catálogo"
              title="Carga de productos y accesorios"
              description="Sube un Excel, revisa lo que va a pasar y sólo entonces aplícalo."
            />
            <ImportKindNotice kind="products" />
            <ProductImportScreen ctx={ctx} />
          </div>
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}
