"use client";

/**
 * UNIT-IMPORT · cargar muchos equipos con serie desde un Excel.
 *
 * UNA FILA = UN EQUIPO FÍSICO. La pantalla lo dice antes de adjuntar nada,
 * porque el error de siempre es escribir «iPhone 16, cantidad 2»: eso no dice
 * qué dos teléfonos son.
 *
 * Dos pasos, siempre: previsualizar (no registra nada) y registrar. Aquí no se
 * valida un IMEI ni se cuenta un equipo: se enseña lo que respondió el
 * servidor, fila por fila, y entra todo el archivo o no entra nada.
 */

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import {
  applyUnitImport, approveUnitImportTerms, fetchUnitImportJob, fetchUnitImportVoidPreview, previewUnitImport,
  unitImportErrorsUrl, unitImportJobPath, unitImportReportUrl, unitImportTemplateUrl, voidUnitImport,
  UnitImportVoidError,
  type UnitEntryKind, type UnitImportBlocking, type UnitImportJob, type UnitImportLine, type UnitImportPriceReview,
  type UnitImportPriceUpdate, type UnitImportRow, type UnitImportSimulation, type UnitImportVoidPreview,
} from "@/app/lib/stock-units";

import { EXISTING_STOCK_HELP } from "../lib/inventory-receipts";
import { CostColumnNotice } from "./CostColumnNotice";
import { ImportKindNotice } from "./ImportKindNotice";
import { OutdatedPreviewNotice, outdatedPreview, type OutdatedPreview } from "./ImportWizard";
import { Panel, TableWrap, Td, Th } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const CONDITIONS: Record<string, string> = {
  new: "Nuevo", open_box: "Caja abierta", refurbished: "Reacondicionado", used: "Usado",
};

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

function Outcome({ row }: { row: UnitImportRow }) {
  if (row.errors.length) {
    return (
      <ul className="space-y-0.5 text-danger">
        {row.errors.map((error) => <li key={error}>{error}</li>)}
      </ul>
    );
  }
  if (row.action === "skip") {
    return <span className="text-muted">{row.warnings[0] ?? "No se carga."}</span>;
  }
  return (
    <span>
      Se registrará
      {row.warnings.length ? <span className="block text-xs text-muted">{row.warnings.join(" · ")}</span> : null}
    </span>
  );
}

/**
 * INV-LOAD-01 — what registering the file would do, as the SERVER simulated it from the shop as it is now: per branch ×
 * product, the available devices before → after, and how many Kardex lines of each type it writes.
 */
function Simulation({ simulation }: { simulation: UnitImportSimulation }) {
  const { movements } = simulation;
  return (
    <section className="mb-4 space-y-3" aria-labelledby="unit-import-simulation">
      <h3 id="unit-import-simulation" className="text-sm font-semibold text-foreground">Resultado si registras el archivo</h3>
      <p role="note" aria-label="Movimientos en el Kardex" className="text-sm text-muted">
        Movimientos en el Kardex: {movements.initial_stock} de inventario inicial · {movements.purchase_entry} de compra
        {movements.existing_stock !== undefined ? ` · ${movements.existing_stock} de inventario existente` : ""}.
      </p>
      <TableWrap>
        <caption className="sr-only">Resultado si registras el archivo</caption>
        <thead>
          <tr className="border-b border-bd-border">
            <Th>Sucursal</Th><Th>Producto</Th><Th>Entran</Th><Th>Equipos disponibles</Th><Th>Stock</Th>
          </tr>
        </thead>
        <tbody>
          {simulation.shelves.map((shelf) => (
            <tr key={`${shelf.branch_id}-${shelf.product_id}`} className="border-b border-bd-border">
              <Td>{shelf.branch}</Td>
              <Td>{shelf.product}</Td>
              <Td>{shelf.adds}</Td>
              <Td>{shelf.units_before} → {shelf.units_after}</Td>
              <Td>{shelf.stock_before} → {shelf.stock_after}</Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </section>
  );
}

const LINE_LABELS: Record<string, string> = { new: "equipos nuevos", used: "seminuevos" };

function soles(amount: string) {
  return `S/ ${amount}`;
}

function signed(difference: string) {
  return difference.startsWith("-") ? `-S/ ${difference.slice(1)}` : `+S/ ${difference}`;
}

function shopState(entry: UnitImportPriceReview) {
  if (entry.pending_draft) return "Borrador sin habilitar: no se vende (cambiar el precio no la habilita)";
  if (entry.is_sold_online) return "Publicada en la tienda";
  return entry.is_published_online ? "No se vende en la tienda" : "No publicada";
}

function when(at: string | null) {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("es-PE", { dateStyle: "short", timeStyle: "short" });
}

/**
 * IMPORT-TERMS-APPROVAL — the file brings devices' own sale prices or warranties (the authority of `products.manage`).
 * Who holds it may approve them, so somebody without it can register the load with them. Who does not hold it
 * registers the devices without them, or waits for that approval (the link reopens this load). The server decides.
 */
function TermsApproval({ job, canApprove, disabled, onApprove, onRegisterWithout }: {
  job: UnitImportJob;
  canApprove: boolean;
  disabled: boolean;
  onApprove: () => void;
  onRegisterWithout: () => void;
}) {
  const approval = job.terms_approval ?? null;
  const valid = Boolean(approval?.valid);
  const rows = job.commercial_terms_rows?.length ?? 0;
  // The whole address, to paste in a message: the block only renders in the browser, after a load arrived.
  const link = `${window.location.origin}${unitImportJobPath(job.id)}`;
  return (
    <section
      role="group"
      aria-label="Precios y garantías del archivo"
      className="mb-4 space-y-2 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning"
    >
      {canApprove && !valid ? (
        <p>
          Esta carga trae precios o garantías de seminuevos{rows ? ` (${plural(rows, "fila", "filas")})` : ""}. Tú
          administras productos: si la registras tú, se aplican. Si la registrará otra persona sin ese permiso,
          apruébalos antes.
        </p>
      ) : valid ? (
        <p>Esta carga trae precios o garantías de seminuevos y ya están aprobados: al registrarla se aplican.</p>
      ) : (
        <p>
          Esta carga trae precios o garantías de seminuevos. Necesita la aprobación de quien administra productos.
        </p>
      )}
      <p className="text-xs">
        Preparó: {job.created_by || "—"}
        {approval && valid ? ` · Aprobados por ${approval.by_name}${approval.at ? ` el ${when(approval.at)}` : ""}` : ""}
      </p>
      {approval && !valid ? (
        <p>
          La aprobación de {approval.by_name} ya no vale: los precios o garantías cambiaron, o ya no administra
          productos.
        </p>
      ) : null}
      {canApprove && !valid ? (
        <button type="button" className={internalPrimaryButtonClass} disabled={disabled} onClick={onApprove}>
          Aprobar precios y garantías
        </button>
      ) : null}
      {!canApprove && !valid ? (
        <>
          <p>
            Puedes registrar los equipos ahora sin los precios ni las garantías del archivo: quedan sin precio propio y
            con la garantía de la empresa. Para registrarlos con ellos, comparte este enlace con quien administra
            productos y vuelve a abrirlo cuando los apruebe.
          </p>
          <button type="button" className={internalButtonClass} disabled={disabled} onClick={onRegisterWithout}>
            Registrar sin precios ni garantías
          </button>
        </>
      ) : null}
      <p className="text-xs">
        Enlace de esta carga: <span className="break-all font-mono">{link}</span>
      </p>
    </section>
  );
}

/**
 * NEW-TEMPLATE-PRICE — the variants whose price in the file is not their price, as the SERVER compared them. The
 * price is never applied by itself: who administers products may tick a proposal, and registering asks to confirm.
 */
function PriceReview({ review, canUpdate, chosen, disabled, onToggle }: {
  review: UnitImportPriceReview[];
  canUpdate: boolean;
  chosen: number[];
  disabled: boolean;
  onToggle: (variantId: number, on: boolean) => void;
}) {
  return (
    <section className="mb-4 space-y-3" aria-labelledby="unit-import-prices">
      <h3 id="unit-import-prices" className="text-sm font-semibold text-foreground">Precios de venta del archivo</h3>
      <p role="note" aria-label="Precio de las variantes" className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
        Un equipo nuevo se vende al precio de su variante. El precio del archivo no se aplica solo
        {canUpdate
          ? ": marca las variantes cuyo precio quieres actualizar; al registrar se te pedirá confirmarlo."
          : ". Sólo quien administra productos puede cambiar el precio de una variante."}
      </p>
      <TableWrap>
        <caption className="sr-only">Precios de venta del archivo</caption>
        <thead>
          <tr className="border-b border-bd-border">
            <Th>Variante</Th><Th>Precio actual</Th><Th>Precio del archivo</Th><Th>Diferencia</Th>
            <Th>Entran</Th><Th>Ya en stock (sucursales del archivo)</Th><Th>En la tienda</Th><Th>Propuesta</Th>
          </tr>
        </thead>
        <tbody>
          {review.map((entry) => (
            <tr key={entry.variant_id} className="border-b border-bd-border align-top">
              <Td>{entry.name}</Td>
              <Td>{entry.has_price ? soles(entry.current_price) : "Sin precio"}</Td>
              <Td>{entry.file_prices.map(soles).join(" · ")}</Td>
              <Td>{entry.difference !== null && entry.has_price ? signed(entry.difference) : "—"}</Td>
              <Td>{entry.units_in_file}</Td>
              <Td>{entry.units_in_stock}</Td>
              <Td>{shopState(entry)}</Td>
              <Td>
                {entry.conflict || entry.proposed_price === null ? (
                  <span className="text-danger">
                    El archivo trae {entry.file_prices.length} precios para esta variante: no se propone ninguno.
                  </span>
                ) : canUpdate ? (
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={chosen.includes(entry.variant_id)}
                      disabled={disabled}
                      onChange={(e) => onToggle(entry.variant_id, e.target.checked)}
                    />
                    Actualizar el precio de la variante a {soles(entry.proposed_price)}
                  </label>
                ) : (
                  <span className="text-muted">
                    Propuesta: {soles(entry.proposed_price)}. Sólo quien administra productos puede aplicarla.
                  </span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </section>
  );
}

/**
 * INV-LOAD-02 — annul an applied load WHOLE. The server voids every device the load created, or none: while one of
 * them has done anything since it entered, the answer names it and nothing changes. The reason is mandatory.
 */
export function AnnulLoad({ job, onAnnulled }: { job: UnitImportJob; onAnnulled: (job: UnitImportJob) => void }) {
  const headingId = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocking, setBlocking] = useState<UnitImportBlocking[]>([]);
  // INV-LOAD-02 review — what the annulment would do, asked to the server before confirming.
  const [preview, setPreview] = useState<UnitImportVoidPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const voided = job.summary.voided;

  if (voided) {
    return (
      <p role="status" className="text-sm text-foreground">
        Esta carga se anuló: {plural(voided.units, "equipo salió", "equipos salieron")} del stock con una línea de
        corrección en el Kardex. Motivo: {voided.reason}
      </p>
    );
  }

  async function start() {
    setOpen(true);
    setWorking(true);
    setError(null);
    try {
      const next = await fetchUnitImportVoidPreview(job.id);
      setPreview(next);
      setBlocking(next.blocking);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "No se pudo preparar la anulación.");
    } finally {
      setWorking(false);
    }
  }

  async function annul() {
    setWorking(true);
    setError(null);
    setBlocking([]);
    try {
      onAnnulled(await voidUnitImport(job.id, reason.trim(), confirmed));
      setOpen(false);
    } catch (e: unknown) {
      if (e instanceof UnitImportVoidError) setBlocking(e.blocking);
      setError(e instanceof Error ? e.message : "No se pudo anular la carga.");
    } finally {
      setWorking(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className={internalButtonClass} onClick={start}>
        Anular esta carga
      </button>
    );
  }
  const prepared = preview ? preview.inspected + preview.published + preview.with_photos > 0 : false;
  const count = preview?.units ?? job.summary.applied?.units ?? 0;
  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded-lg border border-bd-border px-4 py-3">
      <h3 id={headingId} className="text-sm font-semibold text-foreground">Anular esta carga</h3>
      <p className="text-sm text-muted">
        Cada equipo que entró con este archivo sale del stock con una línea de corrección en el Kardex (nada se
        borra). Si alguno ya se vendió, apartó, trasladó o cambió de variante, no se anula ninguno.
      </p>
      {prepared && preview ? (
        <div role="note" aria-label="Trabajo hecho sobre estos equipos" className="space-y-2 rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-warning">
          <p>
            Ya se trabajó sobre estos equipos: {preview.inspected} inspeccionados, {preview.published} publicados y{" "}
            {preview.with_photos} con fotos. Al anular la carga salen del stock y de la web.
          </p>
          <label className="flex items-center gap-2 font-semibold">
            <input type="checkbox" checked={confirmed} disabled={working}
              onChange={(e) => setConfirmed(e.target.checked)} />
            Entiendo que se anulan también los equipos inspeccionados, publicados o con fotos
          </label>
        </div>
      ) : null}
      <label className="block text-xs font-semibold text-muted">
        Motivo de la anulación
        <textarea
          className={`mt-1 ${internalInputClass}`}
          value={reason}
          maxLength={preview?.reason_max ?? 240}
          rows={2}
          disabled={working}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {blocking.length ? (
        <ul aria-label="Equipos que impiden anular" className="list-disc space-y-0.5 pl-5 text-sm text-danger">
          {blocking.map((unit) => (
            <li key={unit.unit_id}>
              Equipo {unit.label_code || `#${unit.unit_id}`}: {unit.reason}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          className={internalPrimaryButtonClass}
          disabled={working || !preview || !reason.trim() || blocking.length > 0 || (prepared && !confirmed)}
          onClick={annul}
        >
          Anular {plural(count, "equipo", "equipos")}
        </button>
        <button type="button" className={internalButtonClass} disabled={working} onClick={() => setOpen(false)}>
          Cancelar
        </button>
      </div>
    </section>
  );
}

export function StockUnitImport({ branches, canSeeCost = false }: {
  branches: { id: number; name: string }[];
  /** `costs.view`: without it the screen says, before anything is attached, that the «Costo» column stays empty. */
  canSeeCost?: boolean;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [branch, setBranch] = useState("");
  const [reason, setReason] = useState("");
  const [entryKind, setEntryKind] = useState<UnitEntryKind>("purchase");
  // INV-LOAD-02 review — which devices the file loads, ALWAYS said: a renamed or pasted sheet no longer says it, and
  // a used device must never enter as new by default. «mixed» = the combined template, row by row (its «Línea»).
  const [line, setLine] = useState<UnitImportLine | "mixed" | "">("");
  const [job, setJob] = useState<UnitImportJob | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // IMPORT-STALE-01: the apply was refused because the preview is old or the shop moved since.
  const [outdated, setOutdated] = useState<OutdatedPreview | null>(null);
  // NEW-TEMPLATE-PRICE — the price proposals ticked for THIS preview, and the confirmation step before registering.
  const [chosen, setChosen] = useState<{ jobId: number; ids: number[] }>({ jobId: 0, ids: [] });
  const [confirming, setConfirming] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // IMPORT-TERMS-APPROVAL — «?job=<id>» reopens a previewed load: to approve its prices and warranties, or to register
  // it once they are approved. Read once, from the address (no Suspense boundary for `useSearchParams`).
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("job");
    const id = raw && /^[0-9]+$/.test(raw) ? Number(raw) : null;
    if (id) {
      void run(async () => {
        const loaded = await fetchUnitImportJob(id);
        // Review of #434 — the detail answers any kind of load; only a device load is registered here.
        if ((loaded.import_type as string) !== "units") throw new Error("Esta carga no es de equipos.");
        return loaded;
      });
    }
  }, []);

  async function run(task: () => Promise<UnitImportJob>) {
    setWorking(true);
    setError(null);
    setOutdated(null);
    setConfirming(false);
    try {
      setJob(await task());
    } catch (e: unknown) {
      const refused = outdatedPreview(e);
      if (refused) {
        // Nothing was registered, and this preview can never be applied now.
        setOutdated(refused);
        setJob((current) => (current ? { ...current, is_applicable: false } : current));
        return;
      }
      setError(e instanceof Error ? e.message : "No se pudo completar la operación.");
    } finally {
      setWorking(false);
    }
  }

  function choose(next: File | null) {
    setFile(next);
    setJob(null);
    setError(null);
    setOutdated(null);
  }

  function uploadAgain() {
    choose(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  const applied = job?.status === "applied";
  const registered = job?.summary.applied?.units ?? 0;
  const rows = job?.rows ?? [];
  const unmarked = job?.summary.imei_on_unmarked_products ?? 0;
  const review = job?.summary.price_review ?? [];
  const canUpdatePrices = Boolean(job?.can_update_prices);
  const chosenIds = job && canUpdatePrices && chosen.jobId === job.id ? chosen.ids : [];
  const updates: (UnitImportPriceUpdate & { name: string; from: string; has_price: boolean })[] = review
    .filter((entry) => chosenIds.includes(entry.variant_id) && !entry.conflict && entry.proposed_price !== null)
    .map((entry) => ({
      variant_id: entry.variant_id, price: entry.proposed_price as string, confirm: true,
      name: entry.name, from: entry.current_price, has_price: entry.has_price,
    }));
  const priceChanges = job?.summary.applied?.price_updates ?? [];
  // IMPORT-TERMS-APPROVAL — registering WITH the file's prices and warranties needs `products.manage` or a valid
  // approval; without either, only «Registrar sin precios ni garantías» is offered (the server would refuse).
  const carriesTerms = Boolean(job?.carries_commercial_terms);
  const termsBlocked = carriesTerms && !canUpdatePrices && !job?.terms_approval?.valid;

  function toggle(variantId: number, on: boolean) {
    if (!job) return;
    const ids = chosen.jobId === job.id ? chosen.ids : [];
    setChosen({ jobId: job.id, ids: on ? [...ids, variantId] : ids.filter((id) => id !== variantId) });
    setConfirming(false);
  }

  function register(currentJob: UnitImportJob) {
    if (updates.length && !confirming) {
      setConfirming(true);
      return;
    }
    const confirmed = updates.map(({ variant_id, price, confirm }) => ({ variant_id, price, confirm }));
    run(() => applyUnitImport(currentJob.id, confirmed));
  }

  function approveTerms(currentJob: UnitImportJob) {
    // The answer carries no rows: the preview on screen keeps its own.
    run(async () => ({ ...currentJob, ...(await approveUnitImportTerms(currentJob.id)) }));
  }

  function registerWithoutTerms(currentJob: UnitImportJob) {
    run(() => applyUnitImport(currentJob.id, [], { withoutCommercialTerms: true }));
  }

  return (
    <div className="space-y-6">
      <Panel
        title="Cargar equipos desde Excel"
        description="Para registrar de una vez los equipos de una compra o del inventario inicial."
        action={
          <div className="flex flex-wrap gap-2">
            {/* INV-LOAD-02 — one template per line; the combined one stays for the files people already have. */}
            <a className={internalButtonClass} href={unitImportTemplateUrl("new")}>Stock de equipos nuevos</a>
            <a className={internalButtonClass} href={unitImportTemplateUrl("used")}>Stock de seminuevos</a>
            <a className={internalButtonClass} href={unitImportTemplateUrl()}>Descargar plantilla</a>
          </div>
        }
      >
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Para cargar el stock real usa «Stock de equipos nuevos» o «Stock de seminuevos»: cada una carga sólo su
            línea. «Descargar plantilla» es la plantilla combinada de siempre.
          </p>
          <ImportKindNotice kind="units" />
          {canSeeCost ? null : <CostColumnNotice column="Costo" />}

          <div className="grid gap-3 md:grid-cols-3">
            <label className="block text-xs font-semibold text-muted">
              Archivo de equipos (.xlsx)
              <input
                ref={inputRef}
                type="file"
                accept=".xlsx"
                className={`mt-1 ${internalInputClass}`}
                disabled={working}
                onChange={(e) => choose(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="block text-xs font-semibold text-muted">
              Sucursal para las filas sin sucursal
              <select
                className={`mt-1 ${internalInputClass}`}
                value={branch}
                disabled={working}
                onChange={(e) => { setBranch(e.target.value); setJob(null); }}
              >
                <option value="">Ninguna: cada fila la indica</option>
                {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </label>
            <fieldset className="md:col-span-2">
              <legend className="block text-xs font-semibold text-muted">Equipos del archivo (obligatorio)</legend>
              <div className="mt-1 flex flex-wrap gap-4 text-sm text-foreground">
                {([
                  ["new", "Nuevos"],
                  ["used", "Seminuevos"],
                  ["mixed", "Ambos: la columna «Línea» de la plantilla combinada lo dice en cada fila"],
                ] as const).map(([value, label]) => (
                  <label key={value} className="flex items-center gap-2">
                    <input type="radio" name="unit-import-line" value={value} disabled={working}
                      checked={line === value} onChange={() => { setLine(value); setJob(null); }} />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset className="md:col-span-2">
              {/* INV-OPENING-01 — what these devices ARE in the Kardex. */}
              <legend className="block text-xs font-semibold text-muted">Tipo de ingreso</legend>
              <div className="mt-1 flex flex-wrap gap-4 text-sm text-foreground">
                <label className="flex min-h-11 items-center gap-2">
                  <input type="radio" name="unit-import-entry-kind" value="purchase" disabled={working}
                    className="h-4 w-4 accent-[var(--primary)]"
                    checked={entryKind === "purchase"} onChange={() => { setEntryKind("purchase"); setJob(null); }} />
                  Compra a proveedor
                </label>
                {/* The help sits OUTSIDE the <label> and is linked by aria-describedby, so the radio's name stays short. */}
                <div className="max-w-md">
                  <label className="flex min-h-11 items-center gap-2">
                    <input type="radio" name="unit-import-entry-kind" value="opening" disabled={working}
                      className="h-4 w-4 accent-[var(--primary)]"
                      aria-describedby="unit-import-entry-opening-help"
                      checked={entryKind === "opening"} onChange={() => { setEntryKind("opening"); setJob(null); }} />
                    Inventario inicial
                  </label>
                  <p id="unit-import-entry-opening-help" className="ml-6 text-xs text-muted">
                    Equipos que ya tenías. Se pueden cargar en varias tandas mientras el artículo no tenga otros movimientos
                    en esa sucursal.
                  </p>
                </div>
                {/* STOCK-EXISTING-01 — what the shop already had, on any shelf (it closes the opening, like a purchase). */}
                <div className="max-w-md">
                  <label className="flex min-h-11 items-center gap-2">
                    <input type="radio" name="unit-import-entry-kind" value="existing" disabled={working}
                      className="h-4 w-4 accent-[var(--primary)]"
                      aria-describedby="unit-import-entry-existing-help"
                      checked={entryKind === "existing"} onChange={() => { setEntryKind("existing"); setJob(null); }} />
                    Ingreso de inventario existente
                  </label>
                  <p id="unit-import-entry-existing-help" className="ml-6 text-xs text-muted">
                    {EXISTING_STOCK_HELP}
                  </p>
                </div>
              </div>
            </fieldset>
            <label className="block text-xs font-semibold text-muted">
              Motivo para las filas sin motivo
              <input
                className={`mt-1 ${internalInputClass}`}
                value={reason}
                maxLength={300}
                placeholder="Compra F001-204, inventario inicial…"
                disabled={working}
                onChange={(e) => { setReason(e.target.value); setJob(null); }}
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className={internalPrimaryButtonClass}
              disabled={!file || !line || working || applied}
              onClick={() => file && line && run(() => previewUnitImport(file, {
                branch: branch ? Number(branch) : null, reason, entryKind, line: line === "mixed" ? undefined : line,
              }))}
            >
              Previsualizar
            </button>
            <span className="text-xs text-muted">
              {line ? "Previsualizar no registra nada." : "Elige si el archivo trae equipos nuevos o seminuevos."}
            </span>
          </div>
        </div>
      </Panel>

      {error ? (
        <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {outdated ? <OutdatedPreviewNotice outdated={outdated} onUploadAgain={uploadAgain} /> : null}

      {applied && job ? (
        <div className="space-y-3">
          <div role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">
            Se registraron {plural(registered, "equipo", "equipos")} y quedaron en el Kardex.{" "}
            <Link className="font-semibold underline" href="/admin/inventory/units">Ver los equipos</Link>
          </div>
          {job.summary.applied?.commercial_terms === "dropped" ? (
            <p role="note" aria-label="Sin precios ni garantías" className="text-sm text-foreground">
              Se registraron sin los precios ni las garantías del archivo: quedan sin precio propio y con la garantía
              de la empresa. Quien administra productos puede fijarlos en la ficha de cada equipo.
            </p>
          ) : null}
          {priceChanges.length ? (
            <p role="note" aria-label="Precios actualizados" className="text-sm text-foreground">
              Se actualizó el precio de {plural(priceChanges.length, "variante", "variantes")}:{" "}
              {priceChanges.map((change) => {
                const name = review.find((entry) => entry.variant_id === change.variant_id)?.name ?? `#${change.variant_id}`;
                return `${name} (${soles(change.old_price)} → ${soles(change.new_price)})`;
              }).join(" · ")}
            </p>
          ) : null}
          {job.options?.line === "used" ? (
            <p role="note" aria-label="Después de cargar seminuevos" className="text-sm text-muted">
              Los seminuevos entran pendientes de inspección y sin publicar. Inspecciónalos, súbeles fotos y
              publícalos desde su ficha en{" "}
              <Link className="underline" href="/admin/inventory/used">Seminuevos</Link>.
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <a className={internalButtonClass} href={unitImportReportUrl(job.id, "csv")}>Antes y después (CSV)</a>
            <a className={internalButtonClass} href={unitImportReportUrl(job.id, "xlsx")}>Antes y después (Excel)</a>
          </div>
          <AnnulLoad job={job} onAnnulled={setJob} />
        </div>
      ) : null}

      {job && !applied ? (
        <Panel
          title="Lo que pasará con cada fila"
          description={`${job.original_filename}${job.options?.line ? ` · plantilla de ${LINE_LABELS[job.options.line]}` : ""} · ${plural(job.counts.create, "equipo por registrar", "equipos por registrar")} · ${job.counts.error} con error · ${plural(job.counts.skip, "fila omitida", "filas omitidas")}`}
          action={
            job.counts.error ? (
              <a className={internalButtonClass} href={unitImportErrorsUrl(job.id)}>Descargar errores (CSV)</a>
            ) : (job.is_applicable || outdated) && job.counts.create > 0 && !termsBlocked ? (
              <button
                type="button"
                className={internalPrimaryButtonClass}
                disabled={working || Boolean(outdated)}
                onClick={() => register(job)}
              >
                Registrar {plural(job.counts.create, "equipo", "equipos")}
              </button>
            ) : null
          }
        >
          {job.counts.error ? (
            <p className="mb-3 text-sm text-danger">
              Corrige las filas con error en el archivo y vuelve a subirlo. Mientras haya una, no se
              registra ningún equipo.
            </p>
          ) : null}
          {carriesTerms && !job.counts.error && job.counts.create > 0 ? (
            <TermsApproval
              job={job}
              canApprove={canUpdatePrices}
              disabled={working || Boolean(outdated) || !job.is_applicable}
              onApprove={() => approveTerms(job)}
              onRegisterWithout={() => registerWithoutTerms(job)}
            />
          ) : null}
          {!job.counts.error && job.counts.create > 0 && job.summary.simulation ? (
            <Simulation simulation={job.summary.simulation} />
          ) : null}
          {review.length ? (
            <PriceReview
              review={review}
              canUpdate={canUpdatePrices}
              chosen={chosenIds}
              disabled={working || Boolean(job.counts.error)}
              onToggle={toggle}
            />
          ) : null}
          {confirming && updates.length ? (
            <section
              role="group"
              aria-labelledby="unit-import-confirm-prices"
              className="mb-4 space-y-3 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning"
            >
              <h3 id="unit-import-confirm-prices" className="font-semibold">
                Confirma el cambio de precio de {plural(updates.length, "variante", "variantes")}
              </h3>
              <ul className="list-disc space-y-0.5 pl-5">
                {updates.map((update) => (
                  <li key={update.variant_id}>
                    {update.name}: {update.has_price ? soles(update.from) : "sin precio"} → {soles(update.price)}
                  </li>
                ))}
              </ul>
              <p>
                Cambia el precio de la variante para todos sus equipos, en caja y en la web. Se registra con los
                equipos: entra todo o nada.
              </p>
              <div className="flex flex-wrap gap-3">
                <button type="button" className={internalPrimaryButtonClass} disabled={working}
                  onClick={() => register(job)}>
                  Confirmar: registrar y actualizar precios
                </button>
                <button type="button" className={internalButtonClass} disabled={working}
                  onClick={() => setConfirming(false)}>
                  Volver
                </button>
              </div>
            </section>
          ) : null}
          {unmarked > 0 ? (
            <p
              role="note"
              aria-label="IMEI en productos sin marca"
              className="mb-3 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning"
            >
              {plural(unmarked, "equipo trae IMEI", "equipos traen IMEI")} y su producto no está marcado
              «lleva IMEI». El IMEI se guardará, pero no se exigirá al vender. Si alguna fila es de otro
              producto, corrígela antes de registrar; cada una lleva su aviso.
            </p>
          ) : null}
          <TableWrap>
            <caption className="sr-only">Equipos del archivo</caption>
            <thead>
              <tr className="border-b border-bd-border">
                <Th>Fila</Th><Th>Producto</Th><Th>Sucursal</Th><Th>Número de serie</Th>
                <Th>IMEI</Th><Th>IMEI 2</Th><Th>Condición</Th><Th>Proveedor</Th><Th>Fecha de ingreso</Th>
                <Th>Resultado</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.sheet}-${row.row}`} className="border-b border-bd-border align-top">
                  <Td muted>{row.row}</Td>
                  <Td>{row.data.name ?? ""}</Td>
                  <Td>{row.data.branch ?? ""}</Td>
                  <Td><span className="font-mono text-xs">{row.data.serial_number ?? ""}</span></Td>
                  <Td><span className="font-mono text-xs">{row.data.imei ?? ""}</span></Td>
                  <Td><span className="font-mono text-xs">{row.data.imei2 ?? ""}</span></Td>
                  <Td>{row.data.condition ? CONDITIONS[row.data.condition] ?? row.data.condition : ""}</Td>
                  <Td>{row.data.supplier ?? ""}</Td>
                  <Td>{row.data.received_on ?? ""}</Td>
                  <Td><Outcome row={row} /></Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          {job.rows_truncated ? (
            <p className="mt-3 text-xs text-muted">
              Se muestran las primeras filas: las de error primero y las que llevan un aviso después.
              El archivo completo se registra al confirmar.
            </p>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}
