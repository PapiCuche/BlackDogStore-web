"use client";

/**
 * Inventario › Equipos — devices tracked by serial number.
 *
 * A DEVICE IS STOCK. Everything this screen does to one — bring it in, set it
 * aside, write it off, take it back — is an operation the server validates and
 * writes to the Kardex. Nothing is counted here and no identifier is judged
 * here: the table shows what the server lists, the filters are answered by the
 * server, and a refusal is shown in the server's words.
 *
 * COMMERCE-ADMIN-UX-01: filters by line, model and the catalogue's options
 * (capacity, colour…), read from the existing catalogue endpoints — hidden when
 * the caller may not read the catalogue — and a detail drawer per device built
 * from the row the list already carries. The cost appears there only for
 * `costs.view`, and only because the server sent it.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchAdminCatalogModels, fetchCatalogAttributes,
  type AdminCatalogAttribute, type AdminCatalogModel,
} from "@/app/admin/lib/internal-api";
import { notifyStockChanged } from "@/app/lib/stock-events";

import {
  actOnStockUnit, fetchSerializedProducts, fetchStockUnits,
  type SerializedProduct, type StockUnit, type StockUnitPage, type UnitAction,
} from "@/app/lib/stock-units";

import {
  EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatDateTime,
} from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { NextStepBar } from "./NextStepBar";
import { StockUnitForm, type UnitSaved } from "./StockUnitForm";

type Filters = {
  product: string; status: string; condition: string; search: string;
  line: "" | "new" | "used"; model: string;
  /** attribute id → option id */
  options: Record<number, number>;
};
const NO_FILTERS: Filters = { product: "", status: "", condition: "", search: "", line: "", model: "", options: {} };

/** What a link may preselect: the line and the product. Anything else is ignored. */
export type UnitInitialFilters = Partial<Pick<Filters, "line" | "product">>;

/**
 * COMMERCE-ADMIN-UX-01 — «Equipos nuevos» (`?line=new`) and the registration's
 * «Ver los equipos» (`?product=41`) open this list already filtered.
 */
export function unitFiltersFromSearch(search: string): UnitInitialFilters {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const found: UnitInitialFilters = {};
  const line = params.get("line");
  if (line === "new" || line === "used") found.line = line;
  const product = params.get("product");
  if (product && /^\d+$/.test(product)) found.product = product;
  return found;
}

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

function money(value: string) {
  const n = Number.parseFloat(value);
  return Number.isNaN(n) ? value : `S/ ${n.toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/**
 * One device, everything the list already said about it. No request of its
 * own: what is shown is the row the server sent, cost included only when the
 * server included it (`costs.view`) AND the screen was told the caller has it.
 */
function UnitDetail({ unit, canSeeCost, onClose }: { unit: StockUnit; canSeeCost: boolean; onClose: () => void }) {
  const rows: [string, React.ReactNode][] = [
    ["Producto", unit.product_name],
    ["Línea", unit.condition_line === "used" ? "Seminuevo" : "Nuevo"],
    ["Sucursal", unit.branch_name],
    ["Estado", unit.status === "reserved"
      ? (unit.held_online
        ? `Apartado web${unit.hold_until ? ` hasta ${formatDateTime(unit.hold_until)}` : ""}`
        : "Apartado por personal")
      : unit.status_label],
    ["Serie", <span key="s" className="font-mono">{unit.serial_number}</span>],
    ["IMEI", unit.imei ? <span key="i" className="font-mono">{unit.imei}{unit.imei2 ? ` · ${unit.imei2}` : ""}</span> : "—"],
    ["Condición", unit.condition_label],
    ["Ingreso", formatDateTime(unit.received_at)],
  ];
  if (unit.sold_at) rows.push(["Vendido", formatDateTime(unit.sold_at)]);
  if (unit.order_id) rows.push(["Pedido", `#${unit.order_id}`]);
  if (unit.condition_line === "used" || unit.battery_health != null || unit.cosmetic_grade_label) {
    rows.push(["Batería", unit.battery_health == null ? "No informada" : `${unit.battery_health}%`]);
    rows.push(["Estado estético", unit.cosmetic_grade_label || "—"]);
  }
  rows.push(["Precio propio", unit.sale_price ? money(unit.sale_price) : "El del producto"]);
  if (unit.public_notes) rows.push(["Nota pública", unit.public_notes]);
  if (unit.notes) rows.push(["Nota interna", unit.notes]);
  if (unit.photos && unit.photos.length > 0) rows.push(["Fotos", String(unit.photos.length)]);
  if (canSeeCost && unit.cost !== undefined) rows.push(["Costo", unit.cost ? money(unit.cost) : "Sin costo registrado"]);

  return (
    <aside
      role="dialog"
      aria-label={`Detalle del equipo ${unit.serial_number}`}
      className="mb-4 rounded-lg border border-bd-border bg-background p-4"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <p className="text-sm font-semibold text-foreground">Equipo · <span className="font-mono">{unit.serial_number}</span></p>
        <button type="button" className={internalButtonClass} onClick={onClose}>Cerrar</button>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3 border-b border-bd-border/50 py-1">
            <dt className="text-muted">{label}</dt>
            <dd className="text-right text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

export function StockUnitsPanel({
  branch, branches, canAdjust, canSeeCost = false, initialFilters, openRegistration = false, registerProductId = null,
}: {
  /** The branch in view; "all" for every branch the caller reaches. */
  branch: number | "all" | undefined;
  branches: { id: number; name: string }[];
  canAdjust: boolean;
  /** `costs.view`: the drawer shows the cost the server sent. Without it there is none to show. */
  canSeeCost?: boolean;
  /** COMMERCE-ADMIN-UX-01: filters a link asked for (`unitFiltersFromSearch`). */
  initialFilters?: UnitInitialFilters;
  /** INV-UX-A1: «Recibir mercadería» opens the registration form (`?register=1`)… */
  openRegistration?: boolean;
  /** …with this product already chosen (`?product=`). */
  registerProductId?: number | null;
}) {
  const [page, setPage] = useState<StockUnitPage | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [draft, setDraft] = useState<Filters>(() => ({ ...NO_FILTERS, ...initialFilters }));
  const [applied, setApplied] = useState<Filters>(() => ({ ...NO_FILTERS, ...initialFilters }));
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saved, setSaved] = useState<UnitSaved | null>(null);

  const [products, setProducts] = useState<SerializedProduct[]>([]);
  const [models, setModels] = useState<AdminCatalogModel[]>([]);
  const [attributes, setAttributes] = useState<AdminCatalogAttribute[]>([]);
  const [detail, setDetail] = useState<StockUnit | null>(null);
  const [registering, setRegistering] = useState(openRegistration);
  const [working, setWorking] = useState(false);

  const [pending, setPending] = useState<{ unit: StockUnit; action: UnitAction } | null>(null);
  const [actionReason, setActionReason] = useState("");

  useEffect(() => {
    let cancelled = false;
    const { options, ...rest } = applied;
    fetchStockUnits({ branch, ...rest, options: Object.values(options), page: pageNumber }).then(
      (loaded) => {
        if (cancelled) return;
        setPage(loaded);
        setError(null);
        // The drawer follows the list: a device that changed shows its new state.
        setDetail((open) => (open ? loaded.results.find((u) => u.id === open.id) ?? open : open));
      },
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

  useEffect(() => {
    let cancelled = false;
    // The catalogue's models and options, for the filters. Somebody who may not
    // read the catalogue keeps every other filter: these simply do not appear.
    fetchAdminCatalogModels().then(
      (loaded) => { if (!cancelled) setModels(loaded); },
      () => { /* not offered */ },
    );
    fetchCatalogAttributes().then(
      (loaded) => { if (!cancelled) setAttributes(loaded); },
      () => { /* not offered */ },
    );
    return () => { cancelled = true; };
  }, []);

  async function act(unit: StockUnit, action: UnitAction, why = "") {
    setWorking(true);
    setError(null);
    setNotice(null);
    try {
      await actOnStockUnit(unit.id, action, why.trim());
      notifyStockChanged();
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
      {saved ? (
        <NextStepBar product={saved.product} published={saved.published} labelBatch={saved.labelBatch} />
      ) : null}

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
            initialProductId={registerProductId}
            onSaved={(message, info) => { setNotice(message); setSaved(info ?? null); setReloadKey((key) => key + 1); }}
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
              <button type="button" className={internalPrimaryButtonClass} onClick={() => { setRegistering(true); setNotice(null); setSaved(null); }}>
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
            Línea
            <select value={draft.line} onChange={(e) => setDraft({ ...draft, line: e.target.value as Filters["line"] })} className={`mt-1.5 ${internalInputClass}`}>
              <option value="">Nuevos y seminuevos</option>
              <option value="new">Nuevos</option>
              <option value="used">Seminuevos</option>
            </select>
          </label>
          {models.length > 0 ? (
            <label className="block text-xs text-muted">
              Modelo
              <select value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                <option value="">Todos</option>
                {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </label>
          ) : null}
          {attributes.map((attribute) => (
            <label key={attribute.id} className="block text-xs text-muted">
              {attribute.name}
              <select
                value={String(draft.options[attribute.id] ?? "")}
                onChange={(e) => {
                  const options = { ...draft.options };
                  if (e.target.value) options[attribute.id] = Number(e.target.value);
                  else delete options[attribute.id];
                  setDraft({ ...draft, options });
                }}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">Todas</option>
                {(attribute.options ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          ))}
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
          <div className="flex items-end gap-2">
            <button type="submit" className={internalButtonClass}>Filtrar</button>
            <button
              type="button"
              className={internalButtonClass}
              onClick={() => { setDraft(NO_FILTERS); setApplied(NO_FILTERS); setPageNumber(1); }}
            >
              Limpiar
            </button>
          </div>
        </form>

        {error ? <ErrorBox message={error} /> : null}

        {detail ? <UnitDetail unit={detail} canSeeCost={canSeeCost} onClose={() => setDetail(null)} /> : null}

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
                  <Th right>Acciones</Th>
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
                        {unit.status === "reserved" ? (unit.held_online ? "Apartado web" : "Apartado por personal") : unit.status_label}
                      </span>
                    </Td>
                    <Td muted>{formatDateTime(unit.received_at)}</Td>
                    <Td right>
                        <span className="inline-flex flex-wrap justify-end gap-1.5">
                          <button
                            type="button"
                            aria-label={`Detalle del equipo ${unit.serial_number}`}
                            className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25"
                            onClick={() => setDetail(unit)}
                          >
                            Detalle
                          </button>
                          {canAdjust ? actionsFor(unit).map((action) => (
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
                          )) : null}
                        </span>
                    </Td>
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
