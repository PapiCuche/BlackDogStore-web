"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useStoreName, useStorefront } from "./components/StorefrontProvider";
import { ProductCard } from "./components/ProductCard";
import Hero from "./components/Hero";
import { fetcher, apiUrl } from "./lib/api";

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

const CATALOG_SECTIONS = [
  { label: "iPhone", slug: "iphone", icon: "📱" },
  { label: "Apple Watch", slug: "apple-watch", icon: "⌚" },
  { label: "iPad", slug: "ipad", icon: "🖥" },
  { label: "Mac", slug: "mac", icon: "💻" },
  { label: "Accesorios", slug: "accesorios", icon: "🎧" },
  { label: "Audífonos", slug: "audifonos", icon: "🎵" },
];

const STATS = [
  { stat: "5,000+", label: "Dispositivos reparados" },
  { stat: "6 meses", label: "Garantía garantizada" },
  { stat: "100%", label: "Repuestos originales" },
  { stat: "0 soles", label: "Diagnóstico" },
];

const REPAIR_SERVICES = [
  {
    title: "Cambio de Pantalla",
    desc: "OLED/LCD con calibración de True Tone. No aparece mensaje de pieza reparada.",
    badge: "Más solicitado",
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Batería",
    desc: "Baterías originales Nasan certificadas. Recupera la autonomía de tu iPhone.",
    badge: "Cert. Nasan",
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h2a2 2 0 002-2V8a2 2 0 00-2-2h-2M3 8h14v12H3V8zM9 4h6v4H9V4z" />
      </svg>
    ),
  },
  {
    title: "Tapa Trasera",
    desc: "Tecnología láser para cambio preciso y seguro. Sin rastro de reparación.",
    badge: "Tecnología láser",
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 4h16v16H4zM9 9h6v6H9z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Glass",
    desc: "Protector de vidrio templado premium. Instalación sin burbujas ni polvo.",
    badge: null,
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
];

const BRANDS_STRIP = [
  "iPhone 17 Pro Max",
  "iPhone 16 Pro",
  "iPhone 16",
  "iPhone 15 Pro",
  "iPhone 15",
  "iPhone 14 Pro",
  "iPhone 14",
  "iPhone 13",
  "iPhone 12",
];

export default function Home() {
  // Phase 3: the tenant's own WhatsApp, not a compiled-in number.
  const { whatsapp_link: whatsappLink, phone: storePhone } =
    useStorefront().contact;
  // Phase 3: the shop's own name. The claims around it are still the pilot's
  // marketing copy — per-tenant landing content is a separate concern.
  const storeName = useStoreName();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then(setProducts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-[#080808] text-white">
      <Hero />

      {/* Stats bar */}
      <section className="border-y border-white/[0.06] bg-[#111]">
        <div className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-white/[0.06] lg:grid-cols-4">
          {STATS.map((item) => (
            <div key={item.label} className="px-8 py-8 text-center">
              <p className="font-display text-4xl font-black tracking-tight text-white lg:text-5xl">
                {item.stat}
              </p>
              <p className="mt-1.5 text-xs uppercase tracking-widest text-zinc-500">{item.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Scrolling marquee strip */}
      <div className="overflow-hidden border-b border-white/[0.06] bg-[#0d0d0d] py-3">
        <div className="flex animate-[marquee_25s_linear_infinite] gap-8 whitespace-nowrap">
          {[...BRANDS_STRIP, ...BRANDS_STRIP, ...BRANDS_STRIP].map((brand, i) => (
            <span key={i} className="flex items-center gap-8 text-[10px] font-bold uppercase tracking-[0.3em] text-zinc-700">
              {brand}
              <span className="h-1 w-1 rounded-full bg-zinc-700" />
            </span>
          ))}
        </div>
      </div>

      <main className="mx-auto max-w-7xl px-6 lg:px-8">

        {/* Category sections grid */}
        <section className="py-16">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Catálogo</span>
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-white sm:text-6xl">
                Categorías
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold uppercase tracking-widest text-zinc-400 transition hover:text-white">
              Ver todos →
            </Link>
          </div>
          <div className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {CATALOG_SECTIONS.map((section) => (
              <Link
                key={section.slug}
                href={`/product?category=${section.slug}`}
                className="group flex flex-col items-center gap-3 rounded-2xl border border-white/[0.08] bg-[#111] p-5 text-center transition hover:border-white/20 hover:bg-[#161616]"
              >
                <span className="text-2xl">{section.icon}</span>
                <span className="font-display text-xs font-black uppercase tracking-widest text-zinc-400 transition group-hover:text-white">
                  {section.label}
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
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-white sm:text-6xl">
                Servicio<br />Técnico Apple
              </h2>
            </div>
            <a href="/services" className="text-sm font-bold uppercase tracking-widest text-zinc-400 transition hover:text-white">
              Ver todos →
            </a>
          </div>

          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {REPAIR_SERVICES.map((s) => (
              <a
                key={s.title}
                href="/services"
                className="group relative overflow-hidden rounded-2xl border border-white/[0.08] bg-[#111] p-6 transition hover:border-white/20 hover:bg-[#161616]"
              >
                {/* Badge */}
                {s.badge && (
                  <div className="mb-4 inline-flex rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-zinc-400">
                    {s.badge}
                  </div>
                )}
                {!s.badge && <div className="mb-4 h-5" />}

                {/* Icon */}
                <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-white">
                  {s.icon}
                </div>

                <h3 className="font-display text-xl font-black uppercase text-white">{s.title}</h3>
                <p className="mt-2 text-sm leading-6 text-zinc-500">{s.desc}</p>

                <div className="mt-5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-zinc-500 transition group-hover:text-white">
                  Consultar precio
                  <span className="transition group-hover:translate-x-1">→</span>
                </div>
              </a>
            ))}
          </div>
        </section>

        {/* Guarantee strip */}
        <section className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#111]">
          <div className="topo-bg absolute inset-0 opacity-60 pointer-events-none" />
          <div className="dot-grid absolute right-0 top-0 h-64 w-64 opacity-30 pointer-events-none" />
          <div className="relative grid gap-8 px-8 py-12 sm:px-12 sm:py-16 lg:grid-cols-2 lg:items-center">
            <div>
              <span className="section-label">Confianza</span>
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-white sm:text-6xl">
                ¿Tu iPhone<br />No Funciona?
              </h2>
              <p className="mt-5 max-w-md text-base leading-7 text-zinc-400">
                En {storeName} ofrecemos servicio técnico especializado en productos Apple.
                Diagnóstico gratuito y reparaciones con garantía.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <a
                  href={whatsappLink || "#"}
                  className="inline-flex items-center gap-2.5 rounded-full bg-white px-7 py-3.5 text-sm font-black uppercase tracking-widest text-[#080808] transition hover:bg-zinc-200"
                >
                  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
                  </svg>
                  Diagnóstico Gratis
                </a>
                <a
                  href="/services"
                  className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.05] px-7 py-3.5 text-sm font-bold uppercase tracking-widest text-white transition hover:border-white/25 hover:bg-white/10"
                >
                  Ver servicios
                </a>
              </div>
            </div>

            {/* Right: guarantee cards */}
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: "Tecnología Láser", sub: "Para tapa trasera", num: "01" },
                { label: "Baterías Nasan", sub: "Certificadas", num: "02" },
                { label: "True Tone", sub: "No se pierde", num: "03" },
                { label: "Sin Pieza\nReparada", sub: "Mensaje no aparece", num: "04" },
              ].map((card) => (
                <div
                  key={card.num}
                  className="rounded-2xl border border-white/[0.08] bg-[#0d0d0d] p-5"
                >
                  <p className="font-display text-xs font-black text-zinc-700">{card.num}</p>
                  <p className="mt-2 font-display text-sm font-black uppercase leading-tight text-white whitespace-pre-line">
                    {card.label}
                  </p>
                  <p className="mt-1 text-[10px] text-zinc-600">{card.sub}</p>
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
              <h2 className="font-display mt-2 text-5xl font-black uppercase leading-none tracking-tight text-white sm:text-6xl">
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
              <img src="/assets/branding/logo-icon.png" alt="" className="h-16 w-16 opacity-10 invert" />
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

        {/* Pre-sale / preventa section */}
        <section className="mb-20 overflow-hidden rounded-3xl bg-white">
          <div className="relative grid gap-0 lg:grid-cols-2">
            {/* Left: text on white */}
            <div className="relative overflow-hidden bg-[#080808] px-8 py-12 sm:px-12">
              <div className="dot-grid absolute right-0 top-0 h-40 w-40 opacity-20 pointer-events-none" />
              <span className="section-label text-zinc-500">Preventa</span>
              <h2 className="font-display mt-3 text-5xl font-black uppercase leading-none tracking-tight text-white sm:text-6xl">
                iPhone 17<br />Pro Max
              </h2>
              <p className="mt-4 text-sm leading-6 text-zinc-400">
                Separa el tuyo con solo <strong className="text-white">$300</strong> de reserva.
                Contacta ahora para asegurar tu pedido.
              </p>
              <a
                href={whatsappLink ? `${whatsappLink}?text=${encodeURIComponent("Hola, quiero más información")}` : "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-6 inline-flex items-center gap-2.5 rounded-full bg-white px-7 py-3.5 text-sm font-black uppercase tracking-widest text-[#080808] transition hover:bg-zinc-200"
              >
                Reservar ahora
              </a>
            </div>

            {/* Right: white panel */}
            <div className="flex flex-col items-center justify-center bg-white px-8 py-12 text-center sm:px-12">
              <p className="font-display text-6xl font-black uppercase tracking-tight text-[#080808] sm:text-7xl">
                PREVENTA
              </p>
              <div className="mt-2 rounded-full bg-[#080808] px-6 py-2">
                <p className="font-display text-2xl font-black uppercase tracking-widest text-white">
                  EXCLUSIVA
                </p>
              </div>
              <p className="mt-6 text-xs uppercase tracking-[0.3em] text-zinc-400">
                Disponible · 256GB · 512GB · 1TB
              </p>
              {storePhone ? (
                <p className="mt-2 text-xs text-zinc-400">{storePhone}</p>
              ) : null}
            </div>
          </div>
        </section>

      </main>
    </div>
  );
}
