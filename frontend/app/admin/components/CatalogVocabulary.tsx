"use client";

/**
 * Productos › Modelos — CATALOG-COMMERCIAL-01 · A2-2.
 *
 * «Vocabulario de proveedores»: how the company's suppliers write its models and options (aliases), in three
 * scopes — words of a model name, whole model names, options by role — and «Probar una descripción», which asks
 * the server how a description reads in this company's catalogue.
 *
 * THE SERVER DECIDES. It folds, matches, refuses duplicates and conflicts, and resolves descriptions
 * (`catalog_aliases`, `configuration_resolver`); this screen only shows what it answered, next to the field it
 * names. The only checks here are trivial (an empty field). Doubts and their possible actions are INFORMATION:
 * nothing is created, chosen or registered from the resolver panel.
 *
 * Reading needs `products.view`; writing `products.manage`, which the server checks on every request. Without it
 * the forms are simply not drawn. No amount, quantity, price or IMEI is ever asked or sent.
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  DEFAULT_LINE_LABELS,
  createCatalogAlias,
  deleteCatalogAlias,
  fetchCatalogAliases,
  fetchCatalogAttributes,
  fetchCatalogSettings,
  fetchCatalogVocabulary,
  resolveCatalogRows,
  updateCatalogAlias,
  type AliasScope,
  type AttributeRole,
  type CatalogAlias,
  type CatalogAliasWrite,
  type CatalogLine,
  type CatalogVocabulary,
  type ResolveAnswer,
  type ResolveConfiguration,
  type ResolveDoubt,
  type ResolveOptionRead,
  type ResolveRowInput,
  type ResolveRowResult,
  type ResolverStatus,
} from "../lib/internal-api";
import {
  ALIAS_SCOPE_HELP,
  ALIAS_SCOPE_LABELS,
  DOUBT_ACTION_LABELS,
  DOUBT_REASON_HELP,
  DOUBT_REASON_LABELS,
  MODEL_READ_LABELS,
  aliasWriteError,
  doubtRoleLabel,
  missingLabel,
  readError,
  viaLabel,
  type AliasField,
} from "../lib/catalog-vocabulary";
import {
  RESOLVER_STATUS_HELP, RESOLVER_STATUS_LABELS, RESOLVER_STATUS_ORDER, ROLE_LABELS, ROLE_ORDER,
} from "../lib/palette";
import { EmptyBox, ErrorBox, Panel, Spinner } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

/** 44 × 44 px touch targets and a visible focus on every control of this screen. */
const touch = "min-h-[44px]";
const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2";
const buttonClass = `${touch} ${focusRing} ${internalButtonClass}`;
const primaryClass = `${touch} ${focusRing} ${internalPrimaryButtonClass}`;
const linkButtonClass = `inline-flex ${touch} items-center rounded-lg px-2 text-sm font-semibold text-foreground underline underline-offset-4 ${focusRing} disabled:cursor-not-allowed disabled:opacity-40`;

const SCOPES: AliasScope[] = ["model_word", "model", "option"];

/** The anchor of each tab: «#vocabulario-opciones» opens the options tab (the palette links there). */
export const SCOPE_ANCHORS: Record<AliasScope, string> = {
  model_word: "vocabulario-palabras",
  model: "vocabulario-modelos",
  option: "vocabulario-opciones",
};

function scopeFromHash(): AliasScope {
  if (typeof window === "undefined") return "model_word";
  const hash = window.location.hash.slice(1);
  return SCOPES.find((scope) => SCOPE_ANCHORS[scope] === hash) ?? "model_word";
}

// ---------------------------------------------------------------------------
// One alias: where it points
// ---------------------------------------------------------------------------

/** The alias's real target, in words: the replacement word, the model or the option with its attribute and role. */
export function aliasTarget(alias: CatalogAlias): string {
  if (alias.scope === "model_word") return `«${alias.replacement}»`;
  if (alias.scope === "model") return alias.model_name ?? "Modelo sin nombre";
  const role = alias.role ? ROLE_LABELS[alias.role] : null;
  const where = [alias.attribute_name, role && role !== alias.attribute_name ? role : null].filter(Boolean).join(" · ");
  return `${alias.option_label ?? "Opción sin nombre"}${where ? ` (${where})` : ""}`;
}

const TARGET_PREFIX: Record<AliasScope, string> = {
  model_word: "se lee como",
  model: "es el modelo",
  option: "es la opción",
};

// ---------------------------------------------------------------------------
// The create / edit form
// ---------------------------------------------------------------------------

type FieldErrors = Partial<Record<AliasField | "form", string>>;

const ALIAS_LABELS: Record<AliasScope, string> = {
  model_word: "Palabra del proveedor",
  model: "Nombre del proveedor",
  option: "Texto del proveedor",
};

function AliasForm({
  scope, initial, vocabulary, vocabularyError, companyId, onSaved, onCancel, onGone, onChangedUnderUs, onBusy,
  attributeNames,
}: {
  /** A write is on its way: the section holds every other control still meanwhile. */
  onBusy: (busy: boolean) => void;
  /** The company's attribute names by id: two options of one role with the same label say which is which. */
  attributeNames: ReadonlyMap<number, string>;
  scope: AliasScope;
  /** Editing this alias; creating a new one without it. */
  initial?: CatalogAlias;
  vocabulary: CatalogVocabulary | null;
  vocabularyError: string | null;
  companyId: number | null;
  onSaved: (alias: CatalogAlias, created: boolean) => void;
  onCancel: () => void;
  /** 404 for the alias itself: it is gone (or of another company). The list reads again. */
  onGone: (text: string) => void;
  /** A target that is gone (404 on a model or option) or a `duplicate` (another write came first): the lists read
   *  again, the form stays. */
  onChangedUnderUs: () => void;
}) {
  const ids = useId();
  const [alias, setAlias] = useState(initial?.alias ?? "");
  const [replacement, setReplacement] = useState(initial?.replacement ?? "");
  const [modelId, setModelId] = useState(initial?.model_id ? String(initial.model_id) : "");
  const [optionId, setOptionId] = useState(initial?.option_id ? String(initial.option_id) : "");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);

  const fieldId = (field: AliasField) => `${ids}-${field}`;
  const errorId = (field: AliasField | "form") => `${ids}-${field}-error`;

  function focusField(field: AliasField | null) {
    const target = field ? document.getElementById(fieldId(field)) : null;
    (target ?? formRef.current?.querySelector<HTMLElement>("[data-form-error]"))?.focus();
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    // Only emptiness is checked here: every rule of the text is the server's.
    const empty: FieldErrors = {};
    if (!alias.trim()) empty.alias = "Escribe el alias.";
    if (scope === "model_word" && !replacement.trim()) empty.replacement = "Escribe la palabra por la que se reemplaza.";
    if (scope === "model" && !modelId) empty.model_id = "Elige el modelo.";
    if (scope === "option" && !optionId) empty.option_id = "Elige la opción.";
    if (Object.keys(empty).length > 0) {
      setErrors(empty);
      const first = (["alias", "replacement", "model_id", "option_id"] as AliasField[]).find((f) => empty[f]) ?? null;
      requestAnimationFrame(() => focusField(first));
      return;
    }
    const body: CatalogAliasWrite = { alias: alias.trim() };
    if (scope === "model_word") body.replacement = replacement.trim();
    if (scope === "model") body.model_id = Number(modelId);
    if (scope === "option") body.option_id = Number(optionId);
    sending.current = true;
    setBusy(true);
    onBusy(true);
    setErrors({});
    try {
      const saved = initial
        ? await updateCatalogAlias(initial.id, body, companyId)
        : await createCatalogAlias({ scope, ...body }, companyId);
      onSaved(saved, !initial);
      if (!initial) {
        setAlias(""); setReplacement(""); setModelId(""); setOptionId("");
        // The form stays open for the next one: the focus stays in it (the result is announced, not focused).
        requestAnimationFrame(() => focusField("alias"));
      }
    } catch (err) {
      const refused = aliasWriteError(err, initial ? "No se pudo guardar el alias." : "No se pudo crear el alias.");
      const status = (err as { status?: number })?.status;
      if (initial && status === 404 && (!refused.field || refused.field === "alias")) {
        onGone(refused.message);
        return;
      }
      if (status === 404 || (err as { code?: string | null })?.code === "duplicate") onChangedUnderUs();
      const text = refused.kind ? `${refused.kind}: ${refused.message}` : refused.message;
      const field = refused.field === "scope" ? null : refused.field;
      setErrors(field ? { [field]: text } : { form: text });
      requestAnimationFrame(() => focusField(field));
    } finally {
      sending.current = false;
      setBusy(false);
      onBusy(false);
    }
  }

  // The options of each role; a label repeated inside a role says its attribute («Negro (Tono)»).
  const optionGroups = ROLE_ORDER.map((role) => {
    const rows = vocabulary?.options[role] ?? [];
    const seen = new Map<string, number>();
    for (const row of rows) seen.set(row.label.toLocaleLowerCase("es"), (seen.get(row.label.toLocaleLowerCase("es")) ?? 0) + 1);
    return {
      role,
      rows: rows.map((row) => {
        const twin = (seen.get(row.label.toLocaleLowerCase("es")) ?? 0) > 1;
        const attribute = attributeNames.get(row.attribute_id);
        return { id: row.id, text: twin ? `${row.label} (${attribute ?? `atributo ${row.attribute_id}`})` : row.label };
      }),
    };
  }).filter((group) => group.rows.length > 0);
  // Editing an alias whose option the vocabulary no longer lists: it stays selectable as what it is.
  const missingOption = initial?.option_id && vocabulary
    && !optionGroups.some((group) => group.rows.some((row) => row.id === initial.option_id))
    ? { id: initial.option_id, text: `${initial.option_label ?? `Opción ${initial.option_id}`}${initial.attribute_name ? ` (${initial.attribute_name})` : ""}` }
    : null;

  const describe = (field: AliasField, hint?: string) =>
    [errors[field] ? errorId(field) : null, hint].filter(Boolean).join(" ") || undefined;

  const fieldError = (field: AliasField) => errors[field] ? (
    <span id={errorId(field)} role="alert" className="mt-1 block text-xs text-danger">{errors[field]}</span>
  ) : null;

  const title = initial ? `Editar alias «${initial.alias}»` : `Nuevo alias: ${ALIAS_SCOPE_LABELS[scope].toLowerCase()}`;

  return (
    <form
      ref={formRef}
      onSubmit={submit}
      noValidate
      aria-label={title}
      className="grid min-w-0 gap-3 rounded-lg border border-bd-border bg-background p-4 sm:grid-cols-2"
    >
      <div className="min-w-0 text-xs text-muted">
        <label className="block" htmlFor={fieldId("alias")}>{ALIAS_LABELS[scope]}</label>
        <input
          id={fieldId("alias")}
          value={alias}
          onChange={(e) => setAlias(e.target.value)}
          maxLength={60}
          autoComplete="off"
          spellCheck={false}
          disabled={busy}
          aria-invalid={errors.alias ? true : undefined}
          aria-describedby={describe("alias")}
          className={`mt-1.5 ${touch} ${internalInputClass}`}
        />
        {fieldError("alias")}
      </div>

      {scope === "model_word" ? (
        <div className="min-w-0 text-xs text-muted">
          <label className="block" htmlFor={fieldId("replacement")}>Se lee como (palabra de tus modelos)</label>
          <input
            id={fieldId("replacement")}
            value={replacement}
            onChange={(e) => setReplacement(e.target.value)}
            maxLength={60}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            aria-invalid={errors.replacement ? true : undefined}
            aria-describedby={describe("replacement")}
            className={`mt-1.5 ${touch} ${internalInputClass}`}
          />
          {fieldError("replacement")}
        </div>
      ) : null}

      {scope === "model" ? (
        <div className="min-w-0 text-xs text-muted">
          <label className="block" htmlFor={fieldId("model_id")}>Modelo</label>
          <select
            id={fieldId("model_id")}
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            disabled={busy || !vocabulary}
            aria-invalid={errors.model_id ? true : undefined}
            aria-describedby={describe("model_id")}
            className={`mt-1.5 ${touch} ${internalInputClass}`}
          >
            <option value="">{vocabulary ? "Elige un modelo…" : vocabularyError ? "No se pudieron cargar los modelos" : "Cargando modelos…"}</option>
            {(vocabulary?.models ?? []).map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
            {initial?.model_id && vocabulary && !vocabulary.models.some((m) => m.id === initial.model_id) ? (
              <option value={initial.model_id}>{initial.model_name ?? `Modelo ${initial.model_id}`}</option>
            ) : null}
          </select>
          {fieldError("model_id")}
        </div>
      ) : null}

      {scope === "option" ? (
        <div className="min-w-0 text-xs text-muted">
          <label className="block" htmlFor={fieldId("option_id")}>Opción</label>
          <select
            id={fieldId("option_id")}
            value={optionId}
            onChange={(e) => setOptionId(e.target.value)}
            disabled={busy || !vocabulary}
            aria-invalid={errors.option_id ? true : undefined}
            aria-describedby={describe("option_id")}
            className={`mt-1.5 ${touch} ${internalInputClass}`}
          >
            <option value="">{vocabulary ? "Elige una opción…" : vocabularyError ? "No se pudieron cargar las opciones" : "Cargando opciones…"}</option>
            {optionGroups.map((group) => (
              <optgroup key={group.role} label={ROLE_LABELS[group.role]}>
                {group.rows.map((option) => <option key={option.id} value={option.id}>{option.text}</option>)}
              </optgroup>
            ))}
            {missingOption ? <option value={missingOption.id}>{missingOption.text}</option> : null}
          </select>
          {fieldError("option_id")}
        </div>
      ) : null}

      {vocabularyError && scope !== "model_word" ? (
        <p className="text-xs text-danger sm:col-span-2">No se pudo leer la lista de destinos: {vocabularyError}</p>
      ) : null}
      {errors.form ? (
        <p data-form-error tabIndex={-1} role="alert" className="rounded-lg border border-danger-border px-3 py-2 text-sm text-danger focus:outline-none sm:col-span-2">
          {errors.form}
        </p>
      ) : null}
      <p className="text-xs text-muted sm:col-span-2">
        El servidor revisa que no se repita ni choque con un nombre real u otro alias; si lo rechaza, lo dice junto al campo.
      </p>
      <div className="flex flex-wrap justify-end gap-2 sm:col-span-2">
        <button type="button" className={buttonClass} onClick={onCancel} disabled={busy}>Cancelar</button>
        <button type="submit" className={primaryClass} disabled={busy}>
          {busy ? "Guardando…" : initial ? "Guardar alias" : "Agregar alias"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Delete, with an explicit confirmation
// ---------------------------------------------------------------------------

function DeleteConfirm({
  alias, companyId, onDeleted, onCancel, onGone, onBusy,
}: {
  onBusy: (busy: boolean) => void;
  alias: CatalogAlias;
  companyId: number | null;
  onDeleted: () => void;
  onCancel: () => void;
  onGone: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sending = useRef(false);
  const heading = useRef<HTMLParagraphElement>(null);
  const ids = useId();
  useEffect(() => { heading.current?.focus(); }, []);

  async function confirm() {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    onBusy(true);
    setError(null);
    try {
      await deleteCatalogAlias(alias.id, companyId);
      onDeleted();
    } catch (err) {
      const refused = aliasWriteError(err, "No se pudo borrar el alias.");
      if ((err as { status?: number })?.status === 404) onGone(refused.message);
      else setError(refused.message);
    } finally {
      sending.current = false;
      setBusy(false);
      onBusy(false);
    }
  }

  return (
    <div
      role="group"
      aria-label={`Borrar alias «${alias.alias}»`}
      aria-describedby={`${ids}-que`}
      className="mt-2 space-y-2 rounded-lg border border-danger-border bg-background p-3 text-sm"
    >
      <p ref={heading} tabIndex={-1} className="font-semibold text-foreground focus:outline-none">
        ¿Borrar el alias «{alias.alias}»?
      </p>
      <p id={`${ids}-que`} className="text-xs text-muted">
        Las descripciones de proveedores que lo usan dejarán de leerse como {aliasTarget(alias)}. No cambia ningún
        modelo, opción, variante ni stock.
      </p>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className={buttonClass} onClick={onCancel} disabled={busy}>Cancelar</button>
        <button
          type="button"
          className={`${touch} ${focusRing} rounded-lg border border-danger-border px-4 py-2.5 text-sm font-bold text-danger transition hover:bg-danger-surface motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-40`}
          onClick={() => void confirm()}
          disabled={busy}
        >
          {busy ? "Borrando…" : "Sí, borrar"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// One row of the list
// ---------------------------------------------------------------------------

function AliasRow({
  alias, canManage, mode, onEdit, onDelete, onClose, form, confirm, editButton, deleteButton, held,
}: {
  /** Another alias's write is on its way: this row's buttons wait. */
  held: boolean;
  alias: CatalogAlias;
  canManage: boolean;
  mode: "view" | "edit" | "delete";
  onEdit: () => void;
  onDelete: () => void;
  onClose: () => void;
  form: React.ReactNode;
  confirm: React.ReactNode;
  editButton: (node: HTMLButtonElement | null) => void;
  deleteButton: (node: HTMLButtonElement | null) => void;
}) {
  const ids = useId();
  return (
    <li className="min-w-0 border-b border-bd-border/60 py-3 last:border-b-0" aria-label={`Alias ${alias.alias}`}>
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 flex-1 text-sm text-foreground">
          <strong className="break-words font-semibold">«{alias.alias}»</strong>{" "}
          <span className="text-muted">{TARGET_PREFIX[alias.scope]}</span>{" "}
          <span className="break-words">{aliasTarget(alias)}</span>
          {alias.shadowed ? (
            <>
              {" "}
              <span
                className="ml-1 inline-flex items-center whitespace-nowrap rounded-full border border-warning-border bg-warning-surface px-2 text-[11px] font-semibold text-warning"
                aria-describedby={`${ids}-sombra`}
              >
                Sin efecto
              </span>
              <span id={`${ids}-sombra`} className="mt-1 block text-xs text-warning">
                Hoy un nombre o una etiqueta real se lee igual: este alias no se usa. Puedes borrarlo.
              </span>
            </>
          ) : null}
        </p>
        {canManage ? (
          <span className="flex shrink-0 flex-wrap gap-1.5">
            <button
              type="button"
              ref={editButton}
              className={buttonClass}
              onClick={mode === "edit" ? onClose : onEdit}
              disabled={held}
              aria-expanded={mode === "edit"}
              aria-controls={mode === "edit" ? `${ids}-panel` : undefined}
              aria-label={`Editar alias ${alias.alias}`}
            >
              Editar
            </button>
            <button
              type="button"
              ref={deleteButton}
              className={buttonClass}
              onClick={mode === "delete" ? onClose : onDelete}
              disabled={held}
              aria-expanded={mode === "delete"}
              aria-controls={mode === "delete" ? `${ids}-panel` : undefined}
              aria-label={`Borrar alias ${alias.alias}`}
            >
              Borrar
            </button>
          </span>
        ) : null}
      </div>
      {mode !== "view" ? <div id={`${ids}-panel`} className="mt-2 min-w-0">{mode === "edit" ? form : confirm}</div> : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// «Vocabulario de proveedores»
// ---------------------------------------------------------------------------

export function CatalogAliasesSection({ canManage, companyId }: { canManage: boolean; companyId: number | null }) {
  const [aliases, setAliases] = useState<CatalogAlias[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [vocabulary, setVocabulary] = useState<CatalogVocabulary | null>(null);
  const [vocabularyError, setVocabularyError] = useState<string | null>(null);
  const [scope, setScope] = useState<AliasScope>(scopeFromHash);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<{ id: number; mode: "edit" | "delete" } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const alertRef = useRef<HTMLParagraphElement>(null);
  const focusNotice = useRef(false);
  const tabs = useRef(new Map<AliasScope, HTMLButtonElement>());
  const rowButtons = useRef(new Map<string, HTMLButtonElement>());
  const newButton = useRef<HTMLButtonElement>(null);
  const request = useRef(0);
  const vocabularyRequest = useRef(0);
  // L1 (review): one write at a time; while it is on its way nothing else opens, closes or switches.
  const [writing, setWriting] = useState(false);
  const [attributeNames, setAttributeNames] = useState<ReadonlyMap<number, string>>(new Map());
  const ids = useId();
  const createId = `${ids}-nuevo`;

  const load = useCallback(() => {
    const mine = ++request.current;
    fetchCatalogAliases({ q: applied }, companyId).then(
      (answer) => { if (mine === request.current) { setAliases(answer.results); setListError(null); } },
      (err) => { if (mine === request.current) setListError(readError(err, "No se pudo cargar el vocabulario.")); },
    );
  }, [applied, companyId]);

  // L2 (review): only the newest read lands (an older one, or another company's, is dropped).
  const loadVocabulary = useCallback(() => {
    const mine = ++vocabularyRequest.current;
    fetchCatalogVocabulary(companyId).then(
      (loaded) => { if (mine === vocabularyRequest.current) { setVocabulary(loaded); setVocabularyError(null); } },
      (err) => { if (mine === vocabularyRequest.current) setVocabularyError(readError(err, "No se pudo cargar el vocabulario.")); },
    );
  }, [companyId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadVocabulary(); }, [loadVocabulary]);
  // The attribute names, only to tell apart two options of one role with the same label (the page reads them too).
  useEffect(() => {
    let live = true;
    fetchCatalogAttributes(companyId).then(
      (rows) => { if (live) setAttributeNames(new Map(rows.map((row) => [row.id, row.name] as const))); },
      () => { /* the labels are shown without it */ },
    );
    return () => { live = false; };
  }, [companyId]);
  useEffect(() => {
    if (focusNotice.current && notice) { noticeRef.current?.focus(); focusNotice.current = false; }
  }, [notice]);
  useEffect(() => { if (alert) alertRef.current?.focus(); }, [alert]);
  // After a saved edit the list is read again; the row may be gone (it no longer matches the search). Once the
  // new list is in, the focus goes back to its row button, else to the search field — never to <body>.
  const searchRef = useRef<HTMLInputElement>(null);
  const focusAfterReload = useRef<string | null>(null);
  const restoreFocus = useCallback((key: string) => {
    const button = rowButtons.current.get(key);
    if (button && button.isConnected) button.focus();
    else searchRef.current?.focus();
  }, []);
  useEffect(() => {
    const key = focusAfterReload.current;
    if (!key || aliases === null) return;
    focusAfterReload.current = null;
    requestAnimationFrame(() => restoreFocus(key));
  }, [aliases, restoreFocus]);

  const counts = useMemo(() => {
    const out: Record<AliasScope, number> = { model_word: 0, model: 0, option: 0 };
    for (const row of aliases ?? []) out[row.scope] += 1;
    return out;
  }, [aliases]);
  const shown = useMemo(() => (aliases ?? []).filter((row) => row.scope === scope), [aliases, scope]);

  function choose(next: AliasScope, focus = false) {
    if (writing) return;
    setScope(next);
    setCreating(false);
    setOpen(null);
    if (focus) tabs.current.get(next)?.focus();
  }

  function onTabKey(event: React.KeyboardEvent<HTMLButtonElement>) {
    const index = SCOPES.indexOf(scope);
    const next = event.key === "ArrowRight" ? SCOPES[(index + 1) % SCOPES.length]
      : event.key === "ArrowLeft" ? SCOPES[(index + SCOPES.length - 1) % SCOPES.length]
        : event.key === "Home" ? SCOPES[0]
          : event.key === "End" ? SCOPES[SCOPES.length - 1] : null;
    if (!next) return;
    event.preventDefault();
    choose(next, true);
  }

  /** A result, announced (`role="status"`); it takes the focus only when the control that did it is gone. */
  function said(text: string, focus = true) {
    setAlert(null);
    setNotice(text);
    focusNotice.current = focus;
  }

  function gone(text: string) {
    setOpen(null);
    setNotice(null);
    setAlert(text);
    load();
    loadVocabulary();
  }

  /** Close THIS alias's row, only if it is still the one open (an answer may land after the user moved on). */
  function closeRow(alias: CatalogAlias, mode: "edit" | "delete", reloading = false) {
    setOpen((current) => (current?.id === alias.id ? null : current));
    const key = `${mode}-${alias.id}`;
    // The list is read again: the focus is settled once it is in (see `focusAfterReload`).
    if (reloading) focusAfterReload.current = key;
    // After the render that releases the held controls (a disabled button cannot take the focus).
    requestAnimationFrame(() => restoreFocus(key));
  }

  function search(event: React.FormEvent) {
    event.preventDefault();
    if (writing) return;
    setOpen(null);
    setApplied(query.trim());
  }

  const groups: { role: AttributeRole | null; rows: CatalogAlias[] }[] = scope === "option"
    ? ROLE_ORDER.map((role) => ({ role, rows: shown.filter((row) => (row.role ?? "other") === role) }))
      .filter((group) => group.rows.length > 0)
    : [{ role: null, rows: shown }];

  const renderRow = (alias: CatalogAlias) => (
    <AliasRow
      key={alias.id}
      alias={alias}
      canManage={canManage}
      mode={open?.id === alias.id ? open.mode : "view"}
      held={writing}
      onEdit={() => { setCreating(false); setNotice(null); setOpen({ id: alias.id, mode: "edit" }); }}
      onDelete={() => { setCreating(false); setNotice(null); setOpen({ id: alias.id, mode: "delete" }); }}
      onClose={() => setOpen(null)}
      editButton={(node) => { if (node) rowButtons.current.set(`edit-${alias.id}`, node); else rowButtons.current.delete(`edit-${alias.id}`); }}
      deleteButton={(node) => { if (node) rowButtons.current.set(`delete-${alias.id}`, node); else rowButtons.current.delete(`delete-${alias.id}`); }}
      form={(
        <AliasForm
          scope={alias.scope}
          initial={alias}
          vocabulary={vocabulary}
          vocabularyError={vocabularyError}
          companyId={companyId}
          onSaved={(saved) => {
            closeRow(alias, "edit", true);
            said(`Alias «${saved?.alias ?? alias.alias}» guardado.`, false);
            load();
            loadVocabulary();
          }}
          onCancel={() => closeRow(alias, "edit")}
          onGone={gone}
          onChangedUnderUs={() => { load(); loadVocabulary(); }}
          onBusy={setWriting}
          attributeNames={attributeNames}
        />
      )}
      confirm={(
        <DeleteConfirm
          alias={alias}
          companyId={companyId}
          onDeleted={() => {
            setOpen((current) => (current?.id === alias.id ? null : current));
            said(`Se borró el alias «${alias.alias}».`);
            load();
            loadVocabulary();
          }}
          onCancel={() => closeRow(alias, "delete")}
          onGone={gone}
          onBusy={setWriting}
        />
      )}
    />
  );

  return (
    <Panel
      id="vocabulario"
      title="Vocabulario de proveedores"
      description="Cómo escriben tus proveedores tus modelos y opciones. Un alias sólo ayuda a leer sus listas: nunca crea modelos, opciones, variantes ni stock, y nunca convierte un importe en precio."
    >
      <div className="min-w-0 space-y-4">
        {notice ? (
          <p ref={noticeRef} tabIndex={-1} role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success focus:outline-none">
            {notice}
          </p>
        ) : null}
        {alert ? (
          <p ref={alertRef} tabIndex={-1} role="alert" className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning focus:outline-none">
            {alert}
          </p>
        ) : null}

        <div role="tablist" aria-label="Tipos de alias" className="flex min-w-0 flex-wrap gap-2">
          {SCOPES.map((item) => (
            <button
              key={item}
              id={SCOPE_ANCHORS[item]}
              ref={(node) => { if (node) tabs.current.set(item, node); else tabs.current.delete(item); }}
              type="button"
              role="tab"
              aria-selected={scope === item}
              aria-controls={scope === item ? `${ids}-panel-${item}` : undefined}
              tabIndex={scope === item ? 0 : -1}
              disabled={writing && scope !== item}
              onClick={() => choose(item)}
              onKeyDown={onTabKey}
              className={`${touch} ${focusRing} scroll-mt-24 rounded-lg border px-3 text-sm font-semibold transition-colors motion-reduce:transition-none ${
                scope === item ? "border-foreground/40 bg-foreground/[0.06] text-foreground" : "border-bd-border text-muted hover:text-foreground"
              }`}
            >
              {ALIAS_SCOPE_LABELS[item]}
              {aliases ? <span className="ml-1.5 tabular-nums text-xs font-normal">({counts[item]})</span> : null}
            </button>
          ))}
        </div>

        <form role="search" onSubmit={search} className="flex min-w-0 flex-wrap items-end gap-2" aria-label="Buscar en el vocabulario">
          <label className="block min-w-0 flex-1 basis-48 text-xs text-muted" htmlFor={`${ids}-q`}>
            Buscar alias, modelo, opción o palabra
            <input
              id={`${ids}-q`}
              ref={searchRef}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={60}
              autoComplete="off"
              className={`mt-1.5 ${touch} ${internalInputClass}`}
            />
          </label>
          <button type="submit" className={buttonClass} disabled={writing}>Buscar</button>
          {applied ? (
            <button type="button" className={buttonClass} onClick={() => { setQuery(""); setApplied(""); }} disabled={writing}>Ver todos</button>
          ) : null}
        </form>

        <div
          id={`${ids}-panel-${scope}`}
          role="tabpanel"
          aria-labelledby={SCOPE_ANCHORS[scope]}
          tabIndex={0}
          className={`min-w-0 space-y-4 rounded-lg ${focusRing}`}
        >
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
            <p className="min-w-0 flex-1 basis-60 text-xs text-muted">{ALIAS_SCOPE_HELP[scope]}</p>
            {canManage ? (
              <button
                type="button"
                ref={newButton}
                className={buttonClass}
                onClick={() => { setOpen(null); setNotice(null); setCreating((value) => !value); }}
                disabled={writing}
                aria-expanded={creating}
                aria-controls={creating ? createId : undefined}
              >
                + Agregar alias
              </button>
            ) : null}
          </div>

          {canManage && creating ? (
            <div id={createId}>
              <AliasForm
                key={scope}
                scope={scope}
                vocabulary={vocabulary}
                vocabularyError={vocabularyError}
                companyId={companyId}
                onSaved={(saved) => { said(`Alias «${saved?.alias ?? ""}» agregado.`, false); load(); loadVocabulary(); }}
                onCancel={() => { setCreating(false); requestAnimationFrame(() => newButton.current?.focus()); }}
                onGone={gone}
                onChangedUnderUs={() => { load(); loadVocabulary(); }}
                onBusy={setWriting}
                attributeNames={attributeNames}
              />
            </div>
          ) : null}

          {listError ? (
            <div className="space-y-2">
              <ErrorBox message={listError} />
              <button type="button" className={buttonClass} onClick={load}>Volver a cargar</button>
            </div>
          ) : null}
          {aliases === null && !listError ? <Spinner label="Cargando el vocabulario…" /> : null}
          {aliases && shown.length === 0 ? (
            <EmptyBox message={applied
              ? `Ningún alias de este tipo coincide con «${applied}».`
              : canManage ? "Todavía no hay alias de este tipo. Agrega el primero." : "Todavía no hay alias de este tipo."}
            />
          ) : null}
          {aliases && shown.length > 0 ? groups.map((group) => (
            <section key={group.role ?? "all"} aria-label={group.role ? ROLE_LABELS[group.role] : ALIAS_SCOPE_LABELS[scope]} className="min-w-0">
              {group.role ? <h3 className="text-xs font-semibold uppercase tracking-widest text-muted">{ROLE_LABELS[group.role]}</h3> : null}
              <ul className="min-w-0">{group.rows.map(renderRow)}</ul>
            </section>
          )) : null}
        </div>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// «Probar una descripción»
// ---------------------------------------------------------------------------

const STATUS_TONE: Record<ResolverStatus, string> = {
  variante_existente: "border-success-border text-success",
  combinacion_valida_sin_variante: "border-bd-border text-foreground",
  sin_paleta: "border-bd-border text-muted",
  incompleta: "border-warning-border bg-warning-surface text-warning",
  fuera_de_paleta: "border-warning-border bg-warning-surface text-warning",
  opcion_desconocida: "border-warning-border bg-warning-surface text-warning",
  nombre_con_opciones: "border-warning-border bg-warning-surface text-warning",
  ambigua: "border-warning-border bg-warning-surface text-warning",
  modelo_desconocido: "border-danger-border bg-danger-surface text-danger",
};

export function ResolverStatusBadge({ status }: { status: ResolverStatus }) {
  return (
    <span className={`inline-flex min-h-6 items-center whitespace-nowrap rounded-full border px-2 text-[11px] font-semibold ${STATUS_TONE[status] ?? "border-bd-border text-muted"}`}>
      {RESOLVER_STATUS_LABELS[status] ?? status}
    </span>
  );
}

const readLine = (read: ResolveOptionRead) => {
  const how = viaLabel(read.via);
  return (
    <>
      <span className="text-muted">{ROLE_LABELS[read.role] ?? read.role}:</span>{" "}
      «{read.text}» → <strong className="font-semibold">{read.label}</strong>
      {how ? <span className="text-muted"> ({how})</span> : null}
    </>
  );
};

function DoubtView({ doubt }: { doubt: ResolveDoubt }) {
  return (
    <li className="min-w-0 rounded-lg border border-warning-border bg-background p-3">
      <p className="text-sm text-foreground">
        <span className="text-muted">{doubtRoleLabel(doubt.role)}:</span> «<span className="break-words">{doubt.text}</span>»{" "}
        <span className="whitespace-nowrap rounded-full border border-warning-border px-2 text-[11px] font-semibold text-warning">
          {DOUBT_REASON_LABELS[doubt.reason] ?? doubt.reason}
        </span>
      </p>
      <p className="mt-1 text-xs text-muted">{DOUBT_REASON_HELP[doubt.reason] ?? ""}</p>
      {doubt.readings.length > 0 ? (
        <div className="mt-2">
          <p className="text-xs font-semibold text-foreground">Lecturas posibles</p>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs text-foreground">
            {doubt.readings.map((reading, index) => (
              <li key={index} className="break-words">
                {reading.map((part, at) => (
                  <span key={at}>
                    {at > 0 ? " + " : ""}«{part.text}» → {part.label ? <strong className="font-semibold">{part.label}</strong> : <em className="text-muted">sin opción conocida</em>}
                  </span>
                ))}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {doubt.actions.length > 0 ? (
        <div className="mt-2">
          <p className="text-xs font-semibold text-foreground">Qué puede hacer una persona (no se hace desde aquí)</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-muted">
            {doubt.actions.map((action) => <li key={action}>{DOUBT_ACTION_LABELS[action] ?? action}</li>)}
          </ul>
        </div>
      ) : null}
    </li>
  );
}

function ConfigurationView({ configuration, index, total }: { configuration: ResolveConfiguration; index: number; total: number }) {
  const label = `Configuración ${index + 1} de ${total}: ${RESOLVER_STATUS_LABELS[configuration.status] ?? configuration.status}`;
  return (
    <article aria-label={label} className="min-w-0 space-y-3 rounded-lg border border-bd-border bg-background p-4">
      <header className="flex min-w-0 flex-wrap items-center gap-2">
        <ResolverStatusBadge status={configuration.status} />
        {configuration.colour_text ? <span className="break-words text-xs text-muted">Color dicho: «{configuration.colour_text}»</span> : null}
      </header>
      <p className="text-xs text-muted">{RESOLVER_STATUS_HELP[configuration.status] ?? ""}</p>

      {configuration.product_id ? (
        <p className="text-sm">
          <span className="text-muted">Variante: </span>
          <Link href={`/admin/products/${configuration.product_id}`} className={`break-words underline underline-offset-4 ${focusRing}`}>
            {configuration.product_name ?? `Producto ${configuration.product_id}`}
          </Link>
          {configuration.draft_pending ? <span className="ml-1 text-xs text-muted">(borrador pendiente)</span> : null}
        </p>
      ) : null}

      <div>
        <p className="text-xs font-semibold text-foreground">Opciones leídas</p>
        {configuration.options.length > 0 ? (
          <ul className="mt-1 space-y-0.5 text-sm text-foreground">
            {configuration.options.map((read) => <li key={`${read.role}-${read.option_id}`} className="break-words">{readLine(read)}</li>)}
          </ul>
        ) : <p className="mt-1 text-xs text-muted">Ninguna.</p>}
      </div>

      {configuration.missing.length > 0 ? (
        <div>
          <p className="text-xs font-semibold text-foreground">Falta decir</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {configuration.missing.map((name) => (
              <li key={name} className="rounded-full border border-warning-border px-2 text-xs text-warning">{missingLabel(name)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {configuration.doubts.length > 0 ? (
        <div>
          <p className="text-xs font-semibold text-foreground">Dudas ({configuration.doubts.length})</p>
          <ul className="mt-1 space-y-2">
            {configuration.doubts.map((doubt, at) => <DoubtView key={`${doubt.role}-${doubt.reason}-${at}`} doubt={doubt} />)}
          </ul>
        </div>
      ) : null}

      {configuration.candidates.length > 0 ? (
        <div>
          <p className="text-xs font-semibold text-foreground">Variantes que encajan en parte (sólo información; ninguna se elige)</p>
          <ul className="mt-1 space-y-0.5 text-sm">
            {configuration.candidates.map((candidate) => (
              <li key={candidate.product_id} className="break-words">
                <Link href={`/admin/products/${candidate.product_id}`} className={`underline underline-offset-4 ${focusRing}`}>{candidate.name}</Link>
                {candidate.draft_pending ? <span className="ml-1 text-xs text-muted">(borrador pendiente)</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

function ResultView({ row, summary, lineLabels }: {
  row: ResolveRowResult; summary: ResolveAnswer["summary"]; lineLabels: Record<CatalogLine, string>;
}) {
  const model = row.model;
  const counted = RESOLVER_STATUS_ORDER.filter((status) => (summary[status] ?? 0) > 0);
  return (
    <div className="min-w-0 space-y-4">
      {row.error ? (
        <p role="alert" className="rounded-lg border border-danger-border px-4 py-3 text-sm text-danger">{row.error}</p>
      ) : null}
      {model ? (
        <dl className="grid min-w-0 gap-x-4 gap-y-2 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]">
          <dt className="text-xs text-muted">Modelo escrito</dt>
          <dd className="break-words text-foreground">«{model.text}»</dd>
          <dt className="text-xs text-muted">Cómo se leyó</dt>
          <dd className="text-foreground">
            {MODEL_READ_LABELS[model.status] ?? model.status}
            {model.model_name ? <> → <strong className="font-semibold">{model.model_name}</strong></> : null}
          </dd>
          {model.candidates.length > 0 ? (
            <>
              <dt className="text-xs text-muted">Modelos posibles</dt>
              <dd className="break-words text-foreground">{model.candidates.map((c) => c.name).join(" · ")}</dd>
            </>
          ) : null}
          {model.extracted.length > 0 ? (
            <>
              <dt className="text-xs text-muted">Opciones dentro del nombre</dt>
              <dd className="min-w-0">
                <ul className="space-y-0.5">{model.extracted.map((read) => <li key={`${read.role}-${read.option_id}`} className="break-words">{readLine(read)}</li>)}</ul>
              </dd>
            </>
          ) : null}
          {row.line ? (
            <>
              <dt className="text-xs text-muted">Línea</dt>
              <dd className="text-foreground">{lineLabels[row.line] ?? row.line}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
      {counted.length > 0 ? (
        <p className="text-xs text-muted" aria-label="Resumen de la lectura">
          {counted.map((status) => `${summary[status]} ${RESOLVER_STATUS_LABELS[status].toLowerCase()}`).join(" · ")}
        </p>
      ) : null}
      {row.configurations.length > 0 ? (
        <div className="space-y-3">
          {row.configurations.map((configuration, index) => (
            <ConfigurationView key={index} configuration={configuration} index={index} total={row.configurations.length} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

type TryFields = { model_text: string; line: string; capacity_text: string; colour_text: string; sim_text: string; region_text: string };
const EMPTY_TRY: TryFields = { model_text: "", line: "", capacity_text: "", colour_text: "", sim_text: "", region_text: "" };

/** The fields the resolver takes, with the server's lengths. Nothing else travels (no amount, quantity or IMEI). */
const TRY_INPUTS: { name: Exclude<keyof TryFields, "line" | "model_text">; label: string; max: number }[] = [
  { name: "capacity_text", label: "Capacidad", max: 60 },
  { name: "colour_text", label: "Color (uno o varios)", max: 120 },
  { name: "sim_text", label: "SIM", max: 60 },
  { name: "region_text", label: "Región", max: 60 },
];

export function ResolverTryPanel({ companyId }: { companyId: number | null }) {
  const ids = useId();
  const [fields, setFields] = useState<TryFields>(EMPTY_TRY);
  const [modelError, setModelError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<{ row: ResolveRowResult; summary: ResolveAnswer["summary"] } | null>(null);
  const [legendOpen, setLegendOpen] = useState(false);
  // L5 (review): the company's own names for the lines; the platform's words only when they cannot be read.
  const [lineLabels, setLineLabels] = useState<Record<CatalogLine, string>>(DEFAULT_LINE_LABELS);
  useEffect(() => {
    let live = true;
    fetchCatalogSettings(companyId).then(
      (settings) => {
        if (!live) return;
        setLineLabels({
          new: settings?.new_line_label?.trim() || DEFAULT_LINE_LABELS.new,
          used: settings?.used_line_label?.trim() || DEFAULT_LINE_LABELS.used,
        });
      },
      () => { /* the platform's words stay */ },
    );
    return () => { live = false; };
  }, [companyId]);
  const sending = useRef(false);
  const resultRef = useRef<HTMLHeadingElement>(null);
  const focusResult = useRef(false);
  useEffect(() => {
    if (focusResult.current && (answer || error)) { resultRef.current?.focus(); focusResult.current = false; }
  }, [answer, error]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    if (!fields.model_text.trim()) {
      setModelError("Escribe el modelo como lo escribe el proveedor.");
      document.getElementById(`${ids}-model_text`)?.focus();
      return;
    }
    setModelError(null);
    const row: ResolveRowInput = { key: "prueba", model_text: fields.model_text.trim() };
    if (fields.line) row.line = fields.line;
    for (const input of TRY_INPUTS) if (fields[input.name].trim()) row[input.name] = fields[input.name].trim();
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await resolveCatalogRows([row], companyId);
      const mine = result.rows.find((r) => r.key === "prueba") ?? result.rows[0];
      if (!mine) throw new Error("empty");
      setAnswer({ row: mine, summary: result.summary });
    } catch (err) {
      setAnswer(null);
      setError(readError(err, "No se pudo leer la descripción."));
    } finally {
      focusResult.current = true;
      sending.current = false;
      setBusy(false);
    }
  }

  const set = (name: keyof TryFields) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setFields((current) => ({ ...current, [name]: event.target.value }));

  return (
    <Panel
      id="probar-descripcion"
      title="Probar una descripción"
      description="Escribe una línea como la manda un proveedor y mira cómo la lee el catálogo de la empresa. Sólo lee: no crea ni cambia nada."
    >
      <div className="min-w-0 space-y-5">
        <form onSubmit={submit} noValidate aria-label="Probar una descripción" className="grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div className="min-w-0 text-xs text-muted sm:col-span-2">
            <label className="block" htmlFor={`${ids}-model_text`}>Modelo</label>
            <input
              id={`${ids}-model_text`}
              value={fields.model_text}
              onChange={set("model_text")}
              maxLength={160}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={modelError ? true : undefined}
              aria-describedby={modelError ? `${ids}-model_text-error` : undefined}
              className={`mt-1.5 ${touch} ${internalInputClass}`}
            />
            {modelError ? <span id={`${ids}-model_text-error`} role="alert" className="mt-1 block text-xs text-danger">{modelError}</span> : null}
          </div>
          <label className="block min-w-0 text-xs text-muted" htmlFor={`${ids}-line`}>
            Línea
            <select id={`${ids}-line`} value={fields.line} onChange={set("line")} className={`mt-1.5 ${touch} ${internalInputClass}`}>
              <option value="">Sin indicar ({lineLabels.new})</option>
              <option value="new">{lineLabels.new}</option>
              <option value="used">{lineLabels.used}</option>
            </select>
          </label>
          {TRY_INPUTS.map((input) => (
            <label key={input.name} className="block min-w-0 text-xs text-muted" htmlFor={`${ids}-${input.name}`}>
              {input.label}
              <input
                id={`${ids}-${input.name}`}
                value={fields[input.name]}
                onChange={set(input.name)}
                maxLength={input.max}
                autoComplete="off"
                spellCheck={false}
                className={`mt-1.5 ${touch} ${internalInputClass}`}
              />
            </label>
          ))}
          <div className="flex flex-wrap items-end justify-end gap-2 sm:col-span-2 lg:col-span-3">
            <button
              type="button"
              className={buttonClass}
              onClick={() => { setFields(EMPTY_TRY); setAnswer(null); setError(null); setModelError(null); }}
              disabled={busy}
            >
              Limpiar
            </button>
            <button type="submit" className={primaryClass} disabled={busy}>{busy ? "Leyendo…" : "Probar"}</button>
          </div>
        </form>

        <div>
          <button
            type="button"
            className={linkButtonClass}
            aria-expanded={legendOpen}
            aria-controls={legendOpen ? `${ids}-leyenda` : undefined}
            onClick={() => setLegendOpen((value) => !value)}
          >
            ¿Qué significa cada estado?
          </button>
          {legendOpen ? (
            <dl id={`${ids}-leyenda`} className="mt-2 grid min-w-0 gap-x-4 gap-y-2 text-xs sm:grid-cols-[max-content_minmax(0,1fr)]">
              {RESOLVER_STATUS_ORDER.map((status) => (
                <div key={status} className="contents">
                  <dt><ResolverStatusBadge status={status} /></dt>
                  <dd className="text-muted">{RESOLVER_STATUS_HELP[status]}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </div>

        {answer || error ? (
          <section aria-labelledby={`${ids}-resultado`} className="min-w-0 space-y-3 border-t border-bd-border pt-4">
            <h3 id={`${ids}-resultado`} ref={resultRef} tabIndex={-1} className="text-sm font-semibold text-foreground focus:outline-none">
              Resultado
            </h3>
            {error ? <ErrorBox message={error} /> : null}
            {answer ? <ResultView row={answer.row} summary={answer.summary} lineLabels={lineLabels} /> : null}
          </section>
        ) : null}
      </div>
    </Panel>
  );
}
