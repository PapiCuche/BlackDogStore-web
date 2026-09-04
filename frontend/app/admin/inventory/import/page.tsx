"use client";

/**
 * Carga masiva de inventario — Commercial Phase C1.4.
 *
 * THIS SCREEN WRITES TO THE KARDEX, AND SAYS SO
 * ---------------------------------------------
 * A product import can be corrected by editing the product. This one cannot:
 * every change becomes a stock movement, and the Kardex is an append-only
 * record of physical fact. Undoing it means issuing compensating movements that
 * are themselves permanent history. So the confirmation step spells that out
 * before the button is pressed.
 *
 * THE RULE THE WHOLE SCREEN IS BUILT AROUND
 * -----------------------------------------
 *     an EMPTY cell  →  DO NOT TOUCH THIS STOCK
 *     an explicit 0  →  SET THIS STOCK TO ZERO
 *
 * They are opposite instructions and they look almost identical in a
 * spreadsheet. The owner's real inventory export has 696 rows and a completely
 * empty quantity column — it is the catalogue printed out, waiting for somebody
 * to walk the shelves. Read blank as zero and one upload writes off the shop.
 *
 * WHICH WAREHOUSE IS WHICH SHOP IS ASKED, NOT GUESSED
 * ---------------------------------------------------
 * The file's column is called `ALMACEN 1 - 11416`. That number is a warehouse id
 * in the system that exported it. Treating it as one of ours would point at
 * whichever branch happened to have that primary key.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import { DashboardSection } from "../../components/dashboard-ui";
import {
  CountsBar,
  HistoryTable,
  Notices,
  PreviewTable,
  STEP_LABELS_STOCK,
  Stepper,
} from "../../components/ImportWizard";
import {
  applyImport,
  fetchImportHistory,
  importErrorReportUrl,
  inspectImportFile,
  inventoryExportUrl,
  previewStockImport,
  type ImportJob,
  type InspectResult,
  type InspectedSheet,
} from "../../lib/internal-api";

const BUTTON =
  "rounded-lg px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40";
const PRIMARY = `${BUTTON} bg-foreground text-background hover:bg-foreground/90`;
const DANGER = `${BUTTON} bg-amber-400 text-background hover:bg-amber-300`;
const GHOST = `${BUTTON} border border-bd-border text-foreground/85 hover:bg-surface-2`;

function text(row: { data: Record<string, unknown> }, key: string) {
  const value = row.data?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function StockImportScreen({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<InspectResult | null>(null);
  const [sheet, setSheet] = useState<InspectedSheet | null>(null);
  const [branchMap, setBranchMap] = useState<Record<string, number>>({});
  const [mode, setMode] = useState("reconcile_target");
  const [job, setJob] = useState<ImportJob | null>(null);
  const [history, setHistory] = useState<ImportJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportBranches, setExportBranches] = useState<number[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const loadHistory = useCallback(() => {
    fetchImportHistory(companyId, "stock").then(setHistory).catch(() => {});
  }, [companyId]);

  useEffect(loadHistory, [loadHistory]);

  function reset() {
    setStep(0);
    setFile(null);
    setInspection(null);
    setSheet(null);
    setBranchMap({});
    setJob(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  async function onFile(selected: File) {
    setBusy(true);
    setError(null);
    try {
      const result = await inspectImportFile(companyId, selected, "stock");
      setFile(selected);
      setInspection(result);
      const preferred =
        result.sheets.find((entry) => entry.detected) ?? result.sheets[0];
      setSheet(preferred ?? null);
      setBranchMap({});
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
      const result = await previewStockImport(companyId, file, {
        branchMap,
        mode,
        sheetName: sheet.name,
        headerRow: sheet.header_row,
        mapping: sheet.profile?.mapping ?? sheet.mapping,
      });
      setJob(result);
      setStep(3);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo previsualizar.");
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

  const branches = inspection?.branches ?? [];
  const warehouses = sheet?.warehouse_columns ?? [];
  const mapped = Object.keys(branchMap).length;

  return (
    <div className="space-y-8">
      <Stepper labels={STEP_LABELS_STOCK} current={step} />

      {error && (
        <p className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      )}

      {step === 0 && (
        <>
          <DashboardSection
            title="1 · Elige el archivo"
            description="Archivos .xlsx de hasta 10 MB. Una celda de cantidad vacía NO se interpreta como cero: esa fila no se toca."
          >
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx"
              disabled={busy}
              onChange={(event) => {
                const selected = event.target.files?.[0];
                if (selected) void onFile(selected);
              }}
              className="block w-full text-sm text-background file:mr-4 file:rounded-lg file:border-0 file:bg-foreground file:px-4 file:py-2 file:text-sm file:font-semibold file:text-background"
            />
          </DashboardSection>

          <DashboardSection
            title="Descargar inventario"
            description="Descarga, cuenta en Excel y vuelve a subir el mismo archivo."
          >
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {branches.length === 0 && (
                  <p className="text-xs text-muted">
                    Elige un archivo primero, o descarga todas las sucursales a las
                    que tienes acceso.
                  </p>
                )}
                {branches.map((branch) => (
                  <label
                    key={branch.id}
                    className="flex items-center gap-2 rounded-lg border border-bd-border px-3 py-1.5 text-xs text-foreground/85"
                  >
                    <input
                      type="checkbox"
                      checked={exportBranches.includes(branch.id)}
                      onChange={(event) =>
                        setExportBranches((current) =>
                          event.target.checked
                            ? [...current, branch.id]
                            : current.filter((id) => id !== branch.id),
                        )
                      }
                    />
                    {branch.name}
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <a
                  className={GHOST}
                  href={inventoryExportUrl(companyId, exportBranches, "current")}
                >
                  Con las cantidades actuales
                </a>
                <a
                  className={GHOST}
                  href={inventoryExportUrl(companyId, exportBranches, "blank")}
                >
                  En blanco, para conteo físico
                </a>
              </div>
              <p className="text-xs text-muted">
                Para un conteo real conviene la hoja en blanco: con las cantidades
                ya escritas es fácil leer «14», ver catorce más o menos y no
                corregir nada.
              </p>
            </div>
          </DashboardSection>
        </>
      )}

      {step === 1 && inspection && sheet && (
        <DashboardSection
          title="2 · Qué sucursal es cada almacén"
          description="El número del encabezado viene del sistema que exportó el archivo y no se interpreta como una sucursal de aquí."
        >
          <div className="space-y-3">
            {warehouses.length === 0 && (
              <p className="text-sm text-warning">
                No se reconoció ninguna columna de almacén en «{sheet.name}».
                Revisa que los encabezados incluyan una columna que empiece por
                ALMACEN, DEPOSITO, SUCURSAL o TIENDA.
              </p>
            )}
            {warehouses.map((column) => (
              <label key={column.index} className="block text-xs">
                <span className="mb-1 block uppercase tracking-widest text-muted">
                  {column.header}
                </span>
                <select
                  value={branchMap[String(column.index)] ?? ""}
                  onChange={(event) => {
                    const next = { ...branchMap };
                    if (event.target.value === "") delete next[String(column.index)];
                    else next[String(column.index)] = Number(event.target.value);
                    setBranchMap(next);
                  }}
                  className="w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground"
                >
                  <option value="">— no importar esta columna —</option>
                  {branches.map((branch) => (
                    <option key={branch.id} value={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <div className="flex gap-2 pt-2">
              <button className={GHOST} type="button" onClick={reset}>
                Cambiar archivo
              </button>
              <button
                className={PRIMARY}
                type="button"
                disabled={mapped === 0}
                onClick={() => setStep(2)}
              >
                Continuar
              </button>
            </div>
          </div>
        </DashboardSection>
      )}

      {step === 2 && (
        <DashboardSection
          title="3 · Qué significan los números del archivo"
          description="En ambos modos, una celda vacía deja el stock como está."
        >
          <div className="space-y-3">
            {(
              [
                [
                  "reconcile_target",
                  "Ajuste a stock objetivo",
                  "El número es lo que HAY en el estante. El sistema calcula la diferencia contra el stock actual y genera la corrección. Es el modo normal.",
                ],
                [
                  "initial",
                  "Carga inicial",
                  "Sólo para productos que todavía no tienen ningún movimiento en esa sucursal. Si ya hay historial de Kardex, esa fila da error.",
                ],
              ] as const
            ).map(([value, label, help]) => (
              <button
                key={value}
                type="button"
                onClick={() => setMode(value)}
                className={
                  "w-full rounded-xl border px-4 py-3 text-left transition " +
                  (mode === value
                    ? "border-bd-border bg-surface-2"
                    : "border-bd-border hover:bg-surface")
                }
              >
                <span className="font-semibold text-foreground">{label}</span>
                <p className="mt-1 text-xs text-muted">{help}</p>
              </button>
            ))}
            <div className="flex gap-2 pt-2">
              <button className={GHOST} type="button" onClick={() => setStep(1)}>
                Atrás
              </button>
              <button
                className={PRIMARY}
                type="button"
                disabled={busy}
                onClick={() => void runPreview()}
              >
                {busy ? "Leyendo…" : "Previsualizar"}
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
          <Notices job={job} />

          <p className="mb-4 rounded-lg border border-bd-border bg-background/30 px-4 py-2 text-xs text-muted">
            Las filas marcadas <strong className="text-warning">Omitir</strong>{" "}
            tienen la celda de cantidad vacía: su stock no cambia. Un cero escrito
            explícitamente sí baja el stock a cero.
          </p>

          {job.counts.error > 0 && (
            <div className="mb-4 rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
              Hay {job.counts.error} fila(s) con error y no se aplicará nada.{" "}
              <a className="underline" href={importErrorReportUrl(companyId, job.id)}>
                Descargar el detalle
              </a>
            </div>
          )}

          <PreviewTable
            rows={job.rows ?? []}
            truncated={job.rows_truncated}
            columns={[
              { key: "name", label: "Producto", get: (r) => text(r, "name") },
              { key: "branch", label: "Sucursal", get: (r) => text(r, "branch_name") },
              {
                key: "current",
                label: "Stock actual",
                get: (r) => text(r, "current_preview"),
              },
              {
                key: "target",
                label: "Stock del archivo",
                get: (r) =>
                  r.data?.quantity_kind === "blank" ? "(vacío)" : text(r, "target"),
              },
              {
                key: "delta",
                label: "Diferencia",
                get: (r) =>
                  r.data?.quantity_kind === "blank" ? "—" : text(r, "delta_preview"),
              },
            ]}
          />

          {step === 4 && (
            <p className="mt-4 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
              Esta operación generará movimientos de Kardex. Quedan registrados de
              forma permanente y sólo se pueden revertir con movimientos
              compensatorios, que también quedan registrados. La diferencia se
              vuelve a calcular en el momento de aplicar, así que una venta hecha
              mientras revisabas esta pantalla no se pierde.
            </p>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            <button className={GHOST} type="button" onClick={reset}>
              Empezar de nuevo
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
                className={DANGER}
                type="button"
                disabled={busy || !job.is_applicable}
                onClick={() => void runApply()}
              >
                {busy
                  ? "Aplicando…"
                  : `Generar movimientos para ${job.counts.update} fila(s)`}
              </button>
            )}
          </div>
        </DashboardSection>
      )}

      {step === 5 && job && (
        <DashboardSection title="6 · Resultado">
          <p className="mb-4 rounded-lg border border-success-border bg-success-surface px-4 py-3 text-sm text-success">
            Inventario aplicado. Se generaron {job.summary?.applied?.movements ?? 0}{" "}
            movimiento(s) de Kardex; {job.summary?.applied?.already_matching ?? 0}{" "}
            fila(s) ya coincidían.
          </p>
          <div className="flex flex-wrap gap-2">
            <Link className={GHOST} href="/admin/inventory/movements">
              Ver el Kardex
            </Link>
            <button className={GHOST} type="button" onClick={reset}>
              Importar otro archivo
            </button>
          </div>
        </DashboardSection>
      )}

      <DashboardSection
        title="Historial de importaciones"
        description="Qué se importó, quién lo hizo, en qué sucursales y qué produjo."
      >
        <HistoryTable jobs={history} />
      </DashboardSection>
    </div>
  );
}

export default function StockImportPage() {
  return (
    <InternalControlGuard>
      {(ctx) => (
        <AdminShell
          user={ctx.user}
          dashboard={ctx.dashboard}
          onSelectCompany={ctx.selectCompany}
        >
          <div className="space-y-6">
            <header>
              <h1 className="text-xl font-semibold text-foreground">
                Carga masiva de inventario
              </h1>
              <p className="text-sm text-muted">
                Una celda vacía deja el stock como está. Un cero escrito lo pone en
                cero.
              </p>
            </header>
            <StockImportScreen ctx={ctx} />
          </div>
        </AdminShell>
      )}
    </InternalControlGuard>
  );
}
