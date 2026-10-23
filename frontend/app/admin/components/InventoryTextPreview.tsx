"use client";

/**
 * INV-TEXT-01/02: reconocimiento inicial EN EL NAVEGADOR.
 * Sin proveedor IA, sin almacenamiento y sin escritura de inventario. La única llamada es la búsqueda de
 * variantes del propio catálogo (sólo lectura), sin texto, costos, cantidades, IMEI ni series.
 * El modo minucioso sigue siendo los formularios y las importaciones existentes.
 *
 * INV-TEXT-03: «Generar borrador» prepara la importación oficial sin escribir nada (docs/adr-inv-text-03.md).
 * Ofertas: ensayo del importador oficial en el servidor, que lo revierte todo. Equipos propios: borrador en la
 * pestaña; sus IMEI y series no salen a ningún servidor. No hay botón de aplicar.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  MAX_TEXT_CHARS, previewSupplierText, refreshAfterEdits,
  type InventoryLine, type InventoryOrigin, type TextCandidate, type TextPreview,
} from "../lib/inventory-text-preview";
import { capacityInRange, maskedSource, matchCandidates, type MatchResult } from "../lib/inventory-text-match";
import {
  downloadOfferDraft, offerDraftPayload, offerSummary, planDraft, requestOfferDraft, unitDraftTsv, unitSummary,
  type DraftCandidate, type DraftSummary, type OfferDraftBody, type OfferDraftSettings, type UnitDraftSettings,
} from "../lib/inventory-text-draft";
import { fetchSuppliers, type SupplierRow } from "../lib/internal-api";

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

/** A notice about the cost, without its amounts, for whoever cannot see costs. */
function withoutAmounts(warning: string): string {
  return warning
    .replace(/^Costos ambiguos \([^)]*\)/, "Costos ambiguos")
    .replace(/^Importe \S+ demasiado bajo/, "Importe demasiado bajo");
}

/** A date as the import reads it (AAAA-MM-DD), in the browser's own day. */
function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function freshOfferSettings(): OfferDraftSettings {
  const today = new Date();
  const week = new Date(today);
  week.setDate(today.getDate() + 6);
  return { supplierId: null, validFrom: isoDay(today), validUntil: isoDay(week), leadTimeDays: "", complete: false };
}

type Access = {
  canSeeCosts: boolean;
  /** `products.manage`: the official offer preview's own permission — and so the draft's. */
  canManageProducts?: boolean;
  /** `inventory.adjust`: the device upload the own-stock draft is for. */
  canReceiveUnits?: boolean;
  companyId?: number | null;
};

export function InventoryTextPreview({ canSeeCosts, canManageProducts = false, canReceiveUnits = false, companyId = null }: Access) {
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
  // INV-TEXT-03 — the draft. Everything here lives in this tab; any correction makes the draft stale.
  const [prices, setPrices] = useState<Record<number, string>>({});
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [offerSettings, setOfferSettings] = useState<OfferDraftSettings>(freshOfferSettings);
  const [unitSettings, setUnitSettings] = useState<UnitDraftSettings>({ branch: "", reason: "" });
  const [suppliers, setSuppliers] = useState<SupplierRow[] | null>(null);
  const [suppliersFailed, setSuppliersFailed] = useState(false);
  const [draft, setDraft] = useState<{ summary: DraftSummary; body: OfferDraftBody | null; tsv: string } | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [draftNotice, setDraftNotice] = useState("");
  const draftRun = useRef(0);
  const dropDraft = () => { draftRun.current += 1; setDraft(null); setDrafting(false); setDraftNotice(""); };
  const current = (candidate: TextCandidate): TextCandidate => ({ ...candidate, ...edits[candidate.id] });
  // Review: a correction makes the row's earlier result stale — it is dropped, and searched again on request.
  const edit = (id: number, change: Edit) => {
    dropDraft();
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
      const found = await matchCandidates(preview.candidates.map(current), companyId);
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
      setPrices({}); setQuantities({}); setDraftError(""); dropDraft();
      // A supplier list that failed to load is asked for again on the next analysis.
      if (suppliersFailed) { setSuppliersFailed(false); setSuppliers(null); }
      setError("");
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : "No se pudo interpretar el texto.");
    }
  }
  const offersAllowed = origin === "supplier" && canManageProducts;
  useEffect(() => {
    if (!preview || !offersAllowed || suppliers !== null) return;
    let alive = true;
    fetchSuppliers({ active: "true" }, companyId)
      .then(page => { if (alive) setSuppliers(page.results); })
      .catch(() => {
        if (!alive) return;
        setSuppliers([]);
        setSuppliersFailed(true);
        setDraftError("No se pudieron cargar los proveedores. Vuelve a analizar la lista para reintentar.");
      });
    return () => { alive = false; };
  }, [preview, offersAllowed, suppliers, companyId]);

  /** The rows as they are now, with what the draft adds to each. */
  function draftRows(): DraftCandidate[] {
    if (!preview) return [];
    return refreshAfterEdits(preview.candidates.map(current)).map(candidate => {
      const found = matches[String(candidate.id)];
      const productId = chosen[candidate.id] ? Number(chosen[candidate.id]) : null;
      const quantity = quantities[candidate.id] ?? "";
      return {
        ...candidate, productId,
        productName: found?.options.find(option => option.product_id === productId)?.name ?? "",
        commercialPrice: prices[candidate.id] ?? "",
        confirmedQuantity: quantity === "" ? null : Number(quantity),
      };
    });
  }

  async function generateDraft() {
    setDraftError("");
    dropDraft();
    const plan = planDraft(draftRows(), origin);
    if (origin === "own") {
      setDraft({ summary: unitSummary(plan), body: null, tsv: unitDraftTsv(plan, unitSettings, canSeeCosts) });
      return;
    }
    if (offerSettings.supplierId === null) {
      setDraftError("Elige el proveedor de la lista.");
      return;
    }
    const body = offerDraftPayload(plan, offerSettings, canSeeCosts);
    if (!body.rows.length) {
      // Nothing to rehearse: the summary says why every row stayed out.
      setDraft({ summary: offerSummary(plan, null), body: null, tsv: "" });
      return;
    }
    const run = draftRun.current;
    setDrafting(true);
    try {
      const answer = await requestOfferDraft(body, companyId);
      if (run !== draftRun.current) return;
      setDraft({ summary: offerSummary(plan, answer), body, tsv: "" });
    } catch (cause) {
      if (run === draftRun.current) setDraftError(cause instanceof Error ? cause.message : "No se pudo generar el borrador.");
    } finally {
      if (run === draftRun.current) setDrafting(false);
    }
  }

  async function downloadDraft() {
    if (!draft?.body) return;
    try {
      await downloadOfferDraft(draft.body, companyId);
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : "No se pudo descargar el borrador.");
    }
  }

  async function copyUnits() {
    if (!draft?.tsv) return;
    try {
      await navigator.clipboard.writeText(draft.tsv);
      setDraftNotice("Filas copiadas, sin encabezados: pégalas bajo los encabezados de la plantilla de «Carga de equipos».");
    } catch {
      setDraftNotice("No se pudo copiar: este navegador no permite usar el portapapeles aquí.");
    }
  }

  /** «Cancelar»: the draft and the review are discarded; the pasted text stays. */
  function cancelDraft() {
    dropDraft();
    setDraftError("");
    setPreview(null);
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
          {/* `relative`: the table's sr-only labels are absolute; without it they escape the scroll box (360 px). */}
          <div className="relative overflow-x-auto">
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
                  {offersAllowed ? <th scope="col" className="p-2">Precio comercial</th> : null}
                  {offersAllowed ? <th scope="col" className="p-2">Cantidad</th> : null}
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
                    {offersAllowed ? (
                      <td className="p-2">
                        <input aria-label={`Precio comercial del registro ${candidate.id}`} inputMode="decimal"
                          value={prices[candidate.id] ?? ""} placeholder="Escribir"
                          onChange={event => {
                            dropDraft();
                            const value = event.target.value.replace(/[^0-9.,]/g, "").slice(0, 12);
                            setPrices(all => ({ ...all, [candidate.id]: value }));
                          }}
                          className="w-20 rounded border border-bd-border bg-background p-1" />
                      </td>
                    ) : null}
                    {offersAllowed ? (
                      <td className="p-2">
                        <input aria-label={`Cantidad del registro ${candidate.id}`} inputMode="numeric"
                          value={quantities[candidate.id] ?? ""} placeholder="Sin confirmar"
                          aria-describedby="draft-quantity-help"
                          onChange={event => {
                            dropDraft();
                            const value = event.target.value.replace(/\D/g, "").slice(0, 6);
                            setQuantities(all => ({ ...all, [candidate.id]: value }));
                          }}
                          className="w-20 rounded border border-bd-border bg-background p-1" />
                      </td>
                    ) : null}
                    <td className="p-2">
                      {found ? (
                        <>
                          <select aria-label={`Variante del registro ${candidate.id}`} value={chosen[candidate.id] ?? ""}
                            aria-describedby={found.reason ? `match-reason-${candidate.id}` : undefined}
                            onChange={event => { dropDraft(); setChosen(all => ({ ...all, [candidate.id]: event.target.value })); }}
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
                            <li key={index} className="text-warning">{canSeeCosts ? warning : withoutAmounts(warning)}</li>)}</ul>
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
                  <li key={index}><span className="font-mono">{maskedSource(item.line)}</span> — {item.reason}</li>
                ))}
              </ul>
            </details>
          ) : null}
          <p className="text-xs text-muted">
            Ningún candidato está confirmado. Las cantidades no se deducen de filas repetidas;
            una lista del proveedor no constituye recepción física. Luhn no verifica la existencia real de un equipo.
          </p>
          <DraftSection
            origin={origin} offersAllowed={offersAllowed} unitsAllowed={canReceiveUnits} suppliers={suppliers}
            offerSettings={offerSettings} unitSettings={unitSettings} drafting={drafting} error={draftError}
            notice={draftNotice} draft={draft?.summary ?? null} canDownload={Boolean(draft?.body)}
            onOfferSettings={change => { dropDraft(); setOfferSettings(all => ({ ...all, ...change })); }}
            onUnitSettings={change => { dropDraft(); setUnitSettings(all => ({ ...all, ...change })); }}
            onGenerate={() => void generateDraft()} onDownload={() => void downloadDraft()}
            onCopy={() => void copyUnits()} onKeepEditing={dropDraft} onCancel={cancelDraft} />
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

type DraftSectionProps = {
  origin: InventoryOrigin;
  offersAllowed: boolean;
  unitsAllowed: boolean;
  suppliers: SupplierRow[] | null;
  offerSettings: OfferDraftSettings;
  unitSettings: UnitDraftSettings;
  drafting: boolean;
  error: string;
  notice: string;
  draft: DraftSummary | null;
  canDownload: boolean;
  onOfferSettings: (change: Partial<OfferDraftSettings>) => void;
  onUnitSettings: (change: Partial<UnitDraftSettings>) => void;
  onGenerate: () => void;
  onDownload: () => void;
  onCopy: () => void;
  onKeepEditing: () => void;
  onCancel: () => void;
};

const fieldClass = "w-full rounded-lg border border-bd-border bg-background p-2";

/** INV-TEXT-03 — «Generar borrador» and its summary. Nothing here writes inventory. */
function DraftSection(props: DraftSectionProps) {
  const { origin, offersAllowed, unitsAllowed, offerSettings, unitSettings, draft } = props;
  const allowed = origin === "supplier" ? offersAllowed : unitsAllowed;
  return (
    <section aria-label="Borrador de importación" className="space-y-3 rounded-lg border border-bd-border p-3">
      <div>
        <h4 className="text-sm font-semibold">Borrador de importación</h4>
        <p className="mt-1 text-xs text-muted">
          {origin === "supplier"
            ? "Prepara las filas de «Ofertas de proveedores» y ensáyalas con su importador. No se escribe nada: ni ofertas, ni precios, ni stock."
            : "Prepara las filas de «Carga de equipos». Las series e IMEI no salen de esta pestaña; la carga oficial los valida al subir el archivo."}
        </p>
      </div>
      {!allowed ? (
        <p className="text-xs text-muted">
          {origin === "supplier"
            ? "El borrador de ofertas requiere permiso para gestionar productos."
            : "El borrador de equipos requiere permiso para ajustar inventario."}
        </p>
      ) : (
        <>
          {origin === "supplier" ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <label className="space-y-1 text-xs">
                <span className="block font-semibold">Proveedor</span>
                <select aria-label="Proveedor del borrador" className={fieldClass}
                  value={offerSettings.supplierId ?? ""}
                  onChange={event => props.onOfferSettings({ supplierId: event.target.value ? Number(event.target.value) : null })}>
                  <option value="">{props.suppliers === null ? "Cargando…" : "Elegir…"}</option>
                  {(props.suppliers ?? []).map(supplier => (
                    <option key={supplier.id} value={String(supplier.id)}>{supplier.name}</option>
                  ))}
                </select>
              </label>
              <label className="space-y-1 text-xs">
                <span className="block font-semibold">Vigente desde</span>
                <input type="date" aria-label="Vigente desde del borrador" className={fieldClass} value={offerSettings.validFrom}
                  onChange={event => props.onOfferSettings({ validFrom: event.target.value })} />
              </label>
              <label className="space-y-1 text-xs">
                <span className="block font-semibold">Vigente hasta</span>
                <input type="date" aria-label="Vigente hasta del borrador" className={fieldClass} value={offerSettings.validUntil}
                  onChange={event => props.onOfferSettings({ validUntil: event.target.value })} />
              </label>
              <label className="space-y-1 text-xs">
                <span className="block font-semibold">Plazo (días)</span>
                <input aria-label="Plazo del borrador" inputMode="numeric" className={fieldClass} value={offerSettings.leadTimeDays}
                  onChange={event => props.onOfferSettings({ leadTimeDays: event.target.value.replace(/\D/g, "").slice(0, 3) })} />
              </label>
              <label className="flex items-start gap-2 text-xs sm:col-span-2 lg:col-span-4">
                <input type="checkbox" checked={offerSettings.complete}
                  onChange={event => props.onOfferSettings({ complete: event.target.checked })} />
                <span>
                  Es la lista completa del proveedor: sus ofertas vigentes que no estén aquí se desactivarían al
                  aplicar en «Ofertas de proveedores», con confirmación. Sin marcar: lista parcial, lo demás no cambia.
                </span>
              </label>
              <p id="draft-quantity-help" className="text-xs text-muted sm:col-span-2 lg:col-span-4">
                Cantidad: vacía = sin confirmar (no se vende); 0 = agotada; un número = confirmada. Nunca se suman filas repetidas.
              </p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-xs">
                <span className="block font-semibold">Sucursal (como figura en el sistema)</span>
                <input aria-label="Sucursal del borrador" className={fieldClass} value={unitSettings.branch} maxLength={120}
                  onChange={event => props.onUnitSettings({ branch: event.target.value })} />
              </label>
              <label className="space-y-1 text-xs">
                <span className="block font-semibold">Motivo / referencia</span>
                <input aria-label="Motivo del borrador" className={fieldClass} value={unitSettings.reason} maxLength={120}
                  onChange={event => props.onUnitSettings({ reason: event.target.value })} />
              </label>
            </div>
          )}
          <button type="button" onClick={props.onGenerate} disabled={props.drafting}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-background disabled:opacity-40">
            {props.drafting ? "Generando…" : "Generar borrador"}
          </button>
        </>
      )}
      {props.error ? <p role="alert" className="text-xs text-danger">{props.error}</p> : null}
      {draft ? <DraftSummaryPanel {...props} draft={draft} /> : null}
    </section>
  );
}

function DraftSummaryPanel(props: DraftSectionProps & { draft: DraftSummary }) {
  const { draft } = props;
  const counts = draft.origin === "supplier"
    ? [
      ["Nuevas ofertas", draft.newOffers], ["Actualizaciones", draft.updates], ["Agotadas", draft.soldOut],
      ["Sin cambios", draft.unchanged], ["Cambios de precio", draft.priceChanges],
      ...(draft.deactivations !== null ? [["Posibles desactivaciones", draft.deactivations] as const] : []),
      ["Cantidades sin confirmar", draft.unknownQuantities],
    ]
    : [["Equipos para recibir", draft.unitsToReceive]];
  return (
    <div role="region" aria-label="Resumen del borrador" className="space-y-3 rounded-lg bg-background p-3 text-xs">
      <p className="font-semibold">
        Borrador listo: no se escribió nada.
        {draft.origin === "supplier"
          ? " Es lo que haría la importación oficial de ofertas con estas filas."
          : " La Carga de equipos vuelve a validar cada serie e IMEI contra los equipos existentes."}
      </p>
      <ul className="grid gap-1 sm:grid-cols-2">
        {counts.map(([label, value]) => <li key={label}>{`${label}: ${value}`}</li>)}
        <li>{`Necesitan revisión: ${draft.review.length}`}</li>
        <li>{`Duplicados: ${draft.duplicates}`}</li>
      </ul>
      {draft.priceChanges ? (
        <p className="text-warning">Hay cambios de precio comercial: al aplicar, el flujo oficial pide aceptarlos o mantener los actuales.</p>
      ) : null}
      {draft.looksTruncated ? (
        <p className="text-danger">La lista completa desactivaría la mayoría de lo vigente: parece incompleta y el flujo oficial la rechazará.</p>
      ) : null}
      {draft.review.length ? (
        <ul aria-label="Filas que necesitan revisión" className="space-y-1">
          {draft.review.map((item, index) => <li key={`${item.id}-${index}`} className="text-warning">{`Registro ${item.id}: ${item.reason}`}</li>)}
        </ul>
      ) : null}
      {props.notice ? <p role="status">{props.notice}</p> : null}
      <div className="flex flex-wrap gap-2">
        {draft.origin === "supplier" && props.canDownload ? (
          <button type="button" onClick={props.onDownload} className="rounded-lg border border-bd-border px-3 py-1.5 font-semibold">
            Descargar borrador (.xlsx)
          </button>
        ) : null}
        {draft.origin === "own" && draft.unitsToReceive ? (
          <button type="button" onClick={props.onCopy} className="rounded-lg border border-bd-border px-3 py-1.5 font-semibold">
            Copiar filas para la Carga de equipos
          </button>
        ) : null}
        <button type="button" onClick={props.onKeepEditing} className="rounded-lg border border-bd-border px-3 py-1.5 font-semibold">
          Seguir corrigiendo
        </button>
        <button type="button" onClick={props.onCancel} className="rounded-lg border border-bd-border px-3 py-1.5 font-semibold">
          Cancelar
        </button>
      </div>
      <p className="text-muted">
        {draft.origin === "supplier"
          ? "Para aplicarlo, sube el archivo en «Ofertas de proveedores»: allí se vuelve a revisar y se confirma."
          : "Para recibir los equipos, pega las filas en la plantilla de «Carga de equipos» y súbela allí."}
      </p>
    </div>
  );
}
