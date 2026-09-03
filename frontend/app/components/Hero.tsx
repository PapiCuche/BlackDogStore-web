"use client";

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";
import { BrandLogo } from "./BrandLogo";

const REPAIR_HIGHLIGHTS = [
  { icon: "📱", label: "Cambio de Pantalla" },
  { icon: "🔋", label: "Cambio de Batería" },
  { icon: "🔲", label: "Tapa Trasera" },
  { icon: "🔍", label: "Cambio de Glass" },
];

export default function Hero() {
  // Phase 3: the tenant's own WhatsApp, not a compiled-in number.
  const whatsappLink = useStorefront().contact.whatsapp_link;
  // M12F — el copy YA NO es del piloto. `page` viene de la fila de ESTA empresa;
  // el texto aprobado del manual de Black Dog vive en la fila de Black Dog,
  // escrito por una migración, igual que su identidad comercial desde la Fase 3.
  // Vacío cae a lo genérico de la plataforma, NUNCA a lo de otra empresa.
  const { company, contact, policies, page } = useStorefront();

  const eyebrow =
    page.hero_eyebrow || [company.name, contact.city].filter(Boolean).join(" · ");
  // Los saltos de línea son composición del titular: quien escribe
  // «Tu Apple, / con respaldo / especializado» decide el ritmo de lectura. Se
  // parten y se pintan como líneas — nunca como marcado.
  const titleLines = (page.hero_title || company.name || "").split("\n").filter(Boolean);
  const subtitle = page.hero_subtitle;
  const primaryLabel = page.hero_primary_cta_label || "Ver catálogo";
  const primaryHref = page.hero_primary_cta_url || "/product";

  return (
    <section className="relative overflow-hidden bg-background">
      {/* Topographic texture */}
      <div className="topo-bg absolute inset-0 pointer-events-none" />

      {/* Dot decoration — top right */}
      <div className="dot-grid absolute right-0 top-0 h-56 w-56 opacity-40 pointer-events-none" />
      {/* Dot decoration — bottom left */}
      <div className="dot-grid absolute left-0 bottom-0 h-48 w-48 opacity-30 pointer-events-none" />

      <div className="relative mx-auto max-w-7xl px-6 pt-16 pb-0 lg:px-8 lg:pt-24">
        <div className="grid gap-12 lg:grid-cols-2 lg:items-center">

          {/* Left: copy */}
          <div>
            {/* Label */}
            <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-4 py-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-white" />
              <span className="text-[10px] font-bold uppercase tracking-[0.3em] text-zinc-400">
                {[company.name, contact.city].filter(Boolean).join(" · ")}
              </span>
            </div>

            {/*
              COPY APROBADO POR EL MANUAL, no redactado aquí.

              Lo que había antes decía "El Mejor Servicio Apple en Perú", y eran
              dos problemas en una frase. "El mejor" es un superlativo que nadie
              puede demostrar. Y "Servicio Apple" se lee como servicio oficial de
              Apple, que es exactamente lo que el manual prohíbe afirmar sin una
              acreditación vigente: la forma correcta es "especializada en
              productos y equipos Apple".

              La frase principal y el descriptor salen literalmente del manual.
            */}
            {/*
              `clamp()` en vez de tres saltos de breakpoint: el titular escala de
              forma continua entre 320 y 1440 px, así que no hay un ancho donde
              se quede grande justo antes de saltar. `text-balance` reparte las
              líneas cuando el tenant escribe un titular de una sola línea larga.
            */}
            <h1
              className="font-display mt-5 font-black uppercase leading-[0.92] tracking-tight text-foreground text-balance"
              style={{ fontSize: "clamp(2.5rem, 9vw, 6rem)" }}
            >
              {titleLines.map((line, i) => (
                <span key={i} className="block">
                  {i === titleLines.length - 1 && titleLines.length > 1 ? (
                    <span className="relative inline-block">
                      {line}
                      {/* El subrayado acompaña a la ÚLTIMA línea, sea cual sea:
                          es composición, no una palabra concreta del piloto. */}
                      <span
                        aria-hidden="true"
                        className="absolute -bottom-1 left-0 right-0 h-[5px] rounded-full bg-foreground opacity-80"
                      />
                    </span>
                  ) : (
                    line
                  )}
                </span>
              ))}
            </h1>

            <p className="mt-8 max-w-md text-base leading-7 text-muted text-pretty">
              {subtitle}
              {policies.warranty_text ? `${subtitle ? " " : ""}${policies.warranty_text}` : ""}
            </p>

            {/* CTA buttons */}
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href={primaryHref}
                className="inline-flex min-h-11 items-center gap-2 rounded-full bg-foreground px-7 py-3.5 text-sm font-black uppercase tracking-widest text-background transition-colors hover:bg-foreground/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                {primaryLabel}
              </Link>
              <a
                href={whatsappLink || "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.05] px-7 py-3.5 text-sm font-bold uppercase tracking-widest text-white transition hover:border-white/30 hover:bg-white/10"
              >
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
                </svg>
                WhatsApp
              </a>
            </div>

            {/* Location — hidden entirely when this tenant published none. */}
            <p
              className="mt-6 flex items-center gap-2 text-xs text-zinc-600"
              hidden={!contact.address && !contact.city}
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {[contact.address, contact.city].filter(Boolean).join(", ")}
            </p>
          </div>

          {/* Right: dog mascot graphic */}
          <div className="relative flex items-center justify-center lg:justify-end">
            <div className="relative flex h-[340px] w-[340px] items-center justify-center sm:h-[420px] sm:w-[420px]">
              {/* Subtle glow rings */}
              <div className="absolute inset-8 rounded-full border border-white/[0.04]" />
              <div className="absolute inset-20 rounded-full border border-white/[0.03]" />

              {/* Dog icon — white on dark */}
              {/*
                LA SUPERFICIE MANDA, NO EL TEMA. Este hero es negro en los dos
                temas —`bg-background` más abajo—, así que su logotipo es SIEMPRE
                la versión blanca. Si aquí se leyera el tema resuelto, el tema
                claro pondría el logo negro sobre este fondo negro: el mismo
                defecto que M12E vino a cerrar, reintroducido por otra puerta.

                Composición VERTICAL: el manual la asigna a piezas principales,
                y aquí se dibuja a 256-320 px, muy por encima de sus 140 px de
                ancho mínimo.
              */}
              <BrandLogo
                placement="hero"
                surface="dark"
                className="relative z-10 h-64 w-64 object-contain drop-shadow-[0_8px_48px_rgba(255,255,255,0.1)] sm:h-80 sm:w-80"
                wordmarkClassName="relative z-10 font-display text-5xl font-black uppercase tracking-tight text-white/90"
              />

              {/*
                El plazo de garantía viene de la configuración del tenant, no
                compilado. Antes decía "6 MESES" fijo, y el propio manual marca
                la política de garantía como PENDIENTE de redactar: prometer un
                plazo concreto en la portada mientras la política no existe es
                comprometer a la tienda a algo que todavía no ha decidido.

                Sin dato configurado no se dibuja nada. Un hueco es honesto; un
                número inventado, no.
              */}
              {policies.warranty_text ? (
                <div className="absolute right-2 top-12 max-w-[11rem] rounded-2xl border border-white/10 bg-surface px-4 py-3 shadow-2xl sm:right-0">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Garantía</p>
                  <p className="font-display mt-0.5 text-sm font-black leading-tight text-white">
                    {policies.warranty_text}
                  </p>
                </div>
              ) : null}

              {/* Floating badge — bottom left */}
              <div className="absolute bottom-14 left-2 rounded-2xl border border-white/10 bg-surface px-4 py-3 shadow-2xl sm:left-0">
                <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Baterías</p>
                <p className="font-display text-xl font-black text-white">NASAN</p>
                <p className="text-[9px] text-zinc-600">Certificadas ✓</p>
              </div>
            </div>
          </div>
        </div>

        {/* Bottom: repair services strip */}
        <div className="mt-12 border-t border-white/[0.06]">
          <div className="grid grid-cols-2 divide-x divide-white/[0.06] sm:grid-cols-4">
            {REPAIR_HIGHLIGHTS.map((item) => (
              <Link
                key={item.label}
                href="/services"
                className="group flex items-center gap-3 px-6 py-5 transition hover:bg-white/[0.03]"
              >
                <span className="text-xl">{item.icon}</span>
                <span className="text-sm font-semibold text-zinc-400 transition group-hover:text-white">
                  {item.label}
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
