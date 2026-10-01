"use client";

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";
import { BrandLogo } from "./BrandLogo";

/*
  AQUÍ HABÍA UNA FRANJA DE CUATRO SERVICIOS CON EMOJIS, y sobraba por tres
  motivos a la vez.

  Repetía la lista de servicios que ya está más abajo y en el pie —tres sitios
  para lo mismo—, competía visualmente con los pilares que venían justo debajo
  con la misma forma de rejilla, y usaba emojis como lenguaje visual de una
  marca cuyo manual no los contempla. Un emoji se dibuja distinto en cada
  sistema operativo: no es un icono, es una cita a la tipografía de otro.

  El hero pasa de cinco bandas apiladas a una.
*/


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
    /*
      LA LOSA DE MARCA. Oscura en los dos temas, a propósito.

      M12F convirtió esto en `bg-background` al traducir la paleta, y en modo
      claro el hero pasó a ser crema: la marca desapareció de su propia
      portada. Una página clara con un hero negro no es una inconsistencia —
      es la decisión de diseño que este manual tomó. Lo que sería un fallo es
      que el sistema no supiera qué contraste usar encima, y para eso están
      los tokens `slab-*`.
    */
    <section className="relative overflow-hidden bg-slab text-slab-foreground">
      {/*
        EL ISOTIPO SANGRANDO — el device de la tarjeta de presentación.

        En las dos caras de la tarjeta el bulldog aparece enorme, en un tono
        apenas separado del fondo, cortado por el borde. No es contenido: es la
        textura de la marca. Aquí hace lo mismo y ocupa el sitio que antes
        llenaban una malla de puntos, una topografía, dos anillos, dos tarjetas
        flotantes y un lockup gigante — seis elementos compitiendo por una zona
        que la referencia deja casi vacía.

        `aria-hidden` porque es textura. Quien no ve la página no se pierde nada.
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-28 top-1/2 hidden w-[44rem] -translate-y-1/2 opacity-[0.05] lg:block"
      >
        <BrandLogo
          placement="compact"
          surface="dark"
          className="h-auto w-full object-contain"
          wordmarkClassName="sr-only"
        />
      </div>

      {/*
        EL MARCO. Las dos caras de la tarjeta llevan un rectángulo de una línea
        separado del borde. Es lo que hace que la pieza se lea como impresa y no
        como una pantalla, y cuesta un borde.
      */}
      <div className="relative mx-auto max-w-7xl px-6 py-10 lg:px-8 lg:py-14">
        <div className="border border-slab-border px-6 py-16 sm:px-10 lg:px-14 lg:py-24">
          {/* 7/5, no mitad y mitad: la tarjeta es asimétrica y ésa es la mitad
              de su carácter. */}
          <div className="grid gap-12 lg:grid-cols-12 lg:items-center">

            {/* Left: copy */}
            <div className="lg:col-span-7">
            {/* Label */}
            <div className="inline-flex items-center gap-2.5 border border-slab-border px-3.5 py-1.5">
              {/* EL PUNTO DORADO. Uno de los usos que el manual reserva al
                  acento: pequeño, sobre negro, donde rinde 8.4:1. No es
                  decoración perdida — es el 3–5 % puesto donde se ve. */}
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              <span className="text-[10px] font-bold uppercase tracking-[0.3em] text-slab-muted">
                {eyebrow}
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
              SIN `break-words`. Partir «ESPECIALIZADO» a mitad de palabra no
              es responsive: es un titular roto. El tamaño se elige para que la
              palabra más larga quepa en la columna —medido con navegador— y el
              ancho máximo evita la línea de sesenta caracteres que ningún
              titular debería tener.

              `clamp()` escala de forma continua, así que no hay un ancho donde
              se quede grande justo antes de saltar.
            */}
            <h1
              className="font-display mt-5 max-w-[15ch] font-black uppercase leading-[0.95] tracking-tight text-slab-foreground text-balance"
              style={{ fontSize: "clamp(2.25rem, 4.2vw, 4rem)" }}
            >
              {titleLines.map((line, i) => (
                <span key={i} className="block">
                  {i === titleLines.length - 1 && titleLines.length > 1 ? (
                    <span className="relative inline-block">
                      {line}
                      {/* El subrayado acompaña a la ÚLTIMA línea, sea cual sea:
                          es composición, no una palabra concreta del piloto. */}
                      {/* Una regla fina y dorada, no una barra. El acento del
                          manual va en detalles como éste: pequeño, sobre negro,
                          señalando dónde termina la frase. */}
                      <span
                        aria-hidden="true"
                        className="absolute -bottom-1.5 left-0 right-0 h-px bg-accent"
                      />
                    </span>
                  ) : (
                    line
                  )}
                </span>
              ))}
            </h1>

            <p className="mt-8 max-w-md text-base leading-7 text-slab-muted text-pretty">
              {subtitle}
              {policies.warranty_text ? `${subtitle ? " " : ""}${policies.warranty_text}` : ""}
            </p>

            {/* CTA buttons */}
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href={primaryHref}
                className="inline-flex min-h-11 items-center gap-2 rounded-full bg-slab-foreground px-7 py-3.5 text-sm font-black uppercase tracking-widest text-slab transition-colors hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                {primaryLabel}
              </Link>
              <a
                href={whatsappLink || "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center gap-2 rounded-full border border-slab-border bg-slab-surface px-7 py-3.5 text-sm font-bold uppercase tracking-widest text-slab-foreground transition-colors hover:bg-slab-surface/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
                </svg>
                WhatsApp
              </a>
            </div>

            {/* Location — hidden entirely when this tenant published none. */}
            <p
              className="mt-6 flex items-center gap-2 text-xs text-slab-muted"
              hidden={!contact.address && !contact.city}
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {[contact.address, contact.city].filter(Boolean).join(", ")}
            </p>
          </div>

            {/*
              LA COLUMNA DERECHA SE VACÍA A PROPÓSITO.

              Tenía un isotipo de 44 rem dentro de dos anillos concéntricos.
              Antes de eso, un lockup vertical gigante con dos tarjetas
              flotantes encima. La referencia hace lo contrario: en la tarjeta,
              la zona que no lleva texto está VACÍA, y el bulldog vive detrás
              de todo, cortado por el borde.

              El isotipo ya está — sangrando por la derecha, al 7 %. Aquí no va
              nada. Ése es el espacio negativo que la composición pedía y que
              cinco elementos apilados le habían quitado.
            */}
            <div className="hidden lg:col-span-5 lg:block" aria-hidden="true" />
          </div>
        </div>

      </div>
    </section>
  );
}
