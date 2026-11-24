"use client";

/**
 * CATALOG-REFERENCE-01 · PR F — «Vender equipo por modelo».
 *
 * La caja usa EL MISMO catálogo que la tienda y el panel: el vendedor busca el modelo y elige capacidad → color →
 * SIM/región. Sólo se pregunta lo que varía y sólo se ofrece lo que existe (la paleta menos sus exclusiones, que
 * manda el servidor); cada opción dice cuántos equipos propios hay en ESTA sucursal. Con la combinación elegida:
 *
 *   «Agregar a la venta»   la línea del producto; el IMEI del equipo que se lleva se escanea como siempre
 *                          (SERIAL-PICK), en la misma cesta;
 *   «… bajo pedido»        si una oferta vigente la cubre (plazo y precio; nunca el stock del proveedor);
 *   «Llegó el equipo»      el equipo se registra aquí mismo — IMEI 1, IMEI 2, serie, proveedor, costo (sólo con
 *                          permiso) y precio — y entra a la venta identificado. Si la combinación aún no es una
 *                          variante, el servidor la crea (habilitada para la tienda física, nunca publicada en la web).
 *                          POS-ORDER-ARRIVAL-01: si un pedido pagado espera esa variante, primero se pregunta de quién
 *                          es: «Es del pedido #N (cliente)» lo entrega a su pedido (no entra a esta venta); «Ingresar
 *                          como stock» pide un motivo cuando el pedido espera en ESTA sucursal. Cómo se leyó el IMEI
 *                          (lector o a mano) viaja con el equipo.
 *
 * El servidor decide todo otra vez; esto sólo evita ofrecer lo que va a negar (AGENTS §7).
 */

import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import {
  PosDeviceReceiveError,
  fetchPosModelConfigurations,
  posModelSearch,
  posReceiveDevice,
  type PosConfigAttribute,
  type PosConfigVariant,
  type PosModelConfigurations,
  type PosModelHit,
  type PosPendingOrderLine,
  type PosProduct,
  type PosUnit,
} from "../../lib/internal-api";
import { ProductImage } from "@/app/components/ProductImage";
import { newSaleKey } from "../../lib/pos-in-flight";
import {
  NO_OPTION,
  autoFill,
  chosenCombination,
  isOptional,
  effectivePick,
  optionState,
  pickOption,
  receiveProblems,
  stepsToAsk,
  type Picked,
  type ReceiveField,
  type ReceiveForm,
} from "../../lib/pos-device-picker";
import { newRhythm, rhythmChanged, rhythmKey, rhythmMethod, type EntryMethod } from "../../lib/unit-entry";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "../../components/internal-ui";
import { leadTimeText } from "./PosOnOrder";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" }) : String(value);
}

const LABEL = "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted";
const EMPTY_FORM: ReceiveForm = { imei: "", imei2: "", serial: "", price: "", cost: "" };
/** The server asks the same of a reason to take a device as stock over a waiting order. */
const REASON_MIN = 5;
const REASON_MAX = 500;

type FormField = ReceiveField | "supplier" | "reason";

/** What the server calls each field, as this form calls it. */
const SERVER_FIELD: Record<string, FormField> = {
  imei: "imei", imei2: "imei2", serial_number: "serial", price: "price", cost: "cost", supplier: "supplier",
  reason: "reason",
};

/**
 * What the server answers when the order line chosen no longer waits for a device (received meanwhile, no longer
 * pending, the order no longer paid, a return on it, or out of reach): that line leaves the choice.
 */
const ORDER_LINE_GONE = new Set(["already_received", "wrong_state", "not_paid", "return_open", "not_on_order", "not_found"]);

/** Whose device it is: free stock, or the device a paid order waits for. */
type Target = { kind: "stock" } | { kind: "order"; line: PosPendingOrderLine };

function orderName(line: PosPendingOrderLine) {
  return `#${line.order_number}${line.customer ? ` (${line.customer})` : ""}`;
}

function productOf(variant: PosConfigVariant): PosProduct {
  return {
    id: variant.product_id, name: variant.name, price: variant.price, available: variant.available, barcode: "",
    is_serialized: variant.is_serialized, requires_imei: variant.requires_imei, on_order: variant.on_order,
    priced_by: "product",
  };
}

export function PosDevicePicker({
  companyId, branch, onAddProduct, onAddOnOrder, onReceived, focusRequested = false, onFocusHandled,
}: {
  companyId: number | null;
  branch: number | null;
  onAddProduct: (product: PosProduct) => void;
  onAddOnOrder: (product: PosProduct) => void;
  /** `method`: how the device's IMEI was read in the form — the scanner or by hand. */
  onReceived: (unit: PosUnit, product: PosProduct, method: EntryMethod) => void;
  /**
   * NAV-SALES-01 — «Venta rápida de equipo» (`?panel=equipo`): open the block, bring it into view and put the caret
   * in the model search (or on the block's button once a model is chosen). The till clears the request through
   * `onFocusHandled`, so a block mounted again later does not take the focus back from the scanner.
   */
  focusRequested?: boolean;
  onFocusHandled?: () => void;
}) {
  const panelId = useId();
  const [open, setOpen] = useState(focusRequested);
  const sectionRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const modelInputRef = useRef<HTMLInputElement>(null);
  // A request that arrives while mounted opens the block at once (adjusted while rendering, like the branch below).
  const [openedFor, setOpenedFor] = useState(focusRequested);
  if (focusRequested !== openedFor) {
    setOpenedFor(focusRequested);
    if (focusRequested) setOpen(true);
  }
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<PosModelHit[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [model, setModel] = useState<PosModelHit | null>(null);
  const [config, setConfig] = useState<PosModelConfigurations | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Picked>({});
  const [receiving, setReceiving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // The model search, a moment after the typing stops.
  useEffect(() => {
    if (!open || model || branch === null) return;
    const q = term.trim();
    let cancelled = false;
    const timer = setTimeout(() => {
      if (q.length < 2) {
        setHits([]);
        return;
      }
      void posModelSearch(companyId, branch, q)
        .then((rows) => { if (!cancelled) { setHits(rows); setSearchError(null); } })
        .catch((err) => { if (!cancelled) setSearchError(err instanceof Error ? err.message : "No se pudo buscar."); });
    }, 200);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [open, model, term, branch, companyId]);

  // What the configurations were read for: the model, the till's branch, and a counter a receipt bumps.
  const [reloads, setReloads] = useState(0);
  const modelId = model?.id ?? null;
  useEffect(() => {
    if (modelId === null || branch === null) return;
    let cancelled = false;
    void fetchPosModelConfigurations(companyId, branch, modelId)
      .then((answer) => { if (!cancelled) { setConfig(answer); setConfigError(null); } })
      .catch((err) => {
        if (!cancelled) setConfigError(err instanceof Error ? err.message : "No se pudieron leer las configuraciones.");
      });
    return () => { cancelled = true; };
  }, [modelId, branch, companyId, reloads]);

  // Review of #375: another branch is another shelf. What was read and chosen for the previous one is dropped at
  // once (adjusted while rendering, React's pattern for a prop that changes) and the model is read again above.
  // The branch NOW, for an answer that arrives later (a receipt in flight when the branch changed).
  const branchRef = useRef(branch);
  useEffect(() => { branchRef.current = branch; }, [branch]);
  // Shown above the steps: what happened to a receipt that answered after the branch changed.
  const [elsewhere, setElsewhere] = useState<string | null>(null);

  const [shownBranch, setShownBranch] = useState(branch);
  if (shownBranch !== branch) {
    setShownBranch(branch);
    setConfig(null);
    setPicked({});
    setReceiving(false);
    setNotice(null);
    setHits([]);
  }

  const chooseModel = (hit: PosModelHit) => {
    setElsewhere(null);
    setModel(hit);
    setPicked({});
    setReceiving(false);
    setNotice(null);
    setConfig(null);
  };

  const resetModel = () => {
    setModel(null);
    setConfig(null);
    setPicked({});
    setReceiving(false);
    setNotice(null);
  };

  const combination = useMemo(() => (config ? chosenCombination(config, picked) : null), [config, picked]);
  const variant = combination?.variant ?? null;

  // The model search is disabled until the till has a branch: the caret waits for it.
  useEffect(() => {
    if (!focusRequested || !open || branch === null) return;
    sectionRef.current?.scrollIntoView?.({ block: "start" });
    (modelInputRef.current ?? toggleRef.current)?.focus({ preventScroll: true });
    onFocusHandled?.();
  }, [focusRequested, open, branch, onFocusHandled]);

  return (
    <section ref={sectionRef} className="scroll-mt-24 rounded-xl border border-bd-border bg-surface">
      <button
        ref={toggleRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 rounded-xl px-4 py-3 text-left text-sm font-semibold text-foreground transition hover:bg-foreground/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/30"
      >
        <span>Vender equipo por modelo</span>
        <span aria-hidden="true" className={`text-muted transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}>⌄</span>
      </button>
      <div id={panelId} hidden={!open} className="space-y-4 border-t border-bd-border px-4 pb-4 pt-3">
        {elsewhere ? (
          <p className="rounded-lg border border-warning-border bg-warning-surface px-3 py-2 text-sm text-warning" role="status">
            {elsewhere}
          </p>
        ) : null}
        {!model ? (
          <div>
            <label className={LABEL} htmlFor={`${panelId}-model`}>Modelo</label>
            <input
              ref={modelInputRef}
              id={`${panelId}-model`}
              autoComplete="off"
              disabled={branch === null}
              className={internalInputClass}
              placeholder="Escribe el nombre del modelo"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
            {searchError ? <p className="mt-2 text-sm text-danger" role="alert">{searchError}</p> : null}
            {hits.length ? (
              <ul className="mt-2 overflow-hidden rounded-lg border border-bd-border">
                {hits.map((hit) => (
                  <li key={hit.id} className="border-b border-bd-border/70 last:border-0">
                    <button
                      type="button"
                      onClick={() => chooseModel(hit)}
                      className="flex w-full min-w-0 items-center justify-between gap-3 px-3 py-2.5 text-left text-sm transition hover:bg-foreground/[0.03] focus-visible:bg-foreground/[0.05] focus-visible:outline-none"
                    >
                      <span className="min-w-0 flex-1 break-words text-foreground">{hit.name}</span>
                      <span className={`shrink-0 text-xs ${hit.available > 0 ? "text-muted" : "text-danger"}`}>
                        {hit.available} en esta sucursal
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : term.trim().length >= 2 && !searchError ? (
              <p className="mt-2 text-xs text-muted">Ningún modelo con ese nombre.</p>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="min-w-0 break-words text-base font-semibold text-foreground">{model.name}</p>
            <button type="button" onClick={resetModel} className="text-xs font-semibold text-muted underline-offset-4 hover:text-foreground hover:underline">
              Cambiar modelo
            </button>
          </div>
        )}

        {configError ? <p className="text-sm text-danger" role="alert">{configError}</p> : null}

        {config ? (
          <>
            {config.combinations.length === 0 ? (
              <p className="text-sm text-muted">
                Este modelo todavía no tiene configuraciones. Defínelas en Catálogo › Modelos.
              </p>
            ) : null}
            {stepsToAsk(config).map((attribute) => (
              <OptionStep
                key={attribute.id}
                attribute={attribute}
                config={config}
                picked={picked}
                onPick={(optionId) => {
                  setReceiving(false);
                  setNotice(null);
                  setPicked((current) => autoFill(config, pickOption(config, current, attribute.id, optionId)));
                }}
              />
            ))}
            {combination ? (
              <section aria-label="Equipo elegido" className="rounded-lg border border-bd-border bg-background p-3">
                <ChosenSummary config={config} picked={picked} variant={variant} />
                <div className="mt-3 flex flex-wrap gap-2">
                  {variant && variant.sellable && variant.available > 0 ? (
                    <button type="button" className={internalPrimaryButtonClass} onClick={() => onAddProduct(productOf(variant))}>
                      Agregar a la venta
                    </button>
                  ) : null}
                  {variant?.on_order ? (
                    <button type="button" className={internalButtonClass} onClick={() => onAddOnOrder(productOf(variant))}>
                      + Bajo pedido ({leadTimeText(variant.on_order.lead_time_days)}) · {money(variant.on_order.price)}
                    </button>
                  ) : null}
                  {config.can.receive && (variant ? !variant.pending_draft && variant.is_active : config.can.create_variant) && !receiving ? (
                    <button type="button" className={internalButtonClass} onClick={() => { setReceiving(true); setNotice(null); }}>
                      Llegó el equipo
                    </button>
                  ) : null}
                </div>
                <ReceiveHint config={config} variant={variant} />
                {notice ? <p className="mt-3 rounded-lg border border-success-border bg-success-surface px-3 py-2 text-sm text-success" role="status">{notice}</p> : null}
                {receiving && branch !== null ? (
                  <ReceiveDeviceForm
                    key={combination.key}
                    companyId={companyId}
                    branch={branch}
                    config={config}
                    optionIds={combination.option_ids}
                    variant={variant}
                    pendingLines={combination.pending_order_lines ?? []}
                    onCancel={() => setReceiving(false)}
                    onDone={(result, sentFor) => {
                      if (result.mode === "order") {
                        // POS-ORDER-ARRIVAL-01: sold to its order on arrival — never into this sale, whatever branch.
                        const where = result.branch && result.branch.id !== sentFor ? ` Entró en ${result.branch.name}.` : "";
                        setReceiving(false);
                        setNotice(`${result.product.name}: equipo entregado al pedido #${result.order?.id ?? ""}. No se agregó a esta venta.${where}`);
                        setReloads((n) => n + 1);
                        return;
                      }
                      if (sentFor !== branchRef.current) {
                        // Re-review of #376: the device entered the shelf of the branch it was sent for. The till
                        // now sells from another one, so it never goes into this sale.
                        setElsewhere(`${result.product.name}: el equipo quedó registrado en la sucursal anterior y no se agregó a esta venta.`);
                        return;
                      }
                      setReceiving(false);
                      setNotice(`${result.product.name}: equipo registrado y agregado a la venta.${result.warnings.length ? ` ${result.warnings.join(" ")}` : ""}`);
                      onReceived(result.unit, result.product, result.method === "scanner" ? "scanner" : "manual");
                      setReloads((n) => n + 1);
                    }}
                  />
                ) : null}
              </section>
            ) : null}
          </>
        ) : model && !configError ? (
          <p className="text-sm text-muted" role="status">Cargando configuraciones…</p>
        ) : null}
      </div>
    </section>
  );
}

function OptionStep({
  attribute, config, picked, onPick,
}: {
  attribute: PosConfigAttribute;
  config: PosModelConfigurations;
  picked: Picked;
  onPick: (optionId: number) => void;
}) {
  const labelId = useId();
  const colour = attribute.role === "color" || attribute.kind === "color";
  return (
    <div>
      <p id={labelId} className={LABEL}>{attribute.name}</p>
      <div role="radiogroup" aria-label={attribute.name} className="flex flex-wrap gap-2">
        {[...attribute.options, ...(isOptional(config, attribute.id)
          // Re-review of #376: without a palette a variant may lack this role; «Sin …» reaches it (and unpicks).
          ? [{ id: NO_OPTION, label: `Sin ${attribute.name}`, swatch_hex: "" }] : [])].map((option) => {
          const state = optionState(config, picked, attribute.id, option.id);
          const checked = picked[attribute.id] === option.id;
          return (
            <label
              key={option.id}
              className={`relative inline-flex min-h-11 cursor-pointer select-none items-center gap-2 rounded-lg border px-3 py-2 text-sm transition has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-foreground/30 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-40 ${
                checked ? "border-foreground bg-foreground/[0.06] font-semibold text-foreground" : "border-bd-border bg-surface text-foreground hover:border-foreground/30"
              }`}
            >
              <input
                type="radio"
                className="sr-only"
                name={`attribute-${attribute.id}`}
                value={option.id}
                checked={checked}
                disabled={!state.possible}
                onChange={() => onPick(option.id)}
              />
              {/* G (owner's visual order 4): finish picture → company swatch → the name alone. */}
              {colour && "thumbnail" in option && option.thumbnail ? (
                <span aria-hidden="true" data-testid="pos-chip-picture" className="relative h-5 w-5 shrink-0 overflow-hidden rounded-full border border-foreground/20 bg-background">
                  {/* Review of #377: through the optimizer at chip size, never the original file. */}
                  <ProductImage src={option.thumbnail} alt="" sizes="20px" className="scale-[2.2] object-cover" />
                </span>
              ) : colour && option.swatch_hex ? (
                <span
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 rounded-full border border-foreground/20"
                  style={{ backgroundColor: option.swatch_hex }}
                />
              ) : null}
              <span>{option.label}</span>
              {/* Only a real count is drawn: «sin stock» on every chip would be noise; the chosen device says it. */}
              {state.possible && state.available > 0 ? (
                <span className="rounded-full bg-success-surface px-1.5 font-mono text-[11px] text-success">
                  {state.available}<span className="sr-only"> disponibles aquí</span>
                </span>
              ) : null}
              {!state.possible ? <span className="sr-only">no existe</span> : null}
            </label>
          );
        })}
      </div>
    </div>
  );
}

function ChosenSummary({ config, picked, variant }: {
  config: PosModelConfigurations;
  picked: Picked;
  variant: PosConfigVariant | null;
}) {
  const full = effectivePick(config, picked);
  const words = config.attributes
    .map((attribute) => attribute.options.find((option) => option.id === full[attribute.id])?.label)
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <p className="break-words text-sm font-semibold text-foreground">{variant ? variant.name : `${config.model.name} · ${words}`}</p>
        <p className="text-xs text-muted">{words}</p>
      </div>
      <div className="min-w-0 sm:max-w-[45%] sm:shrink-0 sm:text-right">
        {variant ? (
          <>
            <p className="font-mono text-sm text-foreground">{Number(variant.price) > 0 ? money(variant.price) : "sin precio"}</p>
            <p className={`text-xs ${variant.available > 0 ? "text-success" : "text-danger"}`}>
              {variant.available > 0 ? `${variant.available} en esta sucursal` : "Sin stock en esta sucursal"}
            </p>
            {variant.on_order ? (
              <p className="text-xs text-warning">Bajo pedido: {leadTimeText(variant.on_order.lead_time_days)}</p>
            ) : null}
          </>
        ) : (
          <p className="text-xs text-muted">Aún no es una variante: se crea al registrar el primer equipo.</p>
        )}
      </div>
    </div>
  );
}

function ReceiveHint({ config, variant }: { config: PosModelConfigurations; variant: PosConfigVariant | null }) {
  let text: string | null = null;
  if (variant?.pending_draft) {
    text = "Esta variante es un borrador sin habilitar para la venta: habilítala en Catálogo › Modelos.";
  } else if (variant && !variant.is_active) {
    text = "Esta variante está inactiva: actívala en Catálogo antes de recibir equipos.";
  } else if (!config.can.receive) {
    text = "Para registrar un equipo que llegó, pide a un usuario autorizado que lo reciba («Mover inventario»).";
  } else if (!variant && !config.can.create_variant) {
    text = "Esta combinación aún no existe como variante: pide a un usuario autorizado («Administrar productos») que reciba el equipo.";
  }
  return text ? <p className="mt-3 text-xs text-muted">{text}</p> : null;
}

function ReceiveDeviceForm({
  companyId, branch, config, optionIds, variant, pendingLines, onCancel, onDone,
}: {
  companyId: number | null;
  branch: number;
  config: PosModelConfigurations;
  optionIds: number[];
  variant: PosConfigVariant | null;
  /** POS-ORDER-ARRIVAL-01: the paid orders that wait for a device of this combination. */
  pendingLines: PosPendingOrderLine[];
  onCancel: () => void;
  /** `sentFor`: the branch the device was received at (the till's when the form was sent). */
  onDone: (result: Awaited<ReturnType<typeof posReceiveDevice>>, sentFor: number) => void;
}) {
  const id = useId();
  // What waits for this device: the configurations' lines, or the ones a 409 named (another till sold one meanwhile).
  const [waiting, setWaiting] = useState<PosPendingOrderLine[]>(pendingLines);
  // With an order waiting, the seller first says whose device it is; with none, it is stock.
  const [target, setTarget] = useState<Target | null>(pendingLines.length ? null : { kind: "stock" });
  const toOrder = target?.kind === "order" ? target.line : null;
  // Stock over an order that waits HERE is refused by the server unless confirmed with a reason.
  const needsReason = target?.kind === "stock" && waiting.some((line) => line.here);
  const priceEditable = !toOrder && (!variant || config.can.set_price);
  const [form, setForm] = useState<ReceiveForm>({ ...EMPTY_FORM, price: variant && priceEditable ? variant.price : "" });
  const [supplier, setSupplier] = useState("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Partial<Record<FormField, string>>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // One key per device being registered: a retry after a failure or a timeout carries the SAME key, so the server
  // answers with the device it already registered instead of registering a second one.
  const keyRef = useRef<string | null>(newSaleKey());
  const imeiRef = useRef<HTMLInputElement | null>(null);
  // How each identifier arrived, measured in its keys: a scanner writes in a burst (SERIAL-PICK's rule).
  const rhythms = useRef({ imei: newRhythm(), serial: newRhythm() });

  useEffect(() => { if (target) imeiRef.current?.focus(); }, [target]);

  const set = (field: ReceiveField) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    if (field === "imei" || field === "serial") rhythmChanged(rhythms.current[field], value);
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  };

  // A scanner ends the IMEI with Enter: that moves to the next field, it never sends a half-filled form.
  const next = (field: ReceiveField) => (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") {
      if (field === "imei" || field === "serial") rhythmKey(rhythms.current[field], event.key, performance.now());
      return;
    }
    event.preventDefault();
    const fields = Array.from(event.currentTarget.form?.querySelectorAll<HTMLElement>("input, select") ?? []);
    fields[fields.indexOf(event.currentTarget) + 1]?.focus();
  };

  // The first field that is wrong takes the caret, so the person corrects it without hunting for it.
  const focusFirst = (problems: Partial<Record<FormField, string>>) => {
    const first = (["imei", "imei2", "serial", "supplier", "cost", "price", "reason"] as const).find((name) => problems[name]);
    if (first) document.getElementById(`${id}-${first}`)?.focus();
  };

  const choose = (value: Target | null) => {
    setTarget(value);
    setErrors({});
    setFailure(null);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || !target) return;
    // Delivered to its order the device has no price here (the field is hidden): what was typed for stock is ignored.
    const problems: Partial<Record<FormField, string>> = receiveProblems(
      toOrder ? { ...form, price: "" } : form, { needsPrice: !toOrder && !variant },
    );
    const why = reason.split(/\s+/).filter(Boolean).join(" ");
    if (needsReason && why.length < REASON_MIN) {
      problems.reason = `Escribe por qué entra como stock (al menos ${REASON_MIN} caracteres).`;
    }
    setErrors(problems);
    setFailure(null);
    if (Object.keys(problems).length) {
      focusFirst(problems);
      return;
    }
    if (!keyRef.current) {
      setFailure("Este navegador no puede generar una clave segura para registrar el equipo.");
      return;
    }
    const imei = form.imei.trim();
    const serial = form.serial.trim();
    const body: Parameters<typeof posReceiveDevice>[2] = toOrder
      ? { order_item: toOrder.order_item }
      : { model: config.model.id, option_ids: optionIds };
    if (imei) body.imei = imei;
    if (form.imei2.trim()) body.imei2 = form.imei2.trim();
    if (serial) body.serial_number = serial;
    if (config.can.view_costs && form.cost.trim()) body.cost = form.cost.trim();
    if (!toOrder) {
      const price = form.price.trim();
      if (priceEditable && price && (!variant || Number(price) !== Number(variant.price))) body.price = price;
      if (supplier) body.supplier = Number(supplier);
      if (needsReason) {
        body.confirm_stock = true;
        body.reason = why;
      }
    }
    body.method = imei ? rhythmMethod(rhythms.current.imei, imei) : rhythmMethod(rhythms.current.serial, serial);
    setBusy(true);
    try {
      const result = await posReceiveDevice(companyId, branch, body, keyRef.current);
      keyRef.current = newSaleKey();
      onDone(result, branch);
    } catch (err) {
      if (err instanceof PosDeviceReceiveError && err.code === "idempotency_conflict") {
        // The key already registered a device with other data (an earlier try may have gone through): a new key for
        // the next try, and the seller checks the shelf first instead of registering the same device twice.
        keyRef.current = newSaleKey();
        setFailure("Este equipo pudo quedar registrado en un intento anterior: escanéalo en «Escanear código» antes de registrarlo otra vez.");
      } else if (toOrder && err instanceof PosDeviceReceiveError && ORDER_LINE_GONE.has(err.code)) {
        // Review of #400: that line no longer waits — it leaves the choice; with none left, the device is plain stock
        // (no reason asked). Nothing was written, so the same key serves the next try.
        const left = waiting.filter((line) => line.order_item !== toOrder.order_item);
        setWaiting(left);
        setTarget(left.length ? null : { kind: "stock" });
        setFailure(err.message);
      } else if (err instanceof PosDeviceReceiveError && err.code === "pending_order_line") {
        // A paid order waits for this device (it may have been sold after the picker was read): ask again whose it
        // is. Nothing was written, so the same key serves the next try.
        setWaiting(err.lines);
        setTarget(null);
        setFailure(err.message);
      } else if (err instanceof PosDeviceReceiveError && SERVER_FIELD[err.field]) {
        const problems = { [SERVER_FIELD[err.field]]: err.message };
        setErrors(problems);
        focusFirst(problems);
      } else {
        setFailure(err instanceof Error ? err.message : "No se pudo registrar el equipo.");
      }
    } finally {
      setBusy(false);
    }
  };

  const field = (name: ReceiveField, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}, hint?: string) => {
    const error = errors[name];
    const describedBy = [hint ? `${id}-${name}-hint` : "", error ? `${id}-${name}-error` : ""].filter(Boolean).join(" ") || undefined;
    return (
      <div>
        <label className={LABEL} htmlFor={`${id}-${name}`}>{label}</label>
        <input
          id={`${id}-${name}`}
          name={name}
          ref={name === "imei" ? imeiRef : undefined}
          autoComplete="off"
          className={`${internalInputClass} ${error ? "border-danger-border" : ""}`}
          value={form[name]}
          onChange={set(name)}
          onKeyDown={next(name)}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...props}
        />
        {hint ? <p id={`${id}-${name}-hint`} className="mt-1 text-[11px] text-muted">{hint}</p> : null}
        {error ? <p id={`${id}-${name}-error`} className="mt-1 text-xs text-danger">{error}</p> : null}
      </div>
    );
  };

  const failureBox = failure
    ? <p className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-sm text-danger" role="alert">{failure}</p>
    : null;

  if (!target) {
    const canDeliver = Boolean(config.can.receive_on_order);
    return (
      <section aria-labelledby={`${id}-whose`} className="mt-4 space-y-3 border-t border-bd-border pt-4">
        <p id={`${id}-whose`} className="text-sm font-semibold text-foreground">¿Es el equipo de un pedido?</p>
        <p className="text-xs text-muted">
          {waiting.length === 1 ? "Un pedido pagado espera" : `${waiting.length} pedidos pagados esperan`} un equipo de esta
          variante. Si es el suyo, se entrega a su pedido y no entra a esta venta.
        </p>
        {failureBox}
        <ul className="space-y-2">
          {waiting.map((line) => (
            <li key={line.order_item} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
              {canDeliver && line.quantity === 1 ? (
                <button type="button" className={internalPrimaryButtonClass} onClick={() => choose({ kind: "order", line })}>
                  Es del pedido {orderName(line)}
                </button>
              ) : (
                <span className="text-sm text-foreground">Pedido {orderName(line)}</span>
              )}
              <span className="min-w-0 break-words text-xs text-muted">
                {line.quantity > 1 ? `Lleva ${line.quantity} equipos: recíbela desde el pedido. ` : ""}
                {line.here ? "Se despacha desde esta sucursal." : `Se despacha desde ${line.branch.name || "otra sucursal"}.`}
              </span>
            </li>
          ))}
        </ul>
        {!canDeliver ? (
          <p className="text-xs text-muted">
            Entregarlo a su pedido requiere «Mover inventario» y «Administrar pedidos»: pide a un usuario autorizado que lo reciba.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <button type="button" className={internalButtonClass} onClick={() => choose({ kind: "stock" })}>
            Ingresar como stock
          </button>
          <button type="button" onClick={onCancel} className={internalButtonClass}>Cancelar</button>
        </div>
      </section>
    );
  }

  return (
    <form onSubmit={submit} noValidate className="mt-4 space-y-3 border-t border-bd-border pt-4" aria-label="Llegó el equipo">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-foreground">
          {toOrder ? `Llegó el equipo del pedido ${orderName(toOrder)}` : "Llegó el equipo"}
        </p>
        {waiting.length ? (
          <button type="button" onClick={() => choose(null)} disabled={busy}
            className="text-xs font-semibold text-muted underline-offset-4 hover:text-foreground hover:underline">
            Cambiar
          </button>
        ) : null}
      </div>
      {toOrder ? (
        <p className="text-xs text-muted">
          Se registra y queda vendido a ese pedido{toOrder.here ? "" : `, en ${toOrder.branch.name || "su sucursal"}`}; no entra a esta venta.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {field("imei", "IMEI 1", { inputMode: "numeric", placeholder: "15 dígitos", className: `${internalInputClass} font-mono ${errors.imei ? "border-danger-border" : ""}` })}
        {field("imei2", "IMEI 2 (si lo tiene)", { inputMode: "numeric", placeholder: "15 dígitos", className: `${internalInputClass} font-mono ${errors.imei2 ? "border-danger-border" : ""}` },
          "Segundo IMEI del mismo equipo: no es una segunda SIM física.")}
        {field("serial", "Serie (si la tiene)", { className: `${internalInputClass} font-mono uppercase ${errors.serial ? "border-danger-border" : ""}` })}
        {!toOrder && config.can.choose_supplier ? (
          config.suppliers?.length ? (
            <div>
              <label className={LABEL} htmlFor={`${id}-supplier`}>Proveedor (opcional)</label>
              <select
                id={`${id}-supplier`}
                name="supplier"
                className={internalInputClass}
                value={supplier}
                onChange={(e) => { setSupplier(e.target.value); setErrors((c) => ({ ...c, supplier: undefined })); }}
                aria-invalid={errors.supplier ? true : undefined}
              >
                <option value="">Sin indicar</option>
                {config.suppliers.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
              </select>
              {errors.supplier ? <p className="mt-1 text-xs text-danger">{errors.supplier}</p> : null}
            </div>
          ) : (
            // D-F-10: the receiver always sees the field; with no active supplier it says where one is created.
            <div>
              <p className={LABEL}>Proveedor (opcional)</p>
              <p className="py-2 text-xs text-muted">
                No hay proveedores activos: créalo en{" "}
                {config.can.open_suppliers ? (
                  <Link href="/admin/products/suppliers" className="font-semibold text-foreground underline underline-offset-4">
                    Proveedores
                  </Link>
                ) : "Proveedores"}
                .
              </p>
            </div>
          )
        ) : null}
        {config.can.view_costs
          ? field("cost", "Costo unitario (opcional)", { inputMode: "decimal", placeholder: "p. ej. 3100.00", className: `${internalInputClass} font-mono` })
          : null}
        {toOrder ? null : priceEditable ? (
          field("price", "Precio de venta", { inputMode: "decimal", placeholder: "p. ej. 3899.00", className: `${internalInputClass} font-mono ${errors.price ? "border-danger-border" : ""}` },
            variant ? "Cambiarlo cambia el precio de esta variante." : "Precio de la variante nueva en tienda física. No se publica en la web.")
        ) : variant ? (
          <div>
            <p className={LABEL}>Precio de venta</p>
            <p className="py-2.5 font-mono text-sm text-foreground">{Number(variant.price) > 0 ? money(variant.price) : "sin precio"}</p>
          </div>
        ) : null}
      </div>
      {needsReason ? (
        <div>
          <label className={LABEL} htmlFor={`${id}-reason`}>Motivo para ingresarlo como stock</label>
          <input
            id={`${id}-reason`}
            name="reason"
            autoComplete="off"
            maxLength={REASON_MAX}
            className={`${internalInputClass} ${errors.reason ? "border-danger-border" : ""}`}
            value={reason}
            onChange={(e) => { setReason(e.target.value); setErrors((c) => ({ ...c, reason: undefined })); }}
            aria-invalid={errors.reason ? true : undefined}
            aria-describedby={`${id}-reason-hint${errors.reason ? ` ${id}-reason-error` : ""}`}
          />
          <p id={`${id}-reason-hint`} className="mt-1 text-[11px] text-muted">
            Un pedido pagado espera esta variante en esta sucursal. El motivo queda registrado.
          </p>
          {errors.reason ? <p id={`${id}-reason-error`} className="mt-1 text-xs text-danger">{errors.reason}</p> : null}
        </div>
      ) : null}
      {failureBox}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className={internalPrimaryButtonClass}>
          {busy ? "Registrando…" : toOrder ? `Registrar y entregar al pedido #${toOrder.order_number}` : "Registrar y agregar a la venta"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className={internalButtonClass}>Cancelar</button>
      </div>
    </form>
  );
}
