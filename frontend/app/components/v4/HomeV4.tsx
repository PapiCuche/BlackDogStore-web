"use client";

/**
 * P2 · F2 — the home page of the V4 preview (`/v4`).
 *
 * EVERYTHING ON IT IS THE SHOP'S. Two sources and nothing else:
 *  - the storefront config (`useStorefront`): the hero texts and image the shop
 *    wrote in its panel (or its running `home_hero` campaign, which wins, as on
 *    the V3 home), the other running home campaigns, and its name;
 *  - the published models (`/catalog/models/`), read on the server by the page
 *    and handed in as `models`.
 *
 * WHAT IT NEVER DOES: write a tagline the shop did not write (no hero texts →
 * no hero at all), invent a price, a stock count, a review, opening hours or an
 * address, or put a purchase wall («La Manada») in front of the catalogue.
 * A model with nothing to sell says «Agotado» instead of a price.
 *
 * Every model links to its V4 page; the one call to action goes down to them.
 */

import Link from "next/link";
import { ProductImage } from "../ProductImage";
import { StoreLink } from "../StoreLink";
import { useStorefront } from "../StorefrontProvider";
import { formatMoney } from "../../lib/format";
import { photoLabel } from "../../lib/model-configurator";
import {
  SOLD_OUT_LABEL,
  type CatalogModelCard,
  type CatalogModelList,
  type Lines,
} from "../../lib/catalog-models";
import type { StorefrontCampaign, StorefrontPage } from "../../lib/storefront";

const MODELS_ID = "modelos";
/** The home campaigns besides the hero, in the page's order. */
const PROMO_SLOTS = ["home_featured", "home_promo", "home_bottom_promo"] as const;

function modelHref(slug: string): string {
  return `/v4/modelo/${encodeURIComponent(slug)}`;
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

type HeroContent = { eyebrow: string; title: string; subtitle: string; image: string; imageAlt: string };

/**
 * What the shop wrote for its hero, with V3's rule (the running `home_hero`
 * campaign wins over the page's texts), or `null` when it wrote no text: an
 * image alone is not a hero, and a hero is never filled with a made-up line.
 */
function heroContent(page: StorefrontPage, campaign: StorefrontCampaign | undefined): HeroContent | null {
  const eyebrow = (campaign?.badge || page.hero_eyebrow || "").trim();
  const title = (campaign?.title || page.hero_title || "").trim();
  const subtitle = (campaign?.body || page.hero_subtitle || "").trim();
  if (!eyebrow && !title && !subtitle) return null;
  return {
    eyebrow, title, subtitle,
    image: campaign?.image_url || page.hero_image_url || "",
    // A campaign's picture shows the campaign; the page's own one is decoration next to the headline.
    imageAlt: campaign?.image_url ? campaign.title : "",
  };
}

function HeroV4({ hero, hasModels }: { hero: HeroContent; hasModels: boolean }) {
  const { eyebrow, title, subtitle, image, imageAlt } = hero;
  return (
    <section data-testid="v4-hero" aria-labelledby={title ? "v4-hero-title" : undefined} className="v4-hero">
      <div className="mx-auto max-w-5xl px-4 pt-14 text-center sm:px-6 lg:pt-20">
        {eyebrow ? <p className="v4-eyebrow v4-eyebrow-center">{eyebrow}</p> : null}
        {title ? (
          <h1 id="v4-hero-title" className="v4-display mt-4 text-balance [overflow-wrap:anywhere]">
            {title.split("\n").filter(Boolean).map((line, index) => (
              <span key={index} className="block">{line}</span>
            ))}
          </h1>
        ) : null}
        {subtitle ? <p className="v4-lead mx-auto mt-5 max-w-2xl text-pretty">{subtitle}</p> : null}
        {hasModels ? (
          <div className="mt-8 flex justify-center">
            <a href={`#${MODELS_ID}`} className="v4-cta v4-cta-inline">Ver modelos</a>
          </div>
        ) : null}
      </div>
      {image ? (
        <div className="mx-auto mt-10 max-w-5xl px-4 sm:px-6 lg:mt-14">
          <div className="relative aspect-[16/9]">
            <ProductImage
              src={image}
              alt={imageAlt}
              sizes="(min-width: 1024px) 976px, 100vw"
              className="object-contain"
              priority
              fetchPriority="high"
            />
          </div>
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

function ModelCardV4({ model, lines, eager }: { model: CatalogModelCard; lines: Lines; eager: boolean }) {
  const caption = model.image_url ? photoLabel(model.image_url, "catalog") : null;
  const lineNames = [
    model.has_new ? lines.new?.label : "",
    model.has_used ? lines.used?.label : "",
  ].filter(Boolean);
  return (
    <Link href={modelHref(model.slug)} className="v4-card group">
      <div className="v4-card-stage relative aspect-[4/3] sm:aspect-square">
        {model.image_url ? (
          <ProductImage
            src={model.image_url}
            alt=""
            sizes="(min-width: 1280px) 380px, (min-width: 640px) 50vw, 100vw"
            className={`object-contain p-8 ${model.sold_out ? "opacity-50" : ""}`}
            priority={eager}
            // V4-QA: the LCP without a hero picture. A panel upload is a plain `<img>`, where `priority` only drops
            // `lazy`: it also needs the high fetch priority (and the preload `ProductImage` emits with it).
            fetchPriority={eager ? "high" : undefined}
          />
        ) : (
          <span aria-hidden="true" className="flex h-full items-center justify-center text-5xl font-bold text-[color:var(--v4-line-strong)]">
            {model.name.trim().charAt(0).toUpperCase() || "·"}
          </span>
        )}
        {caption ? <span className="v4-image-label">{caption}</span> : null}
      </div>
      <div className="flex flex-1 flex-col px-6 pb-6 pt-5 text-center">
        {model.category ? <p className="v4-small">{model.category.name}</p> : null}
        <p className="v4-card-title mt-1">{model.name}</p>
        {lineNames.length > 0 ? <p className="v4-small mt-1">{lineNames.join(" · ")}</p> : null}
        <p className="mt-auto pt-4">
          {model.sold_out ? (
            <span className="v4-sold-out">{SOLD_OUT_LABEL}</span>
          ) : model.from_price !== null ? (
            <span className="v4-data">Desde S/ {formatMoney(model.from_price)}</span>
          ) : null}
        </p>
      </div>
    </Link>
  );
}

function ModelGridV4({ models, isPageTitle, eagerFirst }: {
  models: CatalogModelList | null;
  /** Without a hero, «Modelos» is the page's heading. */
  isPageTitle: boolean;
  eagerFirst: boolean;
}) {
  const { company } = useStorefront();
  const Heading = isPageTitle ? "h1" : "h2";
  return (
    <section id={MODELS_ID} aria-labelledby="v4-models-title" className="mx-auto max-w-7xl scroll-mt-6 px-4 py-14 sm:px-6 lg:px-10 lg:py-20">
      <div className="text-center">
        {isPageTitle && company.name ? <p className="v4-eyebrow v4-eyebrow-center">{company.name}</p> : null}
        <Heading id="v4-models-title" className="v4-heading mt-3">Modelos</Heading>
      </div>
      {models === null ? (
        <p role="alert" className="v4-small mt-10 text-center">No se pudieron cargar los modelos.</p>
      ) : models.results.length === 0 ? (
        <p className="v4-small mt-10 text-center">Aún no hay modelos publicados.</p>
      ) : (
        <ul aria-labelledby="v4-models-title" className="mt-10 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {models.results.map((model, index) => (
            <li key={model.slug} className="min-w-0">
              <ModelCardV4 model={model} lines={models.lines} eager={eagerFirst && index === 0} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Campaigns
// ---------------------------------------------------------------------------

function CampaignV4({ campaign }: { campaign: StorefrontCampaign }) {
  return (
    <article className="v4-card v4-campaign">
      {campaign.image_url ? (
        <div className="v4-card-stage relative aspect-[16/10]">
          <ProductImage src={campaign.image_url} alt="" sizes="(min-width: 1024px) 600px, 100vw" className="object-contain p-6" />
        </div>
      ) : null}
      <div className="flex flex-1 flex-col p-6 sm:p-8">
        {campaign.badge ? <p className="v4-eyebrow">{campaign.badge}</p> : null}
        {/* V4-QA: a sibling of «Modelos» (h1 without a hero, h2 with one), never an h3 that skips a level. */}
        <h2 className="v4-card-title mt-3">{campaign.title}</h2>
        {campaign.subtitle ? <p className="v4-data mt-2">{campaign.subtitle}</p> : null}
        {campaign.body ? <p className="v4-small mt-3 leading-6">{campaign.body}</p> : null}
        {campaign.cta_url ? (
          <StoreLink href={campaign.cta_url} className="v4-link mt-5 self-start">
            {campaign.cta_label || "Ver más"}
          </StoreLink>
        ) : null}
      </div>
    </article>
  );
}

function CampaignsV4() {
  const { campaigns } = useStorefront();
  const shown = PROMO_SLOTS
    .map((slot) => campaigns[slot])
    .filter((campaign): campaign is StorefrontCampaign => Boolean(campaign?.title?.trim()));
  if (shown.length === 0) return null;
  return (
    <section aria-label="Campañas" className="mx-auto max-w-7xl px-4 pb-20 sm:px-6 lg:px-10">
      <div className="grid gap-5 lg:grid-cols-2">
        {shown.map((campaign) => <CampaignV4 key={campaign.slot} campaign={campaign} />)}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

export function HomeV4({ models }: { models: CatalogModelList | null }) {
  const { page, campaigns } = useStorefront();
  const hero = heroContent(page, campaigns.home_hero);
  const hasModels = Boolean(models && models.results.length > 0);

  return (
    <div className="v4-page">
      {hero ? <HeroV4 hero={hero} hasModels={hasModels} /> : null}
      {/* Without a hero picture, the first model's photo is the largest thing on screen. */}
      <ModelGridV4 models={models} isPageTitle={!hero?.title} eagerFirst={!hero?.image} />
      <CampaignsV4 />
    </div>
  );
}

export default HomeV4;
