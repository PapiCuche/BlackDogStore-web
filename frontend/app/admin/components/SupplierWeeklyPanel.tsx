"use client";

/**
 * Productos › Proveedores › Ofertas › «Semana» — SUPPLY-WEEKLY-01.
 *
 * One row per supplier × variant of the week (`GET /admin/supplier-offers/weekly/`): the model and the options
 * of each role, what the offer charges and how many the supplier has left, the lead time and the validity, and
 * what changed against the supplier's previous list (nuevo, precio cambió, cantidad cambió, retirado, no
 * mencionado, sin cambio). The deltas come from the APPLIED lists; the screen never computes one.
 *
 * The shop's OWN stock is a column of its own: a supplier's quantity is never added to it. The agreed cost is
 * shown only when the server sent it (`costs.view`). READ ONLY: nothing here changes an offer or a price.
 *
 * SUPPLY-MARGIN-01: «Precio sugerido» (cost + the company's margin, computed by the server) sits next to the offer's
 * price, only when the rows carry the key (`costs.view`). A proposal to read; this screen writes nothing.
 */

import { useEffect, useMemo, useState } from "react";

import {
  SUPPLIER_OFFER_STATE_LABELS,
  SUPPLIER_WEEKLY_DELTAS,
  SUPPLIER_WEEKLY_DELTA_LABELS,
  fetchSupplierWeekly,
  type SupplierRow,
  type SupplierWeekly,
  type SupplierWeeklyDelta,
  type SupplierWeeklyRow,
} from "../lib/internal-api";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass } from "./internal-ui";

const PRESSED = "border-foreground/40 bg-foreground/[0.06] text-foreground";

const DELTA_TONE: Record<SupplierWeeklyDelta, string> = {
  nuevo: "border-success-border bg-success-surface text-success",
  precio_cambio: "border-warning-border bg-warning-surface text-warning",
  cantidad_cambio: "border-bd-border bg-background text-foreground",
  retirado: "border-danger-border bg-danger-surface text-danger",
  no_mencionado: "border-warning-border text-warning",
  sin_cambio: "border-bd-border text-muted",
  inactivo: "border-bd-border text-muted",
};

function DeltaBadge({ delta }: { delta: SupplierWeeklyDelta }) {
  return (
    <span data-delta={delta} className={`inline-flex whitespace-nowrap rounded border px-2 py-0.5 text-[11px] font-medium ${DELTA_TONE[delta] ?? "border-bd-border text-muted"}`}>
      {SUPPLIER_WEEKLY_DELTA_LABELS[delta] ?? delta}
    </span>
  );
}

function day(value: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? "");
  if (!match) return value || "—";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return date.toLocaleDateString("es-PE", { day: "2-digit", month: "short" });
}

/** `value` (YYYY-MM-DD) moved by `days`, in the calendar (no time zone involved). */
export function shiftDay(value: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days));
  return date.toISOString().slice(0, 10);
}

/** «128 GB · Negro · eSIM · Japón» — the options of the variant, by role, as the catalogue has them. */
export function variantOptions(row: Pick<SupplierWeeklyRow, "capacity" | "color" | "sim" | "region">): string {
  return [row.capacity, row.color, row.sim, row.region].filter(Boolean).join(" · ");
}

/** «S/ 100.00 → S/ 105.00», «5 → 8»: what changed against the previous list, from what the server sent. */
export function deltaDetail(row: SupplierWeeklyRow): string[] {
  const out: string[] = [];
  if (row.price_kept) out.push("Precio nuevo no aplicado: se mantuvo el actual");
  if (!row.previous) return out;
  if (row.changes.includes("price") && row.previous.commercial_price && row.listed_price) {
    out.push(`Precio: ${formatSoles(row.previous.commercial_price)} → ${formatSoles(row.listed_price)}`);
  }
  if (row.changes.includes("quantity")) {
    const before = row.previous.declared_quantity ?? "sin cantidad";
    const after = row.declared_quantity ?? "sin cantidad";
    out.push(`Cantidad: ${before} → ${after}`);
  }
  return out;
}

function matches(row: SupplierWeeklyRow, words: string[]): boolean {
  if (!words.length) return true;
  const text = [row.model, row.product_name, row.supplier_name, row.capacity, row.color, row.sim, row.region]
    .filter(Boolean).join(" ").toLocaleLowerCase("es");
  return words.every((word) => text.includes(word));
}

/** SUPPLY-MARGIN-01: the server's suggestion, or why there is none. Shown only when the row carries the key. */
export function suggestedText(value: string | null | undefined): string {
  return value ? formatSoles(value) : "Sin sugerencia";
}

function hasSuggestion(row: SupplierWeeklyRow): boolean {
  return Object.prototype.hasOwnProperty.call(row, "suggested_price");
}

function available(row: SupplierWeeklyRow): string {
  if (row.remaining === null) return "Sin confirmar";
  return String(row.remaining);
}

export function SupplierWeeklyPanel({
  companyId, suppliers,
}: { companyId: number | null; suppliers: SupplierRow[] }) {
  const [supplier, setSupplier] = useState("");
  // A day inside the week asked for; empty = the current week (the server decides «today»).
  const [weekDay, setWeekDay] = useState("");
  const [data, setData] = useState<SupplierWeekly | null>(null);
  const [error, setError] = useState<string | null>(null);
  // What the shown answer was asked with: while it differs from the filters, a newer answer is on its way.
  const [answered, setAnswered] = useState<string | null>(null);
  const [deltaFilter, setDeltaFilter] = useState<SupplierWeeklyDelta | "">("");
  const [search, setSearch] = useState("");
  const asked = `${supplier}|${weekDay}|${companyId ?? ""}`;
  const loading = answered !== asked;

  useEffect(() => {
    let current = true;
    fetchSupplierWeekly({ supplier: supplier ? Number(supplier) : "", week: weekDay }, companyId).then(
      (answer) => { if (current) { setData(answer); setError(null); setAnswered(asked); } },
      (err) => {
        if (!current) return;
        setError(err instanceof Error ? err.message : "No se pudo cargar la semana de ofertas.");
        setAnswered(asked);
      },
    );
    return () => { current = false; };
  }, [supplier, weekDay, companyId, asked]);

  const words = useMemo(() => search.toLocaleLowerCase("es").split(/\s+/).filter(Boolean), [search]);
  const rows = useMemo(
    () => (data?.results ?? []).filter((row) => (!deltaFilter || row.delta === deltaFilter) && matches(row, words)),
    [data, deltaFilter, words],
  );
  const withCost = Boolean(data?.with_cost);
  const withSuggestion = Boolean(data?.results.some(hasSuggestion));
  const isCurrent = Boolean(data && data.today >= data.week_start && data.today <= data.week_end);

  return (
    <Panel
      title="Semana de ofertas"
      description="Lo que dijo cada lista de proveedor esta semana frente a la anterior. El stock propio va en su columna: lo que declara un proveedor nunca se suma a él."
    >
      <div className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-end" role="group" aria-label="Filtrar la semana">
        <label className="block text-xs text-muted">
          Proveedor
          <select value={supplier} onChange={(e) => setSupplier(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Todos los proveedores</option>
            {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <div className="text-xs text-muted">
          <span id="supplier-week-label" className="block">
            Semana {data ? `${data.week} · ${day(data.week_start)} – ${day(data.week_end)}` : "…"}
          </span>
          <span className="mt-1.5 inline-flex flex-wrap gap-2" role="group" aria-labelledby="supplier-week-label">
            <button type="button" className={`min-h-[44px] ${internalButtonClass}`} disabled={!data || loading}
              onClick={() => data && setWeekDay(shiftDay(data.week_start, -7))}>
              ← Semana anterior
            </button>
            <button type="button" className={`min-h-[44px] ${internalButtonClass}`} disabled={!data || loading || isCurrent}
              onClick={() => setWeekDay("")}>
              Esta semana
            </button>
            <button type="button" className={`min-h-[44px] ${internalButtonClass}`} disabled={!data || loading || isCurrent}
              onClick={() => data && setWeekDay(shiftDay(data.week_start, 7))}>
              Semana siguiente →
            </button>
          </span>
        </div>
        <label className="block text-xs text-muted">
          Buscar
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Modelo, color, proveedor…"
            className={`mt-1.5 ${internalInputClass}`} />
        </label>
      </div>

      {data ? (
        <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filtrar por cambio">
          <button type="button" aria-pressed={deltaFilter === ""} onClick={() => setDeltaFilter("")}
            className={`min-h-[44px] ${internalButtonClass} ${deltaFilter === "" ? PRESSED : ""}`}>
            Todos ({data.total})
          </button>
          {SUPPLIER_WEEKLY_DELTAS.map((delta) => (
            <button key={delta} type="button" aria-pressed={deltaFilter === delta} onClick={() => setDeltaFilter(delta)}
              className={`min-h-[44px] ${internalButtonClass} ${deltaFilter === delta ? PRESSED : ""}`}>
              {SUPPLIER_WEEKLY_DELTA_LABELS[delta]} ({data.counts[delta] ?? 0})
            </button>
          ))}
        </div>
      ) : null}

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {loading && !data && !error ? <Spinner label="Cargando la semana…" /> : null}
      {data?.truncated ? (
        <p role="note" className="mb-3 text-xs text-warning">
          Se muestran las primeras {data.results.length} de {data.total} filas: filtra por proveedor para ver el resto.
        </p>
      ) : null}
      {data && rows.length === 0 ? <EmptyBox message="No hay ofertas que coincidan en esta semana." /> : null}
      {data && rows.length > 0 ? (
        <>
          {/* Phones: one card per row, everything in view without scrolling sideways. */}
          <ul className="space-y-3 md:hidden" aria-label="Ofertas de la semana" data-testid="supplier-week-cards">
            {rows.map((row) => (
              <li key={`${row.supplier_id}-${row.product_id}`} className="rounded-lg border border-bd-border p-3 text-sm">
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 break-words font-semibold text-foreground">{row.model ?? row.product_name}</span>
                  <DeltaBadge delta={row.delta} />
                </div>
                {variantOptions(row) ? <span className="block text-xs text-muted">{variantOptions(row)}</span> : null}
                <span className="block text-xs text-muted">{row.supplier_name}</span>
                {deltaDetail(row).map((text) => <span key={text} className="block text-xs text-foreground">{text}</span>)}
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  <dt className="text-muted">Precio oferta</dt><dd className="text-right">{formatSoles(row.commercial_price)}</dd>
                  {withSuggestion ? (<><dt className="text-muted">Precio sugerido</dt><dd className="text-right">{suggestedText(row.suggested_price)}</dd></>) : null}
                  <dt className="text-muted">Precio variante</dt><dd className="text-right">{formatSoles(row.variant_price)}</dd>
                  {withCost ? (<><dt className="text-muted">Costo</dt><dd className="text-right">{row.agreed_cost ? formatSoles(row.agreed_cost) : "Sin costo"}</dd></>) : null}
                  <dt className="text-muted">Quedan (proveedor)</dt><dd className="text-right">{available(row)}</dd>
                  <dt className="text-muted">Stock propio</dt><dd className="text-right">{row.own_stock}</dd>
                  <dt className="text-muted">Plazo</dt><dd className="text-right">{row.lead_time_days} d</dd>
                  <dt className="text-muted">Vigencia</dt><dd className="text-right">{day(row.valid_from)} – {day(row.valid_until)}</dd>
                  <dt className="text-muted">Estado</dt><dd className="text-right">{SUPPLIER_OFFER_STATE_LABELS[row.state] ?? row.state}</dd>
                </dl>
              </li>
            ))}
          </ul>
          <div className="hidden md:block" data-testid="supplier-week-table">
            <TableWrap>
              <thead>
                <tr className="border-b border-bd-border">
                  <Th>Variante</Th><Th>Proveedor</Th><Th>Cambio</Th><Th right>Precio oferta</Th>
                  {withSuggestion ? <Th right>Precio sugerido</Th> : null}
                  <Th right>Precio variante</Th>
                  {withCost ? <Th right>Costo pactado</Th> : null}
                  <Th right>Quedan (proveedor)</Th><Th right>Stock propio</Th><Th right>Plazo</Th><Th>Vigencia</Th><Th>Estado</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.supplier_id}-${row.product_id}`} className="border-b border-bd-border/60"
                    data-testid={`week-row-${row.supplier_id}-${row.product_id}`}>
                    <Td>
                      <span className="font-semibold">{row.model ?? row.product_name}</span>
                      {variantOptions(row) ? <span className="block text-xs text-muted">{variantOptions(row)}</span> : null}
                      {row.model ? <span className="block text-xs text-muted">{row.product_name}</span> : null}
                    </Td>
                    <Td muted>{row.supplier_name}{row.supplier_active ? "" : " (inactivo)"}</Td>
                    <Td>
                      <DeltaBadge delta={row.delta} />
                      {deltaDetail(row).map((text) => <span key={text} className="block text-xs text-foreground">{text}</span>)}
                      {row.message_date ? <span className="block text-xs text-muted">Lista del {day(row.message_date)}</span> : null}
                    </Td>
                    <Td right>{formatSoles(row.commercial_price)}</Td>
                    {withSuggestion ? <Td right>{suggestedText(row.suggested_price)}</Td> : null}
                    <Td right>{formatSoles(row.variant_price)}</Td>
                    {withCost ? <Td right>{row.agreed_cost ? formatSoles(row.agreed_cost) : "Sin costo"}</Td> : null}
                    <Td right>{available(row)}</Td>
                    <Td right>{row.own_stock}</Td>
                    <Td right>{row.lead_time_days} {row.lead_time_days === 1 ? "día" : "días"}</Td>
                    <Td muted>{day(row.valid_from)} – {day(row.valid_until)}</Td>
                    <Td>{SUPPLIER_OFFER_STATE_LABELS[row.state] ?? row.state}</Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          </div>
        </>
      ) : null}
    </Panel>
  );
}
