"use client";

/**
 * INV-LOAD-02 · las cargas de equipos ya registradas.
 *
 * Para descargar su «Antes y después» o anularla entera también después de salir de la pantalla de la carga. Sólo
 * se listan las registradas (una vista previa no tiene nada que anular). El servidor decide qué cargas ve cada
 * persona (las de sus sucursales) y vuelve a comprobarlo al anular.
 */

import { useState } from "react";

import { fetchRecentUnitLoads, unitImportReportUrl, type UnitImportJob } from "@/app/lib/stock-units";

import { Panel, TableWrap, Td, Th, formatDateTime } from "./InventoryUi";
import { AnnulLoad } from "./StockUnitImport";
import { internalButtonClass } from "./internal-ui";

type Load = UnitImportJob & { applied_at?: string | null };

const LINES: Record<string, string> = { new: "Nuevos", used: "Seminuevos" };

export function RecentUnitLoads() {
  const [loads, setLoads] = useState<Load[] | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function show() {
    setWorking(true);
    setError(null);
    try {
      setLoads(await fetchRecentUnitLoads());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "No se pudo cargar el historial.");
    } finally {
      setWorking(false);
    }
  }

  function annulled(next: UnitImportJob) {
    setLoads((current) => current?.map((load) => (load.id === next.id ? { ...load, ...next } : load)) ?? null);
  }

  return (
    <Panel
      title="Cargas registradas"
      description="Las últimas cargas de equipos: su informe antes y después, y anularlas si fueron un error."
      action={
        <button type="button" className={internalButtonClass} disabled={working} onClick={show}>
          {working ? "Cargando…" : "Ver cargas registradas"}
        </button>
      }
    >
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {loads && loads.length === 0 ? (
        <p className="text-sm text-muted">Todavía no hay cargas de equipos registradas.</p>
      ) : null}
      {loads && loads.length > 0 ? (
        <TableWrap>
          <caption className="sr-only">Cargas de equipos registradas</caption>
          <thead>
            <tr className="border-b border-bd-border">
              <Th>Carga</Th><Th>Archivo</Th><Th>Línea</Th><Th>Registrada</Th><Th right>Equipos</Th><Th>Acciones</Th>
            </tr>
          </thead>
          <tbody>
            {loads.map((load) => (
              <tr key={load.id} className="border-b border-bd-border align-top">
                <Td muted>#{load.id}</Td>
                <Td>{load.original_filename}</Td>
                <Td>{load.options?.line ? LINES[load.options.line] : "Combinada"}</Td>
                <Td>{load.applied_at ? formatDateTime(load.applied_at) : ""}</Td>
                <Td right>{load.summary.applied?.units ?? 0}</Td>
                <Td>
                  <div className="flex flex-col gap-2">
                    <a className="text-sm font-semibold underline" href={unitImportReportUrl(load.id, "csv")}>
                      Antes y después (CSV)
                    </a>
                    <AnnulLoad job={load} onAnnulled={annulled} />
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      ) : null}
    </Panel>
  );
}
