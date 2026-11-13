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
 *
 * A1b-2b: a configuration that is complete and still without a variant offers
 * «Crear variante» (only with `products.manage`): the product is born as a
 * pending DRAFT (hidden, without a price, without stock) under the name the
 * server would generate, which the operator may edit first. Nothing partial is
 * ever sent: only the combination's own options.
 */

import Link from "next/link";
import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  DEFAULT_LINE_LABELS, createModelVariant, setModelPalette,
  type AdminModelPalette, type CatalogLine, type CockpitVariant, type PaletteAttribute, type PaletteCombination, type PaletteStatus,
} from "../lib/internal-api";
import {
  PALETTE_STATUS_LABELS, ROLE_LABELS, cockpitErrorMessage, paletteSelection, sameSelection, variantNamePreview,
} from "../lib/palette";
import { EmptyBox, ErrorBox, Panel, Spinner, Td, Th } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

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
    <span className={`inline-flex min-h-6 items-center whitespace-nowrap rounded-full border px-2 text-[11px] font-semibold ${tone}`}>
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

/** The label of a configuration for buttons and titles: its options in reading order («128 GB · Negro»). */
const comboTitle = (combination: PaletteCombination, columns: PaletteAttribute[]) =>
  columns.map((attribute) => combination.labels[String(attribute.id)]).filter(Boolean).join(" · ");

/** The touch size of the panel's own controls (44 × 44 px, also on a phone). */
const touch = "min-h-11";

function CreateVariantForm({
  modelId, companyId, modelName, combination, columns, lineLabels, onCreated, onCancel, onStale,
}: {
  modelId: number;
  companyId: number | null;
  modelName: string;
  combination: PaletteCombination;
  /** The palette's attributes in reading order: the order of the generated name. */
  columns: PaletteAttribute[];
  lineLabels: Record<CatalogLine, string>;
  onCreated: (variant: CockpitVariant) => void;
  onCancel: () => void;
  /** The server said the palette moved under us (duplicate, gone): read it again. */
  onStale: () => void;
}) {
  const title = comboTitle(combination, columns);
  const labels = columns.map((attribute) => combination.labels[String(attribute.id)]).filter(Boolean);
  const [line, setLine] = useState<CatalogLine>("new");
  const proposal = variantNamePreview(modelName, labels, line);
  // Until the operator types, the name follows the proposal (a line change updates it).
  const [typed, setTyped] = useState<string | null>(null);
  const name = typed ?? proposal;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A second click before React re-renders must not send a second POST.
  const sending = useRef(false);
  const ids = useId();
  const nameField = useRef<HTMLInputElement>(null);
  // Opening the form takes the focus to the name: a keyboard or a reader lands on what to check.
  useEffect(() => { nameField.current?.focus(); }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    const clean = name.split(/\s+/).filter(Boolean).join(" ");
    if (!clean) { setError("Escribe el nombre de la variante."); return; }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      // The proposal untouched: the server generates the same name itself (and audits it as generated).
      const created = await createModelVariant(modelId, {
        condition_line: line,
        option_ids: combination.option_ids,
        ...(clean !== proposal ? { name: clean } : {}),
      }, companyId);
      onCreated({ ...created, name: created?.name || clean });
    } catch (err) {
      setError(cockpitErrorMessage(err, "No se pudo crear la variante.",
        "El modelo o alguna de sus opciones ya no existe en esta empresa. Recarga la página."));
      const code = (err as { code?: string | null })?.code;
      if (code === "duplicate" || code === "option_not_in_palette" || code === "variant_incomplete") onStale();
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      aria-label={`Crear variante ${title}`}
      className="grid gap-3 rounded-lg border border-bd-border bg-background p-4 text-left sm:grid-cols-2"
    >
      <p className="text-sm text-foreground sm:col-span-2">
        Nueva variante: <strong>{title}</strong>
      </p>
      <p id={`${ids}-nota`} className="text-xs text-muted sm:col-span-2">
        Nace como <strong className="text-foreground">borrador</strong>: oculta, sin precio y sin stock. No se vende
        hasta que le pongas precio y la habilites para la venta en «Variantes».
      </p>
      <label className="block text-xs text-muted">
        Línea
        <select
          value={line}
          onChange={(e) => setLine(e.target.value as CatalogLine)}
          disabled={busy}
          className={`mt-1.5 ${touch} ${internalInputClass}`}
        >
          <option value="new">{lineLabels.new}</option>
          <option value="used">{lineLabels.used}</option>
        </select>
      </label>
      <label className="block text-xs text-muted">
        Nombre de la variante
        <input
          ref={nameField}
          value={name}
          onChange={(e) => setTyped(e.target.value)}
          maxLength={255}
          disabled={busy}
          aria-describedby={`${ids}-propuesta`}
          className={`mt-1.5 ${touch} ${internalInputClass}`}
        />
      </label>
      <p id={`${ids}-propuesta`} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted sm:col-span-2">
        <span>Propuesto: modelo y opciones en su orden. Puedes cambiarlo antes de crearla.</span>
        {typed !== null && typed !== proposal ? (
          <button type="button" className={`${touch} text-foreground underline underline-offset-4`} onClick={() => setTyped(null)} disabled={busy}>
            Volver al nombre propuesto
          </button>
        ) : null}
      </p>
      {error ? <p role="alert" className="rounded-lg border border-danger-border px-3 py-2 text-sm text-danger sm:col-span-2">{error}</p> : null}
      <div className="flex flex-wrap justify-end gap-2 sm:col-span-2">
        <button type="button" className={`${touch} ${internalButtonClass}`} onClick={onCancel} disabled={busy}>Cancelar</button>
        <button type="submit" className={`${touch} ${internalPrimaryButtonClass}`} disabled={busy} aria-describedby={`${ids}-nota`}>
          {busy ? "Creando…" : "Crear variante"}
        </button>
      </div>
    </form>
  );
}

export function ModelPalettePanel({
  modelId, companyId, canManage, palette, error, onSaved, lineLabels = DEFAULT_LINE_LABELS, onCreated, onStale,
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
  /** A1b-2b: the company's names for the lines (new / used). */
  lineLabels?: Record<CatalogLine, string>;
  /** A1b-2b: a draft was created from a configuration; the parent reads the model and the palette again. */
  onCreated?: (variant: CockpitVariant) => void;
  /** A1b-2b: the server refused because the palette moved; the parent reads it again. */
  onStale?: () => void;
}) {
  const saved = useMemo(() => (palette ? paletteSelection(palette) : new Set<number>()), [palette]);
  // The saved set the server last answered, as a key: a re-read that brings the SAME palette (after a variant
  // was saved below) keeps what is being ticked; one that brings another palette (someone else saved) resets it.
  const savedKey = useMemo(() => [...saved].sort((a, b) => a - b).join("-"), [saved]);
  const [draftFor, setDraftFor] = useState<{ baseline: string | null; chosen: Set<number> }>({ baseline: null, chosen: new Set() });
  const chosen = draftFor.baseline === savedKey ? draftFor.chosen : saved;
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [onlyFree, setOnlyFree] = useState(false);
  // A1b-2b: the configuration whose «Crear variante» form is open (one at a time).
  const [creatingKey, setCreatingKey] = useState<string | null>(null);
  // After a creation the form is gone: the result is announced and takes the focus.
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const focusNotice = useRef(false);
  useEffect(() => {
    if (focusNotice.current && notice) { noticeRef.current?.focus(); focusNotice.current = false; }
  }, [notice]);

  const dirty = !sameSelection(chosen, saved);

  function toggle(id: number, checked: boolean) {
    const next = new Set(chosen);
    if (checked) next.add(id); else next.delete(id);
    setDraftFor({ baseline: savedKey, chosen: next });
    setNotice(null);
    setCreatingKey(null);
  }

  function cancel() {
    setDraftFor({ baseline: null, chosen: new Set() });
    setSaveError(null);
  }

  async function save() {
    if (!palette) return;
    setSaving(true);
    setSaveError(null);
    setNotice(null);
    try {
      const answer = await setModelPalette(modelId, [...chosen].sort((a, b) => a - b), companyId);
      setDraftFor({ baseline: null, chosen: new Set() });
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
  // «Crear variante» is offered only on complete configurations without a variant, and only to `products.manage`.
  const canCreate = canManage && Boolean(onCreated);
  const showActions = canCreate && combinations.some((c) => c.status === "combinacion_valida_sin_variante");
  const columnCount = paletteColumns.length + 2 + (showActions ? 1 : 0);

  function created(variant: CockpitVariant) {
    setCreatingKey(null);
    setNotice(`Se creó el borrador «${variant.name}». Está oculto y sin precio: ponle precio en «Variantes» y habilítalo para la venta.`);
    focusNotice.current = true;
    onCreated?.(variant);
  }

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
                    {/* The role, when the company's own name does not already say it («Capacidad» is «Capacidad»). */}
                    {(ROLE_LABELS[attribute.role] ?? ROLE_LABELS.other).localeCompare(attribute.name, "es", { sensitivity: "base" }) !== 0 ? (
                      <span className="text-xs font-normal text-muted">rol: {ROLE_LABELS[attribute.role] ?? ROLE_LABELS.other}</span>
                    ) : null}
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
                <button type="button" className={`${touch} ${internalButtonClass}`} onClick={cancel} disabled={!dirty || saving}>Cancelar</button>
                <button type="button" className={`${touch} ${internalPrimaryButtonClass}`} onClick={() => void save()} disabled={!dirty || saving}>
                  {saving ? "Guardando…" : "Guardar paleta"}
                </button>
              </div>
            ) : null}
            {notice ? (
              <p ref={noticeRef} tabIndex={-1} role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success focus:outline-none">
                {notice}
              </p>
            ) : null}
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
                <label className={`inline-flex ${touch} cursor-pointer items-center gap-2 text-xs text-muted`}>
                  <input type="checkbox" className="h-4 w-4" checked={onlyFree} onChange={(e) => setOnlyFree(e.target.checked)} />
                  Sólo las que aún no tienen variante
                </label>
                {showActions && dirty ? (
                  <p role="note" className="text-xs text-muted">Guarda o cancela los cambios de la paleta antes de crear una variante.</p>
                ) : null}
                {combinations.length === 0 ? (
                  <EmptyBox message={onlyFree ? "Todas las configuraciones tienen variante." : "La paleta no forma ninguna configuración."} />
                ) : (
                  <div className="@container overflow-x-auto">
                    <table className="w-full min-w-[32rem] border-collapse text-sm" aria-label="Configuraciones de la paleta">
                      <thead>
                        <tr className="border-b border-bd-border">
                          {paletteColumns.map((attribute) => <Th key={attribute.id}>{attribute.name}</Th>)}
                          <Th>Estado</Th>
                          <Th>Producto</Th>
                          {showActions ? <Th right>Acción</Th> : null}
                        </tr>
                      </thead>
                      <tbody>
                        {combinations.map((combination) => (
                          <Fragment key={combination.key}>
                          <tr className="border-b border-bd-border/60">
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
                            {showActions ? (
                              <Td right>
                                {combination.status === "combinacion_valida_sin_variante" ? (
                                  <button
                                    type="button"
                                    className={`${touch} whitespace-nowrap ${internalButtonClass}`}
                                    onClick={() => { setNotice(null); setCreatingKey(creatingKey === combination.key ? null : combination.key); }}
                                    disabled={dirty || saving}
                                    title={dirty ? "Guarda o cancela los cambios de la paleta primero." : undefined}
                                    aria-expanded={creatingKey === combination.key}
                                    aria-label={`Crear variante ${comboTitle(combination, paletteColumns)}`}
                                  >
                                    Crear variante
                                  </button>
                                ) : null}
                              </Td>
                            ) : null}
                          </tr>
                          {showActions && creatingKey === combination.key && !dirty ? (
                            <tr className="border-b border-bd-border/60">
                              <td colSpan={columnCount} className="px-3 py-3">
                                {/* Pinned to the visible width of the scrolling table: usable on a phone without scrolling sideways. */}
                                <div className="sticky left-0 w-[calc(100cqw-1.5rem)]">
                                <CreateVariantForm
                                  modelId={modelId}
                                  companyId={companyId}
                                  modelName={palette.model_name}
                                  combination={combination}
                                  columns={paletteColumns}
                                  lineLabels={lineLabels}
                                  onCreated={created}
                                  onCancel={() => setCreatingKey(null)}
                                  onStale={() => onStale?.()}
                                />
                                </div>
                              </td>
                            </tr>
                          ) : null}
                          </Fragment>
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
