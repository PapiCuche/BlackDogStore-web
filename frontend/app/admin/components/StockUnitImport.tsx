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
import { useState } from "react";

import {
  applyUnitImport, previewUnitImport, unitImportErrorsUrl, unitImportTemplateUrl,
  type UnitImportJob, type UnitImportRow,
} from "@/app/lib/stock-units";

import { ImportKindNotice } from "./ImportKindNotice";
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

export function StockUnitImport({ branches }: { branches: { id: number; name: string }[] }) {
  const [file, setFile] = useState<File | null>(null);
  const [branch, setBranch] = useState("");
  const [reason, setReason] = useState("");
  const [job, setJob] = useState<UnitImportJob | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(task: () => Promise<UnitImportJob>) {
    setWorking(true);
    setError(null);
    try {
      setJob(await task());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "No se pudo completar la operación.");
    } finally {
      setWorking(false);
    }
  }

  function choose(next: File | null) {
    setFile(next);
    setJob(null);
    setError(null);
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
        action={<a className={internalButtonClass} href={unitImportTemplateUrl()}>Descargar plantilla</a>}
      >
        <div className="space-y-4">
          <ImportKindNotice kind="units" />

          <div className="grid gap-3 md:grid-cols-3">
            <label className="block text-xs font-semibold text-muted">
              Archivo de equipos (.xlsx)
              <input
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
              onClick={() => file && run(() => previewUnitImport(file, { branch: branch ? Number(branch) : null, reason }))}
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

      {applied ? (
        <div role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">
          Se registraron {plural(registered, "equipo", "equipos")} y quedaron en el Kardex.{" "}
          <Link className="font-semibold underline" href="/admin/inventory/units">Ver los equipos</Link>
        </div>
      ) : null}

      {job && !applied ? (
        <Panel
          title="Lo que pasará con cada fila"
          description={`${job.original_filename} · ${plural(job.counts.create, "equipo por registrar", "equipos por registrar")} · ${job.counts.error} con error · ${plural(job.counts.skip, "fila omitida", "filas omitidas")}`}
          action={
            job.counts.error ? (
              <a className={internalButtonClass} href={unitImportErrorsUrl(job.id)}>Descargar errores (CSV)</a>
            ) : job.is_applicable && job.counts.create > 0 ? (
              <button
                type="button"
                className={internalPrimaryButtonClass}
                disabled={working}
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
                <Th>IMEI</Th><Th>IMEI 2</Th><Th>Condición</Th><Th>Resultado</Th>
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
