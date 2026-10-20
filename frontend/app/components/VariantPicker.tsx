"use client";

/**
 * DC2 — one picker per attribute of a model, stacked in the model's order.
 *
 * Each picker is a radio group: Tab enters at the chosen option, the arrows
 * move between the options that can be chosen, Space/Enter chooses.
 *
 * SUPPLIER-DEVICE-FLOW-02 (owner, 2026-10-20) — published is not available.
 * An option that leads only to SOLD-OUT variants can be chosen: the page then
 * shows that variant's photos and last price, says «Agotado» and sells
 * nothing. It is drawn muted and says «agotado» in its accessible name. An
 * option that leads to no variant at all stays VISIBLE, says «no disponible»,
 * is skipped by the arrows and pressing it does nothing.
 *
 * Text options are pills; colour options are round swatches painted with the
 * tenant's `swatch_hex`, named by their label (the chosen colour's name is also
 * written next to the attribute name, so it is not a colour-only cue).
 */

import { useId, useRef } from "react";
import {
  SOLD_OUT_LABEL,
  isOptionAvailable,
  isOptionSelectable,
  type ModelAttribute,
  type Selection,
  type Variant,
} from "../lib/catalog-models";

type Props = {
  attributes: ModelAttribute[];
  variants: Variant[];
  selection: Selection;
  onSelect: (index: number, optionId: number) => void;
};

const NOT_AVAILABLE = "no disponible";
const SOLD_OUT = SOLD_OUT_LABEL.toLowerCase();

function OptionGroup({
  attribute, index, variants, attributes, selection, onSelect,
}: {
  attribute: ModelAttribute;
  index: number;
  variants: Variant[];
  attributes: ModelAttribute[];
  selection: Selection;
  onSelect: (index: number, optionId: number) => void;
}) {
  const labelId = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const chosenId = selection[String(attribute.id)];
  const chosen = attribute.options.find((option) => option.id === chosenId) ?? null;
  const states = attribute.options.map((option) => {
    const selectable = isOptionSelectable(variants, attributes, selection, index, option.id);
    return {
      option,
      selectable,
      soldOut: selectable && !isOptionAvailable(variants, attributes, selection, index, option.id),
    };
  });
  const isColor = attribute.kind === "color";
  // Roving tab stop: the chosen option, or the first selectable one.
  const tabStop = Math.max(
    0,
    states.findIndex((s) => s.option.id === chosenId) >= 0
      ? states.findIndex((s) => s.option.id === chosenId)
      : states.findIndex((s) => s.selectable),
  );

  function move(from: number, step: 1 | -1 | "first" | "last") {
    const enabled = states.map((s, i) => (s.selectable ? i : -1)).filter((i) => i >= 0);
    if (enabled.length === 0) return;
    let target: number;
    if (step === "first") target = enabled[0];
    else if (step === "last") target = enabled[enabled.length - 1];
    else {
      const order = step === 1 ? enabled : [...enabled].reverse();
      target = order.find((i) => (step === 1 ? i > from : i < from)) ?? order[0];
    }
    onSelect(index, states[target].option.id);
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
    <div className="border-t border-bd-border pt-5">
      <p id={labelId} className="text-sm font-semibold text-foreground">
        {attribute.name}
        {isColor && chosen ? <>{" "}<span className="ml-1 font-normal text-muted">{chosen.label}</span></> : null}
      </p>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        className={`mt-3 flex flex-wrap ${isColor ? "gap-3" : "gap-2"}`}
      >
        {states.map(({ option, selectable, soldOut }, at) => {
          const checked = option.id === chosenId;
          const name = !selectable
            ? `${option.label}, ${NOT_AVAILABLE}`
            : soldOut ? `${option.label}, ${SOLD_OUT}` : option.label;
          const muted = !selectable || soldOut;
          const common = {
            ref: (el: HTMLButtonElement | null) => { refs.current[at] = el; },
            type: "button" as const,
            role: "radio",
            "aria-checked": checked,
            "aria-disabled": selectable ? undefined : true,
            tabIndex: at === tabStop ? 0 : -1,
            onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => onKeyDown(event, at),
            onClick: () => { if (selectable) onSelect(index, option.id); },
            "data-option-id": option.id,
          };
          if (isColor) {
            return (
              <button
                key={option.id}
                {...common}
                aria-label={name}
                title={name}
                className={`relative flex h-11 w-11 items-center justify-center rounded-full border-2 transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
                  checked ? "border-foreground" : "border-transparent hover:border-foreground/30"
                } ${selectable ? "" : "cursor-not-allowed"}`}
              >
                <span
                  aria-hidden="true"
                  // The ring keeps a white or very light swatch visible on a light page.
                  className={`block h-8 w-8 rounded-full border border-foreground/25 ${muted ? "opacity-35" : ""}`}
                  style={{ backgroundColor: option.swatch_hex || "transparent" }}
                />
                {muted ? (
                  <span aria-hidden="true" className="pointer-events-none absolute h-[2px] w-9 rotate-45 rounded bg-foreground/60" />
                ) : null}
              </button>
            );
          }
          return (
            <button
              key={option.id}
              {...common}
              aria-label={muted ? name : undefined}
              className={`min-h-11 rounded-xl border px-4 py-2.5 text-sm font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
                checked
                  ? "border-foreground bg-foreground text-background"
                  : !selectable
                    ? "cursor-not-allowed border-dashed border-bd-border bg-transparent text-muted line-through"
                    : soldOut
                      ? "border-dashed border-bd-border bg-transparent text-muted line-through hover:border-foreground/40"
                      : "border-bd-border bg-surface text-foreground hover:border-foreground/40"
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function VariantPicker({ attributes, variants, selection, onSelect }: Props) {
  return (
    <div className="space-y-5">
      {attributes.map((attribute, index) => (
        <OptionGroup
          key={attribute.id}
          attribute={attribute}
          index={index}
          attributes={attributes}
          variants={variants}
          selection={selection}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
