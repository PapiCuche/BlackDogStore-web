"use client";

/**
 * Inventario › Seminuevos — DEVICE-COMMERCE-01 · DC2.
 *
 * The used devices, one row each, with what the shop publishes about them:
 * battery, cosmetic grade, its own sale price and photos. Staff see the full
 * identifiers here, as on «Equipos»; the shop only ever gets a hint.
 *
 * A battery nobody measured is «no informado», never a number. The cost is
 * the server's to send: it only comes for `costs.view`, and it is not edited
 * here.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  deleteUnitPhoto, fetchStockUnits, updateStockUnitListing, uploadUnitPhoto,
  type StockUnit, type StockUnitPage, type UnitListingWrite,
} from "@/app/lib/stock-units";
import { fetchCosmeticGrades, type CosmeticGradeRow } from "../lib/internal-api";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

export const batteryCell = (value: number | null | undefined) =>
  value === null || value === undefined ? "no informado" : `${value}%`;

const PRICE = /^\d+(\.\d{1,2})?$/;

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
  price: unit.sale_price ?? "",
});

/** Edit one device's listing. Battery and price may be left blank: blank is «not stated», sent as null. */
export function UnitListingDialog({
  unit, grades, canEdit, canSeeCost, onClose, onSaved,
}: {
  unit: StockUnit;
  grades: CosmeticGradeRow[];
  canEdit: boolean;
  canSeeCost: boolean;
  onClose: () => void;
  onSaved: (unit: StockUnit) => void;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<ListingDraft>(() => toDraft(unit));
  const [photos, setPhotos] = useState(unit.photos ?? []);
  const [file, setFile] = useState<File | null>(null);
  const [alt, setAlt] = useState("");
  const [errors, setErrors] = useState<Partial<Record<keyof ListingDraft, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

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

  function validate(): UnitListingWrite | null {
    const next: Partial<Record<keyof ListingDraft, string>> = {};
    const battery = draft.battery.trim();
    if (battery && (!/^\d+$/.test(battery) || Number(battery) > 100)) next.battery = "Un número entero de 0 a 100, o vacío si no se midió.";
    const price = draft.price.trim();
    if (price && !PRICE.test(price)) next.price = "Un importe como 2599 o 2599.90, o vacío para usar el precio del producto.";
    setErrors(next);
    if (Object.keys(next).length) return null;
    return {
      battery_health: battery ? Number(battery) : null,
      cosmetic_grade: draft.grade ? Number(draft.grade) : null,
      public_notes: draft.notes,
      sale_price: price || null,
    };
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const body = validate();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await updateStockUnitListing(unit.id, body);
      onSaved({ ...unit, ...saved, photos });
    } catch (err) {
      setError(message(err, "No se pudo guardar el equipo."));
    } finally {
      setBusy(false);
    }
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

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-t-xl border border-bd-border bg-surface p-5 shadow-xl sm:rounded-xl sm:p-6"
      >
        <h2 id={titleId} className="text-lg font-bold text-foreground">{unit.product_name}</h2>
        <p className="mt-1 font-mono text-xs text-muted">
          {unit.serial_number}{unit.imei ? ` · ${unit.imei}` : ""}
        </p>

        <form onSubmit={save} className="mt-5 grid gap-3 sm:grid-cols-2" noValidate>
          <fieldset disabled={!canEdit || busy} className="contents">
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
            <label className="block text-xs text-muted">
              Precio de venta (S/)
              <input
                inputMode="decimal"
                value={draft.price}
                onChange={(e) => setDraft({ ...draft, price: e.target.value })}
                placeholder="Precio del producto"
                aria-invalid={errors.price ? true : undefined}
                className={`mt-1.5 ${internalInputClass}`}
              />
              {errors.price ? <span role="alert" className="mt-1 block text-danger">{errors.price}</span> : null}
            </label>
            {canSeeCost && unit.cost !== undefined ? (
              <p className="text-xs text-muted">
                Costo
                <span className="mt-1.5 block py-2.5 text-sm text-foreground">{unit.cost === null ? "Sin costo" : formatSoles(unit.cost)}</span>
              </p>
            ) : <span className="hidden sm:block" />}
            <label className="block text-xs text-muted sm:col-span-2">
              Notas para la tienda
              <textarea value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} rows={3} className={`mt-1.5 ${internalInputClass}`} />
            </label>
          </fieldset>

          <div className="sm:col-span-2">
            <p className="text-xs font-semibold text-muted">Fotos ({photos.length})</p>
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
            ) : null}
          </div>

          {error ? <p role="alert" className="text-sm text-danger sm:col-span-2">{error}</p> : null}

          <div className="flex justify-end gap-2 sm:col-span-2">
            <button type="button" className={internalButtonClass} onClick={onClose} disabled={busy}>{canEdit ? "Cancelar" : "Cerrar"}</button>
            {canEdit ? <button type="submit" className={internalPrimaryButtonClass} disabled={busy}>{busy ? "Guardando…" : "Guardar"}</button> : null}
          </div>
        </form>
      </div>
    </div>
  );
}

export function UsedUnitsPanel({
  branch, canEdit, canSeeCost,
}: {
  branch: number | "all" | undefined;
  /** `inventory.adjust`: the server checks it again on every change. */
  canEdit: boolean;
  /** `costs.view`: the server only sends the cost to whoever has it. */
  canSeeCost: boolean;
}) {
  const [page, setPage] = useState<StockUnitPage | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const [status, setStatus] = useState("");
  const [grades, setGrades] = useState<CosmeticGradeRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<StockUnit | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchStockUnits({ branch, line: "used", search: applied, status, page: pageNumber }).then(
      (loaded) => { if (!cancelled) { setPage(loaded); setError(null); } },
      (err) => { if (!cancelled) setError(message(err, "No se pudieron cargar los equipos.")); },
    );
    return () => { cancelled = true; };
  }, [branch, applied, status, pageNumber, reloadKey]);

  useEffect(() => {
    fetchCosmeticGrades().then(setGrades, () => { /* the editor still works with «Sin grado» */ });
  }, []);

  const gradeLabel = useCallback(
    (unit: StockUnit) => unit.cosmetic_grade_label || grades.find((g) => g.id === unit.cosmetic_grade)?.label || "—",
    [grades],
  );

  const totalPages = page ? Math.max(1, Math.ceil(page.count / page.page_size)) : 1;

  return (
    <Panel
      title="Equipos seminuevos"
      description="Cada fila es un equipo. Completa su batería, su estado, su precio y sus fotos para publicarlo en la tienda."
    >
      <form
        className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_14rem_auto]"
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
        <div className="flex items-end"><button type="submit" className={internalButtonClass}>Filtrar</button></div>
      </form>

      {notice ? <p role="status" className="mb-4 rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}
      {error ? <div className="mb-4"><ErrorBox message={error} /></div> : null}
      {page === null && !error ? <Spinner label="Cargando equipos…" /> : null}
      {page && page.results.length === 0 ? (
        <EmptyBox message="No hay equipos seminuevos que coincidan. Aparecen aquí los equipos de productos de la línea de seminuevos." />
      ) : null}

      {page && page.results.length > 0 ? (
        <>
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Producto</Th><Th>Serie</Th><Th>IMEI</Th><Th>Batería</Th><Th>Estado estético</Th>
                <Th right>Precio de venta</Th><Th>Estado</Th><Th right>Fotos</Th><Th right>Acciones</Th>
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
                  <Td right>{unit.sale_price ? formatSoles(unit.sale_price) : <span className="text-muted">Precio del producto</span>}</Td>
                  <Td>
                    <span className={`rounded-full border px-2.5 py-1 text-[11px] ${STATUS_TONE[unit.status]}`}>{unit.status_label}</span>
                  </Td>
                  <Td right>{unit.photos?.length ?? 0}</Td>
                  <Td right>
                    <button
                      type="button"
                      className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25"
                      onClick={() => { setEditing(unit); setNotice(null); }}
                      aria-label={`${canEdit ? "Editar" : "Ver"} ${unit.serial_number}`}
                    >
                      {canEdit ? "Editar" : "Ver"}
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
          canSeeCost={canSeeCost}
          onClose={() => { setEditing(null); setReloadKey((k) => k + 1); }}
          onSaved={() => { setEditing(null); setNotice("Equipo guardado."); setReloadKey((k) => k + 1); }}
        />
      ) : null}
    </Panel>
  );
}
