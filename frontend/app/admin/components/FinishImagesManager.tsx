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
 * FASE 1.2 · Y: each picture says WHICH VIEW it is (main, front, back, side, other — or not said), and «Reemplazar foto»
 * gives it a new file keeping its place (order, primary, alt text, view): the new file says where it comes from and its
 * licence check starts over. A colour's swatch may be flagged approximate (pending commercial validation), and a model
 * may draw its own shade of a company colour («Ajustar muestra»); the panel says «Aproximada», the shop never does.
 *
 * EACH CHANGE IS SAVED WHEN MADE (upload, primary, order, alt text, provenance, licence check, removal); the server
 * is the authority and answers the gallery as it stands. Order is changed with buttons, so the keyboard does it too,
 * and focus stays on the picture that moved. Seeing needs `products.view`; every change, `products.manage`.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { PRODUCT_IMAGE_ACCEPT } from "@/app/lib/admin";
import { storefrontMediaStyle } from "@/app/lib/storefront-media";
import {
  FINISH_VIEW_LABELS, SWATCH_APPROXIMATE_CHECKBOX, SWATCH_APPROXIMATE_LABEL, SWATCH_UNSET_LABEL, deleteFinishImage,
  fetchFinishImages, finishViewLabel, patchFinishImage, provenanceErrors, reorderFinishImages, replaceFinishImage,
  setModelSwatch, uploadFinishImage,
  type Finish, type FinishImage, type FinishImagePatch, type FinishListing, type FinishView, type Provenance,
  type SourceKind,
} from "../lib/catalog-presentation-api";
import { CatalogApiError, updateAttributeOption } from "../lib/internal-api";
import { ImageDropzone } from "./ImageDropzone";
import { TRANSPARENCY_GRID_STYLE } from "./ImageUploadField";
import { ErrorBox, FOCUS_RING_ON_SCRIPT, Panel, Spinner } from "./InventoryUi";
import { formatBytes, rejectReason } from "./ProductGallery";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

/** The panel (and its `#acabados-title` heading, the last place focus can land). */
const PANEL_ID = "acabados";
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

const HEX = /^#[0-9A-Fa-f]{6}$/;
const validHex = (value: string) => (HEX.test(value.trim()) ? value.trim().toUpperCase() : null);

/** FASE 1.2 · Y — which view of the object a picture is; "" = not said. */
function ViewSelect({
  value, onChange, disabled, id, label, ariaLabel,
}: {
  value: FinishView; onChange: (value: FinishView) => void; disabled?: boolean;
  id?: string; label?: string; ariaLabel?: string;
}) {
  const own = useId();
  const fieldId = id ?? own;
  return (
    <div>
      {label ? <label htmlFor={fieldId} className="block text-xs text-muted">{label}</label> : null}
      <select
        id={fieldId}
        aria-label={ariaLabel}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as FinishView)}
        className={`mt-1.5 ${touch} ${internalInputClass}`}
      >
        <option value="">Sin indicar</option>
        {(Object.entries(FINISH_VIEW_LABELS) as [Exclude<FinishView, "">, string][]).map(([key, text]) => (
          <option key={key} value={key}>{text}</option>
        ))}
      </select>
    </div>
  );
}

/** «Aproximada»: a swatch chosen as an approximation, pending commercial validation. Panel only. */
function ApproximateBadge() {
  return (
    <span
      title="Muestra aproximada, pendiente de validación comercial"
      className="rounded-full border border-warning-border bg-warning-surface px-2.5 py-1 text-[11px] font-semibold text-warning"
    >
      {SWATCH_APPROXIMATE_LABEL}
    </span>
  );
}

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
  const [view, setView] = useState<FinishView>("");
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
      const image = await uploadFinishImage(modelId, finish.option.id, file, provenance, alt.trim(), companyId, view);
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
      <ViewSelect id={`${id}-view`} label="Vista (opcional)" value={view} onChange={setView} disabled={sending} />
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
// A swatch value (shared by the company form and the model form)
// ---------------------------------------------------------------------------

/**
 * A swatch value: the #RRGGBB field (it can be typed), a colour picker that fills it, and «Muestra aproximada». Nothing
 * is invented: no official code is suggested and no photo is recoloured. Saves only a valid #RRGGBB.
 */
function SwatchForm({
  fieldLabel, pickerLabel, initialHex = "", initialApproximate = false, submitLabel, onSave, children, fieldRef,
}: {
  /** The #RRGGBB field, for a host that moves focus to it. */
  fieldRef?: React.Ref<HTMLInputElement>;
  fieldLabel: string;
  pickerLabel: string;
  initialHex?: string;
  initialApproximate?: boolean;
  submitLabel: string;
  onSave: (hex: string, approximate: boolean) => Promise<void>;
  /** More actions beside «Guardar» (e.g. going back to the company's swatch). */
  children?: React.ReactNode;
}) {
  // Review of #377: an unset field starts EMPTY (a colour picker always holds a colour, so starting it at black made
  // «black» impossible to choose). The picker fills the field; the field can also be typed.
  const [value, setValue] = useState(initialHex);
  const [approximate, setApproximate] = useState(initialApproximate);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const fieldId = useId();
  const pickerId = useId();
  const checkId = useId();
  const hex = validHex(value);
  async function save() {
    if (!hex) return;
    setSaving(true);
    setFailed(null);
    try {
      await onSave(hex, approximate);
    } catch (err) {
      setFailed(message(err, "No se pudo guardar la muestra."));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label htmlFor={fieldId} className="text-muted">{fieldLabel}</label>
      <input
        ref={fieldRef}
        id={fieldId}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="#RRGGBB"
        maxLength={7}
        disabled={saving}
        spellCheck={false}
        className={`w-28 font-mono ${internalInputClass}`}
      />
      <label htmlFor={pickerId} className="sr-only">{pickerLabel}</label>
      <input
        id={pickerId}
        type="color"
        value={hex ?? "#808080"}
        onInput={(e) => setValue((e.target as HTMLInputElement).value)}
        onChange={(e) => setValue(e.target.value)}
        disabled={saving}
        className="h-11 w-11 cursor-pointer rounded-lg border border-bd-border bg-background p-1"
      />
      <label htmlFor={checkId} className={`flex items-center gap-2 text-foreground ${touch}`}>
        <input
          id={checkId}
          type="checkbox"
          className="h-4 w-4"
          checked={approximate}
          disabled={saving}
          onChange={(e) => setApproximate(e.target.checked)}
        />
        {SWATCH_APPROXIMATE_CHECKBOX}
      </label>
      <button type="button" className={smallButton} disabled={!hex || saving} onClick={() => void save()}>
        {saving ? "Guardando…" : submitLabel}
      </button>
      {children}
      {failed ? <p role="alert" className="w-full text-danger">{failed}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// A colour whose swatch the company has not configured (G, owner's visual order 4)
// ---------------------------------------------------------------------------

/**
 * «Muestra sin configurar»: an imported colour still holding the importer's neutral default. The shop draws no dot
 * for it (only its name, or its finish picture); here the company picks its own swatch — flagged approximate when it
 * is not validated yet.
 */
function SwatchSetter({ colour, canManage, companyId, onSaved }: {
  colour: { id: number; label: string };
  canManage: boolean;
  companyId: number | null;
  onSaved: () => void;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs" role="group" aria-label={`Muestra de ${colour.label}`}>
      <span className="rounded-full border border-dashed border-bd-border px-2 py-0.5 font-semibold text-muted">{SWATCH_UNSET_LABEL}</span>
      {canManage ? (
        <SwatchForm
          fieldLabel={`Muestra de ${colour.label} (#RRGGBB)`}
          pickerLabel={`Elegir la muestra de ${colour.label}`}
          submitLabel="Guardar muestra"
          onSave={async (hex, approximate) => {
            await updateAttributeOption(colour.id, { swatch_hex: hex, swatch_is_approximate: approximate }, companyId);
            onSaved();
          }}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// «Ajustar muestra»: the company's swatch (shared by every model) and this model's own (FASE 1.2 · Y)
// ---------------------------------------------------------------------------

function SwatchEditor({ finish, modelId, modelName, companyId, onSaved }: {
  finish: Finish;
  modelId: number;
  modelName: string;
  companyId: number | null;
  onSaved: (notice: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const regionId = useId();
  const option = finish.option;
  const ownHex = option.model_swatch_hex ?? "";
  const ownApproximate = option.model_swatch_is_approximate ?? false;
  const companyApproximate = option.swatch_is_approximate ?? false;
  // Review of #388: after «Usar la muestra de la empresa» its button is gone; focus goes to the model's field (a
  // new one: the form is keyed on what the server holds, so it shows the value saved, never the old one).
  const ownField = useRef<HTMLInputElement>(null);
  const focusOwnField = useRef(false);
  useEffect(() => {
    if (!focusOwnField.current || ownHex) return;
    focusOwnField.current = false;
    ownField.current?.focus();
  });
  return (
    <div className="mt-2">
      <button
        type="button"
        className={smallButton}
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((value) => !value)}
      >
        Ajustar muestra de {option.label}
      </button>
      {open ? (
        <div id={regionId} className="mt-2 space-y-3 rounded-lg border border-bd-border p-3">
          {option.swatch_set !== false ? (
            <fieldset className="min-w-0 space-y-2">
              <legend className="text-xs font-semibold text-foreground">Muestra de la empresa (todos los modelos con {option.label})</legend>
              <SwatchForm
                key={`${option.swatch_hex}|${companyApproximate}`}
                fieldLabel={`Muestra de ${option.label} para la empresa (#RRGGBB)`}
                pickerLabel={`Elegir la muestra de ${option.label} para la empresa`}
                initialHex={option.swatch_hex}
                initialApproximate={companyApproximate}
                submitLabel="Guardar muestra de la empresa"
                onSave={async (hex, approximate) => {
                  await updateAttributeOption(option.id, { swatch_hex: hex, swatch_is_approximate: approximate }, companyId);
                  onSaved(`Muestra de ${option.label} guardada para la empresa.`);
                }}
              />
            </fieldset>
          ) : null}
          {option.model_swatch_editable ? (
            <fieldset className="min-w-0 space-y-2">
              <legend className="text-xs font-semibold text-foreground">Muestra solo para {modelName}</legend>
              <p className="text-xs text-muted">
                {ownHex ? `Este modelo usa su propia muestra (${ownHex}).` : "Vacía: este modelo usa la muestra de la empresa."}
              </p>
              <SwatchForm
                key={`${ownHex}|${ownApproximate}`}
                fieldRef={ownField}
                fieldLabel={`Muestra de ${option.label} solo en ${modelName} (#RRGGBB)`}
                pickerLabel={`Elegir la muestra de ${option.label} solo en ${modelName}`}
                initialHex={ownHex}
                initialApproximate={ownApproximate}
                submitLabel="Guardar muestra de este modelo"
                onSave={async (hex, approximate) => {
                  await setModelSwatch(modelId, { option_id: option.id, swatch_hex: hex, swatch_is_approximate: approximate }, companyId);
                  onSaved(`Muestra de ${option.label} guardada para ${modelName}.`);
                }}
              >
                {ownHex ? (
                  <UseCompanySwatch
                    label={option.label}
                    onClear={async () => {
                      await setModelSwatch(modelId, { option_id: option.id, swatch_hex: "" }, companyId);
                      focusOwnField.current = true;
                      onSaved(`${modelName} vuelve a usar la muestra de ${option.label} de la empresa.`);
                    }}
                  />
                ) : null}
              </SwatchForm>
            </fieldset>
          ) : option.model_swatch_editable === false ? (
            <p role="note" className="text-xs text-muted">
              Para darle a {modelName} una muestra propia de {option.label}, márcalo antes en «Opciones de este modelo».
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function UseCompanySwatch({ label, onClear }: { label: string; onClear: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        className={smallButton}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setFailed(null);
          try { await onClear(); } catch (err) { setFailed(message(err, "No se pudo guardar la muestra.")); } finally { setBusy(false); }
        }}
      >
        Usar la muestra de {label} de la empresa
      </button>
      {failed ? <p role="alert" className="w-full text-danger">{failed}</p> : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// «Reemplazar foto»: a new file in the same place (FASE 1.2 · Y)
// ---------------------------------------------------------------------------

function ReplaceForm({
  modelId, image, name, kinds, companyId, firstFieldRef, onReplaced, onCancel,
}: {
  modelId: number; image: FinishImage; name: string; kinds: { value: SourceKind; label: string }[];
  companyId: number | null; firstFieldRef?: React.Ref<HTMLSelectElement>;
  onReplaced: () => void; onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [provenance, setProvenance] = useState<Provenance>(EMPTY_PROVENANCE);
  const [errors, setErrors] = useState<Partial<Record<keyof Provenance, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  function choose(files: File[]) {
    const chosen = files[0];
    if (!chosen) return;
    const reason = rejectReason(chosen);
    if (reason) { setFile(null); setFileError(`${chosen.name}: ${reason}.`); return; }
    setFileError(null);
    setFile(chosen);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const found = provenanceErrors(provenance);
    setErrors(found);
    if (!file) setFileError("Elige la foto nueva.");
    if (!file || Object.keys(found).length) return;
    setSending(true);
    setFormError(null);
    try {
      await replaceFinishImage(modelId, image.id, file, provenance, companyId);
      onReplaced();
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
    <form onSubmit={submit} noValidate aria-label={`Reemplazar la ${name}`} className="space-y-3 rounded-lg border border-bd-border p-3">
      <p className="text-xs text-muted">
        La foto nueva ocupa el mismo lugar: orden, principal, texto alternativo y vista se mantienen. Indica de dónde sale:
        la verificación de la licencia empieza de nuevo.
      </p>
      <ImageDropzone
        label={file ? "Elegir otra foto nueva" : "Elegir la foto nueva"}
        accept={PRODUCT_IMAGE_ACCEPT}
        multiple={false}
        onFiles={choose}
        disabled={sending}
        hint="PNG (con transparencia), JPEG o WebP, hasta 8 MB."
      />
      {file ? <p className="min-w-0 break-all text-xs text-foreground">{file.name} · {formatBytes(file.size)}</p> : null}
      {fileError ? <p role="alert" className="text-xs text-danger">{fileError}</p> : null}
      <ProvenanceFields value={provenance} onChange={setProvenance} errors={errors} kinds={kinds} disabled={sending} firstFieldRef={firstFieldRef} />
      {formError ? <p role="alert" className="text-xs text-danger">{formError}</p> : null}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={smallButton} onClick={onCancel} disabled={sending}>Cancelar</button>
        <button type="submit" className={`${touch} ${internalPrimaryButtonClass} text-xs`} disabled={sending}>
          {sending ? "Reemplazando…" : "Reemplazar foto"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// One picture
// ---------------------------------------------------------------------------

function ImageCard({
  image, index, count, colour, modelId, modelName, kinds, canManage, companyId, busy, onPatch, onMove, onRemove,
  onReplaced,
}: {
  image: FinishImage; index: number; count: number; colour: string; modelId: number; modelName: string;
  kinds: { value: SourceKind; label: string }[]; canManage: boolean; companyId: number | null; busy: boolean;
  onPatch: (data: FinishImagePatch) => Promise<boolean>; onMove: (step: -1 | 1) => void; onRemove: () => void;
  /** FASE 1.2 · Y: the picture took a new file; the host re-reads the gallery and says so. */
  onReplaced: () => void;
}) {
  const position = index + 1;
  const name = `imagen ${position} de ${colour}`;
  // null = what the server holds; a string = what the person is typing (saved on leaving the field).
  const [altDraft, setAltDraft] = useState<string | null>(null);
  const alt = altDraft ?? image.alt_text;
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [provenance, setProvenance] = useState<Provenance>(EMPTY_PROVENANCE);
  const [errors, setErrors] = useState<Partial<Record<keyof Provenance, string>>>({});
  // Review of #374 (a11y): opening or closing a step never drops focus to the start of the page.
  const confirmYes = useRef<HTMLButtonElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const originField = useRef<HTMLSelectElement>(null);
  const replaceButton = useRef<HTMLButtonElement>(null);
  const replaceOrigin = useRef<HTMLSelectElement>(null);
  const focusNext = useRef<"confirm" | "remove" | "edit" | "origin" | "replace" | "replaceOrigin" | null>(null);
  useEffect(() => {
    const target = focusNext.current;
    if (!target) return;
    focusNext.current = null;
    ({
      confirm: confirmYes, remove: removeButton, edit: editButton, origin: originField, replace: replaceButton,
      replaceOrigin,
    })[target].current?.focus();
  });

  function openConfirm() { focusNext.current = "confirm"; setConfirming(true); }
  function cancelConfirm() { focusNext.current = "remove"; setConfirming(false); }
  function closeEditing() { focusNext.current = "edit"; setEditing(false); }
  function openReplacing() { focusNext.current = "replaceOrigin"; setReplacing(true); }
  function closeReplacing() { focusNext.current = "replace"; setReplacing(false); }

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
        <div className="flex flex-wrap gap-x-1"><dt className="text-muted">Vista:</dt><dd className="text-foreground">{finishViewLabel(image.view)}</dd></div>
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
          <ViewSelect
            label="Vista"
            ariaLabel={`Vista de la ${name}`}
            value={image.view ?? ""}
            disabled={busy}
            onChange={(view) => { if (view !== (image.view ?? "")) void onPatch({ view }); }}
          />
          {ownPhoto && !verified ? null : (
          <label className={`flex items-center gap-2 text-xs text-foreground ${touch}`}>
            <input
              type="checkbox"
              data-verify
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

          {replacing ? (
            <ReplaceForm
              modelId={modelId}
              image={image}
              name={name}
              kinds={kinds}
              companyId={companyId}
              firstFieldRef={replaceOrigin}
              onCancel={closeReplacing}
              onReplaced={() => { setReplacing(false); onReplaced(); }}
            />
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
              {!replacing ? (
                <button ref={replaceButton} type="button" aria-label={`Reemplazar foto: ${name}`} disabled={busy} onClick={openReplacing} className={smallButton}>
                  Reemplazar foto
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

/** The swatch a colour is drawn with on this model; an older server sends only the company's (and `swatch_set`). */
const shownSwatch = (finish: Finish) => (
  finish.option.shown_swatch_hex !== undefined
    ? finish.option.shown_swatch_hex
    : finish.option.swatch_set !== false ? finish.option.swatch_hex : ""
);

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
  // Review of #374 (a11y): after a removal, the picture now in its place (or the one before), else the colour,
  // else (a colour outside the palette leaves the listing with its last picture) the panel's title.
  const focusAfterRemoval = useRef<{ colourId: number; index: number } | null>(null);
  // After «Revisé la licencia»: its checkbox again, or the picture's card when the checkbox is gone (an own photo
  // that is no longer verified has nothing to verify). A checkbox disabled while saving loses focus in a browser.
  const focusAfterVerify = useRef<number | null>(null);
  // After «Guardar muestra»: the colour's heading (the control that had focus is gone once the swatch is set).
  const focusAfterSwatch = useRef<number | null>(null);
  // After «Reemplazar foto»: the picture's card (its form is gone; the picture kept its place).
  const focusAfterReplace = useRef<number | null>(null);
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
    (card
      ?? root.current.querySelector<HTMLElement>(`#acabado-${wanted.colourId}`)
      ?? document.getElementById(`${PANEL_ID}-title`))?.focus();
  }, [listing, busy]);

  useEffect(() => {
    const colourId = focusAfterSwatch.current;
    if (colourId === null || !root.current) return;
    focusAfterSwatch.current = null;
    (root.current.querySelector<HTMLElement>(`#acabado-${colourId}`) ?? document.getElementById(`${PANEL_ID}-title`))?.focus();
  }, [listing]);

  useEffect(() => {
    const imageId = focusAfterReplace.current;
    if (imageId === null || !root.current) return;
    focusAfterReplace.current = null;
    (root.current.querySelector<HTMLElement>(`[data-image-id="${imageId}"]`) ?? document.getElementById(`${PANEL_ID}-title`))?.focus();
  }, [listing]);

  useEffect(() => {
    const imageId = focusAfterVerify.current;
    if (imageId === null || !root.current || busy !== null) return;
    focusAfterVerify.current = null;
    const card = root.current.querySelector<HTMLElement>(`[data-image-id="${imageId}"]`);
    const check = card?.querySelector<HTMLInputElement>("input[data-verify]");
    (check && !check.disabled ? check : card ?? document.getElementById(`${PANEL_ID}-title`))?.focus();
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
      id={PANEL_ID}
      titleFocusable
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
                        {/* FASE 1.2 · Y: what is drawn — this model's own swatch, else the company's, else nothing. */}
                        {HEX.test(shownSwatch(finish)) ? (
                          <span
                            aria-hidden="true"
                            data-testid="finish-swatch"
                            className="h-6 w-6 shrink-0 rounded-full border border-foreground/25"
                            style={{ backgroundColor: shownSwatch(finish) }}
                          />
                        ) : null}
                        <h3 id={`acabado-${finish.option.id}`} tabIndex={-1} className={`break-words text-sm font-semibold text-foreground ${FOCUS_RING_ON_SCRIPT}`}>{finish.option.label}</h3>
                        {finish.image_pending ? (
                          <span className="rounded-full border border-warning-border bg-warning-surface px-2.5 py-1 text-[11px] font-semibold text-warning">Imagen pendiente</span>
                        ) : (
                          <span className="rounded-full border border-bd-border px-2.5 py-1 text-[11px] font-semibold text-muted">
                            {finish.images.length === 1 ? "1 imagen" : `${finish.images.length} imágenes`}
                          </span>
                        )}
                        {finish.option.shown_swatch_is_approximate ? <ApproximateBadge /> : null}
                        {finish.option.model_swatch_hex ? (
                          <span className="text-[11px] text-muted">Muestra propia de este modelo</span>
                        ) : null}
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
                    {finish.option.swatch_set === false && !finish.not_colour ? (
                      <SwatchSetter
                        colour={finish.option}
                        canManage={canManage}
                        companyId={companyId}
                        onSaved={() => {
                          setNotice(`Muestra de ${finish.option.label} guardada.`);
                          // Review of #377: the control leaves with the saved swatch; focus goes to the colour.
                          focusAfterSwatch.current = finish.option.id;
                          void load();
                          hostChanged.current?.();
                        }}
                      />
                    ) : null}
                    {canManage && !finish.not_colour ? (
                      <SwatchEditor
                        finish={finish}
                        modelId={modelId}
                        modelName={modelName}
                        companyId={companyId}
                        onSaved={(text) => {
                          setNotice(text);
                          void load();
                          hostChanged.current?.();
                        }}
                      />
                    ) : null}
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
                            modelId={modelId}
                            modelName={modelName}
                            kinds={kinds}
                            canManage={canManage}
                            companyId={companyId}
                            busy={busy !== null}
                            onPatch={(data) => {
                              if ("license_verified" in data) focusAfterVerify.current = image.id;
                              return act(image.id, () => patchFinishImage(modelId, image.id, data, companyId), "Cambio guardado.");
                            }}
                            onMove={(step) => move(finish, index, step)}
                            onRemove={() => {
                              focusAfterRemoval.current = { colourId: finish.option.id, index };
                              void act(image.id, () => deleteFinishImage(modelId, image.id, companyId), `Imagen quitada de ${finish.option.label}.`);
                            }}
                            onReplaced={() => {
                              focusAfterReplace.current = image.id;
                              setActionError(null);
                              setNotice(`Imagen ${index + 1} de ${finish.option.label} reemplazada.`);
                              void load();
                              hostChanged.current?.();
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
