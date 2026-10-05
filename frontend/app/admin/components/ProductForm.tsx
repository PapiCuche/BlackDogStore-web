"use client";

import Link from "next/link";
import { useState } from "react";
import {
  AdminProduct, AdminCategory, PRODUCT_IMAGE_ACCEPT, createAdminProduct, patchAdminProduct,
  uploadProductImage,
} from "../../lib/admin";
import { ImageDropzone } from "./ImageDropzone";
import { rejectReason } from "./ProductGallery";
import { internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

type FormData = {
  name: string;
  slug: string;
  description: string;
  price: string;
  inventory: string;
  image_url: string;
  category: string;
  is_active: boolean;
};

type Props = {
  product?: AdminProduct;
  categories: AdminCategory[];
  onSaved: (product: AdminProduct) => void;
};

export function ProductForm({ product, categories, onSaved }: Props) {
  const [form, setForm] = useState<FormData>({
    name: product?.name ?? "",
    slug: product?.slug ?? "",
    description: product?.description ?? "",
    price: product?.price ?? "",
    inventory: product ? String(product.inventory) : "0",
    // Con galería la dirección es de la principal: no se edita ni se guarda aquí.
    image_url: product?.images?.length ? "" : product?.image_url ?? "",
    category: product?.category_id ? String(product.category_id) : "",
    is_active: product?.is_active ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // PRODUCT-MEDIA. Con galería, la dirección de la imagen es la de la
  // principal y la mantiene el servidor: aquí ni se muestra ni se envía.
  const hasGallery = Boolean(product?.images?.length);
  // Al CREAR todavía no hay producto donde colgar imágenes: se eligen aquí y
  // se suben en cuanto existe.
  const [queued, setQueued] = useState<File[]>([]);
  const [queueNotice, setQueueNotice] = useState<string | null>(null);
  const [created, setCreated] = useState<{ id: number; failures: string[] } | null>(null);

  function queueFiles(files: File[]) {
    const problems: string[] = [];
    const accepted: File[] = [];
    for (const file of files) {
      const reason = rejectReason(file);
      if (reason) problems.push(`${file.name}: ${reason}.`);
      else accepted.push(file);
    }
    setQueueNotice(problems.length ? `No se añadieron: ${problems.join(" ")}` : null);
    if (accepted.length) setQueued((prev) => [...prev, ...accepted]);
  }

  function set(field: keyof FormData, value: string | boolean) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const priceNum = parseFloat(form.price);
    if (isNaN(priceNum) || priceNum <= 0) {
      setError("El precio debe ser mayor que 0.");
      return;
    }
    const inventoryNum = parseInt(form.inventory, 10);
    if (!product && (isNaN(inventoryNum) || inventoryNum < 0)) {
      setError("El stock inicial no puede ser negativo.");
      return;
    }

    // PHASE 2D — `inventory` IS ONLY SENT ON CREATE.
    //
    // On create it is the opening stock, which the backend turns into an
    // `initial_stock` movement in the operator's branch. On edit the backend
    // REJECTS it: stock is a derived aggregate over BranchStock now, and typing
    // a new number into a product form would change stock with no branch, no
    // Kardex line, no actor and no reason. Editing stock is a movement.
    const typesAddress = !hasGallery && queued.length === 0;
    const editable = {
      name: form.name.trim(),
      slug: form.slug.trim() || undefined,
      description: form.description.trim(),
      price: priceNum.toFixed(2),
      ...(typesAddress ? { image_url: form.image_url.trim() } : {}),
      category: form.category ? parseInt(form.category, 10) : null,
      is_active: form.is_active,
    };

    setSaving(true);
    try {
      if (product) {
        onSaved(await patchAdminProduct(product.id, editable));
        return;
      }
      const saved = await createAdminProduct({ ...editable, inventory: inventoryNum });
      // El producto ya existe. Lo que falle a partir de aquí se dice tal cual:
      // no se finge que no se creó ni que las imágenes subieron.
      const failures: string[] = [];
      for (const file of queued) {
        try {
          await uploadProductImage(saved.id, file);
        } catch (err) {
          failures.push(`${file.name}: ${err instanceof Error ? err.message : "no se pudo subir."}`);
        }
      }
      if (failures.length) {
        setQueued([]);
        setCreated({ id: saved.id, failures });
        return;
      }
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al guardar.");
    } finally {
      setSaving(false);
    }
  }

  const inputCls = internalInputClass;

  if (created) {
    return (
      <div role="alert" className="rounded-xl border border-warning-border bg-warning-surface px-5 py-4 text-sm text-warning">
        <p className="font-semibold">
          El producto se creó, pero {created.failures.length === 1 ? "una imagen no se pudo subir" : `${created.failures.length} imágenes no se pudieron subir`}.
        </p>
        <ul className="mt-2 list-disc pl-5 text-xs">
          {created.failures.map((line) => <li key={line}>{line}</li>)}
        </ul>
        <Link href={`/admin/products/${created.id}`} className="mt-3 inline-flex font-semibold underline underline-offset-4">
          Abrir el producto
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div>
          <label htmlFor="product-name" className="block text-xs text-muted mb-1.5">
            Nombre <span className="text-danger">*</span>
          </label>
          <input
            id="product-name"
            name="name"
            type="text"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            required
            disabled={saving}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="product-slug" className="block text-xs text-muted mb-1.5">
            Slug (opcional — se genera automáticamente)
          </label>
          <input
            id="product-slug"
            name="slug"
            type="text"
            value={form.slug}
            onChange={(e) => set("slug", e.target.value)}
            disabled={saving}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="product-price" className="block text-xs text-muted mb-1.5">
            Precio (S/) <span className="text-danger">*</span>
          </label>
          <input
            id="product-price"
            name="price"
            type="number"
            step="0.01"
            min="0.01"
            value={form.price}
            onChange={(e) => set("price", e.target.value)}
            required
            disabled={saving}
            className={inputCls}
          />
        </div>
        <div>
          {product ? (
            <p className="block text-xs text-muted mb-1.5">Stock</p>
          ) : (
            <label htmlFor="product-inventory" className="block text-xs text-muted mb-1.5">
              Stock inicial
            </label>
          )}
          {product ? (
            <div className="flex items-center gap-3">
              <span className="text-sm tabular-nums text-foreground">
                {product.inventory} u.
              </span>
              <Link
                href={`/admin/products/${product.id}/stock-card`}
                className="text-xs text-muted underline underline-offset-4 transition hover:text-foreground"
              >
                Ver Kardex
              </Link>
            </div>
          ) : (
            <input
              id="product-inventory"
              name="inventory"
              type="number"
              min="0"
              value={form.inventory}
              onChange={(e) => set("inventory", e.target.value)}
              disabled={saving}
              className={inputCls}
            />
          )}
          <p className="mt-1.5 text-[11px] text-muted">
            {product
              ? "El stock se mueve desde Inventario, con sucursal y motivo."
              : "Se registrará como stock inicial en tu sucursal, con su línea en el Kardex."}
          </p>
        </div>
        <div>
          <label htmlFor="product-category" className="block text-xs text-muted mb-1.5">Categoría</label>
          <select
            id="product-category"
            name="category"
            value={form.category}
            onChange={(e) => set("category", e.target.value)}
            disabled={saving}
            className={inputCls}
          >
            <option value="">Sin categoría</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        {!hasGallery && queued.length === 0 ? (
          <div>
            <label htmlFor="product-image-url" className="block text-xs text-muted mb-1.5">
              Dirección de imagen externa (opcional)
            </label>
            {/* `text` y no `url`: una ruta del propio sitio es válida y un
                campo `url` la rechaza en el navegador. Valida el servidor. */}
            <input
              id="product-image-url"
              name="image_url"
              type="text"
              inputMode="url"
              value={form.image_url}
              onChange={(e) => set("image_url", e.target.value)}
              disabled={saving}
              placeholder="https://…"
              className={inputCls}
            />
            <p className="mt-1 text-xs text-muted">
              {product
                ? "Sólo si la imagen está alojada fuera. Para subir archivos usa la galería."
                : "Sólo si la imagen está alojada fuera. Para subir archivos, elígelos abajo."}
            </p>
          </div>
        ) : null}
      </div>
      <div>
        <label htmlFor="product-description" className="block text-xs text-muted mb-1.5">Descripción</label>
        <textarea
          id="product-description"
          name="description"
          value={form.description}
          onChange={(e) => set("description", e.target.value)}
          rows={3}
          disabled={saving}
          className={inputCls}
        />
      </div>
      <div className="flex items-center gap-3">
        <input
          type="checkbox"
          id="is_active"
          checked={form.is_active}
          onChange={(e) => set("is_active", e.target.checked)}
          disabled={saving}
          className="accent-current"
        />
        <label htmlFor="is_active" className="text-sm text-foreground">
          Producto activo (visible en catálogo y disponible en checkout)
        </label>
      </div>

      {error && <p role="alert" className="text-sm text-danger">{error}</p>}

      {!product ? (
        <div>
          <p className="mb-1.5 text-xs text-muted">Imágenes</p>
          <ImageDropzone
            label="Elegir imágenes"
            accept={PRODUCT_IMAGE_ACCEPT}
            onFiles={queueFiles}
            disabled={saving}
            hint="PNG, JPEG o WebP, hasta 8 MB cada una. Se suben al crear el producto; la primera queda como principal."
          />
          {queueNotice ? <p role="status" className="mt-2 text-xs text-warning">{queueNotice}</p> : null}
          {queued.length ? (
            <ul className="mt-3 space-y-1.5">
              {queued.map((file, index) => (
                <li key={`${index}-${file.name}`} className="flex items-center justify-between gap-3 rounded-lg border border-bd-border bg-background px-3 py-2 text-xs">
                  <span className="min-w-0 break-all text-foreground">{file.name}</span>
                  <span className="flex shrink-0 items-center gap-3">
                    {index === 0 ? <span className="text-muted">Principal</span> : null}
                    <button
                      type="button"
                      aria-label={`Quitar ${file.name}`}
                      disabled={saving}
                      onClick={() => setQueued((prev) => prev.filter((_, i) => i !== index))}
                      className="text-muted underline underline-offset-4 hover:text-danger"
                    >
                      Quitar
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <button
        type="submit"
        disabled={saving}
        className={internalPrimaryButtonClass}
      >
        {saving ? (product ? "Guardando…" : queued.length ? "Creando y subiendo imágenes…" : "Creando…") : product ? "Guardar cambios" : "Crear producto"}
      </button>
    </form>
  );
}
