"use client";

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";

const SERVICE_SHORTCUTS = [
  { label: "Pantallas", href: "/services" },
  { label: "Baterías", href: "/services" },
  { label: "Diagnóstico", href: "/services" },
  { label: "Accesorios", href: "/product?category=accesorios" },
];

export default function Hero() {
  const { company, branding, contact } = useStorefront();
  const whatsappLink = contact.whatsapp_link;

  return (
    <section className="relative overflow-hidden border-b border-bd-border bg-background">
      <div className="topo-bg pointer-events-none absolute inset-0 opacity-80" />

      <div className="relative mx-auto max-w-7xl px-6 pb-10 pt-14 sm:pt-20 lg:px-8 lg:pb-14 lg:pt-20">
        <div className="grid gap-12 lg:grid-cols-[1.08fr_0.92fr] lg:items-end">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="h-px w-10 bg-primary/70" aria-hidden="true" />
              <p className="text-[10px] font-bold uppercase tracking-[0.26em] text-muted">
                {[company.name, contact.city].filter(Boolean).join(" · ")}
              </p>
            </div>

            <h1 className="mt-6 max-w-4xl font-display text-[clamp(3.5rem,9vw,7.2rem)] font-black italic uppercase leading-[0.83] tracking-[-0.055em] text-foreground">
              Compra.
              <br />
              Repara.
              <br />
              <span className="text-muted">Sigue.</span>
            </h1>

            <p className="mt-7 max-w-xl text-base leading-7 text-muted sm:text-lg sm:leading-8">
              Productos, accesorios y servicio técnico en una experiencia directa:
              información clara, disponibilidad visible y contacto cuando lo necesitas.
            </p>

            <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
              <Link
                href="/product"
                className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-6 py-3 text-sm font-extrabold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
              >
                Explorar catálogo
              </Link>
              <Link
                href="/services"
                className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border bg-surface px-6 py-3 text-sm font-bold uppercase tracking-[0.08em] text-foreground transition hover:border-foreground/25 hover:bg-surface-2"
              >
                Ver servicios
              </Link>
              {whatsappLink ? (
                <a
                  href={whatsappLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-12 items-center justify-center rounded-xl px-4 py-3 text-sm font-semibold text-muted transition hover:text-foreground"
                >
                  Consultar por WhatsApp ↗
                </a>
              ) : null}
            </div>

            {(contact.address || contact.city) ? (
              <p className="mt-7 flex items-center gap-2 text-xs text-muted">
                <svg className="h-3.5 w-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M17.657 16.657L13.414 20.9a2 2 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                {[contact.address, contact.city].filter(Boolean).join(", ")}
              </p>
            ) : null}
          </div>

          <div className="relative min-h-[360px] overflow-hidden rounded-[1.75rem] border border-bd-border bg-surface p-6 sm:min-h-[440px] sm:p-8">
            <div className="topo-bg pointer-events-none absolute inset-0 opacity-70" />
            <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full border border-foreground/[0.05]" />
            <div className="pointer-events-none absolute -right-4 top-8 h-32 w-32 rounded-full border border-foreground/[0.04]" />

            <div className="relative flex h-full min-h-[312px] flex-col justify-between sm:min-h-[376px]">
              <div className="flex items-start justify-between gap-6">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-muted">
                    {company.name || "Servicio técnico"}
                  </p>
                  <p className="mt-2 max-w-xs text-sm leading-6 text-muted">
                    Tienda y soporte técnico reunidos en una sola experiencia.
                  </p>
                </div>

                {branding.logo_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={branding.logo_url}
                    alt=""
                    className="h-12 max-w-28 object-contain opacity-90 sm:h-14 sm:max-w-36"
                  />
                ) : null}
              </div>

              <div>
                <p className="font-display text-7xl font-black italic leading-none tracking-[-0.08em] text-foreground/[0.08] sm:text-9xl" aria-hidden="true">
                  01
                </p>
                <div className="-mt-4 border-l border-primary/70 pl-5 sm:-mt-6">
                  <p className="max-w-sm font-display text-2xl font-extrabold uppercase leading-tight tracking-[-0.025em] text-foreground sm:text-3xl">
                    Tecnología útil.
                    <br />
                    Atención directa.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-bd-border pt-5 sm:grid-cols-4">
                {SERVICE_SHORTCUTS.map((item) => (
                  <Link
                    key={item.label}
                    href={item.href}
                    className="group flex items-center justify-between gap-2 py-2 text-xs font-semibold text-muted transition hover:text-foreground"
                  >
                    {item.label}
                    <span className="transition-transform group-hover:translate-x-0.5" aria-hidden="true">→</span>
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
