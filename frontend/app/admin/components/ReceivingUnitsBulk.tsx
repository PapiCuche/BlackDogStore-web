"use client";

/**
 * INV-INTAKE-BULK-01 — herramientas de una línea por equipo en «Recibir mercadería», para cientos de equipos:
 *
 * - «Pegar varios»: series e IMEI pegados (uno por línea, o «serie IMEI»). Se revisa lo pegado y se confirma antes de
 *   agregar. La vista previa sólo muestra identificadores enmascarados (•••• y los 4 últimos, como la revisión).
 * - «Aplicar a todos»: costo, estado estético, batería y precio propio, con los mismos permisos que cada fila.
 * - La sugerencia «¿Quitar la S inicial?» de una serie leída de la caja de Apple (NO VERIFICADO: nunca se quita sola).
 *
 * Todo vive en la memoria de la página; el servidor vuelve a revisarlo en «Revisar y confirmar».
 */

import { useId, useState } from "react";

import { isUsedLine, MAX_DEVICES, unitsInUse, type DraftLine, type DraftUnit } from "../lib/inventory-receipts";
import type { CosmeticGradeRow } from "../lib/internal-api";
import {
  appleBoxSerialFix, applicableFields, applyProblems, applyToAllUnits, isReviewShortcut, mergePastedUnits, parseUnitPaste,
  PASTE_MAX_TOKENS, type ApplyValues, type PastePreview,
} from "../lib/receive-bulk";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const PANEL = "space-y-3 rounded-lg border border-bd-border p-3";
const LABEL = "block text-xs font-semibold text-foreground";

/** INV-INTAKE-BULK-01 · an Apple box barcode may put an «S» before the serial: offered, never done by itself. */
export function SerialPrefixHint({ serial, onAccept }: { serial: string; onAccept: (serial: string) => void }) {
  const fix = appleBoxSerialFix(serial);
  if (!fix) return null;
  return (
    <div className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-foreground sm:col-span-2 lg:col-span-3">
      <p>
        Esta serie tiene 13 caracteres y empieza con «S». El código de barras de la caja de Apple puede anteponer una S
        a la serie: compárala con la del equipo (Ajustes › General › Información).
      </p>
      <button type="button" className={`${internalButtonClass} mt-2 min-h-11`} onClick={() => onAccept(fix)}>
        ¿Quitar la S inicial?
      </button>
    </div>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="font-semibold text-foreground">{title}</p>
      <p className="break-words font-mono text-[11px] text-muted">{items.join(" · ")}</p>
    </div>
  );
}

function PasteUnitsPanel({ line, onUnits, onDone }: { line: DraftLine; onUnits: (units: DraftUnit[]) => void; onDone: (said: string) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const [stripS, setStripS] = useState(false);
  const [preview, setPreview] = useState<PastePreview | null>(null);

  const read = (strip: boolean) => setPreview(parseUnitPaste(text, line.units, { stripS: strip }));
  const add = preview?.units.length ?? 0;
  const afterAdd = unitsInUse(line).length + add;

  return (
    <div className={PANEL}>
      <div>
        <label htmlFor={id} className={LABEL}>Series o IMEI, uno por línea</label>
        <textarea
          id={id} value={text} rows={6} spellCheck={false} autoComplete="off"
          onChange={(e) => { setText(e.target.value); setPreview(null); }}
          onKeyDown={(e) => { if (isReviewShortcut(e) && text.trim()) { e.preventDefault(); read(stripS); } }}
          data-review-shortcut="off"
          className={`mt-1.5 font-mono text-xs ${internalInputClass}`}
        />
        <p className="mt-1 text-xs text-muted">
          Un valor de 15 dígitos que supera el dígito de control va al IMEI; cualquier otro, a la serie. «Serie IMEI» en la
          misma línea es un solo equipo. Hasta {PASTE_MAX_TOKENS} códigos por vez. Ctrl + Enter (⌘ + Enter) revisa lo pegado.
        </p>
      </div>
      <button type="button" className={`${internalButtonClass} min-h-11`} disabled={!text.trim()} onClick={() => read(stripS)}>
        Revisar lo pegado
      </button>
      {preview ? (
        <section aria-label="Vista previa de lo pegado" className="space-y-2 text-xs">
          {preview.tooMany ? (
            <p role="alert" className="text-danger">
              Pegaste {preview.tokens} códigos y el máximo es {PASTE_MAX_TOKENS} por vez. Pega una parte, agrégala y luego pega el resto.
            </p>
          ) : (
            <>
              <p role="status" className="font-semibold text-foreground">
                {add === 0 ? "No hay equipos nuevos para agregar."
                  : `Se ${add === 1 ? "agregará" : "agregarán"} ${plural(add, "equipo", "equipos")}: ${preview.imeis} con IMEI y ${preview.serials} con serie.`}
                {` Se leyeron ${plural(preview.tokens, "código", "códigos")}.`}
              </p>
              {preview.withoutSerial > 0 ? (
                <p className="text-warning">
                  {preview.withoutSerial} sin número de serie: la serie es obligatoria; escanéala en su fila antes de seguir.
                </p>
              ) : null}
              <List title="Repetidos en lo pegado (se agregan una sola vez):" items={preview.duplicates} />
              <List title="Ya están en esta línea (no se agregan otra vez):" items={preview.already} />
              <List title="No se reconocen (no se agregan):" items={preview.invalid.map((i) => `${i.masked} (${i.reason})`)} />
              {preview.sPrefixed > 0 ? (
                <label className="flex min-h-11 cursor-pointer items-start gap-2 text-foreground">
                  <input
                    type="checkbox" className="mt-0.5 h-5 w-5 shrink-0" checked={stripS}
                    onChange={(e) => { setStripS(e.target.checked); read(e.target.checked); }}
                  />
                  <span>
                    Quitar la «S» inicial de {plural(preview.sPrefixed, "serie", "series")} de 13 caracteres
                    <span className="block text-muted">
                      El código de la caja de Apple puede anteponer una S a la serie. No está verificado: compáralo con la serie del equipo.
                    </span>
                  </span>
                </label>
              ) : null}
              {afterAdd > MAX_DEVICES ? (
                <p className="text-warning">
                  Esta línea tendría {afterAdd} equipos: se reciben hasta {MAX_DEVICES} por vez. Registra el resto en otra recepción.
                </p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <button
                  type="button" className={`${internalPrimaryButtonClass} min-h-11`} disabled={add === 0}
                  onClick={() => {
                    onUnits(mergePastedUnits(line, preview.units));
                    setText(""); setPreview(null); setStripS(false);
                    onDone(`Se ${add === 1 ? "agregó" : "agregaron"} ${plural(add, "equipo", "equipos")}.`);
                  }}
                >
                  {`Agregar ${plural(add, "equipo", "equipos")}`}
                </button>
                <button type="button" className={`${internalButtonClass} min-h-11`} onClick={() => setPreview(null)}>Cancelar</button>
              </div>
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}

function ApplyInput({ label, value, onChange, error, inputMode, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; error?: string; inputMode: "decimal" | "numeric"; placeholder: string;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={LABEL}>{label}</label>
      <input
        id={id} value={value} inputMode={inputMode} placeholder={placeholder} autoComplete="off" spellCheck={false}
        aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => onChange(e.target.value)}
        className={`mt-1.5 min-h-11 ${internalInputClass} ${error ? "border-danger" : ""}`}
      />
      {error ? <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
    </div>
  );
}

function ApplyToAllPanel({ line, fields, access, grades, onUnits }: {
  line: DraftLine; fields: (keyof ApplyValues)[]; access: Parameters<typeof applyToAllUnits>[2]; grades: CosmeticGradeRow[];
  onUnits: (units: DraftUnit[]) => void;
}) {
  const gradeId = useId();
  const [values, setValues] = useState<ApplyValues>({});
  const [overwrite, setOverwrite] = useState(false);
  const [errors, setErrors] = useState<ReturnType<typeof applyProblems>>({});
  const [said, setSaid] = useState("");
  const set = (field: keyof ApplyValues) => (value: string) => { setValues((v) => ({ ...v, [field]: value })); setSaid(""); };
  const targets = unitsInUse(line).length;
  const anything = fields.some((f) => values[f]?.trim());

  function apply() {
    const found = applyProblems(values);
    setErrors(found);
    if (Object.keys(found).length) { setSaid(""); return; }
    const out = applyToAllUnits(line.units, values, access, { overwrite });
    onUnits(out.units);
    setSaid(
      `Listo: se ${out.changed === 1 ? "completó" : "completaron"} ${plural(out.changed, "equipo", "equipos")}.`
      + (out.kept ? ` ${out.kept === 1 ? "1 valor ya escrito se mantuvo" : `${out.kept} valores ya escritos se mantuvieron`}.` : ""),
    );
  }

  return (
    <fieldset className={PANEL}>
      <legend className="px-1 text-xs font-semibold text-foreground">Aplicar a todos los equipos de esta línea</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.includes("cost") ? (
          <ApplyInput label="Costo por equipo (S/)" value={values.cost ?? ""} onChange={set("cost")} error={errors.cost} inputMode="decimal" placeholder="Ej.: 2500.00" />
        ) : null}
        {fields.includes("grade") ? (
          <div>
            <label htmlFor={gradeId} className={LABEL}>Estado estético</label>
            <select id={gradeId} value={values.grade ?? ""} onChange={(e) => set("grade")(e.target.value)} className={`mt-1.5 min-h-11 ${internalInputClass}`}>
              <option value="">Sin cambiar</option>
              {grades.map((g) => <option key={g.id} value={String(g.id)}>{g.label}</option>)}
            </select>
          </div>
        ) : null}
        {fields.includes("battery") ? (
          <ApplyInput
            label="Batería medida (%)" value={values.battery ?? ""} error={errors.battery} inputMode="numeric" placeholder="Ej.: 93"
            onChange={(v) => set("battery")(v.replace(/[^\d%\s]/g, "").slice(0, 5))}
          />
        ) : null}
        {fields.includes("price") ? (
          <ApplyInput label="Precio de venta del equipo (S/)" value={values.price ?? ""} onChange={set("price")} error={errors.price} inputMode="decimal" placeholder="Ej.: 1899.00" />
        ) : null}
      </div>
      <label className="flex min-h-11 cursor-pointer items-start gap-2 text-xs text-foreground">
        <input type="checkbox" className="mt-0.5 h-5 w-5 shrink-0" checked={overwrite} onChange={(e) => { setOverwrite(e.target.checked); setSaid(""); }} />
        <span>
          Reemplazar también lo que ya está escrito
          <span className="block text-muted">Sin marcar, sólo se completan los equipos que tienen ese campo vacío.</span>
        </span>
      </label>
      {fields.some((f) => f === "grade" || f === "battery") ? (
        <p className="text-xs text-muted">El estado estético y la batería no se escriben en un equipo marcado «Nuevo».</p>
      ) : null}
      <button type="button" className={`${internalPrimaryButtonClass} min-h-11`} disabled={targets === 0 || !anything} onClick={apply}>
        {targets === 1 ? "Aplicar a 1 equipo" : `Aplicar a los ${targets} equipos`}
      </button>
      {targets === 0 ? <p className="text-xs text-muted">Primero anota o pega las series: se aplica a los equipos con serie o IMEI.</p> : null}
      {said ? <p role="status" className="text-xs text-success">{said}</p> : null}
    </fieldset>
  );
}

/** The two bulk tools of one «por equipo» line, each behind its own button so the rows stay in view. */
export function UnitsBulkTools({ line, canCosts, canPrice, grades, onUnits }: {
  line: DraftLine; canCosts: boolean; canPrice: boolean; grades: CosmeticGradeRow[]; onUnits: (units: DraftUnit[]) => void;
}) {
  const [open, setOpen] = useState<"paste" | "apply" | null>(null);
  const [said, setSaid] = useState("");
  const access = { costs: canCosts, prices: canPrice, used: isUsedLine(line.product), grades: grades.length > 0 };
  const fields = applicableFields(access);
  const toggle = (which: "paste" | "apply") => { setSaid(""); setOpen((o) => (o === which ? null : which)); };
  const tab = (which: "paste" | "apply", label: string) => (
    <button
      type="button" aria-expanded={open === which} onClick={() => toggle(which)}
      className={`${internalButtonClass} min-h-11 ${open === which ? "border-foreground font-bold" : ""}`}
    >
      {label}
    </button>
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {tab("paste", "Pegar varios")}
        {fields.length > 0 ? tab("apply", "Aplicar a todos") : null}
      </div>
      {open === "paste" ? (
        <PasteUnitsPanel line={line} onUnits={onUnits} onDone={(text) => { setSaid(text); setOpen(null); }} />
      ) : null}
      {open === "apply" && fields.length > 0 ? (
        <ApplyToAllPanel line={line} fields={fields} access={access} grades={grades} onUnits={onUnits} />
      ) : null}
      <p role="status" className="text-xs text-success">{said}</p>
    </div>
  );
}
