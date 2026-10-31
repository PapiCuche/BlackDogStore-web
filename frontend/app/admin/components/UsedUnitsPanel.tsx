"use client";

/**
 * Inventario › Seminuevos — DEVICE-COMMERCE-01 · DC2, INV-RECEIVE-PRICE-03.
 *
 * The used devices, one row each, with what the shop publishes about them. Three authorities that never imply one
 * another, each checked again by the server:
 *
 *   · `inventory.adjust`  the device's data — battery, cosmetic grade, details for the buyer, photos;
 *   · `products.manage`   its SALE PRICE (the catalogue's decision, not the stockroom's). The price lives on the
 *                         device's ficha, so it also needs `inventory.adjust`; without both the field is read only
 *                         and the screen says why;
 *   · `costs.view`        its acquisition cost, shown apart from the price and never edited here.
 *
 * A used device has no fallback price: without its own it is sold NOWHERE — neither at the till nor online — until
 * somebody with authority prices it. Setting a price publishes nothing.
 *
 * A battery nobody measured is «no informado», never a number. Staff see the full identifiers here, as on
 * «Equipos»; the shop only ever gets a hint.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  deleteUnitPhoto, fetchStockUnits, unitWebReasonText, updateStockUnitListing, uploadUnitPhoto,
  type StockUnit, type StockUnitPage, type UnitListingWrite,
} from "@/app/lib/stock-units";
import { fetchCosmeticGrades, type CosmeticGradeRow } from "../lib/internal-api";
import { PENDING_PRICE_TEXT } from "../lib/inventory-receipts";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

export const batteryCell = (value: number | null | undefined) =>
  value === null || value === undefined ? "no informado" : `${value}%`;

const PRICE = /^\d{1,8}(\.\d{1,2})?$/;

export { PENDING_PRICE_TEXT };

/** Whether a stored price can be charged: the server's rule (greater than zero). */
export const hasUsablePrice = (price: string | null | undefined) => Boolean(price) && Number(price) > 0;

const STATUS_TONE: Record<StockUnit["status"], string> = {
  available: "border-success-border text-success",
  reserved: "border-warning-border text-warning",
  sold: "border-bd-border text-muted",
  written_off: "border-danger-border text-danger",
};

type ListingDraft = { battery: string; grade: string; notes: string; price: string };

const toDraft = (unit: StockUnit): ListingDraft => ({
  battery: unit.battery_health === null || unit.battery_health === undefined ? "" : String(unit.battery_health),
  grade: unit.cosmetic_grade ? String(unit.cosmetic_grade) : "",
  notes: unit.public_notes ?? "",
  price: hasUsablePrice(unit.sale_price) ? unit.sale_price! : "",
});

/** What a used device still lacks to be published well. Recommended, not required by the server. */
export function missingDetails(unit: StockUnit): string[] {
  const missing: string[] = [];
  if (unit.battery_health === null || unit.battery_health === undefined) missing.push("la batería medida");
  if (!unit.cosmetic_grade) missing.push("el estado estético");
  if (!unit.photos || unit.photos.length === 0) missing.push("fotos del equipo");
  return missing;
}

/** «Se ofrece» or «No se ofrece: …», from the server's reading of the list row. */
export function webStatusText(unit: StockUnit): string | null {
  if (!unit.web) return null;
  if (unit.web.offered) return "Se ofrece en la tienda en línea.";
  const reasons = unit.web.reasons.map(unitWebReasonText);
  return `No se ofrece en la tienda en línea: ${reasons.join("; ")}.`;
}

/** Edit one device's listing. Battery may be left blank: blank is «not stated», sent as null. */
/** Why the price is read only for this person, in words that send them to the right permission. */
export function priceLockedReason({ sold, canManageProducts }: { sold: boolean; canManageProducts: boolean }): string {
  if (sold) return "Este equipo ya se vendió: su precio es el de esa venta y no se cambia.";
  if (canManageProducts) {
    return "El precio vive en la ficha del equipo, que también pide el permiso de ajustar inventario: pídelo a quien administra los permisos.";
  }
  return "Sólo quien gestiona productos puede fijar o cambiar el precio de venta.";
}

export function UnitListingDialog({
  unit, grades, canEdit, canManagePrice, canManageProducts = false, canSeeCost, onClose, onSaved,
}: {
  unit: StockUnit;
  grades: CosmeticGradeRow[];
  /** `inventory.adjust`: the device's data and photos. */
  canEdit: boolean;
  /** `products.manage` + `inventory.adjust`: its sale price. */
  canManagePrice: boolean;
  /** `products.manage` alone: only changes WHY the price is read only. */
  canManageProducts?: boolean;
  canSeeCost: boolean;
  onClose: () => void;
  onSaved: (unit: StockUnit) => void;
}) {
  const titleId = useId();
  const pendingId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const initial = useRef<ListingDraft>(toDraft(unit));
  const [draft, setDraft] = useState<ListingDraft>(() => toDraft(unit));
  const [photos, setPhotos] = useState(unit.photos ?? []);
  const [file, setFile] = useState<File | null>(null);
  const [alt, setAlt] = useState("");
  const [errors, setErrors] = useState<Partial<Record<keyof ListingDraft, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A price change waits for an explicit «Sí»: it is what the till charges and the shop shows.
  const [confirming, setConfirming] = useState<UnitListingWrite | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);

  // The question appears below the fields: the cursor goes to its answer (on a phone it is out of sight otherwise).
  useEffect(() => {
    if (!confirming) return;
    confirmButton.current?.scrollIntoView?.({ block: "center" });
    confirmButton.current?.focus();
  }, [confirming]);

  const sold = unit.status === "sold";
  const priceEditable = canManagePrice && canEdit && !sold;
  const canSave = canEdit || priceEditable;
  const priced = hasUsablePrice(unit.sale_price);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    first.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { if (!busy) onClose(); return; }
      if (event.key !== "Tab" || !panel.current) return;
      const stops = [...panel.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])",
      )];
      if (stops.length === 0) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
      event.preventDefault();
      stops[next].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  /** Only what changed, and the price only for whoever may set it: the server refuses a price from anybody else. */
  function validate(): UnitListingWrite | null {
    const next: Partial<Record<keyof ListingDraft, string>> = {};
    const battery = draft.battery.trim();
    if (battery && (!/^\d+$/.test(battery) || Number(battery) > 100)) next.battery = "Un número entero de 0 a 100, o vacío si no se midió.";
    const price = draft.price.trim();
    if (priceEditable && price && (!PRICE.test(price) || Number(price) <= 0)) {
      next.price = "Un importe mayor que cero, como 2599 o 2599.90. Vacío deja el equipo sin precio.";
    }
    setErrors(next);
    if (Object.keys(next).length) return null;
    const before = initial.current;
    const body: UnitListingWrite = {};
    if (canEdit) {
      if (battery !== before.battery.trim()) body.battery_health = battery ? Number(battery) : null;
      if (draft.grade !== before.grade) body.cosmetic_grade = draft.grade ? Number(draft.grade) : null;
      if (draft.notes !== before.notes) body.public_notes = draft.notes;
    }
    if (priceEditable && price !== before.price.trim()) body.sale_price = price || null;
    return body;
  }

  async function write(body: UnitListingWrite) {
    setBusy(true);
    setError(null);
    try {
      const saved = await updateStockUnitListing(unit.id, body);
      onSaved({ ...unit, ...saved, photos });
    } catch (err) {
      setConfirming(null);
      setError(message(err, "No se pudo guardar el equipo."));
    } finally {
      setBusy(false);
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const body = validate();
    if (!body) return;
    if (Object.keys(body).length === 0) { onClose(); return; }
    if ("sale_price" in body) { setConfirming(body); return; }
    await write(body);
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const photo = await uploadUnitPhoto(unit.id, file, alt.trim());
      setPhotos((current) => [...current, photo]);
      setFile(null);
      setAlt("");
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      setError(message(err, "No se pudo subir la foto."));
    } finally {
      setBusy(false);
    }
  }

  async function removePhoto(photoId: number) {
    setBusy(true);
    setError(null);
    try {
      await deleteUnitPhoto(unit.id, photoId);
      setPhotos((current) => current.filter((p) => p.id !== photoId));
    } catch (err) {
      setError(message(err, "No se pudo eliminar la foto."));
    } finally {
      setBusy(false);
    }
  }

  const web = webStatusText(unit);
  const missing = missingDetails({ ...unit, photos });
  const nextPrice = confirming?.sale_price ?? null;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-t-xl border border-bd-border bg-surface p-5 shadow-xl sm:rounded-xl sm:p-6"
      >
        <h2 id={titleId} className="break-words text-lg font-bold text-foreground">{unit.product_name}</h2>
        <p className="mt-1 break-all font-mono text-xs text-muted">
          {unit.serial_number}{unit.imei ? ` · ${unit.imei}` : ""}
        </p>
        <p className="mt-1 text-xs text-muted">{unit.status_label} · {unit.branch_name}</p>

        <form onSubmit={save} className="mt-5 space-y-5" noValidate>
          <fieldset disabled={!canEdit || busy} className="grid gap-3 rounded-lg border border-bd-border p-3 sm:grid-cols-2 sm:p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-widest text-muted">Datos del equipo</legend>
            <label className="block text-xs text-muted">
              Batería (%)
              <input
                ref={first}
                inputMode="numeric"
                value={draft.battery}
                onChange={(e) => setDraft({ ...draft, battery: e.target.value })}
                placeholder="No informado"
                aria-invalid={errors.battery ? true : undefined}
                className={`mt-1.5 ${internalInputClass}`}
              />
              {errors.battery ? <span role="alert" className="mt-1 block text-danger">{errors.battery}</span> : null}
            </label>
            <label className="block text-xs text-muted">
              Estado estético
              <select value={draft.grade} onChange={(e) => setDraft({ ...draft, grade: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                <option value="">Sin grado</option>
                {grades.filter((g) => g.is_active || String(g.id) === draft.grade).map((grade) => (
                  <option key={grade.id} value={grade.id}>{grade.label}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-muted sm:col-span-2">
              Detalles para el comprador
              <textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} rows={3} className={`mt-1.5 ${internalInputClass}`} />
            </label>

            <div className="sm:col-span-2">
              <p className="text-xs font-semibold text-muted">Fotos del equipo ({photos.length})</p>
              {photos.length > 0 ? (
                <ul className="mt-2 flex flex-wrap gap-3">
                  {photos.map((photo, index) => (
                    <li key={photo.id} className="w-24">
                      {/* eslint-disable-next-line @next/next/no-img-element -- staff preview of an uploaded file */}
                      <img src={photo.url} alt={photo.alt_text || `Foto ${index + 1}`} className="h-24 w-24 rounded-lg border border-bd-border bg-background object-contain" />
                      {canEdit ? (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void removePhoto(photo.id)}
                          aria-label={`Eliminar foto ${index + 1}`}
                          className="mt-1 w-full rounded-md border border-danger-border px-2 py-1 text-xs font-semibold text-danger disabled:opacity-40"
                        >
                          Eliminar
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
              {canEdit ? (
                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <label className="block min-w-40 flex-1 text-xs text-muted">
                    Nueva foto
                    <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-1.5 block w-full text-sm text-foreground" />
                  </label>
                  <label className="block min-w-40 flex-1 text-xs text-muted">
                    Texto alternativo
                    <input value={alt} onChange={(e) => setAlt(e.target.value)} className={`mt-1.5 ${internalInputClass}`} />
                  </label>
                  <button type="button" className={internalButtonClass} disabled={busy || !file} onClick={() => void upload()}>Subir foto</button>
                </div>
              ) : (
                <p className="mt-2 text-xs text-muted">Cambiar estos datos y las fotos necesita el permiso de ajustar inventario.</p>
              )}
            </div>
          </fieldset>

          <section aria-label="Información comercial" className="space-y-3 rounded-lg border border-bd-border p-3 sm:p-4">
            <h3 className="text-xs font-semibold uppercase tracking-widest text-muted">Información comercial</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {canSeeCost && unit.cost !== undefined ? (
                <div className="text-xs text-muted">
                  <p>Costo de adquisición (interno)</p>
                  <p className="mt-1.5 py-2.5 text-sm text-foreground">{unit.cost === null ? "Sin costo registrado" : formatSoles(unit.cost)}</p>
                  <p>Lo que pagó la tienda. Nunca decide el precio.</p>
                </div>
              ) : null}
              {priceEditable ? (
                <label className="block text-xs text-muted">
                  Precio de venta (S/)
                  <input
                    inputMode="decimal"
                    value={draft.price}
                    disabled={busy}
                    onChange={(e) => { setDraft({ ...draft, price: e.target.value }); setConfirming(null); }}
                    placeholder="Sin precio"
                    aria-invalid={errors.price ? true : undefined}
                    aria-describedby={!draft.price.trim() ? pendingId : undefined}
                    className={`mt-1.5 ${internalInputClass}`}
                  />
                  {errors.price ? <span role="alert" className="mt-1 block text-danger">{errors.price}</span> : null}
                  <span className="mt-1 block">Lo que se cobra en caja y se muestra en línea por ESTE equipo.</span>
                </label>
              ) : (
                <div className="text-xs text-muted">
                  <p>Precio de venta</p>
                  <p className="mt-1.5 py-2.5 text-sm text-foreground">{priced ? formatSoles(unit.sale_price!) : "Sin precio"}</p>
                  <p>{priceLockedReason({ sold, canManageProducts })}</p>
                </div>
              )}
            </div>
            {!sold && !(priceEditable ? draft.price.trim() : priced) ? (
              <p id={pendingId} role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-foreground">
                {PENDING_PRICE_TEXT}
              </p>
            ) : null}
            {web ? <p className={`text-xs ${unit.web?.offered ? "text-success" : "text-muted"}`}>{web}</p> : null}
            {missing.length > 0 && !sold ? (
              <p className="text-xs text-muted">Para publicarlo completo se recomienda añadir {missing.join(", ")}.</p>
            ) : null}
            <p className="text-xs text-muted">Fijar el precio no publica nada: publicar se decide en «Publicación e-commerce».</p>
          </section>

          {confirming ? (
            <div role="alertdialog" aria-label="Confirmar el precio de venta" className="space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3 text-sm text-foreground">
              <p>
                {nextPrice
                  ? `Vas a ${priced ? `cambiar el precio de venta de ${formatSoles(unit.sale_price!)} a` : "fijar el precio de venta en"} ${formatSoles(nextPrice)}. Es lo que se cobrará en caja y se mostrará en línea por este equipo.`
                  : "Vas a quitar el precio de venta: el equipo dejará de venderse en caja y de ofrecerse en línea hasta que tenga otro."}
              </p>
              <div className="flex flex-wrap gap-2">
                <button ref={confirmButton} type="button" className={internalPrimaryButtonClass} disabled={busy} onClick={() => void write(confirming)}>
                  {busy ? "Guardando…" : nextPrice ? "Sí, guardar este precio" : "Sí, quitar el precio"}
                </button>
                <button type="button" className={internalButtonClass} disabled={busy} onClick={() => setConfirming(null)}>Revisar</button>
              </div>
            </div>
          ) : null}

          {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}

          <div className="flex justify-end gap-2">
            <button type="button" className={internalButtonClass} onClick={onClose} disabled={busy}>{canSave ? "Cancelar" : "Cerrar"}</button>
            {canSave && !confirming ? <button type="submit" className={internalPrimaryButtonClass} disabled={busy}>{busy ? "Guardando…" : "Guardar"}</button> : null}
          </div>
        </form>
      </div>
    </div>
  );
}

export function UsedUnitsPanel({
  branch, canEdit, canManagePrice = false, canManageProducts = false, canSeeCost, initialPriced = "", initialProduct = "",
}: {
  branch: number | "all" | undefined;
  /** `inventory.adjust`: the device's data and photos. The server checks it again on every change. */
  canEdit: boolean;
  /** `products.manage` AND `inventory.adjust`: its sale price. The server checks both again. */
  canManagePrice?: boolean;
  /** `products.manage` alone: only changes how a read-only price is explained. */
  canManageProducts?: boolean;
  /** `costs.view`: the server only sends the cost to whoever has it. */
  canSeeCost: boolean;
  /** «Sin precio» from a link (the reception's next step). */
  initialPriced?: "" | "yes" | "no";
  /** One article from a link: its id. */
  initialProduct?: string;
}) {
  const [page, setPage] = useState<StockUnitPage | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const [status, setStatus] = useState("");
  const [priced, setPriced] = useState<"" | "yes" | "no">(initialPriced);
  const [product, setProduct] = useState(initialProduct);
  const [grades, setGrades] = useState<CosmeticGradeRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<StockUnit | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchStockUnits({ branch, line: "used", search: applied, status, priced, product, page: pageNumber }).then(
      (loaded) => { if (!cancelled) { setPage(loaded); setError(null); } },
      (err) => { if (!cancelled) setError(message(err, "No se pudieron cargar los equipos.")); },
    );
    return () => { cancelled = true; };
  }, [branch, applied, status, priced, product, pageNumber, reloadKey]);

  useEffect(() => {
    fetchCosmeticGrades().then(setGrades, () => { /* the editor still works with «Sin grado» */ });
  }, []);

  const gradeLabel = useCallback(
    (unit: StockUnit) => unit.cosmetic_grade_label || grades.find((g) => g.id === unit.cosmetic_grade)?.label || "—",
    [grades],
  );

  const totalPages = page ? Math.max(1, Math.ceil(page.count / page.page_size)) : 1;
  const productName = product ? page?.results.find((u) => String(u.product_id) === product)?.product_name : undefined;

  return (
    <Panel
      title="Equipos seminuevos"
      description="Cada fila es un equipo. Su precio de venta es suyo: sin él no se vende en caja ni se ofrece en línea. La batería, el estado y las fotos completan lo que ve el comprador."
    >
      <form
        className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_12rem_12rem_auto]"
        onSubmit={(e) => { e.preventDefault(); setPageNumber(1); setApplied(search.trim()); }}
      >
        <label className="block text-xs text-muted">
          Serie o IMEI
          <input value={search} onChange={(e) => setSearch(e.target.value)} spellCheck={false} className={`mt-1.5 font-mono ${internalInputClass}`} />
        </label>
        <label className="block text-xs text-muted">
          Estado
          <select value={status} onChange={(e) => { setStatus(e.target.value); setPageNumber(1); }} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Todos</option>
            {(page?.statuses ?? []).map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <label className="block text-xs text-muted">
          Precio de venta
          <select value={priced} onChange={(e) => { setPriced(e.target.value as "" | "yes" | "no"); setPageNumber(1); }} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Todos</option>
            <option value="no">Sin precio</option>
            <option value="yes">Con precio</option>
          </select>
        </label>
        <div className="flex items-end"><button type="submit" className={internalButtonClass}>Filtrar</button></div>
      </form>

      {product ? (
        <p className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted" role="status">
          <span>Sólo los equipos de {productName ? `«${productName}»` : "un artículo"}.</span>
          <button type="button" className="font-semibold text-foreground underline" onClick={() => { setProduct(""); setPageNumber(1); }}>Ver todos</button>
        </p>
      ) : null}
      {!canManagePrice ? (
        <p className="mb-4 text-xs text-muted">
          {canManageProducts
            ? "Puedes gestionar productos, pero el precio de un equipo vive en su ficha de inventario: cambiarlo también pide el permiso de ajustar inventario."
            : "Tu usuario puede ver los precios, no cambiarlos: fijar el precio de venta de un equipo es de quien gestiona productos."}
        </p>
      ) : null}

      {notice ? <p role="status" className="mb-4 rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}
      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {page === null && !error ? <Spinner label="Cargando equipos…" /> : null}
      {page && page.results.length === 0 ? (
        <EmptyBox message={priced === "no"
          ? "No hay equipos seminuevos sin precio que coincidan."
          : "No hay equipos seminuevos que coincidan. Aparecen aquí los equipos de productos de la línea de seminuevos."}
        />
      ) : null}

      {page && page.results.length > 0 ? (
        <>
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Producto</Th><Th>Serie</Th><Th>IMEI</Th><Th>Batería</Th><Th>Estado estético</Th>
                <Th right>Precio de venta</Th><Th>Estado</Th><Th>En línea</Th><Th right>Fotos</Th><Th right>Acciones</Th>
              </tr>
            </thead>
            <tbody>
              {page.results.map((unit) => (
                <tr key={unit.id} className="border-b border-bd-border/60">
                  <Td>{unit.product_name}</Td>
                  <Td><span className="font-mono">{unit.serial_number}</span></Td>
                  <Td muted={!unit.imei}>
                    {unit.imei ? <span className="font-mono">{unit.imei}</span> : "—"}
                    {unit.imei2 ? <span className="block font-mono text-xs text-muted">{unit.imei2}</span> : null}
                  </Td>
                  <Td muted={unit.battery_health === null || unit.battery_health === undefined}>{batteryCell(unit.battery_health)}</Td>
                  <Td muted>{gradeLabel(unit)}</Td>
                  <Td right>
                    {hasUsablePrice(unit.sale_price)
                      ? formatSoles(unit.sale_price!)
                      : <span className="text-warning">Sin precio (pendiente)</span>}
                  </Td>
                  <Td>
                    <span className={`rounded-full border px-2.5 py-1 text-[11px] ${STATUS_TONE[unit.status]}`}>{unit.status_label}</span>
                  </Td>
                  <Td muted={!unit.web?.offered}>
                    {unit.web
                      ? (unit.web.offered
                        ? <span className="text-success">Se ofrece</span>
                        : <span title={webStatusText(unit) ?? undefined}>No · {unitWebReasonText(String(unit.web.reasons[0] ?? ""))}</span>)
                      : "—"}
                  </Td>
                  <Td right>{unit.photos?.length ?? 0}</Td>
                  <Td right>
                    <button
                      type="button"
                      className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25"
                      onClick={() => { setEditing(unit); setNotice(null); }}
                      aria-label={`${canEdit || canManagePrice ? "Editar" : "Ver"} ${unit.serial_number}`}
                    >
                      {canEdit || canManagePrice ? "Editar" : "Ver"}
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          <div className="mt-4 flex items-center justify-between">
            <p className="text-xs text-muted">{page.count === 1 ? "1 equipo" : `${page.count} equipos`} · página {page.page} de {totalPages}</p>
            <div className="flex gap-2">
              <button type="button" className={internalButtonClass} disabled={pageNumber <= 1} onClick={() => setPageNumber((n) => Math.max(1, n - 1))}>Anterior</button>
              <button type="button" className={internalButtonClass} disabled={pageNumber >= totalPages} onClick={() => setPageNumber((n) => n + 1)}>Siguiente</button>
            </div>
          </div>
        </>
      ) : null}

      {editing ? (
        <UnitListingDialog
          unit={editing}
          grades={grades}
          canEdit={canEdit}
          canManagePrice={canManagePrice}
          canManageProducts={canManageProducts}
          canSeeCost={canSeeCost}
          onClose={() => { setEditing(null); setReloadKey((k) => k + 1); }}
          onSaved={() => { setEditing(null); setNotice("Equipo guardado."); setReloadKey((k) => k + 1); }}
        />
      ) : null}
    </Panel>
  );
}
