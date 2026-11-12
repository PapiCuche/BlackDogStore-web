"use client";

/**
 * CATALOG-COMMERCIAL-01 · A1 — «Opciones de este modelo»: the PALETTE of a model.
 *
 * Which capacities, colours, SIM types and regions (whatever the company calls
 * them: the attribute's ROLE says what it is) this model admits, chosen among
 * the company's own options; and every configuration those options make, with
 * its state: with a variant already, or valid and still without one. Variants
 * that exist outside today's palette are NAMED here, never corrected: history
 * is kept (ADR §5).
 *
 * The server decides on every save (`products.manage`); this panel only shows
 * what it answered. Nothing here touches stock.
 */

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  setModelPalette, type AdminModelPalette, type PaletteAttribute, type PaletteStatus,
} from "../lib/internal-api";
import { PALETTE_STATUS_LABELS, ROLE_LABELS, paletteSelection, sameSelection } from "../lib/palette";
import { EmptyBox, ErrorBox, Panel, Spinner, Td, Th } from "./InventoryUi";
import { internalButtonClass, internalPrimaryButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The state of a configuration or a variant, as a small label. */
export function PaletteStatusBadge({ status }: { status: PaletteStatus }) {
  const tone = status === "variante_existente"
    ? "border-success-border text-success"
    : status === "combinacion_valida_sin_variante"
      ? "border-bd-border text-muted"
      : "border-warning-border bg-warning-surface text-warning";
  return (
    <span className={`inline-flex min-h-6 items-center rounded-full border px-2 text-[11px] font-semibold ${tone}`}>
      {PALETTE_STATUS_LABELS[status]}
    </span>
  );
}

function OptionChoice({
  attribute, optionId, label, swatch, checked, disabled, onChange,
}: {
  attribute: PaletteAttribute; optionId: number; label: string; swatch: string; checked: boolean; disabled: boolean;
  onChange: (id: number, checked: boolean) => void;
}) {
  return (
    <label
      className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border px-3 text-sm transition-colors ${
        checked ? "border-foreground/40 bg-foreground/[0.04] text-foreground" : "border-bd-border text-muted hover:border-foreground/25"
      } ${disabled ? "cursor-default opacity-70" : ""}`}
    >
      <input
        type="checkbox"
        className="h-4 w-4"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(optionId, e.target.checked)}
        name={`paleta-${attribute.id}`}
        value={optionId}
      />
      {attribute.kind === "color" && swatch ? (
        <span aria-hidden="true" className="h-3.5 w-3.5 rounded-full border border-foreground/25" style={{ backgroundColor: swatch }} />
      ) : null}
      <span>{label}</span>
    </label>
  );
}

export function ModelPalettePanel({
  modelId, companyId, canManage, palette, error, onSaved,
}: {
  modelId: number;
  companyId: number | null;
  canManage: boolean;
  /** The palette as the server last answered; null while it loads. */
  palette: AdminModelPalette | null;
  /** Why it could not load, when it could not. */
  error: string | null;
  /** The server's answer to a save: the parent keeps it and reads the model again. */
  onSaved: (palette: AdminModelPalette) => void;
}) {
  const saved = useMemo(() => (palette ? paletteSelection(palette) : new Set<number>()), [palette]);
  // What is ticked now. Keyed by the palette the server answered: a new answer resets what was being ticked.
  const [draftFor, setDraftFor] = useState<{ source: AdminModelPalette | null; chosen: Set<number> }>({ source: null, chosen: new Set() });
  const chosen = draftFor.source === palette ? draftFor.chosen : saved;
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [onlyFree, setOnlyFree] = useState(false);

  const dirty = !sameSelection(chosen, saved);

  function toggle(id: number, checked: boolean) {
    const next = new Set(chosen);
    if (checked) next.add(id); else next.delete(id);
    setDraftFor({ source: palette, chosen: next });
    setNotice(null);
  }

  function cancel() {
    setDraftFor({ source: palette, chosen: new Set(saved) });
    setSaveError(null);
  }

  async function save() {
    if (!palette) return;
    setSaving(true);
    setSaveError(null);
    setNotice(null);
    try {
      const answer = await setModelPalette(modelId, [...chosen].sort((a, b) => a - b), companyId);
      onSaved(answer);
      setNotice("Paleta guardada.");
    } catch (err) {
      setSaveError(message(err, "No se pudo guardar la paleta."));
    } finally {
      setSaving(false);
    }
  }

  const paletteColumns = palette ? palette.attributes.filter((a) => a.options.some((o) => o.in_palette)) : [];
  const combinations = palette
    ? (onlyFree ? palette.combinations.filter((c) => c.status !== "variante_existente") : palette.combinations)
    : [];
  const troubled = palette ? palette.variants.filter((v) => v.status === "fuera_de_paleta" || v.status === "incompleta") : [];

  return (
    <Panel
      id="paleta"
      title="Opciones de este modelo"
      description="Qué capacidades, colores, tipos de SIM y regiones admite este modelo. Sus variantes sólo combinan lo que esté marcado aquí; lo que ya existe no se corrige solo, se señala."
    >
      <div className="space-y-5">
        {error ? <ErrorBox message={error} /> : null}
        {!palette && !error ? <Spinner label="Cargando la paleta…" /> : null}
        {palette ? (
          <>
            {palette.attributes.length === 0 ? (
              <p className="rounded-lg border border-bd-border px-3 py-2 text-xs text-muted">
                La empresa todavía no tiene atributos. Créalos en{" "}
                <Link href="/admin/products/models#atributos" className="text-foreground underline underline-offset-4">Atributos y opciones</Link>
                {" "}(con su rol: capacidad, color, SIM, región) y vuelve a este modelo.
              </p>
            ) : null}
            {!palette.has_palette && palette.attributes.length > 0 ? (
              <p role="note" className="rounded-lg border border-bd-border px-3 py-2 text-sm text-muted">
                Este modelo todavía no dice qué opciones admite: sus variantes pueden combinar cualquier opción de la empresa.
                Marca las que correspondan y guarda la paleta.
              </p>
            ) : null}

            <div className="space-y-4">
              {palette.attributes.map((attribute) => (
                <fieldset key={attribute.id} aria-label={attribute.name} className="min-w-0">
                  <legend className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold text-foreground">
                    {attribute.name}
                    <span className="text-xs font-normal text-muted">{ROLE_LABELS[attribute.role] ?? ROLE_LABELS.other}</span>
                  </legend>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {attribute.options.length === 0 ? <span className="text-xs text-muted">Sin opciones.</span> : null}
                    {attribute.options.map((option) => (
                      <OptionChoice
                        key={option.id}
                        attribute={attribute}
                        optionId={option.id}
                        label={option.label}
                        swatch={option.swatch_hex}
                        checked={chosen.has(option.id)}
                        disabled={!canManage || saving}
                        onChange={toggle}
                      />
                    ))}
                  </div>
                </fieldset>
              ))}
            </div>

            {canManage && palette.attributes.length > 0 ? (
              <div className="flex flex-wrap items-center justify-end gap-2">
                {dirty ? <span className="mr-auto text-xs text-muted">Cambios sin guardar.</span> : null}
                <button type="button" className={internalButtonClass} onClick={cancel} disabled={!dirty || saving}>Cancelar</button>
                <button type="button" className={internalPrimaryButtonClass} onClick={() => void save()} disabled={!dirty || saving}>
                  {saving ? "Guardando…" : "Guardar paleta"}
                </button>
              </div>
            ) : null}
            {notice ? <p role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}
            {saveError ? <p role="alert" className="rounded-lg border border-danger-border px-4 py-3 text-sm text-danger">{saveError}</p> : null}

            {palette.has_palette ? (
              <div className="space-y-3 border-t border-bd-border pt-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground">Configuraciones</h3>
                  <p className="text-xs text-muted">
                    {plural(palette.summary.combinations, "configuración", "configuraciones")}
                    {" · "}{palette.summary.existing} con variante
                    {" · "}{palette.summary.valid_without_variant} sin variante
                    {palette.summary.outside ? ` · ${palette.summary.outside} fuera de la paleta` : ""}
                    {palette.summary.incomplete ? ` · ${palette.summary.incomplete} ${palette.summary.incomplete === 1 ? "incompleta" : "incompletas"}` : ""}
                  </p>
                </div>
                {palette.truncated ? (
                  <p role="note" className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning">
                    La paleta tiene más configuraciones de las que se muestran ({palette.combinations.length} de {palette.summary.combinations}).
                    Reduce las opciones marcadas para verlas todas.
                  </p>
                ) : null}
                <label className="flex items-center gap-2 text-xs text-muted">
                  <input type="checkbox" checked={onlyFree} onChange={(e) => setOnlyFree(e.target.checked)} />
                  Sólo las que aún no tienen variante
                </label>
                {combinations.length === 0 ? (
                  <EmptyBox message={onlyFree ? "Todas las configuraciones tienen variante." : "La paleta no forma ninguna configuración."} />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[32rem] border-collapse text-sm" aria-label="Configuraciones de la paleta">
                      <thead>
                        <tr className="border-b border-bd-border">
                          {paletteColumns.map((attribute) => <Th key={attribute.id}>{attribute.name}</Th>)}
                          <Th>Estado</Th>
                          <Th>Producto</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {combinations.map((combination) => (
                          <tr key={combination.key} className="border-b border-bd-border/60">
                            {paletteColumns.map((attribute) => (
                              <Td key={attribute.id}>{combination.labels[String(attribute.id)] ?? "—"}</Td>
                            ))}
                            <Td><PaletteStatusBadge status={combination.status} /></Td>
                            <Td>
                              {combination.products.length === 0 ? <span className="text-muted">—</span> : (
                                <span className="flex flex-col gap-0.5">
                                  {combination.products.map((product) => (
                                    <Link key={product.product_id} href={`/admin/products/${product.product_id}`} className="underline-offset-4 hover:underline">
                                      {product.name}
                                      <span className="ml-1 text-xs text-muted">{product.condition_line === "used" ? "(seminuevo)" : ""}</span>
                                    </Link>
                                  ))}
                                </span>
                              )}
                            </Td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {troubled.length > 0 ? (
                  <div className="rounded-lg border border-warning-border bg-warning-surface p-3">
                    <h4 className="text-xs font-semibold text-warning">Variantes fuera de la paleta o incompletas</h4>
                    <p className="mt-1 text-xs text-warning">
                      Siguen vendiéndose y conservan su historial. Amplía la paleta o edita la variante si corresponde.
                    </p>
                    <ul aria-label="Variantes fuera de la paleta o incompletas" className="mt-2 space-y-1 text-sm">
                      {troubled.map((variant) => (
                        <li key={variant.product_id} className="flex flex-wrap items-center gap-2">
                          <Link href={`/admin/products/${variant.product_id}`} className="text-foreground underline-offset-4 hover:underline">{variant.name}</Link>
                          {variant.status ? <PaletteStatusBadge status={variant.status} /> : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </Panel>
  );
}
