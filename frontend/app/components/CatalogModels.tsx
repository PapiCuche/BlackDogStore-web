"use client";

/**
 * DC2 — the models of a category, above its product grid.
 *
 * A category that sells devices by model (a phone in several capacities and
 * colours, new and used) shows ONE card per model instead of one per variant.
 * The two lines are offered as tabs only when the category has both; their
 * names are the company's, from the API. A category without models renders
 * nothing here and the page stays exactly as it was.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { ProductImage } from "./ProductImage";
import { formatMoney } from "../lib/format";
import {
  fetchCatalogModels,
  type CatalogModelList,
  type ConditionLine,
} from "../lib/catalog-models";

type Props = {
  category: string;
  line: ConditionLine | "";
  onLineChange: (line: ConditionLine | "") => void;
  /** Told once the list is known, so the grid can leave out the products a model card already shows. */
  onLoaded?: (hasModels: boolean) => void;
};

export function CatalogModels({ category, line, onLineChange, onLoaded }: Props) {
  const [data, setData] = useState<CatalogModelList | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchCatalogModels({ category, line }).then(
      (loaded) => {
        if (cancelled) return;
        setData(loaded);
        onLoaded?.(loaded.results.length > 0);
      },
      () => {
        // Models are an addition to the catalogue: without them the product grid still sells everything.
        if (cancelled) return;
        setData(null);
        onLoaded?.(false);
      },
    );
    return () => { cancelled = true; };
    // `onLoaded` is a notification, not an input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, line]);

  if (!data) return null;
  const { lines, results } = data;
  const both = (lines.new?.count ?? 0) > 0 && (lines.used?.count ?? 0) > 0;
  if (results.length === 0 && !line) return null;

  const tab = (value: ConditionLine | "", label: string) => (
    <button
      key={value || "all"}
      type="button"
      aria-pressed={line === value}
      onClick={() => onLineChange(value)}
      className={`min-h-11 rounded-full px-5 py-2 text-sm font-bold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
        line === value ? "bg-foreground text-background" : "text-muted hover:text-foreground"
      }`}
    >
      {label}
    </button>
  );

  return (
    <section aria-labelledby="catalog-models-title" className="mb-12">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <h2 id="catalog-models-title" className="font-display text-2xl font-semibold tracking-tight text-foreground">
          Modelos
        </h2>
        {both ? (
          <div role="group" aria-label="Línea" className="inline-flex rounded-full border border-bd-border bg-surface p-1">
            {tab("", "Todos")}
            {tab("new", lines.new.label)}
            {tab("used", lines.used.label)}
          </div>
        ) : null}
      </div>

      {results.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-bd-border px-6 py-10 text-center text-sm text-muted">
          No hay modelos en esta línea por ahora.
        </p>
      ) : (
        <ul className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {results.map((model) => (
            <li key={model.slug} className="min-w-0">
              <Link
                href={`/modelo/${model.slug}${line ? `?line=${line}` : ""}`}
                className="group flex h-full flex-col overflow-hidden rounded-lg border border-bd-border bg-background transition-shadow duration-300 hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary"
              >
                <div className="relative aspect-[4/3] overflow-hidden">
                  {model.image_url ? (
                    <ProductImage
                      src={model.image_url}
                      alt={model.name}
                      sizes="(max-width: 640px) 100vw, (max-width: 1280px) 50vw, 33vw"
                      className="object-contain p-6 transition-transform duration-300 ease-out group-hover:scale-105"
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center">
                      <div className="flex h-20 w-20 items-center justify-center rounded-full border border-bd-border text-2xl font-semibold text-foreground/[0.12]" aria-hidden="true">
                        {model.name.trim().charAt(0).toUpperCase() || "·"}
                      </div>
                    </div>
                  )}
                </div>
                <div className="flex flex-1 flex-col gap-2 p-5">
                  <p className="font-display text-lg font-semibold leading-snug text-foreground">{model.name}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {model.has_new && lines.new?.label ? (
                      <span className="rounded-full border border-bd-border px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-muted">{lines.new.label}</span>
                    ) : null}
                    {model.has_used && lines.used?.label ? (
                      <span className="rounded-full border border-bd-border px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-muted">{lines.used.label}</span>
                    ) : null}
                  </div>
                  {model.from_price !== null ? (
                    <p className="mt-auto pt-2 text-sm text-muted">
                      Desde <span className="font-display text-lg font-extrabold tabular-nums text-foreground">S/ {formatMoney(model.from_price)}</span>
                    </p>
                  ) : null}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
