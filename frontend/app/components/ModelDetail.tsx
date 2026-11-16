"use client";

/**
 * DC2 — the page of a model: one product, its two lines.
 *
 * NEW line: one picker per attribute; the chosen combination is a variant, and
 * its price, photos and availability follow the choice. «Agregar al carrito»
 * adds one device of that variant — the server picks which one.
 *
 * USED line: each card is ONE device, with its own price and photos; «Comprar
 * este equipo» puts exactly that device in the cart.
 *
 * The line names come from the API (each company names its own lines); a line
 * with nothing to show is not offered.
 *
 * SUPPLIER-DEVICE-FLOW-02 (owner, 2026-10-20) — published is not available,
 * and sold out is not hidden. A sold-out variant can be chosen: its photos and
 * last price are shown, it says «Agotado» and the cart button is disabled. A
 * model with nothing to sell says «Agotado» next to its name; with no line to
 * show at all (a used-only model without devices) its own photo is shown.
 *
 * P2 · F1 — the behaviour (reading the model, the choice, the cart writes)
 * lives in `lib/model-configurator.ts`, shared with the V4 preview. This file
 * is only the V3 presentation of it.
 */

import Link from "next/link";
import { useRef } from "react";
import { ProductImage } from "./ProductImage";
import { UsedUnitCard } from "./UsedUnitCard";
import { VariantPicker } from "./VariantPicker";
import { formatMoney } from "../lib/format";
import { OnOrderOfferPrompt, OverStockOnOrderOption } from "./OnOrderOffer";
import { RichText } from "./RichText";
import { SOLD_OUT_LABEL, availabilityLabel, type CatalogModel } from "../lib/catalog-models";
import {
  isModelSoldOut,
  useCatalogModel,
  useLineChoice,
  useNewLineConfigurator,
  useUsedLineConfigurator,
  type ConfiguratorStatus as Status,
} from "../lib/model-configurator";

function StatusBox({ status }: { status: Status }) {
  if (!status) return null;
  return (
    <div
      role={status.kind === "error" ? "alert" : "status"}
      className={`mt-4 rounded-xl border p-3 text-sm ${
        status.kind === "success"
          ? "border-bd-border bg-background text-foreground"
          : "border-danger-border bg-danger-surface text-danger"
      }`}
    >
      {status.message}
      {status.kind === "success" ? (
        <>
          {" "}
          <Link href="/cart" className="font-bold underline underline-offset-4">Ver carrito</Link>
        </>
      ) : null}
    </div>
  );
}

function NewLine({
  model, onReload,
}: { model: CatalogModel; onReload: () => void }) {
  const {
    attributes, variants, selection, variant, gallery, imageIndex, shown, showImage: setChosenImage,
    choose, add, busy, status, offer, available, onOrder, overStock,
  } = useNewLineConfigurator(model, onReload);

  if (variants.length === 0) {
    return <p className="rounded-2xl border border-dashed border-bd-border px-6 py-10 text-center text-sm text-muted">No hay opciones disponibles en esta línea por ahora.</p>;
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[1.15fr_0.85fr] lg:gap-14">
      <div className="min-w-0">
        <div className="relative overflow-hidden rounded-[1.75rem] border border-bd-border bg-surface">
          {shown ? (
            <div data-testid="model-main-image" className="relative aspect-[4/3] min-h-72 lg:min-h-[520px]">
              <ProductImage
                key={shown.url}
                src={shown.url}
                alt={shown.alt_text || variant?.name || model.name}
                className="object-contain p-6 sm:p-10"
                sizes="(max-width: 1024px) 100vw, 58vw"
                priority
              />
            </div>
          ) : (
            <div className="flex aspect-[4/3] min-h-72 items-center justify-center lg:min-h-[520px]">
              <div className="flex h-28 w-28 items-center justify-center rounded-full border border-bd-border text-5xl font-black text-foreground/[0.10]" aria-hidden="true">
                {model.name.trim().charAt(0).toUpperCase() || "·"}
              </div>
            </div>
          )}
        </div>
        {gallery.length > 1 ? (
          <div role="group" aria-label="Imágenes de la opción elegida" className="mt-3 flex gap-2 overflow-x-auto pb-1">
            {gallery.map((image, index) => (
              <button
                key={image.url}
                type="button"
                aria-label={`Ver imagen ${index + 1} de ${gallery.length}`}
                aria-pressed={index === imageIndex}
                onClick={() => setChosenImage(index)}
                className={`relative h-20 w-20 shrink-0 overflow-hidden rounded-xl border bg-surface transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
                  index === imageIndex ? "border-foreground" : "border-bd-border hover:border-foreground/40"
                }`}
              >
                <ProductImage src={image.url} alt="" sizes="80px" className="object-contain p-1.5" />
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col">
        <VariantPicker attributes={attributes} variants={variants} selection={selection} onSelect={choose} />

        <div className="mt-8 rounded-2xl border border-bd-border bg-surface p-5 sm:p-6">
          {variant ? <p className="text-sm font-semibold text-foreground">{variant.name}</p> : null}
          <span className="mt-3 block text-[10px] font-bold uppercase tracking-[0.14em] text-muted">
            {onOrder ? "Precio bajo pedido" : "Precio"}
          </span>
          <p
            data-testid="model-price"
            aria-live="polite"
            className="mt-1 font-display text-4xl font-extrabold tabular-nums tracking-[-0.04em] text-foreground sm:text-5xl"
          >
            {variant ? `S/ ${formatMoney(onOrder ? onOrder.price : variant.price)}` : "—"}
          </p>
          <div className="mt-4 flex items-center gap-2 text-sm">
            <span className={`h-2 w-2 rounded-full ${onOrder ? "bg-warning" : available ? "bg-success" : "bg-muted/40"}`} aria-hidden="true" />
            <span data-testid="model-availability" className={available ? "text-foreground" : "text-muted"}>
              {variant ? availabilityLabel(variant) : SOLD_OUT_LABEL}
            </span>
          </div>
          {onOrder ? (
            <p className="mt-2 text-xs leading-5 text-muted">
              No lo tenemos en tienda ahora: lo pedimos para ti al confirmar tu compra.
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void add(onOrder !== null)}
            disabled={busy || !variant || !available}
            className="mt-5 w-full rounded-full bg-foreground px-4 py-3.5 text-sm font-extrabold uppercase tracking-[0.08em] text-background transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Agregando…" : onOrder ? "Pedir bajo pedido" : !variant || !available ? SOLD_OUT_LABEL : "Agregar al carrito"}
          </button>
          {offer ? (
            <OnOrderOfferPrompt offer={offer} busy={busy} onAccept={() => void add(true)} />
          ) : overStock ? (
            <OverStockOnOrderOption offer={overStock} busy={busy} onChoose={() => void add(true)} />
          ) : null}
          <StatusBox status={status} />
        </div>
      </div>
    </div>
  );
}

function UsedLine({ model, onReload }: { model: CatalogModel; onReload: () => void }) {
  const { order, units, filterable, filters, setFilter, visible, busyUnit, status, buy } =
    useUsedLineConfigurator(model, onReload);

  if (units.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-bd-border px-6 py-10 text-center text-sm text-muted">
        No hay equipos disponibles en esta línea por ahora. Vuelve pronto o consulta con la tienda.
      </p>
    );
  }

  return (
    <div>
      {filterable.length > 0 ? (
        <div className="mb-6 flex flex-wrap gap-3" role="group" aria-label="Filtrar equipos">
          {filterable.map((filter) => (
            <label key={filter.name} className="block min-w-40 text-xs font-semibold text-muted">
              {filter.name}
              <select
                value={filters[filter.name] ?? ""}
                onChange={(event) => setFilter(filter.name, event.target.value)}
                className="mt-1.5 block w-full min-h-11 rounded-xl border border-bd-border bg-surface px-3 text-sm text-foreground focus:border-foreground/25 focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
              >
                <option value="">Todas</option>
                {filter.values.map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
          ))}
        </div>
      ) : null}

      <StatusBox status={status} />

      <p className="mb-4 mt-2 text-xs text-muted" aria-live="polite">
        {visible.length === 1 ? "1 equipo" : `${visible.length} equipos`}
      </p>
      {visible.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-bd-border px-6 py-10 text-center text-sm text-muted">
          Ningún equipo coincide con estos filtros.
        </p>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((unit) => (
            <UsedUnitCard
              key={unit.id}
              unit={unit}
              modelName={model.name}
              attributeOrder={order}
              busy={busyUnit === unit.id}
              onBuy={(chosen) => void buy(chosen)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ModelDetail({ slug, initialLine }: { slug: string; initialLine?: string }) {
  const { model, error, reload: load } = useCatalogModel(slug);
  const { offered, line, setLine } = useLineChoice(model, initialLine);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);

  if (error && model === undefined) {
    return (
      <div className="mx-auto max-w-7xl px-6 py-16 lg:px-8">
        <div className="rounded-2xl border border-danger-border bg-danger-surface p-6 text-sm text-danger" role="alert">{error}</div>
      </div>
    );
  }

  if (model === undefined) {
    return (
      <div className="mx-auto max-w-7xl px-6 py-12 lg:px-8" aria-label="Cargando modelo">
        <div className="grid gap-10 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="aspect-[4/3] animate-pulse rounded-[1.75rem] bg-surface" />
          <div className="space-y-4">
            <div className="h-10 w-2/3 animate-pulse rounded-xl bg-surface" />
            <div className="h-32 animate-pulse rounded-2xl bg-surface" />
          </div>
        </div>
      </div>
    );
  }

  if (model === null) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center bg-background px-6">
        <div className="max-w-md rounded-2xl border border-dashed border-bd-border px-6 py-12 text-center">
          <p className="font-display text-2xl font-extrabold uppercase text-foreground">Modelo no encontrado</p>
          <p className="mt-2 text-sm leading-6 text-muted">El modelo no está disponible o la dirección ya no corresponde a uno publicado.</p>
          <Link href="/product" className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
            Ver catálogo
          </Link>
        </div>
      </div>
    );
  }

  function onTabKey(event: React.KeyboardEvent<HTMLButtonElement>, at: number) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const last = offered.length - 1;
    const next = event.key === "Home" ? 0 : event.key === "End" ? last
      : event.key === "ArrowRight" ? (at >= last ? 0 : at + 1) : (at <= 0 ? last : at - 1);
    setLine(offered[next]);
    tabs.current[next]?.focus();
  }

  const panelId = `model-line-${slug}`;
  const soldOut = isModelSoldOut(model);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8 lg:py-14">
        <nav className="mb-8 flex flex-wrap items-center gap-2 text-xs font-medium text-muted" aria-label="Ruta del modelo">
          <Link href="/" className="transition hover:text-foreground">Inicio</Link>
          <span aria-hidden="true">/</span>
          <Link href="/product" className="transition hover:text-foreground">Catálogo</Link>
          {model.category ? (
            <>
              <span aria-hidden="true">/</span>
              <Link href={`/product?category=${model.category.slug}`} className="transition hover:text-foreground">
                {model.category.name}
              </Link>
            </>
          ) : null}
          <span aria-hidden="true">/</span>
          <span className="text-foreground/80">{model.name}</span>
        </nav>

        <header className="mb-8 max-w-3xl">
          <h1 className="font-display text-[min(3rem,10.5vw)] font-semibold leading-[1.08] tracking-tight text-foreground sm:text-6xl">
            {model.name}
          </h1>
          {soldOut ? (
            <span
              data-testid="model-sold-out"
              className="mt-4 inline-flex rounded-full border border-danger-border bg-danger-surface px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest text-danger"
            >
              {SOLD_OUT_LABEL}
            </span>
          ) : null}
          {model.description?.trim() ? (
            <RichText source={model.description} collapsible testId="model-description" className="mt-4 text-sm leading-7 text-muted sm:text-base" />
          ) : null}
        </header>

        {error ? (
          <div className="mb-6 rounded-xl border border-danger-border bg-danger-surface p-3 text-sm text-danger" role="alert">{error}</div>
        ) : null}

        {offered.length > 1 ? (
          <div role="tablist" aria-label="Línea" className="mb-8 inline-flex rounded-full border border-bd-border bg-surface p-1">
            {offered.map((key, at) => (
              <button
                key={key}
                ref={(el) => { tabs.current[at] = el; }}
                id={`${panelId}-tab-${key}`}
                type="button"
                role="tab"
                aria-selected={line === key}
                aria-controls={panelId}
                tabIndex={line === key ? 0 : -1}
                onClick={() => setLine(key)}
                onKeyDown={(event) => onTabKey(event, at)}
                className={`min-h-11 rounded-full px-5 py-2 text-sm font-bold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
                  line === key ? "bg-foreground text-background" : "text-muted hover:text-foreground"
                }`}
              >
                {model.lines[key].label}
              </button>
            ))}
          </div>
        ) : null}

        {line === null ? (
          <div className="grid gap-10 lg:grid-cols-[1.15fr_0.85fr] lg:gap-14">
            {model.image_url ? (
              <div className="relative overflow-hidden rounded-[1.75rem] border border-bd-border bg-surface">
                <div data-testid="model-main-image" className="relative aspect-[4/3] min-h-72 lg:min-h-[520px]">
                  <ProductImage
                    src={model.image_url}
                    alt={model.name}
                    className="object-contain p-6 opacity-50 sm:p-10"
                    sizes="(max-width: 1024px) 100vw, 58vw"
                    priority
                  />
                </div>
              </div>
            ) : null}
            <p className="self-start rounded-2xl border border-dashed border-bd-border px-6 py-10 text-center text-sm text-muted">
              Este modelo está agotado por ahora. Vuelve pronto o consulta con la tienda.
            </p>
          </div>
        ) : (
          <div
            id={panelId}
            {...(offered.length > 1 ? { role: "tabpanel", "aria-labelledby": `${panelId}-tab-${line}` } : {})}
          >
            {line === "new" ? <NewLine model={model} onReload={load} /> : <UsedLine model={model} onReload={load} />}
          </div>
        )}
      </div>
    </div>
  );
}
