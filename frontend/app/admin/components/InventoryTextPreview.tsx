"use client";

/**
 * INV-TEXT-01: reconocimiento inicial EN EL NAVEGADOR.
 * Sin fetch, sin proveedor IA, sin almacenamiento y sin escritura de inventario.
 * El modo minucioso sigue siendo los formularios y las importaciones existentes.
 */
import { useRef, useState } from "react";
import Link from "next/link";
import {
  MAX_TEXT_CHARS, previewSupplierText, refreshAfterEdits,
  type InventoryLine, type InventoryOrigin, type TextCandidate, type TextPreview,
} from "../lib/inventory-text-preview";
import { capacityInRange, maskedSource, matchCandidates, type MatchResult } from "../lib/inventory-text-match";

/** INV-TEXT-02: what the person corrected on a candidate. Stays in this preview; nothing is written. */
type Edit = Partial<Pick<TextCandidate, "model" | "capacityGb" | "colour" | "supplierCost" | "line">>;

function masked(value: string): string {
  if (!value) return "—";
  return "••••" + value.slice(-4);
}

/** INV-TEXT-02: what tells this variant apart — RAM, SIM, region, colour, watts, length, generation. */
function variantOf(candidate: TextCandidate): string {
  return [
    candidate.ramGb !== null ? `${candidate.ramGb} GB RAM` : "",
    candidate.sim === "esim" ? "eSIM" : candidate.sim === "chip" ? "Chip" : "",
    candidate.region, candidate.colour,
    candidate.watts !== null ? `${candidate.watts} W` : "", candidate.length,
    candidate.generation ? `${candidate.generation}.ª generación` : "",
  ].filter(Boolean).join(" · ");
}

export function InventoryTextPreview({ canSeeCosts }: { canSeeCosts: boolean }) {
  const [raw, setRaw] = useState("");
  const [line, setLine] = useState<InventoryLine>("used");
  const [origin, setOrigin] = useState<InventoryOrigin>("supplier");
  const [preview, setPreview] = useState<TextPreview | null>(null);
  const [error, setError] = useState("");
  const [edits, setEdits] = useState<Record<number, Edit>>({});
  const [matches, setMatches] = useState<Record<string, MatchResult>>({});
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [reviewed, setReviewed] = useState<Record<number, boolean>>({});
  const [matching, setMatching] = useState(false);
  const [matchError, setMatchError] = useState("");
  const current = (candidate: TextCandidate): TextCandidate => ({ ...candidate, ...edits[candidate.id] });
  // Review: a correction makes the row's earlier result stale — it is dropped, and searched again on request.
  const edit = (id: number, change: Edit) => {
    setEdits(all => ({ ...all, [id]: { ...all[id], ...change } }));
    if (change.supplierCost === undefined) {
      // A search in flight answers for the row as it WAS: its answer for this row is ignored.
      versions.current[id] = (versions.current[id] ?? 0) + 1;
      setMatches(all => { const next = { ...all }; delete next[String(id)]; return next; });
      setChosen(all => { const next = { ...all }; delete next[id]; return next; });
    }
  };
  // Each analysis has its own number: a search answered after a new analysis is ignored.
  const generation = useRef(0);
  const versions = useRef<Record<number, number>>({});
  async function findVariants() {
    if (!preview) return;
    const asked = generation.current;
    const sentVersions = { ...versions.current };
    const same = (id: number) => (versions.current[id] ?? 0) === (sentVersions[id] ?? 0);
    setMatching(true);
    setMatchError("");
    try {
      const found = await matchCandidates(preview.candidates.map(current));
      if (asked !== generation.current) return;
      const fresh = Object.fromEntries(Object.entries(found).filter(([key]) => same(Number(key))));
      setMatches(all => ({ ...all, ...fresh }));
      // An exact match is preselected; several, never. A choice the person already made is kept.
      setChosen(all => ({
        ...Object.fromEntries(Object.entries(fresh)
          .filter(([, result]) => result.status === "exact")
          .map(([key, result]) => [Number(key), String(result.options[0].product_id)])),
        ...all,
      }));
    } catch (cause) {
      if (asked === generation.current) setMatchError(cause instanceof Error ? cause.message : "No se pudo consultar el catálogo.");
    } finally {
      if (asked === generation.current) setMatching(false);
    }
  }
  function analyze() {
    try {
      setPreview(previewSupplierText(raw, { line, origin }));
      generation.current += 1;
      versions.current = {};
      setEdits({}); setMatches({}); setChosen({}); setReviewed({}); setMatching(false);
      setError("");
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : "No se pudo interpretar el texto.");
    }
  }
  function changeRaw(value: string) {
    setRaw(value);
    setPreview(null);
    setError("");
  }
  return (
    <div className="space-y-4 rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
      <div>
        <h3 className="text-sm font-semibold">Reconocer lista de WhatsApp · Vista previa local</h3>
        <p className="mt-1 text-xs text-muted">
          Elige el origen y la línea, pega varios mensajes y revisa el resultado.
          El texto no sale del navegador ni va a ninguna IA. No crea ni modifica productos, costos o stock.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="block font-semibold">Estos productos son</span>
          <select aria-label="Línea declarada" className="w-full rounded-lg border border-bd-border bg-background p-2"
            value={line} onChange={event => { setLine(event.target.value as InventoryLine); setPreview(null); }}>
            <option value="used">Seminuevos</option>
            <option value="new">Nuevos</option>
            <option value="accessory">Accesorios</option>
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="block font-semibold">Origen de la lista</span>
          <select aria-label="Origen declarado" className="w-full rounded-lg border border-bd-border bg-background p-2"
            value={origin} onChange={event => { setOrigin(event.target.value as InventoryOrigin); setPreview(null); }}>
            <option value="supplier">Proveedor (NO stock propio)</option>
            <option value="own">Recepción física propia (solo preparación)</option>
          </select>
        </label>
      </div>
      <label className="block space-y-1 text-sm">
        <span className="block font-semibold">Texto de WhatsApp</span>
        <textarea aria-label="Texto para reconocer" rows={7} maxLength={MAX_TEXT_CHARS}
          className="w-full resize-y rounded-lg border border-bd-border bg-background p-3 font-mono text-xs"
          placeholder={"iPhone 13\n128 GB\nBatería 93%\nNS: EJEMPLO123\nVersión: Chip\nColor: azul"}
          value={raw} onChange={event => changeRaw(event.target.value)} />
        <span className="block text-xs text-muted">{raw.length} / {MAX_TEXT_CHARS} caracteres. Los datos solo viven en esta pestaña.</span>
      </label>
      <button type="button" onClick={analyze} disabled={!raw.trim()}
        className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-background disabled:opacity-40">
        Analizar localmente
      </button>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {preview ? (
        <div className="space-y-3" aria-label="Vista previa de lista">
          <p role="status" className="text-sm font-medium">
            {preview.candidates.length} registros candidatos · {preview.discardedLines} líneas sin producto
            {" · "}{preview.candidates.filter(c => reviewed[c.id]).length} de {preview.candidates.length} revisados
            {preview.truncated ? " · Límite de 300 candidatos: lista incompleta, no aplicar" : ""}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => void findVariants()} disabled={matching || !preview.candidates.length}
              className="rounded-lg border border-bd-border px-3 py-1.5 text-xs font-semibold">
              {matching ? "Buscando…" : "Buscar en el catálogo"}
            </button>
            <span className="text-xs text-muted">
              Envía al servidor sólo modelo, capacidad, color, tipo y línea; nunca el texto, costos ni IMEI.
            </span>
          </div>
          {matchError ? <p role="alert" className="text-xs text-danger">{matchError}</p> : null}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Registros reconocidos, corregibles, con su variante del catálogo</caption>
              <thead>
                <tr className="border-b border-bd-border">
                  <th scope="col" className="p-2">#</th>
                  <th scope="col" className="p-2">Tipo</th>
                  <th scope="col" className="p-2">Modelo (corregible)</th>
                  <th scope="col" className="p-2">Capacidad</th>
                  <th scope="col" className="p-2">Color</th>
                  <th scope="col" className="p-2">Variante</th>
                  <th scope="col" className="p-2">Serie / IMEI</th>
                  {canSeeCosts ? <th scope="col" className="p-2">Costo proveedor</th> : null}
                  <th scope="col" className="p-2">Variante del catálogo</th>
                  <th scope="col" className="p-2">Revisión</th>
                </tr>
              </thead>
              <tbody>
                {refreshAfterEdits(preview.candidates.map(current)).map(candidate => {
                  const found = matches[String(candidate.id)];
                  return (
                  <tr key={candidate.id} className="border-b border-bd-border align-top">
                    <td className="p-2">{candidate.id}</td>
                    <td className="p-2">
                      <select aria-label={`Línea del registro ${candidate.id}`} value={candidate.line}
                        onChange={event => edit(candidate.id, { line: event.target.value as InventoryLine })}
                        className="rounded border border-bd-border bg-background p-1">
                        <option value="new">Nuevo</option><option value="used">Seminuevo</option>
                        <option value="accessory">Accesorio</option>
                      </select>
                    </td>
                    <td className="p-2">
                      <input aria-label={`Modelo del registro ${candidate.id}`} value={candidate.model} maxLength={120}
                        onChange={event => edit(candidate.id, { model: event.target.value })}
                        className="w-44 rounded border border-bd-border bg-background p-1" />
                      <details className="mt-1">
                        <summary className="cursor-pointer text-muted">Ver texto original</summary>
                        <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{maskedSource(candidate.source)}</pre>
                      </details>
                    </td>
                    <td className="p-2">
                      <input aria-label={`Capacidad del registro ${candidate.id}`} inputMode="numeric"
                        value={candidate.capacityGb ?? ""}
                        onChange={event => {
                          const value = event.target.value.replace(/\D/g, "");
                          edit(candidate.id, { capacityGb: value ? Number(value) : null });
                        }}
                        maxLength={4} className="w-16 rounded border border-bd-border bg-background p-1" /> GB
                      {capacityInRange(candidate.capacityGb) ? null
                        : <p className="mt-1 text-danger">Capacidad fuera de rango.</p>}
                    </td>
                    <td className="p-2">
                      <input aria-label={`Color del registro ${candidate.id}`} value={candidate.colour} maxLength={60}
                        onChange={event => edit(candidate.id, { colour: event.target.value })}
                        className="w-28 rounded border border-bd-border bg-background p-1" />
                    </td>
                    <td className="p-2">{variantOf({ ...candidate, colour: "" }) || "—"}
                      {candidate.batteryPercent !== null ? ` · ${candidate.batteryPercent}%` : ""}</td>
                    <td className="p-2">{masked(candidate.serial)} / {masked(candidate.imei)}
                      {candidate.imei2 ? " / " + masked(candidate.imei2) : ""}</td>
                    {canSeeCosts ? (
                      <td className="p-2">
                        <input aria-label={`Costo del registro ${candidate.id}`} inputMode="decimal"
                          value={candidate.supplierCost} placeholder="Pendiente"
                          onChange={event => edit(candidate.id, { supplierCost: event.target.value.replace(/[^0-9.,]/g, "") })}
                          className="w-20 rounded border border-bd-border bg-background p-1" />
                      </td>
                    ) : null}
                    <td className="p-2">
                      {found ? (
                        <>
                          <select aria-label={`Variante del registro ${candidate.id}`} value={chosen[candidate.id] ?? ""}
                            aria-describedby={found.reason ? `match-reason-${candidate.id}` : undefined}
                            onChange={event => setChosen(all => ({ ...all, [candidate.id]: event.target.value }))}
                            disabled={!found.options.length}
                            className="max-w-48 rounded border border-bd-border bg-background p-1">
                            <option value="">{found.options.length ? "Elegir…" : "Sin coincidencia"}</option>
                            {found.options.map(option => (
                              <option key={option.product_id} value={String(option.product_id)}>{option.name}</option>
                            ))}
                          </select>
                          {found.reason ? <p id={`match-reason-${candidate.id}`} className="mt-1 text-warning">{found.reason}</p> : null}
                        </>
                      ) : <span className="text-muted">—</span>}
                    </td>
                    <td className="p-2">
                      <label className="mb-1 flex items-center gap-1">
                        <input type="checkbox" checked={Boolean(reviewed[candidate.id])}
                          onChange={event => setReviewed(all => ({ ...all, [candidate.id]: event.target.checked }))} />
                        Revisado<span className="sr-only"> registro {candidate.id}</span>
                      </label>
                      {candidate.warnings.length
                        ? <ul className="space-y-1">{candidate.warnings.map((warning, index) =>
                            <li key={index} className="text-warning">{warning}</li>)}</ul>
                        : <span className="text-success">Sin avisos de formato</span>}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {preview.unrecognized.length ? (
            <details className="text-xs">
              <summary>{preview.unrecognized.length} líneas no reconocidas: revisa que ninguna sea un producto</summary>
              <ul className="mt-2 space-y-1" aria-label="Líneas no reconocidas">
                {preview.unrecognized.map((item, index) => (
                  <li key={index}><span className="font-mono">{item.line}</span> — {item.reason}</li>
                ))}
              </ul>
            </details>
          ) : null}
          <p className="text-xs text-muted">
            Ningún candidato está confirmado. Las cantidades no se deducen de filas repetidas;
            una lista del proveedor no constituye recepción física. Luhn no verifica la existencia real de un equipo.
          </p>
          <p className="text-xs text-muted">
            Para validar y registrar individualmente, utiliza las herramientas existentes:
            {" "}<Link className="underline" href="/admin/inventory/units/import">Carga de equipos</Link>
            {" · "}<Link className="underline" href="/admin/products/suppliers/offers">Ofertas de proveedores</Link>.
          </p>
        </div>
      ) : null}
    </div>
  );
}
