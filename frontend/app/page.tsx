"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useStoreName, useStorefront } from "./components/StorefrontProvider";
import { ProductCard } from "./components/ProductCard";
import Hero from "./components/Hero";
import { fetcher, apiUrl } from "./lib/api";

type Category = { id: number; name: string; slug: string };

type Product = {
  id: number;
  slug: string;
  name: string;
  description?: string;
  price: number;
  inventory?: number;
  image_url?: string;
  average_rating?: number | null;
  review_count?: number;
  category?: { id: number; name: string; slug: string };
};



const REPAIR_SERVICES = [
  {
    title: "Cambio de Pantalla",
    desc: "Evaluamos el módulo y te mostramos las opciones disponibles según el modelo y la condición del equipo.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Batería",
    desc: "Revisamos el estado de la batería y la alternativa de reemplazo compatible antes de intervenir el equipo.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h2a2 2 0 002-2V8a2 2 0 00-2-2h-2M3 8h14v12H3V8zM9 4h6v4H9V4z" />
      </svg>
    ),
  },
  {
    title: "Tapa Trasera",
    desc: "Evaluamos el daño de la tapa y la alternativa de reparación adecuada para el modelo y acabado.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4h16v16H4zM9 9h6v6H9z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Glass",
    desc: "Revisamos el vidrio y el módulo para definir la intervención adecuada antes de realizar el servicio.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
];


export default function Home() {
  // Phase 3: the tenant's own WhatsApp, not a compiled-in number.
  const { whatsapp_link: whatsappLink } = useStorefront().contact;
  const storeName = useStoreName();
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then(setProducts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetcher<Category[]>(apiUrl("/categories"))
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Hero />

      <main className="mx-auto max-w-7xl px-6 lg:px-8">

        {/* Category sections grid */}
        {categories.length > 0 ? (\n        <section className="border-b border-bd-border py-14 sm:py-16">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Catálogo</span>
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-foreground sm:text-6xl">
                Categorías
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold uppercase tracking-widest text-muted-foreground transition hover:text-foreground">
              Ver todos →
            </Link>
          </div>
          <div className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {categories.slice(0, 6).map((section, index) => (
              <Link
                key={section.slug}
                href={`/product?category=${section.slug}`}
                className="group flex min-h-32 flex-col justify-between rounded-xl border border-bd-border bg-surface p-5 text-left transition hover:-translate-y-0.5 hover:border-accent/60 hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-accent">
                  0{index + 1}
                </span>
                <span className="flex items-end justify-between gap-3">
                  <span className="font-display text-sm font-black uppercase tracking-wide text-muted-foreground transition group-hover:text-foreground">
                    {section.label}
                  </span>
                  <span className="text-muted-foreground transition group-hover:translate-x-1 group-hover:text-foreground" aria-hidden="true">
                    →
                  </span>
                </span>
              </Link>
            ))}
          </div>
        </section>

        {/* Services section */}
        <section className="py-20">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Reparaciones</span>
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-foreground sm:text-6xl">
                Servicio<br />Técnico
              </h2>
            </div>
            <a href="/services" className="text-sm font-bold uppercase tracking-widest text-muted-foreground transition hover:text-foreground">
              Ver todos →
            </a>
          </div>

          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {REPAIR_SERVICES.map((s) => (
              <a
                key={s.title}
                href="/services"
                className="group relative overflow-hidden rounded-xl border border-bd-border bg-surface p-6 transition hover:border-accent/60 hover:bg-surface-elevated"
              >
                {/* Badge */}
                {s.badge && (
                  <div className="mb-4 inline-flex rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-zinc-400">
                    {s.badge}
                  </div>
                )}
                {!s.badge && <div className="mb-4 h-5" />}

                {/* Icon */}
                <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg border border-bd-border bg-background text-foreground">
                  {s.icon}
                </div>

                <h3 className="font-display text-xl font-black uppercase text-foreground">{s.title}</h3>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{s.desc}</p>

                <div className="mt-5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-muted-foreground transition group-hover:text-foreground">
                  Consultar precio
                  <span className="transition group-hover:translate-x-1">→</span>
                </div>
              </a>
            ))}
          </div>
        </section>

        {/* Service process */}
        <section className="relative overflow-hidden rounded-2xl border border-bd-border bg-surface">
          <div className="topo-bg pointer-events-none absolute inset-0 opacity-60" />
          <div className="relative grid gap-10 px-8 py-12 sm:px-12 sm:py-14 lg:grid-cols-2 lg:items-center">
            <div>
              <span className="section-label">Servicio técnico</span>
              <h2 className="font-display mt-2 text-4xl font-black uppercase leading-none tracking-tight text-foreground sm:text-5xl">
                ¿Tu equipo necesita revisión?
              </h2>
              <p className="mt-5 max-w-lg text-base leading-7 text-muted-foreground">
                En {storeName || "la tienda"} revisamos el equipo antes de definir la intervención.
                Consulta disponibilidad, condiciones y precio antes de autorizar el servicio.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                {whatsappLink ? (
                  <a
                    href={whatsappLink}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-12 items-center rounded-xl bg-primary px-6 py-3 text-sm font-bold uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    Consultar por WhatsApp
                  </a>
                ) : null}
                <Link
                  href="/services"
                  className="inline-flex min-h-12 items-center rounded-xl border border-bd-border bg-background px-6 py-3 text-sm font-bold uppercase tracking-[0.12em] text-foreground transition hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Ver servicios
                </Link>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-bd-border bg-bd-border">
              {[
                { label: "Evaluación", sub: "Revisamos el problema", num: "01" },
                { label: "Opciones", sub: "Te mostramos alternativas", num: "02" },
                { label: "Confirmación", sub: "Decides antes de intervenir", num: "03" },
                { label: "Seguimiento", sub: "Consulta el avance", num: "04" },
              ].map((card) => (
                <div key={card.num} className="bg-background p-5 sm:p-6">
                  <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-accent">{card.num}</p>
                  <p className="mt-5 font-display text-sm font-black uppercase leading-tight text-foreground">
                    {card.label}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">{card.sub}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Products section */}
        <section className="py-20">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Catálogo</span>
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-foreground sm:text-6xl">
                Productos<br />Destacados
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold uppercase tracking-widest text-zinc-400 transition hover:text-white">
              Ver catálogo completo →
            </Link>
          </div>

          {error ? (
            <div className="mt-8 rounded-2xl border border-red-500/20 bg-red-500/10 p-6 text-sm text-red-300">
              Error al cargar productos: {error}
            </div>
          ) : loading ? (
            <div className="mt-10 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-72 animate-pulse rounded-2xl bg-white/[0.04]" />
              ))}
            </div>
          ) : products.length === 0 ? (
            <div className="mt-8 flex flex-col items-center gap-6 rounded-3xl border border-dashed border-white/10 p-16 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-bd-border bg-surface text-muted-foreground" aria-hidden="true">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20 7l-8-4-8 4m16 0-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                </svg>
              </div>
              <div>
                <p className="font-display text-2xl font-black uppercase text-zinc-600">
                  Catálogo en preparación
                </p>
                <p className="mt-1 text-sm text-zinc-700">Escríbenos por WhatsApp para consultar disponibilidad.</p>
              </div>
              <a
                href={whatsappLink || "#"}
                className="rounded-full bg-white px-6 py-3 text-xs font-black uppercase tracking-widest text-[#080808] transition hover:bg-zinc-200"
              >
                Consultar stock
              </a>
            </div>
          ) : (
            <div className="mt-10 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {products.slice(0, 6).map((product) => (
                <ProductCard key={product.id} {...product} />
              ))}
            </div>
          )}
        </section>

      </main>
    </div>
  );
}
