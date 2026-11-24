"use client";

/**
 * VARIANT-SAFE-01 — «Archivar», «Desarchivar» o «Eliminar» una variante, desde su fila del modelo.
 *
 * Lo primero es preguntar al servidor qué depende de ella (`deletion-check`): la explicación sale de esa
 * respuesta, nunca de una suposición de la pantalla. Eliminar sólo se ofrece a quien tiene «Eliminar
 * variantes» y sólo a una variante sin historial, con motivo y su nombre escrito; con historial, se explica
 * qué la bloquea y se ofrece archivarla. Archivar se niega mientras quede stock, y lo dice con las cifras.
 * El servidor vuelve a decidir todo bajo bloqueo: un rechazo suyo se muestra tal cual y se vuelve a leer.
 */

import { useEffect, useId, useRef, useState } from "react";

import {
  CatalogApiError, archiveVariant, deleteVariant, fetchVariantDeletionCheck, unarchiveVariant,
  type VariantDeletionCheck,
} from "../lib/internal-api";
import { ErrorBox, Spinner } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

export type VariantLifecycleMode = "archive" | "unarchive" | "delete";

const REASON_MIN = 5;
const REASON_MAX = 500;
const touch = "min-h-[44px]";

const TITLES: Record<VariantLifecycleMode, string> = {
  archive: "Archivar variante",
  unarchive: "Desarchivar variante",
  delete: "Eliminar variante",
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What still sits on a shelf, in words (only what is above zero). */
export function stockSentence(stock: VariantDeletionCheck["stock"]): string {
  const parts = [
    stock.available_units ? plural(stock.available_units, "equipo disponible", "equipos disponibles") : "",
    stock.reserved_units ? plural(stock.reserved_units, "equipo apartado", "equipos apartados") : "",
    stock.in_transit_units ? plural(stock.in_transit_units, "equipo en tránsito", "equipos en tránsito") : "",
    stock.stock ? plural(stock.stock, "unidad en stock", "unidades en stock") : "",
  ].filter(Boolean);
  return parts.join(", ");
}

export function VariantLifecycleDialog({
  productId, name, mode: initialMode, companyId, onDone, onCancel,
}: {
  productId: number;
  name: string;
  mode: VariantLifecycleMode;
  companyId: number | null;
  /** The action went through: the message to announce (the editor reads the model again). */
  onDone: (message: string, mode: VariantLifecycleMode) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<VariantLifecycleMode>(initialMode);
  const [check, setCheck] = useState<VariantDeletionCheck | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [reads, setReads] = useState(0);
  const titleId = useId();
  const reasonId = useId();
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let live = true;
    fetchVariantDeletionCheck(productId, companyId).then(
      (answer) => { if (live) { setCheck(answer); setLoadError(null); } },
      (err) => { if (live) setLoadError(err instanceof Error ? err.message : "No se pudo revisar la variante."); },
    );
    return () => { live = false; };
  }, [productId, companyId, reads]);

  useEffect(() => { heading.current?.focus(); }, [mode]);

  const cleanReason = reason.trim();
  const reasonOk = cleanReason.length >= REASON_MIN && cleanReason.length <= REASON_MAX;
  const nameOk = check !== null && typed.trim() === check.name.trim();

  async function run() {
    if (!check) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "delete") {
        await deleteVariant(productId, { reason: cleanReason, confirm_name: typed.trim() }, companyId);
        onDone(`«${check.name}» se eliminó. El motivo quedó en la auditoría.`, mode);
      } else if (mode === "archive") {
        await archiveVariant(productId, cleanReason, companyId);
        onDone(`«${check.name}» quedó archivada: ya no aparece en la tienda, la caja ni las cargas.`, mode);
      } else {
        await unarchiveVariant(productId, cleanReason, companyId);
        onDone(`«${check.name}» se desarchivó. Sigue inactiva: actívala cuando vuelva a venderse.`, mode);
      }
    } catch (err) {
      setError(err instanceof CatalogApiError || err instanceof Error ? err.message : "No se pudo completar la acción.");
      // What depends on it may have changed under us: read it again.
      setReads((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }

  let body: React.ReactNode;
  if (loadError) {
    body = <ErrorBox message={loadError} />;
  } else if (!check) {
    body = <Spinner label="Revisando qué depende de esta variante…" />;
  } else if (mode === "delete" && !check.can_delete) {
    body = (
      <p className="text-sm text-foreground">
        Eliminar una variante necesita el permiso «Eliminar variantes». Puedes archivarla.
      </p>
    );
  } else if (mode === "delete" && !check.deletable) {
    body = (
      <div className="space-y-2 text-sm">
        <p className="text-foreground">
          No se puede eliminar: tiene historial o datos que dependen de ella. Se conservan; archívala para que deje de
          usarse.
        </p>
        <ul aria-label="Lo que impide eliminarla" className="list-disc space-y-0.5 pl-5 text-muted">
          {check.blockers.map((blocker) => (
            <li key={blocker.code}>{blocker.label}: {blocker.count}</li>
          ))}
        </ul>
      </div>
    );
  } else if (mode === "archive" && check.archived) {
    body = <p className="text-sm text-foreground">Esta variante ya está archivada.</p>;
  } else if (mode === "archive" && !check.archivable) {
    body = (
      <p className="text-sm text-foreground">
        Todavía tiene stock ({stockSentence(check.stock) || "en alguna sucursal"}): trasládalo, véndelo o dalo de baja
        antes de archivarla.
      </p>
    );
  } else if (mode === "unarchive" && !check.archived) {
    body = <p className="text-sm text-foreground">Esta variante no está archivada.</p>;
  } else if (mode !== "delete" && !check.can_archive) {
    body = <p className="text-sm text-foreground">Necesitas el permiso «Administrar productos».</p>;
  } else {
    body = (
      <form
        className="space-y-3 text-sm"
        onSubmit={(event) => { event.preventDefault(); if (reasonOk && (mode !== "delete" || nameOk)) void run(); }}
      >
        <p className="text-foreground">
          {mode === "delete"
            ? "No tiene historial ni datos que dependan de ella. Se eliminará para siempre junto con sus opciones y sus códigos."
            : mode === "archive"
              ? "Se conserva con todo su historial, pero sale de la tienda, la caja, las cargas y la paleta. Nadie podrá crear otra igual."
              : "Volverá al catálogo inactiva: no se vende hasta que la actives."}
        </p>
        <label htmlFor={reasonId} className="block text-xs text-muted">
          Motivo (queda en la auditoría)
        </label>
        <textarea
          id={reasonId}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          maxLength={REASON_MAX}
          className={internalInputClass}
        />
        {mode === "delete" ? (
          <label className="block text-xs text-muted">
            Escribe «{check.name}» para confirmar
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              className={`mt-1.5 ${internalInputClass}`}
            />
          </label>
        ) : null}
        <button
          type="submit"
          className={`${touch} ${internalPrimaryButtonClass}`}
          disabled={busy || !reasonOk || (mode === "delete" && !nameOk)}
        >
          {busy ? "Guardando…" : mode === "delete" ? "Eliminar definitivamente" : mode === "archive" ? "Archivar" : "Desarchivar"}
        </button>
      </form>
    );
  }

  const offerArchive = mode === "delete" && check !== null && !check.deletable && check.archivable && check.can_archive;

  return (
    <section role="dialog" aria-labelledby={titleId} className="space-y-3 rounded-lg border border-bd-border p-3">
      <h3 id={titleId} ref={heading} tabIndex={-1} className="break-words text-sm font-semibold text-foreground focus:outline-none">
        {TITLES[mode]}: {check?.name ?? name}
      </h3>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {body}
      <div className="flex flex-wrap gap-2">
        {offerArchive ? (
          <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => { setError(null); setMode("archive"); }}>
            Archivar en su lugar
          </button>
        ) : null}
        <button type="button" className={`${touch} ${internalButtonClass}`} onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
      </div>
    </section>
  );
}
