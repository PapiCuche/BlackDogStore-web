"use client";

/**
 * SALES-QUOTE-01 — componer una cotización: sucursal, cliente (de la ficha o un prospecto), productos
 * (variantes por búsqueda; un seminuevo, por equipo), cantidades, descuento manual (sólo con permiso),
 * vigencia y condiciones comerciales.
 *
 * LOS PRECIOS LOS DECIDE EL SERVIDOR. El editor muestra el precio de la caja para orientar; al guardar, el
 * servidor vuelve a calcular todo con las reglas de la caja (promociones, IGV) y lo que devuelve es lo que vale.
 * Guardar una cotización ya enviada crea una revisión nueva: la anterior se conserva.
 *
 * TRADE-IN-QUOTE-00: el bloque opcional «Equipo ofrecido en parte de pago (referencial)» es parte del borrador;
 * su valorización nunca es un descuento ni cambia el total (ver `QuoteTradeIn`).
 */
import { useEffect, useId, useState } from "react";
import { fetchCustomers, type CustomerRow } from "../../lib/internal-api";
import {
  createQuote,
  draftProblem,
  fetchQuoteUnits,
  formatMoney,
  lineTotal,
  searchQuoteProducts,
  updateQuote,
  type QuoteContext,
  type QuoteDetail,
  type QuoteDraft,
  type QuoteDraftLine,
  type QuoteProductHit,
  type QuoteUnitHit,
} from "../../lib/sales-quotes-api";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../internal-ui";
import { TradeInEditor } from "./QuoteTradeIn";

const LABEL = "mb-1 block text-[11px] font-semibold uppercase tracking-widest text-muted";
let lineSeq = 0;

function nextKey() {
  lineSeq += 1;
  return `line-${lineSeq}`;
}

export function QuoteEditor({
  context, initial, quoteId, offered = false, onSaved,
}: {
  context: QuoteContext;
  initial: QuoteDraft;
  /** null: a new quote. */
  quoteId: number | null;
  /** The quote being edited was already offered: saving writes a new revision. */
  offered?: boolean;
  onSaved: (quote: QuoteDetail) => void;
}) {
  const ids = useId();
  const [draft, setDraft] = useState<QuoteDraft>(initial);
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<QuoteProductHit[]>([]);
  const [customerTerm, setCustomerTerm] = useState("");
  const [customerHits, setCustomerHits] = useState<CustomerRow[]>([]);
  const [unitsFor, setUnitsFor] = useState<{ line: string; units: QuoteUnitHit[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const discountLocked = !context.can_apply_discount;
  const problem = draftProblem(draft, context.can_apply_discount || sameDiscount(initial, draft));

  function patch(change: Partial<QuoteDraft>) {
    setDraft((current) => ({ ...current, ...change }));
  }

  function patchLine(key: string, change: Partial<QuoteDraftLine>) {
    setDraft((current) => ({ ...current, lines: current.lines.map((l) => (l.key === key ? { ...l, ...change } : l)) }));
  }

  const searching = Boolean(draft.branch) && term.trim().length >= 2;
  const shownHits = searching ? hits : [];
  const searchingCustomers = context.can_search_customers && customerTerm.trim().length >= 2;
  const shownCustomers = searchingCustomers ? customerHits : [];

  useEffect(() => {
    if (!searching) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchQuoteProducts(draft.branch as number, term.trim())
        .then((data) => { if (!cancelled) setHits(data.results); })
        .catch(() => { if (!cancelled) setHits([]); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [term, draft.branch, searching]);

  useEffect(() => {
    if (!searchingCustomers) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchCustomers(null, { search: customerTerm.trim() })
        .then((page) => { if (!cancelled) setCustomerHits(page.results.slice(0, 8)); })
        .catch(() => { if (!cancelled) setCustomerHits([]); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [customerTerm, searchingCustomers]);

  async function addProduct(hit: QuoteProductHit, onOrder = false) {
    const line: QuoteDraftLine = {
      key: nextKey(), product: hit.id, name: hit.name, quantity: 1,
      shownPrice: onOrder && hit.on_order ? hit.on_order.price : hit.price,
      on_order: onOrder, supply_reason: "", unit: null, unitLabel: "",
      pricedBy: onOrder ? "offer" : hit.priced_by,
    };
    setDraft((current) => ({ ...current, lines: [...current.lines, line] }));
    setTerm("");
    setHits([]);
    if (line.pricedBy === "unit" && draft.branch) {
      try {
        const data = await fetchQuoteUnits(draft.branch, hit.id);
        setUnitsFor({ line: line.key, units: data.results });
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudieron cargar los equipos.");
      }
    }
  }

  function pickUnit(lineKey: string, unit: QuoteUnitHit) {
    const label = [unit.imei && `IMEI ${unit.imei}`, unit.serial_number && `Serie ${unit.serial_number}`,
      unit.grade && `Grado ${unit.grade}`].filter(Boolean).join(" · ");
    patchLine(lineKey, { unit: unit.id, unitLabel: label, shownPrice: unit.sale_price, quantity: 1 });
    setUnitsFor(null);
  }

  async function save() {
    if (problem || busy) return;
    setBusy(true);
    setError(null);
    try {
      const saved = quoteId ? await updateQuote(quoteId, draft) : await createQuote(draft);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar la cotización.");
    } finally {
      setBusy(false);
    }
  }

  const shownTotal = draft.lines.reduce((sum, line) => sum + lineTotal(line), 0);

  return (
    <form
      className="space-y-6"
      onSubmit={(event) => { event.preventDefault(); void save(); }}
      aria-describedby={`${ids}-note`}
    >
      <p id={`${ids}-note`} className="text-sm text-muted">
        Una cotización no cobra ni separa stock. Los precios y el IGV los calcula el servidor con las reglas de la caja.
        {offered ? " Esta cotización ya fue ofrecida: al guardar se crea una revisión nueva y la anterior queda como reemplazada." : ""}
      </p>

      <section className="grid gap-4 rounded-xl border border-bd-border bg-surface p-4 sm:grid-cols-2">
        <label className="block">
          <span className={LABEL}>Sucursal</span>
          <select
            value={draft.branch ?? ""}
            onChange={(event) => patch({ branch: event.target.value ? Number(event.target.value) : null, lines: [] })}
            className={internalInputClass}
            required
          >
            <option value="">Elige la sucursal</option>
            {context.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </label>
        <label className="block">
          <span className={LABEL}>Válida hasta</span>
          <input
            type="date"
            value={draft.valid_until}
            onChange={(event) => patch({ valid_until: event.target.value })}
            className={internalInputClass}
          />
        </label>
        {context.can_assign_seller && context.sellers.length > 0 ? (
          <label className="block sm:col-span-2">
            <span className={LABEL}>Vendedor</span>
            <select
              value={draft.seller ?? ""}
              onChange={(event) => patch({ seller: event.target.value ? Number(event.target.value) : null })}
              className={internalInputClass}
            >
              <option value="">{context.seller.name} (yo)</option>
              {context.sellers.filter((s) => s.id !== context.seller.id).map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>
        ) : null}
      </section>

      <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4" aria-labelledby={`${ids}-customer`}>
        <h2 id={`${ids}-customer`} className={LABEL}>Cliente</h2>
        {draft.customer ? (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="font-semibold">{draft.customerLabel}</span>
            <button type="button" className={internalButtonClass} onClick={() => patch({ customer: null, customerLabel: "" })}>
              Cambiar
            </button>
          </div>
        ) : (
          <>
            {context.can_search_customers ? (
              <div className="relative">
                <label className="block">
                  <span className="sr-only">Buscar cliente</span>
                  <input
                    type="search"
                    value={customerTerm}
                    onChange={(event) => setCustomerTerm(event.target.value)}
                    placeholder="Buscar en la ficha de clientes (nombre o documento)"
                    className={internalInputClass}
                    role="combobox"
                    aria-autocomplete="list"
                    aria-controls={`${ids}-customers`}
                    aria-expanded={shownCustomers.length > 0}
                  />
                </label>
                {shownCustomers.length > 0 ? (
                  <ul id={`${ids}-customers`} className="mt-1 divide-y divide-bd-border rounded-lg border border-bd-border bg-background">
                    {shownCustomers.map((c) => (
                      <li key={c.id}>
                        <button
                          type="button"
                          className="w-full px-3 py-2 text-left text-sm hover:bg-foreground/[0.04] focus-visible:bg-foreground/[0.06]"
                          onClick={() => { patch({ customer: c.id, customerLabel: c.display_name }); setCustomerTerm(""); }}
                        >
                          {c.display_name}
                          <span className="ml-2 text-xs text-muted">{c.document_number}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className={LABEL}>Nombre (prospecto)</span>
                <input value={draft.customer_name} onChange={(e) => patch({ customer_name: e.target.value })} className={internalInputClass} maxLength={255} />
              </label>
              <div className="grid grid-cols-[7rem_1fr] gap-2">
                <label className="block">
                  <span className={LABEL}>Doc.</span>
                  <select value={draft.customer_document_type} onChange={(e) => patch({ customer_document_type: e.target.value })} className={internalInputClass}>
                    <option value="">—</option>
                    <option value="dni">DNI</option>
                    <option value="ruc">RUC</option>
                    <option value="ce">CE</option>
                    <option value="passport">Pasaporte</option>
                  </select>
                </label>
                <label className="block">
                  <span className={LABEL}>Número</span>
                  <input value={draft.customer_document_number} onChange={(e) => patch({ customer_document_number: e.target.value })} className={internalInputClass} maxLength={20} />
                </label>
              </div>
              <label className="block">
                <span className={LABEL}>Teléfono</span>
                <input type="tel" value={draft.customer_phone} onChange={(e) => patch({ customer_phone: e.target.value })} className={internalInputClass} maxLength={30} />
              </label>
              <label className="block">
                <span className={LABEL}>Correo</span>
                <input type="email" value={draft.customer_email} onChange={(e) => patch({ customer_email: e.target.value })} className={internalInputClass} />
              </label>
            </div>
          </>
        )}
      </section>

      <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4" aria-labelledby={`${ids}-lines`}>
        <h2 id={`${ids}-lines`} className={LABEL}>Productos</h2>
        <div>
          <label className="block">
            <span className="sr-only">Buscar producto</span>
            <input
              type="search"
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder={draft.branch ? "Buscar producto o variante (nombre o código)" : "Elige primero la sucursal"}
              disabled={!draft.branch}
              className={internalInputClass}
              role="combobox"
              aria-autocomplete="list"
              aria-controls={`${ids}-hits`}
              aria-expanded={shownHits.length > 0}
            />
          </label>
          {shownHits.length > 0 ? (
            <ul id={`${ids}-hits`} className="mt-1 divide-y divide-bd-border rounded-lg border border-bd-border bg-background">
              {shownHits.map((hit) => (
                <li key={hit.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{hit.name}</span>
                    <span className="block text-xs text-muted">
                      {hit.priced_by === "unit" ? "Precio por equipo" : formatMoney(hit.price)} · {hit.available} en esta sucursal
                    </span>
                  </span>
                  <span className="flex gap-2">
                    <button type="button" className={internalButtonClass} onClick={() => void addProduct(hit)}>Agregar</button>
                    {hit.on_order ? (
                      <button type="button" className={internalButtonClass} onClick={() => void addProduct(hit, true)}>
                        Bajo pedido
                      </button>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {draft.lines.length === 0 ? (
          <p className="text-sm text-muted">Todavía no hay productos.</p>
        ) : (
          <ul className="divide-y divide-bd-border">
            {draft.lines.map((line) => (
              <li key={line.key} className="space-y-2 py-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{line.name}</p>
                    <p className="text-xs text-muted">
                      {line.on_order ? "Bajo pedido · " : ""}
                      {line.unitLabel || (line.pricedBy === "unit" ? "Elige el equipo" : "")}
                    </p>
                  </div>
                  <label className="flex items-center gap-2 text-sm">
                    <span className="text-muted">Cant.</span>
                    <input
                      type="number"
                      min={1}
                      step={1}
                      inputMode="numeric"
                      value={line.quantity}
                      disabled={line.pricedBy === "unit"}
                      onChange={(event) => patchLine(line.key, { quantity: Math.max(1, Math.floor(Number(event.target.value) || 1)) })}
                      className={`${internalInputClass} w-20`}
                      aria-label={`Cantidad de ${line.name}`}
                    />
                  </label>
                  <span className="w-28 text-right text-sm font-semibold tabular-nums">{formatMoney(lineTotal(line))}</span>
                  <button
                    type="button"
                    className={internalButtonClass}
                    onClick={() => setDraft((current) => ({ ...current, lines: current.lines.filter((l) => l.key !== line.key) }))}
                    aria-label={`Quitar ${line.name}`}
                  >
                    Quitar
                  </button>
                </div>
                {line.on_order && context.can_order_over_stock ? (
                  <label className="block text-sm">
                    <span className="text-xs text-muted">Motivo del pedido al proveedor (si hay stock propio)</span>
                    <input value={line.supply_reason} onChange={(e) => patchLine(line.key, { supply_reason: e.target.value })} className={internalInputClass} maxLength={200} />
                  </label>
                ) : null}
                {line.pricedBy === "unit" && !line.on_order && draft.branch ? (
                  <button
                    type="button"
                    className={internalButtonClass}
                    onClick={async () => {
                      const data = await fetchQuoteUnits(draft.branch as number, line.product);
                      setUnitsFor({ line: line.key, units: data.results });
                    }}
                  >
                    {line.unit ? "Cambiar equipo" : "Elegir equipo"}
                  </button>
                ) : null}
                {unitsFor?.line === line.key ? (
                  unitsFor.units.length === 0 ? (
                    <p className="text-sm text-muted">No hay equipos disponibles con precio en esta sucursal.</p>
                  ) : (
                    <ul className="divide-y divide-bd-border rounded-lg border border-bd-border">
                      {unitsFor.units.map((unit) => (
                        <li key={unit.id}>
                          <button
                            type="button"
                            disabled={!unit.ready}
                            onClick={() => pickUnit(line.key, unit)}
                            className="flex w-full flex-wrap justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-foreground/[0.04] disabled:opacity-50"
                          >
                            <span>{[unit.imei && `IMEI ${unit.imei}`, unit.serial_number && `Serie ${unit.serial_number}`, unit.grade && `Grado ${unit.grade}`].filter(Boolean).join(" · ")}</span>
                            <span className="font-semibold tabular-nums">{formatMoney(unit.sale_price)}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <p className="text-right text-sm text-muted">
          Referencial antes de descuentos y promociones: <span className="font-semibold text-foreground tabular-nums">{formatMoney(shownTotal)}</span>
        </p>
      </section>

      <section className="grid gap-3 rounded-xl border border-bd-border bg-surface p-4 sm:grid-cols-3" aria-labelledby={`${ids}-discount`}>
        <h2 id={`${ids}-discount`} className={`${LABEL} sm:col-span-3`}>Descuento manual</h2>
        {discountLocked ? (
          <p className="text-sm text-muted sm:col-span-3">
            {draft.discount_type
              ? `Descuento autorizado: ${draft.discount_type === "percent" ? `${draft.discount_value} %` : formatMoney(draft.discount_value)} (${draft.discount_reason}). Sólo quien tiene permiso de descuentos puede cambiarlo.`
              : "Necesitas permiso de descuentos para aplicar uno. Las promociones automáticas se aplican solas."}
          </p>
        ) : (
          <>
            <label className="block">
              <span className="text-xs text-muted">Tipo</span>
              <select
                value={draft.discount_type}
                onChange={(e) => patch({ discount_type: e.target.value as QuoteDraft["discount_type"] })}
                className={internalInputClass}
              >
                <option value="">Sin descuento</option>
                <option value="amount">Monto</option>
                <option value="percent">Porcentaje</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-muted">Valor</span>
              <input
                type="number" min={0} step="0.01" inputMode="decimal"
                value={draft.discount_value}
                disabled={!draft.discount_type}
                onChange={(e) => patch({ discount_value: e.target.value })}
                className={internalInputClass}
              />
            </label>
            <label className="block">
              <span className="text-xs text-muted">Motivo</span>
              <input
                value={draft.discount_reason}
                disabled={!draft.discount_type}
                onChange={(e) => patch({ discount_reason: e.target.value })}
                className={internalInputClass}
                maxLength={200}
              />
            </label>
          </>
        )}
      </section>

      <TradeInEditor value={draft.trade_in} shownTotal={shownTotal} onChange={(trade_in) => patch({ trade_in })} />

      <section className="grid gap-3 rounded-xl border border-bd-border bg-surface p-4 sm:grid-cols-2">
        <label className="block">
          <span className={LABEL}>Condiciones comerciales (se imprimen)</span>
          <textarea
            rows={4}
            value={draft.conditions}
            onChange={(e) => patch({ conditions: e.target.value })}
            className={internalInputClass}
            maxLength={2000}
            placeholder="Forma de pago, plazo de entrega, garantía…"
          />
        </label>
        <label className="block">
          <span className={LABEL}>Notas internas (no se imprimen)</span>
          <textarea rows={4} value={draft.notes} onChange={(e) => patch({ notes: e.target.value })} className={internalInputClass} maxLength={2000} />
        </label>
      </section>

      {error ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">{error}</p> : null}
      {problem && !error ? <p className="text-sm text-muted" aria-live="polite">{problem}</p> : null}

      <div className="flex flex-wrap justify-end gap-3">
        <button type="submit" className={internalPrimaryButtonClass} disabled={Boolean(problem) || busy}>
          {busy ? "Guardando…" : offered ? "Guardar como revisión nueva" : "Guardar borrador"}
        </button>
      </div>
    </form>
  );
}

function sameDiscount(a: QuoteDraft, b: QuoteDraft): boolean {
  return a.discount_type === b.discount_type && a.discount_value === b.discount_value
    && a.discount_reason === b.discount_reason;
}
