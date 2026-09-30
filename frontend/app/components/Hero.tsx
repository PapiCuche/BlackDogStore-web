"use client";

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";

const JOURNEYS = [
  { label: "Compra", detail: "Productos y accesorios", href: "/product" },
  { label: "Repara", detail: "Servicio técnico", href: "/services" },
  { label: "Sigue", detail: "Tus pedidos", href: "/orders" },
];

export default function Hero() {
  const { company, branding, contact } = useStorefront();

  return (
    <section className="relative overflow-hidden bg-background text-foreground">
      <div className="topo-bg pointer-events-none absolute inset-0" aria-hidden="true" />
      <div className="absolute inset-x-0 top-0 h-px bg-accent/70" aria-hidden="true" />

      <div className="relative mx-auto max-w-7xl px-6 py-14 sm:py-20 lg:px-8 lg:py-24">
        <div className="grid gap-12 lg:grid-cols-12 lg:items-center lg:gap-10">
          <div className="lg:col-span-7">
            <div className="flex items-center gap-3">
              <span className="h-px w-10 bg-accent" aria-hidden="true" />
              <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                {[company.name, contact.city].filter(Boolean).join(" · ")}
              </p>
            </div>

            <h1 className="mt-7 max-w-4xl font-display text-5xl font-black uppercase leading-[0.92] tracking-[-0.045em] text-foreground sm:text-6xl lg:text-7xl xl:text-[5.4rem]">
              Compra, repara y vuelve a usar tu equipo.
            </h1>

            <p className="mt-7 max-w-2xl text-base leading-7 text-muted-foreground sm:text-lg sm:leading-8">
              Explora el catálogo o coordina una evaluación técnica. Precio,
              disponibilidad y condiciones visibles antes de decidir.
            </p>

            <div className="mt-9 flex flex-wrap items-center gap-3">
              <Link
                href="/product"
                className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-6 py-3 text-sm font-bold uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                Ver catálogo
              </Link>
              <Link
                href="/services"
                className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border bg-surface px-6 py-3 text-sm font-bold uppercase tracking-[0.12em] text-foreground transition hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                Servicio técnico
              </Link>
              {contact.whatsapp_link ? (
                <a
                  href={contact.whatsapp_link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-12 items-center px-2 text-sm font-semibold text-muted-foreground underline-offset-4 transition hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Consultar por WhatsApp
                </a>
              ) : null}
            </div>

            <div className="mt-12 grid border-y border-bd-border sm:grid-cols-3">
              {JOURNEYS.map((item, index) => (
                <Link
                  key={item.label}
                  href={item.href}
                  className={`group py-5 sm:px-5 sm:first:pl-0 ${
                    index < JOURNEYS.length - 1 ? "border-b border-bd-border sm:border-b-0 sm:border-r" : ""
                  }`}
                >
                  <span className="block text-[10px] font-bold uppercase tracking-[0.22em] text-accent">
                    0{index + 1} · {item.label}
                  </span>
                  <span className="mt-1 flex items-center gap-2 text-sm font-medium text-muted-foreground transition group-hover:text-foreground">
                    {item.detail}
                    <span aria-hidden="true" className="transition-transform group-hover:translate-x-1">→</span>
                  </span>
                </Link>
              ))}
            </div>
          </div>

          <div className="lg:col-span-5">
            <div className="relative min-h-[360px] overflow-hidden rounded-2xl border border-bd-border bg-surface sm:min-h-[430px]">
              <div className="topo-bg pointer-events-none absolute inset-0 opacity-70" aria-hidden="true" />
              <div className="absolute left-6 top-6 z-20 text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                Catálogo + servicio
              </div>
              <div className="absolute right-0 top-0 h-24 w-px bg-accent" aria-hidden="true" />
              <div className="absolute right-0 top-0 h-px w-24 bg-accent" aria-hidden="true" />

              <div className="absolute inset-0 flex items-center justify-center p-10 sm:p-14">
                {branding.logo_url ? (
                  <img
                    src={branding.logo_url}
                    alt={company.name}
                    className="max-h-64 w-full object-contain opacity-95 sm:max-h-72"
                  />
                ) : (
                  <span className="max-w-sm text-center font-display text-4xl font-black uppercase tracking-tight text-foreground">
                    {company.name}
                  </span>
                )}
              </div>

              <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between border-t border-bd-border bg-background/80 px-6 py-4 backdrop-blur">
                <span className="text-xs text-muted-foreground">
                  {contact.city || "Atención técnica y comercial"}
                </span>
                <span className="h-2 w-2 bg-accent" aria-hidden="true" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
