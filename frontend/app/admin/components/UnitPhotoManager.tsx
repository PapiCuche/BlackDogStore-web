"use client";

/**
 * UNIT-PHOTO-02 — the real photos of ONE used device, in «Seminuevos».
 *
 * The owner's rule: a photo of a used device is a photo of THAT device — main, front, back, sides and every visible
 * defect — never a generic picture. So each photo says which side it shows (a defect says what it shows), one is the
 * primary (the one a card, a cart and an order line show first), and none reaches the shop until somebody with
 * `inventory.units.inspect` ticks «Confirmo que esta foto es de este equipo exacto». A replaced file is not the one
 * somebody confirmed: it has to be confirmed again.
 *
 * Two authorities the server checks again on every change: `inventory.adjust` uploads, orders, replaces, chooses the
 * primary and deletes (`canEdit`); `inventory.units.inspect` confirms (`canAttest`). Every change answers with the
 * whole device, so this list is always the server's.
 *
 * Reordering works with the keyboard («Subir» / «Bajar») and by dragging on a pointer device. Background removal is
 * not here: the owner approves a tool first.
 */

import { useId, useRef, useState } from "react";

import {
  UNIT_PHOTO_VIEW_TEXT, attestUnitPhoto, deleteUnitPhoto, reorderUnitPhotos, replaceUnitPhoto, updateUnitPhoto,
  uploadUnitPhoto, type StockUnit, type UnitPhoto, type UnitPhotoView,
} from "@/app/lib/stock-units";
import { internalButtonClass, internalInputClass } from "./internal-ui";

const ACCEPT = "image/png,image/jpeg,image/webp";
const SMALL = "rounded-md border px-2 py-1 text-xs font-semibold disabled:opacity-40";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

/** «Frontal», «Defecto: rayón en el marco»… — the server's label, else the same rule from what it sent. */
export function unitPhotoLabel(photo: Pick<UnitPhoto, "view" | "view_label" | "defect_note">): string {
  if (photo.view_label) return photo.view_label;
  const label = UNIT_PHOTO_VIEW_TEXT[photo.view ?? "other"] ?? UNIT_PHOTO_VIEW_TEXT.other;
  return photo.view === "defect" && photo.defect_note ? `${label}: ${photo.defect_note}` : label;
}

/** Moves the item at `from` to `to`, as the order the server will be told. */
export function moved<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function UnitPhotoManager({
  unitId, photos, canEdit, canAttest, onChange,
}: {
  unitId: number;
  photos: UnitPhoto[];
  /** `inventory.adjust`: upload, order, replace, primary, delete. */
  canEdit: boolean;
  /** `inventory.units.inspect`: confirm that a photo is of this device. */
  canAttest: boolean;
  /** The server's device after each change. */
  onChange: (unit: StockUnit) => void;
}) {
  const ids = useId();
  const [file, setFile] = useState<File | null>(null);
  const [view, setView] = useState<UnitPhotoView>("front");
  const [defectNote, setDefectNote] = useState("");
  const [alt, setAlt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const replaceInputs = useRef<Record<number, HTMLInputElement | null>>({});

  const publicCount = photos.filter((photo) => photo.public !== false).length;

  async function run(action: () => Promise<StockUnit>, fallback: string, done?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      onChange(await action());
      if (done) setNotice(done);
      return true;
    } catch (err) {
      setError(message(err, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (!file) return;
    if (view === "defect" && !defectNote.trim()) {
      setError("Una foto de un defecto dice qué defecto muestra.");
      return;
    }
    const ok = await run(
      () => uploadUnitPhoto(unitId, file, { view, defectNote: defectNote.trim(), altText: alt.trim() }),
      "No se pudo subir la foto.",
      "Foto subida. No se muestra en la tienda hasta que se confirme que es de este equipo.",
    );
    if (ok) {
      setFile(null);
      setAlt("");
      setDefectNote("");
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function reorder(from: number, to: number) {
    const next = moved(photos, from, to);
    if (next === photos) return;
    void run(() => reorderUnitPhotos(unitId, next.map((photo) => photo.id)), "No se pudo cambiar el orden.",
      "Orden guardado.");
  }

  return (
    <section aria-labelledby={`${ids}-title`} className="space-y-3 rounded-lg border border-bd-border p-3 sm:p-4">
      <h3 id={`${ids}-title`} className="text-xs font-semibold uppercase tracking-widest text-muted">
        Fotos reales del equipo ({photos.length})
      </h3>
      <p className="text-xs text-muted">
        Cada foto es de ESTE equipo: principal, frente, reverso, lados y cada defecto visible. Sólo las confirmadas se
        muestran en la tienda ({publicCount} de {photos.length}).
      </p>

      {photos.length > 0 ? (
        <ol aria-label="Fotos del equipo, en orden" className="grid gap-3 sm:grid-cols-2">
          {photos.map((photo, index) => {
            const label = unitPhotoLabel(photo);
            const name = `foto ${index + 1} (${label})`;
            const isPublic = photo.public !== false;
            return (
              <li
                key={photo.id}
                data-testid="unit-photo"
                draggable={canEdit && !busy}
                onDragStart={() => setDragging(index)}
                onDragOver={(event) => { if (dragging !== null) event.preventDefault(); }}
                onDrop={(event) => { event.preventDefault(); if (dragging !== null) reorder(dragging, index); setDragging(null); }}
                onDragEnd={() => setDragging(null)}
                className={`flex gap-3 rounded-lg border p-2 ${dragging === index ? "border-foreground/40" : "border-bd-border"}`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- staff preview of an uploaded file */}
                <img
                  src={photo.url}
                  alt={photo.alt_text || `Foto ${index + 1}: ${label}`}
                  className="h-20 w-20 shrink-0 rounded-md border border-bd-border bg-background object-contain"
                />
                <div className="min-w-0 flex-1 space-y-1.5 text-xs">
                  <p className="break-words font-semibold text-foreground">
                    {index + 1}. {label}
                    {photo.is_primary ? (
                      <span className="ml-1.5 rounded-full border border-bd-border px-1.5 py-0.5 text-[10px] text-muted">Principal</span>
                    ) : null}
                  </p>
                  <p data-testid="unit-photo-state" className={isPublic ? "text-success" : "text-warning"}>
                    {isPublic ? "Se muestra en la tienda" : "No se muestra en la tienda: falta confirmarla"}
                  </p>
                  {canAttest ? (
                    <label className="flex items-start gap-2 text-foreground">
                      <input
                        type="checkbox"
                        checked={Boolean(photo.attested)}
                        disabled={busy}
                        onChange={(event) => void run(
                          () => attestUnitPhoto(unitId, photo.id, event.target.checked),
                          "No se pudo guardar la confirmación.",
                          event.target.checked ? "Foto confirmada como de este equipo." : "Confirmación retirada.",
                        )}
                        className="mt-0.5 h-4 w-4"
                        aria-label={`Confirmo que la ${name} es de este equipo exacto`}
                      />
                      <span>Confirmo que esta foto es de este equipo exacto</span>
                    </label>
                  ) : photo.attested ? (
                    <p className="text-muted">Confirmada{photo.attested_by ? ` por ${photo.attested_by}` : ""}.</p>
                  ) : (
                    <p className="text-muted">La confirma quien tiene el permiso «Inspeccionar seminuevos».</p>
                  )}
                  {canEdit ? (
                    <div className="flex flex-wrap gap-1.5">
                      <button type="button" disabled={busy || index === 0} onClick={() => reorder(index, index - 1)}
                        aria-label={`Subir ${name}`} className={`${SMALL} border-bd-border text-foreground`}>Subir</button>
                      <button type="button" disabled={busy || index === photos.length - 1} onClick={() => reorder(index, index + 1)}
                        aria-label={`Bajar ${name}`} className={`${SMALL} border-bd-border text-foreground`}>Bajar</button>
                      {!photo.is_primary ? (
                        <button type="button" disabled={busy}
                          onClick={() => void run(() => updateUnitPhoto(unitId, photo.id, { is_primary: true }),
                            "No se pudo elegir la foto principal.", "Foto principal elegida.")}
                          aria-label={`Hacer principal la ${name}`} className={`${SMALL} border-bd-border text-foreground`}>
                          Hacer principal
                        </button>
                      ) : null}
                      <button type="button" disabled={busy} onClick={() => replaceInputs.current[photo.id]?.click()}
                        aria-label={`Reemplazar ${name}`} className={`${SMALL} border-bd-border text-foreground`}>Reemplazar</button>
                      <input
                        ref={(node) => { replaceInputs.current[photo.id] = node; }}
                        type="file"
                        accept={ACCEPT}
                        hidden
                        aria-hidden="true"
                        tabIndex={-1}
                        data-testid={`unit-photo-replace-${photo.id}`}
                        onChange={(event) => {
                          const chosen = event.target.files?.[0];
                          event.target.value = "";
                          if (chosen) {
                            void run(() => replaceUnitPhoto(unitId, photo.id, chosen), "No se pudo reemplazar la foto.",
                              "Foto reemplazada: hay que confirmarla de nuevo.");
                          }
                        }}
                      />
                      <button type="button" disabled={busy}
                        onClick={() => void run(() => deleteUnitPhoto(unitId, photo.id), "No se pudo eliminar la foto.",
                          "Foto eliminada.")}
                        aria-label={`Eliminar ${name}`} className={`${SMALL} border-danger-border text-danger`}>Eliminar</button>
                    </div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="text-xs text-muted">Sin fotos de este equipo.</p>
      )}

      {canEdit ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-xs text-muted">
            Nueva foto
            <input ref={fileInput} type="file" accept={ACCEPT} disabled={busy}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-1.5 block w-full text-sm text-foreground" />
          </label>
          <label className="block text-xs text-muted">
            Qué muestra
            <select value={view} disabled={busy} onChange={(e) => setView(e.target.value as UnitPhotoView)}
              className={`mt-1.5 ${internalInputClass}`}>
              {(Object.keys(UNIT_PHOTO_VIEW_TEXT) as UnitPhotoView[]).map((value) => (
                <option key={value} value={value}>{UNIT_PHOTO_VIEW_TEXT[value]}</option>
              ))}
            </select>
          </label>
          {view === "defect" ? (
            <label className="block text-xs text-muted sm:col-span-2">
              Qué defecto muestra (lo lee el comprador)
              <input value={defectNote} maxLength={160} disabled={busy} onChange={(e) => setDefectNote(e.target.value)}
                className={`mt-1.5 ${internalInputClass}`} />
            </label>
          ) : null}
          <label className="block text-xs text-muted">
            Texto alternativo
            <input value={alt} maxLength={160} disabled={busy} onChange={(e) => setAlt(e.target.value)}
              className={`mt-1.5 ${internalInputClass}`} />
          </label>
          <div className="flex items-end">
            <button type="button" className={internalButtonClass} disabled={busy || !file} onClick={() => void upload()}>
              Subir foto
            </button>
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted">Subir, ordenar o cambiar fotos necesita el permiso de ajustar inventario.</p>
      )}

      {notice ? <p role="status" className="text-xs text-success">{notice}</p> : null}
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    </section>
  );
}
