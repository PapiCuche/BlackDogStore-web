"use client";

/**
 * SERIAL-PICK-01 — el campo donde se dice QUÉ equipo.
 *
 * DOS FORMAS, Y NO SE TRATAN IGUAL:
 *
 *   lector    el código entra a ráfaga y se acepta solo.
 *   a mano    cuando el lector no lee. Un IMEI se valida aquí (15 dígitos y su
 *             dígito de control) y el equipo que el servidor encuentra se
 *             MUESTRA —modelo, estado, IMEI, serie— para que la persona diga
 *             «sí, es este» antes de aceptarlo. Un dígito cambiado es otro
 *             equipo; verlo es la comprobación.
 *
 * Cuál de las dos fue lo decide el ritmo de las teclas, o el botón «escribirlo
 * a mano». El servidor lo guarda junto a quién lo hizo.
 */

import { useRef, useState } from "react";
import type { UnitDescription } from "../../lib/admin";
import {
  imeiProblem, newRhythm, resetRhythm, rhythmChanged, rhythmKey, rhythmMethod, type EntryMethod,
} from "../lib/unit-entry";

type Props = {
  /** Pregunta al servidor qué equipo es ese código. Rechaza con su motivo. */
  lookup: (code: string, method: EntryMethod) => Promise<UnitDescription>;
  onPicked: (unit: UnitDescription, method: EntryMethod) => void | Promise<void>;
  /**
   * El equipo que se espera lleva IMEI: a mano se escribe el IMEI, y se
   * comprueba aquí antes de preguntar. Sin saberlo, o si no lleva, lo que se
   * escribe lo juzga el servidor (una serie puede ser sólo números).
   */
  requiresImei?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
};

export function UnitEntry({ lookup, onPicked, requiresImei, disabled, autoFocus }: Props) {
  const [code, setCode] = useState("");
  const [byHand, setByHand] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toConfirm, setToConfirm] = useState<UnitDescription | null>(null);
  const field = useRef<HTMLInputElement>(null);
  const rhythm = useRef(newRhythm());

  function forgetTyping() {
    resetRhythm(rhythm.current);
  }

  async function submit() {
    const clean = code.trim();
    if (!clean || busy || disabled) return;
    const method: EntryMethod = byHand ? "manual" : rhythmMethod(rhythm.current, clean);
    forgetTyping();
    setError(null);
    setToConfirm(null);

    if (method === "manual" && requiresImei) {
      // A mano, un equipo con IMEI es su IMEI: se comprueba antes de preguntar por él.
      const problem = imeiProblem(clean);
      if (problem) {
        setError(problem);
        return;
      }
    }

    setBusy(true);
    try {
      const unit = await lookup(clean, method);
      if (method === "scanner") {
        setCode("");
        await onPicked(unit, "scanner");
      } else {
        setToConfirm(unit);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo comprobar ese equipo.");
    } finally {
      setBusy(false);
      field.current?.focus();
    }
  }

  async function confirm() {
    if (!toConfirm) return;
    const unit = toConfirm;
    setToConfirm(null);
    setCode("");
    setBusy(true);
    try {
      await onPicked(unit, "manual");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo registrar ese equipo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <label className="block text-xs text-muted" htmlFor="unit-entry-code">IMEI o serie del equipo</label>
      <input
        id="unit-entry-code"
        ref={field}
        value={code}
        autoFocus={autoFocus}
        disabled={disabled || busy}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => {
          rhythmChanged(rhythm.current, event.target.value);
          setCode(event.target.value);
          setError(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            void submit();
            return;
          }
          rhythmKey(rhythm.current, event.key, performance.now());
        }}
        placeholder={byHand ? "Escribe el IMEI (15 dígitos) y pulsa Enter" : "Escanea el IMEI o la serie"}
        className="w-full rounded border border-bd-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none"
      />

      <button
        type="button"
        aria-pressed={byHand}
        disabled={disabled || busy}
        onClick={() => { setByHand((was) => !was); setError(null); forgetTyping(); field.current?.focus(); }}
        className="text-xs text-muted underline transition hover:text-foreground"
      >
        {byHand ? "Volver a usar el lector" : "El lector no lee: escribirlo a mano"}
      </button>

      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}

      {toConfirm ? (
        <UnitConfirmation
          unit={toConfirm}
          onConfirm={() => void confirm()}
          onReject={() => { setToConfirm(null); field.current?.focus(); }}
        />
      ) : null}
    </div>
  );
}

/**
 * El equipo que el servidor encontró para un código escrito a mano, a la vista
 * de quien lo tiene en la mano. Nada se acepta hasta que diga que es ese.
 */
export function UnitConfirmation({ unit, onConfirm, onReject }: {
  unit: UnitDescription;
  onConfirm: () => void;
  onReject: () => void;
}) {
  return (
    <div role="group" aria-label="Confirma el equipo" className="rounded-lg border border-bd-border bg-surface p-3 text-sm">
      <p className="font-semibold text-foreground">{unit.product_name}</p>
      <p className="mt-1 text-xs text-muted">
        {unit.condition_label}
        {unit.imei ? ` · IMEI ${unit.imei}` : ""}
        {unit.imei2 ? ` · IMEI 2 ${unit.imei2}` : ""}
        {` · Serie ${unit.serial_number}`}
      </p>
      <p className="mt-2 text-xs text-muted">Compáralo con el equipo que tienes en la mano.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={onConfirm} className="rounded-full bg-foreground px-4 py-1.5 text-xs font-semibold text-background">
          Confirmar este equipo
        </button>
        <button type="button" onClick={onReject} className="rounded-full border border-bd-border px-4 py-1.5 text-xs text-foreground">
          No es este
        </button>
      </div>
    </div>
  );
}
