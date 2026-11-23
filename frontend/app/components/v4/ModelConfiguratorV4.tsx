"use client";

/**
 * P2 · F1 — the model page in the V4 design (preview at `/v4/modelo/<slug>`).
 *
 * THE SAME BEHAVIOUR AS V3. Reading the model, choosing a line and a
 * combination, the gallery and the cart writes are the hooks in
 * `lib/model-configurator.ts`; this file is only another way to show them:
 * a large gallery on the left, the name and the option chips on the right,
 * and a summary that stays in view with the choice, its price, its
 * availability and the button.
 *
 * WHAT THE PAGE ALWAYS SAYS: the model, the chosen option (or device), its
 * condition (the line's name, as the company calls it), the price, the
 * availability and the photos. A used device adds its grade and its battery
 * reading, each only when the shop published one.
 *
 * WHAT IT ONLY SAYS WHEN THE SHOP SAID IT: the warranty. With no warranty text
 * and no link configured, the page does not mention a warranty at all.
 *
 * THE PICKERS (P2 · F3): in the order of the attributes' roles — capacity,
 * colour, SIM, region, then any other — and SIM and region only when the
 * model's variants differ by them. A server that sends no roles keeps the
 * company's `sort_order`.
 *
 * ACCESSORIES (P2 · F3): «Compatible con {modelo}» below, only from
 * `model.accessories` (`AccessoriesV4`); with none, nothing.
 *
 * WHERE EACH PHOTO COMES FROM: a picture the shop did not upload says
 * «Imagen referencial»; a used device's own photos say «Fotos de este equipo»
 * (`photoLabel`).
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ImagePending } from "../ImagePending";
import { ProductImage } from "../ProductImage";
import { CrossfadeStage } from "./CrossfadeStage";
import { OnOrderOfferPrompt, OverStockOnOrderOption } from "../OnOrderOffer";
import { RichText } from "../RichText";
import { useStorefront } from "../StorefrontProvider";
import { UsedUnitCardV4 } from "./UsedUnitCardV4";
import { SupportStripV4 } from "./SupportStripV4";
import { AccessoriesV4 } from "./AccessoriesV4";
import { formatMoney } from "../../lib/format";
import {
  SOLD_OUT_LABEL,
  availabilityLabel,
  chipPicture,
  choiceOutcome,
  hasPicker,
  imeiHintText,
  optionsText,
  type CatalogModel,
  type ConditionLine,
  type ModelAttribute,
  type Selection,
  type Variant,
  type VariantImage,
} from "../../lib/catalog-models";
import {
  isModelSoldOut,
  photoLabel,
  useCatalogModel,
  useLineChoice,
  useNewLineConfigurator,
  useUsedLineConfigurator,
  useVariantInUrl,
  VARIANT_PARAM,
  type ConfiguratorStatus,
} from "../../lib/model-configurator";

// A used device the API lists is one the shop can sell now: said with the same words as a new one in stock.
const USED_UNIT_AVAILABILITY = availabilityLabel({ available: true, availability: "own_stock" });

// ---------------------------------------------------------------------------
// Chips: a radio group with one tab stop and arrow keys
// ---------------------------------------------------------------------------

type Chip = {
  key: string;
  label: string;
  /** «agotado» / «no disponible» go into the accessible name too. */
  state?: "available" | "sold-out" | "unavailable";
  /**
   * A colour: its swatch as the server resolves it for this model (FASE 1.2 · Y: the model's own, else the company's;
   * '' = none configured: no dot) and its finish picture (drawn first).
   */
  swatch?: string;
  picture?: string | null;
};

/** G (owner's visual order 4): finish picture → swatch (model's, else company's) → nothing; the name is always beside it. */
function ChipMark({ chip }: { chip: Chip }) {
  if (chip.picture) {
    return (
      <span aria-hidden="true" data-testid="v4-chip-picture" className="v4-swatch relative overflow-hidden">
        <ProductImage src={chip.picture} alt="" sizes="24px" className="scale-[2.2] object-cover" />
      </span>
    );
  }
  if (chip.swatch) return <span aria-hidden="true" className="v4-swatch" style={{ backgroundColor: chip.swatch }} />;
  return null;
}

const NOT_AVAILABLE = "no disponible";
const SOLD_OUT = SOLD_OUT_LABEL.toLowerCase();

function ChipGroup({
  label, detail, chips, chosen, onChoose,
}: {
  label: string;
  /** Written next to the label (the chosen colour's name), so a colour is never a colour-only cue. */
  detail?: string;
  chips: Chip[];
  chosen: string | null;
  onChoose: (key: string) => void;
}) {
  const labelId = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectable = chips.map((chip) => chip.state !== "unavailable");
  const chosenAt = chips.findIndex((chip) => chip.key === chosen);
  const tabStop = Math.max(0, chosenAt >= 0 ? chosenAt : selectable.indexOf(true));

  function move(from: number, step: 1 | -1 | "first" | "last") {
    const enabled = selectable.map((ok, i) => (ok ? i : -1)).filter((i) => i >= 0);
    if (enabled.length === 0) return;
    let target: number;
    if (step === "first") target = enabled[0];
    else if (step === "last") target = enabled[enabled.length - 1];
    else {
      const order = step === 1 ? enabled : [...enabled].reverse();
      target = order.find((i) => (step === 1 ? i > from : i < from)) ?? order[0];
    }
    onChoose(chips[target].key);
    refs.current[target]?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, at: number) {
    const keys: Record<string, 1 | -1 | "first" | "last"> = {
      ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1, Home: "first", End: "last",
    };
    const step = keys[event.key];
    if (step === undefined) return;
    event.preventDefault();
    move(at, step);
  }

  return (
    <div>
      <p id={labelId} className="v4-section-title">
        {label}
        {detail ? <>{" "}<span className="v4-small font-normal">{detail}</span></> : null}
      </p>
      <div role="radiogroup" aria-labelledby={labelId} className="mt-3 flex flex-wrap gap-2.5">
        {chips.map((chip, at) => {
          const state = chip.state ?? "available";
          const name = state === "unavailable" ? `${chip.label}, ${NOT_AVAILABLE}`
            : state === "sold-out" ? `${chip.label}, ${SOLD_OUT}` : undefined;
          return (
            <button
              key={chip.key}
              ref={(el) => { refs.current[at] = el; }}
              type="button"
              role="radio"
              aria-checked={chip.key === chosen}
              aria-disabled={state === "unavailable" ? true : undefined}
              aria-label={name}
              data-state={state}
              tabIndex={at === tabStop ? 0 : -1}
              onKeyDown={(event) => onKeyDown(event, at)}
              onClick={() => { if (state !== "unavailable") onChoose(chip.key); }}
              className="v4-chip"
            >
              <ChipMark chip={chip} />
              {chip.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function AttributeChips({
  attribute, index, attributes, variants, selection, preferred, onSelect, finishImages,
}: {
  /** The model's finish pictures by colour: a colour chip shows its own (never a demo). */
  finishImages?: Record<string, VariantImage[]>;
  attribute: ModelAttribute;
  index: number;
  attributes: ModelAttribute[];
  variants: Variant[];
  selection: Selection;
  /** The buyer's remembered choices: the same ones the press uses, so the chip predicts exactly where it lands. */
  preferred: Selection;
  onSelect: (index: number, optionId: number) => void;
}) {
  const chosenId = selection[String(attribute.id)];
  const isColor = attribute.kind === "color";
  // PR D: what pressing it leads to (`chooseOption`): a buyable variant, a sold-out one, or none at all.
  const chips: Chip[] = attribute.options.map((option) => {
    return {
      key: String(option.id),
      label: option.label,
      state: choiceOutcome(variants, attributes, selection, index, option.id, { preferred }),
      ...(isColor ? { swatch: option.swatch_hex, picture: chipPicture(finishImages, option.id) } : {}),
    };
  });
  const chosen = attribute.options.find((option) => option.id === chosenId) ?? null;
  return (
    <ChipGroup
      label={attribute.name}
      detail={isColor && chosen ? chosen.label : undefined}
      chips={chips}
      chosen={chosenId === undefined ? null : String(chosenId)}
      onChoose={(key) => onSelect(index, Number(key))}
    />
  );
}

// ---------------------------------------------------------------------------
// Gallery
// ---------------------------------------------------------------------------

type Picture = { url: string; alt_text: string; is_demo?: boolean };

/**
 * P2 · F2 — how wide the stage really is (the LCP image): the whole screen
 * below `lg`; from `lg`, the first column of `GRID` (1.15 of 2.15 parts of the
 * row, after the page padding and the 4 rem gap), capped by `max-w-7xl`.
 */
const GALLERY_SIZES = "(min-width: 1280px) 610px, (min-width: 1024px) 47vw, 100vw";

function Gallery({
  pictures, index, onIndex, source, alt, emptyText, initial, pending = false, finishKey = null,
}: {
  pictures: Picture[];
  index: number;
  onIndex: (index: number) => void;
  source: "catalog" | "device";
  alt: string;
  /** Said when there is no picture; without it, the model's initial is drawn. */
  emptyText?: string;
  initial: string;
  /** PR C: the chosen variant and its finish have no picture: the neutral marker, in the photo's own box. */
  pending?: boolean;
  /** PR D: the finish the pictures belong to; another one never cross-fades from the previous colour. */
  finishKey?: string | null;
}) {
  const shown = pictures[index] ?? pictures[0] ?? null;
  const caption = shown ? photoLabel(shown.url, source, shown.is_demo) : null;
  return (
    <section data-testid="v4-gallery" aria-label="Fotos">
      <div className="v4-stage">
        {/* PR D: ONE box with a fixed aspect ratio whatever it holds (photo, «Imagen pendiente» or the initial), so
            a choice never moves the page; the photo cross-fades inside it (`CrossfadeStage`). */}
        <div data-testid="v4-stage" className="relative aspect-square sm:aspect-[5/4]">
          {/* Mounted while there is a photo or not: a pending finish empties it at once, never keeping a colour. */}
          <CrossfadeStage
            picture={pending ? null : shown}
            alt={alt}
            sizes={GALLERY_SIZES}
            imageClassName="object-contain p-8 sm:p-14"
            finishKey={finishKey}
          />
          {shown ? (
            caption ? <span className="v4-image-label">{caption}</span> : null
          ) : pending ? (
            <ImagePending className="text-[color:var(--v4-muted)]" />
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 px-6 text-center">
              <span aria-hidden="true" className="text-6xl font-bold text-[color:var(--v4-line-strong)]">{initial}</span>
              {emptyText ? <p className="v4-small">{emptyText}</p> : null}
            </div>
          )}
        </div>
      </div>
      {pictures.length > 1 ? (
        // V4-QA: the padding keeps the focus ring (2 px + 3 px offset) inside the scroller, which would clip it; the
        // negative margin and the shorter top margin leave the strip where it was.
        <div role="group" aria-label="Elegir foto" className="-mx-1.5 mt-2.5 flex gap-2.5 overflow-x-auto p-1.5">
          {pictures.map((picture, at) => (
            <button
              key={picture.url}
              type="button"
              aria-label={`Ver imagen ${at + 1} de ${pictures.length}`}
              aria-pressed={at === index}
              onClick={() => onIndex(at)}
              className="v4-thumb"
            >
              <ProductImage src={picture.url} alt="" sizes="72px" className="object-contain p-1.5" />
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Summary pieces
// ---------------------------------------------------------------------------

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="v4-small">{term}</dt>
      <dd className="v4-data mt-0.5 break-words">{children}</dd>
    </div>
  );
}

function StatusLine({ status, about }: { status: ConfiguratorStatus; about?: string }) {
  if (!status) return null;
  return (
    <div
      role={status.kind === "error" ? "alert" : "status"}
      className={`mt-4 rounded-2xl border p-3.5 text-sm ${
        status.kind === "success" ? "border-bd-border bg-surface text-foreground" : "border-danger-border bg-danger-surface text-danger"
      }`}
    >
      {about ? <span className="font-bold">{about}: </span> : null}
      {status.message}
      {status.kind === "success" ? (
        <>
          {" "}
          <Link href="/cart" className="v4-link">Ver carrito</Link>
        </>
      ) : null}
    </div>
  );
}

/**
 * V4-QA — what a screen reader hears when the choice changes: the option, its price and its availability, in ONE
 * polite, atomic region. The visible price is not a live region of its own: alone it would be read without saying
 * what it is the price of, and next to this one it would be read twice. Not announced on arrival (live regions only
 * speak about changes).
 */
function ChoiceAnnouncement({ text }: { text: string }) {
  return (
    <p data-testid="v4-choice-announcement" className="sr-only" aria-live="polite" aria-atomic="true">{text}</p>
  );
}

/** Only what the shop configured; with nothing configured, nothing at all. */
function WarrantyV4() {
  const { policies } = useStorefront();
  const text = policies.warranty_text?.trim() ?? "";
  const url = policies.warranty_url?.trim() ?? "";
  if (!text && !url) return null;
  return (
    <section data-testid="v4-warranty" className="mt-8 border-t border-bd-border pt-6">
      <h2 className="v4-section-title">Garantía</h2>
      {text ? <p className="v4-small mt-2 whitespace-pre-line leading-6">{text}</p> : null}
      {url ? (
        <a href={url} className="v4-link mt-3 inline-block text-sm">Ver la política de garantía</a>
      ) : null}
    </section>
  );
}

/**
 * The page is ONE grid: the heading (with the line picker) in the right
 * column's first row, the gallery on the left across both rows, and the
 * line's options and summary below the heading.
 *
 * The heading is rendered by the page, NOT by the line: switching between
 * new and used mounts another line component, and a picker inside it would
 * be destroyed together with the keyboard focus it had (review of #308).
 * A line only fills these two slots.
 */
const GRID = "grid gap-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:grid-rows-[auto_1fr] lg:gap-x-16 lg:gap-y-0";

function Slots({ gallery, children }: { gallery: ReactNode; children: ReactNode }) {
  return (
    <>
      <div className="min-w-0 lg:sticky lg:top-[calc(var(--v4-sticky-offset,0px)+2rem)] lg:col-start-1 lg:row-span-2 lg:row-start-1 lg:self-start">{gallery}</div>
      <div className="min-w-0 lg:col-start-2 lg:row-start-2">{children}</div>
    </>
  );
}

function initialOf(model: CatalogModel): string {
  return model.name.trim().charAt(0).toUpperCase() || "·";
}

// ---------------------------------------------------------------------------
// The two lines
// ---------------------------------------------------------------------------

function NewLineV4({
  model, onReload, initialVariant, onVariant,
}: {
  model: CatalogModel;
  onReload: () => void;
  /** V4-VARIANT-URL: the variant to start from (the page's `?variante=`, or the last one chosen on this page). */
  initialVariant?: string;
  /** Told every chosen variant, so coming back from the used line starts from it again. */
  onVariant: (slug: string) => void;
}) {
  // P2 · V4: capacity, colour, SIM, region, then the rest (`ATTRIBUTE_ROLE_ORDER`), whatever their `sort_order`.
  const c = useNewLineConfigurator(model, onReload, { order: "role", initialVariant });
  const condition = model.lines?.new?.label ?? "";
  // V4-VARIANT-URL: the address always names the chosen variant (`?variante=`), so it can be shared.
  const chosenSlug = c.variant?.slug ?? null;
  useVariantInUrl(chosenSlug);
  useEffect(() => { if (chosenSlug) onVariant(chosenSlug); }, [chosenSlug, onVariant]);

  if (c.variants.length === 0) {
    return (
      <Slots gallery={<Gallery pictures={[]} index={0} onIndex={() => {}} source="catalog" alt={model.name} initial={initialOf(model)} />}>
        <p className="v4-small mt-10">No hay opciones disponibles en esta línea por ahora.</p>
      </Slots>
    );
  }

  const variant = c.variant;
  return (
    <Slots
      gallery={
        <Gallery
          pictures={c.gallery}
          index={c.imageIndex}
          onIndex={c.showImage}
          source="catalog"
          alt={variant?.name || model.name}
          initial={initialOf(model)}
          pending={c.imagePending}
          finishKey={c.finishKey}
        />
      }
    >
      <div className="mt-10 space-y-8">
        {/* SIM and region only when the variants differ by them (`hasPicker`); the index stays the hook's. */}
        {c.attributes.map((attribute, index) => (hasPicker(attribute, c.variants) ? (
          <AttributeChips
            key={attribute.id}
            attribute={attribute}
            index={index}
            attributes={c.attributes}
            variants={c.variants}
            selection={c.selection}
            preferred={c.preferred}
            onSelect={c.choose}
            finishImages={model.finish_images}
          />
        ) : null))}
      </div>
      {/* PR D: what the configurator moved by itself to keep the choice real, said where the eye is. Not a live region:
          the announcement below says it once, with the new price. */}
      {c.adjusted ? (
        <p data-testid="v4-choice-adjusted" className="v4-small v4-adjusted mt-6">{c.adjusted}</p>
      ) : null}

      <section data-testid="v4-summary" aria-label="Tu elección" className="v4-summary mt-10">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
          <Fact term="Modelo">{model.name}</Fact>
          <Fact term="Opción">{variant?.name ?? "—"}</Fact>
          <Fact term="Condición">{condition}</Fact>
          <Fact term="Disponibilidad">{variant ? availabilityLabel(variant) : SOLD_OUT_LABEL}</Fact>
        </dl>
        <div className="mt-6 flex items-baseline justify-between gap-4 border-t border-bd-border pt-5">
          <span className="v4-small">{c.onOrder ? "Precio bajo pedido" : "Precio"}</span>
          <p data-testid="v4-price" className="v4-price">
            {variant ? `S/ ${formatMoney(c.onOrder ? c.onOrder.price : variant.price)}` : "—"}
          </p>
        </div>
        <ChoiceAnnouncement
          text={variant
            ? `${c.adjusted ? `${c.adjusted} ` : ""}${variant.name}: S/ ${formatMoney(c.onOrder ? c.onOrder.price : variant.price)}, ${availabilityLabel(variant)}`
            : ""}
        />
        {c.onOrder ? (
          <p className="v4-small mt-2 leading-6">No lo tenemos en tienda ahora: lo pedimos para ti al confirmar tu compra.</p>
        ) : null}
        <button
          type="button"
          onClick={() => void c.add(c.onOrder !== null)}
          disabled={c.busy || !variant || !c.available}
          className="v4-cta mt-5"
        >
          {c.busy ? "Agregando…" : c.onOrder ? "Pedir bajo pedido" : !variant || !c.available ? SOLD_OUT_LABEL : "Agregar al carrito"}
        </button>
        {c.offer ? (
          <OnOrderOfferPrompt offer={c.offer} busy={c.busy} onAccept={() => void c.add(true)} />
        ) : c.overStock ? (
          <OverStockOnOrderOption offer={c.overStock} busy={c.busy} onChoose={() => void c.add(true)} />
        ) : null}
        <StatusLine status={c.status} />
      </section>
      <WarrantyV4 />
    </Slots>
  );
}

function UsedLineV4({ model, onReload }: { model: CatalogModel; onReload: () => void }) {
  const c = useUsedLineConfigurator(model, onReload, { order: "role" });
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [photo, setPhoto] = useState<{ unit: string | null; index: number }>({ unit: null, index: 0 });
  const condition = model.lines?.used?.label ?? "";
  // Until the buyer picks one, the first device is on show. Once they picked one, ONLY that one: if it left
  // (sold meanwhile, or filtered out) nothing takes its place — another device under «Comprar este equipo»
  // is a device they never chose (review of #308).
  const chosen = chosenId === null
    ? c.visible[0] ?? null
    : c.visible.find((unit) => unit.id === chosenId) ?? null;
  // What the last purchase said is shown next to THAT device, and only there.
  const saidHere = Boolean(chosen && c.statusUnit?.id === chosen.id);
  const saidAbout = c.statusUnit ? optionsText(c.statusUnit.options ?? {}, c.order) || model.name : undefined;
  const photoIndex = chosen && photo.unit === chosen.id ? photo.index : 0;
  const options = chosen ? optionsText(chosen.options ?? {}, c.order) : "";
  const imei = chosen ? imeiHintText(chosen.imei_hint) : null;

  if (c.units.length === 0) {
    return (
      <Slots gallery={<Gallery pictures={[]} index={0} onIndex={() => {}} source="device" alt={model.name} initial={initialOf(model)} />}>
        <p className="v4-small mt-10">No hay equipos disponibles en esta línea por ahora. Vuelve pronto o consulta con la tienda.</p>
      </Slots>
    );
  }

  return (
    <Slots
      gallery={
        <Gallery
          key={chosen?.id ?? "none"}
          pictures={chosen?.photos ?? []}
          index={photoIndex}
          onIndex={(index) => setPhoto({ unit: chosen?.id ?? null, index })}
          source="device"
          alt={`${model.name}${options ? `, ${options}` : ""}`}
          emptyText={chosen ? "Este equipo no tiene fotos publicadas." : undefined}
          initial={initialOf(model)}
        />
      }
    >
      {c.filterable.length > 0 ? (
        <div className="mt-10 space-y-6">
          {/* V4-QA: one value per filter, so a radio group like every other picker (one tab stop, arrow keys). */}
          {c.filterable.map((filter) => (
            <ChipGroup
              key={filter.name}
              label={filter.name}
              chips={["", ...filter.values].map((value) => ({ key: value, label: value || "Todas" }))}
              chosen={c.filters[filter.name] ?? ""}
              onChoose={(value) => c.setFilter(filter.name, value)}
            />
          ))}
        </div>
      ) : null}

      <p className="v4-small mt-10" aria-live="polite">
        {c.visible.length === 1 ? "1 equipo" : `${c.visible.length} equipos`}
      </p>
      {/* Always on the page: a region created with its text already in it is not read by NVDA/JAWS, so a summary that
          disappears (its device filtered out) and comes back must not take the announcement with it. */}
      <ChoiceAnnouncement
        text={chosen ? `${options || model.name}: S/ ${formatMoney(chosen.price)}, ${USED_UNIT_AVAILABILITY}` : ""}
      />
      {c.visible.length === 0 ? (
        <p className="v4-small mt-4">Ningún equipo coincide con estos filtros.</p>
      ) : (
        <ul className="mt-4 space-y-3" aria-label="Equipos">
          {c.visible.map((unit) => (
            <li key={unit.id}>
              <UsedUnitCardV4
                unit={unit}
                modelName={model.name}
                attributeOrder={c.order}
                selected={chosen?.id === unit.id}
                onSelect={() => setChosenId(unit.id)}
              />
            </li>
          ))}
        </ul>
      )}

      {!saidHere ? <StatusLine status={c.status} about={saidAbout} /> : null}

      {chosen ? (
        <section data-testid="v4-summary" aria-label="Tu elección" className="v4-summary mt-10">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            <Fact term="Modelo">{model.name}</Fact>
            <Fact term="Equipo">{options || model.name}</Fact>
            <Fact term="Condición">{condition}</Fact>
            {chosen.cosmetic_grade ? <Fact term="Estado">{chosen.cosmetic_grade.label}</Fact> : null}
            {chosen.battery_health !== null && chosen.battery_health !== undefined ? (
              <Fact term="Batería">{`${chosen.battery_health}%`}</Fact>
            ) : null}
            <Fact term="Disponibilidad">{USED_UNIT_AVAILABILITY}</Fact>
          </dl>
          {chosen.cosmetic_grade?.description ? (
            <p className="v4-small mt-4 leading-6">{chosen.cosmetic_grade.description}</p>
          ) : null}
          {chosen.notes ? <p className="v4-small mt-2 leading-6">{chosen.notes}</p> : null}
          {imei ? <p className="v4-small mt-2">{imei}</p> : null}
          <div className="mt-6 flex items-baseline justify-between gap-4 border-t border-bd-border pt-5">
            <span className="v4-small">Precio</span>
            <p data-testid="v4-price" className="v4-price">S/ {formatMoney(chosen.price)}</p>
          </div>
          <button
            type="button"
            onClick={() => { setChosenId(chosen.id); void c.buy(chosen); }}
            disabled={c.busyUnit === chosen.id}
            className="v4-cta mt-5"
          >
            {c.busyUnit === chosen.id ? "Agregando…" : "Comprar este equipo"}
          </button>
          {saidHere ? <StatusLine status={c.status} /> : null}
        </section>
      ) : c.visible.length > 0 ? (
        <p className="v4-small mt-10">Elige un equipo para ver su resumen.</p>
      ) : null}
      <WarrantyV4 />
    </Slots>
  );
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export function ModelConfiguratorV4({
  slug, initialLine, initialModel, initialVariant, catalogHref = "/v4",
}: {
  slug: string;
  /**
   * CATALOG-REFERENCE-01 · PR D — where «Catálogo» leads: the V4 home in the preview (the default), the shop's
   * catalogue (`/product`) on the public model page.
   */
  catalogHref?: string;
  initialLine?: string;
  /** P2 · F2: the server's read for this request — `null` is «no such model»; absent, the browser reads it. */
  initialModel?: CatalogModel | null;
  /**
   * V4-VARIANT-URL: the NEW variant to start from — the slug of one of `initialModel`'s variants, matched by the
   * page against `?variante=`, never the raw request value. Read once: a later prop does not move the buyer's choice.
   */
  initialVariant?: string;
}) {
  const { model, error, reload } = useCatalogModel(slug, initialModel);
  const { offered, line, setLine } = useLineChoice(model, initialLine);
  // The last new variant chosen: switching to the used line and back starts from it, as the address says.
  // PR D (review of #373): the ADDRESS's `?variante=` comes first. After a choice rewrote it (`replaceState`), leaving
  // and coming back with «Atrás» restores the page's first payload (its `initialVariant`) while the address names the
  // variant the buyer had chosen. `useSearchParams` is the request's on the server and the address in the browser, so
  // on a first load both agree with `initialVariant`. A slug that is not one of the model's variants is ignored; with
  // no server read (no model yet) it is checked later, against the model, by `selectionForVariant`.
  const fromAddress = useSearchParams()?.get(VARIANT_PARAM) ?? undefined;
  const [chosenVariant, setChosenVariant] = useState(() => (
    fromAddress && (!initialModel || (initialModel.variants ?? []).some((variant) => variant.slug === fromAddress))
      ? fromAddress
      : initialVariant));

  if (error && model === undefined) {
    return (
      <div className="v4-page mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-10">
        <div role="alert" className="rounded-2xl border border-danger-border bg-danger-surface p-6 text-sm text-danger">{error}</div>
      </div>
    );
  }

  if (model === undefined) {
    return (
      <div role="status" className="v4-page mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-10">
        <span className="sr-only">Cargando modelo…</span>
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-16">
          <div className="v4-stage aspect-square animate-pulse sm:aspect-[5/4]" />
          <div className="space-y-5">
            <div className="h-12 w-2/3 animate-pulse rounded-xl bg-surface" />
            <div className="h-40 animate-pulse rounded-3xl bg-surface" />
          </div>
        </div>
      </div>
    );
  }

  if (model === null) {
    return (
      <div className="v4-page mx-auto max-w-3xl px-4 py-24 sm:px-6">
        <h1 className="v4-title">Modelo no encontrado</h1>
        <p className="v4-small mt-4 leading-6">El modelo no está disponible o la dirección ya no corresponde a uno publicado.</p>
        <Link href={catalogHref} className="v4-link mt-8 inline-block">Ver catálogo</Link>
      </div>
    );
  }

  const soldOut = isModelSoldOut(model);
  const lineChips: Chip[] = offered.map((key) => ({ key, label: model.lines[key].label }));

  const intro = (
    <header>
      {model.category ? <p className="v4-eyebrow">{model.category.name}</p> : null}
      <h1 className="v4-title mt-3">{model.name}</h1>
      {soldOut ? (
        <p data-testid="model-sold-out" className="v4-small mt-3 font-bold text-danger">{SOLD_OUT_LABEL}</p>
      ) : null}
      {model.description?.trim() ? (
        <RichText source={model.description} collapsible testId="model-description" className="v4-small mt-5 max-w-xl leading-7" />
      ) : null}
      {error ? (
        <div role="alert" className="mt-6 rounded-2xl border border-danger-border bg-danger-surface p-3.5 text-sm text-danger">{error}</div>
      ) : null}
      {offered.length > 1 && line ? (
        <div className="mt-10">
          <ChipGroup label="Condición" chips={lineChips} chosen={line} onChoose={(key) => setLine(key as ConditionLine)} />
        </div>
      ) : null}
    </header>
  );

  return (
    <>
      <div className="v4-page mx-auto max-w-7xl px-4 pb-20 pt-8 sm:px-6 lg:px-10 lg:pt-12">
        <nav aria-label="Ruta del modelo" className="v4-small mb-8 flex flex-wrap items-center gap-2">
          <Link href="/" className="hover:text-foreground">Inicio</Link>
          <span aria-hidden="true">/</span>
          <Link href={catalogHref} className="hover:text-foreground">Catálogo</Link>
          {model.category ? (
            <>
              <span aria-hidden="true">/</span>
              <Link href={`/product?category=${model.category.slug}`} className="hover:text-foreground">{model.category.name}</Link>
            </>
          ) : null}
          <span aria-hidden="true">/</span>
          <span aria-current="page" className="text-foreground">{model.name}</span>
        </nav>

        <div className={GRID}>
          <div className="min-w-0 lg:col-start-2 lg:row-start-1">{intro}</div>
          {line === "new" ? (
            <NewLineV4 key="new" model={model} onReload={reload} initialVariant={chosenVariant} onVariant={setChosenVariant} />
          ) : line === "used" ? (
            <UsedLineV4 key="used" model={model} onReload={reload} />
          ) : (
            <Slots
              gallery={
                <Gallery
                  pictures={model.image_url ? [{ url: model.image_url, alt_text: "" }] : []}
                  index={0}
                  onIndex={() => {}}
                  source="catalog"
                  alt={model.name}
                  initial={initialOf(model)}
                />
              }
            >
              <p className="v4-small mt-10">Este modelo está agotado por ahora. Vuelve pronto o consulta con la tienda.</p>
              <WarrantyV4 />
            </Slots>
          )}
        </div>

        <AccessoriesV4 modelName={model.name} accessories={model.accessories} onReload={reload} />
      </div>
      <SupportStripV4 />
    </>
  );
}

export default ModelConfiguratorV4;
