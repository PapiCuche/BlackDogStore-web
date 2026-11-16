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
 * WHERE EACH PHOTO COMES FROM: a picture the shop did not upload says
 * «Imagen referencial»; a used device's own photos say «Fotos de este equipo»
 * (`photoLabel`).
 */

import Link from "next/link";
import { useId, useRef, useState, type ReactNode } from "react";
import { ProductImage } from "../ProductImage";
import { OnOrderOfferPrompt, OverStockOnOrderOption } from "../OnOrderOffer";
import { RichText } from "../RichText";
import { useStorefront } from "../StorefrontProvider";
import { UsedUnitCardV4 } from "./UsedUnitCardV4";
import { SupportStripV4 } from "./SupportStripV4";
import { formatMoney } from "../../lib/format";
import {
  SOLD_OUT_LABEL,
  availabilityLabel,
  imeiHintText,
  isOptionAvailable,
  isOptionSelectable,
  optionsText,
  type CatalogModel,
  type ConditionLine,
  type ModelAttribute,
  type Selection,
  type Variant,
} from "../../lib/catalog-models";
import {
  isModelSoldOut,
  photoLabel,
  useCatalogModel,
  useLineChoice,
  useNewLineConfigurator,
  useUsedLineConfigurator,
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
  swatch?: string;
};

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
              {chip.swatch !== undefined ? (
                <span aria-hidden="true" className="v4-swatch" style={{ backgroundColor: chip.swatch || "transparent" }} />
              ) : null}
              {chip.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function AttributeChips({
  attribute, index, attributes, variants, selection, onSelect,
}: {
  attribute: ModelAttribute;
  index: number;
  attributes: ModelAttribute[];
  variants: Variant[];
  selection: Selection;
  onSelect: (index: number, optionId: number) => void;
}) {
  const chosenId = selection[String(attribute.id)];
  const isColor = attribute.kind === "color";
  const chips: Chip[] = attribute.options.map((option) => {
    const selectable = isOptionSelectable(variants, attributes, selection, index, option.id);
    const available = selectable && isOptionAvailable(variants, attributes, selection, index, option.id);
    return {
      key: String(option.id),
      label: option.label,
      state: !selectable ? "unavailable" : available ? "available" : "sold-out",
      ...(isColor ? { swatch: option.swatch_hex } : {}),
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

type Picture = { url: string; alt_text: string };

function Gallery({
  pictures, index, onIndex, source, alt, emptyText, initial,
}: {
  pictures: Picture[];
  index: number;
  onIndex: (index: number) => void;
  source: "catalog" | "device";
  alt: string;
  /** Said when there is no picture; without it, the model's initial is drawn. */
  emptyText?: string;
  initial: string;
}) {
  const shown = pictures[index] ?? pictures[0] ?? null;
  const caption = shown ? photoLabel(shown.url, source) : null;
  return (
    <section data-testid="v4-gallery" aria-label="Fotos">
      <div className="v4-stage">
        {shown ? (
          <div className="relative aspect-square sm:aspect-[5/4]">
            <ProductImage
              key={shown.url}
              src={shown.url}
              alt={shown.alt_text || alt}
              className="object-contain p-8 sm:p-14"
              sizes="(max-width: 1024px) 100vw, 55vw"
              priority
            />
            {caption ? <span className="v4-image-label">{caption}</span> : null}
          </div>
        ) : (
          <div className="flex aspect-square flex-col items-center justify-center gap-4 px-6 text-center sm:aspect-[5/4]">
            <span aria-hidden="true" className="text-6xl font-bold text-[color:var(--v4-line-strong)]">{initial}</span>
            {emptyText ? <p className="v4-small">{emptyText}</p> : null}
          </div>
        )}
      </div>
      {pictures.length > 1 ? (
        <div role="group" aria-label="Elegir foto" className="mt-4 flex gap-2.5 overflow-x-auto pb-1">
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

function StatusLine({ status }: { status: ConfiguratorStatus }) {
  if (!status) return null;
  return (
    <div
      role={status.kind === "error" ? "alert" : "status"}
      className={`mt-4 rounded-2xl border p-3.5 text-sm ${
        status.kind === "success" ? "border-bd-border bg-surface text-foreground" : "border-danger-border bg-danger-surface text-danger"
      }`}
    >
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

function Layout({ gallery, children }: { gallery: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-16">
      <div className="min-w-0 lg:sticky lg:top-8 lg:self-start">{gallery}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function initialOf(model: CatalogModel): string {
  return model.name.trim().charAt(0).toUpperCase() || "·";
}

// ---------------------------------------------------------------------------
// The two lines
// ---------------------------------------------------------------------------

function NewLineV4({ model, intro, onReload }: { model: CatalogModel; intro: ReactNode; onReload: () => void }) {
  const c = useNewLineConfigurator(model, onReload);
  const condition = model.lines?.new?.label ?? "";

  if (c.variants.length === 0) {
    return (
      <Layout gallery={<Gallery pictures={[]} index={0} onIndex={() => {}} source="catalog" alt={model.name} initial={initialOf(model)} />}>
        {intro}
        <p className="v4-small mt-10">No hay opciones disponibles en esta línea por ahora.</p>
      </Layout>
    );
  }

  const variant = c.variant;
  return (
    <Layout
      gallery={
        <Gallery
          pictures={c.gallery}
          index={c.imageIndex}
          onIndex={c.showImage}
          source="catalog"
          alt={variant?.name || model.name}
          initial={initialOf(model)}
        />
      }
    >
      {intro}
      <div className="mt-10 space-y-8">
        {c.attributes.map((attribute, index) => (
          <AttributeChips
            key={attribute.id}
            attribute={attribute}
            index={index}
            attributes={c.attributes}
            variants={c.variants}
            selection={c.selection}
            onSelect={c.choose}
          />
        ))}
      </div>

      <section data-testid="v4-summary" aria-label="Tu elección" className="v4-summary mt-10">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
          <Fact term="Modelo">{model.name}</Fact>
          <Fact term="Opción">{variant?.name ?? "—"}</Fact>
          <Fact term="Condición">{condition}</Fact>
          <Fact term="Disponibilidad">{variant ? availabilityLabel(variant) : SOLD_OUT_LABEL}</Fact>
        </dl>
        <div className="mt-6 flex items-baseline justify-between gap-4 border-t border-bd-border pt-5">
          <span className="v4-small">{c.onOrder ? "Precio bajo pedido" : "Precio"}</span>
          <p data-testid="v4-price" aria-live="polite" className="v4-price">
            {variant ? `S/ ${formatMoney(c.onOrder ? c.onOrder.price : variant.price)}` : "—"}
          </p>
        </div>
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
    </Layout>
  );
}

function UsedLineV4({ model, intro, onReload }: { model: CatalogModel; intro: ReactNode; onReload: () => void }) {
  const c = useUsedLineConfigurator(model, onReload);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [photo, setPhoto] = useState<{ unit: string | null; index: number }>({ unit: null, index: 0 });
  const condition = model.lines?.used?.label ?? "";
  const chosen = c.visible.find((unit) => unit.id === chosenId) ?? c.visible[0] ?? null;
  const photoIndex = chosen && photo.unit === chosen.id ? photo.index : 0;
  const options = chosen ? optionsText(chosen.options ?? {}, c.order) : "";
  const imei = chosen ? imeiHintText(chosen.imei_hint) : null;

  if (c.units.length === 0) {
    return (
      <Layout gallery={<Gallery pictures={[]} index={0} onIndex={() => {}} source="device" alt={model.name} initial={initialOf(model)} />}>
        {intro}
        <p className="v4-small mt-10">No hay equipos disponibles en esta línea por ahora. Vuelve pronto o consulta con la tienda.</p>
      </Layout>
    );
  }

  return (
    <Layout
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
      {intro}

      {c.filterable.length > 0 ? (
        <div className="mt-10 space-y-6">
          {c.filterable.map((filter) => (
            <div key={filter.name} role="group" aria-label={`Filtrar por ${filter.name}`}>
              <p className="v4-section-title">{filter.name}</p>
              <div className="mt-3 flex flex-wrap gap-2.5">
                {["", ...filter.values].map((value) => (
                  <button
                    key={value || "todas"}
                    type="button"
                    aria-pressed={(c.filters[filter.name] ?? "") === value}
                    onClick={() => c.setFilter(filter.name, value)}
                    className="v4-chip"
                  >
                    {value || "Todas"}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <p className="v4-small mt-10" aria-live="polite">
        {c.visible.length === 1 ? "1 equipo" : `${c.visible.length} equipos`}
      </p>
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
            <p data-testid="v4-price" aria-live="polite" className="v4-price">S/ {formatMoney(chosen.price)}</p>
          </div>
          <button
            type="button"
            onClick={() => void c.buy(chosen)}
            disabled={c.busyUnit === chosen.id}
            className="v4-cta mt-5"
          >
            {c.busyUnit === chosen.id ? "Agregando…" : "Comprar este equipo"}
          </button>
          <StatusLine status={c.status} />
        </section>
      ) : (
        <StatusLine status={c.status} />
      )}
      <WarrantyV4 />
    </Layout>
  );
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export function ModelConfiguratorV4({ slug, initialLine }: { slug: string; initialLine?: string }) {
  const { model, error, reload } = useCatalogModel(slug);
  const { offered, line, setLine } = useLineChoice(model, initialLine);

  if (error && model === undefined) {
    return (
      <div className="v4-page mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-10">
        <div role="alert" className="rounded-2xl border border-danger-border bg-danger-surface p-6 text-sm text-danger">{error}</div>
      </div>
    );
  }

  if (model === undefined) {
    return (
      <div className="v4-page mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-10" aria-label="Cargando modelo">
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
        <Link href="/product" className="v4-link mt-8 inline-block">Ver catálogo</Link>
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
          <Link href="/product" className="hover:text-foreground">Catálogo</Link>
          {model.category ? (
            <>
              <span aria-hidden="true">/</span>
              <Link href={`/product?category=${model.category.slug}`} className="hover:text-foreground">{model.category.name}</Link>
            </>
          ) : null}
          <span aria-hidden="true">/</span>
          <span className="text-foreground">{model.name}</span>
        </nav>

        {line === "new" ? (
          <NewLineV4 key="new" model={model} intro={intro} onReload={reload} />
        ) : line === "used" ? (
          <UsedLineV4 key="used" model={model} intro={intro} onReload={reload} />
        ) : (
          <Layout
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
            {intro}
            <p className="v4-small mt-10">Este modelo está agotado por ahora. Vuelve pronto o consulta con la tienda.</p>
            <WarrantyV4 />
          </Layout>
        )}
      </div>
      <SupportStripV4 />
    </>
  );
}

export default ModelConfiguratorV4;
