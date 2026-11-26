"use client";

/**
 * SELECTOR-UX-01 — THE device configurator's look: one step of chips per role (capacity buttons, colour swatches,
 * SIM, region), the same everywhere a model's configuration is chosen. Born in the till's «Vender equipo por modelo»
 * (CATALOG-REFERENCE-01 · PR F, G); the till renders exactly what it rendered before through this component.
 *
 *   single    a radio group per step: arrows move between options, the native way.
 *   multiple  the colour step is a group of checkboxes (several colours, one configuration each); every other step
 *             stays a radio group (one capacity, SIM and region in common).
 *
 * An option that does not exist with what is chosen is disabled and says why (to a screen reader always; on screen
 * when the caller gives a note). The logic lives in `lib/device-configurator`; this file only draws.
 */

import { useId } from "react";

import { ProductImage } from "@/app/components/ProductImage";
import { NO_OPTION, isColourAttribute, type ConfiguratorAttribute, type ConfiguratorOption } from "../lib/device-configurator";

export const CONFIGURATOR_LABEL = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";

/** What the chip says of its option beyond its name: read in the text, in doubt, or chosen by hand. */
export type ChipMark = { tone: "recognised" | "doubtful" | "manual"; text: string };

const MARK_CLASS: Record<ChipMark["tone"], string> = {
  recognised: "border border-success-border bg-success-surface text-success",
  doubtful: "border border-warning-border bg-warning-surface text-warning",
  manual: "border border-bd-border bg-background text-muted",
};

export function ConfiguratorStep({
  attribute, multiple = false, extraOptions = [], isChecked, stateOf, onToggle, markOf, unavailableText = "no existe",
  lockedText, note, name, disabled = false,
}: {
  attribute: ConfiguratorAttribute;
  /** A group of checkboxes (several options) instead of a radio group. */
  multiple?: boolean;
  /** Options drawn after the attribute's own (the till's «Sin <atributo>»). */
  extraOptions?: ConfiguratorOption[];
  isChecked: (optionId: number) => boolean;
  stateOf: (optionId: number) => { possible: boolean; available?: number };
  onToggle: (optionId: number) => void;
  markOf?: (optionId: number) => ChipMark | null;
  /** Said (to a screen reader) on an option that does not exist with what is chosen. */
  unavailableText?: string;
  /** The step is fixed (the text already says it): only the chosen option stays enabled, and this says why. */
  lockedText?: string;
  /** Shown under the chips. */
  note?: string | null;
  /** The radio group's name; by default one per attribute. */
  name?: string;
  /** Every option off (the screen is busy, e.g. registering): nothing changes meanwhile. */
  disabled?: boolean;
}) {
  const labelId = useId();
  const colour = isColourAttribute(attribute);
  return (
    <div>
      <p id={labelId} className={CONFIGURATOR_LABEL}>{attribute.name}</p>
      <div role={multiple ? "group" : "radiogroup"} aria-label={attribute.name} className="flex flex-wrap gap-2">
        {[...attribute.options, ...extraOptions].map((option) => {
          const state = stateOf(option.id);
          const checked = isChecked(option.id);
          const locked = Boolean(lockedText) && !checked;
          const mark = markOf?.(option.id) ?? null;
          return (
            <label
              key={option.id}
              className={`relative inline-flex min-h-11 cursor-pointer select-none items-center gap-2 rounded-lg border px-3 py-2 text-sm transition has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-foreground/30 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-40 ${
                checked ? "border-foreground bg-foreground/[0.06] font-semibold text-foreground" : "border-bd-border bg-surface text-foreground hover:border-foreground/30"
              }`}
            >
              <input
                type={multiple ? "checkbox" : "radio"}
                className="sr-only"
                name={name ?? `attribute-${attribute.id}`}
                value={option.id}
                checked={checked}
                disabled={disabled || !state.possible || locked}
                onChange={() => onToggle(option.id)}
              />
              {/* G (owner's visual order 4): finish picture → company swatch → the name alone. */}
              {colour && option.id !== NO_OPTION && option.thumbnail ? (
                <span aria-hidden="true" data-testid="pos-chip-picture" className="relative h-5 w-5 shrink-0 overflow-hidden rounded-full border border-foreground/20 bg-background">
                  {/* Review of #377: through the optimizer at chip size, never the original file. */}
                  <ProductImage src={option.thumbnail} alt="" sizes="20px" className="scale-[2.2] object-cover" />
                </span>
              ) : colour && option.swatch_hex ? (
                <span
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 rounded-full border border-foreground/20"
                  style={{ backgroundColor: option.swatch_hex }}
                />
              ) : null}
              <span>{option.label}</span>
              {/* Only a real count is drawn: «sin stock» on every chip would be noise; the chosen device says it. */}
              {state.possible && (state.available ?? 0) > 0 ? (
                <span className="rounded-full bg-success-surface px-1.5 font-mono text-[11px] text-success">
                  {state.available}<span className="sr-only"> disponibles aquí</span>
                </span>
              ) : null}
              {mark ? (
                <span className={`rounded-full px-1.5 text-[10px] font-semibold uppercase tracking-wide ${MARK_CLASS[mark.tone]}`}>
                  {mark.text}
                </span>
              ) : null}
              {!state.possible ? <span className="sr-only">{unavailableText}</span> : null}
              {state.possible && locked ? <span className="sr-only">{lockedText}</span> : null}
            </label>
          );
        })}
      </div>
      {lockedText ? <p className="mt-1.5 text-xs text-muted">{lockedText}</p> : null}
      {note ? <p className="mt-1.5 text-xs text-muted">{note}</p> : null}
    </div>
  );
}
