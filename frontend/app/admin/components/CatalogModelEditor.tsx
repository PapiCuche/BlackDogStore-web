"use client";

/**
 * Productos › Modelos › un modelo — DEVICE-COMMERCE-01 · DC2.
 *
 * Its own fields, and the products that are its variants. A variant is an
 * existing product placed in the model with a line (new or used) and one
 * option per attribute; the server refuses a combination that is already
 * taken or an option of another attribute, and says which field is wrong.
 */

import Link from "next/link";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import {
  fetchAdminCategories, fetchAdminProducts, fetchProductImages,
  type AdminCategory, type AdminProduct, type AdminProductImage,
} from "@/app/lib/admin";
import {
  DEFAULT_LINE_LABELS,
  fetchAdminCatalogModel,
  fetchAdminCatalogModels,
  fetchCatalogAttributes,
  fetchCatalogSettings,
  fetchModelPalette,
  setProductVariant,
  updateAdminCatalogModel,
  type AdminCatalogAttribute,
  type AdminCatalogModel,
  type AdminCatalogModelDetail,
  type AdminModelPalette,
  type AdminModelVariant,
  type CatalogLine,
} from "../lib/internal-api";
import { paletteAttributes } from "../lib/palette";
import { RichText } from "../../components/RichText";
import { DESCRIPTION_MAX_LENGTH } from "@/app/lib/markdown-lite";
import { DescriptionEditor } from "./DescriptionEditor";
import { TRANSPARENCY_GRID_STYLE } from "./ImageUploadField";
import { ModelPalettePanel, PaletteStatusBadge } from "./ModelPalettePanel";
import { ProductGallery, type GalleryWork } from "./ProductGallery";
import { EmptyBox, ErrorBox, Panel, Spinner, TableWrap, Td, Th, formatSoles } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

type Draft = {
  name: string; slug: string; category_id: string; description: string; image_url: string;
  is_published_online: boolean; sort_order: string;
};

const toDraft = (model: AdminCatalogModelDetail): Draft => ({
  name: model.name,
  slug: model.slug,
  category_id: model.category_id ? String(model.category_id) : "",
  description: model.description ?? "",
  image_url: model.image_url ?? "",
  is_published_online: model.is_published_online,
  sort_order: String(model.sort_order ?? 0),
});

/** The model's cover: a thumbnail when the address can be shown, and the address itself folded away. */
/** An address the panel may show as an image: http(s) or a path of this site, never `javascript:` or `//host`. */
const isShowable = (address: string) =>
  /^https?:\/\//i.test(address) || (address.startsWith("/") && !address.startsWith("//"));

function ModelImage({ url, name }: { url?: string | null; name: string }) {
  const [broken, setBroken] = useState(false);
  const address = (url || "").trim();
  if (!address) return <span>Sin imagen</span>;
  const showable = isShowable(address);
  return (
    <div className="flex flex-wrap items-start gap-3">
      {showable && !broken ? (
        // eslint-disable-next-line @next/next/no-img-element -- staff preview of an external or uploaded cover
        <img
          src={address}
          alt={`Imagen de ${name}`}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
          style={TRANSPARENCY_GRID_STYLE}
          className="h-24 w-24 shrink-0 rounded-lg border border-bd-border object-contain"
        />
      ) : (
        <span className="text-xs text-muted">{broken ? "No se pudo cargar la imagen." : "La dirección no es una imagen que se pueda mostrar."}</span>
      )}
      <details className="min-w-0 flex-1 text-xs">
        <summary className="cursor-pointer text-muted hover:text-foreground">Ver la dirección de la imagen</summary>
        <p className="mt-1 break-all font-mono text-foreground">{address}</p>
        {showable ? (
          <a href={address} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-foreground underline underline-offset-4">Abrir la imagen original</a>
        ) : null}
      </details>
    </div>
  );
}

/**
 * CATALOG-VARIANT-IMAGES: the picture a variant shows, small, on squares so a transparent PNG reads as
 * transparent; and how many it has.
 */
function VariantThumb({ variant }: { variant: AdminModelVariant }) {
  const [broken, setBroken] = useState(false);
  const address = (variant.image_url || "").trim();
  const count = variant.image_count ?? 0;
  return (
    <span className="flex min-w-0 items-center gap-2.5">
      <span
        data-transparency-grid="true"
        style={TRANSPARENCY_GRID_STYLE}
        className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-md border border-bd-border"
      >
        {address && isShowable(address) && !broken ? (
          // eslint-disable-next-line @next/next/no-img-element -- staff thumbnail of an uploaded or external image
          <img
            src={address}
            alt={`Imagen de ${variant.name}`}
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setBroken(true)}
            className="max-h-full max-w-full object-contain"
          />
        ) : null}
      </span>
      <span className="text-xs text-muted">{count === 0 ? "Sin imágenes" : plural(count, "imagen", "imágenes")}</span>
    </span>
  );
}

function AddVariantForm({
  modelId, companyId, attributes, restricted, lineLabels, onSaved, onCancel,
}: {
  modelId: number;
  companyId: number | null;
  /** A1: with a palette, only its attributes and only its options (the server refuses the rest anyway). */
  attributes: AdminCatalogAttribute[];
  restricted: boolean;
  lineLabels: Record<CatalogLine, string>;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<AdminProduct[]>([]);
  const [searching, setSearching] = useState(false);
  const [product, setProduct] = useState<AdminProduct | null>(null);
  const [line, setLine] = useState<CatalogLine>("new");
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [field, setField] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function find(event: React.FormEvent) {
    event.preventDefault();
    setSearching(true);
    setError(null);
    try {
      const page = await fetchAdminProducts({ search: search.trim(), page_size: 10, company: companyId });
      setResults(page.results);
    } catch (err) {
      setError(message(err, "No se pudieron buscar productos."));
    } finally {
      setSearching(false);
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!product) { setError("Elige el producto que será la variante."); return; }
    if (restricted && attributes.some((attribute) => !chosen[attribute.id])) {
      // The server would answer `variant_incomplete`: every attribute of the palette takes one option.
      setError("Elige una opción de cada atributo de la paleta.");
      setField("options");
      return;
    }
    setSaving(true);
    setError(null);
    setField(null);
    try {
      await setProductVariant(product.id, {
        model_id: modelId,
        condition_line: line,
        option_ids: Object.values(chosen).filter(Boolean).map(Number),
      }, companyId);
      onSaved();
    } catch (err) {
      setError(message(err, "No se pudo guardar la variante."));
      setField((err as { field?: string | null }).field ?? null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mb-6 space-y-4 rounded-lg border border-bd-border bg-background p-4">
      <form onSubmit={find} className="flex flex-wrap items-end gap-3" role="search" aria-label="Buscar producto">
        <label className="block min-w-48 flex-1 text-xs text-muted">
          Producto
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Nombre del producto" className={`mt-1.5 ${internalInputClass}`} />
        </label>
        <button type="submit" className={internalButtonClass} disabled={searching}>{searching ? "Buscando…" : "Buscar"}</button>
      </form>

      {results.length > 0 ? (
        <fieldset>
          <legend className="text-xs text-muted">Resultados</legend>
          <div className="mt-2 max-h-56 space-y-1 overflow-y-auto">
            {results.map((row) => (
              <label key={row.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-foreground/[0.03]">
                <input type="radio" name="variant-product" checked={product?.id === row.id} onChange={() => setProduct(row)} />
                <span className="min-w-0 flex-1 truncate">{row.name}</span>
                <span className="text-xs text-muted">{formatSoles(row.price)}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}

      <form onSubmit={save} className="grid gap-3 md:grid-cols-3" aria-label="Agregar variante">
        <p className="text-sm text-foreground md:col-span-3">
          {product ? <>Producto elegido: <strong>{product.name}</strong></> : <span className="text-muted">Busca y elige un producto.</span>}
          {field === "product" ? <span className="ml-2 text-danger">← revisa este dato</span> : null}
        </p>
        <label className="block text-xs text-muted">
          Línea
          <select value={line} onChange={(e) => setLine(e.target.value as CatalogLine)} className={`mt-1.5 ${internalInputClass}`} aria-invalid={field === "condition_line" ? true : undefined}>
            <option value="new">{lineLabels.new}</option>
            <option value="used">{lineLabels.used}</option>
          </select>
        </label>
        {restricted ? (
          <p className="text-xs text-muted md:col-span-3">
            Sólo las opciones que admite este modelo (<a href="#paleta" className="underline underline-offset-4 hover:text-foreground">Opciones de este modelo</a>).
          </p>
        ) : null}
        {attributes.map((attribute) => (
          <label key={attribute.id} className="block text-xs text-muted">
            {attribute.name}
            <select
              value={chosen[attribute.id] ?? ""}
              onChange={(e) => setChosen({ ...chosen, [attribute.id]: e.target.value })}
              aria-invalid={field === "option_ids" || field === "options" ? true : undefined}
              className={`mt-1.5 ${internalInputClass}`}
            >
              <option value="">{restricted ? "Elige…" : "No aplica"}</option>
              {[...attribute.options].sort((a, b) => a.sort_order - b.sort_order).map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </label>
        ))}
        {error ? <p role="alert" className="text-sm text-danger md:col-span-3">{error}</p> : null}
        <div className="flex justify-end gap-2 md:col-span-3">
          <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
          <button type="submit" className={internalPrimaryButtonClass} disabled={saving || !product}>{saving ? "Guardando…" : "Guardar variante"}</button>
        </div>
      </form>
    </div>
  );
}

const optionKey = (chosen: Record<number, string>) =>
  Object.values(chosen).filter(Boolean).map(Number).sort((a, b) => a - b).join("-");

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * CATALOG-VARIANTS-UX — «Editar variante». The same product, never a new one:
 * its model, line and options go through the variant endpoint, its price and
 * publication through the product. Stock and cost are not edited here.
 *
 * What the variant already carries decides what may change, and the server
 * decides again on every save; the form only explains it beforehand.
 */
function EditVariantForm({
  variant, modelId, models, attributes, restricted, noAttributes, lineLabels, companyId, onSaved, onCancel,
}: {
  variant: AdminModelVariant;
  modelId: number;
  models: AdminCatalogModel[];
  /** A1: with a palette, its attributes and options; an option the variant holds outside it is still listed. */
  attributes: AdminCatalogAttribute[];
  restricted: boolean;
  /** The company has no attributes yet: nothing to choose as capacity or colour. */
  noAttributes: boolean;
  lineLabels: Record<CatalogLine, string>;
  companyId: number | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const initialOptions = () => Object.fromEntries(
    variant.options.map((option) => [option.attribute_id, String(option.option_id)]),
  ) as Record<number, string>;
  const [targetModel, setTargetModel] = useState(String(modelId));
  const [line, setLine] = useState<CatalogLine>(variant.condition_line);
  const [chosen, setChosen] = useState<Record<number, string>>(initialOptions);
  const [price, setPrice] = useState(variant.price);
  const [published, setPublished] = useState(Boolean(variant.is_published_online));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [field, setField] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const identityRule = variant.identity_change ?? "free";
  const lineRule = variant.line_change ?? "free";
  const history = variant.history ?? { sold: 0, units: 0, reserved: 0 };
  const identityLocked = identityRule === "blocked";
  const lineLocked = lineRule === "blocked";

  const identityChanged =
    Number(targetModel) !== modelId || optionKey(chosen) !== optionKey(initialOptions());
  const lineChanged = line !== variant.condition_line;
  const variantChanged = identityChanged || lineChanged;
  const priceChanged = Number(price) !== Number(variant.price);
  const publicationChanged = published !== Boolean(variant.is_published_online);
  // The server asks a superior's reason for ANY change of a variant with
  // history once its identity is `superior` — the line included.
  const needsReason = identityRule === "superior" && variantChanged;

  // The current model is always an option, even before the list arrives.
  const modelChoices = models.some((m) => m.id === modelId)
    ? models
    : [{ id: modelId, name: "Este modelo" } as AdminCatalogModel, ...models];

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setField(null);
    if (!variantChanged && !priceChanged && !publicationChanged) {
      setError("No hay cambios que guardar.");
      return;
    }
    if (priceChanged && (price.trim() === "" || !Number.isFinite(Number(price)) || Number(price) < 0)) {
      setError("Escribe un precio válido.");
      setField("price");
      return;
    }
    if (needsReason && reason.trim().length < 5) {
      setError("Escribe el motivo del cambio (al menos 5 caracteres): quedará en la auditoría.");
      setField("reason");
      return;
    }
    setSaving(true);
    const productFields = {
      ...(priceChanged ? { price: price.trim() } : {}),
      ...(publicationChanged ? { is_published_online: published } : {}),
    };
    try {
      // CATALOG-EDITOR-FIX-01: ONE save, ONE writer. Price and publication travel with the variant (its
      // identity as it is when it does not change) and the server keeps all of it or nothing. The screen's
      // company goes with it: a MASTER who switched company saves in THAT company, not the browser's last.
      await setProductVariant(variant.product_id, {
        model_id: Number(targetModel),
        condition_line: line,
        option_ids: Object.values(chosen).filter(Boolean).map(Number),
        ...(needsReason ? { reason: reason.trim() } : {}),
        ...productFields,
      }, companyId);
      onSaved();
    } catch (err) {
      setError(message(err, "No se pudo guardar la variante."));
      setField((err as { field?: string | null }).field ?? null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="grid gap-3 rounded-lg border border-bd-border bg-background p-4 text-left md:grid-cols-3" aria-label={`Editar variante ${variant.name}`}>
      <p className="text-sm text-foreground md:col-span-3">
        Editando <strong>{variant.name}</strong>. Es el mismo producto: conserva su stock, sus equipos y su historial.
      </p>
      {noAttributes ? (
        <p className="text-xs text-muted md:col-span-3">
          La empresa todavía no tiene atributos: para elegir capacidad o color, créalos en{" "}
          <Link href="/admin/products/models#atributos" className="underline underline-offset-4 hover:text-foreground">
            Atributos y opciones
          </Link>{" "}y vuelve a esta variante.
        </p>
      ) : null}

      {identityRule === "superior" ? (
        <p role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-warning md:col-span-3">
          Ya tiene {plural(history.sold, "venta", "ventas")} y {plural(history.units, "equipo", "equipos")}: cambiar modelo u
          opciones lo hace un administrador de la empresa, con motivo.
        </p>
      ) : null}
      {identityLocked ? (
        <p role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-warning md:col-span-3">
          Hay equipos apartados o en un carrito ({history.reserved}): su modelo, sus opciones y su línea no cambian
          hasta que se liberen esas reservas. El precio y la publicación sí se pueden cambiar.
        </p>
      ) : null}

      <label className="block text-xs text-muted">
        Modelo
        <select
          value={targetModel}
          onChange={(e) => setTargetModel(e.target.value)}
          disabled={identityLocked}
          aria-invalid={field === "product_model" ? true : undefined}
          className={`mt-1.5 ${internalInputClass}`}
        >
          {modelChoices.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </label>
      <label className="block text-xs text-muted">
        Línea
        <select
          value={line}
          onChange={(e) => setLine(e.target.value as CatalogLine)}
          disabled={lineLocked}
          aria-invalid={field === "condition_line" ? true : undefined}
          className={`mt-1.5 ${internalInputClass}`}
        >
          <option value="new">{lineLabels.new}</option>
          <option value="used">{lineLabels.used}</option>
        </select>
        {lineLocked && !identityLocked ? (
          <span className="mt-1 block text-xs text-muted">
            Tiene equipos propios: su línea es la de esos equipos y no cambia. Registra los equipos en la variante que corresponde.
          </span>
        ) : null}
      </label>
      {restricted && variant.palette_status && variant.palette_status !== "variante_existente" ? (
        <p role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-warning md:col-span-3">
          Esta variante está {variant.palette_status === "incompleta" ? "incompleta" : "fuera de la paleta"} del modelo. Conserva su
          historial; si la cambias, sólo se admiten las opciones de la paleta.
        </p>
      ) : null}
      {attributes.map((attribute) => (
        <label key={attribute.id} className="block text-xs text-muted">
          {attribute.name}
          <select
            value={chosen[attribute.id] ?? ""}
            onChange={(e) => setChosen({ ...chosen, [attribute.id]: e.target.value })}
            disabled={identityLocked}
            aria-invalid={field === "options" ? true : undefined}
            className={`mt-1.5 ${internalInputClass}`}
          >
            <option value="">{restricted ? "Elige…" : "No aplica"}</option>
            {[...attribute.options].sort((a, b) => a.sort_order - b.sort_order).map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
      ))}
      <label className="block text-xs text-muted">
        Precio (S/)
        <input
          type="number"
          min="0"
          step="0.01"
          inputMode="decimal"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          aria-invalid={field === "price" ? true : undefined}
          className={`mt-1.5 ${internalInputClass}`}
        />
      </label>
      <label className="flex items-center gap-2 self-end pb-2 text-sm text-foreground">
        <input type="checkbox" checked={published} onChange={(e) => setPublished(e.target.checked)} />
        Publicado en la tienda
      </label>

      {needsReason ? (
        <label className="block text-xs text-muted md:col-span-3">
          Motivo del cambio (obligatorio)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            maxLength={500}
            aria-required="true"
            aria-invalid={field === "reason" ? true : undefined}
            className={`mt-1.5 ${internalInputClass}`}
          />
          <span className="mt-1 block text-xs text-muted">Queda en la auditoría junto con su historial.</span>
        </label>
      ) : null}

      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs md:col-span-3">
        <Link href={`/admin/products/${variant.product_id}`} className="text-muted underline underline-offset-4 hover:text-foreground">
          Fotos y datos del producto
        </Link>
        <Link href={`/admin/products/${variant.product_id}/stock-card`} className="text-muted underline underline-offset-4 hover:text-foreground">
          Costo y stock
        </Link>
      </p>

      {error ? <p role="alert" className="text-sm text-danger md:col-span-3">{error}</p> : null}
      <div className="flex justify-end gap-2 md:col-span-3">
        <button type="button" className={internalButtonClass} onClick={onCancel} disabled={saving}>Cancelar</button>
        <button type="submit" className={internalPrimaryButtonClass} disabled={saving}>{saving ? "Guardando…" : "Guardar cambios"}</button>
      </div>
    </form>
  );
}

export function CatalogModelEditor({
  modelId, canManage, companyId,
}: { modelId: number; canManage: boolean; companyId: number | null }) {
  const [model, setModel] = useState<AdminCatalogModelDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [attributes, setAttributes] = useState<AdminCatalogAttribute[]>([]);
  const [lineLabels, setLineLabels] = useState<Record<CatalogLine, string>>(DEFAULT_LINE_LABELS);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  // Quitar una variante con historial: lo hace un superior, con motivo.
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [removeReason, setRemoveReason] = useState("");
  const [models, setModels] = useState<AdminCatalogModel[]>([]);
  const [attributesLoaded, setAttributesLoaded] = useState(false);
  // A1: the palette of the model as the server last answered it (null while it loads).
  const [palette, setPalette] = useState<AdminModelPalette | null>(null);
  const [paletteError, setPaletteError] = useState<string | null>(null);
  const paletteRequest = useRef(0);
  // CATALOG-EDITOR-FIX-01: the model's data is shown; «Editar modelo» opens the form.
  // «?editar=modelo» (the list's «Editar modelo») opens the form. This editor mounts only in the browser,
  // once the session is known, so the address can be read here.
  const [editingModel, setEditingModel] = useState(
    () => canManage && typeof window !== "undefined"
      && new URLSearchParams(window.location.search).get("editar") === "modelo",
  );
  const [slugConfirmed, setSlugConfirmed] = useState(false);
  // CATALOG-VARIANT-IMAGES: the variant whose pictures are open, and its gallery as the server keeps it.
  const [galleryFor, setGalleryFor] = useState<number | null>(null);
  const [galleryImages, setGalleryImages] = useState<AdminProductImage[] | null>(null);
  const [galleryError, setGalleryError] = useState<string | null>(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const galleryButtons = useRef(new Map<number, HTMLButtonElement>());
  const galleryHeading = useRef<HTMLHeadingElement>(null);
  // A late answer for the variant opened before must not fill the gallery of the one open now.
  const galleryRequest = useRef(0);
  // The open variant, for callbacks that arrive late; and what its gallery still holds (not yet uploaded).
  const galleryOpen = useRef<number | null>(null);
  const [galleryWork, setGalleryWork] = useState<GalleryWork>({ staged: 0, sending: false });
  const galleryLocked = galleryWork.staged > 0 || galleryWork.sending;
  const onGalleryWork = useCallback((work: GalleryWork) => {
    setGalleryWork((current) => (
      current.staged === work.staged && current.sending === work.sending ? current : work));
  }, []);

  // While «Editar modelo» is open a reload (after a variant save) keeps what is being typed.
  const editingModelRef = useRef(false);
  // Only the newest read may land: an older one, or one that a save already overtook, is dropped.
  const loadRequest = useRef(0);
  const load = useCallback(() => {
    const request = ++loadRequest.current;
    fetchAdminCatalogModel(modelId, companyId).then(
      (loaded) => {
        if (request !== loadRequest.current) return;
        setModel(loaded);
        setDraft((current) => (editingModelRef.current && current ? current : toDraft(loaded)));
        setError(null);
      },
      (err) => { if (request === loadRequest.current) setError(message(err, "No se pudo cargar el modelo.")); },
    );
  }, [modelId, companyId]);

  const loadPalette = useCallback(() => {
    const request = ++paletteRequest.current;
    fetchModelPalette(modelId, companyId).then(
      (loaded) => { if (request === paletteRequest.current) { setPalette(loaded); setPaletteError(null); } },
      (err) => { if (request === paletteRequest.current) setPaletteError(message(err, "No se pudo cargar la paleta del modelo.")); },
    );
  }, [modelId, companyId]);

  useEffect(() => { load(); loadPalette(); }, [load, loadPalette]);
  // The company's models, for moving a variant: only once someone edits one.
  useEffect(() => {
    if (editingId === null || models.length) return;
    fetchAdminCatalogModels(companyId).then(setModels, () => {});
  }, [editingId, models.length, companyId]);
  useEffect(() => {
    fetchAdminCategories(companyId).then(setCategories, () => {});
    fetchCatalogAttributes(companyId).then((rows) => { setAttributes(rows); setAttributesLoaded(true); }, () => {});
    fetchCatalogSettings(companyId).then(
      (s) => setLineLabels({
        new: s.new_line_label?.trim() || DEFAULT_LINE_LABELS.new,
        used: s.used_line_label?.trim() || DEFAULT_LINE_LABELS.used,
      }),
      () => {},
    );
  }, [companyId]);

  useEffect(() => { editingModelRef.current = editingModel; }, [editingModel]);
  const loaded = model !== null;
  // «#variantes» goes to the variants — once there is something to show.
  useEffect(() => {
    if (!loaded) return;
    const target = window.location.hash.slice(1);
    if (target) document.getElementById(target)?.scrollIntoView({ block: "start" });
  }, [loaded]);

  function closeModelForm() {
    setSlugConfirmed(false);
    setEditingModel(false);
    // Leaving the form leaves «?editar=modelo» too: a reload shows the model, not the form again.
    const url = new URL(window.location.href);
    if (url.searchParams.has("editar")) {
      url.searchParams.delete("editar");
      window.history.replaceState(window.history.state, "", url);
    }
  }

  function cancelModel() {
    if (model) setDraft(toDraft(model));
    closeModelForm();
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await updateAdminCatalogModel(modelId, {
        name: draft.name.trim(),
        slug: draft.slug.trim(),
        category_id: draft.category_id ? Number(draft.category_id) : null,
        description: draft.description,
        image_url: draft.image_url.trim(),
        is_published_online: draft.is_published_online,
        sort_order: Number(draft.sort_order) || 0,
      }, companyId);
      if (saved && typeof saved === "object" && "variants" in saved) {
        loadRequest.current += 1;
        setModel(saved);
        setDraft(toDraft(saved));
      }
      else load();
      closeModelForm();
      setNotice("Modelo guardado.");
    } catch (err) {
      setError(message(err, "No se pudo guardar el modelo."));
    } finally {
      setSaving(false);
    }
  }

  function openGallery(productId: number) {
    if (galleryLocked) return;
    if (galleryFor === productId) { closeGallery(); return; }
    const request = ++galleryRequest.current;
    galleryOpen.current = productId;
    setGalleryWork({ staged: 0, sending: false });
    setGalleryFor(productId);
    setGalleryImages(null);
    setGalleryError(null);
    fetchProductImages(productId, companyId).then(
      (images) => { if (request === galleryRequest.current) setGalleryImages(images); },
      (err) => { if (request === galleryRequest.current) setGalleryError(message(err, "No se pudo cargar la galería.")); },
    );
  }

  function closeGallery() {
    if (galleryLocked) return;
    const opener = galleryFor === null ? undefined : galleryButtons.current.get(galleryFor);
    galleryRequest.current += 1;
    galleryOpen.current = null;
    setGalleryFor(null);
    setGalleryImages(null);
    setGalleryError(null);
    opener?.focus();
  }

  // The panel opens below the table: its title takes the focus, so a keyboard or a reader lands on it.
  useEffect(() => {
    if (galleryFor !== null) galleryHeading.current?.focus();
  }, [galleryFor]);

  /** Only the gallery of the variant open NOW; the model is read again once a change settles (`onSettled`). */
  function galleryChanged(productId: number, images: AdminProductImage[]) {
    if (galleryOpen.current !== productId) return;
    setGalleryImages(images);
  }

  async function coverFrom(image: AdminProductImage) {
    setCoverBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await updateAdminCatalogModel(modelId, { image_url: image.url }, companyId);
      if (saved && typeof saved === "object" && "variants" in saved) {
        loadRequest.current += 1; // a read already on its way is older than this answer
        setModel(saved);
        setDraft((current) => (current ? { ...current, image_url: saved.image_url ?? "" } : current));
      } else load();
      setNotice("Portada del modelo actualizada.");
    } catch (err) {
      setError(message(err, "No se pudo cambiar la portada del modelo."));
    } finally {
      setCoverBusy(false);
    }
  }

  async function removeVariant(productId: number, line: CatalogLine, reason?: string) {
    setError(null);
    setNotice(null);
    try {
      await setProductVariant(productId, {
        model_id: null, condition_line: line, option_ids: [], ...(reason ? { reason } : {}),
      }, companyId);
      setNotice("El producto ya no es una variante de este modelo.");
      setRemovingId(null);
      setRemoveReason("");
      load();
      loadPalette();
    } catch (err) {
      setError(message(err, "No se pudo quitar la variante."));
    }
  }

  if (!model || !draft) {
    return error ? <ErrorBox message={error} /> : <Spinner label="Cargando modelo…" />;
  }
  // A published model's address is a page people may have; changing it breaks those links.
  const slugChanged = model.is_published_online && draft.slug.trim() !== "" && draft.slug.trim() !== model.slug;
  const noAttributes = attributesLoaded && attributes.length === 0;
  // A1: with a palette, a variant picks among the palette's attributes and options only. Editing one that holds an
  // option outside today's palette still lists that option, so the form shows what the variant is.
  const restricted = Boolean(palette?.has_palette);
  const formAttributes = (variant?: AdminModelVariant): AdminCatalogAttribute[] => {
    if (!palette || !restricted) return attributes;
    const own = new Map((variant?.options ?? []).map((o) => [o.attribute_id, o]));
    const rows = paletteAttributes(palette).map((attribute) => {
      const held = own.get(attribute.id);
      const options = held && !attribute.options.some((o) => o.id === held.option_id)
        ? [...attribute.options, { id: held.option_id, label: held.label, swatch_hex: held.swatch_hex, sort_order: Number.MAX_SAFE_INTEGER }]
        : attribute.options;
      return { id: attribute.id, name: attribute.name, kind: attribute.kind, role: attribute.role, sort_order: attribute.sort_order, options };
    });
    for (const link of own.values()) {
      if (rows.some((r) => r.id === link.attribute_id)) continue;
      const source = attributes.find((a) => a.id === link.attribute_id);
      rows.push({
        id: link.attribute_id, name: link.attribute_name, kind: source?.kind ?? "text", role: source?.role ?? "other",
        sort_order: source?.sort_order ?? 0,
        options: [{ id: link.option_id, label: link.label, swatch_hex: link.swatch_hex, sort_order: 0 }],
      });
    }
    return rows;
  };

  return (
    <div className="space-y-6">
      <p className="text-sm">
        <Link href="/admin/products/models" className="text-muted underline-offset-4 hover:text-foreground hover:underline">← Todos los modelos</Link>
      </p>
      {notice ? <p role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}
      {error ? <ErrorBox message={error} /> : null}

      <Panel
        id="modelo"
        title={model.name}
        description="Los datos que la tienda muestra en la página del modelo."
        action={canManage && !editingModel ? (
          <button type="button" className={internalPrimaryButtonClass} onClick={() => setEditingModel(true)}>Editar modelo</button>
        ) : null}
      >
        {editingModel && canManage ? (
          <form onSubmit={save} className="grid gap-3 md:grid-cols-2" aria-label="Editar modelo">
            <label className="block text-xs text-muted">
              Nombre
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <label className="block text-xs text-muted">
              Dirección
              <input value={draft.slug} onChange={(e) => { setDraft({ ...draft, slug: e.target.value }); setSlugConfirmed(false); }} className={`mt-1.5 font-mono ${internalInputClass}`} />
            </label>
            {slugChanged ? (
              <div className="space-y-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-warning md:col-span-2">
                <p role="note">
                  El modelo está publicado: su página actual, /modelo/{model.slug}, dejará de funcionar y los enlaces
                  que ya compartiste llevarán a una página no encontrada. La nueva será /modelo/{draft.slug.trim() || "…"}.
                </p>
                <label className="flex items-center gap-2 text-foreground">
                  <input type="checkbox" checked={slugConfirmed} onChange={(e) => setSlugConfirmed(e.target.checked)} />
                  Entiendo que la dirección anterior dejará de funcionar
                </label>
              </div>
            ) : null}
            <label className="block text-xs text-muted">
              Categoría
              <select value={draft.category_id} onChange={(e) => setDraft({ ...draft, category_id: e.target.value })} className={`mt-1.5 ${internalInputClass}`}>
                <option value="">Sin categoría</option>
                {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label className="block text-xs text-muted">
              Orden
              <input type="number" value={draft.sort_order} onChange={(e) => setDraft({ ...draft, sort_order: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <label className="block text-xs text-muted md:col-span-2">
              Imagen (dirección)
              <input value={draft.image_url} onChange={(e) => setDraft({ ...draft, image_url: e.target.value })} className={`mt-1.5 ${internalInputClass}`} />
            </label>
            <div className="md:col-span-2">
              <DescriptionEditor
                id="model-description"
                label="Descripción"
                value={draft.description}
                onChange={(description) => setDraft({ ...draft, description })}
                disabled={saving}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-foreground">
              <input type="checkbox" checked={draft.is_published_online} onChange={(e) => setDraft({ ...draft, is_published_online: e.target.checked })} />
              Publicado en la tienda en línea
            </label>
            <div className="flex justify-end gap-2 md:col-span-2">
              <button type="button" className={internalButtonClass} onClick={cancelModel} disabled={saving}>Cancelar</button>
              <button type="submit" className={internalPrimaryButtonClass} disabled={saving || (slugChanged && !slugConfirmed) || draft.description.length > DESCRIPTION_MAX_LENGTH}>
                {saving ? "Guardando…" : "Guardar cambios"}
              </button>
            </div>
          </form>
        ) : (
          <dl className="grid gap-x-6 gap-y-3 text-sm md:grid-cols-2">
            <div><dt className="text-xs text-muted">Nombre</dt><dd className="text-foreground">{model.name}</dd></div>
            <div><dt className="text-xs text-muted">Dirección</dt><dd className="font-mono text-foreground">/modelo/{model.slug}</dd></div>
            <div><dt className="text-xs text-muted">Categoría</dt><dd className="text-foreground">{categories.find((c) => c.id === model.category_id)?.name ?? (model.category_id ? "—" : "Sin categoría")}</dd></div>
            <div><dt className="text-xs text-muted">Tienda</dt><dd className="text-foreground">{model.is_published_online ? "Publicado" : "Oculto"} · orden {model.sort_order ?? 0}</dd></div>
            <div className="md:col-span-2"><dt className="text-xs text-muted">Imagen</dt><dd className="text-foreground"><ModelImage url={model.image_url} name={model.name} /></dd></div>
            <div className="md:col-span-2">
              <dt className="text-xs text-muted">Descripción</dt>
              <dd className="mt-1">
                {model.description?.trim() ? (
                  <RichText
                    source={model.description}
                    testId="model-description-preview"
                    className="max-h-[28rem] overflow-y-auto rounded-lg border border-bd-border bg-background px-4 py-3 text-sm leading-7 text-muted"
                  />
                ) : <span className="text-foreground">Sin descripción</span>}
                {canManage ? (
                  <button type="button" className={`mt-2 ${internalButtonClass}`} onClick={() => setEditingModel(true)}>Editar descripción</button>
                ) : null}
              </dd>
            </div>
          </dl>
        )}
      </Panel>

      <ModelPalettePanel
        modelId={modelId}
        companyId={companyId}
        canManage={canManage}
        palette={palette}
        error={paletteError}
        onSaved={(answer) => { paletteRequest.current += 1; setPalette(answer); setPaletteError(null); load(); }}
      />

      <Panel
        id="variantes"
        title="Variantes"
        description="Los productos que son opciones de este modelo, con su línea y sus opciones."
        action={canManage && !adding ? (
          <button type="button" className={internalPrimaryButtonClass} onClick={() => setAdding(true)}>+ Agregar variante</button>
        ) : null}
      >
        {canManage && adding ? (
          <AddVariantForm
            modelId={modelId}
            companyId={companyId}
            attributes={formAttributes()}
            restricted={restricted}
            lineLabels={lineLabels}
            onCancel={() => setAdding(false)}
            onSaved={() => { setAdding(false); setNotice("Variante guardada."); load(); loadPalette(); }}
          />
        ) : null}
        {noAttributes ? (
          <p className="mb-4 rounded-lg border border-bd-border px-3 py-2 text-xs text-muted">
            Para que cada variante diga su capacidad o su color, crea primero los atributos y sus opciones en{" "}
            <Link href="/admin/products/models#atributos" className="text-foreground underline underline-offset-4">Atributos y opciones</Link>.
          </p>
        ) : null}
        {model.variants.length === 0 ? (
          <EmptyBox message="Este modelo todavía no tiene variantes." />
        ) : (
          <TableWrap>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Producto</Th><Th>Línea</Th><Th>Opciones</Th><Th right>Precio</Th><Th right>Stock</Th><Th>Tienda</Th>
                {canManage ? <Th right>Acciones</Th> : null}
              </tr>
            </thead>
            <tbody>
              {model.variants.map((variant) => (
                <Fragment key={variant.product_id}>
                  <tr className="border-b border-bd-border/60">
                    <Td>
                      <Link href={`/admin/products/${variant.product_id}`} className="underline-offset-4 hover:underline">{variant.name}</Link>
                      {variant.sku || variant.code ? <span className="block font-mono text-xs text-muted">{variant.sku || variant.code}</span> : null}
                      <span className="mt-1.5 block"><VariantThumb variant={variant} /></span>
                    </Td>
                    <Td muted>{lineLabels[variant.condition_line] ?? variant.condition_line}</Td>
                    <Td>
                      <span className="flex flex-wrap gap-1.5">
                        {variant.options.map((option) => (
                          <span key={option.option_id} className="inline-flex items-center gap-1 rounded-full border border-bd-border px-2 py-0.5 text-xs">
                            {option.swatch_hex ? (
                              <span aria-hidden="true" className="h-3 w-3 rounded-full border border-foreground/25" style={{ backgroundColor: option.swatch_hex }} />
                            ) : null}
                            <span className="text-muted">{option.attribute_name}:</span> {option.label}
                          </span>
                        ))}
                        {variant.options.length === 0 ? <span className="text-muted">—</span> : null}
                        {variant.palette_status && variant.palette_status !== "variante_existente" ? (
                          <PaletteStatusBadge status={variant.palette_status} />
                        ) : null}
                      </span>
                    </Td>
                    <Td right muted>{formatSoles(variant.price)}</Td>
                    <Td right>{variant.stock}</Td>
                    <Td muted>{variant.is_published_online ? "Publicada" : "Oculta"}</Td>
                    {canManage ? (
                      <Td right>
                        <span className="inline-flex flex-wrap justify-end gap-1.5">
                          <button
                            type="button"
                            ref={(node) => {
                              if (node) galleryButtons.current.set(variant.product_id, node);
                              else galleryButtons.current.delete(variant.product_id);
                            }}
                            className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 disabled:opacity-50"
                            onClick={() => openGallery(variant.product_id)}
                            disabled={galleryLocked}
                            aria-expanded={galleryFor === variant.product_id}
                            aria-controls="imagenes-variante"
                            aria-label={`Imágenes de ${variant.name}`}
                          >
                            Imágenes
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25"
                            onClick={() => setEditingId(editingId === variant.product_id ? null : variant.product_id)}
                            aria-expanded={editingId === variant.product_id}
                            aria-label={`Editar variante ${variant.name}`}
                          >
                            Editar variante
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-bd-border px-2.5 py-1.5 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 disabled:opacity-50"
                            disabled={variant.removal === "blocked"}
                            title={variant.removal === "blocked"
                              ? "Tiene equipos, stock o pedidos sin entregar: sacarla cambiaría la línea de esos equipos. Edítala en su lugar."
                              : undefined}
                            onClick={() => (variant.removal === "superior"
                              ? setRemovingId(removingId === variant.product_id ? null : variant.product_id)
                              : void removeVariant(variant.product_id, variant.condition_line))}
                            aria-label={`Quitar ${variant.name} del modelo`}
                          >
                            Quitar del modelo
                          </button>
                        </span>
                      </Td>
                    ) : null}
                  </tr>
                  {canManage && removingId === variant.product_id ? (
                    <tr className="border-b border-bd-border/60">
                      <td colSpan={8} className="px-3 py-3">
                        <div data-testid={`remove-variant-${variant.product_id}`} className="space-y-2 text-sm">
                          <p className="text-foreground">
                            Esta variante ya tiene ventas: sacarla del modelo lo hace un administrador de la empresa, con
                            un motivo que queda en la auditoría.
                          </p>
                          <label className="block text-xs text-muted">
                            Motivo
                            <textarea
                              value={removeReason}
                              onChange={(e) => setRemoveReason(e.target.value)}
                              rows={2}
                              className={`mt-1.5 ${internalInputClass}`}
                            />
                          </label>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              className={internalPrimaryButtonClass}
                              disabled={removeReason.trim().length < 5}
                              onClick={() => void removeVariant(variant.product_id, variant.condition_line, removeReason.trim())}
                            >
                              Quitar del modelo
                            </button>
                            <button type="button" className={internalButtonClass} onClick={() => setRemovingId(null)}>Cancelar</button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                  {canManage && editingId === variant.product_id ? (
                    <tr className="border-b border-bd-border/60">
                      <td colSpan={8} className="px-3 py-3">
                        <EditVariantForm
                          variant={variant}
                          modelId={modelId}
                          models={models}
                          attributes={formAttributes(variant)}
                          restricted={restricted}
                          noAttributes={noAttributes}
                          lineLabels={lineLabels}
                          companyId={companyId}
                          onCancel={() => setEditingId(null)}
                          onSaved={() => { setEditingId(null); setNotice("Variante actualizada."); load(); loadPalette(); }}
                        />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </TableWrap>
        )}
        {canManage && galleryFor !== null ? (() => {
          const variant = model.variants.find((row) => row.product_id === galleryFor);
          if (!variant) return null;
          const title = `Imágenes de ${variant.name}`;
          return (
            <section
              id="imagenes-variante"
              aria-labelledby="imagenes-variante-titulo"
              className="mt-6 min-w-0 rounded-xl border border-bd-border bg-background p-4 sm:p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3
                    id="imagenes-variante-titulo"
                    ref={galleryHeading}
                    tabIndex={-1}
                    className="break-words text-sm font-semibold text-foreground focus:outline-none"
                  >
                    {title}
                  </h3>
                  <p className="mt-1 text-xs text-muted">
                    Sólo de esta variante: las demás conservan las suyas. La principal es la que muestra la tienda al
                    elegirla.
                  </p>
                </div>
                <button
                  type="button"
                  className={internalButtonClass}
                  onClick={closeGallery}
                  disabled={galleryLocked}
                  aria-label={`Cerrar imágenes de ${variant.name}`}
                >
                  Cerrar
                </button>
              </div>
              {galleryLocked ? (
                <p role="status" className="mt-3 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">
                  {galleryWork.sending
                    ? "Subiendo imágenes… Espera a que terminen para cerrar o cambiar de variante."
                    : `${galleryWork.staged === 1 ? "1 imagen sin subir" : `${galleryWork.staged} imágenes sin subir`}: `
                      + "súbelas o quítalas de la lista para cerrar o cambiar de variante."}
                </p>
              ) : null}
              <div className="mt-4">
                {galleryError ? <ErrorBox message={galleryError} /> : galleryImages === null ? (
                  <Spinner label="Cargando imágenes…" />
                ) : (
                  <ProductGallery
                    key={variant.product_id}
                    productId={variant.product_id}
                    productName={variant.name}
                    images={galleryImages}
                    onChange={(images) => galleryChanged(variant.product_id, images)}
                    onWorkChange={onGalleryWork}
                    onSettled={load}
                    companyId={companyId}
                    stageBeforeUpload
                    renderImageActions={(image, position) => (
                      (model.image_url || "") === image.url ? (
                        <span className="min-h-9 content-center rounded-full border border-bd-border px-2.5 text-[11px] font-semibold text-foreground">
                          Portada del modelo
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={coverBusy}
                          onClick={() => void coverFrom(image)}
                          aria-label={`Usar como portada del modelo: imagen ${position}`}
                          className={`${internalButtonClass} min-h-9 px-3 py-1.5 text-xs`}
                        >
                          Usar como portada del modelo
                        </button>
                      )
                    )}
                  />
                )}
              </div>
            </section>
          );
        })() : null}
      </Panel>
    </div>
  );
}
