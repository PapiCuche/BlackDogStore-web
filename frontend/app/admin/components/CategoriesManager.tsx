"use client";

/**
 * Productos › Categorías — las familias del catálogo, como la tienda las muestra.
 *
 * Tres decisiones por categoría, y son de la tienda, no del código:
 *   · activa        se ofrece al público (menú, filtro, portada). Retirarla
 *                   no oculta sus productos;
 *   · en la portada la portada la ilustra entre sus familias;
 *   · el orden      en que aparecen.
 *
 * Cada cambio se guarda al momento y la lista se repinta con lo que el
 * servidor devolvió: no hay un estado local que pueda decir otra cosa.
 */

import { useCallback, useEffect, useState } from "react";

import {
  createAdminCategory, fetchAdminCategories, updateAdminCategory, type AdminCategory,
} from "../../lib/admin";

import { internalButtonClass, internalInputClass, internalPrimaryButtonClass } from "./internal-ui";

function ordered(rows: AdminCategory[]): AdminCategory[] {
  return [...rows].sort(
    (a, b) => (a.home_order ?? 0) - (b.home_order ?? 0) || a.name.localeCompare(b.name, "es"),
  );
}

const countLabel = (n: number) => (n === 1 ? "1 producto" : `${n} productos`);

export function CategoriesManager({ canManage }: { canManage: boolean }) {
  const [categories, setCategories] = useState<AdminCategory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [name, setName] = useState("");

  const load = useCallback(
    () => fetchAdminCategories().then((rows) => setCategories(ordered(rows))),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    fetchAdminCategories().then(
      (rows) => { if (!cancelled) setCategories(ordered(rows)); },
      (err) => {
        if (cancelled) return;
        setCategories([]);
        setError(err instanceof Error ? err.message : "No se pudieron cargar las categorías.");
      },
    );
    return () => { cancelled = true; };
  }, []);

  /** Run writes, then show what the server has — also when one of them failed. */
  async function save(action: () => Promise<unknown>, done?: string) {
    setWorking(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar.");
    } finally {
      await load().catch(() => undefined);
      setWorking(false);
    }
  }

  function move(index: number, step: -1 | 1) {
    if (!categories || !categories[index + step]) return;
    const next = [...categories];
    [next[index], next[index + step]] = [next[index + step], next[index]];
    // The whole list is renumbered by POSITION, and only the rows whose number
    // changes are written. Swapping the two stored numbers is not enough:
    // categories nobody arranged yet share one, and a row left at its old
    // number would jump ahead of the two that just moved.
    void save(async () => {
      for (const [position, category] of next.entries()) {
        if (category.home_order !== position + 1) {
          await updateAdminCategory(category.id, { home_order: position + 1 });
        }
      }
    });
  }

  if (categories === null) return <p className="text-sm text-muted">Cargando categorías…</p>;

  return (
    <div className="space-y-6">
      {error ? (
        <p role="alert" className="rounded-lg border border-danger-border bg-danger-surface px-3 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}
      {notice ? <p role="status" className="text-sm text-muted">{notice}</p> : null}

      {categories.length === 0 ? (
        <p className="text-sm text-muted">Todavía no hay categorías en el catálogo.</p>
      ) : (
        <ol className="space-y-2">
          {categories.map((category, index) => (
            <li
              key={category.id}
              className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-bd-border bg-surface p-4"
            >
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-foreground [overflow-wrap:anywhere]">{category.name}</h3>
                <p className="mt-0.5 text-xs text-muted">{countLabel(category.product_count ?? 0)}</p>
              </div>
              <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <input
                    type="checkbox"
                    aria-label={`«${category.name}» activa`}
                    checked={category.is_active !== false}
                    disabled={!canManage || working}
                    onChange={(e) => void save(
                      () => updateAdminCategory(category.id, { is_active: e.target.checked }),
                      e.target.checked
                        ? `«${category.name}» vuelve a ofrecerse en la tienda.`
                        : `«${category.name}» ya no se ofrece; sus productos siguen en el catálogo.`,
                    )}
                    className="h-4 w-4 accent-[var(--primary)]"
                  />
                  Activa
                </label>
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <input
                    type="checkbox"
                    aria-label={`Mostrar «${category.name}» en la portada`}
                    checked={category.show_on_home !== false}
                    disabled={!canManage || working}
                    onChange={(e) => void save(
                      () => updateAdminCategory(category.id, { show_on_home: e.target.checked }),
                    )}
                    className="h-4 w-4 accent-[var(--primary)]"
                  />
                  En la portada
                </label>
                {canManage ? (
                  <span className="inline-flex gap-1.5">
                    <button
                      type="button"
                      aria-label={`Subir «${category.name}»`}
                      disabled={working || index === 0}
                      onClick={() => move(index, -1)}
                      className="grid h-9 w-9 place-items-center rounded-lg border border-bd-border text-sm text-foreground transition-colors hover:border-foreground/25 disabled:opacity-30"
                    >
                      <span aria-hidden="true">↑</span>
                    </button>
                    <button
                      type="button"
                      aria-label={`Bajar «${category.name}»`}
                      disabled={working || index === categories.length - 1}
                      onClick={() => move(index, 1)}
                      className="grid h-9 w-9 place-items-center rounded-lg border border-bd-border text-sm text-foreground transition-colors hover:border-foreground/25 disabled:opacity-30"
                    >
                      <span aria-hidden="true">↓</span>
                    </button>
                  </span>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}

      {canManage ? (
        <form
          className="flex flex-wrap items-end gap-3 rounded-xl border border-bd-border bg-surface p-4"
          onSubmit={(e) => {
            e.preventDefault();
            const value = name.trim();
            if (!value) return;
            void save(async () => {
              await createAdminCategory({ name: value });
              setName("");
            }, `Categoría «${value}» creada.`);
          }}
        >
          <label className="block min-w-[14rem] flex-1 text-xs text-muted">
            Nombre de la categoría nueva
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} className={`mt-1.5 ${internalInputClass}`} />
          </label>
          <button type="submit" className={internalPrimaryButtonClass} disabled={working || !name.trim()}>
            Crear categoría
          </button>
        </form>
      ) : null}

      <p className="text-xs text-muted">
        La imagen de cada categoría se coloca en{" "}
        <a href="/admin/settings/storefront" className={`${internalButtonClass} !inline !border-0 !bg-transparent !p-0 underline underline-offset-4`}>
          Administración › Escaparate
        </a>.
      </p>
    </div>
  );
}
