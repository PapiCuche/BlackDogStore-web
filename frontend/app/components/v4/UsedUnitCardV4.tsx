"use client";

/**
 * P2 · F1 — one used device in the V4 list, chosen with one press.
 *
 * Choosing it does not buy it: it fills the summary (and the gallery with this
 * device's own photos), where «Comprar este equipo» is. Everything on the card
 * is what the shop published about THIS device: its options, its grade, its
 * battery reading (only when there is one — never a made-up number), declared
 * faults and its own warranty (USED-READY-01), and its price. The photo is
 * this device's own: the web only offers a used device that has one.
 */

import { ProductImage } from "../ProductImage";
import { formatMoney } from "../../lib/format";
import { optionsText, unitWarrantyText, type UsedUnit } from "../../lib/catalog-models";

type Props = {
  unit: UsedUnit;
  modelName: string;
  /** Attribute names in the model's order, so «128 GB · Azul» reads the same on every card. */
  attributeOrder?: string[];
  selected: boolean;
  onSelect: () => void;
};

export function UsedUnitCardV4({ unit, modelName, attributeOrder = [], selected, onSelect }: Props) {
  const photo = unit.photos?.[0] ?? null;
  const options = optionsText(unit.options ?? {}, attributeOrder);
  const details = [
    unit.cosmetic_grade ? `Estado: ${unit.cosmetic_grade.label}` : null,
    unit.battery_health !== null && unit.battery_health !== undefined ? `Batería: ${unit.battery_health}%` : null,
    // USED-READY-01 — only what the shop stated about THIS device.
    unit.functional_state === "with_faults" ? "Con fallas declaradas" : null,
    unitWarrantyText(unit.warranty_days),
  ].filter(Boolean);

  return (
    <button type="button" aria-pressed={selected} onClick={onSelect} className="v4-unit">
      <span className="relative block h-20 w-20 shrink-0 overflow-hidden rounded-xl bg-surface">
        {photo ? (
          <ProductImage src={photo.url} alt="" sizes="80px" className="object-contain p-1.5" />
        ) : (
          <span className="v4-small flex h-full items-center justify-center text-center text-xs">Sin fotos</span>
        )}
      </span>
      <span className="flex min-w-0 flex-1 flex-col justify-center">
        <span className="v4-data block truncate">{options || modelName}</span>
        {details.length > 0 ? <span className="v4-small mt-1 block">{details.join(" · ")}</span> : null}
        <span className="v4-price mt-1.5 block">S/ {formatMoney(unit.price)}</span>
      </span>
    </button>
  );
}
