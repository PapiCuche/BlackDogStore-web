"use client";

/**
 * La imagen de cada categoría, colocada desde el panel.
 *
 * Cada hueco se guarda en cuanto cambia: aquí no hay más campos que esperar, y
 * un botón «Guardar» por categoría sería un paso que nadie recordaría dar.
 */

import { useEffect, useState } from "react";
import { ImageUploadField } from "../../components/ImageUploadField";
import { fetchAdminCategories, updateAdminCategory, type AdminCategory } from "../../../lib/admin";

export function CategoryImagesEditor({
  companyId, canManage, onNotice,
}: {
  companyId: number | null;
  canManage: boolean;
  onNotice: (message: string) => void;
}) {
  const [categories, setCategories] = useState<AdminCategory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState<Record<number, string>>({});

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rows = await fetchAdminCategories();
        if (!cancelled) setCategories(rows);
      } catch (err) {
        if (!cancelled) {
          setCategories([]);
          setError(err instanceof Error ? err.message : "No se pudieron cargar las categorías.");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [companyId]);

  async function place(category: AdminCategory, url: string) {
    setFailures((current) => ({ ...current, [category.id]: "" }));
    try {
      const saved = await updateAdminCategory(category.id, { image_url: url });
      setCategories((rows) => (rows ?? []).map((row) => (row.id === saved.id ? saved : row)));
      onNotice(url ? `Imagen de «${category.name}» guardada.` : `Imagen de «${category.name}» quitada.`);
    } catch (err) {
      setFailures((current) => ({
        ...current,
        [category.id]: err instanceof Error ? err.message : "No se pudo guardar.",
      }));
    }
  }

  if (categories === null) return <p className="text-sm text-muted">Cargando categorías…</p>;
  if (error) return <p role="alert" className="text-sm text-danger">{error}</p>;
  if (categories.length === 0) {
    return <p className="text-sm text-muted">Todavía no hay categorías en el catálogo.</p>;
  }

  return (
    <ul className="grid gap-5 sm:grid-cols-2">
      {categories.map((category) => (
        <li key={category.id} className="min-w-0 rounded-xl border border-bd-border p-4">
          <ImageUploadField
            label={category.name}
            name={`category-${category.id}`}
            value={category.image_url ?? ""}
            companyId={companyId}
            readOnly={!canManage}
            errors={failures[category.id] ? { [`category-${category.id}`]: [failures[category.id]] } : undefined}
            onChange={(_name, url) => void place(category, url)}
          />
        </li>
      ))}
    </ul>
  );
}
