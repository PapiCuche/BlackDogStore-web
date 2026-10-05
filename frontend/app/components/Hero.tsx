"use client";

import { useStorefront } from "./StorefrontProvider";
import { StoreLink } from "./StoreLink";
import { storefrontMediaStyle } from "../lib/storefront-media";
import { useCatalogCategories } from "../lib/catalog-categories";

/**
 * HERO — V3: texto a la izquierda, imagen a la derecha, sobre el fondo de la
 * página. Es el único hero de la tienda.
 *
 * TODO SALE DE LA TIENDA. El titular, el antetítulo, los botones y la imagen
 * vienen de su configuración (`page`) o de la campaña vigente (`home_hero`).
 * La imagen es la que la tienda subió desde su panel
 * (Configuración › Escaparate): aquí no hay ninguna compilada, ni una lista de
 * imágenes por nombre de tienda. Los rótulos bajo los botones son las
 * categorías de SU catálogo, y el taller sólo se nombra si publicó servicios.
 *
 * LA IMAGEN SE APOYA SOBRE EL FONDO. Un recorte PNG conserva su transparencia,
 * se muestra entero (`object-contain`) y sin caja detrás; la sombra sigue su
 * silueta y sólo se aplica a las imágenes subidas aquí, no a una URL externa.
 */
export default function Hero() {
  const { company, contact, page, campaigns, services } = useStorefront();
  const campaign = campaigns.home_hero;
  const title = campaign?.title || page.hero_title || company.name;
  // La campaña vigente manda; sin ella, la imagen que la tienda dejó colocada.
  const image = campaign?.image_url || page.hero_image_url || "";
  // El título de la campaña describe la imagen de la campaña. La imagen propia
  // de la portada es decorativa: el titular ya dice lo que hay que decir.
  const imageAlt = campaign?.image_url ? campaign.title : "";
  const eyebrow = campaign?.badge || page.hero_eyebrow
    || [company.name, contact.city].filter(Boolean).join(" · ");
  const subtitle = campaign?.body || page.hero_subtitle;
  const categories = useCatalogCategories();
  const repairs = services.length > 0;
  // El segundo botón es el que la tienda configuró. Sin configurar, lleva al
  // taller sólo si hay taller: una tienda que no repara no lo anuncia.
  const secondaryHref = campaign?.secondary_cta_url || page.hero_secondary_cta_url
    || (repairs ? "/services" : "");
  const secondaryLabel = campaign?.secondary_cta_label || page.hero_secondary_cta_label
    || "Servicio técnico";
  const chips = [...categories.slice(0, 3).map((category) => category.name), ...(repairs ? ["Soporte técnico"] : [])];

  return (
    <section className="v3-hero bg-background" data-hero>
      <div className="v3-container grid items-center gap-10 py-14 lg:grid-cols-2 lg:py-20">
        <div className="v3-hero-copy min-w-0">
          {eyebrow ? <p className="section-label">{eyebrow}</p> : null}
          <h1 className="v3-hero-title mt-5 text-balance [overflow-wrap:anywhere]">
            {title.split("\n").filter(Boolean).map((line, index) => (
              <span key={index} className="block">{line}</span>
            ))}
          </h1>
          {subtitle ? (
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-muted text-pretty">{subtitle}</p>
          ) : null}
          <div className="mt-7 flex flex-wrap gap-3">
            <StoreLink
              href={campaign?.cta_url || page.hero_primary_cta_url || "/product"}
              className="v3-button"
            >
              {campaign?.cta_label || page.hero_primary_cta_label || "Ver catálogo"}
            </StoreLink>
            {secondaryHref ? (
              <StoreLink href={secondaryHref} className="v3-button v3-button-outline">
                {secondaryLabel}
              </StoreLink>
            ) : null}
          </div>
          {chips.length > 0 ? (
            <ul className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted">
              {chips.map((chip) => <li key={chip}>{chip}</li>)}
            </ul>
          ) : null}
        </div>

        {image ? (
          <div data-hero-art className="v3-hero-art relative aspect-square min-w-0">
            {/* `<img>` y no `next/image`: la imagen es de la tienda, de tamaño
                desconocido, y el optimizador sólo admite los hosts de
                `NEXT_PUBLIC_IMAGE_HOSTS`. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={image}
              alt={imageAlt}
              style={storefrontMediaStyle(image)}
              className="absolute inset-0 h-full w-full object-contain"
            />
          </div>
        ) : null}
      </div>
    </section>
  );
}
