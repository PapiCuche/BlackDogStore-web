"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useStorefront } from "./components/StorefrontProvider";
import { ProductCarousel } from "./components/ProductCarousel";
import { StorefrontMotion } from "./components/StorefrontMotion";
import { BrandLogo } from "./components/BrandLogo";
import { StoreLink } from "./components/StoreLink";
import Hero from "./components/Hero";
import { fetcher, apiUrl } from "./lib/api";
import { categoryHref, homeCategories, useCatalogCategories } from "./lib/catalog-categories";
import { storefrontMediaStyle } from "./lib/storefront-media";

/**
 * PORTADA — V3.
 *
 * La composición es la de la V3: hero, firma de marca, categorías ilustradas,
 * proceso de servicio, catálogo en carrusel, soluciones, cercanía, preguntas
 * y campaña.
 *
 * LOS DATOS SON LOS DE LA TIENDA. Categorías, productos, servicios, preguntas,
 * contacto, políticas y campañas vienen del backend, y las imágenes son las que
 * la tienda subió desde su panel. Aquí no hay una lista de categorías escrita a
 * mano ni imágenes elegidas por el nombre de la tienda: otra empresa en la
 * misma plataforma ve SU catálogo y SUS imágenes con esta misma composición.
 */

type Product = {
  id: number; slug: string; name: string; description?: string; price: number;
  inventory?: number; image_url?: string; average_rating?: number | null;
  review_count?: number; category?: { id: number; name: string; slug: string };
};

/** El proceso de una reparación, en el orden en que le pasa al cliente. */
const STEPS = [
  "Cuéntanos qué ocurre", "Revisamos el equipo", "Explicamos las opciones", "Realizamos el servicio",
];

export default function Home() {
  const { company, contact, services, faqs, policies, campaigns, page } = useStorefront();
  // Las familias de la portada las elige la tienda (`show_on_home`) y las
  // ordena el servidor. El menú y el catálogo siguen ofreciendo todas.
  const categories = homeCategories(useCatalogCategories());
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const promo = campaigns.home_bottom_promo;
  const repairs = services.length > 0;

  useEffect(() => {
    let current = true;
    fetcher<Product[]>(apiUrl("/products?ordering=newest"))
      .then((data) => { if (current) setProducts(data); })
      .catch(() => {
        if (current) setError("No pudimos cargar el catálogo. Puedes volver a intentarlo desde la tienda.");
      })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, []);

  return (
    <StorefrontMotion>
      <div className="bg-background text-foreground">
        <Hero />

        {/* FIRMA DE MARCA. Sobre el fondo del tema, con el isotipo de la tienda
            como marca de agua tenue (13 %) en ambos temas. */}
        <section data-testid="brand-statement" className="v3-brand-signature bg-background text-foreground">
          <div className="v3-container relative flex min-h-72 flex-col items-start justify-between gap-10 overflow-hidden py-14 sm:flex-row sm:items-center">
            <div className="relative z-10 max-w-xl">
              <p className="section-label text-primary">{company.name}</p>
              <h2 className="v3-heading mt-4">
                Compra con claridad.
                {repairs ? <><br />Repara con respaldo.</> : null}
              </h2>
              <p className="mt-5 max-w-lg text-sm leading-6 text-muted">
                {repairs
                  ? "Equipos, accesorios y servicio técnico especializado. Te acompañamos antes, durante y después de tu compra."
                  : "Conoce la condición, la garantía y las opciones de entrega antes de decidir."}
              </p>
            </div>
            <div
              className="v3-brand-watermark pointer-events-none relative w-40 shrink-0 self-end sm:w-64 sm:self-auto lg:w-80"
              aria-hidden="true"
            >
              <BrandLogo placement="compact" surface="theme" className="h-auto w-full object-contain" wordmarkClassName="sr-only" />
            </div>
          </div>
        </section>

        <main>
          {categories.length > 0 ? (
            <section className="bg-surface py-16 lg:py-20" aria-labelledby="categories-title">
              <div className="v3-container">
                <div className="flex flex-wrap items-end justify-between gap-4">
                  <div>
                    <h2 id="categories-title" className="v3-heading">Encuentra lo que necesitas.</h2>
                    <p className="mt-3 text-muted">Productos y accesorios, organizados para elegir con claridad.</p>
                  </div>
                  <Link href="/product" className="v3-text-link">
                    Ver catálogo completo <span aria-hidden="true">↗</span>
                  </Link>
                </div>
                <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {categories.map((category) => (
                    <Link key={category.slug} href={categoryHref(category.slug)} className="v3-category group" data-motion-reveal>
                      <h3 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">{category.name}</h3>
                      {category.image_url ? (
                        <div data-image-slot="filled" className="relative my-5 aspect-square">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            src={category.image_url}
                            alt=""
                            loading="lazy"
                            style={storefrontMediaStyle(category.image_url)}
                            className="absolute inset-0 h-full w-full object-contain transition-transform duration-300 ease-out group-hover:scale-105"
                          />
                        </div>
                      ) : (
                        <div data-image-slot="empty" className="my-5 flex aspect-square items-center justify-center" aria-hidden="true">
                          <BrandLogo placement="compact" surface="theme" className="h-16 w-16 object-contain opacity-20" wordmarkClassName="sr-only" />
                        </div>
                      )}
                      <span className="mt-3 block text-sm">Explorar <span aria-hidden="true">›</span></span>
                    </Link>
                  ))}
                </div>
              </div>
            </section>
          ) : null}

          {repairs ? (
            <section className="v3-container grid gap-12 py-16 lg:grid-cols-2 lg:gap-20 lg:py-24">
              <div className="min-w-0">
                <p className="section-label">Servicio técnico especializado</p>
                <h2 className="v3-heading mt-4 text-balance break-words">
                  {page.services_hero_title || "¿Tu equipo no funciona como antes?"}
                </h2>
                <p className="mt-5 max-w-lg leading-7 text-muted">
                  {page.services_hero_subtitle
                    || "Revisión y reparación de hardware y software. El alcance, el costo y la garantía se confirman después de evaluar tu equipo."}
                </p>
                <div className="mt-7 flex flex-wrap gap-3">
                  {contact.whatsapp_link ? (
                    <StoreLink href={contact.whatsapp_link} className="v3-button">Solicitar revisión</StoreLink>
                  ) : null}
                  <Link href="/services" className="v3-button v3-button-outline">Ver servicios</Link>
                </div>
                {page.services_image_url ? (
                  <div data-storefront-section-image="services" className="mt-8 flex min-h-48 items-center justify-center p-4 sm:p-6">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={page.services_image_url}
                      alt=""
                      loading="lazy"
                      style={storefrontMediaStyle(page.services_image_url)}
                      className="max-h-72 w-auto max-w-full object-contain"
                    />
                  </div>
                ) : null}
              </div>
              <ol className="divide-y divide-bd-border">
                {STEPS.map((step, index) => (
                  <li key={step} className="flex min-h-24 items-center gap-6 py-5">
                    <span className="text-xs font-semibold tabular-nums text-primary">{String(index + 1).padStart(2, "0")}</span>
                    <span className="text-lg sm:text-xl">{step}</span>
                  </li>
                ))}
              </ol>
            </section>
          ) : null}

          <section className="bg-surface py-16 lg:py-20">
            <div className="v3-container">
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div>
                  <p className="section-label">Descubre el catálogo</p>
                  <h2 className="v3-heading mt-3">Tu próximo equipo.</h2>
                </div>
                <Link href="/product" className="v3-text-link">Ver todos <span aria-hidden="true">↗</span></Link>
              </div>
              {error ? (
                <div role="alert" className="mt-8 rounded-lg border border-danger-border bg-danger-surface p-6 text-danger">
                  <p>{error}</p>
                  <Link href="/product" className="mt-3 inline-block underline underline-offset-4">Abrir catálogo</Link>
                </div>
              ) : loading ? (
                <div aria-label="Cargando productos" className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {[1, 2, 3].map((n) => <div key={n} className="h-96 animate-pulse rounded-lg bg-background" />)}
                </div>
              ) : products.length ? (
                <ProductCarousel products={products.slice(0, 6)} />
              ) : (
                <div className="mt-8 rounded-lg bg-background p-8">
                  <h3 className="text-xl font-semibold">Catálogo en preparación</h3>
                  <p className="mt-3 text-muted">Consulta disponibilidad con nuestro equipo.</p>
                  {contact.whatsapp_link ? (
                    <StoreLink href={contact.whatsapp_link} className="v3-button mt-6">Consultar stock</StoreLink>
                  ) : null}
                </div>
              )}
            </div>
          </section>

          {repairs ? (
            <section className="v3-container py-16">
              <p className="section-label">Cuidamos tu equipo</p>
              <h2 className="v3-heading mt-3">Soluciones especializadas.</h2>
              <div className="mt-8 grid gap-x-12 sm:grid-cols-2">
                {services.slice(0, 4).map((service) => (
                  <Link key={service.title} href="/services" className="group flex items-start justify-between gap-4 border-t border-bd-border py-6">
                    <div className="min-w-0">
                      <h3 className="text-lg font-semibold [overflow-wrap:anywhere]">{service.title}</h3>
                      <p className="mt-2 text-sm leading-6 text-muted">{service.description}</p>
                    </div>
                    <span aria-hidden="true" className="transition-transform group-hover:translate-x-1">↗</span>
                  </Link>
                ))}
              </div>
            </section>
          ) : null}

          {contact.address || policies.warranty_text ? (
            <section className="bg-surface py-16">
              <div className="v3-container">
                <p className="section-label">Respaldo que puedes ver</p>
                <h2 className="v3-heading mt-3">Cerca de ti, antes y después.</h2>
                <div className={`mt-8 grid gap-4 ${page.location_image_url ? "lg:grid-cols-3" : "md:grid-cols-2"}`}>
                  {page.location_image_url ? (
                    <div data-storefront-section-image="location" className="flex min-h-64 items-center justify-center p-4 sm:p-6">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={page.location_image_url}
                        alt=""
                        loading="lazy"
                        style={storefrontMediaStyle(page.location_image_url)}
                        className="max-h-80 w-auto max-w-full object-contain"
                      />
                    </div>
                  ) : null}
                  {contact.address ? (
                    <div className="rounded-lg bg-background p-8">
                      <p className="section-label">Visítanos</p>
                      <h3 className="mt-4 text-2xl font-semibold">Nuestra tienda</h3>
                      <p className="mt-4 leading-7 text-muted">{contact.address}<br />{contact.city}</p>
                      <Link href="/contact" className="v3-text-link mt-6">Ubicación y contacto ↗</Link>
                    </div>
                  ) : null}
                  {policies.warranty_text ? (
                    <div className="rounded-lg bg-background p-8">
                      <p className="section-label">Compra informado</p>
                      <h3 className="mt-4 text-2xl font-semibold">Condiciones claras</h3>
                      <p className="mt-4 leading-7 text-muted">{policies.warranty_text}</p>
                      {policies.warranty_url ? (
                        <StoreLink href={policies.warranty_url} className="v3-text-link mt-6">Consultar garantía ↗</StoreLink>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </section>
          ) : null}

          {faqs.length ? (
            <section className="v3-container py-16">
              <h2 className="v3-heading">Antes de decidir.</h2>
              <div className="mt-8 divide-y divide-bd-border">
                {faqs.map((faq) => (
                  <details key={faq.question} className="v3-faq py-5">
                    <summary className="cursor-pointer text-base font-medium">{faq.question}</summary>
                    <p className="mt-4 max-w-3xl text-sm leading-7 text-muted">{faq.answer}</p>
                  </details>
                ))}
              </div>
            </section>
          ) : null}

          {promo ? (
            <section data-motion-reveal className="v3-promo bg-slab text-slab-foreground">
              <div className="v3-container grid items-center gap-8 py-14 md:grid-cols-2">
                <div className="min-w-0">
                  {promo.badge ? <p className="section-label text-accent">{promo.badge}</p> : null}
                  <h2 className="v3-heading mt-4 [overflow-wrap:anywhere]">{promo.title}</h2>
                  {promo.subtitle ? <p className="mt-3 text-lg">{promo.subtitle}</p> : null}
                  {promo.body ? <p className="mt-4 text-sm leading-7 text-slab-muted">{promo.body}</p> : null}
                  {promo.cta_url ? (
                    <StoreLink href={promo.cta_url} className="v3-button v3-button-light mt-6">
                      {promo.cta_label || "Consultar"}
                    </StoreLink>
                  ) : null}
                  {promo.secondary_cta_url ? (
                    <StoreLink href={promo.secondary_cta_url} className="ml-4 underline underline-offset-4">
                      {promo.secondary_cta_label || "Más información"}
                    </StoreLink>
                  ) : null}
                </div>
                {promo.image_url ? (
                  <div className="relative aspect-video">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={promo.image_url}
                      alt={promo.title}
                      loading="lazy"
                      style={storefrontMediaStyle(promo.image_url, "slab")}
                      className="absolute inset-0 h-full w-full object-contain"
                    />
                  </div>
                ) : (
                  <div aria-hidden="true" className="hidden justify-center opacity-15 md:flex">
                    <BrandLogo placement="compact" surface="dark" className="h-44 w-44 object-contain" wordmarkClassName="sr-only" />
                  </div>
                )}
              </div>
            </section>
          ) : null}
        </main>
      </div>
    </StorefrontMotion>
  );
}
