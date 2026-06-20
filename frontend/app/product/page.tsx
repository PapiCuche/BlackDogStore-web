"use client";

import { useEffect, useState } from "react";
import { ProductCard } from "../components/ProductCard";
import { API_BASE, fetcher } from "../lib/api";

type Category = { id: number; name: string; slug: string };
type Product = {
  id: number;
  slug: string;
  name: string;
  description?: string;
  price: number;
  category?: Category;
  image_url?: string;
  average_rating?: number | null;
  review_count?: number;
};

export default function CatalogPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetcher<Product[]>(`${API_BASE}/products/`),
      fetcher<Category[]>(`${API_BASE}/categories/`),
    ])
      .then(([prods, cats]) => { setProducts(prods); setCategories(cats); })
      .catch((err) => setError(err instanceof Error ? err.message : "Error al cargar el catálogo."))
      .finally(() => setLoading(false));
  }, []);

  const filtered = products.filter((p) => {
    const matchCat = selectedCategory === null || p.category?.id === selectedCategory;
    const matchQ = search === "" || p.name.toLowerCase().includes(search.toLowerCase());
    return matchCat && matchQ;
  });

  return (
    <div className="min-h-screen bg-[#080808] text-white">

      {/* Header */}
      <section className="relative overflow-hidden border-b border-white/[0.06]">
        <div className="topo-bg absolute inset-0 pointer-events-none" />
        <div className="dot-grid absolute right-0 top-0 h-64 w-64 opacity-30 pointer-events-none" />
        <div className="relative mx-auto max-w-7xl px-6 py-16 lg:px-8 lg:py-20">
          <span className="section-label">Tienda</span>
          <h1 className="font-display mt-3 text-6xl font-black uppercase leading-[0.9] tracking-tight text-white sm:text-7xl">
            Catálogo<br />de Productos
          </h1>
          <p className="mt-4 max-w-lg text-base text-zinc-500">
            Equipos Apple originales, accesorios y repuestos con garantía. Envío a todo el Perú.
          </p>
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-6 py-12 lg:px-8">

        {/* Filters */}
        <div className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setSelectedCategory(null)}
              className={`rounded-full px-4 py-2 text-xs font-bold uppercase tracking-widest transition ${
                selectedCategory === null
                  ? "bg-white text-[#080808]"
                  : "border border-white/10 bg-white/[0.04] text-zinc-400 hover:border-white/25 hover:text-white"
              }`}
            >
              Todos
            </button>
            {categories.map((cat) => (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`rounded-full px-4 py-2 text-xs font-bold uppercase tracking-widest transition ${
                  selectedCategory === cat.id
                    ? "bg-white text-[#080808]"
                    : "border border-white/10 bg-white/[0.04] text-zinc-400 hover:border-white/25 hover:text-white"
                }`}
              >
                {cat.name}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="relative">
            <svg className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="search"
              placeholder="Buscar productos..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-full border border-white/10 bg-white/[0.04] py-2.5 pl-10 pr-4 text-sm text-white placeholder-zinc-600 focus:border-white/25 focus:outline-none sm:w-64"
            />
          </div>
        </div>

        {/* Count */}
        {!loading && !error && (
          <p className="mb-8 text-xs uppercase tracking-widest text-zinc-600">
            {filtered.length} {filtered.length === 1 ? "producto" : "productos"}
            {selectedCategory !== null || search ? " encontrados" : " en catálogo"}
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
              <div key={i} className="h-72 animate-pulse rounded-2xl bg-white/[0.04]" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center gap-6 rounded-3xl border border-dashed border-white/10 p-16 text-center">
            <img src="/assets/branding/logo-icon.png" alt="" className="h-14 w-14 opacity-[0.06] invert" />
            <div>
              <p className="font-display text-2xl font-black uppercase text-zinc-700">Sin resultados</p>
              <p className="mt-1 text-sm text-zinc-700">
                {search ? `No hay productos para "${search}"` : "No hay productos en esta categoría."}
              </p>
            </div>
            {(search || selectedCategory !== null) && (
              <button
                onClick={() => { setSearch(""); setSelectedCategory(null); }}
                className="rounded-full border border-white/10 px-5 py-2 text-xs font-bold uppercase tracking-widest text-zinc-500 transition hover:border-white/25 hover:text-white"
              >
                Limpiar filtros
              </button>
            )}
          </div>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((product) => (
              <ProductCard key={product.id} {...product} />
            ))}
          </div>
        )}

        {/* CTA */}
        <div className="mt-16 overflow-hidden rounded-3xl border border-white/[0.08] bg-[#111]">
          <div className="flex flex-col items-center gap-4 px-8 py-12 text-center sm:px-12">
            <p className="font-display text-3xl font-black uppercase text-white sm:text-4xl">
              ¿No encuentras lo que buscas?
            </p>
            <p className="text-sm text-zinc-500">Escríbenos y te ayudamos a conseguirlo.</p>
            <a
              href="https://wa.me/51936449536"
              className="mt-2 inline-flex items-center gap-2.5 rounded-full bg-white px-7 py-3.5 text-xs font-black uppercase tracking-widest text-[#080808] transition hover:bg-zinc-200"
            >
              <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
              </svg>
              Consultar por WhatsApp
            </a>
          </div>
        </div>

      </main>
    </div>
  );
}
