"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useStorefront } from "./components/StorefrontProvider";
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
  category?: Category;
};

const REPAIR_SERVICES = [
  {
    title: "Cambio de Pantalla",
    desc: "Evaluación, reemplazo de pantalla y pruebas funcionales antes de la entrega.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Batería",
    desc: "Revisión del estado de batería, reemplazo cuando corresponde y pruebas posteriores.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h2a2 2 0 002-2V8a2 2 0 00-2-2h-2M3 8h14v12H3V8zM9 4h6v4H9V4z" />
      </svg>
    ),
  },
  {
    title: "Tapa Trasera",
    desc: "Evaluación de carcasa y tapa trasera con intervención según el estado del equipo.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4h16v16H4zM9 9h6v6H9z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Glass",
    desc: "Revisión del cristal y alternativas de intervención según el modelo y el daño.",
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
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoriesLoading, setCategoriesLoading] = useState(true);
  const [categoryError, setCategoryError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then(setProducts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));

    fetcher<Category[]>(apiUrl("/categories"))
      .then((data) => {
        setCategories(data);
        setCategoryError(false);
      })
      .catch(() => {
        setCategories([]);
        setCategoryError(true);
      })
      .finally(() => setCategoriesLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Hero />

      <main className="mx-auto max-w-7xl px-6 lg:px-8">

        {/* Catalog entry point */}
        <section className="py-16 sm:py-20">
          <div className="grid gap-8 border-b border-bd-border pb-8 lg:grid-cols-[0.8fr_1.2fr] lg:items-end">
            <div>
              <span className="section-label">Catálogo</span>
              <h2 className="mt-3 max-w-xl font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground sm:text-6xl">
                Encuentra tu próximo equipo.
              </h2>
            </div>
            <div className="max-w-xl lg:justify-self-end">
              <p className="text-sm leading-6 text-muted sm:text-base sm:leading-7">
                Navega por categorías, revisa disponibilidad y entra al detalle antes de decidir.
              </p>
              <Link href="/product" className="mt-4 inline-flex text-sm font-bold text-foreground transition hover:text-muted">
                Ver catálogo completo →
              </Link>
            </div>
          </div>

          {categoriesLoading ? (
            <div className="grid sm:grid-cols-2 lg:grid-cols-3" aria-label="Cargando categorías">
              {[1, 2, 3, 4, 5, 6].map((item) => (
                <div key={item} className="min-h-28 animate-pulse border-b border-bd-border bg-foreground/[0.02] sm:px-5 lg:min-h-36 lg:border-r lg:px-6" />
              ))}
            </div>
          ) : categories.length > 0 ? (
            <div className="grid sm:grid-cols-2 lg:grid-cols-3">
              {categories.slice(0, 6).map((category, index) => (
                <Link
                  key={category.id}
                  href={`/product?category=${category.slug}`}
                  className="group flex min-h-28 items-end justify-between gap-4 border-b border-bd-border py-5 transition hover:bg-surface sm:px-5 lg:min-h-36 lg:border-r lg:px-6"
                >
                  <span>
                    <span className="block text-[10px] font-bold tracking-[0.18em] text-muted">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className="mt-2 block font-display text-xl font-extrabold uppercase tracking-[-0.02em] text-foreground sm:text-2xl">
                      {category.name}
                    </span>
                  </span>
                  <span className="text-xl text-muted transition-transform group-hover:translate-x-1 group-hover:text-foreground" aria-hidden="true">→</span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="border-b border-bd-border py-8 text-sm text-muted">
              {categoryError
                ? "No se pudieron cargar las categorías. El catálogo completo sigue disponible."
                : "Todavía no hay categorías publicadas."}
            </div>
          )}
        </section>

        {/* Services section */}
        <section className="py-16 sm:py-20">
          <div className="flex flex-col gap-5 border-b border-bd-border pb-8 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Servicio técnico</span>
              <h2 className="mt-3 font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground sm:text-6xl">
                Tu equipo.<br />Nuestro proceso.
              </h2>
            </div>
            <Link href="/services" className="text-sm font-bold text-foreground transition hover:text-muted">
              Ver todos los servicios →
            </Link>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4">
            {REPAIR_SERVICES.map((service, index) => (
              <Link
                key={service.title}
                href="/services"
                className="group border-b border-bd-border py-7 transition hover:bg-surface sm:px-5 lg:border-r lg:px-6"
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold tracking-[0.18em] text-muted">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="text-muted transition group-hover:text-foreground">{service.icon}</span>
                </div>
                <h3 className="mt-8 font-display text-lg font-extrabold uppercase tracking-[-0.02em] text-foreground">
                  {service.title}
                </h3>
                <p className="mt-3 text-sm leading-6 text-muted">{service.desc}</p>
                <span className="mt-6 inline-flex text-xs font-bold uppercase tracking-[0.08em] text-foreground">
                  Ver servicio →
                </span>
              </Link>
            ))}
          </div>
        </section>

        {/* Service process */}
        <section className="relative overflow-hidden rounded-[1.75rem] border border-bd-border bg-surface">
          <div className="topo-bg pointer-events-none absolute inset-0 opacity-60" />
          <div className="relative grid gap-10 px-7 py-10 sm:px-10 sm:py-12 lg:grid-cols-[0.8fr_1.2fr] lg:px-12 lg:py-14">
            <div>
              <span className="section-label">Cómo trabajamos</span>
              <h2 className="mt-3 font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground sm:text-5xl">
                Un proceso<br />que puedes seguir.
              </h2>
              <p className="mt-5 max-w-md text-sm leading-7 text-muted">
                Primero revisamos el equipo y explicamos el siguiente paso. La intervención y sus condiciones deben quedar claras antes de continuar.
              </p>
              <Link
                href="/services"
                className="mt-7 inline-flex min-h-11 items-center rounded-xl border border-bd-border px-5 py-2.5 text-xs font-bold uppercase tracking-[0.08em] text-foreground transition hover:border-foreground/25 hover:bg-surface-2"
              >
                Conocer el servicio
              </Link>
            </div>

            <ol className="grid sm:grid-cols-2">
              {[
                { num: "01", title: "Recepción", text: "Registramos el equipo y el motivo de la visita." },
                { num: "02", title: "Evaluación", text: "Revisamos el caso y definimos qué necesita atención." },
                { num: "03", title: "Intervención", text: "El trabajo avanza con el alcance acordado para el equipo." },
                { num: "04", title: "Entrega", text: "Revisamos el resultado y comunicamos el cierre del servicio." },
              ].map((step) => (
                <li key={step.num} className="border-b border-bd-border p-5 sm:border-l sm:p-6">
                  <span className="text-[10px] font-bold tracking-[0.18em] text-muted">{step.num}</span>
                  <h3 className="mt-5 font-display text-lg font-extrabold uppercase text-foreground">{step.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-muted">{step.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Products section */}
        <section className="py-16 sm:py-20">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Selección</span>
              <h2 className="mt-3 font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground sm:text-6xl">
                Recién llegados.
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold text-foreground transition hover:text-muted">
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
                <div key={i} className="h-72 animate-pulse rounded-2xl bg-foreground/[0.04]" />
              ))}
            </div>
          ) : products.length === 0 ? (
            <div className="mt-8 rounded-2xl border border-dashed border-bd-border px-6 py-14 text-center">
              <p className="font-display text-2xl font-extrabold uppercase text-foreground">
                Catálogo en preparación
              </p>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">
                Todavía no hay productos publicados para mostrar en esta sección.
              </p>
              {whatsappLink ? (
                <a
                  href={whatsappLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.08em] text-foreground transition hover:border-foreground/25"
                >
                  Consultar disponibilidad
                </a>
              ) : null}
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
