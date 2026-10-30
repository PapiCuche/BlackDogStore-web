"use client";

/**
 * INV-OPS-01 — paso «Artículos» del asistente «Recibir mercadería».
 *
 * Se busca el producto con el buscador de siempre (`ProductPicker`: nombre,
 * SKU o código escaneado + Enter). Un producto con serie abre su editor de
 * equipos (una fila por equipo físico); uno por cantidad abre un contador. Cada
 * línea se puede corregir o quitar mientras se arma la lista.
 *
 * Aquí sólo se valida lo evidente (campos vacíos, repetidos). Qué es un IMEI
 * válido o si un equipo ya existe lo dice el servidor en el paso siguiente.
 * Nada de lo escrito sale de la memoria de la página.
 */

import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import {
  blankUnit, isUsedLine, newLine, UNIT_CONDITIONS, unitsInUse,
  type DraftLine, type DraftUnit, type LineProblems, type ReceiptProduct,
} from "../lib/inventory-receipts";
import type { CosmeticGradeRow } from "../lib/internal-api";
import { internalButtonClass, internalInputClass } from "./internal-ui";
import { ProductPicker, type PickerProduct } from "./ProductPicker";

export const toReceiptProduct = (p: PickerProduct): ReceiptProduct => ({
  id: p.id,
  name: p.name,
  sku: p.primary_code || undefined,
  is_serialized: Boolean(p.is_serialized),
  requires_imei: Boolean(p.requires_imei),
  condition_line: p.condition_line,
});

const BADGE = "rounded-full border border-bd-border px-2 py-0.5 text-[11px] font-medium text-muted";
const BIG_BUTTON = `${internalButtonClass} min-h-11 min-w-11`;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function Field({
  label, value, onChange, error, mono, inputMode, placeholder, unit, field, onEnter, onBlur, maxLength,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  mono?: boolean;
  inputMode?: "numeric" | "decimal" | "text";
  placeholder?: string;
  unit?: string;
  field?: string;
  onEnter?: () => void;
  onBlur?: () => void;
  maxLength?: number;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-semibold text-foreground">{label}</label>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        onKeyDown={onEnter ? (e) => { if (e.key === "Enter") { e.preventDefault(); onEnter(); } } : undefined}
        inputMode={inputMode}
        placeholder={placeholder}
        maxLength={maxLength}
        spellCheck={false}
        autoComplete="off"
        data-unit={unit}
        data-field={field}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`mt-1.5 min-h-11 ${mono ? "font-mono" : ""} ${internalInputClass} ${error ? "border-danger" : ""}`}
      />
      {error ? <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
    </div>
  );
}

function QuantityEditor({
  line, problems, canCosts, onChange, onEnter,
}: {
  line: DraftLine; problems?: LineProblems; canCosts: boolean;
  onChange: (patch: Partial<DraftLine>) => void; onEnter: () => void;
}) {
  const current = /^\d+$/.test(line.quantity.trim()) ? Number(line.quantity.trim()) : 0;
  const set = (n: number) => onChange({ quantity: String(Math.max(1, n)) });
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div>
        <span className="block text-xs font-semibold text-foreground" id={`${line.key}-qty-label`}>Cantidad que entra</span>
        <div className="mt-1.5 flex items-stretch gap-2" role="group" aria-labelledby={`${line.key}-qty-label`}>
          <button type="button" className={BIG_BUTTON} aria-label="Una unidad menos" disabled={current <= 1} onClick={() => set(current - 1)}>−</button>
          <input
            aria-label="Cantidad"
            value={line.quantity}
            inputMode="numeric"
            autoComplete="off"
            data-field="quantity"
            data-line={line.key}
            aria-invalid={problems?.quantity ? true : undefined}
            onChange={(e) => onChange({ quantity: e.target.value.replace(/[^\d]/g, "") })}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onEnter(); } }}
            className={`min-h-11 w-24 text-center text-lg font-semibold tabular-nums ${internalInputClass} ${problems?.quantity ? "border-danger" : ""}`}
          />
          <button type="button" className={BIG_BUTTON} aria-label="Una unidad más" onClick={() => set(current + 1)}>+</button>
        </div>
        {problems?.quantity ? <p role="alert" className="mt-1 text-xs text-danger">{problems.quantity}</p> : null}
      </div>
      {canCosts ? (
        <Field
          label="Costo por unidad (opcional)" value={line.unitCost} inputMode="decimal" placeholder="Ej.: 12.50"
          onChange={(unitCost) => onChange({ unitCost })} error={problems?.unitCost}
        />
      ) : null}
    </div>
  );
}

function UnitsEditor({
  line, problems, canCosts, canPrice, onChange, grades,
}: {
  line: DraftLine; problems?: LineProblems; canCosts: boolean; canPrice: boolean; onChange: (patch: Partial<DraftLine>) => void;
  grades: CosmeticGradeRow[];
}) {
  const pending = useRef<{ unit: string; field: string } | null>(null);
  const root = useRef<HTMLDivElement>(null);

  // After adding a row the focus goes to its serial number, ready for the next scan.
  useEffect(() => {
    const want = pending.current;
    if (!want) return;
    pending.current = null;
    root.current?.querySelector<HTMLInputElement>(`[data-unit="${want.unit}"][data-field="${want.field}"]`)?.focus();
  });

  const setUnit = (key: string, patch: Partial<DraftUnit>) =>
    onChange({ units: line.units.map((u) => (u.key === key ? { ...u, ...patch } : u)) });

  const addAfter = (key: string) => {
    const index = line.units.findIndex((u) => u.key === key);
    const next = line.units[index + 1];
    if (next) { root.current?.querySelector<HTMLInputElement>(`[data-unit="${next.key}"][data-field="serial_number"]`)?.focus(); return; }
    const row = line.units[index];
    if (!row.serial_number.trim() && !row.imei.trim()) return; // nothing typed: do not stack blank rows
    const fresh = blankUnit(row.condition);
    pending.current = { unit: fresh.key, field: "serial_number" };
    onChange({ units: [...line.units, fresh] });
  };

  const focusField = (unit: string, field: string) =>
    root.current?.querySelector<HTMLInputElement>(`[data-unit="${unit}"][data-field="${field}"]`)?.focus();

  const addEmpty = () => {
    const last = line.units[line.units.length - 1];
    const fresh = blankUnit(last?.condition ?? "new");
    pending.current = { unit: fresh.key, field: "serial_number" };
    onChange({ units: [...line.units, fresh] });
  };

  const imeiLabel = line.product.requires_imei ? "IMEI *" : "IMEI (opcional)";
  const baseCondition = line.product.condition_line === "used" ? "used" : "new";
  return (
    <div ref={root} className="space-y-4">
      {line.units.map((unit, index) => {
        const p = problems?.units[unit.key] ?? {};
        return (
          <fieldset key={unit.key} className="rounded-lg border border-bd-border p-3 sm:p-4">
            <legend className="px-1 text-xs font-semibold uppercase tracking-widest text-muted">Equipo {index + 1}</legend>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field
                label="Número de serie *" mono value={unit.serial_number} unit={unit.key} field="serial_number"
                onChange={(serial_number) => setUnit(unit.key, { serial_number })} error={p.serial_number}
                onEnter={() => (line.product.requires_imei ? focusField(unit.key, "imei") : addAfter(unit.key))}
              />
              <Field
                label={imeiLabel} mono inputMode="numeric" placeholder="15 dígitos" value={unit.imei} unit={unit.key} field="imei"
                onChange={(imei) => setUnit(unit.key, { imei })} error={p.imei} onEnter={() => addAfter(unit.key)}
              />
              <Field
                label="IMEI 2 (opcional)" mono inputMode="numeric" placeholder="Sólo si tiene dos" value={unit.imei2} unit={unit.key} field="imei2"
                onChange={(imei2) => setUnit(unit.key, { imei2 })} error={p.imei2} onEnter={() => addAfter(unit.key)}
              />
              <div>
                <label htmlFor={`${unit.key}-cond`} className="block text-xs font-semibold text-foreground">Estado del equipo</label>
                <select
                  id={`${unit.key}-cond`} value={unit.condition}
                  onChange={(e) => setUnit(unit.key, { condition: e.target.value })}
                  className={`mt-1.5 min-h-11 ${internalInputClass}`}
                >
                  {UNIT_CONDITIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </div>
              {/* A used device carries what was measured and seen; a new one only what its box says. */}
              {unit.condition !== "new" ? (
                <Field
                  label="Batería medida (%)" inputMode="numeric" placeholder="Ej.: 93" value={unit.battery} unit={unit.key}
                  field="battery" onChange={(battery) => setUnit(unit.key, { battery: battery.replace(/[^\d%\s]/g, "").slice(0, 5) })}
                  error={p.battery}
                />
              ) : null}
              {unit.condition !== "new" && grades.length > 0 ? (
                <div>
                  <label htmlFor={`${unit.key}-grade`} className="block text-xs font-semibold text-foreground">Estado estético</label>
                  <select
                    id={`${unit.key}-grade`} value={unit.grade}
                    onChange={(e) => setUnit(unit.key, { grade: e.target.value })}
                    className={`mt-1.5 min-h-11 ${internalInputClass}`}
                  >
                    <option value="">Sin indicar</option>
                    {grades.map((g) => <option key={g.id} value={String(g.id)}>{g.label}</option>)}
                  </select>
                </div>
              ) : null}
              {canCosts ? (
                <Field
                  label="Costo del equipo (opcional)" inputMode="decimal" placeholder="Ej.: 2500.00" value={unit.cost}
                  unit={unit.key} field="cost" onChange={(cost) => setUnit(unit.key, { cost })} error={p.cost}
                />
              ) : null}
              {/* INV-COM-01A: a used-line article is priced and described device by device; the others sell at the
                  product's price, which a reception never changes. Without authority over prices the device
                  is received unpriced and waits to be completed. */}
              {canPrice && isUsedLine(line.product) ? (
                <Field
                  label="Precio de venta del equipo (S/, opcional)" inputMode="decimal" placeholder="Ej.: 1899.00" value={unit.price}
                  unit={unit.key} field="price" onChange={(price) => setUnit(unit.key, { price })} error={p.price}
                />
              ) : null}
              {isUsedLine(line.product) ? (
                <Field
                  label="Detalles para el comprador (opcional)" value={unit.publicNotes} unit={unit.key} field="publicNotes"
                  onChange={(publicNotes) => setUnit(unit.key, { publicNotes })} error={p.publicNotes}
                />
              ) : null}
              <div className="sm:col-span-2 lg:col-span-3">
                <Field
                  label="Observaciones internas (opcional)" value={unit.notes} unit={unit.key} field="notes"
                  onChange={(notes) => setUnit(unit.key, { notes })} error={p.notes}
                />
              </div>
            </div>
            {p.row ? <p role="alert" className="mt-2 text-xs text-danger">{p.row}</p> : null}
            {/* Only the identifiers make a device: a row with just a cost or a state is not sent. */}
            {!unit.serial_number.trim() && !unit.imei.trim() && !unit.imei2.trim()
              && (unit.cost.trim() || unit.condition !== baseCondition) ? (
              <p role="status" className="mt-2 text-xs text-warning">Esta fila no tiene serie ni IMEI y no se enviará.</p>
            ) : null}
            {line.units.length > 1 ? (
              <button
                type="button" className={`${internalButtonClass} mt-3 min-h-11`}
                aria-label={`Quitar el equipo ${index + 1} de ${line.product.name}`}
                onClick={() => onChange({ units: line.units.filter((u) => u.key !== unit.key) })}
              >
                Quitar este equipo
              </button>
            ) : null}
          </fieldset>
        );
      })}
      <button type="button" className={`${internalButtonClass} min-h-11`} onClick={addEmpty}>Agregar otro equipo</button>
      <p className="text-xs text-muted">
        Con el lector: escanea la serie, pulsa Enter, escanea el IMEI y pulsa Enter; aparece el equipo siguiente.
      </p>
    </div>
  );
}

export function ReceivingLinesEditor({
  lines, onLines, canCosts, canManageProducts, problems, focus, onCreateArticle, grades = [], listPanel = null, listPending = 0,
}: {
  /** The company's cosmetic grades for used devices; empty when it has none or the person cannot read them. */
  grades?: CosmeticGradeRow[];
  /** INV-RECEIVE-UX-02: «Pegar una lista», next to the search. Kept mounted so switching never loses it. */
  listPanel?: ReactNode;
  /** Rows of the pasted list still to resolve: said on the tab. */
  listPending?: number;
  lines: DraftLine[];
  onLines: (next: DraftLine[]) => void;
  canCosts: boolean;
  canManageProducts: boolean;
  /** Obvious problems per line, shown once the person tried to continue. */
  problems: Record<string, LineProblems> | null;
  /** Bring this line into view (a «Corregir» link). `token` changes on every request. */
  focus: { lineKey: string; token: number; unitKey?: string } | null;
  /** Called right before leaving to create an article, to keep the draft in memory. */
  onCreateArticle: () => void;
}) {
  const [pickerToken, setPickerToken] = useState(0);
  const [notice, setNotice] = useState("");
  // Coming back with rows still waiting in the list (a step back, «otra recepción»): the list is what is shown.
  const [mode, setMode] = useState<"search" | "paste">(() => (listPanel && listPending > 0 ? "paste" : "search"));
  const wanted = useRef<{ line: string; unit?: string } | null>(null);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!focus) return;
    wanted.current = { line: focus.lineKey, unit: focus.unitKey };
  }, [focus]);

  // Move the cursor where the next thing is typed: the new (or corrected) line's first field.
  useEffect(() => {
    const want = wanted.current;
    if (!want) return;
    const card = container.current?.querySelector<HTMLElement>(`[data-line-card="${want.line}"]`);
    if (!card) return;
    wanted.current = null;
    card.scrollIntoView?.({ block: "center" });
    const target = (want.unit
      ? card.querySelector<HTMLInputElement>(`[data-unit="${want.unit}"][aria-invalid="true"]`)
        ?? card.querySelector<HTMLInputElement>(`[data-unit="${want.unit}"]`)
      : null) ?? card.querySelector<HTMLInputElement>('input:not([type="hidden"])');
    target?.focus();
  }, [lines, focus]);

  const patch = (key: string, change: Partial<DraftLine>) =>
    onLines(lines.map((l) => (l.key === key ? { ...l, ...change } : l)));

  function pick(product: PickerProduct | null) {
    if (!product) return;
    const existing = lines.find((l) => l.product.id === product.id);
    if (existing) {
      setNotice(`«${product.name}» ya está en la lista: sigue completándolo ahí.`);
      wanted.current = { line: existing.key };
      onLines([...lines]);
      return;
    }
    setNotice("");
    const line = newLine(toReceiptProduct(product));
    wanted.current = { line: line.key };
    onLines([...lines, line]);
  }

  const totalDevices = lines.reduce((n, l) => n + (l.product.is_serialized ? unitsInUse(l).length : 0), 0);
  const totalUnits = lines.reduce((n, l) => n + (l.product.is_serialized ? 0 : Number(l.quantity) || 0), 0);

  return (
    <div ref={container} className="space-y-5">
      <div>
        {listPanel ? (
          <div role="group" aria-label="Cómo agregar mercadería" className="mb-3 flex flex-wrap gap-2">
            <button
              type="button" aria-pressed={mode === "search"} onClick={() => setMode("search")}
              className={`${BIG_BUTTON} ${mode === "search" ? "border-foreground font-bold" : ""}`}
            >
              Buscar o escanear un artículo
            </button>
            <button
              type="button" aria-pressed={mode === "paste"} onClick={() => setMode("paste")}
              className={`${BIG_BUTTON} ${mode === "paste" ? "border-foreground font-bold" : ""}`}
            >
              Pegar una lista{listPending > 0 ? ` (${listPending} en la lista)` : ""}
            </button>
          </div>
        ) : null}
        {/* NAV-UX-02: the official Excel loads stay where they are, with their own preview and confirmation; this
            only points to them (both need `inventory.adjust`, which receiving already requires). They leave the
            assistant, so they are not part of the toggle group above. */}
        {listPanel ? (
          <p className="mb-3 text-xs text-muted">
            ¿Tienes un Excel? Usa la carga oficial (sale del asistente):{" "}
            <Link href="/admin/inventory/units/import" className="font-semibold text-foreground underline">Importar equipos desde Excel</Link>
            {" · "}
            <Link href="/admin/inventory/import" className="font-semibold text-foreground underline">Importar cantidades desde Excel</Link>
          </p>
        ) : null}
        <div hidden={mode !== "search"}>
          <ProductPicker
            label="Buscar el artículo que llegó"
            inputId="receive-product"
            value={null}
            onChange={pick}
            focusToken={pickerToken}
            helper="Escribe el nombre, el SKU o escanea el código de barras y pulsa Enter. Si el artículo ya existe, se usa el mismo: no se crea otro."
          />
          {notice ? <p role="status" className="mt-2 text-xs text-warning">{notice}</p> : null}
          <div className="mt-3 rounded-lg border border-dashed border-bd-border p-3 text-xs text-muted">
            {canManageProducts ? (
              <p>
                ¿Buscaste y no aparece?{" "}
                <Link
                  href="/admin/products/register?from=receive"
                  onClick={onCreateArticle}
                  data-receiving-leave="ok"
                  className="font-semibold text-foreground underline underline-offset-4"
                >
                  No existe: crear artículo
                </Link>
                . Al terminar volverás aquí con el artículo ya elegido y tu lista intacta.
              </p>
            ) : (
              <p>
                ¿Buscaste y no aparece? Tu usuario no puede crear artículos. Pide a alguien con permiso para
                administrar productos que lo registre y luego vuelve a buscarlo aquí.
              </p>
            )}
            <p className="mt-2">
              ¿Tienes muchos equipos en una lista?{" "}
              {listPanel ? (
                <>Usa «Pegar una lista» arriba: el sistema busca el catálogo por ti y tú revisas cada equipo. </>
              ) : (
                <Link href="/admin/inventory/bulk" className="font-semibold text-foreground underline underline-offset-4">
                  Cargar desde una lista pegada
                </Link>
              )}
              Para un archivo Excel usa{" "}
              <Link href="/admin/inventory/bulk" className="font-semibold text-foreground underline underline-offset-4">la carga masiva</Link>.
            </p>
          </div>
        </div>
        {listPanel ? <div hidden={mode !== "paste"}>{listPanel}</div> : null}
      </div>

      {lines.length === 0 ? (
        <p className="rounded-lg border border-dashed border-bd-border px-4 py-6 text-center text-sm text-muted">
          Todavía no agregaste nada. Busca un artículo arriba para empezar.
        </p>
      ) : (
        <section aria-label="Lo que vas a recibir" className="space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-semibold text-foreground">Lo que vas a recibir</h3>
            <p className="text-xs text-muted" role="status">
              {plural(lines.length, "artículo", "artículos")}
              {totalUnits > 0 ? ` · ${plural(totalUnits, "unidad", "unidades")}` : ""}
              {totalDevices > 0 ? ` · ${plural(totalDevices, "equipo", "equipos")}` : ""}
            </p>
          </div>
          {lines.map((line) => {
            const lineProblems = problems?.[line.key];
            return (
              <article
                key={line.key}
                data-line-card={line.key}
                aria-label={line.product.name}
                className={`rounded-xl border bg-surface p-4 ${lineProblems ? "border-danger" : "border-bd-border"}`}
              >
                <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h4 className="break-words text-sm font-semibold text-foreground">{line.product.name}</h4>
                    <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                      {line.product.sku ? <span className="font-mono text-foreground">{line.product.sku}</span> : null}
                      <span className={BADGE}>{line.product.is_serialized ? "Equipos con serie" : "Por cantidad"}</span>
                    </p>
                  </div>
                  <button
                    type="button" className={`${internalButtonClass} min-h-11`}
                    aria-label={`Quitar ${line.product.name} de la lista`}
                    onClick={() => onLines(lines.filter((l) => l.key !== line.key))}
                  >
                    Quitar
                  </button>
                </header>
                {lineProblems?.line ? <p role="alert" className="mb-3 text-xs text-danger">{lineProblems.line}</p> : null}
                {line.product.is_serialized ? (
                  <UnitsEditor line={line} problems={lineProblems} canCosts={canCosts} canPrice={canManageProducts} grades={grades} onChange={(change) => patch(line.key, change)} />
                ) : (
                  <QuantityEditor
                    line={line} problems={lineProblems} canCosts={canCosts}
                    onChange={(change) => patch(line.key, change)}
                    onEnter={() => setPickerToken((t) => t + 1)}
                  />
                )}
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
