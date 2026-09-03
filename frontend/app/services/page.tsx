import Link from "next/link";
import type { Metadata } from "next";
import { ServicesCta } from "./ServicesCta";

// The shop's name comes from the root layout's title template, which knows
// which tenant owns this host. The description is still the pilot's service copy
// — per-tenant landing content is tracked in docs/saas-multiempresa.md.
export const metadata: Metadata = {
  title: "Servicio técnico especializado en equipos Apple",
  description:
    "Reparación de iPhone: cambio de pantalla, batería, tapa trasera y glass. Diagnóstico gratuito.",
};

const services = [
  {
    num: "01",
    title: "Cambio de Pantalla",
    description:
      "Pantallas OLED/LCD con calibración de color, brillo y True Tone. No aparece el mensaje de pieza reparada. Instalación sin burbujas ni marcos desalineados.",
    devices: ["iPhone", "iPad"],
    time: "2–3 horas",
    highlight: "No pierde True Tone",
  },
  {
    num: "02",
    title: "Cambio de Batería",
    description:
      "Baterías Nasan con certificado de autenticidad. Recupera la autonomía de tu iPhone.",
    devices: ["iPhone", "iPad", "MacBook"],
    time: "1–2 horas",
    highlight: "Baterías Nasan ✓",
  },
  {
    num: "03",
    title: "Cambio de Tapa Trasera",
    description:
      "Tecnología láser para un cambio preciso y seguro. Los cambios no muestran el mensaje de pieza reparada. Tu iPhone lucirá impecable nuevamente.",
    devices: ["iPhone"],
    time: "2–3 horas",
    highlight: "Tecnología láser",
  },
  {
    num: "04",
    title: "Cambio de Glass",
    description:
      "Cristal frontal de protección premium. Instalación limpia sin polvo ni burbujas. Compatible con todos los modelos de iPhone.",
    devices: ["iPhone", "iPad"],
    time: "1 hora",
    highlight: null,
  },
  {
    num: "05",
    title: "Daño por Líquidos",
    description:
      "Diagnóstico gratuito y limpieza ultrasónica de la placa para recuperar tu dispositivo tras contacto con agua u otros líquidos.",
    devices: ["iPhone", "MacBook", "iPad"],
    time: "24–48 horas",
    highlight: "Diagnóstico gratis",
  },
  {
    num: "06",
    title: "Diagnóstico Técnico",
    description:
      "Evaluación completa del estado de tu dispositivo Apple: hardware, batería, conectores y sistema operativo. Sin costo.",
    devices: ["iPhone", "iPad", "MacBook", "Apple Watch"],
    time: "30 min",
    highlight: "Gratis",
  },
  {
    num: "07",
    title: "Recuperación de Datos",
    description:
      "Recuperamos fotos, contactos, notas y archivos de dispositivos dañados, con pantalla rota o que no encienden.",
    devices: ["iPhone", "MacBook", "iPad"],
    time: "1–5 días",
    highlight: null,
  },
  {
    num: "08",
    title: "Software y Sistema",
    description:
      "Actualizaciones de iOS/macOS, configuración de iCloud, recuperación de Apple ID, restauración DFU y resolución de errores.",
    devices: ["iPhone", "iPad", "MacBook"],
    time: "1 hora",
    highlight: null,
  },
];

const stats = [
  { stat: "5,000+", label: "Dispositivos reparados" },
  { stat: "6 meses", label: "Garantía en reparaciones" },
  { stat: "Nasan", label: "Baterías con certificado" },
  { stat: "S/ 0", label: "Diagnóstico" },
];

const faqs = [
  {
    q: "¿Cuánto demora una reparación?",
    a: "Los cambios de batería y pantalla se realizan el mismo día, en 1–3 horas. Reparaciones más complejas como daño por líquidos pueden tomar 24–72 horas.",
  },
  {
    q: "¿Qué repuestos utilizan?",
    a: "Trabajamos con baterías Nasan, que llegan con certificado de autenticidad de Nasan Technology. Antes de cada reparación te indicamos qué repuesto se usará y en qué condiciones.",
  },
  {
    q: "¿Aparece el mensaje de 'pieza reparada'?",
    a: "No. Nuestros cambios de pantalla, tapa trasera y batería no muestran el mensaje de pieza reparada en el iPhone.",
  },
  {
    q: "¿Tienen garantía los servicios?",
    a: "Todos nuestros servicios incluyen 6 meses de garantía en la mano de obra y el repuesto instalado. Aplican ciertas restricciones.",
  },
  {
    q: "¿Puedo llevar mi equipo sin cita?",
    a: "Sí. Para reparaciones complejas recomendamos coordinar por WhatsApp para asegurar disponibilidad del repuesto.",
  },
];

export default function ServicesPage() {
  return (
    <div className="min-h-screen bg-[#080808] text-white">

      {/* Hero */}
      <section className="relative overflow-hidden border-b border-white/[0.06]">
        <div className="topo-bg absolute inset-0 pointer-events-none" />
        <div className="dot-grid absolute right-0 top-0 h-72 w-72 opacity-35 pointer-events-none" />
        <div className="dot-grid absolute left-0 bottom-0 h-56 w-56 opacity-25 pointer-events-none" />

        <div className="relative mx-auto max-w-7xl px-6 py-20 lg:px-8 lg:py-28">
          <span className="section-label">Servicio Técnico</span>
          <h1 className="font-display mt-4 max-w-3xl text-6xl font-black uppercase leading-[0.9] tracking-tight text-white sm:text-7xl lg:text-8xl">
            ¿Tu iPhone<br />No Funciona<br />
            <span className="text-zinc-500">Como Antes?</span>
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-7 text-zinc-400">
            Técnicos especializados en equipos Apple. Te decimos qué repuesto
            se usa y en qué condiciones antes de empezar.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <ServicesCta
              label="Diagnóstico gratuito"
              className="inline-flex items-center gap-2.5 rounded-full bg-white px-8 py-4 text-sm font-black uppercase tracking-widest text-[#080808] transition hover:bg-zinc-200"
            />
            <Link
              href="/product"
              className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.05] px-8 py-4 text-sm font-bold uppercase tracking-widest text-white transition hover:border-white/25 hover:bg-white/10"
            >
              Ver catálogo
            </Link>
          </div>
        </div>
      </section>

      {/* Stats bar */}
      <section className="border-b border-white/[0.06] bg-[#111]">
        <div className="mx-auto grid max-w-7xl grid-cols-2 divide-x divide-white/[0.06] lg:grid-cols-4">
          {stats.map((item) => (
            <div key={item.label} className="px-8 py-8 text-center">
              <p className="font-display text-4xl font-black tracking-tight text-white lg:text-5xl">{item.stat}</p>
              <p className="mt-1.5 text-xs uppercase tracking-widest text-zinc-500">{item.label}</p>
            </div>
          ))}
        </div>
      </section>

      <main className="mx-auto max-w-7xl px-6 py-16 lg:px-8">

        {/* Services grid */}
        <section>
          <span className="section-label">Servicios disponibles</span>
          <h2 className="font-display mt-3 text-4xl font-black uppercase tracking-tight text-white sm:text-5xl">
            ¿Qué podemos reparar?
          </h2>

          <div className="mt-10 divide-y divide-white/[0.06]">
            {services.map((service) => (
              <div
                key={service.title}
                className="group flex flex-col gap-4 py-8 sm:flex-row sm:items-start sm:gap-8 lg:items-center"
              >
                {/* Number */}
                <p className="font-display shrink-0 text-4xl font-black text-zinc-800 lg:text-5xl">{service.num}</p>

                {/* Content */}
                <div className="flex-1">
                  <div className="flex flex-wrap items-center gap-3">
                    <h3 className="font-display text-2xl font-black uppercase text-white">{service.title}</h3>
                    {service.highlight && (
                      <span className="rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-zinc-400">
                        {service.highlight}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 max-w-xl text-sm leading-6 text-zinc-500">{service.description}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {service.devices.map((d) => (
                      <span
                        key={d}
                        className="rounded-full border border-white/[0.08] bg-white/[0.04] px-2.5 py-0.5 text-[10px] font-semibold text-zinc-500"
                      >
                        {d}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Time + CTA */}
                <div className="flex shrink-0 items-center gap-6 sm:flex-col sm:items-end sm:gap-3">
                  <p className="text-xs text-zinc-600">⏱ {service.time}</p>
                  <ServicesCta
                    label="Consultar"
                    withIcon={false}
                    className="rounded-full border border-white/10 bg-white/[0.05] px-5 py-2 text-xs font-bold uppercase tracking-widest text-zinc-400 transition hover:border-white/25 hover:text-white"
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Nasan Certificate section */}
        <section className="mt-20 overflow-hidden rounded-3xl border border-white/[0.08] bg-[#111]">
          <div className="grid gap-0 lg:grid-cols-2">
            <div className="border-b border-white/[0.06] px-8 py-12 sm:px-12 lg:border-b-0 lg:border-r">
              <span className="section-label">Certificación</span>
              <h2 className="font-display mt-3 text-4xl font-black uppercase leading-none tracking-tight text-white sm:text-5xl">
                Baterías<br />Nasan<br />Originales
              </h2>
              <p className="mt-4 text-sm leading-6 text-zinc-500">
                Esta tienda cuenta con el certificado de autenticidad de{" "}
                <strong className="text-white">Nasan Technology</strong> — empresa oficial de
                baterías para iPhone, con certificado de autenticidad de Nasan Technology.
              </p>
              <div className="mt-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.06] px-4 py-2">
                <span className="h-2 w-2 rounded-full bg-white" />
                <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-300">
                  Certificado Nasan — Abril 2025
                </span>
              </div>
            </div>
            <div className="px-8 py-12 sm:px-12">
              <div className="grid grid-cols-2 gap-4">
                {[
                  { label: "6 Meses", sub: "De garantía" },
                  { label: "Nasan", sub: "Certificada" },
                  { label: "Certificado", sub: "Nasan Technology" },
                  { label: "Sin msg", sub: "Pieza reparada" },
                ].map((item) => (
                  <div key={item.label} className="rounded-2xl border border-white/[0.06] bg-[#0d0d0d] p-5">
                    <p className="font-display text-3xl font-black text-white">{item.label}</p>
                    <p className="mt-1 text-xs text-zinc-600">{item.sub}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* FAQ */}
        <section className="mt-20">
          <span className="section-label">FAQ</span>
          <h2 className="font-display mt-3 text-4xl font-black uppercase tracking-tight text-white sm:text-5xl">
            Preguntas<br />Frecuentes
          </h2>
          <div className="mt-10 divide-y divide-white/[0.06]">
            {faqs.map((faq) => (
              <div key={faq.q} className="py-7">
                <h3 className="font-display text-lg font-black uppercase text-white">{faq.q}</h3>
                <p className="mt-2 text-sm leading-6 text-zinc-500">{faq.a}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Final CTA */}
        <section className="my-20 overflow-hidden rounded-3xl bg-white text-center">
          <div className="relative px-8 py-16 sm:px-12">
            <p className="font-display text-5xl font-black uppercase leading-none tracking-tight text-[#080808] sm:text-6xl">
              ¿Listo para<br />reparar tu Apple?
            </p>
            <p className="mt-4 text-sm text-zinc-600">
              Escríbenos ahora y cuéntanos qué le pasa a tu equipo. Respuesta en minutos.
            </p>
            <ServicesCta
              label="Hablar con un técnico"
              className="mt-8 inline-flex items-center gap-2.5 rounded-full bg-[#080808] px-8 py-4 text-sm font-black uppercase tracking-widest text-white transition hover:bg-zinc-800"
            />
          </div>
        </section>

      </main>
    </div>
  );
}
