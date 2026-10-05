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

import { useCallback, useEffect, useState } from "react";

import {
  actOnStockUnit, fetchSerializedProducts, fetchStockUnits, receiveStockUnits,
  StockUnitApiError, type NewUnit, type SerializedProduct, type StockUnit,
  type StockUnitPage, type UnitAction,
} from "@/app/lib/stock-units";

import {
  EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatDateTime,
} from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

type Filters = { product: string; status: string; condition: string; search: string };
const NO_FILTERS: Filters = { product: "", status: "", condition: "", search: "" };

const BLANK_UNIT: NewUnit = { serial_number: "", imei: "", imei2: "", condition: "new", cost: "" };

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
  const [receiving, setReceiving] = useState(false);
  const [productId, setProductId] = useState("");
  const [targetBranch, setTargetBranch] = useState("");
  const [reason, setReason] = useState("");
  const [lines, setLines] = useState<NewUnit[]>([{ ...BLANK_UNIT }]);
  const [batchCondition, setBatchCondition] = useState("new");
  const [batchCost, setBatchCost] = useState("");
  const [receiveError, setReceiveError] = useState<string | null>(null);
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
      () => { /* The table still works without the list; the form says so. */ },
    );
    return () => { cancelled = true; };
  }, []);

  const product = products.find((p) => String(p.id) === productId) ?? null;
  const entryBranch = typeof branch === "number" ? branch : Number(targetBranch) || null;
  const filled = lines.filter((line) => line.serial_number.trim());

  const setLine = useCallback((index: number, patch: Partial<NewUnit>) => {
    setLines((prev) => prev.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  }, []);

  async function receive() {
    if (!product || !entryBranch) return;
    setWorking(true);
    setReceiveError(null);
    setNotice(null);
    try {
      const created = await receiveStockUnits({
        product_id: product.id, branch: entryBranch, reason: reason.trim(),
        units: filled.map((line) => ({
          serial_number: line.serial_number.trim(),
          imei: product.requires_imei ? line.imei.trim() : "",
          imei2: product.requires_imei ? line.imei2.trim() : "",
          condition: batchCondition,
          cost: batchCost.trim(),
        })),
      });
      setNotice(`${plural(created.results.length)} ${created.results.length === 1 ? "ingresado" : "ingresados"} al inventario.`);
      setLines([{ ...BLANK_UNIT }]);
      setReason("");
      setReceiving(false);
      setReloadKey((key) => key + 1);
    } catch (err) {
      // What was typed stays: one wrong digit should not cost the whole batch.
      const line = err instanceof StockUnitApiError ? err.line : null;
      const message = err instanceof Error ? err.message : "No se pudieron ingresar los equipos.";
      setReceiveError(line ? `Equipo ${line}: ${message}` : message);
    } finally {
      setWorking(false);
    }
  }

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
      {canAdjust ? (
        <Panel
          title="Ingreso de equipos"
          description="Cada equipo entra con su número de serie y queda como una entrada en el Kardex. El stock del producto es la cantidad de equipos disponibles."
          action={
            receiving ? null : (
              <button type="button" className={internalPrimaryButtonClass} onClick={() => { setReceiving(true); setNotice(null); }}>
                Registrar equipos
              </button>
            )
          }
        >
          {notice ? <p role="status" className="text-sm text-success">{notice}</p> : null}
          {!receiving && !notice ? (
            <p className="text-sm text-muted">Registra aquí los equipos que llegan a la sucursal.</p>
          ) : null}
          {receiving ? (
            <div className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
                {typeof branch !== "number" ? (
                  <label className="block text-xs text-muted">
                    Sucursal de ingreso
                    <select value={targetBranch} onChange={(e) => setTargetBranch(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
                      <option value="">Elige una sucursal</option>
                      {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                    </select>
                  </label>
                ) : null}
                <label className="block text-xs text-muted">
                  Producto a recibir
                  <select value={productId} onChange={(e) => setProductId(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
                    <option value="">Elige un producto</option>
                    {products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </label>
                <label className="block text-xs text-muted">
                  Condición de los equipos
                  <select value={batchCondition} onChange={(e) => setBatchCondition(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
                    {(page?.conditions ?? [{ value: "new", label: "Nuevo" }]).map((c) => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </select>
                </label>
                <label className="block text-xs text-muted">
                  Costo por equipo (opcional)
                  <input value={batchCost} onChange={(e) => setBatchCost(e.target.value)} inputMode="decimal" className={`mt-1.5 ${internalInputClass}`} />
                </label>
                <label className="block text-xs text-muted md:col-span-2">
                  Motivo
                  <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ej.: compra a proveedor, guía 001-234" className={`mt-1.5 ${internalInputClass}`} />
                </label>
              </div>
              {products.length === 0 ? (
                <p className="text-xs text-muted">
                  Ningún producto se controla todavía por número de serie. Márcalo así en el producto mientras su stock esté en cero.
                </p>
              ) : null}

              {product ? (
                <ol className="space-y-2">
                  {lines.map((line, index) => (
                    <li key={index} className="grid gap-2 rounded-lg border border-bd-border p-3 md:grid-cols-3">
                      <label className="block text-xs text-muted">
                        Serie del equipo {index + 1}
                        <input
                          value={line.serial_number}
                          onChange={(e) => setLine(index, { serial_number: e.target.value })}
                          autoCapitalize="characters" spellCheck={false}
                          className={`mt-1.5 font-mono ${internalInputClass}`}
                        />
                      </label>
                      {product.requires_imei ? (
                        <>
                          <label className="block text-xs text-muted">
                            IMEI del equipo {index + 1}
                            <input
                              value={line.imei}
                              onChange={(e) => setLine(index, { imei: e.target.value })}
                              inputMode="numeric" spellCheck={false}
                              className={`mt-1.5 font-mono ${internalInputClass}`}
                            />
                          </label>
                          <label className="block text-xs text-muted">
                            Segundo IMEI {index + 1} (opcional)
                            <input
                              value={line.imei2}
                              onChange={(e) => setLine(index, { imei2: e.target.value })}
                              inputMode="numeric" spellCheck={false}
                              className={`mt-1.5 font-mono ${internalInputClass}`}
                            />
                          </label>
                        </>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : null}

              {receiveError ? <ErrorBox message={receiveError} /> : null}

              <div className="flex flex-wrap gap-2">
                {product ? (
                  <button type="button" className={internalButtonClass} disabled={working} onClick={() => setLines((prev) => [...prev, { ...BLANK_UNIT }])}>
                    Añadir otro equipo
                  </button>
                ) : null}
                <button
                  type="button"
                  className={internalPrimaryButtonClass}
                  disabled={working || !product || !entryBranch || !reason.trim() || filled.length === 0}
                  onClick={() => void receive()}
                >
                  Ingresar {plural(filled.length)}
                </button>
                <button type="button" className={internalButtonClass} disabled={working} onClick={() => { setReceiving(false); setReceiveError(null); }}>
                  Cancelar
                </button>
              </div>
            </div>
          ) : null}
        </Panel>
      ) : null}

      <Panel title="Equipos" description="Por número de serie. Busca por la serie o por los últimos dígitos del IMEI.">
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
          <EmptyBox message="No hay equipos que coincidan. Los equipos aparecen aquí cuando se registran con su número de serie." />
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
