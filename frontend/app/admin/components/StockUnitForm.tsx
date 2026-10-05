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

import {
  fetchUnitProducts, receiveStockUnits, setProductTracking, StockUnitApiError,
  type NewUnit, type Option, type UnitField, type UnitProduct,
} from "@/app/lib/stock-units";

import { ErrorBox } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

type Row = { serial_number: string; imei: string; imei2: string };
const BLANK: Row = { serial_number: "", imei: "", imei2: "" };
type Problem = { line: number | null; field: UnitField | null; message: string };
type Done = { serial: string; imei: string };

const plural = (n: number) => (n === 1 ? "1 equipo" : `${n} equipos`);

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
  branch, branches, conditions, canChangeTracking, onSaved, onClose,
}: {
  /** The branch in view; "all" when the screen shows every branch the caller reaches. */
  branch: number | "all" | undefined;
  branches: { id: number; name: string }[];
  conditions: Option[];
  /** Whether this person may switch a product to tracking by serial. The server checks again. */
  canChangeTracking: boolean;
  /** A device (or several) entered the inventory: the caller refreshes its table. */
  onSaved: (message: string) => void;
  onClose: () => void;
}) {
  const [products, setProducts] = useState<UnitProduct[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [branchId, setBranchId] = useState(typeof branch === "number" ? String(branch) : "");
  const [productId, setProductId] = useState("");
  const [condition, setCondition] = useState(conditions[0]?.value ?? "new");
  const [cost, setCost] = useState("");
  const [price, setPrice] = useState("");
  const [reason, setReason] = useState("");
  const [rows, setRows] = useState<Row[]>([{ ...BLANK }]);
  const [several, setSeveral] = useState(false);
  const [cellular, setCellular] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [working, setWorking] = useState(false);
  const [done, setDone] = useState<Done[]>([]);
  const serialInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchUnitProducts().then(
      (loaded) => { if (!cancelled) setProducts(loaded.results); },
      (err) => { if (!cancelled) setLoadError(err instanceof Error ? err.message : "No se pudieron cargar los productos."); },
    );
    return () => { cancelled = true; };
  }, []);

  const product = products?.find((p) => String(p.id) === productId) ?? null;
  const tracked = Boolean(product?.is_serialized);
  const needsImei = Boolean(product?.requires_imei);
  const filled = rows.filter((row) => row.serial_number.trim());
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
      setProducts((current) => (current ?? []).map((p) => (
        p.id === product.id
          ? { ...p, is_serialized: updated.is_serialized, requires_imei: updated.requires_imei }
          : p
      )));
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
      cost: cost.trim(),
      price_override: price.trim(),
    }));
    try {
      await receiveStockUnits({ product_id: product.id, branch: Number(branchId), reason: reason.trim(), units });
      const saved = units.map((unit) => ({ serial: unit.serial_number, imei: unit.imei }));
      onSaved(
        saved.length === 1
          ? `Equipo registrado: ${product.name} · Serie ${saved[0].serial}${saved[0].imei ? ` · IMEI ${saved[0].imei}` : ""}.`
          : `${plural(saved.length)} registrados: ${product.name}.`,
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
  const general = problem && (problem.field === null || problem.field === "condition") ? problem.message : null;

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
        <div>
          <label htmlFor="unit-branch" className="block text-xs font-semibold text-foreground">Sucursal</label>
          <select id="unit-branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
            <option value="">Elige una sucursal</option>
            {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="unit-product" className="block text-xs font-semibold text-foreground">Producto / modelo</label>
          <select
            id="unit-product"
            value={productId}
            onChange={(e) => { setProductId(e.target.value); setProblem(null); setCellular(false); }}
            className={`mt-1.5 ${internalInputClass}`}
          >
            <option value="">{products === null ? "Cargando…" : "Elige un producto"}</option>
            {(products ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.is_serialized ? p.name : `${p.name} · sin seguimiento por serie`}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="unit-condition" className="block text-xs font-semibold text-foreground">Condición</label>
          <select id="unit-condition" value={condition} onChange={(e) => setCondition(e.target.value)} className={`mt-1.5 ${internalInputClass}`}>
            {conditions.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
      </div>

      {product && !tracked ? (
        <div role="note" className="space-y-3 rounded-lg border border-warning-border bg-warning-surface p-4 text-sm text-foreground">
          <p>
            «{product.name}» se controla por cantidad, no por número de serie: por eso todavía no
            se le pueden registrar equipos.
          </p>
          {product.can_change_tracking && canChangeTracking ? (
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
          ) : product.can_change_tracking ? (
            <p className="text-xs text-muted">
              Su stock está en cero: quien administra el inventario puede activarle el seguimiento
              por serie.
            </p>
          ) : (
            <p className="text-xs text-muted">
              Tiene {product.stock === 1 ? "1 unidad" : `${product.stock} unidades`} en stock. Para
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
              label="Número de serie *" required mono value={row.serial_number}
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

      <div className="grid gap-4 md:grid-cols-3">
        <Input label="Costo (opcional)" inputMode="decimal" value={cost} onChange={setCost} error={errorFor("cost")} />
        <Input
          label="Precio de este equipo (opcional)" inputMode="decimal" value={price} onChange={setPrice}
          error={errorFor("price_override")}
          placeholder={product ? `Catálogo: ${product.price}` : undefined}
        />
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
                #{index + 1} · Serie {item.serial}{item.imei ? ` · IMEI ${item.imei}` : ""}
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
