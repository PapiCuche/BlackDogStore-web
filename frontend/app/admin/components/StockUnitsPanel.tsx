"use client";

/**
 * Inventario › Equipos — devices tracked by serial number.
 *
 * A DEVICE IS STOCK. Everything this screen does to one — bring it in, set it
 * aside, write it off, take it back — is an operation the server validates and
 * writes to the Kardex. Nothing is counted here and no identifier is judged
 * here: the table shows what the server lists, the filters are answered by the
 * server, and a refusal is shown in the server's words.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  actOnStockUnit, fetchSerializedProducts, fetchStockUnits,
  type SerializedProduct, type StockUnit, type StockUnitPage, type UnitAction,
} from "@/app/lib/stock-units";

import {
  EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatDateTime,
} from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { StockUnitForm } from "./StockUnitForm";

type Filters = { product: string; status: string; condition: string; search: string };
const NO_FILTERS: Filters = { product: "", status: "", condition: "", search: "" };

const ACTION_LABEL: Record<UnitAction, string> = {
  reserve: "Apartar",
  release: "Liberar",
  "write-off": "Dar de baja",
  return: "Registrar devolución",
};

const STATUS_TONE: Record<StockUnit["status"], string> = {
  available: "border-success-border text-success",
  reserved: "border-warning-border text-warning",
  sold: "border-bd-border text-muted",
  written_off: "border-danger-border text-danger",
};

function actionsFor(unit: StockUnit): UnitAction[] {
  if (unit.status === "available") return ["reserve", "write-off"];
  if (unit.status === "reserved") return ["release"];
  if (unit.status === "sold") return ["return"];
  return [];
}

const plural = (n: number) => (n === 1 ? "1 equipo" : `${n} equipos`);

export function StockUnitsPanel({
  branch, branches, canAdjust,
}: {
  /** The branch in view; "all" for every branch the caller reaches. */
  branch: number | "all" | undefined;
  branches: { id: number; name: string }[];
  canAdjust: boolean;
}) {
  const [page, setPage] = useState<StockUnitPage | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [draft, setDraft] = useState<Filters>(NO_FILTERS);
  const [applied, setApplied] = useState<Filters>(NO_FILTERS);
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [products, setProducts] = useState<SerializedProduct[]>([]);
  const [registering, setRegistering] = useState(false);
  const [working, setWorking] = useState(false);

  const [pending, setPending] = useState<{ unit: StockUnit; action: UnitAction } | null>(null);
  const [actionReason, setActionReason] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchStockUnits({ branch, ...applied, page: pageNumber }).then(
      (loaded) => { if (!cancelled) { setPage(loaded); setError(null); } },
      (err) => { if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar los equipos."); },
    );
    return () => { cancelled = true; };
  }, [branch, applied, pageNumber, reloadKey]);

  useEffect(() => {
    let cancelled = false;
    fetchSerializedProducts().then(
      (loaded) => { if (!cancelled) setProducts(loaded.results); },
      () => { /* The table still works without the filter's list. */ },
    );
    return () => { cancelled = true; };
    // Re-read after every change: a product may just have started being tracked by serial.
  }, [reloadKey]);

  async function act(unit: StockUnit, action: UnitAction, why = "") {
    setWorking(true);
    setError(null);
    setNotice(null);
    try {
      await actOnStockUnit(unit.id, action, why.trim());
      setPending(null);
      setActionReason("");
      setReloadKey((key) => key + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo completar la operación.");
    } finally {
      setWorking(false);
    }
  }

  const totalPages = page ? Math.max(1, Math.ceil(page.count / page.page_size)) : 1;

  return (
    <div className="space-y-6">
      {notice ? <p role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}

      {canAdjust && registering ? (
        <Panel
          title="Registrar equipo"
          description="Un equipo, un registro: su número de serie y su IMEI lo identifican dentro del stock de su producto."
        >
          <StockUnitForm
            branch={branch}
            branches={branches}
            conditions={page?.conditions ?? [{ value: "new", label: "Nuevo" }]}
            canChangeTracking={canAdjust}
            onSaved={(message) => { setNotice(message); setReloadKey((key) => key + 1); }}
            onClose={() => setRegistering(false)}
          />
        </Panel>
      ) : null}

      <Panel
        title="Equipos"
        description="Cada fila es un equipo físico. Busca por la serie o por los últimos dígitos del IMEI."
        action={
          canAdjust && !registering ? (
            <div className="flex flex-wrap items-center gap-2">
              <Link className={internalButtonClass} href="/admin/inventory/units/import">
                Cargar desde Excel
              </Link>
              <button type="button" className={internalPrimaryButtonClass} onClick={() => { setRegistering(true); setNotice(null); }}>
                + Registrar equipo
              </button>
            </div>
          ) : null
        }
      >
        <form
          className="mb-4 grid gap-3 md:grid-cols-2 lg:grid-cols-5"
          onSubmit={(e) => { e.preventDefault(); setPageNumber(1); setApplied(draft); }}
        >
          <label className="block text-xs text-muted">
            Producto
            <select value={draft.product} onChange={(e) => setDraft({ ...draft, product: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
              <option value="">Todos</option>
              {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Estado
            <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
              <option value="">Todos</option>
              {(page?.statuses ?? []).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Condición
            <select value={draft.condition} onChange={(e) => setDraft({ ...draft, condition: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
              <option value="">Todas</option>
              {(page?.conditions ?? []).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Serie o IMEI
            <input value={draft.search} onChange={(e) => setDraft({ ...draft, search: e.target.value })} spellCheck={false} className={`mt-1.5 font-mono ${internalInputClass}`} />
          </label>
          <div className="flex items-end">
            <button type="submit" className={internalButtonClass}>Filtrar</button>
          </div>
        </form>

        {error ? <ErrorBox message={error} /> : null}

        {pending ? (
          <div className="mb-4 space-y-3 rounded-lg border border-bd-border bg-background p-4">
            <p className="text-sm text-foreground">
              {ACTION_LABEL[pending.action]} · <span className="font-mono">{pending.unit.serial_number}</span>
            </p>
            <label className="block text-xs text-muted">
              Motivo de la operación
              <input value={actionReason} onChange={(e) => setActionReason(e.target.value)} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <div className="flex gap-2">
              <button
                type="button" className={internalPrimaryButtonClass}
                disabled={working || !actionReason.trim()}
                onClick={() => void act(pending.unit, pending.action, actionReason)}
              >
                Confirmar
              </button>
              <button type="button" className={internalButtonClass} disabled={working} onClick={() => { setPending(null); setActionReason(""); }}>
                Cancelar
              </button>
            </div>
          </div>
        ) : null}

        {page === null && !error ? <Spinner label="Cargando equipos…" /> : null}
        {page && page.results.length === 0 ? (
          <EmptyBox message={canAdjust
            ? "No hay equipos que coincidan. Usa «+ Registrar equipo» para ingresar uno con su número de serie y su IMEI."
            : "No hay equipos que coincidan. Los equipos aparecen aquí cuando se registran con su número de serie."} />
        ) : null}

        {page && page.results.length > 0 ? (
          <>
            <TableWrap>
              <thead>
                <tr className="border-b border-bd-border">
                  <Th>Serie</Th><Th>IMEI</Th><Th>Producto</Th><Th>Sucursal</Th>
                  <Th>Condición</Th><Th>Estado</Th><Th>Ingreso</Th>
                  {canAdjust ? <Th right>Acciones</Th> : null}
                </tr>
              </thead>
              <tbody>
                {page.results.map((unit) => (
                  <tr key={unit.id} className="border-b border-bd-border/60">
                    <Td><span className="font-mono">{unit.serial_number}</span></Td>
                    <Td muted={!unit.imei}>
                      {unit.imei ? <span className="font-mono">{unit.imei}</span> : "—"}
                      {unit.imei2 ? <span className="block font-mono text-xs text-muted">{unit.imei2}</span> : null}
                    </Td>
                    <Td>{unit.product_name}</Td>
                    <Td muted>{unit.branch_name}</Td>
                    <Td muted>{unit.condition_label}</Td>
                    <Td>
                      <span className={`rounded-full border px-2.5 py-1 text-[11px] ${STATUS_TONE[unit.status]}`}>
                        {unit.status_label}
                      </span>
                    </Td>
                    <Td muted>{formatDateTime(unit.received_at)}</Td>
                    {canAdjust ? (
                      <Td right>
                        <span className="inline-flex flex-wrap justify-end gap-1.5">
                          {actionsFor(unit).map((action) => (
                            <button
                              key={action}
                              type="button"
                              disabled={working}
                              className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 disabled:opacity-40"
                              onClick={() => {
                                if (action === "release") void act(unit, action);
                                else { setPending({ unit, action }); setActionReason(""); setError(null); }
                              }}
                            >
                              {ACTION_LABEL[action]}
                            </button>
                          ))}
                        </span>
                      </Td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </TableWrap>
            <div className="mt-4 flex items-center justify-between">
              <p className="text-xs text-muted">{plural(page.count)} · página {page.page} de {totalPages}</p>
              <div className="flex gap-2">
                <button type="button" className={internalButtonClass} disabled={pageNumber <= 1} onClick={() => setPageNumber((n) => Math.max(1, n - 1))}>
                  Anterior
                </button>
                <button type="button" className={internalButtonClass} disabled={pageNumber >= totalPages} onClick={() => setPageNumber((n) => n + 1)}>
                  Siguiente
                </button>
              </div>
            </div>
          </>
        ) : null}
      </Panel>
    </div>
  );
}
