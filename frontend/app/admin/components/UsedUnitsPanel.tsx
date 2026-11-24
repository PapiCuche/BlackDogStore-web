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
 * USED-READY-01 — the shop sells THIS device, so it is offered online only when it is available, priced, its
 * inspection is APPROVED (`inventory.units.inspect`), it is itself PUBLISHED (`products.manage`) and it has at least
 * ONE real photo — UNIT-PHOTO-02: confirmed as THIS device by an inspector (`UnitPhotoManager`). The till sells it
 * once approved. Warranty (days; empty = the company's policy), functional state,
 * defects and accessories are what the buyer reads on its card. Every device enters pending and unpublished.
 *
 * A battery nobody measured is «no informado», never a number. Staff see the full identifiers here, as on
 * «Equipos»; the shop only ever gets a hint.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  FUNCTIONAL_STATE_TEXT, INSPECTION_TEXT, fetchStockUnits, inspectStockUnit, publicPhotoCount, setStockUnitPublication,
  unitWebReasonText, updateStockUnitListing,
  type FunctionalState, type StockUnit, type StockUnitPage, type UnitInspection, type UnitListingWrite,
} from "@/app/lib/stock-units";
import { unitIdentifier } from "@/app/lib/unit-identifier";
import { fetchCosmeticGrades, type CosmeticGradeRow } from "../lib/internal-api";
import { PENDING_PRICE_TEXT } from "../lib/inventory-receipts";
import { ClaimsHint } from "./ClaimsHint";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { UnitPhotoManager } from "./UnitPhotoManager";
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
  voided: "border-bd-border text-muted line-through",
  in_transit: "border-warning-border text-warning",
};

type ListingDraft = {
  battery: string; grade: string; notes: string; price: string;
  warranty: string; functional: FunctionalState | ""; defects: string; accessories: string;
};

const toDraft = (unit: StockUnit): ListingDraft => ({
  battery: unit.battery_health === null || unit.battery_health === undefined ? "" : String(unit.battery_health),
  grade: unit.cosmetic_grade ? String(unit.cosmetic_grade) : "",
  notes: unit.public_notes ?? "",
  price: hasUsablePrice(unit.sale_price) ? unit.sale_price! : "",
  warranty: unit.warranty_days === null || unit.warranty_days === undefined ? "" : String(unit.warranty_days),
  functional: unit.functional_state ?? "",
  defects: unit.defects ?? "",
  accessories: unit.accessories ?? "",
});

/** What a used device would be better published with. Recommended: the server does not require these two. */
export function missingDetails(unit: StockUnit): string[] {
  const missing: string[] = [];
  if (unit.battery_health === null || unit.battery_health === undefined) missing.push("la batería medida");
  if (!unit.cosmetic_grade) missing.push("el estado estético");
  return missing;
}

/**
 * USED-READY-01 — what still keeps THIS device from being published, in words: the server's own rule (it refuses
 * the publication with these same reasons). Empty: it may be published. UNIT-PHOTO-02: only a photo confirmed as
 * THIS device counts (`publicPhotoCount`); with photos but none confirmed, it says so.
 */
export function publicationBlockers(unit: StockUnit, photoCount = publicPhotoCount(unit.photos)): string[] {
  const blockers: string[] = [];
  if (!["available", "reserved", "in_transit"].includes(unit.status)) blockers.push("ya no está en la tienda");
  if (!hasUsablePrice(unit.sale_price)) blockers.push("precio de venta");
  if (unit.inspection_status === "rejected") blockers.push("una inspección aprobada (fue rechazada)");
  else if (unit.inspection_status !== "approved") blockers.push("la inspección aprobada");
  if (photoCount === 0) {
    blockers.push((unit.photos?.length ?? 0) > 0
      ? "al menos una foto confirmada como de este equipo exacto"
      : "al menos una foto real del equipo");
  }
  return blockers;
}

/** «Con 90 días de garantía», «Sin garantía», or the company's policy. */
export function warrantyText(days: number | null | undefined): string {
  if (days === null || days === undefined) return "Según la política de garantía de la empresa";
  if (days === 0) return "Sin garantía";
  return days === 1 ? "1 día de garantía" : `${days} días de garantía`;
}

const INSPECTION_TONE: Record<UnitInspection, string> = {
  pending: "border-warning-border text-warning",
  approved: "border-success-border text-success",
  rejected: "border-danger-border text-danger",
};

const inspectionOf = (unit: StockUnit): UnitInspection => unit.inspection_status ?? "pending";

/** «Se ofrece» or «No se ofrece: …», from the server's reading of the list row. */
export function webStatusText(unit: StockUnit): string | null {
  if (!unit.web) return null;
  if (unit.web.offered) return "Se ofrece en la tienda en línea.";
  const reasons = unit.web.reasons.map(unitWebReasonText);
  return `No se ofrece en la tienda en línea: ${reasons.join("; ")}.`;
}

/** Why the price is read only for this person, in words that send them to the right permission. */
export function priceLockedReason({ sold, canManageProducts }: { sold: boolean; canManageProducts: boolean }): string {
  if (sold) return "Este equipo ya se vendió: su precio es el de esa venta y no se cambia.";
  if (canManageProducts) {
    return "El precio vive en la ficha del equipo, que también pide el permiso de ajustar inventario: pídelo a quien administra los permisos.";
  }
  return "Sólo quien gestiona productos puede fijar o cambiar el precio de venta.";
}

/** Edit one device's listing. Battery may be left blank: blank is «not stated», sent as null. */
export function UnitListingDialog({
  unit, grades, canEdit, canManagePrice, canManageProducts = false, canSeeCost, canInspect = false, onClose, onSaved,
  onChanged,
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
  /** `inventory.units.inspect`: approve or reject its inspection. */
  canInspect?: boolean;
  onClose: () => void;
  onSaved: (unit: StockUnit) => void;
  /** An inspection or a publication was saved: the dialog stays open on the fresh device. */
  onChanged?: (unit: StockUnit) => void;
}) {
  const titleId = useId();
  const pendingId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const initial = useRef<ListingDraft>(toDraft(unit));
  const [draft, setDraft] = useState<ListingDraft>(() => toDraft(unit));
  const [photos, setPhotos] = useState(unit.photos ?? []);
  const [errors, setErrors] = useState<Partial<Record<keyof ListingDraft, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A price change waits for an explicit «Sí»: it is what the till charges and the shop shows.
  const [confirming, setConfirming] = useState<UnitListingWrite | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  // USED-READY-01 — the inspection and the publication are decided apart from «Guardar».
  const [current, setCurrent] = useState<StockUnit>(unit);
  const [inspectionNote, setInspectionNote] = useState("");
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null);

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
    const warranty = draft.warranty.trim();
    if (priceEditable && warranty && (!/^\d{1,4}$/.test(warranty) || Number(warranty) > 3650)) {
      next.warranty = "Días enteros de 0 a 3650, o vacío para la política de la empresa.";
    }
    if (canEdit && draft.functional === "with_faults" && !draft.defects.trim()) {
      next.defects = "Un equipo con fallas dice cuáles.";
    }
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
      if (draft.functional !== before.functional) body.functional_state = draft.functional;
      if (draft.defects !== before.defects) body.defects = draft.defects;
      if (draft.accessories !== before.accessories) body.accessories = draft.accessories;
    }
    if (priceEditable && price !== before.price.trim()) body.sale_price = price || null;
    if (priceEditable && warranty !== before.warranty.trim()) body.warranty_days = warranty ? Number(warranty) : null;
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

  /** UNIT-PHOTO-02: every photo change answers with the whole device — its photos and what the web says of it. */
  function photosChanged(saved: StockUnit) {
    setPhotos(saved.photos ?? []);
    setCurrent((before) => ({ ...before, ...saved }));
    onChanged?.(saved);
  }

  async function decide(decision: "approved" | "rejected") {
    if (decision === "rejected" && !inspectionNote.trim()) {
      setError("Indica por qué se rechaza la inspección.");
      return;
    }
    setBusy(true);
    setError(null);
    setDecisionNotice(null);
    try {
      const saved = await inspectStockUnit(unit.id, decision, inspectionNote.trim());
      setCurrent((before) => ({ ...before, ...saved }));
      setInspectionNote("");
      setDecisionNotice(decision === "approved" ? "Inspección aprobada." : "Inspección rechazada: el equipo no se vende.");
      onChanged?.(saved);
    } catch (err) {
      setError(message(err, "No se pudo guardar la inspección."));
    } finally {
      setBusy(false);
    }
  }

  async function publish(published: boolean) {
    setBusy(true);
    setError(null);
    setDecisionNotice(null);
    try {
      const saved = await setStockUnitPublication(unit.id, published);
      setCurrent((before) => ({ ...before, ...saved }));
      setDecisionNotice(published ? "Equipo publicado en la tienda en línea." : "Equipo retirado de la tienda en línea.");
      onChanged?.(saved);
    } catch (err) {
      setError(message(err, "No se pudo cambiar la publicación."));
    } finally {
      setBusy(false);
    }
  }

  const web = webStatusText(unit);
  const missing = missingDetails(unit);
  const nextPrice = confirming?.sale_price ?? null;
  const inspection = inspectionOf(current);
  const inHand = ["available", "reserved", "in_transit"].includes(current.status);
  const blockers = publicationBlockers(current, publicPhotoCount(photos));

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
          {unit.serial_number || ""}{unit.serial_number && unit.imei ? " · " : ""}{unit.imei ?? ""}
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
            <div className="sm:col-span-2">
              <label className="block text-xs text-muted">
                Detalles para el comprador
                <textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} rows={3} className={`mt-1.5 ${internalInputClass}`} />
              </label>
              {/* P2 · F5c — the buyer reads these notes: claims control (Manual V4 §21), advice only. */}
              <ClaimsHint text={draft.notes} />
            </div>
            <label className="block text-xs text-muted">
              Estado funcional
              <select
                value={draft.functional}
                onChange={(e) => setDraft({ ...draft, functional: e.target.value as FunctionalState | "" })}
                className={`mt-1.5 ${internalInputClass}`}
              >
                <option value="">No informado</option>
                {(Object.keys(FUNCTIONAL_STATE_TEXT) as FunctionalState[]).map((value) => (
                  <option key={value} value={value}>{FUNCTIONAL_STATE_TEXT[value]}</option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Accesorios incluidos
              <input
                value={draft.accessories}
                maxLength={300}
                onChange={(e) => setDraft({ ...draft, accessories: e.target.value })}
                placeholder="Caja, cargador, cable…"
                className={`mt-1.5 ${internalInputClass}`}
              />
            </label>
            <label className="block text-xs text-muted sm:col-span-2">
              Defectos (los lee el comprador)
              <textarea
                value={draft.defects}
                maxLength={500}
                onChange={(e) => setDraft({ ...draft, defects: e.target.value })}
                rows={2}
                aria-invalid={errors.defects ? true : undefined}
                className={`mt-1.5 ${internalInputClass}`}
              />
              {errors.defects ? <span role="alert" className="mt-1 block text-danger">{errors.defects}</span> : null}
            </label>
            {!canEdit ? (
              <p className="text-xs text-muted sm:col-span-2">Cambiar estos datos necesita el permiso de ajustar inventario.</p>
            ) : null}
          </fieldset>

          {/* UNIT-PHOTO-02 — outside the data fieldset: an inspector without `inventory.adjust` still confirms. */}
          <UnitPhotoManager
            unitId={unit.id}
            photos={photos}
            canEdit={canEdit}
            canAttest={canInspect}
            onChange={photosChanged}
          />

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
            {priceEditable ? (
              <label className="block text-xs text-muted">
                Garantía (días)
                <input
                  inputMode="numeric"
                  value={draft.warranty}
                  disabled={busy}
                  onChange={(e) => setDraft({ ...draft, warranty: e.target.value })}
                  placeholder="Política de la empresa"
                  aria-invalid={errors.warranty ? true : undefined}
                  className={`mt-1.5 ${internalInputClass}`}
                />
                {errors.warranty ? <span role="alert" className="mt-1 block text-danger">{errors.warranty}</span> : null}
                <span className="mt-1 block">Vacío: la política de garantía de la empresa. 0: sin garantía.</span>
              </label>
            ) : (
              <p className="text-xs text-muted">Garantía: {warrantyText(unit.warranty_days)}</p>
            )}
            {!sold && !(priceEditable ? draft.price.trim() : priced) ? (
              <p id={pendingId} role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-foreground">
                {PENDING_PRICE_TEXT}
              </p>
            ) : null}
            {web ? <p className={`text-xs ${unit.web?.offered ? "text-success" : "text-muted"}`}>{web}</p> : null}
            {missing.length > 0 && !sold ? (
              <p className="text-xs text-muted">Se recomienda añadir {missing.join(" y ")}.</p>
            ) : null}
            <p className="text-xs text-muted">Fijar el precio no publica nada: el equipo se publica aparte, abajo.</p>
          </section>

          <section aria-label="Inspección y publicación" className="space-y-3 rounded-lg border border-bd-border p-3 sm:p-4">
            <h3 className="text-xs font-semibold uppercase tracking-widest text-muted">Inspección y publicación</h3>
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <span data-testid="unit-inspection" className={`inline-block whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] ${INSPECTION_TONE[inspection]}`}>
                {INSPECTION_TEXT[inspection]}
              </span>
              <span data-testid="unit-publication" className="text-xs text-muted">
                {current.published_online ? "Publicado en línea" : "No publicado en línea"}
              </span>
            </p>
            {current.inspection_note ? <p className="text-xs text-muted">Nota: {current.inspection_note}</p> : null}
            {inspection !== "approved" ? (
              <p className="text-xs text-muted">Sin inspección aprobada no se vende en caja ni se publica en línea.</p>
            ) : null}
            {canInspect && inHand ? (
              <div className="space-y-2">
                <label className="block text-xs text-muted">
                  Nota de la inspección {inspection === "approved" ? "" : "(obligatoria para rechazar)"}
                  <input
                    value={inspectionNote}
                    maxLength={300}
                    disabled={busy}
                    onChange={(e) => setInspectionNote(e.target.value)}
                    className={`mt-1.5 ${internalInputClass}`}
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  {inspection !== "approved" ? (
                    <button type="button" className={internalPrimaryButtonClass} disabled={busy} onClick={() => void decide("approved")}>
                      Aprobar inspección
                    </button>
                  ) : null}
                  {inspection !== "rejected" ? (
                    <button type="button" className={internalButtonClass} disabled={busy} onClick={() => void decide("rejected")}>
                      Rechazar inspección
                    </button>
                  ) : null}
                </div>
              </div>
            ) : !canInspect && inspection !== "approved" ? (
              <p className="text-xs text-muted">Aprobar la inspección es de quien tiene el permiso «Inspeccionar seminuevos».</p>
            ) : null}
            {canManagePrice && canEdit ? (
              current.published_online ? (
                <button type="button" className={internalButtonClass} disabled={busy} onClick={() => void publish(false)}>
                  Retirar de la tienda en línea
                </button>
              ) : blockers.length > 0 ? (
                <p data-testid="publication-blockers" className="text-xs text-muted">Para publicarlo falta: {blockers.join(", ")}.</p>
              ) : (
                <button type="button" className={internalPrimaryButtonClass} disabled={busy} onClick={() => void publish(true)}>
                  Publicar este equipo
                </button>
              )
            ) : (
              <p className="text-xs text-muted">Publicar o retirar un equipo pide gestionar productos y ajustar inventario.</p>
            )}
            {decisionNotice ? <p role="status" className="text-xs text-success">{decisionNotice}</p> : null}
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

/** STOCK-UX-WALK — the pills of a device's row or card. They never wrap: three lines read as three states. */
function UnitPills({ unit, only }: { unit: StockUnit; only?: "status" | "inspection" }) {
  const pill = "inline-block whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px]";
  return (
    <>
      {only !== "inspection" ? <span className={`${pill} ${STATUS_TONE[unit.status]}`}>{unit.status_label}</span> : null}
      {only !== "status" ? (
        <span className={`${pill} ${INSPECTION_TONE[inspectionOf(unit)]}`}>{INSPECTION_TEXT[inspectionOf(unit)]}</span>
      ) : null}
    </>
  );
}

function PriceCell({ unit }: { unit: StockUnit }) {
  return hasUsablePrice(unit.sale_price)
    ? <>{formatSoles(unit.sale_price!)}</>
    : <span className="text-warning">Sin precio (pendiente)</span>;
}

function WebCell({ unit }: { unit: StockUnit }) {
  if (!unit.web) return <>—</>;
  return unit.web.offered
    ? <span className="text-success">Se ofrece</span>
    : <span title={webStatusText(unit) ?? undefined}>No · {unitWebReasonText(String(unit.web.reasons[0] ?? ""))}</span>;
}

/** UNIT-PHOTO-02: confirmed (shown in the shop) of all. */
function PhotoCount({ unit }: { unit: StockUnit }) {
  return <span title="Confirmadas como de este equipo / todas">{publicPhotoCount(unit.photos)}/{unit.photos?.length ?? 0}</span>;
}

export function UsedUnitsPanel({
  branch, canEdit, canManagePrice = false, canManageProducts = false, canSeeCost, canInspect = false, initialPriced = "",
  initialProduct = "",
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
  /** `inventory.units.inspect`: approve or reject an inspection. The server says it too (`can_inspect`). */
  canInspect?: boolean;
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
  const [inspectionFilter, setInspectionFilter] = useState<"" | UnitInspection>("");
  const [grades, setGrades] = useState<CosmeticGradeRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<StockUnit | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchStockUnits({ branch, line: "used", search: applied, status, priced, product, inspection: inspectionFilter, page: pageNumber }).then(
      (loaded) => { if (!cancelled) { setPage(loaded); setError(null); } },
      (err) => { if (!cancelled) setError(message(err, "No se pudieron cargar los equipos.")); },
    );
    return () => { cancelled = true; };
  }, [branch, applied, status, priced, product, inspectionFilter, pageNumber, reloadKey]);

  useEffect(() => {
    fetchCosmeticGrades().then(setGrades, () => { /* the editor still works with «Sin grado» */ });
  }, []);

  const gradeLabel = useCallback(
    (unit: StockUnit) => unit.cosmetic_grade_label || grades.find((g) => g.id === unit.cosmetic_grade)?.label || "—",
    [grades],
  );

  const totalPages = page ? Math.max(1, Math.ceil(page.count / page.page_size)) : 1;
  const actionLabel = canEdit || canManagePrice ? "Editar" : "Ver";
  const open = (unit: StockUnit) => { setEditing(unit); setNotice(null); };
  const productName = product ? page?.results.find((u) => String(u.product_id) === product)?.product_name : undefined;

  return (
    <Panel
      title="Equipos seminuevos"
      description="Cada fila es un equipo. Se vende en caja con precio e inspección aprobada; en línea, además, publicado y con al menos una foto real confirmada como de ese equipo. La batería, el estado, la garantía, los defectos y los accesorios completan lo que ve el comprador."
    >
      <form
        className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_12rem_12rem_12rem_auto]"
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
        <label className="block text-xs text-muted">
          Inspección
          <select
            value={inspectionFilter}
            onChange={(e) => { setInspectionFilter(e.target.value as "" | UnitInspection); setPageNumber(1); }}
            className={`mt-1.5 ${internalInputClass}`}
          >
            <option value="">Todas</option>
            {(Object.keys(INSPECTION_TEXT) as UnitInspection[]).map((value) => (
              <option key={value} value={value}>{INSPECTION_TEXT[value]}</option>
            ))}
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
          {/* STOCK-UX-WALK — phones: one card per device (the `SupplierWeeklyPanel` pattern), its button in reach. */}
          <ul className="space-y-3 md:hidden" aria-label="Equipos seminuevos" data-testid="used-units-cards">
            {page.results.map((unit) => (
              <li key={unit.id} className="rounded-lg border border-bd-border bg-background p-3 text-sm">
                <p className="break-words font-semibold text-foreground">{unit.product_name}</p>
                <p className="mt-0.5 break-all font-mono text-xs text-muted">
                  {unit.imei || unitIdentifier(unit)}{unit.imei2 ? ` · ${unit.imei2}` : ""}
                  {unit.serial_number && unit.imei ? ` · Serie ${unit.serial_number}` : ""}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5"><UnitPills unit={unit} /></div>
                <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                  <dt className="text-muted">Precio de venta</dt><dd className="text-right"><PriceCell unit={unit} /></dd>
                  <dt className="text-muted">Batería</dt><dd className="text-right">{batteryCell(unit.battery_health)}</dd>
                  <dt className="text-muted">Estado estético</dt><dd className="text-right">{gradeLabel(unit)}</dd>
                  <dt className="text-muted">En línea</dt><dd className="text-right"><WebCell unit={unit} /></dd>
                  <dt className="text-muted">Fotos</dt><dd className="text-right"><PhotoCount unit={unit} /></dd>
                </dl>
                <button
                  type="button"
                  className={`mt-3 w-full min-h-11 ${internalButtonClass}`}
                  onClick={() => open(unit)}
                  aria-label={`${actionLabel} ${unitIdentifier(unit)}`}
                >
                  {actionLabel}
                </button>
              </li>
            ))}
          </ul>
          {/* From `md`: the table, opening with its only action so it is in view without scrolling sideways. */}
          <div className="hidden md:block" data-testid="used-units-table">
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Acciones</Th><Th>Producto</Th><Th>Serie</Th><Th>IMEI</Th><Th>Batería</Th><Th>Estado estético</Th>
                <Th right>Precio de venta</Th><Th>Estado</Th><Th>Inspección</Th><Th>En línea</Th><Th right>Fotos</Th>
              </tr>
            </thead>
            <tbody>
              {page.results.map((unit) => (
                <tr key={unit.id} className="border-b border-bd-border/60">
                  <Td>
                    <button
                      type="button"
                      className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25"
                      onClick={() => open(unit)}
                      aria-label={`${actionLabel} ${unitIdentifier(unit)}`}
                    >
                      {actionLabel}
                    </button>
                  </Td>
                  <Td><span className="block min-w-[12rem]">{unit.product_name}</span></Td>
                  <Td><span className="font-mono">{unitIdentifier(unit)}</span></Td>
                  <Td muted={!unit.imei}>
                    {unit.imei ? <span className="font-mono">{unit.imei}</span> : "—"}
                    {unit.imei2 ? <span className="block font-mono text-xs text-muted">{unit.imei2}</span> : null}
                  </Td>
                  <Td muted={unit.battery_health === null || unit.battery_health === undefined}>{batteryCell(unit.battery_health)}</Td>
                  <Td muted>{gradeLabel(unit)}</Td>
                  <Td right><PriceCell unit={unit} /></Td>
                  <Td><UnitPills unit={unit} only="status" /></Td>
                  <Td><UnitPills unit={unit} only="inspection" /></Td>
                  <Td muted={!unit.web?.offered}><WebCell unit={unit} /></Td>
                  <Td right><PhotoCount unit={unit} /></Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          </div>
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
          canInspect={canInspect || Boolean(page?.can_inspect)}
          onClose={() => { setEditing(null); setReloadKey((k) => k + 1); }}
          onSaved={() => { setEditing(null); setNotice("Equipo guardado."); setReloadKey((k) => k + 1); }}
        />
      ) : null}
    </Panel>
  );
}
