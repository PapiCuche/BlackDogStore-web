import Link from "next/link";
import type { Metadata } from "next";
import { ServicesCta } from "./ServicesCta";

export const metadata: Metadata = {
  title: "Servicio técnico",
  description:
    "Información de servicio técnico, evaluación y canales de contacto de la tienda.",
};

const serviceAreas = [
  {
    num: "01",
    title: "Pantalla y visualización",
    description:
      "Revisión de golpes, vidrio, táctil, imagen y otros problemas visibles antes de definir la intervención.",
  },
  {
    num: "02",
    title: "Batería y energía",
    description:
      "Evaluación de autonomía, carga, apagados inesperados y comportamiento de energía del equipo.",
  },
  {
    num: "03",
    title: "Carcasa y daño físico",
    description:
      "Revisión de tapa, marco, botones, conectores y daños físicos para definir las opciones disponibles.",
  },
  {
    num: "04",
    title: "Software y configuración",
    description:
      "Diagnóstico de configuración, sistema, cuentas y problemas de funcionamiento que no requieren asumir una falla física.",
  },
];

const process = [
  { num: "01", title: "Evaluación", detail: "Primero entendemos el problema y el estado del equipo." },
  { num: "02", title: "Opciones", detail: "Te mostramos la alternativa disponible y sus condiciones." },
  { num: "03", title: "Confirmación", detail: "La intervención se realiza después de tu confirmación." },
  { num: "04", title: "Seguimiento", detail: "Puedes consultar el avance por los canales publicados." },
];

const faqs = [
  {
    q: "¿Cómo sé qué servicio necesita mi equipo?",
    a: "La evaluación inicial sirve para identificar el problema antes de elegir una intervención específica.",
  },
  {
    q: "¿Cuánto demora un servicio?",
    a: "Depende del modelo, el daño, la disponibilidad y la intervención necesaria. Confirma el plazo antes de autorizar el trabajo.",
  },
  {
    q: "¿Qué garantía aplica?",
    a: "Las condiciones de garantía dependen del servicio y de la política publicada por la tienda. Revisa esas condiciones antes de confirmar.",
  },
  {
    q: "¿Debo coordinar antes de llevar el equipo?",
    a: "Puedes usar el canal de contacto publicado para consultar disponibilidad y coordinar la atención antes de desplazarte.",
  },
];

export default function ServicesPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <section className="relative overflow-hidden border-b border-bd-border">
        <div className="topo-bg pointer-events-none absolute inset-0" aria-hidden="true" />
        <div className="absolute left-0 top-0 h-px w-28 bg-accent" aria-hidden="true" />
        <div className="relative mx-auto max-w-7xl px-6 py-16 lg:px-8 lg:py-24">
          <span className="section-label">Servicio técnico</span>
          <h1 className="mt-4 max-w-4xl font-display text-5xl font-black uppercase leading-[0.92] tracking-tight text-foreground sm:text-6xl lg:text-7xl">
            Entender el problema antes de intervenir.
          </h1>
          <p className="mt-6 max-w-2xl text-base leading-7 text-muted-foreground sm:text-lg sm:leading-8">
            Consulta una evaluación, revisa las opciones disponibles y confirma condiciones,
            precio y plazo antes de autorizar el servicio.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <ServicesCta
              label="Consultar servicio"
              className="inline-flex min-h-12 items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            <Link
              href="/product"
              className="inline-flex min-h-12 items-center rounded-xl border border-bd-border bg-surface px-6 py-3 text-sm font-bold uppercase tracking-[0.12em] text-foreground transition hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Ver catálogo
            </Link>
          </div>
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-6 py-14 lg:px-8 lg:py-16">
        <section>
          <span className="section-label">Áreas de consulta</span>
          <div className="mt-3 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <h2 className="max-w-2xl font-display text-4xl font-black uppercase leading-none tracking-tight text-foreground sm:text-5xl">
              Un punto de partida para evaluar el equipo.
            </h2>
            <p className="max-w-md text-sm leading-6 text-muted-foreground">
              La disponibilidad real de cada intervención se confirma después de revisar el modelo y la condición del equipo.
            </p>
          </div>

          <div className="mt-10 grid gap-4 md:grid-cols-2">
            {serviceAreas.map((service) => (
              <article
                key={service.num}
                className="rounded-xl border border-bd-border bg-surface p-6 transition hover:border-accent/60 hover:bg-surface-elevated sm:p-7"
              >
                <div className="flex items-start justify-between gap-4">
                  <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-accent">{service.num}</p>
                  <ServicesCta
                    label="Consultar"
                    withIcon={false}
                    className="text-xs font-bold uppercase tracking-[0.12em] text-muted-foreground transition hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  />
                </div>
                <h3 className="mt-8 font-display text-2xl font-black uppercase text-foreground">
                  {service.title}
                </h3>
                <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
                  {service.description}
                </p>
              </article>
            ))}
          </div>
        </section>

        <section className="mt-20 overflow-hidden rounded-2xl border border-bd-border bg-surface">
          <div className="grid lg:grid-cols-2">
            <div className="relative border-b border-bd-border p-8 sm:p-10 lg:border-b-0 lg:border-r">
              <div className="topo-bg pointer-events-none absolute inset-0 opacity-60" aria-hidden="true" />
              <div className="relative">
                <span className="section-label">Proceso</span>
                <h2 className="mt-3 font-display text-4xl font-black uppercase leading-none text-foreground sm:text-5xl">
                  Qué ocurre antes de confirmar.
                </h2>
                <p className="mt-4 max-w-lg text-sm leading-6 text-muted-foreground">
                  El flujo debe dejar claro qué se sabe, qué falta confirmar y cuándo se autoriza una intervención.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-px bg-bd-border">
              {process.map((item) => (
                <div key={item.num} className="bg-background p-5 sm:p-6">
                  <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-accent">{item.num}</span>
                  <h3 className="mt-5 font-display text-sm font-black uppercase text-foreground">{item.title}</h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">{item.detail}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mt-20">
          <span className="section-label">Preguntas frecuentes</span>
          <h2 className="mt-3 font-display text-4xl font-black uppercase leading-none text-foreground sm:text-5xl">
            Antes de llevar tu equipo.
          </h2>
          <div className="mt-10 divide-y divide-bd-border border-y border-bd-border">
            {faqs.map((faq) => (
              <div key={faq.q} className="grid gap-3 py-6 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] sm:gap-10 sm:py-7">
                <h3 className="font-display text-base font-black uppercase text-foreground">{faq.q}</h3>
                <p className="text-sm leading-6 text-muted-foreground">{faq.a}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="my-20 rounded-2xl border border-bd-border bg-surface px-8 py-12 text-center sm:px-12">
          <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-accent">Consulta</p>
          <p className="mx-auto mt-3 max-w-3xl font-display text-4xl font-black uppercase leading-none text-foreground sm:text-5xl">
            Cuéntanos qué ocurre con tu equipo.
          </p>
          <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-muted-foreground">
            Confirma disponibilidad y condiciones con la tienda antes de acercarte o autorizar un servicio.
          </p>
          <ServicesCta
            label="Hablar con la tienda"
            className="mt-8 inline-flex min-h-12 items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </section>
      </main>
    </div>
  );
}
