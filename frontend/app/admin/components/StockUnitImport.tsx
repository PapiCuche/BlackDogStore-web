"use client";

/**
 * UNIT-IMPORT · cargar muchos equipos con serie desde un Excel.
 *
 * UNA FILA = UN EQUIPO FÍSICO. La pantalla lo dice antes de adjuntar nada,
 * porque el error de siempre es escribir «iPhone 16, cantidad 2»: eso no dice
 * qué dos teléfonos son.
 *
 * Dos pasos, siempre: previsualizar (no registra nada) y registrar. Aquí no se
 * valida un IMEI ni se cuenta un equipo: se enseña lo que respondió el
 * servidor, fila por fila, y entra todo el archivo o no entra nada.
 */

import Link from "next/link";
import { useRef, useState } from "react";

import {
  applyUnitImport, previewUnitImport, unitImportErrorsUrl, unitImportReportUrl, unitImportTemplateUrl,
  voidUnitImport, UnitImportVoidError,
  type UnitImportBlocking, type UnitImportJob, type UnitImportRow, type UnitImportSimulation,
} from "@/app/lib/stock-units";

import { CostColumnNotice } from "./CostColumnNotice";
import { ImportKindNotice } from "./ImportKindNotice";
import { OutdatedPreviewNotice, outdatedPreview, type OutdatedPreview } from "./ImportWizard";
import { Panel, TableWrap, Td, Th } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const CONDITIONS: Record<string, string> = {
  new: "Nuevo", open_box: "Caja abierta", refurbished: "Reacondicionado", used: "Usado",
};

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

function Outcome({ row }: { row: UnitImportRow }) {
  if (row.errors.length) {
    return (
      <ul className="space-y-0.5 text-danger">
        {row.errors.map((error) => <li key={error}>{error}</li>)}
      </ul>
    );
  }
  if (row.action === "skip") {
    return <span className="text-muted">{row.warnings[0] ?? "No se carga."}</span>;
  }
  return (
    <span>
      Se registrará
      {row.warnings.length ? <span className="block text-xs text-muted">{row.warnings.join(" · ")}</span> : null}
    </span>
  );
}

/**
 * INV-LOAD-01 — what registering the file would do, as the SERVER simulated it from the shop as it is now: per branch ×
 * product, the available devices before → after, and how many Kardex lines of each type it writes.
 */
function Simulation({ simulation }: { simulation: UnitImportSimulation }) {
  const { movements } = simulation;
  return (
    <section className="mb-4 space-y-3" aria-labelledby="unit-import-simulation">
      <h3 id="unit-import-simulation" className="text-sm font-semibold text-foreground">Resultado si registras el archivo</h3>
      <p role="note" aria-label="Movimientos en el Kardex" className="text-sm text-muted">
        Movimientos en el Kardex: {movements.initial_stock} de inventario inicial · {movements.purchase_entry} de compra.
      </p>
      <TableWrap>
        <caption className="sr-only">Resultado si registras el archivo</caption>
        <thead>
          <tr className="border-b border-bd-border">
            <Th>Sucursal</Th><Th>Producto</Th><Th>Entran</Th><Th>Equipos disponibles</Th><Th>Stock</Th>
          </tr>
        </thead>
        <tbody>
          {simulation.shelves.map((shelf) => (
            <tr key={`${shelf.branch_id}-${shelf.product_id}`} className="border-b border-bd-border">
              <Td>{shelf.branch}</Td>
              <Td>{shelf.product}</Td>
              <Td>{shelf.adds}</Td>
              <Td>{shelf.units_before} → {shelf.units_after}</Td>
              <Td>{shelf.stock_before} → {shelf.stock_after}</Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </section>
  );
}

const LINE_LABELS: Record<string, string> = { new: "equipos nuevos", used: "seminuevos" };

/**
 * INV-LOAD-02 — annul an applied load WHOLE. The server voids every device the load created, or none: while one of
 * them has done anything since it entered, the answer names it and nothing changes. The reason is mandatory.
 */
export function AnnulLoad({ job, onAnnulled }: { job: UnitImportJob; onAnnulled: (job: UnitImportJob) => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocking, setBlocking] = useState<UnitImportBlocking[]>([]);
  const voided = job.summary.voided;

  if (voided) {
    return (
      <p role="status" className="text-sm text-foreground">
        Esta carga se anuló: {plural(voided.units, "equipo salió", "equipos salieron")} del stock con una línea de
        corrección en el Kardex. Motivo: {voided.reason}
      </p>
    );
  }

  async function annul() {
    setWorking(true);
    setError(null);
    setBlocking([]);
    try {
      onAnnulled(await voidUnitImport(job.id, reason.trim()));
      setOpen(false);
    } catch (e: unknown) {
      if (e instanceof UnitImportVoidError) setBlocking(e.blocking);
      setError(e instanceof Error ? e.message : "No se pudo anular la carga.");
    } finally {
      setWorking(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className={internalButtonClass} onClick={() => setOpen(true)}>
        Anular esta carga
      </button>
    );
  }
  return (
    <section aria-labelledby="unit-import-annul" className="space-y-3 rounded-lg border border-bd-border px-4 py-3">
      <h3 id="unit-import-annul" className="text-sm font-semibold text-foreground">Anular esta carga</h3>
      <p className="text-sm text-muted">
        Cada equipo que entró con este archivo sale del stock con una línea de corrección en el Kardex (nada se
        borra). Si alguno ya se vendió, apartó, trasladó o cambió de variante, no se anula ninguno.
      </p>
      <label className="block text-xs font-semibold text-muted">
        Motivo de la anulación
        <textarea
          className={`mt-1 ${internalInputClass}`}
          value={reason}
          maxLength={300}
          rows={2}
          disabled={working}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {blocking.length ? (
        <ul aria-label="Equipos que impiden anular" className="list-disc space-y-0.5 pl-5 text-sm text-danger">
          {blocking.map((unit) => (
            <li key={unit.unit_id}>
              Equipo {unit.label_code || `#${unit.unit_id}`}: {unit.reason}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          className={internalPrimaryButtonClass}
          disabled={working || !reason.trim()}
          onClick={annul}
        >
          Anular {plural(job.summary.applied?.units ?? 0, "equipo", "equipos")}
        </button>
        <button type="button" className={internalButtonClass} disabled={working} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </section>
  );
}

export function StockUnitImport({ branches, canSeeCost = false }: {
  branches: { id: number; name: string }[];
  /** `costs.view`: without it the screen says, before anything is attached, that the «Costo» column stays empty. */
  canSeeCost?: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [branch, setBranch] = useState("");
  const [reason, setReason] = useState("");
  const [entryKind, setEntryKind] = useState<"purchase" | "opening">("purchase");
  const [job, setJob] = useState<UnitImportJob | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // IMPORT-STALE-01: the apply was refused because the preview is old or the shop moved since.
  const [outdated, setOutdated] = useState<OutdatedPreview | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function run(task: () => Promise<UnitImportJob>) {
    setWorking(true);
    setError(null);
    setOutdated(null);
    try {
      setJob(await task());
    } catch (e: unknown) {
      const refused = outdatedPreview(e);
      if (refused) {
        // Nothing was registered, and this preview can never be applied now.
        setOutdated(refused);
        setJob((current) => (current ? { ...current, is_applicable: false } : current));
        return;
      }
      setError(e instanceof Error ? e.message : "No se pudo completar la operación.");
    } finally {
      setWorking(false);
    }
  }

  function choose(next: File | null) {
    setFile(next);
    setJob(null);
    setError(null);
    setOutdated(null);
  }

  function uploadAgain() {
    choose(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  const applied = job?.status === "applied";
  const registered = job?.summary.applied?.units ?? 0;
  const rows = job?.rows ?? [];
  const unmarked = job?.summary.imei_on_unmarked_products ?? 0;

  return (
    <div className="space-y-6">
      <Panel
        title="Cargar equipos desde Excel"
        description="Para registrar de una vez los equipos de una compra o del inventario inicial."
        action={
          <div className="flex flex-wrap gap-2">
            {/* INV-LOAD-02 — one template per line; the combined one stays for the files people already have. */}
            <a className={internalButtonClass} href={unitImportTemplateUrl("new")}>Stock de equipos nuevos</a>
            <a className={internalButtonClass} href={unitImportTemplateUrl("used")}>Stock de seminuevos</a>
            <a className={internalButtonClass} href={unitImportTemplateUrl()}>Descargar plantilla</a>
          </div>
        }
      >
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Para cargar el stock real usa «Stock de equipos nuevos» o «Stock de seminuevos»: cada una carga sólo su
            línea. «Descargar plantilla» es la plantilla combinada de siempre.
          </p>
          <ImportKindNotice kind="units" />
          {canSeeCost ? null : <CostColumnNotice column="Costo" />}

          <div className="grid gap-3 md:grid-cols-3">
            <label className="block text-xs font-semibold text-muted">
              Archivo de equipos (.xlsx)
              <input
                ref={inputRef}
                type="file"
                accept=".xlsx"
                className={`mt-1 ${internalInputClass}`}
                disabled={working}
                onChange={(e) => choose(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="block text-xs font-semibold text-muted">
              Sucursal para las filas sin sucursal
              <select
                className={`mt-1 ${internalInputClass}`}
                value={branch}
                disabled={working}
                onChange={(e) => { setBranch(e.target.value); setJob(null); }}
              >
                <option value="">Ninguna: cada fila la indica</option>
                {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </label>
            <fieldset className="md:col-span-2">
              {/* INV-OPENING-01 — what these devices ARE in the Kardex. */}
              <legend className="block text-xs font-semibold text-muted">Tipo de ingreso</legend>
              <div className="mt-1 flex flex-wrap gap-4 text-sm text-foreground">
                <label className="flex items-center gap-2">
                  <input type="radio" name="unit-import-entry-kind" value="purchase" disabled={working}
                    checked={entryKind === "purchase"} onChange={() => { setEntryKind("purchase"); setJob(null); }} />
                  Compra a proveedor
                </label>
                <label className="flex items-center gap-2">
                  <input type="radio" name="unit-import-entry-kind" value="opening" disabled={working}
                    checked={entryKind === "opening"} onChange={() => { setEntryKind("opening"); setJob(null); }} />
                  Inventario inicial (equipos que ya tenías; se puede cargar en varias tandas mientras el artículo no tenga otros movimientos en esa sucursal)
                </label>
              </div>
            </fieldset>
            <label className="block text-xs font-semibold text-muted">
              Motivo para las filas sin motivo
              <input
                className={`mt-1 ${internalInputClass}`}
                value={reason}
                maxLength={300}
                placeholder="Compra F001-204, inventario inicial…"
                disabled={working}
                onChange={(e) => { setReason(e.target.value); setJob(null); }}
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className={internalPrimaryButtonClass}
              disabled={!file || working || applied}
              onClick={() => file && run(() => previewUnitImport(file, { branch: branch ? Number(branch) : null, reason, entryKind }))}
            >
              Previsualizar
            </button>
            <span className="text-xs text-muted">Previsualizar no registra nada.</span>
          </div>
        </div>
      </Panel>

      {error ? (
        <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {outdated ? <OutdatedPreviewNotice outdated={outdated} onUploadAgain={uploadAgain} /> : null}

      {applied && job ? (
        <div className="space-y-3">
          <div role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">
            Se registraron {plural(registered, "equipo", "equipos")} y quedaron en el Kardex.{" "}
            <Link className="font-semibold underline" href="/admin/inventory/units">Ver los equipos</Link>
          </div>
          {job.options?.line === "used" ? (
            <p role="note" aria-label="Después de cargar seminuevos" className="text-sm text-muted">
              Los seminuevos entran pendientes de inspección y sin publicar. Inspecciónalos, súbeles fotos y
              publícalos desde su ficha en{" "}
              <Link className="underline" href="/admin/inventory/used">Seminuevos</Link>.
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <a className={internalButtonClass} href={unitImportReportUrl(job.id, "csv")}>Antes y después (CSV)</a>
            <a className={internalButtonClass} href={unitImportReportUrl(job.id, "xlsx")}>Antes y después (Excel)</a>
          </div>
          <AnnulLoad job={job} onAnnulled={setJob} />
        </div>
      ) : null}

      {job && !applied ? (
        <Panel
          title="Lo que pasará con cada fila"
          description={`${job.original_filename}${job.options?.line ? ` · plantilla de ${LINE_LABELS[job.options.line]}` : ""} · ${plural(job.counts.create, "equipo por registrar", "equipos por registrar")} · ${job.counts.error} con error · ${plural(job.counts.skip, "fila omitida", "filas omitidas")}`}
          action={
            job.counts.error ? (
              <a className={internalButtonClass} href={unitImportErrorsUrl(job.id)}>Descargar errores (CSV)</a>
            ) : (job.is_applicable || outdated) && job.counts.create > 0 ? (
              <button
                type="button"
                className={internalPrimaryButtonClass}
                disabled={working || Boolean(outdated)}
                onClick={() => run(() => applyUnitImport(job.id))}
              >
                Registrar {plural(job.counts.create, "equipo", "equipos")}
              </button>
            ) : null
          }
        >
          {job.counts.error ? (
            <p className="mb-3 text-sm text-danger">
              Corrige las filas con error en el archivo y vuelve a subirlo. Mientras haya una, no se
              registra ningún equipo.
            </p>
          ) : null}
          {!job.counts.error && job.counts.create > 0 && job.summary.simulation ? (
            <Simulation simulation={job.summary.simulation} />
          ) : null}
          {unmarked > 0 ? (
            <p
              role="note"
              aria-label="IMEI en productos sin marca"
              className="mb-3 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning"
            >
              {plural(unmarked, "equipo trae IMEI", "equipos traen IMEI")} y su producto no está marcado
              «lleva IMEI». El IMEI se guardará, pero no se exigirá al vender. Si alguna fila es de otro
              producto, corrígela antes de registrar; cada una lleva su aviso.
            </p>
          ) : null}
          <TableWrap>
            <caption className="sr-only">Equipos del archivo</caption>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Fila</Th><Th>Producto</Th><Th>Sucursal</Th><Th>Número de serie</Th>
                <Th>IMEI</Th><Th>IMEI 2</Th><Th>Condición</Th><Th>Proveedor</Th><Th>Fecha de ingreso</Th>
                <Th>Resultado</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.sheet}-${row.row}`} className="border-b border-bd-border align-top">
                  <Td muted>{row.row}</Td>
                  <Td>{row.data.name ?? ""}</Td>
                  <Td>{row.data.branch ?? ""}</Td>
                  <Td><span className="font-mono text-xs">{row.data.serial_number ?? ""}</span></Td>
                  <Td><span className="font-mono text-xs">{row.data.imei ?? ""}</span></Td>
                  <Td><span className="font-mono text-xs">{row.data.imei2 ?? ""}</span></Td>
                  <Td>{row.data.condition ? CONDITIONS[row.data.condition] ?? row.data.condition : ""}</Td>
                  <Td>{row.data.supplier ?? ""}</Td>
                  <Td>{row.data.received_on ?? ""}</Td>
                  <Td><Outcome row={row} /></Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          {job.rows_truncated ? (
            <p className="mt-3 text-xs text-muted">
              Se muestran las primeras filas: las de error primero y las que llevan un aviso después.
              El archivo completo se registra al confirmar.
            </p>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}
