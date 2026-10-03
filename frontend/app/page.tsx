"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useStoreName, useStorefront } from "./components/StorefrontProvider";
import { ProductCarousel } from "./components/ProductCarousel";
import { StorefrontMotion } from "./components/StorefrontMotion";
import { categoryHref, useCatalogCategories } from "./lib/catalog-categories";
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

/*
  AQUÍ HABÍA SEIS CATEGORÍAS ESCRITAS A MANO — iPhone, Apple Watch, iPad, Mac…
  Eran las del piloto, y cualquier otra tienda las veía en su portada con
  enlaces a categorías que no vende. Las categorías salen ahora del catálogo
  real (`useCatalogCategories`), en el orden que da el servidor. El icono es
  uno solo y neutro: dibujar un teléfono para «Laptops» sería volver a suponer
  qué vende la tienda.
*/
const CATEGORY_GLYPH = (
  <><rect x="4" y="4" width="7" height="7" rx="1.6" /><rect x="13" y="4" width="7" height="7" rx="1.6" /><rect x="4" y="13" width="7" height="7" rx="1.6" /><rect x="13" y="13" width="7" height="7" rx="1.6" /></>
);

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
  { title: "Especialización", label: "Conocemos lo que vendemos y reparamos" },
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

/** El titular de sección de la portada: sin mayúsculas forzadas, peso medio. */
const SECTION_TITLE =
  "text-[clamp(1.75rem,3.6vw,2.75rem)] font-semibold leading-[1.08] tracking-tight text-foreground text-balance";

/**
 * LA FRANJA DE MARCA. Negra, con el isotipo de la tienda sangrando por la
 * derecha, y una frase. El isotipo es el que la tienda subió: otra tienda ve el
 * suyo, o ninguno.
 *
 * La frase no supone qué vende la tienda. «Repara con respaldo» sólo aparece si
 * la tienda publicó servicios: una que no repara no lo dice.
 */
function BrandStatement({ storeName, repairs }: { storeName: string; repairs: boolean }) {
  return (
    <section data-testid="brand-statement" className="relative overflow-hidden bg-slab text-slab-foreground">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-10 top-1/2 w-[20rem] -translate-y-1/2 opacity-[0.16] sm:w-[26rem] lg:right-[8%]"
      >
        <BrandLogo placement="compact" surface="dark" className="h-auto w-full object-contain" wordmarkClassName="sr-only" />
      </div>
      <div className="relative mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8 lg:py-20">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">{storeName}</p>
        <h2 className="mt-4 max-w-[22ch] text-[clamp(1.6rem,3.4vw,2.5rem)] font-semibold uppercase leading-[1.1] tracking-tight text-slab-foreground text-balance">
          Compra con claridad.{repairs ? " Repara con respaldo." : ""}
        </h2>
        <p className="mt-6 max-w-[52ch] text-base leading-7 text-slab-muted text-pretty">
          Conoce la condición, la garantía y las opciones de entrega antes de decidir.
        </p>
      </div>
    </section>
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
  const { faqs, page } = useStorefront();
  const categories = useCatalogCategories();
  const { address, city } = useStorefront().contact;
  const storeAddress = [address, city].filter(Boolean).join(", ");

  useEffect(() => {
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then(setProducts)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <StorefrontMotion>
    <div className="min-h-screen bg-background text-foreground">
      <Hero />

      {/*
        DESPUÉS DEL HERO, EL CONTRAPESO. Con el hero claro va la franja de marca
        oscura, con el isotipo: es lo que devuelve a la página el negro de la
        marca que el hero dejó de llevar. Con la losa oscura ya está ese negro,
        y dos bloques negros seguidos serían uno solo demasiado alto: ahí van
        los pilares.
      */}
      {page.hero_variant === "light" ? (
        <BrandStatement storeName={storeName} repairs={services.length > 0} />
      ) : (
        <>
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
        </>
      )}

      <main className="mx-auto max-w-7xl px-6 lg:px-8">

        {/*
          CATEGORÍAS REALES, CADA UNA CON SU HUECO DE IMAGEN.

          Las categorías salen del catálogo y la imagen de cada una la sube la
          tienda desde su panel. Aquí no hay ninguna imagen compilada: la de
          otra tienda no es de ésta, y una categoría sin imagen se presenta con
          su nombre sobre un hueco tranquilo, no con una imagen rota.

          `object-contain` y sin fondo propio: un PNG sin fondo se apoya sobre
          la tarjeta, que es para lo que se sube así.
        */}
        {categories.length > 0 ? (
        <section className="py-16 lg:py-20" data-motion-reveal>
          <h2 className={SECTION_TITLE}>Encuentra lo que necesitas.</h2>
          <p className="mt-2 text-base text-muted">Elige una categoría.</p>
          <div className="mt-8 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
            {categories.slice(0, 8).map((category) => (
              <Link
                key={category.slug}
                href={categoryHref(category.slug)}
                className="group flex min-w-0 flex-col rounded-2xl bg-surface p-4 transition-colors hover:bg-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary sm:p-6"
              >
                <span className="text-lg font-semibold tracking-tight text-foreground [overflow-wrap:anywhere] sm:text-2xl">
                  {category.name}
                </span>
                {category.image_url ? (
                  <span data-image-slot="filled" className="mt-4 flex aspect-[4/3] items-center justify-center">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={category.image_url}
                      alt=""
                      loading="lazy"
                      className="max-h-full max-w-full object-contain transition-transform duration-300 group-hover:scale-[1.03]"
                    />
                  </span>
                ) : (
                  <span
                    data-image-slot="empty"
                    aria-hidden="true"
                    className="mt-4 flex aspect-[4/3] items-center justify-center rounded-xl bg-background/60 text-muted/50"
                  >
                    <svg {...STROKE} className="h-8 w-8">{CATEGORY_GLYPH}</svg>
                  </span>
                )}
                <span className="mt-4 text-sm font-medium text-foreground">
                  Ver más <span aria-hidden="true" className="inline-block transition-transform group-hover:translate-x-0.5">›</span>
                </span>
              </Link>
            ))}
          </div>
        </section>
        ) : null}

        {/*
          SERVICIO TÉCNICO — sólo si la tienda publicó algún servicio. Una tienda
          que no repara no anuncia un taller.

          A la izquierda, el titular y el texto que la tienda escribió para sus
          servicios. A la derecha, SUS servicios, numerados en el orden que
          decidió en el panel. Antes había aquí cuatro pasos escritos a mano que
          todas las tiendas compartían; los servicios reales dicen más y no
          suponen cómo trabaja nadie.
        */}
        {services.length > 0 ? (
        <section className="grid gap-10 py-16 lg:grid-cols-12 lg:gap-16 lg:py-20" data-motion-reveal>
          <div className="min-w-0 lg:col-span-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">
              Servicio técnico
            </p>
            <h2 className={`${SECTION_TITLE} mt-3 uppercase`}>
              {page.services_hero_title || "¿Tu equipo no funciona?"}
            </h2>
            <p className="mt-5 max-w-[46ch] text-base leading-7 text-muted text-pretty">
              {page.services_hero_subtitle
                || `En ${storeName} ofrecemos servicio técnico con diagnóstico previo y condiciones claras antes de empezar.`}
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              {whatsappLink ? (
                <a
                  href={whatsappLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-11 items-center rounded-full bg-foreground px-6 py-3 text-sm font-semibold text-background transition-colors hover:bg-foreground/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                >
                  Solicitar revisión
                </a>
              ) : null}
              <Link
                href="/services"
                className="inline-flex min-h-11 items-center rounded-full border border-foreground/30 px-6 py-3 text-sm font-semibold text-foreground transition-colors hover:border-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Ver servicios
              </Link>
            </div>
          </div>

          <ol className="min-w-0 lg:col-span-7">
            {services.slice(0, 4).map((s, i) => (
              <li key={s.title} className="border-b border-bd-border first:border-t">
                <Link
                  href="/services"
                  className="group flex min-h-11 items-baseline gap-5 py-6 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                >
                  {/* `primary`, no `accent`: sobre la superficie clara el dorado
                      puro no llega al contraste mínimo. */}
                  <span className="shrink-0 text-xs font-semibold tabular-nums text-primary">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-lg font-medium tracking-tight text-foreground sm:text-xl">
                      {s.title}
                    </span>
                    {s.description ? (
                      <span className="mt-1.5 block max-w-[52ch] text-sm leading-6 text-muted text-pretty">
                        {s.description}
                      </span>
                    ) : null}
                  </span>
                  <span aria-hidden="true" className="shrink-0 text-muted transition-transform group-hover:translate-x-1">→</span>
                </Link>
              </li>
            ))}
          </ol>
        </section>
        ) : null}

        {/* Products section */}
        <section className="py-20" data-motion-reveal>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className={SECTION_TITLE}>Productos destacados</h2>
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
              {/* El logotipo de ESTA tienda, por `BrandLogo`. Aquí había un PNG
                  del piloto escrito en la ruta. */}
              <div className="opacity-20" aria-hidden="true">
                <BrandLogo
                  placement="compact"
                  surface="theme"
                  className="h-16 w-16 object-contain"
                  wordmarkClassName="sr-only"
                />
              </div>
              <div>
                <p className="font-display text-2xl font-black uppercase text-muted">
                  Catálogo en preparación
                </p>
                <p className="mt-1 text-sm text-muted">
                  {whatsappLink ? "Escríbenos por WhatsApp para consultar disponibilidad." : "Vuelve pronto: estamos publicando los productos."}
                </p>
              </div>
              {whatsappLink ? (
              <a
                href={whatsappLink}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-full bg-foreground px-6 py-3 text-xs font-black uppercase tracking-widest text-background transition hover:bg-foreground/90"
              >
                Consultar stock
              </a>
              ) : null}
            </div>
          ) : (
            /* STOREFRONT-V3 — los mismos productos, en una fila que se
               recorre. Conserva enlaces, stock y precio de cada tarjeta. */
            <ProductCarousel products={products.slice(0, 6)} />
          )}
        </section>

        {/*
          PREGUNTAS FRECUENTES — las que la tienda publicó, y sólo ésas. Es la
          misma lista que muestra `/services`; aquí van las primeras. Sin
          preguntas publicadas no hay bloque: una pregunta inventada es una
          promesa que nadie hizo.
        */}
        {faqs.length > 0 ? (
          <section className="py-20" data-motion-reveal>
            <h2 className={SECTION_TITLE}>Preguntas frecuentes</h2>
            <div className="mt-10 border-t border-bd-border">
              {faqs.slice(0, 6).map((faq) => (
                <details key={faq.question} className="group border-b border-bd-border">
                  <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-6 py-5 text-base font-semibold text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
                    {faq.question}
                    <span className="shrink-0 text-muted transition-transform group-open:rotate-45" aria-hidden="true">+</span>
                  </summary>
                  <p className="max-w-[70ch] pb-6 text-sm leading-7 text-muted text-pretty">{faq.answer}</p>
                </details>
              ))}
            </div>
          </section>
        ) : null}

        {/*
          CERCA DE TI — sólo con lo que la tienda publicó. Sin dirección ni
          ciudad no hay bloque: una tienda que sólo vende en línea no anuncia un
          mostrador.
        */}
        {storeAddress ? (
          <section className="py-16 lg:py-20" data-motion-reveal>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <h2 className={SECTION_TITLE}>Cerca de ti, antes y después.</h2>
              {whatsappLink ? (
                <p className="max-w-[40ch] text-sm leading-6 text-muted">
                  Visítanos o consulta por WhatsApp. Coordinamos tu compra y la entrega con información clara.
                </p>
              ) : null}
            </div>
            <div className="mt-8 grid gap-3 sm:gap-4 md:grid-cols-2">
              <div className="rounded-2xl bg-surface p-6 sm:p-8">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">Visítanos</p>
                <h3 className="mt-4 text-xl font-semibold tracking-tight text-foreground">Nuestra tienda</h3>
                <p className="mt-3 text-base leading-7 text-muted text-pretty">{storeAddress}</p>
              </div>
              <div className="rounded-2xl bg-surface p-6 sm:p-8">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary">Compra informado</p>
                <h3 className="mt-4 text-xl font-semibold tracking-tight text-foreground">Todo claro</h3>
                <p className="mt-3 text-base leading-7 text-muted text-pretty">
                  Consulta la condición, lo que incluye, la garantía y la entrega antes de comprar.
                </p>
              </div>
            </div>
          </section>
        ) : null}

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
    </StorefrontMotion>
  );
}
