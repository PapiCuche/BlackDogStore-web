"use client";

/**
 * Productos › Proveedores › Ofertas — DEVICE-COMMERCE-01 · DC3.
 *
 * An offer says that a supplier sells a product at a price, for a number of
 * CONFIRMED units, between two dates (both included), delivered in N days. Only
 * a confirmed, current offer with units left makes a product sellable «bajo
 * pedido»; the server decides that and says it as the offer's `state`.
 *
 * THE COST. `agreed_cost` comes only to `costs.view`. A cost is shown when the
 * offer carries the key — never derived, never a zero standing in for «you may
 * not see it».
 *
 * Weekly updates are a re-upload of the Excel: preview, then apply. The file
 * is a PARTIAL update (SUPPLIER-DEVICE-FLOW-02): the preview shows, per row,
 * what the offer was and what it becomes; a new commercial price is applied
 * only after an explicit decision (accept, or keep the current ones); a row
 * nobody could match may be mapped to a variant by the supplier's code.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchAdminProducts, type AdminProduct } from "@/app/lib/admin";
import {
  CatalogApiError,
  SUPPLIER_OFFER_ACTION_LABELS,
  SUPPLIER_OFFER_STATE_LABELS,
  createSupplierOffer,
  deactivateSupplierOffer,
  fetchSupplierOfferHistory,
  fetchSupplierOffers,
  fetchSuppliers,
  supplierOfferExportUrl,
  supplierOfferTemplateUrl,
  updateSupplierOffer,
  type SupplierOffer,
  type SupplierOfferHistoryEntry,
  type SupplierOfferState,
  type SupplierOfferWrite,
  type SupplierRow,
} from "../lib/internal-api";
import {
  CHANGE_LABELS,
  WeeklyImportError,
  aiImportNotice,
  applyWeeklyImport,
  previewWeeklyImport,
  saveSupplierProductRef,
  summaryOf,
  type AlreadyApplied,
  type PriceChangeRow,
  type PriceDecision,
  type SupplierImportChange,
  type WeeklyImportPreview,
  type WeeklyImportResult,
  type WeeklyImportRow,
} from "../lib/supplier-import-api";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatDateTime, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

/** The cost is the server's to send. Its key, not its value, says whether it may be shown. */
export function hasAgreedCost(offer: SupplierOffer): offer is SupplierOffer & { agreed_cost: string | null } {
  return Object.prototype.hasOwnProperty.call(offer, "agreed_cost");
}

/** «12 jun 2026», from a YYYY-MM-DD date, without a timezone shifting the day. */
function formatDay(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? "");
  if (!match) return value || "—";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return date.toLocaleDateString("es-PE", { day: "2-digit", month: "short", year: "numeric" });
}

const STATE_TONE: Record<SupplierOfferState, string> = {
  active: "border-success-border bg-success-surface text-success",
  scheduled: "border-bd-border text-muted",
  unconfirmed: "border-warning-border bg-warning-surface text-warning",
  exhausted: "border-warning-border bg-warning-surface text-warning",
  expired: "border-bd-border text-muted",
  inactive: "border-bd-border text-muted",
};

export function OfferStateBadge({ state }: { state: SupplierOfferState }) {
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-medium ${STATE_TONE[state] ?? "border-bd-border text-muted"}`}>
      {SUPPLIER_OFFER_STATE_LABELS[state] ?? state}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Product picker — search, then choose one
// ---------------------------------------------------------------------------

type PickedProduct = { id: number; name: string };

function ProductPicker({
  label, value, onChange, companyId,
}: {
  label: string;
  value: PickedProduct | null;
  onChange: (product: PickedProduct | null) => void;
  companyId: number | null;
}) {
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<AdminProduct[]>([]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (term.trim().length < 2) { setHits([]); return; }
      fetchAdminProducts({ search: term.trim(), page_size: 8, company: companyId }).then(
        (page) => { if (!cancelled) setHits(page.results ?? []); },
        () => { if (!cancelled) setHits([]); },
      );
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [term, companyId]);

  if (value) {
    return (
      <div className="block text-xs text-muted">
        {label}
        <div className="mt-1.5 flex items-center justify-between gap-2 rounded-lg border border-bd-border bg-background px-3 py-2 text-sm text-foreground">
          <span className="min-w-0 truncate">{value.name}</span>
          <button type="button" className="text-xs font-semibold text-muted hover:text-foreground" onClick={() => onChange(null)} aria-label={`Quitar ${value.name}`}>
            Quitar
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <label className="block text-xs text-muted">
        {label}
        <input type="search" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Busca por nombre o código" className={`mt-1.5 ${internalInputClass}`} />
      </label>
      {hits.length ? (
        <ul className="mt-1 max-h-56 overflow-auto rounded-lg border border-bd-border bg-surface" aria-label={`Resultados: ${label}`}>
          {hits.map((product) => (
            <li key={product.id}>
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-sm text-foreground hover:bg-foreground/[0.04]"
                onClick={() => { onChange({ id: product.id, name: product.name }); setTerm(""); setHits([]); }}
              >
                {product.name}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Offer form
// ---------------------------------------------------------------------------

type OfferDraft = {
  supplier_id: string;
  product: PickedProduct | null;
  commercial_price: string;
  agreed_cost: string;
  confirmed_quantity: string;
  valid_from: string;
  valid_until: string;
  lead_time_days: string;
  is_active: boolean;
};

function draftOf(offer: SupplierOffer | null): OfferDraft {
  if (!offer) {
    return {
      supplier_id: "", product: null, commercial_price: "", agreed_cost: "", confirmed_quantity: "",
      valid_from: "", valid_until: "", lead_time_days: "", is_active: true,
    };
  }
  return {
    supplier_id: String(offer.supplier_id),
    product: { id: offer.product_id, name: offer.product_name },
    commercial_price: offer.commercial_price ?? "",
    agreed_cost: hasAgreedCost(offer) ? offer.agreed_cost ?? "" : "",
    confirmed_quantity: offer.confirmed_quantity === null ? "" : String(offer.confirmed_quantity),
    valid_from: offer.valid_from ?? "",
    valid_until: offer.valid_until ?? "",
    lead_time_days: String(offer.lead_time_days ?? ""),
    is_active: offer.is_active,
  };
}

function OfferForm({
  offer, suppliers, showCost, companyId, onSaved, onCancel,
}: {
  /** null = a new offer. */
  offer: SupplierOffer | null;
  suppliers: SupplierRow[];
  /** A cost field only for who may see costs — and, on an edit, only when the offer carried the key. */
  showCost: boolean;
  companyId: number | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<OfferDraft>(() => draftOf(offer));
  const [fields, setFields] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const missing: Record<string, string> = {};
    if (!draft.supplier_id) missing.supplier_id = "Elige el proveedor.";
    if (!offer && !draft.product) missing.product_id = "Elige el producto.";
    if (!draft.commercial_price.trim()) missing.commercial_price = "Escribe el precio de venta bajo pedido.";
    if (!draft.valid_from) missing.valid_from = "Indica desde cuándo vale.";
    if (!draft.valid_until) missing.valid_until = "Indica hasta cuándo vale.";
    if (!draft.lead_time_days.trim()) missing.lead_time_days = "Indica en cuántos días entrega.";
    if (Object.keys(missing).length) { setFields(missing); return; }

    const body: SupplierOfferWrite = {
      supplier_id: Number(draft.supplier_id),
      commercial_price: draft.commercial_price.trim(),
      confirmed_quantity: draft.confirmed_quantity.trim() === "" ? null : Number(draft.confirmed_quantity),
      valid_from: draft.valid_from,
      valid_until: draft.valid_until,
      lead_time_days: Number(draft.lead_time_days),
      ...(showCost ? { agreed_cost: draft.agreed_cost.trim() === "" ? null : draft.agreed_cost.trim() } : {}),
      ...(offer ? { is_active: draft.is_active } : { product_id: draft.product!.id }),
    };
    setSaving(true);
    setFields({});
    setError(null);
    try {
      if (offer) await updateSupplierOffer(offer.id, body, companyId);
      else await createSupplierOffer(body, companyId);
      onSaved();
    } catch (err) {
      setError(message(err, "No se pudo guardar la oferta."));
      if (err instanceof CatalogApiError) {
        if (Object.keys(err.fields).length) setFields(err.fields);
        if (err.field) setFields((f) => ({ ...f, [err.field as string]: err.message }));
      }
    } finally {
      setSaving(false);
    }
  }

  const fieldError = (key: string) => (fields[key] ? <span className="mt-1 block text-danger">{fields[key]}</span> : null);
  const input = (key: keyof OfferDraft, label: string, extra: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="block text-xs text-muted">
      {label}
      <input
        value={String(draft[key] ?? "")}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
        aria-invalid={fields[key] ? true : undefined}
        className={`mt-1.5 ${internalInputClass}`}
        {...extra}
      />
      {fieldError(key)}
    </label>
  );

  return (
    <form
      onSubmit={submit}
      noValidate
      aria-label={offer ? `Editar oferta de ${offer.product_name}` : "Nueva oferta"}
      className="mb-6 grid gap-3 rounded-lg border border-bd-border bg-background p-4 md:grid-cols-2"
    >
      <label className="block text-xs text-muted">
        Proveedor
        <select
          value={draft.supplier_id}
          onChange={(e) => setDraft({ ...draft, supplier_id: e.target.value })}
          aria-invalid={fields.supplier_id ? true : undefined}
          className={`mt-1.5 ${internalInputClass}`}
        >
          <option value="">Elige un proveedor</option>
          {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}{s.is_active ? "" : " (inactivo)"}</option>)}
        </select>
        {fieldError("supplier_id")}
      </label>
      {offer ? (
        <div className="block text-xs text-muted">
          Producto
          <p className="mt-1.5 rounded-lg border border-bd-border px-3 py-2 text-sm text-foreground">{offer.product_name}</p>
        </div>
      ) : (
        <div>
          <ProductPicker label="Producto" value={draft.product} onChange={(product) => setDraft({ ...draft, product })} companyId={companyId} />
          {fieldError("product_id")}
        </div>
      )}
      {input("commercial_price", "Precio de venta bajo pedido (S/)", { inputMode: "decimal" })}
      {showCost ? input("agreed_cost", "Costo acordado con el proveedor (S/)", { inputMode: "decimal" }) : null}
      <label className="block text-xs text-muted">
        Cantidad confirmada
        <input
          type="number"
          min={0}
          value={draft.confirmed_quantity}
          onChange={(e) => setDraft({ ...draft, confirmed_quantity: e.target.value })}
          aria-invalid={fields.confirmed_quantity ? true : undefined}
          className={`mt-1.5 ${internalInputClass}`}
        />
        <span className="mt-1 block">En blanco = sin confirmar: la oferta no vende hasta que se confirme.</span>
        {fieldError("confirmed_quantity")}
      </label>
      {input("lead_time_days", "Entrega en (días)", { type: "number", min: 0 })}
      {input("valid_from", "Vigente desde", { type: "date" })}
      {input("valid_until", "Vigente hasta (incluido)", { type: "date" })}
      {offer ? (
        <label className="flex items-center gap-2 self-end text-sm text-foreground">
          <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} />
          Activa
        </label>
      ) : null}
      {error ? <p role="alert" className="text-xs text-danger md:col-span-2">{error}</p> : null}
      <div className="flex justify-end gap-2 md:col-span-2">
        <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
        <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>
          {saving ? "Guardando…" : offer ? "Guardar oferta" : "Crear oferta"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Deactivate and history
// ---------------------------------------------------------------------------

function DeactivateForm({
  offer, companyId, onDone, onCancel,
}: { offer: SupplierOffer; companyId: number | null; onDone: () => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!reason.trim()) { setError("Escribe el motivo."); return; }
    setSaving(true);
    setError(null);
    try {
      await deactivateSupplierOffer(offer.id, reason.trim(), companyId);
      onDone();
    } catch (err) {
      setError(message(err, "No se pudo desactivar la oferta."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Desactivar oferta de ${offer.product_name}`} className="mb-6 flex flex-wrap items-end gap-3 rounded-lg border border-warning-border bg-warning-surface p-4">
      <label className="block min-w-60 flex-1 text-xs text-muted">
        Motivo de la desactivación
        <input value={reason} onChange={(e) => setReason(e.target.value)} className={`mt-1.5 ${internalInputClass}`} />
      </label>
      <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
      <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Desactivando…" : "Desactivar oferta"}</button>
      {error ? <p role="alert" className="w-full text-xs text-danger">{error}</p> : null}
    </form>
  );
}

function historyDetail(entry: SupplierOfferHistoryEntry): string {
  const meta = entry.metadata ?? {};
  if (typeof meta.reason === "string" && meta.reason) return `Motivo: ${meta.reason}`;
  const changed = Array.isArray(meta.changed) ? meta.changed : Array.isArray(meta.fields) ? meta.fields : null;
  if (changed && changed.length) return `Cambió: ${changed.map(String).join(", ")}`;
  return "";
}

function HistoryPanel({ offer, companyId, onClose }: { offer: SupplierOffer; companyId: number | null; onClose: () => void }) {
  const [entries, setEntries] = useState<SupplierOfferHistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSupplierOfferHistory(offer.id, companyId).then(
      (rows) => { setEntries(rows); setError(null); },
      (err) => setError(message(err, "No se pudo cargar el historial.")),
    );
  }, [offer.id, companyId]);

  return (
    <section role="region" aria-label={`Historial de la oferta de ${offer.product_name}`} className="mb-6 rounded-lg border border-bd-border bg-background p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Historial · {offer.product_name} · {offer.supplier_name}</h3>
        <button type="button" className={internalButtonClass} onClick={onClose}>Cerrar</button>
      </div>
      {error ? <ErrorBox message={error} /> : null}
      {entries === null && !error ? <Spinner label="Cargando historial…" /> : null}
      {entries && entries.length === 0 ? <EmptyBox message="Sin movimientos registrados." /> : null}
      {entries && entries.length > 0 ? (
        <ol className="space-y-2 text-sm">
          {entries.map((entry, index) => (
            <li key={`${entry.at}-${index}`} className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-bd-border/60 pb-2 last:border-0">
              <span className="font-semibold text-foreground">{SUPPLIER_OFFER_ACTION_LABELS[entry.action] ?? entry.action}</span>
              <span className="text-muted">{formatDateTime(entry.at)}</span>
              <span className="text-muted">{entry.actor || "Sistema"}</span>
              {historyDetail(entry) ? <span className="w-full text-xs text-muted">{historyDetail(entry)}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Offers
// ---------------------------------------------------------------------------

type Mode = { kind: "none" } | { kind: "create" } | { kind: "edit"; id: number } | { kind: "deactivate"; id: number } | { kind: "history"; id: number };

function OffersSection({
  canManage, canSeeCosts, companyId, suppliers, reloadKey,
}: {
  canManage: boolean;
  canSeeCosts: boolean;
  companyId: number | null;
  suppliers: SupplierRow[];
  reloadKey: number;
}) {
  const [offers, setOffers] = useState<SupplierOffer[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [supplier, setSupplier] = useState<string>("");
  const [product, setProduct] = useState<PickedProduct | null>(null);
  const [state, setState] = useState<"" | "active" | "expired" | "inactive">("");
  const [mode, setMode] = useState<Mode>({ kind: "none" });
  const [seededFromUrl, setSeededFromUrl] = useState(false);

  // `?supplier=` from the suppliers list. Read once, on the client.
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("supplier");
    const timer = window.setTimeout(() => {
      if (fromUrl && /^\d+$/.test(fromUrl)) setSupplier(fromUrl);
      setSeededFromUrl(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const load = useCallback(() => {
    fetchSupplierOffers({
      supplier: supplier ? Number(supplier) : "", product: product?.id ?? "", state,
    }, companyId).then(
      (rows) => { setOffers(rows); setError(null); },
      (err) => setError(message(err, "No se pudieron cargar las ofertas.")),
    );
  }, [supplier, product, state, companyId]);

  useEffect(() => {
    if (seededFromUrl) load();
  }, [load, seededFromUrl, reloadKey]);

  const showCostColumn = (offers ?? []).some(hasAgreedCost);
  const current = (id: number) => offers?.find((offer) => offer.id === id) ?? null;
  const done = () => { setMode({ kind: "none" }); load(); };

  const editing = mode.kind === "edit" ? current(mode.id) : null;
  const deactivating = mode.kind === "deactivate" ? current(mode.id) : null;
  const historyOf = mode.kind === "history" ? current(mode.id) : null;

  return (
    <Panel
      title="Ofertas de proveedores"
      description="Sólo una oferta vigente, con cantidad confirmada y unidades por vender, permite vender bajo pedido."
      action={canManage && mode.kind === "none" ? (
        <button type="button" className={internalPrimaryButtonClass} onClick={() => setMode({ kind: "create" })}>+ Nueva oferta</button>
      ) : null}
    >
      {canManage && mode.kind === "create" ? (
        <OfferForm offer={null} suppliers={suppliers} showCost={canSeeCosts} companyId={companyId} onSaved={done} onCancel={() => setMode({ kind: "none" })} />
      ) : null}
      {canManage && editing ? (
        <OfferForm
          key={editing.id}
          offer={editing}
          suppliers={suppliers}
          showCost={canSeeCosts && hasAgreedCost(editing)}
          companyId={companyId}
          onSaved={done}
          onCancel={() => setMode({ kind: "none" })}
        />
      ) : null}
      {canManage && deactivating ? (
        <DeactivateForm offer={deactivating} companyId={companyId} onDone={done} onCancel={() => setMode({ kind: "none" })} />
      ) : null}
      {historyOf ? <HistoryPanel offer={historyOf} companyId={companyId} onClose={() => setMode({ kind: "none" })} /> : null}

      <div className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,12rem)] md:items-end" role="group" aria-label="Filtrar ofertas">
        <label className="block text-xs text-muted">
          Proveedor
          <select value={supplier} onChange={(e) => setSupplier(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Todos los proveedores</option>
            {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <ProductPicker label="Producto" value={product} onChange={setProduct} companyId={companyId} />
        <label className="block text-xs text-muted">
          Estado
          <select value={state} onChange={(e) => setState(e.target.value as typeof state)} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Todos</option>
            <option value="active">Vigentes</option>
            <option value="expired">Vencidas</option>
            <option value="inactive">Desactivadas</option>
          </select>
        </label>
      </div>

      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {offers === null && !error ? <Spinner label="Cargando ofertas…" /> : null}
      {offers && offers.length === 0 ? <EmptyBox message="No hay ofertas que coincidan." /> : null}
      {offers && offers.length > 0 ? (
        <TableWrap>
          <thead>
            <tr className="border-b border-bd-border">
              <Th>Producto</Th><Th>Proveedor</Th><Th right>Precio de venta</Th>
              {showCostColumn ? <Th right>Costo acordado</Th> : null}
              <Th right>Confirmadas</Th><Th right>Comprometidas</Th><Th right>Quedan</Th>
              <Th>Vigencia</Th><Th right>Entrega</Th><Th>Estado</Th><Th right>Acciones</Th>
            </tr>
          </thead>
          <tbody>
            {offers.map((offer) => (
              <tr key={offer.id} className="border-b border-bd-border/60" data-testid={`offer-row-${offer.id}`}>
                <Td>
                  <span className="font-semibold">{offer.product_name}</span>
                  {offer.deactivated_reason ? <span className="block text-xs text-muted">Motivo: {offer.deactivated_reason}</span> : null}
                </Td>
                <Td muted>{offer.supplier_name}</Td>
                <Td right>{formatSoles(offer.commercial_price)}</Td>
                {showCostColumn ? (
                  <Td right>{hasAgreedCost(offer) ? (offer.agreed_cost ? formatSoles(offer.agreed_cost) : "Sin costo") : ""}</Td>
                ) : null}
                <Td right>{offer.confirmed_quantity === null ? "Sin confirmar" : offer.confirmed_quantity}</Td>
                <Td right>{offer.committed_quantity}</Td>
                <Td right>{offer.remaining ?? "—"}</Td>
                <Td muted>{formatDay(offer.valid_from)} – {formatDay(offer.valid_until)}</Td>
                <Td right>{offer.lead_time_days} {offer.lead_time_days === 1 ? "día" : "días"}</Td>
                <Td><OfferStateBadge state={offer.state} /></Td>
                <Td right>
                  <span className="inline-flex flex-wrap justify-end gap-2">
                    <button type="button" className={internalButtonClass} onClick={() => setMode({ kind: "history", id: offer.id })} aria-label={`Historial de ${offer.product_name} (${offer.supplier_name})`}>
                      Historial
                    </button>
                    {canManage ? (
                      <button type="button" className={internalButtonClass} onClick={() => setMode({ kind: "edit", id: offer.id })} aria-label={`Editar ${offer.product_name} (${offer.supplier_name})`}>
                        Editar
                      </button>
                    ) : null}
                    {canManage && offer.is_active ? (
                      <button type="button" className={internalButtonClass} onClick={() => setMode({ kind: "deactivate", id: offer.id })} aria-label={`Desactivar ${offer.product_name} (${offer.supplier_name})`}>
                        Desactivar
                      </button>
                    ) : null}
                  </span>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      ) : null}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Excel: template, preview, apply, export
// ---------------------------------------------------------------------------

const IMPORT_ACTION_LABELS: Record<string, string> = {
  create: "Nueva", update: "Cambia", no_change: "Sin cambios", error: "Error", skip: "Omitida",
};

function formatChange(field: SupplierImportChange, value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return field === "confirmed_quantity" || field === "available" ? "Sin confirmar" : "—";
  }
  if (field === "commercial_price" || field === "agreed_cost") return formatSoles(String(value));
  if (field === "valid_from" || field === "valid_until") return formatDay(String(value));
  if (field === "is_active") return value ? "Sí" : "No";
  return String(value);
}

export type RowChange = { field: SupplierImportChange; label: string; before: string; after: string };

/**
 * «Antes → después» for each field a row changes, from the server's `before`
 * block. A cost appears only when the server sent it in `before` (costs.view).
 */
export function rowDiff(row: WeeklyImportRow): RowChange[] {
  const before = row.data.before;
  if (!before || !row.data.changes) return [];
  const after = row.data as Record<string, unknown>;
  return row.data.changes
    .filter((field) => field !== "sale_mode" && (field !== "agreed_cost" || Object.prototype.hasOwnProperty.call(before, "agreed_cost")))
    .map((field) => {
      // An older server compared `confirmed_quantity`; its sold-out «after» was what was committed.
      const next = field === "confirmed_quantity" && row.data.sold_out ? before.committed_quantity : after[field];
      return {
        field,
        label: CHANGE_LABELS[field],
        before: formatChange(field, (before as Record<string, unknown>)[field]),
        after: formatChange(field, next),
      };
    });
}

function resultText(result: WeeklyImportResult): string {
  const parts = [`Ofertas actualizadas: ${result.created} nuevas, ${result.updated} modificadas, ${result.unchanged} sin cambios.`];
  if (result.sold_out) parts.push(`${result.sold_out} agotadas.`);
  if (result.prices_changed) parts.push(`${result.prices_changed} precios nuevos aceptados.`);
  if (result.prices_kept) parts.push(`Se mantuvo el precio actual de ${result.prices_kept} ofertas.`);
  if (result.mappings) parts.push(`${result.mappings} códigos de proveedor recordados.`);
  return parts.join(" ");
}

type Mapping = { row: number; supplierId: number; code: string; product: PickedProduct | null };

function ImportSection({
  canManage, companyId, onApplied, suppliers,
}: { canManage: boolean; companyId: number | null; onApplied: () => void; suppliers: SupplierRow[] }) {
  // INV-SNAPSHOT-01: a complete list of ONE supplier, and the two confirmations it needs to switch offers off.
  const [complete, setComplete] = useState(false);
  const [completeSupplier, setCompleteSupplier] = useState("");
  const [allIncluded, setAllIncluded] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<WeeklyImportPreview | null>(null);
  const [result, setResult] = useState<WeeklyImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState<PriceChangeRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [acceptPrices, setAcceptPrices] = useState(false);
  const [reapplied, setReapplied] = useState<AlreadyApplied | null>(null);
  const [confirmReapply, setConfirmReapply] = useState(false);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [mapNote, setMapNote] = useState<string | null>(null);
  // INV-SWITCH-01: asked per import; the server decides whether AI may be used.
  const [useAi, setUseAi] = useState(false);

  async function runPreview() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setRefused([]);
    setResult(null);
    setPreview(null);
    setAcceptPrices(false);
    setConfirmReapply(false);
    setReapplied(null);
    setMapping(null);
    try {
      setAllIncluded(false);
      setConfirmOff(false);
      const shown = await previewWeeklyImport(file, companyId, {
        useAi, completeSupplierId: complete && completeSupplier ? Number(completeSupplier) : null,
      });
      setPreview(shown);
      setReapplied(shown.already_applied ?? null);
    } catch (err) {
      setError(message(err, "No se pudo leer el archivo."));
    } finally {
      setBusy(false);
    }
  }

  async function apply(decision: PriceDecision | null) {
    if (!preview) return;
    setBusy(true);
    setError(null);
    setRefused([]);
    try {
      setResult(await applyWeeklyImport(preview.id, decision, companyId, {
        confirmReapply: Boolean(reapplied) && confirmReapply,
        snapshot: preview.snapshot ? { allMessagesIncluded: allIncluded, deactivations: preview.snapshot.deactivate_count } : undefined,
      }));
      setPreview(null);
      setFile(null);
      setMapNote(null);
      if (fileRef.current) fileRef.current.value = "";
      onApplied();
    } catch (err) {
      if (err instanceof WeeklyImportError && err.code === "stale") {
        // Nothing was written. The file is still chosen: preview it again.
        setPreview(null);
        setError(`${err.message} No se aplicó nada: pulsa «Previsualizar» otra vez.`);
      } else if (err instanceof WeeklyImportError && err.code === "already_applied") {
        // Found applied only now (another person applied the same file meanwhile): ask, then retry.
        setReapplied(err.alreadyApplied);
        setConfirmReapply(false);
        setError(err.message);
      } else if (err instanceof WeeklyImportError && err.code === "price_confirmation_required") {
        setRefused(err.priceChanges);
        setError(err.message);
      } else {
        setError(message(err, "No se pudo aplicar el archivo."));
      }
    } finally {
      setBusy(false);
    }
  }

  async function saveMapping() {
    if (!mapping?.product) return;
    setBusy(true);
    setError(null);
    try {
      const ref = await saveSupplierProductRef(mapping.supplierId, { supplier_code: mapping.code, product_id: mapping.product.id }, companyId);
      setMapNote(`El código «${ref.supplier_code}» del proveedor queda asociado a «${ref.product_name}». Se volvió a previsualizar el archivo.`);
      setMapping(null);
    } catch (err) {
      setError(message(err, "No se pudo guardar el código del proveedor."));
      setBusy(false);
      return;
    }
    setBusy(false);
    await runPreview();
  }

  const shown = preview ? preview.rows.filter((row) => row.action !== "no_change") : [];
  const summary = preview ? summaryOf(preview) : null;
  const priceRows = preview ? preview.rows.filter((row) => row.action === "update" && row.data.price_change) : [];

  return (
    <Panel
      title="Actualizar ofertas desde Excel"
      description="Descarga la plantilla, complétala con las ofertas de la semana y súbela: primero ves qué cambiaría, luego lo aplicas. Sólo cambian las ofertas que trae el archivo."
      action={(
        <span className="inline-flex flex-wrap gap-2">
          <a className={internalButtonClass} href={supplierOfferTemplateUrl(companyId)}>Descargar plantilla</a>
          <a className={internalButtonClass} href={supplierOfferExportUrl(companyId)}>Exportar ofertas</a>
        </span>
      )}
    >
      {canManage ? (
        <div className="flex flex-wrap items-end gap-3">
          <label className="block min-w-60 flex-1 text-xs text-muted">
            Archivo de ofertas (.xlsx)
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); setResult(null); setMapNote(null); }}
              className={`mt-1.5 ${internalInputClass}`}
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} disabled={busy} />
            Usar IA en esta importación
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={complete} disabled={busy}
              onChange={(e) => { setComplete(e.target.checked); setPreview(null); }} />
            Es la lista completa de un proveedor
          </label>
          {complete ? (
            <select aria-label="Proveedor de la lista completa" value={completeSupplier} disabled={busy}
              onChange={(e) => { setCompleteSupplier(e.target.value); setPreview(null); }} className={internalInputClass}>
              <option value="">Elige el proveedor…</option>
              {suppliers.map((supplier) => <option key={supplier.id} value={String(supplier.id)}>{supplier.name}</option>)}
            </select>
          ) : null}
          <button type="button" className={internalButtonClass} onClick={() => void runPreview()} disabled={!file || busy || (complete && !completeSupplier)}>
            {busy && !preview ? "Leyendo…" : "Previsualizar"}
          </button>
        </div>
      ) : (
        <p className="text-sm text-muted">Puedes descargar la plantilla y exportar las ofertas. Subir cambios requiere permiso para gestionar productos.</p>
      )}

      {error ? <div className="mt-4"><ErrorBox message={error} /></div> : null}
      {refused.length ? (
        <ul className="mt-2 list-disc pl-5 text-xs text-danger" aria-label="Precios que el servidor pidió confirmar">
          {refused.map((row) => (
            <li key={row.row_number}>Fila {row.row_number}: {row.product_name} · {formatSoles(row.before)} → {formatSoles(row.after)}</li>
          ))}
        </ul>
      ) : null}
      {mapNote ? <p role="status" className="mt-4 text-sm text-success">{mapNote}</p> : null}
      {result ? (
        <p role="status" className="mt-4 text-sm text-success">{resultText(result)}</p>
      ) : null}

      {preview && aiImportNotice(preview.ai) ? (
        <p role="status" data-testid="import-ai-notice" className="mt-4 text-xs text-muted">{aiImportNotice(preview.ai)}</p>
      ) : null}
      {preview && summary ? (
        <div className="mt-5 space-y-4" data-testid="supplier-import-preview">
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-6">
            {[
              ["Filas", preview.rows_total], ["Nuevas", summary.new], ["Cambian", summary.updated],
              ["Agotadas", summary.sold_out], ["Sin cambios", summary.unchanged], ["Con error", summary.errors],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-bd-border bg-background p-3">
                <dt className="text-xs text-muted">{label}</dt>
                <dd className={`text-base font-semibold ${label === "Con error" && Number(value) > 0 ? "text-danger" : "text-foreground"}`}>{value}</dd>
              </div>
            ))}
          </dl>
          {preview.cost_column_ignored ? (
            <p role="note" className="rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-warning">
              El archivo trae una columna de costo y no tienes permiso para ver costos: esa columna se ignoró. Los costos existentes no cambian.
            </p>
          ) : null}
          {shown.length ? (
            <TableWrap>
              <thead>
                <tr className="border-b border-bd-border">
                  <Th right>Fila</Th><Th>Acción</Th><Th>Proveedor</Th><Th>Producto</Th><Th right>Precio</Th>
                  <Th right>Disponible</Th><Th>Vigencia</Th><Th>Antes → después</Th><Th>Observaciones</Th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => {
                  const diff = rowDiff(row);
                  const mappable = row.action === "error" ? row.data.mappable : undefined;
                  const choosing = mapping?.row === row.row_number ? mapping : null;
                  return (
                    <tr key={row.row_number} className="border-b border-bd-border/60" data-testid={`import-row-${row.row_number}`}>
                      <Td right>{row.row_number}</Td>
                      <Td>
                        <span className={row.action === "error" ? "font-semibold text-danger" : row.data.sold_out ? "font-semibold text-warning" : ""}>
                          {row.data.sold_out ? "Agotada" : IMPORT_ACTION_LABELS[row.action] ?? row.action}
                        </span>
                      </Td>
                      <Td muted>{row.data.supplier || "—"}</Td>
                      <Td>{row.data.product_name || "—"}</Td>
                      <Td right>{row.data.commercial_price ? formatSoles(row.data.commercial_price) : "—"}</Td>
                      <Td right>{row.data.confirmed_quantity ?? "—"}</Td>
                      <Td muted>
                        {row.data.valid_from ? formatDay(String(row.data.valid_from)) : "—"} – {row.data.valid_until ? formatDay(String(row.data.valid_until)) : "—"}
                      </Td>
                      <Td muted>
                        {diff.length ? (
                          <ul className="space-y-0.5" aria-label={`Cambios de la fila ${row.row_number}`}>
                            {diff.map((change) => (
                              <li key={change.field}>
                                <span className="text-foreground">{change.label}:</span> {change.before} → <span className="font-semibold text-foreground">{change.after}</span>
                              </li>
                            ))}
                          </ul>
                        ) : row.action === "create" ? "Oferta nueva" : "—"}
                      </Td>
                      <Td muted>
                        {row.errors.map((text) => <span key={text} className="block text-danger">{text}</span>)}
                        {row.warnings.map((text) => <span key={text} className="block text-warning">{text}</span>)}
                        {row.data.map_supplier_code ? (
                          <span className="block">Se recordará el código del proveedor «{row.data.map_supplier_code}».</span>
                        ) : null}
                        {canManage && mappable && !choosing ? (
                          <button
                            type="button"
                            className={`mt-1 ${internalButtonClass}`}
                            onClick={() => setMapping({ row: row.row_number, supplierId: mappable.supplier_id, code: mappable.supplier_code, product: null })}
                          >
                            Elegir variante
                          </button>
                        ) : null}
                        {choosing ? (
                          <div className="mt-2 space-y-2" role="group" aria-label={`Variante para el código ${choosing.code}`}>
                            <ProductPicker
                              label={`Variante para «${choosing.code}»`}
                              value={choosing.product}
                              onChange={(product) => setMapping({ ...choosing, product })}
                              companyId={companyId}
                            />
                            <span className="inline-flex gap-2">
                              <button type="button" className={internalButtonClass} onClick={() => setMapping(null)} disabled={busy}>Cancelar</button>
                              <button type="button" className={internalPrimaryButtonClass} onClick={() => void saveMapping()} disabled={busy || !choosing.product}>
                                Guardar código
                              </button>
                            </span>
                          </div>
                        ) : null}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </TableWrap>
          ) : (
            <p className="text-sm text-muted">Ninguna fila cambia algo.</p>
          )}
          {priceRows.length && preview.rows_error === 0 ? (
            <fieldset className="rounded-lg border border-warning-border bg-warning-surface p-3 text-sm" aria-label="Precios comerciales que cambian">
              <legend className="px-1 text-xs font-semibold text-warning">El archivo cambia lo que paga el cliente</legend>
              <ul className="mb-2 space-y-0.5 text-foreground">
                {priceRows.map((row) => (
                  <li key={row.row_number}>
                    Fila {row.row_number}: {row.data.product_name} · {row.data.supplier} · {formatSoles(row.data.price_change!.before)} → <span className="font-semibold">{formatSoles(row.data.price_change!.after)}</span>
                  </li>
                ))}
              </ul>
              <label className="flex items-center gap-2 text-foreground">
                <input type="checkbox" checked={acceptPrices} onChange={(e) => setAcceptPrices(e.target.checked)} />
                Acepto los precios nuevos de {priceRows.length === 1 ? "esta oferta" : `estas ${priceRows.length} ofertas`}
              </label>
              <p className="mt-1 text-xs text-muted">Sin aceptarlos puedes aplicar lo demás y mantener los precios actuales.</p>
            </fieldset>
          ) : null}
          {reapplied ? (
            <fieldset className="rounded-lg border border-warning-border bg-warning-surface p-3 text-sm" aria-label="Archivo ya aplicado">
              <label className="flex items-center gap-2 text-foreground">
                <input type="checkbox" checked={confirmReapply} onChange={(e) => setConfirmReapply(e.target.checked)} />
                Este archivo ya se aplicó el {reapplied.applied_at ? formatDateTime(reapplied.applied_at) : "—"}; aplicarlo otra vez vuelve a fijar lo disponible.
              </label>
              <p className="mt-1 text-xs text-muted">Lo vendido desde entonces no se descuenta: cada oferta vuelve a quedar con la cantidad del archivo.</p>
            </fieldset>
          ) : null}
          {preview.snapshot ? (
            <fieldset data-testid="snapshot-absent" className="rounded-lg border border-warning-border bg-warning-surface p-3 text-sm"
              aria-label="Lista completa: ofertas ausentes">
              <p className="font-medium text-foreground">
                Lista completa de {preview.snapshot.supplier_name}: se desactivarán {preview.snapshot.deactivate_count} ofertas vigentes ausentes
                {preview.snapshot.with_commitments ? ` (${preview.snapshot.with_commitments} con ventas comprometidas)` : ""}.
                Las programadas no cambian.
              </p>
              {preview.snapshot.looks_truncated ? (
                <p role="alert" className="mt-1 text-xs text-danger">
                  Desactivaría la mayoría de las ofertas vigentes de este proveedor: la lista parece incompleta y no se
                  puede aplicar como completa. Revisa que estén todos los mensajes, o cárgala como parcial.
                </p>
              ) : null}
              {preview.snapshot.absent.length ? (
                <ul className="mt-2 list-disc pl-5 text-xs">
                  {preview.snapshot.absent.map((row) => (
                    <li key={row.offer_id} className={row.committed ? "text-warning" : undefined}>
                      <span>{row.product_name}</span>
                      {row.committed ? ` — ${row.committed} vendidas: siguen su curso; deja de venderse lo que queda` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
              <label className="mt-2 flex items-center gap-2 text-foreground">
                <input type="checkbox" checked={allIncluded} onChange={(e) => setAllIncluded(e.target.checked)} />
                La lista incluye todos los mensajes y archivos del proveedor
              </label>
              <label className="mt-1 flex items-center gap-2 text-foreground">
                <input type="checkbox" checked={confirmOff} onChange={(e) => setConfirmOff(e.target.checked)} />
                Confirmo desactivar {preview.snapshot.deactivate_count} ofertas ausentes
              </label>
            </fieldset>
          ) : null}
          <div className="flex flex-wrap items-center justify-end gap-3">
            {preview.rows_error > 0 ? (
              <p className="text-xs text-danger">Corrige las filas con error y vuelve a subir el archivo para aplicarlo.</p>
            ) : null}
            <button type="button" className={internalButtonClass} onClick={() => setPreview(null)} disabled={busy}>Descartar</button>
            {priceRows.length ? (
              <button type="button" className={internalButtonClass} onClick={() => void apply("keep")} disabled={busy || preview.rows_error > 0 || (Boolean(reapplied) && !confirmReapply) || (Boolean(preview.snapshot) && (!(allIncluded && confirmOff) || Boolean(preview.snapshot?.looks_truncated)))}>
                Aplicar sin cambiar precios
              </button>
            ) : null}
            <button
              type="button"
              className={internalPrimaryButtonClass}
              onClick={() => void apply(priceRows.length ? "accept" : null)}
              disabled={busy || preview.rows_error > 0 || (priceRows.length > 0 && !acceptPrices) || (Boolean(reapplied) && !confirmReapply)
                || (Boolean(preview.snapshot) && (!(allIncluded && confirmOff) || Boolean(preview.snapshot?.looks_truncated)))}
            >
              {busy ? "Aplicando…" : "Aplicar cambios"}
            </button>
          </div>
        </div>
      ) : null}
    </Panel>
  );
}

export function SupplierOffersManager({
  canManage, canSeeCosts, companyId,
}: { canManage: boolean; canSeeCosts: boolean; companyId: number | null }) {
  const [suppliers, setSuppliers] = useState<SupplierRow[]>([]);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    fetchSuppliers({}, companyId).then((page) => setSuppliers(page.results), () => { /* the list still works without names */ });
  }, [companyId]);

  return (
    <div className="space-y-6">
      <OffersSection
        canManage={canManage}
        canSeeCosts={canSeeCosts}
        companyId={companyId}
        suppliers={suppliers}
        reloadKey={reloadKey}
      />
      <ImportSection canManage={canManage} companyId={companyId} suppliers={suppliers} onApplied={() => setReloadKey((key) => key + 1)} />
    </div>
  );
}
