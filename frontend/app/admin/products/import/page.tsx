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
  rowImagesLabel,
} from "../../components/ImportWizard";
import { fetchAdminCategories, type AdminCategory } from "../../../lib/admin";
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
    if (inputRef.current) inputRef.current.value = "";
  }

  async function onFile(selected: File) {
    setBusy(true);
    setError(null);
    try {
      const result = await inspectImportFile(companyId, selected, "products");
      setFile(selected);
      setInspection(result);
      // Never a help sheet of the template («Instrucciones», «Ejemplo»).
      const candidates = result.sheets.filter((entry) => !entry.help);
      const preferred = candidates.find((entry) => entry.detected) ?? candidates[0];
      setSheet(preferred ?? null);
      setMapping(preferred?.profile?.mapping ?? preferred?.mapping ?? {});
      setHeaderRow(preferred?.header_row ?? 1);
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
        options: { mode, create_missing_categories: createCategories, default_category_id: defaultCategory },
        images,
        imagesZip,
      });
      setJob(result);
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
      const result = await applyImport(companyId, job);
      setJob(result);
      setStep(5);
      loadHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo aplicar.");
    } finally {
      setBusy(false);
    }
  }

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
            <a className={GHOST} href={productTemplateUrl(companyId)}>
              Descargar plantilla
            </a>
            <p className="text-xs text-muted">
              La plantilla trae una hoja de instrucciones y un ejemplo completo. Las imágenes no van
              dentro del Excel: se adjuntan en el paso 3.
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
              {Object.entries(inspection.fields).map(([field, meta]) => (
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
                Una celda vacía nunca borra lo que ya está guardado. En «Destino», vacío publica lo nuevo y
                deja lo existente como está.
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

          <PreviewTable
            rows={job.rows ?? []}
            truncated={job.rows_truncated}
            columns={[
              { key: "code", label: "Código", get: (r) => text(r, "code") },
              { key: "barcode", label: "EAN", get: (r) => text(r, "barcode") },
              { key: "name", label: "Nombre", get: (r) => text(r, "name") },
              { key: "category", label: "Categoría", get: (r) => text(r, "category") || text(r, "category_default") },
              { key: "destination", label: "Destino", get: (r) => destinationLabel(r) },
              { key: "price", label: "Precio", get: (r) => text(r, "price") },
              { key: "images", label: "Imágenes", get: (r) => rowImagesLabel(r) },
            ]}
          />

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
                disabled={busy || !job.is_applicable}
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
              title="Carga masiva de productos"
              description="Sube un Excel, revisa lo que va a pasar y sólo entonces aplícalo."
            />
            <ProductImportScreen ctx={ctx} />
          </div>
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}
