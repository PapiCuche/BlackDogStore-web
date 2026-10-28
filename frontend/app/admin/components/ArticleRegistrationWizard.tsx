"use client";

/**
 * Productos › Registro — «Registrar artículo». COMMERCE-ADMIN-UX-01.
 *
 * Cuatro pasos: el tipo, los datos, el stock (o el proveedor) y la revisión.
 * ESTA PANTALLA NO ESCRIBE NADA POR SU CUENTA: arma un cuerpo y lo manda a
 * `POST /api/admin/products/register/`, que llama en UNA transacción a los
 * servicios de siempre (el producto, el modelo y la variante, la recepción de
 * equipos, la entrada de stock con su costo, el proveedor y su oferta).
 *
 *   · «Revisar» pregunta al servidor con `dry_run`: los mismos servicios, sin
 *     escribir. Lo que rechazaría y por qué no se publicaría se ve ANTES.
 *   · «Registrar» manda una `Idempotency-Key`; reintentar lo mismo la reutiliza
 *     (el servidor contesta lo primero), cambiar el borrador la renueva.
 *   · Las imágenes del producto y las fotos reales de un seminuevo se suben
 *     DESPUÉS, por sus endpoints de siempre; si sólo faltaba la imagen para
 *     publicar, se publica entonces con la acción en bloque de siempre.
 *
 * Los permisos de cada paso son los de la acción de debajo: un paso que quien
 * mira no puede usar se ve deshabilitado y dice por qué. Sin `costs.view` no se
 * pide, no se muestra ni se envía ningún costo, ni el margen.
 */

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  PRODUCT_IMAGE_ACCEPT,
  bulkSetProductVisibility,
  fetchAdminCategories,
  publicationProblemLabel,
  uploadProductImage,
  type AdminCategory,
} from "../../lib/admin";
import {
  ARTICLE_KINDS,
  RegistrationApiError,
  buildRegistrationBody,
  bulkSetModelVisibility,
  costState,
  emptyDraft,
  emptyUnit,
  estimatedMargin,
  kindState,
  newIdempotencyKey,
  stockState,
  submitRegistration,
  type ArticleKind,
  type RegistrationAccess,
  type RegistrationDraft,
  type RegistrationResult,
} from "../../lib/registration";
import { uploadUnitPhoto } from "../../lib/stock-units";
import {
  fetchAdminCatalogModels,
  fetchCatalogAttributes,
  fetchCosmeticGrades,
  fetchSuppliers,
  type AdminCatalogAttribute,
  type AdminCatalogModel,
  type CosmeticGradeRow,
  type SupplierRow,
} from "../lib/internal-api";
import { pastedCodes, scanInputProps, useScanRows } from "../lib/scan-rows";
import { imeiProblem } from "../lib/unit-entry";
import { ImeiCompletionHint } from "./ImeiCompletionHint";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";
import { NextStepBar, extractLabelBatch } from "./NextStepBar";

type Branch = { id: number; name: string };
type Props = { access: RegistrationAccess; companyId: number | null; branches: Branch[] };
type Step = "kind" | "product" | "stock" | "review" | "done";

const NEW_MODEL = "__new";
const NEW_SUPPLIER = "__new";

const STEP_ORDER: Step[] = ["kind", "product", "stock", "review"];

/** Enter's path through a new-device row (the order the fields are shown). */
const UNIT_CHAIN = ["serial_number", "imei", "imei2"] as const;

function stockStepLabel(kind: ArticleKind | null): string {
  return kind === "supplier" ? "Proveedor" : "Stock";
}

function stepLabel(step: Step, kind: ArticleKind | null): string {
  return { kind: "Tipo", product: "Datos", stock: stockStepLabel(kind), review: "Revisar", done: "Listo" }[step];
}

/** El paso del asistente al que pertenece lo que el servidor rechazó. */
function stepOfError(error: RegistrationApiError): Step {
  if (error.step === "kind") return "kind";
  if (error.step === "product" || error.step === "model" || error.field?.startsWith("product.")
    || error.field === "sku" || error.field === "options") return "product";
  if (error.step === "review") return "review";
  return "stock";
}

function errorText(err: unknown): string {
  if (err instanceof RegistrationApiError) return err.line ? `${err.message} (línea ${err.line})` : err.message;
  if (err instanceof TypeError) return "No se pudo conectar con el servidor. Vuelve a intentarlo: no se registrará dos veces.";
  return err instanceof Error && err.message ? err.message : "No se pudo completar la operación.";
}

const soles = (value: string) => `S/ ${value}`;

function Field({ id, label, children, hint }: { id: string; label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs text-muted">{label}</label>
      {children}
      {hint ? <p className="mt-1 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return <p className="rounded-lg border border-bd-border bg-background px-4 py-3 text-sm text-muted">{children}</p>;
}

export function ArticleRegistrationWizard({ access, companyId, branches }: Props) {
  const [step, setStep] = useState<Step>("kind");
  const [draft, setDraft] = useState<RegistrationDraft>(() => ({
    ...emptyDraft(), branch: branches[0] ? String(branches[0].id) : "",
  }));
  const [categories, setCategories] = useState<AdminCategory[]>([]);
  const [models, setModels] = useState<AdminCatalogModel[]>([]);
  const [attributes, setAttributes] = useState<AdminCatalogAttribute[]>([]);
  const [grades, setGrades] = useState<CosmeticGradeRow[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierRow[]>([]);
  const [loadNotice, setLoadNotice] = useState<string | null>(null);
  const [images, setImages] = useState<File[]>([]);
  const [photos, setPhotos] = useState<File[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<RegistrationResult | null>(null);
  const [reviewError, setReviewError] = useState<RegistrationApiError | Error | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done, setDone] = useState<{
    result: RegistrationResult;
    failures: string[];
    publishedLater: boolean | null;
    refusedLater: string[];
    modelPublishedLater: boolean;
  } | null>(null);
  // One key per draft: kept across retries of the same body, renewed by any change.
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const failed: string[] = [];
    const soft = <T,>(promise: Promise<T>, fallback: T, what: string) =>
      promise.catch(() => { failed.push(what); return fallback; });
    Promise.all([
      soft(fetchAdminCategories(companyId), [] as AdminCategory[], "categorías"),
      soft(fetchAdminCatalogModels(companyId), [] as AdminCatalogModel[], "modelos"),
      soft(fetchCatalogAttributes(companyId), [] as AdminCatalogAttribute[], "capacidades y colores"),
      soft(fetchCosmeticGrades(companyId), [] as CosmeticGradeRow[], "escala de estado"),
      soft(fetchSuppliers({ active: "true" }, companyId).then((page) => page.results), [] as SupplierRow[], "proveedores"),
    ]).then(([cats, mods, attrs, grs, sups]) => {
      if (!alive) return;
      setCategories(cats);
      setModels(mods);
      setAttributes(attrs);
      setGrades(grs.filter((g) => g.is_active));
      setSuppliers(sups);
      setLoadNotice(failed.length ? `No se pudieron cargar: ${failed.join(", ")}. Puedes seguir; esos campos quedan vacíos.` : null);
    });
    return () => { alive = false; };
  }, [companyId]);

  function update(change: Partial<RegistrationDraft>) {
    setIdempotencyKey(null);
    setPreview(null);
    setDraft((prev) => ({ ...prev, ...change }));
  }

  // SUPPLIER-DEVICE-FLOW-02 — the new-device rows take a scanner: Enter goes
  // serial → IMEI → second IMEI → the next row; at the last row it opens a new
  // one (if this one has something) instead of moving on.
  const scanFocusRow = useRef<number | null>(null);
  const unitScan = useScanRows(UNIT_CHAIN.length, draft.units.length, () => {
    const last = draft.units[draft.units.length - 1];
    if (!last || !(last.serial_number.trim() || last.imei.trim())) return;
    scanFocusRow.current = draft.units.length;
    update({ units: [...draft.units, emptyUnit()] });
  });
  const focusUnit = unitScan.focus;
  useEffect(() => {
    if (scanFocusRow.current === null) return;
    focusUnit(scanFocusRow.current, 0);
    scanFocusRow.current = null;
  }, [draft.units.length, focusUnit]);

  /** A list pasted into a device field (one code per line) fills that field down the rows, adding rows. */
  function pasteUnits(index: number, key: (typeof UNIT_CHAIN)[number], event: React.ClipboardEvent<HTMLInputElement>) {
    const codes = pastedCodes(event.clipboardData.getData("text"));
    if (codes.length === 0) return;
    event.preventDefault();
    const rows = [...draft.units];
    codes.forEach((code, offset) => {
      const at = index + offset;
      rows[at] = { ...(rows[at] ?? emptyUnit()), [key]: code };
    });
    update({ units: rows });
  }

  const kind = draft.kind;
  const isDevice = kind === "new_device" || kind === "used_device";
  const costs = costState(access);
  const stock = kind ? stockState(kind, access) : null;
  const margin = useMemo(() => {
    const cost = kind === "used_device" ? draft.used.cost || draft.reference_cost : draft.reference_cost;
    const price = kind === "used_device" && draft.used.sale_price ? draft.used.sale_price : draft.price;
    return estimatedMargin(price, cost, access);
  }, [access, draft.price, draft.reference_cost, draft.used.cost, draft.used.sale_price, kind]);
  const offersSupplier = kind === "supplier" || kind === "new_device" || kind === "accessory";
  const wantsSupplier = kind === "supplier" || (offersSupplier && draft.supplier_mode !== "none");

  // -- validation per step (the server decides; this saves a round trip) -----

  function productProblems(): string[] {
    const found: string[] = [];
    if (!draft.name.trim()) found.push("Indica el nombre.");
    const price = Number(draft.price.replace(",", "."));
    if (!draft.price.trim() || !Number.isFinite(price) || price <= 0) found.push("El precio de venta debe ser mayor que 0.");
    if (isDevice && draft.model_mode === "existing" && !draft.model_id) found.push("Elige el modelo del equipo.");
    if (draft.model_mode === "new" && (isDevice || kind === "supplier") && !draft.model_name.trim()) {
      found.push("Escribe el nombre del modelo nuevo.");
    }
    return found;
  }

  function stockProblems(): string[] {
    const found: string[] = [];
    if (kind === "new_device") {
      draft.units.forEach((unit, index) => {
        if (!unit.serial_number.trim() && !unit.imei.trim()) return;
        if (!unit.serial_number.trim()) found.push(`Equipo ${index + 1}: indica el número de serie.`);
        if (unit.imei.trim()) {
          const problem = imeiProblem(unit.imei);
          if (problem) found.push(`Equipo ${index + 1}: ${problem}`);
        } else if (draft.requires_imei) {
          found.push(`Equipo ${index + 1}: indica el IMEI.`);
        }
      });
    }
    if (kind === "used_device") {
      if (!draft.used.serial_number.trim()) found.push("Indica el número de serie del equipo.");
      if (draft.used.imei.trim()) {
        const problem = imeiProblem(draft.used.imei);
        if (problem) found.push(problem);
      } else {
        found.push("Indica el IMEI del equipo.");
      }
      if (draft.used.imei2.trim()) {
        const problem = imeiProblem(draft.used.imei2);
        if (problem) found.push(`Segundo IMEI: ${problem}`);
        else if (draft.used.imei2.trim() === draft.used.imei.trim()) found.push("El segundo IMEI es distinto del primero.");
      }
    }
    if (wantsSupplier) {
      if (draft.supplier_mode !== "existing" && kind === "supplier" && !draft.supplier_name.trim() && !draft.supplier_id) {
        found.push("Elige el proveedor o escribe el nombre de uno nuevo.");
      }
      if (!draft.offer.commercial_price.trim()) found.push("Indica el precio comercial de la oferta.");
      if (!draft.offer.lead_time_days.trim()) found.push("Indica el plazo de entrega en días.");
    }
    return found;
  }

  function goTo(next: Step) {
    setProblems([]);
    setStep(next);
  }

  function continueFrom(current: Step) {
    if (current === "kind") {
      if (!kind) { setProblems(["Elige qué vas a registrar."]); return; }
      goTo("product");
      return;
    }
    if (current === "product") {
      const found = productProblems();
      if (found.length) { setProblems(found); return; }
      goTo("stock");
    }
  }

  async function review() {
    const found = stockProblems();
    if (found.length) { setProblems(found); return; }
    setProblems([]);
    setBusy(true);
    setReviewError(null);
    setSubmitError(null);
    setPreview(null);
    setStep("review");
    try {
      setPreview(await submitRegistration(buildRegistrationBody(draft, access, { dryRun: true }), { companyId }));
    } catch (err) {
      setReviewError(err instanceof Error ? err : new Error(errorText(err)));
    } finally {
      setBusy(false);
    }
  }

  async function register() {
    setBusy(true);
    setSubmitError(null);
    const key = idempotencyKey ?? newIdempotencyKey();
    setIdempotencyKey(key);
    let result: RegistrationResult;
    try {
      result = await submitRegistration(buildRegistrationBody(draft, access), { key, companyId });
    } catch (err) {
      setSubmitError(errorText(err));
      setBusy(false);
      return;
    }
    // Registered. What follows uses the endpoints of always; a failure is said, never hidden.
    const failures: string[] = [];
    const productId = result.product.id;
    let uploaded = 0;
    if (productId && result.product.created) {
      for (const file of images) {
        try {
          await uploadProductImage(productId, file);
          uploaded += 1;
        } catch (err) {
          failures.push(`${file.name}: ${err instanceof Error ? err.message : "no se pudo subir."}`);
        }
      }
    }
    const unitId = result.units[0]?.id;
    if (kind === "used_device" && unitId) {
      for (const file of photos) {
        try {
          await uploadUnitPhoto(unitId, file);
        } catch (err) {
          failures.push(`${file.name}: ${err instanceof Error ? err.message : "no se pudo subir."}`);
        }
      }
    }
    let publishedLater: boolean | null = null;
    let refusedLater: string[] = [];
    const onlyImage = result.publication.problems.length > 0 && result.publication.problems.every((p) => p === "no_image");
    if (productId && result.publication.requested && !result.publication.published && onlyImage && uploaded > 0) {
      try {
        const answer = await bulkSetProductVisibility("publish", { ids: [productId] }, companyId);
        publishedLater = answer.changed > 0;
        refusedLater = answer.refused.flatMap((item) => item.problems);
      } catch (err) {
        publishedLater = false;
        failures.push(`Publicar: ${err instanceof Error ? err.message : "no se pudo."}`);
      }
    }
    // The new model was created hidden with its first variant: now that the
    // variant is published, the model is too (the action in bulk of always).
    let modelPublishedLater = false;
    const modelId = result.model?.id;
    if (publishedLater && result.publication.model_hidden && modelId) {
      try {
        modelPublishedLater = (await bulkSetModelVisibility("publish", [modelId], companyId)).total > 0;
      } catch (err) {
        failures.push(`Publicar el modelo: ${err instanceof Error ? err.message : "no se pudo."}`);
      }
    }
    setDone({ result, failures, publishedLater, refusedLater, modelPublishedLater });
    setStep("done");
    setBusy(false);
  }

  function restart() {
    setIdempotencyKey(null);
    setDraft({ ...emptyDraft(), branch: branches[0] ? String(branches[0].id) : "" });
    setImages([]);
    setPhotos([]);
    setPreview(null);
    setReviewError(null);
    setSubmitError(null);
    setDone(null);
    setStep("kind");
  }

  // -- pieces ------------------------------------------------------------------

  const stepper = step !== "done" ? (
    <ol className="flex flex-wrap gap-2 text-xs" aria-label="Pasos del registro">
      {STEP_ORDER.map((s, index) => (
        <li
          key={s}
          aria-current={s === step ? "step" : undefined}
          className={`rounded-full border px-3 py-1 ${s === step ? "border-foreground font-semibold text-foreground" : "border-bd-border text-muted"}`}
        >
          {index + 1}. {stepLabel(s, kind)}
        </li>
      ))}
    </ol>
  ) : null;

  const problemList = problems.length ? (
    <ul className="list-disc space-y-1 rounded-lg border border-warning-border bg-warning-surface py-3 pl-8 pr-4 text-sm text-warning">
      {problems.map((p) => <li key={p}>{p}</li>)}
    </ul>
  ) : null;

  const inputCls = internalInputClass;

  function kindStep() {
    return (
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-foreground">¿Qué vas a registrar?</h2>
        <div role="radiogroup" aria-label="Tipo de artículo" className="grid gap-3 sm:grid-cols-2">
          {ARTICLE_KINDS.map((item) => {
            const state = kindState(item.kind, access);
            const selected = draft.kind === item.kind;
            return (
              <button
                key={item.kind}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={!state.enabled}
                onClick={() => update({ kind: item.kind, supplier_mode: item.kind === "supplier" ? "existing" : "none" })}
                className={`rounded-xl border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-60 ${selected ? "border-foreground bg-background" : "border-bd-border bg-surface hover:border-foreground/40"}`}
              >
                <span className="block font-semibold text-foreground">{item.label}</span>
                <span className="mt-1 block text-sm text-muted">{item.description}</span>
                {state.reason ? <span className="mt-2 block text-xs text-warning">{state.reason}</span> : null}
              </button>
            );
          })}
        </div>
        {problemList}
        <div className="flex justify-end">
          <button type="button" onClick={() => continueFrom("kind")} className={internalPrimaryButtonClass}>Continuar</button>
        </div>
      </section>
    );
  }

  function modelFields() {
    if (!isDevice && kind !== "supplier") return null;
    return (
      <div className="grid gap-4 rounded-lg border border-bd-border p-4 md:grid-cols-2">
        <Field id="reg-model" label={isDevice ? "Modelo" : "Modelo (opcional)"}
          hint="El modelo agrupa sus variantes en una sola página de la tienda.">
          <select
            id="reg-model"
            value={draft.model_mode === "new" ? NEW_MODEL : draft.model_id}
            onChange={(e) => (e.target.value === NEW_MODEL
              ? update({ model_mode: "new", model_id: "" })
              : update({ model_mode: "existing", model_id: e.target.value }))}
            className={inputCls}
          >
            <option value="">{isDevice ? "Elige un modelo" : "Sin modelo"}</option>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            <option value={NEW_MODEL}>Crear un modelo nuevo…</option>
          </select>
        </Field>
        {draft.model_mode === "new" ? (
          <Field id="reg-model-name" label="Nombre del modelo nuevo">
            <input id="reg-model-name" value={draft.model_name} onChange={(e) => update({ model_name: e.target.value })} className={inputCls} />
          </Field>
        ) : null}
        {attributes.map((attribute) => (
          <Field key={attribute.id} id={`reg-attr-${attribute.id}`} label={attribute.name}>
            <select
              id={`reg-attr-${attribute.id}`}
              value={draft.options[String(attribute.id)] ?? ""}
              onChange={(e) => update({ options: { ...draft.options, [String(attribute.id)]: e.target.value } })}
              className={inputCls}
            >
              <option value="">—</option>
              {attribute.options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </Field>
        ))}
        {attributes.length === 0 ? (
          <p className="text-xs text-muted md:col-span-2">
            Las capacidades y los colores se crean en{" "}
            <Link href="/admin/products/models" className="underline underline-offset-4">Modelos y variantes</Link>.
          </p>
        ) : null}
      </div>
    );
  }

  function productStep() {
    return (
      <section className="space-y-5">
        <h2 className="text-lg font-bold text-foreground">Datos del artículo</h2>
        {modelFields()}
        <div className="grid gap-4 md:grid-cols-2">
          <Field id="reg-name" label="Nombre">
            <input id="reg-name" value={draft.name} onChange={(e) => update({ name: e.target.value })} className={inputCls} />
          </Field>
          <Field id="reg-category" label="Categoría">
            <select id="reg-category" value={draft.category} onChange={(e) => update({ category: e.target.value })} className={inputCls}>
              <option value="">Sin categoría</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <Field id="reg-price" label="Precio de venta (S/)">
            <input id="reg-price" inputMode="decimal" value={draft.price} onChange={(e) => update({ price: e.target.value })} className={inputCls} />
          </Field>
          <Field id="reg-sku" label="Código / SKU (opcional)" hint="Se guarda como código de barras principal del producto.">
            <input id="reg-sku" value={draft.sku} onChange={(e) => update({ sku: e.target.value })} className={inputCls} />
          </Field>
          {costs.enabled ? (
            <Field id="reg-reference-cost" label="Costo de referencia (S/)"
              hint="Lo que esperas pagar por unidad. Se usa mientras una sucursal no tenga costo promedio.">
              <input id="reg-reference-cost" inputMode="decimal" value={draft.reference_cost}
                onChange={(e) => update({ reference_cost: e.target.value })} className={inputCls} />
            </Field>
          ) : null}
          {costs.enabled ? (
            <div className="self-end rounded-lg border border-bd-border bg-background px-4 py-3 text-sm">
              <p className="text-foreground">
                {margin ? `Margen estimado: ${soles(margin.amount)} (${margin.percent} %)` : "Margen estimado: indica precio y costo."}
              </p>
            </div>
          ) : (
            <p className="self-end text-xs text-muted">{costs.reason}</p>
          )}
          <Field id="reg-online" label="Mostrar en e-commerce"
            hint="Se publica si está activo, con precio y categoría (y con imagen si la empresa lo exige). Si no, se crea oculto y se dice por qué.">
            <select id="reg-online" value={draft.show_online ? "yes" : "no"} onChange={(e) => update({ show_online: e.target.value === "yes" })} className={inputCls}>
              <option value="yes">Sí</option>
              <option value="no">No</option>
            </select>
          </Field>
          <Field id="reg-active" label="Producto activo" hint="Inactivo: no se vende en ningún canal.">
            <select id="reg-active" value={draft.is_active ? "yes" : "no"} onChange={(e) => update({ is_active: e.target.value === "yes" })} className={inputCls}>
              <option value="yes">Sí</option>
              <option value="no">No</option>
            </select>
          </Field>
        </div>
        <Field id="reg-description" label="Descripción (opcional)">
          <textarea id="reg-description" rows={3} value={draft.description} onChange={(e) => update({ description: e.target.value })} className={inputCls} />
        </Field>
        <Field id="reg-images" label="Imágenes del producto (opcional)" hint="PNG, JPEG o WebP. Se suben al registrar; la primera queda como principal.">
          <input id="reg-images" type="file" multiple accept={PRODUCT_IMAGE_ACCEPT}
            onChange={(e) => setImages(Array.from(e.target.files ?? []))} className="text-sm" />
        </Field>
        {images.length ? <p className="text-xs text-muted">{images.map((f) => f.name).join(", ")}</p> : null}
        {problemList}
        <div className="flex justify-between">
          <button type="button" onClick={() => goTo("kind")} className={internalButtonClass}>Atrás</button>
          <button type="button" onClick={() => continueFrom("product")} className={internalPrimaryButtonClass}>Continuar</button>
        </div>
      </section>
    );
  }

  function branchSelect(id: string, label: string, value: string, onChange: (value: string) => void) {
    return (
      <Field id={id} label={label}>
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
          {branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      </Field>
    );
  }

  function newDeviceStock() {
    return (
      <div className="space-y-4">
        {branchSelect("reg-branch", "Sucursal de ingreso", draft.branch, (value) => update({ branch: value }))}
        <label className="flex items-center gap-2 text-sm text-foreground">
          <input type="checkbox" checked={draft.requires_imei} onChange={(e) => update({ requires_imei: e.target.checked })} />
          Cada equipo lleva IMEI
        </label>
        <p className="text-xs text-muted">Uno por fila. Deja la lista vacía para registrar el producto sin equipos todavía.</p>
        {draft.units.map((unit, index) => {
          const n = index + 1;
          const set = (change: Partial<typeof unit>) =>
            update({ units: draft.units.map((u, i) => (i === index ? { ...u, ...change } : u)) });
          return (
            <div key={index} className="grid gap-3 rounded-lg border border-bd-border p-3 md:grid-cols-4">
              <Field id={`reg-serial-${n}`} label={`Número de serie ${n}`}>
                <input id={`reg-serial-${n}`} value={unit.serial_number} onChange={(e) => set({ serial_number: e.target.value })}
                  onPaste={(e) => pasteUnits(index, "serial_number", e)} {...unitScan.bind(index, 0)} {...scanInputProps} className={inputCls} />
              </Field>
              <Field id={`reg-imei-${n}`} label={`IMEI ${n}`}>
                <input id={`reg-imei-${n}`} inputMode="numeric" value={unit.imei} onChange={(e) => set({ imei: e.target.value })}
                  onPaste={(e) => pasteUnits(index, "imei", e)} {...unitScan.bind(index, 1)} {...scanInputProps} className={inputCls} />
              </Field>
              <Field id={`reg-imei2-${n}`} label={`Segundo IMEI ${n} (opcional)`}>
                <input id={`reg-imei2-${n}`} inputMode="numeric" value={unit.imei2} onChange={(e) => set({ imei2: e.target.value })}
                  onPaste={(e) => pasteUnits(index, "imei2", e)} {...unitScan.bind(index, 2)} {...scanInputProps} className={inputCls} />
              </Field>
              {costs.enabled ? (
                <Field id={`reg-unit-cost-${n}`} label={`Costo del equipo ${n} (S/)`}>
                  <input id={`reg-unit-cost-${n}`} inputMode="decimal" value={unit.cost} onChange={(e) => set({ cost: e.target.value })} className={inputCls} />
                </Field>
              ) : null}
              {draft.units.length > 1 ? (
                <button type="button" onClick={() => update({ units: draft.units.filter((_, i) => i !== index) })}
                  className="self-end text-left text-xs text-muted underline underline-offset-4 hover:text-danger">
                  Quitar equipo {n}
                </button>
              ) : null}
            </div>
          );
        })}
        <button type="button" onClick={() => update({ units: [...draft.units, emptyUnit()] })} className={internalButtonClass}>
          Agregar equipo
        </button>
      </div>
    );
  }

  function usedDeviceStock() {
    const u = draft.used;
    const set = (change: Partial<typeof u>) => update({ used: { ...u, ...change } });
    return (
      <div className="grid gap-4 md:grid-cols-2">
        {branchSelect("reg-branch", "Sucursal de ingreso", draft.branch, (value) => update({ branch: value }))}
        <Field id="reg-used-serial" label="Número de serie">
          <input id="reg-used-serial" value={u.serial_number} onChange={(e) => set({ serial_number: e.target.value })} className={inputCls} />
        </Field>
        <Field id="reg-used-imei" label="IMEI">
          <input id="reg-used-imei" inputMode="numeric" value={u.imei} onChange={(e) => set({ imei: e.target.value })} {...scanInputProps} className={inputCls} />
          <ImeiCompletionHint value={u.imei} onComplete={(full) => set({ imei: full })} />
        </Field>
        <Field id="reg-used-imei2" label="Segundo IMEI (opcional)">
          <input id="reg-used-imei2" inputMode="numeric" value={u.imei2} onChange={(e) => set({ imei2: e.target.value })} {...scanInputProps} className={inputCls} />
          <ImeiCompletionHint value={u.imei2} onComplete={(full) => set({ imei2: full })} />
        </Field>
        <Field id="reg-used-battery" label="Batería (%)">
          <input id="reg-used-battery" inputMode="numeric" value={u.battery_health} onChange={(e) => set({ battery_health: e.target.value })} className={inputCls} />
        </Field>
        <Field id="reg-used-grade" label="Estado estético">
          <select id="reg-used-grade" value={u.cosmetic_grade} onChange={(e) => set({ cosmetic_grade: e.target.value })} className={inputCls}>
            <option value="">Sin indicar</option>
            {grades.map((g) => <option key={g.id} value={g.code}>{g.label}</option>)}
          </select>
        </Field>
        <Field id="reg-used-price" label="Precio de este equipo (S/)" hint="Lo que paga el comprador por ESTE equipo.">
          <input id="reg-used-price" inputMode="decimal" value={u.sale_price} onChange={(e) => set({ sale_price: e.target.value })} className={inputCls} />
        </Field>
        {costs.enabled ? (
          <Field id="reg-used-cost" label="Costo de este equipo (S/)">
            <input id="reg-used-cost" inputMode="decimal" value={u.cost} onChange={(e) => set({ cost: e.target.value })} className={inputCls} />
          </Field>
        ) : null}
        <Field id="reg-used-notes" label="Detalles para el comprador (opcional)">
          <input id="reg-used-notes" value={u.public_notes} onChange={(e) => set({ public_notes: e.target.value })} className={inputCls} />
        </Field>
        <Field id="reg-used-photos" label="Fotos reales del equipo" hint="Se suben al registrar, a la ficha de este equipo.">
          <input id="reg-used-photos" type="file" multiple accept="image/png,image/jpeg,image/webp"
            onChange={(e) => setPhotos(Array.from(e.target.files ?? []))} className="text-sm" />
        </Field>
        {photos.length ? <p className="text-xs text-muted md:col-span-2">{photos.map((f) => f.name).join(", ")}</p> : null}
      </div>
    );
  }

  function accessoryStock() {
    if (!stock?.enabled) return <Notice>{stock?.reason}</Notice>;
    const used = new Set(draft.stock.map((row) => row.branch));
    return (
      <div className="space-y-3">
        <p className="text-xs text-muted">Cuántas unidades entran en cada sucursal. Cada fila es una línea del Kardex.</p>
        {draft.stock.map((row, index) => {
          const n = index + 1;
          const set = (change: Partial<typeof row>) =>
            update({ stock: draft.stock.map((r, i) => (i === index ? { ...r, ...change } : r)) });
          return (
            <div key={index} className="grid gap-3 rounded-lg border border-bd-border p-3 md:grid-cols-4">
              {branchSelect(`reg-stock-branch-${n}`, `Sucursal ${n}`, row.branch, (value) => set({ branch: value }))}
              <Field id={`reg-stock-qty-${n}`} label={`Cantidad ${n}`}>
                <input id={`reg-stock-qty-${n}`} inputMode="numeric" value={row.quantity} onChange={(e) => set({ quantity: e.target.value })} className={inputCls} />
              </Field>
              {costs.enabled ? (
                <Field id={`reg-stock-cost-${n}`} label={`Costo unitario ${n}`}>
                  <input id={`reg-stock-cost-${n}`} inputMode="decimal" value={row.unit_cost} onChange={(e) => set({ unit_cost: e.target.value })} className={inputCls} />
                </Field>
              ) : null}
              <button type="button" onClick={() => update({ stock: draft.stock.filter((_, i) => i !== index) })}
                className="self-end text-left text-xs text-muted underline underline-offset-4 hover:text-danger">
                Quitar sucursal {n}
              </button>
            </div>
          );
        })}
        <button
          type="button"
          disabled={branches.length === 0}
          onClick={() => {
            const free = branches.find((b) => !used.has(String(b.id))) ?? branches[0];
            update({ stock: [...draft.stock, { branch: free ? String(free.id) : "", quantity: "", unit_cost: "" }] });
          }}
          className={internalButtonClass}
        >
          Agregar sucursal
        </button>
      </div>
    );
  }

  function supplierBlock() {
    if (!offersSupplier) return null;
    const o = draft.offer;
    const setOffer = (change: Partial<typeof o>) => update({ offer: { ...o, ...change } });
    return (
      <div className="space-y-4 rounded-lg border border-bd-border p-4">
        {kind !== "supplier" ? (
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input type="checkbox" checked={draft.supplier_mode !== "none"}
              onChange={(e) => update({ supplier_mode: e.target.checked ? "existing" : "none" })} />
            También se vende bajo pedido con la oferta de un proveedor
          </label>
        ) : null}
        {wantsSupplier ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Field id="reg-supplier" label="Proveedor">
              <select
                id="reg-supplier"
                value={draft.supplier_mode === "new" ? NEW_SUPPLIER : draft.supplier_id}
                onChange={(e) => (e.target.value === NEW_SUPPLIER
                  ? update({ supplier_mode: "new", supplier_id: "" })
                  : update({ supplier_mode: "existing", supplier_id: e.target.value }))}
                className={inputCls}
              >
                <option value="">Elige un proveedor</option>
                {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                <option value={NEW_SUPPLIER}>Proveedor nuevo…</option>
              </select>
            </Field>
            {draft.supplier_mode === "new" ? (
              <Field id="reg-supplier-name" label="Nombre del proveedor nuevo">
                <input id="reg-supplier-name" value={draft.supplier_name} onChange={(e) => update({ supplier_name: e.target.value })} className={inputCls} />
              </Field>
            ) : null}
            <Field id="reg-offer-price" label="Precio comercial (S/)" hint="Lo que paga el cliente cuando compra bajo pedido.">
              <input id="reg-offer-price" inputMode="decimal" value={o.commercial_price} onChange={(e) => setOffer({ commercial_price: e.target.value })} className={inputCls} />
            </Field>
            {costs.enabled ? (
              <Field id="reg-offer-cost" label="Costo pactado (S/)">
                <input id="reg-offer-cost" inputMode="decimal" value={o.agreed_cost} onChange={(e) => setOffer({ agreed_cost: e.target.value })} className={inputCls} />
              </Field>
            ) : null}
            <Field id="reg-offer-qty" label="Cantidad confirmada" hint="Vacío = sin confirmar: no se vende.">
              <input id="reg-offer-qty" inputMode="numeric" value={o.confirmed_quantity} onChange={(e) => setOffer({ confirmed_quantity: e.target.value })} className={inputCls} />
            </Field>
            <Field id="reg-offer-from" label="Vigente desde">
              <input id="reg-offer-from" type="date" value={o.valid_from} onChange={(e) => setOffer({ valid_from: e.target.value })} className={inputCls} />
            </Field>
            <Field id="reg-offer-until" label="Vigente hasta">
              <input id="reg-offer-until" type="date" value={o.valid_until} onChange={(e) => setOffer({ valid_until: e.target.value })} className={inputCls} />
            </Field>
            <Field id="reg-offer-lead" label="Plazo de entrega (días)">
              <input id="reg-offer-lead" inputMode="numeric" value={o.lead_time_days} onChange={(e) => setOffer({ lead_time_days: e.target.value })} className={inputCls} />
            </Field>
            <p className="text-xs text-muted md:col-span-2">
              El producto queda en «Bajo pedido»: se vende con esta oferta sin tocar tu stock propio.
            </p>
          </div>
        ) : null}
      </div>
    );
  }

  function stockStep() {
    return (
      <section className="space-y-5">
        <h2 className="text-lg font-bold text-foreground">{kind === "supplier" ? "Proveedor y oferta" : "Stock"}</h2>
        {kind === "supplier" ? <Notice>{stock?.reason}</Notice> : null}
        {kind === "new_device" ? newDeviceStock() : null}
        {kind === "used_device" ? usedDeviceStock() : null}
        {kind === "accessory" ? accessoryStock() : null}
        {supplierBlock()}
        {problemList}
        <div className="flex justify-between">
          <button type="button" onClick={() => goTo("product")} className={internalButtonClass}>Atrás</button>
          <button type="button" onClick={review} className={internalPrimaryButtonClass}>Revisar</button>
        </div>
      </section>
    );
  }

  function publicationLine(result: RegistrationResult) {
    const p = result.publication;
    if (!p.requested) return "No se mostrará en e-commerce (así lo pediste).";
    if (!result.product.created && result.product.is_published_online) return "Ya está publicado en e-commerce.";
    if (p.problems.length === 0) return "Se mostrará en e-commerce.";
    const reasons = p.problems.map(publicationProblemLabel).join(", ");
    if (p.problems.every((code) => code === "no_image") && images.length) {
      return `Se creará oculto porque ${reasons}; se publicará en cuanto suban las imágenes elegidas.`;
    }
    return `No se publicará en e-commerce: ${reasons}. Se creará oculto; lo puedes publicar después desde Publicación e-commerce.`;
  }

  /** A new model follows its first variant: hidden with it (`publication.model_hidden`). */
  function hiddenModelLine(result: RegistrationResult, registered: boolean) {
    if (!result.publication.model_hidden || !result.model) return null;
    const name = `El modelo nuevo «${result.model.name}»`;
    return registered
      ? `${name} quedó oculto en e-commerce porque su primera variante no se publicó; publícalo desde Publicación e-commerce cuando la variante esté lista.`
      : `${name} se creará oculto en e-commerce, igual que su primera variante.`;
  }

  function reviewStep() {
    const back = reviewError instanceof RegistrationApiError ? stepOfError(reviewError) : "stock";
    const kindLabel = ARTICLE_KINDS.find((k) => k.kind === kind)?.label ?? "";
    return (
      <section className="space-y-5">
        <h2 className="text-lg font-bold text-foreground">Revisar y registrar</h2>
        {busy && !preview && !reviewError ? <p className="text-sm text-muted">Comprobando con el servidor…</p> : null}
        {reviewError ? (
          <div role="alert" className="space-y-3 rounded-lg border border-danger/40 bg-background px-4 py-3 text-sm text-danger">
            <p>{errorText(reviewError)}</p>
            <button type="button" onClick={() => goTo(back === "review" ? "stock" : back)} className={internalButtonClass}>
              Volver a {stepLabel(back === "review" ? "stock" : back, kind)}
            </button>
          </div>
        ) : null}
        {preview ? (
          <div className="space-y-3 rounded-lg border border-bd-border bg-background p-4 text-sm">
            <dl className="grid gap-2 sm:grid-cols-[max-content_1fr] sm:gap-x-6">
              <dt className="text-muted">Tipo</dt><dd className="text-foreground">{kindLabel}</dd>
              <dt className="text-muted">Producto</dt>
              <dd className="text-foreground">
                {preview.product.created ? `${preview.product.name} (nuevo)` : `${preview.product.name} (ya existe: se le agrega lo que registras)`}
              </dd>
              {preview.model ? (<><dt className="text-muted">Modelo</dt><dd className="text-foreground">{preview.model.name}{preview.model.created ? " (nuevo)" : ""}</dd></>) : null}
              {preview.units.length ? (<><dt className="text-muted">Equipos</dt><dd className="text-foreground">{preview.units.length} con su serie</dd></>) : null}
              {kind === "accessory" && draft.stock.length && stock?.enabled ? (
                <><dt className="text-muted">Stock</dt><dd className="text-foreground">
                  {draft.stock.map((row) => `${branches.find((b) => String(b.id) === row.branch)?.name ?? "—"}: ${row.quantity}`).join(" · ")}
                </dd></>
              ) : null}
              {preview.supplier ? (<><dt className="text-muted">Proveedor</dt><dd className="text-foreground">{preview.supplier.name}{preview.supplier.created ? " (nuevo)" : ""} · bajo pedido</dd></>) : null}
              <dt className="text-muted">E-commerce</dt>
              <dd className="text-foreground">
                {publicationLine(preview)}
                {hiddenModelLine(preview, false) ? <span className="block text-warning">{hiddenModelLine(preview, false)}</span> : null}
              </dd>
            </dl>
            {preview.warnings.length ? (
              <ul className="list-disc space-y-1 pl-5 text-warning">{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            ) : null}
          </div>
        ) : null}
        {submitError ? <p role="alert" className="text-sm text-danger">{submitError}</p> : null}
        <div className="flex justify-between">
          <button type="button" disabled={busy} onClick={() => goTo("stock")} className={internalButtonClass}>Atrás</button>
          <button type="button" disabled={busy || !preview} onClick={register} className={internalPrimaryButtonClass}>
            {busy && preview ? "Registrando…" : "Registrar artículo"}
          </button>
        </div>
      </section>
    );
  }

  function doneStep() {
    if (!done) return null;
    const { result, failures, publishedLater, refusedLater, modelPublishedLater } = done;
    const modelVisible = Boolean(result.model) && (!result.publication.model_hidden || modelPublishedLater);
    const storefront = result.links.storefront
      ?? (publishedLater && result.product.is_active
        ? (result.model && modelVisible ? `/modelo/${result.model.slug}` : `/product/${result.product.slug}`)
        : null);
    const hiddenModel = modelPublishedLater ? null : hiddenModelLine(result, true);
    let publication: string;
    if (result.publication.published || publishedLater) publication = "Publicado en e-commerce.";
    else if (!result.publication.requested) publication = "Oculto en e-commerce, como pediste.";
    else {
      const codes = refusedLater.length ? refusedLater : result.publication.problems;
      publication = `No se publicó en e-commerce: ${codes.map(publicationProblemLabel).join(", ")}.`;
    }
    return (
      <section className="space-y-4">
        <h2 className="text-lg font-bold text-foreground">Artículo registrado</h2>
        <p className="text-sm text-foreground">
          {result.product.name}
          {result.replayed ? " — ya se había registrado con este mismo envío; no se repitió." : ""}
        </p>
        <p className="text-sm text-muted">{publication}</p>
        {hiddenModel ? <p className="text-sm text-warning">{hiddenModel}</p> : null}
        {result.warnings.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-warning">{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        ) : null}
        {failures.length ? (
          <div className="rounded-lg border border-warning-border bg-warning-surface px-4 py-3 text-sm text-warning">
            <p className="font-semibold">El artículo se registró, pero no todo lo que sigue se completó:</p>
            <ul className="mt-1 list-disc pl-5">{failures.map((f) => <li key={f}>{f}</li>)}</ul>
          </div>
        ) : null}
        {(result.product.id ?? result.product.existing_id) !== null ? (
          // INV-UX-A1. The storefront link below is the wizard's own (it knows the model page).
          <NextStepBar
            product={{ id: (result.product.id ?? result.product.existing_id) as number, name: result.product.name, slug: result.product.slug }}
            showStorefront={false}
            labelBatch={extractLabelBatch(result)}
          />
        ) : null}
        <div className="flex flex-wrap gap-2">
          {result.links.product ? <Link href={result.links.product} className={internalPrimaryButtonClass}>Abrir el producto</Link> : null}
          {result.links.units ? <Link href={result.links.units} className={internalButtonClass}>Ver los equipos</Link> : null}
          {storefront ? (
            <a href={storefront} target="_blank" rel="noreferrer" className={internalButtonClass}>Ver en la tienda (pestaña nueva)</a>
          ) : null}
          <button type="button" onClick={restart} className={internalButtonClass}>Registrar otro artículo</button>
        </div>
      </section>
    );
  }

  return (
    <div className="space-y-5 rounded-xl border border-bd-border bg-surface p-5 sm:p-6">
      {stepper}
      {loadNotice ? <Notice>{loadNotice}</Notice> : null}
      {step === "kind" ? kindStep() : null}
      {step === "product" ? productStep() : null}
      {step === "stock" ? stockStep() : null}
      {step === "review" ? reviewStep() : null}
      {step === "done" ? doneStep() : null}
    </div>
  );
}
