"use client";

/**
 * SELECTOR-UX-01 — «Elegir en el selector visual», under one line of «Actualizar lista de proveedor».
 *
 * THE device configurator (the till's chips, `DeviceConfigurator`) in multiple mode over the model's real palette:
 * capacity, SIM and region in common (one each), colours marked and unmarked (one configuration per colour). What the
 * line says is marked «leído», what stays in doubt «dudoso», what a person adds «a mano». A common step the text says
 * is fixed here (changing it is correcting the text). «Usar esta selección» hands the choice to the screen, which asks
 * the server what each configuration is; nothing here writes, prices or saves.
 */

import { useId, useMemo, useState } from "react";

import {
  colourAttributeOf, multiOptionState, multiResults, pickCommon, pickedWords, stepsToAsk, toggleColour, type MultiPick,
} from "../lib/device-configurator";
import type { ResolveRowResult } from "../lib/internal-api";
import type { SupplierLine } from "../lib/supplier-list-grammar";
import { lockedSteps, readingOf, sameSelection, type SupplierSpace } from "../lib/supplier-list-selector";
import { ConfiguratorStep, type ChipMark } from "./DeviceConfigurator";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

const touch = "min-h-[44px]";
const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2";

export type SpaceState = SupplierSpace | null | "error" | undefined;

export function SupplierLineSelector({
  line, result, modelName, space, onLoad, applied, problems, busy, onApply,
}: {
  line: SupplierLine;
  result: ResolveRowResult | undefined;
  modelName: string;
  /** undefined: not read yet; null: the model has no palette; "error": it could not be read. */
  space: SpaceState;
  onLoad: () => void;
  /** The choice in use for this line; null: the line as read. */
  applied: MultiPick | null;
  /** What the last choice could not send, in words. */
  problems: string[];
  busy: boolean;
  onApply: (pick: MultiPick | null) => void;
}) {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const ready = space && space !== "error" ? space : null;
  const reading = useMemo(() => (ready ? readingOf(ready, result) : null), [ready, result]);
  const locked = useMemo(() => (ready ? lockedSteps(ready, line, result) : {}), [ready, line, result]);
  const [draft, setDraft] = useState<MultiPick | null>(null);
  // What the person marked stays until the screen uses it (a refused or failed answer never loses it).
  const pending = draft && !(applied && sameSelection(draft, applied)) ? draft : null;
  const pick = pending ?? applied ?? reading?.pick ?? { picked: {}, colours: [] };
  const results = ready ? multiResults(ready, pick) : [];
  const colour = ready ? colourAttributeOf(ready) : null;
  const inUse = applied ?? reading?.pick ?? null;
  const changed = Boolean(inUse) && !sameSelection(pick, inUse!);

  const toggle = () => {
    if (!open && (space === undefined || space === "error")) onLoad();
    setOpen((value) => !value);
  };

  const markOf = (optionId: number): ChipMark | null => {
    if (!reading) return null;
    if (reading.recognised.has(optionId)) return { tone: "recognised", text: "leído" };
    if (reading.doubtful.has(optionId)) return { tone: "doubtful", text: "dudoso" };
    const chosen = Object.values(pick.picked).includes(optionId) || pick.colours.includes(optionId);
    return chosen ? { tone: "manual", text: "a mano" } : null;
  };

  return (
    <section aria-label={`Selector visual · línea ${line.number}`} className="min-w-0 rounded-lg border border-bd-border bg-background">
      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={toggle}
        className={`flex w-full ${touch} items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm font-semibold text-foreground ${focusRing}`}>
        <span>
          {`Elegir en el selector visual · ${modelName}`}
          {applied ? <span className="ml-2 rounded-full border border-bd-border px-2 text-[11px] font-semibold text-muted">en uso</span> : null}
        </span>
        <span aria-hidden="true" className={`text-muted transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}>⌄</span>
      </button>
      <div id={panelId} hidden={!open} className="min-w-0 space-y-4 border-t border-bd-border px-3 pb-3 pt-3">
        {space === undefined ? <p role="status" className="text-xs text-muted">Cargando la paleta del modelo…</p> : null}
        {space === "error" ? (
          <p role="alert" className="text-sm text-danger">No se pudo leer la paleta del modelo. Cierra y abre el selector para intentarlo otra vez.</p>
        ) : null}
        {space === null ? (
          <p className="text-sm text-muted">
            {`${modelName} no tiene paleta: el selector necesita las capacidades y colores que el modelo admite. Defínelas en Catálogo › Modelos.`}
          </p>
        ) : null}
        {ready ? (
          <>
            <p className="text-xs text-muted">
              Marca uno o varios colores: cada color es una configuración con la capacidad, la SIM y la región en común.
              Lo que la lista dice viene marcado «leído»; lo que añadas queda «a mano». Nada se guarda ni se crea aquí.
            </p>
            {stepsToAsk(ready).map((attribute) => {
              const isColour = colour?.id === attribute.id;
              return (
                <ConfiguratorStep
                  key={attribute.id}
                  name={`${panelId}-attribute-${attribute.id}`}
                  attribute={attribute}
                  multiple={isColour}
                  isChecked={(optionId) => (isColour ? pick.colours.includes(optionId) : pick.picked[attribute.id] === optionId)}
                  stateOf={(optionId) => multiOptionState(ready, pick, attribute.id, optionId)}
                  onToggle={(optionId) => setDraft(isColour ? toggleColour(pick, optionId) : pickCommon(ready, pick, attribute.id, optionId))}
                  markOf={markOf}
                  unavailableText="no existe con lo elegido antes"
                  lockedText={isColour ? undefined : locked[attribute.id]}
                  note={isColour ? "Gris: ese color no existe con la capacidad elegida." : null}
                />
              );
            })}
            <div aria-label={`Resultado del selector · línea ${line.number}`} role="group" className="min-w-0 space-y-2">
              <p className="text-xs font-semibold text-foreground">
                {results.length === 1 ? "1 configuración" : `${results.length} configuraciones`}
              </p>
              {results.length ? (
                <ul className="min-w-0 space-y-1 text-xs">
                  {results.map((item) => {
                    const words = pickedWords(ready, item.colourId !== null && colour ? { ...pick.picked, [colour.id]: item.colourId } : pick.picked);
                    const handPicked = item.colourId !== null && reading && !reading.recognised.has(item.colourId);
                    const state = item.problem === "incomplete"
                      ? { tone: "text-warning", text: `Incompleta: falta ${item.missing.join(", ")}.` }
                      : item.problem === "incompatible"
                        ? { tone: "text-warning", text: "No existe en este modelo (el modelo excluye esa combinación)." }
                        : item.combination?.variant
                          ? { tone: "text-success", text: `Variante existente: ${item.combination.variant.name}.` }
                          : item.combination?.archived
                            ? { tone: "text-muted", text: "Sólo una variante archivada la tiene: no se usa." }
                            : { tone: "text-foreground", text: "Sin variante todavía: se puede crear como borrador, con permiso." };
                    return (
                      <li key={item.colourId ?? 0} className="min-w-0 break-words">
                        <span className="font-semibold text-foreground">{`${modelName} · ${words}`}</span>
                        {handPicked ? <span className="ml-1 text-muted">(color a mano: la lista no lo dice)</span> : null}
                        <span className={`block ${state.tone}`}>{state.text}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-muted">Marca al menos un color.</p>
              )}
              <p className="text-[11px] text-muted">El servidor vuelve a comprobar cada configuración al usar la selección.</p>
            </div>
            {problems.length ? (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-warning">{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <button type="button" className={`${touch} ${focusRing} ${internalPrimaryButtonClass}`}
                disabled={busy || !results.length || (!changed && Boolean(applied))}
                onClick={() => { setDraft(pick); onApply(pick); }}>
                {`Usar esta selección (${results.length})`}
              </button>
              {applied || pending ? (
                <button type="button" className={`${touch} ${focusRing} ${internalButtonClass}`} disabled={busy}
                  onClick={() => { setDraft(null); if (applied) onApply(null); }}>
                  Volver a lo leído en la línea
                </button>
              ) : null}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
