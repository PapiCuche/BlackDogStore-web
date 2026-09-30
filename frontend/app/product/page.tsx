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
  // Phase 3: the tenant's own WhatsApp, not a compiled-in number.
  const whatsappLink = useStorefront().contact.whatsapp_link;
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [categoriesError, setCategoriesError] = useState(false);
  const [searchInput, setSearchInput] = useState("");

  const selectedCategory = searchParams.get("category") ?? "";
  const search = searchParams.get("search") ?? "";
  const inStock = searchParams.get("in_stock") === "true";
  const ordering = searchParams.get("ordering") ?? "";

  const updateParam = useCallback(
    (key: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
      const nextQuery = params.toString();
      router.push(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  const clearFilters = useCallback(() => {
    router.push(pathname, { scroll: false });
  }, [router, pathname]);

  useEffect(() => {
    fetcher<Category[]>(apiUrl("/categories"))
      .then((next) => {
        setCategories(next);
        setCategoriesError(false);
      })
      .catch(() => {
        setCategories([]);
        setCategoriesError(true);
      });
  }, []);

  useEffect(() => {
    setSearchInput(search);
  }, [search]);

  useEffect(() => {
    if (searchInput === search) return;
    const timer = window.setTimeout(() => updateParam("search", searchInput), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput, search, updateParam]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    if (selectedCategory) params.set("category", selectedCategory);
    if (search) params.set("search", search);
    if (inStock) params.set("in_stock", "true");
    if (ordering) params.set("ordering", ordering);
    const qs = params.toString();
    fetcher<Product[]>(apiUrl(`/products${qs ? `?${qs}` : ""}`))
      .then(setProducts)
      .catch((err) => setError(err instanceof Error ? err.message : "Error al cargar el catálogo."))
      .finally(() => setLoading(false));
  }, [selectedCategory, search, inStock, ordering]);

  const hasActiveFilters = Boolean(selectedCategory || search || inStock || ordering);

  return (
    <div className="min-h-screen bg-background text-foreground">

      {/* Page header */}
      <section className="relative overflow-hidden border-b border-bd-border">
        <div className="topo-bg pointer-events-none absolute inset-0" />
        <div className="absolute left-0 top-0 h-px w-28 bg-accent" aria-hidden="true" />
        <div className="relative mx-auto max-w-7xl px-6 py-14 lg:px-8 lg:py-18">
          <span className="section-label">Catálogo</span>
          <h1 className="font-display mt-3 max-w-3xl text-5xl font-black uppercase leading-[0.92] tracking-tight text-foreground sm:text-6xl">
            Encuentra lo que necesitas.
          </h1>
          <p className="mt-4 max-w-xl text-base leading-7 text-muted-foreground">
            Filtra por categoría, disponibilidad o precio. Cada producto muestra su estado antes de abrir el detalle.
          </p>
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-6 py-12 lg:px-8">

        {/* Filters bar */}
        <div className="mb-10 flex flex-col gap-4">

          {/* Category chips */}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => updateParam("category", "")}
              className={`rounded-full px-4 py-2 text-xs font-bold uppercase tracking-widest transition ${
                !selectedCategory
                  ? "bg-primary text-background"
                  : "border border-bd-border bg-surface text-muted-foreground hover:border-accent/60 hover:text-foreground"
              }`}
            >
              Todos
            </button>
            {categories.map((cat) => (
              <button
                key={cat.id}
                onClick={() => updateParam("category", selectedCategory === cat.slug ? "" : cat.slug)}
                className={`rounded-full px-4 py-2 text-xs font-bold uppercase tracking-widest transition ${
                  selectedCategory === cat.slug
                    ? "bg-primary text-background"
                    : "border border-bd-border bg-surface text-muted-foreground hover:border-accent/60 hover:text-foreground"
                }`}
              >
                {cat.name}
              </button>
            ))}
          </div>

          {/* Search + stock + ordering row */}
          <div className="flex flex-wrap items-center gap-3">

            {/* Search */}
            <div className="relative flex-1 min-w-48">
              <svg className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="search"
                placeholder="Buscar productos..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="w-full rounded-xl border border-bd-border bg-surface py-2.5 pl-10 pr-4 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20"
              />
            </div>

            {/* In-stock toggle */}
            <button
              onClick={() => updateParam("in_stock", inStock ? "" : "true")}
              className={`rounded-full px-4 py-2.5 text-xs font-bold uppercase tracking-widest transition ${
                inStock
                  ? "bg-primary text-background"
                  : "border border-bd-border bg-surface text-muted-foreground hover:border-accent/60 hover:text-foreground"
              }`}
            >
              Solo en stock
            </button>

            {/* Ordering */}
            <select
              value={ordering}
              onChange={(e) => updateParam("ordering", e.target.value)}
              className="rounded-xl border border-bd-border bg-surface py-2.5 pl-4 pr-8 text-xs font-bold uppercase tracking-widest text-muted-foreground focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20"
            >
              {ORDERING_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>

            {/* Clear filters */}
            {hasActiveFilters && (
              <button
                onClick={clearFilters}
                className="text-xs font-bold uppercase tracking-widest text-muted-foreground transition hover:text-foreground"
              >
                × Limpiar
              </button>
            )}
          </div>
        </div>

        {categoriesError ? (
          <p className="mb-6 text-xs text-amber-300">
            No pudimos cargar las categorías. El catálogo sigue disponible.
          </p>
        ) : null}

        {/* Count */}
        {!loading && !error && (
          <p className="mb-8 text-xs uppercase tracking-widest text-muted-foreground">
            {products.length} {products.length === 1 ? "producto" : "productos"}
            {hasActiveFilters ? " encontrados" : " en catálogo"}
          </p>
        )}

        {/* Grid */}
        {error ? (
          <div className="rounded-2xl border border-red-500/20 bg-red-500/10 p-6 text-sm text-red-300">
            Error al cargar: {error}
          </div>
        ) : loading ? (
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="h-72 animate-pulse rounded-xl bg-surface" />
            ))}
          </div>
        ) : products.length === 0 ? (
          <div className="flex flex-col items-center gap-6 rounded-2xl border border-dashed border-bd-border p-10 text-center sm:p-16">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-bd-border bg-surface text-muted-foreground" aria-hidden="true">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-5.2-5.2m2.2-5.3a7.5 7.5 0 11-15 0 7.5 7.5 0 0115 0z" />
              </svg>
            </div>
            <div>
              <p className="font-display text-2xl font-black uppercase text-muted-foreground">Sin resultados</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {search
                  ? `No hay productos para "${search}"`
                  : "No hay productos en esta categoría."}
              </p>
            </div>
            {hasActiveFilters ? (
              <button
                type="button"
                onClick={clearFilters}
                className="rounded-xl border border-bd-border bg-surface px-5 py-2.5 text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground transition hover:border-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
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

        {whatsappLink ? (
          <div className="mt-16 overflow-hidden rounded-2xl border border-bd-border bg-surface">
            <div className="flex flex-col items-center gap-4 px-8 py-10 text-center sm:px-12">
              <p className="font-display text-3xl font-black uppercase text-foreground sm:text-4xl">
                ¿No encuentras lo que buscas?
              </p>
              <p className="text-sm text-muted-foreground">
                Consulta disponibilidad directamente con la tienda.
              </p>
              <a
                href={whatsappLink}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-flex min-h-12 items-center rounded-xl bg-primary px-6 py-3 text-xs font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Consultar por WhatsApp
              </a>
            </div>
          </div>
        ) : null}

      </main>
    </div>
  );
}

export default function CatalogPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-7xl px-6 py-12 lg:px-8">
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="h-72 animate-pulse rounded-xl bg-surface" />
            ))}
          </div>
        </div>
      </div>
    }>
      <CatalogContent />
    </Suspense>
  );
}
