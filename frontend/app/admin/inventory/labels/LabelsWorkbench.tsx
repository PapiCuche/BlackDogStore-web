"use client";

/**
 * LABEL-03 — elegir productos y equipos de UNA sucursal, ver cómo saldrán sus
 * etiquetas y descargar el PDF a escala real.
 *
 * Un accesorio lleva tantas copias de su etiqueta como se pidan; un equipo,
 * una etiqueta. Lo que no cabe o le falta un dato sale «Para revisar» con su
 * motivo y no entra en el PDF. Nada de esto imprime ni marca nada como impreso.
 *
 * Otra sucursal es otra selección: la página lo monta con `key={branch}`, así lo
 * elegido en una sucursal no se arrastra a otra.
 */

import { useCallback, useState } from "react";

import { ErrorBox, Panel } from "../../components/InventoryUi";
import { fetchBranchStock, type BranchStockRow } from "../../../lib/inventory";
import { fetchSerializedProducts, fetchStockUnits, type StockUnit } from "../../../lib/stock-units";
import {
  LabelsNotReady, MAX_COPIES, fetchLabelsPdf, previewLabels, type LabelPreview, type LabelSelection,
} from "./labels-api";

type PickedProduct = { id: number; name: string; copies: number; quantity: number };
type PickedUnit = { id: number; name: string; serial: string };

const BUTTON =
  "rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-40";
const SECONDARY =
  "rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 disabled:opacity-40";
const INPUT = "w-full rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground";

function clampCopies(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_COPIES, Math.max(1, Math.trunc(value)));
}

export function LabelsWorkbench({ branch }: { branch: number }) {
  const [search, setSearch] = useState("");
  const [stock, setStock] = useState<BranchStockRow[]>([]);
  const [units, setUnits] = useState<StockUnit[]>([]);
  const [products, setProducts] = useState<PickedProduct[]>([]);
  const [picked, setPicked] = useState<PickedUnit[]>([]);
  const [preview, setPreview] = useState<LabelPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const find = useCallback(async () => {
    setError(null);
    try {
      const [stockPage, unitPage, serialized] = await Promise.all([
        fetchBranchStock({ branch, search: search.trim(), status: "in_stock", page_size: 20 }),
        fetchStockUnits({ branch, search: search.trim(), status: "available" }),
        fetchSerializedProducts(),
      ]);
      // Un producto con número de serie se etiqueta equipo por equipo, no por cantidad.
      const byUnit = new Set(serialized.results.map((p) => p.id));
      setStock(stockPage.results.filter((row) => !byUnit.has(row.product)));
      setUnits(unitPage.results.slice(0, 20));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo buscar.");
    }
  }, [branch, search]);

  const selection = (): LabelSelection => ({
    branch,
    products: products.map((p) => ({ id: p.id, copies: p.copies })),
    units: picked.map((u) => u.id),
  });
  const empty = products.length === 0 && picked.length === 0;

  const changed = () => {
    setPreview(null);
    setDone(null);
  };

  const addProduct = (row: BranchStockRow) => {
    if (products.some((p) => p.id === row.product)) return;
    setProducts((list) => [
      ...list, { id: row.product, name: row.product_name, copies: 1, quantity: row.quantity },
    ]);
    changed();
  };

  const addUnit = (unit: StockUnit) => {
    if (picked.some((u) => u.id === unit.id)) return;
    setPicked((list) => [...list, { id: unit.id, name: unit.product_name, serial: unit.serial_number }]);
    changed();
  };

  const runPreview = async () => {
    setBusy(true);
    setError(null);
    try {
      setPreview(await previewLabels(selection()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo preparar la vista previa.");
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const pdf = await fetchLabelsPdf(selection());
      const url = URL.createObjectURL(pdf.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "etiquetas.pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setDone(
        `PDF descargado: ${pdf.pages} etiqueta${pdf.pages === 1 ? "" : "s"}` +
          (pdf.review ? `; ${pdf.review} quedan para revisar y no van en el PDF.` : "."),
      );
    } catch (err) {
      // Todo «para revisar»: se muestra el motivo de cada etiqueta, no sólo el aviso.
      if (err instanceof LabelsNotReady) setPreview(err.preview);
      setError(err instanceof Error ? err.message : "No se pudo generar el PDF.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <Panel title="Buscar" description="Accesorios con existencias y equipos disponibles de esta sucursal.">
        <form
          className="flex flex-wrap items-end gap-3 p-5"
          onSubmit={(event) => {
            event.preventDefault();
            void find();
          }}
        >
          <label className="block min-w-48 flex-1 text-xs text-muted">
            Nombre, serie o IMEI
            <input className={`mt-1 ${INPUT}`} value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <button type="submit" className={BUTTON}>Buscar</button>
        </form>
        {stock.length > 0 || units.length > 0 ? (
          <div className="grid gap-4 border-t border-bd-border p-5 md:grid-cols-2">
            <ul aria-label="Accesorios encontrados" className="min-w-0 space-y-2">
              {stock.map((row) => (
                <li key={row.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 break-words">
                    {row.product_name} <span className="text-xs text-muted">· {row.quantity} u.</span>
                  </span>
                  <button type="button" className={SECONDARY} onClick={() => addProduct(row)}
                    aria-label={`Agregar ${row.product_name}`}>
                    Agregar
                  </button>
                </li>
              ))}
            </ul>
            <ul aria-label="Equipos encontrados" className="min-w-0 space-y-2">
              {units.map((unit) => (
                <li key={unit.id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 break-words">
                    {unit.product_name} <span className="font-mono text-xs text-muted">{unit.serial_number}</span>
                  </span>
                  <button type="button" className={SECONDARY} onClick={() => addUnit(unit)}
                    aria-label={`Agregar equipo ${unit.serial_number}`}>
                    Agregar
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Panel>

      <Panel
        title="Selección"
        description="Un accesorio: tantas copias como pidas. Un equipo: una etiqueta."
        action={
          <div className="flex flex-wrap gap-2">
            <button type="button" className={SECONDARY} disabled={empty || busy} onClick={() => void runPreview()}>
              Vista previa
            </button>
            <button type="button" className={BUTTON} disabled={empty || busy} onClick={() => void download()}>
              Descargar PDF
            </button>
          </div>
        }
      >
        <div className="space-y-3 p-5">
          {empty ? <p className="text-sm text-muted">Busca y agrega productos o equipos.</p> : null}
          {products.map((p) => (
            <div key={`p${p.id}`} className="flex flex-wrap items-center gap-3 text-sm">
              <span className="min-w-0 flex-1 break-words">{p.name}</span>
              <label className="text-xs text-muted">
                Copias
                <input
                  type="number" min={1} max={MAX_COPIES} value={p.copies}
                  aria-label={`Copias de ${p.name}`}
                  className="ml-2 w-20 rounded-lg border border-bd-border bg-background px-2 py-1 text-sm text-foreground"
                  onChange={(e) => {
                    const copies = clampCopies(Number(e.target.value));
                    setProducts((list) => list.map((x) => (x.id === p.id ? { ...x, copies } : x)));
                    changed();
                  }}
                />
              </label>
              <button type="button" className={SECONDARY} aria-label={`Quitar ${p.name}`}
                onClick={() => { setProducts((list) => list.filter((x) => x.id !== p.id)); changed(); }}>
                Quitar
              </button>
            </div>
          ))}
          {picked.map((u) => (
            <div key={`u${u.id}`} className="flex flex-wrap items-center gap-3 text-sm">
              <span className="min-w-0 flex-1 break-words">
                {u.name} <span className="font-mono text-xs text-muted">{u.serial}</span>
              </span>
              <button type="button" className={SECONDARY} aria-label={`Quitar equipo ${u.serial}`}
                onClick={() => { setPicked((list) => list.filter((x) => x.id !== u.id)); changed(); }}>
                Quitar
              </button>
            </div>
          ))}
          <p className="text-xs text-muted">
            El PDF tiene el tamaño de la etiqueta: imprímelo a tamaño real (100 %). Descargarlo no marca nada
            como impreso.
          </p>
        </div>
      </Panel>

      {error ? <ErrorBox message={error} /> : null}
      {done ? (
        <p role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">{done}</p>
      ) : null}

      {preview ? (
        <Panel
          title="Vista previa"
          description={`${preview.summary.ready} lista(s) · ${preview.summary.review} para revisar · ${preview.summary.pages} etiqueta(s) en el PDF`}
        >
          <ul className="divide-y divide-bd-border">
            {preview.labels.map((row) => (
              <li key={`${row.kind}${row.ref}`} className="space-y-1 px-5 py-4 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 break-words font-semibold text-foreground">{row.title}</span>
                  <span
                    className={
                      row.status === "ready"
                        ? "rounded-full border border-success-border px-2 py-0.5 text-xs text-success"
                        : "rounded-full border border-danger-border px-2 py-0.5 text-xs text-danger"
                    }
                  >
                    {row.status === "ready" ? "Lista" : "Para revisar"}
                  </span>
                </div>
                <p className="text-xs text-muted">
                  {row.layout.name}
                  {row.kind === "accessory" ? ` · ${row.copies} copia(s)` : " · 1 etiqueta"}
                  {row.code ? ` · ${row.code.value} (${row.code.width_mm} mm)` : ""}
                </p>
                {row.lines.length > 0 ? (
                  <p className="break-words text-xs text-foreground">{row.lines.join(" | ")}</p>
                ) : null}
                {row.reasons.map((reason) => (
                  <p key={reason} className="text-xs text-danger">{reason}</p>
                ))}
                {row.warnings.map((warning) => (
                  <p key={warning} className="text-xs text-muted">{warning}</p>
                ))}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
