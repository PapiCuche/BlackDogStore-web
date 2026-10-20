"use client";

/**
 * «Ventas › Consultar disponibilidad» — COMMERCE-ADMIN-UX-01.
 *
 * A seller asks «do we have it, where, and if not, can we get it?». The answer
 * comes whole from the server; this screen only lays it out:
 *
 *   OWN STOCK and SUPPLIER OFFERS are two columns that never meet. What a
 *   supplier confirms is never added to the shelf, and its column says «no es
 *   stock propio» in so many words.
 *
 *   FRESH, WITHOUT HAMMERING. A query on every change (the search box waits for
 *   the typing to stop), a refresh button, a light poll only while the page is
 *   visible, and a re-ask when the tab comes back or another screen of this
 *   browser says stock moved (a sale at the till, a device set aside).
 *
 *   NO COST, no full IMEI: the server never sends them, and nothing here asks.
 */

import Link from "next/link";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import {
  fetchAvailability, fetchAvailabilityUnits,
  type AvailabilityFacets, type AvailabilityPage, type AvailabilityProduct, type AvailabilityUnitsPage,
  type UnitStatusFilter,
} from "@/app/lib/availability";
import { onStockChanged } from "@/app/lib/stock-events";

import { EmptyBox, ErrorBox, Spinner, formatDateTime, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass } from "./internal-ui";

const DEFAULT_POLL_MS = 45_000;
const DEBOUNCE_MS = 350;
/** Two re-asks closer than this (focus + visibility firing together) are one. */
const MIN_REFRESH_GAP_MS = 3_000;

type Filters = {
  q: string;
  branch: number | "all";
  category: number | "";
  line: "" | "new" | "used";
  model: number | "";
  /** attribute id → option id */
  options: Record<number, number>;
};

const NO_FILTERS: Filters = { q: "", branch: "all", category: "", line: "", model: "", options: {} };

const UNIT_STATUS_LABEL: Record<UnitStatusFilter, string> = {
  live: "Disponibles y apartados",
  available: "Sólo disponibles",
  reserved: "Sólo apartados",
  sold: "Vendidos",
  written_off: "Dados de baja",
  all: "Todos",
};

function LineBadge({ line }: { line: AvailabilityProduct["line"] }) {
  return line === "used" ? (
    <span className="rounded-full border border-warning-border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-warning">
      Seminuevo
    </span>
  ) : (
    <span className="rounded-full border border-success-border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-success">
      Nuevo
    </span>
  );
}

function formatDay(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

function priceText(product: AvailabilityProduct): string {
  const range = product.unit_price_range;
  if (product.line === "used" && range) {
    return range.min === range.max ? formatSoles(range.min) : `${formatSoles(range.min)} – ${formatSoles(range.max)}`;
  }
  return formatSoles(product.price);
}

function OwnStock({ product, multiBranch }: { product: AvailabilityProduct; multiBranch: boolean }) {
  const { total, by_branch: branches } = product.stock;
  const reserved = total.reserved_web + total.reserved_staff;
  return (
    <div className="space-y-1">
      <p className={`text-sm font-semibold tabular-nums ${total.available > 0 ? "text-foreground" : "text-danger"}`}>
        {total.available > 0 ? `${total.available} disponible${total.available === 1 ? "" : "s"}` : "Sin stock propio"}
      </p>
      {multiBranch ? (
        <ul className="text-xs text-muted">
          {branches.map((b) => (
            <li key={b.branch_id} className="tabular-nums">{b.branch_name}: {b.available}</li>
          ))}
        </ul>
      ) : null}
      {product.is_serialized ? (
        <p className="text-xs text-muted tabular-nums">
          {reserved > 0 ? `Apartados ${reserved} (web ${total.reserved_web} · personal ${total.reserved_staff}) · ` : ""}
          Vendidos {total.sold} · De baja {total.written_off}
        </p>
      ) : null}
    </div>
  );
}

function SupplierColumn({ product, canSeeSuppliers }: { product: AvailabilityProduct; canSeeSuppliers: boolean }) {
  if (product.supplier_offers.length === 0) {
    return <p className="text-xs text-muted">Sin ofertas vigentes</p>;
  }
  return (
    <div className="space-y-1.5">
      <ul className="space-y-1">
        {product.supplier_offers.map((offer, index) => (
          <li key={index} className="text-xs text-foreground">
            <span className="font-semibold tabular-nums">{formatSoles(offer.commercial_price)}</span>
            {" · "}
            <span className="tabular-nums">{offer.remaining} por pedir</span>
            {" · "}
            {offer.lead_time_days === 1 ? "1 día" : `${offer.lead_time_days} días`}
            <span className="text-muted"> · hasta {formatDay(offer.valid_until)}</span>
            {canSeeSuppliers && offer.supplier_name ? <span className="block text-muted">{offer.supplier_name}</span> : null}
          </li>
        ))}
      </ul>
      {!product.sells_on_order ? (
        <p className="text-[11px] text-warning">Este producto se vende sólo con stock propio.</p>
      ) : null}
    </div>
  );
}

/**
 * SUPPLIER-DEVICE-FLOW-02 — «Vender en caja»: the till sells what this page
 * found. Own stock sells from the shelf; a valid offer of a product sold «Bajo
 * pedido» sells on order there too (the till's «Bajo pedido» option). The
 * till has no way to preselect a product from the address yet: it opens and the
 * seller scans or searches it. Who may read this page may sell (`sales.pos.use`).
 */
export const POS_HREF = "/admin/sales/pos";

function SellLinks({ product }: { product: AvailabilityProduct }) {
  const own = product.stock.total.available > 0;
  const onOrder = !own && product.sells_on_order && product.supplier_offers.length > 0;
  if (!own && !onOrder) return null;
  return (
    <Link
      href={POS_HREF}
      className="inline-block rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground hover:border-foreground/25"
      aria-label={`${own ? "Vender en caja" : "Vender bajo pedido en caja"}: ${product.name}`}
    >
      {own ? "Vender en caja" : "Vender bajo pedido en caja"}
    </Link>
  );
}

function UnitsDrawer({
  product, branch,
}: {
  product: AvailabilityProduct;
  branch: number | "all";
}) {
  const [unitStatus, setUnitStatus] = useState<UnitStatusFilter>("live");
  const [data, setData] = useState<AvailabilityUnitsPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => onStockChanged(() => setReloadKey((k) => k + 1)), []);

  useEffect(() => {
    let cancelled = false;
    fetchAvailabilityUnits(product.id, { branch, unitStatus }).then(
      (loaded) => { if (!cancelled) { setData(loaded); setError(null); } },
      (err) => { if (!cancelled) setError(err instanceof Error ? err.message : "No se pudieron cargar los equipos."); },
    );
    return () => { cancelled = true; };
  }, [product.id, branch, unitStatus, reloadKey]);

  return (
    <div className="space-y-3 rounded-lg border border-bd-border bg-background p-4" aria-label={`Equipos de ${product.name}`}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="text-sm font-semibold text-foreground">Equipos de {product.name}</p>
        <label className="block text-xs text-muted">
          Estado del equipo
          <select
            value={unitStatus}
            onChange={(e) => setUnitStatus(e.target.value as UnitStatusFilter)}
            className={`mt-1 ${internalInputClass}`}
          >
            {(Object.keys(UNIT_STATUS_LABEL) as UnitStatusFilter[]).map((key) => (
              <option key={key} value={key}>{UNIT_STATUS_LABEL[key]}</option>
            ))}
          </select>
        </label>
      </div>
      {error ? <ErrorBox message={error} /> : null}
      {!data && !error ? <Spinner label="Cargando equipos…" /> : null}
      {data && data.results.length === 0 ? <EmptyBox message="No hay equipos en este estado." /> : null}
      {data && data.results.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-bd-border text-left text-[11px] uppercase tracking-widest text-muted">
                <th className="px-2 py-2">IMEI / serie</th>
                <th className="px-2 py-2">Sucursal</th>
                <th className="px-2 py-2">Estado</th>
                {product.line === "used" ? <th className="px-2 py-2">Batería · estado</th> : null}
                <th className="px-2 py-2 text-right">Precio</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((unit) => (
                <tr key={unit.id} className="border-b border-bd-border/60">
                  <td className="px-2 py-2 font-mono text-xs">
                    {unit.imei_last4 ? `IMEI ••••${unit.imei_last4}` : unit.serial_last4 ? `Serie ••••${unit.serial_last4}` : "—"}
                  </td>
                  <td className="px-2 py-2 text-muted">{unit.branch_name}</td>
                  <td className="px-2 py-2">
                    {unit.hold_kind === "web"
                      ? `Apartado web${unit.hold_until ? ` hasta ${formatDateTime(unit.hold_until)}` : ""}`
                      : unit.hold_kind === "staff"
                        ? "Apartado por personal"
                        : unit.status_label}
                  </td>
                  {product.line === "used" ? (
                    <td className="px-2 py-2 text-muted">
                      {unit.battery_health === null ? "Batería no informada" : `Batería ${unit.battery_health}%`}
                      {unit.cosmetic_grade_label ? ` · ${unit.cosmetic_grade_label}` : ""}
                    </td>
                  ) : null}
                  <td className="px-2 py-2 text-right tabular-nums">{formatSoles(unit.sale_price ?? product.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.truncated ? (
            <p className="mt-2 text-xs text-muted">Se muestran {data.results.length} de {data.count} equipos.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function AvailabilityPanel({ pollMs = DEFAULT_POLL_MS }: { pollMs?: number }) {
  const [draftQ, setDraftQ] = useState("");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [pageNumber, setPageNumber] = useState(1);
  const [data, setData] = useState<AvailabilityPage | null>(null);
  const [facets, setFacets] = useState<AvailabilityFacets | null>(null);
  const [branches, setBranches] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const sequence = useRef(0);
  const lastFetch = useRef(0);
  const needFacets = useRef(true);

  const load = useCallback(async () => {
    const mine = ++sequence.current;
    lastFetch.current = Date.now();
    setLoading(true);
    try {
      const wantFacets = needFacets.current;
      const page = await fetchAvailability({
        q: filters.q, branch: filters.branch, category: filters.category, line: filters.line,
        model: filters.model, options: Object.values(filters.options), page: pageNumber, facets: wantFacets,
      });
      if (mine !== sequence.current) return; // a newer question is already on its way
      if (page.facets) { setFacets(page.facets); needFacets.current = false; }
      if (filters.branch === "all") setBranches(page.scope.branches);
      setData(page);
      setError(null);
      setUpdatedAt(new Date());
    } catch (err) {
      if (mine !== sequence.current) return;
      setError(err instanceof Error ? err.message : "No se pudo consultar la disponibilidad.");
    } finally {
      if (mine === sequence.current) setLoading(false);
    }
  }, [filters, pageNumber]);

  // Every change of the question asks again.
  useEffect(() => { void load(); }, [load]);

  // The search box waits for the typing to stop.
  useEffect(() => {
    if (draftQ === filters.q) return;
    const timer = setTimeout(() => {
      setPageNumber(1);
      setFilters((current) => ({ ...current, q: draftQ }));
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draftQ, filters.q]);

  // Light polling, only while somebody can see the page; a re-ask on coming
  // back to it and whenever another screen says stock moved.
  useEffect(() => {
    const refreshIfStale = () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastFetch.current < MIN_REFRESH_GAP_MS) return;
      void load();
    };
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, pollMs);
    document.addEventListener("visibilitychange", refreshIfStale);
    window.addEventListener("focus", refreshIfStale);
    const unsubscribe = onStockChanged(() => { void load(); });
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfStale);
      window.removeEventListener("focus", refreshIfStale);
      unsubscribe();
    };
  }, [load, pollMs]);

  function change(patch: Partial<Filters>) {
    setPageNumber(1);
    setOpen(null);
    setFilters((current) => ({ ...current, ...patch }));
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.count / data.page_size)) : 1;
  const multiBranch = (data?.scope.branches.length ?? 0) > 1;
  const canSeeSuppliers = data?.can_see_suppliers ?? false;

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
        <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onSubmit={(e) => e.preventDefault()} role="search">
          <label className="block text-xs text-muted sm:col-span-2">
            Buscar
            <input
              type="search"
              value={draftQ}
              onChange={(e) => setDraftQ(e.target.value)}
              placeholder="Nombre, código, modelo, capacidad o color"
              className={`mt-1.5 ${internalInputClass}`}
            />
          </label>
          <label className="block text-xs text-muted">
            Sucursal
            <select
              value={String(filters.branch)}
              onChange={(e) => change({ branch: e.target.value === "all" ? "all" : Number(e.target.value) })}
              className={`mt-1.5 ${internalInputClass}`}
            >
              <option value="all">Todas mis sucursales</option>
              {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
          <label className="block text-xs text-muted">
            Línea
            <select
              value={filters.line}
              onChange={(e) => change({ line: e.target.value as Filters["line"] })}
              className={`mt-1.5 ${internalInputClass}`}
            >
              <option value="">Nuevos y seminuevos</option>
              <option value="new">Nuevos</option>
              <option value="used">Seminuevos</option>
            </select>
          </label>
          <label className="block text-xs text-muted">
            Categoría
            <select
              value={String(filters.category)}
              onChange={(e) => change({ category: e.target.value ? Number(e.target.value) : "" })}
              className={`mt-1.5 ${internalInputClass}`}
            >
              <option value="">Todas</option>
              {(facets?.categories ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          {facets && facets.models.length > 0 ? (
            <label className="block text-xs text-muted">
              Modelo
              <select
                value={String(filters.model)}
                onChange={(e) => change({ model: e.target.value ? Number(e.target.value) : "" })}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">Todos</option>
                {facets.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </label>
          ) : null}
          {(facets?.attributes ?? []).map((attribute) => (
            <label key={attribute.id} className="block text-xs text-muted">
              {attribute.name}
              <select
                value={String(filters.options[attribute.id] ?? "")}
                onChange={(e) => {
                  const options = { ...filters.options };
                  if (e.target.value) options[attribute.id] = Number(e.target.value);
                  else delete options[attribute.id];
                  change({ options });
                }}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">Todas</option>
                {(attribute.options ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
          ))}
        </form>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted" aria-live="polite">
            {loading && data ? "Actualizando…" : updatedAt ? `Actualizado ${updatedAt.toLocaleTimeString("es-PE")}` : ""}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              className={internalButtonClass}
              onClick={() => { setDraftQ(""); setPageNumber(1); setOpen(null); setFilters(NO_FILTERS); }}
            >
              Limpiar filtros
            </button>
            <button type="button" className={internalButtonClass} onClick={() => void load()} disabled={loading}>
              Actualizar
            </button>
          </div>
        </div>
      </div>

      {error ? <ErrorBox message={error} /> : null}
      {!data && loading && !error ? <Spinner label="Consultando disponibilidad…" /> : null}
      {data && data.results.length === 0 ? (
        <EmptyBox message="No hay productos que coincidan con la búsqueda." />
      ) : null}

      {data && data.results.length > 0 ? (
        <section className="min-w-0 rounded-xl border border-bd-border bg-surface">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse">
              <thead>
                <tr className="border-b border-bd-border text-left text-[11px] font-semibold uppercase tracking-widest text-muted">
                  <th className="px-3 py-2.5">Producto</th>
                  <th className="px-3 py-2.5 text-right">Precio</th>
                  <th className="px-3 py-2.5">Stock propio</th>
                  <th className="px-3 py-2.5">
                    Proveedor (bajo pedido)
                    <span className="block text-[10px] font-normal normal-case tracking-normal">no es stock propio</span>
                  </th>
                  <th className="px-3 py-2.5 text-right"><span className="sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((product) => (
                  <Fragment key={product.id}>
                    <tr className="border-b border-bd-border/60 align-top" data-testid={`availability-row-${product.id}`}>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-foreground">{product.name}</span>
                          <LineBadge line={product.line} />
                        </div>
                        <p className="mt-1 text-xs text-muted">
                          {[product.model?.name, ...product.options.map((o) => o.label), product.barcode]
                            .filter(Boolean).join(" · ")}
                        </p>
                      </td>
                      <td className="px-3 py-3 text-right text-sm tabular-nums">{priceText(product)}</td>
                      <td className="px-3 py-3"><OwnStock product={product} multiBranch={multiBranch} /></td>
                      <td className="px-3 py-3"><SupplierColumn product={product} canSeeSuppliers={canSeeSuppliers} /></td>
                      <td className="px-3 py-3 text-right">
                        <div className="flex flex-col items-end gap-1.5">
                          <SellLinks product={product} />
                          {product.is_serialized ? (
                            <button
                              type="button"
                              className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground hover:border-foreground/25"
                              aria-expanded={open === product.id}
                              onClick={() => setOpen(open === product.id ? null : product.id)}
                            >
                              {open === product.id ? "Ocultar equipos" : "Ver equipos"}
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {open === product.id ? (
                      <tr className="border-b border-bd-border/60">
                        <td colSpan={5} className="px-3 py-3">
                          <UnitsDrawer product={product} branch={filters.branch} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-bd-border px-4 py-3">
            <p className="text-xs text-muted">
              {data.count === 1 ? "1 producto" : `${data.count} productos`} · página {data.page} de {totalPages}
            </p>
            <div className="flex gap-2">
              <button type="button" className={internalButtonClass} disabled={pageNumber <= 1} onClick={() => setPageNumber((n) => Math.max(1, n - 1))}>
                Anterior
              </button>
              <button type="button" className={internalButtonClass} disabled={pageNumber >= totalPages} onClick={() => setPageNumber((n) => n + 1)}>
                Siguiente
              </button>
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
