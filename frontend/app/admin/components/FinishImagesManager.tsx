"use client";

/**
 * Productos › Modelos › un modelo › «Imágenes por acabado» — CATALOG-REFERENCE-01 · PR E (API of PR C).
 *
 * ONE GALLERY PER COLOUR of the model, shared by every capacity, SIM and region of that colour. A colour without
 * pictures shows «Imagen pendiente» in the shop — never another colour's photo.
 *
 * EVERY PICTURE SAYS WHERE IT COMES FROM. Uploading asks for its origin (own photo, authorised by a supplier, licensed
 * stock or a demo resource) and, unless it is the shop's own photo, the licence or authorisation text; the source
 * address and the attribution are optional. Checking the licence records who and when (the server does). A demo
 * resource is labelled as such here and «Imagen referencial» in the shop.
 *
 * EACH CHANGE IS SAVED WHEN MADE (upload, primary, order, alt text, provenance, licence check, removal); the server
 * is the authority and answers the gallery as it stands. Order is changed with buttons, so the keyboard does it too,
 * and focus stays on the picture that moved. Seeing needs `products.view`; every change, `products.manage`.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { PRODUCT_IMAGE_ACCEPT } from "@/app/lib/admin";
import { storefrontMediaStyle } from "@/app/lib/storefront-media";
import {
  deleteFinishImage, fetchFinishImages, patchFinishImage, provenanceErrors, reorderFinishImages, uploadFinishImage,
  type Finish, type FinishImage, type FinishImagePatch, type FinishListing, type Provenance, type SourceKind,
} from "../lib/catalog-presentation-api";
import { CatalogApiError } from "../lib/internal-api";
import { ImageDropzone } from "./ImageDropzone";
import { TRANSPARENCY_GRID_STYLE } from "./ImageUploadField";
import { ErrorBox, Panel, Spinner } from "./InventoryUi";
import { formatBytes, rejectReason } from "./ProductGallery";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

const touch = "min-h-[44px]";

const smallButton = `${internalButtonClass} min-h-[44px] px-3 py-1.5 text-xs`;

/** What a person chooses when uploading; the server sends its own labels (`source_kinds`), these are the fallback. */
export const SOURCE_KIND_LABELS: Record<Exclude<SourceKind, "unknown">, string> = {
  own_photo: "Foto propia",
  supplier_authorized: "Autorizada por proveedor o distribuidor",
  licensed_stock: "Banco con licencia comercial",
  demo: "Recurso demostrativo",
};

const EMPTY_PROVENANCE: Provenance = { source_kind: "", license: "", source_url: "", attribution: "" };

const formatDate = (iso: string | null) => {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("es-PE", { day: "2-digit", month: "2-digit", year: "numeric" });
};

// ---------------------------------------------------------------------------
// Provenance fields — shared by the upload form and «Editar procedencia»
// ---------------------------------------------------------------------------

export function ProvenanceFields({
  value, onChange, errors, kinds, disabled, firstFieldRef,
}: {
  value: Provenance;
  onChange: (value: Provenance) => void;
  errors: Partial<Record<keyof Provenance, string>>;
  kinds: { value: SourceKind; label: string }[];
  disabled?: boolean;
  /** The origin select, for a host that moves focus into the form when it opens. */
  firstFieldRef?: React.Ref<HTMLSelectElement>;
}) {
  const id = useId();
  const field = (name: keyof Provenance) => ({
    id: `${id}-${name}`,
    "aria-invalid": errors[name] ? true : undefined,
    "aria-describedby": errors[name] ? `${id}-${name}-error` : undefined,
  });
  const error = (name: keyof Provenance) => (errors[name] ? (
    <p id={`${id}-${name}-error`} className="mt-1 text-xs text-danger">{errors[name]}</p>
  ) : null);
  const licenseRequired = value.source_kind !== "" && value.source_kind !== "own_photo";
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div>
        <label htmlFor={`${id}-source_kind`} className="block text-xs text-muted">Origen de la imagen (obligatorio)</label>
        <select
          ref={firstFieldRef}
          {...field("source_kind")}
          value={value.source_kind}
          disabled={disabled}
          required
          onChange={(e) => onChange({ ...value, source_kind: e.target.value as SourceKind })}
          className={`mt-1.5 ${internalInputClass}`}
        >
          <option value="">Elige el origen…</option>
          {kinds.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
        </select>
        {error("source_kind")}
      </div>
      <div>
        <label htmlFor={`${id}-license`} className="block text-xs text-muted">
          Licencia o autorización{licenseRequired ? " (obligatoria)" : value.source_kind === "own_photo" ? " (opcional en foto propia)" : ""}
        </label>
        <input
          {...field("license")}
          value={value.license}
          disabled={disabled}
          maxLength={255}
          required={licenseRequired}
          placeholder={value.source_kind === "demo" ? "Uso demostrativo autorizado por…" : "Autorización escrita del distribuidor, 2026-11"}
          onChange={(e) => onChange({ ...value, license: e.target.value })}
          className={`mt-1.5 ${internalInputClass}`}
        />
        {error("license")}
      </div>
      <div>
        <label htmlFor={`${id}-source_url`} className="block text-xs text-muted">Dirección de origen (opcional)</label>
        <input
          {...field("source_url")}
          type="url"
          inputMode="url"
          value={value.source_url}
          disabled={disabled}
          maxLength={500}
          placeholder="https://…"
          onChange={(e) => onChange({ ...value, source_url: e.target.value })}
          className={`mt-1.5 ${internalInputClass}`}
        />
        {error("source_url")}
      </div>
      <div>
        <label htmlFor={`${id}-attribution`} className="block text-xs text-muted">Atribución (opcional)</label>
        <input
          {...field("attribution")}
          value={value.attribution}
          disabled={disabled}
          maxLength={255}
          placeholder="Foto: …"
          onChange={(e) => onChange({ ...value, attribution: e.target.value })}
          className={`mt-1.5 ${internalInputClass}`}
        />
        {error("attribution")}
      </div>
    </div>
  );
}

/** A server refusal placed under its field when it names one, else said for the whole form. */
function serverFieldError(error: unknown): { field: keyof Provenance | "file" | "alt_text" | null; text: string } {
  const text = message(error, "No se pudo guardar.");
  const field = error instanceof CatalogApiError ? error.field : null;
  if (field === "source_kind" || field === "license" || field === "source_url" || field === "attribution"
    || field === "file" || field === "alt_text") {
    return { field, text };
  }
  return { field: null, text };
}

// ---------------------------------------------------------------------------
// Upload form of one colour
// ---------------------------------------------------------------------------

function UploadForm({
  modelId, modelName, finish, kinds, companyId, onUploaded, onCancel,
}: {
  modelId: number; modelName: string; finish: Finish; kinds: { value: SourceKind; label: string }[];
  companyId: number | null; onUploaded: (image: FinishImage) => void; onCancel: () => void;
}) {
  const id = useId();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [provenance, setProvenance] = useState<Provenance>(EMPTY_PROVENANCE);
  const [alt, setAlt] = useState("");
  const [errors, setErrors] = useState<Partial<Record<keyof Provenance, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  function choose(files: File[]) {
    const chosen = files[0];
    if (!chosen) return;
    const reason = rejectReason(chosen);
    if (reason) { setFile(null); setFileError(`${chosen.name}: ${reason}.`); return; }
    setFileError(null);
    setFile(chosen);
    setPreview(typeof URL.createObjectURL === "function" ? URL.createObjectURL(chosen) : null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = provenanceErrors(provenance);
    setErrors(found);
    if (!file) setFileError("Elige la imagen que vas a subir.");
    if (!file || Object.keys(found).length) return;
    setSending(true);
    setFormError(null);
    try {
      const image = await uploadFinishImage(modelId, finish.option.id, file, provenance, alt.trim(), companyId);
      onUploaded(image);
    } catch (error) {
      const { field, text } = serverFieldError(error);
      if (field === "file") setFileError(text);
      else if (field && field !== "alt_text") setErrors({ [field]: text });
      else setFormError(text);
    } finally {
      setSending(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Subir imagen de ${finish.option.label}`} className="mt-3 space-y-3 rounded-lg border border-bd-border bg-surface p-3">
      <ImageDropzone
        label={file ? "Elegir otra imagen" : "Elegir imagen"}
        accept={PRODUCT_IMAGE_ACCEPT}
        multiple={false}
        onFiles={choose}
        hint="PNG (con transparencia), JPEG o WebP, hasta 8 MB."
      />
      {file ? (
        <div className="flex min-w-0 items-center gap-3 text-xs">
          {preview ? (
            <span style={TRANSPARENCY_GRID_STYLE} className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-md border border-bd-border">
              {/* Vista previa local (blob:), aún no está en el servidor. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={preview} alt={`Vista previa de ${file.name}`} className="max-h-full max-w-full object-contain" />
            </span>
          ) : null}
          <span className="min-w-0 break-all text-foreground">{file.name} · {formatBytes(file.size)}</span>
        </div>
      ) : null}
      {fileError ? <p role="alert" className="text-xs text-danger">{fileError}</p> : null}
      <ProvenanceFields value={provenance} onChange={setProvenance} errors={errors} kinds={kinds} disabled={sending} />
      <label htmlFor={`${id}-alt`} className="block text-xs text-muted">
        Texto alternativo
        <input
          id={`${id}-alt`}
          value={alt}
          maxLength={160}
          disabled={sending}
          placeholder={`${modelName} ${finish.option.label}`}
          onChange={(e) => setAlt(e.target.value)}
          className={`mt-1.5 ${internalInputClass}`}
        />
      </label>
      {formError ? <p role="alert" className="text-xs text-danger">{formError}</p> : null}
      {Object.keys(errors).length ? (
        <p role="alert" className="text-xs text-danger">Revisa la procedencia: hay campos marcados.</p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={`${touch} ${internalButtonClass}`} onClick={onCancel} disabled={sending}>Cancelar</button>
        <button type="submit" className={`${touch} ${internalPrimaryButtonClass}`} disabled={sending}>
          {sending ? "Subiendo…" : "Subir imagen"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// One picture
// ---------------------------------------------------------------------------

function ImageCard({
  image, index, count, colour, modelName, kinds, canManage, busy, onPatch, onMove, onRemove,
}: {
  image: FinishImage; index: number; count: number; colour: string; modelName: string;
  kinds: { value: SourceKind; label: string }[]; canManage: boolean; busy: boolean;
  onPatch: (data: FinishImagePatch) => Promise<boolean>; onMove: (step: -1 | 1) => void; onRemove: () => void;
}) {
  const position = index + 1;
  const name = `imagen ${position} de ${colour}`;
  // null = what the server holds; a string = what the person is typing (saved on leaving the field).
  const [altDraft, setAltDraft] = useState<string | null>(null);
  const alt = altDraft ?? image.alt_text;
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [provenance, setProvenance] = useState<Provenance>(EMPTY_PROVENANCE);
  const [errors, setErrors] = useState<Partial<Record<keyof Provenance, string>>>({});
  // Review of #374 (a11y): opening or closing a step never drops focus to the start of the page.
  const confirmYes = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const originField = useRef<HTMLSelectElement>(null);
  const focusNext = useRef<"confirm" | "remove" | "edit" | "origin" | null>(null);
  useEffect(() => {
    const target = focusNext.current;
    if (!target) return;
    focusNext.current = null;
    ({ confirm: confirmYes, remove: removeButton, edit: editButton, origin: originField })[target].current?.focus();
  });

  function openConfirm() { focusNext.current = "confirm"; setConfirming(true); }
  function cancelConfirm() { focusNext.current = "remove"; setConfirming(false); }
  function closeEditing() { focusNext.current = "edit"; setEditing(false); }

  function startEditing() {
    setProvenance({
      source_kind: image.source.kind === "unknown" ? "" : image.source.kind,
      license: image.source.license, source_url: image.source.url, attribution: image.source.attribution,
    });
    setErrors({});
    focusNext.current = "origin";
    setEditing(true);
  }

  async function saveProvenance(event: React.FormEvent) {
    event.preventDefault();
    const found = provenanceErrors(provenance);
    setErrors(found);
    if (Object.keys(found).length) return;
    const ok = await onPatch({
      source_kind: provenance.source_kind as SourceKind, license: provenance.license,
      source_url: provenance.source_url, attribution: provenance.attribution,
    });
    if (ok) closeEditing();
  }

  const verified = image.source.license_verified;
  // PR C: the shop's own photo never counts as an unverified licence (`catalog_image_sources`): nothing to check.
  const ownPhoto = image.source.kind === "own_photo";
  const positionLabel = `Imagen ${position} de ${colour}`;
  return (
    <li
      data-image-id={image.id}
      tabIndex={-1}
      aria-label={positionLabel}
      className="flex min-w-0 flex-col rounded-xl border border-bd-border bg-background p-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      <div style={TRANSPARENCY_GRID_STYLE} className="relative flex aspect-[4/3] items-center justify-center overflow-hidden rounded-lg">
        {/* Imagen del propio tenant, de tamaño desconocido: no pasa por el optimizador. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={image.url} alt={image.alt_text || `${modelName} ${colour}`} loading="lazy" style={storefrontMediaStyle(image.url)} className="max-h-full max-w-full object-contain" />
        <div className="absolute left-2 top-2 flex flex-wrap gap-1">
          {image.is_primary ? (
            <span className="rounded-full bg-foreground px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-background">Principal</span>
          ) : null}
          {image.source.is_demo ? (
            <span className="rounded-full border border-warning-border bg-warning-surface px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-warning">
              Demostrativa
            </span>
          ) : null}
        </div>
      </div>

      <dl className="mt-3 space-y-1 text-xs">
        <div className="flex flex-wrap gap-x-1"><dt className="text-muted">Origen:</dt><dd className="text-foreground">{image.source.kind_label}</dd></div>
        {image.source.license ? (
          <div className="flex flex-wrap gap-x-1"><dt className="text-muted">Licencia:</dt><dd className="break-words text-foreground">{image.source.license}</dd></div>
        ) : null}
        {image.source.attribution ? (
          <div className="flex flex-wrap gap-x-1"><dt className="text-muted">Atribución:</dt><dd className="break-words text-foreground">{image.source.attribution}</dd></div>
        ) : null}
        {image.source.url ? (
          <div className="flex min-w-0 flex-wrap gap-x-1">
            <dt className="text-muted">Fuente:</dt>
            <dd className="min-w-0 break-all"><a href={image.source.url} target="_blank" rel="noreferrer noopener" className="text-foreground underline underline-offset-4">{image.source.url}</a></dd>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-x-1">
          <dt className="text-muted">Licencia verificada:</dt>
          <dd className={verified ? "text-success" : ownPhoto ? "text-muted" : "text-warning"}>
            {verified
              ? `Sí${image.source.license_verified_by ? `, por ${image.source.license_verified_by}` : ""}${image.source.license_verified_at ? ` el ${formatDate(image.source.license_verified_at)}` : ""}`
              : ownPhoto ? "No aplica (foto propia)" : "Sin verificar"}
          </dd>
        </div>
        {image.source.is_demo ? (
          <p className="text-warning">Recurso demostrativo: la tienda lo rotula «Imagen referencial».</p>
        ) : null}
      </dl>

      {canManage ? (
        <div className="mt-3 space-y-3">
          <label className="block text-xs text-muted">
            Texto alternativo
            <input
              aria-label={`Texto alternativo de la ${name}`}
              value={alt}
              maxLength={160}
              disabled={busy}
              placeholder={`${modelName} ${colour}`}
              onChange={(e) => setAltDraft(e.target.value)}
              onBlur={() => {
                if (altDraft !== null && altDraft.trim() !== image.alt_text) void onPatch({ alt_text: altDraft.trim() });
                setAltDraft(null);
              }}
              className={`mt-1 ${internalInputClass}`}
            />
          </label>
          {ownPhoto && !verified ? null : (
          <label className={`flex items-center gap-2 text-xs text-foreground ${touch}`}>
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={verified}
              disabled={busy || image.source.kind === "unknown"}
              onChange={(e) => void onPatch({ license_verified: e.target.checked })}
              aria-label={`Licencia verificada: ${name}`}
            />
            Revisé la licencia o la autorización
          </label>
          )}

          {editing ? (
            <form onSubmit={saveProvenance} noValidate aria-label={`Procedencia de la ${name}`} className="space-y-3 rounded-lg border border-bd-border p-3">
              <ProvenanceFields value={provenance} onChange={setProvenance} errors={errors} kinds={kinds} disabled={busy} firstFieldRef={originField} />
              <p className="text-xs text-muted">Cambiar la procedencia quita la verificación de la licencia.</p>
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" className={smallButton} onClick={closeEditing} disabled={busy}>Cancelar</button>
                <button type="submit" className={`${touch} ${internalPrimaryButtonClass} text-xs`} disabled={busy}>Guardar procedencia</button>
              </div>
            </form>
          ) : null}

          {confirming ? (
            <div className="flex flex-wrap items-center gap-2 text-xs" role="group" aria-label={`Confirmar quitar la ${name}`}>
              <span className="text-foreground">¿Quitar esta imagen? La tienda dejará de mostrarla.</span>
              <button ref={confirmYes} type="button" onClick={() => { setConfirming(false); onRemove(); }} disabled={busy} className="min-h-[44px] rounded-lg border border-danger-border bg-danger-surface px-3 font-semibold text-danger">
                Sí, quitar
              </button>
              <button type="button" onClick={cancelConfirm} className="min-h-[44px] px-2 text-muted underline underline-offset-4">Cancelar</button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" data-move="before" aria-label={`Mover la ${name} antes`} disabled={busy || index === 0} onClick={() => onMove(-1)} className={smallButton}>
                <span aria-hidden="true">←</span>
              </button>
              <button type="button" data-move="after" aria-label={`Mover la ${name} después`} disabled={busy || index === count - 1} onClick={() => onMove(1)} className={smallButton}>
                <span aria-hidden="true">→</span>
              </button>
              {!image.is_primary ? (
                <button type="button" aria-label={`Marcar como principal: ${name}`} disabled={busy} onClick={() => void onPatch({ is_primary: true })} className={smallButton}>
                  Marcar como principal
                </button>
              ) : null}
              {!editing ? (
                <button ref={editButton} type="button" aria-label={`Editar procedencia: ${name}`} disabled={busy} onClick={startEditing} className={smallButton}>
                  Editar procedencia
                </button>
              ) : null}
              <button ref={removeButton} type="button" aria-label={`Quitar la ${name}`} disabled={busy} onClick={openConfirm} className="min-h-[44px] px-1 text-xs text-muted underline-offset-4 hover:text-danger hover:underline">
                Quitar
              </button>
            </div>
          )}
        </div>
      ) : image.alt_text ? <p className="mt-2 text-xs text-muted">{image.alt_text}</p> : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// The manager
// ---------------------------------------------------------------------------

export function FinishImagesManager({
  modelId, modelName, canManage, companyId, onChanged,
}: {
  modelId: number; modelName: string; canManage: boolean; companyId: number | null;
  /** A picture was added, removed or changed: the host may re-read what depends on it (the summary). */
  onChanged?: () => void;
}) {
  const [listing, setListing] = useState<FinishListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [uploadingFor, setUploadingFor] = useState<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const focusAfter = useRef<{ imageId: number; move: "before" | "after" } | null>(null);
  // Review of #374 (a11y): after a removal, the picture now in its place (or the one before), else the colour.
  const focusAfterRemoval = useRef<{ colourId: number; index: number } | null>(null);
  const hostChanged = useRef(onChanged);
  useEffect(() => { hostChanged.current = onChanged; });

  const load = useCallback(() => (
    fetchFinishImages(modelId, companyId).then(
      (data) => { setListing(data); setError(null); },
      (err) => { setError(message(err, "No se pudieron leer las imágenes por acabado.")); },
    )
  ), [modelId, companyId]);

  useEffect(() => { void load(); }, [load]);

  // Keyboard reorder: focus stays on the picture that moved (its same button, or the other one at an end).
  useEffect(() => {
    const wanted = focusAfter.current;
    // Only once the buttons are enabled again (a disabled button cannot take focus).
    if (!wanted || !root.current || busy !== null) return;
    focusAfter.current = null;
    const card = root.current.querySelector<HTMLElement>(`[data-image-id="${wanted.imageId}"]`);
    if (!card) return;
    const same = card.querySelector<HTMLButtonElement>(`[data-move="${wanted.move}"]`);
    const other = card.querySelector<HTMLButtonElement>(`[data-move="${wanted.move === "before" ? "after" : "before"}"]`);
    (same && !same.disabled ? same : other)?.focus();
  }, [listing, busy]);

  useEffect(() => {
    const wanted = focusAfterRemoval.current;
    if (!wanted || !root.current || busy !== null) return;
    focusAfterRemoval.current = null;
    const finish = root.current.querySelector<HTMLElement>(`[data-finish-id="${wanted.colourId}"]`);
    const cards = finish ? Array.from(finish.querySelectorAll<HTMLElement>("[data-image-id]")) : [];
    const card = cards[Math.min(wanted.index, cards.length - 1)];
    (card ?? root.current.querySelector<HTMLElement>(`#acabado-${wanted.colourId}`))?.focus();
  }, [listing, busy]);

  const kinds = listing?.source_kinds?.length
    ? listing.source_kinds
    : (Object.entries(SOURCE_KIND_LABELS) as [SourceKind, string][]).map(([value, label]) => ({ value, label }));

  async function act(imageId: number, run: () => Promise<unknown>, done?: string): Promise<boolean> {
    setBusy(imageId);
    setActionError(null);
    setNotice(null);
    try {
      await run();
      await load();
      if (done) setNotice(done);
      hostChanged.current?.();
      return true;
    } catch (err) {
      setActionError(message(err, "No se pudo guardar el cambio."));
      await load();
      return false;
    } finally {
      setBusy(null);
    }
  }

  function move(finish: Finish, index: number, step: -1 | 1) {
    const order = finish.images.map((image) => image.id);
    const target = index + step;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    const imageId = finish.images[index].id;
    focusAfter.current = { imageId, move: step === -1 ? "before" : "after" };
    void act(imageId, () => reorderFinishImages(modelId, finish.option.id, order, companyId),
      `Imagen movida a la posición ${target + 1} de ${finish.option.label}.`);
  }

  const finishes = listing?.finishes ?? [];
  const pending = finishes.filter((finish) => finish.image_pending && !finish.not_colour).length;

  return (
    <Panel
      id="acabados"
      title="Imágenes por acabado"
      description="Una galería por color, compartida por todas sus capacidades, SIM y regiones. Un color sin fotos se muestra como «Imagen pendiente», nunca con la foto de otro color."
    >
      <div ref={root} className="space-y-4">
        {error ? (
          <div className="space-y-2">
            <ErrorBox message={error} />
            <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => void load()}>Reintentar</button>
          </div>
        ) : listing === null ? <Spinner label="Cargando las imágenes por acabado…" /> : finishes.length === 0 ? (
          <p className="rounded-lg border border-dashed border-bd-border px-4 py-6 text-center text-sm text-muted">
            Este modelo todavía no tiene colores: márcalos en «Opciones de este modelo» o crea una variante con color.
          </p>
        ) : (
          <>
            <p className="text-xs text-muted" aria-live="polite">
              {finishes.length} {finishes.length === 1 ? "color" : "colores"}
              {pending ? ` · ${pending} con imagen pendiente` : " · todos con imagen"}
              {` · hasta ${listing.max_per_colour} imágenes por color`}
            </p>
            {notice ? <p role="status" className="rounded-lg border border-success-border px-3 py-2 text-xs text-success">{notice}</p> : null}
            {actionError ? <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-xs text-danger">{actionError}</p> : null}
            <ul className="space-y-4" aria-label="Acabados">
              {finishes.map((finish) => {
                const full = finish.images.length >= listing.max_per_colour;
                return (
                  <li key={finish.option.id} data-finish-id={finish.option.id} aria-labelledby={`acabado-${finish.option.id}`} className="min-w-0 rounded-xl border border-bd-border p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          aria-hidden="true"
                          className="h-6 w-6 shrink-0 rounded-full border border-foreground/25"
                          style={{ backgroundColor: /^#[0-9A-Fa-f]{6}$/.test(finish.option.swatch_hex) ? finish.option.swatch_hex : "transparent" }}
                        />
                        <h3 id={`acabado-${finish.option.id}`} tabIndex={-1} className="break-words text-sm font-semibold text-foreground focus:outline-none">{finish.option.label}</h3>
                        {finish.image_pending ? (
                          <span className="rounded-full border border-warning-border bg-warning-surface px-2.5 py-1 text-[11px] font-semibold text-warning">Imagen pendiente</span>
                        ) : (
                          <span className="rounded-full border border-bd-border px-2.5 py-1 text-[11px] font-semibold text-muted">
                            {finish.images.length === 1 ? "1 imagen" : `${finish.images.length} imágenes`}
                          </span>
                        )}
                      </div>
                      {canManage && !finish.not_colour && uploadingFor !== finish.option.id ? (
                        <button
                          type="button"
                          className={`${touch} ${internalButtonClass}`}
                          disabled={full}
                          title={full ? `Este color ya tiene ${listing.max_per_colour} imágenes.` : undefined}
                          onClick={() => setUploadingFor(finish.option.id)}
                        >
                          Subir imagen de {finish.option.label}
                        </button>
                      ) : null}
                    </div>
                    {finish.not_colour ? (
                      <p role="note" className="mt-2 text-xs text-warning">
                        Esta opción ya no es un color: sus imágenes siguen editables y se pueden quitar, pero no se suben nuevas.
                      </p>
                    ) : null}
                    {canManage && uploadingFor === finish.option.id ? (
                      <UploadForm
                        modelId={modelId}
                        modelName={modelName}
                        finish={finish}
                        kinds={kinds}
                        companyId={companyId}
                        onCancel={() => setUploadingFor(null)}
                        onUploaded={() => {
                          setUploadingFor(null);
                          setNotice(`Imagen subida a ${finish.option.label}.`);
                          void load();
                          hostChanged.current?.();
                        }}
                      />
                    ) : null}
                    {finish.images.length ? (
                      <ul aria-label={`Imágenes de ${finish.option.label}`} className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {finish.images.map((image, index) => (
                          <ImageCard
                            key={image.id}
                            image={image}
                            index={index}
                            count={finish.images.length}
                            colour={finish.option.label}
                            modelName={modelName}
                            kinds={kinds}
                            canManage={canManage}
                            busy={busy !== null}
                            onPatch={(data) => act(image.id, () => patchFinishImage(modelId, image.id, data, companyId), "Cambio guardado.")}
                            onMove={(step) => move(finish, index, step)}
                            onRemove={() => {
                              focusAfterRemoval.current = { colourId: finish.option.id, index };
                              void act(image.id, () => deleteFinishImage(modelId, image.id, companyId), `Imagen quitada de ${finish.option.label}.`);
                            }}
                          />
                        ))}
                      </ul>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </Panel>
  );
}
