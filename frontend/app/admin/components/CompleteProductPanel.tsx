"use client";

/**
 * ECOM-READY-D1 — «Completar ficha»: un panel por producto, dentro de
 * Publicación e-commerce.
 *
 * Arregla lo que falta (precio, categoría, descripción, foto) SIN salir de la
 * pantalla, enseña lo que el cliente vería (la tarjeta pública que devuelve el
 * servidor, también si está oculto) y deja publicar u ocultar de forma
 * explícita. No trae reglas propias:
 *
 *   · lo pendiente sale de `publication_problems` (el servidor, la misma regla);
 *   · publicar pasa por la acción en bloque con un solo id (auditada, y que
 *     rechaza lo que tiene problemas); ocultar siempre se puede;
 *   · las fotos van por la galería (mismos límites y validación del servidor);
 *   · quien sólo puede ver, ve la vista previa y no los editores.
 *
 * Nada se guarda en el navegador.
 */

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  PRODUCT_IMAGE_ACCEPT,
  PRODUCT_IMAGE_MAX_BYTES,
  bulkSetProductVisibility,
  fetchAdminCategories,
  fetchProductPreview,
  patchAdminProduct,
  publicationProblemLabel,
  uploadProductImage,
  type AdminCategory,
  type AdminProduct,
  type StorefrontPreview,
} from "../../lib/admin";
import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

const PRICE_PATTERN = /^\d{1,8}(\.\d{1,2})?$/;

/** Lo que se escribe como precio, o el motivo por el que no vale. */
export function validatePrice(raw: string): string | null {
  const value = raw.trim().replace(",", ".");
  if (!PRICE_PATTERN.test(value) || Number(value) <= 0) {
    return "Escribe un precio mayor que 0, con hasta 2 decimales.";
  }
  return null;
}

function Field({
  id, label, hint, error, children,
}: { id: string; label: string; hint?: string; error?: string | null; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs font-semibold text-muted">{label}</label>
      {children}
      {hint && !error ? <p id={`${id}-hint`} className="mt-1 text-xs text-muted">{hint}</p> : null}
      {error ? <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
    </div>
  );
}

function PreviewCard({ preview }: { preview: StorefrontPreview }) {
  const { product, visibility, availability } = preview;
  const photo = product.images?.[0]?.url || product.image_url;
  const used = visibility.placement === "model_page";
  return (
    <article aria-label="Tarjeta del cliente" className="overflow-hidden rounded-xl border border-bd-border bg-background">
      <div className="flex h-40 items-center justify-center bg-surface p-3">
        {photo ? (
          // eslint-disable-next-line @next/next/no-img-element -- staff preview of the public card
          <img src={photo} alt={product.name} className="max-h-full max-w-full object-contain" />
        ) : (
          <p className="text-xs text-muted">Sin foto: el cliente verá un espacio vacío.</p>
        )}
      </div>
      <div className="space-y-1.5 p-4">
        {product.category ? <p className="text-xs text-muted">{product.category.name}</p> : null}
        <h4 className="text-sm font-bold text-foreground">{product.name}</h4>
        {product.description ? <p className="text-xs text-muted">{product.description}</p> : null}
        <p className="text-base font-bold text-foreground">
          {used ? "Precio por equipo" : `S/ ${product.price}`}
        </p>
        <p className="text-xs font-semibold text-foreground">{availability.label}</p>
        {availability.note ? <p className="text-xs text-warning">{availability.note}</p> : null}
        {used ? (
          <p className="text-xs text-muted">Se ofrece en la página de su modelo, equipo por equipo.</p>
        ) : null}
        {visibility.model_hidden ? (
          <p className="text-xs text-warning">Su modelo está oculto: la página del modelo no se muestra.</p>
        ) : null}
      </div>
    </article>
  );
}

export function CompleteProductPanel({
  product, canManage, companyId, onClose, onChanged,
}: {
  product: AdminProduct;
  canManage: boolean;
  companyId: number | null;
  onClose: () => void;
  /** Algo cambió: la lista de detrás se vuelve a pedir. */
  onChanged: () => void;
}) {
  const uid = useId();
  const titleId = `${uid}-title`;
  const panel = useRef<HTMLElement>(null);
  const priceInput = useRef<HTMLInputElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  const [preview, setPreview] = useState<StorefrontPreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [categories, setCategories] = useState<AdminCategory[] | null>(null);

  const startPrice = Number(product.price) > 0 ? product.price : "";
  const [price, setPrice] = useState(startPrice);
  const [categoryId, setCategoryId] = useState(product.category_id ? String(product.category_id) : "");
  const [description, setDescription] = useState(product.description ?? "");
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [photoFile, setPhotoFile] = useState<File | null>(null);

  const refresh = useCallback(async () => {
    try {
      setPreview(await fetchProductPreview(product.id, companyId));
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "No se pudo cargar la vista previa.");
    }
  }, [product.id, companyId]);

  useEffect(() => {
    let alive = true;
    fetchProductPreview(product.id, companyId)
      .then((data) => { if (alive) { setPreview(data); setLoadError(null); } })
      .catch((err) => { if (alive) setLoadError(err instanceof Error ? err.message : "No se pudo cargar la vista previa."); });
    return () => { alive = false; };
  }, [product.id, companyId]);

  useEffect(() => {
    if (!canManage) return;
    let alive = true;
    fetchAdminCategories(companyId)
      .then((data) => { if (alive) setCategories(data); })
      .catch(() => { if (alive) setCategories([]); });
    return () => { alive = false; };
  }, [canManage, companyId]);

  useEffect(() => {
    // Sin precio, lo primero es el precio; si no, entra por el cierre.
    const previous = document.activeElement as HTMLElement | null;
    (canManage && !startPrice ? priceInput.current : closeButton.current)?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab" || !panel.current) return;
      const stops = [...panel.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])",
      )];
      if (stops.length === 0) return;
      const at = stops.indexOf(document.activeElement as HTMLElement);
      const next = event.shiftKey ? (at <= 0 ? stops.length - 1 : at - 1) : (at + 1) % stops.length;
      event.preventDefault();
      stops[next].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const setError = (field: string, message: string | null) => setErrors((prev) => ({ ...prev, [field]: message }));

  async function save(field: "price" | "category" | "description", event?: FormEvent) {
    event?.preventDefault();
    setNotice(null);
    let data: Parameters<typeof patchAdminProduct>[1];
    if (field === "price") {
      const problem = validatePrice(price);
      if (problem) { setError("price", problem); return; }
      data = { price: price.trim().replace(",", ".") };
    } else if (field === "category") {
      if (!categoryId) { setError("category", "Elige una categoría."); return; }
      data = { category: Number(categoryId) };
    } else {
      data = { description: description.trim() };
    }
    setError(field, null);
    setBusy(field);
    try {
      await patchAdminProduct(product.id, data, companyId);
      setNotice(field === "price" ? "Precio guardado." : field === "category" ? "Categoría guardada." : "Descripción guardada.");
      await refresh();
      onChanged();
    } catch (err) {
      setError(field, err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      setBusy(null);
    }
  }

  function pickPhoto(file: File | null) {
    setPhotoFile(null);
    setError("photo", null);
    if (!file) return;
    if (!PRODUCT_IMAGE_ACCEPT.split(",").includes(file.type)) {
      setError("photo", "Usa una imagen PNG, JPG o WebP.");
      return;
    }
    if (file.size > PRODUCT_IMAGE_MAX_BYTES) {
      setError("photo", "La imagen pesa más de 8 MB.");
      return;
    }
    setPhotoFile(file);
  }

  async function uploadPhoto(event: FormEvent) {
    event.preventDefault();
    if (!photoFile) { setError("photo", "Elige primero una imagen."); return; }
    setNotice(null);
    setBusy("photo");
    try {
      await uploadProductImage(product.id, photoFile, product.name);
      setPhotoFile(null);
      setNotice("Foto subida.");
      await refresh();
      onChanged();
    } catch (err) {
      setError("photo", err instanceof Error ? err.message : "No se pudo subir la imagen.");
    } finally {
      setBusy(null);
    }
  }

  async function setVisibility(action: "publish" | "hide") {
    setNotice(null);
    setBusy(action);
    try {
      const answer = await bulkSetProductVisibility(action, { ids: [product.id] }, companyId);
      const refused = answer.refused.find((item) => item.id === product.id);
      if (refused) {
        setError("visibility", `No se publicó: ${refused.problems.map(publicationProblemLabel).join(", ")}.`);
      } else {
        setError("visibility", null);
        setNotice(action === "publish" ? "Publicado en la tienda en línea." : "Oculto de la tienda en línea.");
      }
      await refresh();
      onChanged();
    } catch (err) {
      setError("visibility", err instanceof Error ? err.message : "No se pudo cambiar la visibilidad.");
    } finally {
      setBusy(null);
    }
  }

  const problems = preview?.publication_problems ?? product.publication_problems ?? [];
  const noPrice = problems.includes("no_price");
  const visible = preview?.visibility.visible ?? false;
  const published = preview?.visibility.published ?? false;
  const hasProblems = problems.length > 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50">
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="h-full w-full max-w-lg overflow-y-auto overscroll-contain border-l border-bd-border bg-surface p-4 shadow-xl sm:p-6"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-muted">{canManage ? "Completar ficha" : "Ficha del producto"}</p>
            <h2 id={titleId} className="break-words text-lg font-bold text-foreground">{product.name}</h2>
          </div>
          <button ref={closeButton} type="button" onClick={onClose} className={internalButtonClass}>Cerrar</button>
        </div>

        {loadError ? <p role="alert" className="mt-4 text-sm text-danger">{loadError}</p> : null}
        {!preview && !loadError ? <p className="mt-4 text-sm text-muted">Cargando…</p> : null}
        {notice ? <p role="status" className="mt-4 text-sm text-foreground">{notice}</p> : null}

        {preview ? (
          <div
            role="status"
            data-testid="visibility-banner"
            className={`mt-4 rounded-lg border px-3 py-2 text-sm font-semibold ${visible ? "border-bd-border text-foreground" : "border-warning text-warning"}`}
          >
            {preview.visibility.label}
          </div>
        ) : null}

        <section aria-labelledby={`${uid}-pending`} className="mt-5">
          <h3 id={`${uid}-pending`} className="text-sm font-bold text-foreground">Para publicarse</h3>
          {hasProblems ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
              {problems.map((code) => <li key={code}>Le falta: {publicationProblemLabel(code)}</li>)}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-muted">Listo: no le falta nada.</p>
          )}
        </section>

        {canManage ? (
          <div className="mt-5 space-y-5">
            {noPrice ? (
              <p className="rounded-lg border border-warning px-3 py-2 text-sm text-warning">
                Empieza por el precio: sin precio no se puede publicar.
              </p>
            ) : null}

            <form onSubmit={(e) => save("price", e)} noValidate className="flex items-end gap-2">
              <div className="flex-1">
                <Field id={`${uid}-price`} label="Precio de venta (S/)" error={errors.price} hint="Lo que paga el cliente.">
                  <input
                    id={`${uid}-price`}
                    ref={priceInput}
                    inputMode="decimal"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                    placeholder="0.00"
                    aria-invalid={Boolean(errors.price)}
                    aria-describedby={errors.price ? `${uid}-price-error` : `${uid}-price-hint`}
                    className={internalInputClass}
                  />
                </Field>
              </div>
              <button type="submit" disabled={busy !== null} className={internalPrimaryButtonClass}>
                {busy === "price" ? "Guardando…" : "Guardar precio"}
              </button>
            </form>

            <form onSubmit={(e) => save("category", e)} noValidate className="flex items-end gap-2">
              <div className="flex-1">
                <Field id={`${uid}-category`} label="Categoría" error={errors.category}>
                  <select
                    id={`${uid}-category`}
                    value={categoryId}
                    onChange={(e) => setCategoryId(e.target.value)}
                    aria-invalid={Boolean(errors.category)}
                    className={internalInputClass}
                  >
                    <option value="">Sin categoría</option>
                    {(categories ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </Field>
              </div>
              <button type="submit" disabled={busy !== null} className={internalButtonClass}>
                {busy === "category" ? "Guardando…" : "Guardar categoría"}
              </button>
            </form>

            <form onSubmit={(e) => save("description", e)} noValidate className="space-y-2">
              <Field id={`${uid}-description`} label="Descripción corta" error={errors.description} hint="Una o dos frases que ayuden a decidir la compra.">
                <textarea
                  id={`${uid}-description`}
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className={internalInputClass}
                />
              </Field>
              <button type="submit" disabled={busy !== null} className={internalButtonClass}>
                {busy === "description" ? "Guardando…" : "Guardar descripción"}
              </button>
            </form>

            <form onSubmit={uploadPhoto} noValidate className="space-y-2">
              <Field id={`${uid}-photo`} label="Foto" error={errors.photo} hint="PNG, JPG o WebP, hasta 8 MB. Se agrega a la galería del producto.">
                <input
                  id={`${uid}-photo`}
                  type="file"
                  accept={PRODUCT_IMAGE_ACCEPT}
                  onChange={(e) => pickPhoto(e.target.files?.[0] ?? null)}
                  aria-invalid={Boolean(errors.photo)}
                  className="block w-full text-sm text-foreground"
                />
              </Field>
              <button type="submit" disabled={busy !== null || !photoFile} className={internalButtonClass}>
                {busy === "photo" ? "Subiendo…" : "Subir foto"}
              </button>
            </form>
          </div>
        ) : (
          <p className="mt-5 text-sm text-muted">Para cambiar estos datos hace falta el permiso «Gestionar productos».</p>
        )}

        {preview ? (
          <section aria-labelledby={`${uid}-preview`} className="mt-6">
            <h3 id={`${uid}-preview`} className="mb-2 text-sm font-bold text-foreground">Vista previa para el cliente</h3>
            <PreviewCard preview={preview} />
          </section>
        ) : null}

        {preview && preview.offered_units.length ? (
          <section aria-labelledby={`${uid}-units`} className="mt-6">
            <h3 id={`${uid}-units`} className="text-sm font-bold text-foreground">Unidades ofrecidas</h3>
            <p className="mt-1 text-xs text-muted">Equipos disponibles en la sucursal de despacho. Sin IMEI ni serie.</p>
            <ul className="mt-2 space-y-2">
              {preview.offered_units.map((unit, index) => (
                <li key={unit.label_code ?? `sin-codigo-${index}`} className="rounded-lg border border-bd-border bg-background p-3 text-sm">
                  <p className="font-semibold text-foreground">
                    {unit.label_code ?? "Equipo sin código de etiqueta"}
                    <span className="ml-2 text-xs font-normal text-muted">
                      {unit.offered ? "Se ofrece" : "No se ofrece"}
                    </span>
                  </p>
                  <p className="text-xs text-muted">
                    {[
                      unit.price ? `S/ ${unit.price}` : null,
                      unit.cosmetic_grade,
                      unit.battery_health != null ? `Batería ${unit.battery_health}%` : null,
                      unit.photo_count === 1 ? "1 foto" : `${unit.photo_count} fotos`,
                    ].filter(Boolean).join(" · ")}
                  </p>
                  {!unit.offered && unit.reason_label ? <p className="text-xs text-warning">{unit.reason_label}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {canManage && preview ? (
          <section aria-label="Visibilidad" className="mt-6 space-y-2 border-t border-bd-border pt-4">
            {errors.visibility ? <p role="alert" className="text-sm text-danger">{errors.visibility}</p> : null}
            <div className="flex flex-wrap items-center gap-2">
              {!published ? (
                <button
                  type="button"
                  onClick={() => setVisibility("publish")}
                  disabled={busy !== null || hasProblems}
                  aria-describedby={hasProblems ? `${uid}-publish-hint` : undefined}
                  className={internalPrimaryButtonClass}
                >
                  {busy === "publish" ? "Publicando…" : "Publicar en e-commerce"}
                </button>
              ) : null}
              {published ? (
                <button type="button" onClick={() => setVisibility("hide")} disabled={busy !== null} className={internalButtonClass}>
                  {busy === "hide" ? "Ocultando…" : "Ocultar del e-commerce"}
                </button>
              ) : null}
            </div>
            {!published && hasProblems ? (
              <p id={`${uid}-publish-hint`} className="text-xs text-muted">Completa lo que falta para poder publicarlo.</p>
            ) : null}
          </section>
        ) : null}
      </section>
    </div>
  );
}
