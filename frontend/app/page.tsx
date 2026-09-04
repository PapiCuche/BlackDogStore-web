"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useStoreName, useStorefront } from "./components/StorefrontProvider";
import { ProductCard } from "./components/ProductCard";
import { BrandLogo } from "./components/BrandLogo";
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

/*
  ICONOS DE TRAZO, NO EMOJIS.

  Aquí había 📱 ⌚ 🖥 💻 🎧 🎵. Un emoji no es un icono: es una cita a la
  tipografía de otro sistema, y se dibuja distinto en macOS, en Windows y en
  Android — tres identidades visuales que la marca no eligió. Además mezclaba
  dos lenguajes en la misma página, porque el resto de la web ya usa trazo de
  1.5 px.

  Una sola familia, un solo grosor, la misma caja de 24.
*/
const STROKE = {
  fill: "none" as const,
  viewBox: "0 0 24 24",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const CATALOG_SECTIONS = [
  {
    label: "iPhone", slug: "iphone",
    path: <><rect x="7" y="2.5" width="10" height="19" rx="2.2" /><path d="M10.6 5.4h2.8" /></>,
  },
  {
    label: "Apple Watch", slug: "apple-watch",
    path: <><rect x="7.5" y="6.5" width="9" height="11" rx="2.4" /><path d="M9.6 6.5 10 3h4l.4 3.5M9.6 17.5 10 21h4l.4-3.5" /></>,
  },
  {
    label: "iPad", slug: "ipad",
    path: <><rect x="4.5" y="2.5" width="15" height="19" rx="2" /><path d="M10.4 5.2h3.2" /></>,
  },
  {
    label: "Mac", slug: "mac",
    path: <><rect x="3" y="4.5" width="18" height="11.5" rx="1.6" /><path d="M2 19.5h20" /></>,
  },
  {
    label: "Accesorios", slug: "accesorios",
    path: <><path d="M5 12.5a7 7 0 0 1 14 0" /><rect x="3" y="12" width="4" height="7" rx="1.6" /><rect x="17" y="12" width="4" height="7" rx="1.6" /></>,
  },
  {
    label: "Audífonos", slug: "audifonos",
    path: <><path d="M9 4.5v10.2" /><circle cx="7" cy="16.5" r="2.4" /><path d="M9 7.5 18 5.5v9" /><circle cx="16" cy="16.5" r="2.4" /></>,
  },
];

/*
  LOS CUATRO NÚMEROS QUE HABÍA AQUÍ NO ERAN COMPROBABLES.

    "5,000+ Dispositivos reparados"  — nadie los ha contado
    "6 meses Garantía garantizada"   — el manual marca la política de garantía
                                       como PENDIENTE de redactar
    "100% Repuestos originales"      — se contradecía con la propia tarjeta de
                                       batería, que ofrece una marca de terceros
    "0 soles Diagnóstico"            — la política de servicio técnico también
                                       está pendiente

  Se sustituyen por los pilares que el manual SÍ define y que además son
  verificables mirando la tienda: especialización, respaldo, transparencia y
  experiencia. Una cifra inventada es peor que ninguna cifra, porque es la que
  un cliente cita cuando reclama.
*/
const PILLARS = [
  { title: "Especialización", label: "Productos y equipos Apple" },
  { title: "Respaldo", label: "Condiciones claras y postventa" },
  { title: "Transparencia", label: "Estado, procedencia y entrega" },
  { title: "Experiencia", label: "Atención antes, durante y después" },
];

/*
  AQUÍ HABÍA UNA CUARTA LISTA DE SERVICIOS.

  Compilada, con su propia redacción y su propio badge: «Más solicitado» — una
  métrica que nadie ha medido, del mismo tipo que las que M12F.1 retiró de
  /services. Sobrevivió porque estaba en otro fichero.

  Es el mismo defecto que ya se cerró dos veces: el pie tenía su lista, la
  página de servicios la suya, y la portada ésta. Ahora las tres leen los
  servicios ACTIVOS del tenant. Desactivar uno lo quita de los tres sitios.
*/


/**
 * M12F — la tira sale del CATÁLOGO, no de una lista escrita a mano.
 *
 * Antes había aquí nueve modelos compilados encabezados por «iPhone 17 Pro
 * Max»: el mismo defecto que la preventa, sólo que más silencioso. Una lista
 * de modelos envejece sola y nadie despliega para actualizar una marquesina.
 *
 * Ahora son los productos que este tenant vende de verdad. Si no vende
 * ninguno, la tira no se dibuja: una franja vacía girando en bucle no informa
 * de nada.
 */
function marqueeItems(products: Product[]): string[] {
  const names = Array.from(new Set(products.map((p) => p.name).filter(Boolean)));
  return names.slice(0, 12);
}

/**
 * Un destino de campaña puede ser interno o externo, y no son lo mismo.
 *
 * Una ruta del propio sitio va por `<Link>`: navegación de cliente, sin
 * recargar. Una URL externa —o un `tel:` / `mailto:`— va por `<a>` con
 * `rel="noopener noreferrer"`, porque abrir en otra pestaña sin eso deja al
 * destino acceso a `window.opener`.
 *
 * El esquema ya lo validó el backend: sólo llegan aquí rutas internas, http(s),
 * `tel:` y `mailto:`. Esto decide cómo NAVEGAR, no si el destino es seguro.
 */
function PromoLink({
  href,
  className,
  children,
}: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  const internal = href.startsWith("/") && !href.startsWith("//");
  if (internal) {
    return (
      <Link href={href} className={className}>
        {children}
      </Link>
    );
  }
  const newTab = href.startsWith("http");
  return (
    <a
      href={href}
      className={className}
      {...(newTab ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

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
  const marquee = marqueeItems(products);
  // M12F — la promoción inferior es un DATO del tenant. Si no hay campaña
  // vigente en este slot, la sección no se dibuja: mejor nada que la preventa
  // del año pasado.
  const bottomPromo = useStorefront().campaigns.home_bottom_promo;
  // La MISMA fuente que /services y el pie. Tres listas de lo mismo divergen.
  const services = useStorefront().services;

  useEffect(() => {
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then(setProducts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Hero />

      {/*
        LOS PILARES, EN LENGUAJE DE TARJETA.

        Eran cuatro celdas centradas con borde a los lados: la forma de un panel
        de control. El reverso de la tarjeta resuelve una lista exactamente así:
        un punto pequeño, una línea que los une, y el texto alineado a la
        izquierda. Nada de cajas.

        En móvil la línea es vertical y la lista se lee como una enumeración; en
        escritorio se tumba y los cuatro se reparten. Es la misma pieza girada,
        no dos diseños distintos.

        (Sustituyen a las cuatro cifras que nadie podía respaldar.)
      */}
      <section className="border-b border-bd-border">
        <div className="mx-auto max-w-7xl px-6 py-14 lg:px-8 lg:py-16">
          <ol className="relative grid gap-8 sm:grid-cols-2 lg:grid-cols-4 lg:gap-10">
            {/* La línea conectora: un hairline que atraviesa los cuatro puntos. */}
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-[3px] top-2 hidden h-[calc(100%-1rem)] w-px bg-bd-border sm:block lg:left-0 lg:top-[3px] lg:h-px lg:w-full"
            />
            {PILLARS.map((item) => (
              <li key={item.title} className="relative min-w-0 pl-6 lg:pl-0 lg:pt-8">
                <span
                  aria-hidden="true"
                  className="absolute left-0 top-[7px] h-[7px] w-[7px] rounded-full bg-primary lg:left-0 lg:top-0"
                />
                <p className="font-display text-[clamp(0.95rem,1.6vw,1.15rem)] font-black uppercase tracking-tight text-foreground">
                  {item.title}
                </p>
                <p className="mt-1.5 max-w-[28ch] text-xs leading-5 text-muted text-pretty">
                  {item.label}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </section>


      <main className="mx-auto max-w-7xl px-6 lg:px-8">

        {/* Category sections grid */}
        <section className="py-16">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Catálogo</span>
              <h2 className="font-display mt-2 text-[clamp(1.75rem,8vw,3.75rem)] font-black uppercase leading-none tracking-tight text-foreground break-words">
                Categorías
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold uppercase tracking-widest text-muted transition hover:text-foreground">
              Ver todos →
            </Link>
          </div>
          <div className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {CATALOG_SECTIONS.map((section) => (
              /*
                MENOS CARD, MÁS CELDA. Seis rectángulos redondeados idénticos
                con borde y fondo es el aspecto de cualquier panel de control.
                Aquí basta una celda con una línea: el borde superior se enciende
                al pasar, y ésa es toda la interacción que necesita.
              */
              <Link
                key={section.slug}
                href={`/product?category=${section.slug}`}
                className="group relative flex flex-col items-start gap-4 border-t border-bd-border px-1 py-6 transition-colors hover:border-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                <svg {...STROKE} className="h-7 w-7 text-muted transition-colors group-hover:text-foreground" aria-hidden="true">
                  {section.path}
                </svg>
                <span className="font-display text-xs font-black uppercase tracking-widest text-foreground">
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
              <h2 className="font-display mt-2 text-[clamp(1.75rem,8vw,3.75rem)] font-black uppercase leading-none tracking-tight text-foreground break-words">
                {/*
                  "Servicio Técnico Apple" se lee como servicio oficial de
                  Apple. El manual lo prohíbe sin acreditación vigente y da la
                  forma correcta: especializado EN equipos Apple.
                */}
                Servicio Técnico<br />Especializado
              </h2>
            </div>
            <a href="/services" className="text-sm font-bold uppercase tracking-widest text-muted transition hover:text-foreground">
              Ver todos →
            </a>
          </div>

          {/*
            LISTA, NO CUATRO CARDS. Un servicio no es una unidad que haya que
            encajonar: es una fila de un catálogo de trabajos. La regla de
            arriba lo separa del anterior y el número da el orden, que aquí sí
            significa algo — es el que el taller decidió en el panel.
          */}
          <ol className="mt-10 grid gap-x-10 gap-y-0 sm:grid-cols-2">
            {services.slice(0, 4).map((s, i) => (
              <li key={s.title} className="border-t border-bd-border">
                <Link
                  href="/services"
                  className="group flex min-h-11 items-start gap-5 py-6 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                >
                  {/*
                    `primary`, no `accent`. Sobre el crema del tema claro el
                    dorado puro rinde 2.12:1: §24 del encargo lo dice y la
                    medición lo confirma. `primary` es ese mismo dorado
                    oscurecido conservando el tono, calculado para leerse.
                    Sobre la losa oscura sí va el dorado puro.
                  */}
                  <span className="font-display shrink-0 text-sm font-black tabular-nums text-primary">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="font-display block text-lg font-black uppercase tracking-tight text-foreground">
                      {s.title}
                    </span>
                    {s.description ? (
                      <span className="mt-1.5 block max-w-[46ch] text-sm leading-6 text-muted text-pretty">
                        {s.description}
                      </span>
                    ) : null}
                  </span>
                  <span
                    aria-hidden="true"
                    className="shrink-0 pt-1 text-muted transition-transform group-hover:translate-x-1"
                  >
                    →
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        </section>

        {/*
          EL PROCESO, COMO PROCESO.

          Era una card redondeada con textura, malla de puntos y cuatro cards
          más dentro: seis cajas para explicar cuatro pasos. El reverso de la
          tarjeta resuelve esto con una losa oscura, una lista y nada más.

          Aquí los números SÍ significan algo —es la secuencia real de una
          reparación, en orden— así que numerar informa en vez de decorar.

          Retirado: «Baterías Nasan · Con certificado». Es la cuarta vez que
          aparece esa certificación en un fichero distinto, y sigue sin haber
          documento en el proyecto. Queda el repuesto, que sí es comprobable.
        */}
        <section className="-mx-6 my-20 bg-slab px-6 py-16 text-slab-foreground sm:px-10 lg:-mx-8 lg:px-14 lg:py-20">
          <div className="grid gap-12 lg:grid-cols-12 lg:items-start lg:gap-16">
            <div className="lg:col-span-5">
              <span className="section-label text-slab-muted">Cómo trabajamos</span>
              <h2 className="font-display mt-3 text-[clamp(1.75rem,3.4vw,2.75rem)] font-black uppercase leading-[1.05] tracking-tight text-slab-foreground">
                ¿Tu iPhone no funciona?
              </h2>
              <p className="mt-5 max-w-[46ch] text-base leading-7 text-slab-muted text-pretty">
                En {storeName} ofrecemos servicio técnico especializado en
                equipos Apple, con diagnóstico previo y condiciones claras
                antes de empezar.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <a
                  href={whatsappLink || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-11 items-center gap-2.5 rounded-full bg-slab-foreground px-7 py-3.5 text-sm font-black uppercase tracking-widest text-slab transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
                  </svg>
                  Consultar por WhatsApp
                </a>
                <Link
                  href="/services"
                  className="inline-flex min-h-11 items-center gap-2 rounded-full border border-slab-border px-7 py-3.5 text-sm font-bold uppercase tracking-widest text-slab-foreground transition-colors hover:bg-slab-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Ver servicios
                </Link>
              </div>
            </div>

            <ol className="lg:col-span-7">
              {[
                { label: "Diagnóstico", sub: "Antes de tocar nada." },
                { label: "Repuesto acordado", sub: "Te decimos cuál se usa y en qué condiciones." },
                { label: "Costo confirmado", sub: "Antes de empezar el trabajo." },
                { label: "Explicación clara", sub: "Qué se hizo y qué cubre." },
              ].map((step, i) => (
                <li
                  key={step.label}
                  className="flex items-baseline gap-6 border-t border-slab-border py-5 last:border-b"
                >
                  <span className="font-display shrink-0 text-sm font-black tabular-nums text-accent">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="min-w-0">
                    <span className="font-display block text-base font-black uppercase tracking-tight text-slab-foreground">
                      {step.label}
                    </span>
                    <span className="mt-1 block text-sm leading-6 text-slab-muted text-pretty">
                      {step.sub}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Products section */}
        <section className="py-20">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <span className="section-label">Catálogo</span>
              <h2 className="font-display mt-2 text-[clamp(1.75rem,8vw,3.75rem)] font-black uppercase leading-none tracking-tight text-foreground break-words">
                Productos<br />Destacados
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold uppercase tracking-widest text-muted transition hover:text-foreground">
              Ver catálogo completo →
            </Link>
          </div>

          {error ? (
            <div className="mt-8 rounded-2xl border border-danger-border bg-danger-surface p-6 text-sm text-danger">
              Error al cargar productos: {error}
            </div>
          ) : loading ? (
            <div className="mt-10 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-72 animate-pulse rounded-2xl bg-surface" />
              ))}
            </div>
          ) : products.length === 0 ? (
            <div className="mt-8 flex flex-col items-center gap-6 rounded-3xl border border-dashed border-bd-border p-16 text-center">
              <img src="/assets/branding/logo-icon.png" alt="" className="h-16 w-16 opacity-10 invert" />
              <div>
                <p className="font-display text-2xl font-black uppercase text-muted">
                  Catálogo en preparación
                </p>
                <p className="mt-1 text-sm text-muted">Escríbenos por WhatsApp para consultar disponibilidad.</p>
              </div>
              <a
                href={whatsappLink || "#"}
                className="rounded-full bg-foreground px-6 py-3 text-xs font-black uppercase tracking-widest text-background transition hover:bg-foreground/90"
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

        {/*
          M12F — LA PROMOCIÓN ES UN DATO, NO UN COMPONENTE.

          Aquí vivía un <h2> con «iPhone 17 Pro Max» dentro. Cambiar de campaña
          exigía tocar este fichero y desplegar; no cambiarla dejaba una
          preventa caducada en portada — el fallo peor, porque nadie despliega
          para borrar algo que ya no existe.

          Sin campaña vigente no se dibuja nada. El backend ya filtró por
          empresa, estado y ventana temporal: si esto llega vacío es porque no
          hay nada que anunciar, y una sección vacía sería peor que ninguna.

          Todo el texto se pinta como TEXTO. No hay `dangerouslySetInnerHTML`
          en ninguna parte: lo escribe personal del tenant desde un panel, no
          el equipo que revisa este código.
        */}
          {/*
            LA MARQUESINA, ABAJO.

            Estaba en el primer scroll, entre los pilares y las categorías, y ahí
            hacía dos cosas mal: robaba jerarquía a lo que sí importa y, siendo
            una tira que se desplaza sola, se leía como un ticker financiero —que
            es justo el registro contrario al de esta marca.

            Aquí abajo cumple lo que sí aporta: enseñar qué modelos hay, con el
            catálogo real, a quien ya bajó. Y sigue quieta bajo movimiento
            reducido.
          */}
        {marquee.length > 0 ? (
          <div
            className="overflow-hidden border-b border-bd-border bg-surface py-3"
            // Decorativa: repite nombres que ya están en la rejilla de abajo. Un
            // lector de pantalla que la leyera tres veces seguidas no ganaría
            // nada y perdería el hilo de la página.
            aria-hidden="true"
          >
            <div className="flex animate-[marquee_25s_linear_infinite] gap-8 whitespace-nowrap">
              {[...marquee, ...marquee, ...marquee].map((brand, i) => (
                <span key={i} className="flex items-center gap-8 text-[10px] font-bold uppercase tracking-[0.3em] text-muted">
                  {brand}
                  <span className="h-1 w-1 rounded-full bg-muted" />
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {bottomPromo ? (
          <section className="mb-20 overflow-hidden rounded-3xl bg-slab text-slab-foreground">
            <div className="grid gap-0 lg:grid-cols-2">
              <div className="relative overflow-hidden px-8 py-12 sm:px-12">
                <div className="dot-grid pointer-events-none absolute right-0 top-0 h-40 w-40 opacity-20" />
                {bottomPromo.badge ? (
                  <span className="section-label text-accent">{bottomPromo.badge}</span>
                ) : null}
                <h2
                  className="font-display mt-3 font-black uppercase leading-none tracking-tight text-slab-foreground text-balance"
                  style={{ fontSize: "clamp(1.6rem, 6vw, 3.75rem)" }}
                >
                  {bottomPromo.title}
                </h2>
                {bottomPromo.subtitle ? (
                  <p className="mt-3 text-base font-semibold text-slab-foreground/85 text-pretty">
                    {bottomPromo.subtitle}
                  </p>
                ) : null}
                {bottomPromo.body ? (
                  <p className="mt-4 max-w-prose text-sm leading-6 text-slab-muted text-pretty">
                    {bottomPromo.body}
                  </p>
                ) : null}

                <div className="mt-6 flex flex-wrap gap-3">
                  {bottomPromo.cta_label && bottomPromo.cta_url ? (
                    <PromoLink
                      href={bottomPromo.cta_url}
                      className="inline-flex min-h-11 items-center gap-2.5 rounded-full bg-slab-foreground px-7 py-3.5 text-sm font-black uppercase tracking-widest text-slab transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {bottomPromo.cta_label}
                    </PromoLink>
                  ) : null}
                  {bottomPromo.secondary_cta_label && bottomPromo.secondary_cta_url ? (
                    <PromoLink
                      href={bottomPromo.secondary_cta_url}
                      className="inline-flex min-h-11 items-center gap-2.5 rounded-full border border-slab-border px-7 py-3.5 text-sm font-bold uppercase tracking-widest text-slab-foreground transition-colors hover:bg-slab-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {bottomPromo.secondary_cta_label}
                    </PromoLink>
                  ) : null}
                </div>
              </div>

              <div className="flex flex-col items-center justify-center bg-slab-surface px-8 py-12 text-center sm:px-12">
                {bottomPromo.image_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={bottomPromo.image_url}
                    alt={bottomPromo.title}
                    className="max-h-64 w-auto max-w-full object-contain"
                    loading="lazy"
                  />
                ) : (
                  /*
                    SIN IMAGEN, EL ISOTIPO. Aquí se repetía la etiqueta de la
                    campaña como titular gigante —«PREVENTA» dos veces en el
                    mismo bloque— y competía con el título real del producto,
                    que está a la izquierda.

                    El manual reserva el isotipo justo para esto: ocupar un
                    espacio de marca cuando no hay contenido que poner. En
                    cuanto el taller suba una imagen, ésa manda.
                  */
                  <div aria-hidden="true" className="w-full max-w-[190px] opacity-25">
                    <BrandLogo
                      placement="compact"
                      surface="dark"
                      className="h-auto w-full object-contain"
                      wordmarkClassName="sr-only"
                    />
                  </div>
                )}
                {bottomPromo.product ? (
                  <Link
                    href={`/product/${bottomPromo.product.slug}`}
                    className="mt-6 text-xs uppercase tracking-[0.3em] text-accent underline underline-offset-4 transition-opacity hover:opacity-80"
                  >
                    Ver ficha del producto
                  </Link>
                ) : storePhone ? (
                  <p className="mt-6 text-xs text-slab-muted">{storePhone}</p>
                ) : null}
              </div>
            </div>
          </section>
        ) : null}

      </main>
    </div>
  );
}
