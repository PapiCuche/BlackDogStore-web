"use client";

import { useEffect, useState, useCallback, Suspense } from "react";
import { useStorefront } from "../components/StorefrontProvider";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { ProductCard } from "../components/ProductCard";
import { fetcher, apiUrl } from "../lib/api";

type Category = { id: number; name: string; slug: string };
type Product = {
  id: number;
  slug: string;
  name: string;
  description?: string;
  price: number;
  inventory?: number;
  category?: Category;
  image_url?: string;
  average_rating?: number | null;
  review_count?: number;
};

const ORDERING_OPTIONS = [
  { value: "", label: "Relevancia" },
  { value: "price", label: "Precio: menor a mayor" },
  { value: "-price", label: "Precio: mayor a menor" },
  { value: "name", label: "Nombre A–Z" },
  { value: "newest", label: "Más recientes" },
];

function CatalogContent() {
  const whatsappLink = useStorefront().contact.whatsapp_link;
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryError, setCategoryError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const selectedCategory = searchParams.get("category") ?? "";
  const search = searchParams.get("search") ?? "";
  const inStock = searchParams.get("in_stock") === "true";
  const ordering = searchParams.get("ordering") ?? "";
  const [searchInput, setSearchInput] = useState(search);

  const updateParam = useCallback(
    (key: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
      const query = params.toString();
      router.push(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  const clearFilters = useCallback(() => {
    setSearchInput("");
    router.push(pathname, { scroll: false });
  }, [router, pathname]);

  useEffect(() => {
    setSearchInput(search);
  }, [search]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (searchInput !== search) updateParam("search", searchInput);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput, search, updateParam]);

  useEffect(() => {
    fetcher<Category[]>(apiUrl("/categories"))
      .then((data) => {
        setCategories(data);
        setCategoryError(false);
      })
      .catch(() => {
        setCategories([]);
        setCategoryError(true);
      });
  }, []);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      setLoading(true);
      setError(null);
      const params = new URLSearchParams();
      if (selectedCategory) params.set("category", selectedCategory);
      if (search) params.set("search", search);
      if (inStock) params.set("in_stock", "true");
      if (ordering) params.set("ordering", ordering);
      const qs = params.toString();

      try {
        const data = await fetcher<Product[]>(apiUrl(`/products${qs ? `?${qs}` : ""}`));
        if (!cancelled) setProducts(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Error al cargar el catálogo.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedCategory, search, inStock, ordering]);

  const hasActiveFilters = Boolean(selectedCategory || search || inStock || ordering);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <section className="relative overflow-hidden border-b border-bd-border">
        <div className="topo-bg pointer-events-none absolute inset-0 opacity-75" />
        <div className="relative mx-auto max-w-7xl px-6 py-14 lg:px-8 lg:py-20">
          <span className="section-label">Catálogo</span>
          <h1 className="mt-3 max-w-3xl font-display text-5xl font-black italic uppercase leading-[0.88] tracking-[-0.05em] text-foreground sm:text-7xl">
            Productos para elegir con claridad.
          </h1>
          <p className="mt-5 max-w-xl text-sm leading-7 text-muted sm:text-base">
            Filtra, compara disponibilidad y entra al detalle de cada producto antes de decidir.
          </p>
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-6 py-10 lg:px-8 lg:py-12">
        <div className="mb-10 space-y-4 border-b border-bd-border pb-8">
          <div className="flex flex-wrap gap-2" aria-label="Filtrar por categoría">
            <button
              type="button"
              onClick={() => updateParam("category", "")}
              aria-pressed={!selectedCategory}
              className={`rounded-full border px-4 py-2 text-xs font-bold transition ${
                !selectedCategory
                  ? "border-primary bg-primary text-background"
                  : "border-bd-border bg-surface text-muted hover:border-foreground/25 hover:text-foreground"
              }`}
            >
              Todos
            </button>
            {categories.map((cat) => (
              <button
                key={cat.id}
                type="button"
                onClick={() => updateParam("category", selectedCategory === cat.slug ? "" : cat.slug)}
                aria-pressed={selectedCategory === cat.slug}
                className={`rounded-full border px-4 py-2 text-xs font-bold transition ${
                  selectedCategory === cat.slug
                    ? "border-primary bg-primary text-background"
                    : "border-bd-border bg-surface text-muted hover:border-foreground/25 hover:text-foreground"
                }`}
              >
                {cat.name}
              </button>
            ))}
          </div>

          {categoryError ? (
            <p className="text-xs text-amber-200/80">
              No se pudieron cargar las categorías. Aún puedes buscar y ordenar productos.
            </p>
          ) : null}

          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
            <label className="relative block">
              <span className="sr-only">Buscar productos</span>
              <svg className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="search"
                placeholder="Buscar por nombre o descripción"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="w-full rounded-xl border border-bd-border bg-surface py-3 pl-10 pr-4 text-sm text-foreground placeholder:text-muted/70 focus:border-foreground/25 focus:outline-none"
              />
            </label>

            <button
              type="button"
              onClick={() => updateParam("in_stock", inStock ? "" : "true")}
              aria-pressed={inStock}
              className={`rounded-xl border px-4 py-3 text-xs font-bold uppercase tracking-[0.06em] transition ${
                inStock
                  ? "border-primary bg-primary text-background"
                  : "border-bd-border bg-surface text-muted hover:border-foreground/25 hover:text-foreground"
              }`}
            >
              Solo en stock
            </button>

            <label>
              <span className="sr-only">Ordenar catálogo</span>
              <select
                value={ordering}
                onChange={(e) => updateParam("ordering", e.target.value)}
                className="h-full min-h-12 rounded-xl border border-bd-border bg-surface px-4 text-xs font-bold text-muted focus:border-foreground/25 focus:outline-none"
              >
                {ORDERING_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="flex min-h-6 items-center justify-between gap-4">
            {!loading && !error ? (
              <p className="text-xs text-muted" aria-live="polite">
                {products.length} {products.length === 1 ? "producto" : "productos"}
                {hasActiveFilters ? " encontrados" : " en catálogo"}
              </p>
            ) : <span />}

            {hasActiveFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="text-xs font-bold text-muted transition hover:text-foreground"
              >
                Limpiar filtros
              </button>
            ) : null}
          </div>
        </div>

        {error ? (
          <div className="rounded-2xl border border-red-500/25 bg-red-500/10 p-6 text-sm text-red-200" role="alert">
            No pudimos cargar el catálogo. {error}
          </div>
        ) : loading ? (
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3" aria-label="Cargando productos">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="aspect-[4/3] animate-pulse rounded-2xl bg-surface" />
            ))}
          </div>
        ) : products.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-bd-border px-6 py-16 text-center">
            <p className="font-display text-2xl font-extrabold uppercase text-foreground">Sin resultados</p>
            <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">
              {search
                ? `No encontramos productos para “${search}”.`
                : "No hay productos disponibles con estos filtros."}
            </p>
            {hasActiveFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="mt-6 rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25"
              >
                Limpiar filtros
              </button>
            ) : null}
          </div>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            {products.map((product) => (
              <ProductCard key={product.id} {...product} />
            ))}
          </div>
        )}

        <div className="mt-16 border-t border-bd-border pt-10">
          <div className="grid gap-5 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <p className="font-display text-2xl font-extrabold uppercase text-foreground sm:text-3xl">
                ¿No encuentras lo que buscas?
              </p>
              <p className="mt-2 text-sm text-muted">Consulta disponibilidad o alternativas directamente con la tienda.</p>
            </div>
            {whatsappLink ? (
              <a
                href={whatsappLink}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-6 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
              >
                Consultar por WhatsApp
              </a>
            ) : null}
          </div>
        </div>
      </main>
    </div>
  );
}

export default function CatalogPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-background">
          <div className="mx-auto max-w-7xl px-6 py-12 lg:px-8">
            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <div key={i} className="aspect-[4/3] animate-pulse rounded-2xl bg-surface" />
              ))}
            </div>
          </div>
        </div>
      }
    >
      <CatalogContent />
    </Suspense>
  );
}
