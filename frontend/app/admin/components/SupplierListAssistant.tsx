"use client";

/**
 * Productos › Proveedores › Ofertas › «Actualizar lista de proveedor» — CATALOG-COMMERCIAL-01 · B1-1.
 *
 * A supplier's WhatsApp price list becomes DRAFTS of supplier offers, one line = one offer:
 *
 *   paste → read (`supplier-list-grammar`, the company's vocabulary only) → the server resolves each line
 *   (`POST /admin/catalog/resolve/`, R-COL, statuses, doubts) → a person classifies every amount, types the
 *   commercial price and selects the clean configurations → «Vista previa de lo que se guardará» → the dry run of
 *   the official importer (`/text-preview/offer-draft/`) → the official importer itself (preview and apply, with
 *   every B0 control: message date, content already applied, same rows, reactivations, cleared quantities).
 *
 * Owner's decisions of 2026-11-14 (D-B1-1 to D-B1-4) are the rules of `supplier-list-plan`. Nothing here classifies an
 * amount, fills a price, confirms a quantity, executes a suggested action or saves a doubt. «Crear variante» (a
 * pending draft, never sellable) and «Es un alias de…» are separate actions, each confirmed, each `products.manage`.
 *
 * PRIVACY: the pasted text lives only in this component's memory — no storage, no URL, no log, no error message.
 * Personal data and identifiers are hidden before reading and never reach any request.
 *
 * Reading needs `products.view` (the page's guard); saving and the actions need `products.manage`; a cost needs
 * `costs.view`. The server checks each one again: this screen only avoids offering what it would refuse.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CatalogApiError, createCatalogAlias, createModelVariant, fetchCatalogVocabulary, fetchCompanyConfiguration, fetchSuppliers,
  resolveCatalogRows,
  type AttributeRole, type CatalogAliasWrite, type CatalogVocabulary, type ResolveDoubt,
  type ResolveRowResult, type SupplierRow,
} from "../lib/internal-api";
import {
  DOUBT_ACTION_LABELS, DOUBT_REASON_HELP, DOUBT_REASON_LABELS, MODEL_READ_LABELS, aliasWriteError, doubtRoleLabel,
  missingLabel, readError, viaLabel,
} from "../lib/catalog-vocabulary";
import { RESOLVER_STATUS_HELP, RESOLVER_STATUS_LABELS, RESOLVER_STATUS_ORDER, ROLE_LABELS, ROLE_ORDER } from "../lib/palette";
import { offerDraftFile, requestOfferDraft, type OfferDraftAnswer, type OfferDraftAnswerRow, type OfferDraftBody } from "../lib/inventory-text-draft";
import {
  MAX_LIST_CHARS, readSupplierList, resolvePayload, supplierLexicon,
  type LineCorrection, type SupplierAmount, type SupplierLine, type SupplierListReading,
} from "../lib/supplier-list-grammar";
import { correctionsAgainst, sharedShare, withCorrections } from "../lib/supplier-list-corrections";
import {
  AMOUNT_CLASS_LABELS, AMOUNT_LABEL_HINT, AMOUNT_PROBLEM_TEXT, LIST_CURRENCY_CODES, amountChoices, amountCurrency,
  amountVisible, configurationEntries, draftBody, draftRows, dryRunByKey, entryIssues, lineCost, lineReferences,
  messageDateIssue, mayCreateVariant, repeatedIn, serverToday, COLOUR_DOUBT_TEXT,
  type AmountClass, type AmountContext, type AmountDecision, type ConfigEntry, type EntryInputs,
} from "../lib/supplier-list-plan";
import { ResolverStatusBadge } from "./CatalogVocabulary";
import { SupplierOfferImport, type OfferImportHandoff } from "./SupplierOffersManager";
import { ErrorBox, Panel } from "./InventoryUi";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const touch = "min-h-[44px]";
const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2";
const buttonClass = `${touch} ${focusRing} ${internalButtonClass}`;
const primaryClass = `${touch} ${focusRing} ${internalPrimaryButtonClass}`;
const smallInput = `${internalInputClass} ${touch}`;

const HIDDEN_TEXT: Record<string, string> = {
  contact: "datos de contacto", phone: "un teléfono", imei: "un IMEI", serial: "una serie", number: "un número largo",
};

type Props = {
  /** `products.manage`: save offers, create a variant, register an alias. */
  canManage: boolean;
  /** `costs.view`: see a supplier's amount and classify it as the cost. */
  canSeeCosts: boolean;
  /** `company.view`: read the company's currency (the suggestion for the list's). */
  canViewCompany: boolean;
  companyId: number | null;
};

/** Pages of active suppliers read at most (25 each): beyond it the screen says it is incomplete. */
const SUPPLIER_PAGES = 20;

/** A refused «Crear variante», in its own words (review M3): never the alias texts. */
function variantWriteError(error: unknown): string {
  if (error instanceof CatalogApiError) {
    if (error.status === 403) return "No tienes permiso para crear variantes (hace falta gestionar productos).";
    if (error.status === 404) return "El modelo o una de sus opciones ya no existe en esta empresa. Vuelve a leer la lista.";
    if (error.status === 409 || error.code === "duplicate") return error.message || "Esa variante ya existe: vuelve a leer la lista.";
    if (error.status === 429) return "Demasiados cambios seguidos. Espera un momento y vuelve a intentarlo.";
    return error.message || "No se pudo crear la variante.";
  }
  if (error instanceof TypeError) return "No se pudo conectar con el servidor. Revisa tu conexión y vuelve a intentarlo.";
  return "No se pudo crear la variante.";
}

type Pending =
  | { kind: "variant"; key: string }
  | { kind: "alias"; id: string; text: string; role: AttributeRole | "model" | null };

/**
 * The screen as the page mounts it: a change of company is a NEW screen — the reading, the text, the supplier and
 * every decision belong to the company they were made in, and none of them is carried to another one.
 */
export function SupplierListAssistantScreen(props: Props) {
  return <SupplierListAssistant key={props.companyId ?? "none"} {...props} />;
}

export function SupplierListAssistant({ canManage, canSeeCosts, canViewCompany, companyId }: Props) {
  // ── What the company knows ──────────────────────────────────────────────────────────────────────────────────
  const [vocabulary, setVocabulary] = useState<CatalogVocabulary | null>(null);
  const [vocabularyError, setVocabularyError] = useState("");
  const [suppliers, setSuppliers] = useState<SupplierRow[] | null>(null);
  const [suppliersError, setSuppliersError] = useState("");
  /** undefined: not read yet; null: it could not be read. */
  const [companyCurrency, setCompanyCurrency] = useState<string | null | undefined>(canViewCompany ? undefined : null);

  // ── The list ────────────────────────────────────────────────────────────────────────────────────────────────
  const [supplierId, setSupplierId] = useState("");
  const [messageDate, setMessageDate] = useState("");
  const [listCurrency, setListCurrency] = useState("");
  const [leadTimeDays, setLeadTimeDays] = useState("");
  const [text, setText] = useState("");
  const [reading, setReading] = useState<SupplierListReading | null>(null);
  const [results, setResults] = useState<Record<string, ResolveRowResult>>({});
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState("");

  // ── What the person decided ─────────────────────────────────────────────────────────────────────────────────
  const [decisions, setDecisions] = useState<Record<string, AmountDecision | undefined>>({});
  const [prices, setPrices] = useState<Record<string, string | undefined>>({});
  const [quantities, setQuantities] = useState<Record<string, string | undefined>>({});
  const [ignoredWords, setIgnoredWords] = useState<Record<string, boolean | undefined>>({});
  const [selected, setSelected] = useState<Record<string, boolean | undefined>>({});
  /**
   * The list as it was PASTED (memory only): what the supplier sent. Every reading of an edited text is compared with
   * it (owner's rule on compound colours), edits made before the first reading included. Only «Empezar otra lista»
   * forgets it: anything pasted or typed afterwards is a correction of it. A typed list (no paste) is received at its
   * first reading.
   */
  const [received, setReceived] = useState<string | null>(null);
  const [corrections, setCorrections] = useState<Record<string, LineCorrection>>({});
  /** Every new reading (or edit of the text) has its own number: an answer for an older one is dropped. */
  const readingRun = useRef(0);
  const readingNow = useRef<SupplierListReading | null>(null);
  const textBox = useRef<HTMLTextAreaElement>(null);

  // ── Actions and saving ──────────────────────────────────────────────────────────────────────────────────────
  const [pending, setPending] = useState<Pending | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  /** A write succeeded but the reading after it failed: the screen says so and offers to read again (review M3). */
  const [staleReading, setStaleReading] = useState("");
  /** Configurations a variant was just created for: «Crear variante» is not offered again before a fresh reading. */
  const [created, setCreated] = useState<Record<string, boolean | undefined>>({});
  /** The text as it is NOW (a refresh that answers for an older text is discarded). */
  const textNow = useRef("");
  const [dryRun, setDryRun] = useState<{ body: OfferDraftBody; answer: OfferDraftAnswer; byKey: Record<string, OfferDraftAnswerRow> } | null>(null);
  const [dryRunning, setDryRunning] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [handoff, setHandoff] = useState<OfferImportHandoff | null>(null);
  const [preparing, setPreparing] = useState(false);
  const run = useRef(0);
  const importerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let alive = true;
    fetchCatalogVocabulary(companyId)
      .then((data) => { if (alive) setVocabulary(data); })
      .catch((error) => { if (alive) setVocabularyError(readError(error, "No se pudo cargar el vocabulario del catálogo.")); });
    if (canManage) {
      // Every page of active suppliers (review M5), as «Recibir mercadería» reads them.
      void (async () => {
        try {
          const all: SupplierRow[] = [];
          let count = 0;
          for (let page = 1; page <= SUPPLIER_PAGES; page += 1) {
            const answer = await fetchSuppliers({ active: "true", page }, companyId);
            if (!alive) return;
            all.push(...answer.results);
            count = answer.count;
            if (!answer.results.length || all.length >= answer.count) break;
          }
          if (!alive) return;
          setSuppliers(all);
          if (count > all.length) {
            setSuppliersError(`Se muestran los primeros ${all.length} de ${count} proveedores activos. Si no ves el tuyo, búscalo en «Proveedores».`);
          }
        } catch {
          if (alive) { setSuppliers([]); setSuppliersError("No se pudieron cargar los proveedores."); }
        }
      })();
    }
    if (canViewCompany) {
      fetchCompanyConfiguration(companyId)
        .then((data) => { if (alive) setCompanyCurrency(data.settings?.currency || null); })
        .catch(() => { if (alive) setCompanyCurrency(null); });
    }
    return () => { alive = false; };
  }, [companyId, canManage, canViewCompany]);

  useEffect(() => { if (handoff) importerRef.current?.focus(); }, [handoff]);

  /** Any change to what would be sent makes the dry run and the importer stale: both are dropped. */
  const dropDraft = useCallback(() => {
    run.current += 1;
    setDryRun(null);
    setDryRunning(false);
    setHandoff(null);
    setSaveError("");
  }, []);

  const ctx: AmountContext = { canSeeCosts, canViewCompany, companyCurrency: companyCurrency ?? null, listCurrency };
  const inputs: EntryInputs = { decisions, prices, quantities, ignoredWords };
  useEffect(() => { readingNow.current = reading; }, [reading]);
  const lines = useMemo(() => (reading ? withCorrections(reading.lines, corrections) : []), [reading, corrections]);
  const entries = useMemo(() => configurationEntries(lines, results), [lines, results]);
  const issuesOf = (entry: ConfigEntry) => entryIssues(entry, inputs, ctx);
  // The selection, in list order; a variant already chosen earlier in the list blocks a later one.
  const chosen: ConfigEntry[] = [];
  for (const entry of entries) {
    if (!canManage || !selected[entry.key] || issuesOf(entry).length) continue;
    if (repeatedIn(entry, chosen)) continue;
    chosen.push(entry);
  }
  const dateIssue = messageDateIssue(messageDate);

  // ── Reading ─────────────────────────────────────────────────────────────────────────────────────────────────

  async function resolve(lines: SupplierLine[], keep: Record<string, ResolveRowResult>) {
    const rows = resolvePayload(lines);
    if (!rows.length) return keep;
    const answer = await resolveCatalogRows(rows, companyId);
    const next = { ...keep };
    for (const row of answer.rows) next[row.key] = row;
    return next;
  }

  async function readList() {
    if (!vocabulary || !text.trim()) return;
    const asked = text;
    const run = ++readingRun.current;
    dropDraft();
    setResolveError("");
    setNotice("");
    setResolving(true);
    try {
      // The palette or the aliases may have changed in another tab: every reading uses the catalogue as it is now.
      let source = vocabulary;
      try {
        source = await fetchCatalogVocabulary(companyId);
        setVocabulary(source);
      } catch {
        /* keep the vocabulary we have: the resolve below is still the server's answer */
      }
      if (run !== readingRun.current || asked !== textNow.current) return;
      // A dry run started while the catalogue was being read belongs to the old reading.
      dropDraft();
      let parsed: SupplierListReading;
      let corrected: Record<string, LineCorrection>;
      try {
        const lexicon = supplierLexicon(source);
        parsed = readSupplierList(asked, lexicon);
        corrected = received === null ? {} : correctionsAgainst(received, asked, lexicon);
      } catch (error) {
        setResolveError(error instanceof Error ? error.message : "No se pudo leer la lista.");
        return;
      }
      if (received === null) setReceived(asked);
      setReading(parsed);
      setCorrections(corrected);
      setResults({});
      setDecisions({}); setPrices({}); setQuantities({}); setIgnoredWords({}); setSelected({}); setPending(null);
      try {
        const answer = await resolve(parsed.lines, {});
        if (run === readingRun.current) setResults(answer);
      } catch (error) {
        if (run === readingRun.current) setResolveError(readError(error, "No se pudo consultar el catálogo."));
      }
    } finally {
      setResolving(false);
    }
  }

  /**
   * After a variant or an alias was created: the vocabulary is read again, the SAME text is read again and resolved.
   * What a person decided stays where the line still says the same; a configuration that changed loses its inputs.
   */
  async function refresh(): Promise<boolean> {
    const asked = textNow.current;
    let lexiconSource = vocabulary;
    try {
      lexiconSource = await fetchCatalogVocabulary(companyId);
      setVocabulary(lexiconSource);
    } catch {
      /* keep the vocabulary we have: the resolve below is still the server's answer */
    }
    if (!lexiconSource || !asked.trim()) return true;
    const lexicon = supplierLexicon(lexiconSource);
    const parsed = readSupplierList(asked, lexicon);
    // The received text is read with the NEW vocabulary too. A correction that cut a received colour stays blocked
    // even when that colour is now in the palette: the supplier offered the whole colour.
    const corrected = received === null ? {} : correctionsAgainst(received, asked, lexicon);
    let fresh: Record<string, ResolveRowResult>;
    try {
      fresh = await resolve(parsed.lines, {});
    } catch {
      return false;
    }
    // The text was edited meanwhile: this answer is for an older text and is dropped.
    if (asked !== textNow.current) return true;
    const changed = (key: string) => {
      const before = results[key]?.configurations.map((c) => c.colour_text).join("|");
      const after = fresh[key]?.configurations.map((c) => c.colour_text).join("|");
      return before !== after;
    };
    const keepFor = <T,>(all: Record<string, T>) => Object.fromEntries(
      Object.entries(all).filter(([key]) => !changed(key.split("-C")[0])),
    ) as Record<string, T>;
    // Review M2: a decision belongs to ONE amount (its key names it); a line whose amounts changed loses them all.
    const amountsOf = (lines: SupplierLine[]) => new Map(lines.map((line) => [line.key, line.amounts.map((a) => a.key).join("|")]));
    const before = amountsOf(reading?.lines ?? []);
    const after = amountsOf(parsed.lines);
    const amountKeys = new Set(parsed.lines.flatMap((line) => line.amounts.map((a) => a.key)));
    setDecisions((all) => Object.fromEntries(Object.entries(all).filter(([key]) => {
      const lineKey = key.split("-A")[0];
      return amountKeys.has(key) && before.get(lineKey) === after.get(lineKey);
    })));
    setPrices(keepFor); setQuantities(keepFor); setSelected(keepFor);
    setReading(parsed);
    setCorrections(corrected);
    setResults(fresh);
    setCreated({});
    setStaleReading("");
    return true;
  }

  async function readAgain() {
    setActionBusy(true);
    try {
      if (!(await refresh())) setStaleReading("Todavía no se pudo volver a leer la lista. Inténtalo otra vez en un momento.");
    } finally {
      setActionBusy(false);
    }
  }

  function changeText(value: string) {
    setText(value);
    textNow.current = value;
    readingRun.current += 1;
    if (reading) {
      // The text changed: what was read and decided belongs to the old text.
      setReading(null); setResults({}); setDecisions({}); setPrices({}); setCorrections({});
      setQuantities({}); setIgnoredWords({}); setSelected({}); setPending(null); dropDraft();
    }
  }

  /**
   * The first paste of a list — into an empty box, or over all of it — is what was received: the clipboard text
   * itself, decided here (a paste that changes nothing fires no change event). Once there is a received list, a paste
   * is a correction like any other edit.
   */
  function pasteText(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    if (received !== null) return;
    const box = event.currentTarget;
    const replaces = !box.value.trim() || (box.selectionStart === 0 && box.selectionEnd === box.value.length);
    const pasted = (event.clipboardData?.getData("text") ?? "").slice(0, MAX_LIST_CHARS);
    if (replaces && pasted.trim()) setReceived(pasted);
  }

  /** «Empezar otra lista»: the text and what was received are forgotten; the next paste is a new list. */
  function startOver() {
    changeText("");
    setReceived(null);
    textBox.current?.focus();
  }

  function editText() {
    textBox.current?.focus();
    textBox.current?.scrollIntoView?.({ block: "center" });
  }

  // ── Actions (D-B1-4): each one explicit, confirmed and `products.manage` ───────────────────────────────────────

  async function createVariant(entry: ConfigEntry) {
    const modelId = results[entry.line.key]?.model?.model_id;
    if (!modelId) return;
    setActionBusy(true);
    setActionError("");
    try {
      await createModelVariant(modelId, { condition_line: "new", option_ids: entry.configuration.option_ids }, companyId);
    } catch (error) {
      setActionError(variantWriteError(error));
      setActionBusy(false);
      return;
    }
    // Written. Whatever happens with the reading after it, the variant EXISTS: never offered again before a fresh
    // reading, never reported as a failed write (review M3).
    setPending(null);
    setCreated((all) => ({ ...all, [entry.key]: true }));
    dropDraft();
    try {
      if (await refresh()) {
        setNotice("Variante creada como borrador pendiente: oculta, sin precio ni stock. No se vende ni se publica hasta habilitarla en la paleta del modelo.");
      } else {
        setStaleReading("La variante se creó como borrador, pero no se pudo volver a leer la lista: lo que ves puede estar desactualizado.");
      }
    } finally {
      setActionBusy(false);
    }
  }

  async function registerAlias(body: CatalogAliasWrite) {
    setActionBusy(true);
    setActionError("");
    try {
      await createCatalogAlias(body, companyId);
    } catch (error) {
      setActionError(aliasWriteError(error, "No se pudo registrar el alias.").message);
      setActionBusy(false);
      return;
    }
    setPending(null);
    dropDraft();
    try {
      if (await refresh()) setNotice(`«${body.alias}» quedó registrado como alias de la empresa. La lista se volvió a leer.`);
      else setStaleReading(`«${body.alias}» quedó registrado como alias, pero no se pudo volver a leer la lista: lo que ves puede estar desactualizado.`);
    } finally {
      setActionBusy(false);
    }
  }

  // ── Saving (D-B1-3) ─────────────────────────────────────────────────────────────────────────────────────────

  const saveIssues: string[] = [];
  if (!supplierId) saveIssues.push("Elige el proveedor de la lista.");
  if (dateIssue) saveIssues.push(dateIssue);
  // The official importer refuses a row without it («Indica el plazo en días»): asked here, never assumed.
  if (!leadTimeDays.trim()) saveIssues.push("Indica el plazo de entrega en días: el importador oficial lo exige.");
  if (!chosen.length) saveIssues.push("Elige al menos una configuración limpia.");

  async function rehearse() {
    if (saveIssues.length) return;
    const body = draftBody(draftRows(chosen, inputs, ctx), { supplierId: Number(supplierId), messageDate, leadTimeDays });
    const mine = ++run.current;
    setDryRunning(true);
    setSaveError("");
    setHandoff(null);
    try {
      const answer = await requestOfferDraft(body, companyId);
      if (mine !== run.current) return;
      setDryRun({ body, answer, byKey: dryRunByKey(answer) });
    } catch (error) {
      if (mine === run.current) setSaveError(error instanceof Error ? error.message : "No se pudo ensayar la lista.");
    } finally {
      if (mine === run.current) setDryRunning(false);
    }
  }

  async function continueToImporter() {
    if (!dryRun || preparing) return;
    const mine = run.current;
    setPreparing(true);
    setSaveError("");
    try {
      const file = await offerDraftFile(dryRun.body, companyId);
      if (mine !== run.current) return;
      setHandoff({ file, completeSupplierId: null, messageDate: dryRun.body.message_date, partialOnly: true });
    } catch (error) {
      if (mine === run.current) setSaveError(error instanceof Error ? error.message : "No se pudo preparar el archivo.");
    } finally {
      setPreparing(false);
    }
  }

  const rejected = dryRun ? Object.values(dryRun.byKey).filter((row) => row.action === "error") : [];

  // ── Rendering ───────────────────────────────────────────────────────────────────────────────────────────────

  const changeDecision = (key: string, decision: AmountDecision | undefined) => {
    dropDraft();
    setDecisions((all) => ({ ...all, [key]: decision }));
  };

  return (
    <div className="min-w-0 space-y-6">
      <Panel title="Datos de la lista" description="Quién la envió, cuándo y en qué moneda. Nada se guarda hasta el último paso del importador oficial.">
        <fieldset className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <legend className="sr-only">Datos de la lista</legend>
          {canManage ? (
            <label className="block min-w-0 text-xs text-muted">
              Proveedor
              <select aria-label="Proveedor de la lista" className={`mt-1.5 ${smallInput}`} value={supplierId}
                onChange={(event) => { dropDraft(); setSupplierId(event.target.value); }}>
                <option value="">{suppliers === null ? "Cargando…" : "Elegir…"}</option>
                {(suppliers ?? []).map((supplier) => <option key={supplier.id} value={String(supplier.id)}>{supplier.name}</option>)}
              </select>
              {suppliersError ? <span className="mt-1 block text-danger">{suppliersError}</span> : null}
            </label>
          ) : null}
          <label className="block min-w-0 text-xs text-muted">
            Fecha del mensaje
            <input type="date" required aria-label="Fecha del mensaje del proveedor" max={serverToday()} value={messageDate}
              className={`mt-1.5 ${smallInput}`} aria-describedby="list-date-help"
              onChange={(event) => { dropDraft(); setMessageDate(event.target.value); }} />
          </label>
          <label className="block min-w-0 text-xs text-muted">
            Moneda de la lista
            <select aria-label="Moneda de la lista" className={`mt-1.5 ${smallInput}`} value={listCurrency} aria-describedby="list-currency-help"
              onChange={(event) => { dropDraft(); setListCurrency(event.target.value); }}>
              <option value="">Elegir…</option>
              {[...new Set([...(companyCurrency ? [companyCurrency] : []), ...LIST_CURRENCY_CODES])].map((code) => (
                <option key={code} value={code}>{code === companyCurrency ? `${code} (moneda de la empresa)` : code}</option>
              ))}
            </select>
          </label>
          {canManage ? (
            <label className="block min-w-0 text-xs text-muted">
              Plazo de entrega (días)
              <input inputMode="numeric" required aria-label="Plazo de entrega (días)" value={leadTimeDays} className={`mt-1.5 ${smallInput}`}
                onChange={(event) => { dropDraft(); setLeadTimeDays(event.target.value.replace(/\D/g, "").slice(0, 3)); }} />
            </label>
          ) : null}
          <p id="list-date-help" className="text-xs text-muted sm:col-span-2 lg:col-span-4">
            El día en que el proveedor envió la lista (no el de hoy). La vigencia se calcula desde esa fecha y una lista
            que ya se cargó se reconoce aunque la pegues otra vez.
            {messageDate && dateIssue ? <span className="mt-1 block text-danger">{dateIssue}</span> : null}
          </p>
          <p id="list-currency-help" className="text-xs text-muted sm:col-span-2 lg:col-span-4">
            {companyCurrency
              ? `Sugerida: ${companyCurrency}, la moneda de la empresa. Elígela tú: sin moneda ningún importe se clasifica, y un importe en otra moneda nunca se convierte.`
              : companyCurrency === null
                ? "No se pudo leer la moneda de la empresa: los importes sólo pueden ser referencia o ignorarse."
                : "Leyendo la moneda de la empresa…"}
            {reading?.mentionedCurrencies.length
              ? <span className="mt-1 block text-warning">{`El texto menciona: ${reading.mentionedCurrencies.join(", ")}.`}</span> : null}
          </p>
        </fieldset>
      </Panel>

      <Panel title="Pegar la lista" description="Una línea por oferta. El texto se queda en esta pestaña: no se guarda, no va a ninguna IA y los datos personales no salen del navegador.">
        <label className="block text-xs text-muted">
          Texto de la lista
          <textarea ref={textBox} aria-label="Texto de la lista del proveedor" rows={8} maxLength={MAX_LIST_CHARS} value={text}
            disabled={actionBusy} aria-describedby={actionBusy ? "list-busy-help" : undefined}
            onChange={(event) => changeText(event.target.value)} onPaste={pasteText}
            className={`mt-1.5 ${internalInputClass} resize-y font-mono text-xs`} />
        </label>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" className={primaryClass} onClick={() => void readList()}
            disabled={!vocabulary || !text.trim() || resolving || actionBusy}>
            {resolving ? "Leyendo…" : "Leer la lista"}
          </button>
          {text || received !== null ? (
            <button type="button" className={buttonClass} onClick={startOver} disabled={resolving || actionBusy}>
              Empezar otra lista<span className="sr-only"> (vacía el texto)</span>
            </button>
          ) : null}
          <span className="text-xs text-muted">{text.length} / {MAX_LIST_CHARS} caracteres</span>
          {actionBusy ? <span id="list-busy-help" className="text-xs text-muted">Guardando una acción: el texto se puede editar al terminar.</span> : null}
        </div>
        {received !== null && text.trim() && sharedShare(received, text) < 0.5 ? (
          <p className="mt-3 text-xs text-warning">
            Este texto se compara con la lista que pegaste primero: lo que cambie cuenta como corrección. Si es otra
            lista, pulsa «Empezar otra lista».
          </p>
        ) : null}
        {vocabularyError ? <div className="mt-3"><ErrorBox message={vocabularyError} /></div> : null}
        {resolveError ? <div className="mt-3"><ErrorBox message={resolveError} /></div> : null}
      </Panel>

      {notice ? <p role="status" className="rounded-lg border border-success-border px-4 py-3 text-sm text-success">{notice}</p> : null}
      {actionError ? <div role="alert"><ErrorBox message={actionError} /></div> : null}
      {staleReading ? (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
          <span>{staleReading}</span>
          <button type="button" className={buttonClass} disabled={actionBusy} onClick={() => void readAgain()}>Volver a leer la lista</button>
        </div>
      ) : null}

      {reading ? (
        <ReadingView
          reading={{ ...reading, lines }} results={results} entries={entries} ctx={ctx} inputs={inputs} canManage={canManage}
          vocabulary={vocabulary} chosen={chosen} selected={selected} issuesOf={issuesOf} dryRun={dryRun?.byKey ?? null}
          pending={pending} actionBusy={actionBusy} created={created}
          onDecision={changeDecision}
          onPrice={(key, value) => { dropDraft(); setPrices((all) => ({ ...all, [key]: value })); }}
          onQuantity={(key, value) => { dropDraft(); setQuantities((all) => ({ ...all, [key]: value })); }}
          onIgnoreWords={(key, value) => { dropDraft(); setIgnoredWords((all) => ({ ...all, [key]: value })); }}
          onSelect={(key, value) => { dropDraft(); setSelected((all) => ({ ...all, [key]: value })); }}
          onPending={(next) => { setActionError(""); setPending(next); }}
          onCreateVariant={(entry) => void createVariant(entry)}
          onAlias={(body) => void registerAlias(body)}
          onEditText={editText}
        />
      ) : null}

      {reading ? (
        canManage ? (
          <section aria-label="Vista previa de lo que se guardará" className="min-w-0 space-y-4 rounded-xl border border-bd-border bg-surface p-5">
            <div>
              <h2 className="text-sm font-semibold text-foreground">Vista previa de lo que se guardará</h2>
              <p className="mt-1 text-xs text-muted">
                Exactamente estas ofertas, y ninguna otra. Es una lista parcial: las ofertas del proveedor que no están
                aquí no cambian, y las líneas con dudas quedan en pantalla sin guardarse.
              </p>
            </div>
            {chosen.length ? (
              <div className="relative overflow-x-auto">
                <table className="w-full min-w-[36rem] text-left text-xs">
                  <caption className="sr-only">Ofertas que se enviarán al importador oficial</caption>
                  <thead>
                    <tr className="border-b border-bd-border">
                      <th scope="col" className="p-2">Línea</th>
                      <th scope="col" className="p-2">Texto</th>
                      <th scope="col" className="p-2">Variante</th>
                      <th scope="col" className="p-2">Precio comercial</th>
                      {canSeeCosts ? <th scope="col" className="p-2">Costo</th> : null}
                      <th scope="col" className="p-2">Cantidad</th>
                      <th scope="col" className="p-2">Avisos</th>
                    </tr>
                  </thead>
                  <tbody>
                    {chosen.map((entry) => {
                      const answered = dryRun?.byKey[entry.key];
                      const cost = lineCost(entry.line, decisions, ctx);
                      const quantity = (quantities[entry.key] ?? "").trim();
                      return (
                        <tr key={entry.key} className="border-b border-bd-border align-top" data-testid={`save-row-${entry.key}`}>
                          <td className="p-2">{`Línea ${entry.line.number}`}</td>
                          <td className="p-2 break-words">
                            {entry.line.correction ? (
                              <>
                                <span className="block text-muted">{`Recibido: ${entry.line.correction.received
                                  ? (canSeeCosts ? entry.line.correction.received.display : entry.line.correction.received.displayWithoutAmounts)
                                  : "sin línea recibida emparejada"}`}</span>
                                <span className="block">{`Corregido a mano: ${canSeeCosts ? entry.line.display : entry.line.displayWithoutAmounts}`}</span>
                                {entry.line.correction.removedWords.length ? (
                                  <span className="block text-warning">{`Quitado de lo recibido: ${entry.line.correction.removedWords
                                    .map((word) => (!canSeeCosts && /\d/.test(word) ? "«•••»" : `«${word}»`)).join(", ")}`}</span>
                                ) : null}
                              </>
                            ) : (
                              <span className="block">{`Recibido: ${canSeeCosts ? entry.line.display : entry.line.displayWithoutAmounts}`}</span>
                            )}
                          </td>
                          <td className="p-2 break-words">
                            {entry.configuration.product_name ?? `Producto ${entry.configuration.product_id}`}
                            {entry.configuration.options.map((read) => (
                              <span key={`${read.role}-${read.option_id}`} className="block text-muted">
                                {`${ROLE_LABELS[read.role] ?? read.role}: «${read.text}» → ${read.label}${viaLabel(read.via) ? ` (${viaLabel(read.via)})` : ""}`}
                              </span>
                            ))}
                          </td>
                          <td className="p-2">{(prices[entry.key] ?? "").trim()}</td>
                          {canSeeCosts ? <td className="p-2">{cost ?? "—"}</td> : null}
                          <td className="p-2">{quantity || "Sin confirmar"}</td>
                          <td className="p-2">
                            {entry.configuration.draft_pending ? <span className="block text-warning">Borrador pendiente: no vende hasta habilitarla.</span> : null}
                            {answered?.errors.map((error) => <span key={error} className="block text-danger">{error}</span>)}
                            {answered?.warnings.map((warning) => <span key={warning} className="block text-warning">{warning}</span>)}
                            {answered && answered.action !== "error" && !answered.errors.length ? <span className="block text-success">Lista para el importador.</span> : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted">Todavía no elegiste ninguna configuración. Sólo se pueden elegir las que no tienen dudas.</p>
            )}
            {saveIssues.length && chosen.length ? (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-warning">{saveIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
            ) : null}
            <div className="flex flex-wrap gap-3">
              <button type="button" className={primaryClass} disabled={Boolean(saveIssues.length) || dryRunning || resolving} onClick={() => void rehearse()}>
                {dryRunning ? "Ensayando…" : "Ensayar con el importador oficial"}
              </button>
              {dryRun && !rejected.length && !handoff ? (
                <button type="button" className={buttonClass} disabled={preparing} onClick={() => void continueToImporter()}>
                  {preparing ? "Preparando el archivo…" : "Continuar al importador oficial"}
                </button>
              ) : null}
            </div>
            {saveError ? <ErrorBox message={saveError} /> : null}
            {dryRun ? <DryRunSummary answer={dryRun.answer} rejected={rejected.length} /> : null}
            {handoff ? (
              <section ref={importerRef} tabIndex={-1} aria-label="Importador oficial de ofertas"
                className={`min-w-0 space-y-2 outline-none ${focusRing}`}>
                <p className="text-xs text-muted">
                  El importador oficial de «Ofertas de proveedores» con estas filas. Revisa su vista previa: sólo «Aplicar»
                  escribe, con sus confirmaciones. Si cambias algo arriba, este importador se cierra.
                </p>
                <SupplierOfferImport canManage={canManage} companyId={companyId} suppliers={suppliers ?? []} handoff={handoff}
                  onApplied={() => setNotice("El importador oficial aplicó las ofertas. Las líneas con dudas siguen en pantalla, sin guardar.")} />
              </section>
            ) : null}
          </section>
        ) : (
          <p className="rounded-lg border border-bd-border bg-surface px-4 py-3 text-sm text-muted">
            Guardar ofertas requiere permiso para gestionar productos. Puedes leer la lista y ver cómo se interpreta.
          </p>
        )
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The reading, line by line
// ---------------------------------------------------------------------------

type ReadingProps = {
  reading: SupplierListReading;
  results: Record<string, ResolveRowResult>;
  entries: ConfigEntry[];
  ctx: AmountContext;
  inputs: EntryInputs;
  canManage: boolean;
  vocabulary: CatalogVocabulary | null;
  chosen: ConfigEntry[];
  selected: Record<string, boolean | undefined>;
  issuesOf: (entry: ConfigEntry) => string[];
  dryRun: Record<string, OfferDraftAnswerRow> | null;
  pending: Pending | null;
  actionBusy: boolean;
  /** Configurations a variant was created for, before a fresh reading. */
  created: Record<string, boolean | undefined>;
  onDecision: (key: string, decision: AmountDecision | undefined) => void;
  onPrice: (key: string, value: string) => void;
  onQuantity: (key: string, value: string) => void;
  onIgnoreWords: (key: string, value: boolean) => void;
  onSelect: (key: string, value: boolean) => void;
  onPending: (pending: Pending | null) => void;
  onCreateVariant: (entry: ConfigEntry) => void;
  onAlias: (body: CatalogAliasWrite) => void;
  /** «Corregir el texto»: back to the text box (for writing mistakes only). */
  onEditText: () => void;
};

function ReadingView(props: ReadingProps) {
  const { reading, entries } = props;
  const counts = RESOLVER_STATUS_ORDER
    .map((status) => [status, entries.filter((entry) => entry.configuration.status === status).length] as const)
    .filter(([, count]) => count > 0);
  return (
    <div className="min-w-0 space-y-4">
      <p role="status" aria-label="Resumen de la lectura" className="text-sm text-foreground">
        {[`${reading.lines.length} líneas`, `${entries.length} configuraciones`,
          ...counts.map(([status, count]) => `${count} ${RESOLVER_STATUS_LABELS[status].toLowerCase()}`)].join(" · ")}
      </p>
      {reading.truncated ? (
        <p role="alert" className="text-sm text-danger">La lista tiene más de 300 líneas con oferta: sólo se leyeron las primeras. Divídela.</p>
      ) : null}
      {reading.skipped.length ? (
        <details className="text-xs text-muted">
          <summary className={`${focusRing} cursor-pointer`}>{`${reading.skipped.length} líneas sin oferta (no se envían)`}</summary>
          <ul className="mt-2 space-y-1">
            {reading.skipped.map((item) => (
              <li key={item.number} className="break-words font-mono">
                {`${item.number}: ${props.ctx.canSeeCosts ? item.display : item.displayWithoutAmounts}`}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <ol className="min-w-0 space-y-4">
        {reading.lines.map((line) => (
          <li key={line.key} className="min-w-0">
            <LineView {...props} line={line} result={props.results[line.key]}
              lineEntries={entries.filter((entry) => entry.line.key === line.key)} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function LineView(props: ReadingProps & { line: SupplierLine; result: ResolveRowResult | undefined; lineEntries: ConfigEntry[] }) {
  const { line, result, ctx, inputs, canManage, pending } = props;
  const model = result?.model ?? null;
  const read: [string, string][] = [
    ["Capacidad", line.capacityText], ["SIM", line.simText], ["Región", line.regionText], ["Color", line.colourText],
  ];
  return (
    <article aria-label={`Línea ${line.number}`} className="min-w-0 space-y-4 rounded-xl border border-bd-border bg-surface p-4 sm:p-5">
      <header className="min-w-0 space-y-1">
        <h3 className="text-sm font-semibold text-foreground">{`Línea ${line.number}`}</h3>
        {line.correction ? (
          <CorrectedText correction={line.correction} line={line} ctx={ctx} />
        ) : (
          <p className="break-words font-mono text-xs text-muted">{ctx.canSeeCosts ? line.display : line.displayWithoutAmounts}</p>
        )}
        {line.hidden.length ? (
          <p className="text-xs text-warning">
            {`Se ocultaron datos (${line.hidden.map((kind) => HIDDEN_TEXT[kind] ?? kind).join(", ")}): no se leen ni se envían.`}
          </p>
        ) : null}
      </header>

      <dl className="grid min-w-0 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_minmax(0,1fr)]">
        <dt className="text-xs text-muted">Modelo leído</dt>
        <dd className="min-w-0 break-words">
          {line.model ? <>«{line.model.text}»</> : <span className="text-warning">No se reconoce ningún modelo de la empresa: la línea no se envía.</span>}
          {model ? (
            <span className="block text-xs text-muted">
              {MODEL_READ_LABELS[model.status] ?? model.status}
              {model.model_name ? <> → <strong className="font-semibold text-foreground">{model.model_name}</strong></> : null}
            </span>
          ) : null}
        </dd>
        {read.filter(([, value]) => value).map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-xs text-muted">{name}</dt>
            <dd className="min-w-0 break-words">«{value}»</dd>
          </div>
        ))}
        {line.otherTexts.length ? (
          <>
            <dt className="text-xs text-muted">Otras opciones</dt>
            <dd className="min-w-0 break-words">{line.otherTexts.map((value) => `«${value}»`).join(", ")} <span className="text-xs text-muted">(se muestran, no se envían)</span></dd>
          </>
        ) : null}
        <dt className="text-xs text-muted">Cantidad dicha</dt>
        <dd>{line.statedQuantity ? `«${line.statedQuantity.text}» — sin confirmar: sólo cuenta la que escribas.` : "Ninguna (sin confirmar)."}</dd>
      </dl>

      {line.conflicts.length ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-warning">{line.conflicts.map((text) => <li key={text}>{text}</li>)}</ul>
      ) : null}

      {line.colourDoubt ? (
        <ColourDoubtView line={line} modelId={model?.model_id ?? null} canManage={canManage} vocabulary={props.vocabulary}
          pending={pending} actionBusy={props.actionBusy} onPending={props.onPending} onAlias={props.onAlias}
          onEditText={props.onEditText} />
      ) : null}
      {line.correction?.blocked ? (
        <div role="group" aria-label={`Corrección bloqueada de la línea ${line.number}`}
          className="min-w-0 space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3">
          <p className="text-sm text-warning">{line.correction.blocked}</p>
          {model?.model_id ? <PaletteLink modelId={model.model_id} /> : null}
        </div>
      ) : null}

      {line.unknownWords.length ? (
        <div className="min-w-0 space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3">
          <p className="text-xs font-semibold text-warning">Palabras sin reconocer (no se envían)</p>
          <ul className="flex min-w-0 flex-wrap gap-2">
            {line.unknownWords.map((word, index) => {
              const id = `${line.key}:word:${index}`;
              // Without `costs.view` a word holding a figure is a supplier amount in disguise: never shown, never an alias.
              const hiddenWord = !ctx.canSeeCosts && /\d/.test(word);
              return (
                <li key={id} className="min-w-0">
                  <span className="break-words text-sm text-foreground">{hiddenWord ? "«•••»" : `«${word}»`}</span>
                  {canManage && props.vocabulary && !hiddenWord ? (
                    <button type="button" className={`ml-2 ${buttonClass}`}
                      onClick={() => props.onPending({ kind: "alias", id, text: word, role: null })}>
                      Es un alias de…<span className="sr-only">{` («${word}»)`}</span>
                    </button>
                  ) : null}
                  {pending?.kind === "alias" && pending.id === id && props.vocabulary ? (
                    <AliasForm text={word} role={null} vocabulary={props.vocabulary} busy={props.actionBusy}
                      onCancel={() => props.onPending(null)} onConfirm={props.onAlias} />
                  ) : null}
                </li>
              );
            })}
          </ul>
          {line.ignorableWords.length === line.unknownWords.length ? (
            <label className={`flex items-center gap-2 text-xs text-foreground ${touch}`}>
              <input type="checkbox" checked={Boolean(inputs.ignoredWords[line.key])}
                onChange={(event) => props.onIgnoreWords(line.key, event.target.checked)} />
              {`Ignorar las palabras sin reconocer de la línea ${line.number}`}
            </label>
          ) : (
            <p className="text-xs text-muted">
              Alguna toca el modelo, la capacidad o el color: puede cambiar lo que dicen, así que no se puede ignorar.
              Regístrala como alias o deja la línea pendiente.
            </p>
          )}
        </div>
      ) : null}

      {line.amounts.length ? (
        <div className="min-w-0 space-y-2">
          {line.amounts.map((amount, index) => (
            <AmountView key={amount.key} amount={amount} index={index + 1} line={line} ctx={ctx}
              decision={inputs.decisions[amount.key]} onDecision={props.onDecision} />
          ))}
        </div>
      ) : <p className="text-xs text-muted">La línea no trae importes.</p>}

      {result?.error ? <p role="alert" className="text-sm text-danger">{result.error}</p> : null}
      {line.model && !result && !props.lineEntries.length ? <p className="text-xs text-muted">Esperando la lectura del servidor…</p> : null}
      <div className="min-w-0 space-y-3">
        {props.lineEntries.map((entry) => <ConfigurationView key={entry.key} {...props} entry={entry} />)}
      </div>
    </article>
  );
}

function AmountView({ amount, index, line, ctx, decision, onDecision }: {
  amount: SupplierAmount; index: number; line: SupplierLine; ctx: AmountContext; decision: AmountDecision | undefined;
  onDecision: (key: string, decision: AmountDecision | undefined) => void;
}) {
  const visible = amountVisible(amount, ctx);
  const { allowed, why } = amountChoices(amount, ctx);
  const currency = amountCurrency(amount, ctx);
  const offered: AmountClass[] = visible ? ["cost", "reference", "ignore"] : ["ignore"];
  const needsExact = decision?.kind === "cost" && (amount.value === null || ["ambiguous_separator", "range", "several", "invalid"].includes(amount.problem ?? ""));
  const name = `Importe ${index} de la línea ${line.number}`;
  return (
    <fieldset aria-label={name} className="min-w-0 rounded-lg border border-bd-border bg-background p-3">
      <legend className="sr-only">{name}</legend>
      <p className="text-sm text-foreground">
        {visible ? <strong className="font-semibold">{amount.raw}</strong> : <span>Importe oculto</span>}
        {visible && currency ? <span className="ml-1 text-xs text-muted">{currency}{amount.currency ? " (escrito)" : " (moneda de la lista)"}</span> : null}
        {visible && amount.label ? <span className="ml-1 text-xs text-muted">{`· ${AMOUNT_LABEL_HINT[amount.label]}`}</span> : null}
      </p>
      {visible && amount.dollarSign ? (
        <p className="mt-1 text-xs text-warning">Escrito con «$»: puede ser dólares u otra moneda con ese signo. Se toma la moneda de la lista; nunca se convierte.</p>
      ) : null}
      {amount.problem ? <p className="mt-1 text-xs text-warning">{AMOUNT_PROBLEM_TEXT[amount.problem]}</p> : null}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {offered.map((kind) => (
          <label key={kind} className={`flex items-center gap-2 text-sm ${touch} ${allowed.includes(kind) ? "text-foreground" : "text-muted"}`}>
            <input type="radio" name={amount.key} value={kind} checked={decision?.kind === kind} disabled={!allowed.includes(kind)}
              onChange={() => onDecision(amount.key, { kind, exact: kind === "cost" ? decision?.exact : undefined })} />
            {AMOUNT_CLASS_LABELS[kind]}
          </label>
        ))}
      </div>
      {why ? <p className="mt-1 text-xs text-muted">{why}</p> : null}
      {needsExact ? (
        <label className="mt-2 block text-xs text-muted">
          Costo exacto (el importe escrito no es un número claro)
          <input inputMode="decimal" aria-label={`Costo exacto del importe ${index} de la línea ${line.number}`}
            value={decision?.exact ?? ""} className={`mt-1 ${smallInput} max-w-48`}
            onChange={(event) => onDecision(amount.key, { kind: "cost", exact: event.target.value.slice(0, 20) })} />
        </label>
      ) : null}
    </fieldset>
  );
}

function ConfigurationView(props: ReadingProps & { entry: ConfigEntry; result: ResolveRowResult | undefined }) {
  const { entry, inputs, ctx, canManage, pending, chosen, dryRun } = props;
  const { configuration, line } = entry;
  const position = `línea ${line.number}, configuración ${entry.index + 1}`;
  const issues = props.issuesOf(entry);
  const repeated = repeatedIn(entry, chosen);
  const isChosen = chosen.some((other) => other.key === entry.key);
  const references = lineReferences(line, inputs.decisions, ctx);
  const answered = dryRun?.[entry.key];
  const modelId = props.result?.model?.model_id ?? null;
  const creating = pending?.kind === "variant" && pending.key === entry.key;
  return (
    <article aria-label={`Configuración ${entry.index + 1} de la línea ${line.number}`}
      className="min-w-0 space-y-3 rounded-lg border border-bd-border bg-background p-3 sm:p-4">
      <header className="flex min-w-0 flex-wrap items-center gap-2">
        <ResolverStatusBadge status={configuration.status} />
        {configuration.colour_text ? <span className="break-words text-xs text-muted">{`Color dicho: «${configuration.colour_text}»`}</span> : null}
      </header>
      <p className="text-xs text-muted">{RESOLVER_STATUS_HELP[configuration.status] ?? ""}</p>

      {configuration.product_id ? (
        <p className="text-sm">
          <span className="text-muted">Variante: </span>
          <Link href={`/admin/products/${configuration.product_id}`} target="_blank" rel="noopener noreferrer"
            className={`break-words underline underline-offset-4 ${focusRing}`}>
            {configuration.product_name ?? `Producto ${configuration.product_id}`}
            <span className="sr-only"> (se abre en otra pestaña; la lista pegada se queda aquí)</span>
          </Link>
          {configuration.draft_pending ? (
            <span className="mt-1 block text-xs text-warning">
              Borrador pendiente: la oferta se puede registrar, pero no vende hasta habilitar la variante en la paleta del modelo.
            </span>
          ) : null}
        </p>
      ) : null}

      {configuration.options.length ? (
        <ul className="space-y-0.5 text-xs text-foreground">
          {configuration.options.map((read) => (
            <li key={`${read.role}-${read.option_id}`} className="break-words">
              <span className="text-muted">{ROLE_LABELS[read.role] ?? read.role}:</span> «{read.text}» → <strong className="font-semibold">{read.label}</strong>
              {viaLabel(read.via) ? <span className="text-muted"> ({viaLabel(read.via)})</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {configuration.missing.length ? (
        <p className="text-xs text-warning">{`Falta decir: ${configuration.missing.map(missingLabel).join(", ")}.`}</p>
      ) : null}
      {configuration.doubts.length ? (
        <ul className="min-w-0 space-y-2">
          {configuration.doubts.map((doubt, index) => (
            <DoubtView key={`${doubt.role}-${index}`} {...props} doubt={doubt} id={`${entry.key}:doubt:${index}`} modelId={modelId} />
          ))}
        </ul>
      ) : null}
      {configuration.candidates.length ? (
        <p className="break-words text-xs text-muted">
          {`Variantes que encajan en parte (ninguna se elige): ${configuration.candidates.map((c) => c.name).join(" · ")}`}
        </p>
      ) : null}

      {canManage && modelId && !props.created[entry.key] && mayCreateVariant(entry, inputs) ? (
        creating ? (
          <div role="group" aria-label={`Crear variante · ${position}`} className="space-y-2 rounded-lg border border-bd-border p-3">
            <p className="text-xs text-foreground">
              Se creará la variante como borrador: oculta, a 0,00, sin stock ni movimientos y sin publicar. No se vende
              hasta que alguien la habilite en la paleta del modelo. Nunca se crean modelos ni opciones.
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={primaryClass} disabled={props.actionBusy} onClick={() => props.onCreateVariant(entry)}>
                Confirmar: crear borrador
              </button>
              <button type="button" className={buttonClass} disabled={props.actionBusy} onClick={() => props.onPending(null)}>Cancelar</button>
            </div>
          </div>
        ) : (
          <button type="button" className={buttonClass} aria-label={`Crear variante · ${position}`}
            onClick={() => props.onPending({ kind: "variant", key: entry.key })}>
            Crear variante
          </button>
        )
      ) : null}

      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
        <label className="block min-w-0 text-xs text-muted">
          Precio comercial (lo que paga el cliente)
          <input inputMode="decimal" aria-label={`Precio comercial · ${position}`} value={inputs.prices[entry.key] ?? ""}
            readOnly={!canManage}
            placeholder="Escribir" className={`mt-1 ${smallInput}`}
            onChange={(event) => props.onPrice(entry.key, event.target.value.replace(/[^0-9.,]/g, "").slice(0, 16))} />
          {references.length ? (
            <span className="mt-1 block text-foreground">
              {`Referencia del proveedor: ${references.map((amount) => `${amount.raw} ${amountCurrency(amount, ctx) ?? ""}`.trim()).join(" · ")} (no se copia ni se guarda)`}
            </span>
          ) : null}
        </label>
        <label className="block min-w-0 text-xs text-muted">
          Cantidad confirmada (vacía = sin confirmar)
          <input inputMode="numeric" aria-label={`Cantidad confirmada · ${position}`} value={inputs.quantities[entry.key] ?? ""}
            readOnly={!canManage}
            placeholder="Sin confirmar" className={`mt-1 ${smallInput}`}
            onChange={(event) => props.onQuantity(entry.key, event.target.value.replace(/\D/g, "").slice(0, 6))} />
        </label>
      </div>

      {canManage ? (
        <div className="min-w-0 space-y-1">
          <label className={`flex items-center gap-2 text-sm text-foreground ${touch}`}>
            <input type="checkbox" checked={isChosen}
              disabled={Boolean(issues.length) || (Boolean(repeated) && !isChosen)}
              onChange={(event) => props.onSelect(entry.key, event.target.checked)} />
            Incluir en el guardado<span className="sr-only">{` · ${position}`}</span>
          </label>
          {repeated && !isChosen ? (
            <p className="text-xs text-warning">{`Repetida: la misma variante ya va en la línea ${repeated.line.number}. Sólo una puede guardarse.`}</p>
          ) : null}
          {issues.length ? (
            <ul aria-label={`Por qué no se puede guardar · ${position}`} className="list-disc space-y-0.5 pl-5 text-xs text-muted">
              {issues.map((issue) => <li key={issue}>{issue}</li>)}
            </ul>
          ) : null}
          {answered?.errors.length ? (
            <ul className="space-y-0.5 text-xs text-danger" aria-label={`Rechazos del importador · ${position}`}>
              {answered.errors.map((error) => <li key={error}>{error}</li>)}
            </ul>
          ) : null}
          {answered?.warnings.length ? (
            <ul className="space-y-0.5 text-xs text-warning">{answered.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function DoubtView(props: ReadingProps & { doubt: ResolveDoubt; id: string; modelId: number | null }) {
  const { doubt, id, canManage, pending, vocabulary } = props;
  const aliasRole = doubt.role === "model" ? "model" : doubt.role;
  const canAlias = canManage && Boolean(vocabulary) && doubt.actions.includes("alias_of") && aliasRole !== "other";
  return (
    <li className="min-w-0 rounded-lg border border-warning-border p-3">
      <p className="text-sm text-foreground">
        <span className="text-muted">{doubtRoleLabel(doubt.role)}:</span> «<span className="break-words">{doubt.text}</span>»{" "}
        <span className="whitespace-nowrap rounded-full border border-warning-border px-2 text-[11px] font-semibold text-warning">
          {DOUBT_REASON_LABELS[doubt.reason] ?? doubt.reason}
        </span>
      </p>
      <p className="mt-1 text-xs text-muted">{DOUBT_REASON_HELP[doubt.reason] ?? ""}</p>
      {doubt.readings.length ? (
        <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs text-foreground">
          {doubt.readings.map((reading, index) => (
            <li key={index} className="break-words">
              {reading.map((part) => `«${part.text}» → ${part.label ?? "sin opción conocida"}`).join(" + ")}
            </li>
          ))}
        </ol>
      ) : null}
      <ul className="mt-1 list-disc pl-5 text-xs text-muted">
        {doubt.actions.map((action) => <li key={action}>{DOUBT_ACTION_LABELS[action] ?? action}</li>)}
      </ul>
      <div className="mt-2 flex flex-wrap gap-2">
        {canAlias ? (
          <button type="button" className={buttonClass}
            onClick={() => props.onPending({ kind: "alias", id, text: doubt.text, role: aliasRole as AttributeRole | "model" })}>
            Es un alias de…<span className="sr-only">{` («${doubt.text}»)`}</span>
          </button>
        ) : null}
        {doubt.actions.includes("add_to_palette") && props.modelId ? <PaletteLink modelId={props.modelId} /> : null}
      </div>
      {pending?.kind === "alias" && pending.id === id && vocabulary ? (
        <AliasForm text={doubt.text} role={pending.role} vocabulary={vocabulary} busy={props.actionBusy}
          onCancel={() => props.onPending(null)} onConfirm={props.onAlias} />
      ) : null}
    </li>
  );
}

/**
 * A colour that could not be sent (review H1 and the owner's rule of 2026-11-14). A word is never taken out of a colour
 * on this screen: «glaciar blanco» may be ONE commercial colour. The ways out: the model's palette, a company alias
 * (A2), or — for a writing mistake only — correcting the text; a correction that just deletes words stays blocked.
 */
function ColourDoubtView({ line, modelId, canManage, vocabulary, pending, actionBusy, onPending, onAlias, onEditText }: {
  line: SupplierLine; modelId: number | null; canManage: boolean; vocabulary: CatalogVocabulary | null;
  pending: Pending | null; actionBusy: boolean; onPending: (pending: Pending | null) => void;
  onAlias: (body: CatalogAliasWrite) => void; onEditText: () => void;
}) {
  const doubt = line.colourDoubt!;
  const unknown = doubt.reason === "unknown_words" ? doubt.words.filter((word) => word.kind === "unknown").map((word) => `«${word.text}»`) : [];
  return (
    <div role="group" aria-label={`Duda de color de la línea ${line.number}`}
      className="min-w-0 space-y-2 rounded-lg border border-warning-border bg-warning-surface p-3">
      <p className="text-sm text-warning">{COLOUR_DOUBT_TEXT[doubt.reason](doubt.text)}</p>
      {unknown.length ? (
        <div className="space-y-1 text-xs text-foreground">
          <p>
            {`No se quita ${unknown.join(", ")} para dejar sólo el color: «${doubt.text}» podría ser el nombre comercial de un color. Resuélvelo así:`}
          </p>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>{"Si es un color propio (por ejemplo «Glaciar Blanco»), agrégalo a la paleta del modelo y vuelve a leer la lista."}</li>
            <li>Si es otra forma de escribir un color que ya existe, regístralo como alias de la empresa.</li>
            <li>Si es un error de escritura, corrígelo en el texto y vuelve a leer. Borrar la palabra no lo resuelve: la línea sigue bloqueada.</li>
          </ul>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {modelId ? <PaletteLink modelId={modelId} /> : null}
        <button type="button" className={buttonClass} onClick={onEditText}>Corregir el texto y volver a leer</button>
      </div>
      {canManage && vocabulary && doubt.reason === "unknown_words" ? (
        <div className="space-y-1 border-t border-warning-border pt-2">
          <p className="text-xs text-muted">
            Un alias de la frase entera afecta a toda la empresa: todas sus listas y archivos la leerán así desde ahora.
          </p>
          <button type="button" className={buttonClass} disabled={actionBusy}
            onClick={() => onPending({ kind: "alias", id: `${line.key}:colour`, text: doubt.text, role: "color" })}>
            Es un alias de…<span className="sr-only">{` («${doubt.text}»)`}</span>
          </button>
          {pending?.kind === "alias" && pending.id === `${line.key}:colour` ? (
            <AliasForm text={doubt.text} role="color" vocabulary={vocabulary} busy={actionBusy}
              onCancel={() => onPending(null)} onConfirm={onAlias} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * A line a person corrected after the list was received: what the supplier sent and what is read now, side by side
 * (owner's rule: the received text, the proposed reading and the confirmed correction are never mixed).
 */
function CorrectedText({ correction, line, ctx }: { correction: LineCorrection; line: SupplierLine; ctx: AmountContext }) {
  const received = correction.received ? (ctx.canSeeCosts ? correction.received.display : correction.received.displayWithoutAmounts) : null;
  return (
    <dl className="grid min-w-0 gap-x-3 text-xs sm:grid-cols-[max-content_minmax(0,1fr)]">
      <dt className="text-muted">Texto recibido</dt>
      <dd className="min-w-0 break-words font-mono text-muted">{received ?? "No se puede emparejar con una línea recibida."}</dd>
      <dt className="text-muted">Corregido a mano</dt>
      <dd className="min-w-0 break-words font-mono text-foreground">{ctx.canSeeCosts ? line.display : line.displayWithoutAmounts}</dd>
      {correction.removedWords.length ? (
        <>
          <dt className="text-muted">Quitado de lo recibido</dt>
          <dd className="min-w-0 break-words text-warning">
            {correction.removedWords.map((word) => (!ctx.canSeeCosts && /\d/.test(word) ? "«•••»" : `«${word}»`)).join(", ")}
          </dd>
        </>
      ) : null}
    </dl>
  );
}

/** The model's palette in ANOTHER tab: the pasted list (memory only) is never lost by following it. */
function PaletteLink({ modelId }: { modelId: number }) {
  return (
    <Link href={`/admin/products/models/${modelId}`} target="_blank" rel="noopener noreferrer"
      className={`inline-flex items-center ${buttonClass}`}>
      Abrir la paleta del modelo<span className="sr-only"> (se abre en otra pestaña)</span>
    </Link>
  );
}

/**
 * «Es un alias de…»: a company alias (A2 endpoint, `products.manage`, audited), written only after the person reviews
 * and confirms it. Never a new model, option or palette.
 */
function AliasForm({ text, role, vocabulary, busy, onCancel, onConfirm }: {
  text: string; role: AttributeRole | "model" | null; vocabulary: CatalogVocabulary; busy: boolean;
  onCancel: () => void; onConfirm: (body: CatalogAliasWrite) => void;
}) {
  const [target, setTarget] = useState("");
  const [replacement, setReplacement] = useState("");
  const [review, setReview] = useState(false);
  const roles = role === null ? ROLE_ORDER : role === "model" ? [] : [role];
  const body: CatalogAliasWrite | null = target.startsWith("option:")
    ? { scope: "option", alias: text, option_id: Number(target.slice(7)) }
    : target.startsWith("model:") ? { scope: "model", alias: text, model_id: Number(target.slice(6)) }
      : target === "word" && replacement.trim() ? { scope: "model_word", alias: text, replacement: replacement.trim() } : null;
  const targetName = target.startsWith("option:")
    ? Object.values(vocabulary.options).flat().find((option) => option.id === Number(target.slice(7)))?.label
    : target.startsWith("model:") ? vocabulary.models.find((model) => model.id === Number(target.slice(6)))?.name
      : replacement.trim();
  return (
    <div role="group" aria-label={`Alias para «${text}»`} className="mt-2 min-w-0 space-y-2 rounded-lg border border-bd-border bg-surface p-3">
      <label className="block text-xs text-muted">
        Es un alias de
        <select aria-label="Es un alias de" value={target} className={`mt-1 ${smallInput}`}
          onChange={(event) => { setTarget(event.target.value); setReview(false); }}>
          <option value="">Elegir…</option>
          {roles.map((name) => vocabulary.options[name].length ? (
            <optgroup key={name} label={ROLE_LABELS[name]}>
              {vocabulary.options[name].map((option) => <option key={option.id} value={`option:${option.id}`}>{option.label}</option>)}
            </optgroup>
          ) : null)}
          {role === null || role === "model" ? (
            <optgroup label="Modelos">
              {vocabulary.models.map((model) => <option key={model.id} value={`model:${model.id}`}>{model.name}</option>)}
            </optgroup>
          ) : null}
          {role === null ? <option value="word">Una palabra de los nombres de modelo…</option> : null}
        </select>
      </label>
      {target === "word" ? (
        <label className="block text-xs text-muted">
          Se lee como la palabra
          <input aria-label="Se lee como la palabra" value={replacement} maxLength={40} className={`mt-1 ${smallInput}`}
            onChange={(event) => { setReplacement(event.target.value); setReview(false); }} />
        </label>
      ) : null}
      {review && body ? (
        <p className="text-xs text-foreground">
          {`Se registrará «${text}» como alias de «${targetName ?? ""}» para toda la empresa: las listas y archivos lo leerán así desde ahora.`}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {review && body ? (
          <button type="button" className={primaryClass} disabled={busy} onClick={() => onConfirm(body)}>Confirmar alias</button>
        ) : (
          <button type="button" className={buttonClass} disabled={!body} onClick={() => setReview(true)}>Revisar</button>
        )}
        <button type="button" className={buttonClass} disabled={busy} onClick={onCancel}>Cancelar</button>
      </div>
    </div>
  );
}

function DryRunSummary({ answer, rejected }: { answer: OfferDraftAnswer; rejected: number }) {
  const s = answer.summary;
  const counts: [string, number][] = [
    ["Nuevas", s.new ?? 0], ["Cambian", s.updated ?? 0], ["Sin cambios", s.unchanged ?? 0],
    ["Repetidas (no cambian nada)", s.repeated ?? 0], ["De un mensaje anterior", s.older ?? 0], ["Con error", rejected],
  ];
  return (
    <div role="region" aria-label="Resultado del ensayo" className="space-y-2 rounded-lg bg-background p-3 text-xs">
      <p className="font-semibold text-foreground">Ensayo del importador oficial: no se escribió nada.</p>
      <ul className="grid gap-1 sm:grid-cols-3">{counts.map(([label, value]) => <li key={label}>{`${label}: ${value}`}</li>)}</ul>
      {rejected ? (
        <p role="alert" className="text-danger">
          El importador oficial es todo o nada: quita de la selección las filas rechazadas o corrígelas y vuelve a ensayar.
        </p>
      ) : null}
      {answer.already_applied_content ? (
        <p role="alert" className="text-danger">
          {answer.already_applied_content.detail ?? `Esta lista ya se cargó (carga n.º ${answer.already_applied_content.job_id}).`}
        </p>
      ) : null}
      {!answer.already_applied_content && answer.same_rows_applied ? (
        <p className="text-warning">{answer.same_rows_applied.detail ?? "Son las mismas filas de una carga anterior: el importador pedirá confirmarlo."}</p>
      ) : null}
      {s.reactivations ? <p className="text-warning">{`${s.reactivations} oferta(s) vencida(s) se reactivarían: el importador pedirá confirmarlo.`}</p> : null}
      {s.quantity_cleared ? <p className="text-warning">{`${s.quantity_cleared} fila(s) llegan sin cantidad sobre una oferta confirmada: el importador pedirá decidirlo.`}</p> : null}
      {s.price_changes ? <p className="text-warning">Hay cambios de precio comercial: el importador pide aceptarlos o mantener los actuales.</p> : null}
    </div>
  );
}
