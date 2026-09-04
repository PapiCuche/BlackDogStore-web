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

const REPAIR_SERVICES = [
  {
    title: "Cambio de Pantalla",
    desc: "Pantallas OLED/LCD con calibración de color y brillo. Te confirmamos el costo antes de reparar.",
    badge: "Más solicitado",
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    title: "Cambio de Batería",
    // "Originales" y una marca de terceros en la misma frase se contradicen:
    // una batería Nasan es de Nasan, no original de Apple. Se dice lo que es.
    desc: "Baterías Nasan. Recupera la autonomía de tu iPhone.",
    badge: "Nasan",
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h2a2 2 0 002-2V8a2 2 0 00-2-2h-2M3 8h14v12H3V8zM9 4h6v4H9V4z" />
      </svg>
    ),
  },
  {
    title: "Tapa Trasera",
    desc: "Cambio de tapa trasera con acabado cuidado. Revisamos el equipo y explicamos el alcance.",
    badge: null,
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

  useEffect(() => {
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then(setProducts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <Hero />

      {/* Pilares — reemplazan a las cuatro cifras que nadie podía respaldar */}
      <section className="border-y border-bd-border bg-surface">
        {/*
          UNA COLUMNA HASTA 400 px. Con dos columnas en un móvil de 320, cada
          celda tiene 112 px útiles tras el `px-6` — y «Especialización» no cabe
          en 112 px a ningún tamaño legible. El navegador no lo encoge: lo
          desborda, y la portada entera se desplazaba a lo ancho.

          `min-w-0` en las celdas porque una celda de rejilla no baja de su
          ancho intrínseco por defecto, y ése es el mecanismo exacto del
          desbordamiento.
        */}
        <div className="mx-auto grid max-w-7xl grid-cols-1 divide-y divide-bd-border xs:grid-cols-2 xs:divide-x xs:divide-y-0 lg:grid-cols-4">
          {PILLARS.map((item) => (
            <div key={item.title} className="min-w-0 px-6 py-8 text-center sm:px-8">
              <p className="font-display text-[clamp(0.95rem,2.2vw,1.25rem)] font-black uppercase tracking-tight text-foreground break-words">
                {item.title}
              </p>
              <p className="mt-1.5 text-xs leading-5 text-muted text-pretty">{item.label}</p>
            </div>
          ))}
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
              <Link
                key={section.slug}
                href={`/product?category=${section.slug}`}
                className="group flex flex-col items-center gap-3 rounded-2xl border border-bd-border bg-surface p-5 text-center transition hover:border-bd-border hover:bg-surface"
              >
                <span className="text-2xl">{section.icon}</span>
                <span className="font-display text-xs font-black uppercase tracking-widest text-muted transition group-hover:text-foreground">
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

          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {REPAIR_SERVICES.map((s) => (
              <a
                key={s.title}
                href="/services"
                className="group relative overflow-hidden rounded-2xl border border-bd-border bg-surface p-6 transition hover:border-bd-border hover:bg-surface"
              >
                {/* Badge */}
                {s.badge && (
                  <div className="mb-4 inline-flex rounded-full border border-bd-border bg-surface-2 px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-muted">
                    {s.badge}
                  </div>
                )}
                {!s.badge && <div className="mb-4 h-5" />}

                {/* Icon */}
                <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-bd-border bg-surface text-foreground">
                  {s.icon}
                </div>

                <h3 className="font-display text-xl font-black uppercase text-foreground">{s.title}</h3>
                <p className="mt-2 text-sm leading-6 text-muted">{s.desc}</p>

                <div className="mt-5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-muted transition group-hover:text-foreground">
                  Consultar precio
                  <span className="transition group-hover:translate-x-1">→</span>
                </div>
              </a>
            ))}
          </div>
        </section>

        {/* Guarantee strip */}
        <section className="relative overflow-hidden rounded-3xl border border-bd-border bg-surface">
          <div className="topo-bg absolute inset-0 opacity-60 pointer-events-none" />
          <div className="dot-grid absolute right-0 top-0 h-64 w-64 opacity-30 pointer-events-none" />
          <div className="relative grid gap-8 px-8 py-12 sm:px-12 sm:py-16 lg:grid-cols-2 lg:items-center">
            <div>
              <span className="section-label">Confianza</span>
              <h2 className="font-display mt-2 text-[clamp(1.75rem,8vw,3.75rem)] font-black uppercase leading-none tracking-tight text-foreground break-words">
                ¿Tu iPhone<br />No Funciona?
              </h2>
              <p className="mt-5 max-w-md text-base leading-7 text-muted">
                En {storeName} ofrecemos servicio técnico especializado en
                equipos Apple, con diagnóstico previo y condiciones claras
                antes de empezar.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <a
                  href={whatsappLink || "#"}
                  className="inline-flex items-center gap-2.5 rounded-full bg-foreground px-7 py-3.5 text-sm font-black uppercase tracking-widest text-background transition hover:bg-foreground/90"
                >
                  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
                  </svg>
                  Consultar por WhatsApp
                </a>
                <a
                  href="/services"
                  className="inline-flex items-center gap-2 rounded-full border border-bd-border bg-surface px-7 py-3.5 text-sm font-bold uppercase tracking-widest text-foreground transition hover:border-bd-border hover:bg-surface-2"
                >
                  Ver servicios
                </a>
              </div>
            </div>

            {/* Right: guarantee cards */}
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: "Diagnóstico", sub: "Antes de reparar", num: "01" },
                { label: "Baterías Nasan", sub: "Con certificado", num: "02" },
                { label: "Costo confirmado", sub: "Antes de empezar", num: "03" },
                { label: "Explicación\nclara", sub: "Del diagnóstico", num: "04" },
              ].map((card) => (
                <div
                  key={card.num}
                  className="rounded-2xl border border-bd-border bg-surface p-5"
                >
                  <p className="font-display text-xs font-black text-muted">{card.num}</p>
                  <p className="mt-2 font-display text-sm font-black uppercase leading-tight text-foreground whitespace-pre-line">
                    {card.label}
                  </p>
                  <p className="mt-1 text-[10px] text-muted">{card.sub}</p>
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
              <h2 className="font-display mt-2 text-[clamp(1.75rem,8vw,3.75rem)] font-black uppercase leading-none tracking-tight text-foreground break-words">
                Productos<br />Destacados
              </h2>
            </div>
            <Link href="/product" className="text-sm font-bold uppercase tracking-widest text-muted transition hover:text-foreground">
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
                  <p
                    className="font-display font-black uppercase tracking-tight text-slab-foreground text-balance"
                    style={{ fontSize: "clamp(1.75rem, 7vw, 4.5rem)" }}
                  >
                    {bottomPromo.badge || bottomPromo.title}
                  </p>
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
