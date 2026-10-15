"use client";

/**
 * DC2 — one used device, as the buyer sees it before choosing THIS one.
 *
 * Everything on the card is what the store published about that device: its
 * photos, options, battery reading, cosmetic grade, price and notes. A battery
 * with no reading says «no informado» — a number is never made up — and the
 * identifier is only the hint the server sends («IMEI termina en 1234»).
 */

import { useId, useState } from "react";
import { ProductImage } from "./ProductImage";
import { formatMoney } from "../lib/format";
import { batteryText, imeiHintText, optionsText, type UsedUnit } from "../lib/catalog-models";

type Props = {
  unit: UsedUnit;
  modelName: string;
  /** Attribute names in the model's order, so «128 GB · Azul» reads the same on every card. */
  attributeOrder?: string[];
  busy?: boolean;
  onBuy: (unit: UsedUnit) => void;
};

export function UsedUnitCard({ unit, modelName, attributeOrder = [], busy = false, onBuy }: Props) {
  const titleId = useId();
  const galleryId = useId();
  const [showGallery, setShowGallery] = useState(false);
  const [shown, setShown] = useState(0);
  const photos = unit.photos ?? [];
  const photo = photos[shown] ?? photos[0] ?? null;
  const options = optionsText(unit.options ?? {}, attributeOrder);
  const imei = imeiHintText(unit.imei_hint);

  return (
    <article
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-bd-border bg-surface"
    >
      <div className="relative aspect-[4/3] bg-background">
        {photo ? (
          <ProductImage
            key={photo.url}
            src={photo.url}
            alt={photo.alt_text || `${modelName}${options ? `, ${options}` : ""}`}
            sizes="(max-width: 640px) 100vw, (max-width: 1280px) 50vw, 33vw"
            className="object-contain p-4"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">Sin fotos</span>
          </div>
        )}
      </div>

      {photos.length > 1 ? (
        <div className="px-4 pt-3">
          <button
            type="button"
            aria-expanded={showGallery}
            aria-controls={galleryId}
            onClick={() => setShowGallery((open) => !open)}
            className="text-xs font-bold text-foreground underline underline-offset-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          >
            {showGallery ? "Ocultar fotos" : `Ver las ${photos.length} fotos`}
          </button>
          {showGallery ? (
            <div id={galleryId} role="group" aria-label="Fotos del equipo" className="mt-2 flex gap-2 overflow-x-auto pb-1">
              {photos.map((item, index) => (
                <button
                  key={item.url}
                  type="button"
                  aria-label={`Ver foto ${index + 1} de ${photos.length}`}
                  aria-pressed={index === shown}
                  onClick={() => setShown(index)}
                  className={`relative h-16 w-16 shrink-0 overflow-hidden rounded-lg border bg-background focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
                    index === shown ? "border-foreground" : "border-bd-border"
                  }`}
                >
                  <ProductImage src={item.url} alt="" sizes="64px" className="object-contain p-1" />
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-1 flex-col p-4 sm:p-5">
        <h3 id={titleId} className="font-display text-lg font-semibold leading-snug text-foreground">
          {options || modelName}
        </h3>
        <ul className="mt-3 space-y-1.5 text-sm text-muted">
          <li>{batteryText(unit.battery_health)}</li>
          {unit.cosmetic_grade ? (
            <li>
              <span className="font-semibold text-foreground">Estado: {unit.cosmetic_grade.label}</span>
              {unit.cosmetic_grade.description ? (
                <span className="mt-0.5 block text-xs">{unit.cosmetic_grade.description}</span>
              ) : null}
            </li>
          ) : null}
          {imei ? <li>{imei}</li> : null}
        </ul>
        {unit.notes ? <p className="mt-3 text-sm leading-6 text-muted">{unit.notes}</p> : null}

        <div className="mt-auto pt-5">
          <p className="font-display text-2xl font-extrabold tabular-nums tracking-[-0.03em] text-foreground">
            S/ {formatMoney(unit.price)}
          </p>
          <button
            type="button"
            onClick={() => onBuy(unit)}
            disabled={busy}
            aria-describedby={titleId}
            className="mt-3 w-full rounded-full bg-foreground px-4 py-3 text-sm font-extrabold uppercase tracking-[0.06em] text-background transition hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Agregando…" : "Comprar este equipo"}
          </button>
        </div>
      </div>
    </article>
  );
}
