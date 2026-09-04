"use client";

/**
 * M12F.1 — la página de servicios, con su contenido en manos del taller.
 *
 * LO QUE ESTO CIERRA
 * ------------------
 * Esta página publicaba como hechos varias afirmaciones que el propio proyecto
 * contradice o no respalda: «5.000+ dispositivos reparados» sin ninguna fuente,
 * «todos nuestros servicios incluyen 6 meses de garantía» cuando el manual del
 * piloto dice que la cobertura de servicios técnicos DEPENDE del trabajo, y una
 * certificación de la que no hay documento en el proyecto.
 *
 * Ninguna sobrevive por estar compilada. Ahora el contenido es del tenant, y
 * quien lo escribe responde por él.
 *
 * TODO SE PINTA COMO TEXTO. Sin `dangerouslySetInnerHTML`, porque esto lo
 * escribe personal del taller desde un panel.
 *
 * LAS SECCIONES VACÍAS NO SE DIBUJAN. Un taller sin métricas verificadas no
 * enseña un bloque de cifras: un bloque vacío es peor que ninguno, y una cifra
 * inventada peor que las dos cosas.
 */

import Link from "next/link";
import { useStorefront } from "../components/StorefrontProvider";
import { ServicesCta } from "./ServicesCta";

export function ServicesContent() {
  const { company, page, services, faqs, metrics } = useStorefront();

  // Los saltos de línea son composición del titular, no marcado.
  const titleLines = (page.services_hero_title || company.name || "")
    .split("\n")
    .filter(Boolean);

  return (
    <div className="min-h-screen bg-background text-foreground">

      {/* Hero */}
      <section className="relative overflow-hidden border-b border-bd-border">
        <div className="topo-bg pointer-events-none absolute inset-0" />
        <div className="dot-grid pointer-events-none absolute right-0 top-0 h-72 w-72 opacity-35" />
        <div className="dot-grid pointer-events-none absolute left-0 bottom-0 h-56 w-56 opacity-25" />

        <div className="relative mx-auto max-w-7xl px-6 py-20 lg:px-8 lg:py-28">
          <span className="section-label">Servicio Técnico</span>
          <h1
            className="font-display mt-4 max-w-3xl font-black uppercase leading-[0.9] tracking-tight text-foreground text-balance break-words"
            style={{ fontSize: "clamp(1.75rem, 8vw, 6rem)" }}
          >
            {titleLines.map((line, i) => (
              <span key={i} className="block">{line}</span>
            ))}
          </h1>
          {page.services_hero_subtitle ? (
            <p className="mt-6 max-w-xl text-lg leading-7 text-muted text-pretty">
              {page.services_hero_subtitle}
            </p>
          ) : null}
          <div className="mt-8 flex flex-wrap gap-3">
            {/*
              «Diagnóstico gratuito» era la etiqueta de este botón y no había
              política que lo respaldara. El botón sigue; la afirmación no.
            */}
            <ServicesCta
              label="Hablar con un técnico"
              className="inline-flex min-h-11 items-center gap-2.5 rounded-full bg-foreground px-8 py-4 text-sm font-black uppercase tracking-widest text-background transition-colors hover:bg-foreground/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            />
            <Link
              href="/product"
              className="inline-flex min-h-11 items-center gap-2 rounded-full border border-bd-border bg-surface px-8 py-4 text-sm font-bold uppercase tracking-widest text-foreground transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              Ver catálogo
            </Link>
          </div>
        </div>
      </section>

      {/* Métricas — sólo si el taller ha publicado alguna. */}
      {metrics.length > 0 ? (
        <section className="border-b border-bd-border bg-surface">
          <div className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-bd-border lg:grid-cols-4">
            {metrics.map((m) => (
              <div key={`${m.value}-${m.label}`} className="px-8 py-8 text-center">
                <p className="font-display text-4xl font-black tracking-tight text-foreground lg:text-5xl">
                  {m.value}
                </p>
                <p className="mt-1.5 text-xs uppercase tracking-widest text-muted">
                  {m.label}
                </p>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <main className="mx-auto max-w-7xl px-6 py-16 lg:px-8">

        {/* Servicios */}
        {services.length > 0 ? (
          <section>
            <span className="section-label">Servicios disponibles</span>
            <h2 className="font-display mt-3 text-[clamp(1.6rem,7vw,3rem)] font-black uppercase tracking-tight text-foreground break-words">
              ¿Qué podemos reparar?
            </h2>

            <div className="mt-10 divide-y divide-bd-border">
              {services.map((service, i) => (
                <div
                  key={service.title}
                  className="group flex flex-col gap-4 py-8 sm:flex-row sm:items-start sm:gap-8 lg:items-center"
                >
                  <p className="font-display shrink-0 text-4xl font-black text-muted/40 lg:text-5xl">
                    {String(i + 1).padStart(2, "0")}
                  </p>

                  {/* `min-w-0` para que un título largo pueda truncarse en vez
                      de empujar el tiempo fuera de la pantalla. */}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-3">
                      <h3 className="font-display text-2xl font-black uppercase text-foreground">
                        {service.title}
                      </h3>
                      {service.highlight ? (
                        <span className="rounded-full border border-bd-border bg-surface-2 px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-muted">
                          {service.highlight}
                        </span>
                      ) : null}
                    </div>
                    {service.description ? (
                      <p className="mt-2 max-w-xl text-sm leading-6 text-muted text-pretty">
                        {service.description}
                      </p>
                    ) : null}
                    {service.devices_text ? (
                      <p className="mt-3 text-[10px] font-semibold uppercase tracking-widest text-muted">
                        {service.devices_text}
                      </p>
                    ) : null}
                  </div>

                  <div className="flex shrink-0 items-center gap-6 sm:flex-col sm:items-end sm:gap-3">
                    {service.estimated_time_text ? (
                      /*
                        «ESTIMADO», y la palabra importa. El manual pide informar
                        que el tiempo puede variar según equipo, falla y
                        disponibilidad de repuestos. Un «2–3 horas» a secas es
                        una promesa; etiquetado es información.
                      */
                      <p className="text-xs text-muted">
                        Estimado: {service.estimated_time_text}
                      </p>
                    ) : null}
                    <ServicesCta
                      label="Consultar"
                      withIcon={false}
                      className="min-h-11 rounded-full border border-bd-border bg-surface px-5 py-2 text-xs font-bold uppercase tracking-widest text-muted transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {/* Garantía — la del taller, no una inventada. */}
        {page.services_warranty_note ? (
          <section className="mt-20 rounded-3xl border border-bd-border bg-surface px-8 py-10 sm:px-12">
            <span className="section-label">Garantía</span>
            <p className="mt-3 max-w-3xl text-base leading-7 text-foreground/85 text-pretty">
              {page.services_warranty_note}
            </p>
          </section>
        ) : null}

        {/* Preguntas frecuentes */}
        {faqs.length > 0 ? (
          <section className="mt-20">
            <span className="section-label">FAQ</span>
            <h2 className="font-display mt-3 text-[clamp(1.6rem,7vw,3rem)] font-black uppercase tracking-tight text-foreground break-words">
              Preguntas<br />Frecuentes
            </h2>
            <div className="mt-10 divide-y divide-bd-border">
              {faqs.map((faq) => (
                <div key={faq.question} className="py-7">
                  <h3 className="font-display text-lg font-black uppercase text-foreground">
                    {faq.question}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-muted text-pretty">
                    {faq.answer}
                  </p>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {/* Llamada final — se pinta con el color del texto, así que su
            contraste es el contrario al de la página. */}
        <section className="my-20 overflow-hidden rounded-3xl bg-foreground text-center">
          <div className="relative px-8 py-16 sm:px-12">
            <p className="font-display text-[clamp(1.75rem,8vw,3.75rem)] font-black uppercase leading-none tracking-tight text-background break-words">
              ¿Listo para<br />reparar tu Apple?
            </p>
            <p className="mt-4 text-sm text-background/70">
              Escríbenos y cuéntanos qué le pasa a tu equipo.
            </p>
            <ServicesCta
              label="Hablar con un técnico"
              className="mt-8 inline-flex min-h-11 items-center gap-2.5 rounded-full bg-background px-8 py-4 text-sm font-black uppercase tracking-widest text-foreground transition-opacity hover:opacity-85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            />
          </div>
        </section>

      </main>
    </div>
  );
}
