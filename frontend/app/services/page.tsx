import Link from "next/link";
import type { Metadata } from "next";
import { ServicesCta } from "./ServicesCta";

export const metadata: Metadata = {
  title: "Servicio técnico",
  description: "Información sobre evaluación, reparación y soporte técnico.",
};

const services = [
  {
    num: "01",
    title: "Pantalla",
    description:
      "Evaluación del daño, reemplazo cuando corresponde y pruebas funcionales antes de la entrega.",
    devices: ["Smartphone", "Tablet"],
  },
  {
    num: "02",
    title: "Batería",
    description:
      "Revisión del estado de batería, diagnóstico del consumo y reemplazo cuando el caso lo requiere.",
    devices: ["Smartphone", "Tablet", "Laptop"],
  },
  {
    num: "03",
    title: "Carcasa y tapa",
    description:
      "Evaluación de daños externos y alternativas de intervención según el modelo y la condición del equipo.",
    devices: ["Smartphone"],
  },
  {
    num: "04",
    title: "Cristal",
    description:
      "Revisión del cristal y del conjunto de pantalla para definir la intervención adecuada.",
    devices: ["Smartphone", "Tablet"],
  },
  {
    num: "05",
    title: "Daño por líquidos",
    description:
      "Inspección técnica del equipo para identificar componentes afectados y el alcance posible de recuperación.",
    devices: ["Smartphone", "Tablet", "Laptop"],
  },
  {
    num: "06",
    title: "Diagnóstico técnico",
    description:
      "Evaluación del equipo, síntomas reportados y componentes relacionados antes de proponer una intervención.",
    devices: ["Smartphone", "Tablet", "Laptop", "Wearable"],
  },
  {
    num: "07",
    title: "Datos y respaldo",
    description:
      "Evaluación de alternativas de acceso o recuperación de información según el estado del dispositivo.",
    devices: ["Smartphone", "Tablet", "Laptop"],
  },
  {
    num: "08",
    title: "Software y sistema",
    description:
      "Soporte para configuración, actualizaciones, restauración y problemas de funcionamiento del sistema.",
    devices: ["Smartphone", "Tablet", "Laptop"],
  },
];

const process = [
  {
    num: "01",
    title: "Recepción",
    text: "Registramos el equipo y el motivo de la visita.",
  },
  {
    num: "02",
    title: "Evaluación",
    text: "Revisamos el caso y comunicamos qué necesita atención.",
  },
  {
    num: "03",
    title: "Acuerdo",
    text: "El alcance y las condiciones se confirman antes de continuar.",
  },
  {
    num: "04",
    title: "Entrega",
    text: "Se revisa el resultado y se comunica el cierre del servicio.",
  },
];

const faqs = [
  {
    q: "¿Cómo sé qué reparación necesita mi equipo?",
    a: "La intervención se define después de revisar el equipo y relacionar el síntoma reportado con el diagnóstico técnico.",
  },
  {
    q: "¿Puedo consultar antes de llevar el equipo?",
    a: "Sí. Puedes comunicarte con la tienda para explicar el problema y confirmar el canal de atención disponible.",
  },
  {
    q: "¿Las condiciones son iguales para todos los servicios?",
    a: "No necesariamente. El alcance, disponibilidad y condiciones dependen del equipo y del trabajo que se acuerde para cada caso.",
  },
  {
    q: "¿Dónde reviso la garantía o las condiciones del servicio?",
    a: "La tienda debe informarte las condiciones aplicables al servicio antes del cierre. Conserva la constancia o documento que recibas.",
  },
];

export default function ServicesPage() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <section className="relative overflow-hidden border-b border-bd-border">
        <div className="relative mx-auto max-w-7xl px-6 py-16 lg:px-8 lg:py-24">
          <span className="section-label">Servicio técnico</span>
          <h1 className="mt-4 max-w-4xl font-display text-5xl font-black italic uppercase leading-[0.86] tracking-[-0.05em] text-foreground sm:text-7xl lg:text-8xl">
            Primero entendemos el problema.
          </h1>
          <p className="mt-6 max-w-2xl text-base leading-7 text-muted sm:text-lg sm:leading-8">
            El servicio comienza con una evaluación clara del equipo y continúa con el alcance que se acuerde para el caso.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <ServicesCta
              label="Consultar servicio"
              className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-6 py-3 text-sm font-extrabold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
            />
            <Link
              href="/product"
              className="inline-flex min-h-12 items-center justify-center rounded-xl border border-bd-border bg-surface px-6 py-3 text-sm font-bold uppercase tracking-[0.08em] text-foreground transition hover:border-foreground/25"
            >
              Ver catálogo
            </Link>
          </div>
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-6 py-14 lg:px-8 lg:py-20">
        <section>
          <div className="grid gap-6 border-b border-bd-border pb-8 lg:grid-cols-[0.75fr_1.25fr] lg:items-end">
            <div>
              <span className="section-label">Servicios disponibles</span>
              <h2 className="mt-3 font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground sm:text-5xl">
                Casos que podemos evaluar.
              </h2>
            </div>
            <p className="max-w-xl text-sm leading-7 text-muted lg:justify-self-end">
              La lista orienta el tipo de atención. El diagnóstico final depende del equipo y de su condición real.
            </p>
          </div>

          <div className="grid sm:grid-cols-2">
            {services.map((service) => (
              <article key={service.title} className="border-b border-bd-border py-8 sm:px-6 lg:py-10">
                <span className="text-[10px] font-bold tracking-[0.18em] text-muted">{service.num}</span>
                <h3 className="mt-5 font-display text-2xl font-extrabold uppercase tracking-[-0.025em] text-foreground">
                  {service.title}
                </h3>
                <p className="mt-3 max-w-xl text-sm leading-7 text-muted">{service.description}</p>
                <div className="mt-5 flex flex-wrap gap-2">
                  {service.devices.map((device) => (
                    <span key={device} className="rounded-full border border-bd-border px-3 py-1 text-[10px] font-semibold text-muted">
                      {device}
                    </span>
                  ))}
                </div>
                <ServicesCta
                  label="Consultar"
                  withIcon={false}
                  className="mt-6 inline-flex rounded-xl border border-bd-border px-4 py-2.5 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25"
                />
              </article>
            ))}
          </div>
        </section>

        <section className="mt-16 overflow-hidden rounded-[1.75rem] border border-bd-border bg-surface sm:mt-20">
          <div>
            <div className="grid gap-8 p-7 sm:p-10 lg:grid-cols-[0.7fr_1.3fr] lg:p-12">
              <div>
                <span className="section-label">Proceso</span>
                <h2 className="mt-3 font-display text-4xl font-black italic uppercase leading-[0.92] tracking-[-0.04em] text-foreground">
                  Un servicio que puedes seguir.
                </h2>
              </div>
              <ol className="grid sm:grid-cols-2">
                {process.map((step) => (
                  <li key={step.num} className="border-b border-bd-border p-5 sm:border-l sm:p-6">
                    <span className="text-[10px] font-bold tracking-[0.18em] text-muted">{step.num}</span>
                    <h3 className="mt-4 font-display text-lg font-extrabold uppercase text-foreground">{step.title}</h3>
                    <p className="mt-2 text-sm leading-6 text-muted">{step.text}</p>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </section>

        <section className="mt-16 sm:mt-20">
          <span className="section-label">Preguntas frecuentes</span>
          <h2 className="mt-3 font-display text-4xl font-black italic uppercase tracking-[-0.04em] text-foreground sm:text-5xl">
            Antes de dejar tu equipo.
          </h2>
          <div className="mt-8 divide-y divide-bd-border">
            {faqs.map((faq) => (
              <article key={faq.q} className="grid gap-3 py-6 md:grid-cols-[0.8fr_1.2fr] md:gap-8">
                <h3 className="font-display text-lg font-extrabold uppercase text-foreground">{faq.q}</h3>
                <p className="text-sm leading-7 text-muted">{faq.a}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="my-16 border-t border-bd-border pt-10 sm:my-20">
          <div className="grid gap-6 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <p className="font-display text-3xl font-black italic uppercase tracking-[-0.035em] text-foreground sm:text-4xl">
                Cuéntanos qué le pasa a tu equipo.
              </p>
              <p className="mt-2 text-sm text-muted">La tienda te indicará el siguiente paso de atención.</p>
            </div>
            <ServicesCta
              label="Hablar con la tienda"
              className="inline-flex min-h-12 items-center justify-center rounded-xl bg-primary px-6 py-3 text-sm font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
            />
          </div>
        </section>
      </main>
    </div>
  );
}
