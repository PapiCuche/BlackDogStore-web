"use client";

/**
 * SCAN-TO-STOCK-01 — «Registrar equipo por escaneo».
 *
 * Sucursal → nuevo o seminuevo → modelo y variante (o el código comercial de la caja) → IMEI 1 → IMEI 2 → serie →
 * proveedor y datos → revisión → «Confirmar ingreso». Un solo campo recibe todas las lecturas del lector (o lo que se
 * escribe a mano); el servidor dice qué es cada código y la pantalla lo coloca, o pregunta si no es seguro
 * (`lib/scan-intake`). Ninguna lectura confirma: sólo el botón. El foco vuelve solo al campo del lector.
 *
 * Registrar usa lo que ya existe: un equipo nuevo es «Llegó el equipo» de la caja (variante de la paleta, pedido pagado
 * que lo espera, Kardex, etiqueta), un seminuevo entra a su variante de seminuevos pendiente de inspección. El servidor
 * decide todo otra vez (AGENTS §7).
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { ErrorBox } from "./InventoryUi";
import { NextStepBar, extractLabelBatch } from "./NextStepBar";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import type { PosModelHit, PosPendingOrderLine } from "../lib/internal-api";
import {
  NO_OPTION, autoFill, chosenCombination, isOptional, optionState, pickOption, stepsToAsk, type Picked,
} from "../lib/pos-device-picker";
import { optionState as configuratorOptionState } from "../lib/device-configurator";
import { ConfiguratorStep } from "./DeviceConfigurator";
import {
  CHOICE_LABEL, EMPTY_IDENTIFIERS, ReadQueue, SLOT_LABEL, ScanIntakeError, applyOutcome, decide, expectedSlot,
  isTerminator, readTerminator, reviewProblems, saveTerminator, scanCheck, scanConfigurations, scanModelSearch,
  scanReceive, pickOfVariant, usedSpace, type Choice, type Identifiers, type ScanConfigurations, type ScanProduct,
  type ScanReceived, type Slot, type Terminator,
} from "../lib/scan-intake";
import { newRhythm, resetRhythm, rhythmChanged, rhythmKey, rhythmMethod, type EntryMethod } from "../lib/unit-entry";
import { useCosmeticGrades } from "../lib/use-cosmetic-grades";

type Tone = "ok" | "warn" | "error" | "info";
type LogLine = { id: number; tone: Tone; text: string };
type Ask = { value: string; options: Choice[]; message: string; method: EntryMethod };
type Condition = "new" | "used";

/** The refusals that mean «this identifier is already a device» (K4). */
const DUPLICATE_CODES = new Set(["duplicate_imei", "duplicate_serial"]);

/** Where a key is never routed to the scanner field (G2): someone is using that widget. */
const BLOCKED_ROUTING = '[role="dialog"], [role="menu"], [role="combobox"], [role="searchbox"], [role="listbox"]';

const TONE_CLASS: Record<Tone, string> = {
  ok: "border-success-border bg-success-surface text-success",
  warn: "border-warning-border bg-warning-surface text-foreground",
  error: "border-danger-border bg-danger-surface text-danger",
  info: "border-bd-border bg-surface text-foreground",
};

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `scan-${Date.now()}-${Math.random()}`;
}

export function ScanIntake({
  companyId, branches, defaultBranch, canPrintLabels,
}: {
  companyId: number | null;
  branches: { id: number; name: string }[];
  defaultBranch: number | null;
  canPrintLabels: boolean;
}) {
  const scanId = useId();
  const sectionRef = useRef<HTMLDivElement>(null);
  const scanRef = useRef<HTMLInputElement>(null);
  const rhythm = useRef(newRhythm());
  const queue = useRef(new ReadQueue());

  const [branchId, setBranchId] = useState<number | null>(defaultBranch ?? branches[0]?.id ?? null);
  const [condition, setCondition] = useState<Condition>("new");
  // Per browser (a scanner is configured once per counter); without storage, Enter.
  const [terminator, setTerminator] = useState<Terminator>(() => (typeof window === "undefined" ? "enter" : readTerminator()));
  const [modelQuery, setModelQuery] = useState("");
  const [hits, setHits] = useState<PosModelHit[]>([]);
  const [config, setConfig] = useState<ScanConfigurations | null>(null);
  const [picked, setPicked] = useState<Picked>({});
  const [usedVariantId, setUsedVariantId] = useState<number | null>(null);
  /** SELECTOR-UX-01 · C: the steps chosen on the used line's configurator while no variant is reached yet. */
  const [usedPicked, setUsedPicked] = useState<Picked>({});
  const [ids, setIds] = useState<Identifiers>(EMPTY_IDENTIFIERS);
  const idsRef = useRef<Identifiers>(EMPTY_IDENTIFIERS);
  const [methods, setMethods] = useState<Partial<Record<Slot, EntryMethod>>>({});
  const [noImei2, setNoImei2] = useState(false);
  const [noSerial, setNoSerial] = useState(false);
  const [scanText, setScanTextState] = useState("");
  // The scanner's buffer, read by key handlers outside the input (F2/F3) without waiting for a render.
  const scanTextRef = useRef("");
  const setScanText = useCallback((value: string) => {
    scanTextRef.current = value;
    setScanTextState(value);
  }, []);
  // F1: every read belongs to one device of one model at one branch. Changing any of them bumps this, and an answer
  // that comes back for an older generation is dropped (and said), never written into the new device.
  const generation = useRef(0);
  // F4: only the latest model configuration request may land.
  const configRequest = useRef(0);
  const askRef = useRef<Ask | null>(null);
  // F7: on a touch screen the scanner field does not raise the on-screen keyboard unless the person asks for it.
  const [coarse] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(pointer: coarse)").matches);
  const [keyboard, setKeyboard] = useState(false);
  const [serialConfirmed, setSerialConfirmed] = useState(false);
  const [log, setLog] = useState<LogLine[]>([]);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [busyReads, setBusyReads] = useState(0);
  const [supplier, setSupplier] = useState("");
  const [cost, setCost] = useState("");
  const [price, setPrice] = useState("");
  const [battery, setBattery] = useState("");
  const [grade, setGrade] = useState("");
  const [functional, setFunctional] = useState("full");
  const [defects, setDefects] = useState("");
  const [accessories, setAccessories] = useState("");
  const [salePrice, setSalePrice] = useState("");
  const [warranty, setWarranty] = useState("");
  const [orderChoice, setOrderChoice] = useState<number | "stock" | null>(null);
  const [stockReason, setStockReason] = useState("");
  const [serverLines, setServerLines] = useState<PosPendingOrderLine[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ message: string; field: string } | null>(null);
  const [result, setResult] = useState<ScanReceived | null>(null);
  const idemKey = useRef(newKey());
  // The body last sent with `idemKey`: the same key never carries another body (the server would answer 409).
  const keyBody = useRef<string | null>(null);
  // A second click before React re-renders must not send twice (the key would replay it, but nothing should go).
  const inFlight = useRef(false);
  // K4: a 5xx (or a lost answer) left it unknown whether this device entered.
  const uncertain = useRef(false);
  const logId = useRef(0);

  const grades = useCosmeticGrades(condition === "used", companyId);
  const can = config?.can;
  const combination = config && condition === "new" ? chosenCombination(config, picked) : null;
  const usedVariant = config?.used_variants.find((v) => v.id === usedVariantId) ?? null;
  const used = useMemo(() => (config ? usedSpace(config) : null), [config]);
  const usedShown: Picked = used && usedVariant ? pickOfVariant(used, usedVariant) : usedPicked;
  const variantProductId = condition === "new" ? combination?.variant?.product_id ?? null : usedVariant?.id ?? null;
  const variantChosen = condition === "new" ? Boolean(combination) : Boolean(usedVariant);
  const requiresImei = condition === "new" ? combination?.variant?.requires_imei ?? true : usedVariant?.requires_imei ?? true;
  const plan = useMemo(() => ({ requiresImei, noImei2, noSerial }), [requiresImei, noImei2, noSerial]);
  const expected = expectedSlot(ids, plan);
  const newCombination = condition === "new" && Boolean(combination) && !combination?.variant;
  // F8: the price of a new variant is asked only of who may create it; anybody else is told before confirming.
  const needsPrice = newCombination && Boolean(can?.create_variant);
  const createForbidden = newCombination && !can?.create_variant;
  const waiting = useMemo(() => {
    const fromConfig = condition === "new" ? (combination?.pending_order_lines ?? []).filter((line) => line.here) : [];
    return serverLines.length ? serverLines : fromConfig;
  }, [condition, combination, serverLines]);

  const openAsk = useCallback((next: Ask | null) => {
    askRef.current = next;
    setAsk(next);
  }, []);

  /** F1: a new device, model or branch — what is still on its way belongs to the previous one. */
  const newGeneration = useCallback(() => {
    generation.current += 1;
  }, []);

  const focusScan = useCallback(() => {
    window.setTimeout(() => scanRef.current?.focus(), 0);
  }, []);

  const say = useCallback((tone: Tone, text: string) => {
    logId.current += 1;
    const line = { id: logId.current, tone, text };
    setLog((lines) => [line, ...lines].slice(0, 6));
  }, []);

  const writeIds = useCallback((next: Identifiers) => {
    idsRef.current = next;
    setIds(next);
  }, []);

  /**
   * `byScan`: the model comes from a commercial code just read (G7). That is not the clerk changing device: reads
   * already on their way (the IMEI scanned right after the box code) stay valid. A model chosen by hand starts over.
   */
  const loadModel = useCallback(async (modelId: number, preset?: ScanProduct, byScan = false) => {
    if (!branchId) return;
    const request = ++configRequest.current;
    if (!byScan) newGeneration();
    try {
      const data = await scanConfigurations(companyId, branchId, modelId);
      if (request !== configRequest.current) return;      // F4: a later choice already asked
      setConfig(data);
      setHits([]);
      setModelQuery(data.model.name);
      setServerLines([]);
      setOrderChoice(null);
      if (preset && preset.condition_line === "used") {
        setCondition("used");
        setUsedVariantId(preset.id);
        setUsedPicked({});
      } else if (preset) {
        setCondition("new");
        const next: Picked = {};
        for (const attribute of data.attributes) {
          const option = attribute.options.find((o) => preset.option_ids.includes(o.id));
          if (option) next[attribute.id] = option.id;
        }
        setPicked(next);
      } else {
        setPicked(autoFill(data, {}));
        setUsedVariantId(data.used_variants.length === 1 ? data.used_variants[0].id : null);
        setUsedPicked({});
      }
    } catch (err) {
      if (request === configRequest.current) say("error", err instanceof Error ? err.message : "No se pudo leer el modelo.");
    }
  }, [branchId, companyId, newGeneration, say]);

  // The model search: from two letters, a short pause after the last key. Stale answers are dropped.
  const searching = !branchId || modelQuery.trim().length < 2 || config?.model.name === modelQuery ? null : modelQuery.trim();
  useEffect(() => {
    if (!searching || !branchId) return;
    let live = true;
    const timer = window.setTimeout(() => {
      scanModelSearch(companyId, branchId, searching)
        .then((rows) => { if (live) setHits(rows); })
        .catch(() => { if (live) setHits([]); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [branchId, companyId, searching]);
  const shownHits = searching ? hits : [];

  const handleCheck = useCallback((check: Awaited<ReturnType<typeof scanCheck>>, method: EntryMethod, choice?: Choice) => {
    const outcome = decide(check, idsRef.current);
    if (outcome.type === "filled") {
      writeIds(applyOutcome(idsRef.current, outcome));
      // F13: «lector» / «a mano» is a label of how the code arrived (SERIAL-PICK). It never gates anything here: what
      // a code IS comes from the server's classifier, never from the rhythm of the keys.
      setMethods((m) => ({ ...m, [outcome.slot]: method }));
      // B2: a Luhn-looking number goes in the serial slot only because the person said «Es la serie».
      if (outcome.slot === "serial") setSerialConfirmed(choice === "serial");
      setError(null);
      say("ok", outcome.message);
      if (outcome.warning) say("warn", outcome.warning);
    } else if (outcome.type === "repeat") {
      say("info", outcome.message);
    } else if (outcome.type === "product") {
      say("ok", outcome.message);
      const product = outcome.product;
      if (!product.model_id) {
        say("warn", "Esa variante no pertenece a un modelo: regístrala desde «Registrar artículo».");
      } else {
        void loadModel(product.model_id, product, true);
      }
    } else if (outcome.type === "ask") {
      openAsk({ value: outcome.value, options: outcome.options, message: outcome.message, method });
    } else {
      say("error", outcome.message);
    }
    focusScan();
  }, [focusScan, loadModel, openAsk, say, writeIds]);

  const read = useCallback((text: string, method: EntryMethod, choice?: Choice) => {
    if (askRef.current && !choice) {
      // F5: one question at a time. The new read is not sent; the person answers first and reads it again.
      say("warn", `Responde primero la pregunta sobre ${askRef.current.value}; después vuelve a leer ${text}.`);
      return;
    }
    const born = generation.current;
    setBusyReads((n) => n + 1);
    void queue.current.push(async () => {
      try {
        const check = await scanCheck(companyId, text, {
          productId: variantProductId, choice, expected: choice ? null : expectedSlot(idsRef.current, plan),
        });
        if (born !== generation.current) {
          say("info", `Lectura de ${text} descartada: llegó después de cambiar de equipo, modelo o sucursal.`);
          return;
        }
        handleCheck(check, method, choice);
      } catch (err) {
        if (born === generation.current) say("error", err instanceof Error ? err.message : "No se pudo consultar el código.");
        focusScan();
      } finally {
        setBusyReads((n) => n - 1);
      }
    });
  }, [companyId, focusScan, handleCheck, plan, say, variantProductId]);

  const takeRead = useCallback((value: string) => {
    const method = rhythmMethod(rhythm.current, value);
    resetRhythm(rhythm.current);
    setScanText("");
    read(value.trim(), method);
  }, [read, setScanText]);

  const onScanKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing) return;
    rhythmKey(rhythm.current, event.key, event.timeStamp);
    const value = event.currentTarget.value;
    if (event.key === "Escape") {
      // F9: Esc empties the field (a half read, a wrong key) without leaving it.
      event.preventDefault();
      resetRhythm(rhythm.current);
      setScanText("");
      return;
    }
    if (event.key === "Enter" && !value.trim()) {
      // A double Enter, or an Enter on an empty field: nothing — and never a confirmation.
      event.preventDefault();
      return;
    }
    if (isTerminator(event.key, terminator, value, event)) {
      event.preventDefault();
      takeRead(value);
      return;
    }
    if (event.key === "Tab" && !event.shiftKey && value.trim()) {
      // G5/G6: Tab leaves the field as always; what was typed and not read is not kept to glue onto the next read.
      // K3: only forward. Shift+Tab goes back to fix something and keeps the text.
      resetRhythm(rhythm.current);
      setScanText("");
      say("info", `Se descartó lo escrito («${value.trim()}»): para leerlo, pulsa Enter o «Leer».`);
    }
  };

  /** G5: the explicit way to read what was typed by hand. */
  const readTyped = () => {
    const value = scanTextRef.current;
    if (value.trim()) takeRead(value);
    scanRef.current?.focus();
  };

  // F3 + G1–G3: a scanner types wherever the focus is. Keys are routed to the scanner field ONLY when they land on the
  // page itself (body) or on a control of THIS screen that is not a text field: a button, a radio, a select (a read
  // must not type-ahead a branch or a supplier). Never from the shell (sidebar, top bar, notifications), a dialog, a
  // menu, a combobox, a search box or a list box. Space is never routed from a button, link, radio or checkbox: it
  // activates them as always. Enter there, with characters in the field, ends that read — it never presses the focused
  // button («Confirmar ingreso», an option of a question) nor changes a select.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const input = scanRef.current;
      if (!input || event.defaultPrevented || event.isComposing) return;
      const target = event.target as HTMLElement | null;
      if (!target || target === input) return;
      const onPage = target === document.body || target === document.documentElement;
      if (!onPage && !sectionRef.current?.contains(target)) return;
      if (target.closest?.(BLOCKED_ROUTING)) return;
      const tag = target.tagName;
      const type = (target as HTMLInputElement).type ?? "";
      const typing = tag === "TEXTAREA" || target.isContentEditable
        || (tag === "INPUT" && !["button", "submit", "radio", "checkbox", "reset"].includes(type));
      if (typing) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const activates = tag === "BUTTON" || tag === "A" || target.getAttribute("role") === "button"
        || (tag === "INPUT" && ["radio", "checkbox", "button", "submit", "reset"].includes(type));
      // G1 + K2: Space activates a button, link, radio or checkbox and opens a select, as always; and from the page it
      // only counts inside a read already begun (a scanner never starts a code with a space).
      if (event.key === " " && (activates || tag === "SELECT" || (onPage && !scanTextRef.current))) return;
      if (event.key.length === 1) {
        event.preventDefault();
        rhythmKey(rhythm.current, event.key, event.timeStamp);
        setScanText(scanTextRef.current + event.key);
        rhythm.current.typed = scanTextRef.current;
        return;
      }
      if (event.key === "Enter" && scanTextRef.current.trim()) {
        event.preventDefault();
        event.stopPropagation();
        takeRead(scanTextRef.current);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [setScanText, takeRead]);

  // F12: a registration on its way is not abandoned by leaving the page: the browser asks, and links wait.
  useEffect(() => {
    if (!submitting) return;
    const onLeave = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const onClick = (event: MouseEvent) => {
      const link = (event.target as HTMLElement | null)?.closest?.("a[href]");
      if (link) {
        event.preventDefault();
        say("warn", "Espera a que termine el registro del equipo antes de salir.");
      }
    };
    window.addEventListener("beforeunload", onLeave);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onLeave);
      document.removeEventListener("click", onClick, true);
    };
  }, [say, submitting]);

  // G8 + K1 + L2/L4: «atrás» of the browser while registering. Each submit pushes ONE guard entry of this page (the
  // router's own state kept, plus its own id) and listens to popstate:
  //   - landing on ANY guard of this page (an earlier submit's own `back()` still on its way) is staying here: nothing;
  //   - landing anywhere else is leaving: the page's address, captured when the submit began, is pushed back and the
  //     person is told.
  // Only the submit that pushed a guard removes it, when it ends, and only if that guard is still on top. Unmounting
  // (another screen, a reset) never navigates.
  const guardSeq = useRef(0);
  const guardCleanup = useRef<(() => void) | null>(null);
  // SELECTOR-UX-01 · C (final review of #437): a guard never navigates once the screen is gone, and a guard is never
  // pushed while an earlier guard's own `back()` is still on its way (it would be left as a forward entry).
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const traversal = useRef<Promise<void> | null>(null);
  const startHistoryGuard = useCallback(async () => {
    if (traversal.current) await traversal.current;
    const id = ++guardSeq.current;
    const here = window.location.href;
    const guard = { ...(window.history.state ?? {}), scanIntakeGuard: id };
    window.history.pushState(guard, "", here);
    const onBack = (event: PopStateEvent) => {
      const state = event.state as { scanIntakeGuard?: number } | null;
      if (state?.scanIntakeGuard) return;
      window.history.pushState(guard, "", here);
      say("warn", "Espera a que termine el registro del equipo antes de salir.");
    };
    window.addEventListener("popstate", onBack);
    guardCleanup.current = () => window.removeEventListener("popstate", onBack);
    return () => {
      window.removeEventListener("popstate", onBack);
      guardCleanup.current = null;
      if (!mounted.current) return;
      if ((window.history.state as { scanIntakeGuard?: number } | null)?.scanIntakeGuard !== id) return;
      traversal.current = new Promise<void>((resolve) => {
        const landed = () => {
          window.removeEventListener("popstate", landed);
          window.clearTimeout(timer);
          traversal.current = null;
          resolve();
        };
        // A browser that never answers the traversal does not hold the next registration for long.
        const timer = window.setTimeout(landed, 1000);
        window.addEventListener("popstate", landed);
      });
      window.history.back();
    };
  }, [say]);
  useEffect(() => () => guardCleanup.current?.(), []);

  const choose = (choice: Choice) => {
    const current = askRef.current;
    if (!current) return;
    openAsk(null);
    read(current.value, current.method, choice);
    focusScan();
  };

  /** F2: a choice is a deliberate click (or Enter/Space with the scanner field empty), never a scanner's Enter. */
  const guardChoiceKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if ((event.key === "Enter" || event.key === " ") && scanTextRef.current.trim()) event.preventDefault();
  };

  const clearSlot = (slot: Slot) => {
    writeIds({ ...idsRef.current, [slot]: "" });
    setMethods((m) => ({ ...m, [slot]: undefined }));
    if (slot === "serial") setSerialConfirmed(false);
    focusScan();
  };

  const resetDevice = (keepModel: boolean) => {
    newGeneration();
    writeIds(EMPTY_IDENTIFIERS);
    setMethods({});
    setNoImei2(false);
    setNoSerial(false);
    setCost("");
    setPrice("");
    setBattery("");
    setGrade("");
    setFunctional("full");
    setDefects("");
    setAccessories("");
    setSalePrice("");
    setWarranty("");
    setOrderChoice(null);
    setStockReason("");
    setServerLines([]);
    setError(null);
    setResult(null);
    openAsk(null);
    setSerialConfirmed(false);
    idemKey.current = newKey();
    keyBody.current = null;
    uncertain.current = false;
    if (!keepModel) {
      configRequest.current += 1;
      setConfig(null);
      setPicked({});
      setUsedVariantId(null); setUsedPicked({});
      setModelQuery("");
    } else if (config && branchId) {
      // Same model and variant; fresh stock and waiting orders — unless another model is asked for meanwhile (F4).
      const request = ++configRequest.current;
      scanConfigurations(companyId, branchId, config.model.id)
        .then((data) => { if (request === configRequest.current) setConfig(data); })
        .catch(() => undefined);
    }
    focusScan();
  };

  const problems = reviewProblems({
    ids, plan, variantChosen, createForbidden, needsPrice, price, cost,
    salePrice: condition === "used" ? salePrice : undefined,
    battery: condition === "used" ? battery : undefined,
    waitingHere: waiting.length > 0, orderChoice, stockReason,
  });

  /** K4: the device this form describes, if the server already holds it in this branch under this variant. */
  const registeredEarlier = async (): Promise<ScanReceived | null> => {
    const code = ids.imei || ids.serial;
    if (!code || !branchId) return null;
    try {
      const found = await scanCheck(companyId, code, { productId: variantProductId, expected: ids.imei ? "imei" : null });
      const unit = found.unit;
      const branchName = branches.find((b) => b.id === branchId)?.name;
      if (!unit || unit.standing !== "registered" || !unit.id) return null;
      // L1: «the same device» needs a variant to compare with: a combination that is not a variant yet never matches.
      if (!variantProductId || unit.product_id !== variantProductId) return null;
      if (unit.branch_name !== branchName) return null;
      return {
        replayed: true, mode: "stock", condition,
        unit: {
          id: unit.id, product_id: unit.product_id ?? 0, product_name: unit.product_name ?? "",
          branch_name: unit.branch_name ?? "", label_code: unit.label_code ?? null,
          imei: ids.imei || null, imei2: ids.imei2 || null, serial_number: ids.serial, status_label: unit.status_label ?? "",
        },
        branch: { id: branchId, name: branchName ?? "" }, created_variant: false, order: null,
        method: methods.imei ?? methods.serial ?? "manual",
        warnings: ["La respuesta del intento anterior no llegó, pero el equipo quedó registrado entonces: no lo registres otra vez."],
      };
    } catch {
      return null;
    }
  };

  const confirm = async () => {
    if (!branchId || inFlight.current || problems.length) return;
    inFlight.current = true;
    setSubmitting(true);
    setError(null);
    // Review of #440: the device is the one on screen WHEN the person confirmed. The guard may wait for an earlier
    // traversal; if the device, model or branch changed meanwhile, nothing is sent.
    const born = generation.current;
    const endGuard = await startHistoryGuard();
    if (born !== generation.current) {
      endGuard();
      inFlight.current = false;
      setSubmitting(false);
      say("info", "No se envió: cambiaste de equipo, modelo o sucursal mientras se preparaba el registro. Revisa y confirma otra vez.");
      return;
    }
    // The identifiers as they are NOW (a read may have landed during the wait), never those of the click's render.
    const ids = idsRef.current;
    const method: EntryMethod = methods.imei ?? methods.serial ?? "manual";
    const shared: Record<string, unknown> = {
      method,
      ...(ids.imei ? { imei: ids.imei } : {}),
      ...(ids.imei2 ? { imei2: ids.imei2 } : {}),
      ...(ids.serial ? { serial_number: ids.serial } : {}),
      ...(ids.serial && serialConfirmed ? { serial_confirmed: true } : {}),
      ...(supplier ? { supplier: Number(supplier) } : {}),
      ...(cost.trim() ? { cost: cost.trim() } : {}),
    };
    let body: Record<string, unknown>;
    if (condition === "new") {
      body = {
        ...shared, condition: "new", model: config?.model.id, option_ids: combination?.option_ids ?? [],
        ...(needsPrice ? { price: price.trim() } : {}),
        ...(typeof orderChoice === "number" ? { order_item: orderChoice } : {}),
        ...(orderChoice === "stock" ? { confirm_stock: true, reason: stockReason.trim() } : {}),
      };
    } else {
      body = {
        ...shared, condition: "used", product_id: usedVariant?.id,
        ...(battery.trim() ? { battery_health: Number(battery) } : {}),
        ...(grade ? { cosmetic_grade: Number(grade) } : {}),
        ...(functional ? { functional_state: functional } : {}),
        ...(defects.trim() ? { defects: defects.trim() } : {}),
        ...(accessories.trim() ? { accessories: accessories.trim() } : {}),
        ...(salePrice.trim() ? { sale_price: salePrice.trim() } : {}),
        ...(warranty.trim() ? { warranty_days: warranty.trim() } : {}),
        ...(orderChoice === "stock" ? { confirm_stock: true, reason: stockReason.trim() } : {}),
      };
    }
    try {
      const sent = JSON.stringify(body);
      if (keyBody.current !== null && keyBody.current !== sent) idemKey.current = newKey();
      keyBody.current = sent;
      const answer = await scanReceive(companyId, branchId, body, idemKey.current);
      uncertain.current = false;
      setResult(answer);
      say("ok", `Equipo registrado: ${answer.unit.label_code || `#${answer.unit.id}`}.`);
    } catch (err) {
      if (err instanceof ScanIntakeError && uncertain.current && DUPLICATE_CODES.has(err.code)) {
        // K4: the earlier attempt failed without an answer and the body (so the key) changed since. If the device the
        // server now names is THIS one — same identifier, same variant, this branch — it entered then: say so.
        const earlier = await registeredEarlier();
        if (born !== generation.current) {
          // L3: the clerk moved on (another device, model or branch) while it was looked up.
          say("info", "Comprobación del intento anterior descartada: cambiaste de equipo, modelo o sucursal.");
          return;
        }
        if (earlier) {
          uncertain.current = false;
          setResult(earlier);
          say("ok", `Equipo registrado en el intento anterior: ${earlier.unit.label_code || `#${earlier.unit.id}`}.`);
          return;
        }
      }
      if (!(err instanceof ScanIntakeError) || err.status >= 500) uncertain.current = true;
      if (err instanceof ScanIntakeError) {
        if (err.code === "pending_order_line") {
          setServerLines(err.lines);
          setOrderChoice(null);
        }
        // F11: a 4xx refusal wrote nothing and the next body will differ: a new key. A 5xx (or a lost answer) may
        // have registered the device: the retry keeps the key, so the server replays instead of writing twice.
        if (err.status >= 400 && err.status < 500) {
          idemKey.current = newKey();
          keyBody.current = null;
        }
        setError({ message: err.message, field: err.field });
      } else {
        setError({ message: err instanceof Error ? err.message : "No se pudo registrar el equipo.", field: "" });
      }
    } finally {
      endGuard();
      inFlight.current = false;
      setSubmitting(false);
    }
  };

  // ---------------------------------------------------------------------------

  if (result) {
    const labels = extractLabelBatch(result);
    return (
      <section aria-labelledby={`${scanId}-done`} className="space-y-4 rounded-xl border border-bd-border bg-surface p-4 sm:p-6">
        <h2 id={`${scanId}-done`} className="text-lg font-bold text-foreground" data-testid="scan-intake-done">
          {result.replayed ? "Este equipo ya estaba registrado con esta confirmación" : "Equipo registrado"}
        </h2>
        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div><dt className="text-muted">Código interno</dt><dd className="font-mono font-semibold">{result.unit.label_code || "—"}</dd></div>
          <div><dt className="text-muted">Variante</dt><dd className="break-words">{result.unit.product_name}</dd></div>
          <div><dt className="text-muted">Sucursal</dt><dd>{result.branch.name}</dd></div>
          <div><dt className="text-muted">IMEI 1</dt><dd className="font-mono break-all">{result.unit.imei || "—"}</dd></div>
          {result.order ? <div><dt className="text-muted">Pedido</dt><dd>Entregado al pedido #{result.order.id}</dd></div> : null}
        </dl>
        {result.warnings.map((warning) => (
          <p key={warning} className={`rounded-lg border px-3 py-2 text-sm ${TONE_CLASS.warn}`}>{warning}</p>
        ))}
        <NextStepBar
          product={{ id: result.unit.product_id, name: result.unit.product_name }}
          showLabels={canPrintLabels} showPublication={false} showStorefront={false} labelBatch={labels}
        >
          <button type="button" className={internalPrimaryButtonClass} onClick={() => resetDevice(true)} data-testid="scan-intake-again">
            Registrar otro de este modelo
          </button>
          <button type="button" className={internalButtonClass} onClick={() => resetDevice(false)}>Otro modelo</button>
          {result.condition === "used" ? (
            <Link href={`/admin/inventory/used?product=${result.unit.product_id}`} className={`${internalButtonClass} inline-flex items-center`}>
              Inspección y fotos
            </Link>
          ) : null}
        </NextStepBar>
      </section>
    );
  }

  const field = (name: string) => (error?.field === name ? error.message : "");

  return (
    <div ref={sectionRef} className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
      <div className="min-w-0 space-y-4">
        {/* 1 · Sucursal y tipo */}
        <section className="grid gap-3 rounded-xl border border-bd-border bg-surface p-4 sm:grid-cols-2">
          <label className="grid gap-1 text-sm">
            <span className="font-semibold">Sucursal</span>
            <select
              className={internalInputClass} value={branchId ?? ""} data-testid="scan-intake-branch"
              onChange={(e) => { setBranchId(Number(e.target.value) || null); resetDevice(false); }}
            >
              {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
          <fieldset className="grid gap-1 text-sm">
            <legend className="font-semibold">Equipo</legend>
            <div className="flex gap-2">
              {(["new", "used"] as Condition[]).map((value) => (
                <button
                  key={value} type="button" aria-pressed={condition === value}
                  className={`${condition === value ? internalPrimaryButtonClass : internalButtonClass} min-h-11 flex-1`}
                  onClick={() => { newGeneration(); setCondition(value); setOrderChoice(null); setServerLines([]); }}
                >
                  {value === "new" ? "Nuevo" : "Seminuevo"}
                </button>
              ))}
            </div>
          </fieldset>
        </section>

        {/* 2 · Modelo y variante (o el código comercial de la caja, leído abajo) */}
        <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
          <label className="grid gap-1 text-sm">
            <span className="font-semibold">Modelo</span>
            <input
              className={internalInputClass} value={modelQuery} data-testid="scan-intake-model"
              placeholder="Busca el modelo (o escanea el código comercial de la caja)"
              onChange={(e) => {
                setModelQuery(e.target.value);
                if (config) {
                  // G4: another model by hand — reads and configuration requests on their way are for the old one.
                  newGeneration();
                  configRequest.current += 1;
                  setConfig(null); setPicked({}); setUsedVariantId(null); setUsedPicked({});
                }
              }}
            />
          </label>
          {shownHits.length ? (
            <ul className="grid gap-1" aria-label="Modelos encontrados">
              {shownHits.map((hit) => (
                <li key={hit.id}>
                  <button type="button" className={`${internalButtonClass} w-full text-left`} onClick={() => void loadModel(hit.id)}>
                    {hit.name}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}

          {config && condition === "new" ? (
            <div className="space-y-3">
              {/* SELECTOR-UX-01 · C: THE device configurator (the till's chips), single mode. */}
              <div role="group" aria-label="Configuración del equipo" className="space-y-3">
                {stepsToAsk(config).map((attribute) => (
                  <ConfiguratorStep
                    key={attribute.id}
                    name={`${scanId}-attribute-${attribute.id}`}
                    attribute={attribute}
                    extraOptions={isOptional(config, attribute.id) ? [{ id: NO_OPTION, label: `Sin ${attribute.name}`, swatch_hex: "" }] : []}
                    isChecked={(optionId) => picked[attribute.id] === optionId}
                    stateOf={(optionId) => optionState(config, picked, attribute.id, optionId)}
                    disabled={submitting}
                    onToggle={(optionId) => {
                      newGeneration();
                      setPicked(autoFill(config, pickOption(config, picked, attribute.id, optionId)));
                      setOrderChoice(null);
                      setServerLines([]);
                    }}
                  />
                ))}
              </div>
              {combination ? (
                <p className="text-sm" data-testid="scan-intake-variant">
                  {combination.variant
                    ? <>Variante: <strong>{combination.variant.name}</strong> · {combination.variant.available} en esta sucursal</>
                    : <>Esta combinación todavía no es una variante: se crea al confirmar (sin publicar en la web){can?.create_variant ? "" : ", y crearla requiere «Administrar productos»"}.</>}
                </p>
              ) : null}
            </div>
          ) : null}

          {config && condition === "used" && used ? (
            <div className="space-y-3">
              {/* SELECTOR-UX-01 · C: the same configurator over the used variants that exist (none is created here). */}
              <div role="group" aria-label="Configuración del equipo" className="space-y-3">
                {stepsToAsk(used).map((attribute) => (
                  <ConfiguratorStep
                    key={attribute.id}
                    name={`${scanId}-used-attribute-${attribute.id}`}
                    attribute={attribute}
                    isChecked={(optionId) => usedShown[attribute.id] === optionId}
                    stateOf={(optionId) => configuratorOptionState(used, usedShown, attribute.id, optionId)}
                    disabled={submitting}
                    onToggle={(optionId) => {
                      newGeneration();
                      const next = autoFill(used, pickOption(used, usedShown, attribute.id, optionId));
                      const reached = chosenCombination(used, next);
                      setUsedPicked(next);
                      setUsedVariantId(reached ? reached.variant.id : null);
                    }}
                    unavailableText="no hay una variante de seminuevos así"
                  />
                ))}
              </div>
              <p className="text-sm" data-testid="scan-intake-used-variant">
                {usedVariant
                  ? <>Variante: <strong>{usedVariant.name}</strong>{usedVariant.pending_draft ? " · borrador" : ""}</>
                  : "Elige la configuración del equipo: sólo se ofrecen las variantes de seminuevos que existen."}
              </p>
              {config.used_variants.some((variant) => !variant.is_serialized) ? (
                <p className="text-xs text-muted">
                  {`Sin control por serie (no se registran aquí): ${config.used_variants.filter((variant) => !variant.is_serialized).map((variant) => variant.name).join(", ")}.`}
                </p>
              ) : null}
            </div>
          ) : null}

          {config && condition === "used" && !used ? (
            config.used_variants.length ? (
              <fieldset className="space-y-1">
                <legend className="text-sm font-semibold">Variante de seminuevos</legend>
                <div className="grid gap-2">
                  {config.used_variants.map((variant) => (
                    <label key={variant.id} className={`flex min-h-11 items-center gap-2 rounded-lg border px-3 text-sm ${variant.is_serialized ? "" : "opacity-50"}`}>
                      <input
                        type="radio" name={`${scanId}-used`} disabled={submitting || !variant.is_serialized}
                        checked={usedVariantId === variant.id} onChange={() => { newGeneration(); setUsedVariantId(variant.id); }}
                      />
                      <span className="min-w-0 break-words">{variant.name}{variant.pending_draft ? " · borrador" : ""}{variant.is_serialized ? "" : " · sin control por serie"}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : (
              <p className="text-sm text-muted">Este modelo no tiene variantes de seminuevos. Créala en «Registrar artículo» o Catálogo › Modelos.</p>
            )
          ) : null}
        </section>
        {/* 3 · El lector */}
        <section className="space-y-3 rounded-xl border-2 border-foreground/20 bg-surface p-4" aria-labelledby={`${scanId}-label`}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <label id={`${scanId}-label`} htmlFor={`${scanId}-input`} className="text-sm font-bold">Escanear o escribir</label>
            <p className="text-sm" data-testid="scan-intake-expected" aria-live="polite">
              Esperando:{" "}
              <strong>
                {!variantChosen && !ids.imei ? "código comercial de la caja o IMEI 1" : expected ? SLOT_LABEL[expected] : "nada más: revisa y confirma"}
              </strong>
            </p>
          </div>
          <div className="flex min-w-0 gap-2">
          <input
            id={`${scanId}-input`} ref={scanRef} value={scanText} autoFocus={!coarse} data-testid="scan-intake-input"
            inputMode={coarse && !keyboard ? "none" : "text"}
            className={`${internalInputClass} min-h-12 min-w-0 flex-1 font-mono text-base`}
            placeholder="Pasa el lector por el código o escríbelo y pulsa Enter"
            autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false} enterKeyHint="next"
            aria-describedby={`${scanId}-log`}
            onChange={(e) => { setScanText(e.target.value); rhythmChanged(rhythm.current, e.target.value); }}
            onKeyDown={onScanKeyDown}
          />
            <button
              type="button" className={`${internalButtonClass} min-h-12 shrink-0`} onClick={readTyped}
              disabled={!scanText.trim()}
            >
              Leer
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
            <label className="inline-flex items-center gap-2">
              El lector termina con
              <select
                className="rounded border border-bd-border bg-background px-2 py-1" value={terminator}
                onChange={(e) => { const v = e.target.value as Terminator; setTerminator(v); saveTerminator(v); }}
              >
                <option value="enter">Enter</option>
                <option value="tab">Tab (Enter también lee)</option>
              </select>
            </label>
            {coarse ? (
              <button
                type="button" className="rounded border border-bd-border px-2 py-1"
                onClick={() => { setKeyboard(!keyboard); scanRef.current?.focus(); }}
              >
                {keyboard ? "Ocultar teclado" : "Teclado (escribir a mano)"}
              </button>
            ) : null}
            {busyReads > 0 ? <span role="status">Consultando…</span> : null}
          </div>

          {ask ? (
            <div role="alertdialog" aria-labelledby={`${scanId}-ask`} className={`space-y-2 rounded-lg border p-3 ${TONE_CLASS.warn}`} data-testid="scan-intake-ask">
              <p id={`${scanId}-ask`} className="text-sm"><span className="font-mono break-all">{ask.value}</span> — {ask.message}</p>
              <div className="flex flex-wrap gap-2">
                {ask.options.map((choice) => (
                  <button key={choice} type="button" className={`${internalButtonClass} min-h-11`} onClick={() => choose(choice)} onKeyDown={guardChoiceKey}>
                    {CHOICE_LABEL[choice]}
                  </button>
                ))}
                <button type="button" className={`${internalButtonClass} min-h-11`} onKeyDown={guardChoiceKey} onClick={() => { openAsk(null); say("info", "Lectura descartada."); focusScan(); }}>
                  Descartar
                </button>
              </div>
            </div>
          ) : null}

          <ol className="grid gap-2 sm:grid-cols-3" aria-label="Identificadores del equipo">
            {(["imei", "imei2", "serial"] as Slot[]).map((slot) => {
              const skipped = (slot === "imei2" && noImei2) || (slot === "serial" && noSerial);
              return (
                <li
                  key={slot} data-testid={`scan-intake-slot-${slot}`}
                  className={`min-w-0 rounded-lg border p-3 ${expected === slot ? "border-foreground/50 ring-2 ring-foreground/10" : "border-bd-border"}`}
                  aria-current={expected === slot ? "step" : undefined}
                >
                  <p className="text-xs font-semibold text-muted">
                    {SLOT_LABEL[slot]}{slot === "imei" && requiresImei ? " *" : ""}
                    {methods[slot] ? <span className="ml-1 font-normal">· {methods[slot] === "scanner" ? "lector" : "a mano"}</span> : null}
                  </p>
                  <p className="mt-1 break-all font-mono text-sm" data-testid={`scan-intake-value-${slot}`}>
                    {ids[slot] || (skipped ? "No tiene" : "—")}
                  </p>
                  {field(slot === "serial" ? "serial_number" : slot) ? (
                    <p className="mt-1 text-xs text-danger">{field(slot === "serial" ? "serial_number" : slot)}</p>
                  ) : null}
                  <div className="mt-2 flex flex-wrap gap-2">
                    {ids[slot] ? (
                      <button type="button" className="text-xs underline" aria-label={`Borrar ${SLOT_LABEL[slot]}`} onClick={() => clearSlot(slot)}>Borrar</button>
                    ) : slot !== "imei" ? (
                      <button
                        type="button" className="text-xs underline"
                        aria-label={skipped ? `Marcar que sí tiene ${SLOT_LABEL[slot]}` : `Marcar que no tiene ${SLOT_LABEL[slot]}`}
                        onClick={() => { if (slot === "imei2") setNoImei2(!noImei2); else setNoSerial(!noSerial); }}
                      >
                        {skipped ? "Sí lo tiene" : "No tiene"}
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>

          <ul id={`${scanId}-log`} aria-live="polite" className="space-y-1" data-testid="scan-intake-log">
            {log.map((line) => (
              <li key={line.id} className={`rounded-md border px-3 py-1.5 text-sm ${TONE_CLASS[line.tone]}`}>{line.text}</li>
            ))}
          </ul>
        </section>
      </div>

      {/* 4 · Datos y revisión */}
      <aside className="min-w-0 space-y-4">
        <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
          <h2 className="text-sm font-bold">Datos del ingreso</h2>
          {can?.choose_supplier ? (
            <label className="grid gap-1 text-sm">
              <span>Proveedor</span>
              <select className={internalInputClass} value={supplier} onChange={(e) => setSupplier(e.target.value)} data-testid="scan-intake-supplier">
                <option value="">Sin indicar</option>
                {(config?.suppliers ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          ) : null}
          {can?.view_costs ? (
            <label className="grid gap-1 text-sm">
              <span>Costo (opcional)</span>
              <input className={internalInputClass} inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} placeholder="3100.00" />
            </label>
          ) : null}
          {needsPrice ? (
            <label className="grid gap-1 text-sm">
              <span>Precio de venta de la variante nueva</span>
              <input className={internalInputClass} inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} data-testid="scan-intake-price" />
            </label>
          ) : null}
          {condition === "used" ? (
            <>
              <label className="grid gap-1 text-sm">
                <span>Batería (%)</span>
                <input className={internalInputClass} inputMode="numeric" value={battery} onChange={(e) => setBattery(e.target.value)} data-testid="scan-intake-battery" />
              </label>
              {grades.length ? (
                <label className="grid gap-1 text-sm">
                  <span>Estado estético</span>
                  <select className={internalInputClass} value={grade} onChange={(e) => setGrade(e.target.value)}>
                    <option value="">Sin indicar</option>
                    {grades.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
                  </select>
                </label>
              ) : null}
              <label className="grid gap-1 text-sm">
                <span>Estado funcional</span>
                <select className={internalInputClass} value={functional} onChange={(e) => setFunctional(e.target.value)}>
                  <option value="full">Sin fallas declaradas</option>
                  <option value="with_faults">Con fallas declaradas</option>
                </select>
              </label>
              <label className="grid gap-1 text-sm">
                <span>Defectos{functional === "with_faults" ? " *" : ""}</span>
                <textarea className={internalInputClass} rows={2} value={defects} onChange={(e) => setDefects(e.target.value)} />
              </label>
              <label className="grid gap-1 text-sm">
                <span>Accesorios</span>
                <input className={internalInputClass} value={accessories} onChange={(e) => setAccessories(e.target.value)} />
              </label>
              {can?.set_price ? (
                <>
                  <label className="grid gap-1 text-sm">
                    <span>Precio de este equipo (opcional)</span>
                    <input className={internalInputClass} inputMode="decimal" value={salePrice} onChange={(e) => setSalePrice(e.target.value)} />
                  </label>
                  <label className="grid gap-1 text-sm">
                    <span>Garantía en días (opcional)</span>
                    <input className={internalInputClass} inputMode="numeric" value={warranty} onChange={(e) => setWarranty(e.target.value)} />
                  </label>
                </>
              ) : (
                <p className="text-xs text-muted">El precio y la garantía del seminuevo los fija quien administra productos.</p>
              )}
            </>
          ) : null}
        </section>

        {waiting.length ? (
          <section className={`space-y-2 rounded-xl border p-4 ${TONE_CLASS.warn}`} data-testid="scan-intake-waiting">
            <h2 className="text-sm font-bold">Un pedido pagado espera este equipo</h2>
            {waiting.map((line) => (
              <label key={line.order_item} className="flex min-h-11 items-center gap-2 text-sm">
                <input
                  type="radio" name={`${scanId}-order`}
                  disabled={!can?.receive_on_order || line.quantity !== 1 || condition === "used"}
                  checked={orderChoice === line.order_item} onChange={() => setOrderChoice(line.order_item)}
                />
                Es del pedido #{line.order_number}{line.customer ? ` (${line.customer})` : ""}
                {condition === "used" ? <span className="text-xs text-muted"> · un seminuevo pedido se recibe desde su pedido</span> : null}
              </label>
            ))}
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <input type="radio" name={`${scanId}-order`} checked={orderChoice === "stock"} onChange={() => setOrderChoice("stock")} />
              Ingresar como stock
            </label>
            {orderChoice === "stock" ? (
              <textarea
                className={internalInputClass} rows={2} value={stockReason} onChange={(e) => setStockReason(e.target.value)}
                placeholder="Por qué entra como stock" aria-label="Motivo"
              />
            ) : null}
          </section>
        ) : null}

        <section className="space-y-3 rounded-xl border border-bd-border bg-surface p-4" aria-labelledby={`${scanId}-review`}>
          <h2 id={`${scanId}-review`} className="text-sm font-bold">Revisión</h2>
          <dl className="grid gap-1 text-sm">
            <div className="flex justify-between gap-2"><dt className="text-muted">Variante</dt>
              <dd className="min-w-0 break-words text-right">{condition === "new" ? combination?.variant?.name ?? (combination ? "Nueva" : "—") : usedVariant?.name ?? "—"}</dd></div>
            {(["imei", "imei2", "serial"] as Slot[]).map((slot) => (
              <div key={slot} className="flex justify-between gap-2"><dt className="text-muted">{SLOT_LABEL[slot]}</dt>
                <dd className="break-all font-mono">{ids[slot] || "—"}</dd></div>
            ))}
          </dl>
          {problems.length ? (
            <ul className="list-disc space-y-1 pl-5 text-xs text-muted" data-testid="scan-intake-problems">
              {problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          ) : null}
          {error ? <ErrorBox message={error.message} /> : null}
          <button
            type="button" className={`${internalPrimaryButtonClass} min-h-12 w-full`} data-testid="scan-intake-confirm"
            onKeyDown={guardChoiceKey}
            disabled={submitting || busyReads > 0 || problems.length > 0 || !can?.receive} onClick={() => void confirm()}
          >
            {submitting ? "Registrando…" : "Confirmar ingreso"}
          </button>
          <button type="button" className={`${internalButtonClass} w-full`} disabled={submitting} onClick={() => resetDevice(true)}>Limpiar este equipo</button>
        </section>
      </aside>
    </div>
  );
}
