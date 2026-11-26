"use client";

/**
 * Inventario › Equipos › Registrar equipo.
 *
 *     CADA EQUIPO FÍSICO ES UNA UNIDAD.
 *
 * El formulario pide lo que identifica a UN equipo —su número de serie, su
 * IMEI— y lo pide a la vista desde el principio. No hay una cantidad que
 * sustituya a esas identidades: dos iPhone iguales son dos registros, cada uno
 * con su serie y su IMEI.
 *
 * Los campos no se esconden a la espera de que alguien elija «el producto
 * correcto». Si el producto elegido no lleva serie, el formulario lo DICE, y si
 * su stock está en cero ofrece activarlo aquí mismo.
 *
 * Qué vale como serie o IMEI, y si un equipo ya existe, lo decide el servidor;
 * su respuesta se muestra junto al campo que señala.
 */

import { useEffect, useId, useRef, useState } from "react";

import { fetchAdminProductDetail } from "@/app/lib/admin";
import {
  receiveStockUnits, setProductTracking, StockUnitApiError,
  type NewUnit, type Option, type UnitEntryKind, type UnitField,
} from "@/app/lib/stock-units";

import { fetchSuppliers, type SupplierRow } from "../lib/internal-api";
import { excludedCondition, lineCondition } from "../lib/inventory-receipts";
import { preferredBranch, rememberBranch } from "../lib/last-branch";
import { ErrorBox } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { extractLabelBatch, type LabelBatchInfo } from "./NextStepBar";
import { ProductPicker, toPickerProduct, type PickerProduct } from "./ProductPicker";

type Row = { serial_number: string; imei: string; imei2: string };
const BLANK: Row = { serial_number: "", imei: "", imei2: "" };
type Problem = { line: number | null; field: UnitField | null; message: string };
type Done = { serial: string; imei: string };

/** What the screen needs to offer «Siguiente paso» after a save (INV-UX-A1). */
export type UnitSaved = {
  product: { id: number; name: string; slug: string };
  published: boolean;
  labelBatch: LabelBatchInfo | null;
};

const plural = (n: number) => (n === 1 ? "1 equipo" : `${n} equipos`);

/**
 * INV-LOAD-01 — the entry types this form offers: the stock the shop already had, or a purchase (the default).
 * STOCK-EXISTING-01 — and the devices the shop already had once the product has other movements in the branch.
 */
const ENTRY_TYPES: { value: UnitEntryKind; label: string }[] = [
  { value: "opening", label: "Inventario inicial" },
  { value: "purchase", label: "Compra" },
  { value: "existing", label: "Ingreso de inventario existente" },
];

/** The supplier pages read for the picker (25 each): past it the form says the list is partial. */
const SUPPLIER_PAGES = 4;

/**
 * INV-LOAD-01 review — «today» as the SERVER counts it (`TIME_ZONE = America/Lima`): a browser ahead of Lima must not
 * offer a day the server will refuse as future. AAAA-MM-DD; the server checks again.
 */
const SERVER_TIME_ZONE = "America/Lima";

function todayIso(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: SERVER_TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

/** An input with its label and, when the server points at it, its error. */
function Input({
  label, value, onChange, error, required, mono, placeholder, inputMode, inputRef,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  required?: boolean;
  mono?: boolean;
  placeholder?: string;
  inputMode?: "numeric" | "decimal" | "text";
  inputRef?: React.Ref<HTMLInputElement>;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-semibold text-foreground">{label}</label>
      <input
        id={id}
        ref={inputRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        placeholder={placeholder}
        inputMode={inputMode}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`mt-1.5 ${mono ? "font-mono" : ""} ${internalInputClass} ${error ? "border-danger" : ""}`}
      />
      {error ? <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
    </div>
  );
}

export function StockUnitForm({
  branch, branches, conditions, canChangeTracking, canSeeCost = false, canSeeSuppliers = false, initialProductId = null,
  onSaved, onClose,
}: {
  /** The branch in view; "all" when the screen shows every branch the caller reaches. */
  branch: number | "all" | undefined;
  branches: { id: number; name: string }[];
  conditions: Option[];
  /** Whether this person may switch a product to tracking by serial. The server checks again. */
  canChangeTracking: boolean;
  /**
   * COST-WRITE-AUTHORITY (owner's decision C) — `costs.view`. Without it the form neither asks for nor sends a
   * cost: the devices enter without one («costo pendiente»), and the server refuses one anyway (400). Off unless
   * the screen says so.
   */
  canSeeCost?: boolean;
  /**
   * INV-LOAD-01 — `products.view`: the supplier list is offered (the same rule as «Recibir mercadería»). Without it the
   * form neither loads nor sends a supplier.
   */
  canSeeSuppliers?: boolean;
  /** `?product=<id>` of «Recibir mercadería»: the product the form opens with. */
  initialProductId?: number | null;
  /** A device (or several) entered the inventory: the caller refreshes its table. */
  onSaved: (message: string, saved?: UnitSaved) => void;
  onClose: () => void;
}) {
  const [product, setProduct] = useState<PickerProduct | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [branchId, setBranchId] = useState(() => {
    const chosen = typeof branch === "number" ? branch : preferredBranch(branches);
    return chosen === null ? "" : String(chosen);
  });
  // INV-INTAKE-GUARD-01: the state is offered by the variant's line (a used variant starts «Usado» and never offers
  // «Nuevo»; a new model variant never offers «Usado»). What the person picked stays while the product allows it.
  const [chosenCondition, setCondition] = useState<string | null>(null);
  const [cost, setCost] = useState("");
  const [reason, setReason] = useState("");
  // INV-LOAD-01 — what the entry IS, who sold it and when it came in. Purchase and today unless the person says so.
  const [entryKind, setEntryKind] = useState<UnitEntryKind>("purchase");
  const [supplier, setSupplier] = useState("");
  const [receivedOn, setReceivedOn] = useState("");
  const [suppliers, setSuppliers] = useState<SupplierRow[]>([]);
  const [supplierNote, setSupplierNote] = useState("");
  const [rows, setRows] = useState<Row[]>([{ ...BLANK }]);
  const [several, setSeveral] = useState(false);
  const [cellular, setCellular] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [working, setWorking] = useState(false);
  const [done, setDone] = useState<Done[]>([]);
  const serialInput = useRef<HTMLInputElement>(null);
  // STOCK-UX-WALK: the picker swaps its search box for the chosen product, and focus fell to <body>. A product
  // tracked by serial sends the cursor to its first identifier, ready for the reader.
  const [focusSerial, setFocusSerial] = useState(0);
  useEffect(() => { if (focusSerial > 0) serialInput.current?.focus(); }, [focusSerial]);

  // `?product=`: load that product once and put it in the picker.
  useEffect(() => {
    if (initialProductId === null) return;
    let cancelled = false;
    fetchAdminProductDetail(initialProductId).then(
      (loaded) => { if (!cancelled) { setProduct(toPickerProduct(loaded)); setLoadError(null); } },
      (err) => { if (!cancelled) setLoadError(err instanceof Error && err.message ? err.message : "No se pudo cargar el producto del enlace."); },
    );
    return () => { cancelled = true; };
  }, [initialProductId]);

  // Supplier list: optional help, never a reason to stop (as in «Recibir mercadería»).
  useEffect(() => {
    if (!canSeeSuppliers) return;
    let cancelled = false;
    void (async () => {
      try {
        const all: SupplierRow[] = [];
        let total = 0;
        for (let page = 1; page <= SUPPLIER_PAGES; page += 1) {
          const answer = await fetchSuppliers({ active: "true", page });
          if (cancelled) return;
          all.push(...answer.results);
          total = answer.count;
          if (answer.results.length === 0 || all.length >= total) break;
        }
        setSuppliers(all);
        if (all.length < total) {
          setSupplierNote(`Se muestran los primeros ${all.length} de ${total} proveedores.`);
        }
      } catch {
        if (!cancelled) setSupplierNote("No se pudo cargar la lista de proveedores. Puedes seguir sin elegir uno.");
      }
    })();
    return () => { cancelled = true; };
  }, [canSeeSuppliers]);

  // The server's own answer when the API gives it; else the rule it applies
  // (only a product with no stock), which the server checks again anyway.
  const canSwitchTracking = product !== null && (product.can_change_tracking ?? product.inventory <= 0);
  const excluded = product ? excludedCondition(product) : null;
  const allowedConditions = conditions.filter((c) => c.value !== excluded);
  const fallbackCondition = product ? lineCondition(product) : (conditions[0]?.value ?? "new");
  const condition = chosenCondition !== null && allowedConditions.some((c) => c.value === chosenCondition)
    ? chosenCondition
    : (allowedConditions.some((c) => c.value === fallbackCondition) ? fallbackCondition : (allowedConditions[0]?.value ?? "new"));
  const tracked = Boolean(product?.is_serialized);
  const needsImei = Boolean(product?.requires_imei);
  // SERIAL-OPTIONAL-01: a row counts with its serial, or with its IMEI when the product carries one.
  const filled = rows.filter((row) => row.serial_number.trim() || (needsImei && row.imei.trim()));
  const ready = tracked && Boolean(branchId) && Boolean(reason.trim()) && filled.length > 0;

  const setRow = (index: number, patch: Partial<Row>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  /** The server's message for one input — of one row, when it named a row. */
  const errorFor = (field: UnitField, index = 0) =>
    problem && problem.field === field && (problem.line === null || problem.line === index + 1)
      ? problem.message : null;

  async function enableTracking() {
    if (!product) return;
    setWorking(true);
    setProblem(null);
    try {
      const updated = await setProductTracking(product.id, { is_serialized: true, requires_imei: cellular });
      setProduct((current) => (
        current && current.id === product.id
          ? { ...current, is_serialized: updated.is_serialized, requires_imei: updated.requires_imei }
          : current
      ));
    } catch (err) {
      setProblem({ line: null, field: null, message: err instanceof Error ? err.message : "No se pudo activar." });
    } finally {
      setWorking(false);
    }
  }

  async function save(another: boolean) {
    if (!product || !ready) return;
    setWorking(true);
    setProblem(null);
    const units: NewUnit[] = filled.map((row) => ({
      serial_number: row.serial_number.trim(),
      imei: row.imei.trim(),
      imei2: row.imei2.trim(),
      condition,
      ...(canSeeCost ? { cost: cost.trim() } : {}),
    }));
    try {
      const response = await receiveStockUnits({
        product_id: product.id, branch: Number(branchId), reason: reason.trim(), units,
        // INV-LOAD-01 — only what the person chose: the server's defaults (purchase, no supplier, today) stand otherwise.
        ...(entryKind !== "purchase" ? { entry_kind: entryKind } : {}),
        ...(canSeeSuppliers && supplier ? { supplier: Number(supplier) } : {}),
        ...(receivedOn ? { received_on: receivedOn } : {}),
      });
      rememberBranch(Number(branchId));
      const saved = units.map((unit) => ({ serial: unit.serial_number, imei: unit.imei }));
      onSaved(
        saved.length === 1
          ? `Equipo registrado: ${product.name}${saved[0].serial ? ` · Serie ${saved[0].serial}` : ""}${saved[0].imei ? ` · IMEI ${saved[0].imei}` : ""}.`
          : `${plural(saved.length)} registrados: ${product.name}.`,
        {
          product: { id: product.id, name: product.name, slug: product.slug },
          published: product.is_active !== false && product.is_published_online === true,
          labelBatch: extractLabelBatch(response),
        },
      );
      if (another) {
        // The model, the branch and the document stay; what belongs to ONE
        // device is emptied for the next one.
        setDone((current) => [...current, ...saved]);
        setRows([{ ...BLANK }]);
        setSeveral(false);
        serialInput.current?.focus();
      } else {
        onClose();
      }
    } catch (err) {
      // What was typed stays: one wrong digit must not cost the whole form.
      const failed = err instanceof StockUnitApiError ? err : null;
      setProblem({
        line: failed?.line ?? null,
        field: failed?.field ?? null,
        message: err instanceof Error ? err.message : "No se pudo registrar el equipo.",
      });
    } finally {
      setWorking(false);
    }
  }

  const imeiLabel = !product || needsImei ? "IMEI *" : "IMEI (opcional para este producto)";
  // A refusal on a field the form does not show (the cost, for who cannot set one) is said at the top.
  const general = problem && (problem.field === null || problem.field === "condition" || problem.field === "notes"
    || (problem.field === "cost" && !canSeeCost) || (problem.field === "supplier" && !canSeeSuppliers))
    ? problem.message : null;

  return (
    <form
      className="space-y-5"
      onSubmit={(event) => { event.preventDefault(); void save(false); }}
      noValidate
    >
      <p className="text-xs text-muted">
        Cada equipo físico se registra por separado, con su propio número de serie e IMEI. Dos
        equipos del mismo modelo son dos registros.
      </p>

      {loadError ? <ErrorBox message={loadError} /> : null}

      <div className="grid gap-4 md:grid-cols-3">
        <div className="md:col-span-3">
          <ProductPicker
            label="Producto / modelo"
            inputId="unit-product"
            value={product}
            onChange={(next) => {
              setProduct(next); setProblem(null); setCellular(false);
              if (next?.is_serialized) setFocusSerial((n) => n + 1);
            }}
            disabled={working}
          />
        </div>
        <div>
          <label htmlFor="unit-branch" className="block text-xs font-semibold text-foreground">Sucursal</label>
          <select id="unit-branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Elige una sucursal</option>
            {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="unit-condition" className="block text-xs font-semibold text-foreground">Condición</label>
          <select id="unit-condition" value={condition} onChange={(e) => setCondition(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
            {allowedConditions.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
      </div>

      {product && !tracked ? (
        <div role="note" className="space-y-3 rounded-lg border border-warning-border bg-warning-surface p-4 text-sm text-foreground">
          <p>
            «{product.name}» se controla por cantidad, no por número de serie: por eso todavía no
            se le pueden registrar equipos.
          </p>
          {canSwitchTracking && canChangeTracking ? (
            <>
              <p className="text-xs text-muted">
                Su stock está en cero, así que puedes activar el seguimiento por serie. Desde
                entonces su stock será la cantidad de equipos registrados y ya no se ajustará por
                cantidad.
              </p>
              <label className="flex items-center gap-2 text-xs text-foreground">
                <input type="checkbox" checked={cellular} onChange={(e) => setCellular(e.target.checked)} className="h-4 w-4 accent-[var(--primary)]" />
                Es un equipo con línea celular (lleva IMEI)
              </label>
              <button type="button" className={internalPrimaryButtonClass} disabled={working} onClick={() => void enableTracking()}>
                Activar seguimiento por serie
              </button>
            </>
          ) : canSwitchTracking ? (
            <p className="text-xs text-muted">
              Su stock está en cero: quien administra el inventario puede activarle el seguimiento
              por serie.
            </p>
          ) : (
            <p className="text-xs text-muted">
              Tiene {product.inventory === 1 ? "1 unidad" : `${product.inventory} unidades`} en stock. Para
              llevarlo por serie su stock tiene que estar en cero: no se puede saber a qué equipo
              corresponde cada unidad ya contada.
            </p>
          )}
        </div>
      ) : null}

      {rows.map((row, index) => {
        const fields = (
          <div className="grid gap-4 md:grid-cols-3">
            <Input
              // SERIAL-OPTIONAL-01: with an IMEI (the product carries one) the serial is optional.
              label={needsImei ? "Número de serie (opcional con IMEI)" : "Número de serie *"} required={!needsImei} mono value={row.serial_number}
              onChange={(value) => setRow(index, { serial_number: value })}
              error={errorFor("serial_number", index)}
              inputRef={index === 0 ? serialInput : undefined}
            />
            <Input
              label={imeiLabel} required={!product || needsImei} mono inputMode="numeric" value={row.imei}
              onChange={(value) => setRow(index, { imei: value })}
              error={errorFor("imei", index)}
              placeholder="15 dígitos"
            />
            <Input
              label="IMEI 2 (opcional)" mono inputMode="numeric" value={row.imei2}
              onChange={(value) => setRow(index, { imei2: value })}
              error={errorFor("imei2", index)}
              placeholder="Sólo si el equipo tiene dos"
            />
          </div>
        );
        return several ? (
          <fieldset key={index} className="rounded-lg border border-bd-border p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-widest text-muted">Equipo {index + 1}</legend>
            {fields}
          </fieldset>
        ) : <div key={index}>{fields}</div>;
      })}

      {/* INV-LOAD-01 — what these devices ARE in the Kardex, who sold them and when they came in. */}
      <div className="grid gap-4 md:grid-cols-3">
        <fieldset className="md:col-span-3">
          <legend className="block text-xs font-semibold text-foreground">Tipo de ingreso</legend>
          <div className="mt-1.5 flex flex-wrap gap-4 text-sm text-foreground">
            {ENTRY_TYPES.map((kind) => (
              <label key={kind.value} className="flex items-center gap-2">
                <input
                  type="radio" name="unit-entry-kind" value={kind.value} disabled={working}
                  checked={entryKind === kind.value} onChange={() => setEntryKind(kind.value)}
                  className="h-4 w-4 accent-[var(--primary)]"
                />
                {kind.label}
              </label>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted">
            {entryKind === "opening"
              ? "Equipos que ya tenías. Se pueden registrar en varias tandas mientras el producto no tenga otros movimientos (ventas, compras, traslados) en esta sucursal."
              : entryKind === "existing"
                ? "Equipos que ya tenías; no es una compra. Para un producto que ya tiene otros movimientos en esta sucursal."
                : "Equipos que llegan comprados. Quedan como compra en el Kardex."}
          </p>
          {errorFor("entry_kind") ? <p role="alert" className="mt-1 text-xs text-danger">{errorFor("entry_kind")}</p> : null}
        </fieldset>
        {canSeeSuppliers ? (
          <div>
            <label htmlFor="unit-supplier" className="block text-xs font-semibold text-foreground">Proveedor (opcional)</label>
            <select
              id="unit-supplier" value={supplier} disabled={working}
              onChange={(e) => setSupplier(e.target.value)} className={`mt-1.5 ${internalInputClass}`}
            >
              <option value="">Sin proveedor</option>
              {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            {supplierNote ? <p role="status" className="mt-1 text-xs text-warning">{supplierNote}</p> : null}
          </div>
        ) : null}
        <div>
          <label htmlFor="unit-received-on" className="block text-xs font-semibold text-foreground">Fecha de ingreso (opcional)</label>
          <input
            id="unit-received-on" type="date" value={receivedOn} max={todayIso()} min="2000-01-01" disabled={working}
            onChange={(e) => setReceivedOn(e.target.value)}
            aria-invalid={errorFor("received_on") ? true : undefined}
            aria-describedby="unit-received-on-help"
            className={`mt-1.5 ${internalInputClass} ${errorFor("received_on") ? "border-danger" : ""}`}
          />
          <p id="unit-received-on-help" className="mt-1 text-xs text-muted">Vacía = hoy.</p>
          {errorFor("received_on") ? <p role="alert" className="mt-1 text-xs text-danger">{errorFor("received_on")}</p> : null}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {canSeeCost ? (
          <Input label="Costo (opcional)" inputMode="decimal" value={cost} onChange={setCost} error={errorFor("cost")} />
        ) : null}
        <Input
          label="Motivo o documento de ingreso *" required value={reason} onChange={setReason}
          error={errorFor("reason")} placeholder="Ej.: factura F001-234 del proveedor"
        />
      </div>

      {general ? <ErrorBox message={general} /> : null}

      {done.length > 0 ? (
        <div className="rounded-lg border border-success-border p-3">
          <p className="text-xs font-semibold text-success">Registrados en esta sesión</p>
          <ol aria-label="Equipos registrados ahora" className="mt-2 space-y-1 text-xs text-foreground">
            {done.map((item, index) => (
              <li key={`${item.serial}-${index}`} className="font-mono">
                #{index + 1}{item.serial ? ` · Serie ${item.serial}` : ""}{item.imei ? ` · IMEI ${item.imei}` : ""}
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {several ? (
          <>
            <button type="button" className={internalButtonClass} disabled={working} onClick={() => setRows((current) => [...current, { ...BLANK }])}>
              Añadir otro equipo
            </button>
            <button type="button" className={internalPrimaryButtonClass} disabled={working || !ready} onClick={() => void save(false)}>
              Guardar {plural(filled.length)}
            </button>
          </>
        ) : (
          <>
            <button type="submit" className={internalPrimaryButtonClass} disabled={working || !ready}>
              Guardar equipo
            </button>
            <button type="button" className={internalButtonClass} disabled={working || !ready} onClick={() => void save(true)}>
              Guardar y añadir otro
            </button>
            <button type="button" className={internalButtonClass} disabled={working} onClick={() => setSeveral(true)}>
              Registrar varios equipos a la vez
            </button>
          </>
        )}
        <button type="button" className={internalButtonClass} disabled={working} onClick={onClose}>
          {done.length > 0 ? "Terminar" : "Cancelar"}
        </button>
      </div>
    </form>
  );
}
