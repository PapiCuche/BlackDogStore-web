"use client";

import Link from "next/link";
import { useState } from "react";
import { AdminProduct, AdminCategory, createAdminProduct, patchAdminProduct } from "../../lib/admin";

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
    image_url: product?.image_url ?? "",
    category: product?.category_id ? String(product.category_id) : "",
    is_active: product?.is_active ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    const editable = {
      name: form.name.trim(),
      slug: form.slug.trim() || undefined,
      description: form.description.trim(),
      price: priceNum.toFixed(2),
      image_url: form.image_url.trim(),
      category: form.category ? parseInt(form.category, 10) : null,
      is_active: form.is_active,
    };

    setSaving(true);
    try {
      const saved = product
        ? await patchAdminProduct(product.id, editable)
        : await createAdminProduct({ ...editable, inventory: inventoryNum });
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error al guardar.");
    } finally {
      setSaving(false);
    }
  }

  const inputCls =
    "w-full bg-zinc-900 border border-white/[0.1] rounded px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-white/30 disabled:opacity-50";

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div>
          <label className="block text-xs text-zinc-400 mb-1.5">
            Nombre <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            required
            disabled={saving}
            className={inputCls}
          />
        </div>
        <div>
          <label className="block text-xs text-zinc-400 mb-1.5">
            Slug (opcional — se genera automáticamente)
          </label>
          <input
            type="text"
            value={form.slug}
            onChange={(e) => set("slug", e.target.value)}
            disabled={saving}
            className={inputCls}
          />
        </div>
        <div>
          <label className="block text-xs text-zinc-400 mb-1.5">
            Precio (S/) <span className="text-red-400">*</span>
          </label>
          <input
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
          <label className="block text-xs text-zinc-400 mb-1.5">
            {product ? "Stock" : "Stock inicial"}
          </label>
          {product ? (
            <div className="flex items-center gap-3">
              <span className="text-sm tabular-nums text-zinc-300">
                {product.inventory} u.
              </span>
              <Link
                href={`/admin/products/${product.id}/stock-card`}
                className="text-xs text-zinc-500 underline underline-offset-4 transition hover:text-white"
              >
                Ver Kardex
              </Link>
            </div>
          ) : (
            <input
              type="number"
              min="0"
              value={form.inventory}
              onChange={(e) => set("inventory", e.target.value)}
              disabled={saving}
              className={inputCls}
            />
          )}
          <p className="mt-1.5 text-[11px] text-zinc-600">
            {product
              ? "El stock se mueve desde Inventario, con sucursal y motivo."
              : "Se registrará como stock inicial en tu sucursal, con su línea en el Kardex."}
          </p>
        </div>
        <div>
          <label className="block text-xs text-zinc-400 mb-1.5">Categoría</label>
          <select
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
        <div>
          <label className="block text-xs text-zinc-400 mb-1.5">URL de imagen</label>
          <input
            type="url"
            value={form.image_url}
            onChange={(e) => set("image_url", e.target.value)}
            disabled={saving}
            className={inputCls}
          />
        </div>
      </div>
      <div>
        <label className="block text-xs text-zinc-400 mb-1.5">Descripción</label>
        <textarea
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
          className="accent-white"
        />
        <label htmlFor="is_active" className="text-sm text-zinc-300">
          Producto activo (visible en catálogo y disponible en checkout)
        </label>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <button
        type="submit"
        disabled={saving}
        className="px-5 py-2 bg-white text-black text-sm font-medium rounded hover:bg-zinc-200 disabled:opacity-50 transition-colors"
      >
        {saving ? "Guardando…" : product ? "Guardar cambios" : "Crear producto"}
      </button>
    </form>
  );
}
